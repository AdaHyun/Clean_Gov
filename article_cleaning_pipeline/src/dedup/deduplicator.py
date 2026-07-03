from __future__ import annotations

from collections import defaultdict

from src.cleaning.text_normalizer import normalize_chinese_web_text
from src.utils import get_path, set_path, sha1_text


def group_by_clean_text_hash(records: list[dict]) -> dict[str, list[dict]]:
    groups = defaultdict(list)
    for rec in records:
        key = sha1_text(normalize_chinese_web_text(get_path(rec, "content.clean_text", "")))
        if not key or len(get_path(rec, "content.clean_text", "")) < 50:
            key = "url:" + (get_path(rec, "canonical_url", "") or get_path(rec, "url", "") or get_path(rec, "doc_id", ""))
        groups[key].append(rec)
    return groups


def choose_canonical(items: list[dict]) -> dict:
    return sorted(items, key=lambda r: (len(get_path(r, "content.clean_text", "") or ""), get_path(r, "dates.publish_date", "")), reverse=True)[0]
