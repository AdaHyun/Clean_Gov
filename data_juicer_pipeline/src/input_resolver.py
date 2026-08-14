"""Resolve explicit or generated clean web-corpus JSONL inputs safely."""

from __future__ import annotations

from pathlib import Path
import re

from paths import WEB_CORPUS_DIR, resolve_from_clean_gov


WEB_CORPUS_NAME_RE = re.compile(r"^gov_corpus_clean_(\d{8})_(\d{6})\.jsonl$")


def resolve_input(value: str | Path | None) -> Path:
    """Resolve an explicit path or select the latest timestamped web corpus."""
    if value:
        path = resolve_from_clean_gov(value)
        if not path.is_file() or path.suffix.lower() != ".jsonl":
            raise FileNotFoundError(f"--input 不是有效 JSONL 文件: {path}")
        return path

    candidates = sorted(
        (
            path.resolve()
            for path in WEB_CORPUS_DIR.glob("gov_corpus_clean_*.jsonl")
            if path.is_file() and WEB_CORPUS_NAME_RE.fullmatch(path.name)
        ),
        key=lambda path: path.name,
    )
    if not candidates:
        raise FileNotFoundError(
            f"{WEB_CORPUS_DIR} 中没有符合 "
            "gov_corpus_clean_YYYYMMDD_HHMMSS.jsonl 的文件；请生成文件或显式传入 --input"
        )
    # The fixed-width timestamp in the filename is lexicographically sortable.
    return candidates[-1]


def default_output_path(input_path: Path) -> Path:
    from .paths import OUTPUT_DIR

    return OUTPUT_DIR / f"{input_path.stem}_stage01_text_cleaned.jsonl"
