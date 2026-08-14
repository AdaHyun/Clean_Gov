"""最小可用的串行批量提交器。"""
from __future__ import annotations

import hashlib
import json
import re
import socket
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import quote

from .client import CompanyApiError, CompanyAsyncClient
from .config import Settings
from .file_utils import (
    MIME_TYPES,
    mask_url_token,
    redact_text,
    redact_value,
    scan_files,
    sha256_file,
    validate_document,
)
from .path_mirror import mirror_document_dir
from .reporting import (
    append_batch_log,
    find_success_by_sha256,
    materialize_duplicate_result,
    mark_reporting_dirty,
    now_iso,
    rebuild_batch_outputs,
    rebuild_manifest,
    refresh_callback_timeouts,
    refresh_submission_timeouts,
    save_task,
    update_task_conditionally,
)
from .storage import atomic_write_json, save_event
from .task_index import acquire_submission_slot, cached_sha256, count_active


def detect_callback_ip(target_host: str) -> str:
    """优先按 Linux 路由表选择到解析服务器的源地址。"""
    try:
        completed = subprocess.run(
            ["ip", "route", "get", target_host],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        match = re.search(r"\bsrc\s+(\d{1,3}(?:\.\d{1,3}){3})\b", completed.stdout)
        if match:
            return match.group(1)
    except (FileNotFoundError, subprocess.SubprocessError):
        pass
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((target_host, 9))
        return str(sock.getsockname()[0])
    finally:
        sock.close()


def resolve_callback_url(settings: Settings, cli_value: str | None = None) -> str:
    if cli_value:
        return cli_value
    if settings.callback_url:
        return settings.callback_url
    if not settings.callback_token:
        raise ValueError("未配置回调地址，且缺少 COMPANY_CALLBACK_TOKEN，无法自动构造")
    target = settings.api_url.split("//", 1)[-1].split("/", 1)[0].split(":", 1)[0]
    source_ip = detect_callback_ip(target)
    return (
        f"http://{source_ip}:{settings.callback_bind_port}/callback"
        f"?token={quote(settings.callback_token, safe='')}"
    )


def make_batch_id(batch_name: str = "") -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    readable = re.sub(r"[^0-9A-Za-z_-]+", "_", batch_name).strip("_")[:30]
    middle = f"_{readable}" if readable else ""
    return f"{timestamp}{middle}_{uuid.uuid4().hex[:8]}"


def count_in_flight(output_root: Path, batch_id: str) -> int:
    return count_active(output_root, batch_id)


def submission_slot(
    settings: Settings,
    batch_id: str,
    request_id: str,
    max_in_flight: int,
    poll_interval: float,
    sleep: Callable[[float], None],
) -> int:
    """等待并原子取得当前 batch 槽位；返回后不持有 SQLite 事务。"""
    polls = 0
    while True:
        refresh_callback_timeouts(
            settings.output_root,
            settings.callback_timeout_minutes,
            batch_id,
        )
        refresh_submission_timeouts(
            settings.output_root,
            settings.submit_unknown_timeout_minutes,
            settings.stale_submitting_minutes,
            batch_id,
        )
        acquired, active = acquire_submission_slot(
            settings.output_root,
            request_id,
            batch_id,
            max_in_flight,
            settings.parser_pool_id,
            settings.service_max_in_flight,
        )
        if acquired:
            if polls:
                append_batch_log(
                    settings.output_root,
                    batch_id,
                    f"queue_slot_acquired active={active} limit={max_in_flight}",
                )
            return active
        if polls == 0 or polls % 12 == 0:
            append_batch_log(
                settings.output_root,
                batch_id,
                f"queue_wait active={active} limit={max_in_flight} "
                f"poll_interval={poll_interval:g}",
            )
        polls += 1
        sleep(poll_interval)


def _document_id(relative_path: str, file_sha256: str) -> str:
    value = f"{relative_path}\0{file_sha256}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()[:32]


def _accepted(body: dict[str, Any]) -> bool:
    status = body.get("Status")
    if status is not None:
        try:
            return int(status) == 200
        except (TypeError, ValueError):
            return False
    try:
        return int(body.get("Code", -1)) == 0
    except (TypeError, ValueError):
        return False


def _base_task(
    settings: Settings,
    path: Path,
    input_root: Path,
    batch_id: str,
    batch_name: str,
    output_layout: str,
    request_id: str,
) -> dict[str, Any]:
    stat = path.stat()
    relative = path.resolve().relative_to(input_root).as_posix()
    digest = cached_sha256(
        settings.output_root, path, stat.st_size, stat.st_mtime_ns
    ) or sha256_file(path)
    mtime = datetime.fromtimestamp(stat.st_mtime, timezone.utc).astimezone().isoformat()
    return {
        "document_id": _document_id(relative, digest),
        "request_id": request_id,
        "batch_id": batch_id,
        "batch_name": batch_name,
        "parser_pool_id": settings.parser_pool_id,
        "parser_api_url": mask_url_token(settings.api_url),
        "input_root": str(input_root),
        "source_absolute_path": str(path.resolve()),
        "source_relative_path": relative,
        "source_file_name": path.name,
        "source_extension": path.suffix.lower(),
        "source_size_bytes": stat.st_size,
        "source_mtime": mtime,
        "source_mtime_ns": stat.st_mtime_ns,
        "file_sha256": digest,
        "output_layout": output_layout,
        "status": "pending",
        "discovered_at": now_iso(),
        "submission_started_at": "",
        "submitted_at": "",
        "callback_at": "",
        "callback_count": 0,
        "error_type": None,
        "error_message": None,
    }


def run_batch(
    settings: Settings,
    input_dir: Path,
    *,
    callback_url: str | None = None,
    recursive: bool = False,
    interval: float = 2.0,
    max_in_flight: int = 10,
    slot_poll_interval: float = 5.0,
    max_files: int | None = None,
    extensions: Iterable[str] | None = None,
    dry_run: bool = False,
    batch_name: str = "",
    force: bool = False,
    output_layout: str = "mirror",
    client: CompanyAsyncClient | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if output_layout not in {"request", "readable", "mirror"}:
        raise ValueError("output_layout 必须是 request、readable 或 mirror")
    if interval < 0:
        raise ValueError("interval 不能为负数")
    if max_in_flight < 1:
        raise ValueError("max_in_flight 必须是正整数")
    if slot_poll_interval <= 0:
        raise ValueError("slot_poll_interval 必须大于 0")
    if max_files is not None and max_files < 1:
        raise ValueError("max_files 必须是正整数")
    root = input_dir.resolve()
    selected_extensions = tuple(extensions or settings.supported_extensions)
    # 默认审计目录内全部文件，未知格式进入 unsupported；显式 --extensions 时只扫描指定类型。
    scan_extensions = tuple(extensions) if extensions is not None else ()
    scan_issues: list[dict[str, str]] = []
    files = scan_files(root, recursive, scan_extensions, issues=scan_issues)
    if max_files is not None:
        files = files[:max_files]
    mirror_collisions: dict[str, list[str]] = {}
    mirror_path_errors: dict[str, str] = {}
    if output_layout == "mirror":
        target_sources: dict[str, list[str]] = {}
        for path in files:
            relative = path.resolve().relative_to(root).as_posix()
            try:
                target = mirror_document_dir(
                    settings.output_root / "documents",
                    relative,
                    "collision-check",
                    "mirror",
                )
            except ValueError as exc:
                mirror_path_errors[relative] = str(exc)
                continue
            assert target is not None
            key = target.resolve().as_posix().casefold()
            target_sources.setdefault(key, []).append(relative)
        for sources in target_sources.values():
            if len(sources) > 1:
                for source in sources:
                    mirror_collisions[source] = [
                        item for item in sources if item != source
                    ]
    batch_id = make_batch_id(batch_name)
    started_at = now_iso()
    resolved_callback = ""
    if not dry_run:
        resolved_callback = resolve_callback_url(settings, callback_url)
    meta = {
        "batch_id": batch_id,
        "batch_name": batch_name,
        "input_dir": str(root),
        "recursive": recursive,
        "extensions": list(selected_extensions),
        "interval": interval,
        "max_in_flight": max_in_flight,
        "batch_max_in_flight": max_in_flight,
        "service_max_in_flight": settings.service_max_in_flight,
        "parser_pool_id": settings.parser_pool_id,
        "parser_api_url": mask_url_token(settings.api_url),
        "slot_poll_interval": slot_poll_interval,
        "max_files": max_files,
        "dry_run": dry_run,
        "force": force,
        "output_layout": output_layout,
        "callback_url_masked": mask_url_token(resolved_callback),
        "scan_issue_count": len(scan_issues),
        "mirror_collision_count": len(mirror_collisions),
        "mirror_path_error_count": len(mirror_path_errors),
        "started_at": started_at,
        "finished_at": "",
    }
    batch_meta_path = settings.output_root / "batches" / batch_id / "batch.json"
    atomic_write_json(batch_meta_path, meta)
    if scan_issues:
        atomic_write_json(
            settings.output_root / "batches" / batch_id / "scan_issues.json",
            scan_issues,
        )
    append_batch_log(settings.output_root, batch_id, f"batch_start files={len(files)} layout={output_layout} callback={mask_url_token(resolved_callback)}")
    api_client = client or CompanyAsyncClient(settings)
    actual_submissions = 0

    for index, path in enumerate(files, 1):
        request_id = str(uuid.uuid4())
        try:
            task = _base_task(settings, path, root, batch_id, batch_name, output_layout, request_id)
        except Exception as exc:
            # 文件在扫描后消失或无法读取时，仍记录批次失败并继续。
            relative = path.name
            task = {
                "request_id": request_id,
                "batch_id": batch_id,
                "batch_name": batch_name,
                "parser_pool_id": settings.parser_pool_id,
                "parser_api_url": mask_url_token(settings.api_url),
                "source_absolute_path": str(path),
                "source_relative_path": relative,
                "source_file_name": path.name,
                "source_extension": path.suffix.lower(),
                "output_layout": output_layout,
                "status": "submit_failed",
                "error_type": "local_file_error",
                "error_message": redact_text(str(exc), (settings.callback_token, settings.aihub_access_key_secret)),
            }
            save_task(settings.output_root, task)
            append_batch_log(settings.output_root, batch_id, f"file_failed path={relative} error_type=local_file_error")
            continue

        extension = str(task["source_extension"])
        if extension not in settings.supported_extensions or extension not in MIME_TYPES:
            task["status"] = "unsupported"
            task["error_type"] = "client_unsupported"
            task["error_message"] = f"客户端未配置该文件类型：{extension}"
            save_task(settings.output_root, task)
            append_batch_log(settings.output_root, batch_id, f"file_unsupported path={task['source_relative_path']} extension={extension}")
            continue
        relative_path = str(task["source_relative_path"])
        if relative_path in mirror_path_errors:
            task["status"] = "preflight_failed"
            task["error_type"] = "mirror_path_invalid"
            task["error_message"] = mirror_path_errors[relative_path]
            save_task(settings.output_root, task)
            append_batch_log(
                settings.output_root,
                batch_id,
                f"file_preflight_failed path={relative_path} "
                "error_type=mirror_path_invalid",
            )
            continue
        if relative_path in mirror_collisions:
            conflicts = ", ".join(mirror_collisions[relative_path])
            task["status"] = "preflight_failed"
            task["error_type"] = "mirror_path_collision"
            task["error_message"] = (
                "完整附件名在跨平台安全化后仍映射到同一目录，"
                "请先重命名其中一个附件："
                f"{conflicts}"
            )
            save_task(settings.output_root, task)
            append_batch_log(
                settings.output_root,
                batch_id,
                f"file_preflight_failed path={relative_path} "
                "error_type=mirror_path_collision",
            )
            continue
        try:
            validation = validate_document(
                path,
                max_size_bytes=settings.max_file_size_mb * 1024 * 1024,
            )
        except (OSError, ValueError) as exc:
            task["status"] = "preflight_failed"
            task["error_type"] = "preflight_io_error"
            task["error_message"] = redact_text(
                str(exc),
                (settings.callback_token, settings.aihub_access_key_secret),
            )[:2000]
            save_task(settings.output_root, task)
            append_batch_log(
                settings.output_root,
                batch_id,
                f"file_preflight_failed path={task['source_relative_path']} "
                "error_type=preflight_io_error",
            )
            continue
        task["preflight"] = validation.as_dict()
        if not validation.valid:
            task["status"] = "preflight_failed"
            task["error_type"] = validation.code
            task["error_message"] = validation.message
            save_task(settings.output_root, task)
            append_batch_log(
                settings.output_root,
                batch_id,
                f"file_preflight_failed path={task['source_relative_path']} "
                f"error_type={validation.code} detected_type={validation.detected_type}",
            )
            continue
        if validation.needs_manual_review:
            task["preflight_needs_manual_review"] = True
            task.setdefault("warnings", []).append(validation.message)
        duplicate = None if force else find_success_by_sha256(settings.output_root, str(task["file_sha256"]))
        if duplicate:
            task["status"] = "skipped_duplicate"
            task["duplicate_of_request_id"] = duplicate.get("request_id")
            task["duplicate_of_batch_id"] = duplicate.get("batch_id")
            save_task(settings.output_root, task)
            try:
                materialize_duplicate_result(settings.output_root, task, duplicate)
            except (OSError, ValueError) as exc:
                task["status"] = "pending"
                task["duplicate_candidate_request_id"] = task.pop(
                    "duplicate_of_request_id",
                    None,
                )
                task["duplicate_candidate_batch_id"] = task.pop(
                    "duplicate_of_batch_id",
                    None,
                )
                task["duplicate_materialization_error"] = redact_text(
                    str(exc),
                    (settings.callback_token, settings.aihub_access_key_secret),
                )[:2000]
                task.setdefault("warnings", []).append(
                    "复用既有成功结果失败，本文件将重新提交"
                )
                append_batch_log(
                    settings.output_root,
                    batch_id,
                    f"duplicate_materialization_failed path={task['source_relative_path']} "
                    f"error_type={type(exc).__name__}",
                )
            else:
                save_task(settings.output_root, task)
                append_batch_log(settings.output_root, batch_id, f"file_skipped_duplicate path={task['source_relative_path']}")
                continue
        if dry_run:
            task["status"] = "dry_run"
            save_task(settings.output_root, task)
            append_batch_log(settings.output_root, batch_id, f"dry_run path={task['source_relative_path']} extension={extension}")
            continue

        # 先持久化 pending，随后 SQLite 事务只负责原子计数并切换为 submitting。
        save_task(settings.output_root, task)
        active_before_submit = submission_slot(
            settings,
            batch_id,
            request_id,
            max_in_flight,
            slot_poll_interval,
            sleep,
        )
        task["status"] = "submitting"
        task["submission_started_at"] = now_iso()
        task["scheduler"] = {
            "batch_max_in_flight": max_in_flight,
            "service_max_in_flight": settings.service_max_in_flight,
            "active_before_submit": active_before_submit,
            "slot_acquired_at": task["submission_started_at"],
        }
        save_task(settings.output_root, task)
        try:
            result, payload = api_client.submit_file(
                path,
                resolved_callback,
                request_id=request_id,
                validation=validation,
            )
            submitted_at = now_iso()
            next_status = "waiting_callback" if _accepted(result.body) else "submit_failed"
            changes: dict[str, Any] = {
                "submitted_at": submitted_at,
                "http_status": result.http_status,
                "retry_count": result.retry_count,
                "status": next_status,
            }
            if next_status == "submit_failed":
                changes["error_type"] = "submission_rejected"
                changes["error_message"] = str(
                    result.body.get("Message", "提交响应不是成功状态")
                )[:1000]
            safe_response = redact_value(
                result.body,
                (settings.callback_token, settings.aihub_access_key_secret),
            )
            current_task, _ = update_task_conditionally(
                settings.output_root,
                request_id,
                {"submitting", "submit_unknown"},
                changes,
            )
            save_event(settings.output_root, request_id, "submission", {
                "submitted_at": submitted_at,
                "request_id": request_id,
                "batch_id": batch_id,
                "input_root": str(root),
                "source_absolute_path": task["source_absolute_path"],
                "source_relative_path": task["source_relative_path"],
                "source_file_name": task["source_file_name"],
                "source_extension": extension,
                "source_size_bytes": task["source_size_bytes"],
                "file_sha256": task["file_sha256"],
                "output_layout": output_layout,
                "http_status": result.http_status,
                "retry_count": result.retry_count,
                "request_summary": api_client.payload_summary(payload),
                "response": safe_response,
            })
            actual_submissions += 1
            current_status = current_task.get("status") if current_task else "missing"
            append_batch_log(
                settings.output_root,
                batch_id,
                f"file_submitted index={index} path={task['source_relative_path']} "
                f"request_id={request_id} status={current_status}",
            )
        except CompanyApiError as exc:
            unknown = bool(exc.submission_unknown)
            submitted_at = now_iso() if unknown else ""
            changes = {
                "submitted_at": submitted_at,
                "status": "submit_unknown" if unknown else "submit_failed",
                "http_status": exc.http_status,
                "error_type": exc.error_type,
                "error_message": redact_text(
                    str(exc),
                    (settings.callback_token, settings.aihub_access_key_secret),
                )[:2000],
            }
            save_event(settings.output_root, request_id, "submission", {
                "submission_started_at": task["submission_started_at"],
                "submitted_at": submitted_at,
                "request_id": request_id,
                "batch_id": batch_id,
                "source_relative_path": task["source_relative_path"],
                "status": changes["status"],
                "error_type": changes["error_type"],
                "error_message": changes["error_message"],
            })
            update_task_conditionally(
                settings.output_root, request_id, {"submitting"}, changes
            )
            append_batch_log(settings.output_root, batch_id, f"file_failed path={task['source_relative_path']} request_id={request_id} error_type={changes['error_type']}")
        except Exception as exc:
            changes = {
                "status": "submit_failed",
                "error_type": "local_error",
                "error_message": redact_text(
                    str(exc),
                    (settings.callback_token, settings.aihub_access_key_secret),
                )[:2000],
            }
            update_task_conditionally(
                settings.output_root, request_id, {"submitting"}, changes
            )
            append_batch_log(settings.output_root, batch_id, f"file_failed path={task['source_relative_path']} request_id={request_id} error_type=local_error")
        finally:
            mark_reporting_dirty(settings.output_root, batch_id)
        if actual_submissions and interval and index < len(files):
            sleep(interval)

    meta["finished_at"] = now_iso()
    atomic_write_json(batch_meta_path, meta)
    summary = rebuild_batch_outputs(settings.output_root, batch_id)
    rebuild_manifest(settings.output_root)
    append_batch_log(settings.output_root, batch_id, f"batch_submit_finished summary={json.dumps(summary, ensure_ascii=False)}")
    return summary
