"""可由 task JSON 重建的 SQLite 运行索引。"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ACTIVE_STATUSES = ("submitting", "waiting_callback", "submit_unknown")
SAFE_NON_ACTIVE_STATUSES = (
    "callback_success",
    "callback_failed",
    "callback_timeout",
    "submit_failed",
    "submit_unknown_timeout",
    "preflight_failed",
    "unsupported",
    "skipped_duplicate",
    "dry_run",
)
DEFAULT_PARSER_POOL_ID = "legacy_unknown"


def index_path(output_root: Path) -> Path:
    return output_root / "state" / "task_index.sqlite3"


def _utc_iso(value: object) -> str:
    text = str(value or "")
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def connect(output_root: Path) -> sqlite3.Connection:
    path = index_path(output_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5.0, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            request_id TEXT PRIMARY KEY,
            batch_id TEXT NOT NULL,
            batch_name TEXT NOT NULL DEFAULT '',
            parser_pool_id TEXT NOT NULL DEFAULT 'legacy_unknown',
            parser_api_url TEXT NOT NULL DEFAULT '',
            source_relative_path TEXT NOT NULL DEFAULT '',
            source_absolute_path TEXT NOT NULL DEFAULT '',
            source_size_bytes INTEGER,
            source_mtime TEXT NOT NULL DEFAULT '',
            source_mtime_ns INTEGER,
            file_sha256 TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            submission_started_at TEXT NOT NULL DEFAULT '',
            submitted_at TEXT NOT NULL DEFAULT '',
            callback_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT '',
            task_json_path TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_tasks_batch_status
            ON tasks(batch_id, status);
        CREATE INDEX IF NOT EXISTS idx_tasks_sha_status
            ON tasks(file_sha256, status);
        CREATE INDEX IF NOT EXISTS idx_tasks_pool_status
            ON tasks(parser_pool_id, status);
        CREATE INDEX IF NOT EXISTS idx_tasks_batch_status_submitted
            ON tasks(batch_id, status, submitted_at);
        CREATE INDEX IF NOT EXISTS idx_tasks_batch_status_submission_started
            ON tasks(batch_id, status, submission_started_at);
        CREATE INDEX IF NOT EXISTS idx_tasks_source_fingerprint
            ON tasks(source_absolute_path, source_size_bytes, source_mtime_ns);
        """
    )
    return connection


_COLUMNS = (
    "request_id", "batch_id", "batch_name", "parser_pool_id", "parser_api_url",
    "source_relative_path", "source_absolute_path", "source_size_bytes",
    "source_mtime", "source_mtime_ns", "file_sha256", "status",
    "submission_started_at", "submitted_at", "callback_at", "updated_at",
    "task_json_path",
)


def task_record(output_root: Path, task: dict[str, Any], path: Path) -> dict[str, Any]:
    try:
        relative_path = path.resolve().relative_to(output_root.resolve()).as_posix()
    except ValueError:
        relative_path = str(path.resolve())
    return {
        "request_id": str(task.get("request_id", "")),
        "batch_id": str(task.get("batch_id", "")),
        "batch_name": str(task.get("batch_name", "")),
        "parser_pool_id": str(task.get("parser_pool_id") or DEFAULT_PARSER_POOL_ID),
        "parser_api_url": str(task.get("parser_api_url", "")),
        "source_relative_path": str(task.get("source_relative_path", "")),
        "source_absolute_path": str(task.get("source_absolute_path", "")),
        "source_size_bytes": task.get("source_size_bytes"),
        "source_mtime": str(task.get("source_mtime", "")),
        "source_mtime_ns": task.get("source_mtime_ns"),
        "file_sha256": str(task.get("file_sha256", "")),
        "status": str(task.get("status", "unknown")),
        "submission_started_at": _utc_iso(task.get("submission_started_at")),
        "submitted_at": _utc_iso(task.get("submitted_at")),
        "callback_at": _utc_iso(task.get("callback_at")),
        "updated_at": _utc_iso(task.get("updated_at") or task.get("callback_at") or task.get("submitted_at") or task.get("discovered_at")),
        "task_json_path": relative_path,
    }


