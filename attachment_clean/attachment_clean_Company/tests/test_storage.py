import json

from src.reporting import save_task
from src.storage import safe_request_id, save_callback
from src.task_index import connect, rebuild_index


def test_callback_saves_raw_result_and_markdown(tmp_path):
    payload = {
        "RequestId": "r/1",
        "Code": 0,
        "Result": {"md_content": "# 结果", "detailed_info": []},
    }
    request_id, directory = save_callback(tmp_path, payload)
    assert request_id.startswith("r_1--")
    assert (directory / "raw_content.md").read_text(encoding="utf-8") == "# 结果"
    assert (directory / "content.md").read_text(encoding="utf-8") == "# 结果\n"
    saved = json.loads((directory / "callback_response.json").read_text(encoding="utf-8"))
    assert saved["payload"]["Code"] == 0


def test_repeated_callback_is_idempotent_and_counted(tmp_path):
    payload = {"RequestId": "r1", "Code": 0, "Result": {"md_content": "ok"}}
    save_callback(tmp_path, payload)
    _, directory = save_callback(tmp_path, payload)
    saved = json.loads((directory / "callback_response.json").read_text(encoding="utf-8"))
    assert saved["callback_count"] == 2
    assert (directory / "events" / "callback_0001.json").is_file()
    assert (directory / "events" / "callback_0002.json").is_file()


def test_failed_callback_cannot_overwrite_existing_success(tmp_path):
    save_task(tmp_path, {
        "request_id": "r1",
        "batch_id": "b1",
        "source_relative_path": "a.pdf",
        "source_extension": ".pdf",
        "output_layout": "request",
        "status": "waiting_callback",
    })
    save_callback(tmp_path, {
        "RequestId": "r1", "Code": 0,
        "Result": {"md_content": "good result"},
    })
    _, directory = save_callback(tmp_path, {
        "RequestId": "r1", "Code": 500, "Message": "late failure",
        "Result": {"md_content": ""},
    })
    assert (directory / "content.md").read_text(encoding="utf-8") == "good result\n"
    canonical = json.loads((directory / "callback_response.json").read_text(encoding="utf-8"))
    latest = json.loads((directory / "latest_callback_response.json").read_text(encoding="utf-8"))
    state = json.loads((directory / "callback_state.json").read_text(encoding="utf-8"))
    assert canonical["payload"]["Code"] == 0
    assert latest["payload"]["Code"] == 500
    assert state["callback_count"] == 2
    assert state["has_success"] is True
    task = json.loads(
        (tmp_path / "batches" / "b1" / "tasks" / "r1.json").read_text(
            encoding="utf-8"
        )
    )
    assert task["status"] == "callback_success"
    assert task["callback_count"] == 2


def test_success_not_overwritten_by_empty_callback(tmp_path):
    save_task(tmp_path, {
        "request_id": "empty-late",
        "batch_id": "b1",
        "source_relative_path": "a.pdf",
        "source_extension": ".pdf",
        "output_layout": "request",
        "status": "waiting_callback",
    })
    save_callback(tmp_path, {
        "RequestId": "empty-late", "Code": 0,
        "Result": {"md_content": "stable"},
    })
    _, directory = save_callback(tmp_path, {
        "RequestId": "empty-late", "Code": 0,
        "Result": {"md_content": ""},
    })
    task = json.loads(
        (tmp_path / "batches/b1/tasks/empty-late.json").read_text(encoding="utf-8")
    )
    assert task["status"] == "callback_success"
    assert (directory / "content.md").read_text(encoding="utf-8") == "stable\n"


def test_empty_success_payload_becomes_empty_result_failure(tmp_path):
    save_task(tmp_path, {
        "request_id": "empty-first",
        "batch_id": "b1",
        "source_relative_path": "a.pdf",
        "source_extension": ".pdf",
        "output_layout": "request",
        "status": "waiting_callback",
    })
    save_callback(tmp_path, {
        "RequestId": "empty-first", "Code": 0,
        "Result": {"md_content": ""},
    })
    task = json.loads(
        (tmp_path / "batches/b1/tasks/empty-first.json").read_text(encoding="utf-8")
    )
    assert task["status"] == "callback_failed"
    assert task["error_type"] == "empty_result"


