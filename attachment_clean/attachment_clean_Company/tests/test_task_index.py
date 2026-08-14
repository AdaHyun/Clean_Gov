import json
import threading
from datetime import datetime, timedelta, timezone

from src.reporting import (
    find_success_by_sha256,
    refresh_callback_timeouts,
    save_task,
)
from src.storage import atomic_write_json, save_callback
from src.task_index import (
    acquire_submission_slot,
    connect,
    count_active,
    index_path,
    reconcile_active_tasks,
    rebuild_index,
    upsert_task,
)


def make_task(request_id, batch_id, status="pending", **extra):
    task = {
        "request_id": request_id,
        "batch_id": batch_id,
        "batch_name": batch_id,
        "parser_pool_id": "h200",
        "parser_api_url": "http://parser/PDF-Parser",
        "source_relative_path": f"{request_id}.pdf",
        "source_absolute_path": f"/input/{request_id}.pdf",
        "source_size_bytes": 10,
        "source_mtime": "2026-01-01T00:00:00+00:00",
        "source_mtime_ns": 1,
        "file_sha256": request_id,
        "status": status,
        "submission_started_at": "",
        "submitted_at": "",
        "callback_at": "",
    }
    task.update(extra)
    return task


def test_batch_limits_and_active_counts_are_independent(tmp_path):
    root = tmp_path / "data"
    for number in range(10):
        save_task(root, make_task(f"a{number}", "A", "waiting_callback"))
    save_task(root, make_task("a-next", "A"))
    save_task(root, make_task("b-next", "B"))
    assert count_active(root, "A") == 10
    assert count_active(root, "B") == 0
    assert acquire_submission_slot(root, "a-next", "A", 10, "h200") == (False, 10)
    assert acquire_submission_slot(root, "b-next", "B", 10, "h200") == (True, 0)


def test_active_count_is_not_global(tmp_path):
    root = tmp_path / "data"
    for number in range(7):
        save_task(root, make_task(f"a{number}", "A", "waiting_callback"))
    for number in range(9):
        save_task(root, make_task(f"b{number}", "B", "waiting_callback"))
    assert count_active(root, "A") == 7
    assert count_active(root, "B") == 9


