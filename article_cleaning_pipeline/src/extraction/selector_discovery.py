from __future__ import annotations

from collections import Counter

from .html_extractor import choose_article_html


def discover_selector(html: str, configured_selectors: list[str] | None = None) -> str:
    _, selector, _ = choose_article_html(html, configured_selectors)
    return selector


def summarize_selectors(selectors: list[str]) -> dict:
    return dict(Counter(selectors).most_common(20))
