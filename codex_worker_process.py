from __future__ import annotations

import sys
import traceback
from typing import Any, Callable, TextIO

from codex_client import CodexAppServerClient, CodexEvent, DEFAULT_CODEX_MODEL
from codex_worker_protocol import (
    decode_worker_line,
    encode_worker_message,
    event_to_payload,
    make_worker_event,
)


ClientFactory = Callable[[Callable[[CodexEvent], None], str], CodexAppServerClient]


class CodexWorkerRuntime:
    def __init__(self, client_factory: ClientFactory | None = None, output: TextIO | None = None) -> None:
        self.client_factory = client_factory or self._default_client_factory
        self.output = output or sys.stdout
        self._clients: dict[tuple[str, str], Any] = {}
        self._chat_models: dict[str, str] = {}
        self._turn_indices: dict[tuple[str, str], Any] = {}

    def emit(self, message_type: str, payload: dict[str, Any] | None = None, request_id: str | None = None) -> None:
        self.output.write(encode_worker_message(make_worker_event(message_type, payload, request_id)))
        self.output.flush()

    def handle_message(self, message: dict[str, Any]) -> bool:
        message_type = str(message.get("type") or "")
        if message_type == "start_turn":
            self._handle_start_turn(message)
        elif message_type == "reply_user_input":
            self._handle_reply_user_input(message)
        elif message_type == "ping":
            self.emit("pong", request_id=message.get("id"))
        elif message_type == "shutdown":
            self.close()
            return False
        else:
            self.emit(
                "error",
                {"message": f"unsupported worker message type: {message_type}"},
                request_id=message.get("id"),
            )
        return True

    def close(self) -> None:
        clients = list(self._clients.values())
        self._clients.clear()
        self._chat_models.clear()
        self._turn_indices.clear()
        for client in clients:
            client.close()

    def _client_for(self, chat_id: str, model: str) -> Any:
        normalized_chat_id = str(chat_id or "").strip()
        normalized_model = str(model or "").strip() or DEFAULT_CODEX_MODEL
        key = (normalized_chat_id, normalized_model)
        if key not in self._clients:
            self._clients[key] = self.client_factory(
                lambda event, chat_id=normalized_chat_id, model=normalized_model: self._on_event(chat_id, model, event),
                normalized_model,
            )
        self._chat_models[normalized_chat_id] = normalized_model
        return self._clients[key]

    def _on_event(self, chat_id: str, model: str, event: CodexEvent) -> None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "event": event_to_payload(event),
        }
        key = (chat_id, model)
        if key in self._turn_indices:
            payload["turn_idx"] = self._turn_indices[key]
        self.emit("event", payload)

    def _handle_start_turn(self, message: dict[str, Any]) -> None:
        payload = dict(message.get("payload") or {})
        chat_id = str(payload.get("chat_id") or "").strip()
        model = str(payload.get("model") or "").strip() or DEFAULT_CODEX_MODEL
        turn_idx = payload.get("turn_idx")
        service_tier = payload.get("service_tier")
        service_tier_arg = service_tier if str(service_tier or "").strip() else None
        client = self._client_for(chat_id, model)
        self._turn_indices[(chat_id, model)] = turn_idx

        thread_id = str(payload.get("thread_id") or "").strip()
        if not thread_id:
            thread_response = client.start_thread(cwd=payload.get("cwd") or "", service_tier=service_tier_arg)
            thread_id = self._extract_id(thread_response, "thread", "thread_id")
        self.emit(
            "thread_state",
            {
                "chat_id": chat_id,
                "turn_idx": turn_idx,
                "model": model,
                "thread_id": thread_id,
            },
            request_id=message.get("id"),
        )

        items = list(payload.get("input_items") or [])
        if not items and payload.get("question"):
            items = [{"type": "text", "text": str(payload.get("question") or "")}]
        turn_response = client.start_turn_items(thread_id, items, service_tier=service_tier_arg)
        turn_id = self._extract_id(turn_response, "turn", "turn_id")
        self.emit(
            "turn_finished",
            {
                "chat_id": chat_id,
                "turn_idx": turn_idx,
                "model": model,
                "thread_id": thread_id,
                "turn_id": turn_id,
            },
            request_id=message.get("id"),
        )

    def _handle_reply_user_input(self, message: dict[str, Any]) -> None:
        payload = dict(message.get("payload") or {})
        chat_id = str(payload.get("chat_id") or "").strip()
        model = str(payload.get("model") or "").strip() or self._chat_models.get(chat_id, DEFAULT_CODEX_MODEL)
        client = self._clients[(chat_id, model)]
        client.respond_tool_request_user_input(payload.get("request_id"), dict(payload.get("answers") or {}))

    @staticmethod
    def _default_client_factory(on_event: Callable[[CodexEvent], None], codex_model: str) -> CodexAppServerClient:
        return CodexAppServerClient(on_event=on_event, codex_model=codex_model)

    @staticmethod
    def _extract_id(response: dict[str, Any], object_key: str, flat_key: str) -> str:
        if not isinstance(response, dict):
            return ""
        nested = response.get(object_key)
        if isinstance(nested, dict) and nested.get("id"):
            return str(nested.get("id") or "")
        return str(response.get(flat_key) or response.get("id") or "")


def main() -> int:
    runtime = CodexWorkerRuntime()
    runtime.emit("ready")
    try:
        for line in sys.stdin:
            try:
                if not runtime.handle_message(decode_worker_line(line)):
                    break
            except Exception as exc:
                runtime.emit(
                    "fatal",
                    {
                        "message": str(exc),
                        "traceback": traceback.format_exc(),
                    },
                )
    finally:
        runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
