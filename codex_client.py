import json
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


CODEX_MODEL_PREFIX = "codex/"
DEFAULT_CODEX_MODEL = "codex/main"
DEFAULT_INITIALIZE_TIMEOUT = 15
DEFAULT_REQUEST_TIMEOUT = 60
DEFAULT_CODEX_APPROVAL_POLICY = "never"
DEFAULT_CODEX_SANDBOX = "danger-full-access"


def is_codex_model(model: str) -> bool:
    return str(model or "").strip().startswith(CODEX_MODEL_PREFIX)


def resolve_codex_launch_command() -> list[str]:
    override = str(os.environ.get("CODEX_BIN") or "").strip()
    if override:
        return [override]

    if os.name == "nt":
        for candidate in ("codex.cmd", "codex.exe", "codex.bat", "codex"):
            resolved = shutil.which(candidate)
            if resolved:
                return [resolved]
        script_path = shutil.which("codex.ps1")
        if script_path:
            for shell_name in ("pwsh.exe", "pwsh", "powershell.exe", "powershell"):
                shell_path = shutil.which(shell_name)
                if shell_path:
                    return [shell_path, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script_path]
            raise FileNotFoundError("Found codex.ps1 but no pwsh/powershell executable is available.")

    resolved = shutil.which("codex")
    if resolved:
        return [resolved]
    raise FileNotFoundError("Codex CLI was not found. Install `codex` or set CODEX_BIN.")


def build_codex_app_server_command(cwd: str | None = None) -> list[str]:
    workspace = str(cwd or "").strip() or str(Path.cwd())
    return [
        *resolve_codex_launch_command(),
        "--dangerously-bypass-approvals-and-sandbox",
        "-C",
        workspace,
        "app-server",
        "--listen",
        "stdio://",
    ]


@dataclass
class CodexEvent:
    type: str
    thread_id: str = ""
    turn_id: str = ""
    item_id: str = ""
    text: str = ""
    phase: str = ""
    status: str = ""
    flags: list[str] = field(default_factory=list)
    request_id: str | int | None = None
    method: str = ""
    params: dict = field(default_factory=dict)
    data: dict = field(default_factory=dict)


