from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml
from bs4 import BeautifulSoup
from dateutil import parser as date_parser
from rapidfuzz import fuzz
from tqdm import tqdm
from w3lib.url import canonicalize_url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.io.jsonl_reader import iter_jsonl_files, read_jsonl
from src.io.jsonl_writer import ensure_dir, write_json, write_jsonl
from src.cleaning.noise_cleaner import detect_noise_hits, remove_noise_lines
from src.cleaning.text_normalizer import html_to_clean_text, normalize_chinese_web_text
from src.export.datatrove_exporter import to_datatrove_document
from src.privacy.sensitive_info_detector import detect_sensitive_info
from src.quality.quality_checker import evaluate_quality
from src.structure.article_structurer import build_content_elements, classify_element
from src.structure.asset_linker import mark_attachment_references, mark_image_roles
from src.structure.table_parser import parse_tables_from_html
from src.utils import (
    choose_article_html,
    clean_text_basic,
    domain_of,
    flatten_keys,
    get_path,
    html_to_text,
    is_missing,
    noise_hits,
    now_iso,
    resolve_existing_path,
    set_path,
    sha1_text,
    type_name,
    write_md,
)


STAGES = [
    "profile", "validation", "normalization", "extraction", "cleaning", "structure",
    "tables", "assets", "sensitive", "dedup", "quality", "datatrove",
]

STAGE_DIR_NAMES = {
    "profile": "00_profile",
    "validation": "01_validation",
    "normalization": "02_normalization",
    "extraction": "03_extraction",
    "cleaning": "04_text_cleaning",
    "structure": "05_structure",
    "tables": "06_tables",
    "assets": "07_assets",
    "sensitive": "08_sensitive",
    "dedup": "09_dedup",
    "quality": "10_quality",
    "datatrove": "11_datatrove",
}


def load_configs():
    cfg = {}
    for p in (ROOT / "configs").glob("*.yaml"):
        with p.open("r", encoding="utf-8") as f:
            cfg[p.stem] = yaml.safe_load(f) or {}
    return cfg


def read_records(path: Path):
    return list(read_jsonl(path)) if path.exists() else []


def find_previous(output_dir: Path, stage: str):
    mapping = {
        "normalization": output_dir / STAGE_DIR_NAMES["validation"] / "validated_records.jsonl",
        "extraction": output_dir / STAGE_DIR_NAMES["normalization"] / "normalized_records.jsonl",
        "cleaning": output_dir / STAGE_DIR_NAMES["extraction"] / "article_html_records.jsonl",
        "structure": output_dir / STAGE_DIR_NAMES["cleaning"] / "content_cleaned_records.jsonl",
        "tables": output_dir / STAGE_DIR_NAMES["structure"] / "structured_records.jsonl",
        "assets": output_dir / STAGE_DIR_NAMES["tables"] / "table_parsed_records.jsonl",
        "sensitive": output_dir / STAGE_DIR_NAMES["assets"] / "asset_linked_records.jsonl",
        "dedup": output_dir / STAGE_DIR_NAMES["sensitive"] / "sensitive_marked_records.jsonl",
        "quality": output_dir / STAGE_DIR_NAMES["dedup"] / "deduplicated_records.jsonl",
        "datatrove": output_dir / STAGE_DIR_NAMES["quality"] / "cleaned_articles.jsonl",
    }
    return mapping[stage]


def iter_all_raw(jsonl_dir: Path):
    for p in iter_jsonl_files(jsonl_dir):
        for rec in read_jsonl(p):
            yield p, rec


