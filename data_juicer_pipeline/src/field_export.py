"""Stream selected fields from a final corpus into a derived JSONL export."""

from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonl_io import iter_jsonl, write_jsonl_record
from paths import resolve_existing_run_paths, resolve_from_clean_gov


MISSING_POLICIES = ("error", "null", "skip-record")
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_MISSING = object()


@dataclass(frozen=True)
class FieldMapping:
    output_name: str
    source_path: str


def parse_field_spec(value: str) -> tuple[FieldMapping, ...]:
    """Parse `output=source.path,title,text` while preserving field order."""
    mappings: list[FieldMapping] = []
    output_names: set[str] = set()
    for raw_item in value.split(","):
        item = raw_item.strip()
        if not item:
            raise ValueError("--fields 中存在空字段")
        if "=" in item:
            output_name, source_path = (part.strip() for part in item.split("=", 1))
        else:
            output_name = source_path = item
        if not output_name or not source_path:
            raise ValueError(f"字段映射无效: {item!r}")
        if output_name in output_names:
            raise ValueError(f"输出字段重复: {output_name}")
        if any(not part for part in source_path.split(".")):
            raise ValueError(f"源字段路径无效: {source_path!r}")
        output_names.add(output_name)
        mappings.append(FieldMapping(output_name, source_path))
    if not mappings:
        raise ValueError("至少需要选择一个字段")
    return tuple(mappings)


def resolve_final_corpus(*, run_id: str | None, input_path: Path | None) -> Path:
    if bool(run_id) == bool(input_path):
        raise ValueError("必须且只能指定 --run-id 或 --input 中的一个")
    if input_path is not None:
        resolved = resolve_from_clean_gov(input_path)
        if not resolved.is_file():
            raise FileNotFoundError(f"输入 JSONL 不存在: {resolved}")
        return resolved

    assert run_id is not None
    directories = resolve_existing_run_paths(run_id)
    output_dir = directories["output"]
    preferred = output_dir / f"corpus_native_cleaned_{run_id}.jsonl"
    if preferred.is_file():
        return preferred
    candidates = sorted(
        path
        for path in output_dir.glob("corpus_native_cleaned_*.jsonl")
        if "_selected_fields" not in path.stem and path.parent.name != "exports"
    )
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise FileNotFoundError(f"运行 {run_id} 的 output 中没有正式语料 JSONL: {output_dir}")
    raise ValueError(
        f"运行 {run_id} 的 output 中有多个正式语料候选，请改用 --input 明确指定: "
        + "，".join(str(path) for path in candidates)
    )


def _nested_value(record: dict[str, Any], source_path: str) -> Any:
    current: Any = record
    for part in source_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return _MISSING
        current = current[part]
    return current


def _default_output(input_path: Path) -> Path:
    return input_path.parent / "exports" / f"{input_path.stem}_selected_fields.jsonl"


def _summary_path(output_path: Path) -> Path:
    return output_path.with_name(f"{output_path.stem}.summary.json")


def _safe_temp_path(output_path: Path) -> Path:
    token = uuid.uuid4().hex
    safe_name = _SAFE_NAME_RE.sub("_", output_path.name)
    return output_path.parent / f".{safe_name}.{token}.tmp"


def export_selected_fields(
    input_path: Path,
    output_path: Path | None,
    mappings: tuple[FieldMapping, ...],
    *,
    missing_policy: str = "error",
    force: bool = False,
    progress_every: int = 10_000,
) -> dict[str, Any]:
    if missing_policy not in MISSING_POLICIES:
        raise ValueError(f"未知缺失字段策略: {missing_policy}")
    if progress_every < 0:
        raise ValueError("progress_every 不能小于0")
    source = input_path.resolve()
    target = (output_path or _default_output(source)).resolve()
    report = _summary_path(target)
    if source == target:
        raise ValueError("输入和输出不能是同一个文件")
    if not source.is_file():
        raise FileNotFoundError(f"输入 JSONL 不存在: {source}")
    if not force:
        for path in (target, report):
            if path.exists():
                raise FileExistsError(f"输出已存在；如确认覆盖请传 --force: {path}")

    target.parent.mkdir(parents=True, exist_ok=True)
    temp = _safe_temp_path(target)
    input_count = 0
    output_count = 0
    skipped_count = 0
    missing_counts: Counter[str] = Counter()
    try:
        with temp.open("w", encoding="utf-8") as handle:
            for line_number, record in iter_jsonl(source):
                input_count += 1
                selected: dict[str, Any] = {}
                missing: list[FieldMapping] = []
                for mapping in mappings:
                    value = _nested_value(record, mapping.source_path)
                    if value is _MISSING:
                        missing.append(mapping)
                        missing_counts[mapping.source_path] += 1
                        if missing_policy == "null":
                            selected[mapping.output_name] = None
                    else:
                        selected[mapping.output_name] = value
                if missing:
                    if missing_policy == "error":
                        names = ", ".join(item.source_path for item in missing)
                        raise KeyError(f"第 {line_number} 条记录缺少字段: {names}")
                    if missing_policy == "skip-record":
                        skipped_count += 1
                        continue
                write_jsonl_record(handle, selected)
                output_count += 1
                if progress_every and input_count % progress_every == 0:
                    print(
                        f"[字段适配] 已读取 {input_count} 条，已输出 {output_count} 条，"
                        f"跳过 {skipped_count} 条",
                        flush=True,
                    )
        temp.replace(target)
    except Exception:
        temp.unlink(missing_ok=True)
        raise

    summary = {
        "status": "success",
        "input": str(source),
        "output": str(target),
        "summary": str(report),
        "field_mappings": [
            {"output_name": item.output_name, "source_path": item.source_path}
            for item in mappings
        ],
        "missing_policy": missing_policy,
        "input_document_count": input_count,
        "output_document_count": output_count,
        "skipped_document_count": skipped_count,
        "missing_field_counts": dict(missing_counts),
    }
    report.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
