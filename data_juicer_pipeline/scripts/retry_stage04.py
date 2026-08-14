"""Retry only the failed Stage 04 queue from one successful native run."""

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

from llm_provider import DATA_CLASSIFICATIONS  # noqa: E402
from llm_retry_pipeline import LLMRetryOptions, run_llm_retry  # noqa: E402
from paths import LLM_ENV_FILE, LLM_PROVIDER_CONFIG, LLM_TAG_LABEL_CONFIG  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="只重试指定成功运行中的 Stage 04 LLM 失败队列"
    )
    parser.add_argument("--run-id", required=True, help="原运行ID，如 20260806_152852_937068")
    parser.add_argument("--retry-input", type=Path)
    parser.add_argument("--base-output", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="只统计重试队列，不写文件、不调用API")
    parser.add_argument("--prepare-only", action="store_true", help="只生成清除旧评分后的首轮输入，不调用API")
    parser.add_argument("--max-direct-chars", type=int, default=20_000)
    parser.add_argument("--sample-chars-per-section", type=int, default=6_000)
    parser.add_argument("--max-rounds", type=int, default=2)
    parser.add_argument("--llm-concurrency", type=int, default=16)
    parser.add_argument("--llm-quality-provider", choices=("company", "siliconflow"))
    parser.add_argument("--llm-quality-min-score", type=float, default=0.6)
    parser.add_argument("--llm-topic-min-confidence", type=float, default=0.9)
    parser.add_argument("--llm-min-public-health-relevance", type=float, default=4.0)
    parser.add_argument("--llm-noise-min-confidence", type=float, default=0.9)
    parser.add_argument("--llm-noise-max-removed-ratio", type=float, default=0.3)
    parser.add_argument("--min-remaining-characters", type=int, default=50)
    parser.add_argument(
        "--data-classification", choices=DATA_CLASSIFICATIONS, default="restricted"
    )
    parser.add_argument("--allow-external-llm", action="store_true")
    parser.add_argument("--llm-provider-config", type=Path, default=LLM_PROVIDER_CONFIG)
    parser.add_argument("--llm-env-file", type=Path, default=LLM_ENV_FILE)
    parser.add_argument("--llm-tag-label-config", type=Path, default=LLM_TAG_LABEL_CONFIG)
    parser.add_argument(
        "--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO"
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        summary = run_llm_retry(
            LLMRetryOptions(
                source_run_id=args.run_id,
                retry_input=args.retry_input,
                base_output=args.base_output,
                output_path=args.output,
                dry_run=args.dry_run,
                prepare_only=args.prepare_only,
                max_direct_chars=args.max_direct_chars,
                sample_chars_per_section=args.sample_chars_per_section,
                max_rounds=args.max_rounds,
                llm_concurrency=args.llm_concurrency,
                llm_quality_provider=args.llm_quality_provider,
                llm_quality_min_score=args.llm_quality_min_score,
                llm_topic_min_confidence=args.llm_topic_min_confidence,
                llm_min_public_health_relevance=args.llm_min_public_health_relevance,
                llm_noise_min_confidence=args.llm_noise_min_confidence,
                llm_noise_max_removed_ratio=args.llm_noise_max_removed_ratio,
                min_remaining_characters=args.min_remaining_characters,
                data_classification=args.data_classification,
                allow_external_llm=args.allow_external_llm,
                llm_provider_config=args.llm_provider_config,
                llm_env_file=args.llm_env_file,
                llm_tag_label_config=args.llm_tag_label_config,
            ),
            command=[sys.executable, *sys.argv],
        )
    except Exception:
        logging.exception("Stage 04失败队列重试失败")
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
