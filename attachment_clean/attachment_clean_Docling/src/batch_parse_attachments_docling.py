from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import traceback
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
from docling.datamodel.base_models import ConversionStatus, InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc import ImageRefMode


PROJECT_ROOT = Path(__file__).resolve().parents[4]
DOCLING_ROOT = PROJECT_ROOT / "Clean_Gov" / "attachment_clean" / "attachment_clean_Docling"
DEFAULT_INPUT_ROOT = PROJECT_ROOT / "Crawler_Gov" / "data" / "attachments"
DEFAULT_OUTPUT_ROOT = DOCLING_ROOT / "data" / "output"


# Docling 可直接处理的常见附件格式。旧版 .doc/.xls/.ppt 需要先转成新格式。
SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".pptx",
    ".xlsx",
    ".html",
    ".htm",
    ".md",
    ".txt",
    ".csv",
    ".png",
    ".jpg",
    ".jpeg",
    ".tif",
    ".tiff",
    ".bmp",
    ".webp",
}

LEGACY_EXTENSIONS = {".doc", ".xls", ".ppt"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "递归解析附件目录，并在独立输出根目录中镜像保存原目录结构。\n"
            "例如：输入根/机构/栏目/文章/附件.pdf -> "
            "输出根/机构/栏目/文章/附件.pdf/附件.md"
        )
    )
    parser.add_argument("--input-root", type=Path, default=DEFAULT_INPUT_ROOT, help="附件根目录")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT, help="解析结果根目录")
    parser.add_argument(
        "--artifacts-path",
        type=Path,
        default=None,
        help="Docling 本地模型目录，例如 Clean_Gov/attachment_clean/attachment_clean_Docling/models",
    )
    parser.add_argument(
        "--ocr-mode",
        choices=("off", "auto", "force"),
        default="off",
        help=(
            "off=不启用OCR；auto=仅对需要OCR的区域识别；"
            "force=整页强制OCR。当前已有文本型PDF建议先用off。"
        ),
    )
    parser.add_argument(
        "--ocr-langs",
        default="ch_sim,en",
        help="EasyOCR语言，逗号分隔；仅在OCR开启时使用",
    )
    parser.add_argument(
        "--table-mode",
        choices=("fast", "accurate"),
        default="fast",
        help="PDF表格识别模式",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=1,
        help="Docling内部CPU线程数。Windows CPU全量解析建议先用1或2",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=None,
        help="最多处理多少个文件，适合先做小批量试跑",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="忽略_SUCCESS.json，重新解析已有成功结果",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只扫描和打印计划，不执行解析",
    )
    parser.add_argument(
        "--save-txt",
        action="store_true",
        help="除Markdown和JSON外，再保存纯文本TXT",
    )
    parser.add_argument(
        "--save-page-images",
        action="store_true",
        help="保存每一页的页面PNG；全量解析通常不建议开启，磁盘占用较大",
    )
    parser.add_argument(
        "--min-pdf-text-chars",
        type=int,
        default=50,
        help="PDF正文少于该字符数时，在报告中标记needs_ocr=true",
    )
    return parser.parse_args()


def normalize_root(path: Path, *, must_exist: bool) -> Path:
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path = path.expanduser().resolve()
    if must_exist and not path.is_dir():
        raise NotADirectoryError(f"目录不存在或不是目录：{path}")
    return path


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def scan_files(input_root: Path, output_root: Path) -> tuple[list[Path], list[Path]]:
    supported: list[Path] = []
    legacy: list[Path] = []

    output_inside_input = is_relative_to(output_root, input_root)

    for path in input_root.rglob("*"):
        if not path.is_file():
            continue

        resolved = path.resolve()
        if output_inside_input and is_relative_to(resolved, output_root):
            continue

        # 跳过Office临时锁文件。
        if path.name.startswith("~$"):
            continue

        suffix = path.suffix.lower()
        if suffix in SUPPORTED_EXTENSIONS:
            supported.append(path)
        elif suffix in LEGACY_EXTENSIONS:
            legacy.append(path)

    supported.sort(key=lambda p: str(p).lower())
    legacy.sort(key=lambda p: str(p).lower())
    return supported, legacy


def output_dir_for(source: Path, input_root: Path, output_root: Path) -> Path:
    """
    输入：input_root/机构/栏目/子栏目/文章/附件.pdf
    输出：output_root/机构/栏目/子栏目/文章/附件.pdf/
    """
    relative = source.relative_to(input_root)
    return output_root / relative.parent / relative.name


def source_signature(source: Path) -> dict[str, int]:
    stat = source.stat()
    return {
        "source_size": stat.st_size,
        "source_mtime_ns": stat.st_mtime_ns,
    }


