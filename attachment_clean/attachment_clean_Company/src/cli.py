"""公司异步接口初版命令行。"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .batch_submit import run_batch
from .callback_server import run_server
from .client import CompanyAsyncClient
from .config import PROJECT_ROOT, Settings, load_settings
from .diagnostics import audit_docx, check_state
from .file_utils import normalize_extensions, redact_value
from .reporting import (
    iter_tasks,
    rebuild_batch_outputs,
    rebuild_manifest,
    refresh_callback_timeouts,
    refresh_submission_timeouts,
    summarize_tasks,
)
from .storage import save_event
from .task_index import rebuild_index, reconcile_active_tasks


def _print(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _submission_record(
    result: Any,
    file_path: Path,
    payload: dict[str, Any],
    settings: Settings,
) -> dict[str, Any]:
    return {
        "submitted_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "request_id": result.request_id,
        "source_file": str(file_path.resolve()),
        "http_status": result.http_status,
        "retry_count": result.retry_count,
        "request_summary": CompanyAsyncClient.payload_summary(payload),
        "response": redact_value(result.body, (settings.callback_token, settings.aihub_access_key_secret)),
    }


def doctor(settings: Settings) -> None:
    auth_ready = True
    auth_error = ""
    try:
        settings.validate_auth()
    except ValueError as exc:
        auth_ready = False
        auth_error = str(exc)
    _print({
        "api_url": settings.api_url,
        "query_url": settings.query_url,
        "parser_pool_id": settings.parser_pool_id,
        "service_max_in_flight": settings.service_max_in_flight,
        "callback_url_configured": bool(settings.callback_url),
        "callback_bind": f"{settings.callback_bind_host}:{settings.callback_bind_port}",
        "callback_timeout_minutes": settings.callback_timeout_minutes,
        "submit_unknown_timeout_minutes": settings.submit_unknown_timeout_minutes,
        "stale_submitting_minutes": settings.stale_submitting_minutes,
        "auth_mode": settings.auth_mode,
        "auth_ready": auth_ready,
        "auth_error": auth_error,
        "max_file_size_mb": settings.max_file_size_mb,
        "supported_extensions": list(settings.supported_extensions),
        "output_root": str(settings.output_root),
    })


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="公司 8899 文档解析异步接口初版")
    parser.add_argument("--config", type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor", help="脱敏检查配置，不访问接口")

    submit = subparsers.add_parser("submit", help="提交一个文件")
    submit.add_argument("--file", type=Path, required=True)
    submit.add_argument("--callback-url", default=None)
    submit.add_argument("--dry-run", action="store_true", help="仅构造并脱敏显示请求")

    query = subparsers.add_parser("query", help="通过 RequestId 查询状态")
    query.add_argument("--request-id", required=True)

    serve = subparsers.add_parser("serve-callback", help="启动本地回调接收服务")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)

    batch = subparsers.add_parser("batch-submit", help="串行批量提交一个input目录")
    batch.add_argument(
        "--input-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "attachments" / "gov_files",
        help="物理附件目录，默认 项目根/data/attachments/gov_files",
    )
    batch.add_argument("--callback-url", default=None)
    batch.add_argument("--recursive", action="store_true")
    batch.add_argument("--interval", type=float, default=2.0)
    batch.add_argument(
        "--max-in-flight",
        type=int,
        default=10,
        help="当前 batch 最多允许多少个在途任务，默认 10",
    )
    batch.add_argument(
        "--slot-poll-interval",
        type=float,
        default=5.0,
        help="队列满时检查可用名额的秒数，默认 5",
    )
    batch.add_argument("--max-files", type=int)
    batch.add_argument("--extensions", nargs="+")
    batch.add_argument("--dry-run", action="store_true")
    batch.add_argument("--batch-name", default="")
    batch.add_argument("--force", action="store_true")
    batch.add_argument(
        "--output-layout", choices=("request", "readable", "mirror"), default="mirror"
    )

    status = subparsers.add_parser("batch-status", help="查看批次提交和回调统计")
    status.add_argument("--batch-id")
    status.add_argument("--status")
    status.add_argument("--extension")
    subparsers.add_parser(
        "rebuild-index",
        help="从历史 task JSON 幂等重建/刷新 SQLite 运行索引",
    )
    check = subparsers.add_parser("check-state", help="只读审计 task JSON 与 SQLite 状态")
    check.add_argument("--batch-id")
    check.add_argument(
        "--repair-active-index",
        action="store_true",
        help="仅修复 SQLite ACTIVE、JSON 已明确终态的安全不一致",
    )
    docx = subparsers.add_parser("audit-docx", help="只读审计 DOCX 状态与文件名截断风险")
    docx.add_argument("--batch-id")
    docx.add_argument("--filename-limit", type=int, default=64)
    docx.add_argument("--parser-log", type=Path, action="append", default=[])
    reports = subparsers.add_parser("rebuild-reports", help="显式重建派生 batch 报表")
    reports.add_argument("--batch-id", required=True)
    reports.add_argument("--manifest", action="store_true", help="同时显式重建全局 manifest")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    settings = load_settings(args.config)
    if args.command == "doctor":
        doctor(settings)
        return
    if args.command == "serve-callback":
        run_server(settings, args.host, args.port)
        return
    if args.command == "rebuild-index":
        _print(rebuild_index(settings.output_root))
        return
    if args.command == "check-state":
        repair = None
        if args.repair_active_index:
            repair = reconcile_active_tasks(settings.output_root, batch_id=args.batch_id)
        result = check_state(settings.output_root, args.batch_id)
        if repair is not None:
            result["repair_active_index"] = repair
        _print(result)
        return
    if args.command == "audit-docx":
        _print(audit_docx(
            settings.output_root,
            args.batch_id,
            filename_limit=args.filename_limit,
            parser_logs=args.parser_log,
        ))
        return
    if args.command == "rebuild-reports":
        result: dict[str, Any] = {
            "batch": rebuild_batch_outputs(settings.output_root, args.batch_id)
        }
        if args.manifest:
            result["manifest_path"] = str(rebuild_manifest(settings.output_root))
        _print(result)
        return
    if args.command == "batch-submit":
        extensions = normalize_extensions(args.extensions) if args.extensions else None
        summary = run_batch(
            settings,
            args.input_dir,
            callback_url=args.callback_url,
            recursive=args.recursive,
            interval=args.interval,
            max_in_flight=args.max_in_flight,
            slot_poll_interval=args.slot_poll_interval,
            max_files=args.max_files,
            extensions=extensions,
            dry_run=args.dry_run,
            batch_name=args.batch_name,
            force=args.force,
            output_layout=args.output_layout,
        )
        _print(summary)
        return
    if args.command == "batch-status":
        refresh_callback_timeouts(
            settings.output_root, settings.callback_timeout_minutes, args.batch_id
        )
        refresh_submission_timeouts(
            settings.output_root,
            settings.submit_unknown_timeout_minutes,
            settings.stale_submitting_minutes,
            args.batch_id,
        )
        if args.batch_id and not args.status and not args.extension:
            _print(rebuild_batch_outputs(settings.output_root, args.batch_id))
            return
        tasks = list(iter_tasks(settings.output_root, args.batch_id))
        if args.status:
            tasks = [task for task in tasks if task.get("status") == args.status]
        if args.extension:
            extension = normalize_extensions([args.extension])[0]
            tasks = [task for task in tasks if task.get("source_extension") == extension]
        _print({
            "summary": summarize_tasks(tasks),
            "tasks": [{
                "request_id": task.get("request_id"),
                "batch_id": task.get("batch_id"),
                "source_relative_path": task.get("source_relative_path"),
                "source_extension": task.get("source_extension"),
                "status": task.get("status"),
                "error_type": task.get("error_type"),
            } for task in tasks],
        })
        return

    client = CompanyAsyncClient(settings)
    if args.command == "submit":
        if args.dry_run:
            request_id, payload = client.build_payload(args.file)
            _print({
                "dry_run": True,
                "request_id": request_id,
                "api_url": settings.api_url,
                "callback_url_configured": bool(args.callback_url or settings.callback_url),
                "payload": client.payload_summary(payload),
            })
            return
        result, payload = client.submit_file(args.file, args.callback_url)
        record = _submission_record(result, args.file, payload, settings)
        output = save_event(settings.output_root, result.request_id, "submission", record)
        _print({"request_id": result.request_id, "response": result.body, "saved_to": str(output)})
        return

    result = client.query(args.request_id)
    record = {
        "queried_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "request_id": result.request_id,
        "http_status": result.http_status,
        "retry_count": result.retry_count,
        "response": result.body,
    }
    output = save_event(settings.output_root, result.request_id, "query_latest", record)
    _print({"request_id": result.request_id, "response": result.body, "saved_to": str(output)})


if __name__ == "__main__":
    main()
