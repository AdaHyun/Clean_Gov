from __future__ import annotations

import re
from collections import Counter

from src.utils import get_path


def classify_element(line: str) -> str:
    if re.match(r"^(第[一二三四五六七八九十百]+[章节条]|[一二三四五六七八九十]+[、.．])", line):
        return "heading" if len(line) < 80 else "policy_clause"
    if re.match(r"^附件[:：]", line):
        return "attachment_ref"
    if re.match(r"^(来源|发布时间|发布日期|索引号)[:：]", line):
        return "publish_metadata_line"
    if re.match(r"^\d+[、.．]", line):
        return "list_item"
    if re.search(r"\d{4}年\d{1,2}月\d{1,2}日$", line) and len(line) < 40:
        return "signature_date"
    return "paragraph"


def build_content_elements(record: dict) -> tuple[list[dict], dict]:
    doc_id = get_path(record, "doc_id", "")
    elements = []
    for i, line in enumerate([x.strip() for x in (get_path(record, "content.clean_text", "") or "").splitlines() if x.strip()], 1):
        typ = classify_element(line)
        elements.append({
            "doc_id": doc_id,
            "element_id": f"{doc_id}#e{i:04d}",
            "type": typ,
            "text": line,
            "order": i,
            "level": 1 if typ in ["heading", "policy_clause"] else 0,
            "source_html_tag": "",
            "quality_flags": [],
        })
    return elements, {"element_count": len(elements), "types": dict(Counter(e["type"] for e in elements))}


element_type = classify_element