def load_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def should_skip(source: Path, output_dir: Path, overwrite: bool) -> bool:
    if overwrite:
        return False

    success_file = output_dir / "_SUCCESS.json"
    record = load_json(success_file)
    if not record:
        return False

    sig = source_signature(source)
    return (
        record.get("source_size") == sig["source_size"]
        and record.get("source_mtime_ns") == sig["source_mtime_ns"]
    )


def make_converter(args: argparse.Namespace) -> DocumentConverter:
    artifacts_path = None
    if args.artifacts_path is not None:
        artifacts_path = normalize_root(args.artifacts_path, must_exist=True)

    pipeline_options = PdfPipelineOptions(
        artifacts_path=artifacts_path,
        do_ocr=args.ocr_mode != "off",
        do_table_structure=True,
    )

    pipeline_options.table_structure_options.mode = (
        TableFormerMode.FAST
        if args.table_mode == "fast"
        else TableFormerMode.ACCURATE
    )
    pipeline_options.table_structure_options.do_cell_matching = True

    # 当前本地模型不完整时，关闭不需要的增强模型，避免额外下载或报错。
    pipeline_options.do_code_enrichment = False
    pipeline_options.do_formula_enrichment = False
    pipeline_options.do_picture_classification = False
    pipeline_options.do_picture_description = False

    # 全量语料默认只保留独立图片，不保留每一页的整页PNG。
    pipeline_options.generate_picture_images = True
    pipeline_options.generate_page_images = args.save_page_images

    pipeline_options.accelerator_options = AcceleratorOptions(
        num_threads=max(1, args.threads),
        device=AcceleratorDevice.CPU,
    )

    if args.ocr_mode != "off":
        langs = [item.strip() for item in args.ocr_langs.split(",") if item.strip()]
        if langs:
            pipeline_options.ocr_options.lang = langs
        pipeline_options.ocr_options.force_full_page_ocr = args.ocr_mode == "force"

    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_options=pipeline_options,
                backend=PyPdfiumDocumentBackend,
            )
        }
    )


def safe_page_count(document: Any) -> int | None:
    try:
        return int(document.num_pages())
    except Exception:
        try:
            return len(document.pages)
        except Exception:
            return None


def collect_errors(result: Any) -> list[str]:
    messages: list[str] = []
    for error in getattr(result, "errors", []) or []:
        message = getattr(error, "error_message", None)
        messages.append(str(message if message is not None else error))
    return messages


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def append_jsonl(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(data, ensure_ascii=False) + "\n")


def process_one(
    converter: DocumentConverter,
    source: Path,
    input_root: Path,
    output_root: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    relative = source.relative_to(input_root)
    output_dir = output_dir_for(source, input_root, output_root)
    output_dir.mkdir(parents=True, exist_ok=True)

    started_at = datetime.now().astimezone().isoformat()
    started = time.perf_counter()
    signature = source_signature(source)

    base_record: dict[str, Any] = {
        "source_file": str(source),
        "relative_source": relative.as_posix(),
        "source_extension": source.suffix.lower(),
        "output_dir": str(output_dir),
        "started_at": started_at,
        **signature,
    }

    try:
        result = converter.convert(source, raises_on_error=False)
        status = result.status
        status_name = getattr(status, "value", str(status))
        errors = collect_errors(result)

        if status not in {
            ConversionStatus.SUCCESS,
            ConversionStatus.PARTIAL_SUCCESS,
        }:
            raise RuntimeError(
                f"Docling转换失败，status={status_name}，errors={errors}"
            )

        doc = result.document
        stem = source.stem
        md_path = output_dir / f"{stem}.md"
        json_path = output_dir / f"{stem}.json"
        txt_path = output_dir / f"{stem}.txt"
        artifacts_dir = output_dir / f"{stem}_artifacts"

        # Markdown负责引用导出的图片；JSON使用占位符，避免在全量场景中
        # 把图片重复写入JSON或触发多次引用路径改写。
        doc.save_as_markdown(
            md_path,
            artifacts_dir=artifacts_dir,
            image_mode=ImageRefMode.REFERENCED,
        )
        doc.save_as_json(
            json_path,
            artifacts_dir=artifacts_dir,
            image_mode=ImageRefMode.PLACEHOLDER,
        )
        if args.save_txt:
            txt_path.write_text(doc.export_to_text(), encoding="utf-8")

        text_content = doc.export_to_text()
        text_chars = len(text_content.strip())
        page_count = safe_page_count(doc)
        elapsed = round(time.perf_counter() - started, 3)

        needs_ocr = (
            source.suffix.lower() == ".pdf"
            and args.ocr_mode == "off"
            and text_chars < args.min_pdf_text_chars
        )

        record = {
            **base_record,
            "status": "partial_success"
            if status == ConversionStatus.PARTIAL_SUCCESS
            else "success",
            "docling_status": status_name,
            "elapsed_seconds": elapsed,
            "page_count": page_count,
            "text_chars": text_chars,
            "needs_ocr": needs_ocr,
            "errors": errors,
            "markdown_path": str(md_path),
            "json_path": str(json_path),
            "txt_path": str(txt_path) if args.save_txt else None,
            "artifacts_dir": str(artifacts_dir),
            "finished_at": datetime.now().astimezone().isoformat(),
        }

        write_json(output_dir / "_SUCCESS.json", record)
        failed_marker = output_dir / "_FAILED.json"
        if failed_marker.exists():
            failed_marker.unlink()
        return record

    except Exception as exc:
        elapsed = round(time.perf_counter() - started, 3)
        record = {
            **base_record,
            "status": "failed",
            "elapsed_seconds": elapsed,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "finished_at": datetime.now().astimezone().isoformat(),
        }
        write_json(output_dir / "_FAILED.json", record)
        return record


def configure_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_path, encoding="utf-8"),
        ],
    )


