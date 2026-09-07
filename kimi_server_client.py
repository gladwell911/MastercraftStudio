"""Kimi Code local server client.

Talks to a spawned ``kimi web`` process over REST (``requests``) and one
WebSocket (``websocket-client``). Replaces the whole codex worker stack for the
``kimi/`` model family: the server process itself provides isolation and the
protocol endpoint.

Threading model mirrors ``CodexWorkerClient``:

- blocking I/O (REST calls, socket reads) happens on background threads owned by
  the caller or by the client's reader thread;
- inbound events are queued and coalesced (consecutive answer deltas for the
  same turn/item merge); the UI thread is only handed batches via a single
  ``messages_pending`` notification plus ``drain_pending_messages()``;
- no wx imports allowed in this module (enforced by tests).

Design: docs/superpowers/specs/2026-08-10-kimicode-server-chat-design.md
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import time
import uuid
from collections import deque
from copy import deepcopy
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Callable

import requests

KIMI_MODEL_PREFIX = "kimi/"
DEFAULT_KIMI_MODEL = "kimi/main"

# Mapping from app model id -> kimi provider model alias passed to the server.
KIMI_SERVER_MODEL_ALIASES: dict[str, str] = {
    "kimi/main": "kimi-code/kimi-for-coding",
    "kimi/highspeed": "kimi-code/kimi-for-coding-highspeed",
    "kimi/k3": "kimi-code/k3",
    "kimi/k3-256k": "kimi-code/k3-256k",
}

DEFAULT_HEALTH_TIMEOUT = 45.0
DEFAULT_REST_TIMEOUT = 60.0
DEFAULT_SHUTDOWN_TIMEOUT = 10.0
DEFAULT_QUEUE_LIMIT = 2000
DEFAULT_RECOVERY_ATTEMPTS = 6
DEFAULT_RECOVERY_BACKOFF = 0.1
DEFAULT_WS_CONNECT_TIMEOUT = 10.0
DEFAULT_WS_SEND_TIMEOUT = 10.0
DEFAULT_VOLATILE_REPLAY_WINDOW = 4096

WS_PATH = "/api/v1/ws"


class KimiServerError(RuntimeError):
    """Raised for spawn, auth, and REST failures with server context."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        result_unknown: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.result_unknown = bool(result_unknown)


def is_kimi_model(model: str) -> bool:
    return str(model or "").strip().startswith(KIMI_MODEL_PREFIX)


def kimi_model_to_server_alias(model: str) -> str:
    text = str(model or "").strip()
    if text in KIMI_SERVER_MODEL_ALIASES:
        return KIMI_SERVER_MODEL_ALIASES[text]
    return KIMI_SERVER_MODEL_ALIASES[DEFAULT_KIMI_MODEL]


def resolve_kimi_launch_command() -> list[str]:
    """Locate the kimi executable: KIMI_BIN env, PATH, then the user install dir."""
    override = os.getenv("KIMI_BIN", "").strip()
    if override:
        path = Path(override)
        if path.is_file():
            return [str(path)]
        raise KimiServerError(f"KIMI_BIN points to a missing file: {override}")
    found = shutil.which("kimi")
    if found:
        return [found]
    userprofile = os.getenv("USERPROFILE", "").strip()
    if userprofile:
        candidate = Path(userprofile) / ".kimi-code" / "bin" / ("kimi.exe" if os.name == "nt" else "kimi")
        if candidate.is_file():
            return [str(candidate)]
    raise KimiServerError("kimi executable not found (set KIMI_BIN or install Kimi Code CLI)")


def pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def kimi_code_home() -> Path:
    override = os.getenv("KIMI_CODE_HOME", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".kimi-code"


def read_server_token() -> str:
    token_file = kimi_code_home() / "server.token"
    try:
        return token_file.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def parse_token_from_banner(line: str) -> str:
    """Extract the bearer token from a ``kimi web`` startup banner line."""
    text = str(line or "")
    for marker in ("Token:", "#token="):
        idx = text.find(marker)
        if idx >= 0:
            return text[idx + len(marker):].strip().split()[0] if text[idx + len(marker):].strip() else ""
    return ""


@dataclass
class KimiEvent:
    type: str
    thread_id: str = ""  # carries the kimi session_id (named thread_id for CodexEvent parity)
    turn_id: str = ""
    item_id: str = ""
    text: str = ""
    raw_text: str = ""
    title: str = ""
    command: str = ""
    exit_code: int | None = None
    subtype: str = ""
    display_kind: str = ""
    phase: str = ""
    status: str = ""
    flags: list[str] = field(default_factory=list)
    request_id: str | int | None = None
    method: str = ""
    params: dict = field(default_factory=dict)
    data: dict = field(default_factory=dict)
    usage: dict = field(default_factory=dict)


def event_to_payload(event: KimiEvent) -> dict[str, Any]:
    if is_dataclass(event):
        return asdict(event)
    return dict(getattr(event, "__dict__", {}) or {})


def event_from_payload(payload: dict[str, Any]) -> KimiEvent:
    if not isinstance(payload, dict):
        raise KimiServerError("Kimi event payload must be dict")
    if not isinstance(payload.get("type"), str) or not payload["type"].strip():
        raise KimiServerError("Kimi event payload requires type")
    allowed = KimiEvent.__dataclass_fields__.keys()
    return KimiEvent(**{key: payload[key] for key in allowed if key in payload})


_TOOL_KIND_DISPLAY = {
    "command": "command",
    "process": "command",
    "file_io": "file",
    "diff": "diff",
    "search": "search",
    "url_fetch": "fetch",
    "agent_call": "agent",
    "agent": "agent",
    "skill_call": "skill",
    "todo_list": "plan",
    "task": "task",
}


def _str(value: Any) -> str:
    # Keep numeric ids intact: turnId/step are 0-based ints from the server,
    # and ``0 or ""`` would silently drop turn 0's id.
    if value is None:
        return ""
    return str(value).strip()


def _text_fragment(value: Any) -> str:
    """Return streamed text exactly as supplied by the server."""
    return "" if value is None else str(value)


def _payload_of(message: dict[str, Any]) -> dict[str, Any]:
    payload = message.get("payload")
    return payload if isinstance(payload, dict) else {}


def _agent_scope(body_type: str, agent_id: str) -> str:
    """Classify protocol ownership without guessing unknown agent identities."""
    normalized = _str(agent_id).lower()
    if body_type.startswith("subagent.") or normalized.startswith(("agent-", "subagent")):
        return "subagent"
    if normalized in {"", "main"}:
        return "main"
    return "unknown"


def map_session_event(message: dict[str, Any]) -> KimiEvent | None:
    """Map one raw WebSocket message to a KimiEvent.

    The server envelope is flat: ``{"type": <event-type>, "seq": n,
    "session_id": ..., "payload": {"type": <event-type>, ...}}``. Control
    messages (ack/server_hello/ping/pong) return None. Unknown event types map to a
    ``notification`` event flagged ``unmapped`` so nothing is dropped silently.
    """
    if not isinstance(message, dict):
        return None
    event_type = _str(message.get("type"))
    if not event_type or event_type in ("ack", "server_hello", "ping", "pong", "error_ack"):
        return None
    session_id = _str(message.get("session_id"))
    body = _payload_of(message)
    body_type = _str(body.get("type")) or event_type
    seq = message.get("seq")

    agent_id = _str(body.get("agentId"))
    agent_scope = _agent_scope(body_type, agent_id)
    event_data: dict[str, Any] = {
        "seq": seq,
        "offset": message.get("offset"),
        "agent_id": agent_id,
        "agent_scope": agent_scope,
        "source_kind": body_type,
    }
    prompt_id = _str(body.get("promptId"))
    if prompt_id:
        event_data["prompt_id"] = prompt_id
    epoch = _str(message.get("epoch"))
    if epoch:
        event_data["epoch"] = epoch
    base: dict[str, Any] = {
        "thread_id": session_id,
        "data": event_data,
    }

    if body_type == "assistant.delta":
        delta = _text_fragment(body.get("delta") if body.get("delta") is not None else body.get("text"))
        return KimiEvent(
            type="agent_message_delta",
            text=delta,
            raw_text=delta,
            turn_id=_str(body.get("turnId")),
            item_id=_str(body.get("messageId") or body.get("itemId")),
            display_kind="assistant",
            **base,
        )
    if body_type == "thinking.delta":
        delta = _text_fragment(body.get("delta") if body.get("delta") is not None else body.get("text"))
        return KimiEvent(
            type="agent_message_delta",
            text=delta,
            raw_text=delta,
            turn_id=_str(body.get("turnId")),
            item_id=_str(body.get("messageId") or body.get("itemId")),
            display_kind="thinking",
            **base,
        )
    if body_type == "turn.started":
        if agent_scope != "main":
            return KimiEvent(
                type="item_started",
                turn_id=_str(body.get("turnId")),
                item_id=agent_id,
                title="subagent" if agent_scope == "subagent" else "agent",
                display_kind="agent",
                status="running",
                text=_str(body.get("prompt")),
                data={**base["data"], "authoritative": False},
                thread_id=session_id,
            )
        return KimiEvent(
            type="turn_started",
            turn_id=_str(body.get("turnId")),
            text=_str(body.get("prompt")),
            **base,
        )
    if body_type == "turn.ended":
        reason = _str(body.get("reason"))
        error = body.get("error") if isinstance(body.get("error"), dict) else {}
        if agent_scope != "main":
            return KimiEvent(
                type="item_completed",
                turn_id=_str(body.get("turnId")),
                item_id=agent_id,
                title="subagent" if agent_scope == "subagent" else "agent",
                display_kind="agent",
                status="completed" if reason in ("completed", "done", "success", "") else reason,
                text=_str(error.get("message")),
                data={**base["data"], "reason": reason, "error": error, "authoritative": False},
                thread_id=session_id,
            )
        return KimiEvent(
            type="turn_completed",
            turn_id=_str(body.get("turnId")),
            status="completed" if reason in ("completed", "done", "success", "") else reason,
            text=_str(error.get("message")),
            data={**base["data"], "reason": reason, "error": error, "authoritative": True},
            thread_id=session_id,
        )
    if body_type in ("turn.step.started", "turn.step.completed", "turn.step.retrying"):
        started = body_type != "turn.step.completed"
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        return KimiEvent(
            type="item_started" if started else "item_completed",
            turn_id=_str(body.get("turnId")),
            item_id=_str(body.get("stepId") or body.get("step")),
            title="step %s" % _str(body.get("step")),
            display_kind="step",
            status=_str(body.get("status")) or ("running" if started else "completed"),
            usage=dict(usage),
            **base,
        )
    if body_type == "turn.step.interrupted":
        return KimiEvent(
            type="item_completed",
            turn_id=_str(body.get("turnId")),
            item_id=_str(body.get("stepId") or body.get("step")),
            title="step %s" % _str(body.get("step")),
            display_kind="step",
            status="interrupted",
            text=_str(body.get("message")),
            **base,
        )
    if body_type in ("tool.call.started", "shell.started"):
        display = body.get("display") if isinstance(body.get("display"), dict) else {}
        kind = _str(display.get("kind") or body.get("kind") or body.get("toolKind"))
        args = body.get("args") if isinstance(body.get("args"), dict) else {}
        return KimiEvent(
            type="item_started",
            turn_id=_str(body.get("turnId")),
            item_id=_str(body.get("toolCallId") or body.get("callId") or body.get("id")),
            title=_str(body.get("description") or body.get("title") or body.get("name") or kind),
            command=_str(body.get("command") or args.get("command")),
            display_kind=_TOOL_KIND_DISPLAY.get(kind, "command" if body_type == "shell.started" else kind or "tool"),
            status="running",
            data={**base["data"], "tool": body},
            thread_id=session_id,
        )
    if body_type in ("tool.progress", "shell.output"):
        delta = _text_fragment(body.get("delta") if body.get("delta") is not None else body.get("output") if body.get("output") is not None else body.get("text"))
        return KimiEvent(
            type="agent_message_delta",
            turn_id=_str(body.get("turnId")),
            item_id=_str(body.get("toolCallId") or body.get("callId") or body.get("id")),
            text=delta,
            display_kind="commentary",
            **base,
        )
    if body_type == "tool.call.delta":
        # Partial JSON argument fragments; not user-visible, skip.
        return None
    if body_type in ("tool.result", "shell.completed"):
        display = body.get("display") if isinstance(body.get("display"), dict) else {}
        kind = _str(display.get("kind") or body.get("kind") or body.get("toolKind"))
        return KimiEvent(
            type="item_completed",
            turn_id=_str(body.get("turnId")),
            item_id=_str(body.get("toolCallId") or body.get("callId") or body.get("id")),
            title=_str(body.get("description") or body.get("title") or body.get("name")),
            command=_str(body.get("command")),
            exit_code=body.get("exitCode") if isinstance(body.get("exitCode"), int) else None,
            status=_str(body.get("status")) or "completed",
            text=_str(body.get("summary") or body.get("output"))[:2000],
            display_kind="command" if body_type == "shell.completed" else _TOOL_KIND_DISPLAY.get(kind, kind or "tool"),
            data={**base["data"], "tool": body},
            thread_id=session_id,
        )
    if body_type.startswith("subagent."):
        state = body_type.rsplit(".", 1)[-1]
        return KimiEvent(
            type="subagent_result" if state in ("completed", "failed") else "item_started",
            turn_id=_str(body.get("turnId")),
            item_id=_str(body.get("subagentId") or body.get("agentId")),
            title=_str(body.get("title") or body.get("name") or "subagent"),
            display_kind="agent",
            status=state,
            text=_str(body.get("summary") or body.get("message")),
            **base,
        )
    if body_type == "prompt.aborted":
        if agent_scope != "main":
            return KimiEvent(
                type="item_completed",
                turn_id=_str(body.get("turnId")),
                item_id=agent_id,
                title="subagent" if agent_scope == "subagent" else "agent",
                display_kind="agent",
                status="interrupted",
                data={**base["data"], "authoritative": False},
                thread_id=session_id,
            )
        return KimiEvent(
            type="turn_completed",
            turn_id=_str(body.get("turnId")),
            status="interrupted",
            **base,
        )
    if body_type == "prompt.completed":
        reason = _str(body.get("reason"))
        if agent_scope != "main":
            return KimiEvent(
                type="item_completed",
                turn_id=_str(body.get("turnId")),
                item_id=agent_id,
                title="subagent" if agent_scope == "subagent" else "agent",
                display_kind="agent",
                status="completed" if reason in ("completed", "done", "success", "") else reason or "completed",
                data={**base["data"], "prompt_id": _str(body.get("promptId")), "reason": reason, "authoritative": False},
                thread_id=session_id,
            )
        return KimiEvent(
            type="turn_completed",
            turn_id=_str(body.get("turnId")),
            status="completed" if reason in ("completed", "done", "success", "") else reason or "completed",
            data={**base["data"], "prompt_id": _str(body.get("promptId")), "reason": reason, "authoritative": True, "fallback": True},
            thread_id=session_id,
        )
    if body_type.startswith("compaction."):
        return KimiEvent(
            type="notification",
            display_kind="compaction",
            status=body_type.rsplit(".", 1)[-1],
            text="compaction %s" % body_type.rsplit(".", 1)[-1],
            **base,
        )
    if body_type == "goal.updated":
        goal = body.get("goal") if isinstance(body.get("goal"), dict) else body
        return KimiEvent(
            type="notification",
            display_kind="goal",
            status=_str(goal.get("status") or body.get("status")),
            text=_str(goal.get("objective") or body.get("objective")),
            data={**base["data"], "goal": goal},
            thread_id=session_id,
        )
    if body_type == "agent.status.updated":
        phase = body.get("phase") if isinstance(body.get("phase"), dict) else {}
        kind = _str(phase.get("kind"))
        usage: dict[str, Any] = {}
        if isinstance(body.get("contextTokens"), (int, float)):
            usage = {
                "context_tokens": body.get("contextTokens"),
                "max_context_tokens": body.get("maxContextTokens"),
            }
        if kind == "awaiting_approval":
            return KimiEvent(
                type="server_request",
                method="approval",
                turn_id=_str(phase.get("turnId")),
                params=dict(body),
                usage=usage,
                **base,
            )
        return KimiEvent(
            type="thread_status_changed",
            status=kind,
            turn_id=_str(phase.get("turnId")),
            usage=usage,
            **base,
        )
    if body_type == "error":
        return KimiEvent(
            type="error",
            turn_id=_str(body.get("turnId")),
            text=_str(body.get("message") or body.get("code")),
            subtype=_str(body.get("code")),
            **base,
        )
    if body_type == "warning":
        return KimiEvent(
            type="notification",
            display_kind="warning",
            text=_str(body.get("message") or body.get("code")),
            **base,
        )
    if body_type in (
        "event.session.work_changed",
        "session.meta.updated",
        "context.spliced",
        "agent.created",
        "agent.disposed",
        "event.session.created",
        "event.session.status_changed",
        "prompt.submitted",
        "prompt.steered",
    ):
        # Known but not user-visible on its own; keep as low-key notification data.
        return KimiEvent(
            type="notification",
            display_kind="session",
            subtype=body_type,
            text="",
            data={**base["data"], "protocol": dict(body)},
            thread_id=session_id,
        )
    return KimiEvent(
        type="notification",
        display_kind="unmapped",
        subtype=body_type,
        data={**base["data"], "unmapped": True, "raw": body},
        thread_id=session_id,
    )


class KimiServerClient:
    """Owns one ``kimi web`` child process, one WebSocket, and the event queue."""

    def __init__(
        self,
        on_message: Callable[[dict[str, Any]], None] | None = None,
        on_exit: Callable[..., None] | None = None,
        *,
        process_factory: Callable[..., Any] | None = None,
        http_session_factory: Callable[[], Any] | None = None,
        ws_factory: Callable[..., Any] | None = None,
        launch_command: list[str] | None = None,
        port: int | None = None,
        token: str | None = None,
        queue_limit: int = DEFAULT_QUEUE_LIMIT,
        health_timeout: float = DEFAULT_HEALTH_TIMEOUT,
        rest_timeout: float = DEFAULT_REST_TIMEOUT,
        start_reader_thread: bool = True,
        recovery_attempts: int = DEFAULT_RECOVERY_ATTEMPTS,
        recovery_backoff: float = DEFAULT_RECOVERY_BACKOFF,
        ws_connect_timeout: float = DEFAULT_WS_CONNECT_TIMEOUT,
        ws_send_timeout: float = DEFAULT_WS_SEND_TIMEOUT,
        volatile_replay_window: int = DEFAULT_VOLATILE_REPLAY_WINDOW,
    ) -> None:
        self.on_message = on_message
        self.on_exit = on_exit
        self.process_factory = process_factory
        self.http_session_factory = http_session_factory
        self.ws_factory = ws_factory
        self.launch_command = list(launch_command) if launch_command else None
        self.port = int(port) if port else None
        self._configured_token = str(token) if token is not None else None
        self.token = self._configured_token
        self.queue_limit = max(int(queue_limit or 0), 1)
        self.health_timeout = float(health_timeout)
        self.rest_timeout = float(rest_timeout)
        self.start_reader_thread = bool(start_reader_thread)
        self.recovery_attempts = max(1, int(recovery_attempts or 1))
        self.recovery_backoff = max(0.0, float(recovery_backoff or 0.0))
        self.ws_connect_timeout = max(0.1, float(ws_connect_timeout or DEFAULT_WS_CONNECT_TIMEOUT))
        self.ws_send_timeout = max(0.1, float(ws_send_timeout or DEFAULT_WS_SEND_TIMEOUT))
        self.volatile_replay_window = max(64, int(volatile_replay_window or DEFAULT_VOLATILE_REPLAY_WINDOW))

        self.process = None
        self.base_url = ""
        self._http: Any = None
        self._ws: Any = None
        self._queue: deque[dict[str, Any]] = deque()
        self._lock = threading.Lock()
        self._lifecycle_lock = threading.RLock()
        self._start_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._ws_thread: threading.Thread | None = None
        self._ws_generation = 0
        self._hello_ids: dict[int, str] = {}
        self._subscribe_requests: dict[str, set[str]] = {}
        # A transport is not recovered merely because TCP connected.  The
        # server must acknowledge hello and the requested subscriptions first.
        self._transport_ready_events: dict[int, threading.Event] = {}
        self._hello_acknowledged: set[int] = set()
        self._subscription_acknowledged: set[int] = set()
        self._banner_thread: threading.Thread | None = None
        self._closed = False
        self._started = False
        self._pending_notification = False
        self._subscribed_sessions: set[str] = set()
        self._session_cursors: dict[str, dict[str, Any]] = {}
        self._stable_event_cursors: dict[str, tuple[str, int]] = {}
        self._volatile_replay_keys: set[tuple[Any, ...]] = set()
        self._volatile_replay_order: deque[tuple[Any, ...]] = deque()
        self._recovery_thread: threading.Thread | None = None
        self._recovery_requested = False
        self._recovery_generation = 0
        self._transport_faults_notified: set[int] = set()
        self._active_transport_fault_id: int | None = None
        self._resync_notified: set[tuple[int, tuple[str, ...]]] = set()
        self._banner_lines: deque[str] = deque(maxlen=100)

    # ------------------------------------------------------------------
    # lifecycle

    def start(self, *, timeout: float | None = None) -> None:
        # Starting, health probing and websocket handshakes are network-bound.
        # Serialize them without holding the lifecycle lock so close/readers can
        # still invalidate state and never wait forever behind a connect call.
        deadline = time.monotonic() + max(0.05, float(timeout)) if timeout is not None else None
        with self._start_lock:
            with self._lifecycle_lock:
                live_process = self.process is not None and self.process.poll() is None
                reusable = live_process and self._http is not None and bool(self.base_url) and bool(self.token)
                self._closed = False
                if reusable:
                    needs_socket = not self._ws_is_usable_locked()
                else:
                    needs_socket = True
                    self._started = False
                    self._banner_lines.clear()
                    # A restarted server owns a new credential.  Only an
                    # explicit constructor override may survive the restart.
                    self.token = self._configured_token
                    port = self.port or pick_free_port()
                    self.port = port
                    self.base_url = f"http://127.0.0.1:{port}"
                    command = list(self.launch_command or resolve_kimi_launch_command())
                    args = command + ["web", "--no-open", "--port", str(port)]
                    if self.process_factory is not None:
                        self.process = self.process_factory(args)
                    else:
                        self.process = subprocess.Popen(
                            args,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            creationflags=self._creationflags(),
                        )
                    self._banner_thread = threading.Thread(target=self._banner_loop, daemon=True)
                    self._banner_thread.start()
                    self._http = self.http_session_factory() if self.http_session_factory else requests.Session()
            if not reusable:
                self._wait_for_health(deadline=deadline)
                if self.token is None:
                    self.token = self._find_token()
                if not self.token:
                    self._abort_start("kimi server token unavailable")
            if needs_socket:
                remaining = None if deadline is None else max(0.05, deadline - time.monotonic())
                if deadline is not None and remaining <= 0.05:
                    raise KimiServerError("Kimi startup deadline exhausted")
                self._connect_ws(timeout=remaining)
            with self._lifecycle_lock:
                self._started = True

    def close(self) -> None:
        ws = None
        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            self._recovery_requested = False
            self._recovery_generation += 1
            ws = self._ws
            self._ws = None
            self._ws_generation += 1
            proc = self.process
        self._close_ws_locked(ws)
        if proc is None:
            return
        with self._lifecycle_lock:
            if proc.poll() is None:
                try:
                    self._request("POST", "/api/v1/shutdown", timeout=DEFAULT_SHUTDOWN_TIMEOUT)
                except Exception:
                    pass
                try:
                    proc.wait(timeout=DEFAULT_SHUTDOWN_TIMEOUT)
                    return
                except Exception:
                    pass
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                    return
                except Exception:
                    pass
                try:
                    if proc.poll() is None:
                        proc.kill()
                        proc.wait(timeout=2)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # REST API

    def create_session(
        self,
        *,
        cwd: str,
        model: str = "",
        title: str = "",
        permission_mode: str = "auto",
        goal_objective: str = "",
    ) -> str:
        agent_config: dict[str, Any] = {"permission_mode": permission_mode}
        alias = kimi_model_to_server_alias(model) if model else ""
        if alias:
            agent_config["model"] = alias
        if goal_objective:
            agent_config["goal_objective"] = goal_objective
        body: dict[str, Any] = {
            "title": title or "kimi chat",
            "metadata": {"cwd": str(cwd)},
            "agent_config": agent_config,
        }
        data = self._request_data("POST", "/api/v1/sessions", json_body=body)
        session_id = _str(data.get("id"))
        if not session_id:
            raise KimiServerError(f"create_session response missing id: {data!r}")
        # The create endpoint does not reliably apply agent_config (probe verdict);
        # push it through the profile endpoint as well.
        try:
            self._request_data("POST", f"/api/v1/sessions/{session_id}/profile",
                               json_body={"agent_config": agent_config})
        except KimiServerError:
            pass
        try:
            self.subscribe([session_id])
        except KimiServerError:
            # ``subscribe`` already records the desired session and schedules
            # transport recovery.  The created session id remains valid and
            # must not be lost or recreated merely because the socket blinked.
            pass
        return session_id

    def submit_prompt(self, session_id: str, content_blocks: list[dict[str, Any]]) -> str:
        data = self._request_data(
            "POST",
            f"/api/v1/sessions/{session_id}/prompts",
            json_body={"content": list(content_blocks)},
        )
        prompt_id = _str(data.get("prompt_id") or data.get("user_message_id"))
        if not prompt_id:
            raise KimiServerError(
                f"submit_prompt response missing prompt_id/user_message_id: {data!r}"
            )
        return prompt_id

    def steer_prompts(self, session_id: str, prompt_ids: list[str]) -> bool | None:
        """Return accepted/rejected/unknown without guessing ambiguous POSTs."""
        try:
            self._request_data(
                "POST",
                f"/api/v1/sessions/{session_id}/prompts:steer",
                json_body={"prompt_ids": [str(p) for p in prompt_ids if str(p or "").strip()]},
            )
            return True
        except KimiServerError as exc:
            if exc.result_unknown:
                return None
            status = exc.status_code
            if isinstance(status, int) and (400 <= status < 500 or status >= 40000):
                return False
            raise

    def list_messages(self, session_id: str, *, timeout: float | None = None) -> list[dict[str, Any]]:
        data = self._request_data("GET", f"/api/v1/sessions/{session_id}/messages", timeout=timeout)
        items = data.get("items")
        return list(items) if isinstance(items, list) else []

    def get_status(self, session_id: str, *, timeout: float | None = None) -> dict[str, Any]:
        return self._request_data("GET", f"/api/v1/sessions/{session_id}/status", timeout=timeout)

    def session_exists(self, session_id: str, *, timeout: float | None = None) -> bool:
        try:
            self._request_data("GET", f"/api/v1/sessions/{session_id}", timeout=timeout)
            return True
        except KimiServerError as exc:
            if exc.status_code == 404:
                return False
            raise

    def answer_approval(
        self,
        session_id: str,
        approval_id: str,
        decision: str,
        *,
        feedback: str = "",
        selected_label: str = "",
    ) -> None:
        body: dict[str, Any] = {"decision": str(decision)}
        if feedback:
            body["feedback"] = feedback
        if selected_label:
            body["selected_label"] = selected_label
        self._request_data(
            "POST",
            f"/api/v1/sessions/{session_id}/approvals/{approval_id}",
            json_body=body,
        )

    def list_approvals(self, session_id: str) -> list[dict[str, Any]]:
        data = self._request_data("GET", f"/api/v1/sessions/{session_id}/approvals")
        items = data.get("items") if isinstance(data, dict) else None
        if isinstance(items, list):
            return items
        return list(data) if isinstance(data, list) else []

    def goal_control(self, session_id: str, action: str) -> None:
        self._request_data(
            "POST",
            f"/api/v1/sessions/{session_id}/profile",
            json_body={"agent_config": {"goal_control": str(action)}},
        )

    def set_goal(self, session_id: str, objective: str) -> None:
        self._request_data(
            "POST",
            f"/api/v1/sessions/{session_id}/profile",
            json_body={"agent_config": {"goal_objective": str(objective)}},
        )

    # ------------------------------------------------------------------
    # WebSocket control messages

    def subscribe(self, session_ids: list[str], *, timeout: float | None = None) -> None:
        ids = [str(s) for s in session_ids if str(s or "").strip()]
        if not ids:
            return
        request_id = self._next_id()
        with self._lifecycle_lock:
            self._subscribed_sessions.update(ids)
            self._subscribe_requests[request_id] = set(ids)
        self._send_ws(
            {"type": "subscribe", "id": request_id, "payload": {"session_ids": ids}},
            send_timeout=timeout,
        )

    def abort(self, session_id: str, prompt_id: str = "") -> None:
        payload: dict[str, Any] = {"session_id": str(session_id)}
        if prompt_id:
            payload["prompt_id"] = str(prompt_id)
        self._send_ws({"type": "abort", "id": self._next_id(), "payload": payload})

    # ------------------------------------------------------------------
    # UI-facing queue (mirrors CodexWorkerClient)

    def drain_pending_messages(self, limit: int = 100) -> list[dict[str, Any]]:
        remaining = max(int(limit or 0), 0)
        drained: list[dict[str, Any]] = []
        if remaining == 0:
            return drained
        notify_pending = False
        with self._lock:
            while remaining and self._queue:
                entry = self._queue.popleft()
                drained.append(entry["message"])
                remaining -= 1
            if drained:
                if self._queue:
                    self._pending_notification = True
                    notify_pending = True
                else:
                    self._pending_notification = False
        if notify_pending and self.on_message is not None:
            self.on_message({"type": "messages_pending"})
        return drained

    # ------------------------------------------------------------------
    # internals

    def _wait_for_health(self, *, deadline: float | None = None) -> None:
        own_deadline = time.monotonic() + self.health_timeout
        deadline = min(own_deadline, deadline) if deadline is not None else own_deadline
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                tail = "\n".join(list(self._banner_lines)[-10:])
                self._abort_start(f"kimi server exited during startup (code {self.process.poll()})\n{tail}")
            try:
                resp = self._http.get(
                    f"{self.base_url}/api/v1/healthz",
                    headers=self._auth_headers(),
                    timeout=max(0.05, min(2.0, deadline - time.monotonic())),
                )
                if getattr(resp, "status_code", 0) == 200:
                    return
            except KimiServerError:
                raise
            except Exception:
                pass
            time.sleep(0.3)
        self._abort_start("timed out waiting for kimi server health endpoint")

    def _abort_start(self, message: str) -> None:
        proc = self.process
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        raise KimiServerError(message)

    def _find_token(self) -> str:
        for line in list(self._banner_lines):
            token = parse_token_from_banner(line)
            if token:
                return token
        return read_server_token()

    def _auth_headers(self) -> dict[str, str]:
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {}

    def _request(self, method: str, path: str, *, json_body: Any = None, timeout: float | None = None):
        if self._http is None:
            raise KimiServerError("kimi client is not started")
        try:
            resp = self._http.request(
                method,
                f"{self.base_url}{path}",
                json=json_body,
                headers=self._auth_headers(),
                timeout=timeout or self.rest_timeout,
            )
        except KimiServerError:
            raise
        except Exception as exc:
            raise KimiServerError(
                f"kimi server {method} {path} failed: {exc}",
                result_unknown=str(method).upper() == "POST",
            ) from exc
        status = getattr(resp, "status_code", 0)
        if status < 200 or status >= 300:
            raise KimiServerError(
                f"kimi server {method} {path} returned {status}: {getattr(resp, 'text', '')[:300]}",
                status_code=int(status or 0),
                result_unknown=(
                    str(method).upper() == "POST"
                    and (int(status or 0) >= 500 or int(status or 0) in {408, 429})
                ),
            )
        return resp

    def _request_data(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        resp = self._request(method, path, json_body=json_body, timeout=timeout)
        try:
            payload = resp.json()
        except Exception as exc:
            raise KimiServerError(f"kimi server {method} {path} returned non-JSON") from exc
        if not isinstance(payload, dict):
            raise KimiServerError(f"kimi server {method} {path} returned unexpected payload")
        code = payload.get("code", 0)
        if code not in (0, None):
            numeric_code = int(code) if isinstance(code, int) else None
            application_server_error = bool(
                isinstance(numeric_code, int)
                and (500 <= numeric_code < 600 or numeric_code >= 50000)
            )
            raise KimiServerError(
                f"kimi server {method} {path} error {code}: {payload.get('msg')}",
                status_code=numeric_code,
                # A POST may have committed before the server encoded an
                # application-level failure in an otherwise successful HTTP
                # response.  Preserve that ambiguity for transcript recovery.
                result_unknown=str(method).upper() == "POST" and application_server_error,
            )
        data = payload.get("data")
        return data if isinstance(data, dict) else ({} if data is None else {"value": data})

    def _connect_ws(self, *, timeout: float | None = None) -> None:
        if timeout is None:
            self._connect_ws_locked()
        else:
            self._connect_ws_locked(timeout=timeout)

    def _connect_ws_locked(self, *, timeout: float | None = None) -> None:
        """Connect and handshake without holding the lifecycle lock.

        The historical name is retained for test and caller compatibility.  A
        socket generation reserves the attempt; only that generation may
        install the candidate or deliver frames.
        """
        with self._lifecycle_lock:
            if self._closed:
                raise KimiServerError("kimi websocket is closing")
            if self.process is None or self.process.poll() is not None:
                raise KimiServerError("kimi server process is not running")
            stale_ws = self._ws
            self._ws = None
            self._ws_generation += 1
            generation = self._ws_generation
            url = f"ws://127.0.0.1:{self.port}{WS_PATH}"
            headers = [f"Authorization: Bearer {self.token}"] if self.token else []
            subscriptions = sorted(self._subscribed_sessions)
            cursors = deepcopy(self._session_cursors)
            hello_id = self._next_id()
            subscribe_id = self._next_id() if subscriptions else ""
            self._hello_ids = {generation: hello_id}
            self._transport_ready_events[generation] = threading.Event()
            self._hello_acknowledged.discard(generation)
            self._subscription_acknowledged.discard(generation)
            if subscribe_id:
                self._subscribe_requests[subscribe_id] = set(subscriptions)
        self._close_ws_locked(stale_ws)
        candidate = None
        try:
            if self.ws_factory is not None:
                candidate = self.ws_factory(url, headers)
            else:
                import websocket

                candidate = websocket.create_connection(
                    url,
                    header=headers,
                    timeout=min(self.ws_connect_timeout, timeout) if timeout is not None else self.ws_connect_timeout,
                )
                # The timeout is a handshake boundary, not an idle lifetime.
                if hasattr(candidate, "settimeout"):
                    candidate.settimeout(None)
            if bool(getattr(candidate, "closed", False)):
                raise KimiServerError("kimi websocket factory returned a closed socket")
            self._send_on_socket_locked(
                candidate,
                {
                    "type": "client_hello",
                    "id": hello_id,
                    "payload": {
                        "client_id": "zgwd",
                        "subscriptions": [],
                        "cursors": cursors,
                    },
                },
            )
            if subscriptions:
                self._send_on_socket_locked(
                    candidate,
                    {
                        "type": "subscribe",
                        "id": subscribe_id,
                        "payload": {"session_ids": subscriptions},
                    },
                )
        except Exception as exc:
            self._close_ws_locked(candidate)
            if isinstance(exc, KimiServerError):
                raise
            raise KimiServerError(f"kimi websocket connection failed: {exc}") from exc
        with self._lifecycle_lock:
            valid = (
                not self._closed
                and generation == self._ws_generation
                and self.process is not None
                and self.process.poll() is None
            )
            if valid:
                self._ws = candidate
                if self.start_reader_thread:
                    self._ws_thread = threading.Thread(
                        target=self._ws_loop,
                        args=(candidate, generation),
                        daemon=True,
                    )
                    self._ws_thread.start()
                else:
                    self._ws_thread = None
        if not valid:
            self._close_ws_locked(candidate)
            raise KimiServerError("kimi websocket connection was superseded")

    def _send_ws(self, message: dict[str, Any], *, send_timeout: float | None = None) -> None:
        last_error: BaseException | None = None
        for attempt in range(2):
            with self._lifecycle_lock:
                closed = self._closed
                usable = self._ws_is_usable_locked()
            if closed:
                raise KimiServerError("kimi websocket is closing")
            try:
                if not usable:
                    self._connect_ws(timeout=send_timeout)
                with self._lifecycle_lock:
                    ws = self._ws
                    generation = self._ws_generation
                if ws is None:
                    raise KimiServerError("kimi websocket is not connected")
                self._send_on_socket_locked(ws, message, send_timeout=send_timeout)
                with self._lifecycle_lock:
                    if self._ws is not ws or self._ws_generation != generation:
                        raise KimiServerError("kimi websocket changed during send")
                return
            except Exception as exc:
                last_error = exc
                self._invalidate_ws(ws if "ws" in locals() else None, str(exc), recover=False)
                with self._lifecycle_lock:
                    may_retry = (
                        attempt == 0
                        and not self._closed
                        and self.process is not None
                        and self.process.poll() is None
                    )
                if may_retry:
                    continue
                break
        error = f"kimi websocket transport failed: {last_error}"
        self._notify_transport_failure_once(error)
        self._request_recovery(error)
        raise KimiServerError(error) from last_error

    def _send_on_socket_locked(
        self,
        ws: Any,
        message: dict[str, Any],
        *,
        send_timeout: float | None = None,
    ) -> None:
        if ws is None:
            raise KimiServerError("kimi websocket is not connected")
        line = json.dumps(message, ensure_ascii=False)
        with self._send_lock:
            previous_timeout = None
            timeout_changed = False
            try:
                if hasattr(ws, "gettimeout") and hasattr(ws, "settimeout"):
                    previous_timeout = ws.gettimeout()
                    bounded_timeout = self.ws_send_timeout
                    if send_timeout is not None:
                        bounded_timeout = max(0.05, min(bounded_timeout, float(send_timeout)))
                    ws.settimeout(bounded_timeout)
                    timeout_changed = True
                ws.send(line)
            finally:
                if timeout_changed:
                    try:
                        ws.settimeout(previous_timeout)
                    except Exception:
                        pass

    def _close_ws_locked(self, ws: Any) -> None:
        if ws is None:
            return
        with self._send_lock:
            try:
                ws.close()
            except Exception:
                pass

    def _ws_is_usable_locked(self) -> bool:
        if self._ws is None:
            return False
        if not self.start_reader_thread:
            return True
        return self._ws_thread is not None and self._ws_thread.is_alive()

    def _banner_loop(self) -> None:
        proc = self.process
        stream = getattr(proc, "stdout", None)
        if stream is None:
            return
        try:
            for line in stream:
                self._banner_lines.append(str(line).rstrip())
        except Exception:
            pass
        finally:
            self._notify_exit(proc)

    def _ws_loop(self, ws: Any | None = None, generation: int | None = None) -> None:
        if ws is None:
            with self._lifecycle_lock:
                ws = self._ws
                generation = self._ws_generation
        if ws is None:
            return
        if generation is None:
            with self._lifecycle_lock:
                generation = self._ws_generation
        while True:
            with self._lifecycle_lock:
                if self._closed or self._ws is not ws or generation != self._ws_generation:
                    return
            try:
                raw = ws.recv()
            except Exception as exc:
                if self._is_ws_timeout(exc):
                    continue
                with self._lifecycle_lock:
                    if self._closed:
                        return
                self._invalidate_ws(ws, str(exc), recover=True, generation=generation)
                return
            if not raw:
                self._invalidate_ws(ws, "kimi websocket closed", recover=True, generation=generation)
                return
            # recv() may return after a replacement socket was installed.
            with self._lifecycle_lock:
                if self._closed or self._ws is not ws or generation != self._ws_generation:
                    return
            try:
                message = json.loads(raw)
            except Exception:
                continue
            if not self._handle_ws_message(message, ws=ws, generation=generation):
                return

    def _handle_ws_message(
        self,
        message: dict[str, Any],
        *,
        ws: Any | None = None,
        generation: int | None = None,
    ) -> bool:
        if ws is not None:
            with self._lifecycle_lock:
                if self._closed or self._ws is not ws:
                    return False
                if generation is not None and generation != self._ws_generation:
                    return False
        if isinstance(message, dict) and _str(message.get("type")) == "ack":
            try:
                self._handle_ack(message, generation=generation)
                return True
            except KimiServerError as exc:
                target = ws
                if target is None:
                    with self._lifecycle_lock:
                        target = self._ws
                self._invalidate_ws(target, str(exc), recover=True, generation=generation)
                return False
        if isinstance(message, dict) and _str(message.get("type")) == "ping":
            payload = _payload_of(message)
            target = ws
            if target is None:
                with self._lifecycle_lock:
                    target = self._ws
            try:
                with self._lifecycle_lock:
                    if self._closed or target is None or self._ws is not target:
                        return False
                    target_generation = self._ws_generation
                self._send_on_socket_locked(
                    target,
                    {"type": "pong", "payload": {"nonce": payload.get("nonce")}},
                )
                with self._lifecycle_lock:
                    if self._ws is not target or self._ws_generation != target_generation:
                        return False
                return True
            except Exception as exc:
                self._invalidate_ws(target, str(exc), recover=True, generation=generation)
                return False
        if not self._accept_session_event(message):
            return True
        event = map_session_event(message)
        if event is None:
            return True
        self._enqueue_event(event)
        return True

    def _invalidate_ws(
        self,
        ws: Any,
        error: str,
        *,
        notify: bool | None = None,
        recover: bool = False,
        generation: int | None = None,
    ) -> bool:
        """Mark only ``ws`` stale; an older reader must not clear its replacement."""
        invalidated = False
        fault_generation = -1
        with self._lifecycle_lock:
            if (
                not self._closed
                and self._ws is ws
                and (generation is None or generation == self._ws_generation)
            ):
                fault_generation = self._ws_generation
                self._ws = None
                self._ws_generation += 1
                invalidated = True
        if invalidated:
            self._close_ws_locked(ws)
        if invalidated and recover:
            self._notify_transport_failure_once(str(error), fault_generation)
            self._request_recovery(str(error))
        elif invalidated and notify:
            self._notify_transport_failure_once(str(error), fault_generation)
        return invalidated

    def _notify_transport_failure_once(self, error: str, generation: int | None = None) -> None:
        with self._lifecycle_lock:
            fault_generation = self._ws_generation if generation is None else int(generation)
            if self._active_transport_fault_id is not None:
                return
            if fault_generation in self._transport_faults_notified:
                return
            self._transport_faults_notified.add(fault_generation)
            self._active_transport_fault_id = fault_generation
            session_ids = sorted(self._subscribed_sessions)
        self._enqueue_control(
            {
                "type": "transport_error",
                "payload": {"error": str(error), "session_ids": session_ids},
            }
        )

    def _handle_ack(self, message: dict[str, Any], *, generation: int | None = None) -> None:
        payload = _payload_of(message)
        code = message.get("code", payload.get("code", 0))
        if code not in (0, None):
            raise KimiServerError(
                f"kimi websocket request rejected ({code}): {message.get('msg') or payload.get('msg') or ''}"
            )
        request_id = _str(message.get("id"))
        with self._lifecycle_lock:
            requested_subscriptions = set(self._subscribe_requests.pop(request_id, set()))
        not_found = {
            _str(value) for value in (payload.get("not_found") or []) if _str(value)
        }
        accepted_value = payload.get("accepted")
        accepted = {
            _str(value) for value in (accepted_value or []) if _str(value)
        } if isinstance(accepted_value, list) else set()
        rejected = requested_subscriptions & not_found
        if requested_subscriptions and isinstance(accepted_value, list):
            rejected.update(requested_subscriptions - accepted)
        if rejected:
            # Do not let one stale session poison every later reconnect.  The
            # socket is still recovered so accepted sessions are resubscribed.
            with self._lifecycle_lock:
                self._subscribed_sessions.difference_update(rejected)
            raise KimiServerError(
                "kimi websocket subscribe rejected: " + ", ".join(sorted(rejected))
            )
        cursors = payload.get("cursors") if isinstance(payload.get("cursors"), dict) else {}
        with self._lifecycle_lock:
            active_generation = self._ws_generation if generation is None else generation
            if generation is not None and generation != self._ws_generation:
                return
            hello_id = self._hello_ids.get(active_generation)
            confirms_epoch = bool(hello_id and _str(message.get("id")) == hello_id)
            if confirms_epoch:
                self._hello_acknowledged.add(active_generation)
            if request_id and requested_subscriptions:
                self._subscription_acknowledged.add(active_generation)
            for session_id, cursor in cursors.items():
                if isinstance(cursor, dict):
                    epoch = _str(cursor.get("epoch"))
                    seq = cursor.get("seq")
                    if epoch and isinstance(seq, int):
                        normalized = _str(session_id)
                        current = self._session_cursors.get(normalized)
                        current_epoch = _str((current or {}).get("epoch")) if isinstance(current, dict) else ""
                        current_seq = int((current or {}).get("seq") or -1) if isinstance(current, dict) else -1
                        if current_epoch and epoch != current_epoch and not confirms_epoch:
                            continue
                        if epoch == current_epoch and seq < current_seq:
                            continue
                        self._session_cursors[normalized] = {"epoch": epoch, "seq": seq}
                        previous = self._stable_event_cursors.get(normalized)
                        if previous is None or (previous[0] != epoch and confirms_epoch) or (previous[0] == epoch and seq > previous[1]):
                            self._stable_event_cursors[normalized] = (epoch, seq)
            # An empty subscription set is ready after hello; otherwise the
            # corresponding subscribe ack is the readiness boundary.
            ready = (
                active_generation in self._hello_acknowledged
                and (
                    not self._subscribed_sessions
                    or active_generation in self._subscription_acknowledged
                )
            )
            if ready:
                event = self._transport_ready_events.get(active_generation)
                if event is not None:
                    event.set()
        resync = payload.get("resync_required")
        if resync:
            if isinstance(resync, list):
                resync_values = resync
            else:
                resync_values = requested_subscriptions or set(cursors) or self._subscribed_sessions
            session_ids = tuple(sorted({_str(value) for value in resync_values if _str(value)}))
            if not session_ids:
                return
            with self._lifecycle_lock:
                identity = (active_generation, session_ids)
                if identity in self._resync_notified:
                    return
                self._resync_notified.add(identity)
            self._enqueue_control(
                {
                    "type": "resync_required",
                    "payload": {"session_ids": list(session_ids)},
                }
            )

    def _accept_session_event(self, message: dict[str, Any]) -> bool:
        """Advance protocol cursors and reject replayed stable events.

        Volatile delta frames may legitimately share a sequence number and are
        distinguished by ``offset``.  Stable frames at or below the last stable
        sequence for the same epoch are replay and must be discarded before
        mapping so they cannot append duplicate answer text.
        """
        if not isinstance(message, dict):
            return False
        session_id = _str(message.get("session_id"))
        seq = message.get("seq")
        if not session_id or not isinstance(seq, int):
            return True
        epoch = _str(message.get("epoch"))
        body = _payload_of(message)
        body_type = _str(body.get("type") or message.get("type"))
        volatile = bool(message.get("volatile")) or body_type.endswith(".delta") or message.get("offset") is not None
        with self._lifecycle_lock:
            current = self._session_cursors.get(session_id)
            current_epoch = _str((current or {}).get("epoch")) if isinstance(current, dict) else ""
            # Sequenced frames may omit epoch after the handshake.  Inherit
            # the confirmed epoch instead of clearing it and reopening replay.
            effective_epoch = epoch or current_epoch
            if current_epoch and epoch and epoch != current_epoch:
                return False
            previous = self._stable_event_cursors.get(session_id)
            if previous is not None and previous[0] == effective_epoch and not volatile and seq <= previous[1]:
                return False
            if volatile:
                offset = message.get("offset")
                content_identity = ""
                if offset is None:
                    # Volatile state/usage frames can share seq without an
                    # offset.  Their stable payload identity distinguishes
                    # legitimate phase transitions while still suppressing
                    # byte-for-byte replay.
                    try:
                        content_identity = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    except (TypeError, ValueError):
                        content_identity = repr(body)
                replay_key = (
                    session_id,
                    effective_epoch,
                    seq,
                    _str(body.get("agentId")) or "main",
                    _str(body.get("turnId")),
                    _str(body.get("messageId") or body.get("itemId") or body_type),
                    offset,
                    content_identity,
                )
                if replay_key in self._volatile_replay_keys:
                    return False
                self._volatile_replay_keys.add(replay_key)
                self._volatile_replay_order.append(replay_key)
                while len(self._volatile_replay_order) > self.volatile_replay_window:
                    expired = self._volatile_replay_order.popleft()
                    self._volatile_replay_keys.discard(expired)
            if not volatile:
                self._stable_event_cursors[session_id] = (effective_epoch, seq)
            if not isinstance(current, dict) or (not current_epoch and epoch) or seq > int(current.get("seq") or -1):
                self._session_cursors[session_id] = {"epoch": effective_epoch, "seq": seq}
        return True

    def _request_recovery(self, error: str) -> None:
        """Coalesce every socket failure into one bounded recovery owner."""
        with self._lifecycle_lock:
            if self._closed or self.process is None or self.process.poll() is not None:
                return
            self._recovery_requested = True
            thread = self._recovery_thread
            if thread is not None and thread.is_alive():
                return
            self._recovery_generation += 1
            generation = self._recovery_generation
            thread = threading.Thread(
                target=self._recovery_loop,
                args=(generation, str(error)),
                daemon=True,
            )
            self._recovery_thread = thread
            thread.start()

    def _recovery_loop(self, generation: int, original_error: str) -> None:
        last_error = original_error
        try:
            with self._lifecycle_lock:
                if generation == self._recovery_generation:
                    self._recovery_requested = False
            for attempt in range(self.recovery_attempts):
                if attempt and self.recovery_backoff:
                    time.sleep(self.recovery_backoff * (2 ** (attempt - 1)))
                with self._lifecycle_lock:
                    if self._closed or generation != self._recovery_generation:
                        return
                    if self.process is None or self.process.poll() is not None:
                        break
                try:
                    self._connect_ws_locked()
                    with self._lifecycle_lock:
                        ready_event = self._transport_ready_events.get(self._ws_generation)
                    # Do not tell the UI that the transport recovered until
                    # hello/subscription ack has established the stream.
                    if ready_event is None or not ready_event.wait(timeout=self.ws_connect_timeout):
                        raise KimiServerError("kimi websocket recovery acknowledgement timed out")
                    with self._lifecycle_lock:
                        if self._closed or generation != self._recovery_generation:
                            return
                        if not self._ws_is_usable_locked():
                            raise KimiServerError("replacement websocket disconnected during recovery")
                        self._recovery_requested = False
                        self._active_transport_fault_id = None
                    with self._lifecycle_lock:
                        recovered_sessions = sorted(self._subscribed_sessions)
                    self._enqueue_control(
                        {
                            "type": "transport_recovered",
                            "payload": {"session_ids": recovered_sessions},
                        }
                    )
                    return
                except Exception as exc:
                    last_error = str(exc)
            with self._lifecycle_lock:
                pending_handoff = self._recovery_requested
            if not pending_handoff:
                self._notify_transport_failure_once(
                    f"kimi websocket recovery exhausted: {last_error}"
                )
        finally:
            handoff = False
            with self._lifecycle_lock:
                if generation == self._recovery_generation:
                    self._recovery_thread = None
                    if self._recovery_requested and not self._closed and self._ws_is_usable_locked():
                        self._recovery_requested = False
                    elif self._recovery_requested and not self._closed and self.process is not None and self.process.poll() is None:
                        # A replacement reader can fail while this worker is
                        # returning.  Hand ownership to a fresh bounded round.
                        handoff = True
            if handoff:
                self._request_recovery(last_error)

    def _enqueue_control(self, message: dict[str, Any]) -> None:
        notify = False
        with self._lock:
            self._queue.append({"kind": "message", "message": message})
            self._enforce_queue_limit_locked()
            if not self._pending_notification:
                self._pending_notification = True
                notify = True
        if notify and self.on_message is not None:
            self.on_message({"type": "messages_pending"})

    def _enqueue_event(self, event: KimiEvent) -> None:
        message = {"type": "event", "payload": {"event": event_to_payload(event)}}
        notify = False
        with self._lock:
            if event.type == "agent_message_delta":
                event_data = event.data if isinstance(event.data, dict) else {}
                agent_id = _str(event_data.get("agent_id") or event_data.get("agentId"))
                source_kind = _str(event_data.get("source_kind")).split(".", 1)[0]
                key = (event.thread_id, event.turn_id, event.item_id, event.display_kind, agent_id, source_kind)
                last_entry = self._queue[-1] if self._queue else None
                can_merge = bool(last_entry and last_entry.get("kind") == "delta" and last_entry.get("key") == key)
                if can_merge:
                    previous_event = ((last_entry.get("message") or {}).get("payload") or {}).get("event") or {}
                    previous_data = previous_event.get("data") if isinstance(previous_event.get("data"), dict) else {}
                    previous_offset = previous_data.get("offset")
                    incoming_offset = (event.data or {}).get("offset") if isinstance(event.data, dict) else None
                    previous_text = str(previous_event.get("text") or "")
                    # Coalescing must not erase an offset gap.  Preserve the
                    # frame as its own queue entry so upper layers can mark
                    # that specific stream incomplete and reconcile via REST.
                    if isinstance(previous_offset, int) and isinstance(incoming_offset, int):
                        can_merge = incoming_offset <= previous_offset + len(previous_text)
                if can_merge:
                    merged = deepcopy(last_entry["message"])
                    merged_event = (merged.get("payload") or {}).get("event") or {}
                    existing_text = str(merged_event.get("text") or "")
                    incoming_text = event.text
                    existing_data = merged_event.get("data") if isinstance(merged_event.get("data"), dict) else {}
                    incoming_data = event.data if isinstance(event.data, dict) else {}
                    start_offset = existing_data.get("offset")
                    incoming_offset = incoming_data.get("offset")
                    if isinstance(start_offset, int) and isinstance(incoming_offset, int):
                        relative_offset = incoming_offset - start_offset
                        if relative_offset < len(existing_text):
                            overlap = min(len(existing_text) - relative_offset, len(incoming_text))
                            if relative_offset >= 0 and existing_text[relative_offset:relative_offset + overlap] == incoming_text[:overlap]:
                                incoming_text = incoming_text[overlap:]
                    merged_event["text"] = existing_text + incoming_text
                    if "raw_text" in merged_event or event.raw_text:
                        existing_raw_text = str(merged_event.get("raw_text") or "")
                        raw_text = event.raw_text
                        if incoming_text != event.text and raw_text == event.text:
                            raw_text = incoming_text
                        merged_event["raw_text"] = existing_raw_text + raw_text
                    last_entry["message"] = merged
                else:
                    self._queue.append({"kind": "delta", "key": key, "message": message})
                    self._enforce_queue_limit_locked()
            else:
                self._queue.append({"kind": "message", "message": message})
                self._enforce_queue_limit_locked()
            if not self._pending_notification:
                self._pending_notification = True
                notify = True
        if notify and self.on_message is not None:
            self.on_message({"type": "messages_pending"})

    def _enforce_queue_limit_locked(self) -> None:
        overflow = len(self._queue) - self.queue_limit
        if overflow <= 0:
            return
        dropped = 0
        for _ in range(overflow):
            entry = self._queue.popleft()
            # A previous overflow warning carries its own dropped count; folding
            # it in keeps the cumulative total accurate instead of reporting
            # only this pass's overflow.
            if "dropped" in entry:
                dropped += int(entry["dropped"])
            else:
                dropped += 1
        self._queue.appendleft(
            {
                "kind": "message",
                "dropped": dropped,
                "message": {
                    "type": "event",
                    "payload": {
                        "event": event_to_payload(
                            KimiEvent(type="notification", display_kind="warning",
                                      text=f"kimi event queue overflow, dropped {dropped} oldest events")
                        )
                    },
                },
            }
        )

    def _notify_exit(self, proc: Any | None = None) -> None:
        if self.on_exit is None:
            return
        if proc is None:
            proc = self.process
        returncode = proc.poll() if proc is not None else None
        try:
            self.on_exit(returncode)
        except Exception:
            pass

    @staticmethod
    def _next_id() -> str:
        return uuid.uuid4().hex

    @staticmethod
    def _is_ws_timeout(exc: BaseException) -> bool:
        if isinstance(exc, TimeoutError):
            return True
        message = str(exc).lower()
        return "timed out" in message or "timeout" in message

    @staticmethod
    def _creationflags() -> int:
        if os.name != "nt":
            return 0
        return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
