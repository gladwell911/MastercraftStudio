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
from collections import OrderedDict
from pathlib import Path

import websocket

from codex_client import (
    CodexAppServerClient, CodexEvent, _link_user_codex_directories, _user_home, _windows_popen_kwargs,
    resolve_codex_launch_command,
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
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._socket = None
        self._endpoint = None
        self._executable = None
        self._token = secrets.token_urlsafe(32)
        self._transport_lock = threading.RLock()
        # Never hold the observer lock while waiting for an RPC reply.
        self._observer_lock = threading.RLock()
        self._observer_stop = threading.Event()
        self._observer_retry = None
        self._observer_thread_id = ""
        self._last_seen_turn_id = None
        self._observed_turns = OrderedDict()
        self._reconnect_events = None
        self._reconnect_bytes = 0
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
        if not self.use_native_config and self._codex_home_dir != _user_home() / ".codex":
            _link_user_codex_directories(_user_home() / ".codex", self._codex_home_dir)
        self._refresh_native_skills(cwd)

    def _request_thread_configuration(self, method, params):
        result = super()._request_thread_configuration(method, params)
        thread = result.get("thread") or {}
        with self._observer_lock:
            if not self._observer_thread_id and thread.get("id"):
                self._observer_thread_id = thread["id"]
                turns = thread.get("turns") or []
                if turns:
                    self._last_seen_turn_id = turns[-1]["id"]
                elif method == "thread/start":
                    self._last_seen_turn_id = ""
        return result

    def _ensure_started(self):
        with self._transport_lock:
            if self._closed or self._observer_stop.is_set():
                raise RuntimeError("Codex 原生终端运行时已关闭。")
            if self._socket is not None and self._initialized:
                return
            if self._proc is None or self._proc.poll() is not None:
                if self._observer_thread_id:
                    raise RuntimeError("原 Codex 共享 App Server 已退出，无法确认原回合状态。")
                self._listener_ready.clear()
                self._endpoint = None
                argv = self._app_server_command()
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
            with self._observer_lock:
                self._socket = ws
                thread_id = self._observer_thread_id
                self._reconnect_events = [] if thread_id else None
                self._reconnect_bytes = 0
            self._stdout_thread = threading.Thread(target=self._read_socket, args=(ws,), daemon=True)
            self._stdout_thread.start()
            try:
                self._initialize()
                if thread_id:
                    self._resubscribe(ws, thread_id)
            except Exception:
                with self._observer_lock:
                    if self._socket is ws:
                        self._socket = None
                        self._initialized = False
                        self._reconnect_events = None
                ws.shutdown()
                raise

    def _resubscribe(self, ws, thread_id):
        resume = self._request_internal("thread/resume", {"threadId": thread_id, "excludeTurns": True}, timeout=5)
        if (resume.get("thread") or {}).get("id") != thread_id:
            raise RuntimeError("Codex 未恢复到原共享线程。")
        read = self._request_internal("thread/read", {"threadId": thread_id, "includeTurns": True}, timeout=5)
        thread = read.get("thread") or {}
        if thread.get("id") != thread_id:
            raise RuntimeError("Codex 未返回原共享线程的状态。")
        with self._observer_lock:
            if self._socket is not ws or self._closed:
                raise RuntimeError(self._transport_error)
            queued = self._reconnect_events
            self._reconnect_events = None
            self._reconcile_turns(thread_id, thread.get("turns") or [], queued)

    def _reconcile_turns(self, thread_id, turns, queued):
        # Only the turns observed while running, and those after the last known
        # turn, can belong to this observation gap. Earlier history stays history.
        ids = [turn["id"] for turn in turns]
        anchor = self._last_seen_turn_id
        new_ids = set(ids[ids.index(anchor) + 1:]) if anchor in ids else set()
        if anchor == "":
            new_ids = set(ids)
        elif anchor is None and ids:
            # Some resumed threads omit turns. An unknown baseline must not
            # turn pre-existing history into new activity on first reconnect.
            self._last_seen_turn_id = ids[-1]
        snapshots = OrderedDict((turn["id"], turn) for turn in turns
                                if turn["id"] in new_ids or self._observed_turns.get(turn["id"]) is not None)
        for event in queued:
            if event.thread_id == thread_id:
                turn_id = event.turn_id
                if turn_id in snapshots or event.type == "turn_started":
                    # Settle older turns before the next turn claims the chat.
                    while snapshots and next(iter(snapshots)) != turn_id:
                        self._recover_turn(thread_id, snapshots.popitem(last=False)[1])
                turn = snapshots.get(turn_id)
                if turn is not None and event.type != "turn_started":
                    self._restore_turn_start(thread_id, turn)
                    if event.type == "turn_completed":
                        self._recover_turn(thread_id, snapshots.pop(turn_id))
            self._deliver_observer_event(event)
        for turn in snapshots.values():
            self._recover_turn(thread_id, turn)

    def _restore_turn_start(self, thread_id, turn):
        if turn["id"] not in self._observed_turns:
            self._emit_protocol_event("turn/started", {"threadId": thread_id, "turn": turn}, {})

    def _recover_turn(self, thread_id, turn):
        self._restore_turn_start(thread_id, turn)
        turn_id = turn["id"]
        seen = self._observed_turns.get(turn_id)
        if seen is None:
            return
        terminal = turn.get("status") in {"completed", "failed", "interrupted"}
        remaining = []
        for item in turn.get("items") or ():
            method = "item/started" if item.get("status") == "inProgress" else "item/completed"
            if (method, item.get("id")) in seen:
                continue
            if terminal and item.get("type") != "userMessage":
                remaining.append(item)
            else:
                self._emit_protocol_event(method, {"threadId": thread_id, "turnId": turn_id, "item": item}, {})
        if terminal:
            self._emit_protocol_event("turn/completed", {"threadId": thread_id, "turn": dict(turn, items=remaining)}, {})

    def _emit_event(self, event):
        with self._observer_lock:
            if self._reconnect_events is not None:
                self._reconnect_events.append(event)
            else:
                self._deliver_observer_event(event)

    def _deliver_observer_event(self, event):
        if event.turn_id and event.thread_id == self._observer_thread_id:
            turn_id = event.turn_id
            if event.type == "turn_started":
                if turn_id in self._observed_turns:
                    return
                self._last_seen_turn_id = turn_id
                self._observed_turns[turn_id] = set()
            elif event.type in {"item_started", "item_completed"} and event.item_id:
                items = self._observed_turns.get(turn_id)
                if items is not None:
                    method = "item/started" if event.type == "item_started" else "item/completed"
                    items.add((method, event.item_id))
            elif event.type == "turn_completed":
                if turn_id in self._observed_turns and self._observed_turns[turn_id] is None:
                    return
                self._observed_turns[turn_id] = None
                self._observed_turns.move_to_end(turn_id)
                if len(self._observed_turns) > 256:
                    self._observed_turns.popitem(last=False)
        super()._emit_event(event)

    def _start_observer_retry(self):
        if self._observer_thread_id and self._observer_retry is None and not self._observer_stop.is_set():
            self._observer_retry = threading.Thread(target=self._retry_observer, daemon=True,
                                                    name="codex-terminal-observer")
            self._observer_retry.start()

    def _retry_observer(self):
        delay = 0.1
        try:
            while not self._observer_stop.wait(delay):
                try:
                    self._ensure_started()
                    return
                except (RuntimeError, OSError, websocket.WebSocketException):
                    if self._proc is None or self._proc.poll() is not None:
                        return
                    delay = min(delay * 2, 5)
        finally:
            with self._observer_lock:
                self._observer_retry = None
                if self._socket is None and self._proc is not None and self._proc.poll() is None:
                    self._start_observer_retry()

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
                with self._observer_lock:
                    if self._socket is not ws:
                        return
                    if self._reconnect_events is not None and isinstance(message, dict) and (
                            "method" in message or message.get("type") == "event_msg"):
                        self._reconnect_bytes += len(frame.encode("utf-8") if isinstance(frame, str) else frame)
                        if len(self._reconnect_events) >= 1024 or self._reconnect_bytes > 4 * 1024 * 1024:
                            # Retry from another authoritative snapshot when
                            # the stream outpaces the bounded reconnect buffer.
                            return
                    self._handle_message(message)
        except (OSError, websocket.WebSocketException):
            pass
        finally:
            with self._observer_lock:
                if self._socket is ws:
                    self._socket = None
                    self._initialized = False
                    self._reconnect_events = None
                    self._fail_pending_requests(self._transport_error)
                    self._start_observer_retry()
                    super()._emit_event(CodexEvent(type="shared_transport_lost", text=self._transport_error))
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
        with self._observer_lock:
            ws = self._socket
            self._socket = None
            self._reconnect_events = None
        if ws is not None:
            ws.shutdown()
        super()._stop_process()

    def close(self):
        self._observer_stop.set()
        with self._transport_lock:
            super().close()
