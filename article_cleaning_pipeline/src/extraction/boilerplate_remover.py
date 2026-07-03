from __future__ import annotations

from bs4 import BeautifulSoup


def remove_html_boilerplate(html: str, remove_selectors: list[str] | None = None) -> str:
    soup = BeautifulSoup(html or "", "lxml")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    for selector in remove_selectors or []:
        for node in soup.select(selector):
            node.decompose()
    return str(soup)
