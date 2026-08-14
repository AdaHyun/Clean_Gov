"""根据旧 batch 构建未处理文件硬链接目录。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.recovery import build_remaining_from_batch  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从已停止批次构建真正 remaining 的硬链接目录")
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--batch-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--link-mode", choices=("hardlink",), default="hardlink")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = build_remaining_from_batch(
        args.input_dir,
        args.batch_dir,
        args.output_dir,
        link_mode=args.link_mode,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
