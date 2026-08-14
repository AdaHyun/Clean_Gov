import json

from jsonl_io import iter_jsonl
from native_validation import validate_native_output


def _write(path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def test_validation_retains_removed_exact_duplicate(tmp_path):
    before = tmp_path / "before.jsonl"
    after = tmp_path / "after.jsonl"
    report = tmp_path / "report.json"
    removed = tmp_path / "removed.jsonl"
    _write(before, [
        {"doc_id": "a", "text": "相同 正文"},
        {"doc_id": "b", "text": "相同\n正文"},
    ])
    _write(after, [{"doc_id": "a", "text": "相同 正文"}])
    summary = validate_native_output(
        before,
        after,
        report,
        removed,
        quarantine_reason="exact_duplicate_after_cleaning",
        stage="03_exact_dedup",
    )
    assert summary["removed_document_count"] == 1
    assert summary["removed_with_exact_survivor_count"] == 1
    row = [value for _, value in iter_jsonl(removed)][0]
    assert row["doc_id"] == "b"
    assert row["duplicate_survivor_doc_id"] == "a"
    assert row["quarantine_reason"] == "exact_duplicate_after_cleaning"
    assert row["quarantine_stage"] == "03_exact_dedup"
    assert summary["before_character_count"] == 10
    assert summary["after_character_count"] == 5
