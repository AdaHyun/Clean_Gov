"""Prepare web and attachment lanes without changing corpus text."""

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

from input_resolver import resolve_input  # noqa: E402
from native_preparation import PreparationOptions, prepare_inputs  # noqa: E402
from paths import DEFAULT_ATTACHMENT_DIR, resolve_from_clean_gov, run_directories  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="只准备原生 Data-Juicer 输入，不执行清洗")
    parser.add_argument(
        "--web-input",
        type=Path,
        help="省略时读取 text_clean 输出目录中时间戳最新的 gov_corpus_clean_*.jsonl",
    )
    parser.add_argument("--attachment-root", type=Path, default=DEFAULT_ATTACHMENT_DIR)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--max-native-chars", type=int, default=3_000_000)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    directories = run_directories(run_id)
    output_dir = (
        resolve_from_clean_gov(args.output_dir)
        if args.output_dir
        else directories["intermediate"] / "00_prepared"
    )
    result = prepare_inputs(
        PreparationOptions(
            web_input=resolve_input(args.web_input),
            attachment_root=resolve_from_clean_gov(args.attachment_root),
            output_dir=output_dir,
            max_native_chars=args.max_native_chars,
            write_outputs=not args.dry_run,
        )
    )
    print(json.dumps(result.summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
