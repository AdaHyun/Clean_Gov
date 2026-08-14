"""配置读取。真实凭据只允许来自环境变量或未提交的 .env。"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .file_utils import MIME_TYPES, normalize_extensions


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    api_url: str
    query_url: str
    output_root: Path
    auth_mode: str
    parse_mode: str
    url_type: int
    output_mode: str
    timeout_seconds: int
    max_retries: int
    max_file_size_mb: int
    supported_extensions: tuple[str, ...]
    query_request_id_field: str
    callback_bind_host: str
    callback_bind_port: int
    max_callback_body_mb: int
    callback_url: str
    callback_token: str
    access_key: str
    access_key_header: str
    access_key_prefix: str
    aihub_access_key_id: str
    aihub_access_key_secret: str
    aihub_product_code: str
    connect_timeout_seconds: int = 10
    submit_timeout_seconds: int = 300
    callback_timeout_minutes: int = 120
    parser_pool_id: str = "legacy_unknown"
    service_max_in_flight: int | None = None
    submit_unknown_timeout_minutes: int = 120
    stale_submitting_minutes: int = 120

    def validate_auth(self) -> None:
        if self.auth_mode == "none":
            return
        if self.auth_mode == "access_key":
            if not self.access_key or not self.access_key_header:
                raise ValueError("access_key 模式需要 COMPANY_ACCESS_KEY 和 COMPANY_ACCESS_KEY_HEADER")
            return
        if self.auth_mode == "aihub_hmac":
            missing = []
            if not self.aihub_access_key_id:
                missing.append("AIHUB_ACCESS_KEY_ID")
            if not self.aihub_access_key_secret:
                missing.append("AIHUB_ACCESS_KEY_SECRET")
            if not self.aihub_product_code:
                missing.append("AIHUB_PRODUCT_CODE")
            if missing:
                raise ValueError("aihub_hmac 模式尚未配置：" + ", ".join(missing))
            return
        raise ValueError(f"不支持的 COMPANY_AUTH_MODE：{self.auth_mode}")


def load_settings(config_path: Path | None = None) -> Settings:
    _load_dotenv(PROJECT_ROOT / ".env.server")
    _load_dotenv(PROJECT_ROOT / ".env")
    path = config_path or PROJECT_ROOT / "config" / "parser_config.json"
    if not path.is_file():
        path = PROJECT_ROOT / "config" / "parser_config.example.json"
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    output_root = Path(raw.get("output_root", "data"))
    if not output_root.is_absolute():
        output_root = PROJECT_ROOT / output_root
    extensions = normalize_extensions(raw.get("supported_extensions", MIME_TYPES.keys()))
    service_limit = raw.get("service_max_in_flight")
    callback_timeout = int(raw.get("callback_timeout_minutes", 120))
    return Settings(
        api_url=os.getenv("COMPANY_PARSE_API_URL", str(raw["api_url"])),
        query_url=os.getenv("COMPANY_QUERY_API_URL", str(raw["query_url"])),
        output_root=output_root,
        auth_mode=os.getenv("COMPANY_AUTH_MODE", str(raw.get("auth_mode", "aihub_hmac"))).lower(),
        parse_mode=str(raw.get("parse_mode", "auto")),
        url_type=int(raw.get("url_type", 2)),
        output_mode=str(raw.get("output_mode", "markdown")),
        timeout_seconds=int(raw.get("timeout_seconds", 60)),
        max_retries=int(raw.get("max_retries", 2)),
        max_file_size_mb=int(raw.get("max_file_size_mb", 100)),
        supported_extensions=extensions,
        query_request_id_field=str(raw.get("query_request_id_field", "RequestID")),
        callback_bind_host=str(raw.get("callback_bind_host", "0.0.0.0")),
        callback_bind_port=int(raw.get("callback_bind_port", 8800)),
        max_callback_body_mb=int(raw.get("max_callback_body_mb", 200)),
        callback_url=os.getenv("COMPANY_CALLBACK_URL", str(raw.get("callback_url", ""))),
        callback_token=os.getenv("COMPANY_CALLBACK_TOKEN", ""),
        access_key=os.getenv("COMPANY_ACCESS_KEY", ""),
        access_key_header=os.getenv("COMPANY_ACCESS_KEY_HEADER", "X-Access-Key"),
        access_key_prefix=os.getenv("COMPANY_ACCESS_KEY_PREFIX", ""),
        aihub_access_key_id=os.getenv("AIHUB_ACCESS_KEY_ID", ""),
        aihub_access_key_secret=os.getenv("AIHUB_ACCESS_KEY_SECRET", ""),
        aihub_product_code=os.getenv("AIHUB_PRODUCT_CODE", ""),
        connect_timeout_seconds=int(raw.get("connect_timeout_seconds", 10)),
        submit_timeout_seconds=int(raw.get("submit_timeout_seconds", 300)),
        callback_timeout_minutes=callback_timeout,
        parser_pool_id=os.getenv(
            "COMPANY_PARSER_POOL_ID",
            str(raw.get("parser_pool_id", "legacy_unknown")),
        ),
        service_max_in_flight=(int(service_limit) if service_limit is not None else None),
        submit_unknown_timeout_minutes=int(
            raw.get("submit_unknown_timeout_minutes", callback_timeout)
        ),
        stale_submitting_minutes=int(
            raw.get("stale_submitting_minutes", callback_timeout)
        ),
    )