def upsert_task(
    output_root: Path,
    task: dict[str, Any],
    path: Path,
    *,
    connection: sqlite3.Connection | None = None,
) -> bool:
    """同步单个事实记录。不同路径复用 RequestId 时拒绝覆盖并返回 False。"""
    record = task_record(output_root, task, path)
    if not record["request_id"] or not record["batch_id"]:
        raise ValueError("task 索引需要 request_id 和 batch_id")
    own_connection = connection is None
    db = connection or connect(output_root)
    try:
        existing = db.execute(
            "SELECT task_json_path FROM tasks WHERE request_id = ?",
            (record["request_id"],),
        ).fetchone()
        if existing is not None and str(existing[0]) != record["task_json_path"]:
            return False
        placeholders = ", ".join("?" for _ in _COLUMNS)
        updates = ", ".join(
            f"{column}=excluded.{column}" for column in _COLUMNS if column != "request_id"
        )
        db.execute(
            f"INSERT INTO tasks ({', '.join(_COLUMNS)}) VALUES ({placeholders}) "
            f"ON CONFLICT(request_id) DO UPDATE SET {updates}",
            tuple(record[column] for column in _COLUMNS),
        )
        return True
    finally:
        if own_connection:
            db.close()


def count_active(output_root: Path, batch_id: str) -> int:
    reconcile_active_tasks(output_root, batch_id=batch_id)
    with closing(connect(output_root)) as db:
        row = db.execute(
            "SELECT COUNT(*) FROM tasks WHERE batch_id = ? AND status IN (?, ?, ?)",
            (batch_id, *ACTIVE_STATUSES),
        ).fetchone()
    return int(row[0])


def acquire_submission_slot(
    output_root: Path,
    request_id: str,
    batch_id: str,
    batch_max_in_flight: int,
    parser_pool_id: str,
    service_max_in_flight: int | None = None,
) -> tuple[bool, int]:
    """以极短的 BEGIN IMMEDIATE 事务原子取得当前 batch 的一个槽位。"""
    reconcile_active_tasks(output_root, batch_id=batch_id)
    if service_max_in_flight is not None:
        reconcile_active_tasks(output_root, parser_pool_id=parser_pool_id)
    db = connect(output_root)
    try:
        db.execute("BEGIN IMMEDIATE")
        active = int(db.execute(
            "SELECT COUNT(*) FROM tasks WHERE batch_id = ? AND status IN (?, ?, ?)",
            (batch_id, *ACTIVE_STATUSES),
        ).fetchone()[0])
        if active >= batch_max_in_flight:
            db.execute("ROLLBACK")
            return False, active
        if service_max_in_flight is not None:
            service_active = int(db.execute(
                "SELECT COUNT(*) FROM tasks WHERE parser_pool_id = ? AND status IN (?, ?, ?)",
                (parser_pool_id, *ACTIVE_STATUSES),
            ).fetchone()[0])
            if service_active >= service_max_in_flight:
                db.execute("ROLLBACK")
                return False, active
        cursor = db.execute(
            "UPDATE tasks SET status = 'submitting', submission_started_at = ?, updated_at = ? "
            "WHERE request_id = ? AND batch_id = ? AND status = 'pending'",
            (
                datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat(),
                request_id,
                batch_id,
            ),
        )
        if cursor.rowcount != 1:
            db.execute("ROLLBACK")
            return False, active
        db.execute("COMMIT")
        return True, active
    except Exception:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()


def active_rows(
    output_root: Path,
    *,
    batch_id: str | None = None,
    parser_pool_id: str | None = None,
) -> list[dict[str, Any]]:
    clauses = ["status IN (?, ?, ?)"]
    parameters: list[Any] = list(ACTIVE_STATUSES)
    if batch_id is not None:
        clauses.append("batch_id = ?")
        parameters.append(batch_id)
    if parser_pool_id is not None:
        clauses.append("parser_pool_id = ?")
        parameters.append(parser_pool_id)
    with closing(connect(output_root)) as db:
        rows = db.execute(
            f"SELECT * FROM tasks WHERE {' AND '.join(clauses)}",
            tuple(parameters),
        ).fetchall()
    return [dict(row) for row in rows]