def stage_profile(jsonl_dir: Path, raw_html_dir: Path, output_dir: Path, cfg):
    out = output_dir / STAGE_DIR_NAMES["profile"]
    ensure_dir(out)
    files = sorted(iter_jsonl_files(jsonl_dir))
    raw_files = list(raw_html_dir.rglob("*")) if raw_html_dir.exists() else []
    raw_file_names = {p.name for p in raw_files if p.is_file()}
    inventory = {"jsonl_input_dir": str(jsonl_dir), "raw_html_dir": str(raw_html_dir), "files": [], "raw_html_file_count": len(raw_file_names)}
    field_counter = Counter()
    missing = defaultdict(Counter)
    types = defaultdict(Counter)
    values = defaultdict(Counter)
    source_rows, channel_rows, asset_rows, raw_rows, table_rows, dup_rows = [], [], [], [], [], []
    title_counter, hash_counter = Counter(), Counter()
    hash_records = defaultdict(list)
    empty_content_records = []
    content_noise = {"empty": 0, "too_short": 0, "noise_hits": Counter()}
    selector_candidates = defaultdict(Counter)
    total = 0

    for p in tqdm(files, desc="00 profile"):
        file_count = 0
        for rec in read_jsonl(p):
            file_count += 1
            total += 1
            keys = flatten_keys(rec)
            field_counter.update(keys)
            for k in keys:
                v = get_path(rec, k.replace("[]", ""), None) if "[]" not in k else None
                types[k].update([type_name(v)])
                if is_missing(v):
                    missing[k].update([p.name])
            for f in ["source.site_name", "source.site_domain", "source.channel_name", "organization.source_department", "classification.document_type", "classification.policy_category", "dates.publish_date"]:
                values[f].update([str(get_path(rec, f, ""))[:120]])
            site = get_path(rec, "source.site_name", "")
            domain = get_path(rec, "source.site_domain", domain_of(get_path(rec, "url", "")))
            channel = get_path(rec, "source.channel_name", "")
            source_rows.append({"file": p.name, "site": site, "domain": domain, "department": get_path(rec, "organization.source_department", ""), "document_type": get_path(rec, "classification.document_type", ""), "policy_category": get_path(rec, "classification.policy_category", "")})
            channel_rows.append({"file": p.name, "site": site, "channel": channel})
            title = get_path(rec, "title", "")
            title_counter.update([title])
            text = get_path(rec, "content.body_text", "") or ""
            if not text.strip():
                content_noise["empty"] += 1
                empty_content_records.append({
                    "doc_id": get_path(rec, "doc_id", ""),
                    "title": title,
                    "url": get_path(rec, "url", ""),
                    "input_file": str(p),
                    "line_no": get_path(rec, "_ingest.line_no", ""),
                    "site_name": site,
                    "channel_name": channel,
                    "reason": "content.body_text_empty_before_hash",
                })
            if len(text.strip()) < 80:
                content_noise["too_short"] += 1
            for hit in noise_hits(text):
                content_noise["noise_hits"].update([hit])
            normalized_text_for_hash = clean_text_basic(text)
            h = sha1_text(normalized_text_for_hash)
            if normalized_text_for_hash:
                hash_counter.update([h])
                hash_records[h].append({
                    "doc_id": get_path(rec, "doc_id", ""),
                    "title": title,
                    "url": get_path(rec, "url", ""),
                    "input_file": str(p),
                    "line_no": get_path(rec, "_ingest.line_no", ""),
                    "site_name": site,
                    "channel_name": channel,
                    "text_length": len(normalized_text_for_hash),
                })
            raw_path = get_path(rec, "crawl.raw_html_path", "")
            resolved = resolve_existing_path(raw_html_dir, ROOT.parents[3], raw_path)
            raw_rows.append({"doc_id": get_path(rec, "doc_id", ""), "raw_html_path": raw_path, "exists": bool(resolved), "resolved_path": resolved})
            body_html = get_path(rec, "content.body_html", "")
            html_for_table = body_html
            if not html_for_table and resolved:
                try:
                    html_for_table = Path(resolved).read_text(encoding="utf-8", errors="replace")
                except Exception:
                    html_for_table = ""
            if html_for_table:
                soup = BeautifulSoup(html_for_table, "lxml")
                table_rows.append({"doc_id": get_path(rec, "doc_id", ""), "table_count": len(soup.find_all("table")), "site": site})
                try:
                    _, sel, conf = choose_article_html(html_for_table, cfg.get("site_rules", {}).get(domain, {}).get("article_selectors", []))
                    selector_candidates[domain].update([sel])
                except Exception:
                    pass
            attachments = get_path(rec, "attachments", []) or []
            images = get_path(rec, "images", []) or []
            asset_rows.append({"doc_id": get_path(rec, "doc_id", ""), "attachment_count": len(attachments), "image_count": len(images), "attachment_exts": ",".join(sorted({str(a.get("file_type") or Path(str(a.get("local_path", ""))).suffix).lower() for a in attachments if isinstance(a, dict)}))})
        inventory["files"].append({"path": str(p), "name": p.name, "records": file_count, "bytes": p.stat().st_size})

    schema_profile = {"total_records": total, "field_frequency": field_counter, "field_types": {k: dict(v) for k, v in types.items()}}
    schema_diff = {"fields_not_in_all_records": {k: v for k, v in field_counter.items() if v < total}, "type_conflicts": {k: dict(v) for k, v in types.items() if len(v) > 1}}
    duplicate_candidates = [
        {
            "hash": h,
            "count": c,
            "basis": "sha1(clean_text_basic(content.body_text))",
            "is_empty_text_hash": False,
            "records": hash_records[h],
        }
        for h, c in hash_counter.most_common()
        if c > 1
    ][:1000]
    write_json(out / "input_inventory.json", inventory)
    write_json(out / "schema_profile.json", schema_profile)
    write_json(out / "schema_diff_report.json", schema_diff)
    write_json(out / "content_noise_profile.json", {**content_noise, "noise_hits": dict(content_noise["noise_hits"])})
    write_json(out / "site_selector_candidates.json", {k: dict(v.most_common(20)) for k, v in selector_candidates.items()})
    write_json(out / "duplicate_candidate_profile.json", duplicate_candidates)
    write_json(out / "empty_content_profile.json", {
        "hash_of_empty_string": sha1_text(""),
        "count": len(empty_content_records),
        "basis": "records whose content.body_text is empty or whitespace before duplicate hashing",
        "records": empty_content_records,
    })
    pd.DataFrame([{"field": k, "value": val, "count": cnt} for k, c in values.items() for val, cnt in c.most_common(100)]).to_excel(out / "field_value_profile.xlsx", index=False)
    pd.DataFrame([{"field": k, "missing_count": sum(c.values()), "missing_rate": sum(c.values()) / max(total, 1), "files": ";".join(c.keys())} for k, c in missing.items()]).to_excel(out / "missing_value_report.xlsx", index=False)
    pd.DataFrame(channel_rows).value_counts().reset_index(name="count").to_excel(out / "channel_distribution.xlsx", index=False)
    pd.DataFrame(source_rows).value_counts().reset_index(name="count").to_excel(out / "source_department_profile.xlsx", index=False)
    pd.DataFrame(asset_rows).to_excel(out / "attachment_image_profile.xlsx", index=False)
    pd.DataFrame(raw_rows).to_excel(out / "raw_html_linkage_report.xlsx", index=False)
    pd.DataFrame(table_rows).to_excel(out / "table_profile.xlsx", index=False)
    template_titles = [{"title": t, "count": c} for t, c in title_counter.most_common(50) if c > 1 or t in ["政府信息公开详情", "健康生活方式"]]
    write_md(out / "data_health_check_report.md", "数据体检报告", [
        ("总体规模", f"JSONL 文件数：{len(files)}；记录数：{total}；raw_html 文件数：{len(raw_file_names)}。"),
        ("字段差异", f"顶层/嵌套字段共 {len(field_counter)} 个，非全量字段 {len(schema_diff['fields_not_in_all_records'])} 个，类型冲突 {len(schema_diff['type_conflicts'])} 个。详见 schema_profile.json 和 schema_diff_report.json。"),
        ("主要问题", f"正文为空 {content_noise['empty']} 条，正文过短 {content_noise['too_short']} 条；导航噪声命中：{dict(content_noise['noise_hits'].most_common(20))}。模板/重复标题示例：{template_titles[:20]}。"),
        ("重复候选", "duplicate_candidate_profile.json 仅统计非空 content.body_text 的 SHA1 重复，并附带每组 doc_id/title/url；空正文单独写入 empty_content_profile.json，避免 SHA1 空串被误认为正文重复。"),
        ("raw_html 关联", f"可在 raw_html_linkage_report.xlsx 查看每条记录 raw_html_path 是否能回溯到真实文件。"),
        ("对同事提示", "请保持 doc_id/title/url/source/organization/classification/dates/content/attachments/images/crawl/raw 的稳定结构；raw_html_path 建议保存相对 Crawler_Gov 根目录的 data/raw_html 路径或可直接定位的 page.html。"),
    ])
    return {"records": total, "manual_review": 0, "outputs": [str(out)]}


