"""Before/after comparison with duplicate-safe doc_id alignment."""

from __future__ import annotations

import csv
import json
import random
import re
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonl_io import iter_jsonl, write_jsonl_record
from protected_blocks import markdown_table_blocks
from source_grouping import group_for


ATTACHMENT_RE = re.compile(r"\[[^\]]+\]\([^)]*\.(?:pdf|docx?|xlsx?|pptx?|rar|zip)(?:\?[^)]*)?\)", re.I)


@dataclass
class ComparisonResult:
    summary: dict[str, Any]
    details: list[dict[str, Any]]


def _line_count(text: str) -> int:
    return text.count("\n") + 1 if text else 0


def _removed_lines(before: str, after: str) -> list[str]:
    remaining = Counter(after.split("\n"))
    removed: list[str] = []
    for line in before.split("\n"):
        if remaining[line] > 0:
            remaining[line] -= 1
        elif line.strip():
            removed.append(line)
    return removed


def load_audits(path: Path | None) -> dict[int, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    result: dict[int, dict[str, Any]] = {}
    for _, row in iter_jsonl(path):
        result[int(row["ordinal"])] = row
    return result


def _build_offset_index(path: Path) -> tuple[dict[str, deque[tuple[int, int]]], Counter[str], int]:
    """Index only doc_id and byte offsets, never complete documents."""
    index: dict[str, deque[tuple[int, int]]] = defaultdict(deque)
    counts: Counter[str] = Counter()
    total = 0
    with path.open("rb") as handle:
        line_number = 0
        while True:
            offset = handle.tell()
            raw = handle.readline()
            if not raw:
                break
            line_number += 1
            record = json.loads(raw.decode("utf-8-sig"))
            if not isinstance(record, dict):
                raise ValueError(f"{path} 第 {line_number} 行不是 JSON 对象")
            doc_id = str(record.get("doc_id") or "")
            index[doc_id].append((line_number, offset))
            counts[doc_id] += 1
            total += 1
    return index, counts, total


def _read_at(handle, offset: int) -> dict[str, Any]:
    handle.seek(offset)
    value = json.loads(handle.readline().decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("偏移位置不是 JSON 对象")
    return value


def compare_files(before_path: Path, after_path: Path, report_dir: Path, *, audit_path: Path | None = None, sample_count: int = 50) -> ComparisonResult:
    report_dir.mkdir(parents=True, exist_ok=True)
    audits = load_audits(audit_path)
    details: list[dict[str, Any]] = []
    summary: Counter[str] = Counter()
    before_ids: Counter[str] = Counter()
    after_ids: Counter[str] = Counter()
    source_chars: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    domain_chars: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    group_chars: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    rule_counts: Counter[str] = Counter()
    removed_line_frequency: Counter[str] = Counter()

    after_index, after_ids, after_total = _build_offset_index(after_path)
    summary["after_document_count"] = after_total
    with after_path.open("rb") as after_handle:
      for ordinal, (_, before) in enumerate(iter_jsonl(before_path), start=1):
        summary["before_document_count"] += 1
        before_id = str(before.get("doc_id") or "")
        before_ids[before_id] += 1
        candidates = after_index.get(before_id)
        if not candidates:
            summary["missing_document_count"] += 1
            continue
        after_line_number, after_offset = candidates.popleft()
        after = _read_at(after_handle, after_offset)
        summary["aligned_document_count"] += 1
        after_id = str(after.get("doc_id") or "")
        before_text = before.get("text") if isinstance(before.get("text"), str) else ""
        after_text = after.get("text") if isinstance(after.get("text"), str) else ""
        before_chars, after_chars = len(before_text), len(after_text)
        removed_chars = max(0, before_chars - after_chars)
        ratio = removed_chars / before_chars if before_chars else 0.0
        before_lines, after_lines = _line_count(before_text), _line_count(after_text)
        removed_lines = _removed_lines(before_text, after_text)
        for line in set(removed_lines):
            removed_line_frequency[line] += 1
        summary["original_total_characters"] += before_chars
        summary["cleaned_total_characters"] += after_chars
        summary["removed_total_characters"] += removed_chars
        summary["original_total_lines"] += before_lines
        summary["cleaned_total_lines"] += after_lines
        summary["removed_total_lines"] += max(0, before_lines - after_lines)
        changed = before_text != after_text
        summary["changed_document_count" if changed else "unchanged_document_count"] += 1
        summary["empty_after_clean_count"] += int(not after_text.strip())
        for threshold in (10, 30, 50, 80):
            summary[f"removal_over_{threshold}_percent_count"] += int(ratio > threshold / 100)
        summary["doc_id_changed_count"] += int(before.get("doc_id") != after.get("doc_id"))
        summary["title_changed_count"] += int(before.get("title") != after.get("title"))
        summary["url_changed_count"] += int(before.get("url") != after.get("url"))
        metadata_before = {key: value for key, value in before.items() if key != "text"}
        metadata_after = {key: value for key, value in after.items() if key != "text"}
        metadata_changed = metadata_before != metadata_after
        summary["metadata_changed_count"] += int(metadata_changed)
        title = str(before.get("title") or "")
        title_removed = bool(title and title in before_text and title not in after_text)
        table_changed = markdown_table_blocks(before_text) != markdown_table_blocks(after_text)
        links_before, links_after = ATTACHMENT_RE.findall(before_text), ATTACHMENT_RE.findall(after_text)
        attachment_removed = bool(Counter(links_before) - Counter(links_after))
        summary["title_removed_from_text_count"] += int(title_removed)
        summary["markdown_table_changed_count"] += int(table_changed)
        summary["attachment_link_removed_count"] += int(attachment_removed)
        audit = audits.get(ordinal, {})
        matched_rules = list(audit.get("matched_rule_ids", []))
        for rule_id in matched_rules:
            rule_counts[str(rule_id)] += 1
        risk_flags = set(audit.get("risk_flags", []))
        if ratio > 0.5:
            risk_flags.add("high_removal_risk")
        if ratio > 0.8:
            risk_flags.add("very_high_removal_risk")
        if not after_text.strip():
            risk_flags.add("empty_after_clean")
        if title_removed:
            risk_flags.add("title_removed_from_text")
        if metadata_changed:
            risk_flags.add("metadata_changed")
        if table_changed:
            risk_flags.add("table_content_changed")
        if attachment_removed:
            risk_flags.add("attachment_link_removed")
        if before_lines > 3 and after_lines <= 1:
            risk_flags.add("suspicious_line_collapse")
        group = group_for(before)
        source_key = group.source or "(missing)"
        source_chars[source_key][0] += before_chars
        source_chars[source_key][1] += after_chars
        domain_chars[group.domain][0] += before_chars
        domain_chars[group.domain][1] += after_chars
        group_chars[group.key][0] += before_chars
        group_chars[group.key][1] += after_chars
        details.append({
            "ordinal": ordinal,
            "after_line_number": after_line_number,
            "doc_id": before_id,
            "title": before.get("title", ""),
            "url": before.get("url", ""),
            "source": group.source,
            "domain": group.domain,
            "before_char_count": before_chars,
            "after_char_count": after_chars,
            "removed_char_count": removed_chars,
            "removed_ratio": round(ratio, 8),
            "before_line_count": before_lines,
            "after_line_count": after_lines,
            "removed_line_count": max(0, before_lines - after_lines),
            "removed_lines": removed_lines,
            "matched_rule_ids": matched_rules,
            "risk_flags": sorted(risk_flags),
            "data_juicer_group": audit.get("group_key", group.key),
        })

    summary["new_document_count"] = after_total - summary["aligned_document_count"]

    total_before_chars = summary["original_total_characters"]
    result_summary: dict[str, Any] = dict(summary)
    result_summary["total_removal_ratio"] = summary["removed_total_characters"] / total_before_chars if total_before_chars else 0.0
    result_summary["duplicate_doc_id_count_before"] = sum(1 for count in before_ids.values() if count > 1)
    result_summary["duplicate_doc_id_count_after"] = sum(1 for count in after_ids.values() if count > 1)
    result_summary["rule_hit_counts"] = dict(rule_counts)
    result_summary["removal_ratio_by_source"] = {key: (old - new) / old if old else 0.0 for key, (old, new) in source_chars.items()}
    result_summary["removal_ratio_by_domain"] = {key: (old - new) / old if old else 0.0 for key, (old, new) in domain_chars.items()}
    result_summary["removal_ratio_by_data_juicer_group"] = {key: (old - new) / old if old else 0.0 for key, (old, new) in group_chars.items()}
    (report_dir / "compare_summary.json").write_text(json.dumps(result_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    detail_fields = ["ordinal", "after_line_number", "doc_id", "title", "url", "source", "domain", "before_char_count", "after_char_count", "removed_char_count", "removed_ratio", "before_line_count", "after_line_count", "removed_line_count", "removed_lines", "matched_rule_ids", "risk_flags", "data_juicer_group"]
    with (report_dir / "document_details.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=detail_fields)
        writer.writeheader()
        for row in details:
            serial = dict(row)
            for key in ("removed_lines", "matched_rule_ids", "risk_flags"):
                serial[key] = json.dumps(serial[key], ensure_ascii=False)
            writer.writerow(serial)
    with (report_dir / "removed_line_frequency.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["removed_line", "document_frequency", "character_length"])
        writer.writeheader()
        for line, count in removed_line_frequency.most_common():
            writer.writerow({"removed_line": line, "document_frequency": count, "character_length": len(line)})
    long_removed = [{"removed_line": line, "document_frequency": count, "character_length": len(line)} for line, count in removed_line_frequency.most_common() if len(line.strip()) > 50]
    with (report_dir / "removed_long_lines.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["removed_line", "document_frequency", "character_length"])
        writer.writeheader(); writer.writerows(long_removed)
    _write_samples(before_path, after_path, details, report_dir, sample_count)
    return ComparisonResult(result_summary, details)


def _write_samples(before_path: Path, after_path: Path, details: list[dict[str, Any]], report_dir: Path, sample_count: int) -> None:
    rng = random.Random(20260721)
    changed_indices = [i for i, row in enumerate(details) if row["removed_char_count"] or row["before_line_count"] != row["after_line_count"]]
    changed_set = set(changed_indices)
    unchanged_indices = [i for i in range(len(details)) if i not in changed_set]
    high = sorted(range(len(details)), key=lambda i: details[i]["removed_ratio"], reverse=True)[:50]
    changed_sample = sorted(set(high + rng.sample(changed_indices, min(sample_count, len(changed_indices)))))
    unchanged_sample = rng.sample(unchanged_indices, min(20, len(unchanged_indices)))
    risky = [i for i, row in enumerate(details) if row["risk_flags"]]
    selections = {
        "changed_samples.jsonl": {int(details[i]["ordinal"]) - 1 for i in changed_sample},
        "unchanged_samples.jsonl": {int(details[i]["ordinal"]) - 1 for i in unchanged_sample},
        "high_risk_samples.jsonl": {int(details[i]["ordinal"]) - 1 for i in risky},
    }
    detail_by_index = {int(row["ordinal"]) - 1: row for row in details}
    handles = {name: (report_dir / name).open("w", encoding="utf-8") for name in selections}
    try:
        after_index, _, _ = _build_offset_index(after_path)
        with after_path.open("rb") as after_handle:
          for index, (_, before) in enumerate(iter_jsonl(before_path)):
            candidates = after_index.get(str(before.get("doc_id") or ""))
            if not candidates:
                continue
            _, offset = candidates.popleft()
            after = _read_at(after_handle, offset)
            for name, selected in selections.items():
                if index in selected:
                    write_jsonl_record(handles[name], {"before": before, "after": after, "comparison": detail_by_index[index]})
    finally:
        for handle in handles.values():
            handle.close()
