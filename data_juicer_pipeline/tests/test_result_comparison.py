import json

from result_comparison import compare_files


def _write(path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def test_comparison_reports_changes_without_metadata_loss(tmp_path):
    before = tmp_path / "before.jsonl"
    after = tmp_path / "after.jsonl"
    reports = tmp_path / "reports"
    rows = [{"doc_id": "1", "title": "题", "url": "https://x/1", "text": "题\n首页\n正文"}]
    _write(before, rows)
    _write(after, [{**rows[0], "text": "题\n正文"}])
    result = compare_files(before, after, reports)
    assert result.summary["changed_document_count"] == 1
    assert result.summary["metadata_changed_count"] == 0
    assert result.details[0]["removed_lines"] == ["首页"]
    assert (reports / "document_details.csv").exists()


def test_comparison_aligns_by_doc_id_and_duplicate_occurrence(tmp_path):
    before = tmp_path / "before.jsonl"
    after = tmp_path / "after.jsonl"
    reports = tmp_path / "reports"
    _write(before, [
        {"doc_id": "dup", "title": "A", "url": "https://x/a", "text": "A1"},
        {"doc_id": "dup", "title": "B", "url": "https://x/b", "text": "B1"},
        {"doc_id": "unique", "title": "C", "url": "https://x/c", "text": "C1"},
    ])
    _write(after, [
        {"doc_id": "unique", "title": "C", "url": "https://x/c", "text": "C1"},
        {"doc_id": "dup", "title": "A", "url": "https://x/a", "text": "A1"},
        {"doc_id": "dup", "title": "B", "url": "https://x/b", "text": "B1"},
    ])
    result = compare_files(before, after, reports)
    assert result.summary["aligned_document_count"] == 3
    assert result.summary["duplicate_doc_id_count_before"] == 1
    assert result.summary["metadata_changed_count"] == 0
