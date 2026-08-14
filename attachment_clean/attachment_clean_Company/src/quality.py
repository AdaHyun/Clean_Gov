"""只记录问题、不自动删除正文的基础 Markdown 质量检查。"""
from __future__ import annotations

import re
from typing import Any


def analyze_markdown(
    request_id: str,
    source_relative_path: str,
    raw: str,
    clean: str,
) -> dict[str, Any]:
    raw_lines = raw.splitlines()
    clean_lines = clean.splitlines()
    heading_count = sum(bool(re.match(r"^#{1,6}\s+", line)) for line in clean_lines)
    table_candidates = sum("|" in line for line in clean_lines)
    image_references = len(re.findall(r"!\[[^\]]*\]\([^)]*\)", clean))
    warnings: list[str] = []
    if not clean.strip():
        warnings.append("empty_content")
    replacement_count = clean.count("\ufffd")
    if replacement_count:
        warnings.append("replacement_character_detected")
    long_line_count = sum(len(line) > 2000 for line in clean_lines)
    if long_line_count:
        warnings.append("very_long_line_detected")
    html_residue = bool(re.search(r"</?[A-Za-z][^>]*>", clean))
    if html_residue:
        warnings.append("html_residue_detected")
    markdown_table = any(
        re.match(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?\s*$", line)
        for line in clean_lines
    )
    return {
        "request_id": request_id,
        "source_relative_path": source_relative_path,
        "is_empty": not bool(clean.strip()),
        "raw_character_count": len(raw),
        "clean_character_count": len(clean),
        "raw_line_count": len(raw_lines) if raw else 0,
        "clean_line_count": len(clean_lines) if clean else 0,
        "heading_count": heading_count,
        "table_candidate_count": table_candidates,
        "image_reference_count": image_references,
        "replacement_character_count": replacement_count,
        "nul_character_count": raw.count("\x00"),
        "very_long_line_count": long_line_count,
        "markdown_table_detected": markdown_table,
        "html_residue_detected": html_residue,
        "warnings": warnings,
    }

