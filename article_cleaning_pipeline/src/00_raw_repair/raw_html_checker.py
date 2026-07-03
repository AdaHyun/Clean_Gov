from __future__ import annotations

from pathlib import Path

from .common import get_path, is_valid_html_file, resolve_local_path


def check_raw_html(record: dict, crawler_root: Path, raw_html_dir: Path, raw_html_files: list[Path] | None = None) -> dict:
    raw_html_path = get_path(record, "crawl.raw_html_path", "") or ""
    resolved = resolve_local_path(raw_html_path, crawler_root, raw_html_dir=raw_html_dir)
    doc_id = get_path(record, "doc_id", "") or ""
    if raw_html_files is None:
        raw_html_files = list(raw_html_dir.rglob("*.htm*")) if raw_html_dir.exists() else []
    doc_id_candidates = [p for p in raw_html_files if doc_id and doc_id in p.name][:1]
    return {
        "raw_html_path": raw_html_path,
        "raw_html_exists": bool(resolved),
        "raw_html_resolved_path": str(resolved) if resolved else "",
        "raw_html_nonempty": bool(resolved and resolved.exists() and resolved.stat().st_size > 0),
        "raw_html_valid_html": is_valid_html_file(resolved),
        "raw_html_path_repairable": bool((not resolved) and doc_id_candidates),
        "raw_html_doc_id_candidate": str(doc_id_candidates[0]) if doc_id_candidates else "",
    }
