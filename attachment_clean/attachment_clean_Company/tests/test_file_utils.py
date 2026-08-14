import base64
import os

import pytest

from src.file_utils import (
    encode_file_base64,
    mask_url_token,
    scan_files,
    sha256_file,
    validate_document,
)

from .helpers import minimal_pdf, write_ooxml


def test_flat_and_recursive_scan(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"a")
    nested = tmp_path / "中文目录"
    nested.mkdir()
    (nested / "b.docx").write_bytes(b"b")
    assert [item.name for item in scan_files(tmp_path, False, ["pdf", "docx"])] == ["a.pdf"]
    assert [item.name for item in scan_files(tmp_path, True, ["pdf", "docx"])] == ["a.pdf", "b.docx"]


def test_sha256_streaming_result(tmp_path):
    path = tmp_path / "a.txt"
    path.write_bytes(b"abc")
    assert sha256_file(path) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_token_masking():
    masked = mask_url_token("http://x/callback?token=1234567890abcdef&x=1")
    assert "1234567890abcdef" not in masked
    assert "1234****cdef" in masked


def test_document_validation_rejects_html_blank_and_truncated_files(tmp_path):
    fake_pdf = tmp_path / "fake.pdf"
    fake_pdf.write_text("<!DOCTYPE html><html><title>DSpace</title></html>", encoding="utf-8")
    assert validate_document(fake_pdf).code == "html_disguised_as_document"

    blank = tmp_path / "blank.txt"
    blank.write_bytes(b"\xef\xbb\xbf \r\n\t\x00")
    assert validate_document(blank).code == "blank_text"

    truncated = tmp_path / "truncated.pdf"
    truncated.write_bytes(b"%PDF-1.4\nmissing end marker")
    assert validate_document(truncated).code == "truncated_pdf"


def test_document_validation_accepts_pdf_and_checks_ooxml_structure(tmp_path):
    pdf = tmp_path / "valid.pdf"
    pdf.write_bytes(minimal_pdf())
    assert validate_document(pdf).valid is True

    docx = tmp_path / "valid.docx"
    write_ooxml(docx)
    result = validate_document(docx)
    assert result.valid is True
    assert result.detected_type == "docx"

    wrong = tmp_path / "wrong.docx"
    wrong.write_bytes(minimal_pdf())
    assert validate_document(wrong).code == "content_type_mismatch"


def test_streaming_base64_matches_standard_encoding(tmp_path):
    path = tmp_path / "a.txt"
    value = (b"abc123" * 1000) + b"tail"
    path.write_bytes(value)
    assert encode_file_base64(path, chunk_size=12) == base64.b64encode(value).decode("ascii")


def test_scan_does_not_follow_symlinks_and_records_evidence(tmp_path):
    target = tmp_path / "target.pdf"
    target.write_bytes(minimal_pdf())
    link = tmp_path / "linked.pdf"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("当前环境不允许创建符号链接")
    issues = []
    files = scan_files(tmp_path, True, [".pdf"], issues=issues)
    assert files == [target]
    assert any(
        issue["path"] == "linked.pdf" and issue["issue_type"] == "symlink_skipped"
        for issue in issues
    )
