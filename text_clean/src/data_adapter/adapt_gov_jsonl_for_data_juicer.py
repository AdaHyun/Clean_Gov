"""Adapt Crawler_Gov JSONL files for Data-Juicer text pipelines.

This is the minimal structure adapter:
- preserve every original field;
- copy ``content.body_text`` to the top-level ``text`` field;
- add lightweight ``adapter_meta`` for later routing and analysis.

It does not clean website chrome, re-extract article bodies from HTML, parse
tables, or parse attachments. Those belong to later processing stages.
"""

import argparse
import json
from pathlib import Path


def find_project_root(script_path):
    resolved = script_path.resolve()
    for parent in [resolved.parent, *resolved.parents]:
        if parent.name == "Clean_Gov":
            return parent.parent
    return resolved.parents[4]


PROJECT_ROOT = find_project_root(Path(__file__))
TEXT_CLEAN_OUTPUT_DIR = PROJECT_ROOT / "Clean_Gov" / "text_clean" / "data" / "output"
DEFAULT_INPUT_DIR = TEXT_CLEAN_OUTPUT_DIR / "gov-input"
DEFAULT_OUTPUT_DIR = TEXT_CLEAN_OUTPUT_DIR / "gov_rawData_juicer"


def get_body_text(record):
    content = record.get("content")
    if not isinstance(content, dict):
        return ""

    body_text = content.get("body_text")
    if body_text is None:
        return ""
    if isinstance(body_text, str):
        return body_text
    return str(body_text)


def get_body_html(record):
    content = record.get("content")
    if not isinstance(content, dict):
        return ""

    body_html = content.get("body_html")
    if body_html is None:
        return ""
    if isinstance(body_html, str):
        return body_html
    return str(body_html)


def get_attachments(record):
    attachments = record.get("attachments")
    if isinstance(attachments, list):
        return attachments
    return []


def build_adapter_meta(record, text):
    body_html = get_body_html(record)
    attachments = get_attachments(record)
    return {
        "text_source": "content.body_text",
        "body_text_len": len(text),
        "has_body_html": bool(body_html.strip()),
        "body_html_len": len(body_html),
        "has_attachment": bool(attachments),
        "attachment_count": len(attachments),
    }


def adapt_file(input_path, output_path):
    rows = 0
    bad_rows = 0
    empty_text_rows = 0

    with input_path.open("r", encoding="utf-8-sig", errors="replace") as src, output_path.open(
        "w", encoding="utf-8", newline="\n"
    ) as dst:
        for line_no, line in enumerate(src, 1):
            if not line.strip():
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                bad_rows += 1
                print(f"[WARN] {input_path.name}:{line_no} skipped invalid JSON: {exc}")
                continue

            if not isinstance(record, dict):
                bad_rows += 1
                print(f"[WARN] {input_path.name}:{line_no} skipped non-object JSON value")
                continue

            text = get_body_text(record)
            if not text.strip():
                empty_text_rows += 1
            record["text"] = text
            record["adapter_meta"] = build_adapter_meta(record, text)

            dst.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            dst.write("\n")
            rows += 1

    return {
        "file": input_path.name,
        "output": output_path.name,
        "rows": rows,
        "bad_rows": bad_rows,
        "empty_text_rows": empty_text_rows,
    }


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR, help="Directory containing source JSONL files.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Directory for adapted JSONL files.")
    parser.add_argument("--prefix", default="juicer_", help="Prefix added to each output filename.")
    return parser.parse_args()


def main():
    args = parse_args()
    input_dir = args.input_dir
    output_dir = args.output_dir

    if not input_dir.exists() or not input_dir.is_dir():
        raise FileNotFoundError(f"Input directory does not exist or is not a directory: {input_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    input_files = sorted(input_dir.glob("*.jsonl"))
    if not input_files:
        raise FileNotFoundError(f"No .jsonl files found in: {input_dir}")

    total_rows = 0
    total_bad_rows = 0
    total_empty_text_rows = 0

    for input_path in input_files:
        output_path = output_dir / f"{args.prefix}{input_path.name}"
        stat = adapt_file(input_path, output_path)
        total_rows += stat["rows"]
        total_bad_rows += stat["bad_rows"]
        total_empty_text_rows += stat["empty_text_rows"]
        print(
            f"[OK] {stat['file']} -> {stat['output']} "
            f"rows={stat['rows']} bad={stat['bad_rows']} empty_text={stat['empty_text_rows']}"
        )

    print(
        f"[DONE] files={len(input_files)} rows={total_rows} "
        f"bad={total_bad_rows} empty_text={total_empty_text_rows} output_dir={output_dir}"
    )


if __name__ == "__main__":
    main()
