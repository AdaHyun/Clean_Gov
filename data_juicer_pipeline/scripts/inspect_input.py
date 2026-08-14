"""Inspect a JSONL and write Stage 01 preflight reports."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from input_inspection import inspect_input  # noqa: E402
from input_resolver import resolve_input  # noqa: E402
from jsonl_io import iter_jsonl, write_jsonl_record  # noqa: E402
from paths import resolve_from_clean_gov, run_directories  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="逐行体检 Stage 01 JSONL 输入")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--sample-count", type=int, default=50)
    parser.add_argument("--top-lines", type=int, default=5000)
    parser.add_argument("--encoding", default="utf-8-sig")
    parser.add_argument("--restore-literal-newlines", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    input_path = resolve_input(args.input)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    directories = run_directories(run_id)
    report_dir = (
        resolve_from_clean_gov(args.report_dir) / run_id
        if args.report_dir
        else directories["reports"]
    )
    result = inspect_input(input_path, report_dir, sample_count=args.sample_count, top_lines=args.top_lines, encoding=args.encoding)
    if args.restore_literal_newlines and result.summary.get("literal_newline_likely_escaped"):
        destination = directories["intermediate"] / "normalized_input.jsonl"
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8") as handle:
            for _, record in iter_jsonl(input_path, encoding=args.encoding):
                if isinstance(record.get("text"), str):
                    record["text"] = record["text"].replace("\\n", "\n")
                write_jsonl_record(handle, record)
        result.summary["restored_literal_newlines_output"] = str(destination)
        (report_dir / "input_inspection_summary.json").write_text(json.dumps(result.summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report_dir": str(report_dir), "summary": result.summary}, ensure_ascii=False, indent=2))
    return 2 if result.issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
