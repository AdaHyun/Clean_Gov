
import argparse
import json
import re
import zipfile
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from xml.sax.saxutils import escape


# -------------------------
# 路径处理
# -------------------------

def get_base_dir() -> Path:
    """根据脚本位置定位 PublicHealth_Gov 项目根目录。"""
    return Path(__file__).resolve().parents[3]


BASE_DIR = get_base_dir()
TEXT_CLEAN_DIR = BASE_DIR / "Clean_Gov" / "text_clean"
DEFAULT_INPUT_DIR = TEXT_CLEAN_DIR / "data" / "output" / "gov-webStructure-clean"
DEFAULT_LOGS_DIR = TEXT_CLEAN_DIR / "data" / "logs"


def get_default_input() -> Path:
    candidates = sorted(
        DEFAULT_INPUT_DIR.glob("gov_corpus_clean_????????_??????.jsonl"),
        key=lambda path: path.stat().st_mtime,
    )
    if candidates:
        return candidates[-1]
    return DEFAULT_INPUT_DIR / "gov_corpus_clean.jsonl"


# -------------------------
# 机构和栏目映射
# -------------------------

ORG_BY_DOMAIN = {
    "nhc.gov.cn": "国家卫生健康委员会",
    "ndcpa.gov.cn": "国家疾病预防控制局",
    "chinacdc.cn": "中国疾病预防控制中心",
    "ncncd.chinacdc.cn": "中国疾控中心慢病中心",
    "nhsa.gov.cn": "国家医疗保障局",
    "hsa.gd.gov.cn": "广东省医疗保障局",
    "wsjkw.gd.gov.cn": "广东省卫生健康委员会",
    "cdcp.gd.gov.cn": "广东省疾病预防控制中心",
    "natcm.gov.cn": "国家中医药管理局",
    "ncmhc.org.cn": "国家心理健康和精神卫生防治中心",
    "cncaprc.gov.cn": "中国老龄协会",
    "sport.gov.cn": "国家体育总局",
    "who.int": "World Health Organization",
    "wjw.gz.gov.cn": "广州市卫健委",
    "kepu.wjw.gz.gov.cn": "广州健康科普平台",
    
}

ORG_BY_DOC_ID_PREFIX = {
    "gz_广州市卫健委_": "广州市卫健委",
    "gz_广州市医保局_": "广州市医保局",
    "gz_广州健康科普平台_": "广州健康科普平台",
}

# doc_id 前缀中表示域名的 token，用于从 doc_id 中剥离域名部分，剩下的作为栏目。
DOMAIN_TOKENS = {
    "nhc.gov.cn": ["nhc", "gov", "cn"],
    "ndcpa.gov.cn": ["ndcpa", "gov", "cn"],
    "chinacdc.cn": ["chinacdc", "cn"],
    "ncncd.chinacdc.cn": ["ncncd", "chinacdc", "cn"],
    "nhsa.gov.cn": ["nhsa", "gov", "cn"],
    "hsa.gd.gov.cn": ["hsa", "gd", "gov", "cn"],
    "wsjkw.gd.gov.cn": ["wsjkw", "gd", "gov", "cn"],
    "cdcp.gd.gov.cn": ["cdcp", "gd", "gov", "cn"],
    "natcm.gov.cn": ["www", "natcm", "gov", "cn"],
    "ncmhc.org.cn": ["ncmhc", "org", "cn"],
    "cncaprc.gov.cn": ["www", "cncaprc", "gov", "cn"],
    "sport.gov.cn": ["sport", "gov", "cn"],
}

CHANNEL_NAME_MAP = {
    "zhengce": "政策文件",
    "nhsa_notices": "通知公告",
    "nhsa_policy_regulations": "政策法规",
    "nhsa_policy_interpretation": "政策解读",
    "nhsa_statistics": "统计数据",
    "nhsa_public_services": "公共服务",
    "gd_nhsa_news": "医保动态",
    "gd_nhsa_notices": "通知公告",
    "gd_nhsa_drug_catalog": "药品目录",
    "publication": "publication",
    "technical-document": "technical-document",
    "meeting-report": "meeting-report",
    "report": "report",
    "guideline": "guideline",
}


# -------------------------
# Excel 写入：纯标准库，不依赖 pandas/openpyxl
# -------------------------

def clean_sheet_name(name: str) -> str:
    name = re.sub(r"[\[\]\:\*\?\/\\]", "_", str(name))
    return name[:31] or "Sheet"


def cell_ref(row_idx: int, col_idx: int) -> str:
    letters = ""
    col = col_idx
    while col:
        col, rem = divmod(col - 1, 26)
        letters = chr(65 + rem) + letters
    return f"{letters}{row_idx}"


