"""Per-group threshold policy matching Data-Juicer's strict greater-than rule."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ThresholdDecision:
    recommended_threshold: int
    actual_threshold: int | None
    allow_deduplication: bool
    small_group_risk: bool
    threshold_source: str
    skipped_reason: str


def recommend_threshold(document_count: int) -> int:
    if document_count < 20:
        return 5
    if document_count < 100:
        return 5
    if document_count < 1000:
        return 10
    return 20


def load_threshold_map(path: Path | None) -> dict[str, int]:
    if path is None:
        return {}
    data: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("--frequency-threshold-map 必须是 YAML 映射")
    result: dict[str, int] = {}
    for key, value in data.items():
        if not isinstance(value, int) or value < 1:
            raise ValueError(f"阈值必须是正整数: {key}={value!r}")
        result[str(key)] = value
    return result


def decide_threshold(
    *,
    group_key: str,
    source: str,
    domain: str,
    document_count: int,
    override: int | None,
    mapping: dict[str, int],
    allow_small_group: bool,
) -> ThresholdDecision:
    recommended = recommend_threshold(document_count)
    threshold = override
    threshold_source = "cli" if override is not None else "policy"
    for candidate in (group_key, f"{source} + {domain}", domain, source):
        if candidate and candidate in mapping:
            threshold = mapping[candidate]
            threshold_source = f"map:{candidate}"
            break
    if threshold is None:
        threshold = recommended
    small = document_count < 20
    if small and not allow_small_group:
        return ThresholdDecision(recommended, None, False, False, threshold_source, "文档数少于 20，默认跳过")
    return ThresholdDecision(recommended, threshold, document_count > 1, small, threshold_source, "" if document_count > 1 else "仅一篇文档")
