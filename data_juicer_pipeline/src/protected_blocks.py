"""Per-document protection of structurally or semantically risky lines."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field


TABLE_LINE_RE = re.compile(r"^\s*\|(?:[^|]*\|){2,}\s*$")
TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+\S")
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\([^\s)]+\.(?:pdf|docx?|xlsx?|pptx?|rar|zip)(?:\?[^)]*)?\)", re.I)
DOCUMENT_NUMBER_RE = re.compile(r"(?:〔|\[)\d{4}(?:〕|\])\s*\d+\s*号|第\s*\d+\s*号")
SIGNATURE_DATE_RE = re.compile(r"(?:人民政府|委员会|卫生健康|疾病预防控制|管理局).{0,20}(?:\d{4}年\d{1,2}月\d{1,2}日)?\s*$")
FOOTER_LABEL_RE = re.compile(r"^\s*(?:主办单位|承办单位|联系电话|邮编|版权所有)[：:]")
CLAUSE_RE = re.compile(r"^\s*(?:[一二三四五六七八九十]+、|（[一二三四五六七八九十]+）|\([一二三四五六七八九十]+\)|\d+[.、]|（\d+）)\s*\S")
POLICY_BASIS_RE = re.compile(r"根据《[^》]+》|依据《[^》]+》|按照《[^》]+》")
PLACEHOLDER_RE = re.compile(r"^__DJ_PROTECTED_[A-F0-9]{16}_\d{5}__$")


def is_table_line(line: str) -> bool:
    return bool(TABLE_LINE_RE.match(line) or TABLE_SEPARATOR_RE.match(line))


def markdown_table_blocks(text: str) -> list[list[str]]:
    """Return contiguous pipe-table blocks with at least two table lines."""
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in text.split("\n"):
        if is_table_line(line):
            current.append(line)
        else:
            if len(current) >= 2:
                blocks.append(current)
            current = []
    if len(current) >= 2:
        blocks.append(current)
    return blocks


@dataclass
class ProtectedEntry:
    placeholder: str
    text: str
    kind: str
    sha256: str


@dataclass
class ProtectionResult:
    text: str
    entries: list[ProtectedEntry]
    stats: dict[str, int | bool]
    original_hash: str
    token: str

    def serializable_map(self) -> dict[str, object]:
        return {
            "token": self.token,
            "original_hash": self.original_hash,
            "entries": [entry.__dict__ for entry in self.entries],
            "stats": self.stats,
        }


@dataclass
class RestoreResult:
    text: str
    success: bool
    errors: list[str] = field(default_factory=list)
    restored_hash: str = ""


def protect_text(text: str, *, doc_key: str, title: str = "", long_line_length: int = 50) -> ProtectionResult:
    """Replace protected line blocks with document-unique placeholders."""
    token = hashlib.sha256(doc_key.encode("utf-8")).hexdigest()[:16].upper()
    lines = text.split("\n")
    entries: list[ProtectedEntry] = []
    output: list[str] = []
    stats: dict[str, int | bool] = {
        "markdown_table_count": 0,
        "table_line_count": 0,
        "heading_line_count": 0,
        "title_line_count": 0,
        "long_line_count": 0,
        "attachment_link_count": 0,
        "document_number_count": 0,
        "signature_date_count": 0,
        "clause_line_count": 0,
        "policy_basis_count": 0,
        "restore_failed_count": 0,
        "hash_equal": False,
    }

    def add(block: str, kind: str) -> None:
        placeholder = f"__DJ_PROTECTED_{token}_{len(entries):05d}__"
        entries.append(ProtectedEntry(placeholder, block, kind, hashlib.sha256(block.encode("utf-8")).hexdigest()))
        output.append(placeholder)

    index = 0
    while index < len(lines):
        if is_table_line(lines[index]):
            end = index
            while end < len(lines) and is_table_line(lines[end]):
                end += 1
            block_lines = lines[index:end]
            if len(block_lines) >= 2:
                stats["markdown_table_count"] = int(stats["markdown_table_count"]) + 1
                stats["table_line_count"] = int(stats["table_line_count"]) + len(block_lines)
                add("\n".join(block_lines), "markdown_table")
                index = end
                continue
        line = lines[index]
        stripped = line.strip()
        kind = ""
        if HEADING_RE.match(line):
            kind = "heading"
            stats["heading_line_count"] = int(stats["heading_line_count"]) + 1
        elif title and stripped == title.strip():
            kind = "title"
            stats["title_line_count"] = int(stats["title_line_count"]) + 1
        elif MARKDOWN_LINK_RE.search(line):
            kind = "attachment_link"
            stats["attachment_link_count"] = int(stats["attachment_link_count"]) + 1
        elif DOCUMENT_NUMBER_RE.search(line):
            kind = "document_number"
            stats["document_number_count"] = int(stats["document_number_count"]) + 1
        elif SIGNATURE_DATE_RE.search(line) and not FOOTER_LABEL_RE.search(line):
            kind = "signature_date"
            stats["signature_date_count"] = int(stats["signature_date_count"]) + 1
        elif CLAUSE_RE.match(line):
            kind = "clause_line"
            stats["clause_line_count"] = int(stats["clause_line_count"]) + 1
        elif POLICY_BASIS_RE.search(line):
            kind = "policy_basis"
            stats["policy_basis_count"] = int(stats["policy_basis_count"]) + 1
        elif len(stripped) >= long_line_length:
            kind = "long_line"
            stats["long_line_count"] = int(stats["long_line_count"]) + 1
        if kind:
            add(line, kind)
        else:
            output.append(line)
        index += 1
    original_hash = hashlib.sha256("\n".join(entry.text for entry in entries).encode("utf-8")).hexdigest()
    return ProtectionResult("\n".join(output), entries, stats, original_hash, token)


def restore_text(text: str, protection: ProtectionResult) -> RestoreResult:
    errors: list[str] = []
    restored = text
    for entry in protection.entries:
        occurrences = restored.count(entry.placeholder)
        if occurrences != 1:
            errors.append(f"{entry.placeholder} 出现 {occurrences} 次")
            continue
        if hashlib.sha256(entry.text.encode("utf-8")).hexdigest() != entry.sha256:
            errors.append(f"{entry.placeholder} 映射哈希不一致")
            continue
        restored = restored.replace(entry.placeholder, entry.text, 1)
    if "__DJ_PROTECTED_" in restored:
        errors.append("恢复后仍有占位符残留")
    restored_hash = hashlib.sha256("\n".join(entry.text for entry in protection.entries).encode("utf-8")).hexdigest()
    if restored_hash != protection.original_hash:
        errors.append("保护内容总哈希不一致")
    protection.stats["restore_failed_count"] = 1 if errors else 0
    protection.stats["hash_equal"] = not errors
    return RestoreResult(restored, not errors, errors, restored_hash)


def protection_from_map(value: dict[str, object]) -> ProtectionResult:
    """Rebuild a protection object after a Data-Juicer JSONL round trip."""
    entries = [ProtectedEntry(**entry) for entry in value.get("entries", []) if isinstance(entry, dict)]
    stats = dict(value.get("stats", {})) if isinstance(value.get("stats"), dict) else {}
    return ProtectionResult("", entries, stats, str(value.get("original_hash", "")), str(value.get("token", "")))