def test_same_batch_last_slot_cannot_be_oversold(tmp_path):
    root = tmp_path / "data"
    save_task(root, make_task("r1", "A"))
    save_task(root, make_task("r2", "A"))
    barrier = threading.Barrier(2)
    results = []

    def acquire(request_id):
        barrier.wait()
        results.append(acquire_submission_slot(root, request_id, "A", 1, "h200")[0])

    threads = [threading.Thread(target=acquire, args=(rid,)) for rid in ("r1", "r2")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == [False, True]
    assert count_active(root, "A") == 1


def test_different_batches_can_each_acquire_their_own_slot(tmp_path):
    root = tmp_path / "data"
    save_task(root, make_task("a", "A"))
    save_task(root, make_task("b", "B"))
    barrier = threading.Barrier(2)
    results = []

    def acquire(request_id, batch_id):
        barrier.wait()
        results.append(acquire_submission_slot(root, request_id, batch_id, 1, "h200")[0])

    threads = [
        threading.Thread(target=acquire, args=("a", "A")),
        threading.Thread(target=acquire, args=("b", "B")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == [True, True]
    assert count_active(root, "A") == count_active(root, "B") == 1


def test_optional_service_limit_is_separate_from_batch_limit(tmp_path):
    root = tmp_path / "data"
    save_task(root, make_task("active", "A", "waiting_callback"))
    save_task(root, make_task("h200-next", "B"))
    legacy = make_task("legacy-next", "C")
    legacy["parser_pool_id"] = "legacy"
    save_task(root, legacy)
    assert acquire_submission_slot(root, "h200-next", "B", 10, "h200", 1) == (False, 0)
    assert acquire_submission_slot(root, "legacy-next", "C", 10, "legacy", 1) == (True, 0)


def test_callback_success_releases_batch_slot_and_late_callback_is_allowed(tmp_path):
    root = tmp_path / "data"
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    save_task(root, make_task("done", "A", "waiting_callback", submitted_at=old))
    assert count_active(root, "A") == 1
    save_callback(root, {
        "RequestId": "done",
        "Code": 0,
        "Result": {"md_content": "complete"},
    })
    assert count_active(root, "A") == 0

    save_task(root, make_task("late", "A", "waiting_callback", submitted_at=old))
    assert refresh_callback_timeouts(root, 30, "A") == 1
    save_callback(root, {
        "RequestId": "late",
        "Code": 0,
        "Result": {"md_content": "late but valid"},
    })
    with open(root / "batches" / "A" / "tasks" / "late.json", encoding="utf-8") as handle:
        assert json.load(handle)["status"] == "callback_success"


def _write_json_status(root, task, status):
    changed = dict(task)
    changed["status"] = status
    path = root / "batches" / task["batch_id"] / "tasks" / f"{task['request_id']}.json"
    atomic_write_json(path, changed)
    return path


def test_reconcile_waiting_callback_to_success(tmp_path):
    root = tmp_path / "data"
    task = make_task("success", "A", "waiting_callback")
    save_task(root, task)
    _write_json_status(root, task, "callback_success")
    result = reconcile_active_tasks(root, batch_id="A")
    assert result["checked_active"] == result["json_reads"] == result["repaired"] == 1
    assert count_active(root, "A") == 0
    db = connect(root)
    try:
        assert db.execute(
            "SELECT status FROM tasks WHERE request_id='success'"
        ).fetchone()[0] == "callback_success"
    finally:
        db.close()


def test_reconcile_waiting_callback_to_failed(tmp_path):
    root = tmp_path / "data"
    task = make_task("failed", "A", "waiting_callback")
    save_task(root, task)
    _write_json_status(root, task, "callback_failed")
    assert reconcile_active_tasks(root, batch_id="A")["repaired"] == 1
    assert count_active(root, "A") == 0


def test_reconcile_does_not_revert_submitting_to_pending(tmp_path):
    root = tmp_path / "data"
    task = make_task("reserved", "A", "pending")
    save_task(root, task)
    assert acquire_submission_slot(root, "reserved", "A", 1, "h200")[0]
    result = reconcile_active_tasks(root, batch_id="A")
    assert result["repaired"] == 0
    db = connect(root)
    try:
        assert db.execute(
            "SELECT status FROM tasks WHERE request_id='reserved'"
        ).fetchone()[0] == "submitting"
    finally:
        db.close()


def test_save_task_raises_when_realtime_index_update_rejected(tmp_path):
    root = tmp_path / "data"
    save_task(root, make_task("same", "A", "waiting_callback"))
    conflicting = make_task("same", "B", "callback_success")
    import pytest

    with pytest.raises(RuntimeError, match="SQLite 索引更新被拒绝"):
        save_task(root, conflicting)


def test_sha_and_timeout_hot_paths_do_not_iter_history(tmp_path, monkeypatch):
    root = tmp_path / "data"
    for batch_number in range(10):
        (root / "batches" / f"old-{batch_number}" / "tasks").mkdir(parents=True)
    db = connect(root)
    try:
        db.execute("BEGIN")
        for number in range(10_000):
            task = make_task(f"r{number}", f"old-{number % 10}", "submit_failed")
            path = root / "batches" / task["batch_id"] / "tasks" / f"r{number}.json"
            path.write_text(json.dumps(task), encoding="utf-8")
            upsert_task(root, task, path, connection=db)
        db.execute("COMMIT")
    finally:
        db.close()

    success = make_task("success", "old", "callback_success", file_sha256="same")
    save_task(root, success)
    content = root / "requests" / "success" / "content.md"
    content.parent.mkdir(parents=True)
    content.write_text("ok\n", encoding="utf-8")
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    save_task(root, make_task("timeout", "current", "waiting_callback", submitted_at=old))
    save_task(root, make_task("next", "current"))
    for number in range(10):
        save_task(root, make_task(f"perf-active-{number}", "perf", "waiting_callback"))
    save_task(root, make_task("callback-perf", "callback-perf", "waiting_callback"))

    def forbidden(*args, **kwargs):
        raise AssertionError("热路径不得调用 iter_tasks")

    monkeypatch.setattr("src.reporting.iter_tasks", forbidden)
    reads = []
    original_read_text = type(root).read_text

    def counted_read_text(path, *args, **kwargs):
        if "batches" in path.parts and "tasks" in path.parts:
            reads.append(path)
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(type(root), "read_text", counted_read_text)
    reconcile = reconcile_active_tasks(root, batch_id="perf")
    assert reconcile["checked_active"] == reconcile["json_reads"] == 10
    assert len(reads) == 10
    assert find_success_by_sha256(root, "same")["request_id"] == "success"
    assert refresh_callback_timeouts(root, 30, "current") == 1
    assert acquire_submission_slot(root, "next", "current", 1, "h200") == (True, 0)
    save_callback(root, {
        "RequestId": "callback-perf",
        "Code": 0,
        "Result": {"md_content": "fast without report rebuild"},
    })
    assert (root / "state" / "reporting_dirty" / "batches" / "callback-perf.dirty").is_file()


def test_callback_timeout_query_uses_composite_index(tmp_path):
    root = tmp_path / "data"
    save_task(root, make_task("r", "A", "waiting_callback", submitted_at="2026-01-01T00:00:00+00:00"))
    db = connect(root)
    try:
        plan = " ".join(
            str(value)
            for row in db.execute(
                "EXPLAIN QUERY PLAN SELECT request_id FROM tasks WHERE batch_id = ? "
                "AND status = 'waiting_callback' AND submitted_at <> '' AND submitted_at < ?",
                ("A", "2026-02-01T00:00:00+00:00"),
            ).fetchall()
            for value in row
        )
    finally:
        db.close()
    assert "idx_tasks_batch_status_submitted" in plan


def test_rebuild_is_idempotent_and_database_can_be_deleted(tmp_path):
    root = tmp_path / "data"
    task = make_task("legacy", "old", "callback_success")
    task.pop("parser_pool_id")
    task.pop("submission_started_at")
    task.pop("source_mtime_ns")
    path = root / "batches" / "old" / "tasks" / "legacy.json"
    atomic_write_json(path, task)
    first = rebuild_index(root)
    second = rebuild_index(root)
    assert first["imported_tasks"] == second["imported_tasks"] == 1
    assert first["missing_fields"] >= 3
    db = connect(root)
    try:
        row = db.execute(
            "SELECT parser_pool_id, status FROM tasks WHERE request_id='legacy'"
        ).fetchone()
        assert tuple(row) == ("legacy_unknown", "callback_success")
    finally:
        db.close()

    index_path(root).unlink()
    rebuilt = rebuild_index(root)
    assert rebuilt["imported_tasks"] == 1
    assert count_active(root, "old") == 0


def test_rebuild_reports_request_id_conflict_without_overwrite(tmp_path):
    root = tmp_path / "data"
    first = make_task("same", "A", "callback_success")
    second = make_task("same", "B", "callback_failed")
    atomic_write_json(root / "batches/A/tasks/same.json", first)
    atomic_write_json(root / "batches/B/tasks/same.json", second)
    result = rebuild_index(root)
    assert result["request_id_conflicts"] == 1
    db = connect(root)
    try:
        row = db.execute("SELECT batch_id, status FROM tasks WHERE request_id='same'").fetchone()
        assert tuple(row) == ("A", "callback_success")
    finally:
        db.close()
