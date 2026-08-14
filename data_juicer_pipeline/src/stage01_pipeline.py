"""End-to-end orchestration for Stage 01 text cleaning."""

from __future__ import annotations

import csv
import json
import logging
import shutil
import sys
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from anomaly_detection import detect_anomalies, has_severe_page_anomaly
from data_juicer_runner import build_config, run_data_juicer, write_config
from deterministic_cleaning import clean_text
from input_inspection import InspectionResult, inspect_input
from input_resolver import resolve_input
from jsonl_io import iter_jsonl, validate_jsonl, write_jsonl_record
from merge_results import OrderedRecordStore
from paths import (
    CONFIG_DIR,
    INTERMEDIATE_DIR,
    LOG_DIR,
    PROJECT_ROOT,
    QUARANTINE_DIR,
    REPORT_DIR,
    RUNS_DIR,
    run_directories,
)
from protected_blocks import protect_text, protection_from_map, restore_text
from quarantine_writer import write_quarantine
from report_writer import write_csv, write_json
from result_comparison import compare_files
from runtime import write_environment
from site_rule_engine import load_site_rules
from source_grouping import group_for
from threshold_policy import ThresholdDecision, decide_threshold, load_threshold_map


LOGGER = logging.getLogger(__name__)


class PipelineBlocked(RuntimeError):
    """A guardrail intentionally stopped formal output."""


@dataclass
class StageOptions:
    input_path: Path | None = None
    output_path: Path | None = None
    work_dir: Path | None = None
    report_dir: Path | None = None
    dry_run: bool = False
    frequency_threshold: int | None = None
    frequency_threshold_map: Path | None = None
    allow_small_group: bool = False
    restore_literal_newlines: bool = False
    sample_count: int = 50
    force: bool = False
    skip_data_juicer: bool = False
    only_group: str | None = None
    log_level: str = "INFO"


def make_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def _create_run_dirs(options: StageOptions, run_id: str) -> dict[str, Path]:
    if options.work_dir is None and options.report_dir is None:
        return run_directories(run_id)
    roots = {
        "intermediate": options.work_dir.resolve() if options.work_dir else INTERMEDIATE_DIR,
        "reports": options.report_dir.resolve() if options.report_dir else REPORT_DIR,
        "quarantine": QUARANTINE_DIR,
        "logs": LOG_DIR,
    }
    result = {name: root / "runs" / run_id for name, root in roots.items()}
    result["output"] = (
        options.output_path.resolve().parent
        if options.output_path
        else RUNS_DIR / run_id / "output"
    )
    for name, path in result.items():
        if name == "output" and options.output_path:
            continue
        path.mkdir(parents=True, exist_ok=False)
    return result


def _blockers(inspection: InspectionResult, *, input_path: Path, output_path: Path, environment: dict[str, Any], restore_requested: bool) -> list[str]:
    summary = inspection.summary
    reasons: list[str] = []
    if not environment.get("ok"):
        reasons.append("Data-Juicer 环境诊断未通过")
    if input_path.resolve() == output_path.resolve():
        reasons.append("输入与输出路径相同")
    if summary.get("invalid_json_count", 0):
        reasons.append(f"存在 {summary['invalid_json_count']} 行无效 JSON")
    if summary.get("text_non_string_count", 0):
        reasons.append(f"存在 {summary['text_non_string_count']} 个非字符串 text")
    total = int(summary.get("total_document_count", 0)) or 1
    missing_ids = int(summary.get("doc_id_missing_count", 0))
    duplicate_ids = int(summary.get("duplicate_doc_id_occurrence_count", 0))
    if missing_ids / total > 0.05 or duplicate_ids / total > 0.05:
        reasons.append(f"doc_id 大量缺失或重复（缺失 {missing_ids}，重复出现 {duplicate_ids}）")
    if float(summary.get("single_line_document_ratio", 0)) > 0.70:
        reasons.append("超过 70% 的非空文档只有一行")
    if summary.get("literal_newline_likely_escaped") and not restore_requested:
        reasons.append("字面量 \\n 很多而真实换行很少；请检查后显式传 --restore-literal-newlines")
    required_free = max(input_path.stat().st_size * 3, 512 * 1024 * 1024)
    if int(environment.get("disk_space", {}).get("free", 0)) < required_free:
        reasons.append(f"磁盘可用空间不足，至少需要约 {required_free} 字节")
    return reasons


