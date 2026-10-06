from __future__ import annotations

import json
import sys
import threading
from collections import OrderedDict
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
    MAX_EVENT_SCOPES = 256
    MAX_STARTUP_EVENTS = 1024
    MAX_STARTUP_EVENT_BYTES = 4 * 1024 * 1024

    def __init__(self, client_factory: ClientFactory | None = None, output: TextIO | None = None) -> None:
        self.client_factory = client_factory or self._default_client_factory
        self.output = output or sys.stdout
        self._lock = threading.RLock()
        self._clients: dict[tuple[str, str], Any] = {}
        self._turn_indices: dict[tuple[str, str], Any] = {}
        self._thread_turn_scopes: OrderedDict[tuple[str, str, str], tuple[int, int]] = OrderedDict()
        self._turn_id_scopes: OrderedDict[tuple[str, str, str], tuple[int, int, str] | None] = OrderedDict()
        self._input_request_clients: dict[tuple[str, str], tuple[str, str]] = {}
        self._ambiguous_input_requests: set[tuple[str, str]] = set()
        self._command_requests: dict[tuple[str, str, str], dict] = {}
        self._answered_command_requests: set[tuple[str, str, str]] = set()
        self._active_command_owners: dict[tuple[str, str], tuple] = {}
        self._startup_events: dict[tuple[str, str], dict] = {}
        self._native_owners: OrderedDict[tuple[str, str, str], dict] = OrderedDict()
        self._item_owners: OrderedDict[tuple[str, str, str, str], tuple[int, int, str]] = OrderedDict()

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
        elif message_type == "reply_command_approval":
            self._handle_reply_command_approval(message)
        elif message_type == "compact_thread":
            self._handle_compact_thread(message)
        elif message_type == "cancel_turn":
            self._handle_cancel_turn(message)
        elif message_type == "read_chat_information":
            self._handle_read_chat_information(message)
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
            self._thread_turn_scopes.clear()
            self._turn_id_scopes.clear()
            self._input_request_clients.clear()
            self._ambiguous_input_requests.clear()
            self._command_requests.clear()
            self._answered_command_requests.clear()
            self._active_command_owners.clear()
            self._startup_events.clear()
            self._native_owners.clear()
            self._item_owners.clear()
        for client in clients:
            client.close()

    def _client_for(self, chat_id: str, model: str) -> Any:
        normalized_chat_id = str(chat_id or "").strip()
        normalized_model = str(model or "").strip() or DEFAULT_CODEX_MODEL
        key = (normalized_chat_id, normalized_model)
        with self._lock:
            if key not in self._clients:
                client = self.client_factory(
                    lambda event, chat_id=normalized_chat_id, model=normalized_model: self._on_event(
                        chat_id, model, event, client
                    ),
                    normalized_model,
                )
                self._clients[key] = client
            return self._clients[key]

    def _on_event(self, chat_id: str, model: str, event: CodexEvent, source_client=None) -> None:
        with self._lock:
            if source_client is not None and self._clients.get((chat_id, model)) is not source_client:
                return
            pending = self._startup_events.get((chat_id, model))
            if pending is not None:
                turn_id = str(getattr(event, "turn_id", "") or "").strip()
                prior_scope = self._turn_id_scopes.get((chat_id, model, turn_id))
                event_thread = str(getattr(event, "thread_id", "") or "").strip()
                item_id = str(getattr(event, "item_id", "") or "").strip()
                prior_item = self._item_owners.get((chat_id, model, turn_id, item_id)) if item_id else None
                if turn_id in pending["prior_turn_ids"] and prior_scope is not None and (not event_thread or event_thread == prior_scope[2]) and (turn_id != pending["steer_turn_id"] or prior_item is not None):
                    self._dispatch_event(chat_id, model, event)
                    return
                if turn_id == pending["steer_turn_id"] and prior_scope is not None and (not event_thread or event_thread == prior_scope[2]):
                    if str(getattr(event, "subtype", "") or "") == "userMessage" and prior_item is None:
                        pending["boundary_seen"] = True
                    if not pending["boundary_seen"] and not pending["prior_terminal_seen"]:
                        self._dispatch_event(chat_id, model, event)
                        if event.type == "turn_completed":
                            pending["prior_terminal_seen"] = True
                        return
                if pending["overflow"]:
                    if event.type == "turn_completed" and turn_id in pending["prior_turn_ids"] and not pending["boundary_seen"] and not any(previous.turn_id == turn_id for previous in pending["prior_completions"]):
                        pending["prior_completions"].append(event)
                    return
                if event.type == "turn_completed" and turn_id in pending["prior_turn_ids"] and not pending["boundary_seen"] and not any(previous.turn_id == turn_id for previous in pending["prior_completions"]):
                    pending["prior_completions"].append(event)
                size = len(json.dumps(event_to_payload(event), ensure_ascii=False).encode("utf-8"))
                if len(pending["events"]) >= self.MAX_STARTUP_EVENTS or pending["bytes"] + size > self.MAX_STARTUP_EVENT_BYTES:
                    pending["events"].clear()
                    pending["overflow"] = True
                    return
                pending["events"].append(event)
                pending["bytes"] += size
                return
            self._dispatch_event(chat_id, model, event)

    def _dispatch_event(self, chat_id: str, model: str, event: CodexEvent) -> None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "model": model,
            "event": event_to_payload(event),
        }
        thread_id = str(getattr(event, "thread_id", "") or "").strip()
        turn_id = str(getattr(event, "turn_id", "") or "").strip()
        key = (chat_id, model)
        request_id = getattr(event, "request_id", None)
        method = str(getattr(event, "method", "") or "")
        event_type = str(getattr(event, "type", "") or "")
        with self._lock:
            scope = None
            known_turn = self._turn_id_scopes.get((chat_id, model, turn_id)) if turn_id else None
            if turn_id and (chat_id, model, turn_id) in self._turn_id_scopes:
                self._turn_id_scopes.move_to_end((chat_id, model, turn_id))
            if known_turn is not None and (not thread_id or known_turn[2] == thread_id):
                scope = known_turn[:2]
            elif thread_id and (not turn_id or (chat_id, model, turn_id) not in self._turn_id_scopes):
                key = (chat_id, model, thread_id)
                scope = self._thread_turn_scopes.get(key)
                if scope is not None:
                    self._thread_turn_scopes.move_to_end(key)
            native = self._native_owners.get((chat_id, model, turn_id)) if turn_id else None
            item_id = str(getattr(event, "item_id", "") or "").strip()
            item_key = (chat_id, model, turn_id, item_id)
            if native is not None and (not thread_id or thread_id == native["thread_id"]):
                owner = self._item_owners.get(item_key) if item_id else None
                if owner is not None:
                    scope = owner[:2]
                else:
                    # Only the accepted user item establishes a steer boundary.
                    if str(getattr(event, "subtype", "") or "") == "userMessage" and item_id:
                        data = event.data if isinstance(event.data, dict) else {}
                        content = data.get("content") or []
                        text = "\n".join(str(part.get("text") or "") for part in content if isinstance(part, dict) and part.get("type") in {"text", "inputText"})
                        if not text:
                            text = str(data.get("text") or event.text or "")
                        boundaries = native["boundaries"]
                        if boundaries and text.strip() == boundaries[0]["text"]:
                            native["current"] = boundaries.pop(0)["owner"]
                            native["steer_boundary_seen"] = True
                    scope = None if native["boundaries"] and not native["steer_boundary_seen"] else native["current"][:2]
                    relevant_item = str(getattr(event, "subtype", "") or "") in {"userMessage", "agentMessage"} or str(getattr(event, "phase", "") or "") == "final_answer" or event_type == "agent_message_delta"
                    if item_id and scope is not None and relevant_item:
                        item_ids = native.setdefault("item_ids", set())
                        if len(item_ids) >= self.MAX_EVENT_SCOPES:
                            scope = None
                            if not native.get("item_limit_reported"):
                                native["item_limit_reported"] = True
                                self.emit("error", {"chat_id": chat_id, "model": model,
                                    "turn_idx": native["current"][0], "context_generation": native["current"][1],
                                    "message": "Codex item ownership limit exceeded"})
                        else:
                            item_ids.add(item_id)
                            self._item_owners[item_key] = native["current"]
                if event_type == "turn_completed":
                    scope = native["owners"][-1][:2]
                    payload["completion_owners"] = [
                        {"turn_idx": owner[0], "context_generation": owner[1]}
                        for owner in native["owners"]
                    ]
            if scope is not None:
                payload["turn_idx"], payload["context_generation"] = scope
            if event_type == "server_request" and method == "item/commandExecution/requestApproval" and request_id is not None and scope is not None and thread_id and turn_id:
                request_key = (chat_id, model, str(request_id))
                params = event.params if isinstance(event.params, dict) else {}
                if request_key not in self._answered_command_requests:
                    self._command_requests.setdefault(request_key, {
                        "client": self._clients.get((chat_id, model)), "request_id": request_id,
                        "thread_id": thread_id, "turn_id": turn_id, "turn_idx": scope[0],
                        "context_generation": scope[1], "decisions": params.get("availableDecisions"),
                    })
            if request_id is not None and (method == "item/tool/requestUserInput" or event_type == "server_request"):
                request_key = (chat_id, str(request_id))
                existing_key = self._input_request_clients.get(request_key)
                if existing_key is not None and existing_key != key:
                    self._ambiguous_input_requests.add(request_key)
                    self._input_request_clients.pop(request_key, None)
                elif request_key not in self._ambiguous_input_requests:
                    self._input_request_clients[request_key] = key
            if event_type == "turn_completed":
                active_owner = self._active_command_owners.get((chat_id, model))
                if active_owner is not None and active_owner[:2] == (thread_id, turn_id):
                    self._active_command_owners.pop((chat_id, model), None)
        self.emit("event", payload)

    def _handle_start_turn(self, message: dict[str, Any]) -> None:
        payload = dict(message.get("payload") or {})
        chat_id = str(payload.get("chat_id") or "").strip()
        model = str(payload.get("model") or "").strip() or DEFAULT_CODEX_MODEL
        turn_idx = payload.get("turn_idx")
        context_generation = int(payload.get("context_generation") or 0)
        service_tier = payload.get("service_tier")
        service_tier_arg = service_tier if str(service_tier or "").strip() else None
        if not chat_id:
            self._emit_protocol_error(message, "start_turn requires payload.chat_id")
            return

        startup_key = (chat_id, model)
        with self._lock:
            self._startup_events[startup_key] = {
                "events": [], "bytes": 0, "overflow": False,
                "prior_completions": [], "boundary_seen": False, "prior_terminal_seen": False,
                "prior_turn_ids": frozenset(key[2] for key, scope in self._turn_id_scopes.items()
                                             if key[:2] == startup_key and scope is not None),
                "steer_turn_id": str(payload.get("turn_id") or "").strip() if payload.get("should_steer") else "",
            }
        accepted = False
        try:
            client = self._client_for(chat_id, model)
            with self._lock:
                self._turn_indices[(chat_id, model)] = turn_idx

            thread_id = str(payload.get("thread_id") or "").strip()
            items = list(payload.get("input_items") or [])
            if not items and payload.get("question"):
                items = [{"type": "text", "text": str(payload.get("question") or "")}]
            should_steer = (bool(payload.get("should_steer")) and bool(thread_id)
                            and bool(str(payload.get("turn_id") or "").strip())
                            and hasattr(client, "steer_turn_items"))
            steered = False
            if should_steer:
                try:
                    turn_response = client.steer_turn_items(
                        thread_id,
                        str(payload.get("turn_id") or "").strip(),
                        items,
                    )
                    steered = True
                except Exception as exc:
                    if not self._is_no_active_turn_error(exc):
                        raise
                    should_steer = False
            if not should_steer:
                if hasattr(client, "prepare_task"):
                    client.prepare_task(payload.get("cwd") or "")
                if not thread_id:
                    thread_id = self._start_thread(client, payload, service_tier_arg)
                elif hasattr(client, "resume_thread"):
                    try:
                        client.resume_thread(
                            thread_id,
                            approval_policy=self._approval_policy(payload),
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
                turn_response = client.start_turn_items(thread_id, items, service_tier=service_tier_arg)
            turn_id = self._extract_id(turn_response, "turn", "turn_id")
            if steered and not turn_id:
                turn_id = str(payload.get("turn_id") or "").strip()
            if turn_id and isinstance(turn_idx, int):
                with self._lock:
                    key = (chat_id, model, turn_id)
                    value = (turn_idx, context_generation, thread_id)
                    overflow = self._startup_events[startup_key]["overflow"]
                    native = self._native_owners.get(key)
                    if not overflow:
                        self._active_command_owners[(chat_id, model)] = (thread_id, turn_id, turn_idx, context_generation)
                        self._remember_scope(self._thread_turn_scopes, (chat_id, model, thread_id), (turn_idx, context_generation))
                        if steered and native is not None and native["thread_id"] == thread_id:
                            if len(native["owners"]) >= self.MAX_EVENT_SCOPES:
                                raise RuntimeError("Codex accepted input owner limit exceeded")
                            native["owners"].append(value)
                            input_text = "\n".join(str(item.get("text") or "") for item in items
                                                   if isinstance(item, dict) and item.get("type") == "text")
                            native["boundaries"].append({"owner": value, "text": input_text.strip()})
                        elif not steered or native is None:
                            if key in self._turn_id_scopes and self._turn_id_scopes[key] != value:
                                self._remember_scope(self._turn_id_scopes, key, None)
                            else:
                                self._remember_scope(self._turn_id_scopes, key, value)
                                self._remember_scope(self._native_owners, key, {"thread_id": thread_id, "current": value, "owners": [value], "boundaries": [], "steer_boundary_seen": False})
            self.emit(
                "thread_state",
                {
                    "chat_id": chat_id,
                    "turn_idx": turn_idx,
                    "model": model,
                    "thread_id": thread_id,
                    "turn_id": turn_id,
                    "context_generation": context_generation,
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
                    "context_generation": context_generation,
                    "active": True,
                },
                request_id=message.get("id"),
            )
            accepted = True
        except Exception as exc:
            self._emit_scoped_error(message, str(exc), chat_id, turn_idx, model)
        finally:
            # Keep the lock through the flush: newly arriving frames cannot
            # overtake earlier frames or the authoritative request ack.
            with self._lock:
                pending = self._startup_events.pop(startup_key, None)
                if accepted and pending is not None:
                    if pending["overflow"]:
                        for event in pending["prior_completions"]:
                            self._dispatch_event(chat_id, model, event)
                        self._emit_scoped_error(message, "Codex startup event buffer exceeded its limit", chat_id, turn_idx, model)
                    else:
                        for event in pending["events"]:
                            self._dispatch_event(chat_id, model, event)
                elif pending is not None:
                    for event in pending["prior_completions"]:
                        self._dispatch_event(chat_id, model, event)

    def _remember_scope(self, scopes: OrderedDict, key: tuple, value: tuple | None) -> None:
        scopes.pop(key, None)
        scopes[key] = value
        while len(scopes) > self.MAX_EVENT_SCOPES:
            retired_key, _ = scopes.popitem(last=False)
            if scopes is self._native_owners:
                for item_key in list(self._item_owners):
                    if item_key[:3] == retired_key:
                        self._item_owners.pop(item_key, None)

    def _start_thread(self, client: Any, payload: dict[str, Any], service_tier_arg: str | None) -> str:
        thread_response = client.start_thread(
            cwd=payload.get("cwd") or "",
            approval_policy=self._approval_policy(payload),
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

    @staticmethod
    def _approval_policy(payload: dict) -> str:
        return "on-request" if payload.get("approval_policy") == "on-request" else "never"

    def _handle_reply_command_approval(self, message: dict[str, Any]) -> None:
        payload = dict(message.get("payload") or {})
        key = (str(payload.get("chat_id") or ""), str(payload.get("model") or ""), str(payload.get("request_id")))
        with self._lock:
            pending = self._command_requests.get(key)
            if pending is None:
                return
            client = self._clients.get(key[:2])
            if client is not pending["client"]:
                self._command_requests.pop(key, None)
                return
            if any(payload.get(field) != pending[field] for field in ("thread_id", "turn_id", "turn_idx", "context_generation")):
                return
            scope = self._turn_id_scopes.get((key[0], key[1], pending["turn_id"]))
            if scope != (pending["turn_idx"], pending["context_generation"], pending["thread_id"]):
                return
            if self._thread_turn_scopes.get((key[0], key[1], pending["thread_id"])) != scope[:2]:
                return
            if self._active_command_owners.get(key[:2]) != (pending["thread_id"], pending["turn_id"], pending["turn_idx"], pending["context_generation"]):
                return
            decision = payload.get("decision")
            allowed = pending["decisions"] if isinstance(pending["decisions"], list) else ["accept", "decline"]
            if decision not in ("accept", "decline", "cancel") or decision not in allowed:
                return
            self._command_requests.pop(key, None)
            self._answered_command_requests.add(key)
            try:
                client.respond_command_approval(pending["request_id"], decision)
            except Exception as exc:
                self._emit_scoped_error(message, str(exc), key[0], pending["turn_idx"], key[1])

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

    def _handle_read_chat_information(self, message: dict[str, Any]) -> None:
        payload = dict(message.get("payload") or {})
        chat_id = str(payload.get("chat_id") or "").strip()
        model = str(payload.get("model") or "").strip() or DEFAULT_CODEX_MODEL
        if not chat_id:
            self._emit_protocol_error(message, "read_chat_information requires payload.chat_id")
            return
        result: dict[str, Any] = {"chat_id": chat_id, "model": model, "identity": payload.get("identity"),
                                  "generation": payload.get("generation"),
                                  "context_only": bool(payload.get("context_only"))}
        try:
            client = self._client_for(chat_id, model)
        except Exception as exc:
            result["usage_error"] = str(exc)
            result["account_error"] = str(exc)
            result["rate_limits_error"] = str(exc)
        else:
            identity = payload.get("identity") or []
            if len(identity) > 2 and identity[2]:
                try:
                    result["native_usage"] = client.read_native_usage(str(identity[2]))
                except Exception as exc:
                    result["usage_error"] = str(exc)
            if result["context_only"]:
                self.emit("chat_information", result, request_id=message.get("id"))
                return
            try:
                result["account"] = client.read_account(refresh_token=False)
            except Exception as exc:
                result["account_error"] = str(exc)
            try:
                result["rate_limits"] = client.read_rate_limits()
            except Exception as exc:
                result["rate_limits_error"] = str(exc)
        self.emit("chat_information", result, request_id=message.get("id"))

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
        camel_key = object_key + "Id"
        return str(response.get(flat_key) or response.get(camel_key) or response.get("id") or "")

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
                "context_generation": int((message.get("payload") or {}).get("context_generation") or 0),
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
