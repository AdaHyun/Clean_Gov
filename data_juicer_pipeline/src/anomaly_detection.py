"""Risk tagging; flags never delete a document."""

from __future__ import annotations

import re

from protected_blocks import markdown_table_blocks


FILE_REF_RE = re.compile(r"\.(?:pdf|docx?|xlsx?|pptx?|rar|zip)(?:\b|\)|\?|$)", re.I)
DATE_LINE_RE = re.compile(r"^\s*(?:\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?|\d{4}年\d{1,2}月)\s*$")
NAV_TERMS = {"首页", "机构", "新闻", "信息", "服务", "互动", "专题", "政务公开", "政务服务", "互动交流", "机构职能"}
FOOTER_RE = re.compile(r"主办单位|承办单位|ICP备|公网安备|网站地图|新媒体矩阵|联系电话|邮编")


def detect_anomalies(text: str, *, title: str = "", before_clean_text: str | None = None) -> list[str]:
    flags: set[str] = set()
    stripped = text.strip()
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if not stripped:
        flags.update(("empty_after_clean", "body_missing"))
    if "data:image/" in text and ";base64," in text:
        flags.add("base64_image_residue")
    if re.search(r"!\[[^\]]*\]\([^)]+\)", text):
        flags.add("markdown_image_residue")
    if re.search(r"&(?:nbsp|amp|lt|gt|#\d+);", text, re.I):
        flags.add("html_entity_residue")
    if len(lines) <= 1 and stripped:
        flags.add("single_line_text")
    nav_hits = sum(line in NAV_TERMS for line in lines)
    footer_hits = sum(bool(FOOTER_RE.search(line)) for line in lines)
    if nav_hits >= 3:
        flags.add("navigation_block_detected")
    if footer_hits >= 2:
        flags.add("footer_block_detected")
    date_lines = sum(bool(DATE_LINE_RE.fullmatch(line)) for line in lines)
    short_lines = sum(len(line) <= 35 for line in lines)
    long_lines = sum(len(line) >= 80 for line in lines)
    list_title = title.strip() in {"工作动态", "通知公告", "政务动态", "新闻中心", "信息公开"}
    breadcrumb = any("当前位置" in line or "您现在所在位置" in line for line in lines)
    if len(lines) >= 6 and date_lines >= 3 and short_lines / len(lines) > 0.7 and long_lines == 0 and (list_title or breadcrumb):
        flags.update(("list_page_contamination", "wrong_page_type", "needs_recrawl"))
    file_refs = len(FILE_REF_RE.findall(text))
    attachment_hint = bool(re.search(r"请(?:点击)?查看附件|^\s*附件[：:]", text, re.M))
    if file_refs and attachment_hint:
        flags.add("has_attachment_reference")
    if file_refs and attachment_hint and len(stripped) < 500 and long_lines == 0:
        flags.update(("attachment_only", "needs_attachment_merge"))
    if title and sum(line == title.strip() for line in lines[:10]) > 1:
        flags.add("duplicate_title")
    if markdown_table_blocks(text):
        flags.add("table_content_protected")
    body_chars = sum(len(line) for line in lines if line not in NAV_TERMS and not FOOTER_RE.search(line))
    if stripped and body_chars < 40 and (nav_hits >= 3 or footer_hits >= 2):
        flags.update(("boilerplate_only", "body_missing"))
    if before_clean_text is not None and before_clean_text and not stripped:
        flags.add("empty_after_clean")
    return sorted(flags)


def has_severe_page_anomaly(flags: list[str]) -> bool:
    return bool({"list_page_contamination", "wrong_page_type", "boilerplate_only", "body_missing", "empty_after_clean"} & set(flags))
