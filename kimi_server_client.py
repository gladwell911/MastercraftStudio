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

WS_PATH = "/api/v1/ws"


class KimiServerError(RuntimeError):
    """Raised for spawn, auth, and REST failures with server context."""


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


def _payload_of(message: dict[str, Any]) -> dict[str, Any]:
    payload = message.get("payload")
    return payload if isinstance(payload, dict) else {}


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

    base: dict[str, Any] = {"thread_id": session_id, "data": {"seq": seq}}

    if body_type == "assistant.delta":
        return KimiEvent(
            type="agent_message_delta",
            text=_str(body.get("delta") or body.get("text")),
            raw_text=_str(body.get("delta") or body.get("text")),
            turn_id=_str(body.get("turnId")),
            item_id=_str(body.get("messageId") or body.get("itemId")),
            display_kind="assistant",
            **base,
        )
    if body_type == "thinking.delta":
        return KimiEvent(
            type="agent_message_delta",
            text=_str(body.get("delta") or body.get("text")),
            raw_text=_str(body.get("delta") or body.get("text")),
            turn_id=_str(body.get("turnId")),
            item_id=_str(body.get("messageId") or body.get("itemId")),
            display_kind="thinking",
            **base,
        )
    if body_type == "turn.started":
        return KimiEvent(
            type="turn_started",
            turn_id=_str(body.get("turnId")),
            text=_str(body.get("prompt")),
            **base,
        )
    if body_type == "turn.ended":
        reason = _str(body.get("reason"))
        error = body.get("error") if isinstance(body.get("error"), dict) else {}
        return KimiEvent(
            type="turn_completed",
            turn_id=_str(body.get("turnId")),
            status="completed" if reason in ("completed", "done", "success", "") else reason,
            text=_str(error.get("message")),
            data={**base["data"], "reason": reason, "error": error},
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
        return KimiEvent(
            type="agent_message_delta",
            turn_id=_str(body.get("turnId")),
            item_id=_str(body.get("toolCallId") or body.get("callId") or body.get("id")),
            text=_str(body.get("delta") or body.get("output") or body.get("text")),
            display_kind="commentary",
            **base,
        )
    if body_type == "tool.call.delta":
        # Partial JSON argument fragments; not user-visible, skip.
        return None
    if body_type in ("tool.result", "shell.completed"):
        return KimiEvent(
            type="item_completed",
            turn_id=_str(body.get("turnId")),
            item_id=_str(body.get("toolCallId") or body.get("callId") or body.get("id")),
            title=_str(body.get("description") or body.get("title") or body.get("name")),
            command=_str(body.get("command")),
            exit_code=body.get("exitCode") if isinstance(body.get("exitCode"), int) else None,
            status=_str(body.get("status")) or "completed",
            text=_str(body.get("summary") or body.get("output"))[:2000],
            display_kind="command" if body_type == "shell.completed" else "tool",
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
        return KimiEvent(
            type="turn_completed",
            turn_id=_str(body.get("turnId")),
            status="interrupted",
            **base,
        )
    if body_type == "prompt.completed":
        reason = _str(body.get("reason"))
        return KimiEvent(
            type="turn_completed",
            turn_id=_str(body.get("turnId")),
            status="completed" if reason in ("completed", "done", "success", "") else reason or "completed",
            data={**base["data"], "prompt_id": _str(body.get("promptId")), "reason": reason},
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
            **base,
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
    ) -> None:
        self.on_message = on_message
        self.on_exit = on_exit
        self.process_factory = process_factory
        self.http_session_factory = http_session_factory
        self.ws_factory = ws_factory
        self.launch_command = list(launch_command) if launch_command else None
        self.port = int(port) if port else None
        self.token = str(token) if token is not None else None
        self.queue_limit = max(int(queue_limit or 0), 1)
        self.health_timeout = float(health_timeout)
        self.rest_timeout = float(rest_timeout)
        self.start_reader_thread = bool(start_reader_thread)

        self.process = None
        self.base_url = ""
        self._http: Any = None
        self._ws: Any = None
        self._queue: deque[dict[str, Any]] = deque()
        self._lock = threading.Lock()
        self._lifecycle_lock = threading.RLock()
        self._send_lock = threading.Lock()
        self._ws_thread: threading.Thread | None = None
        self._banner_thread: threading.Thread | None = None
        self._closed = False
        self._started = False
        self._pending_notification = False
        self._subscribed_sessions: set[str] = set()
        self._banner_lines: deque[str] = deque(maxlen=100)

    # ------------------------------------------------------------------
    # lifecycle

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._started and self.process is not None and self.process.poll() is None:
                self._closed = False
                if not self._ws_is_usable_locked():
                    self._connect_ws_locked()
                return
            self._closed = False
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
            self._wait_for_health()
            if self.token is None:
                self.token = self._find_token()
            if not self.token:
                self._abort_start("kimi server token unavailable")
            self._connect_ws_locked()
            self._started = True

    def close(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            ws = self._ws
            self._ws = None
            self._close_ws_locked(ws)
            proc = self.process
            if proc is None:
                return
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
        self.subscribe([session_id])
        return session_id

    def submit_prompt(self, session_id: str, content_blocks: list[dict[str, Any]]) -> str:
        data = self._request_data(
            "POST",
            f"/api/v1/sessions/{session_id}/prompts",
            json_body={"content": list(content_blocks)},
        )
        return _str(data.get("prompt_id") or data.get("user_message_id"))

    def steer_prompts(self, session_id: str, prompt_ids: list[str]) -> bool:
        """Returns True when the server accepted the steer, False otherwise."""
        try:
            self._request_data(
                "POST",
                f"/api/v1/sessions/{session_id}/prompts:steer",
                json_body={"prompt_ids": [str(p) for p in prompt_ids if str(p or "").strip()]},
            )
            return True
        except KimiServerError:
            return False

    def list_messages(self, session_id: str) -> list[dict[str, Any]]:
        data = self._request_data("GET", f"/api/v1/sessions/{session_id}/messages")
        items = data.get("items")
        return list(items) if isinstance(items, list) else []

    def get_status(self, session_id: str) -> dict[str, Any]:
        return self._request_data("GET", f"/api/v1/sessions/{session_id}/status")

    def session_exists(self, session_id: str) -> bool:
        try:
            self._request_data("GET", f"/api/v1/sessions/{session_id}")
            return True
        except KimiServerError:
            return False

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

    def subscribe(self, session_ids: list[str]) -> None:
        ids = [str(s) for s in session_ids if str(s or "").strip()]
        if not ids:
            return
        with self._lifecycle_lock:
            self._subscribed_sessions.update(ids)
            self._send_ws({"type": "subscribe", "id": self._next_id(), "payload": {"session_ids": ids}})

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

    def _wait_for_health(self) -> None:
        deadline = time.monotonic() + self.health_timeout
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                tail = "\n".join(list(self._banner_lines)[-10:])
                self._abort_start(f"kimi server exited during startup (code {self.process.poll()})\n{tail}")
            try:
                resp = self._http.get(
                    f"{self.base_url}/api/v1/healthz",
                    headers=self._auth_headers(),
                    timeout=2,
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
            raise KimiServerError(f"kimi server {method} {path} failed: {exc}") from exc
        status = getattr(resp, "status_code", 0)
        if status < 200 or status >= 300:
            raise KimiServerError(f"kimi server {method} {path} returned {status}: {getattr(resp, 'text', '')[:300]}")
        return resp

    def _request_data(self, method: str, path: str, *, json_body: Any = None) -> dict[str, Any]:
        resp = self._request(method, path, json_body=json_body)
        try:
            payload = resp.json()
        except Exception as exc:
            raise KimiServerError(f"kimi server {method} {path} returned non-JSON") from exc
        if not isinstance(payload, dict):
            raise KimiServerError(f"kimi server {method} {path} returned unexpected payload")
        code = payload.get("code", 0)
        if code not in (0, None):
            raise KimiServerError(f"kimi server {method} {path} error {code}: {payload.get('msg')}")
        data = payload.get("data")
        return data if isinstance(data, dict) else ({} if data is None else {"value": data})

    def _connect_ws(self) -> None:
        with self._lifecycle_lock:
            self._connect_ws_locked()

    def _connect_ws_locked(self) -> None:
        """Install one authenticated socket while serializing lifecycle changes."""
        if self._closed:
            raise KimiServerError("kimi websocket is closing")
        if self.process is None or self.process.poll() is not None:
            raise KimiServerError("kimi server process is not running")

        stale_ws = self._ws
        self._ws = None
        self._close_ws_locked(stale_ws)

        url = f"ws://127.0.0.1:{self.port}{WS_PATH}"
        headers = [f"Authorization: Bearer {self.token}"] if self.token else []
        candidate = None
        try:
            if self.ws_factory is not None:
                candidate = self.ws_factory(url, headers)
            else:
                import websocket

                # Keep the websocket open for long periods without forcing a teardown
                # on normal idle timeouts; this avoids interpreting harmless recv()
                # timeouts as transport failures.
                candidate = websocket.create_connection(url, header=headers, timeout=None)
            self._send_on_socket_locked(
                candidate,
                {
                    "type": "client_hello",
                    "id": self._next_id(),
                    "payload": {"client_id": "zgwd", "subscriptions": [], "cursors": {}},
                },
            )
            subscriptions = sorted(self._subscribed_sessions)
            if subscriptions:
                self._send_on_socket_locked(
                    candidate,
                    {
                        "type": "subscribe",
                        "id": self._next_id(),
                        "payload": {"session_ids": subscriptions},
                    },
                )
        except Exception as exc:
            self._close_ws_locked(candidate)
            if isinstance(exc, KimiServerError):
                raise
            raise KimiServerError(f"kimi websocket connection failed: {exc}") from exc

        self._ws = candidate
        if self.start_reader_thread:
            self._ws_thread = threading.Thread(target=self._ws_loop, args=(candidate,), daemon=True)
            self._ws_thread.start()

    def _send_ws(self, message: dict[str, Any]) -> None:
        last_error: BaseException | None = None
        notify_error = ""
        with self._lifecycle_lock:
            if self._closed:
                raise KimiServerError("kimi websocket is closing")
            for attempt in range(2):
                try:
                    if not self._ws_is_usable_locked():
                        self._connect_ws_locked()
                    ws = self._ws
                    if ws is None:  # Defensive: _connect_ws_locked either installs or raises.
                        raise KimiServerError("kimi websocket is not connected")
                    self._send_on_socket_locked(ws, message)
                    return
                except Exception as exc:
                    last_error = exc
                    current = self._ws
                    if current is not None:
                        self._ws = None
                        self._close_ws_locked(current)
                    if attempt == 0 and not self._closed and self.process is not None and self.process.poll() is None:
                        continue
                    notify_error = f"kimi websocket transport failed: {exc}"
                    break
        if notify_error:
            self._enqueue_control({"type": "transport_error", "payload": {"error": notify_error}})
        raise KimiServerError(notify_error or "kimi websocket transport failed") from last_error

    def _send_on_socket_locked(self, ws: Any, message: dict[str, Any]) -> None:
        if ws is None:
            raise KimiServerError("kimi websocket is not connected")
        line = json.dumps(message, ensure_ascii=False)
        with self._send_lock:
            ws.send(line)

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

    def _ws_loop(self, ws: Any | None = None) -> None:
        if ws is None:
            with self._lifecycle_lock:
                ws = self._ws
        if ws is None:
            return
        while True:
            with self._lifecycle_lock:
                if self._closed or self._ws is not ws:
                    return
            try:
                raw = ws.recv()
            except Exception as exc:
                if self._is_ws_timeout(exc):
                    continue
                with self._lifecycle_lock:
                    if self._closed:
                        return
                self._invalidate_ws(ws, str(exc), notify=True)
                return
            if not raw:
                self._invalidate_ws(ws, "kimi websocket closed", notify=True)
                return
            try:
                message = json.loads(raw)
            except Exception:
                continue
            if not self._handle_ws_message(message, ws=ws):
                return

    def _handle_ws_message(self, message: dict[str, Any], *, ws: Any | None = None) -> bool:
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
                    self._send_on_socket_locked(
                        target,
                        {"type": "pong", "payload": {"nonce": payload.get("nonce")}},
                    )
                return True
            except Exception as exc:
                self._invalidate_ws(target, str(exc), notify=True)
                return False
        event = map_session_event(message)
        if event is None:
            return True
        self._enqueue_event(event)
        return True

    def _invalidate_ws(self, ws: Any, error: str, *, notify: bool) -> bool:
        """Mark only ``ws`` stale; an older reader must not clear its replacement."""
        invalidated = False
        with self._lifecycle_lock:
            if not self._closed and self._ws is ws:
                self._ws = None
                self._close_ws_locked(ws)
                invalidated = True
        if invalidated and notify:
            self._enqueue_control({"type": "transport_error", "payload": {"error": str(error)}})
        return invalidated

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
                key = (event.thread_id, event.turn_id, event.item_id, event.display_kind)
                last_entry = self._queue[-1] if self._queue else None
                if last_entry and last_entry.get("kind") == "delta" and last_entry.get("key") == key:
                    merged = deepcopy(last_entry["message"])
                    merged_event = (merged.get("payload") or {}).get("event") or {}
                    existing_text = str(merged_event.get("text") or "")
                    merged_event["text"] = existing_text + event.text
                    if "raw_text" in merged_event or event.raw_text:
                        merged_event["raw_text"] = str(merged_event.get("raw_text") or "") + event.raw_text
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
