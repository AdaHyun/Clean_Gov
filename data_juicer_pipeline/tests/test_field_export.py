import json

import pytest

from field_export import export_selected_fields, parse_field_spec


def _write_jsonl(path, records):
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_export_selects_renames_and_reads_nested_fields(tmp_path):
    source = tmp_path / "corpus.jsonl"
    target = tmp_path / "selected.jsonl"
    _write_jsonl(
        source,
        [
            {"doc_id": "d1", "title": "标题", "text": "正文", "stats": {"score": 0.8}},
            {"doc_id": "d2", "title": "标题2", "text": "正文2", "stats": {"score": 0.9}},
        ],
    )

    result = export_selected_fields(
        source,
        target,
        parse_field_spec("id=doc_id,title,text,score=stats.score"),
        progress_every=0,
    )

    assert result["input_document_count"] == result["output_document_count"] == 2
    assert _read_jsonl(target) == [
        {"id": "d1", "title": "标题", "text": "正文", "score": 0.8},
        {"id": "d2", "title": "标题2", "text": "正文2", "score": 0.9},
    ]
    assert target.with_name("selected.summary.json").is_file()


def test_export_null_policy_keeps_record_and_reports_missing(tmp_path):
    source = tmp_path / "corpus.jsonl"
    target = tmp_path / "selected.jsonl"
    _write_jsonl(source, [{"doc_id": "d1", "text": "正文"}])

    result = export_selected_fields(
        source,
        target,
        parse_field_spec("id=doc_id,title,text"),
        missing_policy="null",
        progress_every=0,
    )

    assert _read_jsonl(target) == [{"id": "d1", "title": None, "text": "正文"}]
    assert result["missing_field_counts"] == {"title": 1}


def test_export_error_policy_does_not_leave_partial_output(tmp_path):
    source = tmp_path / "corpus.jsonl"
    target = tmp_path / "selected.jsonl"
    _write_jsonl(source, [{"doc_id": "d1"}])

    with pytest.raises(KeyError):
        export_selected_fields(
            source,
            target,
            parse_field_spec("doc_id,text"),
            progress_every=0,
        )

    assert not target.exists()


def test_parse_field_spec_rejects_duplicate_output_names():
    with pytest.raises(ValueError, match="输出字段重复"):
        parse_field_spec("id=doc_id,id=source_doc_id")