def validate_record(rec):
    errors, warnings = [], []
    for field in ["doc_id", "title", "url"]:
        if not get_path(rec, field, ""):
            errors.append({"field": field, "error": "required_missing", "repair_hint": "parser 应补齐唯一 ID/标题/URL"})
    for field in ["source", "organization", "classification", "dates", "content", "crawl", "raw"]:
        if not isinstance(get_path(rec, field, None), dict):
            warnings.append({"field": field, "error": "not_dict", "repair_hint": "标准 schema 中该字段应为对象"})
    for field in ["attachments", "images"]:
        if not isinstance(get_path(rec, field, []), list):
            warnings.append({"field": field, "error": "not_list", "repair_hint": "标准 schema 中该字段应为数组"})
    text = get_path(rec, "content.body_text", "") or ""
    if len(text.strip()) < 30:
        warnings.append({"field": "content.body_text", "error": "too_short_or_empty", "repair_hint": "后续 extraction 尝试从 raw_html 回溯正文"})
    status = "valid"
    if warnings:
        status = "valid_with_warning"
    if errors:
        status = "invalid_repairable" if get_path(rec, "url", "") else "invalid_drop_candidate"
    return status, errors, warnings


def stage_validation(jsonl_dir, raw_html_dir, output_dir, cfg):
    out = output_dir / STAGE_DIR_NAMES["validation"]; ensure_dir(out)
    valid, invalid, err_rows = [], [], []
    summary = Counter()
    for _, rec in tqdm(iter_all_raw(jsonl_dir), desc="01 validation"):
        status, errors, warnings = validate_record(rec)
        rec.setdefault("pipeline", {})["validation_status"] = status
        rec["pipeline"]["validation_warnings"] = warnings
        rec["pipeline"]["validation_errors"] = errors
        summary.update([status])
        if status.startswith("invalid"):
            invalid.append(rec)
        else:
            valid.append(rec)
        for e in errors + warnings:
            err_rows.append({"doc_id": get_path(rec, "doc_id", ""), "status": status, **e})
    write_jsonl(out / "validated_records.jsonl", valid)
    write_jsonl(out / "invalid_records.jsonl", invalid)
    write_jsonl(out / "validation_errors.jsonl", err_rows)
    write_json(out / "validation_summary.json", dict(summary))
    write_md(out / "validation_report.md", "基础校验报告", [("结果", json.dumps(dict(summary), ensure_ascii=False, indent=2)), ("说明", "valid_with_warning 会继续进入后续层；invalid 记录保留在 invalid_records.jsonl，不静默丢弃。")])
    return {"records": len(valid) + len(invalid), "manual_review": len(invalid), "outputs": [str(out)]}


def parse_date(value):
    from src.normalization.date_normalizer import parse_date_value
    return parse_date_value(value)


def norm_asset(asset, idx, kind, raw_html_dir, project_root):
    if not isinstance(asset, dict):
        return {"asset_id": f"{kind}_{idx}", "raw_value": asset, "path_needs_fix": True}
    a = deepcopy(asset)
    local = str(a.get("local_path", ""))
    ext = (a.get("file_ext") or a.get("file_type") or Path(local).suffix.replace(".", "")).lower()
    a[f"{kind}_id"] = a.get(f"{kind}_id") or f"{kind}_{idx:03d}"
    a["canonical_url"] = canonicalize_url(a.get("url", ""), keep_blank_values=False) if a.get("url") else ""
    a["file_ext"] = ext
    a["file_type"] = ext.upper() if ext else ""
    resolved = resolve_existing_path(raw_html_dir, project_root, local)
    a["local_path_exists"] = bool(resolved)
    a["resolved_local_path"] = resolved
    a["download_status"] = a.get("download_status") or ("downloaded" if resolved else "missing")
    a["path_needs_fix"] = bool(local and not resolved)
    return a


