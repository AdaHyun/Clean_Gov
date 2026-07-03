from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import sys
from pathlib import Path

from src.pipeline_paths import ROOT, load_path_config, require_path, resolve_project_path


RAW_REPAIR_DIR = ROOT / "src" / "00_raw_repair"


def load_raw_repair_package():
    """Load src/00_raw_repair as runtime package raw_repair."""
    if "raw_repair" in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(
        "raw_repair",
        RAW_REPAIR_DIR / "__init__.py",
        submodule_search_locations=[str(RAW_REPAIR_DIR)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 src/00_raw_repair")
    module = importlib.util.module_from_spec(spec)
    sys.modules["raw_repair"] = module
    spec.loader.exec_module(module)


def configured_paths(args) -> dict:
    config = load_path_config(args.config)
    raw_repair_output = resolve_project_path(args.output_dir or require_path(config, "raw_repair.output_dir"))
    paths = {
        "crawler_root": resolve_project_path(args.crawler_root or require_path(config, "crawler.crawler_root")),
        "jsonl_dir": resolve_project_path(args.jsonl_dir or require_path(config, "crawler.jsonl_dir")),
        "raw_html_dir": resolve_project_path(args.raw_html_dir or require_path(config, "crawler.raw_html_dir")),
        "image_root_dir": resolve_project_path(args.image_root_dir or require_path(config, "crawler.image_root_dir")),
        "attachment_root_dir": resolve_project_path(args.attachment_root_dir or require_path(config, "crawler.attachment_root_dir")),
        "output_dir": raw_repair_output,
        "manifest": resolve_project_path(args.manifest or require_path(config, "raw_repair.manifest")),
        "output_raw_dir": resolve_project_path(args.output_raw_dir or require_path(config, "raw_repair.output_raw_dir")),
        "manual_asset_map": resolve_project_path(args.manual_asset_map or require_path(config, "raw_repair.manual_asset_map")),
    }
    return paths


def main():
    parser = argparse.ArgumentParser(description="独立 00_raw_repair 修复入口")
    parser.add_argument("--mode", choices=["scan", "repair", "verify"], required=True)
    parser.add_argument("--config")
    parser.add_argument("--manifest")
    parser.add_argument("--manual-asset-map")
    parser.add_argument("--jsonl-dir")
    parser.add_argument("--raw-html-dir")
    parser.add_argument("--image-root-dir")
    parser.add_argument("--attachment-root-dir")
    parser.add_argument("--crawler-root")
    parser.add_argument("--output-dir")
    parser.add_argument("--output-raw-dir")
    args = parser.parse_args()

    load_raw_repair_package()
    paths = configured_paths(args)

    if args.mode == "scan":
        scanner = importlib.import_module("raw_repair.scanner")
        result = scanner.run_scan(
            paths["jsonl_dir"],
            paths["raw_html_dir"],
            paths["image_root_dir"],
            paths["attachment_root_dir"],
            paths["output_dir"],
            paths["crawler_root"],
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print("\n00_raw_repair scan finished. Review repair_manifest.jsonl/xlsx, then run:\npython pipeline_00_raw_repair.py --mode repair")
        return

    if args.mode == "repair":
        repair_runner = importlib.import_module("raw_repair.repair_runner")
        result = repair_runner.run_repair(paths["manifest"], paths["output_dir"], paths["output_raw_dir"], paths["crawler_root"], paths["raw_html_dir"])
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print(
            "\n00_raw_repair repair finished.\n\n"
            "Please manually review:\n"
            "- data/bodyClean/00_raw_repair/repair_summary.json\n"
            "- data/bodyClean/00_raw_repair/repair_report.md\n"
            "- data/bodyClean/00_raw_repair/failed_after_repair.jsonl\n"
            "- data/bodyClean/00_raw_repair/manual_review_list.jsonl\n"
            "- data/bodyClean/data/raw/*.jsonl\n\n"
            "If you manually fix missing files, run:\n"
            "python pipeline_00_raw_repair.py --mode verify\n\n"
            "After verify and manual confirmation, run:\n"
            "python pipeline_main.py"
        )
        return

    verifier = importlib.import_module("raw_repair.verifier")
    result = verifier.run_verify(
        paths["output_raw_dir"],
        paths["output_dir"],
        paths["crawler_root"],
        paths["raw_html_dir"],
        paths["image_root_dir"],
        paths["attachment_root_dir"],
        paths["manual_asset_map"] if paths["manual_asset_map"].exists() else None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(
        "\n00_raw_repair verify finished.\n\n"
        "Please manually review:\n"
        "- data/bodyClean/00_raw_repair/verify_summary.json\n"
        "- data/bodyClean/00_raw_repair/verify_report.md\n"
        "- data/bodyClean/00_raw_repair/still_failed_after_verify.jsonl\n"
        "- data/bodyClean/00_raw_repair/manual_review_list.updated.jsonl\n"
        "- data/bodyClean/data/raw/*.jsonl\n\n"
        "After manual confirmation, run:\n"
        "python pipeline_main.py"
    )


if __name__ == "__main__":
    main()
