from __future__ import annotations

from dataclasses import dataclass
import json
import hashlib
import re
import time
import uuid


DEFAULT_PAIR_ID = "default"
PROTOCOL_V2 = 2
MAX_SIGNED_64 = (1 << 63) - 1


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


def canonical_durable_bytes(payload: dict) -> bytes:
    """Canonical immutable representation; transport delivery metadata is excluded."""
    immutable = {k: v for k, v in payload.items() if k not in {"canonical_hash", "delivery_attempt", "delivered_at"}}
    try:
        return json.dumps(immutable, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("INVALID_CANONICAL_VALUE") from exc


def canonical_durable_hash(payload: dict) -> str:
    return hashlib.sha256(canonical_durable_bytes(payload)).hexdigest()


def _required_text(payload: dict, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError(f"INVALID_{key.upper()}")
    return value.strip()


def _int64(payload: dict, key: str, *, minimum: int = 0) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum or value > MAX_SIGNED_64:
        raise ValueError(f"INVALID_{key.upper()}")
    return value


def validate_v2_durable(payload: dict, *, verify_hash: bool = True) -> dict:
    if not isinstance(payload, dict) or payload.get("protocol_version") != PROTOCOL_V2:
        raise ValueError("INVALID_PROTOCOL_VERSION")
    if "epoch" in payload:
        raise ValueError("DURABLE_EPOCH_FORBIDDEN")
    _required_text(payload, "event_id")
    _required_text(payload, "kind")
    _required_text(payload, "chat_id")
    _required_text(payload, "domain")
    _int64(payload, "revision")
    _int64(payload, "sync_sequence", minimum=1)
    if "execution_sequence" in payload:
        _int64(payload, "execution_sequence", minimum=1)
    if not isinstance(payload.get("body"), dict):
        raise ValueError("INVALID_BODY")
    supplied = payload.get("canonical_hash")
    if not isinstance(supplied, str) or not supplied.strip():
        raise ValueError("INVALID_CANONICAL_HASH")
    expected = canonical_durable_hash(payload)
    if verify_hash and supplied != expected:
        raise ValueError("CANONICAL_HASH_MISMATCH")
    return {**payload, "canonical_hash": expected}


def validate_v2_ephemeral(payload: dict, *, expected_epoch: str | None = None) -> dict:
    if not isinstance(payload, dict) or payload.get("protocol_version") != PROTOCOL_V2:
        raise ValueError("INVALID_PROTOCOL_VERSION")
    _required_text(payload, "request_id")
    _required_text(payload, "chat_id")
    epoch = _required_text(payload, "epoch")
    if expected_epoch is not None and epoch != expected_epoch:
        raise ValueError("STALE_EPOCH")
    if not isinstance(payload.get("body"), dict):
        raise ValueError("INVALID_BODY")
    return dict(payload)


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


FILE_EVENT_TYPES = {
    "file_list",
    "file_add",
    "file_offer",
    "file_accept",
    "file_reject",
    "file_upload_request",
    "file_download_request",
    "file_progress",
    "file_pause",
    "file_paused",
    "file_resume",
    "file_resumed",
    "file_cancel",
    "file_canceled",
    "file_complete",
    "file_error",
    "file_delete",
    "file_probe",
}


def _int_body_field(body: dict, key: str) -> None:
    if key not in body:
        return
    try:
        body[key] = int(body[key])
    except (TypeError, ValueError):
        body[key] = 0


def build_file_command_event(
    *,
    request_id: str,
    event_type: str,
    device_id: str,
    body: dict,
    chat_id: str | None = None,
) -> dict:
    normalized_type = str(event_type or "").strip()
    if normalized_type not in FILE_EVENT_TYPES:
        raise ValueError("invalid_file_event_type")
    normalized_body = dict(body or {})
    for key in ("size_bytes", "transferred_bytes", "speed_bytes_per_second"):
        _int_body_field(normalized_body, key)
    event = {
        "type": normalized_type,
        "event_id": make_event_id(normalized_type),
        "request_id": str(request_id or "").strip(),
        "device_id": str(device_id or "").strip(),
        "body": normalized_body,
        "ts": now_ts(),
    }
    if chat_id:
        event["chat_id"] = str(chat_id or "").strip()
    return event
