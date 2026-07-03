from __future__ import annotations

from urllib.parse import urlparse

from w3lib.url import canonicalize_url as _canonicalize_url


def normalize_url(url: str) -> str:
    return (url or "").strip()


def canonical_url(url: str) -> str:
    return _canonicalize_url(url, keep_blank_values=False) if url else ""


def site_domain(url: str) -> str:
    netloc = urlparse(url or "").netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


canonicalize_url = _canonicalize_url
