from __future__ import annotations

import sys
import threading
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
        self._lock = threading.RLock()
        self._clients: dict[tuple[str, str], Any] = {}
        self._turn_indices: dict[tuple[str, str], Any] = {}
        self._input_request_clients: dict[tuple[str, str], tuple[str, str]] = {}
        self._ambiguous_input_requests: set[tuple[str, str]] = set()

    def emit(self, message_type: str, payload: dict[str, Any] | None = None, request_id: str | None = None) -> None:
        line = encode_worker_message(make_worker_event(message_type, payload, request_id))
        with self._lock:
            self.output.write(line)
            self.output.flush()

    def handle_message(self, message: dict[str, Any]) -> bool:
        message_type = str(message.get("type") or "")
        if message_type == "start_turn":
            self._handle_start_turn(message)
        elif message_type == "reply_user_input":
            self._handle_reply_user_input(message)
        elif message_type == "compact_thread":
            self._handle_compact_thread(message)
        elif message_type == "cancel_turn":
            self._handle_cancel_turn(message)
        elif message_type == "ping":
            self.emit("pong", request_id=message.get("id"))
        elif message_type == "shutdown":
            self.close()
            return False
        else:
            self._emit_protocol_error(message, f"unsupported worker message type: {message_type}")
        return True

    def close(self) -> None:
        with self._lock:
            clients = list(self._clients.values())
            self._clients.clear()
            self._turn_indices.clear()
            self._input_request_clients.clear()
            self._ambiguous_input_requests.clear()
        for client in clients:
            client.close()

    def _client_for(self, chat_id: str, model: str) -> Any:
        normalized_chat_id = str(chat_id or "").strip()
        normalized_model = str(model or "").strip() or DEFAULT_CODEX_MODEL
        key = (normalized_chat_id, normalized_model)
        with self._lock:
            if key not in self._clients:
                self._clients[key] = self.client_factory(
                    lambda event, chat_id=normalized_chat_id, model=normalized_model: self._on_event(
                        chat_id, model, event
                    ),
                    normalized_model,
                )
            return self._clients[key]

    def _on_event(self, chat_id: str, model: str, event: CodexEvent) -> None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "model": model,
            "event": event_to_payload(event),
        }
        key = (chat_id, model)
        request_id = getattr(event, "request_id", None)
        method = str(getattr(event, "method", "") or "")
        event_type = str(getattr(event, "type", "") or "")
        with self._lock:
            if key in self._turn_indices:
                payload["turn_idx"] = self._turn_indices[key]
            if request_id is not None and (method == "item/tool/requestUserInput" or event_type == "server_request"):
                request_key = (chat_id, str(request_id))
                existing_key = self._input_request_clients.get(request_key)
                if existing_key is not None and existing_key != key:
                    self._ambiguous_input_requests.add(request_key)
                    self._input_request_clients.pop(request_key, None)
                elif request_key not in self._ambiguous_input_requests:
                    self._input_request_clients[request_key] = key
        self.emit("event", payload)

    def _handle_start_turn(self, message: dict[str, Any]) -> None:
        payload = dict(message.get("payload") or {})
        chat_id = str(payload.get("chat_id") or "").strip()
        model = str(payload.get("model") or "").strip() or DEFAULT_CODEX_MODEL
        turn_idx = payload.get("turn_idx")
        service_tier = payload.get("service_tier")
        service_tier_arg = service_tier if str(service_tier or "").strip() else None
        if not chat_id:
            self._emit_protocol_error(message, "start_turn requires payload.chat_id")
            return

        try:
            client = self._client_for(chat_id, model)
            with self._lock:
                self._turn_indices[(chat_id, model)] = turn_idx

            thread_id = str(payload.get("thread_id") or "").strip()
            items = list(payload.get("input_items") or [])
            if not items and payload.get("question"):
                items = [{"type": "text", "text": str(payload.get("question") or "")}]
            if not thread_id:
                thread_id = self._start_thread(client, payload, service_tier_arg)
            elif hasattr(client, "resume_thread"):
                try:
                    client.resume_thread(
                        thread_id,
                        approval_policy="never",
                        sandbox="danger-full-access",
                        personality="pragmatic",
                        cwd=payload.get("cwd") or "",
                        service_tier=service_tier_arg,
                    )
                except Exception as exc:
                    if not (self._is_thread_missing_error(exc) or self._is_rollout_missing_error(exc)):
                        raise
                    thread_id = self._start_thread(client, payload, service_tier_arg)
                    items = self._recovery_input_items(payload, items)

            should_steer = bool(payload.get("should_steer")) and bool(str(payload.get("turn_id") or "").strip())
            if should_steer and hasattr(client, "steer_turn_items"):
                try:
                    turn_response = client.steer_turn_items(
                        thread_id,
                        str(payload.get("turn_id") or "").strip(),
                        items,
                    )
                except Exception as exc:
                    if not self._is_no_active_turn_error(exc):
                        raise
                    turn_response = client.start_turn_items(thread_id, items, service_tier=service_tier_arg)
            else:
                turn_response = client.start_turn_items(thread_id, items, service_tier=service_tier_arg)
            turn_id = self._extract_id(turn_response, "turn", "turn_id")
            self.emit(
                "thread_state",
                {
                    "chat_id": chat_id,
                    "turn_idx": turn_idx,
                    "model": model,
                    "thread_id": thread_id,
                    "turn_id": turn_id,
                    "active": True,
                },
                request_id=message.get("id"),
            )
            self.emit(
                "turn_started_ack",
                {
                    "chat_id": chat_id,
                    "turn_idx": turn_idx,
                    "model": model,
                    "thread_id": thread_id,
                    "turn_id": turn_id,
                    "active": True,
                },
                request_id=message.get("id"),
            )
        except Exception as exc:
            self._emit_scoped_error(message, str(exc), chat_id, turn_idx, model)

    def _start_thread(self, client: Any, payload: dict[str, Any], service_tier_arg: str | None) -> str:
        thread_response = client.start_thread(
            cwd=payload.get("cwd") or "",
            approval_policy="never",
            sandbox="danger-full-access",
            personality="pragmatic",
            service_tier=service_tier_arg,
        )
        return self._extract_id(thread_response, "thread", "thread_id")

    def _recovery_input_items(self, payload: dict[str, Any], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        prompt = self._build_rollout_recovery_prompt(
            list(payload.get("history_turns") or []),
            str(payload.get("question") or ""),
        )
        if not prompt:
            return items
        non_text_items = [item for item in items if not (isinstance(item, dict) and item.get("type") == "text")]
        return [{"type": "text", "text": prompt}, *non_text_items]

    @staticmethod
    def _build_rollout_recovery_prompt(history_turns: list[dict[str, Any]], question: str) -> str:
        clean_question = str(question or "").strip()
        transcript_parts: list[str] = []
        for turn in history_turns or []:
            if not isinstance(turn, dict):
                continue
            prior_question = str(turn.get("question") or "").strip()
            prior_answer = str(turn.get("answer_md") or "").strip()
            if prior_answer == "正在请求...":
                prior_answer = ""
            if prior_question:
                transcript_parts.append(f"用户：{prior_question}")
            if prior_answer:
                transcript_parts.append(f"Codex：{prior_answer}")
        if not transcript_parts:
            return clean_question
        transcript = "\n".join(transcript_parts)
        return (
            "下面是当前聊天在本地保存的历史记录，请把它当作本次会话上下文继续：\n"
            f"{transcript}\n\n"
            "请基于以上上下文继续回答下面这个新问题：\n"
            f"{clean_question}"
        )

    @staticmethod
    def _error_text(exc: Exception | str) -> str:
        return str(exc or "").strip().lower()

    @classmethod
    def _is_thread_missing_error(cls, exc: Exception | str) -> bool:
        text = cls._error_text(exc)
        return "thread not found" in text or "unknown thread" in text

    @classmethod
    def _is_rollout_missing_error(cls, exc: Exception | str) -> bool:
        return "no rollout found" in cls._error_text(exc)

    @classmethod
    def _is_no_active_turn_error(cls, exc: Exception | str) -> bool:
        return "no active turn to steer" in cls._error_text(exc)

    def _handle_reply_user_input(self, message: dict[str, Any]) -> None:
        payload = dict(message.get("payload") or {})
        chat_id = str(payload.get("chat_id") or "").strip()
        request_id = payload.get("request_id")
        if not chat_id:
            self._emit_protocol_error(message, "reply_user_input requires payload.chat_id")
            return
        key = self._client_key_for_reply(chat_id, request_id)
        if key is None:
            self.emit(
                "error",
                {
                    "chat_id": chat_id,
                    "request_id": request_id,
                    "message": "cannot route reply_user_input to a unique Codex client",
                },
                request_id=message.get("id"),
            )
            return
        client = self._clients[key]
        try:
            client.respond_tool_request_user_input(payload.get("request_id"), dict(payload.get("answers") or {}))
        except Exception as exc:
            turn_idx = None
            model = DEFAULT_CODEX_MODEL
            with self._lock:
                turn_idx = self._turn_indices.get(key)
                model = key[1] if len(key) > 1 else DEFAULT_CODEX_MODEL
            self._emit_scoped_error(message, str(exc), chat_id, turn_idx, model)

    def _handle_compact_thread(self, message: dict[str, Any]) -> None:
        payload = dict(message.get("payload") or {})
        chat_id = str(payload.get("chat_id") or "").strip()
        model = str(payload.get("model") or "").strip() or DEFAULT_CODEX_MODEL
        thread_id = str(payload.get("thread_id") or "").strip()
        if not chat_id:
            self._emit_protocol_error(message, "compact_thread requires payload.chat_id")
            return
        if not thread_id:
            self._emit_scoped_error(message, "compact_thread requires payload.thread_id", chat_id, None, model)
            return
        try:
            client = self._client_for(chat_id, model)
            client.compact_thread(thread_id)
        except Exception as exc:
            self._emit_scoped_error(message, str(exc), chat_id, None, model)

    def _handle_cancel_turn(self, message: dict[str, Any]) -> None:
        payload = dict(message.get("payload") or {})
        chat_id = str(payload.get("chat_id") or "").strip()
        model = str(payload.get("model") or "").strip() or DEFAULT_CODEX_MODEL
        thread_id = str(payload.get("thread_id") or "").strip()
        turn_id = str(payload.get("turn_id") or "").strip()
        if not chat_id:
            self._emit_protocol_error(message, "cancel_turn requires payload.chat_id")
            return
        if not thread_id or not turn_id:
            self._emit_scoped_error(message, "cancel_turn requires payload.thread_id and payload.turn_id", chat_id, None, model)
            return
        try:
            client = self._client_for(chat_id, model)
            if hasattr(client, "interrupt_turn"):
                client.interrupt_turn(thread_id, turn_id)
            else:
                client.cancel_turn(thread_id, turn_id)
        except Exception as exc:
            self._emit_scoped_error(message, str(exc), chat_id, None, model)

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

    def _client_key_for_reply(self, chat_id: str, request_id: Any) -> tuple[str, str] | None:
        if not chat_id:
            return None
        with self._lock:
            if request_id is not None:
                request_key = (chat_id, str(request_id))
                if request_key in self._ambiguous_input_requests:
                    return None
                mapped_key = self._input_request_clients.get(request_key)
                if mapped_key is not None:
                    return mapped_key
            matching_keys = [key for key in self._clients if key[0] == chat_id]
            if len(matching_keys) == 1:
                return matching_keys[0]
            return None

    def _emit_scoped_error(
        self,
        message: dict[str, Any],
        error_message: str,
        chat_id: str,
        turn_idx: Any,
        model: str,
    ) -> None:
        self.emit(
            "error",
            {
                "chat_id": chat_id,
                "turn_idx": turn_idx,
                "model": model,
                "message": error_message,
            },
            request_id=message.get("id"),
        )

    def _emit_protocol_error(self, message: dict[str, Any], error_message: str) -> None:
        self.emit("protocol_error", {"message": error_message}, request_id=message.get("id"))


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
