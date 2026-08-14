"""Retry only Stage 04 LLM failures and safely revise a completed corpus."""

from __future__ import annotations

import json
import re
import shutil
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import yaml

from data_juicer_runner import build_native_config, run_data_juicer, write_config
from jsonl_io import iter_jsonl, write_jsonl_record
from llm_policy import apply_local_noise_cleanup, partition_topic_annotations
from llm_provider import (
    DATA_CLASSIFICATIONS,
    LLMProvider,
    inspect_llm_settings,
    resolve_llm_provider,
)
from native_pipeline import _merge_outputs
from native_validation import validate_native_output
from paths import (
    EXPECTED_DATA_JUICER_VERSION,
    LLM_ENV_FILE,
    LLM_PROVIDER_CONFIG,
    LLM_TAG_LABEL_CONFIG,
    NATIVE_CONFIG_DIR,
    resolve_from_clean_gov,
    resolve_existing_run_paths,
    run_directories,
)
from pipeline_timing import TimingRecorder


LLM_TEMPLATE = NATIVE_CONFIG_DIR / "llm_topic_quality.yaml"
RUN_ID_PATTERN = re.compile(r"^\d{8}_\d{6}_\d{6}$")
RETRY_SYSTEM_APPENDIX = """

这是失败队列的结构化重试。上一次结果未通过机器校验。你必须输出一个合法JSON对象，且dimension_scores必须完整包含accuracy、grammar、informativeness、coherence、rigor五个1至5整数；不得省略、改名或输出空对象。不要输出Markdown代码块。
""".strip()


@dataclass(frozen=True)
class LLMRetryOptions:
    source_run_id: str
    retry_input: Path | None = None
    base_output: Path | None = None
    output_path: Path | None = None
    dry_run: bool = False
    prepare_only: bool = False
    max_direct_chars: int = 20_000
    sample_chars_per_section: int = 6_000
    max_rounds: int = 2
    llm_concurrency: int = 16
    llm_quality_provider: str | None = None
    llm_quality_min_score: float = 0.6
    llm_topic_min_confidence: float = 0.9
    llm_min_public_health_relevance: float = 4.0
    llm_noise_min_confidence: float = 0.9
    llm_noise_max_removed_ratio: float = 0.3
    min_remaining_characters: int = 50
    data_classification: str = "restricted"
    allow_external_llm: bool = False
    llm_provider_config: Path = LLM_PROVIDER_CONFIG
    llm_env_file: Path = LLM_ENV_FILE
    llm_tag_label_config: Path = LLM_TAG_LABEL_CONFIG


def _runtime_version() -> str:
    try:
        return version("py-data-juicer")
    except PackageNotFoundError as exc:
        raise RuntimeError("当前 Python 未安装 py-data-juicer，请使用 dj-env 运行") from exc


def _retry_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def _family(record: dict[str, Any]) -> str:
    lane = str(record.get("native_pipeline_lane") or "")
    if lane.startswith("web_"):
        return "web"
    if lane.startswith("attachment_"):
        return "attachment"
    return "unknown"


def _cut_head(text: str, length: int) -> str:
    value = text[:length]
    if len(text) > length and "\n" in value:
        value = value.rsplit("\n", 1)[0]
    return value


def _cut_tail(text: str, length: int) -> str:
    value = text[-length:]
    if len(text) > length and "\n" in value:
        value = value.split("\n", 1)[-1]
    return value


