"""Write risk copies; quarantine is not deletion."""

from __future__ import annotations

from pathlib import Path

from jsonl_io import iter_jsonl, write_jsonl_record


QUARANTINE_FLAGS = (
    "boilerplate_only",
    "body_missing",
    "list_page_contamination",
    "attachment_only",
    "empty_after_clean",
    "high_removal_risk",
    "metadata_changed",
    "table_content_changed",
    "attachment_link_removed",
    "protected_block_restore_failed",
)


def write_quarantine(output_path: Path, details: list[dict[str, object]], quarantine_dir: Path) -> dict[str, int]:
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    handles = {flag: (quarantine_dir / f"{flag}.jsonl").open("w", encoding="utf-8") for flag in QUARANTINE_FLAGS}
    counts = {flag: 0 for flag in QUARANTINE_FLAGS}
    try:
        for index, (_, record) in enumerate(iter_jsonl(output_path)):
            flags = set(details[index].get("risk_flags", [])) if index < len(details) else set()
            for flag in QUARANTINE_FLAGS:
                if flag in flags:
                    write_jsonl_record(handles[flag], record)
                    counts[flag] += 1
    finally:
        for handle in handles.values():
            handle.close()
    return counts