def stage_normalization(jsonl_dir, raw_html_dir, output_dir, cfg):
    out = output_dir / STAGE_DIR_NAMES["normalization"]; ensure_dir(out)
    records = read_records(find_previous(output_dir, "normalization"))
    normalized, corrections = [], []
    vocab = {"document_type": Counter(), "policy_category": Counter(), "channel_name": Counter(), "site_name": Counter()}
    project_root = ROOT.parents[3]
    for rec in tqdm(records, desc="02 normalization"):
        r = deepcopy(rec)
        doc_id = get_path(r, "doc_id", "") or sha1_text(get_path(r, "url", ""))[:16]
        if doc_id != get_path(r, "doc_id", ""):
            corrections.append({"doc_id": doc_id, "field": "doc_id", "old": get_path(r, "doc_id", ""), "new": doc_id, "rule": "url_hash_fallback"})
            r["doc_id"] = doc_id
        url = get_path(r, "url", "")
        canon = canonicalize_url(url, keep_blank_values=False) if url else ""
        set_path(r, "url", url.strip() if isinstance(url, str) else url)
        set_path(r, "canonical_url", canon)
        domain = get_path(r, "source.site_domain", "") or domain_of(url)
        set_path(r, "source.site_domain", domain)
        set_path(r, "source.site_url", get_path(r, "source.site_url", "") or (f"https://{domain}/" if domain else ""))
        set_path(r, "dates.publish_date", parse_date(get_path(r, "dates.publish_date", "") or get_path(r, "raw.raw_date", "")))
        set_path(r, "dates.raw_date", get_path(r, "raw.raw_date", ""))
        set_path(r, "dates.issue_date", parse_date(get_path(r, "dates.issue_date", "")))
        set_path(r, "dates.crawl_date", parse_date(get_path(r, "dates.crawl_date", "")))
        set_path(r, "dates.date_source", "dates.publish_date" if get_path(r, "dates.publish_date", "") else "raw.raw_date")
        set_path(r, "dates.date_conflict", bool(get_path(r, "dates.publish_date", "") and get_path(r, "raw.raw_date", "") and parse_date(get_path(r, "raw.raw_date", "")) != get_path(r, "dates.publish_date", "")))
        source_department = get_path(r, "organization.source_department", "")
        set_path(r, "organization.issuing_department", source_department)
        set_path(r, "organization.content_source", get_path(r, "raw.raw_source", "") or source_department)
        set_path(r, "organization.institution_code", (domain.replace(".", "_") if domain else "unknown"))
        set_path(r, "source.standard_channel_name", cfg.get("channel_map", {}).get(get_path(r, "source.channel_name", ""), get_path(r, "source.channel_name", "")))
        doc_type = get_path(r, "classification.document_type", "") or "其他"
        pol_cat = get_path(r, "classification.policy_category", "") or "未分类"
        set_path(r, "classification.document_type", cfg.get("document_type_rules", {}).get(doc_type, doc_type))
        set_path(r, "classification.policy_category", cfg.get("policy_category_map", {}).get(pol_cat, pol_cat))
        set_path(r, "content.summary", get_path(r, "content.summary", "") or (clean_text_basic(get_path(r, "content.body_text", ""))[:200]))
        set_path(r, "raw.raw_summary", get_path(r, "raw.raw_summary", "") or get_path(r, "content.summary", ""))
        r["attachments"] = [norm_asset(a, i + 1, "attachment", raw_html_dir, project_root) for i, a in enumerate(get_path(r, "attachments", []) or [])]
        r["images"] = [norm_asset(a, i + 1, "image", raw_html_dir, project_root) for i, a in enumerate(get_path(r, "images", []) or [])]
        for k in vocab:
            if k in ["document_type", "policy_category"]:
                vocab[k].update([get_path(r, "classification." + k, "")])
            elif k == "channel_name":
                vocab[k].update([get_path(r, "source.channel_name", "")])
            else:
                vocab[k].update([get_path(r, "source.site_name", "")])
        normalized.append(r)
    write_jsonl(out / "normalized_records.jsonl", normalized)
    write_jsonl(out / "field_correction_log.jsonl", corrections)
    write_json(out / "unmapped_values_report.json", {})
    write_json(out / "controlled_vocabulary.json", {k: dict(v.most_common()) for k, v in vocab.items()})
    write_json(out / "standard_schema.json", cfg.get("standard_schema", {}))
    write_json(out / "normalization_summary.json", {"records": len(normalized), "corrections": len(corrections)})
    write_md(out / "normalization_report.md", "字段标准化报告", [("结果", f"标准化 {len(normalized)} 条；字段修正日志 {len(corrections)} 条。"), ("规则", "机构、栏目、日期、URL、附件和图片字段已归一化；未能判断的枚举值保留原值并进入 controlled_vocabulary。")])
    return {"records": len(normalized), "manual_review": 0, "outputs": [str(out)]}


def read_raw_html_for(rec, raw_html_dir):
    raw_path = get_path(rec, "crawl.raw_html_path", "")
    resolved = resolve_existing_path(raw_html_dir, ROOT.parents[3], raw_path)
    if resolved:
        try:
            return Path(resolved).read_text(encoding="utf-8", errors="replace"), resolved
        except Exception:
            return "", resolved
    return "", ""


def stage_extraction(jsonl_dir, raw_html_dir, output_dir, cfg):
    out = output_dir / STAGE_DIR_NAMES["extraction"]; ensure_dir(out)
    records = read_records(find_previous(output_dir, "extraction"))
    ok, fail, logs = [], [], []
    discovered = defaultdict(Counter)
    for rec in tqdm(records, desc="03 extraction"):
        r = deepcopy(rec)
        domain = get_path(r, "source.site_domain", "")
        selectors = cfg.get("site_rules", {}).get(domain, {}).get("article_selectors", [])
        html = get_path(r, "content.body_html", "")
        method, fallback, resolved = "content.body_html", False, ""
        if not html or len(html_to_text(html)) < 80 or len(noise_hits(html_to_text(html))) >= 2:
            raw_html, resolved = read_raw_html_for(r, raw_html_dir)
            if raw_html:
                html = raw_html
                method, fallback = "raw_html_path", True
        try:
            clean_html, sel, conf = choose_article_html(html, selectors) if html else ("", "", 0)
            text = html_to_text(clean_html)
            if len(text) < 30:
                raise ValueError("extracted_text_too_short")
            set_path(r, "content.clean_html", clean_html)
            set_path(r, "content.extraction_method", method)
            set_path(r, "content.selector_used", sel)
            set_path(r, "content.extraction_confidence", conf)
            set_path(r, "crawl.resolved_raw_html_path", resolved)
            ok.append(r)
            discovered[domain].update([sel])
            logs.append({"doc_id": get_path(r, "doc_id", ""), "method": method, "selector_used": sel, "confidence": conf, "fallback_used": fallback})
        except Exception as exc:
            r.setdefault("manual_review_reasons", []).append("extraction_failed")
            fail.append(r)
            logs.append({"doc_id": get_path(r, "doc_id", ""), "method": method, "failure_reason": str(exc), "fallback_used": fallback})
    write_jsonl(out / "article_html_records.jsonl", ok + fail)
    write_jsonl(out / "extraction_log.jsonl", logs)
    write_json(out / "site_selector_rules_discovered.json", {k: dict(v.most_common(20)) for k, v in discovered.items()})
    write_jsonl(out / "extraction_failure_records.jsonl", fail)
    write_md(out / "extraction_report.md", "正文区域定位报告", [("结果", f"成功/可继续：{len(ok)}；失败需复核：{len(fail)}。"), ("方法", "优先 content.body_html；正文过短或噪声过多时回溯 raw_html_path，再按配置 selector 与自动候选选择正文区域。")])
    return {"records": len(ok) + len(fail), "manual_review": len(fail), "outputs": [str(out)]}