def build_llm_review_text(
    text: str,
    *,
    max_direct_chars: int,
    sample_chars_per_section: int,
) -> tuple[str, str]:
    """Keep short text intact; build a bounded, deterministic view for long text."""
    if len(text) <= max_direct_chars:
        return text, "full_text"
    width = sample_chars_per_section
    midpoint = len(text) // 2
    middle_start = max(midpoint - width // 2, 0)
    middle = text[middle_start : middle_start + width]
    if "\n" in middle:
        parts = middle.split("\n")
        if len(parts) > 2:
            middle = "\n".join(parts[1:-1])
    review = (
        "【长文抽样审阅说明】以下仅为原文的开头、中部和结尾抽样，"
        "仅用于主题与质量判断；不得据此定位或删除栏目噪声，noise_segments必须输出空数组。\n"
        "【原文开头】\n"
        f"{_cut_head(text, width)}\n"
        "【原文中部】\n"
        f"{middle}\n"
        "【原文结尾】\n"
        f"{_cut_tail(text, width)}"
    )
    return review, "representative_sample"


def _clean_retry_record(
    record: dict[str, Any],
    *,
    source_run_id: str,
    round_number: int,
    max_direct_chars: int,
    sample_chars_per_section: int,
) -> tuple[dict[str, Any], str]:
    cleaned = dict(record)
    for key in list(cleaned):
        if key.startswith("llm_") or key in {"quarantine_reason", "quarantine_stage"}:
            cleaned.pop(key, None)
    stats = cleaned.get("__dj__stats__")
    normalized_stats = dict(stats) if isinstance(stats, dict) else {}
    normalized_stats.pop("llm_quality_score", None)
    normalized_stats.pop("llm_quality_record", None)
    normalized_stats.pop("llm_quality_tags", None)
    cleaned["__dj__stats__"] = normalized_stats
    text = cleaned.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"重试记录正文为空: {cleaned.get('doc_id', '<unknown>')}")
    review_text, mode = build_llm_review_text(
        text,
        max_direct_chars=max_direct_chars,
        sample_chars_per_section=sample_chars_per_section,
    )
    cleaned.update(
        {
            "llm_review_text": review_text,
            "llm_retry_source_run_id": source_run_id,
            "llm_retry_round": round_number,
            "llm_retry_input_mode": mode,
            "llm_retry_input_mode_zh": (
                "完整正文重试" if mode == "full_text" else "长文首中尾确定性抽样重试"
            ),
            "llm_retry_source_text_length": len(text),
        }
    )
    return cleaned, mode


def prepare_retry_input(
    source_path: Path,
    target_path: Path | None,
    *,
    source_run_id: str,
    round_number: int,
    max_direct_chars: int,
    sample_chars_per_section: int,
) -> dict[str, Any]:
    if target_path is not None:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if target_path.exists():
            raise FileExistsError(f"重试输入已存在，不允许覆盖: {target_path}")
    counts: Counter[str] = Counter()
    families: Counter[str] = Counter()
    doc_ids: set[str] = set()
    output_handle = target_path.open("w", encoding="utf-8") if target_path else None
    try:
        for _, record in iter_jsonl(source_path):
            doc_id = str(record.get("doc_id") or "").strip()
            if not doc_id:
                raise ValueError("重试队列存在缺少 doc_id 的记录")
            if doc_id in doc_ids:
                raise ValueError(f"重试队列 doc_id 重复: {doc_id}")
            doc_ids.add(doc_id)
            cleaned, mode = _clean_retry_record(
                record,
                source_run_id=source_run_id,
                round_number=round_number,
                max_direct_chars=max_direct_chars,
                sample_chars_per_section=sample_chars_per_section,
            )
            counts["input"] += 1
            counts[mode] += 1
            families[_family(cleaned)] += 1
            if output_handle is not None:
                write_jsonl_record(output_handle, cleaned)
    finally:
        if output_handle is not None:
            output_handle.close()
    return {
        "input_document_count": counts["input"],
        "full_text_count": counts["full_text"],
        "representative_sample_count": counts["representative_sample"],
        "source_counts": dict(families),
        "source_path": str(source_path.resolve()),
        "prepared_path": str(target_path.resolve()) if target_path else None,
        "round": round_number,
        "max_direct_chars": max_direct_chars,
        "sample_chars_per_section": sample_chars_per_section,
    }


def _concat_jsonl(paths: list[Path], target: Path) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(f"汇总文件已存在，不允许覆盖: {target}")
    count = 0
    with target.open("w", encoding="utf-8") as target_handle:
        for path in paths:
            for _, record in iter_jsonl(path):
                write_jsonl_record(target_handle, record)
                count += 1
    return count


def _template_system_prompt() -> str:
    raw = yaml.safe_load(LLM_TEMPLATE.read_text(encoding="utf-8"))
    for step in raw.get("process", []):
        if isinstance(step, dict) and "llm_quality_score_filter" in step:
            return str(step["llm_quality_score_filter"].get("system_prompt") or "")
    raise ValueError(f"模板缺少 llm_quality_score_filter: {LLM_TEMPLATE}")


