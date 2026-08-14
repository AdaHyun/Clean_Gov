"""8899 文档解析接口客户端：提交异步任务与查询任务状态。"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from .auth import build_headers
from .config import Settings
from .file_utils import (
    MIME_TYPES,
    FileValidation,
    encode_file_base64,
    mask_url_token,
    redact_text,
    validate_document,
)


class CompanyApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        request_id: str = "",
        http_status: int | None = None,
        error_type: str = "api_error",
        submission_unknown: bool = False,
    ) -> None:
        super().__init__(message)
        self.request_id = request_id
        self.http_status = http_status
        self.error_type = error_type
        self.submission_unknown = submission_unknown


@dataclass(frozen=True)
class ApiResult:
    request_id: str
    body: dict[str, Any]
    http_status: int
    retry_count: int


def append_callback_url(api_url: str, callback_url: str) -> str:
    if not callback_url:
        return api_url
    parts = urlsplit(api_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["CallbackUrl"] = callback_url
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


class CompanyAsyncClient:
    def __init__(
        self,
        settings: Settings,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self.session = session or requests.Session()
        self.sleep = sleep

    def build_payload(
        self,
        file_path: Path,
        request_id: str | None = None,
        validation: FileValidation | None = None,
    ) -> tuple[str, dict[str, Any]]:
        path = file_path.resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        extension = path.suffix.lower()
        if extension not in self.settings.supported_extensions:
            raise ValueError(f"不支持的文件类型：{extension}")
        size = path.stat().st_size
        if size <= 0:
            raise ValueError("文件大小为 0")
        if size > self.settings.max_file_size_mb * 1024 * 1024:
            raise ValueError(f"文件超过 {self.settings.max_file_size_mb} MB")
        checked = validation or validate_document(
            path,
            max_size_bytes=self.settings.max_file_size_mb * 1024 * 1024,
        )
        if checked.size_bytes != size or checked.extension != extension:
            raise ValueError("文件在预检后发生变化，请重新扫描后提交")
        if not checked.valid:
            raise ValueError(f"文件预检失败 [{checked.code}]：{checked.message}")
        if self.settings.url_type != 2:
            raise ValueError("初版只实现 UrlType=2（Base64）；URL/文件流待接口确认后补充")
        rid = request_id or str(uuid.uuid4())
        mime = MIME_TYPES.get(extension)
        if not mime:
            raise ValueError(f"尚未配置文件类型的 MIME：{extension}")
        encoded = encode_file_base64(path)
        payload: dict[str, Any] = {
            "RequestId": rid,
            "UrlType": 2,
            "FileUrl": f"data:{mime};base64,{encoded}",
            "FileType": extension.lstrip("."),
            "FileName": path.name,
            "ParseMode": self.settings.parse_mode,
        }
        if self.settings.output_mode:
            payload["OutputMode"] = self.settings.output_mode
        return rid, payload

    @staticmethod
    def payload_summary(payload: dict[str, Any]) -> dict[str, Any]:
        summary = dict(payload)
        file_url = str(summary.get("FileUrl", ""))
        if ";base64," in file_url:
            prefix, encoded = file_url.split(",", 1)
            summary["FileUrl"] = f"{prefix},<BASE64省略，{len(encoded)}字符>"
        return summary

    def _post(
        self,
        url: str,
        request_id: str,
        payload: dict[str, Any],
        *,
        read_timeout_seconds: float | None = None,
        retry_on_read_timeout: bool = True,
    ) -> ApiResult:
        headers = build_headers(self.settings, request_id)
        safe_url = mask_url_token(url)

        # 连接超时和读取超时分开设置：
        # - 连接服务器通常不应超过 10 秒；
        # - 读取超时由具体接口决定，提交大文件时可以更长。
        connect_timeout = float(self.settings.connect_timeout_seconds)
        read_timeout = float(
            read_timeout_seconds
            if read_timeout_seconds is not None
            else self.settings.timeout_seconds
        )
        timeout = (connect_timeout, read_timeout)

        retry_count = 0
        while True:
            try:
                response = self.session.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=timeout,
                )

            except requests.ConnectTimeout as exc:
                if retry_count >= self.settings.max_retries:
                    raise CompanyApiError(
                        f"连接接口超时：url={safe_url}，request_id={request_id}，"
                        f"connect_timeout={connect_timeout}s",
                        request_id,
                        error_type="connect_timeout",
                    ) from exc
                retry_count += 1
                self.sleep(2**retry_count)
                continue

            except requests.ReadTimeout as exc:
                # 异步任务提交发生读取超时时，服务端可能已经接收了请求。
                # 此时不能直接重试，否则可能重复创建任务。
                if not retry_on_read_timeout:
                    raise CompanyApiError(
                        f"接口读取超时：服务器在 {read_timeout:g} 秒内未返回响应。"
                        f"服务端可能已经接收任务，请先用 request_id 查询状态，"
                        f"不要立即重复提交。url={safe_url}，request_id={request_id}",
                        request_id,
                        error_type="read_timeout_unknown",
                        submission_unknown=True,
                    ) from exc

                if retry_count >= self.settings.max_retries:
                    raise CompanyApiError(
                        f"接口读取超时：url={safe_url}，request_id={request_id}，"
                        f"read_timeout={read_timeout:g}s",
                        request_id,
                        error_type="read_timeout",
                    ) from exc
                retry_count += 1
                self.sleep(2**retry_count)
                continue

            except requests.ConnectionError as exc:
                # 连接建立后断开时，请求体可能已发送；提交接口禁止盲目重试。
                if not retry_on_read_timeout:
                    raise CompanyApiError(
                        f"提交连接中断，服务端是否接收任务未知：url={safe_url}，"
                        f"request_id={request_id}，错误="
                        f"{redact_text(str(exc), (self.settings.callback_token,))}",
                        request_id,
                        error_type="connection_error_unknown",
                        submission_unknown=True,
                    ) from exc
                if retry_count >= self.settings.max_retries:
                    raise CompanyApiError(
                        f"接口连接失败：url={safe_url}，request_id={request_id}，错误="
                        f"{redact_text(str(exc), (self.settings.callback_token,))}",
                        request_id,
                        error_type="connection_error",
                    ) from exc
                retry_count += 1
                self.sleep(2**retry_count)
                continue

            except requests.RequestException as exc:
                raise CompanyApiError(
                    f"网络请求异常：url={safe_url}，request_id={request_id}，错误="
                    f"{redact_text(str(exc), (self.settings.callback_token,))}",
                    request_id,
                    error_type=(
                        "request_error_unknown" if not retry_on_read_timeout else "request_error"
                    ),
                    submission_unknown=not retry_on_read_timeout,
                ) from exc

            if response.status_code >= 500 and not retry_on_read_timeout:
                raise CompanyApiError(
                    f"提交接口返回 HTTP {response.status_code}；服务端是否已创建任务未知："
                    f"{redact_text(response.text[:500], (self.settings.callback_token,))}",
                    request_id,
                    response.status_code,
                    error_type="http_5xx_unknown",
                    submission_unknown=True,
                )
            if response.status_code >= 500 and retry_count < self.settings.max_retries:
                retry_count += 1
                self.sleep(2**retry_count)
                continue

            if response.status_code >= 400:
                raise CompanyApiError(
                    f"接口返回 HTTP {response.status_code}："
                    f"{redact_text(response.text[:500], (self.settings.callback_token,))}",
                    request_id,
                    response.status_code,
                    error_type="http_4xx" if response.status_code < 500 else "http_5xx",
                )

            try:
                body = response.json()
            except ValueError as exc:
                raise CompanyApiError(
                    f"接口返回内容不是有效 JSON："
                    f"{redact_text(response.text[:500], (self.settings.callback_token,))}",
                    request_id,
                    response.status_code,
                    error_type=(
                        "invalid_json_unknown" if not retry_on_read_timeout else "invalid_json"
                    ),
                    submission_unknown=not retry_on_read_timeout,
                ) from exc

            if not isinstance(body, dict):
                raise CompanyApiError(
                    "接口返回 JSON 顶层不是对象",
                    request_id,
                    response.status_code,
                    error_type=(
                        "invalid_response_unknown"
                        if not retry_on_read_timeout
                        else "invalid_response"
                    ),
                    submission_unknown=not retry_on_read_timeout,
                )

            return ApiResult(request_id, body, response.status_code, retry_count)

    def submit_file(
        self,
        file_path: Path,
        callback_url: str | None = None,
        request_id: str | None = None,
        validation: FileValidation | None = None,
    ) -> tuple[ApiResult, dict[str, Any]]:
        request_id, payload = self.build_payload(file_path, request_id, validation)
        url = append_callback_url(
            self.settings.api_url,
            callback_url or self.settings.callback_url,
        )

        # 默认把提交接口读取超时提高到至少 300 秒。
        # 可在 Settings 中新增 submit_timeout_seconds 单独配置。
        submit_timeout = float(max(self.settings.submit_timeout_seconds, self.settings.timeout_seconds))

        # 提交任务发生读取超时后不自动重试，避免重复提交。
        return (
            self._post(
                url,
                request_id,
                payload,
                read_timeout_seconds=submit_timeout,
                retry_on_read_timeout=False,
            ),
            payload,
        )

    def query(self, request_id: str) -> ApiResult:
        payload = {self.settings.query_request_id_field: request_id}
        return self._post(self.settings.query_url, request_id, payload)
