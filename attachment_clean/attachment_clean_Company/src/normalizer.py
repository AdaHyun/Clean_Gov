"""对接口 Markdown 做最保守的格式规范化。"""
from __future__ import annotations

import re
from typing import Any


def _line_count(text: str) -> int:
    return len(text.splitlines()) if text else 0


def normalize_markdown(text: str) -> tuple[str, dict[str, Any]]:
    raw = text or ""
    value = raw.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    nul_count = value.count("\x00")
    value = value.replace("\x00", "")
    value = "\n".join(line.rstrip() for line in value.split("\n"))
    value = re.sub(r"\n{3,}", "\n\n", value).strip()
    if value:
        value += "\n"
    stats = {
        "raw_character_count": len(raw),
        "clean_character_count": len(value),
        "raw_line_count": _line_count(raw),
        "clean_line_count": _line_count(value),
        "removed_nul_count": nul_count,
    }
    return value, stats