def stage_cleaning(jsonl_dir, raw_html_dir, output_dir, cfg):
    out = output_dir / STAGE_DIR_NAMES["cleaning"]; ensure_dir(out)
    records = read_records(find_previous(output_dir, "cleaning"))
    cleaned, noise_logs, norm_logs = [], [], []
    residue = Counter()
    patterns = cfg.get("noise_patterns", {}).get("patterns", [])
    for rec in tqdm(records, desc="04 cleaning"):
        r = deepcopy(rec)
        raw_text = html_to_clean_text(get_path(r, "content.clean_html", "")) or get_path(r, "content.body_text", "")
        removal = remove_noise_lines(normalize_chinese_web_text(raw_text), patterns)
        text = normalize_chinese_web_text(removal.text)
        set_path(r, "content.clean_text", text)
        set_path(r, "content.raw_text_length", len(raw_text or ""))
        set_path(r, "content.clean_text_length", len(text))
        hits = detect_noise_hits(text)
        residue.update(hits)
        if removal.removed_lines:
            noise_logs.append({"doc_id": get_path(r, "doc_id", ""), "removed_lines": removal.removed_lines[:50], "removed_count": len(removal.removed_lines)})
        norm_logs.append({"doc_id": get_path(r, "doc_id", ""), "raw_length": len(raw_text or ""), "clean_length": len(text), "residual_noise": hits})
        cleaned.append(r)
    write_jsonl(out / "content_cleaned_records.jsonl", cleaned)
    write_jsonl(out / "noise_removal_log.jsonl", noise_logs)
    write_jsonl(out / "text_normalization_log.jsonl", norm_logs)
    write_json(out / "noise_residue_report.json", dict(residue.most_common()))
    write_md(out / "text_cleaning_report.md", "正文去噪与文本规范化报告", [("结果", f"清洗 {len(cleaned)} 条；删除噪声行日志 {len(noise_logs)} 条。"), ("原则", "只做模板噪声删除和格式修复，不改写原文语义。")])
    return {"records": len(cleaned), "manual_review": 0, "outputs": [str(out)]}


def element_type(line):
    return classify_element(line)


def stage_structure(jsonl_dir, raw_html_dir, output_dir, cfg):
    out = output_dir / STAGE_DIR_NAMES["structure"]; ensure_dir(out)
    records = read_records(find_previous(output_dir, "structure"))
    recs, elems, logs = [], [], []
    sig_report, attach_report = Counter(), []
    for rec in tqdm(records, desc="05 structure"):
        r = deepcopy(rec)
        elements, element_summary = build_content_elements(r)
        for e in elements:
            elems.append(e)
            sig_report.update([e["type"]])
            if e["type"] == "attachment_ref":
                attach_report.append(e)
        set_path(r, "content.elements", elements)
        logs.append({"doc_id": get_path(r, "doc_id", ""), **element_summary})
        recs.append(r)
    write_jsonl(out / "structured_records.jsonl", recs)
    write_jsonl(out / "content_elements.jsonl", elems)
    write_jsonl(out / "structure_rebuild_log.jsonl", logs)
    write_json(out / "signature_extraction_report.json", dict(sig_report))
    write_json(out / "attachment_ref_report.json", {"attachment_refs": len(attach_report)})
    write_md(out / "structure_report.md", "正文结构重建报告", [("结果", f"生成 content_elements {len(elems)} 个。"), ("说明", "当前结构识别为轻量规则版：标题/段落/条款/附件引用/签署日期等，复杂政策结构可继续扩展。")])
    return {"records": len(recs), "manual_review": 0, "outputs": [str(out)]}


def expand_table(table):
    rows = []
    grid = []
    spans = {}
    for r_idx, tr in enumerate(table.find_all("tr")):
        row = []
        c_idx = 0
        for cell in tr.find_all(["td", "th"]):
            while spans.get((r_idx, c_idx)):
                row.append(spans[(r_idx, c_idx)])
                c_idx += 1
            text = clean_text_basic(cell.get_text(" ", strip=True))
            rs, cs = int(cell.get("rowspan", 1) or 1), int(cell.get("colspan", 1) or 1)
            for rr in range(r_idx, r_idx + rs):
                for cc in range(c_idx, c_idx + cs):
                    spans[(rr, cc)] = text
            for _ in range(cs):
                row.append(text)
                c_idx += 1
        grid.append(row)
    if not grid:
        return [], []
    headers = grid[0]
    rows = grid[1:]
    return headers, rows


