from __future__ import annotations

from pathlib import Path

from .attachment_checker import check_attachments
from .common import load_html, read_jsonl
from .image_checker import check_images
from .manifest_builder import build_manifest_row
from .raw_html_checker import check_raw_html
from .report_writer import write_scan_outputs


def run_scan(jsonl_dir: Path, raw_html_dir: Path, image_root_dir: Path, attachment_root_dir: Path, output_dir: Path, crawler_root: Path) -> dict:
    manifest = []
    logs = {
        "webpage_check_log": [],
        "raw_html_check_log": [],
        "image_check_log": [],
        "attachment_check_log": [],
        "scan_errors": [],
    }
    total = 0
    manifest_index = 1
    raw_html_files = list(raw_html_dir.rglob("*.htm*")) if raw_html_dir.exists() else []
    for jsonl_file in sorted(jsonl_dir.glob("*.jsonl")):
        try:
            iterator = read_jsonl(jsonl_file)
            for line_no, record in iterator:
                total += 1
                raw_check = check_raw_html(record, crawler_root, raw_html_dir, raw_html_files)
                raw_path = Path(raw_check["raw_html_resolved_path"]) if raw_check["raw_html_resolved_path"] else None
                html = load_html(record, raw_path)
                image_check = check_images(record, html, crawler_root, image_root_dir)
                attachment_check = check_attachments(record, html, crawler_root, attachment_root_dir)
                row = build_manifest_row(manifest_index, jsonl_file, line_no, record, raw_check, image_check, attachment_check, crawler_root)
                if row:
                    manifest.append(row)
                    manifest_index += 1
                base_log = {"jsonl_file": str(jsonl_file), "line_no": line_no, "doc_id": record.get("doc_id", ""), "url": record.get("url", "")}
                logs["webpage_check_log"].append({**base_log, "crawl_status": (record.get("crawl") or {}).get("crawl_status", ""), "http_status": (record.get("crawl") or {}).get("http_status", "")})
                logs["raw_html_check_log"].append({**base_log, **raw_check})
                logs["image_check_log"].append({**base_log, **{k: v for k, v in image_check.items() if k not in ["html_images", "image_details"]}})
                logs["attachment_check_log"].append({**base_log, **{k: v for k, v in attachment_check.items() if k not in ["html_attachments", "attachment_details"]}})
        except Exception as exc:
            logs["scan_errors"].append({"jsonl_file": str(jsonl_file), "error": str(exc)})
    write_scan_outputs(output_dir, manifest, logs, total)
    return {"total_records": total, "manifest_records": len(manifest), "output_dir": str(output_dir)}
