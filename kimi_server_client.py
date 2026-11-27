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

import hashlib
import os
import shutil
import socket
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import requests

from kimi_runtime import child_environment, runtime_path, validate_runtime_version
from kimi_server_api import KimiServerApiMixin
from kimi_server_events import (
    KimiEvent,
    KimiServerError,
    MAX_DIAGNOSTIC_TEXT,
    _provider_origin_timestamp,
    _str,
    event_from_payload,
    event_to_payload,
    kimi_snapshot_total_tokens,
    map_session_event,
)
from kimi_server_queue import KimiServerQueueMixin
from kimi_server_v2 import KimiServerV2Mixin
from kimi_server_websocket import KimiServerWebSocketMixin, WS_PATH
from kimi_transcript import Transcript as Transcript

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


def is_kimi_model(model: str) -> bool:
    return str(model or "").strip().startswith(KIMI_MODEL_PREFIX)


def kimi_model_to_server_alias(model: str) -> str:
    text = str(model or "").strip()
    if text in KIMI_SERVER_MODEL_ALIASES:
        return KIMI_SERVER_MODEL_ALIASES[text]
    return KIMI_SERVER_MODEL_ALIASES[DEFAULT_KIMI_MODEL]


def resolve_kimi_launch_command() -> list[str]:
    """Resolve the verified local Kimi CLI."""
    return _resolve_kimi_runtime()[0]


def _resolve_kimi_runtime():
    try:
        path = runtime_path()
        version = validate_runtime_version(path)
        return [str(path)], version
    except RuntimeError as exc:
        raise KimiServerError(str(exc)) from exc



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


