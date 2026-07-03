from __future__ import annotations

from dateutil import parser as date_parser


def parse_date_value(value) -> str:
    if not value:
        return ""
    try:
        return date_parser.parse(str(value), fuzzy=True).date().isoformat()
    except Exception:
        return str(value)


parse_date = parse_date_value
