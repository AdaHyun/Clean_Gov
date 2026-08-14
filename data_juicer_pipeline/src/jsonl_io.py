"""Streaming JSONL helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, TextIO


@dataclass(frozen=True)
class JsonlIssue:
    line_number: int
    error: str
    raw_excerpt: str


def iter_jsonl(
    path: Path,
    *,
    encoding: str = "utf-8-sig",
    issues: list[JsonlIssue] | None = None,
) -> Iterator[tuple[int, dict[str, Any]]]:
    """Yield JSON objects one physical line at a time and optionally collect errors."""
    with path.open("r", encoding=encoding, newline="") as handle:
        for line_number, raw in enumerate(handle, start=1):
            try:
                value = json.loads(raw)
                if not isinstance(value, dict):
                    raise ValueError("JSONL 行不是对象")
            except (json.JSONDecodeError, ValueError) as exc:
                if issues is None:
                    raise ValueError(f"第 {line_number} 行 JSON 无效: {exc}") from exc
                issues.append(JsonlIssue(line_number, str(exc), raw[:500].rstrip("\r\n")))
                continue
            yield line_number, value


def write_jsonl_record(handle: TextIO, record: dict[str, Any]) -> None:
    handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def validate_jsonl(path: Path, *, encoding: str = "utf-8-sig") -> tuple[int, list[JsonlIssue]]:
    issues: list[JsonlIssue] = []
    count = sum(1 for _ in iter_jsonl(path, encoding=encoding, issues=issues))
    return count, issues
