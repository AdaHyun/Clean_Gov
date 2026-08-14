"""最小异步回调接收服务。仅用于内网小规模联调。"""
from __future__ import annotations

import hmac
import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .config import Settings
from .storage import save_callback


_LOG_LOCK = threading.Lock()


def _callback_log(settings: Settings, message: str) -> None:
    path = settings.output_root / "logs" / "callback.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).astimezone().isoformat()
    with _LOG_LOCK, path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"{timestamp} {message.replace(chr(10), ' ').replace(chr(13), ' ')}\n")


def make_handler(settings: Settings) -> type[BaseHTTPRequestHandler]:
    class CallbackHandler(BaseHTTPRequestHandler):
        server_version = "CompanyCallback/0.1"

        def _reply(self, status: int, value: dict[str, Any]) -> None:
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlsplit(self.path)
            if parsed.path != "/callback":
                self._reply(404, {"Code": 404, "Message": "not found"})
                return
            if settings.callback_token:
                supplied = parse_qs(parsed.query).get("token", [""])[0]
                if not hmac.compare_digest(supplied, settings.callback_token):
                    _callback_log(settings, f"callback_rejected client={self.client_address[0]} reason=invalid_token")
                    self._reply(403, {"Code": 403, "Message": "invalid callback token"})
                    return
            length_text = self.headers.get("Content-Length", "")
            if not length_text.isdigit():
                self._reply(411, {"Code": 411, "Message": "Content-Length required"})
                return
            length = int(length_text)
            if length > settings.max_callback_body_mb * 1024 * 1024:
                self._reply(413, {"Code": 413, "Message": "callback body too large"})
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                _callback_log(settings, f"callback_rejected client={self.client_address[0]} reason=invalid_json")
                self._reply(400, {"Code": 400, "Message": "invalid json"})
                return
            if not isinstance(payload, dict):
                self._reply(400, {"Code": 400, "Message": "json object required"})
                return
            request_id_value = payload.get("RequestId") or payload.get("RequestID") or payload.get("request_id")
            if not request_id_value:
                _callback_log(settings, f"callback_rejected client={self.client_address[0]} reason=missing_request_id")
                self._reply(400, {"Code": 400, "Message": "RequestId required"})
                return
            try:
                request_id, _ = save_callback(
                    settings.output_root,
                    payload,
                    (settings.callback_token, settings.aihub_access_key_secret),
                )
            except Exception as exc:
                # 只记录 RequestId 和异常类型，避免响应体、token 或密钥进入日志。
                print(f"callback save_failed request_id={request_id_value} error={type(exc).__name__}")
                _callback_log(settings, f"callback_failed request_id={request_id_value} error_type={type(exc).__name__}")
                self._reply(500, {"Code": 500, "Message": "save callback failed"})
                return
            _callback_log(settings, f"callback_saved request_id={request_id}")
            self._reply(200, {"Code": 0, "Message": "success", "RequestId": request_id})

        def log_message(self, format: str, *args: object) -> None:
            # 避免把带 token 的完整 URL 写进默认访问日志。
            print(f"callback {self.client_address[0]} {args[1] if len(args) > 1 else ''}")

    return CallbackHandler


def run_server(settings: Settings, host: str | None = None, port: int | None = None) -> None:
    address = (host or settings.callback_bind_host, port or settings.callback_bind_port)
    server = ThreadingHTTPServer(address, make_handler(settings))
    print(f"回调服务已监听 http://{address[0]}:{address[1]}/callback")
    print(f"回调结果目录：{settings.output_root / 'requests'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("回调服务已停止")
    finally:
        server.server_close()
