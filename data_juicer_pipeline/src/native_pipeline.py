"""Configuration-first pipeline built from Data-Juicer 1.5.3 native ops."""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from data_juicer_runner import build_native_config, run_data_juicer, write_config
from input_resolver import resolve_input
from jsonl_io import iter_jsonl, write_jsonl_record
from llm_provider import (
    DATA_CLASSIFICATIONS,
    LLMProvider,
    inspect_llm_settings,
    resolve_llm_provider,
)
from llm_policy import apply_local_noise_cleanup, partition_topic_annotations
from native_preparation import PreparationOptions, prepare_inputs
from native_validation import validate_native_output
from paths import (
    DEFAULT_ATTACHMENT_DIR,
    EXPECTED_DATA_JUICER_VERSION,
    LLM_ENV_FILE,
    LLM_PROVIDER_CONFIG,
    LLM_TAG_LABEL_CONFIG,
    NATIVE_CONFIG_DIR,
    resolve_from_clean_gov,
    run_directories,
)
from pipeline_timing import TimingRecorder, format_duration


NATIVE_LANES = {
    "web_normal": "web_normal.yaml",
    "web_multiline": "web_multiline.yaml",
    "web_table": "web_table.yaml",
    "attachment_text": "attachment_text.yaml",
    "attachment_table": "attachment_table.yaml",
}

QUALITY_LANES = {"web_normal", "web_multiline", "attachment_text"}
TABLE_LANES = {"web_table", "attachment_table"}
QUALITY_TEMPLATE = "quality_text.yaml"
EXACT_DEDUP_TEMPLATE = "exact_dedup.yaml"
LLM_TOPIC_QUALITY_TEMPLATE = "llm_topic_quality.yaml"


@dataclass(frozen=True)
class NativePipelineOptions:
    web_input: Path | None = None
    attachment_root: Path = DEFAULT_ATTACHMENT_DIR
    output_path: Path | None = None
    dry_run: bool = False
    prepare_only: bool = False
    max_native_chars: int = 3_000_000
    line_frequency_threshold: int = 100
    min_text_length: int = 50
    min_alnum_ratio: float = 0.45
    max_special_char_ratio: float = 0.75
    max_char_repetition_ratio: float = 0.5
    allowed_languages: tuple[str, ...] = ("zh",)
    min_language_score: float = 0.5
    enable_llm_quality: bool = False
    llm_quality_provider: str | None = None
    llm_quality_min_score: float = 0.6
    llm_topic_min_confidence: float = 0.9
    llm_min_public_health_relevance: float = 4.0
    llm_noise_min_confidence: float = 0.9
    llm_noise_max_removed_ratio: float = 0.3
    llm_concurrency: int = 16
    data_classification: str = "restricted"
    allow_external_llm: bool = False
    llm_provider_config: Path = LLM_PROVIDER_CONFIG
    llm_env_file: Path = LLM_ENV_FILE
    llm_tag_label_config: Path = LLM_TAG_LABEL_CONFIG
    only_lane: str | None = None


def _run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def _runtime_version() -> str:
    try:
        return version("py-data-juicer")
    except PackageNotFoundError as exc:
        raise RuntimeError(
            "当前 Python 未安装 py-data-juicer；请使用 README 中的 dj-env 绝对路径运行"
        ) from exc


def _print_preparation_summary(summary: dict[str, Any]) -> None:
    counts = summary.get("counts", {})
    web_total = int(counts.get("web_total", 0))
    attachment_total = int(counts.get("attachment_total", 0))
    total = web_total + attachment_total
    print(
        "[Stage 00 | 输入扫描] 完成，"
        f"已扫描 {total}/{total}，剩余 0；"
        f"网页 {web_total}，附件 {attachment_total}，"
        f"待重解析 {int(counts.get('reparse_required', 0))}，"
        f"超长 {int(counts.get('oversized', 0))}",
        flush=True,
    )


def _copy_if_nonempty(source: Path, target: Path) -> int:
    if not source.exists() or source.stat().st_size == 0:
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return sum(1 for _ in iter_jsonl(source))


