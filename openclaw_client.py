import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable


OPENCLAW_MODEL_PREFIX = "openclaw/"
DEFAULT_OPENCLAW_AGENT = "main"
DEFAULT_OPENCLAW_SESSION_KEY = "agent:main:main"
DEFAULT_TIMEOUT_SECONDS = 600
REPLY_PREFIX = "[[reply_to_current]]"
SENDER_METADATA_PREFIX = "Sender (untrusted metadata):"


@dataclass
class OpenClawSessionPointer:
    session_key: str
    session_id: str
    session_file: str
    updated_at: float


@dataclass
class OpenClawSyncEvent:
    event_id: str
    role: str
    text: str
    timestamp: float


def is_openclaw_model(model: str) -> bool:
    return str(model or "").strip().startswith(OPENCLAW_MODEL_PREFIX)


def model_to_agent_id(model: str) -> str:
    text = str(model or "").strip()
    if not is_openclaw_model(text):
        return DEFAULT_OPENCLAW_AGENT
    agent_id = text[len(OPENCLAW_MODEL_PREFIX):].strip()
    return agent_id or DEFAULT_OPENCLAW_AGENT


def normalize_openclaw_text(text: str) -> str:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if normalized.startswith(REPLY_PREFIX):
        normalized = normalized[len(REPLY_PREFIX):].strip()
    return normalized


def resolve_openclaw_sessions_dir(agent_id: str = DEFAULT_OPENCLAW_AGENT) -> Path:
    userprofile = os.getenv("USERPROFILE", "").strip()
    if userprofile:
        return Path(userprofile) / ".openclaw" / "agents" / agent_id / "sessions"
    return Path.home() / ".openclaw" / "agents" / agent_id / "sessions"


def load_session_pointer(
    sessions_json_path: str | Path,
    session_key: str = DEFAULT_OPENCLAW_SESSION_KEY,
) -> OpenClawSessionPointer | None:
    path = Path(sessions_json_path)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    entry = data.get(session_key)
    if not isinstance(entry, dict):
        return None
    session_file = str(entry.get("sessionFile") or "").strip()
    if not session_file:
        return None
    return OpenClawSessionPointer(
        session_key=session_key,
        session_id=str(entry.get("sessionId") or "").strip(),
        session_file=session_file,
        updated_at=_coerce_millis(entry.get("updatedAt")),
    )


def read_session_events(
    session_file_path: str | Path,
    offset: int = 0,
) -> tuple[int, list[OpenClawSyncEvent]]:
    path = Path(session_file_path)
    if not path.exists():
        return (0 if offset < 0 else offset, [])

    size = path.stat().st_size
    start = int(max(offset, 0))
    if start > size:
        start = 0

    events: list[OpenClawSyncEvent] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(start)
        for raw_line in handle:
            line = str(raw_line or "").strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            event = _parse_sync_event(obj)
            if event is not None:
                events.append(event)
        new_offset = handle.tell()
    return new_offset, events


def _parse_sync_event(obj: dict) -> OpenClawSyncEvent | None:
    if not isinstance(obj, dict):
        return None
    if str(obj.get("type") or "") != "message":
        return None
    msg = obj.get("message")
    if not isinstance(msg, dict):
        return None
    role = str(msg.get("role") or "").strip()
    if role not in {"user", "assistant"}:
        return None
    text = _extract_message_text(msg, role)
    text = normalize_openclaw_text(text)
    if not text:
        return None
    return OpenClawSyncEvent(
        event_id=str(obj.get("id") or "").strip(),
        role=role,
        text=text,
        timestamp=_coerce_timestamp(obj.get("timestamp") or msg.get("timestamp")),
    )


def _extract_message_text(message: dict, role: str) -> str:
    content = message.get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts = _extract_text_parts(content, role)
        text = "\n\n".join(parts).strip()
    else:
        text = ""

    if role == "user":
        return _strip_sender_metadata(text)
    return text


