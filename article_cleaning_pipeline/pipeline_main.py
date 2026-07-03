from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from src.io.jsonl_writer import ensure_dir, write_json
from src.pipeline_paths import load_path_config, require_path, resolve_project_path

from src import main_stages


MAIN_STAGE_NAMES = [
    "01_profile",
    "02_validation",
    "03_normalization",
    "04_extraction",
    "05_text_cleaning",
    "06_structure",
    "07_tables",
    "08_assets",
    "09_sensitive",
    "10_dedup",
    "11_quality",
    "12_datatrove_export",
]

MAIN_TO_LEGACY = {
    "01_profile": "profile",
    "02_validation": "validation",
    "03_normalization": "normalization",
    "04_extraction": "extraction",
    "05_text_cleaning": "cleaning",
    "06_structure": "structure",
    "07_tables": "tables",
    "08_assets": "assets",
    "09_sensitive": "sensitive",
    "10_dedup": "dedup",
    "11_quality": "quality",
    "12_datatrove_export": "datatrove",
}

NEW_STAGE_DIRS = {
    "profile": "01_profile",
    "validation": "02_validation",
    "normalization": "03_normalization",
    "extraction": "04_extraction",
    "cleaning": "05_text_cleaning",
    "structure": "06_structure",
    "tables": "07_tables",
    "assets": "08_assets",
    "sensitive": "09_sensitive",
    "dedup": "10_dedup",
    "quality": "11_quality",
    "datatrove": "12_datatrove_export",
}


def configured_paths(args) -> tuple[Path, Path, Path]:
    config = load_path_config(args.config)
    raw_dir = resolve_project_path(args.input_raw_dir or require_path(config, "main_pipeline.input_raw_dir"))
    raw_html_dir = resolve_project_path(args.raw_html_dir or require_path(config, "crawler.raw_html_dir"))
    output_dir = resolve_project_path(args.output_dir or require_path(config, "main_pipeline.output_dir"))
    return raw_dir, raw_html_dir, output_dir


def ensure_repaired_input(raw_dir: Path):
    if not raw_dir.exists() or not list(raw_dir.glob("*.jsonl")):
        message = (
            f"未找到 {raw_dir}/*.jsonl。\n"
            "请先执行：\n"
            "python pipeline_00_raw_repair.py --mode scan\n"
            "python pipeline_00_raw_repair.py --mode repair\n"
            "人工核验和手工补文件后：\n"
            "python pipeline_00_raw_repair.py --mode verify\n\n"
            "确认 verify 结果后，再运行：\n"
            "python pipeline_main.py"
        )
        raise SystemExit(message)


def select_stages(args) -> list[str]:
    if args.stage:
        return [args.stage]
    if args.run_all_from and args.run_all_from != "01":
        raise SystemExit("--run-all-from 目前只支持 01")
    return MAIN_STAGE_NAMES


def main():
    parser = argparse.ArgumentParser(description="正式 clean pipeline 入口，默认执行 01-12，不包含 00_raw_repair")
    parser.add_argument("--stage", choices=MAIN_STAGE_NAMES)
    parser.add_argument("--run-all-from", choices=["01"], default="01")
    parser.add_argument("--config")
    parser.add_argument("--input-raw-dir")
    parser.add_argument("--raw-html-dir")
    parser.add_argument("--output-dir")
    args = parser.parse_args()

    raw_dir, raw_html_dir, output_dir = configured_paths(args)
    ensure_repaired_input(raw_dir)
    ensure_dir(output_dir)
    main_stages.STAGE_DIR_NAMES.update(NEW_STAGE_DIRS)
    cfg = main_stages.load_configs()
    stages = select_stages(args)
    manifest = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "input_raw_dir": str(raw_dir),
        "raw_html_dir": str(raw_html_dir),
        "output_dir": str(output_dir),
        "rule_version": "0.2.0",
        "note": "正式 clean pipeline 不包含 00_raw_repair",
        "stages": {},
    }
    for main_stage in stages:
        legacy_stage = MAIN_TO_LEGACY[main_stage]
        started = datetime.now()
        result = main_stages.STAGE_FUNCS[legacy_stage](raw_dir, raw_html_dir, output_dir, cfg)
        result["elapsed_seconds"] = round((datetime.now() - started).total_seconds(), 3)
        manifest["stages"][main_stage] = result
        if result.get("outputs"):
            write_json(Path(result["outputs"][0]) / "stage_summary.json", result)
        write_json(output_dir / "run_manifest.json", manifest)
    manifest["finished_at"] = datetime.now().isoformat(timespec="seconds")
    write_json(output_dir / "run_manifest.json", manifest)
    write_json(output_dir / "pipeline_summary.json", manifest)
    main_stages.write_top_reports(output_dir, manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
