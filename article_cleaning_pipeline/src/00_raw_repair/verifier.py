from __future__ import annotations

from pathlib import Path

import pandas as pd

from .attachment_checker import check_attachments
from .common import get_path, load_html, read_jsonl, resolve_local_path, set_path, write_jsonl
from .image_checker import check_images
from .raw_html_checker import check_raw_html
from .report_writer import write_verify_outputs


def _load_manual_map(path: Path | None) -> list[dict]:
    if not path or not path.exists():
        return []
    if path.suffix.lower() == ".xlsx":
        return pd.read_excel(path).fillna("").to_dict("records")
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path).fillna("").to_dict("records")
    if path.suffix.lower() == ".json":
        return pd.read_json(path).fillna("").to_dict("records")
    return []


def _apply_manual_map(record: dict, mappings: list[dict], crawler_root: Path) -> tuple[dict, int, list[dict]]:
    doc_id = get_path(record, "doc_id", "")
    applied = 0
    errors = []
    if not mappings:
        return record, applied, errors
    for row in [m for m in mappings if str(m.get("doc_id", "")) == doc_id]:
        asset_type = str(row.get("asset_type", "")).strip()
        assets_key = "images" if asset_type == "image" else "attachments" if asset_type == "attachment" else ""
        if not assets_key:
            errors.append({"doc_id": doc_id, "error": "unknown_asset_type", "row": row})
            continue
        target_url = str(row.get("original_url", ""))
        target_name = str(row.get("asset_name", ""))
        manual_local_path = str(row.get("manual_local_path", ""))
        matched = False
        for asset in record.get(assets_key) or []:
            if not isinstance(asset, dict):
                continue
            if (target_url and asset.get("url") == target_url) or (target_name and (asset.get("name") == target_name or Path(str(asset.get("local_path", ""))).name == target_name)):
                p = Path(manual_local_path)
                resolved = p if p.is_absolute() else crawler_root / p
                asset["local_path"] = manual_local_path.replace("\\", "/")
                asset["local_path_exists"] = resolved.exists() and resolved.stat().st_size > 0
                asset["download_status"] = "manual_saved" if asset["local_path_exists"] else "failed"
                asset["repair_status"] = "verified" if asset["local_path_exists"] else "manual_map_missing_file"
                applied += int(asset["local_path_exists"])
                matched = True
        if not matched:
            errors.append({"doc_id": doc_id, "error": "manual_map_asset_not_matched", "row": row})
    return record, applied, errors


def _verify_assets_in_record(record: dict, crawler_root: Path, image_root_dir: Path, attachment_root_dir: Path) -> tuple[dict, int]:
    failures = 0
    for key, root in [("images", image_root_dir), ("attachments", attachment_root_dir)]:
        for asset in record.get(key) or []:
            if not isinstance(asset, dict):
                failures += 1
                continue
            resolved = resolve_local_path(asset.get("local_path", ""), crawler_root, image_root_dir=root if key == "images" else None, attachment_root_dir=root if key == "attachments" else None)
            exists = bool(resolved and resolved.exists() and resolved.stat().st_size > 0)
            asset["local_path_exists"] = exists
            if exists and str(asset.get("download_status", "")) in {"failed", "pending", "missing", "http_403", "http_404", ""}:
                asset["download_status"] = "manual_saved"
            asset["repair_status"] = "verified" if exists else "missing_after_verify"
            failures += int(not exists)
    return record, failures


def run_verify(raw_dir: Path, output_dir: Path, crawler_root: Path, raw_html_dir: Path, image_root_dir: Path, attachment_root_dir: Path, manual_asset_map: Path | None = None) -> dict:
    if not raw_dir.exists() or not list(raw_dir.glob("*.jsonl")):
        raise FileNotFoundError(f"未找到 repaired JSONL：{raw_dir}/*.jsonl")
    mappings = _load_manual_map(manual_asset_map)
    results, still_failed, manual = [], [], []
    map_errors = []
    updated_files = {}
    for jsonl_file in sorted(raw_dir.glob("*.jsonl")):
        updated_records = []
        for line_no, record in read_jsonl(jsonl_file):
            record, applied, errors = _apply_manual_map(record, mappings, crawler_root)
            map_errors.extend(errors)
            record, asset_failures = _verify_assets_in_record(record, crawler_root, image_root_dir, attachment_root_dir)
            raw_check = check_raw_html(record, crawler_root, raw_html_dir)
            raw_path = Path(raw_check["raw_html_resolved_path"]) if raw_check["raw_html_resolved_path"] else None
            html = load_html(record, raw_path)
            image_check = check_images(record, html, crawler_root, image_root_dir)
            attachment_check = check_attachments(record, html, crawler_root, attachment_root_dir)
            missing_types = []
            if not raw_check["raw_html_exists"] or not raw_check["raw_html_valid_html"]:
                missing_types.append("raw_html_not_verified")
            missing_types.extend(image_check["image_issues"])
            missing_types.extend(attachment_check["attachment_issues"])
            if asset_failures:
                missing_types.append("asset_local_file_missing_after_verify")
            status = "verified" if not missing_types else "still_failed"
            set_path(record, "crawl.raw_repair_verify_status", status)
            result = {"jsonl_file": str(jsonl_file), "line_no": line_no, "doc_id": get_path(record, "doc_id", ""), "verify_status": status, "missing_types": sorted(set(missing_types)), "manual_map_applied": applied}
            results.append(result)
            if status != "verified":
                still_failed.append(result)
                manual.append({**result, "manual_review_reason": "still_failed_after_verify"})
            updated_records.append(record)
        updated_files[str(jsonl_file)] = write_jsonl(jsonl_file, updated_records)
    write_verify_outputs(output_dir, results, still_failed, manual, {"manual_map_rows": len(mappings), "manual_map_errors": len(map_errors), "updated_raw_files": updated_files})
    return {"verified_records": len(results), "still_failed_after_verify": len(still_failed), "raw_dir": str(raw_dir)}