class KimiServerClient(KimiServerApiMixin, KimiServerV2Mixin, KimiServerWebSocketMixin, KimiServerQueueMixin):
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
        self.protocol_version = 1
        self._server_api = False
        self._server_api_turns = {}
        self._server_api_previous_turns = {}
        self._server_api_item_restrictions = {}
        self._server_api_pending = {}
        self._server_api_pending_checks = {}
        self._server_api_terminal_jobs = set()
        self._server_api_terminal_pool = None
        self._server_api_interactions = {}
        self._v2_transcripts = {}
        self._v2_completed = set()
        self._v2_completed_order = deque()
        self._v2_started = set()
        self._v2_frame_texts = {}
        self._v2_tool_frames = {}
        self._v2_ready_sessions = set()
        self._v2_ready_generations = {}
        self._v2_request_generations = {}
        self._v2_subscription_events = {}
        self._v2_usage_windows = {}
        self._v2_usage_events = {}
        self._v2_usage_ledgers = {}
        self.on_message = on_message
        self.on_exit = on_exit
        self.process_factory = process_factory
        self.http_session_factory = http_session_factory
        self.ws_factory = ws_factory
        self.launch_command = list(launch_command) if launch_command else None
        self.runtime_version = ""
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
        self._session_steps: dict[tuple[str, str, str, str], dict[str, Any]] = {}
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
                    if self.launch_command:
                        command = list(self.launch_command)
                    else:
                        command, runtime_version = _resolve_kimi_runtime()
                        self.runtime_version = runtime_version
                        self._server_api = runtime_version == "2.6.3"
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
                            env=child_environment(),
                        )
                    self._banner_thread = threading.Thread(target=self._banner_loop, daemon=True)
                    self._banner_thread.start()
                    http = self.http_session_factory() if self.http_session_factory else requests.Session()
                    if not self.http_session_factory:
                        http.trust_env = False
                    self._http = http
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
            self._clear_v2_readiness()
            self._v2_transcripts.clear()
            self._v2_completed.clear()
            self._v2_completed_order.clear()
            self._v2_started.clear()
            self._v2_frame_texts.clear()
            self._v2_tool_frames.clear()
            self._server_api_item_restrictions.clear()
            self._server_api_previous_turns.clear()
            proc = self.process
            pool = self._server_api_terminal_pool
            if pool is not None:
                pool.shutdown(wait=False, cancel_futures=True)
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
        permission_mode: str | None = None,
        goal_objective: str = "",
    ) -> str:
        if permission_mode is None:
            permission_mode = "manual" if self._server_api else "auto"
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

    def submit_prompt(self, session_id: str, content_blocks: list[dict[str, Any]], *,
                      prompt_id: str = "") -> str:
        body: dict[str, Any] = {"content": list(content_blocks)}
        if self.protocol_version == 2:
            self._require_v2_ready(session_id)
            if prompt_id:
                body["prompt_id"] = prompt_id
        data = self._request_data(
            "POST",
            f"/api/v1/sessions/{session_id}/prompts",
            json_body=body,
        )
        returned_id = _str(data.get("prompt_id") or data.get("user_message_id"))
        if not returned_id:
            raise KimiServerError(
                f"submit_prompt response missing prompt_id/user_message_id: {data!r}"
            )
        if self.protocol_version == 2 and prompt_id and returned_id != prompt_id:
            raise KimiServerError("Kimi returned a different prompt ID; recovering the submitted identity",
                                  result_unknown=True)
        return returned_id

    def steer_prompts(self, session_id: str, prompt_ids: list[str]) -> bool | None:
        """Return accepted/rejected/unknown without guessing ambiguous POSTs."""
        if self._server_api:
            # The normal POST already accepted this independent queued prompt.
            # The new dialect has not demonstrated same-turn alias semantics.
            return False
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

    def list_sessions(self, *, before_id: str | None = None) -> dict[str, Any]:
        params = {"page_size": 100}
        if before_id:
            params["before_id"] = before_id
        return self._request_data("GET", "/api/v1/sessions", params=params)

    def get_session(self, session_id: str, *, timeout: float | None = None) -> dict[str, Any]:
        return self._request_data("GET", f"/api/v1/sessions/{session_id}", timeout=timeout)

    def set_session_title(self, session_id: str, title: str) -> None:
        sid = quote(session_id, safe="")
        self._request_data("POST", f"/api/v1/sessions/{sid}/profile", json_body={"title": title})

    def delete_session(self, session_id: str) -> None:
        sid = quote(session_id, safe="")
        self._request_data("POST", f"/api/v1/sessions/{sid}:delete", json_body={})

    def list_messages(
        self,
        session_id: str,
        *,
        timeout: float | None = None,
        before_id: str | None = None,
    ) -> list[dict[str, Any]]:
        params = {"before_id": str(before_id)} if before_id else None
        data = self._request_data(
            "GET",
            f"/api/v1/sessions/{session_id}/messages",
            timeout=timeout,
            params=params,
        )
        items = data.get("items")
        return list(items) if isinstance(items, list) else []

    def get_status(self, session_id: str, *, timeout: float | None = None) -> dict[str, Any]:
        if self.protocol_version == 2:
            raise KimiServerError("Kimi 2.5.3 legacy status is unsupported; use prompt transcript recovery")
        return self._request_data("GET", f"/api/v1/sessions/{session_id}/status", timeout=timeout)

    def get_snapshot(self, session_id: str, *, timeout: float | None = None) -> dict[str, Any]:
        if self.protocol_version == 2:
            raise KimiServerError("Kimi 2.5.3 context snapshot is currently unsupported")
        return self._request_data("GET", f"/api/v1/sessions/{session_id}/snapshot", timeout=timeout)

    def get_oauth_usage(self, *, timeout: float | None = None) -> dict[str, Any]:
        return self._request_data("GET", "/api/v1/oauth/usage", timeout=timeout)

    def get_auth(self, *, timeout: float | None = None) -> dict[str, Any]:
        return self._request_data("GET", "/api/v1/auth", timeout=timeout)

    def get_oauth_userinfo(self, *, timeout: float | None = None) -> dict[str, Any]:
        return self._request_data("GET", "/api/v1/oauth/userinfo", timeout=timeout)

    def session_exists(self, session_id: str, *, timeout: float | None = None) -> bool:
        try:
            self._request_data("GET", f"/api/v1/sessions/{session_id}", timeout=timeout)
            return True
        except KimiServerError as exc:
            if exc.status_code in {404, 40401}:
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
        if self.protocol_version == 2:
            self.answer_interaction(session_id, approval_id, {"kind": "approval", **body})
        else:
            self._request_data(
                "POST",
                f"/api/v1/sessions/{session_id}/approvals/{approval_id}",
                json_body=body,
            )

    def answer_interaction(self, session_id: str, interaction_id: str, response: dict[str, Any]) -> None:
        sid = quote(session_id, safe="")
        iid = quote(interaction_id, safe="")
        try:
            data = self._request_data("POST", f"/api/v1/sessions/{sid}/interactions/{iid}", json_body=response)
        except KimiServerError as exc:
            if exc.status_code is None:
                exc.result_unknown = True
            raise
        if self._server_api:
            outcome = data.get("outcome")
            intended = (response.get("decision") if response.get("kind") == "approval"
                        else ("dismissed" if response.get("dismissed") else "answered"))
            # In 2.6.3 the interaction-specific URL binds the acknowledgment.
            # Its minimal response contains ended_at and outcome.
            if (data.get("interaction_id", interaction_id) != interaction_id
                    or data.get("status", "ended") != "ended"
                    or outcome not in ("approved", "rejected", "cancelled", "answered", "dismissed")):
                raise KimiServerError("Kimi did not confirm the interaction response", result_unknown=True)
            if outcome != intended:
                raise KimiServerError("Kimi ended the interaction with a different outcome", status_code=40902)
            return
        if data.get("outcome") not in {"approved", "rejected", "cancelled", "answered", "dismissed"}:
            raise KimiServerError("Kimi did not confirm the interaction response", result_unknown=True)

    def interaction_state(self, session_id: str, interaction_id: str, agent_id: str = "main") -> str:
        if self._server_api:
            key = (session_id, interaction_id, agent_id)
            with self._lifecycle_lock:
                ended = self._server_api_interactions.get(key)
            if ended is not None:
                return ended or "ended"
            sid = quote(session_id, safe="")
            try:
                data = self._request_data("GET", f"/api/v1/sessions/{sid}", timeout=min(self.rest_timeout, 5.0))
            except KimiServerError as exc:
                if exc.status_code in {404, 40401}:
                    return "ended"
                raise
            with self._lifecycle_lock:
                ended = self._server_api_interactions.get(key)
            if ended is not None:
                return ended or "ended"
            if "pending_interaction" not in data:
                return ""
            pending = data["pending_interaction"]
            if pending in (None, "none"):
                return "ended"
            if pending not in ("approval", "question"):
                return ""
            return self._server_api_check_pending(session_id, interaction_id, agent_id, pending)
        page = self.get_transcript(session_id, agent_id=agent_id, timeout=min(self.rest_timeout, 5.0))
        return next((_str(row.get("state")) for row in page.get("interactions", [])
                     if row.get("interactionId") == interaction_id), "")

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


    def abort(self, session_id: str, prompt_id: str = "") -> None:
        if self.protocol_version == 2:
            if not session_id or not prompt_id:
                raise KimiServerError("Kimi cancellation requires an exact active prompt identity")
            sid = quote(session_id, safe="")
            pid = quote(prompt_id, safe="")
            data = self._request_data("POST", f"/api/v1/sessions/{sid}/prompts/{pid}:abort")
            if data.get("aborted") is not True:
                raise KimiServerError("Kimi did not accept the prompt cancellation")
            return
        payload: dict[str, Any] = {"session_id": str(session_id)}
        if prompt_id:
            payload["prompt_id"] = str(prompt_id)
        self._send_ws({"type": "abort", "id": self._next_id(), "payload": payload})


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

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        timeout: float | None = None,
        params: dict[str, str] | None = None,
    ):
        if self._http is None:
            raise KimiServerError("kimi client is not started")
        budget = timeout if timeout is not None else self.rest_timeout
        deadline = time.monotonic() + budget
        for attempt in range(2):
            try:
                resp = self._http.request(
                    method, f"{self.base_url}{path}", json=json_body, params=params,
                    headers=self._auth_headers(), timeout=max(0.001, deadline - time.monotonic()),
                )
                break
            except KimiServerError:
                raise
            except Exception as exc:
                # Only safe reads retry a transport failure; an ambiguous POST
                # remains owned by transcript reconciliation and is never replayed.
                transport = isinstance(exc, (ConnectionResetError, requests.ConnectionError))
                if (str(method).upper() == "GET" and transport and attempt == 0
                        and time.monotonic() < deadline):
                    if not self.http_session_factory:
                        self._http.close()
                        http = requests.Session()
                        http.trust_env = False
                        self._http = http
                    continue
                raise KimiServerError(
                    f"kimi local REST {method} {path} failed: {exc}",
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
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        resp = self._request(method, path, json_body=json_body, timeout=timeout, params=params)
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
    def _creationflags() -> int:
        if os.name != "nt":
            return 0
        return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
