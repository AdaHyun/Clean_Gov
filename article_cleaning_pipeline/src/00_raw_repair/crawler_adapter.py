from __future__ import annotations

import importlib
import sys
import urllib.request
from pathlib import Path

from .common import posix_rel


class CrawlerAdapter:
    def __init__(self, crawler_root: Path):
        self.crawler_root = crawler_root
        self.src_root = crawler_root / "src"
        self.fetcher = None
        self.utils = None
        if self.src_root.exists():
            sys.path.insert(0, str(self.src_root))
            try:
                self.fetcher = importlib.import_module("fetcher")
            except Exception:
                self.fetcher = None
            try:
                self.utils = importlib.import_module("utils")
            except Exception:
                self.utils = None

    def fetch_html(self, url: str) -> tuple[bool, str, str]:
        if self.fetcher and hasattr(self.fetcher, "fetch_html"):
            try:
                html = self.fetcher.fetch_html(url)
                if isinstance(html, tuple):
                    html = html[0]
                return True, str(html or ""), ""
            except Exception as exc:
                return False, "", str(exc)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return True, resp.read().decode("utf-8", errors="replace"), ""
        except Exception as exc:
            return False, "", str(exc)

    def download_file(self, url: str, target: Path) -> tuple[bool, str]:
        target.parent.mkdir(parents=True, exist_ok=True)
        if self.fetcher and hasattr(self.fetcher, "download_file"):
            try:
                result = self.fetcher.download_file(url, str(target))
                ok = bool(result) if result is not None else target.exists()
                return ok, "" if ok else "download_file returned false"
            except Exception as exc:
                return False, str(exc)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=45) as resp:
                target.write_bytes(resp.read())
            return target.exists() and target.stat().st_size > 0, ""
        except Exception as exc:
            return False, str(exc)

    def save_raw_html(self, html: str, target: Path) -> str:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html or "", encoding="utf-8")
        return posix_rel(target, self.crawler_root)