def _extract_text_parts(content: list[dict], role: str) -> list[str]:
    final_parts: list[str] = []
    plain_parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "").strip() != "text":
            continue
        piece = str(item.get("text") or "").strip()
        if not piece:
            continue
        if role != "assistant":
            plain_parts.append(piece)
            continue
        phase = _extract_text_phase(item)
        if phase == "commentary":
            continue
        if phase == "final_answer":
            final_parts.append(piece)
            continue
        plain_parts.append(piece)
    return final_parts or plain_parts


def _extract_text_phase(item: dict) -> str:
    phase = str(item.get("phase") or "").strip()
    if phase:
        return phase
    signature = item.get("textSignature")
    if not isinstance(signature, str) or not signature.strip():
        return ""
    try:
        data = json.loads(signature)
    except Exception:
        return ""
    return str(data.get("phase") or "").strip()


def _strip_sender_metadata(text: str) -> str:
    out = normalize_openclaw_text(text)
    if out.startswith(SENDER_METADATA_PREFIX):
        out = re.sub(
            r"^Sender \(untrusted metadata\):\s*```json.*?```\s*",
            "",
            out,
            flags=re.DOTALL,
        ).strip()
    out = re.sub(r"^\[[^\]]+\]\s*", "", out).strip()
    return out


def _coerce_millis(value) -> float:
    try:
        ivalue = float(value)
    except Exception:
        return 0.0
    return ivalue / 1000.0 if ivalue > 10_000_000_000 else ivalue


def _coerce_timestamp(value) -> float:
    if value is None:
        return time.time()
    if isinstance(value, (int, float)):
        return _coerce_millis(value)
    text = str(value).strip()
    if not text:
        return time.time()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:
        return time.time()


