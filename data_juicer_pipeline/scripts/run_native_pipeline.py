"""Run the configuration-first native Data-Juicer corpus pipeline."""

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

from native_pipeline import (  # noqa: E402
    NATIVE_LANES,
    NativePipelineOptions,
    run_native_pipeline,
)
from llm_provider import DATA_CLASSIFICATIONS  # noqa: E402
from paths import (  # noqa: E402
    DEFAULT_ATTACHMENT_DIR,
    LLM_ENV_FILE,
    LLM_PROVIDER_CONFIG,
    LLM_TAG_LABEL_CONFIG,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="原生 Data-Juicer 网页与附件语料流水线")
    parser.add_argument(
        "--web-input",
        type=Path,
        help="省略时读取 text_clean 输出目录中时间戳最新的 gov_corpus_clean_*.jsonl",
    )
    parser.add_argument("--attachment-root", type=Path, default=DEFAULT_ATTACHMENT_DIR)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="只扫描和统计，不写 prepared 或正式输出")
    parser.add_argument("--prepare-only", action="store_true", help="只生成 Data-Juicer 输入和隔离队列")
    parser.add_argument("--max-native-chars", type=int, default=3_000_000)
    parser.add_argument("--line-frequency-threshold", type=int, default=100)
    parser.add_argument("--min-text-length", type=int, default=50)
    parser.add_argument("--min-alnum-ratio", type=float, default=0.45)
    parser.add_argument("--max-special-char-ratio", type=float, default=0.75)
    parser.add_argument("--max-char-repetition-ratio", type=float, default=0.5)
    parser.add_argument(
        "--allowed-language",
        action="append",
        dest="allowed_languages",
        help="FastText 语言代码；可重复传入。默认只保留 zh",
    )
    parser.add_argument("--min-language-score", type=float, default=0.5)
    parser.add_argument(
        "--enable-llm-quality",
        action="store_true",
        help="启用 Stage 04 LLM 主题/质量标注和 Stage 05 本地噪声清理；默认关闭",
    )
    parser.add_argument(
        "--llm-quality-provider",
        choices=("company", "siliconflow"),
        help="省略时使用 configs/llm_providers.yaml 中的默认公司模型",
    )
    parser.add_argument("--llm-quality-min-score", type=float, default=0.6)
    parser.add_argument("--llm-topic-min-confidence", type=float, default=0.9)
    parser.add_argument("--llm-min-public-health-relevance", type=float, default=4.0)
    parser.add_argument("--llm-noise-min-confidence", type=float, default=0.9)
    parser.add_argument("--llm-noise-max-removed-ratio", type=float, default=0.3)
    parser.add_argument(
        "--llm-concurrency",
        type=int,
        default=16,
        help="Stage 04 公司模型并发请求数；默认 16",
    )
    parser.add_argument(
        "--data-classification",
        choices=DATA_CLASSIFICATIONS,
        default="restricted",
        help="默认 restricted；restricted/internal 禁止外部 LLM",
    )
    parser.add_argument(
        "--allow-external-llm",
        action="store_true",
        help="仅 public 数据选择外部提供商时的第二重显式授权",
    )
    parser.add_argument("--llm-provider-config", type=Path, default=LLM_PROVIDER_CONFIG)
    parser.add_argument("--llm-env-file", type=Path, default=LLM_ENV_FILE)
    parser.add_argument("--llm-tag-label-config", type=Path, default=LLM_TAG_LABEL_CONFIG)
    parser.add_argument("--only-lane", choices=sorted(NATIVE_LANES))
    parser.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(asctime)s %(levelname)s %(message)s")
    try:
        summary = run_native_pipeline(
            NativePipelineOptions(
                web_input=args.web_input,
                attachment_root=args.attachment_root,
                output_path=args.output,
                dry_run=args.dry_run,
                prepare_only=args.prepare_only,
                max_native_chars=args.max_native_chars,
                line_frequency_threshold=args.line_frequency_threshold,
                min_text_length=args.min_text_length,
                min_alnum_ratio=args.min_alnum_ratio,
                max_special_char_ratio=args.max_special_char_ratio,
                max_char_repetition_ratio=args.max_char_repetition_ratio,
                allowed_languages=tuple(args.allowed_languages or ("zh",)),
                min_language_score=args.min_language_score,
                enable_llm_quality=args.enable_llm_quality,
                llm_quality_provider=args.llm_quality_provider,
                llm_quality_min_score=args.llm_quality_min_score,
                llm_topic_min_confidence=args.llm_topic_min_confidence,
                llm_min_public_health_relevance=args.llm_min_public_health_relevance,
                llm_noise_min_confidence=args.llm_noise_min_confidence,
                llm_noise_max_removed_ratio=args.llm_noise_max_removed_ratio,
                llm_concurrency=args.llm_concurrency,
                data_classification=args.data_classification,
                allow_external_llm=args.allow_external_llm,
                llm_provider_config=args.llm_provider_config,
                llm_env_file=args.llm_env_file,
                llm_tag_label_config=args.llm_tag_label_config,
                only_lane=args.only_lane,
            ),
            command=[sys.executable, *sys.argv],
        )
    except Exception:
        logging.exception("原生 Data-Juicer 流水线失败")
        return 1
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
