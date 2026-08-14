import json

from jsonl_io import iter_jsonl
from native_pipeline import _combine_outputs


def _write_rows(path, rows):
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_combine_outputs_normalizes_web_and_attachment_columns(tmp_path):
    web = tmp_path / "web.jsonl"
    attachment = tmp_path / "attachment.jsonl"
    output = tmp_path / "combined.jsonl"
    _write_rows(web, [{"doc_id": "web", "text": "网页正文", "flag": True}])
    _write_rows(
        attachment,
        [
            {
                "doc_id": "attachment",
                "text": "附件正文",
                "flag": True,
                "parser_is_empty": False,
                "parser_warnings": "[]",
            }
        ],
    )

    assert _combine_outputs(
        [("web_normal", web), ("attachment_text", attachment)],
        output,
    ) == 2
    rows = [row for _, row in iter_jsonl(output)]
    assert set(rows[0]) == set(rows[1])
    assert rows[0]["parser_is_empty"] is False
    assert rows[0]["parser_warnings"] == ""
    assert rows[0]["native_pipeline_lane"] == "web_normal"
