from __future__ import annotations

from src.utils import get_path


def to_datatrove_document(record: dict) -> dict | None:
    """Convert one cleaned article record to DataTrove's id/text/metadata contract."""
    text = get_path(record, "content.clean_text", "") or ""
    if not text:
        return None
    canonical_doc_id = get_path(record, "dedup.canonical_doc_id", "") or get_path(record, "doc_id", "")
    return {
        "id": canonical_doc_id,
        "text": text,
        "metadata": {
            "doc_id": get_path(record, "doc_id", ""),
            "canonical_doc_id": canonical_doc_id,
            "title": get_path(record, "title", ""),
            "url": get_path(record, "url", ""),
            "canonical_url": get_path(record, "canonical_url", ""),
            "site_name": get_path(record, "source.site_name", ""),
            "institution_code": get_path(record, "organization.institution_code", ""),
            "channel_name": get_path(record, "source.channel_name", ""),
            "standard_channel_name": get_path(record, "source.standard_channel_name", ""),
            "document_type": get_path(record, "classification.document_type", ""),
            "policy_category": get_path(record, "classification.policy_category", ""),
            "content_genre": get_path(record, "classification.content_genre", ""),
            "publish_date": get_path(record, "dates.publish_date", ""),
            "issue_date": get_path(record, "dates.issue_date", ""),
            "crawl_date": get_path(record, "dates.crawl_date", ""),
            "has_attachments": bool(get_path(record, "attachments", [])),
            "has_images": bool(get_path(record, "images", [])),
            "has_tables": bool(get_path(record, "content.tables", [])),
            "sensitive_risk_level": get_path(record, "privacy.sensitive_risk_level", "low"),
            "quality_label": get_path(record, "quality.quality_label", ""),
        },
    }


def export_datatrove_documents(records: list[dict]) -> list[dict]:
    return [doc for rec in records if (doc := to_datatrove_document(rec))]
