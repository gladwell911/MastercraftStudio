from __future__ import annotations

from dataclasses import dataclass
import json
import re
import time
import uuid


DEFAULT_PAIR_ID = "default"


def normalize_pair_id(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", str(value or "").lower())
    normalized = re.sub(r"[-_]+", "-", normalized).strip("-_")
    return normalized or DEFAULT_PAIR_ID


def stream_name(prefix: str, pair_id: str) -> str:
    return f"{prefix}_{normalize_pair_id(pair_id).replace('-', '_')}"


@dataclass(frozen=True)
class NatsSubjects:
    pair_id: str
    commands: str
    events: str
    files: str
    command_stream: str
    event_stream: str

    @classmethod
    def from_pair_id(cls, pair_id: str) -> "NatsSubjects":
        normalized_pair_id = normalize_pair_id(pair_id)
        return cls(
            pair_id=normalized_pair_id,
            commands=f"zgwd.{normalized_pair_id}.commands",
            events=f"zgwd.{normalized_pair_id}.events",
            files=f"zgwd.{normalized_pair_id}.files",
            command_stream=stream_name("ZGWD_COMMANDS", normalized_pair_id),
            event_stream=stream_name("ZGWD_EVENTS", normalized_pair_id),
        )


def now_ts() -> float:
    return time.time()


def make_event_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def encode_payload(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def decode_payload(data: bytes) -> dict:
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_json") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid_payload")
    return payload


def build_response_event(
    request_id: str,
    status: int,
    body: dict,
    chat_id: str | None = None,
) -> dict:
    event = {
        "type": "response",
        "event_id": f"response-{request_id}",
        "request_id": request_id,
        "ok": 200 <= int(status) < 300,
        "status": int(status),
        "body": body,
        "ts": now_ts(),
    }
    if chat_id:
        event["chat_id"] = chat_id
    return event


def build_error_response(request_id: str, status: int, error: str) -> dict:
    return build_response_event(
        request_id=request_id,
        status=status,
        body={"error": error},
    )
