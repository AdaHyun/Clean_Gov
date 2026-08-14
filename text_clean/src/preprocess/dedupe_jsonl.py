#!/usr/bin/env python
"""
Clean government document JSONL files by:
1. Removing records with duplicate exact URL or duplicate exact body text.
2. Removing records whose body text is shorter than a configurable threshold.

Deleted records are written to a separate JSONL file with an added
``_delete_meta`` field explaining why the record was removed.

Default paths are relative to the target project layout:
  <project_root>/Clean_Gov/text_clean/src/preprocess/dedupe_jsonl.py
  <project_root>/Clean_Gov/text_clean/data/output/gov-input/raw_all_documents.jsonl
  <project_root>/Clean_Gov/text_clean/data/output/gov-input/

For local experiments, override paths with command-line arguments.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

def find_project_root(script_path):
    resolved = script_path.resolve()
    for parent in [resolved.parent, *resolved.parents]:
        if parent.name == "Crawler_Gov":
            return parent.parent
    return resolved.parents[4]

PROJECT_ROOT = find_project_root(Path(__file__))
DEFAULT_INPUT_NAME = PROJECT_ROOT / "Crawler_Gov" / "data" / "raw_all_documents.jsonl"
DEFAULT_CLEANED_NAME = PROJECT_ROOT / "Clean_Gov" / "text_clean" / "data" / "output"/"gov-input"/ "raw_all_documents_cleaned.jsonl"
DEFAULT_DELETED_NAME = PROJECT_ROOT / "Clean_Gov" / "text_clean" / "data" / "output"/"gov-input"/ "raw_all_documents_deleted.jsonl"
DEFAULT_REPORT_NAME = PROJECT_ROOT / "Clean_Gov" / "text_clean" / "data" / "output"/"gov-input"/ "raw_all_documents_clean_report.json"


def find_project_root(script_path: Path) -> Path:
    """Infer the project root from the expected Clean_Gov/text_clean layout."""
    resolved = script_path.resolve()
    for parent in [resolved.parent, *resolved.parents]:
        if parent.name == "Clean_Gov":
            return parent.parent
    # Fallback for unusual placements: src/preprocess/script.py -> project root.
    parents = list(resolved.parents)
    return parents[3] if len(parents) > 3 else resolved.parent


def default_io_dir(script_path: Path) -> Path:
    project_root = find_project_root(script_path)
    return project_root / "Clean_Gov" / "text_clean" / "data" / "output" / "gov-input"


def body_text_of(record: dict[str, Any]) -> str:
    content = record.get("content")
    if isinstance(content, dict):
        body = content.get("body_text")
    else:
        body = None
    if body is None:
        return ""
    return str(body)


def parse_args() -> argparse.Namespace:
    io_dir = default_io_dir(Path(__file__))
    parser = argparse.ArgumentParser(
        description="Filter short records and remove exact duplicate URLs/body_text from JSONL."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=io_dir / DEFAULT_INPUT_NAME,
        help=f"Input JSONL path. Default: {io_dir / DEFAULT_INPUT_NAME}",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=io_dir,
        help=f"Directory for output files. Default: {io_dir}",
    )
    parser.add_argument(
        "--cleaned-name",
        default=DEFAULT_CLEANED_NAME,
        help=f"Cleaned JSONL filename. Default: {DEFAULT_CLEANED_NAME}",
    )
    parser.add_argument(
        "--deleted-name",
        default=DEFAULT_DELETED_NAME,
        help=f"Deleted JSONL filename. Default: {DEFAULT_DELETED_NAME}",
    )
    parser.add_argument(
        "--report-name",
        default=DEFAULT_REPORT_NAME,
        help=f"Summary report JSON filename. Default: {DEFAULT_REPORT_NAME}",
    )
    parser.add_argument(
        "--min-body-length",
        type=int,
        default=0,
        help="Drop records whose exact body_text length is below this threshold. Default: 0",
    )
    parser.add_argument(
        "--encoding",
        default="utf-8",
        help="Input/output text encoding. Default: utf-8",
    )
    return parser.parse_args()


def add_delete_meta(
    record: dict[str, Any],
    *,
    reason: str,
    line_no: int,
    body_length: int,
    kept_doc_id: Any = None,
    kept_line_no: int | None = None,
    duplicate_key: str | None = None,
    body_sha256: str | None = None,
) -> dict[str, Any]:
    record["_delete_meta"] = {
        "reason": reason,
        "line_no": line_no,
        "body_length": body_length,
        "kept_doc_id": kept_doc_id,
        "kept_line_no": kept_line_no,
        "duplicate_key": duplicate_key,
        "body_sha256": body_sha256,
    }
    return record


def main() -> int:
    args = parse_args()
    if args.min_body_length < 0:
        raise ValueError("--min-body-length must be >= 0")

    input_path = args.input.resolve()
    output_dir = args.output_dir.resolve()
    cleaned_path = output_dir / args.cleaned_name
    deleted_path = output_dir / args.deleted_name
    report_path = output_dir / args.report_name

    for output_path in [cleaned_path, deleted_path, report_path]:
        if output_path.resolve() == input_path:
            raise ValueError(f"Refusing to overwrite input file: {output_path}")

    output_dir.mkdir(parents=True, exist_ok=True)

    seen_urls: dict[str, tuple[Any, int]] = {}
    seen_bodies: dict[str, tuple[Any, int]] = {}
    stats: dict[str, Any] = {
        "input_path": str(input_path),
        "cleaned_path": str(cleaned_path),
        "deleted_path": str(deleted_path),
        "report_path": str(report_path),
        "min_body_length": args.min_body_length,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "total_lines": 0,
        "invalid_json_lines": 0,
        "kept_records": 0,
        "deleted_records": 0,
        "deleted_short_body": 0,
        "deleted_duplicate_url": 0,
        "deleted_duplicate_body": 0,
        "missing_or_empty_url_records": 0,
        "missing_or_empty_body_records": 0,
        "invalid_json_samples": [],
    }

    with input_path.open("r", encoding=args.encoding) as fin, cleaned_path.open(
        "w", encoding=args.encoding
    ) as fclean, deleted_path.open("w", encoding=args.encoding) as fdel:
        for line_no, line in enumerate(fin, 1):
            stats["total_lines"] += 1
            raw_line = line.rstrip("\n")
            if not raw_line:
                continue

            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                stats["invalid_json_lines"] += 1
                if len(stats["invalid_json_samples"]) < 20:
                    stats["invalid_json_samples"].append(
                        {"line_no": line_no, "error": str(exc), "line_preview": raw_line[:200]}
                    )
                fdel.write(
                    json.dumps(
                        {
                            "_delete_meta": {
                                "reason": "invalid_json",
                                "line_no": line_no,
                                "error": str(exc),
                            },
                            "raw_line": raw_line,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                stats["deleted_records"] += 1
                continue

            if not isinstance(record, dict):
                record = {"_raw_json_value": record}

            doc_id = record.get("doc_id")
            url = record.get("url")
            url_key = str(url) if url is not None else ""
            body_key = body_text_of(record)
            body_length = len(body_key)
            body_sha256 = hashlib.sha256(body_key.encode(args.encoding)).hexdigest()

            if not url_key:
                stats["missing_or_empty_url_records"] += 1
            if not body_key:
                stats["missing_or_empty_body_records"] += 1

            if body_length < args.min_body_length:
                deleted = add_delete_meta(
                    record,
                    reason="short_body",
                    line_no=line_no,
                    body_length=body_length,
                    body_sha256=body_sha256,
                )
                fdel.write(json.dumps(deleted, ensure_ascii=False) + "\n")
                stats["deleted_records"] += 1
                stats["deleted_short_body"] += 1
                continue

            if url_key and url_key in seen_urls:
                kept_doc_id, kept_line_no = seen_urls[url_key]
                deleted = add_delete_meta(
                    record,
                    reason="duplicate_url",
                    line_no=line_no,
                    body_length=body_length,
                    kept_doc_id=kept_doc_id,
                    kept_line_no=kept_line_no,
                    duplicate_key=url_key,
                    body_sha256=body_sha256,
                )
                fdel.write(json.dumps(deleted, ensure_ascii=False) + "\n")
                stats["deleted_records"] += 1
                stats["deleted_duplicate_url"] += 1
                continue

            if body_key in seen_bodies:
                kept_doc_id, kept_line_no = seen_bodies[body_key]
                deleted = add_delete_meta(
                    record,
                    reason="duplicate_body",
                    line_no=line_no,
                    body_length=body_length,
                    kept_doc_id=kept_doc_id,
                    kept_line_no=kept_line_no,
                    duplicate_key=body_sha256,
                    body_sha256=body_sha256,
                )
                fdel.write(json.dumps(deleted, ensure_ascii=False) + "\n")
                stats["deleted_records"] += 1
                stats["deleted_duplicate_body"] += 1
                continue

            if url_key:
                seen_urls[url_key] = (doc_id, line_no)
            seen_bodies[body_key] = (doc_id, line_no)
            fclean.write(json.dumps(record, ensure_ascii=False) + "\n")
            stats["kept_records"] += 1

    stats["finished_at"] = datetime.now().isoformat(timespec="seconds")
    stats["unique_kept_urls"] = len(seen_urls)
    stats["unique_kept_bodies"] = len(seen_bodies)

    with report_path.open("w", encoding=args.encoding) as freport:
        json.dump(stats, freport, ensure_ascii=False, indent=2)

    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
