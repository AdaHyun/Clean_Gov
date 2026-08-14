"""将输入相对路径安全映射到可读结果目录。"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path, PurePosixPath


_DANGEROUS = re.compile(r'[<>:"/\\|?*]')
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def sanitize_component(value: str, max_length: int = 100) -> str:
    original = unicodedata.normalize("NFKC", str(value))
    cleaned = _DANGEROUS.sub("_", original)
    cleaned = _CONTROL.sub("", cleaned)
    cleaned = re.sub(r"\s+", "_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip(" ._")
    if not cleaned or cleaned in {".", ".."}:
        cleaned = "untitled"
    if len(cleaned) > max_length:
        suffix = hashlib.sha256(original.encode("utf-8")).hexdigest()[:10]
        cleaned = f"{cleaned[:max_length - 12].rstrip(' ._')}__{suffix}"
    return cleaned


def mirror_component(value: str, max_length: int = 150) -> str:
    """尽量保留原目录名，仅替换跨平台不安全字符。"""
    original = unicodedata.normalize("NFC", str(value))
    cleaned = _DANGEROUS.sub("_", original)
    cleaned = _CONTROL.sub("", cleaned).rstrip(" .")
    if not cleaned or cleaned in {".", ".."}:
        cleaned = "untitled"
    if len(cleaned) > max_length:
        suffix = hashlib.sha256(original.encode("utf-8")).hexdigest()[:10]
        cleaned = f"{cleaned[:max_length - 12].rstrip(' .')}__{suffix}"
    return cleaned


def safe_relative_parts(
    relative_path: str,
    *,
    preserve_names: bool = False,
) -> tuple[str, ...]:
    normalized = str(relative_path).replace("\\", "/")
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError(f"不安全的输入相对路径：{relative_path}")
    cleaner = mirror_component if preserve_names else sanitize_component
    return tuple(cleaner(part) for part in pure.parts)


def mirror_document_dir(
    documents_root: Path,
    source_relative_path: str,
    request_id: str,
    layout: str,
) -> Path | None:
    if layout == "request":
        return None
    if layout not in {"readable", "mirror"}:
        raise ValueError(f"不支持的输出布局：{layout}")
    parts = safe_relative_parts(
        source_relative_path,
        preserve_names=layout == "mirror",
    )
    if layout == "readable":
        stem = sanitize_component(Path(parts[-1]).stem)
        directory_name = f"{stem}__{request_id[:12]}"
        candidate = documents_root / directory_name
    else:
        candidate = documents_root.joinpath(*parts[:-1], parts[-1])
    root = documents_root.resolve()
    resolved = candidate.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError("镜像输出路径逃离 documents_root")
    return candidate
