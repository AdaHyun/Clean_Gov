import json
from pathlib import Path


def iter_jsonl_files(jsonl_dir: str | Path):
    yield from sorted(Path(jsonl_dir).rglob("*.jsonl"))


def read_jsonl(path: str | Path):
    path = Path(path)
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line_no, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                rec.setdefault("_ingest", {})
                rec["_ingest"].update({"input_file": str(path), "line_no": line_no})
                yield rec
            except Exception as exc:
                yield {"_parse_error": str(exc), "_raw_line": line[:1000], "_ingest": {"input_file": str(path), "line_no": line_no}}
