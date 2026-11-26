"""Authenticated loopback transport for Codex's native remote TUI."""
from __future__ import annotations

import base64
import hashlib
import json
import platform
import re
import secrets
import subprocess
import threading
from pathlib import Path

import websocket

from codex_client import (
    CodexAppServerClient, CodexEvent, _link_user_codex_directories, _user_home, _windows_popen_kwargs,
    build_codex_app_server_command, resolve_codex_launch_command,
)
from remote_machine_config import _dpapi_protect

TOKEN_ENV = "MC_CODEX_REMOTE_TOKEN"
_LISTENING = re.compile(r"\s*listening on: (ws://127\.0\.0\.1:[1-9][0-9]{0,4})\s*")


def native_codex_executable() -> str:
    command = resolve_codex_launch_command()
    path = Path(command[-1])
    if path.suffix.lower() == ".exe" and path.is_file():
        return str(path)
    # The official npm cmd/PowerShell shims have the native executable beside the package.
    # Use the executable so a positional TUI prompt is never parsed by a command shell.
    if path.name.lower() in {"codex.cmd", "codex.ps1"}:
        arch = "arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "x64"
        triplet = "aarch64" if arch == "arm64" else "x86_64"
        scope = path.parent / "node_modules" / "@openai"
        for package_root in (scope / "codex" / "node_modules" / "@openai", scope):
            exe = package_root / f"codex-win32-{arch}" / "vendor" / f"{triplet}-pc-windows-msvc" / "bin" / "codex.exe"
            if exe.is_file():
                return str(exe)
    raise RuntimeError("找不到 Codex 原生可执行文件。请安装支持远端 TUI 的官方 Codex，或将 CODEX_BIN 指向 codex.exe。")


class CodexTerminalClient(CodexAppServerClient):
    def __init__(self, *, native_home=None, **kwargs):
        super().__init__(**kwargs)
        self._codex_home_dir = native_home
        self._socket = None
        self._endpoint = None
        self._executable = None
        self._token = secrets.token_urlsafe(32)
        self._transport_lock = threading.RLock()
        self._listener_ready = threading.Event()
        self._transport_error = "Codex 原生终端连接已断开。请重新打开该聊天检查当前状态。"

    def terminal_endpoint(self):
        protected = _dpapi_protect(self._token.encode("ascii"))
        if protected is None:
            raise RuntimeError("Windows 无法保护 Codex 本机终端凭据。")
        self._ensure_started()
        return {"url": self._endpoint, "executable": self._executable,
                "credential": base64.b64encode(protected).decode("ascii")}

    def prepare_task(self, cwd=None):
        # Updating plugin configuration restarts a stdio process. A terminal TUI
        # can stay connected here while a new turn refreshes native skill discovery.
        self._ensure_started()
        if self._codex_home_dir != _user_home() / ".codex":
            _link_user_codex_directories(_user_home() / ".codex", self._codex_home_dir)
        self._refresh_native_skills(cwd)

    def _ensure_started(self):
        with self._transport_lock:
            if self._closed:
                raise RuntimeError("Codex 原生终端运行时已关闭。")
            if self._socket is not None and self._initialized:
                return
            if self._proc is None or self._proc.poll() is not None:
                self._listener_ready.clear()
                self._endpoint = None
                argv = build_codex_app_server_command(codex_model=self.codex_model)
                self._executable = native_codex_executable()
                argv = [self._executable, *argv[argv.index("app-server"):]]
                argv[argv.index("--listen") + 1] = "ws://127.0.0.1:0"
                argv.extend(("--ws-auth", "capability-token", "--ws-token-sha256",
                             hashlib.sha256(self._token.encode("ascii")).hexdigest()))
                self._proc = subprocess.Popen(
                    argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", errors="replace", env=self._build_launch_env(),
                    **_windows_popen_kwargs(),
                )
                self._stderr_thread = threading.Thread(target=self._read_listener, args=(self._proc,), daemon=True)
                self._stderr_thread.start()
                if not self._listener_ready.wait(6) or self._endpoint is None:
                    self._stop_process()
                    raise RuntimeError("Codex 无法启动受保护的本机 App Server。请确认已安装支持远端 TUI 的 Codex 0.171.0。")
            self._initialized = False
            ws = websocket.create_connection(
                self._endpoint, suppress_origin=True, timeout=5, http_no_proxy=("127.0.0.1",),
                header={"Authorization": f"Bearer {self._token}"},
            )
            ws.settimeout(None)
            self._socket = ws
            self._stdout_thread = threading.Thread(target=self._read_socket, args=(ws,), daemon=True)
            self._stdout_thread.start()
            try:
                self._initialize()
            except Exception:
                self._socket = None
                ws.shutdown()
                raise

    def _read_listener(self, process):
        try:
            for line in process.stderr:
                if process is not self._proc:
                    return
                match = _LISTENING.fullmatch(line)
                if match and self._endpoint is None:
                    self._endpoint = match[1]
                    self._listener_ready.set()
        finally:
            self._listener_ready.set()

    def _read_socket(self, ws):
        try:
            while self._socket is ws:
                frame = ws.recv()
                if not frame:
                    return
                try:
                    message = json.loads(frame)
                except (ValueError, TypeError):
                    continue
                self._handle_message(message)
        except (OSError, websocket.WebSocketException):
            pass
        finally:
            if self._socket is ws:
                self._socket = None
                self._initialized = False
                self._fail_pending_requests(self._transport_error)
                self._emit_event(CodexEvent(type="shared_transport_lost", text=self._transport_error))
            ws.shutdown()

    def _send_json(self, payload):
        with self._send_lock:
            ws = self._socket
            if ws is None:
                raise RuntimeError(self._transport_error)
            try:
                ws.send(json.dumps(payload, ensure_ascii=False))
            except (OSError, websocket.WebSocketException) as exc:
                raise RuntimeError(self._transport_error) from exc

    def _stop_process(self):
        ws = self._socket
        self._socket = None
        if ws is not None:
            ws.shutdown()
        super()._stop_process()
