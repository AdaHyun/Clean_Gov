from __future__ import annotations

from pathlib import Path

from src.utils import resolve_existing_path
from .url_normalizer import canonical_url


def normalize_asset(asset, idx: int, kind: str, raw_html_dir: Path, project_root: Path) -> dict:
    if not isinstance(asset, dict):
        return {"asset_id": f"{kind}_{idx}", "raw_value": asset, "path_needs_fix": True}
    normalized = dict(asset)
    local = str(normalized.get("local_path", ""))
    ext = (normalized.get("file_ext") or normalized.get("file_type") or Path(local).suffix.replace(".", "")).lower()
    normalized[f"{kind}_id"] = normalized.get(f"{kind}_id") or f"{kind}_{idx:03d}"
    normalized["canonical_url"] = canonical_url(normalized.get("url", ""))
    normalized["file_ext"] = ext
    normalized["file_type"] = ext.upper() if ext else ""
    resolved = resolve_existing_path(raw_html_dir, project_root, local)
    normalized["local_path_exists"] = bool(resolved)
    normalized["resolved_local_path"] = resolved
    normalized["download_status"] = normalized.get("download_status") or ("downloaded" if resolved else "missing")
    normalized["path_needs_fix"] = bool(local and not resolved)
    return normalized
