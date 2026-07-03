from __future__ import annotations

from collections import Counter

from src.utils import flatten_keys, type_name


def profile_schema(records: list[dict]) -> dict:
    field_counter = Counter()
    type_counter = {}
    for record in records:
        field_counter.update(flatten_keys(record))
        for field in flatten_keys(record):
            type_counter.setdefault(field, Counter()).update([type_name(record.get(field))])
    return {
        "total_records": len(records),
        "field_frequency": dict(field_counter),
        "field_types": {k: dict(v) for k, v in type_counter.items()},
    }
