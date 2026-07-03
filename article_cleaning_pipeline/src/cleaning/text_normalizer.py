from __future__ import annotations

import re
from html import unescape

from bs4 import BeautifulSoup


def normalize_chinese_web_text(text: str) -> str:
    """Normalize Chinese government web text without rewriting semantics."""
    text = unescape(text or "").replace("\xa0", " ")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"([〔（(])\s+", r"\1", text)
    text = re.sub(r"\s+([〕）)])", r"\1", text)
    text = re.sub(r"(\d{2,4})\s+年\s*(\d{1,2})\s+月\s*(\d{1,2})\s+日", r"\1年\2月\3日", text)
    text = re.sub(r"(?<=\d)\s+(?=\d\s*年)", "", text)
    text = re.sub(r"(\d)\s*-\s*(\d)", r"\1-\2", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def html_to_clean_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return normalize_chinese_web_text(soup.get_text("\n"))


# Backward-compatible alias used by the first pipeline version.
clean_text_basic = normalize_chinese_web_text