def stage_tables(jsonl_dir, raw_html_dir, output_dir, cfg):
    out = output_dir / STAGE_DIR_NAMES["tables"]; ensure_dir(out)
    records = read_records(find_previous(output_dir, "tables"))
    recs, tables, logs, fails = [], [], [], []
    for rec in tqdm(records, desc="06 tables"):
        r = deepcopy(rec)
        parsed, failed = parse_tables_from_html(get_path(r, "content.clean_html", "") or "", get_path(r, "doc_id", ""))
        doc_tables = parsed + failed
        tables.extend(parsed)
        fails.extend(failed)
        set_path(r, "content.tables", doc_tables)
        logs.append({"doc_id": get_path(r, "doc_id", ""), "table_count": len(doc_tables), "failure_count": sum(1 for t in doc_tables if t.get("failure_reason"))})
        recs.append(r)
    write_jsonl(out / "table_parsed_records.jsonl", recs)
    write_jsonl(out / "tables.jsonl", tables)
    write_jsonl(out / "table_parse_log.jsonl", logs)
    write_jsonl(out / "table_failure_records.jsonl", fails)
    write_md(out / "table_report.md", "表格解析报告", [("结果", f"解析表格 {len(tables)} 个；失败 {len(fails)} 个。"), ("策略", "轻量 BeautifulSoup 解析 rowspan/colspan；复杂失败表保留 raw_table_html 并进入后续复核。")])
    return {"records": len(recs), "manual_review": len(fails), "outputs": [str(out)]}


def stage_assets(jsonl_dir, raw_html_dir, output_dir, cfg):
    out = output_dir / STAGE_DIR_NAMES["assets"]; ensure_dir(out)
    records = read_records(find_previous(output_dir, "assets"))
    recs, a_rows, i_rows, logs = [], [], [], []
    for rec in tqdm(records, desc="07 assets"):
        r = deepcopy(rec)
        text = get_path(r, "content.clean_text", "") or ""
        attachments, matched = mark_attachment_references(get_path(r, "attachments", []) or [], text)
        images = mark_image_roles(get_path(r, "images", []) or [])
        r["attachments"] = attachments
        r["images"] = images
        for a in attachments:
            name = a.get("name") or Path(str(a.get("local_path", ""))).name
            a_rows.append({"doc_id": get_path(r, "doc_id", ""), "name": name, "exists": a.get("local_path_exists"), "referenced_in_text": a["referenced_in_text"], "file_ext": a.get("file_ext")})
        for img in images:
            url = img.get("url", "")
            i_rows.append({"doc_id": get_path(r, "doc_id", ""), "url": url, "exists": img.get("local_path_exists"), "image_role": img["image_role"]})
        logs.append({"doc_id": get_path(r, "doc_id", ""), "attachments": len(get_path(r, "attachments", []) or []), "matched_attachment_refs": matched, "images": len(get_path(r, "images", []) or [])})
        recs.append(r)
    write_jsonl(out / "asset_linked_records.jsonl", recs)
    write_jsonl(out / "attachment_status_report.jsonl", a_rows)
    write_jsonl(out / "image_status_report.jsonl", i_rows)
    write_jsonl(out / "asset_linking_log.jsonl", logs)
    write_md(out / "asset_report.md", "附件图片处理报告", [("结果", f"附件记录 {len(a_rows)} 条；图片记录 {len(i_rows)} 条。"), ("说明", "当前不强制解析 PDF/DOCX/Excel 内容，只检查路径、类型和正文引用关系。")])
    return {"records": len(recs), "manual_review": 0, "outputs": [str(out)]}


from src.privacy.sensitive_info_detector import SENSITIVE_RULES


def stage_sensitive(jsonl_dir, raw_html_dir, output_dir, cfg):
    out = output_dir / STAGE_DIR_NAMES["sensitive"]; ensure_dir(out)
    records = read_records(find_previous(output_dir, "sensitive"))
    recs, report, manual = [], [], []
    for rec in tqdm(records, desc="08 sensitive"):
        r = deepcopy(rec)
        text = get_path(r, "content.clean_text", "") or ""
        hits, risk, action = detect_sensitive_info(text)
        set_path(r, "privacy.sensitive_hits", hits[:100])
        set_path(r, "privacy.sensitive_risk_level", risk)
        set_path(r, "privacy.sensitive_action", action)
        row = {"doc_id": get_path(r, "doc_id", ""), "risk_level": risk, "action": action, "hit_count": len(hits), "types": sorted({h["type"] for h in hits})}
        report.append(row)
        if action == "manual_review":
            manual.append({**row, "review_reason": "high_risk_sensitive_info"})
        recs.append(r)
    write_jsonl(out / "sensitive_marked_records.jsonl", recs)
    write_jsonl(out / "sensitive_info_report.jsonl", report)
    write_jsonl(out / "sensitive_manual_review_list.jsonl", manual)
    write_md(out / "sensitive_report.md", "敏感信息识别报告", [("结果", f"高风险复核 {len(manual)} 条。"), ("原则", "公开政务信息默认标记不删除；身份证、患者个案、详细轨迹类进入高风险复核。")])
    return {"records": len(recs), "manual_review": len(manual), "outputs": [str(out)]}


