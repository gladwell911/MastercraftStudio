"""Desktop remote API, snapshots, and event publication."""

import asyncio
import copy
import hashlib
import json
import uuid
import wx
from agy_acp import AGY_MODEL_ID, AgyError
from chat_client import is_openrouter_claude_model, read_openrouter_claude_attachments
from codex_client import (
    DEFAULT_CODEX_MODEL,
    codex_service_tier_label,
    is_codex_model,
    normalize_codex_service_tier,
)
from execution_projection import project_execution_rows
from kimi_server_client import is_kimi_model
from mc_frame_presentation import _finite_timestamp, execution_answer_to_plain
from mc_frame_settings import (
    DEFAULT_MODEL_ID,
    REMOTE_STATE_DEFAULT_TURN_LIMIT,
    REQUESTING_TEXT,
    STARTUP_DEFAULT_MODEL_ID,
    VISIBLE_MODEL_IDS,
)

import mc_frame_bindings as bindings


class RemoteRoutesMixin:
    def _remote_session_usage(self, chat: dict, revision: int = 0) -> dict | None:
        chat_id = str(chat.get("id") or "").strip()
        if not chat_id:
            return None
        model = self._model_for_chat_selection(chat)
        provider = "codex" if is_codex_model(model) else "kimi" if is_kimi_model(model) else ""
        tokens = self._session_total_tokens(chat, model) if provider else None
        owner = self._information_cache_owner(chat, provider) if provider else ()
        key = (revision, provider, owner, tokens)
        records = getattr(self, "_remote_session_usage_records", None)
        if records is None:
            records = self._remote_session_usage_records = {}
        previous = records.get(chat_id)
        if previous and previous[0] == key:
            return previous[1]
        stamp = bindings.app.time.time_ns() // 1000
        if previous:
            stamp = max(stamp, previous[1]["stamp"] + 1)
        usage = {"stamp": stamp, "revision": revision, "supported": bool(provider), "tokens": tokens}
        records[chat_id] = (key, usage)
        return usage

    def _publish_remote_session_usage(self, chat: dict) -> None:
        chat_id = str(chat.get("id") or "").strip()
        current = self._current_chat_state
        if not chat_id or (chat is not current and chat is not self._find_archived_chat(chat_id)):
            return
        store = getattr(self, "chat_store", None)
        revision = 0
        if store is not None and hasattr(store, "get_clear_reconciliation_authority"):
            try:
                revision = store.get_clear_reconciliation_authority(chat_id)["revision"]
            except (ValueError, RuntimeError):
                pass
        previous = getattr(self, "_remote_session_usage_records", {}).get(chat_id)
        usage = self._remote_session_usage(chat, revision)
        if previous and previous[1] is usage:
            return
        self._invalidate_remote_state_cache()
        if usage["supported"] or previous:
            self._publish_remote_nats_event({
                "type": "chat_session_usage", "chat_id": chat_id, "session_usage": usage,
            })

    def _publish_remote_nats_event(self, payload: dict) -> None:
        transport = getattr(self, "_remote_nats_transport", None)
        if transport is None:
            return
        try:
            publish = getattr(transport, "publish_event_threadsafe", None)
            if callable(publish):
                publish(payload)
        except Exception:
            pass

    def _broadcast_remote_event(self, payload: dict, *, retry_on_failure: bool = True,
                                publication_deadline: float | None = None) -> bool | None:
        transport = getattr(self, "_remote_nats_transport", None)
        store = getattr(self, "chat_store", None)
        source_revision = payload.get("_source_revision")
        if source_revision is not None:
            payload = {key: value for key, value in payload.items() if key != "_source_revision"}
        if (
            transport is not None
            and int(getattr(transport, "protocol_version", 1) or 1) >= 2
            and store is not None
            and getattr(store, "v2_writes_enabled", False)
        ):
            chat_id = str(payload.get("chat_id") or "").strip()
            if not chat_id:
                try:
                    store.quarantine("MISSING_OWNER", payload, str(payload.get("event_id") or ""))
                except Exception:
                    pass
                return False if not retry_on_failure else None
            event_id = str(payload.get("event_id") or "").strip() or f"event-{uuid.uuid4().hex}"
            kind = str(payload.get("type") or payload.get("kind") or "event").strip() or "event"
            body = {key: copy.deepcopy(value) for key, value in payload.items()
                    if key not in {"type", "kind", "event_id", "chat_id", "origin_client"}}
            if kind.startswith("file_") and isinstance(payload.get("body"), dict):
                body = copy.deepcopy(payload["body"])
                for key in ("request_id", "device_id", "ts"):
                    if key in payload:
                        body[key] = copy.deepcopy(payload[key])
            if kind == "execution_entry":
                body["kind"] = str(payload.get("kind") or "info").strip() or "info"
            committed = False
            try:
                revision_guard = {}
                if source_revision is not None:
                    current = store.get_clear_reconciliation_authority(chat_id)["revision"]
                    if current > source_revision:
                        return False
                    revision_guard["revision"] = current
                store.commit_durable_fact(
                    pair_id=transport.subjects.pair_id,
                    domain="files" if kind.startswith("file_") else "events",
                    envelope={
                        "event_id": event_id,
                        "kind": kind,
                        "chat_id": chat_id,
                        "origin_client": str(payload.get("origin_client") or "mc").strip(),
                        "body": body,
                        **revision_guard,
                    },
                    execution=kind == "execution_entry",
                    **({"publication_deadline": publication_deadline} if publication_deadline is not None else {}),
                )
                committed = True
                loop = getattr(transport, "_loop", None)
                if loop is not None and loop.is_running():
                    asyncio.run_coroutine_threadsafe(transport.drain_outbox(), loop)
                return True
            except ValueError as exc:
                if not retry_on_failure:
                    if committed:
                        return True
                    raise
                if str(exc) in {"EVENT_ID_CONFLICT", "STALE_REVISION"}:
                    return
            except Exception:
                if not retry_on_failure:
                    if committed:
                        return True
                    raise
                pending = getattr(self, "_pending_v2_remote_facts", None)
                if not isinstance(pending, list):
                    pending = []
                    self._pending_v2_remote_facts = pending
                if len(pending) < 1000:
                    retry_payload = copy.deepcopy(payload)
                    pending.append(retry_payload)
                    def retry() -> None:
                        try:
                            pending.remove(retry_payload)
                        except ValueError:
                            return
                        self._broadcast_remote_event(retry_payload)
                    timer = bindings.app.threading.Timer(0.25, retry)
                    timer.daemon = True
                    timer.start()
                return
        self._publish_remote_nats_event(payload)

    def _push_remote_attention(self, chat_id: str, kind: str, operation_id: str, text: str) -> None:
        transport = getattr(self, "_remote_nats_transport", None)
        store = getattr(self, "chat_store", None)
        if transport is None or store is None or int(getattr(transport, "protocol_version", 1)) < 2:
            return
        try:
            store.commit_attention_notification(pair_id=transport.subjects.pair_id, chat_id=chat_id,
                kind=kind, operation_id=operation_id, text=text)
            loop = getattr(transport, "_loop", None)
            if loop is not None and loop.is_running():
                asyncio.run_coroutine_threadsafe(transport.drain_outbox(), loop)
        except (ValueError, RuntimeError):
            return

    def _push_remote_status(self, status: str, request_kind: str = "") -> None:
        self._invalidate_remote_state_cache()
        payload = {
            "type": "status",
            "chat_id": self.active_chat_id or self.current_chat_id or "",
            "status": status,
            "request_kind": request_kind,
            "settings": {"codex_answer_english_filter_enabled": self.codex_answer_english_filter_enabled},
            "last_event_id": self.active_codex_turn_id or self.active_openclaw_last_event_id or "",
            "ts": bindings.app.time.time(),
        }
        self._broadcast_remote_event(payload)

    def _push_remote_state(self, chat_id: str | None = None) -> None:
        resolved_chat_id = chat_id or self.active_chat_id or self.current_chat_id or ""
        status, body = self._remote_api_state_ui({"chat_id": resolved_chat_id})
        if status >= 400:
            return
        self._broadcast_remote_event(
            {
                "type": "state",
                "chat_id": resolved_chat_id,
                "body": body,
                "ts": bindings.app.time.time(),
            }
        )

    def _push_remote_final_answer(self, chat_id: str, text: str, *, turn_index: int | None = None) -> bool:
        resolved_chat_id = str(chat_id or "").strip()
        final_text = str(text or "").strip()
        # A Codex/Kimi delta can observe the persisted request placeholder before
        # the provider's terminal answer is written.  Publishing that value as
        # assistant_final consumes the canonical message id and prevents the
        # later real final from reaching mobile clients.
        if not resolved_chat_id or not final_text or final_text == REQUESTING_TEXT:
            return False
        transport = getattr(self, "_remote_nats_transport", None)
        store = getattr(self, "chat_store", None)
        # V2 notifications are an immutable projection of durable turn state,
        # never of an early provider callback.  This also leaves the canonical
        # message id available until the actual final is persisted.
        if store is not None and turn_index is not None:
            turns = store.load_turns(resolved_chat_id)
            if turn_index < 0 or turn_index >= len(turns):
                return False
            persisted = turns[turn_index]
            if str(persisted.get("request_status") or "").strip() != "done":
                return False
            final_text = str(persisted.get("answer_md") or "").strip()
            if not final_text or final_text == REQUESTING_TEXT:
                return False
        if (
            resolved_chat_id
            and transport is not None
            and int(getattr(transport, "protocol_version", 1) or 1) >= 2
            and store is not None
            and getattr(store, "v2_writes_enabled", False)
        ):
            try:
                message_id = (store.resolve_canonical_message_by_turn(
                    resolved_chat_id, role="assistant", turn_index=turn_index
                ) if turn_index is not None else store.resolve_canonical_message_by_content(
                    resolved_chat_id, role="assistant", content=final_text))
                chat_summary = (
                    self._current_chat_state
                    if str((self._current_chat_state or {}).get("id") or "").strip()
                    == resolved_chat_id
                    else self._chat_summary_by_id(resolved_chat_id)
                )
                store.commit_message_notification_fact(
                    pair_id=transport.subjects.pair_id,
                    domain="events",
                    chat_id=resolved_chat_id,
                    message_id=message_id,
                    notification_kind="assistant_final",
                    text=final_text,
                    chat_title=str((chat_summary or {}).get("title") or "").strip(),
                    origin_client="mc",
                )
                store.commit_message_execution_projection(
                    pair_id=transport.subjects.pair_id,
                    domain="events",
                    chat_id=resolved_chat_id,
                    message_id=message_id,
                    projection_kind="final",
                )
                loop = getattr(transport, "_loop", None)
                if loop is not None and loop.is_running():
                    asyncio.run_coroutine_threadsafe(transport.drain_outbox(), loop)
                return True
            except ValueError as exc:
                if str(exc) != "EVENT_ID_CONFLICT":
                    try:
                        store.quarantine(
                            str(exc), {"chat_id": resolved_chat_id, "role": "assistant"}
                        )
                    except Exception:
                        pass
                return False
        self._publish_remote_nats_event(
            {
                "type": "final_answer",
                "chat_id": resolved_chat_id,
                "text": final_text,
                "event_id": f"evt-{uuid.uuid4().hex[:8]}",
                "ts": bindings.app.time.time(),
            }
        )
        return True

    def _push_remote_history_changed(self, chat_id: str | None = None) -> None:
        self._invalidate_remote_history_list_cache()
        self._invalidate_remote_state_cache()
        payload = {
            "type": "history_changed",
            "chat_id": str(chat_id or ""),
            "event_id": f"evt-{uuid.uuid4().hex[:8]}",
            "ts": bindings.app.time.time(),
        }
        self._broadcast_remote_event(payload)

    def _push_remote_notes_changed(self, cursor: str | None = None) -> None:
        self._invalidate_remote_notes_changes_cache()
        payload = {
            "type": "notes_changed",
            "cursor": str(cursor or self.notes_store.current_cursor() or "0"),
            "event_id": f"evt-{uuid.uuid4().hex[:8]}",
            "ts": bindings.app.time.time(),
        }
        self._broadcast_remote_event(payload)

    def _push_remote_notes_conflict(self, payload: dict | None = None) -> None:
        conflict = dict(payload or {})
        conflict.update(
            {
                "type": "notes_conflict",
                "event_id": f"evt-{uuid.uuid4().hex[:8]}",
                "ts": bindings.app.time.time(),
            }
        )
        self._broadcast_remote_event(conflict)

    def _push_remote_notes_sync_status(self, status: str | dict, *, cursor: str | None = None, message: str | None = None) -> None:
        payload = dict(status) if isinstance(status, dict) else {"status": str(status or "")}
        if cursor is not None:
            payload["cursor"] = str(cursor or "")
        if message is not None:
            payload["message"] = str(message or "")
        payload.update({"type": "notes_sync_status", "event_id": f"evt-{uuid.uuid4().hex[:8]}", "ts": bindings.app.time.time()})
        self._broadcast_remote_event(payload)

    def _run_remote_ui_route(self, callback, payload: dict | None = None, *, deadline: float | None = None) -> tuple[int, dict]:
        if bindings.app.threading.current_thread() is bindings.app.threading.main_thread():
            return callback(payload) if payload is not None else callback()
        done = bindings.app.threading.Event()
        result: dict[str, object] = {}

        cancelled = bindings.app.threading.Event()

        def _invoke() -> None:
            if cancelled.is_set() or (deadline is not None and bindings.app.time.monotonic() >= deadline):
                result["value"] = (503, {"accepted": False, "error": "file_preparation_timeout"})
                done.set()
                return
            try:
                result["value"] = callback(payload) if payload is not None else callback()
            except Exception as exc:
                result["error"] = exc
            finally:
                done.set()

        if not self._call_after_if_alive(_invoke):
            return 503, {"accepted": False, "error": "ui_unavailable"}
        if not done.wait(5.0 if deadline is None else max(0.0, min(5.0, deadline - bindings.app.time.monotonic()))):
            cancelled.set()
            return 503, {"accepted": False, "error": "ui_timeout"}
        if "error" in result:
            raise result["error"]  # type: ignore[misc]
        value = result.get("value")
        if isinstance(value, tuple) and len(value) == 2:
            return value  # type: ignore[return-value]
        return 500, {"accepted": False, "error": "invalid_route_result"}

    def _remote_turn_payload(self, turn: dict) -> dict:
        question = str(turn.get("question") or "")
        answer_md = str(turn.get("answer_md") or "")
        model = str(turn.get("model") or "")
        signature = (
            question,
            answer_md,
            model,
            turn.get("created_at"),
            turn.get("answer_at"),
            turn.get("question_order"), turn.get("answer_order"),
            turn.get("answer_identity"), turn.get("answer_alias_of"),
            str(turn.get("request_status") or ""),
            str(turn.get("request_error") or ""),
            str(turn.get("client_send_id") or ""),
        )
        cache = getattr(self, "_remote_turn_payload_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._remote_turn_payload_cache = cache
        cache_key = id(turn)
        cached = cache.get(cache_key)
        if isinstance(cached, tuple) and len(cached) == 2 and cached[0] == signature:
            return dict(cached[1])
        answer = "" if model == AGY_MODEL_ID and answer_md == REQUESTING_TEXT else answer_md
        payload = {
            "question": question,
            "client_send_id": str(turn.get("client_send_id") or ""),
            "answer": answer,
            "model": model,
            "assistant_only": (not question.strip()) and bool(answer_md.strip()),
            "pending": str(turn.get("request_status") or "").strip() == "pending" or answer_md == REQUESTING_TEXT,
            "request_status": str(turn.get("request_status") or ""),
            "request_error": str(turn.get("request_error") or ""),
        }
        created_at = _finite_timestamp(turn.get("created_at"))
        if created_at is not None:
            payload["created_at"] = created_at
        answer_at = _finite_timestamp(turn.get("answer_at"))
        if answer_at is not None:
            payload["answer_at"] = answer_at
        for field in ("question_order", "answer_order", "answer_identity", "answer_alias_of"):
            if field in turn:
                payload[field] = turn[field]
        cache[cache_key] = (signature, dict(payload))
        return payload

    def _remote_readable_turn_payload(self, owner, index, turn):
        payload = self._remote_turn_payload(turn)
        store = getattr(self, "chat_store", None)
        if store is not None and hasattr(store, "readable_answer"):
            target = store.readable_answer(owner, turn_index=index)
            if target:
                payload.update(target)
        return payload

    def _remote_chat_summary(self, chat: dict) -> dict:
        if not isinstance(chat, dict):
            return {
                "chat_id": "",
                "title": "新聊天",
                "model": DEFAULT_MODEL_ID,
                "created_at": 0.0,
                "updated_at": 0.0,
                "turn_count": 0,
                "running": False,
                "request_kind": "",
                "current": False,
                "active": False,
                "pinned": False,
                "detail_panel_mode": "answers",
                **self._codex_speed_payload_for_chat(None),
            }
        turns = chat.get("turns") if isinstance(chat.get("turns"), list) else []
        turn_count = len(turns) if turns else int(chat.get("turn_count") or 0)
        chat_id = str(chat.get("id") or "")
        title = str(chat.get("title") or "新聊天")
        model = str(chat.get("model") or DEFAULT_MODEL_ID)
        created_at = float(chat.get("created_at") or 0.0)
        updated_at = float(chat.get("updated_at") or created_at or bindings.app.time.time())
        pending_request = chat.get("codex_pending_request")
        request_kind = "user_input" if isinstance(pending_request, dict) and pending_request else ""
        current_id = str(self.active_chat_id or self.current_chat_id or "").strip()
        snapshot = {
            "chat_id": chat_id,
            "title": title,
            "model": model,
            **({"agy_workspace": chat.get("agy_workspace", ""),
                "agy_history_status": chat.get("native_history_status", ""),
                "agy_native_title": chat.get("native_title", "")} if model == AGY_MODEL_ID else {}),
            "created_at": created_at,
            "updated_at": updated_at,
            "title_source": str(chat.get("title_source") or ("manual" if chat.get("title_manual") else "default")),
            "title_updated_at": float(chat.get("title_updated_at") or updated_at),
            "title_revision": int(chat.get("title_revision") or 1),
            "turn_count": turn_count,
            "running": False,
            "request_kind": request_kind,
            "current": bool(chat_id) and chat_id == current_id,
            "active": bool(chat_id) and chat_id == current_id,
            "pinned": bool(chat.get("pinned")),
            "detail_panel_mode": str(chat.get("detail_panel_mode") or "answers").strip() or "answers",
            **self._codex_speed_payload_for_chat(chat),
        }
        store = getattr(self, "chat_store", None)
        if chat_id and getattr(self, "_chat_store_enabled", False) and store is not None and hasattr(store, "get_clear_reconciliation_authority"):
            try:
                snapshot.update(store.get_clear_reconciliation_authority(chat_id))
                read_state = self._chat_read_state(chat_id)
                if read_state:
                    snapshot["read_state"] = read_state
            except (ValueError, RuntimeError):
                pass
        return snapshot

    def _remote_execution_step_payload(self, chat: dict, *, include_execution_steps: bool = False) -> tuple[list, int]:
        steps = chat.get("execution_steps") if isinstance(chat.get("execution_steps"), list) else []
        if not include_execution_steps:
            return [], len(steps)
        return copy.deepcopy(steps), len(steps)

    def _remote_chat_snapshot(self, chat: dict, *, include_execution_steps: bool = False) -> dict:
        if not isinstance(chat, dict):
            return {
                "chat_id": "",
                "title": "新聊天",
                "model": DEFAULT_MODEL_ID,
                "created_at": 0.0,
                "updated_at": 0.0,
                "turn_count": 0,
                "running": False,
                "request_kind": "",
                "current": False,
                "active": False,
                "pinned": False,
                "detail_panel_mode": "answers",
                "execution_steps": [],
                "execution_step_count": 0,
                "turns": [],
                **self._codex_speed_payload_for_chat(None),
            }
        turns = chat.get("turns") if isinstance(chat.get("turns"), list) else []
        execution_steps, execution_step_count = self._remote_execution_step_payload(
            chat,
            include_execution_steps=include_execution_steps,
        )
        chat_id = str(chat.get("id") or "")
        title = str(chat.get("title") or "新聊天")
        model = str(chat.get("model") or DEFAULT_MODEL_ID)
        created_at = float(chat.get("created_at") or 0.0)
        updated_at = float(chat.get("updated_at") or created_at or bindings.app.time.time())
        pending_request = chat.get("codex_pending_request")
        request_kind = "user_input" if isinstance(pending_request, dict) and pending_request else ""
        current_id = str(self.active_chat_id or self.current_chat_id or "").strip()
        snapshot = {
            "chat_id": chat_id,
            "title": title,
            "model": model,
            "created_at": created_at,
            "updated_at": updated_at,
            "title_source": str(chat.get("title_source") or ("manual" if chat.get("title_manual") else "default")),
            "title_updated_at": float(chat.get("title_updated_at") or updated_at),
            "title_revision": int(chat.get("title_revision") or 1),
            "turn_count": len(turns),
            **({"agy_workspace": chat.get("agy_workspace", ""),
                "agy_history_status": chat.get("native_history_status", ""),
                "agy_native_title": chat.get("native_title", "")} if model == AGY_MODEL_ID else {}),
            "running": False,
            "request_kind": request_kind,
            "current": bool(chat_id) and chat_id == current_id,
            "active": bool(chat_id) and chat_id == current_id,
            "pinned": bool(chat.get("pinned")),
            "detail_panel_mode": str(chat.get("detail_panel_mode") or "answers").strip() or "answers",
            "execution_steps": execution_steps,
            "execution_step_count": execution_step_count,
            "turns": [self._remote_readable_turn_payload(chat_id, index + int(chat.get("_turn_start_index") or 0), turn)
                      for index, turn in enumerate(turns) if isinstance(turn, dict)],
            **self._codex_speed_payload_for_chat(chat),
        }
        store = getattr(self, "chat_store", None)
        if chat_id and getattr(self, "_chat_store_enabled", False) and store is not None and hasattr(store, "get_clear_reconciliation_authority"):
            try:
                snapshot.update(store.get_clear_reconciliation_authority(chat_id))
                read_state = self._chat_read_state(chat_id)
                if read_state:
                    snapshot["read_state"] = read_state
            except (ValueError, RuntimeError):
                pass
        return snapshot

    def _remote_chat_snapshot_page(self, chat: dict, payload: dict | None = None) -> tuple[dict, bool, str]:
        payload = payload or {}
        include_execution_steps = bool(payload.get("include_execution_steps"))
        raw_limit = payload.get("limit")
        try:
            limit = int(raw_limit)
        except Exception:
            limit = 0
        if limit <= 0:
            limit = REMOTE_STATE_DEFAULT_TURN_LIMIT
        limit = min(10, limit)
        raw_turns = chat.get("turns") if isinstance(chat, dict) and isinstance(chat.get("turns"), list) else []
        turns = [turn for turn in raw_turns if isinstance(turn, dict)]
        total = len(turns)
        before_raw = payload.get("before_turn_index")
        try:
            end = int(before_raw)
        except Exception:
            end = total
        end = max(0, min(end, total))
        start = max(0, end - limit)
        paged_chat = dict(chat) if isinstance(chat, dict) else {}
        paged_chat["turns"] = turns[start:end]
        paged_chat["_turn_start_index"] = start
        snapshot = self._remote_chat_snapshot(paged_chat, include_execution_steps=include_execution_steps)
        usage = self._remote_session_usage(chat, int(snapshot.get("revision") or 0))
        if usage is not None:
            snapshot["session_usage"] = usage
        snapshot["turn_count"] = total
        self._bound_remote_chat_content(snapshot)
        while len(snapshot.get("turns", [])) > 1 and len(json.dumps(snapshot, ensure_ascii=True).encode("utf-8")) > 96 * 1024:
            snapshot["turns"].pop(0)
            start += 1
        return snapshot, start > 0, (str(start) if start > 0 else "")

    def _bound_remote_chat_content(self, snapshot: dict) -> None:
        store = getattr(self, "chat_store", None)
        if store is None or not hasattr(store, "content_continuation"):
            return
        for turn in snapshot.get("turns", []):
            for field in ("question", "answer"):
                text = str(turn.get(field) or "")
                prefix, cursor = store.content_continuation(chat_id=snapshot["chat_id"], text=text,
                    secret=str(self.remote_control_token or "content"), prefix_bytes=512)
                if cursor:
                    turn[field], turn[field + "_continuation"] = prefix, cursor
                    if field == "answer":
                        turn["complete_answer_seq"] = turn.get("answer_seq", 0)

    def _remote_chat_snapshot_page_from_store(self, chat: dict, payload: dict | None = None) -> tuple[dict, bool, str]:
        payload = dict(payload or {})
        chat_id = str((chat or {}).get("id") or payload.get("chat_id") or "").strip()
        store = getattr(self, "chat_store", None)
        if not chat_id or store is None or not hasattr(store, "load_turns_page"):
            return self._remote_chat_snapshot_page(chat, payload)
        raw_limit = payload.get("limit")
        try:
            limit = int(raw_limit)
        except Exception:
            limit = 0
        if limit <= 0:
            limit = REMOTE_STATE_DEFAULT_TURN_LIMIT
        limit = min(10, limit)
        before_raw = payload.get("before_turn_index")
        try:
            before_turn_index = int(before_raw) if before_raw is not None else None
        except Exception:
            before_turn_index = None
        total, turns = store.load_turns_page(chat_id, limit=limit, before_turn_index=before_turn_index)
        end = before_turn_index if before_turn_index is not None else total
        end = max(0, min(int(end or 0), int(total or 0)))
        start = max(0, end - max(1, int(limit or 1)))
        paged_chat = dict(chat or {})
        paged_chat["id"] = chat_id
        paged_chat["turns"] = turns
        paged_chat["_turn_start_index"] = start
        if bool(payload.get("include_execution_steps")) and "execution_steps" not in paged_chat:
            try:
                paged_chat["execution_steps"] = store.load_execution_steps(chat_id)
            except Exception:
                paged_chat["execution_steps"] = []
        snapshot = self._remote_chat_snapshot(
            paged_chat,
            include_execution_steps=bool(payload.get("include_execution_steps")),
        )
        usage = self._remote_session_usage(chat, int(snapshot.get("revision") or 0))
        if usage is not None:
            snapshot["session_usage"] = usage
        snapshot["turn_count"] = int(total or 0)
        self._bound_remote_chat_content(snapshot)
        while len(snapshot.get("turns", [])) > 1 and len(json.dumps(snapshot, ensure_ascii=True).encode("utf-8")) > 96 * 1024:
            snapshot["turns"].pop(0)
            start += 1
        return snapshot, start > 0, (str(start) if start > 0 else "")

    def _current_chat_snapshot(self, *, include_execution_steps: bool = False) -> dict:
        chat = dict(self._current_chat_state or {})
        if not chat:
            chat = {"id": self.active_chat_id or self.current_chat_id or "", "title": "新聊天", "turns": self.active_session_turns}
        chat["id"] = str(chat.get("id") or self.active_chat_id or self.current_chat_id or "")
        chat["title"] = str(chat.get("title") or "新聊天")
        chat["model"] = str(chat.get("model") or self._resolve_current_model() or DEFAULT_MODEL_ID)
        chat["created_at"] = float(chat.get("created_at") or self.active_session_started_at or bindings.app.time.time())
        chat["updated_at"] = float(chat.get("updated_at") or bindings.app.time.time())
        chat["turns"] = [self._remote_turn_payload(turn) for turn in self.active_session_turns if isinstance(turn, dict)]
        chat["chat_id"] = chat["id"]
        chat["turn_count"] = len(chat["turns"])
        chat["running"] = bool(self.is_running)
        chat["request_kind"] = "user_input" if self.active_codex_pending_request else ""
        chat["current"] = True
        chat["active"] = True
        chat["pinned"] = bool(chat.get("pinned"))
        chat["detail_panel_mode"] = str(chat.get("detail_panel_mode") or "answers").strip() or "answers"
        execution_steps, execution_step_count = self._remote_execution_step_payload(
            chat,
            include_execution_steps=include_execution_steps,
        )
        chat["execution_steps"] = execution_steps
        chat["execution_step_count"] = execution_step_count
        chat["title_source"] = str(chat.get("title_source") or ("manual" if chat.get("title_manual") else "default"))
        chat["title_updated_at"] = float(chat.get("title_updated_at") or chat["updated_at"])
        chat["title_revision"] = int(chat.get("title_revision") or 1)
        chat.update(self._codex_speed_payload_for_chat(chat))
        return chat

    def _remote_chat_attachment_message_ui(self, payload: dict) -> tuple[int, dict]:
        chat_id = str(payload.get("chat_id") or "").strip()
        send_id = str(payload.get("client_send_id") or "").strip()
        scope = self._remote_attachment_scope(payload, send_id)
        text = str(payload.get("text") or "").strip()
        refs = payload["attachment_file_ids"]
        if not scope or not text:
            return 400, {"accepted": False, "error": "invalid_attachment_submission"}
        if (not isinstance(refs, list) or not refs
                or any(not isinstance(ref, str) or not ref for ref in refs)
                or len(set(refs)) != len(refs)):
            return 400, {"accepted": False, "error": "invalid_attachment_references"}
        store = getattr(self, "chat_store", None)
        receipt = store.remote_attachment_send(scope) if store is not None else None
        if receipt is None:
            return 409, {"accepted": False, "error": "attachment_reference_unavailable"}
        fingerprint = hashlib.sha256(json.dumps(
            [text, refs, payload.get("model"), payload.get("model_change_mode")],
            ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        if receipt["fingerprint"] and receipt["fingerprint"] != fingerprint:
            return 409, {"accepted": False, "error": "attachment_send_conflict"}
        confirmation = {"accepted": True, "chat_id": chat_id, "client_send_id": send_id}
        if receipt["state"] == "accepted":
            return 200, confirmation
        if self._clear_pending_for(chat_id):
            return 409, {"accepted": False, "error": "clear_context_pending"}
        if receipt["revision"] != scope[-1]:
            return 409, {"accepted": False, "error": "attachment_send_expired"}
        if receipt["state"] == "starting":
            return 409, {"accepted": False, "error": "attachment_send_unconfirmed"}
        active = chat_id in {self.active_chat_id, self.current_chat_id}
        target = self._current_chat_state if active else self._find_archived_chat(chat_id)
        attachments = self._resolve_remote_chat_attachments(refs, scope)
        if attachments is None:
            return 409, {"accepted": False, "error": "attachment_reference_unavailable"}
        if active and self.is_running:
            return 409, {"accepted": False, "error": "attachment_chat_busy"}
        model, error = self._resolve_remote_message_model(
            payload, target, current_model=self._model_for_chat_selection(target), is_new_chat=False,
        )
        if error:
            return 400, {"accepted": False, "error": error}
        prepared_attachments = None
        if is_openrouter_claude_model(model):
            try:
                prepared_attachments = read_openrouter_claude_attachments(model, attachments)
            except ValueError as exc:
                return 400, {"accepted": False, "error": str(exc)}
        elif not (is_codex_model(model) or is_kimi_model(model)):
            return 400, {"accepted": False, "error": "attachment_model_unsupported"}
        if not store.claim_remote_attachment_send(scope, fingerprint):
            return 409, {"accepted": False, "error": "attachment_send_unconfirmed"}
        accepted, message = self._submit_archived_remote_question(
            target, text, model, client_send_id=send_id, attachments=attachments,
            attachment_scope=scope, active=active, prepared_attachments=prepared_attachments,
        )
        if not accepted:
            store.release_remote_attachment_send(scope)
            return 400, {"accepted": False, "error": message}
        self._push_remote_state(chat_id)
        self._push_remote_history_changed(chat_id)
        return 200, confirmation

    def _remote_api_message_ui(self, payload: dict) -> tuple[int, dict]:
        if "attachment_file_ids" in payload:
            return self._remote_chat_attachment_message_ui(payload)
        text = str(payload.get("text") or "").strip()
        if not text:
            return 400, {"accepted": False, "error": "empty_text"}
        requested_chat_id = str(payload.get("chat_id") or "").strip()
        previous_owner_id = str(self.active_chat_id or self.current_chat_id or "").strip()
        if self._clear_pending_for(requested_chat_id or previous_owner_id):
            return 409, {"accepted": False, "error": "clear_context_pending"}
        previous_selected_model = str(self.selected_model or STARTUP_DEFAULT_MODEL_ID)
        previous_combo_value = self.model_combo.GetValue() if bindings.app.threading.current_thread() is bindings.app.threading.main_thread() else ""
        previous_input_value = self.input_edit.GetValue() if bindings.app.threading.current_thread() is bindings.app.threading.main_thread() else ""
        previous_focus = wx.Window.FindFocus() if bindings.app.threading.current_thread() is bindings.app.threading.main_thread() else None
        current_ids = {str(self.active_chat_id or "").strip(), str(self.current_chat_id or "").strip()}
        current_ids.discard("")
        archived_target = None
        is_new_chat = False
        if requested_chat_id and requested_chat_id not in current_ids:
            archived_target = self._find_archived_chat(requested_chat_id)
            is_new_chat = archived_target is None
        preview_chat = self._current_chat_state if not requested_chat_id or requested_chat_id in current_ids else archived_target
        if bindings.app.threading.current_thread() is bindings.app.threading.main_thread():
            current_model = self._resolve_current_model()
        else:
            current_model = str(self.selected_model or DEFAULT_MODEL_ID)
        model, model_error = self._resolve_remote_message_model(
            payload,
            preview_chat,
            current_model=current_model,
            is_new_chat=is_new_chat,
        )
        if model_error:
            error = "missing_explicit_model" if model_error == "missing_explicit_model" else "invalid_explicit_model"
            return 400, {"accepted": False, "error": error, "chat_id": requested_chat_id or str(self.active_chat_id or self.current_chat_id or "").strip()}
        if model == AGY_MODEL_ID and (not isinstance(preview_chat, dict) or not preview_chat.get("agy_workspace")):
            return 400, {"accepted": False, "message": "请先为 AGY 新建聊天并选择项目目录"}
        if isinstance(preview_chat, dict) and preview_chat.get("agy_workspace") and model != AGY_MODEL_ID:
            return 400, {"accepted": False, "message": "请为其他提供方新建聊天"}
        if requested_chat_id:
            if requested_chat_id != self.active_chat_id:
                if archived_target is not None:
                    pass
                else:
                    self._start_remote_new_chat({"chat_id": requested_chat_id, "model": model})
            chat_id = requested_chat_id
        else:
            chat_id = self.active_chat_id or self.current_chat_id or self._ensure_active_chat_id()
            if not self.current_chat_id and self.active_chat_id:
                self.current_chat_id = self.active_chat_id
            if not self._current_chat_state.get("id"):
                self._current_chat_state["id"] = chat_id
        target_chat = self._current_chat_state if chat_id in {self.active_chat_id, self.current_chat_id} else self._find_archived_chat(chat_id)
        if archived_target is None and bindings.app.threading.current_thread() is bindings.app.threading.main_thread():
            self.input_edit.SetValue(text)
        title_revision_before = 0
        if isinstance(target_chat, dict):
            try:
                title_revision_before = int(target_chat.get("title_revision") or 0)
            except Exception:
                title_revision_before = 0
        if archived_target is not None:
            ok, message = self._submit_archived_remote_question(archived_target, text, model, client_send_id=str(payload.get("client_send_id") or ""))
        else:
            ok, message = self._submit_question(text, source="remote-ws", model=model, chat_id=chat_id, client_send_id=str(payload.get("client_send_id") or ""))
        restore_previous_owner = False
        if restore_previous_owner and previous_owner_id in {
            str((chat or {}).get("id") or "").strip() for chat in self.archived_chats if isinstance(chat, dict)
        }:
            self._switch_current_chat(previous_owner_id)
        if not ok:
            if is_new_chat:
                if previous_owner_id in {
                    str((chat or {}).get("id") or "").strip() for chat in self.archived_chats if isinstance(chat, dict)
                }:
                    self._switch_current_chat(previous_owner_id)
                self.archived_chats = [
                    chat for chat in self.archived_chats
                    if str((chat or {}).get("id") or "").strip() != requested_chat_id
                ]
                store = getattr(self, "chat_store", None)
                if requested_chat_id and store is not None:
                    store.delete_chat(requested_chat_id)
            self.selected_model = previous_selected_model
            if bindings.app.threading.current_thread() is bindings.app.threading.main_thread():
                self.model_combo.SetValue(previous_combo_value or bindings.app.model_display_name(previous_selected_model))
                self.input_edit.SetValue(previous_input_value)
                if previous_focus is not None:
                    previous_focus.SetFocus()
        if ok:
            target_chat = self._current_chat_state if chat_id in {self.active_chat_id, self.current_chat_id} else self._find_archived_chat(chat_id)
            if isinstance(target_chat, dict):
                target_chat["model"] = model
            if chat_id in {self.active_chat_id, self.current_chat_id}:
                self.selected_model = model
                if bindings.app.threading.current_thread() is bindings.app.threading.main_thread():
                    self.model_combo.SetValue(bindings.app.model_display_name(model))
            self._push_remote_state(chat_id)
            title_revision_after = title_revision_before
            if isinstance(target_chat, dict):
                try:
                    title_revision_after = int(target_chat.get("title_revision") or 0)
                except Exception:
                    title_revision_after = title_revision_before
            if title_revision_after == title_revision_before:
                self._push_remote_history_changed(chat_id)
        if restore_previous_owner and previous_focus is not None and bindings.app.threading.current_thread() is bindings.app.threading.main_thread():
            previous_focus.SetFocus()
        response = {"accepted": ok, "message": message, "chat_id": chat_id, "model": model}
        if ok and isinstance(target_chat, dict):
            correlation = str(payload.get("client_send_id") or "")
            if correlation and any(
                isinstance(turn, dict) and turn.get("client_send_id") == correlation
                for turn in target_chat.get("turns", [])
            ):
                response["client_send_id"] = correlation
        return 200 if ok else 400, response

    def _resolve_remote_message_model(
        self,
        payload: dict,
        target_chat: dict | None,
        *,
        current_model: str,
        is_new_chat: bool,
    ) -> tuple[str, str]:
        change_mode = str((payload or {}).get("model_change_mode") or "inherit").strip().lower()
        if change_mode not in {"inherit", "explicit"}:
            change_mode = "inherit"
        requested_raw = str((payload or {}).get("model") or "").strip()
        requested_model = bindings.app.normalize_model_id(requested_raw, default="") if requested_raw else ""
        chat_model = self._model_for_chat_selection(target_chat)
        if change_mode == "explicit":
            if not requested_raw:
                return "", "missing_explicit_model"
            if requested_model:
                return requested_model, ""
            if requested_raw:
                return "", "invalid_explicit_model"
        if is_new_chat:
            if requested_model:
                return requested_model, ""
            return bindings.app.normalize_model_id(current_model or STARTUP_DEFAULT_MODEL_ID), ""
        if isinstance(target_chat, dict):
            return chat_model or STARTUP_DEFAULT_MODEL_ID, ""
        return bindings.app.normalize_model_id(current_model or STARTUP_DEFAULT_MODEL_ID), ""

    def _remote_api_new_chat(self, payload: dict) -> tuple[int, dict]:
        model = bindings.app.normalize_model_id(payload.get("model"), default="")
        if model != AGY_MODEL_ID:
            return self._run_remote_ui_route(self._remote_api_new_chat_ui, payload)
        with self._agy_creation_lock:
            self._agy_remote_creations += 1
        try:
            return self._remote_create_agy_chat(payload)
        finally:
            with self._agy_creation_lock:
                self._agy_remote_creations -= 1

    def _remote_create_agy_chat(self, payload: dict) -> tuple[int, dict]:
        try:
            raw_cwd = payload.get("agy_workspace")
            client, fields = self._prepare_agy_workspace(raw_cwd if isinstance(raw_cwd, str) else "")
        except AgyError as exc:
            return 400, {"accepted": False, "message": str(exc)}
        committed = False
        try:
            status, body = self._run_remote_ui_route(
                lambda data: self._remote_api_new_chat_ui(data, agy_workspace=(client, fields)), payload
            )
            committed = status == 200
            return status, body
        finally:
            if not committed:
                client.close()

    def _remote_api_new_chat_ui(self, payload: dict, *, agy_workspace=None) -> tuple[int, dict]:
        model = bindings.app.normalize_model_id(payload.get("model"), default="")
        if getattr(self, "_closing", False):
            return 503, {"accepted": False, "message": "电脑端正在关闭"}
        if model == AGY_MODEL_ID and agy_workspace is None:
            return 400, {"accepted": False, "message": "请选择运行电脑上的 AGY 项目目录"}
        if not model and self._resolve_current_model() == AGY_MODEL_ID:
            payload = {**payload, "model": STARTUP_DEFAULT_MODEL_ID}
        fields = agy_workspace[1] if agy_workspace else None
        chat = self._start_remote_new_chat(payload, agy_fields=fields)
        if agy_workspace:
            self._remember_agy_workspace(agy_workspace[0], chat["chat_id"], fields["agy_workspace"])
            chat.update(agy_workspace=fields["agy_workspace"], agy_options=fields["agy_options"])
        return 200, {"accepted": True, **chat}

    def _start_remote_new_chat(self, payload: dict | None = None, *, agy_fields=None) -> dict:
        navigation_deferred = (
            getattr(self, "_chat_navigation_answer_rows_deferred", False)
            or getattr(self, "_chat_navigation_history_selection_deferred", False)
        )
        previous_history_id = self.view_history_id if self.view_mode == "history" else None
        archive_completed = False
        try:
            previous_chat_id = str(self.active_chat_id or self.current_chat_id or "").strip()
            previous_codex_service_tier = self._current_codex_service_tier()
            archived = self._archive_active_session(quick_title=True, schedule_async_rename=True)
            archive_completed = True
            self._reset_active_codex_session_state()
            self._reset_active_kimi_session_state()
            self.view_mode = "active"
            self.view_history_id = None
            self._pending_context_usage_by_turn = {}
            self._codex_early_context_usage_owner = {}
            self._clear_active_claudecode_client()
            self.current_chat_id = ""
            self.active_chat_id = ""
            self.active_session_turns = []
            now = bindings.app.time.time()
            default_title = str((payload or {}).get("title") or "").strip() or self._next_default_chat_title()
            self.active_session_started_at = now
            self._current_chat_state = {
                "id": "",
                "title": default_title,
                "title_manual": False,
                "title_source": "default",
                "title_updated_at": now,
                "title_revision": 1,
                "turns": self.active_session_turns,
                "context_usage": None,
                "created_at": now,
                "updated_at": now,
                "detail_panel_mode": "answers",
                "execution_steps": [],
                "codex_service_tier": "",
            }
            requested_chat_id = str((payload or {}).get("chat_id") or "").strip()
            self.active_chat_id = requested_chat_id or str(uuid.uuid4())
            self.current_chat_id = self.active_chat_id
            self._current_chat_state["id"] = self.active_chat_id
            self._reset_answer_visible_row_limit()
            self._reset_current_turn_execution_view()
            model = bindings.app.normalize_model_id((payload or {}).get("model") or self._resolve_current_model() or DEFAULT_MODEL_ID)
            self.selected_model = model
            self._current_chat_state["model"] = model
            if agy_fields:
                self._current_chat_state.update(agy_fields)
            self._write_active_codex_session_state_to_chat(self._current_chat_state)
            self._write_active_kimi_session_state_to_chat(self._current_chat_state)
            if is_codex_model(model):
                self._current_chat_state["codex_service_tier"] = previous_codex_service_tier
            on_ui_thread = bindings.app.threading.current_thread() is bindings.app.threading.main_thread()
            if on_ui_thread:
                self.model_combo.SetValue(bindings.app.model_display_name(model))
                self._sync_codex_speed_combo_from_chat(self._current_chat_state)
            self._save_state()
        except Exception as error:
            if not archive_completed and previous_history_id and self._find_archived_chat(previous_history_id):
                self.view_mode = "history"
                self.view_history_id = previous_history_id
            self._chat_navigation_answer_rows_deferred = True
            self._chat_navigation_history_selection_deferred = True
            try:
                self._cancel_chat_navigation_focus()
            except Exception as recovery_error:
                error.add_note(f"Could not recover answer rows: {recovery_error!r}")
            try:
                self._flush_chat_navigation_history_selection()
            except Exception as recovery_error:
                self._chat_navigation_history_selection_deferred = True
                error.add_note(f"Could not recover history selection: {recovery_error!r}")
            raise
        if navigation_deferred:
            self._chat_navigation_answer_rows_deferred = False
            self._chat_navigation_history_selection_deferred = False
            self._cancel_chat_navigation_focus()
        if on_ui_thread:
            self.input_edit.SetFocus()
        self._refresh_history(archived["id"] if archived else previous_chat_id or None)
        self._render_answer_list()
        self.SetStatusText("已开始远程新聊天")
        self._push_remote_history_changed(self.active_chat_id)
        return {
            "chat_id": self.active_chat_id,
            "title": self._current_chat_state["title"],
            "model": model,
            "created_at": now,
            "title_source": self._current_chat_state["title_source"],
            "title_updated_at": self._current_chat_state["title_updated_at"],
            "title_revision": self._current_chat_state["title_revision"],
        }

    def _remote_api_reply_request_ui(self, payload: dict) -> tuple[int, dict]:
        text = str(payload.get("text") or "").strip()
        ok, message = self._handle_remote_pending_request_reply(text)
        return 200 if ok else 400, {"accepted": ok, "message": message}

    def _remote_api_model_list_ui(self) -> tuple[int, dict]:
        return 200, {
            "accepted": True,
            "models": [
                {"id": model_id, "label": bindings.app.model_display_name(model_id),
                 **({"agy_workspace": self._agy_workspace_descriptor()} if model_id == AGY_MODEL_ID else {})}
                for model_id in VISIBLE_MODEL_IDS
            ],
        }

    def _remote_api_history_list_ui(self, _payload: dict | None = None) -> tuple[int, dict]:
        self._request_native_history_refresh()
        if (_payload or {}).get("collection_page"):
            return self._remote_api_history_collection_ui(_payload)
        cached = getattr(self, "_remote_history_list_cache", None)
        if isinstance(cached, dict):
            return 200, copy.deepcopy(cached)
        chats = []
        if self.active_chat_id or self.active_session_turns:
            current = dict(self._current_chat_state or {})
            if not current:
                current = {"id": self.active_chat_id or self.current_chat_id or "", "title": "新聊天", "turns": self.active_session_turns}
            current["id"] = str(current.get("id") or self.active_chat_id or self.current_chat_id or "")
            current["title"] = str(current.get("title") or "新聊天")
            current["model"] = str(current.get("model") or self._resolve_current_model() or DEFAULT_MODEL_ID)
            current["created_at"] = float(current.get("created_at") or self.active_session_started_at or bindings.app.time.time())
            current["updated_at"] = float(current.get("updated_at") or bindings.app.time.time())
            current["turns"] = self.active_session_turns
            current_summary = self._remote_chat_summary(current)
            current_summary["running"] = bool(self.is_running)
            current_summary["request_kind"] = "user_input" if self.active_codex_pending_request else ""
            chats.append(current_summary)
        for chat in self.archived_chats:
            chats.append(self._remote_chat_summary(chat))
        body = {"accepted": True, "chats": chats}
        self._remote_history_list_cache = copy.deepcopy(body)
        return 200, body

    def _remote_api_history_collection_ui(self, payload: dict) -> tuple[int, dict]:
        """Summarize only the requested collection page; never hydrate bodies."""
        from chat_store import ChatStore
        revision = int(getattr(self, "_remote_collection_revision", 0))
        query = str(payload.get("query") or "").strip().casefold()
        section = str(payload.get("section") or "recent")
        if section not in {"recent", "pinned", "running"}:
            return 400, {"error": "INVALID_COLLECTION_SECTION"}
        if payload.get("if_revision") == revision and not payload.get("cursor"):
            return 200, {"accepted": True, "not_modified": True, "collection_revision": revision}
        secret = str(self.remote_control_token or "collection")
        offset = 0
        if payload.get("cursor"):
            try:
                cursor = ChatStore._decode_execution_cursor(str(payload["cursor"]), secret)
                if cursor.get("revision") != revision or cursor.get("query") != query or cursor.get("section") != section:
                    raise ValueError("STALE_COLLECTION_CURSOR")
                offset = int(cursor["offset"])
                if offset < 0:
                    raise ValueError("INVALID_COLLECTION_CURSOR")
            except (ValueError, KeyError, TypeError) as exc:
                return 409, {"error": str(exc), "recovery": "REFRESH_COLLECTION"}
        current = dict(getattr(self, "_current_chat_state", {}) or {})
        current_id = str(self.active_chat_id or self.current_chat_id or "")
        current.update({"id": current_id, "turns": self.active_session_turns})
        candidates = {str(chat.get("id") or ""): chat for chat in self.archived_chats if chat.get("id")}
        if current_id:
            candidates[current_id] = current
        rows = [chat for chat in candidates.values() if
                (not query or query in str(chat.get("title") or "").casefold()) and
                (section != "pinned" or chat.get("pinned")) and
                (section != "running" or (chat.get("id") == current_id and self.is_running))]
        rows.sort(key=lambda chat: (float(chat.get("updated_at") or chat.get("created_at") or 0),
                                    str(chat.get("id") or "")), reverse=True)
        limit = max(1, min(30, int(payload.get("limit") or 30)))
        summaries = []
        for chat in rows[offset:offset + limit]:
            summary = self._remote_chat_summary(chat)
            if str(chat.get("id")) == current_id:
                summary["running"] = bool(self.is_running)
            # Budget includes UTF-8 and conservative envelope/continuation room.
            if len(json.dumps(summaries + [summary], ensure_ascii=False).encode("utf-8")) > 120 * 1024:
                break
            summaries.append(summary)
        end = offset + len(summaries)
        more = end < len(rows)
        cursor = ChatStore._execution_cursor_token({"revision": revision, "query": query,
            "section": section, "offset": end}, secret) if more else ""
        return 200, {"accepted": True, "chats": summaries, "partial": True, "has_more": more,
                     "next_cursor": cursor, "collection_revision": revision, "section": section}

    def _remote_api_history_collection_worker(self, payload: dict) -> tuple[int, dict]:
        if not payload.get("collection_page") or not getattr(self, "_chat_store_enabled", False):
            return self._run_remote_ui_route(self._remote_api_history_list_ui, payload)
        from chat_store import ChatStore
        revision = int(getattr(self, "_remote_collection_revision", 0))
        query = str(payload.get("query") or "").strip()
        section = str(payload.get("section") or "recent")
        if section not in {"recent", "pinned", "running"}:
            return 400, {"error": "INVALID_COLLECTION_SECTION"}
        secret = str(self.remote_control_token or "collection")
        offset = 0
        if payload.get("cursor"):
            try:
                cursor = ChatStore._decode_execution_cursor(str(payload["cursor"]), secret)
                if cursor.get("revision") != revision or cursor.get("query") != query or cursor.get("section") != section:
                    raise ValueError("STALE_COLLECTION_CURSOR")
                offset = int(cursor["offset"])
                if offset < 0:
                    raise ValueError("INVALID_COLLECTION_CURSOR")
            except (ValueError, KeyError, TypeError) as exc:
                return 409, {"error": str(exc), "recovery": "REFRESH_COLLECTION"}
        elif payload.get("if_revision") == revision:
            return 200, {"accepted": True, "not_modified": True, "collection_revision": revision}
        # UI captures one live owner only. Indexed SQLite paging, hydration and
        # metadata authority stay on this worker rather than walking all archives.
        def capture(_payload=None):
            self._request_native_history_refresh()
            live = dict(getattr(self, "_current_chat_state", {}) or {})
            live.update({"id": str(self.active_chat_id or self.current_chat_id or ""),
                         "turn_count": len(self.active_session_turns), "running": bool(self.is_running)})
            live.pop("turns", None)
            live.pop("execution_steps", None)
            return 200, live
        status, current = self._run_remote_ui_route(capture)
        if status != 200:
            return status, current
        if section == "running":
            selected = [current] if current.get("id") and current.get("running") else []
            more, end = False, 0
        else:
            include_current = not offset and bool(current.get("id")) and (not query or query.casefold() in str(current.get("title") or "").casefold()) and (section != "pinned" or current.get("pinned"))
            size = min(30, max(1, int(payload.get("limit") or 30)))
            rows, more = self.chat_store.list_chat_summaries_page(limit=max(1, size - int(include_current)),
                offset=offset, query=query, pinned=section == "pinned")
            end = offset + len(rows)
            selected = ([current] if include_current else []) + [row for row in rows if not include_current or row.get("id") != current.get("id")]
        summaries = [self._remote_chat_summary(row) for row in selected]
        for summary in summaries:
            # IDs remain exact; presentation metadata can be bounded independently
            # of the complete canonical chat retained in SQLite.
            for key, value in list(summary.items()):
                if isinstance(value, str) and key != "chat_id":
                    summary[key] = value.encode("utf-8")[:256].decode("utf-8", errors="ignore")
            while len(json.dumps(summary, ensure_ascii=True).encode("utf-8")) > 2048:
                candidates = [key for key, value in summary.items() if isinstance(value, str) and key != "chat_id" and value]
                if not candidates:
                    break
                key = max(candidates, key=lambda key: len(summary[key]))
                summary[key] = summary[key][:len(summary[key]) // 2]
        for summary in summaries:
            if summary.get("chat_id") == current.get("id"):
                summary["running"] = current.get("running", False)
        if revision != int(getattr(self, "_remote_collection_revision", 0)):
            return 409, {"error": "STALE_COLLECTION_REVISION", "recovery": "REFRESH_COLLECTION"}
        cursor = ChatStore._execution_cursor_token({"revision": revision, "query": query,
            "section": section, "offset": end}, secret) if more else ""
        return 200, {"accepted": True, "chats": summaries, "partial": True, "has_more": more,
                     "next_cursor": cursor, "collection_revision": revision, "section": section}

    def _invalidate_remote_history_list_cache(self) -> None:
        self._remote_history_list_cache = None
        self._remote_collection_revision = int(getattr(self, "_remote_collection_revision", 0)) + 1

    def _invalidate_remote_state_cache(self) -> None:
        self._remote_state_cache = None
        try:
            self._remote_state_cache_revision = int(getattr(self, "_remote_state_cache_revision", 0) or 0) + 1
        except Exception:
            self._remote_state_cache_revision = 1

    def _capture_execution_projection_context_ui(self, payload: dict) -> tuple[int, dict]:
        owner = str(payload.get("chat_id") or "").strip()
        active = owner in {str(getattr(self, "active_chat_id", "") or ""),
                           str(getattr(self, "current_chat_id", "") or "")}
        state = self._current_chat_state if isinstance(getattr(self, "_current_chat_state", None), dict) else {}
        steps = state.get("execution_steps") if isinstance(state.get("execution_steps"), list) else ()
        turns = state.get("turns") if isinstance(state.get("turns"), list) else ()
        with self._execution_step_persist_lock:
            dirty = bool(self._pending_execution_step_persists) or bool(self._execution_step_persist_worker_running)
        dirty = dirty or bool(getattr(self, "_codex_ui_batch_depth", 0)) or bool(self._kimi_execution_write_counts.get(owner))
        last_step = steps[-1] if steps and isinstance(steps[-1], dict) else {}
        active_turn_index = self._active_turn_index_value() if active else -1
        if active and self._safe_int(last_step.get("turn_idx"), -1) != active_turn_index:
            last_step = {}
        last_turn = turns[-1] if turns and isinstance(turns[-1], dict) else {}
        return 200, {
            "active": active,
            "active_turn_index": active_turn_index,
            "selected_model": str(getattr(self, "selected_model", "") or ""),
            "english_filter": bool(self.codex_answer_english_filter_enabled),
            "generation": int(getattr(self, "_execution_scan_generation", 0)),
            "steps_owner": id(steps), "steps_count": len(steps),
            "turns_owner": id(turns), "turns_count": len(turns),
            "last_turn_question": str(last_turn.get("question") or ""),
            "last_turn_answer": str(last_turn.get("answer_md") or ""),
            "last_step_key": str(last_step.get("logical_key") or last_step.get("_execution_uid") or last_step.get("event_id") or ""),
            "last_step_text": str(last_step.get("list_text") or ""),
            "dirty": dirty,
        }

    def _verify_execution_projection_context_ui(self, payload: dict) -> tuple[int, dict]:
        status, current = self._capture_execution_projection_context_ui(payload)
        expected = payload.get("expected") if isinstance(payload.get("expected"), dict) else {}
        keys = ("active", "active_turn_index", "selected_model", "english_filter", "generation",
                "steps_owner", "steps_count", "turns_owner", "turns_count", "last_step_key", "last_step_text",
                "last_turn_question", "last_turn_answer")
        valid = status == 200 and not current["dirty"] and all(current.get(key) == expected.get(key) for key in keys)
        return (200, {"valid": True}) if valid else (409, {"error": "EXECUTION_SOURCE_PENDING"})

    def _remote_api_execution_page_ui(self, payload: dict, *, secret: str) -> tuple[int, dict]:
        """Serve frozen pages on the NATS worker; touch wx only for small owner captures."""
        store = getattr(self, "chat_store", None)
        if store is None:
            return 503, {"error": "execution_authority_unavailable"}
        owner = str(payload.get("chat_id") or "").strip()
        body = payload.get("body") if isinstance(payload.get("body"), dict) else payload
        if not owner:
            return 400, {"error": "chat_id_required"}
        pair_id = self._read_pair_id()
        if not secret:
            return 503, {"error": "cursor_secret_unavailable"}
        domain = str(body.get("sequence_domain") or "events")
        cursor = str(body.get("cursor") or "")
        snapshot_id = str(body.get("snapshot_id") or "")
        try:
            if cursor and self._kimi_execution_write_counts.get(owner):
                raise ValueError("EXECUTION_SOURCE_PENDING")
            if not cursor:
                status, context = self._run_remote_ui_route(self._capture_execution_projection_context_ui, payload)
                if status != 200 or context.get("dirty"):
                    return 409, {"error": "EXECUTION_SOURCE_PENDING", "recovery": "SNAPSHOT_REQUIRED"}
                source = store.read_execution_projection_source(owner)
                active = bool(context["active"])
                mode = "active" if active else "history"
                active_index = int(context["active_turn_index"])
                steps = source["steps"]
                if active and any(isinstance(step, dict) and "turn_idx" in step for step in steps):
                    steps = [step for step in steps if not isinstance(step, dict)
                             or "turn_idx" not in step
                             or self._safe_int(step.get("turn_idx"), -1) == active_index]
                if active and context["steps_count"] and context["last_step_key"]:
                    if not any(str(step.get("logical_key") or step.get("_execution_uid") or step.get("event_id") or "")
                               == context["last_step_key"] and str(step.get("list_text") or "") == context["last_step_text"]
                               for step in steps):
                        raise ValueError("EXECUTION_SOURCE_PENDING")
                if active and context["turns_count"] != len(source["turns"]):
                    raise ValueError("EXECUTION_SOURCE_PENDING")
                if active and source["turns"]:
                    last_turn = source["turns"][-1]
                    if (str(last_turn.get("question") or "") != context["last_turn_question"]
                            or str(last_turn.get("answer_md") or "") != context["last_turn_answer"]):
                        raise ValueError("EXECUTION_SOURCE_PENDING")
                rows = project_execution_rows(
                    chat_id=owner, revision=source["revision"], steps=steps,
                    turns=source["turns"], view_mode=mode,
                    active_turn_index=active_index,
                    selected_model=context["selected_model"],
                    requesting_text=REQUESTING_TEXT,
                    answer_message_ids=source["answer_message_ids"],
                    answer_to_plain=lambda answer_md, model: execution_answer_to_plain(
                        answer_md, model, context["english_filter"]),
                )
                snapshot_id = store.create_execution_projection_snapshot(
                    chat_id=owner, revision=source["revision"],
                    source_hash=source["source_hash"], rows=rows,
                )
                verify_status, _ = self._run_remote_ui_route(
                    self._verify_execution_projection_context_ui,
                    {"chat_id": owner, "expected": context},
                )
                if verify_status != 200:
                    raise ValueError("EXECUTION_SOURCE_PENDING")
            page = store.load_execution_projection_page(
                pair_id=pair_id, domain=domain, chat_id=owner,
                secret=secret, snapshot_id=snapshot_id,
                limit=int(body.get("limit") or 100), cursor=cursor,
            )
            return 200, page
        except (TypeError, ValueError) as exc:
            return 409, {"error": str(exc), "recovery": "SNAPSHOT_REQUIRED"}

    def _remote_api_history_read_ui(self, payload: dict) -> tuple[int, dict]:
        self._request_native_history_refresh()
        chat_id = str(payload.get("chat_id") or "").strip()
        if chat_id in {self.active_chat_id, self.current_chat_id, ""}:
            source_chat = dict(self._current_chat_state or {
                "id": self.active_chat_id or self.current_chat_id or "",
                "title": "新聊天",
                "turns": self.active_session_turns,
            })
            source_chat["turns"] = self.active_session_turns
            chat, has_more, oldest_cursor = self._remote_chat_snapshot_page(source_chat, payload)
        else:
            source_chat = self._find_archived_chat(chat_id) or {}
            if (
                getattr(self, "_chat_store_enabled", False)
                and getattr(self, "chat_store", None) is not None
                and hasattr(self.chat_store, "load_turns_page")
                and not isinstance(source_chat.get("turns"), list)
            ):
                chat, has_more, oldest_cursor = self._remote_chat_snapshot_page_from_store(source_chat, payload)
            else:
                source_chat = self._hydrate_chat_from_store(source_chat, include_execution_steps=bool(payload.get("include_execution_steps"))) or {}
                chat, has_more, oldest_cursor = self._remote_chat_snapshot_page(source_chat, payload)
        return 200, {"accepted": True, "chat": chat, "has_more": has_more, "oldest_cursor": oldest_cursor}

    def _remote_api_state_ui(self, payload: dict | None = None) -> tuple[int, dict]:
        payload = payload or {}
        chat_id = str(payload.get("chat_id") or self.active_chat_id or self.current_chat_id or "").strip()
        include_execution_steps = bool(payload.get("include_execution_steps"))
        raw_limit = payload.get("limit", REMOTE_STATE_DEFAULT_TURN_LIMIT)
        raw_before = payload.get("before_turn_index")
        authority_signature = None
        store = getattr(self, "chat_store", None)
        if chat_id and store is not None and hasattr(store, "get_clear_reconciliation_authority"):
            try:
                authority_signature = store.get_clear_reconciliation_authority(chat_id)
            except (ValueError, RuntimeError):
                authority_signature = None
        cache_key = (
            chat_id,
            include_execution_steps,
            str(raw_limit),
            str(raw_before),
            int(getattr(self, "_remote_state_cache_revision", 0) or 0),
            json.dumps(authority_signature, sort_keys=True),
        )
        cache = getattr(self, "_remote_state_cache", None)
        if isinstance(cache, dict) and cache.get("key") == cache_key and isinstance(cache.get("body"), dict):
            return 200, copy.deepcopy(cache["body"])
        if chat_id in {self.active_chat_id, self.current_chat_id, ""}:
            source_chat = dict(self._current_chat_state or {})
            if not source_chat:
                source_chat = {"id": self.active_chat_id or self.current_chat_id or "", "title": "新聊天"}
            source_chat["id"] = str(source_chat.get("id") or self.active_chat_id or self.current_chat_id or "")
            source_chat["turns"] = self.active_session_turns
            source_chat.setdefault("execution_steps", self._current_chat_state.get("execution_steps") if isinstance(self._current_chat_state, dict) else [])
            state_payload = dict(payload)
            state_payload.setdefault("limit", REMOTE_STATE_DEFAULT_TURN_LIMIT)
            chat, has_more, oldest_cursor = self._remote_chat_snapshot_page(source_chat, state_payload)
            chat["running"] = bool(self.is_running)
            chat["current"] = True
            chat["active"] = True
        else:
            state_payload = dict(payload)
            state_payload.setdefault("limit", REMOTE_STATE_DEFAULT_TURN_LIMIT)
            source_chat = self._find_archived_chat(chat_id) or {}
            if (
                getattr(self, "_chat_store_enabled", False)
                and getattr(self, "chat_store", None) is not None
                and hasattr(self.chat_store, "load_turns_page")
                and not isinstance(source_chat.get("turns"), list)
            ):
                chat, has_more, oldest_cursor = self._remote_chat_snapshot_page_from_store(source_chat, state_payload)
            else:
                source_chat = self._hydrate_chat_from_store(
                    source_chat,
                    include_execution_steps=include_execution_steps,
                ) or {}
                chat, has_more, oldest_cursor = self._remote_chat_snapshot_page(source_chat, state_payload)
        if chat.get("model") == AGY_MODEL_ID:
            running = chat_id in self._agy_busy
            chat["active_turn"] = chat["running"] = running
            status, request_kind = ("running" if running else "idle"), ""
        else:
            status = "waiting_user_input" if self.active_codex_pending_request else "idle"
            request_kind = "user_input" if self.active_codex_pending_request else ""
        chat.update(
            {
                "chat_id": chat.get("chat_id") or chat_id,
                "has_more": has_more,
                "oldest_cursor": oldest_cursor,
                "status": status,
                "request_kind": request_kind,
                "settings": {"codex_answer_english_filter_enabled": self.codex_answer_english_filter_enabled},
                "last_event_id": self.active_codex_turn_id or self.active_openclaw_last_event_id or "",
                "remote_runtime": dict(getattr(self, "remote_control_runtime_status", {}) or {}),
                "remote_runtime_url": str(getattr(self, "remote_control_runtime_url", "") or ""),
                "remote_nats_runtime": dict(getattr(self, "remote_nats_runtime_status", {}) or {}),
                "remote_nats_runtime_url": str(getattr(self, "remote_nats_runtime_url", "") or ""),
            }
        )
        body = {"accepted": True, **chat}
        self._remote_state_cache = {"key": cache_key, "body": copy.deepcopy(body)}
        return 200, body

    def _remote_api_rename_chat_ui(self, payload: dict) -> tuple[int, dict]:
        chat_id = str(payload.get("chat_id") or "").strip()
        title = str(payload.get("title") or "").strip()
        if not chat_id or not title:
            return 400, {"accepted": False, "error": "invalid_payload"}
        chat = self._find_archived_chat(chat_id)
        if not chat and chat_id in {self.active_chat_id, self.current_chat_id}:
            chat = self._current_chat_state
        if not isinstance(chat, dict):
            return 404, {"accepted": False, "error": "not_found"}
        incoming_source = str(payload.get("title_source") or "manual").strip() or "manual"
        incoming_updated_at = float(payload.get("title_updated_at") or bindings.app.time.time())
        current_updated_at = float(chat.get("title_updated_at") or chat.get("updated_at") or 0.0)
        incoming_revision = int(payload.get("title_revision") or (int(chat.get("title_revision") or 0) + 1))
        current_revision = int(chat.get("title_revision") or 0)
        current_source = str(chat.get("title_source") or ("manual" if chat.get("title_manual") else "default")).strip() or "default"
        incoming_priority = self._title_source_priority(incoming_source)
        current_priority = self._title_source_priority(current_source)
        if incoming_updated_at < current_updated_at:
            return 200, {
                "accepted": True,
                "chat_id": chat_id,
                "title": str(chat.get("title") or title),
                "title_source": current_source,
                "title_updated_at": current_updated_at,
                "title_revision": current_revision,
            }
        if incoming_updated_at == current_updated_at and incoming_priority < current_priority:
            return 200, {
                "accepted": True,
                "chat_id": chat_id,
                "title": str(chat.get("title") or title),
                "title_source": current_source,
                "title_updated_at": current_updated_at,
                "title_revision": current_revision,
            }
        resolved_revision = incoming_revision
        if incoming_updated_at == current_updated_at and incoming_priority == current_priority:
            resolved_revision = max(current_revision, incoming_revision)
        chat["title"] = title
        chat["title_manual"] = incoming_source == "manual"
        chat["title_source"] = incoming_source
        chat["title_updated_at"] = incoming_updated_at
        chat["title_revision"] = resolved_revision
        self._invalidate_remote_history_list_cache()
        self._invalidate_remote_state_cache()
        self._save_state()
        self._refresh_history(chat_id)
        self._push_remote_history_changed(chat_id)
        return 200, {
            "accepted": True,
            "chat_id": chat_id,
            "title": title,
            "title_source": incoming_source,
            "title_updated_at": incoming_updated_at,
            "title_revision": resolved_revision,
        }

    def _remote_api_update_settings_ui(self, payload: dict) -> tuple[int, dict]:
        if "codex_answer_english_filter_enabled" in payload:
            self.codex_answer_english_filter_enabled = bool(payload.get("codex_answer_english_filter_enabled"))
            self._invalidate_remote_state_cache()
            self._save_state()
        return 200, {
            "accepted": True,
            "settings": {"codex_answer_english_filter_enabled": self.codex_answer_english_filter_enabled},
        }

    def _chat_for_remote_speed_payload(self, payload: dict | None = None) -> tuple[str, dict | None]:
        payload = payload or {}
        chat_id = str(payload.get("chat_id") or self.active_chat_id or self.current_chat_id or "").strip()
        if chat_id in {self.active_chat_id, self.current_chat_id, ""}:
            if not isinstance(getattr(self, "_current_chat_state", None), dict):
                self._current_chat_state = {}
            if chat_id and not self._current_chat_state.get("id"):
                self._current_chat_state["id"] = chat_id
            return chat_id or str(self.active_chat_id or self.current_chat_id or "").strip(), self._current_chat_state
        chat = self._find_archived_chat(chat_id)
        return chat_id, chat if isinstance(chat, dict) else None

    def _remote_speed_state_body(self, chat_id: str, chat: dict | None) -> dict:
        if isinstance(chat, dict):
            status, body = self._remote_api_state_ui({"chat_id": chat_id or self._chat_id_for_speed_chat(chat)})
            if status < 400:
                body.update(self._codex_speed_payload_for_chat(chat))
                return body
        return {
            "accepted": True,
            "chat_id": chat_id,
            "status": "idle",
            "request_kind": "",
            "turns": [],
            **self._codex_speed_payload_for_chat(chat),
        }

    def _remote_api_speed_options_ui(self, payload: dict) -> tuple[int, dict]:
        chat_id, chat = self._chat_for_remote_speed_payload(payload)
        if not isinstance(chat, dict):
            return 404, {"accepted": False, "error": "not_found"}
        return 200, self._remote_speed_state_body(chat_id, chat)

    def _remote_api_set_speed_ui(self, payload: dict) -> tuple[int, dict]:
        chat_id, chat = self._chat_for_remote_speed_payload(payload)
        if not isinstance(chat, dict):
            return 404, {"accepted": False, "error": "not_found"}
        requested = (
            payload.get("codex_service_tier")
            or payload.get("value")
            or payload.get("speed")
            or ""
        )
        requested_text = str(requested or "").strip()
        tier = normalize_codex_service_tier(requested)
        if not requested_text or (
            not tier
            and requested_text.lower() != "standard"
            and requested_text != codex_service_tier_label("standard")
        ):
            return 400, {"accepted": False, "error": "invalid_speed"}
        self._set_codex_service_tier_for_chat(chat, tier)
        if chat is getattr(self, "_current_chat_state", None) or chat_id in {self.active_chat_id, self.current_chat_id}:
            self._sync_codex_speed_combo_from_chat(chat)
        self._save_state()
        self._push_remote_state(chat_id)
        return 200, self._remote_speed_state_body(chat_id, chat)

    def _remote_api_clear_context_ui(self, payload: dict) -> tuple[int, dict]:
        chat_id = str((payload or {}).get("chat_id") or "").strip()
        if not chat_id:
            return 400, {
                "accepted": False,
                "chat_id": "",
                "error": "missing_chat_id",
            }
        request_id = str((payload or {}).get("request_id") or (payload or {}).get("idempotency_key") or "").strip()
        result = self._clear_context_for_chat_id(
            chat_id, auto_resend_first=True, request_id=request_id or f"remote-{uuid.uuid4().hex}"
        )
        if result == "not_found":
            return 404, {
                "accepted": False,
                "chat_id": chat_id,
                "error": "chat_not_found",
            }
        if result != "cleared":
            return 409, {
                "accepted": False,
                "chat_id": chat_id,
                "error": "clear_context_unavailable",
            }
        body = {"accepted": True, "chat_id": chat_id}
        store = getattr(self, "chat_store", None)
        if store is not None and hasattr(store, "get_clear_reconciliation_authority"):
            body.update(store.get_clear_reconciliation_authority(chat_id))
        return 200, body

    def _handle_remote_pending_request_reply(self, text: str) -> tuple[bool, str]:
        pending = self.active_codex_pending_request
        if not pending:
            return False, "当前没有待回复的请求"
        chat_id = str(pending.get("chat_id") or self.active_chat_id or self.current_chat_id or "").strip()
        target_chat = self._current_chat_state if chat_id in {self.active_chat_id, self.current_chat_id, ""} else self._find_archived_chat(chat_id)
        model = str(pending.get("model") or (target_chat or {}).get("model") or self.selected_model or DEFAULT_CODEX_MODEL).strip() or DEFAULT_CODEX_MODEL
        client = self._get_or_create_codex_client(chat_id, model)
        try:
            start = getattr(client, "start", None)
            if callable(start):
                start()
            client.reply_user_input(chat_id, pending.get("request_id"), {"reply": [str(text or "")]})
        except Exception as exc:
            return False, str(exc)
        self.active_codex_pending_request = None
        self._save_state()
        return True, "已提交"

    def _queue_remote_final(self, chat_id: str, turn_index: int) -> None:
        if not chat_id or turn_index < 0:
            return
        pending = getattr(self, "_pending_remote_finals", None)
        if pending is None:
            pending = self._pending_remote_finals = set()
        pending.add((str(chat_id), int(turn_index)))

    def _flush_pending_remote_finals(self) -> None:
        if getattr(self, "_close_started", False):
            return
        pending = getattr(self, "_pending_remote_finals", None)
        if not pending:
            return
        store = getattr(self, "chat_store", None)
        if store is None:
            return
        retry_needed = False
        for chat_id, turn_index in tuple(pending):
            try:
                turns = store.load_turns(chat_id)
                if not (0 <= turn_index < len(turns)):
                    continue
                turn = turns[turn_index]
                if str(turn.get("request_status") or "") != "done":
                    continue
                answer = str(turn.get("answer_md") or "").strip()
                if not answer or answer == REQUESTING_TEXT:
                    continue
                if not self._push_remote_final_answer(chat_id, answer, turn_index=turn_index):
                    retry_needed = True
                    continue
                self._push_remote_history_changed(chat_id)
            except Exception:
                retry_needed = True
                continue
            pending.discard((chat_id, turn_index))
        if retry_needed and not getattr(self, "_pending_remote_final_retry_scheduled", False):
            schedule = getattr(self, "_call_later_if_alive", None)
            if callable(schedule):
                self._pending_remote_final_retry_scheduled = True
                if schedule(1000, self._retry_pending_remote_finals) is None:
                    self._pending_remote_final_retry_scheduled = False

    def _retry_pending_remote_finals(self) -> None:
        self._pending_remote_final_retry_scheduled = False
        self._flush_pending_remote_finals()
