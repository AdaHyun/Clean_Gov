import json
import threading
from http.server import ThreadingHTTPServer

import requests

from src.callback_server import make_handler

from .helpers import make_settings


def start_server(settings):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(settings))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}/callback"


def test_callback_http_validation(tmp_path):
    settings = make_settings(tmp_path)
    server, url = start_server(settings)
    try:
        assert requests.post(url + "?token=wrong", json={"RequestId": "r1"}, timeout=2).status_code == 403
        assert requests.post(url + "?token=test-token", data="{", timeout=2).status_code == 400
        assert requests.post(url + "?token=test-token", json={"Code": 0}, timeout=2).status_code == 400
    finally:
        server.shutdown()
        server.server_close()
    callback_log = (settings.output_root / "logs" / "callback.log").read_text(encoding="utf-8")
    assert settings.callback_token not in callback_log
    assert "invalid_token" in callback_log


def test_callback_creates_mirror_output(tmp_path):
    from src.reporting import save_task

    settings = make_settings(tmp_path)
    task = {
        "document_id": "d1", "request_id": "r1", "batch_id": "b1", "batch_name": "test",
        "source_file_name": "文件.pdf", "source_extension": ".pdf",
        "source_relative_path": "机构/栏目/文件.pdf", "source_absolute_path": "/input/机构/栏目/文件.pdf",
        "source_size_bytes": 10, "source_mtime": "2026-01-01T00:00:00+08:00", "file_sha256": "abc",
        "output_layout": "mirror", "status": "waiting_callback", "submitted_at": "2026-01-01T00:00:00+08:00",
    }
    save_task(settings.output_root, task)
    server, url = start_server(settings)
    try:
        response = requests.post(
            url + "?token=test-token",
            json={"RequestId": "r1", "Code": 0, "Message": "success", "Result": {"md_content": "# 标题\n\n正文"}},
            timeout=3,
        )
        assert response.status_code == 200
    finally:
        server.shutdown()
        server.server_close()
    task_after = json.loads((settings.output_root / "batches" / "b1" / "tasks" / "r1.json").read_text(encoding="utf-8"))
    mirror = settings.output_root / task_after["mirror_document_dir"]
    assert task_after["status"] == "callback_success"
    assert mirror.relative_to(settings.output_root).as_posix() == "documents/机构/栏目/文件.pdf"
    assert (mirror / "raw.md").read_text(encoding="utf-8") == "# 标题\n\n正文"
    assert (mirror / "content.md").read_text(encoding="utf-8").endswith("\n")
    assert (mirror / "metadata.json").is_file()
    assert (mirror / "quality.json").is_file()


def test_request_layout_keeps_only_request_audit_layer(tmp_path):
    from src.reporting import save_task

    settings = make_settings(tmp_path)
    save_task(settings.output_root, {
        "request_id": "r2", "batch_id": "b2", "source_relative_path": "flat.pdf",
        "source_file_name": "flat.pdf", "source_extension": ".pdf", "source_size_bytes": 1,
        "output_layout": "request", "status": "waiting_callback", "submitted_at": "2026-01-01T00:00:00+08:00",
    })
    server, url = start_server(settings)
    try:
        response = requests.post(
            url + "?token=test-token",
            json={"RequestId": "r2", "Code": 0, "Result": {"md_content": "ok"}},
            timeout=3,
        )
        assert response.status_code == 200
    finally:
        server.shutdown()
        server.server_close()
    assert (settings.output_root / "requests" / "r2" / "callback_response.json").is_file()
    assert not (settings.output_root / "documents").exists()


def test_unexpected_callback_save_error_returns_500_without_logging_secret(
    tmp_path, monkeypatch
):
    settings = make_settings(tmp_path)
    secret = settings.callback_token

    def fail_save(*args, **kwargs):
        raise RuntimeError(f"internal failure {secret}")

    monkeypatch.setattr("src.callback_server.save_callback", fail_save)
    server, url = start_server(settings)
    try:
        response = requests.post(
            url + f"?token={secret}",
            json={"RequestId": "request-500", "Code": 0},
            timeout=3,
        )
        assert response.status_code == 500
    finally:
        server.shutdown()
        server.server_close()

    callback_log = (settings.output_root / "logs" / "callback.log").read_text(
        encoding="utf-8"
    )
    assert "request-500" in callback_log
    assert "RuntimeError" in callback_log
    assert secret not in callback_log
