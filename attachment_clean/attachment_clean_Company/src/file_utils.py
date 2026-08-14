"""批量扫描、文件指纹和安全脱敏工具。"""
from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


MIME_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".txt": "text/plain",
    ".rtf": "application/rtf",
}

OOXML_REQUIRED_PARTS: dict[str, tuple[str, ...]] = {
    ".docx": ("[Content_Types].xml", "word/document.xml"),
    ".xlsx": ("[Content_Types].xml", "xl/workbook.xml"),
    ".pptx": ("[Content_Types].xml", "ppt/presentation.xml"),
}
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
PDF_MAGIC = b"%PDF-"
HTML_START = re.compile(r"(?is)^\s*(?:<!doctype\s+html\b|<html\b|<head\b|<body\b)")
PDF_ENCRYPT_REFERENCE = re.compile(rb"/Encrypt\s+\d+\s+\d+\s+R\b")


@dataclass(frozen=True)
class FileValidation:
    """提交前的只读文件检查结果。"""

    valid: bool
    code: str
    message: str
    detected_type: str
    extension: str
    size_bytes: int
    needs_manual_review: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _validation(
    path: Path,
    *,
    valid: bool,
    code: str,
    message: str,
    detected_type: str,
    size_bytes: int,
    needs_manual_review: bool = False,
) -> FileValidation:
    return FileValidation(
        valid=valid,
        code=code,
        message=message,
        detected_type=detected_type,
        extension=path.suffix.lower(),
        size_bytes=size_bytes,
        needs_manual_review=needs_manual_review,
    )


