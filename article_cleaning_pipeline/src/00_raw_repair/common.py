from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

ASSET_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".rar", ".7z", ".csv", ".txt"
}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".svg", ".bmp", ".webp"}
OK_STATUSES = {"success", "downloaded", "cached", "verified", "manual_saved"}
BAD_STATUSES = {"failed", "pending", "missing", "http_403"}


def safe_filename(value: str, max_len: int = 90) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value or "")
    value = re.sub(r"\s+", " ", value).strip(" .")
    return (value or "untitled")[:max_len]


def posix_rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except Exception:
        return path.as_posix()


def read_jsonl(path: Path):
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line_no, line in enumerate(f, 1):
            if not line.strip():
                continue
            yield line_no, json.loads(line)


def write_jsonl(path: Path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            count += 1
    return count


def write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def get_path(obj: dict, dotted: str, default=None):
    cur = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def set_path(obj: dict, dotted: str, value):
    cur = obj
    parts = dotted.split(".")
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def sha12(value: str) -> str:
    return hashlib.sha1((value or "").encode("utf-8", errors="ignore")).hexdigest()[:12]


def resolve_local_path(local_path: str, crawler_root: Path, raw_html_dir: Path | None = None, image_root_dir: Path | None = None, attachment_root_dir: Path | None = None) -> Path | None:
    if not local_path:
        return None
    p = Path(local_path)
    candidates = [p] if p.is_absolute() else []
    candidates.append(crawler_root / p)
    if raw_html_dir:
        candidates.extend([raw_html_dir / p, raw_html_dir / p.name])
    if image_root_dir:
        candidates.extend([image_root_dir / p, image_root_dir / p.name])
    if attachment_root_dir:
        candidates.extend([attachment_root_dir / p, attachment_root_dir / p.name])
    for candidate in candidates:
        try:
            if candidate.exists():
                return candidate
        except OSError:
            continue
    return None


def is_valid_html_file(path: Path | None) -> bool:
    if not path or not path.exists() or path.stat().st_size == 0:
        return False
    head = path.read_text(encoding="utf-8", errors="replace")[:5000].lower()
    return "<html" in head or "<body" in head or "<!doctype html" in head


def load_html(record: dict, raw_html_path: Path | None) -> str:
    body_html = get_path(record, "content.body_html", "") or ""
    if body_html:
        return body_html
    if raw_html_path and raw_html_path.exists():
        return raw_html_path.read_text(encoding="utf-8", errors="replace")
    return ""


def parse_img_links(html: str, page_url: str = "") -> list[dict]:
    soup = BeautifulSoup(html or "", "lxml")
    links = []
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-original") or ""
        if not src:
            continue
        links.append({
            "url": urljoin(page_url, src),
            "alt": img.get("alt", ""),
            "title": img.get("title", ""),
            "image_role": classify_image_role(src, img.get("class", [])),
        })
    return links


def parse_attachment_links(html: str, page_url: str = "") -> list[dict]:
    soup = BeautifulSoup(html or "", "lxml")
    links = []
    for a in soup.find_all("a"):
        href = a.get("href") or ""
        url = urljoin(page_url, href)
        ext = Path(urlparse(url).path).suffix.lower()
        if ext in ASSET_EXTENSIONS:
            links.append({"url": url, "name": a.get_text(" ", strip=True) or Path(urlparse(url).path).name, "file_ext": ext.lstrip("."), "file_type": ext.lstrip(".").upper()})
    return links


def classify_image_role(src: str, classes=None) -> str:
    text = f"{src} {' '.join(classes or [])}".lower()
    if "qrcode" in text or "qr" in text or "code" in text:
        return "qrcode"
    if "logo" in text:
        return "logo"
    if "icon" in text:
        return "icon"
    if "banner" in text:
        return "banner"
    return "unknown"


def default_asset_dir(record: dict, crawler_root: Path, asset_type: str) -> Path:
    root_name = "images" if asset_type == "image" else "attachments"
    site = safe_filename(get_path(record, "source.site_name", "") or "unknown_site")
    channel = safe_filename(get_path(record, "source.channel_name", "") or "unknown_channel")
    title = safe_filename(get_path(record, "title", "") or get_path(record, "doc_id", "untitled"))
    parts = [crawler_root / "data" / root_name, site, channel]
    for part in get_path(record, "crawl.asset_subdir_parts", []) or []:
        parts.append(safe_filename(str(part)))
    parts.append(title)
    path = Path(parts[0])
    for part in parts[1:]:
        path = path / part
    return path


def filename_for_url(url: str, prefix: str) -> str:
    parsed = urlparse(url or "")
    suffix = Path(parsed.path).suffix.lower()
    if not suffix:
        suffix = ".bin"
    stem = safe_filename(Path(parsed.path).stem or prefix)
    return f"{prefix}_{sha12(url)}_{stem}{suffix}"
