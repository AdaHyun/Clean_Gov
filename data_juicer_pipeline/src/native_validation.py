"""Validate native Data-Juicer lane outputs and retain removed records."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

from jsonl_io import iter_jsonl, write_jsonl_record


BASE64_IMAGE_RE = re.compile(r"data:image/[^;]+;base64,", re.IGNORECASE)
INTERNAL_IMAGE_RE = re.compile(r"https?://100\.100\.33\.62", re.IGNORECASE)


def _normalized_hash(text: str) -> str:
    normalized = re.sub(r"\s+", "", unicodedata.normalize("NFC", text))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def validate_native_output(
    before_path: Path,
    after_path: Path,
    report_path: Path,
    removed_path: Path,
    *,
    quarantine_reason: str = "removed_by_native_data_juicer",
    stage: str = "native_data_juicer",
) -> dict[str, Any]:
    """Compare IDs and content-level safety signals without loading full corpora."""
    after_ids: Counter[str] = Counter()
    after_hash_survivor: dict[str, str] = {}
    after_text_metrics: dict[str, tuple[str, int]] = {}
    after_count = 0
    after_character_count = 0
    empty_after = 0
    base64_after = 0
    internal_image_after = 0

    for _, row in iter_jsonl(after_path):
        after_count += 1
        doc_id = str(row.get("doc_id") or "")
        after_ids[doc_id] += 1
        text = row.get("text") if isinstance(row.get("text"), str) else ""
        after_character_count += len(text)
        after_text_metrics[doc_id] = (hashlib.sha256(text.encode("utf-8")).hexdigest(), len(text))
        empty_after += int(not text.strip())
        base64_after += int(bool(BASE64_IMAGE_RE.search(text)))
        internal_image_after += int(bool(INTERNAL_IMAGE_RE.search(text)))
        if text.strip():
            after_hash_survivor.setdefault(_normalized_hash(text), doc_id)

    duplicate_doc_id_group_count_after = sum(1 for value in after_ids.values() if value > 1)

    before_count = 0
    before_character_count = 0
    changed_document_count = 0
    missing_count = 0
    matched_count = 0
    missing_exact_survivor_count = 0
    removed_path.parent.mkdir(parents=True, exist_ok=True)
    with removed_path.open("w", encoding="utf-8") as removed_handle:
        for _, row in iter_jsonl(before_path):
            before_count += 1
            doc_id = str(row.get("doc_id") or "")
            text = row.get("text") if isinstance(row.get("text"), str) else ""
            before_character_count += len(text)
            if after_ids[doc_id] > 0:
                after_ids[doc_id] -= 1
                matched_count += 1
                after_metric = after_text_metrics.get(doc_id)
                if after_metric and after_metric[0] != hashlib.sha256(text.encode("utf-8")).hexdigest():
                    changed_document_count += 1
                continue
            missing_count += 1
            survivor = after_hash_survivor.get(_normalized_hash(text)) if text.strip() else None
            missing_exact_survivor_count += int(bool(survivor))
            quarantined = dict(row)
            quarantined["quarantine_reason"] = quarantine_reason
            quarantined["quarantine_stage"] = stage
            quarantined["duplicate_survivor_doc_id"] = survivor or ""
            write_jsonl_record(removed_handle, quarantined)

    summary: dict[str, Any] = {
        "before_path": str(before_path.resolve()),
        "after_path": str(after_path.resolve()),
        "before_document_count": before_count,
        "after_document_count": after_count,
        "matched_document_count": matched_count,
        "changed_document_count": changed_document_count,
        "removed_document_count": missing_count,
        "removed_with_exact_survivor_count": missing_exact_survivor_count,
        "new_or_duplicate_output_id_count": sum(after_ids.values()),
        "duplicate_doc_id_group_count_after": duplicate_doc_id_group_count_after,
        "empty_text_count_after": empty_after,
        "base64_image_document_count_after": base64_after,
        "internal_parser_image_document_count_after": internal_image_after,
        "before_character_count": before_character_count,
        "after_character_count": after_character_count,
        "character_delta": after_character_count - before_character_count,
        "quarantine_reason": quarantine_reason,
        "stage": stage,
        "removed_records_path": str(removed_path.resolve()),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
