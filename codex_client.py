import json
import os
import shutil
import subprocess
import threading
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
import tempfile

from context_usage import context_window_for_model, normalize_context_usage


CODEX_MODEL_PREFIX = "codex/"
DEFAULT_CODEX_MODEL = "codex/main"
CODEX_MODEL_CONFIGS = {
    "codex/gpt-5.4-medium": {
        "model": "gpt-5.4",
        "model_reasoning_effort": "medium",
    },
    "codex/gpt-5.3-codex-spark-high": {
        "model": "gpt-5.3-codex-spark",
        "model_reasoning_effort": "high",
    },
}
CODEX_MODEL_BY_CONTEXT_WINDOW = {
    121600: "gpt-5.3-codex-spark",
    258400: "gpt-5-codex",
}
DEFAULT_INITIALIZE_TIMEOUT = 45
DEFAULT_REQUEST_TIMEOUT = 60
DEFAULT_CODEX_TURN_TIMEOUT = 300
DEFAULT_CODEX_APPROVAL_POLICY = "never"
DEFAULT_CODEX_SANDBOX = "danger-full-access"


def is_codex_model(model: str) -> bool:
    return str(model or "").strip().startswith(CODEX_MODEL_PREFIX)


def codex_cli_config_for_model(model: str) -> dict[str, str]:
    return dict(CODEX_MODEL_CONFIGS.get(str(model or "").strip(), {}))


def codex_model_label_for_model(model: str) -> str:
    config = codex_cli_config_for_model(model)
    model_name = str(config.get("model") or "").strip()
    effort = str(config.get("model_reasoning_effort") or "").strip()
    if model_name and effort:
        return f"{model_name} {effort}"
    return model_name


def resolve_codex_launch_command() -> list[str]:
    override = str(os.environ.get("CODEX_BIN") or "").strip()
    if override:
        return [override]

    if os.name == "nt":
        for candidate in ("codex.exe", "codex.cmd", "codex.bat", "codex"):
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


def build_codex_app_server_command(cwd: str | None = None, codex_model: str = DEFAULT_CODEX_MODEL) -> list[str]:
    command = [
        *resolve_codex_launch_command(),
        "app-server",
        "--listen",
        "stdio://",
        "--analytics-default-enabled",
    ]
    for key, value in codex_cli_config_for_model(codex_model).items():
        command.extend(["-c", f'{key}="{value}"'])
    return command


def _codex_home_seed_files() -> tuple[str, ...]:
    return ("auth.json", "cap_sid", "config.toml", "models_cache.json", "version.json")


def _copy_codex_home_seed(source_home: Path, target_home: Path) -> None:
    for filename in _codex_home_seed_files():
        source_file = source_home / filename
        if not source_file.exists() or not source_file.is_file():
            continue
        try:
            shutil.copy2(source_file, target_home / filename)
        except Exception:
            pass


def _link_or_copy_path(source: Path, target: Path) -> None:
    try:
        if source.is_dir():
            target.symlink_to(source, target_is_directory=True)
        else:
            target.symlink_to(source)
        return
    except Exception:
        pass
    try:
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
    except Exception:
        pass


def _merge_user_skills_into_codex_home(source_home: Path, target_home: Path) -> None:
    source_skills = source_home / "skills"
    if not source_skills.exists() or not source_skills.is_dir():
        return
    try:
        target_skills = target_home / "skills"
        target_skills.mkdir(parents=True, exist_ok=True)
    except Exception:
        return
    try:
        entries = list(source_skills.iterdir())
    except Exception:
        return
    for source_entry in entries:
        target_entry = target_skills / source_entry.name
        if target_entry.exists() or target_entry.is_symlink():
            continue
        _link_or_copy_path(source_entry, target_entry)