class OpenClawClient:
    def __init__(self, model: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.model = str(model or "").strip()
        self.agent_id = model_to_agent_id(self.model)
        self.timeout = max(int(timeout or DEFAULT_TIMEOUT_SECONDS), 1)

    def stream_chat(
        self,
        user_text: str,
        session_id: str,
        on_delta: Callable[[str], None] | None = None,
    ) -> str:
        if not str(user_text or "").strip():
            return ""
        sid = str(session_id or "").strip()
        if not sid:
            raise RuntimeError("OpenClaw 会话 ID 不能为空。")

        command = self._build_agent_command(user_text, sid)
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout + 30,
                check=False,
                shell=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(self._missing_command_message()) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"OpenClaw 请求超时：{self.timeout} 秒内未完成。") from exc

        stdout = str(completed.stdout or "").strip()
        stderr = str(completed.stderr or "").strip()
        try:
            data = self._parse_json(stdout)
        except RuntimeError:
            if completed.returncode == 0:
                data = {}
            else:
                raise
        if completed.returncode != 0:
            detail = self._extract_error_detail(data, stderr, stdout)
            raise RuntimeError(f"OpenClaw 请求失败：{detail}")

        reply = self._extract_text(data)
        if not reply and stdout and completed.returncode == 0:
            reply = self._extract_plain_stdout(stdout)
        if not reply:
            detail = self._extract_error_detail(data, stderr, stdout)
            raise RuntimeError(f"OpenClaw 未返回可显示内容：{detail}")

        reply = normalize_openclaw_text(reply)
        if callable(on_delta) and reply:
            on_delta(reply)
        return reply

    def _build_agent_command(self, user_text: str, session_id: str) -> list[str]:
        command = self._resolve_openclaw_invocation()
        command.extend(
            [
                "--no-color",
                "agent",
                "--agent",
                self.agent_id,
                "--session-id",
                session_id,
                "--message",
                user_text,
                "--json",
                "--timeout",
                str(self.timeout),
            ]
        )
        return command

    def _resolve_openclaw_invocation(self) -> list[str]:
        resolved = self._resolve_openclaw_command()
        script_path = self._resolve_openclaw_node_script(resolved)
        if script_path:
            return [self._resolve_node_command(), script_path]
        return [resolved]

    def _resolve_openclaw_node_script(self, command_path: str) -> str:
        path = Path(str(command_path or "").strip().strip('"'))
        if path.suffix.lower() != ".cmd":
            return ""
        candidate = path.parent / "node_modules" / "openclaw" / "openclaw.mjs"
        return str(candidate) if candidate.is_file() else ""

    def _resolve_node_command(self) -> str:
        candidates = []
        direct = shutil.which("node")
        if direct:
            candidates.append(direct)
        appdata = os.getenv("APPDATA", "").strip()
        if appdata:
            candidates.append(str(Path(appdata) / "node.exe"))
        for candidate in candidates:
            text = str(candidate or "").strip().strip('"')
            if text and Path(text).is_file():
                return text
        return direct or "node"

    def _parse_json(self, stdout: str) -> dict:
        if not stdout:
            return {}
        try:
            data = json.loads(stdout)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            data = self._extract_json_object(stdout)
            if isinstance(data, dict) and data:
                return data
            raise RuntimeError(f"OpenClaw 返回了无法解析的 JSON：{stdout[:300]}")

    def _extract_json_object(self, stdout: str) -> dict:
        decoder = json.JSONDecoder()
        source = str(stdout or "")
        for idx, ch in enumerate(source):
            if ch != "{":
                continue
            try:
                obj, end = decoder.raw_decode(source[idx:])
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            tail = source[idx + end:].strip()
            if (not tail) or tail.startswith("[plugins]") or tail.startswith("[tool") or tail.startswith("[agent"):
                return obj
        return {}

    def _extract_text(self, data: dict) -> str:
        payloads = data.get("payloads")
        if not isinstance(payloads, list):
            result = data.get("result")
            if isinstance(result, dict):
                payloads = result.get("payloads")
        if not isinstance(payloads, list):
            return ""
        parts = []
        for item in payloads:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if text:
                parts.append(text)
        return "\n\n".join(parts).strip()

    def _extract_plain_stdout(self, stdout: str) -> str:
        lines = []
        for raw_line in str(stdout or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith(("[plugins]", "[tool", "[agent]")):
                continue
            lines.append(line)
        return "\n".join(lines).strip()

    def _extract_error_detail(self, data: dict, stderr: str, stdout: str) -> str:
        text = normalize_openclaw_text(self._extract_text(data))
        if text:
            return text
        err = str(data.get("error") or "").strip()
        if err:
            return err
        if stderr:
            lines = [line.strip() for line in stderr.splitlines() if line.strip()]
            if lines:
                return lines[-1][:300]
        if stdout:
            return stdout[:300]
        return "OpenClaw 未返回更多错误信息。"

    def _resolve_openclaw_command(self) -> str:
        candidates = []
        direct = shutil.which("openclaw")
        if direct:
            candidates.append(direct)
        for name in ("openclaw.cmd", "openclaw.exe", "openclaw.ps1", "openclaw"):
            found = shutil.which(name)
            if found:
                candidates.append(found)

        appdata = os.getenv("APPDATA", "").strip()
        if appdata:
            npm_dir = Path(appdata) / "npm"
            for name in ("openclaw.cmd", "openclaw.ps1", "openclaw"):
                candidates.append(str(npm_dir / name))

        localappdata = os.getenv("LOCALAPPDATA", "").strip()
        userprofile = os.getenv("USERPROFILE", "").strip()
        for base in (localappdata, userprofile):
            if not base:
                continue
            root = Path(base)
            candidates.append(str(root / "Programs" / "nodejs" / "openclaw.cmd"))
            candidates.append(str(root / "Programs" / "nodejs" / "openclaw.exe"))

        seen = set()
        for candidate in candidates:
            text = str(candidate or "").strip().strip('"')
            if not text or text in seen:
                continue
            seen.add(text)
            if Path(text).is_file():
                return text
        raise FileNotFoundError(self._missing_command_message())

    def _missing_command_message(self) -> str:
        return (
            "未找到 openclaw 命令。请确认 OpenClaw 已安装；Windows 常见位置是 "
            "%APPDATA%\\npm\\openclaw.cmd。"
        )
