from pathlib import Path

import pytest

from src.client import CompanyAsyncClient
from src.diagnostics import audit_docx
from src.file_utils import MIME_TYPES
from src.reporting import save_task

from .helpers import make_settings, write_ooxml


REAL_DOCX_NAMES = (
    "2013年食品安全国家标准项目计划(征求意见稿）.docx",
    "职业卫生监督协管巡查工作登记表.docx",
    "慢性阻塞性肺疾病患者常规随访服务记录表.docx",
)


def _payload(tmp_path, filename):
    path = tmp_path / filename
    write_ooxml(path)
    client = CompanyAsyncClient(make_settings(tmp_path))
    return client.build_payload(path, request_id="docx-test")[1]


def _assert_docx_payload(tmp_path, filename):
    payload = _payload(tmp_path, filename)
    assert payload["FileType"] == "docx"
    assert payload["FileName"] == filename
    assert payload["FileName"].endswith(".docx")
    assert payload["FileUrl"].startswith(
        "data:application/vnd.openxmlformats-officedocument.wordprocessingml.document;base64,"
    )


def test_docx_suffix_preserved_short_filename(tmp_path):
    _assert_docx_payload(tmp_path, "短中文.docx")


def test_docx_suffix_preserved_long_chinese_filename(tmp_path):
    filename = "超长中文文件名" * 6 + ".docx"
    assert 64 < len(filename.encode("utf-8")) < 255
    _assert_docx_payload(tmp_path, filename)


def test_docx_suffix_preserved_boundary_63(tmp_path):
    _assert_docx_payload(tmp_path, "a" * 63 + ".docx")


def test_docx_suffix_preserved_boundary_64(tmp_path):
    _assert_docx_payload(tmp_path, "a" * 64 + ".docx")


def test_docx_suffix_preserved_boundary_65(tmp_path):
    _assert_docx_payload(tmp_path, "a" * 65 + ".docx")


def test_docx_payload_file_type(tmp_path):
    payload = _payload(tmp_path, "长中文文件名用于验证类型.docx")
    assert payload["FileType"] == "docx"
    assert MIME_TYPES[".docx"] == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


@pytest.mark.parametrize("filename", REAL_DOCX_NAMES)
def test_docx_payload_filename(tmp_path, filename):
    _assert_docx_payload(tmp_path, filename)


@pytest.mark.parametrize(
    "filename",
    (
        "long-english-filename-" * 8 + ".docx",
        "中文mixed-English文件.docx",
        "带中文括号（测试）.docx",
        "with-ascii-parentheses(test).docx",
        "带 空 格 filename.docx",
        " leading-space.docx",
    ),
)
def test_docx_payload_preserves_special_filename(tmp_path, filename):
    _assert_docx_payload(tmp_path, filename)


@pytest.mark.parametrize("filename", ("a.doc", "a.pdf", "a.xlsx", "a.pptx"))
def test_other_document_suffixes_are_distinct(filename):
    assert Path(filename).suffix.lower() in {".doc", ".pdf", ".xlsx", ".pptx"}
    assert not (
        filename.endswith(".xlsx") and Path(filename).suffix.lower() == ".xls"
    )


def test_audit_docx_flags_real_names_by_utf8_length(tmp_path):
    root = tmp_path / "data"
    for number, filename in enumerate(REAL_DOCX_NAMES):
        save_task(root, {
            "request_id": f"r{number}",
            "batch_id": "gov",
            "source_file_name": filename,
            "source_relative_path": filename,
            "source_extension": ".docx",
            "status": "callback_failed",
        })
    result = audit_docx(root, "gov", filename_limit=64)
    assert result["docx_total"] == 3
    assert result["docx_status_counts"] == {"callback_failed": 3}
    risky_names = {
        item["filename"]
        for item in result["suspected_filename_truncation"]["examples"]
    }
    assert risky_names == set(REAL_DOCX_NAMES)