def _strip_utf8_bom_from_skill_markdown(skills_root: Path) -> None:
    if not skills_root.exists() or not skills_root.is_dir():
        return
    try:
        entries = list(skills_root.iterdir())
    except Exception:
        return
    skill_files = []
    direct_skill = skills_root / "SKILL.md"
    if direct_skill.exists() and direct_skill.is_file():
        skill_files.append(direct_skill)
    for entry in entries:
        candidate = entry / "SKILL.md"
        if candidate.exists() and candidate.is_file():
            skill_files.append(candidate)
    for skill_file in skill_files:
        try:
            data = skill_file.read_bytes()
        except Exception:
            continue
        if not data.startswith(b"\xef\xbb\xbf"):
            continue
        try:
            skill_file.write_bytes(data[3:])
        except Exception:
            continue


def build_codex_app_server_env(cwd: str | None = None) -> tuple[dict[str, str], Path | None]:
    env = os.environ.copy()
    workspace = Path(str(cwd or "").strip() or Path.cwd())
    try:
        codex_home = workspace / ".codex-home"
        codex_home.mkdir(parents=True, exist_ok=True)
    except Exception:
        codex_home = Path(tempfile.mkdtemp(prefix=".codex-home-"))
    source_home = Path(os.environ.get("USERPROFILE") or str(Path.home())) / ".codex"
    _copy_codex_home_seed(source_home, codex_home)
    _merge_user_skills_into_codex_home(source_home, codex_home)
    _strip_utf8_bom_from_skill_markdown(codex_home / "skills")
    env["CODEX_HOME"] = str(codex_home)
    return env, codex_home


def _candidate_codex_home_dirs(cwd: str | None = None) -> list[Path]:
    candidates: list[Path] = []
    if cwd:
        candidates.append(Path(str(cwd)).expanduser() / ".codex-home")
    env_home = str(os.environ.get("CODEX_HOME") or "").strip()
    if env_home:
        candidates.append(Path(env_home).expanduser())
    candidates.append(Path(os.environ.get("USERPROFILE") or str(Path.home())) / ".codex")
    out: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate)
    return out


def read_codex_cli_model_label(cwd: str | None = None) -> str:
    for codex_home in _candidate_codex_home_dirs(cwd):
        config_path = codex_home / "config.toml"
        if not config_path.is_file():
            continue
        try:
            data = tomllib.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        model = str(data.get("model") or "").strip()
        effort = str(data.get("model_reasoning_effort") or data.get("reasoning_effort") or "").strip()
        if model and effort:
            return f"{model} {effort}"
        if model:
            return model
    return ""


def _windows_popen_kwargs() -> dict:
    if os.name != "nt":
        return {}
    kwargs = {}
    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0)
    if creationflags:
        kwargs["creationflags"] = creationflags
    startupinfo_cls = getattr(subprocess, "STARTUPINFO", None)
    use_show_window = int(getattr(subprocess, "STARTF_USESHOWWINDOW", 0) or 0)
    hide_window = int(getattr(subprocess, "SW_HIDE", 0) or 0)
    if startupinfo_cls is not None:
        try:
            startupinfo = startupinfo_cls()
            if use_show_window:
                startupinfo.dwFlags |= use_show_window
            if hide_window:
                startupinfo.wShowWindow = hide_window
            kwargs["startupinfo"] = startupinfo
        except Exception:
            pass
    return kwargs


@dataclass
class CodexEvent:
    type: str
    thread_id: str = ""
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


def _first_non_empty(*values) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _item_title(item: dict) -> str:
    if not isinstance(item, dict):
        return ""
    return _first_non_empty(item.get("title"), item.get("name"), item.get("label"))


def _item_command(item: dict) -> str:
    if not isinstance(item, dict):
        return ""
    return _first_non_empty(item.get("command"), item.get("commandLine"), item.get("cmd"))