def test_duplicate_success_callback_is_idempotent_for_task(tmp_path):
    save_task(tmp_path, {
        "request_id": "repeat-success",
        "batch_id": "b1",
        "source_relative_path": "a.pdf",
        "source_extension": ".pdf",
        "output_layout": "request",
        "status": "waiting_callback",
    })
    payload = {
        "RequestId": "repeat-success", "Code": 0,
        "Result": {"md_content": "same"},
    }
    save_callback(tmp_path, payload)
    save_callback(tmp_path, payload)
    task = json.loads(
        (tmp_path / "batches/b1/tasks/repeat-success.json").read_text(encoding="utf-8")
    )
    assert task["status"] == "callback_success"
    assert task["callback_count"] == 2


def test_unsafe_request_ids_cannot_normalize_to_same_directory():
    assert safe_request_id("normal-id") == "normal-id"
    assert safe_request_id("a/b") != safe_request_id("a?b")


def test_failed_reparse_does_not_overwrite_successful_mirror(tmp_path):
    source = "机构/栏目/文章/附件.pdf"
    for request_id in ("success", "failure"):
        save_task(tmp_path, {
            "request_id": request_id,
            "batch_id": request_id,
            "source_relative_path": source,
            "source_file_name": "附件.pdf",
            "source_extension": ".pdf",
            "output_layout": "mirror",
            "status": "waiting_callback",
        })
    save_callback(tmp_path, {
        "RequestId": "success",
        "Code": 0,
        "Result": {"md_content": "stable content"},
    })
    success_task = json.loads(
        (tmp_path / "batches" / "success" / "tasks" / "success.json").read_text(
            encoding="utf-8"
        )
    )
    mirror = tmp_path / success_task["mirror_document_dir"]
    save_callback(tmp_path, {
        "RequestId": "failure",
        "Code": 500,
        "Result": {"md_content": ""},
    })
    assert (mirror / "content.md").read_text(encoding="utf-8") == "stable content\n"
    failed_task = json.loads(
        (tmp_path / "batches" / "failure" / "tasks" / "failure.json").read_text(
            encoding="utf-8"
        )
    )
    assert failed_task["status"] == "callback_failed"
    assert failed_task["mirror_update_skipped"] == "callback_not_success"


def test_callback_files_survive_sqlite_sync_failure_and_rebuild(tmp_path, monkeypatch):
    save_task(tmp_path, {
        "request_id": "sqlite-failure",
        "batch_id": "b1",
        "source_relative_path": "a.pdf",
        "source_extension": ".pdf",
        "output_layout": "request",
        "status": "waiting_callback",
    })

    def fail_index(*args, **kwargs):
        raise RuntimeError("simulated sqlite failure")

    with monkeypatch.context() as patch:
        patch.setattr("src.reporting.upsert_task", fail_index)
        _, directory = save_callback(tmp_path, {
            "RequestId": "sqlite-failure",
            "Code": 0,
            "Result": {"md_content": "durable"},
        })

    assert (directory / "content.md").read_text(encoding="utf-8") == "durable\n"
    task_path = tmp_path / "batches" / "b1" / "tasks" / "sqlite-failure.json"
    assert json.loads(task_path.read_text(encoding="utf-8"))["status"] == "callback_success"
    assert (directory / "postprocess_warning.json").is_file()
    rebuild_index(tmp_path)
    db = connect(tmp_path)
    try:
        status = db.execute(
            "SELECT status FROM tasks WHERE request_id='sqlite-failure'"
        ).fetchone()[0]
    finally:
        db.close()
    assert status == "callback_success"


def test_callback_partial_failure_can_be_replayed(tmp_path, monkeypatch):
    save_task(tmp_path, {
        "request_id": "replay",
        "batch_id": "b1",
        "source_relative_path": "a.pdf",
        "source_extension": ".pdf",
        "output_layout": "request",
        "status": "waiting_callback",
    })
    payload = {
        "RequestId": "replay",
        "Code": 0,
        "Result": {"md_content": "replay-safe"},
    }

    with monkeypatch.context() as patch:
        patch.setattr(
            "src.reporting.upsert_task",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("first sync failed")),
        )
        _, directory = save_callback(tmp_path, payload)

    assert (directory / "content.md").read_text(encoding="utf-8") == "replay-safe\n"
    assert (directory / "postprocess_warning.json").is_file()
    save_callback(tmp_path, payload)
    task = json.loads(
        (tmp_path / "batches/b1/tasks/replay.json").read_text(encoding="utf-8")
    )
    assert task["status"] == "callback_success"
    db = connect(tmp_path)
    try:
        assert db.execute(
            "SELECT status FROM tasks WHERE request_id='replay'"
        ).fetchone()[0] == "callback_success"
    finally:
        db.close()