def _group_rows(inspection: InspectionResult, options: StageOptions, threshold_map: dict[str, int]) -> tuple[list[dict[str, Any]], dict[str, ThresholdDecision]]:
    rows: list[dict[str, Any]] = []
    decisions: dict[str, ThresholdDecision] = {}
    for key, count in inspection.group_counts.most_common():
        source, domain, slug = inspection.group_metadata[key]
        decision = decide_threshold(
            group_key=key,
            source=source,
            domain=domain,
            document_count=count,
            override=options.frequency_threshold,
            mapping=threshold_map,
            allow_small_group=options.allow_small_group,
        )
        if options.only_group and options.only_group not in {key, slug, source, domain}:
            decision = ThresholdDecision(decision.recommended_threshold, None, False, decision.small_group_risk, decision.threshold_source, "未匹配 --only-group")
        decisions[key] = decision
        rows.append({
            "group_key": key,
            "group_name": slug,
            "domain": domain,
            "source": source,
            "document_count": count,
            "allow_deduplication": decision.allow_deduplication and not options.skip_data_juicer,
            "recommended_threshold": decision.recommended_threshold,
            "actual_threshold": decision.actual_threshold if not options.skip_data_juicer else "",
            "small_group_risk": decision.small_group_risk,
            "threshold_source": decision.threshold_source,
            "skipped_reason": "--skip-data-juicer" if options.skip_data_juicer else decision.skipped_reason,
            "output_file": "",
            "return_code": "",
        })
    return rows, decisions


