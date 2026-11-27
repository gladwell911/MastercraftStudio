"""Authenticated, machine-local transport for the bundled terminal client."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import socket
import socketserver
import struct
import threading
from pathlib import Path

from remote_machine_config import _dpapi_protect, _dpapi_unprotect, _write_json_atomic

PROTOCOL_VERSION = 1
LOOPBACK = "127.0.0.1"
MAX_REQUEST_BYTES = 256 * 1024
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
NONCE_BYTES = 32


class TerminalError(RuntimeError):
    pass


class TerminalUnavailable(TerminalError):
    def __init__(self, message: str, *, verified: bool = False):
        super().__init__(message)
        self.verified = verified


class TerminalOutcomeUnknown(TerminalError):
    pass


def _read(stream, length: int) -> bytes:
    data = stream.read(length)
    if len(data) != length:
        raise EOFError
    return data


def _load_frame(stream, limit: int, key: bytes, direction: bytes) -> dict:
    length, = struct.unpack("!I", _read(stream, 4))
    if not 0 < length <= limit:
        raise ValueError("invalid frame length")
    content = _read(stream, length)
    if not hmac.compare_digest(_read(stream, NONCE_BYTES), hmac.digest(key, direction + content, "sha256")):
        raise ValueError("invalid frame signature")
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError("invalid frame")
    return data


def _send_frame(sock, data: dict, limit: int, key: bytes, direction: bytes) -> None:
    encoded = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if not 0 < len(encoded) <= limit:
        raise ValueError("invalid frame length")
    tag = hmac.digest(key, direction + encoded, "sha256")
    sock.sendall(struct.pack("!I", len(encoded)) + encoded + tag)


def _proof(key: bytes, role: bytes, client_nonce: bytes, server_nonce: bytes) -> bytes:
    return hmac.digest(key, b"MC shared terminal\0" + role + client_nonce + server_nonce, "sha256")


class _TerminalHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        self.request.settimeout(2)
        try:
            key = self.server.secret
            client_nonce = _read(self.rfile, NONCE_BYTES)
            server_nonce = secrets.token_bytes(NONCE_BYTES)
            self.request.sendall(server_nonce + _proof(key, b"server", client_nonce, server_nonce))
            if not hmac.compare_digest(_read(self.rfile, NONCE_BYTES), _proof(key, b"client", client_nonce, server_nonce)):
                return
            self.request.settimeout(6)
            message_key = _proof(key, b"messages", client_nonce, server_nonce)
            request = _load_frame(self.rfile, MAX_REQUEST_BYTES, message_key, b"request")
            if type(request.get("version")) is not int or request["version"] != PROTOCOL_VERSION:
                status, body = 426, {"error": "protocol_incompatible", "version": PROTOCOL_VERSION}
            elif not isinstance(request.get("operation"), str) or not isinstance(request.get("payload"), dict):
                status, body = 400, {"error": "invalid_request"}
            else:
                try:
                    status, body = self.server.dispatch(request["operation"], request["payload"])
                except Exception:
                    status, body = 500, {"error": "local_operation_failed"}
            try:
                _send_frame(self.request, {"status": status, "body": body}, MAX_RESPONSE_BYTES, message_key, b"response")
            except ValueError:
                _send_frame(
                    self.request, {"status": 413, "body": {"error": "response_too_large"}},
                    MAX_RESPONSE_BYTES, message_key, b"response",
                )
        except (OSError, EOFError, ValueError):
            return


class _TerminalListener(socketserver.ThreadingTCPServer):
    daemon_threads = True

    def __init__(self, secret, dispatch):
        self.secret = secret
        self.dispatch = dispatch
        self.slots = threading.BoundedSemaphore(16)
        super().__init__((LOOPBACK, 0), _TerminalHandler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class TerminalServer:
    def __init__(self, endpoint: Path, dispatch):
        self.endpoint = endpoint
        self.dispatch = dispatch
        self.listener = None
        self.thread = None
        self.instance = secrets.token_hex(16)

    def __enter__(self):
        secret = secrets.token_bytes(32)
        protected = _dpapi_protect(secret)
        if protected is None:
            raise TerminalError("Windows 无法保护 MC 本机终端凭据。请以当前用户重新启动 MC。")
        self.listener = _TerminalListener(secret, self.dispatch)
        try:
            self.thread = threading.Thread(
                target=self.listener.serve_forever, kwargs={"poll_interval": 0.1},
                name="mc-shared-terminal", daemon=True,
            )
            self.thread.start()
            _write_json_atomic(self.endpoint, {
                "instance": self.instance,
                "port": self.listener.server_address[1],
                "credential": base64.b64encode(protected).decode("ascii"),
            })
        except BaseException:
            self.close()
            raise
        return self

    def close(self):
        if self.listener is None:
            return
        if self.thread is not None and self.thread.is_alive():
            self.listener.shutdown()
        self.listener.server_close()
        self.listener = None
        try:
            body = json.loads(self.endpoint.read_text(encoding="utf-8"))
            if body.get("instance") == self.instance:
                self.endpoint.unlink(missing_ok=True)
        except (OSError, ValueError, AttributeError):
            pass

    def __exit__(self, *_):
        self.close()


class TerminalClient:
    def __init__(self, port: int, secret: bytes):
        self.port = port
        self.secret = secret

    @classmethod
    def from_endpoint(cls, endpoint: Path):
        try:
            data = json.loads(endpoint.read_text(encoding="utf-8"))
            port = data["port"]
            credential = base64.b64decode(data["credential"], validate=True)
            if type(port) is not int or not 0 < port <= 65535:
                raise ValueError("invalid port")
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise TerminalUnavailable("MC 本机服务尚未发布连接信息。") from exc
        secret = _dpapi_unprotect(credential)
        if secret is None or len(secret) != 32:
            raise TerminalError("无法读取当前 Windows 用户的 MC 本机终端凭据。请以当前用户重新启动 MC。")
        return cls(port, secret)

    def request(self, operation: str, payload: dict, *, version=PROTOCOL_VERSION, timeout=8.0) -> tuple[int, dict]:
        submitted = verified = False
        try:
            with socket.create_connection((LOOPBACK, self.port), timeout=min(timeout, 2)) as sock, sock.makefile("rb") as stream:
                client_nonce = secrets.token_bytes(NONCE_BYTES)
                sock.sendall(client_nonce)
                server_nonce = _read(stream, NONCE_BYTES)
                if not hmac.compare_digest(
                    _read(stream, NONCE_BYTES), _proof(self.secret, b"server", client_nonce, server_nonce),
                ):
                    raise TerminalUnavailable("无法验证 MC 本机服务，请重新启动所需的 MC 安装。")
                verified = True
                sock.sendall(_proof(self.secret, b"client", client_nonce, server_nonce))
                sock.settimeout(timeout)
                submitted = operation == "send"
                message_key = _proof(self.secret, b"messages", client_nonce, server_nonce)
                _send_frame(
                    sock, {"version": version, "operation": operation, "payload": payload},
                    MAX_REQUEST_BYTES, message_key, b"request",
                )
                response = _load_frame(stream, MAX_RESPONSE_BYTES, message_key, b"response")
                status = response.get("status")
                body = response.get("body")
                if type(status) is not int or not isinstance(body, dict):
                    raise ValueError("invalid response")
        except (OSError, EOFError, ValueError) as exc:
            if submitted:
                raise TerminalOutcomeUnknown("提交结果未知，正在只读检查该请求。") from exc
            raise TerminalUnavailable("MC 本机服务未就绪或连接已关闭。", verified=verified) from exc
        if status == 426:
            raise TerminalError("终端命令与 MC 的本机协议不兼容。请退出旧 MC 并运行新版安装后重试。")
        return status, body
