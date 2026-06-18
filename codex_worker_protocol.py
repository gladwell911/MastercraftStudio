from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any

from codex_client import CodexEvent


class CodexWorkerProtocolError(ValueError):
    pass


CHAT_SCOPED_TYPES = {
    "start_turn",
    "reply_user_input",
    "cancel_turn",
    "event",
    "thread_state",
    "request_user_input",
    "turn_started_ack",
    "turn_finished",
    "error",
}


def make_ui_request(request_id: str, message_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"id": str(request_id), "type": str(message_type), "payload": dict(payload or {})}


def make_worker_event(
    message_type: str, payload: dict[str, Any] | None = None, request_id: str | None = None
) -> dict[str, Any]:
    message: dict[str, Any] = {"type": str(message_type), "payload": dict(payload or {})}
    if request_id is not None:
        message["id"] = str(request_id)
    return message


def encode_worker_message(message: dict[str, Any]) -> str:
    if not isinstance(message, dict):
        raise CodexWorkerProtocolError("worker message must be a dict")
    if not str(message.get("type") or "").strip():
        raise CodexWorkerProtocolError("worker message missing type")
    payload = message.get("payload")
    if payload is not None and not isinstance(payload, dict):
        raise CodexWorkerProtocolError("worker message payload must be a dict")
    return json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"


def decode_worker_line(line: str) -> dict[str, Any]:
    try:
        message = json.loads(str(line or "").strip())
    except Exception as exc:
        raise CodexWorkerProtocolError("invalid worker JSON line") from exc
    if not isinstance(message, dict):
        raise CodexWorkerProtocolError("worker JSON line must decode to dict")
    if not str(message.get("type") or "").strip():
        raise CodexWorkerProtocolError("worker message missing type")
    payload = message.get("payload")
    if payload is not None and not isinstance(payload, dict):
        raise CodexWorkerProtocolError("worker message payload must be dict")
    return message


def validate_chat_scoped_message(message: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(message, dict):
        raise CodexWorkerProtocolError("worker message must be a dict")
    message_type = str(message.get("type") or "").strip()
    payload = message.get("payload")
    if message_type in CHAT_SCOPED_TYPES:
        if not isinstance(payload, dict) or not str(payload.get("chat_id") or "").strip():
            raise CodexWorkerProtocolError(f"{message_type} message requires payload.chat_id")
    return message


def event_to_payload(event: CodexEvent) -> dict[str, Any]:
    if is_dataclass(event):
        return asdict(event)
    return dict(getattr(event, "__dict__", {}) or {})


def event_from_payload(payload: dict[str, Any]) -> CodexEvent:
    if not isinstance(payload, dict):
        raise CodexWorkerProtocolError("Codex event payload must be dict")
    if not isinstance(payload.get("type"), str) or not payload["type"].strip():
        raise CodexWorkerProtocolError("Codex event payload requires type")
    allowed = CodexEvent.__dataclass_fields__.keys()
    return CodexEvent(**{key: payload[key] for key in allowed if key in payload})
