from __future__ import annotations

from bs4 import BeautifulSoup

from src.cleaning.text_normalizer import normalize_chinese_web_text


def expand_table(table):
    grid, spans = [], {}
    for r_idx, tr in enumerate(table.find_all("tr")):
        row, c_idx = [], 0
        for cell in tr.find_all(["td", "th"]):
            while spans.get((r_idx, c_idx)):
                row.append(spans[(r_idx, c_idx)])
                c_idx += 1
            text = normalize_chinese_web_text(cell.get_text(" ", strip=True))
            rs, cs = int(cell.get("rowspan", 1) or 1), int(cell.get("colspan", 1) or 1)
            for rr in range(r_idx, r_idx + rs):
                for cc in range(c_idx, c_idx + cs):
                    spans[(rr, cc)] = text
            for _ in range(cs):
                row.append(text)
                c_idx += 1
        grid.append(row)
    return (grid[0], grid[1:]) if grid else ([], [])


def parse_tables_from_html(html: str, doc_id: str) -> tuple[list[dict], list[dict]]:
    soup = BeautifulSoup(html or "", "lxml")
    parsed, failed = [], []
    for i, table in enumerate(soup.find_all("table"), 1):
        table_id = f"{doc_id}#t{i:03d}"
        try:
            headers, rows = expand_table(table)
            parsed.append({
                "doc_id": doc_id,
                "table_id": table_id,
                "table_title": "",
                "headers": headers,
                "rows": rows,
                "notes": [],
                "raw_table_html": str(table)[:5000],
                "parse_status": "success",
            })
        except Exception as exc:
            failed.append({"doc_id": doc_id, "table_id": table_id, "raw_table_html": str(table)[:5000], "failure_reason": str(exc)})
    return parsed, failed
