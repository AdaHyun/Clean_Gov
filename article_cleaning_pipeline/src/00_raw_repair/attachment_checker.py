from __future__ import annotations

from pathlib import Path

from .common import ASSET_EXTENSIONS, OK_STATUSES, get_path, parse_attachment_links, resolve_local_path


def check_attachments(record: dict, html: str, crawler_root: Path, attachment_root_dir: Path) -> dict:
    html_attachments = parse_attachment_links(html, get_path(record, "url", "") or "")
    json_attachments = get_path(record, "attachments", []) or []
    local_missing = 0
    bad_status = 0
    incomplete = 0
    suffix_bad = 0
    details = []
    for idx, att in enumerate(json_attachments, 1):
        local_path = att.get("local_path", "") if isinstance(att, dict) else ""
        resolved = resolve_local_path(local_path, crawler_root, attachment_root_dir=attachment_root_dir)
        status = str(att.get("download_status", "")) if isinstance(att, dict) else ""
        ext = ("." + str(att.get("file_ext") or Path(local_path).suffix.lstrip(".") or att.get("file_type", "")).lower().strip(".")).strip(".")
        ext_with_dot = "." + ext if ext else ""
        exists = bool(resolved and resolved.exists() and resolved.stat().st_size > 0)
        local_missing += int(bool(local_path) and not exists)
        bad_status += int(status not in OK_STATUSES)
        incomplete += int(not isinstance(att, dict) or not att.get("url") or not local_path)
        suffix_bad += int(bool(ext_with_dot) and ext_with_dot not in ASSET_EXTENSIONS)
        details.append({"index": idx, "url": att.get("url", "") if isinstance(att, dict) else "", "local_path": local_path, "exists": exists, "download_status": status, "file_ext": ext_with_dot})
    issues = []
    if html_attachments and not json_attachments:
        issues.append("html_has_attachments_but_json_attachments_empty")
    if len(html_attachments) != len(json_attachments):
        issues.append("attachment_count_mismatch")
    if local_missing:
        issues.append("attachment_local_file_missing")
    if bad_status:
        issues.append("attachment_bad_download_status")
    if incomplete:
        issues.append("attachment_field_incomplete")
    if suffix_bad:
        issues.append("attachment_suffix_bad")
    return {
        "html_attachment_count": len(html_attachments),
        "json_attachment_count": len(json_attachments),
        "attachment_local_missing_count": local_missing,
        "attachment_bad_status_count": bad_status,
        "attachment_field_incomplete_count": incomplete,
        "attachment_suffix_bad_count": suffix_bad,
        "attachment_issues": issues,
        "html_attachments": html_attachments,
        "attachment_details": details,
    }
