"""可切换的接口鉴权头生成器。"""
from __future__ import annotations

import hashlib
import hmac
from datetime import datetime

from .config import Settings


def build_headers(settings: Settings, request_id: str, now: datetime | None = None) -> dict[str, str]:
    settings.validate_auth()
    headers = {"Content-Type": "application/json"}
    if settings.auth_mode == "none":
        return headers
    if settings.auth_mode == "access_key":
        value = settings.access_key
        if settings.access_key_prefix:
            value = f"{settings.access_key_prefix} {value}"
        headers[settings.access_key_header] = value
        return headers

    request_time = (now or datetime.now()).strftime("%Y%m%d%H%M%S")
    signing_text = f"{request_id}:{request_time}"
    signature = hmac.new(
        settings.aihub_access_key_secret.encode("utf-8"),
        signing_text.encode("utf-8"),
        hashlib.sha1,
    ).hexdigest()
    headers.update({
        "X-Aihub-Access-Key": settings.aihub_access_key_id,
        "X-Aihub-Request-Id": request_id,
        "X-Aihub-Request-Time": request_time,
        "X-Aihub-Signature": signature,
        "X-Aihub-Product-Code": settings.aihub_product_code,
    })
    return headers

