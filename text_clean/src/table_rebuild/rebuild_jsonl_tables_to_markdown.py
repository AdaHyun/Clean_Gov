import json
import re
from pathlib import Path
from bs4 import BeautifulSoup
from markdownify import markdownify as md


PROJECT_ROOT = Path(__file__).resolve().parents[4]
TEXT_CLEAN_OUTPUT = PROJECT_ROOT / "Clean_Gov" / "text_clean" / "data" / "output"
INPUT_JSONL = TEXT_CLEAN_OUTPUT / "gov-routing" / "all_with_routing_meta.jsonl"
OUTPUT_JSONL = TEXT_CLEAN_OUTPUT / "gov-table-clean" / "all_rebuilt.jsonl"


def clean_markdown(text: str) -> str:
    if not text:
        return ""

    text = text.replace("\xa0", " ")
    text = text.replace("\u3000", " ")
    text = text.replace(" ", " ")

    # 去掉 markdownify 从 <strong> 转出来的加粗符号
    text = text.replace("**", "")

    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def preprocess_sup_tags(soup: BeautifulSoup):
    """
    把 <sup>1</sup> 转成 [1]，避免脚注数字和正文混在一起。
    例如：死亡数1 -> 死亡数[1]
    """
    for sup in soup.find_all("sup"):
        sup_text = sup.get_text(strip=True)
        if sup_text:
            sup.replace_with(f"[{sup_text}]")


def html_has_table(body_html: str) -> bool:
    if not body_html or not body_html.strip():
        return False

    soup = BeautifulSoup(body_html, "html.parser")
    return soup.find("table") is not None


def html_to_markdown(body_html: str) -> str:
    if not body_html or not body_html.strip():
        return ""

    soup = BeautifulSoup(body_html, "html.parser")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    preprocess_sup_tags(soup)

    markdown_text = md(
        str(soup),
        heading_style="ATX",
        table_infer_header=True,
        strip=["script", "style", "noscript"],
    )

    return clean_markdown(markdown_text)


def get_body_fields(item: dict) -> tuple[str, str]:
    """
    兼容你的数据结构：
    1. content.body_text
    2. content.body_html
    3. 顶层 text
    """
    content_obj = item.get("content", {})
    if not isinstance(content_obj, dict):
        content_obj = {}

    body_text = (
        content_obj.get("body_text")
        or content_obj.get("body_context")
        or item.get("body_text")
        or item.get("body_context")
        or item.get("text")
        or ""
    )

    body_html = (
        content_obj.get("body_html")
        or item.get("body_html")
        or ""
    )

    return body_text, body_html


def item_marked_has_table(item: dict, body_html: str) -> bool:
    """
    优先用 routing_meta 的标记判断。
    如果没有标记，再解析 HTML 判断。
    """
    routing_meta = item.get("routing_meta", {})
    flags = routing_meta.get("flags", [])

    if isinstance(flags, list) and "has_table" in flags:
        return True

    return html_has_table(body_html)


def process_jsonl(input_jsonl: Path, output_jsonl: Path):
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)

    total = 0
    table_docs = 0
    no_table_docs = 0
    fallback_docs = 0
    empty_docs = 0
    failed = 0

    with input_jsonl.open("r", encoding="utf-8") as fin, \
         output_jsonl.open("w", encoding="utf-8") as fout:

        for line_no, line in enumerate(fin, start=1):
            line = line.strip()
            if not line:
                continue

            total += 1

            try:
                item = json.loads(line)

                doc_id = item.get("doc_id", "")
                title = item.get("title", "")
                url = item.get("url", "")

                body_text, body_html = get_body_fields(item)

                has_table = item_marked_has_table(item, body_html)

                if has_table and body_html:
                    rebuilt_text = html_to_markdown(body_html)

                    if rebuilt_text:
                        text = rebuilt_text
                        table_docs += 1
                    else:
                        text = clean_markdown(body_text)
                        fallback_docs += 1
                else:
                    text = clean_markdown(body_text)
                    no_table_docs += 1

                if not text.strip():
                    empty_docs += 1
                    print(f"[WARN] empty text at line {line_no}, doc_id={doc_id}")
                    print(f"  top keys: {list(item.keys())}")
                    content_obj = item.get("content", {})
                    if isinstance(content_obj, dict):
                        print(f"  content keys: {list(content_obj.keys())}")

                output_item = {
                    "doc_id": doc_id,
                    "title": title,
                    "url": url,
                    "text": text,
                }

                fout.write(json.dumps(output_item, ensure_ascii=False) + "\n")

            except Exception as e:
                failed += 1
                print(f"[ERROR] line {line_no}: {e}")

    print("处理完成")
    print(f"总条数: {total}")
    print(f"含表格并重构: {table_docs}")
    print(f"无表格直接保留正文: {no_table_docs}")
    print(f"重构失败回退原正文: {fallback_docs}")
    print(f"空正文条数: {empty_docs}")
    print(f"失败条数: {failed}")
    print(f"输出文件: {output_jsonl}")


if __name__ == "__main__":
    process_jsonl(INPUT_JSONL, OUTPUT_JSONL)