def value_to_cell_xml(value, row_idx: int, col_idx: int) -> str:
    ref = cell_ref(row_idx, col_idx)
    if value is None:
        return f'<c r="{ref}"/>'
    if isinstance(value, bool):
        return f'<c r="{ref}" t="b"><v>{1 if value else 0}</v></c>'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}"><v>{value}</v></c>'

    text = str(value)
    # Excel 单元格最大字符数 32767。
    if len(text) > 32767:
        text = text[:32764] + "..."
    return f'<c r="{ref}" t="inlineStr"><is><t>{escape(text)}</t></is></c>'


def worksheet_xml(rows) -> str:
    rows_xml = []
    for row_idx, row in enumerate(rows, 1):
        cells = "".join(
            value_to_cell_xml(value, row_idx, col_idx)
            for col_idx, value in enumerate(row, 1)
        )
        rows_xml.append(f'<row r="{row_idx}">{cells}</row>')

    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheetViews><sheetView workbookViewId="0"/></sheetViews>'
        '<sheetFormatPr defaultRowHeight="15"/>'
        f'<sheetData>{"".join(rows_xml)}</sheetData>'
        '</worksheet>'
    )


def write_xlsx(path: Path, sheets) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    # Excel sheet 名不能重复。
    used_names = set()
    sheet_items = []
    for raw_name, rows in sheets:
        name = clean_sheet_name(raw_name)
        base = name
        i = 2
        while name in used_names:
            suffix = f"_{i}"
            name = (base[: 31 - len(suffix)] + suffix)[:31]
            i += 1
        used_names.add(name)
        sheet_items.append((name, rows))

    workbook_sheets = []
    workbook_rels = []
    content_overrides = [
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>',
    ]

    for idx, (name, _) in enumerate(sheet_items, 1):
        workbook_sheets.append(f'<sheet name="{escape(name)}" sheetId="{idx}" r:id="rId{idx}"/>')
        workbook_rels.append(
            f'<Relationship Id="rId{idx}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{idx}.xml"/>'
        )
        content_overrides.append(
            f'<Override PartName="/xl/worksheets/sheet{idx}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )

    style_rel_id = len(sheet_items) + 1
    workbook_rels.append(
        f'<Relationship Id="rId{style_rel_id}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
    )

    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets>{"".join(workbook_sheets)}</sheets></workbook>'
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>'
    )
    workbook_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'{"".join(workbook_rels)}</Relationships>'
    )
    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        f'{"".join(content_overrides)}</Types>'
    )
    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
        '<borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
        '</styleSheet>'
    )

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types_xml)
        zf.writestr("_rels/.rels", rels_xml)
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
        zf.writestr("xl/styles.xml", styles_xml)
        for idx, (_, rows) in enumerate(sheet_items, 1):
            zf.writestr(f"xl/worksheets/sheet{idx}.xml", worksheet_xml(rows))


# -------------------------
# JSONL 字段推断
# -------------------------

def normalize_domain(url: str) -> str:
    domain = urlparse(url or "").netloc.lower()
    domain = domain.replace("www.", "")
    return domain


def infer_org(url: str, doc_id: str = "") -> str:
    for prefix, org in ORG_BY_DOC_ID_PREFIX.items():
        if (doc_id or "").startswith(prefix):
            return org

    domain = normalize_domain(url)
    if domain in ORG_BY_DOMAIN:
        return ORG_BY_DOMAIN[domain]

    # 兜底：处理子域名。
    for key, org in ORG_BY_DOMAIN.items():
        if domain.endswith(key):
            return org

    return domain or "未知机构"


def infer_channel_from_who_doc_id(doc_id: str) -> str:
    # 例：WHO-PUB-meeting-report-20251023-01-0001 -> meeting-report
    parts = doc_id.split("-")
    values = []
    for part in parts[2:]:
        if re.fullmatch(r"\d{8}", part):
            break
        values.append(part)
    raw = "-".join(values) if values else "publication"
    return CHANNEL_NAME_MAP.get(raw, raw)


