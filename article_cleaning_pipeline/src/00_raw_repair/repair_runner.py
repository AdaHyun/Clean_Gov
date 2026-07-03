from __future__ import annotations

import json
from collections import defaultdict
from copy import deepcopy
from pathlib import Path

from .common import (
    default_asset_dir,
    filename_for_url,
    get_path,
    load_html,
    parse_attachment_links,
    parse_img_links,
    posix_rel,
    read_jsonl,
    resolve_local_path,
    set_path,
    write_jsonl,
)
from .crawler_adapter import CrawlerAdapter
from .raw_html_checker import check_raw_html
from .report_writer import write_repair_outputs


def _read_manifest(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _target_for_asset(record: dict, asset: dict, crawler_root: Path, asset_type: str, index: int) -> Path:
    local_path = asset.get("local_path") or ""
    if local_path:
        p = Path(local_path)
        return p if p.is_absolute() else crawler_root / p
    prefix = "img" if asset_type == "image" else "att"
    url = asset.get("url", "")
    return default_asset_dir(record, crawler_root, asset_type) / filename_for_url(url, f"{prefix}{index:03d}")


def _repair_assets(record: dict, html: str, crawler_root: Path, adapter: CrawlerAdapter, logs: dict[str, list[dict]]) -> tuple[dict, int, int]:
    repaired = deepcopy(record)
    image_rows = repaired.get("images") or []
    attachment_rows = repaired.get("attachments") or []
    if html:
        if not image_rows:
            image_rows = parse_img_links(html, get_path(record, "url", ""))
        if not attachment_rows:
            attachment_rows = parse_attachment_links(html, get_path(record, "url", ""))
    image_success = 0
    for idx, image in enumerate(image_rows, 1):
        if not isinstance(image, dict):
            continue
        target = _target_for_asset(repaired, image, crawler_root, "image", idx)
        exists = target.exists() and target.stat().st_size > 0
        if not exists and image.get("url"):
            ok, err = adapter.download_file(image["url"], target)
            image["download_status"] = "success" if ok else "failed"
            if err:
                image["error_message"] = err
            logs["image_repair_log"].append({"doc_id": get_path(repaired, "doc_id", ""), "url": image.get("url", ""), "local_path": posix_rel(target, crawler_root), "success": ok, "error": err})
            exists = ok
        image["local_path"] = posix_rel(target, crawler_root)
        image["local_path_exists"] = bool(exists)
        image["repair_status"] = "verified" if exists else "failed"
        image_success += int(exists)
    attachment_success = 0
    for idx, attachment in enumerate(attachment_rows, 1):
        if not isinstance(attachment, dict):
            continue
        target = _target_for_asset(repaired, attachment, crawler_root, "attachment", idx)
        exists = target.exists() and target.stat().st_size > 0
        if not exists and attachment.get("url"):
            ok, err = adapter.download_file(attachment["url"], target)
            attachment["download_status"] = "success" if ok else "failed"
            if err:
                attachment["error_message"] = err
            logs["attachment_repair_log"].append({"doc_id": get_path(repaired, "doc_id", ""), "url": attachment.get("url", ""), "local_path": posix_rel(target, crawler_root), "success": ok, "error": err})
            exists = ok
        attachment["local_path"] = posix_rel(target, crawler_root)
        attachment["local_path_exists"] = bool(exists)
        attachment["repair_status"] = "verified" if exists else "failed"
        attachment_success += int(exists)
    repaired["images"] = image_rows
    repaired["attachments"] = attachment_rows
    return repaired, image_success, attachment_success


def _repair_webpage_if_needed(record: dict, manifest_row: dict, crawler_root: Path, raw_html_dir: Path, adapter: CrawlerAdapter, logs: dict[str, list[dict]]) -> tuple[dict, str]:
    repaired = deepcopy(record)
    raw_check = check_raw_html(record, crawler_root, raw_html_dir)
    raw_path = Path(raw_check["raw_html_resolved_path"]) if raw_check["raw_html_resolved_path"] else None
    html = load_html(record, raw_path)
    needs_fetch = not html or any(x in manifest_row.get("missing_types", []) for x in ["raw_html_missing", "raw_html_empty_file", "raw_html_invalid", "crawl_status_not_success", "http_status_not_200"])
    if needs_fetch and get_path(record, "url", ""):
        ok, fetched_html, err = adapter.fetch_html(get_path(record, "url", ""))
        logs["webpage_refetch_log"].append({"doc_id": get_path(record, "doc_id", ""), "url": get_path(record, "url", ""), "success": ok, "error": err})
        if ok and fetched_html:
            target = raw_html_dir / f"repaired_{get_path(record, 'doc_id', '') or manifest_row.get('manifest_id')}.html"
            rel = adapter.save_raw_html(fetched_html, target)
            set_path(repaired, "crawl.raw_html_path", rel)
            set_path(repaired, "crawl.crawl_status", "success")
            set_path(repaired, "crawl.http_status", 200)
            html = fetched_html
        elif err:
            set_path(repaired, "crawl.repair_error", err)
    return repaired, html


def run_repair(manifest_path: Path, output_dir: Path, output_raw_dir: Path, crawler_root: Path, raw_html_dir: Path) -> dict:
    if not manifest_path.exists():
        raise FileNotFoundError(f"未找到 repair manifest：{manifest_path}")
    manifest = _read_manifest(manifest_path)
    by_file_line = defaultdict(dict)
    for row in manifest:
        by_file_line[str(Path(row["jsonl_file"]).resolve())][int(row["line_no"])] = row
    adapter = CrawlerAdapter(crawler_root)
    logs = {"repair_errors": [], "image_repair_log": [], "attachment_repair_log": [], "webpage_refetch_log": []}
    results, failed, manual = [], [], []
    copied_files = {}
    output_raw_dir.mkdir(parents=True, exist_ok=True)
    for jsonl_file_str, line_map in by_file_line.items():
        source_path = Path(jsonl_file_str)
        output_path = output_raw_dir / f"{source_path.stem}_repaired.jsonl"
        repaired_records = []
        for line_no, record in read_jsonl(source_path):
            row = line_map.get(line_no)
            if not row:
                repaired_records.append(record)
                continue
            try:
                repaired, html = _repair_webpage_if_needed(record, row, crawler_root, raw_html_dir, adapter, logs)
                repaired, image_ok, attachment_ok = _repair_assets(repaired, html, crawler_root, adapter, logs)
                still_failed = [a for a in (repaired.get("images") or []) + (repaired.get("attachments") or []) if isinstance(a, dict) and a.get("repair_status") == "failed"]
                status = "success" if not still_failed else "partial_failed"
                set_path(repaired, "crawl.raw_repair_status", status)
                result = {"manifest_id": row["manifest_id"], "doc_id": row.get("doc_id", ""), "repair_status": status, "image_verified_count": image_ok, "attachment_verified_count": attachment_ok}
                results.append(result)
                if still_failed:
                    failed.append({**row, "repair_status": status, "failed_asset_count": len(still_failed)})
                    manual.append({**row, "manual_review_reason": "failed_after_repair"})
                repaired_records.append(repaired)
            except Exception as exc:
                logs["repair_errors"].append({"jsonl_file": jsonl_file_str, "line_no": line_no, "error": str(exc)})
                failed.append({**row, "repair_status": "error", "error": str(exc)})
                manual.append({**row, "manual_review_reason": "repair_exception", "error": str(exc)})
                repaired_records.append(record)
        copied_files[str(output_path)] = write_jsonl(output_path, repaired_records)
    write_repair_outputs(output_dir, results, failed, manual, logs, copied_files)
    return {"manifest_records": len(manifest), "failed_after_repair": len(failed), "output_raw_dir": str(output_raw_dir)}
