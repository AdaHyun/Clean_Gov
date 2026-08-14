"""Audit-safe topic partitioning and exact-line noise cleanup for LLM annotations."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonl_io import iter_jsonl, write_jsonl_record


DATA_JUICER_STATS_FIELD = "__dj__stats__"
LLM_SCORE_KEY = "llm_quality_score"
LLM_RECORD_KEY = "llm_quality_record"


def _score(record: dict[str, Any]) -> float:
    stats = record.get(DATA_JUICER_STATS_FIELD)
    if not isinstance(stats, dict):
        return 0.0
    value = stats.get(LLM_SCORE_KEY, 0.0)
    if isinstance(value, bool):
        return 0.0
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if 0.0 <= parsed <= 1.0 else 0.0


def _analysis(record: dict[str, Any]) -> dict[str, Any] | None:
    stats = record.get(DATA_JUICER_STATS_FIELD)
    if not isinstance(stats, dict):
        return None
    raw = stats.get(LLM_RECORD_KEY)
    if isinstance(raw, dict):
        value = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return None
    else:
        return None
    return value if isinstance(value, dict) else None


def _list_of_codes(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            code = item.strip()
            if code not in result:
                result.append(code)
    return result


def _bounded_float(value: Any, *, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(parsed, 0.0), 1.0)


def _relevance(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(max(parsed, 0.0), 5.0)


def load_tag_labels(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"LLM 标签对照文件不是 JSON 对象: {path}")
    for key in (
        "topic_tags",
        "content_types",
        "exclusion_tags",
        "noise_types",
        "repairable_flags",
        "topic_decisions",
        "training_use",
    ):
        if not isinstance(value.get(key), dict):
            raise ValueError(f"LLM 标签对照文件缺少对象字段 {key!r}: {path}")
    return value


def _labels(codes: list[str], mapping: dict[str, str]) -> list[str]:
    return [mapping.get(code, f"未映射标签：{code}") for code in codes]


def annotate_with_chinese_labels(
    record: dict[str, Any],
    analysis: dict[str, Any],
    label_config: dict[str, Any],
    *,
    provider_name: str,
) -> dict[str, Any]:
    annotated = dict(record)
    topic_tags = _list_of_codes(analysis.get("topic_tags"))
    exclusion_tags = _list_of_codes(analysis.get("exclusion_tags"))
    repairable_flags = _list_of_codes(analysis.get("repairable_flags"))
    training_use = _list_of_codes(analysis.get("training_use"))
    content_type = str(analysis.get("content_type") or "other").strip()
    decision = str(analysis.get("topic_decision") or "review").strip().lower()
    if decision not in {"keep", "exclude", "review"}:
        decision = "review"

    raw_segments = analysis.get("noise_segments")
    localized_segments: list[dict[str, Any]] = []
    if isinstance(raw_segments, list):
        for segment in raw_segments:
            if not isinstance(segment, dict):
                continue
            noise_type = str(segment.get("noise_type") or "").strip()
            localized = dict(segment)
            localized["noise_type"] = noise_type
            localized["noise_type_zh"] = label_config["noise_types"].get(
                noise_type, f"未映射标签：{noise_type}"
            )
            localized_segments.append(localized)

    annotated.update(
        {
            "llm_quality_score": _score(record),
            "llm_topic_decision": decision,
            "llm_topic_decision_zh": label_config["topic_decisions"].get(
                decision, f"未映射标签：{decision}"
            ),
            "llm_topic_confidence": _bounded_float(analysis.get("topic_confidence")),
            "llm_public_health_relevance": _relevance(
                analysis.get("public_health_relevance")
            ),
            "llm_substantive_public_health_content": bool(
                analysis.get("substantive_public_health_content") is True
            ),
            "llm_content_type": content_type,
            "llm_content_type_zh": label_config["content_types"].get(
                content_type, f"未映射标签：{content_type}"
            ),
            "llm_topic_tags": topic_tags,
            "llm_topic_tags_zh": _labels(topic_tags, label_config["topic_tags"]),
            "llm_exclusion_tags": exclusion_tags,
            "llm_exclusion_tags_zh": _labels(
                exclusion_tags, label_config["exclusion_tags"]
            ),
            "llm_repairable_flags": repairable_flags,
            "llm_repairable_flags_zh": _labels(
                repairable_flags, label_config["repairable_flags"]
            ),
            "llm_training_use": training_use,
            "llm_training_use_zh": _labels(training_use, label_config["training_use"]),
            "llm_noise_segments": localized_segments,
            "llm_analysis_rationale": str(analysis.get("rationale") or ""),
            "llm_analysis_provider": provider_name,
        }
    )
    return annotated


def partition_topic_annotations(
    scored_path: Path,
    annotated_all_path: Path,
    candidate_path: Path,
    topic_excluded_path: Path,
    low_quality_path: Path,
    retry_path: Path,
    review_path: Path,
    report_path: Path,
    label_config_path: Path,
    *,
    min_quality_score: float,
    min_topic_confidence: float,
    min_public_health_relevance: float,
    provider_name: str,
) -> dict[str, Any]:
    """Partition first-pass annotations without silently accepting uncertain rows."""
    if not 0.0 < min_quality_score <= 1.0:
        raise ValueError("LLM quality min score 必须位于 (0, 1]")
    if not 0.0 <= min_topic_confidence <= 1.0:
        raise ValueError("LLM topic confidence 必须位于 [0, 1]")
    if not 1.0 <= min_public_health_relevance <= 5.0:
        raise ValueError("公共卫生相关度阈值必须位于 [1, 5]")
    label_config = load_tag_labels(label_config_path)
    hard_exclusions = set(_list_of_codes(label_config.get("hard_exclusion_tags")))
    for path in (
        annotated_all_path,
        candidate_path,
        topic_excluded_path,
        low_quality_path,
        retry_path,
        review_path,
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise FileExistsError(f"Stage 04 输出已存在，不允许覆盖: {path}")

    counts: Counter[str] = Counter()
    content_types: Counter[str] = Counter()
    topic_tags: Counter[str] = Counter()
    exclusion_tags: Counter[str] = Counter()
    with (
        annotated_all_path.open("w", encoding="utf-8") as annotated_all_handle,
        candidate_path.open("w", encoding="utf-8") as candidate_handle,
        topic_excluded_path.open("w", encoding="utf-8") as excluded_handle,
        low_quality_path.open("w", encoding="utf-8") as low_handle,
        retry_path.open("w", encoding="utf-8") as retry_handle,
        review_path.open("w", encoding="utf-8") as review_handle,
    ):
        for _, row in iter_jsonl(scored_path):
            counts["input"] += 1
            score = _score(row)
            analysis = _analysis(row)
            if score <= 0.0 or analysis is None:
                failed = dict(row)
                failed.update(
                    {
                        "llm_policy_status": "llm_api_or_response_failed_retry_required",
                        "llm_policy_status_zh": "模型调用或结构化结果失败，等待重试",
                        "quarantine_reason": "llm_api_or_response_failed_retry_required",
                        "quarantine_stage": "04_llm_topic_quality",
                    }
                )
                write_jsonl_record(annotated_all_handle, failed)
                write_jsonl_record(retry_handle, failed)
                counts["retry_required"] += 1
                continue

            annotated = annotate_with_chinese_labels(
                row, analysis, label_config, provider_name=provider_name
            )
            content_types[annotated["llm_content_type"]] += 1
            topic_tags.update(annotated["llm_topic_tags"])
            exclusion_tags.update(annotated["llm_exclusion_tags"])

            if score < min_quality_score:
                annotated.update(
                    {
                        "llm_policy_status": "low_quality_excluded",
                        "llm_policy_status_zh": "质量评分低于阈值，已隔离",
                        "quarantine_reason": "llm_quality_score_below_threshold",
                        "quarantine_stage": "04_llm_topic_quality",
                    }
                )
                write_jsonl_record(annotated_all_handle, annotated)
                write_jsonl_record(low_handle, annotated)
                counts["low_quality_excluded"] += 1
                continue

            decision = annotated["llm_topic_decision"]
            confidence = float(annotated["llm_topic_confidence"])
            relevance = float(annotated["llm_public_health_relevance"])
            substantive = bool(annotated["llm_substantive_public_health_content"])
            hard_hits = hard_exclusions.intersection(annotated["llm_exclusion_tags"])
            if (
                decision == "exclude"
                and confidence >= min_topic_confidence
                and not substantive
                and hard_hits
            ):
                annotated.update(
                    {
                        "llm_policy_status": "unrelated_topic_excluded",
                        "llm_policy_status_zh": "高置信度无关主题，已隔离",
                        "quarantine_reason": "llm_unrelated_topic_high_confidence",
                        "quarantine_stage": "04_llm_topic_quality",
                    }
                )
                write_jsonl_record(annotated_all_handle, annotated)
                write_jsonl_record(excluded_handle, annotated)
                counts["topic_excluded"] += 1
                continue

            conflict = bool(hard_hits) or decision != "keep" or not substantive
            if confidence < min_topic_confidence or relevance < min_public_health_relevance:
                conflict = True
            if conflict:
                annotated.update(
                    {
                        "llm_policy_status": "manual_review_required",
                        "llm_policy_status_zh": "主题或标签存在不确定性，等待人工复核",
                        "quarantine_reason": "llm_topic_or_policy_review_required",
                        "quarantine_stage": "04_llm_topic_quality",
                    }
                )
                write_jsonl_record(annotated_all_handle, annotated)
                write_jsonl_record(review_handle, annotated)
                counts["manual_review_required"] += 1
                continue

            annotated.update(
                {
                    "llm_policy_status": "topic_quality_candidate_keep",
                    "llm_policy_status_zh": "主题相关且质量达标，进入本地噪声清理",
                }
            )
            write_jsonl_record(annotated_all_handle, annotated)
            write_jsonl_record(candidate_handle, annotated)
            counts["candidate_keep"] += 1

    summary = {
        "stage": "04_llm_topic_quality",
        "provider": provider_name,
        "input_document_count": counts["input"],
        "candidate_keep_count": counts["candidate_keep"],
        "topic_excluded_count": counts["topic_excluded"],
        "low_quality_excluded_count": counts["low_quality_excluded"],
        "manual_review_required_count": counts["manual_review_required"],
        "llm_retry_required_count": counts["retry_required"],
        "min_quality_score": min_quality_score,
        "min_topic_confidence": min_topic_confidence,
        "min_public_health_relevance": min_public_health_relevance,
        "tag_label_config": str(label_config_path.resolve()),
        "annotated_all_path": str(annotated_all_path.resolve()),
        "content_type_counts": dict(content_types.most_common()),
        "topic_tag_counts": dict(topic_tags.most_common()),
        "exclusion_tag_counts": dict(exclusion_tags.most_common()),
        "candidate_path": str(candidate_path.resolve()),
        "topic_excluded_path": str(topic_excluded_path.resolve()),
        "low_quality_path": str(low_quality_path.resolve()),
        "retry_path": str(retry_path.resolve()),
        "review_path": str(review_path.resolve()),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


@dataclass(frozen=True)
class NoiseCleanupResult:
    text: str
    status: str
    removed_line_count: int
    removed_character_count: int
    removed_types: tuple[str, ...]
    issues: tuple[str, ...]


_HTML_TABLE_LINE = re.compile(r"<\s*/?\s*(?:table|thead|tbody|tfoot|tr|td|th)\b", re.I)
_MARKDOWN_TABLE_LINE = re.compile(r"^\s*\|.*\|\s*$")


def _table_structural_line(line: str) -> bool:
    return bool(_HTML_TABLE_LINE.search(line) or _MARKDOWN_TABLE_LINE.match(line))


def clean_validated_noise_lines(
    text: str,
    segments: list[dict[str, Any]],
    *,
    allowed_noise_types: set[str],
    min_confidence: float,
    max_removed_ratio: float,
    min_remaining_characters: int,
) -> NoiseCleanupResult:
    """Delete only exact, uniquely located, non-table full-line noise blocks."""
    if not segments:
        return NoiseCleanupResult(text, "no_noise_reported", 0, 0, (), ())
    lines = text.splitlines()
    delete_indexes: set[int] = set()
    removed_types: list[str] = []
    issues: list[str] = []

    for segment_index, segment in enumerate(segments):
        noise_type = str(segment.get("noise_type") or "").strip()
        confidence = _bounded_float(segment.get("confidence"))
        exact_lines = segment.get("exact_lines")
        if noise_type not in allowed_noise_types:
            issues.append(f"segment_{segment_index}:unsupported_noise_type:{noise_type}")
            continue
        if confidence < min_confidence:
            issues.append(f"segment_{segment_index}:confidence_below_threshold")
            continue
        if not isinstance(exact_lines, list) or not exact_lines:
            issues.append(f"segment_{segment_index}:missing_exact_lines")
            continue
        expected = [str(item).strip() for item in exact_lines]
        if any(not item for item in expected):
            issues.append(f"segment_{segment_index}:empty_exact_line")
            continue
        if any(_table_structural_line(item) for item in expected):
            issues.append(f"segment_{segment_index}:table_structure_protected")
            continue

        matches: list[int] = []
        width = len(expected)
        for start in range(0, len(lines) - width + 1):
            if [item.strip() for item in lines[start : start + width]] == expected:
                matches.append(start)
        requested_start = segment.get("start_line")
        selected: int | None = None
        if isinstance(requested_start, int) and requested_start >= 1:
            zero_based = requested_start - 1
            if zero_based in matches:
                selected = zero_based
        if selected is None and len(matches) == 1:
            selected = matches[0]
        if selected is None:
            issues.append(
                f"segment_{segment_index}:exact_block_match_count:{len(matches)}"
            )
            continue
        delete_indexes.update(range(selected, selected + width))
        if noise_type not in removed_types:
            removed_types.append(noise_type)

    if issues:
        return NoiseCleanupResult(text, "review_required", 0, 0, (), tuple(issues))
    if not delete_indexes:
        return NoiseCleanupResult(
            text, "review_required", 0, 0, (), ("no_validated_noise_lines",)
        )

    removed_characters = sum(len(lines[index]) for index in delete_indexes)
    ratio = removed_characters / max(len(text), 1)
    if ratio > max_removed_ratio:
        return NoiseCleanupResult(
            text,
            "review_required",
            0,
            0,
            (),
            (f"removed_ratio_exceeds_limit:{ratio:.6f}",),
        )
    kept_lines = [line for index, line in enumerate(lines) if index not in delete_indexes]
    cleaned = "\n".join(kept_lines)
    cleaned = re.sub(r"(?:[ \t]*\n){3,}", "\n\n", cleaned).strip()
    if len(cleaned) < min_remaining_characters:
        return NoiseCleanupResult(
            text,
            "review_required",
            0,
            0,
            (),
            (f"remaining_text_too_short:{len(cleaned)}",),
        )
    return NoiseCleanupResult(
        cleaned,
        "cleaned",
        len(delete_indexes),
        removed_characters,
        tuple(removed_types),
        (),
    )


def apply_local_noise_cleanup(
    candidate_path: Path,
    kept_path: Path,
    cleaned_audit_path: Path,
    review_path: Path,
    report_path: Path,
    label_config_path: Path,
    *,
    min_noise_confidence: float,
    max_removed_ratio: float,
    min_remaining_characters: int,
) -> dict[str, Any]:
    label_config = load_tag_labels(label_config_path)
    allowed_noise_types = set(_list_of_codes(label_config.get("allowed_noise_types")))
    for path in (kept_path, cleaned_audit_path, review_path):
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise FileExistsError(f"Stage 05 输出已存在，不允许覆盖: {path}")

    counts: Counter[str] = Counter()
    removed_type_counts: Counter[str] = Counter()
    with (
        kept_path.open("w", encoding="utf-8") as kept_handle,
        cleaned_audit_path.open("w", encoding="utf-8") as audit_handle,
        review_path.open("w", encoding="utf-8") as review_handle,
    ):
        for _, row in iter_jsonl(candidate_path):
            counts["input"] += 1
            text = row.get("text") if isinstance(row.get("text"), str) else ""
            segments = row.get("llm_noise_segments")
            result = clean_validated_noise_lines(
                text,
                segments if isinstance(segments, list) else [],
                allowed_noise_types=allowed_noise_types,
                min_confidence=min_noise_confidence,
                max_removed_ratio=max_removed_ratio,
                min_remaining_characters=min_remaining_characters,
            )
            processed = dict(row)
            processed["llm_noise_cleanup_status"] = result.status
            processed["llm_noise_cleanup_status_zh"] = {
                "no_noise_reported": "模型未报告栏目噪声，正文保持不变",
                "cleaned": "已由本地程序精确删除栏目噪声",
                "review_required": "噪声删除未通过安全校验，等待人工复核",
            }[result.status]
            processed["llm_noise_removed_line_count"] = result.removed_line_count
            processed["llm_noise_removed_character_count"] = result.removed_character_count
            processed["llm_noise_removed_types"] = list(result.removed_types)
            processed["llm_noise_removed_types_zh"] = _labels(
                list(result.removed_types), label_config["noise_types"]
            )
            processed["llm_noise_cleanup_issues"] = list(result.issues)
            if result.status == "review_required":
                processed.update(
                    {
                        "quarantine_reason": "llm_noise_cleanup_review_required",
                        "quarantine_stage": "05_local_noise_cleanup",
                    }
                )
                write_jsonl_record(review_handle, processed)
                counts["review_required"] += 1
                continue
            if result.status == "cleaned":
                processed["text"] = result.text
                write_jsonl_record(audit_handle, processed)
                counts["cleaned"] += 1
                removed_type_counts.update(result.removed_types)
            else:
                counts["unchanged"] += 1
            processed["llm_policy_status"] = "accepted_after_local_noise_cleanup"
            processed["llm_policy_status_zh"] = "通过主题、质量和本地噪声清理，正式保留"
            write_jsonl_record(kept_handle, processed)

    summary = {
        "stage": "05_local_noise_cleanup",
        "input_document_count": counts["input"],
        "kept_document_count": counts["cleaned"] + counts["unchanged"],
        "noise_cleaned_document_count": counts["cleaned"],
        "unchanged_document_count": counts["unchanged"],
        "noise_cleanup_review_required_count": counts["review_required"],
        "removed_noise_type_counts": dict(removed_type_counts.most_common()),
        "min_noise_confidence": min_noise_confidence,
        "max_removed_ratio": max_removed_ratio,
        "min_remaining_characters": min_remaining_characters,
        "tag_label_config": str(label_config_path.resolve()),
        "kept_path": str(kept_path.resolve()),
        "cleaned_audit_path": str(cleaned_audit_path.resolve()),
        "review_path": str(review_path.resolve()),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
