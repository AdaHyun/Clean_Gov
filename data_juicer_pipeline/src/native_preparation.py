"""Thin input adapter for the native Data-Juicer pipeline.

This module intentionally does not clean text.  It only converts the web
JSONL and the attachment parser's sidecar layout into stable JSONL lanes that
Data-Juicer can consume without loading ``raw.md`` or failed parses.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit, urlunsplit

from jsonl_io import JsonlIssue, iter_jsonl, write_jsonl_record


LANE_NAMES = (
    "web_normal",
    "web_multiline",
    "web_table",
    "attachment_text",
    "attachment_table",
    "reparse_required",
    "oversized",
)

MARKDOWN_TABLE_RE = re.compile(r"(?m)^\s*\|.*\|\s*$")
HTML_TABLE_RE = re.compile(r"<table\b", re.IGNORECASE)


@dataclass(frozen=True)
class PreparationOptions:
    web_input: Path
    attachment_root: Path
    output_dir: Path
    max_native_chars: int = 3_000_000
    write_outputs: bool = True


@dataclass(frozen=True)
class PreparationResult:
    summary: dict[str, Any]
    lane_paths: dict[str, Path]


def _long_path(path: Path) -> str:
    """Return a Windows long-path spelling while remaining portable."""
    value = str(path.resolve())
    if os.name == "nt" and not value.startswith("\\\\?\\"):
        return "\\\\?\\" + value
    return value


def _display_path(path: str | Path) -> str:
    value = str(path)
    return value[4:] if value.startswith("\\\\?\\") else value


def _read_json(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"JSON 不是对象: {_display_path(path)}")
    return value


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _normalized_url(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    try:
        parts = urlsplit(value)
    except ValueError:
        return value
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path or "/"
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def _stable_id(prefix: str, identity: str) -> str:
    return f"{prefix}_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:32]}"


def _unique_id(base: str, seen: Counter[str]) -> str:
    seen[base] += 1
    return base if seen[base] == 1 else f"{base}_{seen[base]}"


def _nonempty_line_count(text: str) -> int:
    return sum(bool(line.strip()) for line in text.splitlines())


def _has_markdown_table(text: str) -> bool:
    if not MARKDOWN_TABLE_RE.search(text):
        return False
    return bool(re.search(r"(?m)^\s*\|?\s*:?-{3,}", text))


def _attachment_directories(root: Path) -> Iterator[tuple[str, list[str]]]:
    for current, _, files in os.walk(_long_path(root)):
        if "metadata.json" in files:
            yield current, files


def _attachment_record(metadata: dict[str, Any], text: str) -> dict[str, Any]:
    relative_path = str(metadata.get("source_relative_path") or "")
    source = relative_path.replace("\\", "/").split("/", 1)[0] if relative_path else ""
    source_doc_id = str(metadata.get("document_id") or "")
    file_sha256 = str(metadata.get("file_sha256") or "")
    identity = file_sha256 or source_doc_id or relative_path
    extension = str(metadata.get("source_extension") or "").lower()
    source_name = str(metadata.get("source_file_name") or Path(relative_path).name)
    return {
        "doc_id": _stable_id("att", identity),
        "source_doc_id": source_doc_id,
        "title": Path(source_name).stem,
        "url": "",
        "text": text,
        "source": source,
        "content_origin": "attachment",
        "document_type": extension.lstrip(".") or "unknown",
        "source_extension": extension,
        "source_relative_path": relative_path,
        "file_sha256": file_sha256,
        "parser_status": str(metadata.get("status") or ""),
        "has_html_table": bool(HTML_TABLE_RE.search(text)),
        "has_markdown_table": _has_markdown_table(text),
        "input_origin": "attachment_documents",
        # Keep parser sidecars opaque while Data-Juicer/Hugging Face infers a
        # tabular schema.  ISO date strings otherwise become datetime values
        # that Data-Juicer 1.5.3's JSONL exporter cannot serialize.
        "metadata_json": json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
    }


def _web_record(original: dict[str, Any], line_number: int, seen: Counter[str]) -> dict[str, Any]:
    source_doc_id = str(original.get("doc_id") or "")
    url = str(original.get("url") or "")
    identity = _normalized_url(url) or f"{source_doc_id}|{line_number}"
    text = str(original.get("text") or "")
    metadata = {key: value for key, value in original.items() if key != "text"}
    return {
        "doc_id": _unique_id(_stable_id("web", identity), seen),
        "source_doc_id": source_doc_id,
        "title": str(original.get("title") or ""),
        "url": url,
        "text": text,
        "source": str(original.get("source") or ""),
        "content_origin": "web",
        "document_type": "html",
        "source_extension": "",
        "source_relative_path": "",
        "file_sha256": "",
        "parser_status": "not_applicable",
        # Keep the canonical Data-Juicer schema identical to attachment
        # records.  Without these defaults, the global JSONL merge changes
        # columns partway through the file and Hugging Face datasets rejects it.
        "parser_is_empty": False,
        "parser_warnings": "[]",
        "has_html_table": bool(HTML_TABLE_RE.search(text)),
        "has_markdown_table": _has_markdown_table(text),
        "input_origin": str(original.get("input_origin") or "web_jsonl"),
        "metadata_json": json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
    }


def prepare_inputs(options: PreparationOptions) -> PreparationResult:
    if not options.web_input.is_file():
        raise FileNotFoundError(f"网页输入不存在: {options.web_input}")
    if not options.attachment_root.is_dir():
        raise FileNotFoundError(f"附件目录不存在: {options.attachment_root}")
    if options.max_native_chars < 1:
        raise ValueError("max_native_chars 必须是正整数")

    output_dir = options.output_dir.resolve()
    lane_paths = {name: output_dir / f"{name}.jsonl" for name in LANE_NAMES}
    handles: dict[str, Any] = {}
    invalid_handle = None
    if options.write_outputs:
        output_dir.mkdir(parents=True, exist_ok=False)
        handles = {name: path.open("w", encoding="utf-8") for name, path in lane_paths.items()}
        invalid_handle = (output_dir / "invalid_input.jsonl").open("w", encoding="utf-8")

    counts: Counter[str] = Counter()
    web_seen: Counter[str] = Counter()
    attachment_seen: Counter[str] = Counter()
    issues: list[JsonlIssue] = []

    def emit(lane: str, record: dict[str, Any]) -> None:
        counts[lane] += 1
        if options.write_outputs:
            write_jsonl_record(handles[lane], record)

    try:
        for line_number, original in iter_jsonl(options.web_input, issues=issues):
            counts["web_total"] += 1
            text = original.get("text")
            if not isinstance(text, str) or not text.strip():
                bad = dict(original)
                bad["quarantine_reason"] = "web_text_missing_or_empty"
                emit("reparse_required", bad)
                continue
            record = _web_record(original, line_number, web_seen)
            if record["has_html_table"] or record["has_markdown_table"]:
                # Table-bearing records bypass spacing, soft-line and quality
                # rules until a dedicated table policy is agreed.
                emit("web_table", record)
            else:
                multiline = _nonempty_line_count(text) >= 3
                emit("web_multiline" if multiline else "web_normal", record)

        for issue in issues:
            counts["invalid_web_json"] += 1
            if invalid_handle is not None:
                write_jsonl_record(invalid_handle, asdict(issue))

        for current, files in _attachment_directories(options.attachment_root):
            counts["attachment_total"] += 1
            try:
                metadata = _read_json(os.path.join(current, "metadata.json"))
                quality = _read_json(os.path.join(current, "quality.json")) if "quality.json" in files else {}
                text = _read_text(os.path.join(current, "content.md")) if "content.md" in files else ""
                record = _attachment_record(metadata, text)
                record["doc_id"] = _unique_id(record["doc_id"], attachment_seen)
                record["parser_is_empty"] = bool(quality.get("is_empty", not text.strip()))
                record["parser_warnings"] = json.dumps(quality.get("warnings", []), ensure_ascii=False)
                status = record["parser_status"]
                if status != "callback_success" or not text.strip():
                    record["quarantine_reason"] = "attachment_parse_failed_or_empty"
                    emit("reparse_required", record)
                elif len(text) > options.max_native_chars:
                    record["quarantine_reason"] = "oversized_for_native_pipeline"
                    emit("oversized", record)
                elif record["source_extension"] == ".xlsx" or record["has_html_table"]:
                    emit("attachment_table", record)
                else:
                    emit("attachment_text", record)
            except Exception as exc:  # keep scanning and record the exact bad directory
                counts["invalid_attachment"] += 1
                if invalid_handle is not None:
                    write_jsonl_record(
                        invalid_handle,
                        {
                            "path": _display_path(current),
                            "error": f"{type(exc).__name__}: {exc}",
                        },
                    )
    finally:
        for handle in handles.values():
            handle.close()
        if invalid_handle is not None:
            invalid_handle.close()

    summary: dict[str, Any] = {
        "web_input": str(options.web_input.resolve()),
        "attachment_root": str(options.attachment_root.resolve()),
        "output_dir": str(output_dir),
        "max_native_chars": options.max_native_chars,
        "write_outputs": options.write_outputs,
        "counts": dict(counts),
        "lane_paths": {name: str(path) for name, path in lane_paths.items()},
    }
    if options.write_outputs:
        (output_dir / "preparation_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return PreparationResult(summary, lane_paths)
