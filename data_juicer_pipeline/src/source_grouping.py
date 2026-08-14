"""Conservative source/domain grouping."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit


UNKNOWN_SOURCE = "unknown_source"


def normalize_domain(url: object) -> str:
    if not isinstance(url, str) or not url.strip():
        return UNKNOWN_SOURCE
    candidate = url.strip()
    if "://" not in candidate:
        candidate = "https://" + candidate
    try:
        host = (urlsplit(candidate).hostname or "").lower().strip(".")
    except ValueError:
        return UNKNOWN_SOURCE
    if host.startswith("www."):
        host = host[4:]
    return host or UNKNOWN_SOURCE


def normalize_source(source: object) -> str:
    if not isinstance(source, str) or not source.strip():
        return ""
    return re.sub(r"\s+", " ", source.strip())


@dataclass(frozen=True)
class SourceGroup:
    key: str
    source: str
    domain: str
    slug: str


def group_for(record: dict[str, Any]) -> SourceGroup:
    source = normalize_source(record.get("source"))
    domain = normalize_domain(record.get("url"))
    if source and domain != UNKNOWN_SOURCE:
        key = f"{source} + {domain}"
    elif domain != UNKNOWN_SOURCE:
        key = domain
    elif source:
        key = f"{source} + {UNKNOWN_SOURCE}"
    else:
        key = UNKNOWN_SOURCE
    readable = re.sub(r"[^0-9A-Za-z._-]+", "_", f"{source}_{domain}").strip("_")[:70]
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
    return SourceGroup(key, source, domain, f"{readable or UNKNOWN_SOURCE}_{digest}")