def _item_exit_code(item: dict) -> int | None:
    if not isinstance(item, dict):
        return None
    value = item.get("exitCode")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _item_file_change_summary(item: dict) -> str:
    if not isinstance(item, dict):
        return "File change"
    path = _first_non_empty(item.get("path"), item.get("filePath"), item.get("file"), item.get("uri"))
    if path:
        return f"Changed {path}"
    files = item.get("files")
    if isinstance(files, list) and files:
        first_file = files[0] if isinstance(files[0], dict) else {}
        first_path = _first_non_empty(
            first_file.get("path"),
            first_file.get("filePath"),
            first_file.get("file"),
            first_file.get("uri"),
        )
        if first_path:
            remaining = max(len(files) - 1, 0)
            if remaining:
                return f"Changed {first_path} and {remaining} more"
            return f"Changed {first_path}"
    return _first_non_empty(item.get("summary"), item.get("description"), "File change")


def _event_from_item(method: str, params: dict) -> CodexEvent:
    item = params.get("item") if isinstance(params.get("item"), dict) else {}
    item_type = str(item.get("type") or "").strip()
    raw_text = str(item.get("text") or "")
    title = _item_title(item)
    command = _item_command(item)
    exit_code = _item_exit_code(item)
    display_kind = "status"
    text = _first_non_empty(raw_text, title)

    if item_type == "commandExecution":
        display_kind = "command"
        text = _first_non_empty(raw_text, command, title)
    elif item_type == "agentMessage":
        display_kind = "commentary"
        text = raw_text
    elif item_type == "fileChange":
        display_kind = "artifact"
        text = _first_non_empty(raw_text, title, _item_file_change_summary(item))
    else:
        if command:
            display_kind = "command"
            text = _first_non_empty(raw_text, command, title)
        elif raw_text:
            display_kind = "commentary"
        elif title:
            display_kind = "status"
        elif any(item.get(key) for key in ("path", "filePath", "file", "uri", "files")):
            display_kind = "artifact"
            text = _item_file_change_summary(item)

    return CodexEvent(
        type="item_completed" if method.endswith("completed") else "item_started",
        thread_id=str(params.get("threadId") or ""),
        turn_id=str(params.get("turnId") or ""),
        item_id=str(item.get("id") or ""),
        text=text,
        raw_text=raw_text,
        title=title,
        command=command,
        exit_code=exit_code,
        subtype=item_type,
        display_kind=display_kind,
        phase=str(item.get("phase") or ""),
        status=item_type,
        data=item,
    )


