"""Streaming JSONL inspection and per-group line-frequency reporting."""

from __future__ import annotations

import csv
import json
import random
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from anomaly_detection import detect_anomalies
from jsonl_io import JsonlIssue, iter_jsonl, write_jsonl_record
from protected_blocks import HEADING_RE, MARKDOWN_LINK_RE, is_table_line, markdown_table_blocks
from source_grouping import group_for


HTML_TAG_RE = re.compile(r"</?[A-Za-z][^>]{0,500}>")
HTML_ENTITY_RE = re.compile(r"&(?:nbsp|amp|lt|gt|quot|apos|#\d+|#x[0-9a-f]+);", re.I)
MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
BASE64_RE = re.compile(r"!\[[^\]]*\]\(data:image/[^;\s]+;base64,", re.I)
ATTACHMENT_LINK_RE = re.compile(r"\[[^\]]+\]\([^)]*\.(?:pdf|docx?|xlsx?|pptx?|rar|zip)(?:\?[^)]*)?\)", re.I)


@dataclass
class LineFrequency:
    group_key: str
    source: str
    domain: str
    raw_line: str
    normalized_line: str
    document_frequency: int = 0
    example_doc_ids: list[str] = field(default_factory=list)
    example_titles: list[str] = field(default_factory=list)
    is_markdown_table: bool = False
    is_heading: bool = False
    is_title_match: bool = False
    is_attachment_link: bool = False


@dataclass
class InspectionResult:
    summary: dict[str, Any]
    group_counts: Counter[str]
    group_metadata: dict[str, tuple[str, str, str]]
    line_frequency_rows: list[dict[str, Any]]
    issues: list[JsonlIssue]


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        if not fieldnames:
            return
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _distribution_rows(counter: Counter[str], key_name: str) -> list[dict[str, Any]]:
    total = sum(counter.values()) or 1
    return [{key_name: key, "count": count, "ratio": round(count / total, 8)} for key, count in counter.most_common()]


def _publish_date_format(value: object) -> str:
    if value in (None, ""):
        return "missing"
    text = str(value).strip()
    patterns = (
        (r"\d{4}-\d{2}-\d{2}", "YYYY-MM-DD"),
        (r"\d{4}/\d{1,2}/\d{1,2}", "YYYY/M/D"),
        (r"\d{4}年\d{1,2}月\d{1,2}日", "Chinese date"),
        (r"\d{4}-\d{2}-\d{2}[ T].+", "datetime"),
    )
    for pattern, label in patterns:
        if re.fullmatch(pattern, text):
            return label
    return "other"


