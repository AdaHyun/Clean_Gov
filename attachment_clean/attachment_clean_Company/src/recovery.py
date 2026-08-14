"""中断批次后的安全 remaining 分类与硬链接构建。"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from .storage import atomic_write_jsonl


_SUBMITTED_STATUSES = {
    "submitting",
    "waiting_callback",
    "submit_unknown",
    "submit_unknown_timeout",  # 兼容旧版本事实记录
    "callback_success",
    "callback_failed",
    "callback_timeout",
}
_DEFINITELY_NOT_SENT_ERRORS = {
    "connect_timeout",
    "local_error",
    "local_file_error",
    "preflight_io_error",
}


def task_was_submitted(task: dict[str, Any]) -> bool:
    """保守识别是否执行过提交；遗留 submitting 按不确定已提交处理。"""
    status = str(task.get("status", ""))
    if status in _SUBMITTED_STATUSES:
        return True
    if status in {"pending", "dry_run", "unsupported", "preflight_failed", "skipped_duplicate"}:
        return False
    if status == "submit_failed" and str(task.get("error_type", "")) in _DEFINITELY_NOT_SENT_ERRORS:
        return False
    return bool(task.get("submitted_at"))


def _load_batch_tasks(batch_dir: Path) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for path in sorted((batch_dir / "tasks").glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            tasks.append(value)
    return tasks


def _mtime_matches(task: dict[str, Any], current_mtime_ns: int) -> bool:
    old_ns = task.get("source_mtime_ns")
    if old_ns is not None:
        try:
            return int(old_ns) == current_mtime_ns
        except (TypeError, ValueError):
            return False
    old_text = str(task.get("source_mtime", ""))
    if not old_text:
        return True  # 历史字段缺失时只能用大小判断，不能臆造 changed。
    try:
        old = datetime.fromisoformat(old_text)
    except ValueError:
        return True
    return int(old.timestamp()) == int(current_mtime_ns / 1_000_000_000)


def _same_source(task: dict[str, Any], size_bytes: int, mtime_ns: int) -> bool:
    try:
        same_size = int(task.get("source_size_bytes")) == size_bytes
    except (TypeError, ValueError):
        same_size = True
    return same_size and _mtime_matches(task, mtime_ns)


def build_remaining_from_batch(
    input_dir: Path,
    batch_dir: Path,
    output_dir: Path,
    *,
    link_mode: str = "hardlink",
) -> dict[str, Any]:
    if link_mode != "hardlink":
        raise ValueError("当前仅支持 --link-mode hardlink；不会静默复制大文件")
    input_root = input_dir.resolve()
    batch_root = batch_dir.resolve()
    output_root = output_dir.resolve()
    if not input_root.is_dir():
        raise ValueError(f"input-dir 不存在：{input_root}")
    if not (batch_root / "tasks").is_dir():
        raise ValueError(f"batch-dir 缺少 tasks 目录：{batch_root}")

    files = sorted(
        path for path in input_root.rglob("*")
        if path.is_file() and not path.is_symlink()
    )
    tasks = _load_batch_tasks(batch_root)
    by_relative: dict[str, list[dict[str, Any]]] = {}
    for task in tasks:
        relative = str(task.get("source_relative_path", ""))
        if relative:
            by_relative.setdefault(relative, []).append(task)

    reports: dict[str, list[dict[str, Any]]] = {
        "submitted": [],
        "processed_not_submitted": [],
        "remaining": [],
        "changed_source": [],
        "link_failed": [],
    }
    hardlink_success = 0
    output_root.mkdir(parents=True, exist_ok=True)
    for source in files:
        relative = source.relative_to(input_root).as_posix()
        stat = source.stat()
        candidates = by_relative.get(relative, [])
        record = {
            "source_relative_path": relative,
            "source_absolute_path": str(source.resolve()),
            "source_size_bytes": stat.st_size,
            "source_mtime_ns": stat.st_mtime_ns,
        }
        if candidates:
            matching = [
                task for task in candidates
                if _same_source(task, stat.st_size, stat.st_mtime_ns)
            ]
            if not matching:
                changed = dict(record)
                changed["old_tasks"] = [{
                    "request_id": task.get("request_id"),
                    "status": task.get("status"),
                    "source_size_bytes": task.get("source_size_bytes"),
                    "source_mtime": task.get("source_mtime"),
                    "source_mtime_ns": task.get("source_mtime_ns"),
                } for task in candidates]
                reports["changed_source"].append(changed)
                continue
            submitted = next((task for task in matching if task_was_submitted(task)), None)
            selected = submitted or matching[-1]
            record.update(
                request_id=selected.get("request_id"),
                status=selected.get("status"),
                batch_id=selected.get("batch_id"),
            )
            reports["submitted" if submitted else "processed_not_submitted"].append(record)
            continue

        reports["remaining"].append(record)
        destination = output_root / Path(relative)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.link(source, destination)
            hardlink_success += 1
        except OSError as exc:
            failed = dict(record)
            failed.update(
                destination=str(destination),
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            reports["link_failed"].append(failed)

    for name, values in reports.items():
        atomic_write_jsonl(output_root / f"{name}.jsonl", values)
    return {
        "original_files": len(files),
        "old_tasks": len(tasks),
        "submitted": len(reports["submitted"]),
        "processed_not_submitted": len(reports["processed_not_submitted"]),
        "remaining": len(reports["remaining"]),
        "changed_source": len(reports["changed_source"]),
        "hardlink_success": hardlink_success,
        "hardlink_failed": len(reports["link_failed"]),
        "output_dir": str(output_root),
    }

