"""Kimi event types, serialization and provider event mapping."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime
from typing import Any


def kimi_snapshot_total_tokens(payload: dict[str, Any]) -> int | None:
    session = payload.get("session") if isinstance(payload, dict) else None
    usage = session.get("usage") if isinstance(session, dict) else None
    if not isinstance(usage, dict):
        return None
    values = []
    for key in ("input_tokens", "output_tokens"):
        value = usage.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        values.append(value)
    return sum(values)


MAX_DIAGNOSTIC_TEXT = 2000


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
    provider: str = "kimi"
    event_id: str = ""
    fragment_id: str = ""
    offset: int | None = None
    session_id: str = ""
    stream_id: str = ""
    tool_call_id: str = ""
    revision: int | None = None
    origin_timestamp: float | None = None

    def __post_init__(self) -> None:
        # Constructors that override ``data`` still carry the authoritative
        # envelope origin through the normalized base data.
        if self.origin_timestamp is None and isinstance(self.data, dict):
            value = self.data.get("provider_origin_timestamp")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                self.origin_timestamp = float(value)


def event_to_payload(event: KimiEvent) -> dict[str, Any]:
    if is_dataclass(event):
        payload = asdict(event)
    else:
        payload = dict(getattr(event, "__dict__", {}) or {})
    data = dict(payload.get("data") or {})
    data["adapter"] = "kimi_server"
    payload["data"] = data
    return payload


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


_MIN_PROVIDER_EPOCH = 946684800.0


_MAX_PROVIDER_EPOCH = 4102444800.0


def _provider_origin_timestamp(*payloads: dict[str, Any]) -> float | None:
    """Normalize numeric or ISO provider time; malformed values stay absent."""
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        for key in ("created_at", "createdAt", "timestamp"):
            value = payload.get(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                parsed = float(value)
            elif isinstance(value, str) and value.strip():
                try:
                    parsed = float(value.strip())
                except ValueError:
                    try:
                        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
                        if moment.tzinfo is None or moment.utcoffset() is None:
                            continue
                        parsed = moment.timestamp()
                    except (ValueError, OverflowError, OSError):
                        continue
            else:
                continue
            if 1_000_000_000_000.0 <= parsed <= 4_102_444_800_000.0:
                parsed /= 1000.0
            if parsed == parsed and abs(parsed) != float("inf") and _MIN_PROVIDER_EPOCH <= parsed <= _MAX_PROVIDER_EPOCH:
                return parsed
    return None


def _text_fragment(value: Any) -> str:
    """Return streamed text exactly as supplied by the server."""
    return "" if value is None else str(value)


def _bounded_fragment(value: Any) -> str:
    return _text_fragment(value)[:MAX_DIAGNOSTIC_TEXT]


def _bounded_payload(value: Any, depth: int = 0) -> Any:
    if depth >= 5:
        return "<truncated>"
    if isinstance(value, dict):
        return {str(key): _bounded_payload(item, depth + 1) for key, item in list(value.items())[:100]}
    if isinstance(value, list):
        return [_bounded_payload(item, depth + 1) for item in value[:50]]
    if isinstance(value, str):
        return _bounded_fragment(value)
    return value


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


def map_session_event(message: dict[str, Any], *, step_scope: dict[str, Any] | None = None) -> KimiEvent | None:
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
        "adapter": "kimi_server",
        "seq": seq,
        "offset": message.get("offset"),
        "agent_id": agent_id,
        "agent_scope": agent_scope,
        "source_kind": body_type,
    }
    origin_timestamp = _provider_origin_timestamp(body, message)
    if origin_timestamp is not None:
        event_data["provider_origin_timestamp"] = origin_timestamp
    for source_key, target_key in (
        ("disclosable", "disclosable"),
        ("isDisclosable", "disclosable"),
        ("private", "private"),
        ("isPrivate", "private"),
        ("non_disclosable", "non_disclosable"),
        ("nonDisclosable", "non_disclosable"),
        ("visibility", "visibility"),
        ("reasoningVisibility", "reasoning_visibility"),
        ("hidden", "hidden"),
    ):
        if source_key in body:
            event_data[target_key] = body[source_key]
    prompt_id = _str(body.get("promptId"))
    if prompt_id:
        event_data["prompt_id"] = prompt_id
    epoch = _str(message.get("epoch"))
    if epoch:
        event_data["epoch"] = epoch
    for key in ("step_id", "step_number"):
        if step_scope and step_scope.get(key) not in (None, ""):
            event_data[key] = step_scope[key]
    offset = body.get("offset") if isinstance(body.get("offset"), int) else message.get("offset")
    event_data["offset"] = offset
    native_id = _str(body.get("messageId") or body.get("itemId") or body.get("streamId") or body.get("eventId"))
    if body_type in {"thinking.delta", "assistant.delta"} and not native_id and step_scope and step_scope.get("step_id"):
        identity = [session_id, epoch or step_scope.get("epoch", ""), _str(body.get("turnId")),
                    agent_id or "main", step_scope["step_id"], body_type.split(".", 1)[0]]
        native_id = "kimi-stream:" + hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode("utf-8")).hexdigest()
        event_data["lifecycle_stream"] = True
    base: dict[str, Any] = {
        "thread_id": session_id,
        "data": event_data,
        "provider": "kimi",
        "session_id": session_id,
        "event_id": _str(body.get("eventId") or native_id or body.get("toolCallId") or body.get("callId") or body.get("stepId") or body.get("id")),
        "fragment_id": _str(body.get("fragmentId") or body.get("deltaId") or message.get("id")),
        "offset": offset if isinstance(offset, int) else None,
        "stream_id": native_id or _str(body.get("toolCallId") or body.get("callId") or body.get("stepId")),
        "tool_call_id": _str(body.get("toolCallId") or body.get("callId")),
        "origin_timestamp": origin_timestamp,
    }

    if body_type == "assistant.delta":
        # Assistant deltas are the canonical final-answer stream. Never apply
        # execution-diagnostic bounds or redaction at the protocol boundary.
        delta = _text_fragment(body.get("delta") if body.get("delta") is not None else body.get("text"))
        return KimiEvent(
            type="agent_message_delta",
            text=delta,
            raw_text=delta,
            turn_id=_str(body.get("turnId")),
            item_id=native_id,
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
            item_id=native_id,
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
        retrying = body_type == "turn.step.retrying"
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        return KimiEvent(
            type="item_started" if started else "item_completed",
            turn_id=_str(body.get("turnId")),
            item_id=_str(body.get("stepId") or body.get("step")),
            title="step %s" % _str(body.get("step")),
            display_kind="waiting" if retrying else "step",
            status=_str(body.get("status")) or ("retrying" if retrying else "running" if started else "completed"),
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
        operation = _str(display.get("operation")).lower()
        if operation in {"search", "grep", "glob", "find"}:
            kind = "search"
        elif operation in {"read", "open"}:
            kind = "file_io"
        elif operation in {"write", "edit", "patch", "delete", "create"}:
            kind = "diff"
        elif operation in {"test", "tests", "pytest", "verify"}:
            kind = "test"
        args = body.get("args") if isinstance(body.get("args"), dict) else {}
        return KimiEvent(
            type="item_started",
            turn_id=_str(body.get("turnId")),
            item_id=_str(body.get("toolCallId") or body.get("callId") or body.get("id")),
            title=_str(body.get("description") or body.get("title") or body.get("name") or kind),
            command=_bounded_fragment(body.get("command") or args.get("command")),
            display_kind=_TOOL_KIND_DISPLAY.get(kind, "command" if body_type == "shell.started" else kind or "tool"),
            status="running",
            data={**base["data"], "operation_kind": _TOOL_KIND_DISPLAY.get(kind, kind or "tool"), "tool": _bounded_payload(body)},
            thread_id=session_id,
        )
    if body_type in ("tool.progress", "shell.output"):
        delta = _bounded_fragment(body.get("delta") if body.get("delta") is not None else body.get("output") if body.get("output") is not None else body.get("text"))
        return KimiEvent(
            type="agent_message_delta",
            turn_id=_str(body.get("turnId")),
            item_id=_str(body.get("toolCallId") or body.get("callId") or body.get("id")),
            text=delta,
            raw_text=delta,
            display_kind="commentary",
            **base,
        )
    if body_type == "tool.call.delta":
        # Partial JSON argument fragments; not user-visible, skip.
        return None
    if body_type in ("tool.result", "shell.completed"):
        display = body.get("display") if isinstance(body.get("display"), dict) else {}
        kind = _str(display.get("kind") or body.get("kind") or body.get("toolKind"))
        operation = _str(display.get("operation")).lower()
        if operation in {"search", "grep", "glob", "find"}:
            kind = "search"
        elif operation in {"read", "open"}:
            kind = "file_io"
        elif operation in {"write", "edit", "patch", "delete", "create"}:
            kind = "diff"
        elif operation in {"test", "tests", "pytest", "verify"}:
            kind = "test"
        result_text = _bounded_fragment(body.get("summary") if body.get("summary") is not None else body.get("output"))
        tool_body = _bounded_payload(body)
        return KimiEvent(
            type="item_completed",
            turn_id=_str(body.get("turnId")),
            item_id=_str(body.get("toolCallId") or body.get("callId") or body.get("id")),
            title=_str(body.get("description") or body.get("title") or body.get("name")),
            command=_bounded_fragment(body.get("command")),
            exit_code=body.get("exitCode") if isinstance(body.get("exitCode"), int) else None,
            status=_str(body.get("status")) or "completed",
            text=result_text,
            raw_text=result_text,
            display_kind="command" if body_type == "shell.completed" else _TOOL_KIND_DISPLAY.get(kind, kind or "tool"),
            data={**base["data"], "operation_kind": _TOOL_KIND_DISPLAY.get(kind, kind or "tool"), "tool": tool_body},
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
    lifecycle_state = body_type.rsplit(".", 1)[-1]
    if lifecycle_state in {"started", "completed", "failed", "interrupted", "retrying", "waiting"}:
        completed = lifecycle_state in {"completed", "failed", "interrupted"}
        return KimiEvent(
            type="item_completed" if completed else "item_started",
            turn_id=_str(body.get("turnId")),
            item_id=_str(body.get("itemId") or body.get("id") or body.get("callId")),
            display_kind="waiting" if lifecycle_state in {"retrying", "waiting"} else "unknown_action",
            status=lifecycle_state,
            text=_text_fragment(body.get("message") or body.get("summary")),
            data={**base["data"], "structured_unknown": True, "protocol": body},
            thread_id=session_id,
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
        data={**base["data"], "unmapped": True, "raw": _bounded_payload(body)},
        thread_id=session_id,
    )
