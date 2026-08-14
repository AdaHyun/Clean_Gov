from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from src.config import Settings


def minimal_pdf(label: str = "test") -> bytes:
    return (
        b"%PDF-1.4\n"
        + f"% {label}\n".encode("utf-8")
        + b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"
        + b"trailer\n<< /Root 1 0 R >>\n%%EOF\n"
    )


def write_ooxml(path: Path) -> None:
    required = {
        ".docx": "word/document.xml",
        ".xlsx": "xl/workbook.xml",
        ".pptx": "ppt/presentation.xml",
    }[path.suffix.lower()]
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        archive.writestr(required, '<?xml version="1.0"?><root/>')


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = dict(
        api_url="http://10.62.32.53:8899/PDF-Parser",
        query_url="http://10.62.32.53:8899/Query",
        output_root=tmp_path / "data",
        auth_mode="none",
        parse_mode="auto",
        url_type=2,
        output_mode="markdown",
        timeout_seconds=3,
        max_retries=0,
        max_file_size_mb=100,
        supported_extensions=(".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".txt", ".rtf"),
        query_request_id_field="RequestID",
        callback_bind_host="127.0.0.1",
        callback_bind_port=8800,
        max_callback_body_mb=10,
        callback_url="http://127.0.0.1:8800/callback?token=test-token",
        callback_token="test-token",
        access_key="",
        access_key_header="X-Access-Key",
        access_key_prefix="",
        aihub_access_key_id="",
        aihub_access_key_secret="",
        aihub_product_code="",
    )
    values.update(overrides)
    return Settings(**values)