def _execute_retry_round(
    *,
    retry_id: str,
    round_number: int,
    input_path: Path,
    input_count: int,
    provider: LLMProvider,
    options: LLMRetryOptions,
    directories: dict[str, Path],
) -> dict[str, Any]:
    round_name = f"round_{round_number:02d}"
    round_dir = directories["intermediate"] / round_name
    report_dir = directories["reports"] / round_name
    quarantine_dir = directories["quarantine"] / round_name
    work_dir = round_dir / "data_juicer_work"
    for path in (round_dir, report_dir, quarantine_dir, work_dir):
        path.mkdir(parents=True, exist_ok=True)
    scored_path = round_dir / "scored.jsonl"
    config_path = round_dir / f"04_llm_retry_{round_name}.yaml"
    system_prompt = _template_system_prompt()
    if round_number > 1:
        system_prompt += "\n\n" + RETRY_SYSTEM_APPENDIX
    config = build_native_config(
        LLM_TEMPLATE,
        input_path,
        scored_path,
        work_dir,
        project_name=f"retry_{retry_id}_{round_name}",
        num_proc=options.llm_concurrency,
        operator_overrides={
            "llm_quality_score_filter": {
                "api_or_hf_model": provider.model,
                "min_score": 0.0,
                "max_score": 1.0,
                "api_endpoint": provider.endpoint,
                "response_path": provider.response_path,
                "sampling_params": provider.sampling_params,
                "input_keys": ["title", "llm_review_text"],
                "field_names": ["文档标题", "政府公共卫生语料审阅文本"],
                "system_prompt": system_prompt,
            }
        },
    )
    write_config(config_path, config)
    run = run_data_juicer(
        config_path,
        scored_path,
        directories["logs"],
        environment_overrides=provider.child_environment(),
        progress_label=f"Stage 04 Retry {round_number} | LLM主题与质量重试",
        expected_total=input_count,
    )
    if run.return_code != 0 or not scored_path.is_file():
        raise RuntimeError(
            f"Stage 04 第{round_number}轮重试失败，返回码 {run.return_code}; "
            f"查看 {run.stderr_path}"
        )
    validation = validate_native_output(
        input_path,
        scored_path,
        report_dir / "scoring_validation.json",
        quarantine_dir / "unexpected_native_removal.jsonl",
        quarantine_reason="unexpected_removal_during_llm_retry",
        stage=f"04_llm_retry_round_{round_number:02d}",
    )
    if validation["removed_document_count"] != 0:
        raise RuntimeError(
            f"第{round_number}轮LLM重试意外移除了记录，已停止；"
            f"查看 {report_dir / 'scoring_validation.json'}"
        )
    paths = {
        "annotated": round_dir / "annotated_zh.jsonl",
        "candidate": round_dir / "candidate_keep.jsonl",
        "topic_excluded": quarantine_dir / "topic_excluded.jsonl",
        "low_quality": quarantine_dir / "low_quality.jsonl",
        "retry": quarantine_dir / "still_failed.jsonl",
        "review": quarantine_dir / "manual_review_required.jsonl",
    }
    partition = partition_topic_annotations(
        scored_path,
        paths["annotated"],
        paths["candidate"],
        paths["topic_excluded"],
        paths["low_quality"],
        paths["retry"],
        paths["review"],
        report_dir / "topic_quality.json",
        resolve_from_clean_gov(options.llm_tag_label_config),
        min_quality_score=options.llm_quality_min_score,
        min_topic_confidence=options.llm_topic_min_confidence,
        min_public_health_relevance=options.llm_min_public_health_relevance,
        provider_name=provider.name,
    )
    return {
        "round": round_number,
        "input": str(input_path),
        "config": str(config_path),
        "scored": str(scored_path),
        "duration_seconds": run.duration_seconds,
        "validation": validation,
        "partition": partition,
        "paths": {key: str(value) for key, value in paths.items()},
    }


def _prepare_cleanup_candidates(source: Path, target: Path) -> dict[str, int]:
    if target.exists():
        raise FileExistsError(f"Stage 05重试输入已存在，不允许覆盖: {target}")
    counts: Counter[str] = Counter()
    with target.open("w", encoding="utf-8") as handle:
        for _, record in iter_jsonl(source):
            prepared = dict(record)
            prepared.pop("llm_review_text", None)
            if prepared.get("llm_retry_input_mode") == "representative_sample":
                prepared["llm_noise_segments"] = []
                prepared["llm_retry_noise_policy"] = "deferred_for_sampled_long_document"
                prepared["llm_retry_noise_policy_zh"] = (
                    "长文抽样仅用于主题质量判断，本轮不依据不完整抽样删除栏目噪声"
                )
                counts["sampled_noise_deferred"] += 1
            else:
                counts["full_text_noise_checked"] += 1
            write_jsonl_record(handle, prepared)
            counts["input"] += 1
    return dict(counts)


