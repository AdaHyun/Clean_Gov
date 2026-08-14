"""Route adapted Crawler_Gov JSONL files into review/processing queues.

This script does not modify input files. It reads Data-Juicer-ready records
from Gov_rawData, adds a routing_meta field in the output copies, and writes
route-specific JSONL files plus summary reports.
"""

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]
TEXT_CLEAN_DIR = PROJECT_ROOT / "Clean_Gov" / "text_clean"
DEFAULT_INPUT_DIR = TEXT_CLEAN_DIR / "data" / "output" / "gov_rawData_juicer"
DEFAULT_OUTPUT_DIR = TEXT_CLEAN_DIR / "data" / "output" / "gov-routing"
DEFAULT_SHORT_LEN = 80
DEFAULT_LONG_LEN = 20000

ROUTE_BODY_TEXT_OK = "body_text_ok"
ROUTE_NEEDS_BODY_EXTRACTION = "needs_body_extraction"
ROUTE_ATTACHMENT_CANDIDATE = "attachment_candidate"
ROUTE_SHORT_OR_EMPTY_NO_ATTACHMENT = "short_or_empty_no_attachment"

ROUTE_FILES = {
    ROUTE_BODY_TEXT_OK: "body_text_ok.jsonl",
    ROUTE_NEEDS_BODY_EXTRACTION: "needs_body_extraction.jsonl",
    ROUTE_ATTACHMENT_CANDIDATE: "attachment_candidates.jsonl",
    ROUTE_SHORT_OR_EMPTY_NO_ATTACHMENT: "short_or_empty_no_attachment.jsonl",
}

NAV_TERMS = [
    "首页",
    "网站首页",
    "机构信息",
    "网站地图",
    "联系我们",
    "返回旧版",
    "当前位置",
    "上一篇",
    "下一篇",
    "招生招聘",
    "中心简介",
]

FOOTER_TERMS = [
    "版权所有",
    "京ICP备",
    "京公网安备",
    "联系方式",
    "微信公众号",
]

PREV_NEXT_TERMS = ["上一篇", "下一篇"]

STRONG_NAV_PATTERNS = [
    "联系我们 | 网站地图",
    "当前位置： 首页",
    "当前位置：首页",
    "上一篇：",
    "下一篇：",
    "京ICP备",
    "京公网安备",
]

ATTACHMENT_TYPE_FLAGS = {
    "pdf": "pdf_attachment",
    "doc": "doc_attachment",
    "docx": "doc_attachment",
    "xls": "xls_attachment",
    "xlsx": "xls_attachment",
    "csv": "xls_attachment",
    "png": "image_attachment",
    "jpg": "image_attachment",
    "jpeg": "image_attachment",
    "gif": "image_attachment",
    "bmp": "image_attachment",
    "webp": "image_attachment",
    "zip": "archive_attachment",
    "rar": "archive_attachment",
    "7z": "archive_attachment",
}


def normalize_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def iter_jsonl(path):
    with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                yield line_no, None, f"invalid JSON: {exc}"
                continue
            if not isinstance(record, dict):
                yield line_no, None, "non-object JSON value"
                continue
            yield line_no, record, None


def get_text(record):
    text = record.get("text")
    if text is None:
        text = (record.get("content") or {}).get("body_text", "")
    return normalize_text(text)


def get_body_html(record):
    return str((record.get("content") or {}).get("body_html") or "")


def get_attachments(record):
    attachments = record.get("attachments")
    return attachments if isinstance(attachments, list) else []


def get_attachment_suffix(attachment):
    if isinstance(attachment, dict):
        candidates = [
            attachment.get("file_type"),
            attachment.get("name"),
            attachment.get("filename"),
            attachment.get("local_path"),
            attachment.get("url"),
        ]
    else:
        candidates = [attachment]

    for candidate in candidates:
        if not candidate:
            continue
        value = str(candidate).strip().lower().split("?")[0].split("#")[0]
        if "." in value:
            return value.rsplit(".", 1)[-1]
        if value in ATTACHMENT_TYPE_FLAGS:
            return value
    return ""


def detect_attachment_flags(attachments):
    flags = set()
    suffix_counts = Counter()
    for attachment in attachments:
        suffix = get_attachment_suffix(attachment)
        if suffix:
            suffix_counts[suffix] += 1
            flag = ATTACHMENT_TYPE_FLAGS.get(suffix)
            if flag:
                flags.add(flag)
    return flags, suffix_counts


def detect_table(body_html):
    return bool(re.search(r"<\s*table\b", body_html or "", flags=re.IGNORECASE))