def inspect_input(
    input_path: Path,
    report_dir: Path,
    *,
    sample_count: int = 50,
    top_lines: int = 5000,
    encoding: str = "utf-8-sig",
) -> InspectionResult:
    """Inspect an input JSONL without loading complete documents into memory."""
    report_dir.mkdir(parents=True, exist_ok=True)
    issues: list[JsonlIssue] = []
    field_issues: list[dict[str, Any]] = []
    source_counts: Counter[str] = Counter()
    channel_counts: Counter[str] = Counter()
    domain_counts: Counter[str] = Counter()
    group_counts: Counter[str] = Counter()
    publish_counts: Counter[str] = Counter()
    origin_counts: Counter[str] = Counter()
    noise_counts: Counter[str] = Counter()
    doc_ids: Counter[str] = Counter()
    char_counts: list[int] = []
    line_counts: list[int] = []
    group_metadata: dict[str, tuple[str, str, str]] = {}
    frequencies: dict[tuple[str, str], LineFrequency] = {}
    samples: list[dict[str, Any]] = []
    rng = random.Random(20260721)
    valid_count = 0
    nonempty_count = 0
    shortest: dict[str, Any] | None = None
    longest: dict[str, Any] | None = None
    counters: Counter[str] = Counter()

    for line_number, record in iter_jsonl(input_path, encoding=encoding, issues=issues):
        valid_count += 1
        if len(samples) < sample_count:
            samples.append(record)
        else:
            choice = rng.randint(1, valid_count)
            if choice <= sample_count:
                samples[choice - 1] = record
        doc_id = record.get("doc_id")
        title = record.get("title")
        url = record.get("url")
        text = record.get("text")
        for field_name, value in (("doc_id", doc_id), ("title", title), ("url", url), ("text", text)):
            if value is None or value == "":
                counters[f"{field_name}_missing_count"] += 1
                field_issues.append({"line_number": line_number, "doc_id": doc_id or "", "field": field_name, "issue": "missing"})
        if doc_id not in (None, ""):
            doc_ids[str(doc_id)] += 1
        if not isinstance(text, str):
            counters["text_non_string_count"] += 1
            field_issues.append({"line_number": line_number, "doc_id": doc_id or "", "field": "text", "issue": f"non_string:{type(text).__name__}"})
            continue
        if text == "":
            counters["text_empty_count"] += 1
        elif not text.strip():
            counters["text_whitespace_only_count"] += 1
        else:
            nonempty_count += 1
        chars = len(text)
        lines = text.count("\n") + 1
        char_counts.append(chars)
        line_counts.append(lines)
        item = {"line_number": line_number, "doc_id": str(doc_id or ""), "character_count": chars}
        if shortest is None or chars < shortest["character_count"]:
            shortest = item
        if longest is None or chars > longest["character_count"]:
            longest = item
        counters["contains_real_newline_count"] += int("\n" in text)
        counters["contains_literal_newline_count"] += int("\\n" in text)
        counters["contains_both_newline_types_count"] += int("\n" in text and "\\n" in text)
        counters["single_line_document_count"] += int("\n" not in text and bool(text.strip()))
        counters["multi_line_document_count"] += int("\n" in text)
        counters["suspected_html_tag_document_count"] += int(bool(HTML_TAG_RE.search(text)))
        entities = HTML_ENTITY_RE.findall(text)
        counters["html_entity_document_count"] += int(bool(entities))
        counters["nbsp_count"] += len(re.findall(r"&nbsp;", text, re.I))
        counters["markdown_image_count"] += len(MARKDOWN_IMAGE_RE.findall(text))
        counters["base64_image_count"] += len(BASE64_RE.findall(text))
        counters["markdown_attachment_link_count"] += len(ATTACHMENT_LINK_RE.findall(text))
        table_blocks = markdown_table_blocks(text)
        counters["markdown_table_document_count"] += int(bool(table_blocks))
        counters["markdown_table_block_count"] += len(table_blocks)
        counters["markdown_table_line_count"] += sum(len(block) for block in table_blocks)
        flags = detect_anomalies(text, title=str(title or ""))
        for flag in flags:
            noise_counts[flag] += 1
        counters["suspected_attachment_page_count"] += int("attachment_only" in flags)
        counters["suspected_list_page_count"] += int("list_page_contamination" in flags)
        counters["suspected_boilerplate_only_count"] += int("boilerplate_only" in flags)
        counters["suspected_duplicate_title_count"] += int("duplicate_title" in flags)

        group = group_for(record)
        group_counts[group.key] += 1
        group_metadata[group.key] = (group.source, group.domain, group.slug)
        source_counts[group.source or "(missing)"] += 1
        domain_counts[group.domain] += 1
        channel_counts[str(record.get("channel") or "(missing)")] += 1
        publish_counts[_publish_date_format(record.get("publish_date"))] += 1
        origin_counts[str(record.get("content_origin") or "(missing)")] += 1

        unique_lines: dict[str, str] = {}
        for raw_line in text.split("\n"):
            normalized = raw_line.strip()
            if normalized:
                unique_lines.setdefault(normalized, raw_line)
        for normalized, raw_line in unique_lines.items():
            key = (group.key, normalized)
            stat = frequencies.get(key)
            if stat is None:
                stat = LineFrequency(
                    group.key,
                    group.source,
                    group.domain,
                    raw_line,
                    normalized,
                    is_markdown_table=is_table_line(raw_line),
                    is_heading=bool(HEADING_RE.match(raw_line)),
                    is_title_match=bool(title and normalized == str(title).strip()),
                    is_attachment_link=bool(MARKDOWN_LINK_RE.search(raw_line)),
                )
                frequencies[key] = stat
            stat.document_frequency += 1
            if len(stat.example_doc_ids) < 5:
                stat.example_doc_ids.append(str(doc_id or ""))
                stat.example_titles.append(str(title or ""))

    duplicate_doc_ids = sum(count - 1 for count in doc_ids.values() if count > 1)
    duplicate_values = sum(1 for count in doc_ids.values() if count > 1)
    physical_lines = valid_count + len(issues)
    single_ratio = counters["single_line_document_count"] / nonempty_count if nonempty_count else 0.0
    summary: dict[str, Any] = {
        "file_path": str(input_path.resolve()),
        "file_size_bytes": input_path.stat().st_size,
        "total_physical_lines": physical_lines,
        "valid_json_count": valid_count,
        "invalid_json_count": len(issues),
        "total_document_count": valid_count,
        **dict(counters),
        "duplicate_doc_id_value_count": duplicate_values,
        "duplicate_doc_id_occurrence_count": duplicate_doc_ids,
        "single_line_document_ratio": single_ratio,
        "average_line_count": statistics.fmean(line_counts) if line_counts else 0,
        "median_line_count": statistics.median(line_counts) if line_counts else 0,
        "average_character_count": statistics.fmean(char_counts) if char_counts else 0,
        "median_character_count": statistics.median(char_counts) if char_counts else 0,
        "shortest_text": shortest,
        "longest_text": longest,
        "literal_newline_likely_escaped": counters["contains_literal_newline_count"] > max(10, counters["contains_real_newline_count"] * 2),
        "source_distribution": dict(source_counts),
        "channel_distribution": dict(channel_counts),
        "domain_distribution": dict(domain_counts),
        "source_domain_group_distribution": dict(group_counts),
        "publish_date_format_distribution": dict(publish_counts),
        "content_origin_distribution": dict(origin_counts),
    }
    (report_dir / "input_inspection_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    with (report_dir / "invalid_json_lines.jsonl").open("w", encoding="utf-8") as handle:
        for issue in issues:
            write_jsonl_record(handle, issue.__dict__)
    with (report_dir / "samples.jsonl").open("w", encoding="utf-8") as handle:
        for sample in samples:
            write_jsonl_record(handle, sample)
    _write_csv(report_dir / "field_issues.csv", field_issues, ["line_number", "doc_id", "field", "issue"])
    _write_csv(report_dir / "domain_distribution.csv", _distribution_rows(domain_counts, "domain"), ["domain", "count", "ratio"])
    _write_csv(report_dir / "source_distribution.csv", _distribution_rows(source_counts, "source"), ["source", "count", "ratio"])
    group_rows = []
    for key, count in group_counts.most_common():
        source, domain, slug = group_metadata[key]
        group_rows.append({"group_key": key, "source": source, "domain": domain, "group_slug": slug, "document_count": count})
    _write_csv(report_dir / "source_domain_groups.csv", group_rows, ["group_key", "source", "domain", "group_slug", "document_count"])
    _write_csv(report_dir / "noise_type_counts.csv", [{"noise_type": key, "document_count": value} for key, value in noise_counts.most_common()], ["noise_type", "document_count"])

    rows: list[dict[str, Any]] = []
    for stat in frequencies.values():
        group_total = group_counts[stat.group_key]
        protected = stat.is_markdown_table or stat.is_heading or stat.is_title_match or stat.is_attachment_link or len(stat.normalized_line) >= 50
        suspected = ""
        if stat.normalized_line in {"首页", "政务公开", "政务服务", "互动交流", "网站地图", "打印本页", "关闭窗口"}:
            suspected = "navigation_or_control"
        elif re.search(r"ICP备|公网安备|主办单位|承办单位", stat.normalized_line):
            suspected = "footer"
        row = {
            "group_key": stat.group_key,
            "source": stat.source,
            "domain": stat.domain,
            "raw_line": stat.raw_line,
            "normalized_line": stat.normalized_line,
            "document_frequency": stat.document_frequency,
            "document_ratio": round(stat.document_frequency / group_total, 8),
            "character_length": len(stat.normalized_line),
            "example_doc_ids": " | ".join(stat.example_doc_ids),
            "example_titles": " | ".join(stat.example_titles),
            "is_markdown_table": stat.is_markdown_table,
            "is_heading": stat.is_heading,
            "is_title_match": stat.is_title_match,
            "is_attachment_link": stat.is_attachment_link,
            "is_long_line": len(stat.normalized_line) >= 50,
            "suspected_noise_type": suspected,
            "recommended_action": "protect" if protected else ("review_for_dedup" if stat.document_frequency > 1 else "keep"),
            "protected": protected,
            "risk_reason": "structured_or_long_content" if protected else "",
        }
        rows.append(row)
    rows.sort(key=lambda row: (row["group_key"], -int(row["document_frequency"]), row["normalized_line"]))
    fields = list(rows[0]) if rows else ["group_key", "source", "domain", "raw_line", "normalized_line", "document_frequency"]
    _write_csv(report_dir / "line_frequency_by_group.csv", rows, fields)
    protected_rows = [row for row in rows if row.get("protected")]
    long_rows = [row for row in rows if row.get("is_long_line") and int(row["document_frequency"]) > 1]
    template_rows = [row for row in rows if row.get("suspected_noise_type") or (not row.get("protected") and int(row["document_frequency"]) > 1)]
    _write_csv(report_dir / "protected_frequent_lines.csv", protected_rows[:top_lines], fields)
    _write_csv(report_dir / "long_frequent_lines.csv", long_rows[:top_lines], fields)
    _write_csv(report_dir / "suspected_template_lines.csv", template_rows[:top_lines], fields)
    return InspectionResult(summary, group_counts, group_metadata, rows, issues)