def _resolved_task_path(output_root: Path, value: object) -> Path | None:
    candidate = Path(str(value or ""))
    path = candidate if candidate.is_absolute() else output_root / candidate
    try:
        resolved = path.resolve()
        resolved.relative_to(output_root.resolve())
    except (OSError, ValueError):
        return None
    return resolved


def reconcile_active_tasks(
    output_root: Path,
    *,
    batch_id: str | None = None,
    parser_pool_id: str | None = None,
) -> dict[str, Any]:
    """仅用明确 terminal 的 task JSON 修复 SQLite ACTIVE 假占槽位。"""
    rows = active_rows(output_root, batch_id=batch_id, parser_pool_id=parser_pool_id)
    result: dict[str, Any] = {
        "checked_active": len(rows),
        "json_reads": 0,
        "repaired": 0,
        "warnings": [],
    }
    candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for row in rows:
        request_id = str(row["request_id"])
        row_batch_id = str(row["batch_id"])
        path = _resolved_task_path(output_root, row.get("task_json_path"))
        expected = (
            output_root / "batches" / row_batch_id / "tasks" / f"{request_id}.json"
        ).resolve()
        if path is None or path != expected:
            result["warnings"].append({
                "request_id": request_id,
                "warning": "task_json_path_mismatch",
                "task_json_path": str(row.get("task_json_path", "")),
            })
            continue
        try:
            task = json.loads(path.read_text(encoding="utf-8"))
            result["json_reads"] += 1
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            result["warnings"].append({
                "request_id": request_id,
                "warning": "task_json_unreadable",
                "error_type": type(exc).__name__,
            })
            continue
        if not isinstance(task, dict):
            result["warnings"].append({
                "request_id": request_id,
                "warning": "task_json_not_object",
            })
            continue
        if str(task.get("request_id", "")) != request_id:
            result["warnings"].append({
                "request_id": request_id,
                "warning": "request_id_mismatch",
            })
            continue
        if str(task.get("batch_id", "")) != row_batch_id:
            result["warnings"].append({
                "request_id": request_id,
                "warning": "batch_id_mismatch",
            })
            continue
        json_status = str(task.get("status", ""))
        if json_status in ACTIVE_STATUSES or json_status == "pending":
            continue
        if json_status not in SAFE_NON_ACTIVE_STATUSES:
            result["warnings"].append({
                "request_id": request_id,
                "warning": "unsafe_non_active_status",
                "json_status": json_status,
            })
            continue
        candidates.append((row, task_record(output_root, task, path)))

    if not candidates:
        return result
    db = connect(output_root)
    try:
        db.execute("BEGIN IMMEDIATE")
        updates = ", ".join(
            f"{column} = ?"
            for column in _COLUMNS
            if column not in {"request_id", "batch_id", "task_json_path"}
        )
        update_columns = tuple(
            column
            for column in _COLUMNS
            if column not in {"request_id", "batch_id", "task_json_path"}
        )
        for row, record in candidates:
            cursor = db.execute(
                f"UPDATE tasks SET {updates} WHERE request_id = ? AND batch_id = ? "
                "AND task_json_path = ? AND status IN (?, ?, ?)",
                tuple(record[column] for column in update_columns)
                + (
                    row["request_id"],
                    row["batch_id"],
                    row["task_json_path"],
                    *ACTIVE_STATUSES,
                ),
            )
            result["repaired"] += int(cursor.rowcount == 1)
        db.execute("COMMIT")
    except Exception:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()
    return result


def submission_state_candidates(
    output_root: Path,
    status: str,
    older_than: str,
    batch_id: str | None = None,
) -> list[dict[str, Any]]:
    if status not in {"submitting", "submit_unknown"}:
        raise ValueError(f"不支持的 submission 状态：{status}")
    time_expression = (
        "COALESCE(NULLIF(submission_started_at, ''), updated_at)"
        if status == "submitting"
        else "COALESCE(NULLIF(submitted_at, ''), updated_at)"
    )
    clauses = ["status = ?", f"{time_expression} <> ''", f"{time_expression} < ?"]
    parameters: list[Any] = [status, _utc_iso(older_than)]
    if batch_id is not None:
        clauses.append("batch_id = ?")
        parameters.append(batch_id)
    with closing(connect(output_root)) as db:
        rows = db.execute(
            f"SELECT request_id, batch_id, task_json_path, status, "
            f"submission_started_at, submitted_at, updated_at FROM tasks "
            f"WHERE {' AND '.join(clauses)}",
            tuple(parameters),
        ).fetchall()
    return [dict(row) for row in rows]


