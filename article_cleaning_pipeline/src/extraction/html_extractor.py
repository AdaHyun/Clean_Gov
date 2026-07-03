from __future__ import annotations

from bs4 import BeautifulSoup

from src.cleaning.text_normalizer import html_to_clean_text


def remove_boilerplate_nodes(soup: BeautifulSoup) -> BeautifulSoup:
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    return soup


def choose_article_html(html: str, selectors: list[str] | None = None) -> tuple[str, str, float]:
    soup = remove_boilerplate_nodes(BeautifulSoup(html or "", "lxml"))
    candidates = []
    for selector in selectors or []:
        for node in soup.select(selector):
            text = node.get_text(" ", strip=True)
            if len(text) > 50:
                candidates.append((len(text), selector, node))
    for node in soup.find_all(["article", "main", "div", "td"]):
        cls = " ".join(node.get("class", []))
        ident = node.get("id", "")
        marker = f"{cls} {ident}".lower()
        if any(x in marker for x in ["content", "article", "detail", "text", "main", "zw", "con"]):
            text = node.get_text(" ", strip=True)
            if len(text) > 50:
                candidates.append((len(text), "auto:" + (ident or cls or node.name), node))
    body = soup.body or soup
    candidates.append((len(body.get_text(" ", strip=True)), "body", body))
    candidates.sort(key=lambda x: x[0], reverse=True)
    _, selector, node = candidates[0]
    return str(node), selector, min(1.0, len(node.get_text(" ", strip=True)) / 1000)


def extract_article_html(html: str, selectors: list[str] | None = None) -> dict:
    clean_html, selector, confidence = choose_article_html(html, selectors)
    return {
        "clean_html": clean_html,
        "selector_used": selector,
        "confidence": confidence,
        "text": html_to_clean_text(clean_html),
    }


html_to_text = html_to_clean_text