def _decoded_probe(value: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            return value.decode(encoding)
        except UnicodeDecodeError:
            continue
    return value.decode("latin-1", errors="ignore")


def _looks_like_html(head: bytes) -> bool:
    text = _decoded_probe(head).replace("\x00", "")
    return bool(HTML_START.match(text))


def _validate_ooxml(path: Path, size: int, extension: str) -> FileValidation:
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            missing = [name for name in OOXML_REQUIRED_PARTS[extension] if name not in names]
            if missing:
                return _validation(
                    path,
                    valid=False,
                    code="ooxml_structure_mismatch",
                    message=f"{extension} 缺少必要组件：{', '.join(missing)}",
                    detected_type="zip",
                    size_bytes=size,
                )
            encrypted = [item.filename for item in archive.infolist() if item.flag_bits & 0x1]
            if encrypted:
                return _validation(
                    path,
                    valid=False,
                    code="encrypted_ooxml",
                    message=f"{extension} 包含加密组件，解析服务无法直接读取",
                    detected_type=extension.lstrip("."),
                    size_bytes=size,
                )
            broken_member = archive.testzip()
            if broken_member:
                return _validation(
                    path,
                    valid=False,
                    code="corrupt_ooxml",
                    message=f"{extension} ZIP CRC 校验失败：{broken_member}",
                    detected_type=extension.lstrip("."),
                    size_bytes=size,
                )
    except (OSError, zipfile.BadZipFile, RuntimeError, ValueError) as exc:
        return _validation(
            path,
            valid=False,
            code="corrupt_ooxml",
            message=f"{extension} 不是可读取的 Office Open XML 文件：{type(exc).__name__}",
            detected_type="corrupt_zip",
            size_bytes=size,
        )
    return _validation(
        path,
        valid=True,
        code="ok",
        message="Office Open XML 结构和 CRC 校验通过",
        detected_type=extension.lstrip("."),
        size_bytes=size,
    )


def validate_document(
    file_path: Path,
    *,
    max_size_bytes: int | None = None,
) -> FileValidation:
    """按真实内容校验支持的文档格式，不修改输入文件。"""
    path = file_path.resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    size = path.stat().st_size
    extension = path.suffix.lower()
    if size <= 0:
        return _validation(
            path,
            valid=False,
            code="empty_file",
            message="文件大小为 0",
            detected_type="empty",
            size_bytes=size,
        )
    if max_size_bytes is not None and size > max_size_bytes:
        return _validation(
            path,
            valid=False,
            code="file_too_large",
            message=f"文件大小 {size} 字节超过客户端上限 {max_size_bytes} 字节",
            detected_type="not_checked",
            size_bytes=size,
        )

    with path.open("rb") as handle:
        head = handle.read(16 * 1024)
        if size > 64 * 1024:
            handle.seek(max(0, size - 64 * 1024))
        else:
            handle.seek(0)
        tail = handle.read(64 * 1024)
    stripped = head.lstrip(b"\xef\xbb\xbf\xff\xfe\x00\t\r\n ")

    if _looks_like_html(head):
        return _validation(
            path,
            valid=False,
            code="html_disguised_as_document",
            message="文件实际内容是 HTML 页面，不是真实文档",
            detected_type="html",
            size_bytes=size,
        )

    if extension == ".pdf":
        if PDF_MAGIC not in head[:1024]:
            return _validation(
                path,
                valid=False,
                code="content_type_mismatch",
                message="PDF 扩展名与文件头不匹配",
                detected_type="zip" if zipfile.is_zipfile(path) else "unknown",
                size_bytes=size,
            )
        if b"%%EOF" not in tail:
            return _validation(
                path,
                valid=False,
                code="truncated_pdf",
                message="PDF 尾部缺少 %%EOF，文件可能截断",
                detected_type="pdf",
                size_bytes=size,
            )
        if PDF_ENCRYPT_REFERENCE.search(tail):
            return _validation(
                path,
                valid=False,
                code="encrypted_pdf",
                message="PDF 包含加密字典，解析服务无法直接读取",
                detected_type="pdf",
                size_bytes=size,
            )
        return _validation(
            path,
            valid=True,
            code="ok",
            message="PDF 文件头和结束标记校验通过",
            detected_type="pdf",
            size_bytes=size,
        )

    if extension in OOXML_REQUIRED_PARTS:
        if not zipfile.is_zipfile(path):
            detected = "ole_compound" if head.startswith(OLE_MAGIC) else "unknown"
            return _validation(
                path,
                valid=False,
                code="content_type_mismatch",
                message=f"{extension} 不是 ZIP/Office Open XML 容器",
                detected_type=detected,
                size_bytes=size,
            )
        return _validate_ooxml(path, size, extension)

    if extension in {".doc", ".xls", ".ppt"}:
        if not head.startswith(OLE_MAGIC):
            return _validation(
                path,
                valid=False,
                code="content_type_mismatch",
                message=f"{extension} 不是 OLE 复合文档",
                detected_type="pdf" if PDF_MAGIC in head[:1024] else "unknown",
                size_bytes=size,
            )
        return _validation(
            path,
            valid=True,
            code="legacy_office_manual_review",
            message="旧版 OLE Office 容器有效，但需确认服务端兼容性",
            detected_type="ole_compound",
            size_bytes=size,
            needs_manual_review=True,
        )

    if extension == ".rtf":
        if not stripped.lower().startswith(b"{\\rtf"):
            return _validation(
                path,
                valid=False,
                code="content_type_mismatch",
                message="RTF 扩展名与文件头不匹配",
                detected_type="unknown",
                size_bytes=size,
            )
        return _validation(
            path,
            valid=True,
            code="ok",
            message="RTF 文件头校验通过",
            detected_type="rtf",
            size_bytes=size,
        )

    if extension == ".txt":
        text = _decoded_probe(head).replace("\x00", "").lstrip("\ufeff")
        if not text.strip():
            return _validation(
                path,
                valid=False,
                code="blank_text",
                message="文本文件只包含空白、BOM 或 NUL",
                detected_type="text",
                size_bytes=size,
            )
        if head.startswith((OLE_MAGIC, b"PK\x03\x04")) or PDF_MAGIC in head[:1024]:
            return _validation(
                path,
                valid=False,
                code="content_type_mismatch",
                message="TXT 扩展名与二进制文件内容不匹配",
                detected_type="binary",
                size_bytes=size,
            )
        return _validation(
            path,
            valid=True,
            code="ok",
            message="文本文件包含有效内容",
            detected_type="text",
            size_bytes=size,
        )

    return _validation(
        path,
        valid=False,
        code="unsupported_extension",
        message=f"不支持的文件类型：{extension or '<none>'}",
        detected_type="unknown",
        size_bytes=size,
    )


def encode_file_base64(path: Path, chunk_size: int = 3 * 1024 * 1024) -> str:
    """分块编码，避免同时在内存中持有整份原始文件和 Base64 副本。"""
    if chunk_size <= 0 or chunk_size % 3:
        raise ValueError("chunk_size 必须是 3 的正整数倍")
    output = io.StringIO()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            output.write(base64.b64encode(chunk).decode("ascii"))
    return output.getvalue()


def normalize_extensions(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        for item in str(value).split(","):
            item = item.strip().lower()
            if not item:
                continue
            extension = item if item.startswith(".") else f".{item}"
            if extension not in result:
                result.append(extension)
    return tuple(result)


def scan_files(
    input_dir: Path,
    recursive: bool,
    extensions: Iterable[str],
    issues: list[dict[str, str]] | None = None,
) -> list[Path]:
    root = input_dir.resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"input目录不存在：{root}")
    allowed = set(normalize_extensions(extensions))

    files: list[Path] = []

    def record(path: Path, issue_type: str, message: str) -> None:
        if issues is not None:
            try:
                relative = path.relative_to(root).as_posix()
            except ValueError:
                relative = str(path)
            issues.append({
                "path": relative,
                "issue_type": issue_type,
                "message": message[:1000],
            })

    def visit(directory: Path) -> None:
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name.casefold())
        except OSError as exc:
            record(directory, "directory_access_error", f"{type(exc).__name__}: {exc}")
            return
        for entry in entries:
            path = Path(entry.path)
            try:
                if entry.is_symlink():
                    record(path, "symlink_skipped", "符号链接未跟随")
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if recursive:
                        visit(path)
                    continue
                if entry.is_file(follow_symlinks=False):
                    if not allowed or path.suffix.lower() in allowed:
                        files.append(path)
            except OSError as exc:
                record(path, "path_access_error", f"{type(exc).__name__}: {exc}")

    visit(root)
    return sorted(files, key=lambda path: path.relative_to(root).as_posix().casefold())


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def mask_url_token(url: str) -> str:
    if not url:
        return url
    parts = urlsplit(url)
    query: list[tuple[str, str]] = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() == "token" and value:
            masked = value[:4] + "****" + value[-4:] if len(value) > 8 else "****"
            query.append((key, masked))
        else:
            query.append((key, value))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query, safe="*"), parts.fragment))


def redact_text(text: str, secrets: Iterable[str] = ()) -> str:
    result = re.sub(r"([?&]token=)[^\s&]+", r"\1****", str(text), flags=re.IGNORECASE)
    for secret in secrets:
        if secret:
            result = result.replace(secret, "****")
    return result


def redact_value(value: Any, secrets: Iterable[str] = ()) -> Any:
    if isinstance(value, dict):
        return {key: redact_value(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_value(item, secrets) for item in value]
    if isinstance(value, tuple):
        return [redact_value(item, secrets) for item in value]
    if isinstance(value, str):
        if ";base64," in value:
            prefix, encoded = value.split(",", 1)
            return f"{prefix},<BASE64省略，{len(encoded)}字符>"
        return redact_text(value, secrets)
    return value
