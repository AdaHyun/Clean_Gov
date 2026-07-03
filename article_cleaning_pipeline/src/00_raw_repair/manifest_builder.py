from __future__ import annotations

from pathlib import Path

from .common import default_asset_dir, get_path, posix_rel


def build_manifest_row(index: int, jsonl_file: Path, line_no: int, record: dict, raw_check: dict, image_check: dict, attachment_check: dict, crawler_root: Path | None = None) -> dict | None:
    missing_types = []
    crawl_status = get_path(record, "crawl.crawl_status", "")
    http_status = get_path(record, "crawl.http_status", None)
    body_text = get_path(record, "content.body_text", "") or ""
    body_html = get_path(record, "content.body_html", "") or ""
    if not get_path(record, "doc_id", ""):
        missing_types.append("doc_id_missing")
    if not get_path(record, "url", ""):
        missing_types.append("url_missing")
    if crawl_status != "success":
        missing_types.append("crawl_status_not_success")
    if http_status not in (200, "200"):
        missing_types.append("http_status_not_200")
    if not body_text.strip():
        missing_types.append("body_text_empty")
    if not body_html.strip():
        missing_types.append("body_html_empty")
    if not raw_check["raw_html_path"]:
        missing_types.append("raw_html_path_empty")
    if not raw_check["raw_html_exists"]:
        missing_types.append("raw_html_missing")
    elif not raw_check["raw_html_nonempty"]:
        missing_types.append("raw_html_empty_file")
    elif not raw_check["raw_html_valid_html"]:
        missing_types.append("raw_html_invalid")
    missing_types.extend(image_check["image_issues"])
    missing_types.extend(attachment_check["attachment_issues"])
    if not missing_types:
        return None
    action = "manual_review"
    if raw_check["raw_html_exists"] or body_html:
        action = "parse_html_and_download_missing_assets"
    if any(x in missing_types for x in ["raw_html_missing", "body_html_empty", "crawl_status_not_success", "http_status_not_200"]):
        action = "refetch_webpage_then_parse_assets"
    priority = "high" if any(x in missing_types for x in ["raw_html_missing", "body_text_empty", "html_has_images_but_json_images_empty", "html_has_attachments_but_json_attachments_empty"]) else "medium"
    return {
        "manifest_id": f"repair_{index:06d}",
        "jsonl_file": str(jsonl_file),
        "line_no": line_no,
        "doc_id": get_path(record, "doc_id", ""),
        "url": get_path(record, "url", ""),
        "title": get_path(record, "title", ""),
        "site_name": get_path(record, "source.site_name", ""),
        "site_domain": get_path(record, "source.site_domain", ""),
        "channel_name": get_path(record, "source.channel_name", ""),
        "parser_type": get_path(record, "crawl.parser_type", ""),
        "crawler_name": get_path(record, "crawl.crawler_name", ""),
        "crawl_status": crawl_status,
        "http_status": http_status,
        "raw_html_path": raw_check["raw_html_path"],
        "raw_html_exists": raw_check["raw_html_exists"],
        "raw_html_path_repairable": raw_check["raw_html_path_repairable"],
        "raw_html_resolved_path": raw_check["raw_html_resolved_path"],
        "check_status": "failed",
        "missing_types": sorted(set(missing_types)),
        "repair_reason": "; ".join(sorted(set(missing_types))),
        "html_img_count": image_check["html_img_count"],
        "json_images_count": image_check["json_images_count"],
        "image_local_missing_count": image_check["image_local_missing_count"],
        "html_attachment_count": attachment_check["html_attachment_count"],
        "json_attachment_count": attachment_check["json_attachment_count"],
        "attachment_local_missing_count": attachment_check["attachment_local_missing_count"],
        "expected_image_dir": posix_rel(default_asset_dir(record, crawler_root, "image"), crawler_root) if crawler_root else "",
        "expected_attachment_dir": posix_rel(default_asset_dir(record, crawler_root, "attachment"), crawler_root) if crawler_root else "",
        "repair_action": action,
        "priority": priority,
        "manual_review_required": action == "manual_review",
    }
