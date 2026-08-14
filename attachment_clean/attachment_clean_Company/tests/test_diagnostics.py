import json

from src.diagnostics import check_state
from src.reporting import save_task
from src.storage import atomic_write_json
from src.task_index import connect


def test_check_state_reports_mismatches_without_repair(tmp_path):
    root = tmp_path / "data"
    save_task(root, {
        "request_id": "active-stale",
        "batch_id": "b1",
        "source_relative_path": "a.pdf",
        "source_extension": ".pdf",
        "status": "waiting_callback",
    })
    path = root / "batches/b1/tasks/active-stale.json"
    task = json.loads(path.read_text(encoding="utf-8"))
    task["status"] = "callback_success"
    atomic_write_json(path, task)

    result = check_state(root, "b1")
    assert result["sqlite_total_tasks"] == 1
    assert result["task_json_total_tasks"] == 1
    assert result["sqlite_active_count"] == 1
    assert result["sqlite_active_json_terminal"]["count"] == 1
    assert result["callback_success_content_missing"]["count"] == 1

    db = connect(root)
    try:
        assert db.execute(
            "SELECT status FROM tasks WHERE request_id='active-stale'"
        ).fetchone()[0] == "waiting_callback"
    finally:
        db.close()


def test_check_state_reports_corrupt_missing_and_content_anomalies(tmp_path):
    root = tmp_path / "data"
    save_task(root, {
        "request_id": "content-but-failed",
        "batch_id": "b1",
        "source_relative_path": "a.pdf",
        "source_extension": ".pdf",
        "status": "callback_failed",
    })
    content = root / "requests/content-but-failed/content.md"
    content.parent.mkdir(parents=True)
    content.write_text("existing", encoding="utf-8")
    corrupt = root / "batches/b1/tasks/corrupt.json"
    corrupt.write_text("{", encoding="utf-8")

    result = check_state(root, "b1")
    assert result["json_corrupt"]["count"] == 1
    assert result["task_json_missing"]["count"] == 0
    assert result["content_exists_but_status_not_success"]["count"] == 1