def _ensure_disjoint_doc_ids(base_path: Path, recovered_path: Path) -> dict[str, int]:
    base_ids: set[str] = set()
    for _, record in iter_jsonl(base_path):
        doc_id = str(record.get("doc_id") or "")
        if not doc_id or doc_id in base_ids:
            raise ValueError(f"原正式结果存在空或重复 doc_id: {doc_id!r}")
        base_ids.add(doc_id)
    recovered_ids: set[str] = set()
    for _, record in iter_jsonl(recovered_path):
        doc_id = str(record.get("doc_id") or "")
        if not doc_id or doc_id in recovered_ids:
            raise ValueError(f"重试恢复结果存在空或重复 doc_id: {doc_id!r}")
        if doc_id in base_ids:
            raise ValueError(f"重试恢复记录已经存在于原正式结果: {doc_id}")
        recovered_ids.add(doc_id)
    return {"base_count": len(base_ids), "recovered_count": len(recovered_ids)}


def _validate_options(options: LLMRetryOptions) -> None:
    if not RUN_ID_PATTERN.fullmatch(options.source_run_id):
        raise ValueError("source_run_id 必须是 YYYYMMDD_HHMMSS_ffffff 格式")
    for name, value in (
        ("max_direct_chars", options.max_direct_chars),
        ("sample_chars_per_section", options.sample_chars_per_section),
        ("max_rounds", options.max_rounds),
        ("llm_concurrency", options.llm_concurrency),
        ("min_remaining_characters", options.min_remaining_characters),
    ):
        if value < 1:
            raise ValueError(f"{name} 必须是正整数")
    for name, value in (
        ("llm_quality_min_score", options.llm_quality_min_score),
        ("llm_topic_min_confidence", options.llm_topic_min_confidence),
        ("llm_noise_min_confidence", options.llm_noise_min_confidence),
        ("llm_noise_max_removed_ratio", options.llm_noise_max_removed_ratio),
    ):
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{name} 必须位于 [0, 1]")
    if options.llm_quality_min_score <= 0.0:
        raise ValueError("llm_quality_min_score 必须大于0")
    if not 1.0 <= options.llm_min_public_health_relevance <= 5.0:
        raise ValueError("llm_min_public_health_relevance 必须位于 [1, 5]")
    if options.sample_chars_per_section * 3 > options.max_direct_chars:
        raise ValueError("sample_chars_per_section的三倍不能超过max_direct_chars")
    if options.data_classification not in DATA_CLASSIFICATIONS:
        raise ValueError(f"未知数据保密级别: {options.data_classification}")