def infer_channel(doc_id: str, url: str) -> str:
    domain = normalize_domain(url)
    doc_id = doc_id or ""

    if doc_id.startswith("WHO-PUB-"):
        return infer_channel_from_who_doc_id(doc_id)

    for prefix in ORG_BY_DOC_ID_PREFIX:
        if doc_id.startswith(prefix):
            value = doc_id[len(prefix):]
            parts = value.rsplit("_", 1)
            if len(parts) == 2 and re.fullmatch(r"(?:\d+|[0-9a-fA-F]{6})", parts[1]):
                value = parts[0]
            return CHANNEL_NAME_MAP.get(value, value or "未分类")

    parts = doc_id.split("_")
    domain_tokens = DOMAIN_TOKENS.get(domain, [])

    if domain_tokens and parts[: len(domain_tokens)] == domain_tokens:
        parts = parts[len(domain_tokens):]

    channel_parts = []
    for part in parts:
        # 遇到日期或纯数字编号，说明栏目部分结束。
        if re.fullmatch(r"\d{8}", part):
            break
        if re.fullmatch(r"\d+", part):
            break
        channel_parts.append(part)

    raw_channel = "_".join(channel_parts).strip("_") or "未分类"
    return CHANNEL_NAME_MAP.get(raw_channel, raw_channel)


def infer_raw_channel(doc_id: str, url: str) -> str:
    """保留未映射前的栏目值，方便排查。"""
    domain = normalize_domain(url)
    doc_id = doc_id or ""

    if doc_id.startswith("WHO-PUB-"):
        parts = doc_id.split("-")
        values = []
        for part in parts[2:]:
            if re.fullmatch(r"\d{8}", part):
                break
            values.append(part)
        return "-".join(values) if values else "publication"

    for prefix in ORG_BY_DOC_ID_PREFIX:
        if doc_id.startswith(prefix):
            value = doc_id[len(prefix):]
            parts = value.rsplit("_", 1)
            if len(parts) == 2 and re.fullmatch(r"(?:\d+|[0-9a-fA-F]{6})", parts[1]):
                value = parts[0]
            return value or "未分类"

    parts = doc_id.split("_")
    domain_tokens = DOMAIN_TOKENS.get(domain, [])
    if domain_tokens and parts[: len(domain_tokens)] == domain_tokens:
        parts = parts[len(domain_tokens):]

    channel_parts = []
    for part in parts:
        if re.fullmatch(r"\d{8}", part):
            break
        if re.fullmatch(r"\d+", part):
            break
        channel_parts.append(part)

    return "_".join(channel_parts).strip("_") or "未分类"


def format_date_yyyymmdd(s: str) -> str:
    if not s:
        return ""
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def infer_publish_date(doc_id: str, url: str, title: str = "", text: str = "") -> str:
    """
    尽量从 doc_id 和 URL 提取发布日期。

    注意：不从正文全文、标题中猜日期，因为政策标题/正文常出现“2030年规划”、
    “1974年文件”等内容，容易把正文中的年份误判为发布日期。
    """
    candidates = [doc_id or "", url or ""]

    # 1. 8 位日期：20260608
    for value in candidates:
        m = re.search(r"(?<!\d)(20\d{6}|19\d{6})(?!\d)", value)
        if m:
            return format_date_yyyymmdd(m.group(1))

    # 2. URL：/art/2026/5/31/...
    m = re.search(r"/(20\d{2}|19\d{2})/(\d{1,2})/(\d{1,2})(?:/|$)", url or "")
    if m:
        y, mo, d = m.groups()
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"

    # 3. URL 或 doc_id 中偶尔会有中文日期。这里只检查短字段，不检查正文。
    for value in candidates:
        m = re.search(r"(20\d{2}|19\d{2})年(\d{1,2})月(\d{1,2})日", value)
        if m:
            y, mo, d = m.groups()
            return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"

    return ""


def get_year(date_str: str) -> str:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str or ""):
        return date_str[:4]
    return "未知"


def calc_text_quality(text: str) -> str:
    length = len(text or "")
    if length == 0:
        return "空文本"
    if length < 50:
        return "过短"
    return "正常"


def normalize_title(title: str) -> str:
    """
    标准化标题，用于发现“肉眼看起来一样，但空格/标点/全半角不同”的重复标题。

    标准化规则：
    1. 全半角归一化；
    2. 英文字母转小写；
    3. 删除空白符；
    4. 删除常见中英文标点。
    """
    value = unicodedata.normalize("NFKC", str(title or ""))
    value = value.strip().lower()
    value = re.sub(r"\s+", "", value)
    value = re.sub(r"[，,。\.、；;：:！!？?‘’“”\"'（）\(\)【】\[\]《》<>〈〉—\-_·•|/\\]", "", value)
    return value


def title_len_bucket(title: str) -> str:
    length = len(title or "")
    if length == 0:
        return "0 空标题"
    if length <= 10:
        return "1-10"
    if length <= 20:
        return "11-20"
    if length <= 30:
        return "21-30"
    if length <= 50:
        return "31-50"
    if length <= 80:
        return "51-80"
    if length <= 120:
        return "81-120"
    return "120以上"