def compute_noise(text):
    nav_hits = sum(text.count(term) for term in NAV_TERMS)
    footer_hits = sum(text.count(term) for term in FOOTER_TERMS)
    prev_next_hits = sum(text.count(term) for term in PREV_NEXT_TERMS)
    strong_hits = sum(1 for pattern in STRONG_NAV_PATTERNS if pattern in text)
    starts_like_nav = (
        text.startswith("首页 ")
        or text.startswith("网站首页 ")
        or text.startswith("联系我们 | 网站地图")
    )
    nav_noise_score = nav_hits + footer_hits * 2 + prev_next_hits * 2 + strong_hits * 3
    return {
        "nav_hits": nav_hits,
        "footer_hits": footer_hits,
        "prev_next_hits": prev_next_hits,
        "strong_hits": strong_hits,
        "starts_like_nav": starts_like_nav,
        "nav_noise_score": nav_noise_score,
    }


def decide_route(flags):
    if "has_attachment" in flags and ("empty_text" in flags or "short_text" in flags):
        return ROUTE_ATTACHMENT_CANDIDATE, "empty or short text with attachments"
    if "empty_text" in flags or "short_text" in flags:
        return ROUTE_SHORT_OR_EMPTY_NO_ATTACHMENT, "empty or short text without attachments"
    if "nav_noise" in flags:
        return ROUTE_NEEDS_BODY_EXTRACTION, "strong navigation or template noise detected in body_text"
    return ROUTE_BODY_TEXT_OK, "body_text likely usable"


def route_record(record, source_file, line_no, short_len, long_len):
    text = get_text(record)
    body_html = get_body_html(record)
    attachments = get_attachments(record)
    attachment_flags, suffix_counts = detect_attachment_flags(attachments)
    noise = compute_noise(text)

    flags = set()
    text_len = len(text)
    body_html_len = len(body_html)
    attachment_count = len(attachments)

    if text_len == 0:
        flags.add("empty_text")
    elif text_len < short_len:
        flags.add("short_text")
    if text_len > long_len:
        flags.add("long_text")

    if body_html.strip():
        flags.add("has_body_html")
    if detect_table(body_html):
        flags.add("has_table")

    if attachment_count:
        flags.add("has_attachment")
        flags.update(attachment_flags)

    images = record.get("images")
    image_count = len(images) if isinstance(images, list) else 0
    if image_count:
        flags.add("has_image")
    if image_count and text_len < short_len and not attachment_count:
        flags.add("likely_image_article")

    if noise["starts_like_nav"] or noise["nav_noise_score"] >= 8:
        flags.add("nav_noise")
    if noise["footer_hits"] >= 1:
        flags.add("footer_noise")
    if noise["prev_next_hits"] >= 1:
        flags.add("prev_next_noise")

    if "empty_text" in flags or "short_text" in flags or "nav_noise" in flags:
        flags.add("needs_manual_review")

    route, reason = decide_route(flags)
    routing_meta = {
        "route": route,
        "flags": sorted(flags),
        "reason": reason,
        "source_file": source_file,
        "line_no": line_no,
        "text_len": text_len,
        "body_html_len": body_html_len,
        "attachment_count": attachment_count,
        "attachment_suffix_counts": dict(sorted(suffix_counts.items())),
        "image_count": image_count,
        "has_table": "has_table" in flags,
        "nav_noise_score": noise["nav_noise_score"],
        "nav_hits": noise["nav_hits"],
        "footer_hits": noise["footer_hits"],
        "prev_next_hits": noise["prev_next_hits"],
        "strong_nav_pattern_hits": noise["strong_hits"],
        "starts_like_nav": noise["starts_like_nav"],
    }
    return routing_meta


def write_jsonl(handle, record):
    handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
    handle.write("\n")