def find_success(output_root: Path, file_sha256: str) -> dict[str, Any] | None:
    with closing(connect(output_root)) as db:
        rows = db.execute(
            "SELECT * FROM tasks WHERE file_sha256 = ? AND status = 'callback_success' "
            "ORDER BY callback_at DESC, request_id LIMIT 20",
            (file_sha256,),
        ).fetchall()
    for row in rows:
        request_id = str(row["request_id"])
        if (output_root / "requests" / request_id / "content.md").is_file():
            return dict(row)
    return None


def cached_sha256(
    output_root: Path, source_absolute_path: Path, size_bytes: int, mtime_ns: int
) -> str | None:
    with closing(connect(output_root)) as db:
        row = db.execute(
            "SELECT file_sha256 FROM tasks WHERE source_absolute_path = ? "
            "AND source_size_bytes = ? AND source_mtime_ns = ? AND file_sha256 <> '' "
            "ORDER BY updated_at DESC LIMIT 1",
            (str(source_absolute_path.resolve()), size_bytes, mtime_ns),
        ).fetchone()
    return str(row[0]) if row else None


def timeout_candidates(output_root: Path, batch_id: str | None, submitted_before: str) -> list[dict[str, Any]]:
    with closing(connect(output_root)) as db:
        if batch_id is None:
            rows = db.execute(
                "SELECT request_id, task_json_path FROM tasks "
                "WHERE status = 'waiting_callback' AND submitted_at <> '' AND submitted_at < ?",
                (_utc_iso(submitted_before),),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT request_id, task_json_path FROM tasks WHERE batch_id = ? "
                "AND status = 'waiting_callback' AND submitted_at <> '' AND submitted_at < ?",
                (batch_id, _utc_iso(submitted_before)),
            ).fetchall()
    return [dict(row) for row in rows]


def rebuild_index(output_root: Path) -> dict[str, Any]:
    """显式扫描历史 JSON；不修改任何历史文件。"""
    paths = sorted((output_root / "batches").glob("*/tasks/*.json"))
    stats: dict[str, Any] = {
        "scanned_tasks": len(paths),
        "imported_tasks": 0,
        "corrupt_json": 0,
        "request_id_conflicts": 0,
        "conflicts": [],
        "missing_fields": 0,
        "status_counts": {},
    }
    statuses: Counter[str] = Counter()
    audited_fields = tuple(column for column in _COLUMNS if column != "task_json_path")
    with closing(connect(output_root)) as db:
        for path in paths:
            try:
                task = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                stats["corrupt_json"] += 1
                continue
            if not isinstance(task, dict):
                stats["corrupt_json"] += 1
                continue
            missing = sum(field not in task for field in audited_fields)
            stats["missing_fields"] += missing
            if not task.get("request_id") or not task.get("batch_id"):
                continue
            if upsert_task(output_root, task, path, connection=db):
                stats["imported_tasks"] += 1
                statuses[str(task.get("status", "unknown"))] += 1
            else:
                stats["request_id_conflicts"] += 1
                existing = db.execute(
                    "SELECT batch_id, task_json_path FROM tasks WHERE request_id = ?",
                    (str(task["request_id"]),),
                ).fetchone()
                stats["conflicts"].append({
                    "request_id": str(task["request_id"]),
                    "kept_batch_id": str(existing["batch_id"]) if existing else "",
                    "kept_task_json_path": str(existing["task_json_path"]) if existing else "",
                    "rejected_task_json_path": path.relative_to(output_root).as_posix(),
                })
    stats["status_counts"] = dict(sorted(statuses.items()))
    stats["index_path"] = str(index_path(output_root))
    return stats
