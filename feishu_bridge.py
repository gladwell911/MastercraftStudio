import json
import os
import threading
from dataclasses import dataclass
from typing import Callable


DEFAULT_FEISHU_DOMAIN = "https://open.feishu.cn"
DEFAULT_FEISHU_REMOTE_MODEL = "codex/main"
DEFAULT_FEISHU_CHAT_ID = "oc_6b45a5887b79b1e179a832d76b4dcb9b"
FEISHU_MESSAGE_PREFIX = "computer_message"
TEXT_MESSAGE_LIMIT = 1500
DEFAULT_FEISHU_BOT_APP_ID = "cli_a93277253679dcd1"
DEFAULT_FEISHU_BOT_APP_SECRET = "9ao6pEUihzZccT3l35RbHbXMTzSMlTJG"


@dataclass
class FeishuIncomingMessage:
    chat_id: str
    message_id: str
    sender_open_id: str = ""
    sender_user_id: str = ""
    text: str = ""
    chat_type: str = ""
    thread_id: str = ""
    message_type: str = "text"


def env_flag(name: str, default: bool = False) -> bool:
    value = str(os.environ.get(name) or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on", "enabled"}


def add_feishu_message_prefix(text: str) -> str:
    clean = str(text or "").strip()
    if not clean:
        return ""
    if clean.startswith(FEISHU_MESSAGE_PREFIX):
        return clean
    return f"{FEISHU_MESSAGE_PREFIX} {clean}"


def strip_feishu_message_prefix(text: str) -> str:
    clean = str(text or "").strip()
    if not clean:
        return ""
    if not clean.startswith(FEISHU_MESSAGE_PREFIX):
        return clean
    return clean[len(FEISHU_MESSAGE_PREFIX):].lstrip(" :\n\r\t")


def parse_text_message_content(content: str) -> str:
    raw = str(content or "").strip()
    if not raw:
        return ""
    try:
        payload = json.loads(raw)
    except Exception:
        return raw
    if isinstance(payload, dict):
        return str(payload.get("text") or "").strip()
    return raw


def split_text_message(text: str, limit: int = TEXT_MESSAGE_LIMIT) -> list[str]:
    clean = str(text or "").strip()
    if not clean:
        return []
    size = max(int(limit or TEXT_MESSAGE_LIMIT), 200)
    if len(clean) <= size:
        return [clean]
    chunks = []
    remaining = clean
    while remaining:
        piece = remaining[:size]
        split_at = piece.rfind("\n")
        if split_at < int(size * 0.5):
            split_at = piece.rfind("。")
        if split_at < int(size * 0.5):
            split_at = piece.rfind(" ")
        if split_at <= 0:
            split_at = len(piece)
        current = remaining[:split_at].strip()
        if not current:
            current = remaining[:size].strip()
            split_at = len(current)
        chunks.append(current)
        remaining = remaining[split_at:].strip()
    return chunks


def format_remote_user_input_request(params: dict) -> str:
    questions = params.get("questions") if isinstance(params.get("questions"), list) else []
    if not questions:
        return "Codex 需要你的输入，请直接回复内容。"

    lines = ["Codex 需要你的输入。"]
    if len(questions) == 1:
        question = questions[0] if isinstance(questions[0], dict) else {}
        lines.extend(_format_single_question(question, include_id=False))
    else:
        lines.append("请按 `问题ID=答案` 每行回复。")
        for question in questions:
            question = question if isinstance(question, dict) else {}
            lines.append("")
            lines.extend(_format_single_question(question, include_id=True))
    return "\n".join(lines).strip()


def _format_single_question(question: dict, include_id: bool) -> list[str]:
    lines = []
    question_id = str(question.get("id") or "").strip()
    header = str(question.get("header") or "").strip()
    prompt = str(question.get("question") or "").strip()
    title = header or question_id or "问题"
    if include_id and question_id:
        title = f"{title} ({question_id})"
    lines.append(title)
    if prompt:
        lines.append(prompt)
    options = question.get("options") if isinstance(question.get("options"), list) else []
    if options:
        lines.append("可选项：")
        for idx, option in enumerate(options, start=1):
            label = str((option or {}).get("label") or "").strip() or f"选项{idx}"
            lines.append(f"{idx}. {label}")
        if bool(question.get("isOther")):
            lines.append(f"{len(options) + 1}. 其他（回复 `其他:内容`）")
        lines.append("可直接回复序号或选项文本。")
    elif bool(question.get("isSecret")):
        lines.append("请直接回复密码内容。")
    else:
        lines.append("请直接回复文本内容。")
    return lines


def parse_remote_user_input_reply(params: dict, text: str) -> tuple[dict[str, list[str]] | None, str]:
    clean = strip_feishu_message_prefix(text)
    questions = params.get("questions") if isinstance(params.get("questions"), list) else []
    if not questions:
        return {}, ""
    if not clean:
        return None, "回复内容为空。"
    if len(questions) == 1:
        question = questions[0] if isinstance(questions[0], dict) else {}
        values, error = _parse_single_question_answer(question, clean)
        if error:
            return None, error
        return {str(question.get("id") or "").strip(): values}, ""

    answers = {}
    by_id = {str((question or {}).get("id") or "").strip(): question for question in questions}
    for raw_line in clean.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if "=" not in line:
            return None, "多问题回复格式错误，请按 `问题ID=答案` 每行回复。"
        question_id, value = line.split("=", 1)
        question_id = question_id.strip()
        if question_id not in by_id:
            return None, f"未知的问题ID：{question_id}"
        parsed, error = _parse_single_question_answer(by_id[question_id], value.strip())
        if error:
            return None, error
        answers[question_id] = parsed
    if not answers:
        return None, "未解析到任何有效答案。"
    return answers, ""


def _parse_single_question_answer(question: dict, text: str) -> tuple[list[str] | None, str]:
    clean = strip_feishu_message_prefix(text)
    options = question.get("options") if isinstance(question.get("options"), list) else []
    if not options:
        return ([clean] if clean else []), ""

    labels = [str((item or {}).get("label") or "").strip() for item in options]
    lower = clean.lower()
    if clean.isdigit():
        idx = int(clean)
        if 1 <= idx <= len(labels):
            label = labels[idx - 1]
            return ([label] if label else []), ""
        if bool(question.get("isOther")) and idx == len(labels) + 1:
            return None, "选择“其他”时，请回复 `其他:内容`。"
    if bool(question.get("isOther")) and (lower.startswith("其他:") or lower.startswith("other:")):
        value = clean.split(":", 1)[1].strip()
        return ([value] if value else []), ""
    for label in labels:
        if clean == label:
            return ([label] if label else []), ""
    return None, "未识别的选项，请回复序号、选项文本，或按提示格式回复。"


def format_remote_approval_request(method: str, params: dict) -> str:
    method_name = str(method or "").strip()
    lines = []
    if method_name == "item/permissions/requestApproval":
        lines.append("Codex 请求额外权限。")
        reason = str(params.get("reason") or "").strip()
        if reason:
            lines.append(f"原因：{reason}")
        permissions = params.get("permissions")
        if isinstance(permissions, dict) and permissions:
            lines.append("权限：")
            lines.append(json.dumps(permissions, ensure_ascii=False, indent=2))
        lines.append("回复 1 允许本次，2 允许本会话，3 拒绝。")
        return "\n".join(lines).strip()
    if method_name == "item/fileChange/requestApproval":
        lines.append("Codex 请求文件变更权限。")
        grant_root = str(params.get("grantRoot") or "").strip()
        if grant_root:
            lines.append(f"授权目录：{grant_root}")
        reason = str(params.get("reason") or "").strip()
        if reason:
            lines.append(f"原因：{reason}")
        lines.append("回复 1 允许本次，2 允许本会话，3 拒绝，4 取消当前轮。")
        return "\n".join(lines).strip()
    lines.append("Codex 请求执行命令。")
    command = str(params.get("command") or "").strip()
    cwd = str(params.get("cwd") or "").strip()
    reason = str(params.get("reason") or "").strip()
    if command:
        lines.append(f"命令：{command}")
    if cwd:
        lines.append(f"工作目录：{cwd}")
    if reason:
        lines.append(f"原因：{reason}")
    lines.append("回复 1 允许本次，2 允许本会话，3 拒绝，4 取消当前轮。")
    return "\n".join(lines).strip()


def parse_remote_approval_reply(method: str, text: str) -> str | None:
    clean = strip_feishu_message_prefix(text).lower()
    if not clean:
        return None
    mapping = {
        "1": "accept",
        "允许": "accept",
        "允许本次": "accept",
        "accept": "accept",
        "2": "acceptForSession",
        "允许本会话": "acceptForSession",
        "session": "acceptForSession",
        "acceptforsession": "acceptForSession",
        "3": "decline",
        "拒绝": "decline",
        "decline": "decline",
    }
    if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
        mapping.update({"4": "cancel", "取消": "cancel", "cancel": "cancel"})
    return mapping.get(clean)


class FeishuBotBridge:
    def __init__(
        self,
        app_id: str,
        app_secret: str,
        on_message: Callable[[FeishuIncomingMessage], None],
        on_error: Callable[[str], None] | None = None,
        on_status: Callable[[str], None] | None = None,
        domain: str = DEFAULT_FEISHU_DOMAIN,
        allowed_chat_ids: list[str] | None = None,
        allowed_open_ids: list[str] | None = None,
    ) -> None:
        self.app_id = str(app_id or "").strip()
        self.app_secret = str(app_secret or "").strip()
        self.on_message = on_message
        self.on_error = on_error
        self.on_status = on_status
        self.domain = str(domain or DEFAULT_FEISHU_DOMAIN).strip() or DEFAULT_FEISHU_DOMAIN
        self.allowed_chat_ids = {item.strip() for item in (allowed_chat_ids or []) if str(item).strip()}
        self.allowed_open_ids = {item.strip() for item in (allowed_open_ids or []) if str(item).strip()}
        self._thread: threading.Thread | None = None
        self._lark = None
        self._api_client = None
        self._ws_client = None
        self._started = False

    @classmethod
    def from_env(
        cls,
        on_message: Callable[[FeishuIncomingMessage], None],
        on_error: Callable[[str], None] | None = None,
        on_status: Callable[[str], None] | None = None,
    ) -> "FeishuBotBridge | None":
        app_id = str(os.environ.get("FEISHU_BOT_APP_ID") or DEFAULT_FEISHU_BOT_APP_ID).strip()
        app_secret = str(os.environ.get("FEISHU_BOT_APP_SECRET") or DEFAULT_FEISHU_BOT_APP_SECRET).strip()
        if not env_flag("FEISHU_BOT_ENABLED", default=bool(app_id and app_secret)):
            return None
        if (not app_id) or (not app_secret):
            return None
        configured_chat_ids = [
            item.strip()
            for item in str(os.environ.get("FEISHU_ALLOWED_CHAT_IDS") or "").split(",")
            if item.strip()
        ]
        allowed_chat_ids = configured_chat_ids or [DEFAULT_FEISHU_CHAT_ID]
        allowed_open_ids = [
            item.strip()
            for item in str(os.environ.get("FEISHU_ALLOWED_OPEN_IDS") or "").split(",")
            if item.strip()
        ]
        domain = str(os.environ.get("FEISHU_BOT_DOMAIN") or DEFAULT_FEISHU_DOMAIN).strip() or DEFAULT_FEISHU_DOMAIN
        return cls(
            app_id=app_id,
            app_secret=app_secret,
            on_message=on_message,
            on_error=on_error,
            on_status=on_status,
            domain=domain,
            allowed_chat_ids=allowed_chat_ids,
            allowed_open_ids=allowed_open_ids,
        )

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._init_clients()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._notify_status("飞书机器人桥接已启动")

    def stop(self) -> None:
        self._started = False

    def send_text(self, chat_id: str, text: str) -> None:
        if (not self._api_client) or (not str(chat_id or "").strip()):
            return
        lark = self._require_sdk()
        for chunk in split_text_message(text):
            request = (
                lark.im.v1.CreateMessageRequest.builder()
                .receive_id_type("chat_id")
                .request_body(
                    lark.im.v1.CreateMessageRequestBody.builder()
                    .receive_id(str(chat_id).strip())
                    .msg_type("text")
                    .content(json.dumps({"text": chunk}, ensure_ascii=False))
                    .build()
                )
                .build()
            )
            response = self._api_client.im.v1.message.create(request)
            if not response.success():
                raise RuntimeError(f"飞书消息发送失败: {response.code} {response.msg}")

    def _run_loop(self) -> None:
        try:
            if self._ws_client is not None:
                self._ws_client.start()
        except Exception as exc:
            self._notify_error(f"飞书长连接失败: {exc}")

    def _init_clients(self) -> None:
        lark = self._require_sdk()
        event_handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._handle_message_receive)
            .build()
        )
        self._api_client = (
            lark.Client.builder()
            .app_id(self.app_id)
            .app_secret(self.app_secret)
            .domain(self.domain)
            .build()
        )
        self._ws_client = lark.ws.Client(
            self.app_id,
            self.app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
            domain=self.domain,
        )

    def _require_sdk(self):
        if self._lark is None:
            try:
                import lark_oapi as lark
            except Exception as exc:
                raise RuntimeError("未安装 lark-oapi，请先执行 `pip install lark-oapi`。") from exc
            self._lark = lark
        return self._lark

    def _handle_message_receive(self, data) -> None:
        try:
            incoming = self._convert_event(data)
        except Exception as exc:
            self._notify_error(f"飞书消息解析失败: {exc}")
            return
        if incoming is None:
            return
        if self.allowed_chat_ids and incoming.chat_id not in self.allowed_chat_ids:
            return
        if self.allowed_open_ids and incoming.sender_open_id not in self.allowed_open_ids:
            return
        try:
            self.on_message(incoming)
        except Exception as exc:
            self._notify_error(f"飞书消息处理失败: {exc}")

    def _convert_event(self, data) -> FeishuIncomingMessage | None:
        event = getattr(data, "event", None)
        if event is None:
            return None
        message = getattr(event, "message", None)
        sender = getattr(event, "sender", None)
        if message is None:
            return None
        message_type = str(getattr(message, "message_type", "") or "").strip() or "text"
        content = parse_text_message_content(str(getattr(message, "content", "") or ""))
        sender_id = getattr(sender, "sender_id", None)
        return FeishuIncomingMessage(
            chat_id=str(getattr(message, "chat_id", "") or "").strip(),
            message_id=str(getattr(message, "message_id", "") or "").strip(),
            sender_open_id=str(getattr(sender_id, "open_id", "") or "").strip(),
            sender_user_id=str(getattr(sender_id, "user_id", "") or "").strip(),
            text=content,
            chat_type=str(getattr(message, "chat_type", "") or "").strip(),
            thread_id=str(getattr(message, "thread_id", "") or "").strip(),
            message_type=message_type,
        )

    def _notify_error(self, text: str) -> None:
        if callable(self.on_error):
            self.on_error(str(text or "").strip())

    def _notify_status(self, text: str) -> None:
        if callable(self.on_status):
            self.on_status(str(text or "").strip())
