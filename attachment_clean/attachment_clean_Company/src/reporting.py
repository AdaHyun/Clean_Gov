"""批次任务索引、统计文件和回调状态关联。"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .path_mirror import mirror_document_dir
from .quality import analyze_markdown
from .storage import atomic_write_json, atomic_write_jsonl, atomic_write_text, request_dir
from .task_index import (
    find_success,
    reconcile_active_tasks,
    submission_state_candidates,
    timeout_candidates,
    upsert_task,
)


_WRITE_LOCK = threading.RLock()


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _ensure_mirror_owner(mirror_dir: Path, task: dict[str, Any]) -> None:
    """防止两个不同源附件写入同一个无 RequestId 镜像目录。"""
    if not mirror_dir.exists():
        return
    metadata_path = mirror_dir / "metadata.json"
    if not metadata_path.is_file():
        if any(mirror_dir.iterdir()):
            raise ValueError(f"镜像目录已存在但缺少 metadata.json：{mirror_dir}")
        return
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"镜像目录 metadata.json 无法读取：{mirror_dir}") from exc
    existing_source = str(metadata.get("source_relative_path", ""))
    current_source = str(task.get("source_relative_path", ""))
    if existing_source and existing_source != current_source:
        raise ValueError(
            "镜像目录已属于另一个源附件："
            f"{existing_source} != {current_source}"
        )


def task_path(output_root: Path, batch_id: str, request_id: str) -> Path:
    return output_root / "batches" / batch_id / "tasks" / f"{request_id}.json"


@contextmanager
def _task_state_lock(output_root: Path) -> Iterable[None]:
    """跨进程串行化很短的 task JSON/index 状态同步。"""
    lock_path = output_root / "locks" / "task_state.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK, lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    time.sleep(0.01)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _save_task_unlocked(output_root: Path, task: dict[str, Any]) -> Path:
    path = task_path(output_root, str(task["batch_id"]), str(task["request_id"]))
    task["updated_at"] = now_iso()
    atomic_write_json(path, task)
    index = {
        "request_id": task["request_id"],
        "batch_id": task["batch_id"],
        "task_path": str(path.relative_to(output_root).as_posix()),
    }
    atomic_write_json(output_root / "request_index" / f"{task['request_id']}.json", index)
    indexed = upsert_task(output_root, task, path)
    if not indexed:
        raise RuntimeError(
            "实时 task SQLite 索引更新被拒绝："
            f"request_id={task['request_id']} task_json_path={index['task_path']}"
        )
    return path


def save_task(output_root: Path, task: dict[str, Any]) -> Path:
    with _task_state_lock(output_root):
        return _save_task_unlocked(output_root, task)


def update_task_conditionally(
    output_root: Path,
    request_id: str,
    allowed_statuses: set[str],
    changes: dict[str, Any],
) -> tuple[dict[str, Any] | None, bool]:
    """基于最新 JSON 状态更新；旧 submit 线程不能覆盖 callback 终态。"""
    with _task_state_lock(output_root):
        task, path = load_task_by_request(output_root, request_id)
        if task is None or path is None:
            return None, False
        if str(task.get("status", "")) not in allowed_statuses:
            return task, False
        task.update(changes)
        _save_task_unlocked(output_root, task)
        return task, True


def load_task_by_request(output_root: Path, request_id: str) -> tuple[dict[str, Any] | None, Path | None]:
    index_path = output_root / "request_index" / f"{request_id}.json"
    if not index_path.is_file():
        return None, None
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
        path = output_root / str(index["task_path"])
        return json.loads(path.read_text(encoding="utf-8")), path
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None, None


def iter_tasks(output_root: Path, batch_id: str | None = None) -> Iterable[dict[str, Any]]:
    pattern_root = output_root / "batches"
    paths = (pattern_root / batch_id / "tasks").glob("*.json") if batch_id else pattern_root.glob("*/tasks/*.json")
    for path in sorted(paths):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            yield value


def find_success_by_sha256(output_root: Path, file_sha256: str) -> dict[str, Any] | None:
    return find_success(output_root, file_sha256)


def append_batch_log(output_root: Path, batch_id: str, message: str) -> None:
    path = output_root / "batch_logs" / f"batch_{batch_id}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"{now_iso()} {message.replace(chr(10), ' ').replace(chr(13), ' ')}\n")


def mark_reporting_dirty(output_root: Path, batch_id: str) -> None:
    """callback 热路径只标记派生报表待刷新，不扫描历史 task。"""
    dirty_root = output_root / "state" / "reporting_dirty"
    atomic_write_text(dirty_root / "batches" / f"{batch_id}.dirty", now_iso() + "\n")
    atomic_write_text(dirty_root / "manifest.dirty", now_iso() + "\n")


def summarize_tasks(tasks: Iterable[dict[str, Any]], batch_meta: dict[str, Any] | None = None) -> dict[str, Any]:
    values = list(tasks)
    statuses = Counter(str(task.get("status", "unknown")) for task in values)
    by_extension: dict[str, dict[str, int]] = defaultdict(lambda: {
        "total": 0, "submit_success": 0, "callback_success": 0, "failed": 0
    })
    for task in values:
        extension = str(task.get("source_extension") or "<none>")
        item = by_extension[extension]
        item["total"] += 1
        status = str(task.get("status", ""))
        if status in {"waiting_callback", "callback_success", "callback_failed", "callback_timeout"}:
            item["submit_success"] += 1
        if status == "callback_success":
            item["callback_success"] += 1
        if status in {
            "unsupported",
            "preflight_failed",
            "submit_failed",
            "submit_unknown",
            "submit_unknown_timeout",
            "callback_failed",
            "callback_timeout",
        }:
            item["failed"] += 1
    meta = batch_meta or {}
    return {
        "batch_id": meta.get("batch_id", values[0].get("batch_id", "") if values else ""),
        "batch_name": meta.get("batch_name", ""),
        "input_dir": meta.get("input_dir", ""),
        "started_at": meta.get("started_at", ""),
        "finished_at": meta.get("finished_at", ""),
        "total_files": len(values),
        "submit_success": sum(statuses[key] for key in ("waiting_callback", "callback_success", "callback_failed", "callback_timeout")),
        "submit_failed": (
            statuses["submit_failed"]
            + statuses["submit_unknown"]
            + statuses["submit_unknown_timeout"]
        ),
        "submit_unknown_timeout": statuses["submit_unknown_timeout"],
        "preflight_failed": statuses["preflight_failed"],
        "unsupported": statuses["unsupported"],
        "waiting_callback": statuses["waiting_callback"],
        "callback_success": statuses["callback_success"],
        "callback_failed": statuses["callback_failed"],
        "callback_timeout": statuses["callback_timeout"],
        "skipped_duplicate": statuses["skipped_duplicate"],
        "needs_manual_review": sum(
            bool(
                task.get("preflight_needs_manual_review")
                or task.get("manual_review_required")
            )
            for task in values
        ),
        "dry_run": statuses["dry_run"],
        "by_extension": dict(sorted(by_extension.items())),
    }


def rebuild_batch_outputs(output_root: Path, batch_id: str) -> dict[str, Any]:
    with _WRITE_LOCK:
        batch_path = output_root / "batches" / batch_id / "batch.json"
        meta: dict[str, Any] = {}
        if batch_path.is_file():
            try:
                meta = json.loads(batch_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                meta = {}
        tasks = list(iter_tasks(output_root, batch_id))
        summary = summarize_tasks(tasks, meta)
        logs = output_root / "batch_logs"
        atomic_write_json(logs / f"batch_{batch_id}_summary.json", summary)
        success = [str(task.get("source_relative_path", "")) for task in tasks if task.get("status") == "callback_success"]
        waiting = [str(task.get("source_relative_path", "")) for task in tasks if task.get("status") == "waiting_callback"]
        failed = [str(task.get("source_relative_path", "")) for task in tasks if task.get("status") in {"preflight_failed", "submit_failed", "submit_unknown", "submit_unknown_timeout", "callback_failed", "callback_timeout"}]
        unsupported = [str(task.get("source_relative_path", "")) for task in tasks if task.get("status") == "unsupported"]
        duplicates = [str(task.get("source_relative_path", "")) for task in tasks if task.get("status") == "skipped_duplicate"]
        atomic_write_text(logs / f"batch_{batch_id}_success.txt", "".join(f"{item}\n" for item in success))
        atomic_write_text(logs / f"batch_{batch_id}_waiting.txt", "".join(f"{item}\n" for item in waiting))
        atomic_write_text(logs / f"batch_{batch_id}_failed.txt", "".join(f"{item}\n" for item in failed))
        atomic_write_text(logs / f"batch_{batch_id}_unsupported.txt", "".join(f"{item}\n" for item in unsupported))
        atomic_write_text(logs / f"batch_{batch_id}_duplicates.txt", "".join(f"{item}\n" for item in duplicates))
        (output_root / "state" / "reporting_dirty" / "batches" / f"{batch_id}.dirty").unlink(missing_ok=True)
        return summary


def refresh_callback_timeouts(output_root: Path, timeout_minutes: int, batch_id: str | None = None) -> int:
    """通过 SQLite 释放 callback 等待槽位；提交不确定状态由独立 TTL 处理。"""
    reconcile_active_tasks(output_root, batch_id=batch_id)
    threshold = datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)
    changed = 0
    for candidate in timeout_candidates(output_root, batch_id, threshold.isoformat()):
        task, updated = update_task_conditionally(
            output_root,
            str(candidate["request_id"]),
            {"waiting_callback"},
            {
                "status": "callback_timeout",
                "error_type": "callback_timeout",
                "error_message": f"提交后超过 {timeout_minutes} 分钟未收到回调",
            },
        )
        if not updated or task is None:
            continue
        mark_reporting_dirty(output_root, str(task["batch_id"]))
        changed += 1
    return changed


def refresh_submission_timeouts(
    output_root: Path,
    submit_unknown_timeout_minutes: int,
    stale_submitting_minutes: int,
    batch_id: str | None = None,
) -> dict[str, int]:
    """有界释放不确定提交；保留事实并绝不自动重提。"""
    reconcile_active_tasks(output_root, batch_id=batch_id)
    now = datetime.now(timezone.utc)
    result = {"submit_unknown_timeout": 0, "stale_submitting": 0}

    unknown_cutoff = now - timedelta(minutes=submit_unknown_timeout_minutes)
    for candidate in submission_state_candidates(
        output_root, "submit_unknown", unknown_cutoff.isoformat(), batch_id
    ):
        task, updated = update_task_conditionally(
            output_root,
            str(candidate["request_id"]),
            {"submit_unknown"},
            {
                "status": "submit_unknown_timeout",
                "error_type": "submit_unknown_timeout",
                "error_message": (
                    f"提交结果不确定超过 {submit_unknown_timeout_minutes} 分钟；"
                    "已释放槽位，禁止自动重提，需人工核对"
                ),
                "manual_review_required": True,
                "submission_uncertainty_timed_out_at": now_iso(),
            },
        )
        if updated and task is not None:
            result["submit_unknown_timeout"] += 1
            mark_reporting_dirty(output_root, str(task["batch_id"]))

    submitting_cutoff = now - timedelta(minutes=stale_submitting_minutes)
    for candidate in submission_state_candidates(
        output_root, "submitting", submitting_cutoff.isoformat(), batch_id
    ):
        task, updated = update_task_conditionally(
            output_root,
            str(candidate["request_id"]),
            {"pending", "submitting"},
            {
                "status": "submit_unknown",
                "error_type": "stale_submitting",
                "error_message": (
                    f"submission_started_at 超过 {stale_submitting_minutes} 分钟仍为 submitting；"
                    "是否已到达解析服务未知，禁止自动重提"
                ),
                "manual_review_required": True,
                "submission_uncertain_at": now_iso(),
            },
        )
        if updated and task is not None:
            result["stale_submitting"] += 1
            mark_reporting_dirty(output_root, str(task["batch_id"]))
    return result


def apply_callback_to_task(
    output_root: Path,
    request_id: str,
    callback: dict[str, Any],
    raw_content: str,
    clean_content: str,
) -> None:
    task, path = load_task_by_request(output_root, request_id)
    if task is None or path is None:
        return
    payload = callback.get("payload") if isinstance(callback.get("payload"), dict) else {}
    try:
        code = int(payload.get("Code", -1))
    except (TypeError, ValueError):
        code = -1
    incoming_success = code == 0 and bool(clean_content.strip())
    latest_payload = (
        callback.get("latest_payload")
        if isinstance(callback.get("latest_payload"), dict)
        else payload
    )
    try:
        latest_code = int(latest_payload.get("Code", -1))
    except (TypeError, ValueError):
        latest_code = -1
    latest_incoming_success = bool(
        callback.get("latest_incoming_success", incoming_success)
    )
    if task.get("status") == "callback_success" and not latest_incoming_success:
        task["callback_count"] = int(callback.get("callback_count", task.get("callback_count", 1)))
        task["latest_callback_at"] = callback.get("latest_received_at", now_iso())
        task["latest_callback_code"] = latest_code
        task["latest_callback_message"] = str(latest_payload.get("Message", ""))[:1000]
        task.setdefault("warnings", []).append(
            "已忽略成功结果之后的失败或空回调，保留原成功正文"
        )
        save_task(output_root, task)
        mark_reporting_dirty(output_root, str(task["batch_id"]))
        return
    callback_at = callback.get("received_at", now_iso())
    changes: dict[str, Any] = {
        "callback_at": callback_at,
        "latest_callback_at": callback.get("latest_received_at", callback_at),
        "callback_count": int(callback.get("callback_count", 1)),
        "callback_code": code,
        "callback_message": str(payload.get("Message", ""))[:1000],
    }
    if code == 0 and clean_content.strip():
        changes.update(status="callback_success", error_type=None, error_message=None)
    elif code == 0:
        changes.update(
            status="callback_failed",
            error_type="empty_result",
            error_message="Code=0 但 md_content 为空",
        )
    else:
        changes.update(
            status="callback_failed",
            error_type=f"callback_code_{code}",
            error_message=changes["callback_message"],
        )

    allowed = {
        "submitting", "waiting_callback", "submit_unknown", "callback_timeout",
        "submit_unknown_timeout", "callback_failed",
    }
    if incoming_success:
        allowed.add("callback_success")
    task, updated = update_task_conditionally(output_root, request_id, allowed, changes)
    if task is None or not updated:
        return

    layout = str(task.get("output_layout", "request"))
    if layout == "mirror" and task["status"] != "callback_success":
        task["mirror_update_skipped"] = "callback_not_success"
        task.setdefault("warnings", []).append(
            "本次回调未成功，未覆盖该附件已有的镜像结果"
        )
        save_task(output_root, task)
        append_batch_log(
            output_root,
            str(task["batch_id"]),
            f"callback_received request_id={request_id} "
            f"status={task['status']} code={code} mirror_update=skipped",
        )
        mark_reporting_dirty(output_root, str(task["batch_id"]))
        return
    if layout in {"readable", "mirror"}:
        documents_root = output_root / "documents"
        try:
            mirror_dir = mirror_document_dir(
                documents_root, str(task.get("source_relative_path", "")), request_id, layout
            )
        except ValueError as exc:
            mirror_dir = documents_root / "unknown" / request_id
            task.setdefault("warnings", []).append(str(exc))
        assert mirror_dir is not None
        if layout == "mirror":
            try:
                _ensure_mirror_owner(mirror_dir, task)
            except ValueError as exc:
                task["mirror_update_skipped"] = "path_collision"
                task.setdefault("warnings", []).append(str(exc))
                save_task(output_root, task)
                append_batch_log(
                    output_root,
                    str(task["batch_id"]),
                    f"callback_received request_id={request_id} "
                    f"status={task['status']} code={code} "
                    "mirror_update=path_collision",
                )
                mark_reporting_dirty(output_root, str(task["batch_id"]))
                return
        mirror_dir.mkdir(parents=True, exist_ok=True)
        quality = analyze_markdown(request_id, str(task.get("source_relative_path", "")), raw_content, clean_content)
        raw_request = request_dir(output_root, request_id)
        task["mirror_document_dir"] = str(mirror_dir.relative_to(output_root).as_posix())
        metadata = {
            "document_id": task.get("document_id", request_id),
            "request_id": request_id,
            "batch_id": task.get("batch_id", ""),
            "source_file_name": task.get("source_file_name", ""),
            "source_extension": task.get("source_extension", ""),
            "source_relative_path": task.get("source_relative_path", ""),
            "source_absolute_path": task.get("source_absolute_path", ""),
            "source_size_bytes": task.get("source_size_bytes", 0),
            "source_mtime": task.get("source_mtime", ""),
            "file_sha256": task.get("file_sha256", ""),
            "parser": "company_api",
            "status": task["status"],
            "submission_time": task.get("submitted_at", ""),
            "callback_time": task.get("callback_at", ""),
            "processing_duration_seconds": payload.get("CostTime"),
            "raw_request_dir": str(raw_request.relative_to(output_root).as_posix()),
            "mirror_document_dir": task["mirror_document_dir"],
            "raw_markdown_path": str((mirror_dir / "raw.md").relative_to(output_root).as_posix()),
            "clean_markdown_path": str((mirror_dir / "content.md").relative_to(output_root).as_posix()),
            "callback_count": task["callback_count"],
        }
        atomic_write_text(mirror_dir / "request_id.txt", request_id + "\n")
        atomic_write_text(mirror_dir / "raw.md", raw_content)
        atomic_write_text(mirror_dir / "content.md", clean_content)
        atomic_write_json(mirror_dir / "callback_response.json", callback)
        atomic_write_json(mirror_dir / "metadata.json", metadata)
        atomic_write_json(mirror_dir / "quality.json", quality)
    save_task(output_root, task)
    append_batch_log(
        output_root,
        str(task["batch_id"]),
        f"callback_received request_id={request_id} status={task['status']} code={code}",
    )
    mark_reporting_dirty(output_root, str(task["batch_id"]))


def materialize_duplicate_result(
    output_root: Path,
    task: dict[str, Any],
    duplicate: dict[str, Any],
) -> None:
    """为当前源路径物化已成功重复文件的正文和显式来源映射。"""
    request_id = str(task["request_id"])
    original_request_id = str(duplicate.get("request_id", ""))
    if not original_request_id:
        raise ValueError("重复任务缺少原始 request_id")
    original_dir = request_dir(output_root, original_request_id)
    original_raw_path = original_dir / "raw_content.md"
    original_content_path = original_dir / "content.md"
    if not original_content_path.is_file():
        raise FileNotFoundError(f"重复任务原始正文不存在：{original_content_path}")
    raw_content = (
        original_raw_path.read_text(encoding="utf-8")
        if original_raw_path.is_file()
        else original_content_path.read_text(encoding="utf-8")
    )
    clean_content = original_content_path.read_text(encoding="utf-8")
    current_dir = request_dir(output_root, request_id)
    reference = {
        "request_id": request_id,
        "status": "skipped_duplicate",
        "source_relative_path": task.get("source_relative_path", ""),
        "file_sha256": task.get("file_sha256", ""),
        "duplicate_of_request_id": original_request_id,
        "duplicate_of_batch_id": duplicate.get("batch_id", ""),
        "original_content_path": str(
            original_content_path.relative_to(output_root).as_posix()
        ),
        "materialized_at": now_iso(),
    }
    atomic_write_json(current_dir / "duplicate_reference.json", reference)
    atomic_write_text(current_dir / "raw_content.md", raw_content)
    atomic_write_text(current_dir / "content.md", clean_content)
    task["duplicate_result_materialized"] = True
    task["duplicate_content_source"] = reference["original_content_path"]

    layout = str(task.get("output_layout", "request"))
    if layout not in {"readable", "mirror"}:
        return
    documents_root = output_root / "documents"
    mirror_dir = mirror_document_dir(
        documents_root,
        str(task.get("source_relative_path", "")),
        request_id,
        layout,
    )
    assert mirror_dir is not None
    if layout == "mirror":
        _ensure_mirror_owner(mirror_dir, task)
    mirror_dir.mkdir(parents=True, exist_ok=True)
    task["mirror_document_dir"] = str(mirror_dir.relative_to(output_root).as_posix())
    metadata = {
        "document_id": task.get("document_id", request_id),
        "request_id": request_id,
        "batch_id": task.get("batch_id", ""),
        "source_file_name": task.get("source_file_name", ""),
        "source_extension": task.get("source_extension", ""),
        "source_relative_path": task.get("source_relative_path", ""),
        "source_absolute_path": task.get("source_absolute_path", ""),
        "source_size_bytes": task.get("source_size_bytes", 0),
        "source_mtime": task.get("source_mtime", ""),
        "file_sha256": task.get("file_sha256", ""),
        "parser": "company_api_duplicate_reference",
        "status": "skipped_duplicate",
        "duplicate_of_request_id": original_request_id,
        "duplicate_of_batch_id": duplicate.get("batch_id", ""),
        "materialized_at": reference["materialized_at"],
        "mirror_document_dir": task["mirror_document_dir"],
    }
    quality = analyze_markdown(
        request_id,
        str(task.get("source_relative_path", "")),
        raw_content,
        clean_content,
    )
    atomic_write_text(mirror_dir / "request_id.txt", request_id + "\n")
    atomic_write_text(
        mirror_dir / "duplicate_of_request_id.txt",
        original_request_id + "\n",
    )
    atomic_write_text(mirror_dir / "raw.md", raw_content)
    atomic_write_text(mirror_dir / "content.md", clean_content)
    atomic_write_json(mirror_dir / "duplicate_reference.json", reference)
    atomic_write_json(mirror_dir / "metadata.json", metadata)
    atomic_write_json(mirror_dir / "quality.json", quality)


def rebuild_manifest(output_root: Path) -> Path:
    records: list[dict[str, Any]] = []
    for task in iter_tasks(output_root):
        request_id = str(task.get("request_id", ""))
        raw_dir = request_dir(output_root, request_id)
        mirror = str(task.get("mirror_document_dir", ""))
        record = {
            "document_id": task.get("document_id", request_id),
            "request_id": request_id,
            "batch_id": task.get("batch_id", ""),
            "source_file_name": task.get("source_file_name", ""),
            "source_relative_path": task.get("source_relative_path", ""),
            "source_extension": task.get("source_extension", ""),
            "source_size_bytes": task.get("source_size_bytes", 0),
            "file_sha256": task.get("file_sha256", ""),
            "status": task.get("status", ""),
            "parser": "company_api",
            "submitted_at": task.get("submitted_at", ""),
            "callback_at": task.get("callback_at", ""),
            "raw_request_dir": str(raw_dir.relative_to(output_root).as_posix()),
            "mirror_document_dir": mirror,
            "content_path": f"{mirror}/content.md" if mirror else str((raw_dir / "content.md").relative_to(output_root).as_posix()),
            "metadata_path": f"{mirror}/metadata.json" if mirror else "",
            "quality_path": f"{mirror}/quality.json" if mirror else "",
            "error_type": task.get("error_type"),
            "error_message": task.get("error_message"),
            "duplicate_of_request_id": task.get("duplicate_of_request_id"),
        }
        records.append(record)
    path = output_root / "manifest.jsonl"
    with _WRITE_LOCK:
        atomic_write_jsonl(path, sorted(records, key=lambda item: (str(item["batch_id"]), str(item["source_relative_path"]), str(item["request_id"]))))
        (output_root / "state" / "reporting_dirty" / "manifest.dirty").unlink(missing_ok=True)
    return path
