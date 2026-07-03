from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from bs4 import BeautifulSoup


NOISE_PATTERNS = [
    "长者版", "无障碍", "首页", "当前位置", "打印本页", "关闭窗口", "上一篇", "下一篇",
    "分享到", "责任编辑", "网站地图", "ICP备案", "主办单位", "版权所有", "English"
]


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def get_path(obj, dotted, default=None):
    cur = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def set_path(obj, dotted, value):
    cur = obj
    parts = dotted.split(".")
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def flatten_keys(obj, prefix=""):
    keys = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            keys.append(p)
            keys.extend(flatten_keys(v, p))
    elif isinstance(obj, list):
        for v in obj[:3]:
            keys.extend(flatten_keys(v, prefix + "[]"))
    return keys


def type_name(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int) and not isinstance(v, bool):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "str"
    if isinstance(v, list):
        return "list"
    if isinstance(v, dict):
        return "dict"
    return type(v).__name__


def is_missing(v):
    return v is None or v == "" or v == [] or v == {}


def sha1_text(text: str):
    return hashlib.sha1((text or "").encode("utf-8", errors="ignore")).hexdigest()


def normalize_url(url: str):
    if not url:
        return ""
    return url.strip()


def domain_of(url: str):
    if not url:
        return ""
    netloc = urlparse(url).netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


def clean_text_basic(text: str):
    text = text or ""
    text = text.replace("\xa0", " ")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"([〔（(])\s+", r"\1", text)
    text = re.sub(r"\s+([〕）)])", r"\1", text)
    text = re.sub(r"(\d{2,4})\s+年\s*(\d{1,2})\s+月\s*(\d{1,2})\s+日", r"\1年\2月\3日", text)
    text = re.sub(r"(\d)\s*-\s*(\d)", r"\1-\2", text)
    text = re.sub(r"(?<=\d)\s+(?=\d\s*年)", "", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def noise_hits(text: str):
    return [p for p in NOISE_PATTERNS if p in (text or "")]


def html_to_text(html: str):
    soup = BeautifulSoup(html or "", "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return clean_text_basic(soup.get_text("\n"))


def choose_article_html(html: str, selectors: list[str] | None = None):
    soup = BeautifulSoup(html or "", "lxml")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    selectors = selectors or []
    candidates = []
    for sel in selectors:
        for node in soup.select(sel):
            txt = node.get_text(" ", strip=True)
            if len(txt) > 50:
                candidates.append((len(txt), sel, node))
    for node in soup.find_all(["article", "main", "div", "td"]):
        cls = " ".join(node.get("class", []))
        ident = node.get("id", "")
        marker = f"{cls} {ident}".lower()
        if any(x in marker for x in ["content", "article", "detail", "text", "main", "zw", "con"]):
            txt = node.get_text(" ", strip=True)
            if len(txt) > 50:
                candidates.append((len(txt), "auto:" + (ident or cls or node.name), node))
    body = soup.body or soup
    candidates.append((len(body.get_text(" ", strip=True)), "body", body))
    candidates.sort(key=lambda x: x[0], reverse=True)
    _, selector, node = candidates[0]
    return str(node), selector, min(1.0, len(node.get_text(" ", strip=True)) / 1000)


def resolve_existing_path(raw_html_dir: Path, project_root: Path, raw_path: str):
    if not raw_path:
        return ""
    p = Path(raw_path)
    candidates = []
    if p.is_absolute():
        candidates.append(p)
    candidates.extend([project_root / p, raw_html_dir / p, raw_html_dir / p.name])
    if "data/raw_html" in raw_path.replace("\\", "/"):
        candidates.append(project_root / "Crawler311" / "corpus_crawler" / "Crawler_Gov" / raw_path)
    for c in candidates:
        try:
            if c.exists():
                return str(c)
        except OSError:
            pass
    return ""


def write_md(path: Path, title: str, sections: list[tuple[str, str]]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n")
        for h, body in sections:
            f.write(f"## {h}\n\n{body.strip()}\n\n")