def run_stage01(options: StageOptions, *, command: list[str] | None = None) -> dict[str, Any]:
    input_path = resolve_input(options.input_path)
    run_id = make_run_id()
    run_dirs = _create_run_dirs(options, run_id)
    output_path = (
        options.output_path.resolve()
        if options.output_path
        else run_dirs["output"] / f"{input_path.stem}_stage01_text_cleaned.jsonl"
    )
    logs, reports, intermediate, quarantine = run_dirs["logs"], run_dirs["reports"], run_dirs["intermediate"], run_dirs["quarantine"]
    (logs / "stdout.log").write_text(f"run_id={run_id}\ninput={input_path}\n", encoding="utf-8")
    (logs / "stderr.log").write_text("", encoding="utf-8")
    write_json(logs / "run_config.json", {**asdict(options), "input_path": str(input_path), "output_path": str(output_path), "run_id": run_id})
    (logs / "command.txt").write_text(" ".join(command or sys.argv), encoding="utf-8")
    environment = write_environment(logs / "environment.json", test_cli=True)
    LOGGER.info("运行 %s：输入 %s", run_id, input_path)
    inspection = inspect_input(input_path, reports, sample_count=options.sample_count)
    threshold_map = load_threshold_map(options.frequency_threshold_map)
    group_rows, decisions = _group_rows(inspection, options, threshold_map)
    group_fields = ["group_key", "group_name", "domain", "source", "document_count", "allow_deduplication", "recommended_threshold", "actual_threshold", "small_group_risk", "threshold_source", "skipped_reason", "output_file", "return_code"]
    write_csv(reports / "data_juicer_group_summary.csv", group_rows, group_fields)
    blockers = _blockers(inspection, input_path=input_path, output_path=output_path, environment=environment, restore_requested=options.restore_literal_newlines)
    dry_summary = {"run_id": run_id, "status": "dry_run_complete" if options.dry_run else "preflight_complete", "input": str(input_path), "output": str(output_path), "blockers": blockers, "run_directories": {key: str(value) for key, value in run_dirs.items()}}
    if options.dry_run:
        write_json(logs / "run_summary.json", dry_summary)
        LOGGER.info("dry-run 完成；未生成正式输出")
        return dry_summary
    if output_path.exists() and not options.force:
        blockers.append(f"输出已存在且未传 --force: {output_path}")
    if blockers and not options.force:
        summary = {**dry_summary, "status": "blocked", "blockers": blockers}
        write_json(logs / "run_summary.json", summary)
        raise PipelineBlocked("；".join(blockers))

    restore_literals = options.restore_literal_newlines and bool(inspection.summary.get("literal_newline_likely_escaped"))
    normalized_path = intermediate / "normalized_input.jsonl"
    grouped_dir = intermediate / "grouped_input"
    protected_dir = intermediate / "protected_input"
    dj_output_dir = intermediate / "data_juicer_output"
    config_dir = intermediate / "configs"
    map_dir = intermediate / "protected_block_maps"
    for path in (grouped_dir, protected_dir, dj_output_dir, config_dir, map_dir):
        path.mkdir(parents=True, exist_ok=True)
    site_rules = load_site_rules(CONFIG_DIR / "site_rules.yaml")
    bypass_path = intermediate / "bypass_after_deterministic.jsonl"
    audit_path = intermediate / "document_audit.jsonl"
    hit_fields = ["rule_id", "rule_type", "matched_text", "document_position", "before_text", "after_text", "removed_character_count", "source", "domain", "doc_id"]
    hit_counts: dict[str, int] = {rule.rule_id: 0 for rule in site_rules}
    protection_totals: dict[str, int] = {}

    with ExitStack() as stack:
        normalized_handle = stack.enter_context(normalized_path.open("w", encoding="utf-8"))
        bypass_handle = stack.enter_context(bypass_path.open("w", encoding="utf-8"))
        audit_handle = stack.enter_context(audit_path.open("w", encoding="utf-8"))
        hit_handle = stack.enter_context((reports / "deterministic_rule_hits.csv").open("w", encoding="utf-8-sig", newline=""))
        hit_writer = csv.DictWriter(hit_handle, fieldnames=hit_fields)
        hit_writer.writeheader()
        grouped_handles: dict[str, TextIO] = {}
        group_handles: dict[str, TextIO] = {}
        map_handles: dict[str, TextIO] = {}
        for ordinal, (_, original) in enumerate(iter_jsonl(input_path), start=1):
            normalized = dict(original)
            if restore_literals and isinstance(normalized.get("text"), str):
                normalized["text"] = normalized["text"].replace("\\n", "\n")
            write_jsonl_record(normalized_handle, normalized)
            group = group_for(normalized)
            text = normalized.get("text") if isinstance(normalized.get("text"), str) else ""
            cleaning = clean_text(
                text,
                title=str(normalized.get("title") or ""),
                source=group.source,
                domain=group.domain,
                doc_id=str(normalized.get("doc_id") or ""),
                site_rules=site_rules,
            )
            for hit in cleaning.hits:
                hit_writer.writerow(hit.as_dict())
                hit_counts[hit.rule_id] = hit_counts.get(hit.rule_id, 0) + 1
            cleaned = dict(normalized)
            cleaned["text"] = cleaning.text
            flags = detect_anomalies(cleaning.text, title=str(cleaned.get("title") or ""), before_clean_text=text)
            decision = decisions[group.key]
            use_dj = decision.allow_deduplication and not options.skip_data_juicer and not has_severe_page_anomaly(flags)
            cleaned["__dj_order"] = ordinal
            cleaned["__dj_group_key"] = group.key
            cleaned["__dj_risk_flags"] = flags + (["small_group_risk"] if decision.small_group_risk and use_dj else [])
            if group.slug not in grouped_handles:
                grouped_handles[group.slug] = stack.enter_context((grouped_dir / f"{group.slug}.jsonl").open("w", encoding="utf-8"))
            write_jsonl_record(grouped_handles[group.slug], cleaned)
            protection_stats: dict[str, Any] = {}
            if use_dj:
                protection = protect_text(cleaning.text, doc_key=f"{ordinal}|{cleaned.get('doc_id', '')}", title=str(cleaned.get("title") or ""))
                metadata = {key: value for key, value in cleaned.items() if key not in {"text", "__dj_order", "__dj_group_key", "__dj_risk_flags"}}
                dj_record = {
                    "text": protection.text,
                    "__dj_order": ordinal,
                    "__dj_group_key": group.key,
                    "__dj_metadata_json": json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
                    "__dj_key_order_json": json.dumps(list(normalized.keys()), ensure_ascii=False),
                    "__dj_protection_json": json.dumps(protection.serializable_map(), ensure_ascii=False, separators=(",", ":")),
                    "__dj_risk_flags_json": json.dumps(cleaned["__dj_risk_flags"], ensure_ascii=False),
                }
                protection_stats = protection.stats
                for key, value in protection.stats.items():
                    if isinstance(value, int):
                        protection_totals[key] = protection_totals.get(key, 0) + value
                if group.slug not in group_handles:
                    group_handles[group.slug] = stack.enter_context((protected_dir / f"{group.slug}.jsonl").open("w", encoding="utf-8"))
                    map_handles[group.slug] = stack.enter_context((map_dir / f"{group.slug}.jsonl").open("w", encoding="utf-8"))
                write_jsonl_record(group_handles[group.slug], dj_record)
                write_jsonl_record(map_handles[group.slug], {"ordinal": ordinal, "doc_id": cleaned.get("doc_id"), "protection": protection.serializable_map()})
            else:
                write_jsonl_record(bypass_handle, cleaned)
            write_jsonl_record(audit_handle, {
                "ordinal": ordinal,
                "doc_id": cleaned.get("doc_id"),
                "group_key": group.key,
                "data_juicer_applied": use_dj,
                "matched_rule_ids": [hit.rule_id for hit in cleaning.hits],
                "risk_flags": cleaned["__dj_risk_flags"],
                "cleaning_counters": cleaning.counters,
                "protection_stats": protection_stats,
            })

    rule_summary_rows = [{"rule_id": rule_id, "hit_count": count} for rule_id, count in sorted(hit_counts.items())]
    write_csv(reports / "deterministic_rule_hit_summary.csv", rule_summary_rows, ["rule_id", "hit_count"])
    write_json(reports / "protected_blocks_summary.json", protection_totals)

    runs_by_slug: dict[str, Any] = {}
    for row in group_rows:
        slug, key = str(row["group_name"]), str(row["group_key"])
        group_input = protected_dir / f"{slug}.jsonl"
        if not group_input.exists():
            continue
        group_output = dj_output_dir / f"{slug}.jsonl"
        config_path = config_dir / f"{slug}.yaml"
        threshold = decisions[key].actual_threshold
        if threshold is None:
            raise PipelineBlocked(f"分组 {key} 缺少实际阈值")
        config = build_config(group_input, group_output, intermediate / "work" / slug, threshold)
        write_config(config_path, config)
        run = run_data_juicer(config_path, group_output, logs / "data_juicer_groups")
        runs_by_slug[slug] = run
        row["output_file"] = str(group_output)
        row["return_code"] = run.return_code
        if run.return_code != 0 or not group_output.is_file():
            write_csv(reports / "data_juicer_group_summary.csv", group_rows, group_fields)
            raise PipelineBlocked(f"Data-Juicer 分组失败: {key}，返回码 {run.return_code}，见 {run.stderr_path}")
    write_csv(reports / "data_juicer_group_summary.csv", group_rows, group_fields)

    store_path = intermediate / "ordered_results.sqlite3"
    restore_failures: list[dict[str, Any]] = []
    with OrderedRecordStore(store_path) as store:
        for _, record in iter_jsonl(bypass_path):
            store.add(int(record["__dj_order"]), record)
        for slug, run in runs_by_slug.items():
            for _, record in iter_jsonl(run.output_path):
                try:
                    mapping = json.loads(str(record.get("__dj_protection_json") or ""))
                    metadata = json.loads(str(record.get("__dj_metadata_json") or ""))
                    key_order = json.loads(str(record.get("__dj_key_order_json") or "[]"))
                    risk_flags = json.loads(str(record.get("__dj_risk_flags_json") or "[]"))
                except json.JSONDecodeError as exc:
                    restore_failures.append({"doc_id": "", "error": f"中间元数据 JSON 损坏: {exc}", "group": slug})
                    continue
                if not isinstance(mapping, dict) or not isinstance(metadata, dict):
                    restore_failures.append({"doc_id": record.get("doc_id"), "error": "保护映射丢失", "group": slug})
                    continue
                protection = protection_from_map(mapping)
                restored = restore_text(str(record.get("text") or ""), protection)
                if not restored.success:
                    restore_failures.append({"doc_id": record.get("doc_id"), "errors": restored.errors, "group": slug})
                    continue
                values = dict(metadata)
                values["text"] = restored.text
                final_record = {key: values[key] for key in key_order if isinstance(key, str) and key in values}
                for key, value in values.items():
                    final_record.setdefault(key, value)
                final_record["__dj_order"] = int(record["__dj_order"])
                final_record["__dj_group_key"] = str(record.get("__dj_group_key") or "")
                final_record["__dj_risk_flags"] = risk_flags if isinstance(risk_flags, list) else []
                store.add(int(record["__dj_order"]), final_record)
        store.commit()
        if restore_failures:
            with (quarantine / "protected_block_restore_failed.jsonl").open("w", encoding="utf-8") as handle:
                for failure in restore_failures:
                    write_jsonl_record(handle, failure)
            raise PipelineBlocked(f"保护块恢复失败 {len(restore_failures)} 篇；不生成正式输出")
        candidate_output = intermediate / "candidate_output.jsonl"
        output_count = store.export(candidate_output)

    expected_count = int(inspection.summary["valid_json_count"])
    parsed_count, output_issues = validate_jsonl(candidate_output)
    if output_count != expected_count or parsed_count != expected_count or output_issues:
        raise PipelineBlocked(f"输出文档数异常：期望 {expected_count}，写入 {output_count}，可解析 {parsed_count}")
    comparison = compare_files(input_path, candidate_output, reports, audit_path=audit_path, sample_count=options.sample_count)
    invariant_errors: list[str] = []
    for key in ("doc_id_changed_count", "title_changed_count", "url_changed_count", "metadata_changed_count", "markdown_table_changed_count", "attachment_link_removed_count", "missing_document_count", "new_document_count"):
        if int(comparison.summary.get(key, 0)):
            invariant_errors.append(f"{key}={comparison.summary[key]}")
    high_removal_count = int(comparison.summary.get("removal_over_50_percent_count", 0))
    if expected_count and high_removal_count >= 5 and high_removal_count / expected_count > 0.15:
        invariant_errors.append(f"大量文档删除比例超过 50%: {high_removal_count}/{expected_count}")
    if float(comparison.summary.get("total_removal_ratio", 0.0)) > 0.50:
        invariant_errors.append(f"总体删除比例超过 50%: {comparison.summary['total_removal_ratio']:.2%}")
    if invariant_errors:
        write_quarantine(candidate_output, comparison.details, quarantine)
        raise PipelineBlocked("输出不变量校验失败：" + ", ".join(invariant_errors))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and options.force:
        output_path.unlink()
    shutil.move(str(candidate_output), str(output_path))
    quarantine_counts = write_quarantine(output_path, comparison.details, quarantine)
    commands = [run.command for run in runs_by_slug.values()]
    summary = {
        "run_id": run_id,
        "status": "success",
        "input": str(input_path),
        "output": str(output_path),
        "input_document_count": expected_count,
        "output_document_count": output_count,
        "data_juicer_commands": commands,
        "comparison": comparison.summary,
        "quarantine_counts": quarantine_counts,
        "run_directories": {key: str(value) for key, value in run_dirs.items()},
    }
    write_json(logs / "run_summary.json", summary)
    return summary