class CodexAppServerClient:
    def __init__(
        self,
        on_event: Callable[[CodexEvent], None] | None = None,
        timeout: int = DEFAULT_REQUEST_TIMEOUT,
    ) -> None:
        self.on_event = on_event
        self.timeout = max(int(timeout or DEFAULT_REQUEST_TIMEOUT), 1)
        self._proc: subprocess.Popen[str] | None = None
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._send_lock = threading.Lock()
        self._request_lock = threading.Lock()
        self._request_seq = 1
        self._pending_requests: dict[str | int, dict] = {}
        self._initialized = False
        self._initializing = False
        self._closed = False

    def close(self) -> None:
        self._closed = True
        proc = self._proc
        if proc is None:
            return
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        self._proc = None

    def start_thread(
        self,
        cwd: str,
        approval_policy: str = DEFAULT_CODEX_APPROVAL_POLICY,
        sandbox: str = DEFAULT_CODEX_SANDBOX,
        personality: str = "pragmatic",
        ephemeral: bool = False,
    ) -> dict:
        params = {
            "cwd": str(cwd or "").strip() or str(Path.cwd()),
            "approvalPolicy": approval_policy,
            "sandbox": sandbox,
            "personality": personality,
            "ephemeral": bool(ephemeral),
        }
        return self.request("thread/start", params)

    def resume_thread(
        self,
        thread_id: str,
        approval_policy: str = DEFAULT_CODEX_APPROVAL_POLICY,
        sandbox: str = DEFAULT_CODEX_SANDBOX,
        personality: str = "pragmatic",
        cwd: str | None = None,
    ) -> dict:
        params = {
            "threadId": str(thread_id or "").strip(),
            "approvalPolicy": approval_policy,
            "sandbox": sandbox,
            "personality": personality,
        }
        if cwd:
            params["cwd"] = str(cwd).strip()
        return self.request("thread/resume", params)

    def read_thread(self, thread_id: str, include_turns: bool = True) -> dict:
        return self.request(
            "thread/read",
            {
                "threadId": str(thread_id or "").strip(),
                "includeTurns": bool(include_turns),
            },
        )

    def start_turn(self, thread_id: str, text: str) -> dict:
        return self.request(
            "turn/start",
            {
                "threadId": str(thread_id or "").strip(),
                "input": [{"type": "text", "text": str(text or "")}],
            },
        )

    def steer_turn(self, thread_id: str, expected_turn_id: str, text: str) -> dict:
        return self.request(
            "turn/steer",
            {
                "threadId": str(thread_id or "").strip(),
                "expectedTurnId": str(expected_turn_id or "").strip(),
                "input": [{"type": "text", "text": str(text or "")}],
            },
        )

    def respond_tool_request_user_input(self, request_id: str | int, answers: dict[str, list[str]]) -> None:
        payload = {
            "answers": {qid: {"answers": list(values or [])} for qid, values in dict(answers or {}).items()}
        }
        self._send_json({"id": request_id, "result": payload})

    def respond_command_approval(self, request_id: str | int, decision) -> None:
        self._send_json({"id": request_id, "result": {"decision": decision}})

    def respond_file_change_approval(self, request_id: str | int, decision: str) -> None:
        self._send_json({"id": request_id, "result": {"decision": decision}})

    def respond_permissions_approval(
        self,
        request_id: str | int,
        permissions: dict | None = None,
        scope: str = "turn",
    ) -> None:
        self._send_json(
            {
                "id": request_id,
                "result": {
                    "permissions": permissions if isinstance(permissions, dict) else {},
                    "scope": scope if scope in {"turn", "session"} else "turn",
                },
            }
        )

    def request(self, method: str, params: dict | None = None, timeout: int | None = None) -> dict:
        self._ensure_started()
        return self._request_internal(method, params=params, timeout=timeout)

    def _ensure_started(self) -> None:
        if self._closed:
            raise RuntimeError("Codex app-server has already been closed.")
        proc = self._proc
        if proc is not None and proc.poll() is None:
            if not self._initialized and not self._initializing:
                self._initialize()
            return

        self._proc = subprocess.Popen(
            build_codex_app_server_command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            bufsize=1,
            env=os.environ.copy(),
        )
        self._stdout_thread = threading.Thread(target=self._stdout_loop, daemon=True)
        self._stderr_thread = threading.Thread(target=self._stderr_loop, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()
        self._initialized = False
        self._initialize()

    def _initialize(self) -> None:
        if self._initialized or self._initializing:
            return
        self._initializing = True
        try:
            self._request_internal(
                "initialize",
                {
                    "clientInfo": {"name": "zgwd", "version": "1.0"},
                    "capabilities": {"experimentalApi": True},
                },
                timeout=DEFAULT_INITIALIZE_TIMEOUT,
            )
            self._send_json({"method": "initialized"})
            self._initialized = True
        finally:
            self._initializing = False

    def _next_request_id(self) -> int:
        with self._request_lock:
            request_id = self._request_seq
            self._request_seq += 1
        return request_id

    def _request_internal(self, method: str, params: dict | None = None, timeout: int | None = None) -> dict:
        request_id = self._next_request_id()
        waiter = threading.Event()
        with self._request_lock:
            self._pending_requests[request_id] = {"event": waiter, "result": None, "error": None}
        self._send_json({"id": request_id, "method": method, "params": params or {}})
        wait_seconds = max(int(timeout or self.timeout), 1)
        if not waiter.wait(wait_seconds):
            with self._request_lock:
                self._pending_requests.pop(request_id, None)
            raise RuntimeError(f"Codex app-server request timed out: {method}")
        with self._request_lock:
            state = self._pending_requests.pop(request_id, {})
        error = state.get("error")
        if error:
            message = str(error.get("message") or "unknown error")
            raise RuntimeError(f"Codex app-server request failed: {method}: {message}")
        result = state.get("result")
        return result if isinstance(result, dict) else {}

    def _send_json(self, payload: dict) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.poll() is not None:
            raise RuntimeError("Codex app-server is not running.")
        line = json.dumps(payload, ensure_ascii=False)
        with self._send_lock:
            proc.stdin.write(line + "\n")
            proc.stdin.flush()

    def _stdout_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            for raw_line in proc.stdout:
                line = str(raw_line or "").strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except Exception:
                    continue
                self._handle_message(message)
        finally:
            if not self._closed:
                self._emit_event(CodexEvent(type="transport_error", text="Codex app-server disconnected."))
            self._fail_pending_requests("Codex app-server disconnected.")

    def _stderr_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for raw_line in proc.stderr:
            line = str(raw_line or "").strip()
            if line:
                self._emit_event(CodexEvent(type="stderr", text=line))

    def _fail_pending_requests(self, message: str) -> None:
        with self._request_lock:
            for state in self._pending_requests.values():
                state["error"] = {"message": message}
            pending = list(self._pending_requests.values())
        for state in pending:
            event = state.get("event")
            if isinstance(event, threading.Event):
                event.set()

    def _handle_message(self, message: dict) -> None:
        if not isinstance(message, dict):
            return
        if "id" in message and ("result" in message or "error" in message) and "method" not in message:
            self._handle_response(message)
            return
        if "id" in message and "method" in message:
            self._emit_event(
                CodexEvent(
                    type="server_request",
                    request_id=message.get("id"),
                    method=str(message.get("method") or ""),
                    params=message.get("params") if isinstance(message.get("params"), dict) else {},
                    data=message,
                )
            )
            return
        method = str(message.get("method") or "").strip()
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        self._emit_protocol_event(method, params, message)

    def _handle_response(self, message: dict) -> None:
        request_id = message.get("id")
        with self._request_lock:
            state = self._pending_requests.get(request_id)
        if not state:
            return
        state["result"] = message.get("result")
        state["error"] = message.get("error")
        event = state.get("event")
        if isinstance(event, threading.Event):
            event.set()

    def _emit_protocol_event(self, method: str, params: dict, raw: dict) -> None:
        if method == "thread/started":
            thread = params.get("thread") if isinstance(params.get("thread"), dict) else {}
            self._emit_event(
                CodexEvent(
                    type="thread_started",
                    thread_id=str(thread.get("id") or ""),
                    data=thread,
                )
            )
            return
        if method == "thread/status/changed":
            status = params.get("status") if isinstance(params.get("status"), dict) else {}
            self._emit_event(
                CodexEvent(
                    type="thread_status_changed",
                    thread_id=str(params.get("threadId") or ""),
                    status=str(status.get("type") or ""),
                    flags=list(status.get("activeFlags") or []),
                    data=status,
                )
            )
            return
        if method == "turn/started":
            turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
            self._emit_event(
                CodexEvent(
                    type="turn_started",
                    thread_id=str(params.get("threadId") or ""),
                    turn_id=str(turn.get("id") or ""),
                    status=str(turn.get("status") or ""),
                    data=turn,
                )
            )
            return
        if method == "turn/completed":
            turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
            self._emit_event(
                CodexEvent(
                    type="turn_completed",
                    thread_id=str(params.get("threadId") or ""),
                    turn_id=str(turn.get("id") or ""),
                    status=str(turn.get("status") or ""),
                    text=str((turn.get("error") or {}).get("message") or ""),
                    data=turn,
                )
            )
            return
        if method == "turn/plan/updated":
            self._emit_event(
                CodexEvent(
                    type="plan_updated",
                    thread_id=str(params.get("threadId") or ""),
                    turn_id=str(params.get("turnId") or ""),
                    text=str(params.get("explanation") or ""),
                    data=params,
                )
            )
            return
        if method == "turn/diff/updated":
            self._emit_event(
                CodexEvent(
                    type="diff_updated",
                    thread_id=str(params.get("threadId") or ""),
                    turn_id=str(params.get("turnId") or ""),
                    text=str(params.get("diff") or ""),
                    data=params,
                )
            )
            return
        if method == "item/agentMessage/delta":
            self._emit_event(
                CodexEvent(
                    type="agent_message_delta",
                    thread_id=str(params.get("threadId") or ""),
                    turn_id=str(params.get("turnId") or ""),
                    item_id=str(params.get("itemId") or ""),
                    text=str(params.get("delta") or ""),
                    data=params,
                )
            )
            return
        if method in {"item/started", "item/completed"}:
            item = params.get("item") if isinstance(params.get("item"), dict) else {}
            self._emit_event(
                CodexEvent(
                    type="item_completed" if method.endswith("completed") else "item_started",
                    thread_id=str(params.get("threadId") or ""),
                    turn_id=str(params.get("turnId") or ""),
                    item_id=str(item.get("id") or ""),
                    text=str(item.get("text") or ""),
                    phase=str(item.get("phase") or ""),
                    status=str(item.get("type") or ""),
                    data=item,
                )
            )
            return
        if method == "error":
            self._emit_event(
                CodexEvent(
                    type="error",
                    text=str(params.get("message") or ""),
                    data=params,
                )
            )
            return
        self._emit_event(CodexEvent(type="notification", method=method, params=params, data=raw))

    def _emit_event(self, event: CodexEvent) -> None:
        if callable(self.on_event):
            self.on_event(event)
