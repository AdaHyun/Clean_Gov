"""Structured wall-clock timing for the native corpus pipeline."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _rounded(value: float) -> float:
    return round(max(value, 0.0), 3)


def format_duration(seconds: float) -> str:
    """Return a compact Chinese duration suitable for CMD progress output."""
    seconds = max(seconds, 0.0)
    if seconds < 60:
        return f"{seconds:.2f} 秒"
    minutes, remaining = divmod(seconds, 60)
    if minutes < 60:
        return f"{int(minutes)} 分 {remaining:.2f} 秒"
    hours, minutes = divmod(int(minutes), 60)
    return f"{hours} 小时 {minutes} 分 {remaining:.2f} 秒"


class TimedOperation:
    """One mutable timing record created and owned by a TimingRecorder."""

    def __init__(
        self,
        recorder: "TimingRecorder",
        name: str,
        *,
        stage: str,
        activity: str,
        lane: str | None = None,
        input_document_count: int | None = None,
    ) -> None:
        self.recorder = recorder
        self.record: dict[str, Any] = {
            "name": name,
            "stage": stage,
            "activity": activity,
            "lane": lane,
            "status": "running",
            "started_at": _timestamp(),
            "finished_at": None,
            "duration_seconds": None,
            "input_document_count": input_document_count,
            "output_document_count": None,
            "input_character_count": None,
            "output_character_count": None,
            "documents_per_second": None,
            "input_characters_per_second": None,
        }
        self._started = time.perf_counter()

    def __enter__(self) -> "TimedOperation":
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: Any) -> bool:
        duration = time.perf_counter() - self._started
        self.record["finished_at"] = _timestamp()
        self.record["duration_seconds"] = _rounded(duration)
        self.record["status"] = "failed" if exc_type is not None else "success"
        if exc_type is not None:
            self.record["error_type"] = exc_type.__name__
        self._refresh_rates()
        self.recorder._complete(self.record)
        return False

    def update_counts(
        self,
        *,
        input_document_count: int | None = None,
        output_document_count: int | None = None,
        input_character_count: int | None = None,
        output_character_count: int | None = None,
    ) -> None:
        updates = {
            "input_document_count": input_document_count,
            "output_document_count": output_document_count,
            "input_character_count": input_character_count,
            "output_character_count": output_character_count,
        }
        for key, value in updates.items():
            if value is not None:
                self.record[key] = int(value)
        self._refresh_rates()
        self.recorder.write_snapshot()

    def as_dict(self) -> dict[str, Any]:
        return dict(self.record)

    def _refresh_rates(self) -> None:
        duration = self.record.get("duration_seconds")
        if not isinstance(duration, (int, float)) or duration <= 0:
            return
        input_documents = self.record.get("input_document_count")
        if isinstance(input_documents, int):
            self.record["documents_per_second"] = round(input_documents / duration, 3)
        input_characters = self.record.get("input_character_count")
        if isinstance(input_characters, int):
            self.record["input_characters_per_second"] = round(input_characters / duration, 3)


class TimingRecorder:
    """Collect operation timings and persist a crash-tolerant JSON snapshot."""

    def __init__(self) -> None:
        self.started_at = _timestamp()
        self._started = time.perf_counter()
        self.finished_at: str | None = None
        self._finished_elapsed: float | None = None
        self.status = "running"
        self.operations: list[dict[str, Any]] = []
        self.output_path: Path | None = None
        self.overall_input_document_count: int | None = None
        self.overall_output_document_count: int | None = None

    def set_output_path(self, output_path: Path) -> None:
        self.output_path = output_path
        self.write_snapshot()

    def measure(
        self,
        name: str,
        *,
        stage: str,
        activity: str,
        lane: str | None = None,
        input_document_count: int | None = None,
    ) -> TimedOperation:
        return TimedOperation(
            self,
            name,
            stage=stage,
            activity=activity,
            lane=lane,
            input_document_count=input_document_count,
        )

    def _complete(self, record: dict[str, Any]) -> None:
        self.operations.append(record)
        if record["status"] == "failed":
            self.status = "failed"
            self.finished_at = record["finished_at"]
            self._finished_elapsed = time.perf_counter() - self._started
        self.write_snapshot()

    def finish(
        self,
        status: str,
        *,
        input_document_count: int | None = None,
        output_document_count: int | None = None,
    ) -> dict[str, Any]:
        self.status = status
        self.finished_at = _timestamp()
        self._finished_elapsed = time.perf_counter() - self._started
        self.overall_input_document_count = input_document_count
        self.overall_output_document_count = output_document_count
        self.write_snapshot()
        return self.as_dict()

    def as_dict(self) -> dict[str, Any]:
        elapsed = (
            self._finished_elapsed
            if self._finished_elapsed is not None
            else time.perf_counter() - self._started
        )
        total_duration = _rounded(elapsed)
        by_activity: dict[str, float] = {}
        for operation in self.operations:
            duration = operation.get("duration_seconds")
            if isinstance(duration, (int, float)):
                activity = str(operation.get("activity") or "other")
                by_activity[activity] = by_activity.get(activity, 0.0) + duration
        by_activity = {key: _rounded(value) for key, value in sorted(by_activity.items())}
        overall_rate = None
        if self.overall_input_document_count is not None and total_duration > 0:
            overall_rate = round(self.overall_input_document_count / total_duration, 3)
        return {
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total_duration_seconds": total_duration,
            "total_duration_display": format_duration(total_duration),
            "input_document_count": self.overall_input_document_count,
            "output_document_count": self.overall_output_document_count,
            "input_documents_per_second": overall_rate,
            "duration_by_activity_seconds": by_activity,
            "operations": [dict(operation) for operation in self.operations],
        }

    def write_snapshot(self) -> None:
        if self.output_path is None:
            return
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.output_path.with_suffix(self.output_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.as_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.output_path)
