import json
from pathlib import Path


def ensure_dir(path: str | Path):
    Path(path).mkdir(parents=True, exist_ok=True)


def write_json(path: str | Path, obj):
    path = Path(path)
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=str)


def write_jsonl(path: str | Path, records):
    path = Path(path)
    ensure_dir(path.parent)
    count = 0
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            count += 1
    return count
