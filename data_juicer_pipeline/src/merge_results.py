"""Disk-backed stable-order result merge."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from jsonl_io import write_jsonl_record


INTERNAL_KEYS = {
    "__dj_order",
    "__dj_group_key",
    "__dj_protection",
    "__dj_protection_json",
    "__dj_metadata_json",
    "__dj_key_order_json",
    "__dj_risk_flags",
    "__dj_risk_flags_json",
}


class OrderedRecordStore:
    """SQLite-backed record store avoids loading all processed documents."""

    def __init__(self, path: Path):
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.execute("CREATE TABLE records (ordinal INTEGER PRIMARY KEY, payload TEXT NOT NULL)")

    def add(self, ordinal: int, record: dict[str, Any]) -> None:
        self.connection.execute(
            "INSERT INTO records(ordinal, payload) VALUES (?, ?)",
            (ordinal, json.dumps(record, ensure_ascii=False, separators=(",", ":"))),
        )

    def commit(self) -> None:
        self.connection.commit()

    def export(self, output_path: Path) -> int:
        count = 0
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8", newline="\n") as handle:
            for _, payload in self.connection.execute("SELECT ordinal, payload FROM records ORDER BY ordinal"):
                record = json.loads(payload)
                for key in INTERNAL_KEYS:
                    record.pop(key, None)
                write_jsonl_record(handle, record)
                count += 1
        return count

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "OrderedRecordStore":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()
