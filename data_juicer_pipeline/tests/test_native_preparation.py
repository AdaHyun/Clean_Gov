import json
from pathlib import Path

from jsonl_io import iter_jsonl
from native_preparation import PreparationOptions, prepare_inputs


def _write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _attachment(root: Path, name: str, *, status: str, text: str, extension: str = ".pdf") -> None:
    directory = root / name
    directory.mkdir(parents=True)
    _write_json(
        directory / "metadata.json",
        {
            "document_id": name,
            "source_file_name": f"{name}{extension}",
            "source_extension": extension,
            "source_relative_path": f"机构/{name}{extension}",
            "file_sha256": name * 8,
            "status": status,
        },
    )
    _write_json(
        directory / "quality.json",
        {"is_empty": not bool(text), "warnings": []},
    )
    (directory / "content.md").write_text(text, encoding="utf-8")
    (directory / "raw.md").write_text("不得读取这一份", encoding="utf-8")


def test_prepare_routes_web_and_attachments_without_cleaning(tmp_path):
    web = tmp_path / "web.jsonl"
    rows = [
        {"doc_id": "collision", "title": "A", "url": "https://x/a", "publish_date": "2026-08-03", "text": "一行正文"},
        {"doc_id": "collision", "title": "B", "url": "https://x/b", "text": "第一行\n第二行\n第三行"},
        {"doc_id": "table", "title": "C", "url": "https://x/c", "text": "| 项目 | 数值 |\n| --- | --- |\n| A | 1 |"},
    ]
    web.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    attachments = tmp_path / "documents"
    _attachment(attachments, "good", status="callback_success", text="附件正文")
    _attachment(attachments, "table", status="callback_success", text="<table><tr><td>1</td></tr></table>", extension=".xlsx")
    _attachment(attachments, "failed", status="callback_failed", text="")

    output = tmp_path / "prepared"
    result = prepare_inputs(PreparationOptions(web, attachments, output))

    assert result.summary["counts"]["web_normal"] == 1
    assert result.summary["counts"]["web_multiline"] == 1
    assert result.summary["counts"]["web_table"] == 1
    assert result.summary["counts"]["attachment_text"] == 1
    assert result.summary["counts"]["attachment_table"] == 1
    assert result.summary["counts"]["reparse_required"] == 1
    normal = [row for _, row in iter_jsonl(result.lane_paths["web_normal"])][0]
    multiline = [row for _, row in iter_jsonl(result.lane_paths["web_multiline"])][0]
    table_web = [row for _, row in iter_jsonl(result.lane_paths["web_table"])][0]
    assert normal["text"] == "一行正文"
    assert "publish_date" not in normal
    assert json.loads(normal["metadata_json"])["publish_date"] == "2026-08-03"
    assert normal["parser_is_empty"] is False
    assert normal["parser_warnings"] == "[]"
    assert normal["doc_id"] != multiline["doc_id"]
    assert table_web["has_markdown_table"] is True
    attachment = [row for _, row in iter_jsonl(result.lane_paths["attachment_text"])][0]
    assert attachment["text"] == "附件正文"
    assert "不得读取" not in attachment["text"]


def test_prepare_dry_run_writes_nothing(tmp_path):
    web = tmp_path / "web.jsonl"
    web.write_text('{"doc_id":"1","title":"t","url":"https://x","text":"正文"}\n', encoding="utf-8")
    attachments = tmp_path / "documents"
    _attachment(attachments, "good", status="callback_success", text="附件正文")
    output = tmp_path / "not-created"
    result = prepare_inputs(PreparationOptions(web, attachments, output, write_outputs=False))
    assert result.summary["counts"]["web_normal"] == 1
    assert not output.exists()