def write_summary_csv(path, rows):
    fieldnames = [
        "source_file",
        "total",
        ROUTE_BODY_TEXT_OK,
        ROUTE_NEEDS_BODY_EXTRACTION,
        ROUTE_ATTACHMENT_CANDIDATE,
        ROUTE_SHORT_OR_EMPTY_NO_ATTACHMENT,
        "table_candidates",
        "empty_text",
        "short_text",
        "has_attachment",
        "has_table",
        "nav_noise",
        "bad_rows",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_report(path, totals, per_file_rows, output_dir, short_len, long_len):
    total = totals["total"]

    def pct(value):
        return f"{value / total:.2%}" if total else "0.00%"

    lines = [
        "# Gov JSONL 分流报告",
        "",
        "## 运行范围",
        "",
        f"- 输入目录：`{DEFAULT_INPUT_DIR}`",
        f"- 输出目录：`{output_dir}`",
        f"- 短文本阈值：`text_len < {short_len}`",
        f"- 长文本阈值：`text_len > {long_len}`",
        "",
        "## 总体统计",
        "",
        "| 类别 | 数量 | 占比 | 处理建议 |",
        "|---|---:|---:|---|",
        f"| body_text_ok | {totals[ROUTE_BODY_TEXT_OK]} | {pct(totals[ROUTE_BODY_TEXT_OK])} | 直接进入 Data-Juicer 正文清洗 |",
        f"| needs_body_extraction | {totals[ROUTE_NEEDS_BODY_EXTRACTION]} | {pct(totals[ROUTE_NEEDS_BODY_EXTRACTION])} | 用 body_html 定位正文，或做模板噪声清理 |",
        f"| attachment_candidate | {totals[ROUTE_ATTACHMENT_CANDIDATE]} | {pct(totals[ROUTE_ATTACHMENT_CANDIDATE])} | 进入附件解析流程，暂不当正文语料 |",
        f"| short_or_empty_no_attachment | {totals[ROUTE_SHORT_OR_EMPTY_NO_ATTACHMENT]} | {pct(totals[ROUTE_SHORT_OR_EMPTY_NO_ATTACHMENT])} | 抽样判断：保留、丢弃或回爬/OCR |",
        f"| table_candidates | {totals['table_candidates']} | {pct(totals['table_candidates'])} | 横向清单，交给表格组检查结构化抽取 |",
        f"| bad_rows | {totals['bad_rows']} | {pct(totals['bad_rows'])} | JSON 异常或非对象行，需回查源文件 |",
        "",
        "## 输出文件说明",
        "",
        "- `all_with_routing_meta.jsonl`：全量数据副本，每条新增 `routing_meta`。",
        "- `body_text_ok.jsonl`：主路线为正常正文的数据。",
        "- `needs_body_extraction.jsonl`：疑似含导航、页脚、上一篇/下一篇等网页壳噪声的数据。",
        "- `attachment_candidates.jsonl`：空/短正文且带附件的数据，适合分给附件解析组。",
        "- `short_or_empty_no_attachment.jsonl`：空/短正文且无附件的数据，适合人工抽样判断。",
        "- `table_candidates.jsonl`：HTML 中含 `<table>` 的横向清单，可能与上述主路线文件有重复。",
        "- `routing_summary.csv`：按源文件汇总各类别数量。",
        "- `bad_rows.jsonl`：无法解析或不是 JSON 对象的异常行。",
        "",
        "## 分类依据",
        "",
        "脚本采用“一个主 route + 多个 flags”的方式。主 route 决定先交给谁处理，flags 保留所有问题线索。",
        "",
        "主 route 优先级：",
        "",
        "1. `attachment_candidate`：`text` 为空或短文本，且存在附件。",
        "2. `short_or_empty_no_attachment`：`text` 为空或短文本，且没有附件。",
        "3. `needs_body_extraction`：`text` 中检测到明显网页导航/页脚/上一篇下一篇等模板噪声。",
        "4. `body_text_ok`：其余数据，认为 `body_text` 基本可作为文章正文。",
        "",
        "常见 flags：",
        "",
        "- `empty_text`：正文为空。",
        "- `short_text`：正文短于阈值。",
        "- `long_text`：正文长于阈值。",
        "- `has_attachment` / `pdf_attachment` / `doc_attachment` / `xls_attachment` / `image_attachment`：附件相关。",
        "- `has_body_html`：保留了 HTML。",
        "- `has_table`：HTML 中含表格。",
        "- `nav_noise` / `footer_noise` / `prev_next_noise`：网页壳噪声线索。",
        "- `likely_image_article`：短文本、无附件但有图片，可能是图片正文或图解页。",
        "- `needs_manual_review`：建议人工抽样确认。",
        "",
        "## 分工处理指南",
        "",
        "### A 组：正常正文",
        "",
        "输入：`body_text_ok.jsonl`",
        "",
        "处理：直接跑 Data-Juicer 清洗配置，例如空白归一化、语言过滤、长度过滤、重复句过滤、特殊字符过滤。",
        "",
        "交付：`body_text_ok_cleaned.jsonl`、清洗前后条数、抽样质量结论。",
        "",
        "### B 组：网页壳噪声",
        "",
        "输入：`needs_body_extraction.jsonl`",
        "",
        "处理：优先用 `content.body_html` 定位正文容器，例如 `.TRS_Editor`、`#articleCon`、`#zoom`、`.article`、`.content`、`.cont`、`.detail-content`。无法定位时再做模板正则清理。",
        "",
        "交付：`body_extracted.jsonl`、抽取失败清单、每个站点模板备注。",
        "",
        "### C 组：附件型页面",
        "",
        "输入：`attachment_candidates.jsonl`",
        "",
        "处理：解析 PDF/DOC/DOCX/XLS/XLSX/图片 OCR/压缩包。不要直接丢弃，也不要直接混入正文语料。",
        "",
        "交付：`attachment_text.jsonl`、`attachment_parse_failed.jsonl`、按附件类型统计失败原因。",
        "",
        "### D 组：空/短文本无附件",
        "",
        "输入：`short_or_empty_no_attachment.jsonl`",
        "",
        "处理：人工抽样后分成 `short_keep`、`short_drop`、`needs_recrawl_or_ocr`。",
        "",
        "交付：保留/丢弃/回爬清单和规则说明。",
        "",
        "### E 组：表格候选",
        "",
        "输入：`table_candidates.jsonl`",
        "",
        "处理：检查 HTML 表格是否为正文信息。需要保留结构时，抽取为 `extracted_tables`，同时生成可读文本版。",
        "",
        "交付：`table_extracted.jsonl` 或带 `extracted_tables` 的增强记录。",
        "",
        "## 按源文件统计",
        "",
        "| source_file | total | body_text_ok | needs_body_extraction | attachment_candidate | short_or_empty_no_attachment | table_candidates | bad_rows |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]

    for row in per_file_rows:
        lines.append(
            f"| {row['source_file']} | {row['total']} | {row[ROUTE_BODY_TEXT_OK]} | "
            f"{row[ROUTE_NEEDS_BODY_EXTRACTION]} | {row[ROUTE_ATTACHMENT_CANDIDATE]} | "
            f"{row[ROUTE_SHORT_OR_EMPTY_NO_ATTACHMENT]} | {row['table_candidates']} | {row['bad_rows']} |"
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--short-len", type=int, default=DEFAULT_SHORT_LEN)
    parser.add_argument("--long-len", type=int, default=DEFAULT_LONG_LEN)
    return parser.parse_args()


def main():
    args = parse_args()
    input_dir = args.input_dir
    output_dir = args.output_dir

    if not input_dir.exists() or not input_dir.is_dir():
        raise FileNotFoundError(f"Input directory does not exist or is not a directory: {input_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    input_files = sorted(input_dir.glob("*.jsonl"))
    if not input_files:
        raise FileNotFoundError(f"No .jsonl files found in: {input_dir}")

    output_handles = {}
    try:
        output_handles["all"] = (output_dir / "all_with_routing_meta.jsonl").open("w", encoding="utf-8", newline="\n")
        output_handles["table"] = (output_dir / "table_candidates.jsonl").open("w", encoding="utf-8", newline="\n")
        output_handles["bad"] = (output_dir / "bad_rows.jsonl").open("w", encoding="utf-8", newline="\n")
        for route, filename in ROUTE_FILES.items():
            output_handles[route] = (output_dir / filename).open("w", encoding="utf-8", newline="\n")

        totals = Counter()
        per_file_rows = []

        for input_path in input_files:
            file_counts = Counter()
            for line_no, record, error in iter_jsonl(input_path):
                if error:
                    bad = {"source_file": input_path.name, "line_no": line_no, "error": error}
                    write_jsonl(output_handles["bad"], bad)
                    totals["bad_rows"] += 1
                    file_counts["bad_rows"] += 1
                    continue

                routing_meta = route_record(record, input_path.name, line_no, args.short_len, args.long_len)
                routed_record = dict(record)
                routed_record["routing_meta"] = routing_meta

                route = routing_meta["route"]
                write_jsonl(output_handles["all"], routed_record)
                write_jsonl(output_handles[route], routed_record)
                if routing_meta["has_table"]:
                    write_jsonl(output_handles["table"], routed_record)
                    totals["table_candidates"] += 1
                    file_counts["table_candidates"] += 1

                totals["total"] += 1
                totals[route] += 1
                file_counts["total"] += 1
                file_counts[route] += 1
                for flag in routing_meta["flags"]:
                    totals[flag] += 1
                    file_counts[flag] += 1

            row = {"source_file": input_path.name}
            for key in [
                "total",
                ROUTE_BODY_TEXT_OK,
                ROUTE_NEEDS_BODY_EXTRACTION,
                ROUTE_ATTACHMENT_CANDIDATE,
                ROUTE_SHORT_OR_EMPTY_NO_ATTACHMENT,
                "table_candidates",
                "empty_text",
                "short_text",
                "has_attachment",
                "has_table",
                "nav_noise",
                "bad_rows",
            ]:
                row[key] = file_counts[key]
            per_file_rows.append(row)

        write_summary_csv(output_dir / "routing_summary.csv", per_file_rows)
        write_report(output_dir / "README.md", totals, per_file_rows, output_dir, args.short_len, args.long_len)

        print(f"[DONE] files={len(input_files)} total={totals['total']} output_dir={output_dir}")
        for route in ROUTE_FILES:
            print(f"[ROUTE] {route}={totals[route]}")
        print(f"[ROUTE] table_candidates={totals['table_candidates']}")
        print(f"[WARN] bad_rows={totals['bad_rows']}")

    finally:
        for handle in output_handles.values():
            handle.close()


if __name__ == "__main__":
    main()
