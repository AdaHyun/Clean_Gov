"""Conservative deterministic cleaning performed before Data-Juicer."""

from __future__ import annotations

import html
import re
from dataclasses import asdict, dataclass
from typing import Any

from site_rule_engine import SiteRule, apply_site_rules


BASE64_IMAGE_RE = re.compile(r"!\[[^\]\r\n]*\]\(data:image/[A-Za-z0-9.+-]+;base64,[A-Za-z0-9+/=\t ]*\)", re.I)
MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]\r\n]*\]\((?!data:image/)[^)\r\n]+\)", re.I)
HTML_ENTITY_RE = re.compile(r"&(?:nbsp|amp|lt|gt|quot|apos|#\d+|#x[0-9a-f]+);", re.I)


@dataclass
class RuleHit:
    rule_id: str
    rule_type: str
    matched_text: str
    document_position: int
    before_text: str
    after_text: str
    removed_character_count: int
    source: str
    domain: str
    doc_id: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CleaningResult:
    text: str
    hits: list[RuleHit]
    counters: dict[str, int]


def _hit(rule_id: str, rule_type: str, matched: str, position: int, before: str, after: str, *, source: str, domain: str, doc_id: str) -> RuleHit:
    return RuleHit(rule_id, rule_type, matched, position, before, after, max(0, len(before) - len(after)), source, domain, doc_id)


def clean_text(
    text: str,
    *,
    title: str = "",
    source: str = "",
    domain: str = "",
    doc_id: str = "",
    site_rules: list[SiteRule] | None = None,
    restore_literal_newlines: bool = False,
) -> CleaningResult:
    """Apply only deterministic, auditable operations."""
    hits: list[RuleHit] = []
    counters = {"base64_characters_removed": 0, "html_entities_decoded": 0, "markdown_images_removed": 0}
    current = text
    if restore_literal_newlines and "\\n" in current:
        before = current
        current = current.replace("\\n", "\n")
        hits.append(_hit("restore_literal_newlines", "normalization", "\\n", 0, before, current, source=source, domain=domain, doc_id=doc_id))

    entity_count = len(HTML_ENTITY_RE.findall(current))
    if entity_count:
        before = current
        current = html.unescape(current).replace("\xa0", " ")
        counters["html_entities_decoded"] = entity_count
        hits.append(_hit("html_unescape", "html_entity", f"{entity_count} entities", 0, before, current, source=source, domain=domain, doc_id=doc_id))

    def strip_images(line: str, position: int) -> str:
        before = line
        base64_matches = list(BASE64_IMAGE_RE.finditer(line))
        if base64_matches:
            removed = sum(len(match.group(0)) for match in base64_matches)
            line = BASE64_IMAGE_RE.sub("", line)
            counters["base64_characters_removed"] += removed
            hits.append(_hit("base64_markdown_image", "base64_image", "<base64 image>", position, before, line, source=source, domain=domain, doc_id=doc_id))
            before = line
        image_matches = list(MARKDOWN_IMAGE_RE.finditer(line))
        if image_matches:
            line = MARKDOWN_IMAGE_RE.sub("", line)
            counters["markdown_images_removed"] += len(image_matches)
            hits.append(_hit("markdown_image_placeholder", "markdown_image", " | ".join(m.group(0)[:200] for m in image_matches), position, before, line, source=source, domain=domain, doc_id=doc_id))
        return line

    lines = [strip_images(line, index) for index, line in enumerate(current.split("\n"))]
    if site_rules:
        lines, removed = apply_site_rules(lines, source=source, domain=domain, rules=site_rules)
        for rule, position, line in removed:
            hits.append(_hit(rule.rule_id, "site_rule", line, position, line, "", source=source, domain=domain, doc_id=doc_id))

    # Strip trailing/leading horizontal whitespace while preserving line structure.
    lines = [re.sub(r"^[\t \u3000]+|[\t \u3000]+$", "", line) for line in lines]

    # Remove consecutive exact duplicate non-empty lines only.
    deduped: list[str] = []
    for position, line in enumerate(lines):
        if line and deduped and line == deduped[-1]:
            hits.append(_hit("consecutive_exact_line_duplicate", "line_duplicate", line, position, line, "", source=source, domain=domain, doc_id=doc_id))
            continue
        deduped.append(line)

    # At the beginning, keep a title line once and remove only nearby exact repeats.
    title_norm = title.strip()
    if title_norm:
        seen_title = False
        normalized: list[str] = []
        for position, line in enumerate(deduped):
            if position < 10 and line.strip() == title_norm:
                if seen_title:
                    hits.append(_hit("duplicate_title_near_start", "duplicate_title", line, position, line, "", source=source, domain=domain, doc_id=doc_id))
                    continue
                seen_title = True
            normalized.append(line)
        deduped = normalized

    # Compress runs of blank lines to two, never flatten paragraphs.
    compact: list[str] = []
    blank_run = 0
    for line in deduped:
        if not line:
            blank_run += 1
            if blank_run > 2:
                continue
        else:
            blank_run = 0
        compact.append(line)
    result = "\n".join(compact).strip("\n")
    return CleaningResult(result, hits, counters)
