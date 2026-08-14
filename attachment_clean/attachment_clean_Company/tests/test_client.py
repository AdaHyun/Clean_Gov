from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from src.client import CompanyApiError, CompanyAsyncClient, append_callback_url
from src.config import Settings

from .helpers import minimal_pdf


class Response:
    status_code = 200
    text = ""

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


class Session:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def settings(tmp_path: Path, auth_mode: str = "none") -> Settings:
    return Settings(
        api_url="http://10.62.32.53:8899/PDF-Parser",
        query_url="http://10.62.32.53:8899/Query",
        output_root=tmp_path,
        auth_mode=auth_mode,
        parse_mode="auto",
        url_type=2,
        output_mode="markdown",
        timeout_seconds=3,
        max_retries=0,
        max_file_size_mb=100,
        supported_extensions=(".pdf", ".doc", ".docx"),
        query_request_id_field="RequestID",
        callback_bind_host="127.0.0.1",
        callback_bind_port=8800,
        max_callback_body_mb=200,
        callback_url="http://10.0.0.8:8800/callback?token=a&x=1",
        callback_token="a",
        access_key="",
        access_key_header="X-Access-Key",
        access_key_prefix="",
        aihub_access_key_id="",
        aihub_access_key_secret="",
        aihub_product_code="",
    )


def test_submit_base64_and_callback_encoding(tmp_path):
    file_path = tmp_path / "测试.pdf"
    file_path.write_bytes(minimal_pdf())
    session = Session(Response({"RequestId": "r1", "Status": "200", "Message": "success"}))
    client = CompanyAsyncClient(settings(tmp_path), session=session)
    result, payload = client.submit_file(file_path)
    assert result.body["Status"] == "200"
    sent_url = session.calls[0][0][0]
    assert parse_qs(urlsplit(sent_url).query)["CallbackUrl"] == [settings(tmp_path).callback_url]
    assert payload["RequestId"] == result.request_id
    assert payload["FileUrl"].startswith("data:application/pdf;base64,")
    assert "BASE64省略" in client.payload_summary(payload)["FileUrl"]


def test_query_uses_documented_request_id_casing(tmp_path):
    session = Session(Response({"code": 1, "Message": "queued"}))
    client = CompanyAsyncClient(settings(tmp_path), session=session)
    client.query("task-1")
    assert session.calls[0][1]["json"] == {"RequestID": "task-1"}


def test_append_callback_preserves_existing_query():
    value = append_callback_url("http://x/PDF-Parser?a=1", "http://callback/x?a=2&b=3")
    query = parse_qs(urlsplit(value).query)
    assert query["a"] == ["1"]
    assert query["CallbackUrl"] == ["http://callback/x?a=2&b=3"]


def test_submit_read_timeout_is_not_retried(tmp_path):
    file = tmp_path / "a.pdf"
    file.write_bytes(minimal_pdf())
    session = Session(requests.ReadTimeout("slow"))
    client = CompanyAsyncClient(settings(tmp_path), session=session, sleep=lambda _: None)
    with pytest.raises(CompanyApiError) as error:
        client.submit_file(file)
    assert error.value.submission_unknown is True
    assert len(session.calls) == 1


def test_submit_connection_drop_is_unknown_and_not_retried(tmp_path):
    file = tmp_path / "a.pdf"
    file.write_bytes(minimal_pdf())
    session = Session(requests.ConnectionError("remote closed"))
    client = CompanyAsyncClient(settings(tmp_path), session=session, sleep=lambda _: None)
    with pytest.raises(CompanyApiError) as error:
        client.submit_file(file)
    assert error.value.submission_unknown is True
    assert error.value.error_type == "connection_error_unknown"
    assert len(session.calls) == 1


def test_all_trial_extensions_have_mime():
    from src.file_utils import MIME_TYPES

    expected = {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".txt", ".rtf"}
    assert expected <= MIME_TYPES.keys()


def test_build_payload_rejects_html_disguised_as_pdf(tmp_path):
    file_path = tmp_path / "fake.pdf"
    file_path.write_text("<!DOCTYPE html><html><title>DSpace</title></html>", encoding="utf-8")
    client = CompanyAsyncClient(settings(tmp_path), session=Session(Response({})))
    with pytest.raises(ValueError, match="html_disguised_as_document"):
        client.build_payload(file_path)
