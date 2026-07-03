from __future__ import annotations

from src.cleaning.text_normalizer import normalize_chinese_web_text
from src.utils import get_path, set_path

from .date_normalizer import parse_date_value
from .url_normalizer import canonical_url, site_domain


def normalize_metadata(record: dict, channel_map: dict | None = None, document_type_rules: dict | None = None, policy_category_map: dict | None = None) -> tuple[dict, list[dict]]:
    channel_map = channel_map or {}
    document_type_rules = document_type_rules or {}
    policy_category_map = policy_category_map or {}
    rec = dict(record)
    corrections = []
    url = get_path(rec, "url", "")
    domain = get_path(rec, "source.site_domain", "") or site_domain(url)
    set_path(rec, "canonical_url", canonical_url(url))
    set_path(rec, "source.site_domain", domain)
    set_path(rec, "source.site_url", get_path(rec, "source.site_url", "") or (f"https://{domain}/" if domain else ""))
    set_path(rec, "source.standard_channel_name", channel_map.get(get_path(rec, "source.channel_name", ""), get_path(rec, "source.channel_name", "")))
    set_path(rec, "dates.publish_date", parse_date_value(get_path(rec, "dates.publish_date", "") or get_path(rec, "raw.raw_date", "")))
    set_path(rec, "dates.raw_date", get_path(rec, "raw.raw_date", ""))
    set_path(rec, "dates.issue_date", parse_date_value(get_path(rec, "dates.issue_date", "")))
    set_path(rec, "dates.crawl_date", parse_date_value(get_path(rec, "dates.crawl_date", "")))
    set_path(rec, "dates.date_conflict", bool(get_path(rec, "dates.publish_date", "") and get_path(rec, "raw.raw_date", "") and parse_date_value(get_path(rec, "raw.raw_date", "")) != get_path(rec, "dates.publish_date", "")))
    source_department = get_path(rec, "organization.source_department", "")
    set_path(rec, "organization.issuing_department", source_department)
    set_path(rec, "organization.content_source", get_path(rec, "raw.raw_source", "") or source_department)
    set_path(rec, "organization.institution_code", domain.replace(".", "_") if domain else "unknown")
    doc_type = get_path(rec, "classification.document_type", "") or "其他"
    policy_category = get_path(rec, "classification.policy_category", "") or "未分类"
    set_path(rec, "classification.document_type", document_type_rules.get(doc_type, doc_type))
    set_path(rec, "classification.policy_category", policy_category_map.get(policy_category, policy_category))
    summary = get_path(rec, "content.summary", "") or normalize_chinese_web_text(get_path(rec, "content.body_text", ""))[:200]
    set_path(rec, "content.summary", summary)
    set_path(rec, "raw.raw_summary", get_path(rec, "raw.raw_summary", "") or summary)
    return rec, corrections
