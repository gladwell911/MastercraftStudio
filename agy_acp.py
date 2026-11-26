"""Antigravity ACP v1 stdio transport and session configuration."""

from concurrent.futures import Future, TimeoutError
from dataclasses import dataclass, field
import base64
import hashlib
import itertools
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import uuid


AGY_MODEL_ID = "agy/main"
AGY_WORKSPACE_CAPABILITY = "agy-workspace-v1"
AGY_CHAT_FIELDS = ("agy_workspace", "agy_session_id", "agy_native_owner", "agy_config", "agy_options")
AGY_IMAGE_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
}
AGY_TEXT_EXTENSIONS = {
    ".txt", ".text", ".md", ".markdown", ".csv", ".tsv", ".log",
    ".rst", ".json", ".yaml", ".yml", ".xml",
}
MAX_AGY_ATTACHMENT_BYTES = 32 * 1024 * 1024


class AgyError(RuntimeError):
    pass


def read_prompt_attachments(capabilities: dict, attachments: list[dict]) -> list[dict]:
    """Build ACP blocks from the selected files using the current handshake."""
    prompt_capabilities = capabilities.get("promptCapabilities")
    if not isinstance(prompt_capabilities, dict):
        prompt_capabilities = {}
    blocks = []
    remaining = MAX_AGY_ATTACHMENT_BYTES
    for attachment in attachments:
        path = Path(attachment["path"])
        name = attachment["name"]
        extension = path.suffix.casefold()
        image_mime = AGY_IMAGE_MIME.get(extension)
        if image_mime:
            if prompt_capabilities.get("image") is not True:
                raise AgyError(f"此电脑的 AGY 未提供图片附件能力：{name}")
        elif extension in AGY_TEXT_EXTENSIONS:
            if prompt_capabilities.get("embeddedContext") is not True:
                raise AgyError(f"此电脑的 AGY 未提供内嵌资源能力：{name}")
        else:
            raise AgyError(f"AGY 无法发送此类附件：{name}。请选择图片或 UTF-8 文本文件。")
        try:
            with path.open("rb") as handle:
                data = handle.read(remaining + 1)
        except OSError as exc:
            raise AgyError(f"无法读取附件：{name}") from exc
        if len(data) > remaining:
            raise AgyError(f"本次 AGY 附件总大小超出 32 MiB：{name}")
        if not data:
            raise AgyError(f"附件没有内容：{name}")
        remaining -= len(data)
        if image_mime:
            blocks.append({"type": "image", "mimeType": image_mime, "data": base64.b64encode(data).decode("ascii")})
            continue
        try:
            encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
            text = data.decode(encoding)
            if "\x00" in text:
                raise ValueError
        except ValueError as exc:
            raise AgyError(f"请将文本附件保存为 UTF-8 或带 BOM 的 UTF-16：{name}") from exc
        blocks.append({"type": "resource", "resource": {
            "uri": path.absolute().as_uri(), "mimeType": "text/plain", "text": text,
        }})
    return blocks


@dataclass
class AgyPrompt:
    on_update: object
    on_interaction: object
    completion: Future = field(default_factory=Future)
    request_ids: list[int] = field(default_factory=list)
    pending: dict = field(default_factory=dict)
    cancelling: bool = False

    def wait(self) -> str:
        try:
            return self.completion.result(30 * 60)
        except TimeoutError as exc:
            raise AgyError("AGY 回合结果未知：等待原生终态超时，请查看原会话后再试") from exc


def find_agy_executable() -> str:
    configured = os.environ.get("MC_AGY_EXECUTABLE", "").strip()
    if configured:
        return str(Path(configured).resolve()) if Path(configured).is_file() else ""
    installed = shutil.which("agy")
    if installed:
        return installed
    local = os.environ.get("LOCALAPPDATA")
    if local:
        root = Path(local)
        for relative in ("agy/bin/agy.exe", "Google/Antigravity CLI/agy.exe", "Programs/Antigravity/agy.exe",
                         "Google/Antigravity/agy.exe", "Antigravity/bin/agy.exe"):
            candidate = root / relative
            if candidate.is_file():
                return str(candidate)
    return ""