def stage_dedup(jsonl_dir, raw_html_dir, output_dir, cfg):
    out = output_dir / STAGE_DIR_NAMES["dedup"]; ensure_dir(out)
    records = read_records(find_previous(output_dir, "dedup"))
    groups = defaultdict(list)
    for rec in records:
        key = sha1_text(clean_text_basic(get_path(rec, "content.clean_text", "")))
        if not key or len(get_path(rec, "content.clean_text", "")) < 50:
            key = "url:" + (get_path(rec, "canonical_url", "") or get_path(rec, "url", "") or get_path(rec, "doc_id", ""))
        groups[key].append(rec)
    deduped, group_rows, cand_rows, logs = [], [], [], []
    for idx, (key, items) in enumerate(groups.items(), 1):
        canonical = sorted(items, key=lambda r: (len(get_path(r, "content.clean_text", "") or ""), get_path(r, "dates.publish_date", "")), reverse=True)[0]
        gid = f"dup_{idx:06d}" if len(items) > 1 else ""
        for r in items:
            if len(items) > 1:
                set_path(r, "dedup.duplicate_group_id", gid)
                set_path(r, "dedup.canonical_doc_id", get_path(canonical, "doc_id", ""))
                set_path(r, "dedup.duplicate_urls", [get_path(x, "url", "") for x in items])
                cand_rows.append({"duplicate_group_id": gid, "doc_id": get_path(r, "doc_id", ""), "canonical_doc_id": get_path(canonical, "doc_id", ""), "method": "clean_text_hash"})
            else:
                set_path(r, "dedup.canonical_doc_id", get_path(r, "doc_id", ""))
        deduped.append(canonical)
        if len(items) > 1:
            group_rows.append({"duplicate_group_id": gid, "canonical_doc_id": get_path(canonical, "doc_id", ""), "size": len(items), "urls": [get_path(x, "url", "") for x in items]})
            logs.append({"duplicate_group_id": gid, "method": "clean_text_hash", "size": len(items)})
    write_jsonl(out / "deduplicated_records.jsonl", deduped)
    write_jsonl(out / "duplicate_groups.jsonl", group_rows)
    write_jsonl(out / "duplicate_candidates.jsonl", cand_rows)
    write_jsonl(out / "dedup_log.jsonl", logs)
    write_md(out / "dedup_report.md", "去重报告", [("结果", f"输入 {len(records)} 条，输出 canonical {len(deduped)} 条，重复组 {len(group_rows)} 个。"), ("与 DataTrove 关系", "本层做工程级精确去重和同文异址合并；DataTrove 后续仍可进行更大规模近重复过滤。")])
    return {"records": len(deduped), "manual_review": 0, "outputs": [str(out)]}


def quality_for(rec):
    return evaluate_quality(rec)


def stage_quality(jsonl_dir, raw_html_dir, output_dir, cfg):
    out = output_dir / STAGE_DIR_NAMES["quality"]; ensure_dir(out)
    records = read_records(find_previous(output_dir, "quality"))
    cleaned, manual, scores = [], [], []
    site_rows = []
    for rec in tqdm(records, desc="10 quality"):
        r = deepcopy(rec)
        label, reasons = quality_for(r)
        set_path(r, "quality.quality_label", label)
        set_path(r, "quality.review_reasons", reasons)
        cleaned.append(r)
        score = max(0, 100 - len(reasons) * 15)
        row = {"doc_id": get_path(r, "doc_id", ""), "quality_label": label, "quality_score": score, "reasons": reasons, "site": get_path(r, "source.site_name", "")}
        scores.append(row)
        site_rows.append({"site": row["site"], "label": label})
        if label in ["needs_review", "drop_candidate"] or reasons:
            manual.append({"doc_id": row["doc_id"], "url": get_path(r, "url", ""), "title": get_path(r, "title", ""), "quality_label": label, "review_reasons": reasons, "suggested_action": "人工确认正文/日期/敏感信息/重复状态"})
    write_jsonl(out / "cleaned_articles.jsonl", cleaned)
    write_jsonl(out / "manual_review_list.jsonl", manual)
    write_jsonl(out / "quality_scores.jsonl", scores)
    pd.DataFrame(site_rows).value_counts().reset_index(name="count").to_excel(out / "site_quality_summary.xlsx", index=False)
    final = {"records": len(cleaned), "manual_review": len(manual), "label_distribution": dict(Counter(x["quality_label"] for x in scores))}
    write_json(out / "final_quality_report.json", final)
    write_md(out / "quality_report.md", "质量评估与人工复核报告", [("结果", json.dumps(final, ensure_ascii=False, indent=2)), ("复核", "manual_review_list.jsonl 汇总所有不确定、残留噪声、敏感、高风险和疑似截断记录。")])
    return {"records": len(cleaned), "manual_review": len(manual), "outputs": [str(out)]}


def stage_datatrove(jsonl_dir, raw_html_dir, output_dir, cfg):
    out = output_dir / STAGE_DIR_NAMES["datatrove"]; ensure_dir(out)
    records = read_records(find_previous(output_dir, "datatrove"))
    docs = []
    for rec in tqdm(records, desc="11 datatrove"):
        doc = to_datatrove_document(rec)
        if doc:
            docs.append(doc)
    write_jsonl(out / "datatrove_documents.jsonl", docs)
    write_json(out / "datatrove_export_summary.json", {"records": len(records), "exported": len(docs), "schema": "id/text/metadata"})
    write_md(out / "datatrove_export_report.md", "DataTrove 导出报告", [("结果", f"导出 {len(docs)} 条 id/text/metadata 记录。"), ("拼接", "该 JSONL 可与同事同 schema 文件直接按行拼接。")])
    return {"records": len(docs), "manual_review": 0, "outputs": [str(out)]}


STAGE_FUNCS = {
    "profile": stage_profile,
    "validation": stage_validation,
    "normalization": stage_normalization,
    "extraction": stage_extraction,
    "cleaning": stage_cleaning,
    "structure": stage_structure,
    "tables": stage_tables,
    "assets": stage_assets,
    "sensitive": stage_sensitive,
    "dedup": stage_dedup,
    "quality": stage_quality,
    "datatrove": stage_datatrove,
}


