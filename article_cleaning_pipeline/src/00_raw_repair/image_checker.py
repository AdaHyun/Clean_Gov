from __future__ import annotations

from pathlib import Path

from .common import OK_STATUSES, get_path, parse_img_links, resolve_local_path


def check_images(record: dict, html: str, crawler_root: Path, image_root_dir: Path) -> dict:
    html_images = parse_img_links(html, get_path(record, "url", "") or "")
    json_images = get_path(record, "images", []) or []
    local_missing = 0
    bad_status = 0
    incomplete = 0
    details = []
    for idx, image in enumerate(json_images, 1):
        local_path = image.get("local_path", "") if isinstance(image, dict) else ""
        resolved = resolve_local_path(local_path, crawler_root, image_root_dir=image_root_dir)
        status = str(image.get("download_status", "")) if isinstance(image, dict) else ""
        exists = bool(resolved and resolved.exists() and resolved.stat().st_size > 0)
        local_missing += int(bool(local_path) and not exists)
        bad_status += int(status not in OK_STATUSES)
        incomplete += int(not isinstance(image, dict) or not image.get("url") or not local_path)
        details.append({"index": idx, "url": image.get("url", "") if isinstance(image, dict) else "", "local_path": local_path, "exists": exists, "download_status": status})
    issues = []
    if html_images and not json_images:
        issues.append("html_has_images_but_json_images_empty")
    if len(html_images) != len(json_images):
        issues.append("image_count_mismatch")
    if local_missing:
        issues.append("image_local_file_missing")
    if bad_status:
        issues.append("image_bad_download_status")
    if incomplete:
        issues.append("image_field_incomplete")
    return {
        "html_img_count": len(html_images),
        "json_images_count": len(json_images),
        "image_local_missing_count": local_missing,
        "image_bad_status_count": bad_status,
        "image_field_incomplete_count": incomplete,
        "image_issues": issues,
        "html_images": html_images,
        "image_details": details,
    }