def validate_workspace(value: str) -> str:
    candidate = Path(value.strip())
    if not candidate.is_absolute():
        raise AgyError("请选择运行电脑上的 AGY 项目目录（绝对路径）")
    try:
        resolved = candidate.resolve(strict=True)
        if not resolved.is_dir():
            raise NotADirectoryError(str(resolved))
        with os.scandir(resolved):
            pass
    except (OSError, ValueError, RuntimeError) as exc:
        raise AgyError(f"AGY 无法访问项目目录：{candidate}") from exc
    return str(resolved)


def configuration_options(result: dict) -> list[dict]:
    """Normalize advertised ACP selectors for the two application pickers."""
    options = []
    for item in result.get("configOptions") or []:
        if not isinstance(item, dict) or item.get("type") != "select" or not item.get("id"):
            continue
        values = []
        for entry in item.get("options") or []:
            if not isinstance(entry, dict):
                continue
            group = entry.get("options")
            for value in group if isinstance(group, list) else [entry]:
                if isinstance(value, dict) and isinstance(value.get("value"), str):
                    values.append({"value": value["value"], "label": value.get("name") or value["value"]})
        if values:
            options.append({"id": item["id"], "label": item.get("name") or item["id"],
                            "category": item.get("category", ""), "current": item.get("currentValue", ""),
                            "options": values})
    covered_categories = {option["category"] for option in options}
    for protocol_key, category, title, current, available, id_key in (
        ("models", "model", "AGY 模型", "currentModelId", "availableModels", "modelId"),
        ("modes", "mode", "执行模式", "currentModeId", "availableModes", "id"),
    ):
        legacy = result.get(protocol_key)
        if not isinstance(legacy, dict) or category in covered_categories:
            continue
        values = [{"value": value[id_key], "label": value.get("name") or value[id_key]}
                  for value in legacy.get(available, [])
                  if isinstance(value, dict) and isinstance(value.get(id_key), str)]
        if values:
            options.append({"id": f"legacy:{category}", "label": title, "category": category,
                            "current": legacy.get(current, ""), "options": values})
    return options


def selected_options(options: list, config: dict) -> dict:
    valid = {option["id"]: {value["value"] for value in option["options"]} for option in options}
    if not isinstance(config, dict) or any(
        not isinstance(value, str) or value not in valid.get(key, ()) for key, value in config.items()
    ):
        raise AgyError("此电脑的 AGY 不支持所选配置，请重新查看该会话的可用选项")
    return dict(config)


