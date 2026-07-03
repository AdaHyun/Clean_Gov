from __future__ import annotations

import re

from src.cleaning.noise_cleaner import detect_noise_hits
from src.utils import get_path


def evaluate_quality(record: dict) -> tuple[str, list[str]]:
    text = get_path(record, "content.clean_text", "") or ""
    reasons = []
    if not text:
        reasons.append("empty_clean_text")
    if len(text) < 80:
        reasons.append("short_clean_text")
    if detect_noise_hits(text):
        reasons.append("residual_navigation_noise")
    if re.search(r"<[^>]+>", text):
        reasons.append("html_tag_residue")
    raw_len = get_path(record, "content.raw_text_length", 0) or len(get_path(record, "content.body_text", "") or "")
    if raw_len and len(text) / max(raw_len, 1) < 0.2:
        reasons.append("possible_truncation")
    if get_path(record, "dates.date_conflict", False):
        reasons.append("date_conflict")
    if get_path(record, "privacy.sensitive_risk_level", "low") == "high":
        reasons.append("high_sensitive_risk")
    if get_path(record, "dedup.duplicate_group_id", ""):
        reasons.append("duplicate_canonical")
    label = "clean"
    if reasons:
        label = "needs_review" if any(r in reasons for r in ["empty_clean_text", "short_clean_text", "high_sensitive_risk"]) else "partial_clean"
    if not text and not get_path(record, "url", ""):
        label = "drop_candidate"
    return label, reasons


quality_for = evaluate_quality
