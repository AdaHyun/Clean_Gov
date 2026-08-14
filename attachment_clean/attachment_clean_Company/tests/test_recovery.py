import json

from src.recovery import build_remaining_from_batch, task_was_submitted
from src.storage import atomic_write_json


def test_task_was_submitted_is_conservative_for_crash_and_unknown():
    assert task_was_submitted({"status": "submitting"})
    assert task_was_submitted({"status": "submit_unknown"})
    assert not task_was_submitted({"status": "preflight_failed"})
    assert not task_was_submitted({
        "status": "submit_failed",
        "error_type": "connect_timeout",
        "submitted_at": "2026-01-01T00:00:00+00:00",
    })


def test_remaining_classification_and_hardlinks(tmp_path):
    input_dir = tmp_path / "input"
    batch_dir = tmp_path / "data" / "batches" / "old"
    output_dir = tmp_path / "remaining"
    input_dir.mkdir()
    paths = []
    for number in range(10):
        path = input_dir / f"f{number}.pdf"
        path.write_bytes(f"file-{number}".encode())
        paths.append(path)
    for number in range(6):
        stat = paths[number].stat()
        status = "waiting_callback" if number < 4 else "preflight_failed"
        task = {
            "request_id": f"r{number}",
            "batch_id": "old",
            "source_relative_path": paths[number].name,
            "source_size_bytes": stat.st_size,
            "source_mtime_ns": stat.st_mtime_ns,
            "status": status,
            "submitted_at": "2026-01-01T00:00:00+00:00" if number < 4 else "",
        }
        atomic_write_json(batch_dir / "tasks" / f"r{number}.json", task)
    result = build_remaining_from_batch(input_dir, batch_dir, output_dir)
    assert result["submitted"] == 4
    assert result["processed_not_submitted"] == 2
    assert result["remaining"] == 4
    assert result["hardlink_success"] == 4
    assert (output_dir / "f9.pdf").stat().st_ino == paths[9].stat().st_ino
    assert len((output_dir / "remaining.jsonl").read_text(encoding="utf-8").splitlines()) == 4


def test_changed_source_is_not_linked_as_remaining(tmp_path):
    input_dir = tmp_path / "input"
    batch_dir = tmp_path / "batch"
    output_dir = tmp_path / "remaining"
    input_dir.mkdir()
    source = input_dir / "changed.pdf"
    source.write_bytes(b"new")
    atomic_write_json(batch_dir / "tasks" / "r.json", {
        "request_id": "r",
        "batch_id": "old",
        "source_relative_path": "changed.pdf",
        "source_size_bytes": 999,
        "source_mtime_ns": 1,
        "status": "preflight_failed",
    })
    result = build_remaining_from_batch(input_dir, batch_dir, output_dir)
    assert result["changed_source"] == 1
    assert result["remaining"] == 0
    assert not (output_dir / "changed.pdf").exists()
    changed = json.loads((output_dir / "changed_source.jsonl").read_text(encoding="utf-8"))
    assert changed["source_relative_path"] == "changed.pdf"
