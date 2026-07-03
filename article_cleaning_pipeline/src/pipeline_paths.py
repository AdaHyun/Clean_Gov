from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


DEFAULT_PATH_CONFIG = {
    "project": {"output_root": "data/bodyClean"},
    "crawler": {
        "crawler_root": "../Crawler_Gov",
        "jsonl_dir": "../Crawler_Gov/data/output",
        "raw_html_dir": "../Crawler_Gov/data/raw_html",
        "image_root_dir": "../Crawler_Gov/data/images",
        "attachment_root_dir": "../Crawler_Gov/data/attachments",
    },
    "raw_repair": {
        "output_dir": "data/bodyClean/00_raw_repair",
        "manifest": "data/bodyClean/00_raw_repair/repair_manifest.jsonl",
        "output_raw_dir": "data/bodyClean/data/raw",
        "manual_asset_map": "data/bodyClean/00_raw_repair/manual_asset_map.xlsx",
    },
    "main_pipeline": {
        "input_raw_dir": "data/bodyClean/data/raw",
        "output_dir": "data/bodyClean",
    },
}


def _deep_update(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_update(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_path_config(config_path: str | Path | None = None) -> dict:
    path = Path(config_path) if config_path else ROOT / "configs" / "path_config.yaml"
    loaded = {}
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
    return _deep_update(DEFAULT_PATH_CONFIG, loaded)


def resolve_project_path(value: str | Path, root: Path = ROOT) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    if path.parts and path.parts[0] == "data":
        return (root.parent / path).resolve()
    primary = (root / path).resolve()
    if primary.exists():
        return primary
    # Also support paths written relative to Clean_Gov/ instead of article_cleaning_pipeline/.
    secondary = (root.parent / path).resolve()
    if secondary.exists():
        return secondary
    return primary


def require_path(config: dict, dotted: str) -> str:
    cur = config
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur or cur[part] in ("", None):
            raise ValueError(f"缺少必要路径配置：{dotted}")
        cur = cur[part]
    return str(cur)