def _merge_outputs(lane_outputs: list[tuple[str, Path]], output_path: Path) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        raise FileExistsError(f"正式输出已存在，不允许覆盖: {output_path}")
    count = 0
    with output_path.open("w", encoding="utf-8") as handle:
        for lane, path in lane_outputs:
            for _, record in iter_jsonl(path):
                opaque_metadata = record.pop("metadata_json", "")
                previous_stats = record.pop("__dj_previous_stats_json", "")
                previous_meta = record.pop("__dj_previous_meta_json", "")
                restored: dict[str, Any] = {}
                if isinstance(opaque_metadata, str) and opaque_metadata:
                    value = json.loads(opaque_metadata)
                    if isinstance(value, dict):
                        restored.update(value)
                # Native output fields are authoritative for canonical IDs,
                # cleaned text, lane flags, and any table metadata emitted by
                # Data-Juicer.
                restored.update(record)
                if isinstance(previous_stats, str) and previous_stats:
                    value = json.loads(previous_stats)
                    if isinstance(value, dict):
                        current = restored.get("__dj__stats__")
                        merged_stats = dict(value)
                        if isinstance(current, dict):
                            merged_stats.update(current)
                        restored["__dj__stats__"] = merged_stats
                if isinstance(previous_meta, str) and previous_meta:
                    value = json.loads(previous_meta)
                    if isinstance(value, dict):
                        current = restored.get("__dj__meta__")
                        merged_meta = dict(value)
                        if isinstance(current, dict):
                            merged_meta.update(current)
                        restored["__dj__meta__"] = merged_meta
                restored["native_pipeline_lane"] = str(record.get("native_pipeline_lane") or lane)
                write_jsonl_record(handle, restored)
                count += 1
    return count


def _combine_outputs(lane_outputs: list[tuple[str, Path]], output_path: Path) -> int:
    """Combine opaque records with one stable top-level JSONL schema."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        raise FileExistsError(f"中间输出已存在，不允许覆盖: {output_path}")

    # Hugging Face datasets infers JSONL columns from an early batch and then
    # rejects later records with additional columns.  Web and attachment lanes
    # can legitimately differ, so discover their union before writing anything.
    def make_opaque(record: dict[str, Any], lane: str) -> dict[str, Any]:
        normalized = dict(record)
        previous_stats = normalized.pop("__dj__stats__", {})
        previous_meta = normalized.pop("__dj__meta__", {})
        normalized["__dj_previous_stats_json"] = json.dumps(
            previous_stats if isinstance(previous_stats, dict) else {},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        normalized["__dj_previous_meta_json"] = json.dumps(
            previous_meta if isinstance(previous_meta, dict) else {},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        normalized["native_pipeline_lane"] = lane
        return normalized

    field_order: list[str] = []
    examples: dict[str, Any] = {}
    observed_types: dict[str, set[type[Any]]] = {}
    for lane, path in lane_outputs:
        for _, record in iter_jsonl(path):
            record = make_opaque(record, lane)
            for key, value in record.items():
                if key not in examples:
                    field_order.append(key)
                    examples[key] = value
                if value is not None:
                    observed_types.setdefault(key, set()).add(type(value))

    for key, types in observed_types.items():
        if len(types) > 1 and not types <= {int, float}:
            rendered = sorted(item.__name__ for item in types)
            raise ValueError(f"合并字段 {key!r} 存在不兼容类型: {rendered}")

    def missing_value(example: Any) -> Any:
        if isinstance(example, bool):
            return False
        if isinstance(example, str):
            return ""
        if isinstance(example, float):
            return 0.0
        if isinstance(example, int):
            return 0
        if isinstance(example, list):
            return []
        if isinstance(example, dict):
            return {key: missing_value(value) for key, value in example.items()}
        return None

    defaults = {key: missing_value(value) for key, value in examples.items()}
    count = 0
    with output_path.open("w", encoding="utf-8") as handle:
        for lane, path in lane_outputs:
            for _, record in iter_jsonl(path):
                record = make_opaque(record, lane)
                normalized = {
                    key: record[key] if key in record else defaults[key]
                    for key in field_order
                }
                write_jsonl_record(handle, normalized)
                count += 1
    return count


def _collect_filter_traces(source_dir: Path, target_dir: Path) -> dict[str, Any]:
    """Copy Data-Juicer's native per-filter tracer files into quarantine."""
    result: dict[str, Any] = {}
    sources = list(source_dir.rglob("filter-*.jsonl"))
    sources.extend(source_dir.rglob("sample_trace-*.jsonl"))
    for source in sorted(set(sources)):
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / source.name
        shutil.copy2(source, target)
        operator_name = source.stem.removeprefix("filter-").removeprefix("sample_trace-")
        result[operator_name] = {
            "path": str(target),
            "count": sum(1 for _ in iter_jsonl(target)),
        }
    return result


def _collect_duplicate_traces(source_dir: Path, target_dir: Path) -> dict[str, Any]:
    """Copy native duplicate-pair traces for the exact-dedup stage."""
    result: dict[str, Any] = {}
    for source in sorted(source_dir.rglob("duplicate-*.jsonl")):
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / source.name
        shutil.copy2(source, target)
        operator_name = source.stem.removeprefix("duplicate-")
        result[operator_name] = {
            "path": str(target),
            "pair_count": sum(1 for _ in iter_jsonl(target)),
        }
    return result


