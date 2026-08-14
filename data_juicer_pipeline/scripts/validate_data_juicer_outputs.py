"""Validate one prepared/native-output pair and retain removed records."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from native_validation import validate_native_output  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="校验原生 Data-Juicer 单通道输出")
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--removed-output", type=Path, required=True)
    args = parser.parse_args()
    summary = validate_native_output(args.before, args.after, args.report, args.removed_output)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
