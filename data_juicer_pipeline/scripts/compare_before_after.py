"""Compare two JSONL files without overwriting either one."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from result_comparison import compare_files  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="比较清洗前后 JSONL")
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--sample-count", type=int, default=50)
    args = parser.parse_args()
    result = compare_files(args.before.resolve(), args.after.resolve(), args.report_dir.resolve(), sample_count=args.sample_count)
    print(json.dumps(result.summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
