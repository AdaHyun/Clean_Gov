"""Position-aware and source-scoped website rule engine."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


LINK_RE = re.compile(r"https?://|\[[^\]]+\]\([^)]+\)", re.I)


@dataclass(frozen=True)
class SiteRule:
    rule_id: str
    description: str
    match_type: str
    pattern: str
    source: str = "*"
    domain: str = "*"
    region: str = ""
    max_position_ratio: float = 1.0
    min_position_ratio: float = 0.0
    preserve_link: bool = True
    preserve_metadata_field: str = ""
    risk_level: str = "low"
    enabled: bool = True
    block_start: str = ""
    block_end: str = ""

    def applies_to(self, source: str, domain: str) -> bool:
        return fnmatch.fnmatchcase(source or "", self.source) and fnmatch.fnmatchcase(domain or "", self.domain)

    def in_region(self, ratio: float) -> bool:
        if self.region == "header_region" and self.max_position_ratio == 1.0 and ratio > 0.35:
            return False
        if self.region == "footer_region" and self.min_position_ratio == 0.0 and ratio < 0.65:
            return False
        return self.min_position_ratio <= ratio <= self.max_position_ratio

    def matches(self, line: str) -> bool:
        stripped = line.strip()
        if self.match_type == "exact_line":
            return stripped == self.pattern
        if self.match_type == "anchored_regex":
            return bool(re.fullmatch(self.pattern, stripped))
        raise ValueError(f"不支持的站点规则类型: {self.match_type}")


def load_site_rules(path: Path) -> list[SiteRule]:
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rows = raw.get("rules", []) if isinstance(raw, dict) else []
    rules: list[SiteRule] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("site_rules.yaml 中每条规则必须是映射")
        data = dict(row)
        data["rule_id"] = str(data.pop("id", data.get("rule_id", "")))
        rule_id = data["rule_id"]
        if not rule_id or rule_id in seen:
            raise ValueError(f"站点规则 id 缺失或重复: {rule_id!r}")
        seen.add(data["rule_id"])
        if data.get("risk_level") == "high" and data.get("enabled", False):
            raise ValueError(f"高风险规则必须默认关闭: {data['rule_id']}")
        rules.append(SiteRule(**data))
    return rules


def apply_site_rules(
    lines: list[str], *, source: str, domain: str, rules: list[SiteRule]
) -> tuple[list[str], list[tuple[SiteRule, int, str]]]:
    """Apply exact/full-match rules with optional header/footer constraints."""
    kept: list[str] = []
    removed: list[tuple[SiteRule, int, str]] = []
    total = max(len(lines) - 1, 1)
    block_members: dict[int, SiteRule] = {}
    for rule in rules:
        if not rule.enabled or not rule.block_start or not rule.block_end or not rule.applies_to(source, domain):
            continue
        start = 0
        while start < len(lines):
            if not re.fullmatch(rule.block_start, lines[start].strip()) or not rule.in_region(start / total):
                start += 1
                continue
            end = start + 1
            while end < len(lines) and not re.fullmatch(rule.block_end, lines[end].strip()):
                end += 1
            if end >= len(lines):
                break  # never delete an unterminated block
            for position in range(start, end + 1):
                block_members[position] = rule
            start = end + 1
    for index, line in enumerate(lines):
        ratio = index / total
        matched: SiteRule | None = block_members.get(index)
        if matched and matched.preserve_link and LINK_RE.search(line):
            matched = None
        for rule in rules:
            if matched is not None:
                break
            if rule.block_start or rule.block_end:
                continue
            if not rule.enabled or not rule.applies_to(source, domain) or not rule.in_region(ratio):
                continue
            if rule.preserve_link and LINK_RE.search(line):
                continue
            if rule.matches(line):
                matched = rule
                break
        if matched:
            removed.append((matched, index, line))
        else:
            kept.append(line)
    return kept, removed
