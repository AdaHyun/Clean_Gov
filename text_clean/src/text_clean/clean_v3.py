"""Clean government JSONL text with rule-based web chrome removal.

The script keeps the previous v3 idea:
- input text usually comes from all_rebuilt.jsonl, optionally enriched by
  all_with_routing_meta.jsonl body_html + trafilatura;
- output records contain doc_id, title, url, text.

Important fix:
- the minimum text-length threshold is now a command-line option;
- the same threshold is applied before cleaning and again after all cleaning.

Default paths are relative to the target project layout, but every path can be
overridden for local experiments.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_MIN_TEXT_LENGTH = 75
DEFAULT_LONG_TEXT_LENGTH = 20000


def find_project_root(script_path: Path) -> Path:
    resolved = script_path.resolve()
    for parent in [resolved.parent, *resolved.parents]:
        if parent.name == "Clean_Gov":
            return parent.parent
    parents = list(resolved.parents)
    return parents[4] if len(parents) > 4 else resolved.parent


PROJECT_ROOT = find_project_root(Path(__file__))
TEXT_CLEAN_OUTPUT = PROJECT_ROOT / "Clean_Gov" / "text_clean" / "data" / "output"
DEFAULT_INPUT = TEXT_CLEAN_OUTPUT / "gov-table-clean" / "all_rebuilt.jsonl"
DEFAULT_ROUTING = TEXT_CLEAN_OUTPUT / "gov-routing" / "all_with_routing_meta.jsonl"
DEFAULT_OUTDIR = TEXT_CLEAN_OUTPUT / "gov-webStructure-clean"


HARD_LITERALS = [
    "网站支持IPv6",
    "网站支持ipv6",
    "长者助手",
    "长者版",
    "长者浏览模式",
    "无障碍",
    "无障碍浏览",
    "无障碍模式",
    "打印本页",
    "【打印】",
    "关闭窗口",
    "【关闭】",
    "&nbsp",
    "分享到：",
    "分享到:",
    "：分享",
    ":分享",
    "微信扫一扫",
    "扫一扫",
    "手机扫码查看",
    "扫码分享",
    "视力保护色：",
    "【字体:大 中 小】",
    "【字体：大 中 小】",
    "字号：[ 大 中 小 ]",
    "字号：[大 中 小]",
    "字号：大 中 小",
    "【大 中 小】",
    "附件下载",
    "查看附件",
    "文件下载",
    "刷新 重试 诊断",
    "code：",
    "code:",
    "vid:",
    "uuid:",
    "requestId:",
    "播放时间：",
    "提示信息",
    "字幕 倍速 清晰度",
    "字幕 音轨 清晰度",
    "音轨 倍速 正常",
    "视频下载：",
    "宣教视频下载：",
    "00:00 / 00:00",
    "确认 取消",
    "404 Not Found",
    "nginx",
    # Common mojibake variants seen in older crawler output.
    "缃戠珯鏀寔IPv6",
    "闀胯€呭姪鎵?",
    "闀胯€呯増",
    "鎵撳嵃鏈〉",
    "鍏抽棴绐楀彛",
    "鍒嗕韩鍒帮細",
    "寰俊鎵竴鎵?",
    "鎵嬫満鎵爜鏌ョ湅",
    "闄勪欢涓嬭浇",
    "鏂囦欢涓嬭浇",
]

NAV_TERMS = [
    "首页",
    "网站首页",
    "网站地图",
    "联系我们",
    "当前位置",
    "机构职能",
    "机构信息",
    "机构概况",
    "中心简介",
    "中心领导",
    "组织机构",
    "政务动态",
    "政务公开",
    "政务服务",
    "互动交流",
    "新闻中心",
    "工作动态",
    "通知公告",
    "政策法规",
    "政策解读",
    "专题专栏",
    "公共服务",
    "在线调查",
    "招生招聘",
    "招聘信息",
    "健康主题",
    "资源共享",
    "业务指导",
    "English",
    "Français",
    "Español",
    "Fran莽ais",
    "Espa帽ol",
    "English 中文",
    "棣栭〉",
    "缃戠珯棣栭〉",
    "缃戠珯鍦板浘",
    "鑱旂郴鎴戜滑",
    "褰撳墠浣嶇疆",
]

FOOTER_TERMS = [
    "版权所有",
    "版权与免责声明",
    "ICP备案",
    "公安备案",
    "公网安备",
    "政府网站标识码",
    "网站标识码",
    "主办单位",
    "承办单位",
    "技术支持",
    "官方微信",
    "官方微博",
    "网站导航",
    "直属单位",
    "网站声明",
    "相关链接",
    "相关附件",
    "相关文件",
    "政府网站",
    "部门网站",
    "下属单位",
    "网站信息",
    "粤ICP备20001927号-1",
    "新媒体矩阵",
    "广州健康通微信公众号",
    "广州健康通小程序",
    "@健康广州微博",
    "广州卫健委南方号",
    "广州卫健委微信公众号",
    "广州卫健委微信视频号",
    "广州地区互联网医院导引平台",
    "Copyright",
    "Reference numbers",
    "WHO Reference Number",
    "Number of pages",
    "Editors",
    "WHO Team",
    "国家部委",
    "省政府及省直部门",
    "中共广东省纪委",
    "纪检监察组",
    "联系电话",
    "联系地址",
    "寄信地址",
    "邮政编码",
    "邮编",
    "主办：",
    "承办：",
    "鐗堟潈鎵€鏈?",
    "ICP澶囨",
    "浜叕缃戝畨澶?",
    "浜琁CP澶?",
    "缃戠珯鏍囪瘑鐮?",
    "鑱旂郴鐢佃瘽",
    "鎶€鏈敮鎸?",
    "瀹樻柟寰俊",
]

PREV_NEXT_TERMS = ["上一篇", "下一篇", "上一页", "下一页", "涓婁竴绡囷細", "涓嬩竴绡囷細"]
ALL_TERMS = HARD_LITERALS + NAV_TERMS + FOOTER_TERMS + PREV_NEXT_TERMS

INLINE_PATTERNS = [
    re.compile(r"【?\s*字体\s*[:：]?\s*大\s*中\s*小\s*】?"),
    re.compile(r"字号\s*[:：]\s*\[?\s*大\s*中\s*小\s*\]?"),
    re.compile(r"\[\s*字体\s*[:：]?\s*大\s*中\s*小\s*\]"),
    re.compile(r"\[\s*智能咨询\s*\]\(\s*['\"]?智能咨询['\"]?\s*\)"),
    re.compile(r"\[\s*市民网页\s*\]\(\s*['\"]?市民网页['\"]?\s*\)"),
    re.compile(r"\[\s*!\[\]\([^)]+\)\s*\]\([^)]+\)"),
    re.compile(r"\[\s*(?:首\s*页|机\s*构|新\s*闻|信\s*息|服\s*务|互\s*动|专\s*题)\s*\]\([^)]*\)"),
    re.compile(r"#{1,6}\s*[:：]\s*分享"),
    re.compile(r"(?m)^\s*#{1,6}\s*$"),
    re.compile(r"\b搜\s*索\b"),
    re.compile(r"(?:信息来源|来源|文章来源)\s*[:：]\s*[^\n。；;]{0,80}"),
    re.compile(r"(?:责任编辑|责任编校|编辑|责编|审核|校对|签发)\s*[:：]\s*[^\n。；;]{0,40}"),
    re.compile(r"(?:时间|发布时间|发布日期)\s*[:：]\s*\d{4}[-年]\d{1,2}[-月]\d{1,2}(?:[日\s]\s*\d{1,2}:\d{2}(?::\d{2})?)?"),
    re.compile(r"https?://[^\s，。；；）)】]+"),
]

HEAD_CHROME_RE = re.compile(
    r"^(?:\s|[\u00a0>›/|·-]|"
    r"网站支持IPv6|网站支持ipv6|长者助手|长者版|长者浏览模式|无障碍(?:浏览|模式)?|"
    r"首页|网站首页|当前位置|机构职能|政务动态|政务公开|政务服务|互动交流|"
    r"新闻中心|工作动态|通知公告|政策法规|政策解读|专题专栏|要闻速递|"
    r"棣栭〉|缃戠珯棣栭〉|褰撳墠浣嶇疆)+"
)

META_BLOCK_RE = re.compile(
    r"^.{0,300}?(?:时间|发布时间|发布日期)\s*[:：]\s*"
    r"\d{4}[-年]\d{1,2}[-月]\d{1,2}"
    r"(?:[日\s]\s*\d{1,2}:\d{2}(?::\d{2})?)?"
    r".{0,160}?(?:分享到[:：]?|【?\s*字体\s*[:：]?\s*大\s*中\s*小\s*】?)",
    flags=re.S,
)

VIDEO_SHELL_RE = re.compile(
    r"(?:刷新\s+重试\s+诊断|code[:：]\s*vid:|uuid:|requestId:|播放时间[:：]|00:00\s*/\s*00:00)",
    flags=re.I,
)

TAIL_START_RE = re.compile(
    r"(?:版权所有|Copyright|Reference numbers|WHO Reference Number|Number of pages|Editors|WHO Team|"
    r"ICP备案|公安备案|公网安备|政府网站标识码|网站标识码|"
    r"主办单位|承办单位|主办[:：]|承办[:：]|技术支持|网站地图|联系我们|网站声明|"
    r"联系电话|联系地址|寄信地址|邮政编码|邮编|国家部委|省政府及省直部门|"
    r"政府网站|部门网站|下属单位|网站信息|粤ICP备20001927号-1|新媒体矩阵|"
    r"广州健康通微信公众号|广州健康通小程序|@健康广州微博|广州卫健委南方号|"
    r"广州卫健委微信公众号|广州卫健委微信视频号|广州地区互联网医院导引平台|"
    r"中共广东省纪委|纪检监察组|"
    r"上一篇[:：]|下一篇[:：]|相关链接|相关附件|相关文件|"
    r"鐗堟潈鎵€鏈?|ICP澶囨|浜叕缃戝畨澶?|缃戠珯鏍囪瘑鐮?|"
    r"鑱旂郴鐢佃瘽|鎶€鏈敮鎸?|瀹樻柟寰俊)"
)


def normalize_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\ufeff", "").replace("\u00a0", " ").replace("\u3000", " ")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def get_text(record: dict[str, Any]) -> str:
    text = record.get("text")
    if text is None:
        content = record.get("content")
        if isinstance(content, dict):
            text = content.get("body_text")
    return normalize_text(text)


def count_noise(text: str) -> tuple[int, int]:
    nav_or_hard = sum(1 for p in HARD_LITERALS + NAV_TERMS if p and p in text)
    footer = sum(1 for p in FOOTER_TERMS if p and p in text)
    if VIDEO_SHELL_RE.search(text):
        nav_or_hard += 3
    return nav_or_hard, footer


def remove_inline_noise(text: str) -> str:
    for literal in HARD_LITERALS:
        if literal:
            text = text.replace(literal, " ")
    for pattern in INLINE_PATTERNS:
        text = pattern.sub(" ", text)
    return normalize_text(text)


def looks_like_nav_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return True
    if re.fullmatch(r"\[\s*(?:智能咨询|市民网页|首\s*页|机\s*构|新\s*闻|信\s*息|服\s*务|互\s*动|专\s*题)\s*\]\([^)]*\)", s):
        return True
    if re.fullmatch(r"\[\s*!\[\]\([^)]+\)\s*\]\([^)]+\)", s):
        return True
    if re.fullmatch(r"(?:搜\s*索|#{1,6}\s*[:：]\s*分享)", s):
        return True
    if len(s) <= 4 and s in {"首页", "返回", "TOP"}:
        return True
    if VIDEO_SHELL_RE.search(s) and len(s) < 500:
        return True
    if s in {"关于我们", "404 Not Found", "nginx"}:
        return True

    soft_hits = sum(1 for p in NAV_TERMS + FOOTER_TERMS + PREV_NEXT_TERMS if p and p in s)
    if soft_hits and len(s) < 120:
        return True
    if soft_hits >= 5:
        return True
    if TAIL_START_RE.search(s) and len(s) < 220:
        return True
    return False


def truncate_head(text: str, max_check: int = 500) -> str:
    head = text[:max_check]
    meta_match = META_BLOCK_RE.search(head)
    if meta_match:
        return text[meta_match.end() :].strip()

    trimmed = HEAD_CHROME_RE.sub("", text, count=1).strip()
    if len(text) - len(trimmed) >= 10:
        return trimmed

    lines = text.splitlines()
    for i, line in enumerate(lines[:12]):
        if not looks_like_nav_line(line):
            return "\n".join(lines[i:]).strip()
    return text


def truncate_tail(text: str) -> str:
    matches = list(TAIL_START_RE.finditer(text))
    if not matches:
        return text
    best = len(text)
    for match in matches:
        idx = match.start()
        if idx > len(text) * 0.45 and idx < best:
            best = idx
    if best < len(text):
        nl = text.rfind("\n", 0, best)
        return text[:nl].strip() if nl > 0 else text[:best].strip()
    return text


def strip_noise_lines(text: str) -> str:
    kept = []
    for line in text.splitlines():
        line = remove_inline_noise(line)
        if looks_like_nav_line(line):
            continue
        kept.append(line.strip())
    return normalize_text("\n".join(kept))


def clean_text(text: str) -> tuple[str, Counter]:
    stats = Counter()
    text = normalize_text(text)

    old = text
    text = truncate_head(text)
    if text != old:
        stats["head_cut"] += 1

    old = text
    text = remove_inline_noise(text)
    if text != old:
        stats["inline_strip"] += 1

    old = text
    text = strip_noise_lines(text)
    if text != old:
        stats["line_strip"] += 1

    old = text
    text = truncate_tail(text)
    if text != old:
        stats["tail_cut"] += 1

    return normalize_text(text), stats


def traf(html: str) -> str | None:
    try:
        import trafilatura

        result = trafilatura.extract(
            html,
            favor_precision=True,
            include_comments=False,
            include_tables=False,
            include_links=False,
        )
        if result and len(result.strip()) >= 50:
            return normalize_text(result)
    except Exception:
        return None
    return None


def iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                yield line_no, None, f"invalid JSON: {exc}"
                continue
            if not isinstance(record, dict):
                yield line_no, None, "non-object JSON value"
                continue
            yield line_no, record, None


def load_routing(path: Path | None) -> dict[str, dict[str, Any]]:
    if not path or not path.exists():
        return {}
    routing_by_id = {}
    for _, record, error in iter_jsonl(path):
        if error or not record:
            continue
        doc_id = record.get("doc_id")
        if doc_id:
            routing_by_id[doc_id] = record
    return routing_by_id


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--routing", type=Path, default=DEFAULT_ROUTING)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTDIR)
    parser.add_argument("--min-text-length", type=int, default=DEFAULT_MIN_TEXT_LENGTH)
    parser.add_argument("--long-text-length", type=int, default=DEFAULT_LONG_TEXT_LENGTH)
    parser.add_argument("--no-trafilatura", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.min_text_length < 0:
        raise ValueError("--min-text-length must be >= 0")
    if not args.input.exists():
        raise FileNotFoundError(f"Input file does not exist: {args.input}")

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    corpus_output = output_dir / f"gov_corpus_clean_{run_timestamp}.jsonl"

    print("Loading...")
    routing_by_id = load_routing(args.routing)
    print(f"routing_meta: {len(routing_by_id)}")

    kept = []
    removed_short = []
    removed_empty = []
    bad_rows = []
    stats = Counter()

    for line_no, record, error in iter_jsonl(args.input):
        if error:
            bad_rows.append({"line_no": line_no, "error": error})
            stats["bad_rows"] += 1
            continue

        stats["total"] += 1
        assert record is not None
        doc_id = record.get("doc_id", "")
        original_text = get_text(record)
        original_len = len(original_text.strip())

        if original_len == 0:
            removed_empty.append(record)
            stats["removed_empty"] += 1
            continue
        if original_len < args.min_text_length:
            removed_short.append(add_remove_meta(record, "short_before_clean", original_len, line_no, args.min_text_length))
            stats["removed_short_before"] += 1
            continue

        cleaned_text = original_text
        routing_record = routing_by_id.get(doc_id)
        if routing_record:
            stats["matched"] += 1
            routing_meta = routing_record.get("routing_meta") or {}
            nav_noise_score = routing_meta.get("nav_noise_score", 0)
            body_html = ((routing_record.get("content") or {}).get("body_html")) or ""
            if not args.no_trafilatura and nav_noise_score > 0 and body_html:
                extracted = traf(body_html)
                if extracted:
                    cleaned_text = extracted
                    stats["traf_used"] += 1
                else:
                    stats["traf_fail"] += 1

        cleaned_text, clean_stats = clean_text(cleaned_text)
        stats.update(clean_stats)

        table_lines = [line for line in original_text.splitlines() if line.strip().startswith("|")]
        if table_lines:
            existing = {line.strip()[:30] for line in cleaned_text.splitlines() if line.strip().startswith("|")}
            new_tables = [line for line in table_lines if line.strip()[:30] not in existing]
            if new_tables:
                cleaned_text = normalize_text(cleaned_text + "\n\n" + "\n".join(new_tables))
                stats["table_restored"] += 1

        final_len = len(cleaned_text.strip())
        if final_len == 0:
            removed_empty.append(add_remove_meta(record, "empty_after_clean", final_len, line_no, args.min_text_length))
            stats["removed_empty_after"] += 1
            continue
        if final_len < args.min_text_length:
            removed_short.append(add_remove_meta(record, "short_after_clean", final_len, line_no, args.min_text_length))
            stats["removed_short_after"] += 1
            continue

        if final_len > args.long_text_length:
            stats["long_text"] += 1

        noise_count, footer_count = count_noise(cleaned_text)
        if noise_count == 0 and footer_count == 0:
            stats["clean"] += 1
        else:
            stats["residual"] += 1

        kept.append(
            {
                "doc_id": doc_id,
                "title": record.get("title", ""),
                "url": record.get("url", ""),
                "text": cleaned_text.strip(),
            }
        )

    qa_pages, kept_final = split_short_title_pages(kept, stats)
    stats["kept"] = len(kept_final)
    stats["removed_short"] = stats["removed_short_before"] + stats["removed_short_after"]

    write_jsonl(corpus_output, kept_final)
    write_jsonl(output_dir / "short_title_pages.jsonl", qa_pages)
    write_jsonl(output_dir / "removed_short.jsonl", removed_short)
    write_jsonl(output_dir / "removed_empty.jsonl", removed_empty)
    write_jsonl(output_dir / "bad_rows.jsonl", bad_rows)

    noise_hits = Counter()
    clean_n = 0
    for record in kept_final:
        hits = [term for term in ALL_TERMS if term and term in record["text"]]
        if not hits:
            clean_n += 1
        else:
            noise_hits.update(hits)
    url_residue = sum(1 for record in kept_final if re.search(r"https?://\S+", record["text"]))

    total = stats["total"]
    kept_count = stats["kept"]
    report = {key: int(value) for key, value in stats.items()}
    report.update(
        {
            "input": str(args.input.resolve()),
            "routing": str(args.routing.resolve()) if args.routing else "",
            "output_dir": str(output_dir),
            "corpus_output": str(corpus_output),
            "min_text_length": args.min_text_length,
            "retention": f"{kept_count / total * 100:.1f}%" if total else "0.0%",
            "clean_rate": f"{clean_n / kept_count * 100:.1f}%" if kept_count else "0.0%",
            "top_residual": dict(noise_hits.most_common(15)),
            "url_residue": url_residue,
        }
    )
    with (output_dir / "cleaning_report.json").open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)

    print("\n===== v3 cleaning done =====")
    print(
        f"input={total} empty_before={stats['removed_empty']} empty_after={stats['removed_empty_after']} "
        f"short_before={stats['removed_short_before']} short_after={stats['removed_short_after']} "
        f"kept={kept_count} qa={stats.get('qa_extracted', 0)}"
    )
    print(f"trafilatura: {stats.get('traf_used', 0)} used / {stats.get('traf_fail', 0)} failed")
    print(
        f"inline_strip={stats.get('inline_strip', 0)} line_strip={stats.get('line_strip', 0)} "
        f"head_cut={stats.get('head_cut', 0)} tail_cut={stats.get('tail_cut', 0)}"
    )
    print(f"clean={clean_n}/{kept_count} residual={stats['residual']} url_residue={url_residue}")
    print("top_residual=" + json.dumps(dict(noise_hits.most_common(8)), ensure_ascii=True))
    print(f"output_dir={output_dir}")
    print(f"corpus={corpus_output}")
    for filename in sorted(os.listdir(output_dir)):
        path = output_dir / filename
        if path.is_file():
            print(f"  {filename} ({path.stat().st_size:,}B)")

    return 0


def add_remove_meta(record: dict[str, Any], reason: str, text_len: int, line_no: int, threshold: int) -> dict[str, Any]:
    output = dict(record)
    output["_remove_meta"] = {
        "reason": reason,
        "line_no": line_no,
        "text_len": text_len,
        "min_text_length": threshold,
    }
    return output


def split_short_title_pages(records: list[dict[str, Any]], stats: Counter) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    video_hits = ["刷新 重试", "字幕 音轨", "字幕 倍速", "视频下载：", "00:00 / 00:00"]
    qa_pages = []
    kept = []
    for record in records:
        text = record["text"]
        is_video = any(hit in text for hit in video_hits)
        has_time = bool(re.search(r"(?:时间|发布时间|发布日期)\s*[:：]\s*\d{4}-\d{2}-\d{2}", text))
        is_short_title = len(text) < 200 and has_time and not is_video
        if is_short_title:
            qa_pages.append(record)
            stats["qa_extracted"] += 1
        else:
            kept.append(record)
    return qa_pages, kept


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())
