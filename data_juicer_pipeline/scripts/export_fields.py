"""Export arbitrary selected fields from a final corpus JSONL."""

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

from field_export import (  # noqa: E402
    MISSING_POLICIES,
    export_selected_fields,
    parse_field_spec,
    resolve_final_corpus,
)
from paths import resolve_from_clean_gov  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="从最终语料中选择、重命名任意数量字段，生成新的 JSONL"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--run-id", help="从指定 run 的 output 自动找到正式结果")
    source.add_argument("--input", type=Path, help="显式指定最终 JSONL；相对 Clean_Gov 解析")
    parser.add_argument(
        "--fields",
        required=True,
        help="逗号分隔；直接保留写 title,text，重命名写 id=doc_id",
    )
    parser.add_argument("--output", type=Path, help="省略时写入源 output/exports 目录")
    parser.add_argument(
        "--missing",
        choices=MISSING_POLICIES,
        default="error",
        help="字段缺失策略：报错、写 null 或跳过整条；默认 error",
    )
    parser.add_argument("--force", action="store_true", help="明确允许覆盖已有导出文件和摘要")
    parser.add_argument("--progress-every", type=int, default=10_000)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        input_path = resolve_final_corpus(run_id=args.run_id, input_path=args.input)
        output_path = resolve_from_clean_gov(args.output) if args.output else None
        summary = export_selected_fields(
            input_path,
            output_path,
            parse_field_spec(args.fields),
            missing_policy=args.missing,
            force=args.force,
            progress_every=args.progress_every,
        )
    except Exception:
        logging.exception("最终语料字段适配失败")
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
