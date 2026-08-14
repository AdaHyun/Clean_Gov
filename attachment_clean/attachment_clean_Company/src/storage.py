"""异步提交、查询和回调结果的落盘逻辑。"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .file_utils import redact_text, redact_value
from .normalizer import normalize_markdown


_CALLBACK_LOCK = threading.RLock()


def safe_request_id(value: object) -> str:
    text = str(value if value is not None else "")
    if not text.strip():
        raise ValueError("RequestId 不能为空")
    if len(text) > 1024:
        raise ValueError("RequestId 超过 1024 个字符")
    if re.fullmatch(r"[0-9A-Za-z._-]{1,160}", text):
        return text
    normalized = re.sub(r"[^0-9A-Za-z._-]+", "_", text).strip("._-")
    prefix = (normalized or "request")[:120]
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}--{digest}"


def atomic_write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(value)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def atomic_write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    lines = "".join(json.dumps(value, ensure_ascii=False) + "\n" for value in values)
    atomic_write_text(path, lines)


def request_dir(output_root: Path, request_id: object) -> Path:
    return output_root / "requests" / safe_request_id(request_id)


def save_event(output_root: Path, request_id: object, name: str, payload: object) -> Path:
    path = request_dir(output_root, request_id) / f"{name}.json"
    atomic_write_json(path, payload)
    return path


def save_callback(
    output_root: Path,
    payload: dict[str, Any],
    secrets: tuple[str, ...] = (),
) -> tuple[str, Path]:
    with _CALLBACK_LOCK:
        return _save_callback_unlocked(output_root, payload, secrets)


def _save_callback_unlocked(
    output_root: Path,
    payload: dict[str, Any],
    secrets: tuple[str, ...] = (),
) -> tuple[str, Path]:
    request_id = payload.get("RequestId") or payload.get("RequestID") or payload.get("request_id")
    if not request_id:
        raise ValueError("回调缺少 RequestId")
    original_request_id = str(request_id)
    rid = safe_request_id(request_id)
    directory = request_dir(output_root, rid)
    state_path = directory / "callback_state.json"
    state: dict[str, Any] = {}
    callback_count = 1
    if state_path.is_file():
        try:
            loaded_state = json.loads(state_path.read_text(encoding="utf-8"))
            state = loaded_state if isinstance(loaded_state, dict) else {}
            callback_count = int(state.get("callback_count", 0)) + 1
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            state = {}
            callback_count = 1
    received_at = datetime.now(timezone.utc).astimezone().isoformat()
    safe_payload = redact_value(payload, secrets)
    wrapped = {
        "received_at": received_at,
        "callback_count": callback_count,
        "original_request_id": original_request_id,
        "request_directory_key": rid,
        "payload": safe_payload,
    }
    events_dir = directory / "events"
    save_path = events_dir / f"callback_{callback_count:04d}.json"
    atomic_write_json(save_path, wrapped)
    atomic_write_json(directory / "latest_callback_response.json", wrapped)
    result = safe_payload.get("Result") if isinstance(safe_payload.get("Result"), dict) else {}
    content = result.get("md_content") if isinstance(result, dict) else None
    raw_content = content if isinstance(content, str) else ""
    clean_content, normalization = normalize_markdown(raw_content)
    try:
        code = int(safe_payload.get("Code", -1))
    except (TypeError, ValueError):
        code = -1
    incoming_success = code == 0 and bool(clean_content.strip())

    canonical_path = directory / "callback_response.json"
    canonical_wrapped: dict[str, Any] | None = None
    if canonical_path.is_file():
        try:
            loaded_canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
            if isinstance(loaded_canonical, dict):
                canonical_wrapped = loaded_canonical
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            canonical_wrapped = None
    had_success = bool(state.get("has_success", False))
    if not had_success and canonical_wrapped is not None:
        canonical_payload = (
            canonical_wrapped.get("payload")
            if isinstance(canonical_wrapped.get("payload"), dict)
            else {}
        )
        canonical_result = (
            canonical_payload.get("Result")
            if isinstance(canonical_payload.get("Result"), dict)
            else {}
        )
        try:
            canonical_code = int(canonical_payload.get("Code", -1))
        except (TypeError, ValueError):
            canonical_code = -1
        had_success = canonical_code == 0 and bool(
            str(canonical_result.get("md_content", "")).strip()
        )

    promote_to_canonical = incoming_success or not had_success
    if promote_to_canonical:
        canonical_wrapped = wrapped
        atomic_write_json(canonical_path, canonical_wrapped)
        atomic_write_text(directory / "raw_content.md", raw_content)
        atomic_write_text(directory / "content.md", clean_content)
        atomic_write_json(directory / "normalization.json", normalization)
        canonical_event = callback_count
        canonical_at = received_at
        canonical_success = incoming_success
    else:
        canonical_event = int(state.get("canonical_event", 1))
        canonical_at = str(state.get("canonical_at", ""))
        canonical_success = True
        raw_path = directory / "raw_content.md"
        clean_path = directory / "content.md"
        raw_content = raw_path.read_text(encoding="utf-8") if raw_path.is_file() else ""
        clean_content = clean_path.read_text(encoding="utf-8") if clean_path.is_file() else ""

    successful_callback_count = int(state.get("successful_callback_count", 0))
    if incoming_success:
        successful_callback_count += 1
    elif had_success and successful_callback_count == 0:
        successful_callback_count = 1
    atomic_write_json(
        state_path,
        {
            "request_id": rid,
            "original_request_id": original_request_id,
            "callback_count": callback_count,
            "latest_at": received_at,
            "latest_event": callback_count,
            "canonical_at": canonical_at,
            "canonical_event": canonical_event,
            "has_success": canonical_success,
            "successful_callback_count": successful_callback_count,
        },
    )

    # 批次关联和镜像层是增量能力；没有索引时仍保留原始 RequestId 结果。
    from .reporting import apply_callback_to_task

    effective_callback = dict(canonical_wrapped or wrapped)
    effective_callback["callback_count"] = callback_count
    effective_callback["latest_received_at"] = received_at
    effective_callback["latest_payload"] = safe_payload
    effective_callback["latest_incoming_success"] = incoming_success
    try:
        apply_callback_to_task(
            output_root,
            rid,
            effective_callback,
            raw_content,
            clean_content,
        )
    except Exception as exc:  # 原始回调已经安全落盘，镜像增强失败不能要求服务端重复回调。
        safe_message = redact_text(
            str(exc),
            secrets,
        )[:2000]
        safe_traceback = redact_text(
            traceback.format_exc(),
            secrets,
        )[-8000:]
        atomic_write_json(directory / "postprocess_warning.json", {
            "request_id": rid,
            "warning": "callback_saved_but_postprocess_failed",
            "stage": "apply_callback_to_task",
            "error_type": type(exc).__name__,
            "error_message": safe_message,
            "traceback": safe_traceback,
            "recorded_at": received_at,
        })
    return rid, directory
