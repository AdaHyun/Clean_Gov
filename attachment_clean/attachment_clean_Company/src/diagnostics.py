"""只读状态与 DOCX 风险诊断。"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .storage import request_dir
from .task_index import ACTIVE_STATUSES, SAFE_NON_ACTIVE_STATUSES, index_path


def _readonly_connection(output_root: Path) -> sqlite3.Connection:
    path = index_path(output_root).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"SQLite task index 不存在：{path}")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _issue(values: list[dict[str, Any]], limit: int = 100) -> dict[str, Any]:
    return {"count": len(values), "examples": values[:limit]}


def _task_paths(output_root: Path, batch_id: str | None) -> list[Path]:
    if batch_id is None:
        return sorted((output_root / "batches").glob("*/tasks/*.json"))
    return sorted((output_root / "batches" / batch_id / "tasks").glob("*.json"))


def check_state(output_root: Path, batch_id: str | None = None) -> dict[str, Any]:
    """全量只读审计；只由显式诊断命令调用。"""
    db = _readonly_connection(output_root)
    try:
        if batch_id is None:
            rows = [dict(row) for row in db.execute("SELECT * FROM tasks").fetchall()]
            conflicts = db.execute(
                "SELECT task_json_path, COUNT(*) AS count FROM tasks "
                "GROUP BY task_json_path HAVING COUNT(*) > 1"
            ).fetchall()
        else:
            rows = [dict(row) for row in db.execute(
                "SELECT * FROM tasks WHERE batch_id = ?", (batch_id,)
            ).fetchall()]
            conflicts = db.execute(
                "SELECT task_json_path, COUNT(*) AS count FROM tasks WHERE batch_id = ? "
                "GROUP BY task_json_path HAVING COUNT(*) > 1",
                (batch_id,),
            ).fetchall()
    finally:
        db.close()

    sqlite_statuses = Counter(str(row["status"]) for row in rows)
    sqlite_by_path = {str(row["task_json_path"]): row for row in rows}
    json_statuses: Counter[str] = Counter()
    parsed_by_relative: dict[str, dict[str, Any]] = {}
    corrupt: list[dict[str, Any]] = []
    json_missing_in_sqlite: list[dict[str, Any]] = []
    request_mismatch: list[dict[str, Any]] = []
    content_non_success: list[dict[str, Any]] = []
    success_missing_content: list[dict[str, Any]] = []
    success_empty_content: list[dict[str, Any]] = []

    paths = _task_paths(output_root, batch_id)
    json_path_set = {
        path.resolve().relative_to(output_root.resolve()).as_posix() for path in paths
    }
    for path in paths:
        relative = path.resolve().relative_to(output_root.resolve()).as_posix()
        try:
            task = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            corrupt.append({"task_json_path": relative, "error_type": type(exc).__name__})
            continue
        if not isinstance(task, dict):
            corrupt.append({"task_json_path": relative, "error_type": "not_object"})
            continue
        parsed_by_relative[relative] = task
        status = str(task.get("status", "unknown"))
        json_statuses[status] += 1
        request_id = str(task.get("request_id", ""))
        if path.stem != request_id:
            request_mismatch.append({
                "task_json_path": relative,
                "filename_request_id": path.stem,
                "json_request_id": request_id,
            })
        if relative not in sqlite_by_path:
            json_missing_in_sqlite.append({
                "task_json_path": relative,
                "request_id": request_id,
                "status": status,
            })
        content = request_dir(output_root, request_id) / "content.md" if request_id else None
        if content is not None and content.is_file() and status != "callback_success":
            content_non_success.append({
                "request_id": request_id,
                "json_status": status,
                "content_path": content.relative_to(output_root).as_posix(),
            })
        if status == "callback_success" and content is not None:
            if not content.is_file():
                success_missing_content.append({"request_id": request_id})
            elif content.stat().st_size == 0 or not content.read_text(
                encoding="utf-8", errors="replace"
            ).strip():
                success_empty_content.append({"request_id": request_id})

    missing_json: list[dict[str, Any]] = []
    active_json_terminal: list[dict[str, Any]] = []
    terminal_json_active: list[dict[str, Any]] = []
    row_request_mismatch: list[dict[str, Any]] = []
    for row in rows:
        relative = str(row["task_json_path"])
        task = parsed_by_relative.get(relative)
        if task is None:
            # 损坏 JSON 已由 json_corrupt 单独报告，不能同时误报为文件缺失。
            if relative not in json_path_set:
                missing_json.append({
                    "request_id": row["request_id"],
                    "task_json_path": relative,
                    "sqlite_status": row["status"],
                })
            continue
        json_request_id = str(task.get("request_id", ""))
        if json_request_id != str(row["request_id"]) or str(task.get("batch_id", "")) != str(row["batch_id"]):
            row_request_mismatch.append({
                "sqlite_request_id": row["request_id"],
                "json_request_id": json_request_id,
                "sqlite_batch_id": row["batch_id"],
                "json_batch_id": task.get("batch_id"),
                "task_json_path": relative,
            })
            continue
        sqlite_status = str(row["status"])
        json_status = str(task.get("status", ""))
        if sqlite_status in ACTIVE_STATUSES and json_status in SAFE_NON_ACTIVE_STATUSES:
            active_json_terminal.append({
                "request_id": row["request_id"],
                "sqlite_status": sqlite_status,
                "json_status": json_status,
            })
        if sqlite_status in SAFE_NON_ACTIVE_STATUSES and json_status in ACTIVE_STATUSES:
            terminal_json_active.append({
                "request_id": row["request_id"],
                "sqlite_status": sqlite_status,
                "json_status": json_status,
            })

    return {
        "batch_id": batch_id,
        "sqlite_total_tasks": len(rows),
        "task_json_total_tasks": len(paths),
        "sqlite_status_counts": dict(sorted(sqlite_statuses.items())),
        "task_json_status_counts": dict(sorted(json_statuses.items())),
        "sqlite_active_count": sum(sqlite_statuses[status] for status in ACTIVE_STATUSES),
        "sqlite_active_json_terminal": _issue(active_json_terminal),
        "sqlite_terminal_json_active": _issue(terminal_json_active),
        "task_json_missing": _issue(missing_json),
        "json_corrupt": _issue(corrupt),
        "request_id_mismatch": _issue(request_mismatch + row_request_mismatch),
        "json_missing_in_sqlite": _issue(json_missing_in_sqlite),
        "task_json_path_conflicts": _issue([
            {"task_json_path": str(row["task_json_path"]), "count": int(row["count"])}
            for row in conflicts
        ]),
        "content_exists_but_status_not_success": _issue(content_non_success),
        "callback_success_content_missing": _issue(success_missing_content),
        "callback_success_content_empty": _issue(success_empty_content),
    }


def audit_docx(
    output_root: Path,
    batch_id: str | None = None,
    *,
    filename_limit: int = 64,
    parser_logs: Iterable[Path] = (),
) -> dict[str, Any]:
    """只读统计 DOCX 结果与可能触发上游文件名截断的名称。"""
    extension_statuses: dict[str, Counter[str]] = {
        extension: Counter() for extension in (".pdf", ".doc", ".docx", ".xlsx", ".pptx")
    }
    risky: list[dict[str, Any]] = []
    docx_statuses: Counter[str] = Counter()
    docx_total = 0
    corrupt_json = 0
    for path in _task_paths(output_root, batch_id):
        try:
            task = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            corrupt_json += 1
            continue
        if not isinstance(task, dict):
            corrupt_json += 1
            continue
        extension = str(task.get("source_extension", "")).lower()
        status = str(task.get("status", "unknown"))
        if extension in extension_statuses:
            extension_statuses[extension][status] += 1
        if extension != ".docx":
            continue
        docx_total += 1
        docx_statuses[status] += 1
        name = str(task.get("source_file_name") or Path(str(task.get("source_relative_path", ""))).name)
        stem = Path(name).stem
        prefixed = "base64_docx_" + name
        facts = {
            "request_id": task.get("request_id"),
            "batch_id": task.get("batch_id"),
            "status": status,
            "source_relative_path": task.get("source_relative_path"),
            "filename": name,
            "stem_characters": len(stem),
            "stem_utf8_bytes": len(stem.encode("utf-8")),
            "basename_characters": len(name),
            "basename_utf8_bytes": len(name.encode("utf-8")),
            "prefixed_characters": len(prefixed),
            "prefixed_utf8_bytes": len(prefixed.encode("utf-8")),
            "suffix": Path(name).suffix.lower(),
        }
        risk_reasons: list[str] = []
        if facts["prefixed_characters"] > filename_limit:
            risk_reasons.append("characters_exceed_limit")
        if facts["prefixed_utf8_bytes"] > filename_limit:
            risk_reasons.append("utf8_bytes_exceed_limit")
        elif facts["prefixed_utf8_bytes"] >= max(1, filename_limit - 8):
            risk_reasons.append("utf8_bytes_near_limit")
        facts["risk_reasons"] = risk_reasons
        if risk_reasons:
            risky.append(facts)

    parser_errors: list[dict[str, Any]] = []
    for log_path in parser_logs:
        try:
            with log_path.open("r", encoding="utf-8", errors="replace") as handle:
                for line_number, line in enumerate(handle, 1):
                    lowered = line.lower()
                    if "package not found" in lowered and ".doc" in lowered:
                        parser_errors.append({
                            "log_path": str(log_path),
                            "line_number": line_number,
                            "message": line.strip()[:1000],
                        })
        except OSError as exc:
            parser_errors.append({
                "log_path": str(log_path),
                "error_type": type(exc).__name__,
                "message": str(exc),
            })

    return {
        "batch_id": batch_id,
        "filename_limit": filename_limit,
        "docx_total": docx_total,
        "docx_status_counts": dict(sorted(docx_statuses.items())),
        "extension_status_counts": {
            extension: dict(sorted(statuses.items()))
            for extension, statuses in extension_statuses.items()
        },
        "suspected_filename_truncation": _issue(risky, limit=1000),
        "parser_package_not_found_doc_errors": _issue(parser_errors, limit=1000),
        "corrupt_task_json": corrupt_json,
        "note": (
            "风险判定同时检查字符数和UTF-8字节数；客户端不截断文件名。"
            "Parser日志若无RequestId，无法可靠自动映射回原task。"
        ),
    }