class AgyAcpClient:
    @staticmethod
    def command(executable: str) -> list[str]:
        return [executable, "acp", "--dangerously-skip-permissions"]

    def __init__(self, executable: str, cwd: str):
        self._lock = threading.RLock()
        self._pending = {}
        self._ids = itertools.count(1)
        self._on_update = None
        self._turn = None
        self._closed = False
        self._cwd = cwd
        self.session_id = ""
        self.options = []
        self.initialization = {}
        try:
            self._process = subprocess.Popen(
                self.command(executable), cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            raise AgyError("无法启动此电脑上的 AGY，请检查 MC_AGY_EXECUTABLE 和安装状态") from exc
        threading.Thread(target=self._read, name="agy-acp-reader", daemon=True).start()

    @property
    def alive(self) -> bool:
        return self._process.poll() is None

    @property
    def capabilities(self) -> dict:
        return self.initialization.get("agentCapabilities") or {}

    def native_owner(self, pair_id: str) -> str:
        # ACP authentication credentials remain with the current Windows user.
        # When the agent reports an account id we bind that identity as well.
        metadata = self.initialization.get("_meta") or {}
        account = metadata.get("accountId") or ""
        user = os.path.expanduser("~")
        return hashlib.sha256(json.dumps([pair_id, user, account], ensure_ascii=False).encode()).hexdigest()

    def native_account(self) -> str:
        return self.native_owner("")

    def native_storage(self) -> str:
        metadata = self.initialization.get("_meta") or {}
        storage = metadata.get("storageId") or ""
        return hashlib.sha256(json.dumps(
            [os.path.normcase(os.path.abspath(os.path.expanduser("~"))), storage], ensure_ascii=False,
        ).encode()).hexdigest()

    def _send(self, payload: dict) -> None:
        with self._lock:
            try:
                self._process.stdin.write(json.dumps({"jsonrpc": "2.0", **payload}, ensure_ascii=False) + "\n")
                self._process.stdin.flush()
            except (OSError, ValueError) as exc:
                raise AgyError("AGY 运行进程已退出，请检查本机安装和登录状态") from exc

    def request(self, method: str, params: dict, *, timeout: float = 8) -> dict:
        request_id = next(self._ids)
        future = Future()
        with self._lock:
            self._pending[request_id] = future
        try:
            self._send({"id": request_id, "method": method, "params": params})
            response = future.result(timeout)
        except TimeoutError as exc:
            raise AgyError(f"AGY 响应超时（{method}），请检查此电脑上的登录和运行状态") from exc
        finally:
            with self._lock:
                self._pending.pop(request_id, None)
        error = response.get("error")
        if isinstance(error, dict):
            raise self._request_error(method, error)
        result = response.get("result")
        return result if isinstance(result, dict) else {}

    @staticmethod
    def _request_error(method, error):
        message = str(error.get("message") or "").strip()
        if method in {"session/load", "session/resume", "session/prompt"} and any(
            term in message.lower() for term in ("already in use", "session is busy", "session is locked")
        ):
            return AgyError("AGY 原会话正在另一进程中使用，请等终端结束后重试")
        if any(term in message.lower() for term in ("auth_required", "authentication required", "not logged in", "sign in", "unauthenticated")):
            return AgyError("请先在运行电脑上登录 AGY，再重新打开聊天")
        if error.get("code") == -32601:
            return AgyError(f"此电脑上的 AGY 不支持 {method}，请检查安装版本")
        return AgyError(f"AGY {method} 失败：{message or error.get('code')}")

    def _read(self) -> None:
        try:
            for line in self._process.stdout:
                try:
                    payload = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(payload, dict):
                    continue
                request_id = payload.get("id")
                method = payload.get("method")
                params = payload.get("params")
                params = params if isinstance(params, dict) else {}
                if method and request_id is not None:
                    self._handle_request(request_id, method, params)
                elif method == "session/update":
                    with self._lock:
                        callback = self._turn.on_update if self._turn is not None else self._on_update
                        if callback and params.get("sessionId") == self.session_id:
                            callback(params.get("update") or {})
                elif request_id is not None:
                    with self._lock:
                        turn = self._turn
                        if turn is not None and request_id in turn.request_ids:
                            self._settle_prompt_request(turn, request_id, payload)
                        future = self._pending.get(request_id)
                        if future is not None and not future.done():
                            future.set_result(payload)
        except (OSError, UnicodeError, AgyError):
            pass
        finally:
            with self._lock:
                if self._turn is not None:
                    self._turn.completion.set_exception(AgyError("AGY 运行进程已退出，回合结果未知，请检查原会话和本机登录状态"))
                    self._turn = None
                for future in self._pending.values():
                    if not future.done():
                        future.set_exception(AgyError("AGY 运行进程已退出，请检查本机安装和登录状态"))
            self._process.stdout.close()

    def _settle_prompt_request(self, turn, request_id, payload):
        latest = turn.request_ids[-1] == request_id
        turn.request_ids.remove(request_id)
        if not latest:
            return
        error = payload.get("error")
        if isinstance(error, dict):
            failure = self._request_error("session/prompt", error)
            if turn.request_ids:
                turn.on_update({"sessionUpdate": "steer_error", "requestId": request_id, "text": str(failure)})
                return
            self._turn = None
            turn.completion.set_exception(failure)
            return
        self._turn = None
        result = payload.get("result")
        turn.completion.set_result(str(result.get("stopReason") or "") if isinstance(result, dict) else "")

    def _handle_request(self, request_id, method: str, params: dict) -> None:
        if method not in {"session/request_permission", "elicitation/create"}:
            self._send({"id": request_id, "error": {"code": -32601, "message": "Client capability unavailable"}})
            return
        with self._lock:
            turn = self._turn
            if turn is None or turn.cancelling or params.get("sessionId") != self.session_id:
                self._send({"id": request_id, "result": self._cancel_interaction(method)})
                return
            if any(request_id == request[0] for request in turn.pending.values()):
                return
            interaction_id = uuid.uuid4().hex
            turn.pending[interaction_id] = (request_id, method)
            turn.on_interaction(interaction_id, method, params)

    @staticmethod
    def _cancel_interaction(method):
        if method == "session/request_permission":
            return {"outcome": {"outcome": "cancelled"}}
        return {"action": "cancel"}

    def initialize(self) -> dict:
        self.initialization = self.request("initialize", {
            "protocolVersion": 1, "clientCapabilities": {"elicitation": {"form": {}}},
        })
        if self.initialization.get("protocolVersion") != 1:
            raise AgyError("此电脑上的 AGY 不支持 ACP v1")
        return self.initialization

    def open_session(self, session_id: str = "", *, replay: bool = False, on_update=None) -> dict:
        params = {"cwd": self._cwd, "mcpServers": []}
        method = "session/new"
        if session_id:
            params["sessionId"] = session_id
            resume = (self.capabilities.get("sessionCapabilities") or {}).get("resume")
            if not replay and isinstance(resume, dict):
                method = "session/resume"
            elif self.capabilities.get("loadSession") is True:
                method = "session/load"
            else:
                raise AgyError("此电脑上的 AGY 未提供原会话恢复能力")
        previous_id = self.session_id
        self.session_id = session_id
        self._on_update = on_update
        try:
            result = self.request(method, params)
        except BaseException:
            self.session_id = previous_id
            raise
        finally:
            self._on_update = None
        returned_id = result.get("sessionId") or session_id
        if not returned_id or (session_id and returned_id != session_id):
            raise AgyError("AGY 未返回预期的原生会话标识")
        self.session_id = returned_id
        self.options = configuration_options(result) or configuration_options(self.initialization)
        return result

    def configure(self, config: dict, fallback_options: list) -> None:
        if not config:
            return
        selected_options(self.options or fallback_options, config)
        for key, value in config.items():
            params = {"sessionId": self.session_id}
            if key == "legacy:model":
                method = "session/set_model"
                params["modelId"] = value
            elif key == "legacy:mode":
                method = "session/set_mode"
                params["modeId"] = value
            else:
                method = "session/set_config_option"
                params.update(configId=key, value=value)
            result = self.request(method, params)
            self.options = configuration_options(result) or self.options

    def begin_prompt(self, question, on_update, on_interaction, *, attachments=()) -> AgyPrompt:
        with self._lock:
            if self._turn is not None:
                raise AgyError("此 AGY 聊天正在执行")
            turn = self._turn = AgyPrompt(on_update, on_interaction)
            try:
                self._send_prompt(turn, question, attachments=attachments)
            except AgyError:
                self._turn = None
                raise
            return turn

    def _send_prompt(self, turn, question, *, attachments=()):
        request_id = next(self._ids)
        prompt = [{"type": "text", "text": question}] if question else []
        prompt.extend(attachments)
        self._send({"id": request_id, "method": "session/prompt", "params": {
            "sessionId": self.session_id, "prompt": prompt,
        }})
        turn.request_ids.append(request_id)

    def steer_prompt(self, turn, question) -> bool:
        with self._lock:
            if self._turn is not turn or turn.cancelling:
                return False
            self._send_prompt(turn, question)
            self._cancel_pending(turn)
            return True

    def cancel_prompt(self, turn) -> bool:
        with self._lock:
            if self._turn is not turn or turn.cancelling:
                return False
            self._send({"method": "session/cancel", "params": {"sessionId": self.session_id}})
            turn.cancelling = True
            self._cancel_pending(turn)
            return True

    def _cancel_pending(self, turn):
        for request_id, method in turn.pending.values():
            self._send({"id": request_id, "result": self._cancel_interaction(method)})
        turn.pending.clear()

    def reply_interaction(self, turn, interaction_id, result) -> bool:
        with self._lock:
            if self._turn is not turn or turn.cancelling:
                return False
            request = turn.pending.pop(interaction_id, None)
            if request is None:
                return False
            self._send({"id": request[0], "result": result})
            return True

    def interaction_active(self, turn, interaction_id) -> bool:
        with self._lock:
            return self._turn is turn and interaction_id in turn.pending

    def decline_interaction(self, turn, interaction_id, method) -> bool:
        result = self._cancel_interaction(method) if method == "session/request_permission" else {"action": "decline"}
        return self.reply_interaction(turn, interaction_id, result)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        if self.alive:
            try:
                self._process.kill()
            except ProcessLookupError:
                pass
        with self._lock:
            self._process.stdin.close()
        self._process.wait(timeout=2)