def write_top_reports(output_dir: Path, manifest):
    profile = output_dir / STAGE_DIR_NAMES["profile"] / "data_health_check_report.md"
    quality = output_dir / STAGE_DIR_NAMES["quality"] / "final_quality_report.json"
    profile_text = profile.read_text(encoding="utf-8") if profile.exists() else "尚未运行 profile。"
    quality_obj = json.loads(quality.read_text(encoding="utf-8")) if quality.exists() else {}
    if not (ROOT / "README.md").exists():
        write_md(ROOT / "README.md", "中文政府/公卫网页 JSONL 清洗系统", [
            ("项目目标", "把原始 parsed JSONL 和 raw_html 清洗为稳定 clean article，并导出可直接喂给 DataTrove 的 id/text/metadata JSONL。"),
            ("安装依赖", "`pip install -r requirements.txt`"),
            ("正式 clean pipeline", "`python pipeline_main.py`"),
            ("输出", "每层都有独立目录、主结果 JSONL、日志、summary/report。"),
        ])
    write_md(ROOT / "technical_report.md", "技术报告", [
        ("数据体检结果", profile_text),
        ("为什么不能直接喂给 DataTrove", "原始 JSONL 存在导航噪声、正文过短、raw_html_path 不统一、字段语义差异、附件图片状态不一、WHO 等扩展字段差异；直接喂入会污染语料并影响去重。"),
        ("Pipeline 设计", "00 体检、01 校验、02 标准化、03 正文定位、04 去噪、05 结构、06 表格、07 资产、08 敏感、09 去重、10 质量、11 DataTrove 导出。每层输出可作为下一层输入。"),
        ("策略说明", "字段标准化保留原始字段并补充 canonical 字段；正文定位优先 body_html 后回溯 raw_html；去噪只删模板噪声和修格式；去重先做精确 hash 同文异址合并；质量层统一人工复核原因。"),
        ("当前限制", "复杂表格、PDF/DOCX/Excel 深度解析、跨语种 WHO 内容抽取、近重复大规模 MinHash/OCR 作为后续扩展；当前版本优先端到端稳定运行。"),
        ("运行摘要", json.dumps(manifest, ensure_ascii=False, indent=2)),
    ])
    write_md(ROOT / "colleague_handoff_report.md", "同事交接报告", [
        ("字段差异", "请查看 00_profile/schema_diff_report.json、field_value_profile.xlsx、missing_value_report.xlsx。WHO 文件存在 language/geo/api/who_metadata 等额外字段，医保/体育文件 raw_html_path 可能指向子目录 page.html。"),
        ("必须字段", "doc_id、title、url、source.site_name、source.site_domain、source.channel_name、dates.publish_date、content.body_text 或 content.body_html、crawl.raw_html_path。"),
        ("可空字段", "attachments/images 可为空数组；issue_date、topic_tags、joint_departments、summary 可为空。"),
        ("语义约定", "source_department 是 parser 原始来源/部门；issuing_department 是发文司局；content_source 是转载来源；issuing_authority 是正式发文主体。"),
        ("枚举和资产", "document_type/policy_category 以 controlled_vocabulary.json 为准；附件需包含 name/url/local_path/file_type，图片需包含 url/local_path/download_status。"),
        ("raw_html 与正文", "raw_html_path 最好保存相对 Crawler_Gov 根目录的 `data/raw_html/...`；body_html 尽量保存正文容器，若只能保存完整 body，清洗层会用 site_rules/自动 selector 处理。"),
        ("拼接标准", "最终 clean 文件保留标准 schema；DataTrove 输出必须为 `id/text/metadata`，可直接按行拼接两个同事的 datatrove_documents.jsonl。"),
        ("风险点", "不要静默丢弃短正文、日期冲突、附件缺失、敏感信息和正文定位失败记录；它们应进入 manual_review_list.jsonl。"),
    ])
    write_json(output_dir / "pipeline_summary.json", manifest)
    copies = [
        (output_dir / STAGE_DIR_NAMES["quality"] / "cleaned_articles.jsonl", output_dir / "cleaned_articles.jsonl"),
        (output_dir / STAGE_DIR_NAMES["quality"] / "manual_review_list.jsonl", output_dir / "manual_review_list.jsonl"),
        (output_dir / STAGE_DIR_NAMES["datatrove"] / "datatrove_documents.jsonl", output_dir / "datatrove_documents.jsonl"),
        (ROOT / "README.md", output_dir / "README.md"),
        (ROOT / "technical_report.md", output_dir / "technical_report.md"),
        (ROOT / "colleague_handoff_report.md", output_dir / "colleague_handoff_report.md"),
    ]
    for src, dst in copies:
        if src.exists():
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


def main():
    default_jsonl = ROOT.parents[1] / "Crawler_Gov" / "data" / "output"
    default_raw = ROOT.parents[1] / "Crawler_Gov" / "data" / "raw_html"
    default_out = ROOT.parents[0] / "data" / "bodyClean"
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl-dir", default=str(default_jsonl))
    ap.add_argument("--raw-html-dir", default=str(default_raw))
    ap.add_argument("--output-dir", default=str(default_out))
    ap.add_argument("--stage", choices=STAGES)
    ap.add_argument("--run-all", action="store_true")
    args = ap.parse_args()
    jsonl_dir, raw_html_dir, output_dir = Path(args.jsonl_dir), Path(args.raw_html_dir), Path(args.output_dir)
    ensure_dir(output_dir)
    cfg = load_configs()
    stages = STAGES if args.run_all or not args.stage else [args.stage]
    manifest = {"started_at": now_iso(), "jsonl_dir": str(jsonl_dir), "raw_html_dir": str(raw_html_dir), "output_dir": str(output_dir), "rule_version": "0.1.0", "stages": {}}
    for stage in stages:
        started = datetime.now()
        result = STAGE_FUNCS[stage](jsonl_dir, raw_html_dir, output_dir, cfg)
        result["elapsed_seconds"] = round((datetime.now() - started).total_seconds(), 3)
        manifest["stages"][stage] = result
        if result.get("outputs"):
            write_json(Path(result["outputs"][0]) / "stage_summary.json", result)
        write_json(output_dir / "run_manifest.json", manifest)
    manifest["finished_at"] = now_iso()
    write_json(output_dir / "run_manifest.json", manifest)
    write_top_reports(output_dir, manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
