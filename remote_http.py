import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable


class RemoteControlHttpServer:
    def __init__(
        self,
        host: str,
        port: int,
        token: str,
        on_message: Callable[[dict], tuple[int, dict]],
        on_new_chat: Callable[[dict], tuple[int, dict]],
        on_reply_request: Callable[[dict], tuple[int, dict]],
        on_state: Callable[[], tuple[int, dict]],
    ) -> None:
        self.host = str(host or "127.0.0.1").strip() or "127.0.0.1"
        self.port = max(int(port or 0), 0)
        self.token = str(token or "").strip()
        self.on_message = on_message
        self.on_new_chat = on_new_chat
        self.on_reply_request = on_reply_request
        self.on_state = on_state
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def bound_port(self) -> int:
        server = self._server
        if server is None:
            return 0
        return int(server.server_address[1])

    def start(self) -> None:
        if self._server is not None:
            return
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, _format, *_args):
                return

            def do_GET(self):
                if not outer._is_authorized(self):
                    outer._write_json(self, 401, {"accepted": False, "error": "unauthorized"})
                    return
                if self.path != "/api/remote/state":
                    outer._write_json(self, 404, {"accepted": False, "error": "not_found"})
                    return
                status, payload = outer.on_state()
                outer._write_json(self, status, payload)

            def do_POST(self):
                if not outer._is_authorized(self):
                    outer._write_json(self, 401, {"accepted": False, "error": "unauthorized"})
                    return
                payload = outer._read_json(self)
                if payload is None:
                    outer._write_json(self, 400, {"accepted": False, "error": "invalid_json"})
                    return
                if self.path == "/api/remote/message":
                    status, body = outer.on_message(payload)
                    outer._write_json(self, status, body)
                    return
                if self.path == "/api/remote/new-chat":
                    status, body = outer.on_new_chat(payload)
                    outer._write_json(self, status, body)
                    return
                if self.path == "/api/remote/reply-request":
                    status, body = outer.on_reply_request(payload)
                    outer._write_json(self, status, body)
                    return
                outer._write_json(self, 404, {"accepted": False, "error": "not_found"})

        self._server = ThreadingHTTPServer((self.host, self.port), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        server = self._server
        if server is None:
            return
        self._server = None
        try:
            server.shutdown()
        except Exception:
            pass
        try:
            server.server_close()
        except Exception:
            pass
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2)
        self._thread = None

    def _is_authorized(self, handler: BaseHTTPRequestHandler) -> bool:
        if not self.token:
            return False
        provided = str(handler.headers.get("X-Remote-Token") or "").strip()
        return provided == self.token

    @staticmethod
    def _read_json(handler: BaseHTTPRequestHandler) -> dict | None:
        try:
            content_length = int(handler.headers.get("Content-Length") or "0")
        except Exception:
            return None
        raw = handler.rfile.read(content_length) if content_length > 0 else b"{}"
        text = RemoteControlHttpServer._decode_request_body(raw, str(handler.headers.get("Content-Type") or ""))
        if text is None:
            return None
        try:
            payload = json.loads(text)
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _decode_request_body(raw: bytes, content_type: str) -> str | None:
        if not raw:
            return "{}"
        encodings: list[str] = []
        match = re.search(r"charset=([^\s;]+)", content_type or "", flags=re.IGNORECASE)
        if match:
            encodings.append(match.group(1).strip().strip("\"'"))
        encodings.extend(["utf-8-sig", "utf-8", "utf-16", "utf-16-le", "utf-16-be", "gb18030", "gbk"])
        tried: set[str] = set()
        for encoding in encodings:
            normalized = str(encoding or "").strip().lower()
            if not normalized or normalized in tried:
                continue
            tried.add(normalized)
            try:
                return raw.decode(normalized)
            except Exception:
                continue
        return None

    @staticmethod
    def _write_json(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