class CodexAppServerClient:
    def __init__(
        self,
        on_event: Callable[[CodexEvent], None] | None = None,
        timeout: int = DEFAULT_REQUEST_TIMEOUT,
        codex_model: str = DEFAULT_CODEX_MODEL,
    ) -> None:
        self.on_event = on_event
        self.timeout = max(int(timeout or DEFAULT_REQUEST_TIMEOUT), 1)
        self.codex_model = str(codex_model or DEFAULT_CODEX_MODEL).strip() or DEFAULT_CODEX_MODEL
        self._proc: subprocess.Popen[str] | None = None
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._send_lock = threading.Lock()
        self._request_lock = threading.Lock()
        self._request_seq = 1
        self._pending_requests: dict[str | int, dict] = {}
        self.last_context_usage = None
        self._initialized = False
        self._initializing = False
        self._closed = False
        self._codex_home_dir: Path | None = None
        self._owns_codex_home_dir = False

    def close(self) -> None:
        self._closed = True
        proc = self._proc
        if proc is not None:
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
        codex_home_dir = self._codex_home_dir
        self._codex_home_dir = None
        owns_codex_home_dir = bool(self._owns_codex_home_dir)
        self._owns_codex_home_dir = False
        if codex_home_dir is not None and owns_codex_home_dir:
            try:
                shutil.rmtree(codex_home_dir)
            except Exception:
                pass

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
        return self._request_clearing_context_usage("thread/start", params)

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
        return self._request_clearing_context_usage("thread/resume", params)

    def read_thread(self, thread_id: str, include_turns: bool = True) -> dict:
        return self.request(
            "thread/read",
            {
                "threadId": str(thread_id or "").strip(),
                "includeTurns": bool(include_turns),
            },
        )

    def start_turn(self, thread_id: str, text: str, timeout: int | None = None) -> dict:
        return self.start_turn_items(
            thread_id,
            [{"type": "text", "text": str(text or "")}],
            timeout=timeout,
        )

    def start_turn_items(self, thread_id: str, items: list[dict], timeout: int | None = None) -> dict:
        return self._request_clearing_context_usage(
            "turn/start",
            {
                "threadId": str(thread_id or "").strip(),
                "input": list(items or []),
            },
            timeout=timeout or DEFAULT_CODEX_TURN_TIMEOUT,
        )

    def steer_turn(self, thread_id: str, expected_turn_id: str, text: str, timeout: int | None = None) -> dict:
        return self.steer_turn_items(
            thread_id,
            expected_turn_id,
            [{"type": "text", "text": str(text or "")}],
            timeout=timeout,
        )

    def steer_turn_items(
        self,
        thread_id: str,
        expected_turn_id: str,
        items: list[dict],
        timeout: int | None = None,
    ) -> dict:
        return self._request_clearing_context_usage(
            "turn/steer",
            {
                "threadId": str(thread_id or "").strip(),
                "expectedTurnId": str(expected_turn_id or "").strip(),
                "input": list(items or []),
            },
            timeout=timeout or DEFAULT_CODEX_TURN_TIMEOUT,
        )

    def _request_clearing_context_usage(self, method: str, params: dict | None = None, timeout: int | None = None) -> dict:
        self.last_context_usage = None
        try:
            return self.request(method, params, timeout=timeout)
        except Exception:
            self.last_context_usage = None
            raise

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
            build_codex_app_server_command(codex_model=self.codex_model),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            bufsize=1,
            env=self._build_launch_env(),
            **_windows_popen_kwargs(),
        )
        self._stdout_thread = threading.Thread(target=self._stdout_loop, daemon=True)
        self._stderr_thread = threading.Thread(target=self._stderr_loop, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()
        self._initialized = False
        self._initialize()

    def _build_launch_env(self) -> dict[str, str]:
        env, codex_home_dir = build_codex_app_server_env()
        self._codex_home_dir = codex_home_dir
        self._owns_codex_home_dir = bool(codex_home_dir and codex_home_dir.name.startswith(".codex-home-"))
        return env

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
                self._emit_event(
                    CodexEvent(
                        type="stderr",
                        text=line,
                        raw_text=line,
                        subtype="stderr",
                        display_kind="error",
                    )
                )

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
        if str(message.get("type") or "").strip() == "event_msg":
            payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
            event_type = str(payload.get("type") or payload.get("method") or "").strip()
            if payload and event_type:
                self._emit_protocol_event(event_type, payload, message)
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
        if method in {"token_count", "codex/event/token_count", "thread/tokenUsage/updated"} or str(params.get("type") or "").strip() == "token_count":
            usage = codex_context_usage_from_payload(params)
            data = dict(params)
            if usage:
                data["context_usage"] = usage
            self._emit_event(
                CodexEvent(
                    type="token_count",
                    thread_id=str(params.get("threadId") or params.get("thread_id") or ""),
                    turn_id=str(params.get("turnId") or params.get("turn_id") or ""),
                    method=method,
                    params=params,
                    data=data,
                    usage=usage or {},
                )
            )
            return
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
                    subtype="turnStarted",
                    display_kind="status",
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
                    raw_text=str((turn.get("error") or {}).get("message") or ""),
                    subtype="turnCompleted",
                    display_kind="status",
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
                    raw_text=str(params.get("explanation") or ""),
                    subtype="turnPlanUpdated",
                    display_kind="plan",
                    data=params,
                )
            )
            return
        if method == "turn/diff/updated":
            diff_text = str(params.get("diff") or "")
            self._emit_event(
                CodexEvent(
                    type="diff_updated",
                    thread_id=str(params.get("threadId") or ""),
                    turn_id=str(params.get("turnId") or ""),
                    text=diff_text,
                    raw_text=diff_text,
                    subtype="turnDiffUpdated",
                    display_kind="artifact",
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
                    raw_text=str(params.get("delta") or ""),
                    subtype="agentMessageDelta",
                    display_kind="commentary",
                    data=params,
                )
            )
            return
        if method in {"item/started", "item/completed"}:
            self._emit_event(_event_from_item(method, params))
            return
        if method == "stderr":
            line = str(params.get("line") or "")
            self._emit_event(
                CodexEvent(
                    type="stderr",
                    text=line,
                    raw_text=line,
                    subtype="stderr",
                    display_kind="error",
                    data=params,
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
        if event.type == "token_count" and event.usage:
            self.last_context_usage = event.usage
        if callable(self.on_event):
            try:
                self.on_event(event)
            except Exception:
                self.last_context_usage = None
                raise


def _non_negative_int(value) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return max(parsed, 0)


def _usage_int_field(stats: dict, names: tuple[str, ...], default: int | None = 0) -> int | None:
    for name in names:
        if name in stats:
            return _non_negative_int(stats.get(name))
    return default


def _first_dict(*values) -> dict:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def _codex_model_from_rate_limits(payload: dict) -> str:
    rate_limits = payload.get("rate_limits") if isinstance(payload.get("rate_limits"), dict) else {}
    return str(rate_limits.get("limit_name") or "").strip()


def _codex_model_from_context_window(context_window: int) -> str:
    try:
        window = int(context_window or 0)
    except Exception:
        window = 0
    return CODEX_MODEL_BY_CONTEXT_WINDOW.get(window, "")


def codex_context_usage_from_payload(payload: dict, fallback_model: str = DEFAULT_CODEX_MODEL) -> dict | None:
    if not isinstance(payload, dict):
        return None
    info = _first_dict(payload.get("info"), payload.get("usage"), payload)
    total_usage = _first_dict(
        info.get("total_token_usage"),
        info.get("totalTokenUsage"),
        info.get("total_usage"),
    )
    total = _usage_int_field(
        total_usage,
        ("total_tokens", "totalTokens", "total", "tokens"),
        default=None,
    )
    if total is None:
        total = _usage_int_field(
            info,
            ("total_tokens", "totalTokens", "totalTokenUsage", "total_token_usage"),
            default=None,
        )
    if total is None or total <= 0:
        component_usage = total_usage if total_usage else info
        input_tokens = _usage_int_field(component_usage, ("input_tokens", "inputTokens", "prompt_tokens", "promptTokens"))
        output_tokens = _usage_int_field(component_usage, ("output_tokens", "outputTokens", "completion_tokens", "completionTokens"))
        cache_read_tokens = _usage_int_field(
            component_usage,
            ("cache_read_input_tokens", "cacheReadInputTokens", "cache_read_tokens", "cacheReadTokens", "cached_input_tokens", "cachedInputTokens"),
        )
        cache_creation_tokens = _usage_int_field(
            component_usage,
            (
                "cache_creation_input_tokens",
                "cacheCreationInputTokens",
                "cache_creation_tokens",
                "cacheCreationTokens",
            ),
        )
        values = [input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens]
        if any(value is None for value in values):
            return None
        total = sum(values)
    if total <= 0:
        return None

    explicit_model_name = str(
        info.get("model")
        or info.get("model_id")
        or info.get("modelId")
        or payload.get("model")
        or payload.get("model_id")
        or payload.get("modelId")
        or ""
    ).strip()
    context_window = _usage_int_field(
        info,
        ("context_window", "contextWindow", "model_context_window", "modelContextWindow", "context_tokens", "contextTokens"),
        default=0,
    )
    model_name = explicit_model_name or _codex_model_from_rate_limits(payload) or _codex_model_from_context_window(context_window) or str(fallback_model or "").strip()
    if context_window <= 0:
        context_window = context_window_for_model(model_name or fallback_model)
    return normalize_context_usage(
        used_tokens=total,
        context_window=context_window,
        source="codex",
        exact=True,
        fresh=True,
        model=model_name,
    ).to_dict()