def main() -> int:
    args = parse_args()
    input_root = normalize_root(args.input_root, must_exist=True)
    output_root = normalize_root(args.output_root, must_exist=False)
    output_root.mkdir(parents=True, exist_ok=True)

    if input_root == output_root:
        raise ValueError("input-root 和 output-root 不能是同一个目录")

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    reports_dir = output_root / "_reports"
    log_path = reports_dir / f"run_{run_id}.log"
    manifest_path = reports_dir / f"manifest_{run_id}.jsonl"
    summary_path = reports_dir / f"summary_{run_id}.json"
    configure_logging(log_path)

    supported, legacy = scan_files(input_root, output_root)
    if args.max_files is not None:
        supported = supported[: max(0, args.max_files)]

    suffix_counts = Counter(path.suffix.lower() for path in supported)
    logging.info("输入根目录：%s", input_root)
    logging.info("输出根目录：%s", output_root)
    logging.info("待处理文件：%d", len(supported))
    logging.info("格式统计：%s", dict(sorted(suffix_counts.items())))
    logging.info("旧版Office待转换文件：%d", len(legacy))

    for path in legacy[:20]:
        logging.warning("跳过旧版Office格式，请先转换：%s", path)
    if len(legacy) > 20:
        logging.warning("其余旧版Office文件未逐条打印：%d", len(legacy) - 20)

    if args.dry_run:
        for source in supported[:50]:
            logging.info(
                "计划：%s -> %s",
                source,
                output_dir_for(source, input_root, output_root),
            )
        if len(supported) > 50:
            logging.info("其余计划未逐条打印：%d", len(supported) - 50)

        summary = {
            "run_id": run_id,
            "dry_run": True,
            "input_root": str(input_root),
            "output_root": str(output_root),
            "total_supported": len(supported),
            "by_extension": dict(sorted(suffix_counts.items())),
            "legacy_office_count": len(legacy),
        }
        write_json(summary_path, summary)
        return 0

    # 当前用户使用本地模型；该变量避免意外联网拉取Hugging Face模型。
    if args.artifacts_path is not None:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")

    converter = make_converter(args)

    counters: Counter[str] = Counter()
    started = time.perf_counter()

    for index, source in enumerate(supported, start=1):
        out_dir = output_dir_for(source, input_root, output_root)
        if should_skip(source, out_dir, args.overwrite):
            counters["skipped_existing"] += 1
            logging.info("[%d/%d] 已完成，跳过：%s", index, len(supported), source)
            continue

        logging.info("[%d/%d] 开始：%s", index, len(supported), source)
        record = process_one(
            converter=converter,
            source=source,
            input_root=input_root,
            output_root=output_root,
            args=args,
        )
        append_jsonl(manifest_path, record)
        counters[record["status"]] += 1
        if record.get("needs_ocr"):
            counters["needs_ocr"] += 1

        logging.info(
            "[%d/%d] %s | %.3fs | %s",
            index,
            len(supported),
            record["status"],
            record.get("elapsed_seconds", 0.0),
            source,
        )

    elapsed = round(time.perf_counter() - started, 3)
    summary = {
        "run_id": run_id,
        "dry_run": False,
        "input_root": str(input_root),
        "output_root": str(output_root),
        "total_scanned_supported": len(supported),
        "by_extension": dict(sorted(suffix_counts.items())),
        "legacy_office_count": len(legacy),
        "counts": dict(counters),
        "elapsed_seconds": elapsed,
        "ocr_mode": args.ocr_mode,
        "table_mode": args.table_mode,
        "threads": args.threads,
        "manifest": str(manifest_path),
        "log": str(log_path),
        "finished_at": datetime.now().astimezone().isoformat(),
    }
    write_json(summary_path, summary)

    logging.info("全部结束：%s", summary)
    return 1 if counters["failed"] > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