def duplicate_counter_stats(counter: Counter, *, exclude_empty: bool = True) -> dict:
    items = []
    for key, count in counter.items():
        if exclude_empty and not key:
            continue
        items.append((key, count))

    duplicate_items = [(key, count) for key, count in items if count > 1]
    return {
        "总记录数": sum(count for _, count in items),
        "唯一值数": len(items),
        "重复值种类数": len(duplicate_items),
        "重复涉及记录数": sum(count for _, count in duplicate_items),
        "重复多余记录数": sum(count - 1 for _, count in duplicate_items),
    }


def make_record_brief(input_file, line_no, org, channel, publish_date, title, url, doc_id):
    return {
        "来源文件": input_file,
        "行号": line_no,
        "机构": org,
        "栏目": channel,
        "发布日期": publish_date,
        "标题": title,
        "URL": url,
        "doc_id": doc_id,
    }


def read_clean_jsonl(path: Path):
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                yield None, f"{path.name}:{line_no}: {exc}"
                continue

            if not isinstance(item, dict):
                yield None, f"{path.name}:{line_no}: 不是 JSON 对象"
                continue

            yield item, ""


# -------------------------
# 统计逻辑
# -------------------------

def build_stats(input_paths, preview_chars: int):
    article_rows = [[
        "来源文件",
        "行号",
        "机构",
        "栏目",
        "原始栏目值",
        "发布日期",
        "年份",
        "标题",
        "URL",
        "doc_id",
        "正文字符数",
        "文本质量",
        "正文预览",
    ]]

    org_counter = Counter()
    channel_counter = Counter()
    org_channel_counter = Counter()
    year_counter = Counter()
    org_year_counter = Counter()

    title_counter = Counter()
    normalized_title_counter = Counter()
    org_title_counter = defaultdict(Counter)
    channel_title_counter = defaultdict(Counter)
    org_normalized_title_counter = defaultdict(Counter)
    channel_normalized_title_counter = defaultdict(Counter)
    title_records = defaultdict(list)
    normalized_title_records = defaultdict(list)
    title_len_distribution = Counter()

    org_text_len = defaultdict(int)
    channel_text_len = defaultdict(int)
    org_empty_text = Counter()
    channel_empty_text = Counter()

    seen_urls = set()
    seen_doc_ids = set()
    duplicate_urls = Counter()
    duplicate_doc_ids = Counter()
    parse_errors = []

    empty_title_count = 0
    total_title_len = 0
    min_title_len = None
    max_title_len = 0

    total_records = 0
    empty_text_count = 0
    short_text_count = 0
    total_text_len = 0

    min_text_len = None
    max_text_len = 0
    min_date = ""
    max_date = ""

    file_counter = Counter()

    for input_path in input_paths:
        line_no = 0
        for item, error in read_clean_jsonl(input_path):
            line_no += 1
            if error:
                parse_errors.append(error)
                continue

            total_records += 1
            file_counter[input_path.name] += 1

            doc_id = str(item.get("doc_id", "") or "")
            title = str(item.get("title", "") or "")
            url = str(item.get("url", "") or "")
            text = str(item.get("text", "") or "")
            normalized_title = normalize_title(title)
            title_len = len(title)

            org = infer_org(url, doc_id)
            channel = infer_channel(doc_id, url)
            raw_channel = infer_raw_channel(doc_id, url)
            publish_date = infer_publish_date(doc_id, url, title, text)
            year = get_year(publish_date)
            text_len = len(text)
            text_quality = calc_text_quality(text)

            title_counter[title] += 1
            normalized_title_counter[normalized_title] += 1
            org_title_counter[org][title] += 1
            channel_title_counter[(org, channel)][title] += 1
            org_normalized_title_counter[org][normalized_title] += 1
            channel_normalized_title_counter[(org, channel)][normalized_title] += 1
            title_len_distribution[title_len_bucket(title)] += 1
            total_title_len += title_len
            if not title:
                empty_title_count += 1
            if min_title_len is None or title_len < min_title_len:
                min_title_len = title_len
            if title_len > max_title_len:
                max_title_len = title_len

            brief = make_record_brief(input_path.name, line_no, org, channel, publish_date, title, url, doc_id)
            if title:
                title_records[title].append(brief)
            if normalized_title:
                normalized_title_records[normalized_title].append(brief)

            if url:
                duplicate_urls[url] += 1
                seen_urls.add(url)
            if doc_id:
                duplicate_doc_ids[doc_id] += 1
                seen_doc_ids.add(doc_id)

            org_counter[org] += 1
            channel_counter[(org, channel)] += 1
            org_channel_counter[(org, channel)] += 1
            year_counter[year] += 1
            org_year_counter[(org, year)] += 1
            org_text_len[org] += text_len
            channel_text_len[(org, channel)] += text_len

            total_text_len += text_len
            if text_len == 0:
                empty_text_count += 1
                org_empty_text[org] += 1
                channel_empty_text[(org, channel)] += 1
            elif text_len < 50:
                short_text_count += 1

            if min_text_len is None or text_len < min_text_len:
                min_text_len = text_len
            if text_len > max_text_len:
                max_text_len = text_len

            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", publish_date):
                if not min_date or publish_date < min_date:
                    min_date = publish_date
                if not max_date or publish_date > max_date:
                    max_date = publish_date

            article_rows.append([
                input_path.name,
                line_no,
                org,
                channel,
                raw_channel,
                publish_date,
                year,
                title,
                url,
                doc_id,
                text_len,
                text_quality,
                text[:preview_chars],
            ])

    duplicate_url_count = sum(1 for _, c in duplicate_urls.items() if c > 1)
    duplicate_doc_id_count = sum(1 for _, c in duplicate_doc_ids.items() if c > 1)

    raw_title_stats = duplicate_counter_stats(title_counter)
    normalized_title_stats = duplicate_counter_stats(normalized_title_counter)

    note_rows = [
        ["说明项", "内容"],
        ["统计时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S")],
        ["输入文件", "；".join(str(p) for p in input_paths)],
        ["适配字段", "doc_id、title、url、text"],
        ["标题统计", "同时统计原始 title 重复、标准化 title 重复、标题长度分布、按机构/栏目重复情况"],
        ["标准化标题规则", "全半角归一化、英文字母小写、删除空白符和常见中英文标点"],
        ["机构推断", "根据 URL 域名映射"],
        ["栏目推断", "优先根据 doc_id 中的栏目片段推断，WHO 根据 WHO-PUB 类型推断"],
        ["发布日期推断", "优先从 doc_id/URL/标题/正文开头提取 YYYYMMDD 或中文日期"],
        ["附件统计", "清洗后 JSONL 已无 attachments 字段，本脚本不统计附件数"],
        ["正文预览长度", preview_chars],
        ["解析错误数", len(parse_errors)],
    ]
    note_rows.extend([["解析错误", err] for err in parse_errors[:200]])
    if len(parse_errors) > 200:
        note_rows.append(["解析错误", f"仅展示前 200 条，实际 {len(parse_errors)} 条"])

    overview_rows = [
        ["指标", "数值"],
        ["总数据条数", total_records],
        ["输入文件数", len(input_paths)],
        ["空标题条数", empty_title_count],
        ["唯一标题数", raw_title_stats["唯一值数"]],
        ["重复标题种类数", raw_title_stats["重复值种类数"]],
        ["重复标题涉及记录数", raw_title_stats["重复涉及记录数"]],
        ["重复标题多余记录数", raw_title_stats["重复多余记录数"]],
        ["唯一标准化标题数", normalized_title_stats["唯一值数"]],
        ["标准化重复标题种类数", normalized_title_stats["重复值种类数"]],
        ["标准化重复标题涉及记录数", normalized_title_stats["重复涉及记录数"]],
        ["标准化重复标题多余记录数", normalized_title_stats["重复多余记录数"]],
        ["平均标题字符数", round(total_title_len / total_records, 2) if total_records else 0],
        ["最短标题字符数", min_title_len if min_title_len is not None else 0],
        ["最长标题字符数", max_title_len],
        ["机构数", len(org_counter)],
        ["机构-栏目组合数", len(channel_counter)],
        ["唯一 URL 数", len(seen_urls)],
        ["重复 URL 数", duplicate_url_count],
        ["唯一 doc_id 数", len(seen_doc_ids)],
        ["重复 doc_id 数", duplicate_doc_id_count],
        ["空文本条数", empty_text_count],
        ["过短文本条数(<50字)", short_text_count],
        ["总正文字符数", total_text_len],
        ["平均正文字符数", round(total_text_len / total_records, 2) if total_records else 0],
        ["最短正文字符数", min_text_len if min_text_len is not None else 0],
        ["最长正文字符数", max_text_len],
        ["最早发布日期", min_date],
        ["最晚发布日期", max_date],
    ]

    file_rows = [["来源文件", "数据条数"]]
    for filename, count in sorted(file_counter.items()):
        file_rows.append([filename, count])

    org_rows = [["机构", "数据条数", "占比", "空文本条数", "总正文字符数", "平均正文字符数"]]
    for org, count in sorted(org_counter.items(), key=lambda x: (-x[1], x[0])):
        org_rows.append([
            org,
            count,
            f"{count / total_records:.2%}" if total_records else "0.00%",
            org_empty_text[org],
            org_text_len[org],
            round(org_text_len[org] / count, 2) if count else 0,
        ])

    channel_rows = [["机构", "栏目", "数据条数", "占比", "空文本条数", "总正文字符数", "平均正文字符数"]]
    for (org, channel), count in sorted(channel_counter.items(), key=lambda x: (-x[1], x[0][0], x[0][1])):
        channel_rows.append([
            org,
            channel,
            count,
            f"{count / total_records:.2%}" if total_records else "0.00%",
            channel_empty_text[(org, channel)],
            channel_text_len[(org, channel)],
            round(channel_text_len[(org, channel)] / count, 2) if count else 0,
        ])

    year_rows = [["年份", "数据条数"]]
    for year, count in sorted(year_counter.items(), key=lambda x: (x[0] == "未知", x[0])):
        year_rows.append([year, count])

    org_year_rows = [["机构", "年份", "数据条数"]]
    for (org, year), count in sorted(org_year_counter.items(), key=lambda x: (x[0][0], x[0][1] == "未知", x[0][1])):
        org_year_rows.append([org, year, count])

    dup_url_rows = [["URL", "重复次数"]]
    for url, count in sorted(duplicate_urls.items(), key=lambda x: -x[1]):
        if count > 1:
            dup_url_rows.append([url, count])

    dup_doc_id_rows = [["doc_id", "重复次数"]]
    for doc_id, count in sorted(duplicate_doc_ids.items(), key=lambda x: -x[1]):
        if count > 1:
            dup_doc_id_rows.append([doc_id, count])

    title_overview_rows = [
        ["指标", "数值"],
        ["总记录数", total_records],
        ["空标题条数", empty_title_count],
        ["非空标题记录数", total_records - empty_title_count],
        ["唯一原始标题数", raw_title_stats["唯一值数"]],
        ["重复原始标题种类数", raw_title_stats["重复值种类数"]],
        ["重复原始标题涉及记录数", raw_title_stats["重复涉及记录数"]],
        ["重复原始标题多余记录数", raw_title_stats["重复多余记录数"]],
        ["唯一标准化标题数", normalized_title_stats["唯一值数"]],
        ["标准化重复标题种类数", normalized_title_stats["重复值种类数"]],
        ["标准化重复标题涉及记录数", normalized_title_stats["重复涉及记录数"]],
        ["标准化重复标题多余记录数", normalized_title_stats["重复多余记录数"]],
        ["平均标题字符数", round(total_title_len / total_records, 2) if total_records else 0],
        ["最短标题字符数", min_title_len if min_title_len is not None else 0],
        ["最长标题字符数", max_title_len],
    ]

    title_len_rows = [["标题长度区间", "记录数", "占比"]]
    bucket_order = ["0 空标题", "1-10", "11-20", "21-30", "31-50", "51-80", "81-120", "120以上"]
    for bucket in bucket_order:
        count = title_len_distribution.get(bucket, 0)
        title_len_rows.append([bucket, count, f"{count / total_records:.2%}" if total_records else "0.00%"] )

    title_org_rows = [[
        "机构",
        "标题记录数",
        "空标题条数",
        "唯一标题数",
        "重复标题种类数",
        "重复标题涉及记录数",
        "重复标题多余记录数",
        "唯一标准化标题数",
        "标准化重复标题种类数",
        "标准化重复标题涉及记录数",
        "标准化重复标题多余记录数",
    ]]
    for org, count in sorted(org_counter.items(), key=lambda x: (-x[1], x[0])):
        raw_stats = duplicate_counter_stats(org_title_counter[org])
        norm_stats = duplicate_counter_stats(org_normalized_title_counter[org])
        title_org_rows.append([
            org,
            count,
            org_title_counter[org].get("", 0),
            raw_stats["唯一值数"],
            raw_stats["重复值种类数"],
            raw_stats["重复涉及记录数"],
            raw_stats["重复多余记录数"],
            norm_stats["唯一值数"],
            norm_stats["重复值种类数"],
            norm_stats["重复涉及记录数"],
            norm_stats["重复多余记录数"],
        ])

    title_channel_rows = [[
        "机构",
        "栏目",
        "标题记录数",
        "空标题条数",
        "唯一标题数",
        "重复标题种类数",
        "重复标题涉及记录数",
        "重复标题多余记录数",
        "唯一标准化标题数",
        "标准化重复标题种类数",
        "标准化重复标题涉及记录数",
        "标准化重复标题多余记录数",
    ]]
    for (org, channel), count in sorted(channel_counter.items(), key=lambda x: (-x[1], x[0][0], x[0][1])):
        raw_stats = duplicate_counter_stats(channel_title_counter[(org, channel)])
        norm_stats = duplicate_counter_stats(channel_normalized_title_counter[(org, channel)])
        title_channel_rows.append([
            org,
            channel,
            count,
            channel_title_counter[(org, channel)].get("", 0),
            raw_stats["唯一值数"],
            raw_stats["重复值种类数"],
            raw_stats["重复涉及记录数"],
            raw_stats["重复多余记录数"],
            norm_stats["唯一值数"],
            norm_stats["重复值种类数"],
            norm_stats["重复涉及记录数"],
            norm_stats["重复多余记录数"],
        ])

    duplicate_title_rows = [["标题", "重复次数", "涉及机构数", "涉及栏目数", "示例URL", "示例doc_id"]]
    for title, records in sorted(title_records.items(), key=lambda x: (-len(x[1]), x[0])):
        if len(records) <= 1:
            continue
        duplicate_title_rows.append([
            title,
            len(records),
            len({r["机构"] for r in records}),
            len({(r["机构"], r["栏目"]) for r in records}),
            records[0]["URL"],
            records[0]["doc_id"],
        ])

    duplicate_title_detail_rows = [["标题", "重复次数", "来源文件", "行号", "机构", "栏目", "发布日期", "URL", "doc_id"]]
    for title, records in sorted(title_records.items(), key=lambda x: (-len(x[1]), x[0])):
        if len(records) <= 1:
            continue
        for r in records:
            duplicate_title_detail_rows.append([
                title,
                len(records),
                r["来源文件"],
                r["行号"],
                r["机构"],
                r["栏目"],
                r["发布日期"],
                r["URL"],
                r["doc_id"],
            ])

    duplicate_normalized_title_rows = [["标准化标题", "重复次数", "原始标题种类数", "原始标题示例", "涉及机构数", "涉及栏目数", "示例URL", "示例doc_id"]]
    for norm_title, records in sorted(normalized_title_records.items(), key=lambda x: (-len(x[1]), x[0])):
        if len(records) <= 1:
            continue
        raw_titles = sorted({r["标题"] for r in records})
        duplicate_normalized_title_rows.append([
            norm_title,
            len(records),
            len(raw_titles),
            "；".join(raw_titles[:5]),
            len({r["机构"] for r in records}),
            len({(r["机构"], r["栏目"]) for r in records}),
            records[0]["URL"],
            records[0]["doc_id"],
        ])

    duplicate_normalized_title_detail_rows = [["标准化标题", "原始标题", "重复次数", "来源文件", "行号", "机构", "栏目", "发布日期", "URL", "doc_id"]]
    for norm_title, records in sorted(normalized_title_records.items(), key=lambda x: (-len(x[1]), x[0])):
        if len(records) <= 1:
            continue
        for r in records:
            duplicate_normalized_title_detail_rows.append([
                norm_title,
                r["标题"],
                len(records),
                r["来源文件"],
                r["行号"],
                r["机构"],
                r["栏目"],
                r["发布日期"],
                r["URL"],
                r["doc_id"],
            ])

    json_report = {
        "统计时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "输入文件": [str(p) for p in input_paths],
        "总览": {row[0]: row[1] for row in overview_rows[1:]},
        "标题统计": {
            "总记录数": total_records,
            "空标题条数": empty_title_count,
            "原始标题": raw_title_stats,
            "标准化标题": normalized_title_stats,
            "标题长度分布": dict(title_len_distribution),
        },
        "机构汇总": {
            org: {
                "数据条数": count,
                "空文本条数": org_empty_text[org],
                "总正文字符数": org_text_len[org],
                "平均正文字符数": round(org_text_len[org] / count, 2) if count else 0,
                "标题统计": {
                    "空标题条数": org_title_counter[org].get("", 0),
                    "原始标题": duplicate_counter_stats(org_title_counter[org]),
                    "标准化标题": duplicate_counter_stats(org_normalized_title_counter[org]),
                },
                "栏目": {},
            }
            for org, count in org_counter.items()
        },
        "年份汇总": dict(year_counter),
        "解析错误数": len(parse_errors),
    }

    for (org, channel), count in channel_counter.items():
        json_report["机构汇总"].setdefault(org, {"数据条数": 0, "栏目": {}})
        json_report["机构汇总"][org]["栏目"][channel] = {
            "数据条数": count,
            "空文本条数": channel_empty_text[(org, channel)],
            "总正文字符数": channel_text_len[(org, channel)],
            "平均正文字符数": round(channel_text_len[(org, channel)] / count, 2) if count else 0,
            "标题统计": {
                "空标题条数": channel_title_counter[(org, channel)].get("", 0),
                "原始标题": duplicate_counter_stats(channel_title_counter[(org, channel)]),
                "标准化标题": duplicate_counter_stats(channel_normalized_title_counter[(org, channel)]),
            },
        }

    sheets = [
        ("说明", note_rows),
        ("总览", overview_rows),
        ("来源文件汇总", file_rows),
        ("机构汇总", org_rows),
        ("栏目汇总", channel_rows),
        ("年份汇总", year_rows),
        ("机构年份汇总", org_year_rows),
        ("标题总览", title_overview_rows),
        ("标题长度分布", title_len_rows),
        ("标题机构汇总", title_org_rows),
        ("标题栏目汇总", title_channel_rows),
        ("重复title", duplicate_title_rows),
        ("重复title明细", duplicate_title_detail_rows),
        ("标准化重复title", duplicate_normalized_title_rows),
        ("标准化重复title明细", duplicate_normalized_title_detail_rows),
        ("文章明细", article_rows),
        ("重复URL", dup_url_rows),
        ("重复doc_id", dup_doc_id_rows),
    ]

    return sheets, json_report


def resolve_input_paths(input_values):
    paths = []
    for value in input_values:
        raw = Path(value)
        path = raw if raw.is_absolute() else BASE_DIR / raw
        if path.is_dir():
            paths.extend(sorted(path.glob("*.jsonl")))
        elif "*" in str(path):
            paths.extend(sorted(path.parent.glob(path.name)))
        else:
            paths.append(path)

    unique_paths = []
    seen = set()
    for path in paths:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique_paths.append(path)

    return unique_paths


def main():
    parser = argparse.ArgumentParser(description="统计清洗后的 4 字段 JSONL 文件，并导出 Excel/JSON 报告。")
    parser.add_argument(
        "--input",
        nargs="+",
        default=[str(get_default_input())],
        help="输入 JSONL 文件、目录或通配符。默认：data/output/gov_corpus_clean.jsonl",
    )
    parser.add_argument(
        "--out-dir",
        default=str(DEFAULT_LOGS_DIR),
        help="输出目录。默认：data/logs",
    )
    parser.add_argument(
        "--preview-chars",
        type=int,
        default=2000,
        help="Excel 文章明细中正文预览保留的字符数。默认：2000",
    )
    args = parser.parse_args()

    input_paths = resolve_input_paths(args.input)
    input_paths = [p for p in input_paths if p.exists() and p.is_file()]

    if not input_paths:
        print("未找到可读取的 JSONL 文件。")
        print(f"默认查找路径：{get_default_input()}")
        return

    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = BASE_DIR / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    xlsx_path = out_dir / f"clean_jsonl_stats_{timestamp}.xlsx"
    json_path = out_dir / f"clean_jsonl_stats_{timestamp}.json"

    sheets, json_report = build_stats(input_paths, args.preview_chars)

    write_xlsx(xlsx_path, sheets)
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(json_report, f, ensure_ascii=False, indent=2)

    total_records = json_report["总览"].get("总数据条数", 0)
    title_stats = json_report.get("标题统计", {})
    raw_title_stats = title_stats.get("原始标题", {})
    norm_title_stats = title_stats.get("标准化标题", {})

    print("版本: with_title_stats_v2")
    print(f"读取文件数: {len(input_paths)}")
    print(f"统计数据条数: {total_records}")
    print("Title 统计:")
    print(f"  空标题条数: {title_stats.get('空标题条数', 0)}")
    print(f"  唯一原始标题数: {raw_title_stats.get('唯一值数', 0)}")
    print(f"  重复原始标题种类数: {raw_title_stats.get('重复值种类数', 0)}")
    print(f"  重复原始标题涉及记录数: {raw_title_stats.get('重复涉及记录数', 0)}")
    print(f"  重复原始标题多余记录数: {raw_title_stats.get('重复多余记录数', 0)}")
    print(f"  唯一标准化标题数: {norm_title_stats.get('唯一值数', 0)}")
    print(f"  标准化重复标题种类数: {norm_title_stats.get('重复值种类数', 0)}")
    print(f"  标准化重复标题涉及记录数: {norm_title_stats.get('重复涉及记录数', 0)}")
    print(f"  标准化重复标题多余记录数: {norm_title_stats.get('重复多余记录数', 0)}")
    print("新增 Excel Sheet:")
    print("  标题总览 / 标题长度分布 / 标题机构汇总 / 标题栏目汇总 / 重复title / 重复title明细 / 标准化重复title / 标准化重复title明细")
    print(f"Excel Report: {xlsx_path.resolve()}")
    print(f"JSON Report:  {json_path.resolve()}")


if __name__ == "__main__":
    main()
