"""Command-line entry point for Stage 01."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stage01_pipeline import PipelineBlocked, StageOptions, run_stage01  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="第一阶段：保守清洁网页文章 text 字段")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--frequency-threshold", type=int)
    parser.add_argument("--frequency-threshold-map", type=Path)
    parser.add_argument("--allow-small-group", action="store_true")
    parser.add_argument("--restore-literal-newlines", action="store_true")
    parser.add_argument("--sample-count", type=int, default=50)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--skip-data-juicer", action="store_true")
    parser.add_argument("--only-group")
    parser.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.frequency_threshold is not None and args.frequency_threshold < 1:
        raise SystemExit("--frequency-threshold 必须是正整数")
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(asctime)s %(levelname)s %(message)s")
    options = StageOptions(
        input_path=args.input,
        output_path=args.output,
        work_dir=args.work_dir,
        report_dir=args.report_dir,
        dry_run=args.dry_run,
        frequency_threshold=args.frequency_threshold,
        frequency_threshold_map=args.frequency_threshold_map,
        allow_small_group=args.allow_small_group,
        restore_literal_newlines=args.restore_literal_newlines,
        sample_count=args.sample_count,
        force=args.force,
        skip_data_juicer=args.skip_data_juicer,
        only_group=args.only_group,
        log_level=args.log_level,
    )
    try:
        summary = run_stage01(options, command=[sys.executable, *sys.argv])
    except PipelineBlocked as exc:
        logging.error("运行被安全规则阻止：%s", exc)
        return 2
    except Exception:
        logging.exception("Stage 01 运行失败")
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
