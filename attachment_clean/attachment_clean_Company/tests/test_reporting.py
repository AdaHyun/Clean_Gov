import json
from datetime import datetime, timedelta, timezone

from src.reporting import (
    rebuild_batch_outputs,
    refresh_callback_timeouts,
    refresh_submission_timeouts,
    save_task,
)
from src.storage import save_callback
from src.task_index import count_active

from .helpers import make_settings


def test_waiting_callback_becomes_timeout_but_remains_traceable(tmp_path):
    settings = make_settings(tmp_path)
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    task = {
        "request_id": "r1", "batch_id": "b1", "source_relative_path": "a.pdf",
        "source_extension": ".pdf", "status": "waiting_callback", "submitted_at": old,
    }
    save_task(settings.output_root, task)
    assert refresh_callback_timeouts(settings.output_root, 30, "b1") == 1
    saved = json.loads((settings.output_root / "batches" / "b1" / "tasks" / "r1.json").read_text(encoding="utf-8"))
    assert saved["status"] == "callback_timeout"
    assert saved["error_type"] == "callback_timeout"


def test_waiting_callback_is_not_written_to_success_file(tmp_path):
    settings = make_settings(tmp_path)
    save_task(settings.output_root, {
        "request_id": "waiting", "batch_id": "b1",
        "source_relative_path": "waiting.pdf", "source_extension": ".pdf",
        "status": "waiting_callback",
    })
    save_task(settings.output_root, {
        "request_id": "success", "batch_id": "b1",
        "source_relative_path": "success.pdf", "source_extension": ".pdf",
        "status": "callback_success",
    })
    rebuild_batch_outputs(settings.output_root, "b1")
    logs = settings.output_root / "batch_logs"
    assert (logs / "batch_b1_success.txt").read_text(encoding="utf-8") == "success.pdf\n"
    assert (logs / "batch_b1_waiting.txt").read_text(encoding="utf-8") == "waiting.pdf\n"


def test_submit_unknown_eventually_releases_slot_without_resubmit(tmp_path):
    settings = make_settings(tmp_path)
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    save_task(settings.output_root, {
        "request_id": "unknown",
        "batch_id": "b1",
        "source_relative_path": "unknown.pdf",
        "source_extension": ".pdf",
        "status": "submit_unknown",
        "submitted_at": old,
    })
    assert refresh_callback_timeouts(settings.output_root, 30, "b1") == 0
    result = refresh_submission_timeouts(settings.output_root, 30, 30, "b1")
    assert result["submit_unknown_timeout"] == 1
    saved = json.loads(
        (settings.output_root / "batches" / "b1" / "tasks" / "unknown.json").read_text(
            encoding="utf-8"
        )
    )
    assert saved["status"] == "submit_unknown_timeout"
    assert saved["manual_review_required"] is True
    assert count_active(settings.output_root, "b1") == 0


def test_stale_submitting_becomes_submit_unknown(tmp_path):
    settings = make_settings(tmp_path)
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    save_task(settings.output_root, {
        "request_id": "stale",
        "batch_id": "b1",
        "source_relative_path": "stale.pdf",
        "source_extension": ".pdf",
        "status": "submitting",
        "submission_started_at": old,
    })
    result = refresh_submission_timeouts(settings.output_root, 30, 30, "b1")
    assert result["stale_submitting"] == 1
    saved = json.loads(
        (settings.output_root / "batches/b1/tasks/stale.json").read_text(encoding="utf-8")
    )
    assert saved["status"] == "submit_unknown"
    assert saved["error_type"] == "stale_submitting"
    assert count_active(settings.output_root, "b1") == 1


def test_acquire_crash_pending_json_eventually_becomes_unknown(tmp_path):
    from src.task_index import acquire_submission_slot, connect

    settings = make_settings(tmp_path)
    save_task(settings.output_root, {
        "request_id": "crash",
        "batch_id": "b1",
        "source_relative_path": "crash.pdf",
        "source_extension": ".pdf",
        "status": "pending",
    })
    assert acquire_submission_slot(
        settings.output_root, "crash", "b1", 1, "h200"
    )[0]
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    db = connect(settings.output_root)
    try:
        db.execute(
            "UPDATE tasks SET submission_started_at=?, updated_at=? WHERE request_id='crash'",
            (old, old),
        )
    finally:
        db.close()
    result = refresh_submission_timeouts(settings.output_root, 30, 30, "b1")
    assert result["stale_submitting"] == 1
    saved = json.loads(
        (settings.output_root / "batches/b1/tasks/crash.json").read_text(encoding="utf-8")
    )
    assert saved["status"] == "submit_unknown"


def test_late_callback_recovers_submit_unknown_timeout(tmp_path):
    settings = make_settings(tmp_path)
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    save_task(settings.output_root, {
        "request_id": "late-unknown",
        "batch_id": "b1",
        "source_relative_path": "late.pdf",
        "source_extension": ".pdf",
        "output_layout": "request",
        "status": "submit_unknown",
        "submitted_at": old,
    })
    refresh_submission_timeouts(settings.output_root, 30, 30, "b1")
    save_callback(settings.output_root, {
        "RequestId": "late-unknown",
        "Code": 0,
        "Result": {"md_content": "late success"},
    })
    saved = json.loads(
        (settings.output_root / "batches/b1/tasks/late-unknown.json").read_text(encoding="utf-8")
    )
    assert saved["status"] == "callback_success"


def test_late_failed_callback_after_callback_timeout(tmp_path):
    settings = make_settings(tmp_path)
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    save_task(settings.output_root, {
        "request_id": "late-failed",
        "batch_id": "b1",
        "source_relative_path": "late.pdf",
        "source_extension": ".pdf",
        "output_layout": "request",
        "status": "waiting_callback",
        "submitted_at": old,
    })
    refresh_callback_timeouts(settings.output_root, 30, "b1")
    save_callback(settings.output_root, {
        "RequestId": "late-failed",
        "Code": 10002,
        "Message": "parser failed",
        "Result": {"md_content": ""},
    })
    saved = json.loads(
        (settings.output_root / "batches/b1/tasks/late-failed.json").read_text(encoding="utf-8")
    )
    assert saved["status"] == "callback_failed"
