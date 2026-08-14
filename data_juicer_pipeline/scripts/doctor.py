"""Diagnose the exact Python/Data-Juicer runtime used for Stage 01."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from runtime import collect_environment  # noqa: E402
from llm_provider import inspect_llm_settings  # noqa: E402
from paths import LLM_ENV_FILE, LLM_PROVIDER_CONFIG  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="检查固定 Data-Juicer 环境")
    parser.add_argument("--output", type=Path, help="可选 JSON 输出路径")
    parser.add_argument("--llm-provider", choices=("company", "siliconflow"))
    args = parser.parse_args()
    result = collect_environment(test_cli=True)
    result["llm_quality"] = inspect_llm_settings(
        LLM_PROVIDER_CONFIG,
        LLM_ENV_FILE,
        args.llm_provider,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