def _execute_native_stage(
    *,
    template_path: Path,
    input_path: Path,
    output_path: Path,
    work_dir: Path,
    config_path: Path,
    logs: Path,
    project_name: str,
    num_proc: int = 1,
    line_frequency_threshold: int | None = None,
    operator_overrides: dict[str, dict[str, Any]] | None = None,
    environment_overrides: dict[str, str] | None = None,
    progress_label: str | None = None,
    input_count: int | None = None,
) -> Path:
    config = build_native_config(
        template_path,
        input_path,
        output_path,
        work_dir,
        project_name=project_name,
        num_proc=num_proc,
        line_frequency_threshold=line_frequency_threshold,
        operator_overrides=operator_overrides,
    )
    write_config(config_path, config)
    dj_run = run_data_juicer(
        config_path,
        output_path,
        logs,
        environment_overrides=environment_overrides,
        progress_label=progress_label or config_path.stem,
        expected_total=input_count,
    )
    if dj_run.return_code != 0 or not output_path.is_file():
        raise RuntimeError(
            f"Data-Juicer 阶段 {config_path.stem} 失败，返回码 {dj_run.return_code}; "
            f"查看 {dj_run.stderr_path}"
        )
    return config_path


def run_native_pipeline(
    options: NativePipelineOptions,
    *,
    command: list[str] | None = None,
) -> dict[str, Any]:
    timings = TimingRecorder()
    if options.only_lane and options.only_lane not in NATIVE_LANES:
        raise ValueError(f"未知 lane: {options.only_lane}; 可选 {sorted(NATIVE_LANES)}")
    if options.min_text_length < 1:
        raise ValueError("min_text_length 必须是正整数")
    for name, value in (
        ("min_alnum_ratio", options.min_alnum_ratio),
        ("max_special_char_ratio", options.max_special_char_ratio),
        ("max_char_repetition_ratio", options.max_char_repetition_ratio),
        ("min_language_score", options.min_language_score),
        ("llm_topic_min_confidence", options.llm_topic_min_confidence),
        ("llm_noise_min_confidence", options.llm_noise_min_confidence),
        ("llm_noise_max_removed_ratio", options.llm_noise_max_removed_ratio),
    ):
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{name} 必须位于 [0, 1]")
    if not options.allowed_languages:
        raise ValueError("allowed_languages 不能为空")
    if not 0.0 < options.llm_quality_min_score <= 1.0:
        raise ValueError("llm_quality_min_score 必须位于 (0, 1]")
    if not 1.0 <= options.llm_min_public_health_relevance <= 5.0:
        raise ValueError("llm_min_public_health_relevance 必须位于 [1, 5]")
    if options.llm_concurrency < 1:
        raise ValueError("llm_concurrency 必须是正整数")
    if options.data_classification not in DATA_CLASSIFICATIONS:
        raise ValueError(
            f"未知数据保密级别 {options.data_classification!r}; "
            f"可选 {list(DATA_CLASSIFICATIONS)}"
        )
    web_input = resolve_input(options.web_input)
    attachment_root = resolve_from_clean_gov(options.attachment_root)
    llm_provider_config = resolve_from_clean_gov(options.llm_provider_config)
    llm_env_file = resolve_from_clean_gov(options.llm_env_file)
    llm_tag_label_config = resolve_from_clean_gov(options.llm_tag_label_config)
    if not llm_tag_label_config.is_file():
        raise FileNotFoundError(f"LLM 中文标签对照文件不存在: {llm_tag_label_config}")
    llm_status = inspect_llm_settings(
        llm_provider_config,
        llm_env_file,
        options.llm_quality_provider,
    )
    llm_status["stage_enabled"] = options.enable_llm_quality
    llm_status["concurrency"] = options.llm_concurrency
    llm_status["data_classification"] = options.data_classification
    llm_status["external_use_explicitly_allowed"] = options.allow_external_llm
    llm_status["outbound_policy_allows_provider"] = bool(
        not llm_status["external"]
        or (
            options.data_classification == "public"
            and options.allow_external_llm
        )
    )
    llm_provider: LLMProvider | None = None
    if options.enable_llm_quality and not options.dry_run and not options.prepare_only:
        llm_provider = resolve_llm_provider(
            llm_provider_config,
            llm_env_file,
            requested_provider=options.llm_quality_provider,
            data_classification=options.data_classification,
            allow_external_llm=options.allow_external_llm,
        )
    run_id = _run_id()
    directories = run_directories(run_id)
    intermediate = directories["intermediate"]
    reports = directories["reports"]
    quarantine = directories["quarantine"]
    logs = directories["logs"]
    runtime_version = _runtime_version()
    if runtime_version != EXPECTED_DATA_JUICER_VERSION:
        raise RuntimeError(
            f"py-data-juicer 版本不符: {runtime_version}; 需要 {EXPECTED_DATA_JUICER_VERSION}"
        )

    output_path = (
        resolve_from_clean_gov(options.output_path)
        if options.output_path
        else directories["output"] / f"corpus_native_cleaned_{run_id}.jsonl"
    )
    run_config = {
        **asdict(options),
        "web_input": str(web_input),
        "attachment_root": str(attachment_root),
        "output_path": str(output_path),
        "run_id": run_id,
        "python": sys.executable,
        "py_data_juicer_version": runtime_version,
        "command": command or sys.argv,
    }
    logs.mkdir(parents=True, exist_ok=True)
    timings.set_output_path(logs / "timing_summary.json")
    (logs / "run_config.json").write_text(
        json.dumps(run_config, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )

    if options.dry_run:
        print("\n[Stage 00 | 输入扫描] 开始", flush=True)
        with timings.measure(
            "00_input_scan",
            stage="00",
            activity="input_scan_and_routing",
        ) as preparation_timing:
            prepared = prepare_inputs(
                PreparationOptions(
                    web_input=web_input,
                    attachment_root=attachment_root,
                    output_dir=intermediate / "00_prepared",
                    max_native_chars=options.max_native_chars,
                    write_outputs=False,
                )
            )
            _print_preparation_summary(prepared.summary)
            prepared_count = int(prepared.summary["counts"].get("web_total", 0)) + int(
                prepared.summary["counts"].get("attachment_total", 0)
            )
            preparation_timing.update_counts(
                input_document_count=prepared_count,
                output_document_count=prepared_count,
            )
        timing_summary = timings.finish(
            "dry_run_complete",
            input_document_count=prepared_count,
            output_document_count=prepared_count,
        )
        print(
            f"[流水线总计] 扫描完成，输入 {prepared_count} 条，"
            f"总耗时 {timing_summary['total_duration_display']}，"
            f"平均 {timing_summary['input_documents_per_second']:.2f} 条/秒",
            flush=True,
        )
        summary = {
            "run_id": run_id,
            "status": "dry_run_complete",
            "preparation": prepared.summary,
            "planned_lanes": list(NATIVE_LANES),
            "planned_stages": [
                "00_prepared",
                "01_normalized",
                "02_quality_filtered",
                "03_global_exact_dedup",
                "04_llm_topic_quality" if options.enable_llm_quality else "04_llm_topic_quality (disabled)",
                "05_local_noise_cleanup" if options.enable_llm_quality else "05_local_noise_cleanup (disabled)",
            ],
            "quality_policy": {
                "table_lanes_bypass_quality_filters": sorted(TABLE_LANES),
                "min_text_length": options.min_text_length,
                "min_alnum_ratio": options.min_alnum_ratio,
                "max_special_char_ratio": options.max_special_char_ratio,
                "max_char_repetition_ratio": options.max_char_repetition_ratio,
                "allowed_languages": list(options.allowed_languages),
                "min_language_score": options.min_language_score,
            },
            "llm_quality_policy": {
                **llm_status,
                "min_score": options.llm_quality_min_score,
                "min_topic_confidence": options.llm_topic_min_confidence,
                "min_public_health_relevance": options.llm_min_public_health_relevance,
                "table_lanes_participate": True,
                "api_failures_enter_retry_queue": True,
                "tag_label_config": str(llm_tag_label_config),
            },
            "output": str(output_path),
            "timing": timing_summary,
            "run_directories": {key: str(value) for key, value in directories.items()},
        }
        (logs / "run_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return summary

    print("\n[Stage 00 | 输入扫描和分流] 开始", flush=True)
    with timings.measure(
        "00_input_scan_and_routing",
        stage="00",
        activity="input_scan_and_routing",
    ) as preparation_timing:
        prepared = prepare_inputs(
            PreparationOptions(
                web_input=web_input,
                attachment_root=attachment_root,
                output_dir=intermediate / "00_prepared",
                max_native_chars=options.max_native_chars,
                write_outputs=True,
            )
        )
        _print_preparation_summary(prepared.summary)
        reparse_count = _copy_if_nonempty(
            prepared.lane_paths["reparse_required"], quarantine / "reparse_required.jsonl"
        )
        oversized_count = _copy_if_nonempty(
            prepared.lane_paths["oversized"], quarantine / "oversized.jsonl"
        )
        prepared_count = int(prepared.summary["counts"].get("web_total", 0)) + int(
            prepared.summary["counts"].get("attachment_total", 0)
        )
        preparation_timing.update_counts(
            input_document_count=prepared_count,
            output_document_count=prepared_count,
        )
    if options.prepare_only:
        timing_summary = timings.finish(
            "prepare_only_complete",
            input_document_count=prepared_count,
            output_document_count=prepared_count,
        )
        print(
            f"[流水线总计] 分流完成，输入 {prepared_count} 条，"
            f"总耗时 {timing_summary['total_duration_display']}，"
            f"平均 {timing_summary['input_documents_per_second']:.2f} 条/秒",
            flush=True,
        )
        summary = {
            "run_id": run_id,
            "status": "prepare_only_complete",
            "preparation": prepared.summary,
            "reparse_quarantine_count": reparse_count,
            "oversized_quarantine_count": oversized_count,
            "llm_quality_policy": {
                **llm_status,
                "min_score": options.llm_quality_min_score,
                "api_failures_enter_retry_queue": True,
                "tag_label_config": str(llm_tag_label_config),
            },
            "timing": timing_summary,
            "run_directories": {key: str(value) for key, value in directories.items()},
        }
        (logs / "run_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return summary

    config_dir = intermediate / "configs"
    normalized_dir = intermediate / "01_normalized"
    quality_dir = intermediate / "02_quality_filtered"
    exact_dir = intermediate / "03_global_exact_dedup"
    topic_dir = intermediate / "04_llm_topic_quality"
    cleanup_dir = intermediate / "05_local_noise_cleanup"
    work_dir = intermediate / "data_juicer_work"
    for path in (
        config_dir,
        normalized_dir,
        quality_dir,
        exact_dir,
        topic_dir,
        cleanup_dir,
        work_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)

    stage02_outputs: list[tuple[str, Path]] = []
    lane_summaries: dict[str, Any] = {}
    for lane, template_name in NATIVE_LANES.items():
        if options.only_lane and lane != options.only_lane:
            continue
        input_path = prepared.lane_paths[lane]
        input_count = int(prepared.summary["counts"].get(lane, 0))
        if input_count == 0:
            lane_summaries[lane] = {"status": "skipped_empty", "input_count": 0}
            continue
        normalized_output = normalized_dir / f"{lane}.jsonl"
        with timings.measure(
            f"01_normalize_{lane}",
            stage="01",
            lane=lane,
            activity="data_juicer",
            input_document_count=input_count,
        ) as normalized_timing:
            normalized_config = _execute_native_stage(
                template_path=NATIVE_CONFIG_DIR / template_name,
                input_path=input_path,
                output_path=normalized_output,
                work_dir=work_dir / "01_normalized" / lane,
                config_path=config_dir / f"01_normalize_{lane}.yaml",
                logs=logs,
                project_name=f"native_{run_id}_01_normalize_{lane}",
                line_frequency_threshold=(
                    options.line_frequency_threshold if lane == "web_multiline" else None
                ),
                progress_label=f"Stage 01 | {lane} | 规范化",
                input_count=input_count,
            )
        with timings.measure(
            f"01_validate_{lane}",
            stage="01",
            lane=lane,
            activity="validation_and_quarantine",
            input_document_count=input_count,
        ) as normalized_validation_timing:
            normalized_validation = validate_native_output(
                input_path,
                normalized_output,
                reports / f"01_normalize_{lane}.json",
                quarantine / f"01_normalize_{lane}_unexpected_removed.jsonl",
                quarantine_reason="unexpected_removal_during_normalization",
                stage="01_normalized",
            )
        normalized_timing.update_counts(
            output_document_count=int(normalized_validation["after_document_count"]),
            input_character_count=int(normalized_validation["before_character_count"]),
            output_character_count=int(normalized_validation["after_character_count"]),
        )
        normalized_validation_timing.update_counts(
            output_document_count=int(normalized_validation["after_document_count"]),
            input_character_count=int(normalized_validation["before_character_count"]),
            output_character_count=int(normalized_validation["after_character_count"]),
        )
        stages: dict[str, Any] = {
            "01_normalized": {
                "config": str(normalized_config),
                "output": str(normalized_output),
                "timing": normalized_timing.as_dict(),
                "validation": normalized_validation,
            }
        }

        quality_output = normalized_output
        quality_count = int(normalized_validation["after_document_count"])
        if lane in QUALITY_LANES:
            quality_output = quality_dir / f"{lane}.jsonl"
            quality_work = work_dir / "02_quality_filtered" / lane
            with timings.measure(
                f"02_quality_{lane}",
                stage="02",
                lane=lane,
                activity="data_juicer",
                input_document_count=quality_count,
            ) as quality_timing:
                quality_config = _execute_native_stage(
                    template_path=NATIVE_CONFIG_DIR / QUALITY_TEMPLATE,
                    input_path=normalized_output,
                    output_path=quality_output,
                    work_dir=quality_work,
                    config_path=config_dir / f"02_quality_{lane}.yaml",
                    logs=logs,
                    project_name=f"native_{run_id}_02_quality_{lane}",
                    operator_overrides={
                        "text_length_filter": {
                            "min_len": options.min_text_length,
                            "max_len": options.max_native_chars,
                        },
                        "alphanumeric_filter": {"min_ratio": options.min_alnum_ratio},
                        "special_characters_filter": {
                            "max_ratio": options.max_special_char_ratio
                        },
                        "character_repetition_filter": {
                            "max_ratio": options.max_char_repetition_ratio
                        },
                        "language_id_score_filter": {
                            "lang": list(options.allowed_languages),
                            "min_score": options.min_language_score,
                        },
                    },
                    progress_label=f"Stage 02 | {lane} | 质量过滤",
                    input_count=quality_count,
                )
            with timings.measure(
                f"02_validate_{lane}",
                stage="02",
                lane=lane,
                activity="validation_and_quarantine",
                input_document_count=quality_count,
            ) as quality_validation_timing:
                quality_validation = validate_native_output(
                    normalized_output,
                    quality_output,
                    reports / f"02_quality_{lane}.json",
                    quarantine / f"02_quality_{lane}_removed.jsonl",
                    quarantine_reason="failed_native_quality_filters",
                    stage="02_quality_filtered",
                )
                filter_traces = _collect_filter_traces(
                    quality_work,
                    quarantine / "02_quality_filter_reasons" / lane,
                )
            quality_timing.update_counts(
                output_document_count=int(quality_validation["after_document_count"]),
                input_character_count=int(quality_validation["before_character_count"]),
                output_character_count=int(quality_validation["after_character_count"]),
            )
            quality_validation_timing.update_counts(
                output_document_count=int(quality_validation["after_document_count"]),
                input_character_count=int(quality_validation["before_character_count"]),
                output_character_count=int(quality_validation["after_character_count"]),
            )
            stages["02_quality_filtered"] = {
                "config": str(quality_config),
                "output": str(quality_output),
                "timing": quality_timing.as_dict(),
                "validation": quality_validation,
                "native_filter_traces": filter_traces,
            }
        else:
            stages["02_quality_filtered"] = {
                "status": "bypassed_by_table_quality_policy",
                "input_and_output": str(normalized_output),
            }
        lane_summaries[lane] = {
            "status": "success",
            "input_count": input_count,
            "stages": stages,
        }
        stage02_outputs.append((lane, quality_output))

    final_inputs: list[tuple[str, Path]] = []
    exact_summary: dict[str, Any]
    topic_summary: dict[str, Any]
    cleanup_summary: dict[str, Any]
    if stage02_outputs:
        combined_path = exact_dir / "all_before_exact_dedup.jsonl"
        with timings.measure(
            "03_combine_all_lanes",
            stage="03",
            lane="global_all",
            activity="merge",
        ) as combine_timing:
            combined_count = _combine_outputs(stage02_outputs, combined_path)
        combine_timing.update_counts(
            input_document_count=combined_count,
            output_document_count=combined_count,
        )

        exact_output = exact_dir / "all_after_exact_dedup.jsonl"
        exact_work = work_dir / "03_global_exact_dedup"
        with timings.measure(
            "03_global_exact_dedup",
            stage="03",
            lane="global_all",
            activity="data_juicer",
            input_document_count=combined_count,
        ) as exact_timing:
            exact_config = _execute_native_stage(
                template_path=NATIVE_CONFIG_DIR / EXACT_DEDUP_TEMPLATE,
                input_path=combined_path,
                output_path=exact_output,
                work_dir=exact_work,
                config_path=config_dir / "03_global_exact_dedup.yaml",
                logs=logs,
                project_name=f"native_{run_id}_03_global_exact_dedup",
                progress_label="Stage 03 | global_all | 全通道正文完全去重",
                input_count=combined_count,
            )
        with timings.measure(
            "03_validate_global_exact_dedup",
            stage="03",
            lane="global_all",
            activity="validation_and_quarantine",
            input_document_count=combined_count,
        ) as exact_validation_timing:
            exact_validation = validate_native_output(
                combined_path,
                exact_output,
                reports / "03_global_exact_dedup.json",
                quarantine / "03_global_exact_duplicates.jsonl",
                quarantine_reason="exact_duplicate_after_cleaning",
                stage="03_global_exact_dedup",
            )
            exact_duplicate_traces = _collect_duplicate_traces(
                exact_work,
                quarantine / "03_global_exact_duplicate_pairs",
            )
        exact_timing.update_counts(
            output_document_count=int(exact_validation["after_document_count"]),
            input_character_count=int(exact_validation["before_character_count"]),
            output_character_count=int(exact_validation["after_character_count"]),
        )
        exact_validation_timing.update_counts(
            output_document_count=int(exact_validation["after_document_count"]),
            input_character_count=int(exact_validation["before_character_count"]),
            output_character_count=int(exact_validation["after_character_count"]),
        )
        exact_summary = {
            "status": "success",
            "input_count": combined_count,
            "config": str(exact_config),
            "input": str(combined_path),
            "output": str(exact_output),
            "timing": exact_timing.as_dict(),
            "validation": exact_validation,
            "native_duplicate_traces": exact_duplicate_traces,
            "near_duplicate_removal_enabled": False,
        }

        if options.enable_llm_quality:
            if llm_provider is None:
                raise RuntimeError("LLM 主题与质量阶段已启用，但提供商未完成解析")
            llm_input_count = int(exact_validation["after_document_count"])
            scored_output = topic_dir / "all_scored_tagged.jsonl"
            annotated_all_output = topic_dir / "all_annotated_zh.jsonl"
            candidate_output = topic_dir / "candidate_keep.jsonl"
            llm_work = work_dir / "04_llm_topic_quality"
            with timings.measure(
                "04_llm_topic_quality_scoring",
                stage="04",
                lane="global_all",
                activity="data_juicer",
                input_document_count=llm_input_count,
            ) as llm_timing:
                llm_config = _execute_native_stage(
                    template_path=NATIVE_CONFIG_DIR / LLM_TOPIC_QUALITY_TEMPLATE,
                    input_path=exact_output,
                    output_path=scored_output,
                    work_dir=llm_work,
                    config_path=config_dir / "04_llm_topic_quality.yaml",
                    logs=logs,
                    project_name=f"native_{run_id}_04_llm_topic_quality",
                    num_proc=options.llm_concurrency,
                    operator_overrides={
                        "llm_quality_score_filter": {
                            "api_or_hf_model": llm_provider.model,
                            "min_score": 0.0,
                            "max_score": 1.0,
                            "api_endpoint": llm_provider.endpoint,
                            "response_path": llm_provider.response_path,
                            "sampling_params": llm_provider.sampling_params,
                        }
                    },
                    environment_overrides=llm_provider.child_environment(),
                    progress_label="Stage 04 | global_all | LLM主题、质量与噪声标注",
                    input_count=llm_input_count,
                )
            with timings.measure(
                "04_validate_llm_scoring",
                stage="04",
                lane="global_all",
                activity="validation_and_quarantine",
                input_document_count=llm_input_count,
            ) as llm_validation_timing:
                scoring_validation = validate_native_output(
                    exact_output,
                    scored_output,
                    reports / "04_llm_scoring_validation.json",
                    quarantine / "04_llm_unexpected_native_removal.jsonl",
                    quarantine_reason="unexpected_removal_during_llm_scoring",
                    stage="04_llm_topic_quality_scoring",
                )
            llm_timing.update_counts(
                output_document_count=int(scoring_validation["after_document_count"]),
                input_character_count=int(scoring_validation["before_character_count"]),
                output_character_count=int(scoring_validation["after_character_count"]),
            )
            llm_validation_timing.update_counts(
                output_document_count=int(scoring_validation["after_document_count"]),
                input_character_count=int(scoring_validation["before_character_count"]),
                output_character_count=int(scoring_validation["after_character_count"]),
            )
            if scoring_validation["removed_document_count"] != 0:
                raise RuntimeError(
                    "LLM 原生标注阶段意外移除了记录；已停止生成正式结果，"
                    f"查看 {reports / '04_llm_scoring_validation.json'}"
                )

            with timings.measure(
                "04_partition_topic_annotations",
                stage="04",
                lane="global_all",
                activity="validation_and_quarantine",
                input_document_count=llm_input_count,
            ) as topic_partition_timing:
                partition_summary = partition_topic_annotations(
                    scored_output,
                    annotated_all_output,
                    candidate_output,
                    quarantine / "04_topic_excluded.jsonl",
                    quarantine / "04_low_quality.jsonl",
                    quarantine / "04_llm_retry_required.jsonl",
                    quarantine / "04_manual_review_required.jsonl",
                    reports / "04_llm_topic_quality.json",
                    llm_tag_label_config,
                    min_quality_score=options.llm_quality_min_score,
                    min_topic_confidence=options.llm_topic_min_confidence,
                    min_public_health_relevance=options.llm_min_public_health_relevance,
                    provider_name=llm_provider.name,
                )
            topic_partition_timing.update_counts(
                output_document_count=int(partition_summary["candidate_keep_count"])
            )
            topic_summary = {
                "status": "success",
                "concurrency": options.llm_concurrency,
                "config": str(llm_config),
                "timing": llm_timing.as_dict(),
                "scoring_validation": scoring_validation,
                **partition_summary,
            }

            kept_output = cleanup_dir / "all_kept.jsonl"
            with timings.measure(
                "05_local_noise_cleanup",
                stage="05",
                lane="global_all",
                activity="local_validated_cleanup",
                input_document_count=int(partition_summary["candidate_keep_count"]),
            ) as cleanup_timing:
                cleanup_result = apply_local_noise_cleanup(
                    candidate_output,
                    kept_output,
                    cleanup_dir / "noise_cleaned.jsonl",
                    quarantine / "05_noise_removal_review_required.jsonl",
                    reports / "05_local_noise_cleanup.json",
                    llm_tag_label_config,
                    min_noise_confidence=options.llm_noise_min_confidence,
                    max_removed_ratio=options.llm_noise_max_removed_ratio,
                    min_remaining_characters=options.min_text_length,
                )
            cleanup_timing.update_counts(
                output_document_count=int(cleanup_result["kept_document_count"])
            )
            cleanup_summary = {"status": "success", **cleanup_result}
            final_inputs.append(("global_all", kept_output))
        else:
            topic_summary = {
                "status": "disabled",
                "input_and_output": str(exact_output),
            }
            cleanup_summary = {
                "status": "disabled",
                "input_and_output": str(exact_output),
            }
            final_inputs.append(("global_all", exact_output))
    else:
        exact_summary = {"status": "skipped_no_records"}
        topic_summary = {"status": "skipped_no_records"}
        cleanup_summary = {"status": "skipped_no_records"}

    print("\n[最终输出 | 合并各通道] 开始", flush=True)
    with timings.measure(
        "final_merge",
        stage="final",
        activity="merge",
    ) as final_merge_timing:
        final_count = _merge_outputs(final_inputs, output_path)
    final_merge_timing.update_counts(
        input_document_count=final_count,
        output_document_count=final_count,
    )
    final_merge_seconds = float(final_merge_timing.record["duration_seconds"] or 0.0)
    print(
        f"[最终输出 | 合并各通道] 完成，输出 {final_count} 条，"
        f"耗时 {format_duration(final_merge_seconds)}",
        flush=True,
    )
    timing_summary = timings.finish(
        "success",
        input_document_count=prepared_count,
        output_document_count=final_count,
    )
    print(
        f"[流水线总计] 完成，输入 {prepared_count} 条，输出 {final_count} 条，"
        f"总耗时 {timing_summary['total_duration_display']}，"
        f"平均 {timing_summary['input_documents_per_second']:.2f} 条/秒",
        flush=True,
    )
    summary = {
        "run_id": run_id,
        "status": "success",
        "input": str(web_input),
        "attachment_root": str(attachment_root),
        "output": str(output_path),
        "output_document_count": final_count,
        "reparse_quarantine_count": reparse_count,
        "oversized_quarantine_count": oversized_count,
        "preparation": prepared.summary,
        "quality_policy": {
            "table_lanes_bypass_quality_filters": sorted(TABLE_LANES),
            "min_text_length": options.min_text_length,
            "min_alnum_ratio": options.min_alnum_ratio,
            "max_special_char_ratio": options.max_special_char_ratio,
            "max_char_repetition_ratio": options.max_char_repetition_ratio,
            "allowed_languages": list(options.allowed_languages),
            "min_language_score": options.min_language_score,
            "global_deduplication": "exact_full_cleaned_text_only",
            "near_duplicate_removal_enabled": False,
        },
        "llm_topic_quality_policy": {
            **llm_status,
            "min_score": options.llm_quality_min_score,
            "min_topic_confidence": options.llm_topic_min_confidence,
            "min_public_health_relevance": options.llm_min_public_health_relevance,
            "all_lanes_including_tables_participate": True,
            "api_or_response_failures_enter_retry_quarantine": True,
            "tag_label_config": str(llm_tag_label_config),
        },
        "local_noise_cleanup_policy": {
            "min_noise_confidence": options.llm_noise_min_confidence,
            "max_removed_ratio": options.llm_noise_max_removed_ratio,
            "exact_unique_full_lines_only": True,
            "table_structure_is_protected": True,
        },
        "lanes": lane_summaries,
        "global_exact_dedup": exact_summary,
        "llm_topic_quality": topic_summary,
        "local_noise_cleanup": cleanup_summary,
        "timing": timing_summary,
        "stage_directories": {
            "01_normalized": str(normalized_dir),
            "02_quality_filtered": str(quality_dir),
            "03_global_exact_dedup": str(exact_dir),
            "04_llm_topic_quality": str(topic_dir),
            "05_local_noise_cleanup": str(cleanup_dir),
        },
        "run_directories": {key: str(value) for key, value in directories.items()},
    }
    (reports / "native_pipeline_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (logs / "run_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary
