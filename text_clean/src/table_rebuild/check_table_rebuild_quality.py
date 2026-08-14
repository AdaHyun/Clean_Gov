import json
import csv
import re
from pathlib import Path
from bs4 import BeautifulSoup


PROJECT_ROOT = Path(__file__).resolve().parents[4]
TEXT_CLEAN_OUTPUT = PROJECT_ROOT / "Clean_Gov" / "text_clean" / "data" / "output"
ORIGINAL_JSONL = TEXT_CLEAN_OUTPUT / "gov-routing" / "all_with_routing_meta.jsonl"
REBUILT_JSONL = TEXT_CLEAN_OUTPUT / "gov-table-clean" / "all_rebuilt.jsonl"

REPORT_DIR = TEXT_CLEAN_OUTPUT / "gov-table-clean"
REPORT_JSON = REPORT_DIR / "table_rebuild_quality_report.json"
FAILED_CSV = REPORT_DIR / "table_rebuild_failed_docs.csv"
SAMPLE_TXT = REPORT_DIR / "table_rebuild_check_samples.txt"


def get_content_obj(item: dict) -> dict:
    content_obj = item.get("content", {})
    return content_obj if isinstance(content_obj, dict) else {}


def get_body_html(item: dict) -> str:
    content_obj = get_content_obj(item)
    return content_obj.get("body_html") or item.get("body_html") or ""


def get_body_text(item: dict) -> str:
    content_obj = get_content_obj(item)
    return (
        content_obj.get("body_text")
        or content_obj.get("body_context")
        or item.get("body_text")
        or item.get("body_context")
        or item.get("text")
        or ""
    )


def routing_marked_has_table(item: dict) -> bool:
    routing_meta = item.get("routing_meta", {})
    flags = routing_meta.get("flags", [])
    return isinstance(flags, list) and "has_table" in flags


def analyze_html_tables(body_html: str) -> dict:
    """
    统计原始 body_html 里的 table 数量、tr 行数、td/th 单元格数量。
    """
    if not body_html or not body_html.strip():
        return {
            "has_table": False,
            "table_count": 0,
            "tr_count": 0,
            "cell_count": 0,
        }

    soup = BeautifulSoup(body_html, "html.parser")
    tables = soup.find_all("table")

    tr_count = 0
    cell_count = 0

    for table in tables:
        rows = table.find_all("tr")
        tr_count += len(rows)

        for row in rows:
            cell_count += len(row.find_all(["td", "th"]))

    return {
        "has_table": len(tables) > 0,
        "table_count": len(tables),
        "tr_count": tr_count,
        "cell_count": cell_count,
    }


def is_markdown_separator_line(line: str) -> bool:
    """
    判断是否是 Markdown 表格分隔行：
    | --- | --- |
    """
    line = line.strip()
    if "|" not in line:
        return False

    parts = [p.strip() for p in line.strip("|").split("|")]
    if len(parts) < 2:
        return False

    return all(re.fullmatch(r":?-{3,}:?", p or "") for p in parts)


def analyze_markdown_tables(text: str) -> dict:
    """
    统计 text 里的 Markdown 表格块数量和行数。
    """
    if not text:
        return {
            "has_md_table": False,
            "md_table_count": 0,
            "md_table_row_count": 0,
        }

    lines = text.splitlines()
    table_count = 0
    table_row_count = 0

    i = 0
    while i < len(lines):
        if is_markdown_separator_line(lines[i]):
            table_count += 1

            # 向上找表头行
            start = i - 1
            while start >= 0 and "|" in lines[start]:
                start -= 1
            start += 1

            # 向下找表格结束
            end = i + 1
            while end < len(lines) and "|" in lines[end]:
                end += 1

            # Markdown 表格真实内容行数 = 表格总行数 - 分隔行
            table_row_count += max(0, (end - start - 1))

            i = end
        else:
            i += 1

    return {
        "has_md_table": table_count > 0,
        "md_table_count": table_count,
        "md_table_row_count": table_row_count,
    }


def load_rebuilt_by_doc_id(path: Path) -> dict:
    data = {}

    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue

            item = json.loads(line)
            doc_id = item.get("doc_id")

            if doc_id:
                data[doc_id] = item

    return data


def main():
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    rebuilt_map = load_rebuilt_by_doc_id(REBUILT_JSONL)

    total_docs = 0
    original_table_docs = 0
    rebuilt_table_success_docs = 0
    rebuilt_table_failed_docs = 0
    empty_text_docs = 0
    html_residue_docs = 0
    row_mismatch_docs = 0
    missing_rebuilt_docs = 0
    no_table_docs = 0

    failed_rows = []
    sample_blocks = []

    with ORIGINAL_JSONL.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue

            total_docs += 1
            original_item = json.loads(line)

            doc_id = original_item.get("doc_id", "")
            title = original_item.get("title", "")
            url = original_item.get("url", "")

            body_html = get_body_html(original_item)
            html_info = analyze_html_tables(body_html)

            original_has_table = routing_marked_has_table(original_item) or html_info["has_table"]

            rebuilt_item = rebuilt_map.get(doc_id)
            if not rebuilt_item:
                missing_rebuilt_docs += 1
                failed_rows.append({
                    "issue": "missing_rebuilt_doc",
                    "doc_id": doc_id,
                    "title": title,
                    "url": url,
                    "original_table_count": html_info["table_count"],
                    "original_tr_count": html_info["tr_count"],
                    "md_table_count": "",
                    "md_table_row_count": "",
                })
                continue

            text = rebuilt_item.get("text", "") or ""
            md_info = analyze_markdown_tables(text)

            if not text.strip():
                empty_text_docs += 1
                failed_rows.append({
                    "issue": "empty_text",
                    "doc_id": doc_id,
                    "title": title,
                    "url": url,
                    "original_table_count": html_info["table_count"],
                    "original_tr_count": html_info["tr_count"],
                    "md_table_count": md_info["md_table_count"],
                    "md_table_row_count": md_info["md_table_row_count"],
                })

            if "<table" in text.lower() or "</table>" in text.lower():
                html_residue_docs += 1
                failed_rows.append({
                    "issue": "html_table_residue",
                    "doc_id": doc_id,
                    "title": title,
                    "url": url,
                    "original_table_count": html_info["table_count"],
                    "original_tr_count": html_info["tr_count"],
                    "md_table_count": md_info["md_table_count"],
                    "md_table_row_count": md_info["md_table_row_count"],
                })

            if original_has_table:
                original_table_docs += 1

                if md_info["has_md_table"]:
                    rebuilt_table_success_docs += 1
                else:
                    rebuilt_table_failed_docs += 1
                    failed_rows.append({
                        "issue": "table_not_converted_to_markdown",
                        "doc_id": doc_id,
                        "title": title,
                        "url": url,
                        "original_table_count": html_info["table_count"],
                        "original_tr_count": html_info["tr_count"],
                        "md_table_count": md_info["md_table_count"],
                        "md_table_row_count": md_info["md_table_row_count"],
                    })

                # 行数粗检：Markdown 表格行数和 HTML tr 行数差太多，说明可能有转换异常
                if md_info["has_md_table"]:
                    diff = abs(html_info["tr_count"] - md_info["md_table_row_count"])

                    # 允许少量误差，复杂 HTML、空行、合并单元格可能造成 1-2 行差异
                    if diff > max(2, html_info["tr_count"] * 0.1):
                        row_mismatch_docs += 1
                        failed_rows.append({
                            "issue": "table_row_count_mismatch",
                            "doc_id": doc_id,
                            "title": title,
                            "url": url,
                            "original_table_count": html_info["table_count"],
                            "original_tr_count": html_info["tr_count"],
                            "md_table_count": md_info["md_table_count"],
                            "md_table_row_count": md_info["md_table_row_count"],
                        })

                if len(sample_blocks) < 10 and md_info["has_md_table"]:
                    table_pos = text.find("|")
                    start = max(0, table_pos - 300)
                    end = min(len(text), table_pos + 1500)

                    sample_blocks.append(
                        "=" * 100 + "\n"
                        f"doc_id: {doc_id}\n"
                        f"title: {title}\n"
                        f"url: {url}\n"
                        f"original_tr_count: {html_info['tr_count']}\n"
                        f"md_table_row_count: {md_info['md_table_row_count']}\n"
                        "-" * 100 + "\n"
                        f"{text[start:end]}\n"
                    )
            else:
                no_table_docs += 1

    report = {
        "total_docs": total_docs,
        "original_table_docs": original_table_docs,
        "rebuilt_table_success_docs": rebuilt_table_success_docs,
        "rebuilt_table_failed_docs": rebuilt_table_failed_docs,
        "no_table_docs": no_table_docs,
        "empty_text_docs": empty_text_docs,
        "html_residue_docs": html_residue_docs,
        "row_mismatch_docs": row_mismatch_docs,
        "missing_rebuilt_docs": missing_rebuilt_docs,
        "can_enter_next_step": (
            rebuilt_table_failed_docs == 0
            and empty_text_docs == 0
            and html_residue_docs == 0
            and missing_rebuilt_docs == 0
        ),
    }

    REPORT_JSON.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    with FAILED_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        fieldnames = [
            "issue",
            "doc_id",
            "title",
            "url",
            "original_table_count",
            "original_tr_count",
            "md_table_count",
            "md_table_row_count",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(failed_rows)

    SAMPLE_TXT.write_text("\n".join(sample_blocks), encoding="utf-8")

    print("检查完成")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"质量报告: {REPORT_JSON}")
    print(f"问题清单: {FAILED_CSV}")
    print(f"抽样预览: {SAMPLE_TXT}")


if __name__ == "__main__":
    main()
