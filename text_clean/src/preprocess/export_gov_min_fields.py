"""Export cleaned government JSONL records with only core text fields.

Output schema:
- doc_id
- title
- url
- text
- attachments

Default paths are relative to the target project layout:
  <project_root>/Clean_Gov/text_clean/src/preprocess/export_gov_min_fields.py
  <project_root>/Clean_Gov/text_clean/data/output/gov-input/raw_all_documents.cleaned.jsonl
  <project_root>/Clean_Gov/text_clean/data/output/gov-input/raw_all_documents.min_fields.jsonl

Use --input and --output to override paths for local experiments.
"""

import argparse
import json
from pathlib import Path


DEFAULT_INPUT_NAME = "raw_all_documents.cleaned.jsonl"
DEFAULT_OUTPUT_NAME = "raw_all_documents.min_fields.jsonl"


def find_project_root(script_path):
    resolved = script_path.resolve()
    for parent in [resolved.parent, *resolved.parents]:
        if parent.name == "Clean_Gov":
            return parent.parent
    return resolved.parents[4]


PROJECT_ROOT = find_project_root(Path(__file__))
GOV_INPUT_DIR = PROJECT_ROOT / "Clean_Gov" / "text_clean" / "data" / "output" / "gov-input"
DEFAULT_INPUT_PATH = GOV_INPUT_DIR / DEFAULT_INPUT_NAME
DEFAULT_OUTPUT_PATH = GOV_INPUT_DIR / DEFAULT_OUTPUT_NAME


def get_text(record):
    text = record.get("text")
    if text is None:
        content = record.get("content")
        if isinstance(content, dict):
            text = content.get("body_text")
    if text is None:
        return ""
    if isinstance(text, str):
        return text
    return str(text)


def get_attachments(record):
    attachments = record.get("attachments")
    if isinstance(attachments, list):
        return attachments
    return []


def build_min_record(record):
    return {
        "doc_id": record.get("doc_id"),
        "title": record.get("title"),
        "url": record.get("url"),
        "text": get_text(record),
        "attachments": get_attachments(record),
    }


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_PATH, help="Input cleaned JSONL path.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="Output JSONL path.")
    return parser.parse_args()


def main():
    args = parse_args()
    input_path = args.input.resolve()
    output_path = args.output.resolve()

    if not input_path.exists() or not input_path.is_file():
        raise FileNotFoundError(f"Input file does not exist: {input_path}")
    if output_path == input_path:
        raise ValueError(f"Refusing to overwrite input file: {input_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

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

            min_record = build_min_record(record)
            if not min_record["text"].strip():
                empty_text_rows += 1

            dst.write(json.dumps(min_record, ensure_ascii=False, separators=(",", ":")))
            dst.write("\n")
            rows += 1

    print(
        f"[DONE] input={input_path} output={output_path} "
        f"rows={rows} bad={bad_rows} empty_text={empty_text_rows}"
    )


if __name__ == "__main__":
    main()