def run_llm_retry(
    options: LLMRetryOptions,
    *,
    command: list[str] | None = None,
) -> dict[str, Any]:
    _validate_options(options)
    runtime = _runtime_version()
    if runtime != EXPECTED_DATA_JUICER_VERSION:
        raise RuntimeError(
            f"py-data-juicer版本不符: {runtime}; 需要 {EXPECTED_DATA_JUICER_VERSION}"
        )
    source_paths = resolve_existing_run_paths(options.source_run_id)
    retry_input = (
        resolve_from_clean_gov(options.retry_input)
        if options.retry_input
        else source_paths["quarantine"] / "04_llm_retry_required.jsonl"
    )
    base_output = (
        resolve_from_clean_gov(options.base_output)
        if options.base_output
        else source_paths["output"]
        / f"corpus_native_cleaned_{options.source_run_id}.jsonl"
    )
    if not retry_input.is_file():
        raise FileNotFoundError(f"Stage 04失败队列不存在: {retry_input}")
    if not base_output.is_file():
        raise FileNotFoundError(f"原正式结果不存在: {base_output}")
    provider_config = resolve_from_clean_gov(options.llm_provider_config)
    env_file = resolve_from_clean_gov(options.llm_env_file)
    label_config = resolve_from_clean_gov(options.llm_tag_label_config)
    if not label_config.is_file():
        raise FileNotFoundError(f"LLM中文标签配置不存在: {label_config}")
    provider_status = inspect_llm_settings(
        provider_config, env_file, options.llm_quality_provider
    )
    inspection = prepare_retry_input(
        retry_input,
        None,
        source_run_id=options.source_run_id,
        round_number=1,
        max_direct_chars=options.max_direct_chars,
        sample_chars_per_section=options.sample_chars_per_section,
    )
    if inspection["input_document_count"] == 0:
        raise ValueError(f"Stage 04失败队列为空: {retry_input}")
    if options.dry_run:
        return {
            "status": "dry_run_complete",
            "source_run_id": options.source_run_id,
            "retry_input": str(retry_input),
            "base_output": str(base_output),
            "inspection": inspection,
            "provider": provider_status,
            "planned_rounds": options.max_rounds,
            "llm_concurrency": options.llm_concurrency,
            "long_document_noise_cleanup": "deferred",
        }

    retry_id = _retry_id()
    directories = run_directories(retry_id)
    timings = TimingRecorder()
    timings.set_output_path(directories["logs"] / "timing_summary.json")
    first_input = directories["intermediate"] / "round_01" / "retry_input.jsonl"
    first_input.parent.mkdir(parents=True, exist_ok=True)
    preparation = prepare_retry_input(
        retry_input,
        first_input,
        source_run_id=options.source_run_id,
        round_number=1,
        max_direct_chars=options.max_direct_chars,
        sample_chars_per_section=options.sample_chars_per_section,
    )
    run_config = {
        **asdict(options),
        "retry_id": retry_id,
        "retry_input": str(retry_input),
        "base_output": str(base_output),
        "python": sys.executable,
        "py_data_juicer_version": runtime,
        "command": command or sys.argv,
    }
    (directories["logs"] / "run_config.json").write_text(
        json.dumps(run_config, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    if options.prepare_only:
        summary = {
            "status": "prepare_only_complete",
            "retry_id": retry_id,
            "source_run_id": options.source_run_id,
            "preparation": preparation,
            "provider": provider_status,
            "directories": {key: str(value) for key, value in directories.items()},
        }
        (directories["reports"] / "retry_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return summary

    provider = resolve_llm_provider(
        provider_config,
        env_file,
        requested_provider=options.llm_quality_provider,
        data_classification=options.data_classification,
        allow_external_llm=options.allow_external_llm,
    )

    candidate_paths: list[Path] = []
    excluded_paths: list[Path] = []
    low_paths: list[Path] = []
    review_paths: list[Path] = []
    annotated_paths: list[Path] = []
    round_summaries: list[dict[str, Any]] = []
    current_input = first_input
    current_count = int(preparation["input_document_count"])
    final_retry_path: Path | None = None
    for round_number in range(1, options.max_rounds + 1):
        with timings.measure(
            f"04_llm_retry_round_{round_number:02d}",
            stage="04_retry",
            activity="data_juicer_and_partition",
            input_document_count=current_count,
        ) as timing:
            round_summary = _execute_retry_round(
                retry_id=retry_id,
                round_number=round_number,
                input_path=current_input,
                input_count=current_count,
                provider=provider,
                options=options,
                directories=directories,
            )
        partition = round_summary["partition"]
        recovered_this_round = current_count - int(partition["llm_retry_required_count"])
        timing.update_counts(output_document_count=recovered_this_round)
        round_summaries.append(round_summary)
        round_paths = {key: Path(value) for key, value in round_summary["paths"].items()}
        candidate_paths.append(round_paths["candidate"])
        excluded_paths.append(round_paths["topic_excluded"])
        low_paths.append(round_paths["low_quality"])
        review_paths.append(round_paths["review"])
        annotated_paths.append(round_paths["annotated"])
        final_retry_path = round_paths["retry"]
        failed_count = int(partition["llm_retry_required_count"])
        if failed_count == 0 or round_number == options.max_rounds:
            break
        next_input = (
            directories["intermediate"]
            / f"round_{round_number + 1:02d}"
            / "retry_input.jsonl"
        )
        next_input.parent.mkdir(parents=True, exist_ok=True)
        prepare_retry_input(
            final_retry_path,
            next_input,
            source_run_id=options.source_run_id,
            round_number=round_number + 1,
            max_direct_chars=options.max_direct_chars,
            sample_chars_per_section=options.sample_chars_per_section,
        )
        current_input = next_input
        current_count = failed_count

    consolidated = {
        "annotated_attempts": directories["intermediate"] / "all_attempts_annotated_zh.jsonl",
        "candidate": directories["intermediate"] / "retry_candidate_keep.jsonl",
        "topic_excluded": directories["quarantine"] / "retry_topic_excluded.jsonl",
        "low_quality": directories["quarantine"] / "retry_low_quality.jsonl",
        "review": directories["quarantine"] / "retry_manual_review_required.jsonl",
        "still_failed": directories["quarantine"] / "retry_still_failed.jsonl",
    }
    consolidated_counts = {
        "annotated_attempt_count": _concat_jsonl(
            annotated_paths, consolidated["annotated_attempts"]
        ),
        "candidate_keep_count": _concat_jsonl(candidate_paths, consolidated["candidate"]),
        "topic_excluded_count": _concat_jsonl(
            excluded_paths, consolidated["topic_excluded"]
        ),
        "low_quality_count": _concat_jsonl(low_paths, consolidated["low_quality"]),
        "manual_review_required_count": _concat_jsonl(
            review_paths, consolidated["review"]
        ),
        "still_failed_count": _concat_jsonl(
            [final_retry_path] if final_retry_path else [], consolidated["still_failed"]
        ),
    }

    cleanup_input = directories["intermediate"] / "05_cleanup_input.jsonl"
    cleanup_preparation = _prepare_cleanup_candidates(
        consolidated["candidate"], cleanup_input
    )
    retry_kept = directories["intermediate"] / "retry_kept.jsonl"
    with timings.measure(
        "05_retry_local_noise_cleanup",
        stage="05_retry",
        activity="local_validated_cleanup",
        input_document_count=consolidated_counts["candidate_keep_count"],
    ) as cleanup_timing:
        cleanup = apply_local_noise_cleanup(
            cleanup_input,
            retry_kept,
            directories["intermediate"] / "retry_noise_cleaned.jsonl",
            directories["quarantine"] / "retry_noise_review_required.jsonl",
            directories["reports"] / "05_retry_local_noise_cleanup.json",
            label_config,
            min_noise_confidence=options.llm_noise_min_confidence,
            max_removed_ratio=options.llm_noise_max_removed_ratio,
            min_remaining_characters=options.min_remaining_characters,
        )
    cleanup_timing.update_counts(output_document_count=cleanup["kept_document_count"])

    output_path = (
        resolve_from_clean_gov(options.output_path)
        if options.output_path
        else directories["output"]
        / f"corpus_native_cleaned_{options.source_run_id}_retry_{retry_id}.jsonl"
    )
    merge_counts = _ensure_disjoint_doc_ids(base_output, retry_kept)
    with timings.measure(
        "retry_final_merge",
        stage="final_retry",
        activity="merge",
        input_document_count=merge_counts["base_count"] + merge_counts["recovered_count"],
    ) as merge_timing:
        revised_count = _merge_outputs(
            [("base", base_output), ("retry_recovered", retry_kept)], output_path
        )
    merge_timing.update_counts(output_document_count=revised_count)
    timing_summary = timings.finish(
        "success",
        input_document_count=int(preparation["input_document_count"]),
        output_document_count=int(cleanup["kept_document_count"]),
    )
    summary = {
        "status": "success",
        "retry_id": retry_id,
        "source_run_id": options.source_run_id,
        "retry_input": str(retry_input),
        "base_output": str(base_output),
        "revised_output": str(output_path),
        "preparation": preparation,
        "rounds": round_summaries,
        "consolidated_counts": consolidated_counts,
        "cleanup_preparation": cleanup_preparation,
        "local_noise_cleanup": cleanup,
        "base_output_document_count": merge_counts["base_count"],
        "recovered_and_added_document_count": merge_counts["recovered_count"],
        "revised_output_document_count": revised_count,
        "provider": {
            **provider_status,
            "data_classification": options.data_classification,
            "concurrency": options.llm_concurrency,
        },
        "paths": {key: str(value) for key, value in consolidated.items()},
        "directories": {key: str(value) for key, value in directories.items()},
        "timing": timing_summary,
    }
    summary_path = directories["reports"] / "retry_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (directories["logs"] / "run_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary
