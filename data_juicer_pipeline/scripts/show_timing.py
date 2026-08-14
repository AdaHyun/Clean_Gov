"""Display timing and throughput for the latest or a selected pipeline run."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from paths import LOG_DIR, RUNS_DIR  # noqa: E402
from pipeline_timing import format_duration  # noqa: E402


NATIVE_DURATION_RE = re.compile(r"Running executor took ([0-9.]+) seconds")


def _available_runs() -> dict[str, Path]:
    """Return run-id to log-directory mappings, preferring the new layout."""
    result: dict[str, Path] = {}
    for legacy_root in (LOG_DIR / "runs", LOG_DIR / "retries"):
        if legacy_root.is_dir():
            for path in legacy_root.iterdir():
                if path.is_dir():
                    result[path.name] = path
    if RUNS_DIR.is_dir():
        for root in RUNS_DIR.iterdir():
            log_dir = root / "logs"
            if root.is_dir() and log_dir.is_dir():
                result[root.name] = log_dir
    return result


def _latest_run(available: dict[str, Path]) -> tuple[str, Path]:
    if not available:
        raise FileNotFoundError(f"没有找到运行目录: {RUNS_DIR}")
    return max(available.items(), key=lambda item: item[1].stat().st_mtime)


def _load_structured(run_dir: Path) -> dict[str, Any] | None:
    path = run_dir / "timing_summary.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _legacy_summary(run_dir: Path) -> dict[str, Any]:
    operations: list[dict[str, Any]] = []
    for path in sorted(run_dir.glob("*.stderr.log")):
        matches = NATIVE_DURATION_RE.findall(path.read_text(encoding="utf-8", errors="replace"))
        if not matches:
            continue
        operations.append(
            {
                "name": path.name.removesuffix(".stderr.log"),
                "activity": "data_juicer",
                "input_document_count": None,
                "output_document_count": None,
                "duration_seconds": float(matches[-1]),
                "documents_per_second": None,
                "input_characters_per_second": None,
            }
        )
    native_total = sum(float(item["duration_seconds"]) for item in operations)
    config = run_dir / "run_config.json"
    summary = run_dir / "run_summary.json"
    total_duration: float | None = None
    if config.is_file() and summary.is_file():
        total_duration = max(summary.stat().st_mtime - config.stat().st_mtime, 0.0)
    return {
        "status": "legacy_log_estimate",
        "total_duration_seconds": total_duration,
        "total_duration_display": (
            format_duration(total_duration) if total_duration is not None else "未知"
        ),
        "duration_by_activity_seconds": {"data_juicer": round(native_total, 3)},
        "operations": operations,
        "legacy_estimate": True,
    }


def _value(value: Any, digits: int = 2) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _print_table(timing: dict[str, Any]) -> None:
    print("阶段/操作\t输入\t输出\t耗时(秒)\t条/秒\t输入字符/秒")
    for operation in timing.get("operations", []):
        print(
            "\t".join(
                (
                    str(operation.get("name", "-")),
                    _value(operation.get("input_document_count"), 0),
                    _value(operation.get("output_document_count"), 0),
                    _value(operation.get("duration_seconds")),
                    _value(operation.get("documents_per_second")),
                    _value(operation.get("input_characters_per_second")),
                )
            )
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="查看语料清洗耗时和吞吐量")
    parser.add_argument("--run-id", help="省略时查看修改时间最新的运行")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    available = _available_runs()
    if args.run_id:
        run_id = args.run_id
        run_dir = available.get(run_id)
        if run_dir is None:
            raise FileNotFoundError(f"运行ID不存在: {run_id}")
    else:
        run_id, run_dir = _latest_run(available)
    timing = _load_structured(run_dir)
    if timing is None:
        timing = _legacy_summary(run_dir)
    print(f"运行ID: {run_id}")
    if timing.get("legacy_estimate"):
        print("说明: 旧运行没有结构化计时；阶段耗时来自原生日志，总耗时由文件时间估算。")
    if args.json:
        print(json.dumps(timing, ensure_ascii=False, indent=2))
        return 0
    print(f"状态: {timing.get('status', '-')}")
    print(f"总耗时: {timing.get('total_duration_display', '-')}")
    activities = timing.get("duration_by_activity_seconds", {})
    if activities:
        print("分类耗时: " + "，".join(f"{key}={value:.2f}秒" for key, value in activities.items()))
    _print_table(timing)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
