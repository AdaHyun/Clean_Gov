from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pandas as pd

from .common import write_json, write_jsonl


def write_markdown(path: Path, title: str, sections: list[tuple[str, str]]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n")
        for heading, body in sections:
            f.write(f"## {heading}\n\n{body.strip()}\n\n")


def write_scan_outputs(output_dir: Path, manifest: list[dict], logs: dict[str, list[dict]], total_records: int):
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "logs").mkdir(parents=True, exist_ok=True)
    write_jsonl(output_dir / "repair_manifest.jsonl", manifest)
    pd.DataFrame(manifest).to_excel(output_dir / "repair_manifest.xlsx", index=False)
    for name, rows in logs.items():
        write_jsonl(output_dir / "logs" / f"{name}.jsonl", rows)
    missing_counter = Counter(mt for row in manifest for mt in row.get("missing_types", []))
    summary = {
        "total_records": total_records,
        "manifest_records": len(manifest),
        "missing_type_distribution": dict(missing_counter.most_common()),
        "priority_distribution": dict(Counter(row.get("priority") for row in manifest)),
    }
    write_json(output_dir / "scan_summary.json", summary)
    write_markdown(output_dir / "scan_report.md", "00_raw_repair scan 报告", [
        ("扫描结果", json.dumps(summary, ensure_ascii=False, indent=2)),
        ("阅读说明", "repair_manifest.jsonl/xlsx 是后续 repair 的输入；scan 阶段不下载、不请求网页、不修改 JSONL。"),
    ])


def write_repair_outputs(output_dir: Path, results: list[dict], failed: list[dict], manual: list[dict], logs: dict[str, list[dict]], copied_files: dict[str, int]):
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "logs").mkdir(parents=True, exist_ok=True)
    write_jsonl(output_dir / "repair_result.jsonl", results)
    write_jsonl(output_dir / "failed_after_repair.jsonl", failed)
    write_jsonl(output_dir / "manual_review_list.jsonl", manual)
    for name, rows in logs.items():
        write_jsonl(output_dir / "logs" / f"{name}.jsonl", rows)
    summary = {
        "manifest_records": len(results),
        "failed_after_repair": len(failed),
        "manual_review": len(manual),
        "repaired_jsonl_files": copied_files,
        "status_distribution": dict(Counter(row.get("repair_status") for row in results)),
    }
    write_json(output_dir / "repair_summary.json", summary)
    write_markdown(output_dir / "repair_report.md", "00_raw_repair repair 报告", [
        ("修复结果", json.dumps(summary, ensure_ascii=False, indent=2)),
        ("下一步", "请人工核验 repair_summary、failed_after_repair、manual_review_list 和 data/bodyClean/data/raw/*.jsonl。人工补文件后运行 python pipeline_00_raw_repair.py --mode verify。"),
    ])


def write_verify_outputs(output_dir: Path, results: list[dict], still_failed: list[dict], manual: list[dict], summary_extra: dict | None = None):
    summary_extra = summary_extra or {}
    write_jsonl(output_dir / "verify_result.jsonl", results)
    write_jsonl(output_dir / "still_failed_after_verify.jsonl", still_failed)
    write_jsonl(output_dir / "manual_review_list.updated.jsonl", manual)
    summary = {
        "verified_records": len(results),
        "still_failed_after_verify": len(still_failed),
        "manual_review": len(manual),
        "status_distribution": dict(Counter(row.get("verify_status") for row in results)),
        **summary_extra,
    }
    write_json(output_dir / "verify_summary.json", summary)
    write_markdown(output_dir / "verify_report.md", "00_raw_repair verify 报告", [
        ("核验结果", json.dumps(summary, ensure_ascii=False, indent=2)),
        ("下一步", "请人工确认 verify 结果。确认后运行 python pipeline_main.py 启动正式 clean pipeline。"),
    ])
