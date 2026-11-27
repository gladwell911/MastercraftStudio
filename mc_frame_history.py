"""Chat history persistence, migration, and selection."""

import asyncio
import copy
import json
import re
import shutil
import sqlite3
import sys
import uuid
import wx
from agy_acp import AGY_CHAT_FIELDS, AGY_MODEL_ID
from native_history import CODEX_STORAGE_FIELDS, NATIVE_CHAT_FIELDS, allows_final_notification
from codex_client import (
    DEFAULT_CODEX_APPROVAL_MODE,
    DESKTOP_DEFAULT_CODEX_APPROVAL_MODE,
    DEFAULT_CODEX_SANDBOX,
    codex_approval_parameters,
    normalize_codex_approval_mode,
    normalize_codex_sandbox,
)
from context_usage import NATIVE_USAGE_FIELDS
from mc_frame_settings import (
    APP_DIR_NAME,
    APP_STATE_FILE,
    CODEX_BACKGROUND_FLUSH_DELAY_MS,
    DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT,
    EMPTY_CURRENT_CHAT_TITLE,
    EXECUTION_LIST_DEFAULT_VISIBLE_ROWS,
    LEGACY_EMPTY_CURRENT_CHAT_TITLE,
    REQUESTING_TEXT,
    STARTUP_DEFAULT_MODEL_ID,
)
from openclaw_client import DEFAULT_OPENCLAW_SESSION_KEY, is_openclaw_model
from pathlib import Path
from realtime_call import DEFAULT_REALTIME_CALL_ROLE, DEFAULT_REALTIME_CALL_SPEECH_RATE

import mc_frame_bindings as bindings


def _unfinished_agy_answer(turn: dict) -> bool:
    return turn.get("model") == AGY_MODEL_ID and turn.get("request_status") in ("pending", "unknown")


class ChatHistoryMixin:
    def _migrate_legacy_state_if_needed(self) -> None:
        if self.state_path.exists():
            return
        appdata = bindings.app.os.getenv("APPDATA", "").strip()
        legacy_candidates = [
            (Path(appdata) / APP_DIR_NAME / APP_STATE_FILE) if appdata else None,
            Path(bindings.app.__file__).resolve().parent / APP_STATE_FILE,
            Path(sys.executable).resolve().parent / APP_STATE_FILE,
            Path.cwd() / APP_STATE_FILE,
        ]
        for old in legacy_candidates:
            if old is None:
                continue
            try:
                if old.exists():
                    shutil.copy2(old, self.state_path)
                    return
            except Exception:
                continue

    def _migrate_legacy_chat_json_if_needed(self) -> None:
        store = getattr(self, "chat_store", None)
        if store is None or store.get_meta("legacy_json_migration_complete") == "1":
            return
        if not self.state_path.exists():
            store.set_meta("legacy_json_migration_complete", "1")
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return

        chats: list[dict] = []
        active_chat = data.get("active_chat") if isinstance(data.get("active_chat"), dict) else {}
        active_id = str(data.get("active_chat_id") or active_chat.get("id") or "").strip()
        active_turns = data.get("active_session_turns")
        if not isinstance(active_turns, list):
            active_turns = active_chat.get("turns") if isinstance(active_chat.get("turns"), list) else []
        if active_id and active_turns:
            active = copy.deepcopy(active_chat)
            active["id"] = active_id
            active["turns"] = active_turns
            chats.append(active)

        archived = data.get("archived_chats")
        if not isinstance(archived, list):
            archived = data.get("chats")
        if isinstance(archived, list):
            chats.extend(copy.deepcopy(chat) for chat in archived if isinstance(chat, dict))

        if not chats:
            store.set_meta("legacy_json_migration_complete", "1")
            return

        for chat in chats:
            if not isinstance(chat, dict):
                continue
            self._normalize_archived_chat(chat)
            chat_id = str(chat.get("id") or "").strip()
            if not chat_id:
                continue
            store.upsert_chat(chat)
            if isinstance(chat.get("turns"), list):
                store.replace_turns(chat_id, chat.get("turns") or [])
            if isinstance(chat.get("execution_steps"), list):
                store.replace_execution_steps(chat_id, chat.get("execution_steps") or [])

        backup = self.state_path.with_name(f"{self.state_path.name}.bak.{int(bindings.app.time.time())}")
        try:
            shutil.copy2(self.state_path, backup)
        except Exception:
            pass
        for key in ("archived_chats", "chats", "active_session_turns"):
            data.pop(key, None)
        if isinstance(data.get("active_chat"), dict):
            data["active_chat"].pop("turns", None)
            data["active_chat"].pop("execution_steps", None)
        try:
            self.state_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            return
        store.set_meta("legacy_json_migration_complete", "1")

    def _legacy_state_paths(self) -> list[Path]:
        appdata = bindings.app.os.getenv("APPDATA", "").strip()
        runtime_dir = Path(bindings.app.__file__).resolve().parent
        exe_dir = Path(sys.executable).resolve().parent
        paths: list[Path] = [
            self.state_path,
            runtime_dir / APP_STATE_FILE,
            runtime_dir / "dist" / "history" / APP_STATE_FILE,
            exe_dir / APP_STATE_FILE,
            exe_dir / "_internal" / "dist" / "history" / APP_STATE_FILE,
            Path.cwd() / APP_STATE_FILE,
        ]
        if appdata:
            paths.append(Path(appdata) / APP_DIR_NAME / APP_STATE_FILE)
        # De-duplicate while preserving order.
        uniq: list[Path] = []
        seen = set()
        for p in paths:
            k = str(p.resolve()) if p.exists() else str(p)
            if k in seen:
                continue
            seen.add(k)
            uniq.append(p)
        return uniq

    def _merge_legacy_archived_chats(self) -> None:
        merged_by_id: dict[str, dict] = {}

        def _score(chat: dict) -> tuple[int, float]:
            turns = chat.get("turns") or []
            ts = float(chat.get("updated_at") or chat.get("created_at") or 0.0)
            return (len(turns), ts)

        for chat in self.archived_chats:
            cid = str(chat.get("id") or "").strip()
            if not cid:
                continue
            merged_by_id[cid] = chat

        changed = False
        for p in self._legacy_state_paths():
            if p == self.state_path or (not p.exists()):
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            archived = data.get("archived_chats")
            if not isinstance(archived, list):
                archived = data.get("chats")
            if not isinstance(archived, list):
                continue
            for chat in archived:
                if not isinstance(chat, dict):
                    continue
                self._normalize_archived_chat(chat)
                cid = str(chat.get("id") or "").strip()
                if not cid:
                    continue
                old = merged_by_id.get(cid)
                if old is None:
                    merged_by_id[cid] = chat
                    changed = True
                    continue
                if _score(chat) > _score(old):
                    merged_by_id[cid] = chat
                    changed = True

        if changed:
            self.archived_chats = list(merged_by_id.values())
            self._sort_archived_chats()
            self._defer_chat_state_save()

    def _slim_active_chat_state(self) -> dict:
        state = self._current_chat_state if isinstance(getattr(self, "_current_chat_state", None), dict) else {}
        now = bindings.app.time.time()
        title_manual = state.get("title_manual")
        if isinstance(title_manual, str):
            title_manual = title_manual.strip().lower() in {"1", "true", "yes", "y", "on"}
        else:
            title_manual = bool(title_manual)
        slim = {
            "id": str(state.get("id") or self.active_chat_id or self.current_chat_id or "").strip(),
            "title": str(state.get("title") or EMPTY_CURRENT_CHAT_TITLE),
            "pinned": bool(state.get("pinned")),
            "title_manual": title_manual,
            "title_source": str(state.get("title_source") or ("manual" if title_manual else "default")),
            "title_updated_at": float(state.get("title_updated_at") or state.get("updated_at") or now),
            "title_revision": int(state.get("title_revision") or 1),
            "model": self._model_for_chat_selection(state) or STARTUP_DEFAULT_MODEL_ID,
            "created_at": float(state.get("created_at") or self.active_session_started_at or now),
            "updated_at": float(state.get("updated_at") or now),
            "detail_panel_mode": self._detail_panel_mode() if hasattr(self, "answer_list") else "answers",
            "codex_context_generation": int(state.get("codex_context_generation") or 0),
        }
        for field in (*AGY_CHAT_FIELDS, *NATIVE_CHAT_FIELDS, *NATIVE_USAGE_FIELDS, "codex_thread_id", "codex_account_id",
                      "codex_terminal_thread", "codex_terminal_host_id", *CODEX_STORAGE_FIELDS,
                      "kimi_session_id", "kimi_account_id", "kimi_verified_account_id",
                      "kimi_information_generation"):
            if field in state:
                slim[field] = copy.deepcopy(state[field])
        return slim

    def _persist_chat_history_to_store(self, active_snapshot=None) -> None:
        store = getattr(self, "chat_store", None)
        if store is None:
            return
        # Execution steps are persisted incrementally as Codex events arrive.
        # Rewriting them on every UI-thread state save stalls keyboard response
        # once a command-heavy turn has hundreds of steps.
        pending_clear = getattr(self, "_shortcut_clear_pending", {})
        if self.active_chat_id or self.current_chat_id or self.active_session_turns:
            active = self._slim_active_chat_state() if active_snapshot is None else active_snapshot
            active_id = str(active.get("id") or self.active_chat_id or self.current_chat_id or "").strip()
            if active_id and active_id not in pending_clear and not self._agy_terminal_write_rejected(active_id):
                active["id"] = active_id
                active["turns"] = self.active_session_turns if isinstance(self.active_session_turns, list) else []
                state = self._current_chat_state if isinstance(getattr(self, "_current_chat_state", None), dict) else {}
                active_steps = state.get("execution_steps") if isinstance(state.get("execution_steps"), list) else []
                active["execution_steps"] = active_steps
                store.upsert_chat(active)
                self._persist_dirty_chat_turns(store, active_id, active["turns"])
                self._persist_initial_execution_steps_if_needed(store, active_id, active_steps)
        for chat in self.archived_chats:
            if not isinstance(chat, dict):
                continue
            chat_id = str(chat.get("id") or "").strip()
            if not chat_id or chat_id in pending_clear or self._agy_terminal_write_rejected(chat_id):
                continue
            store.upsert_chat(chat)
            if isinstance(chat.get("turns"), list):
                self._persist_dirty_chat_turns(store, chat_id, chat.get("turns") or [])
            steps = chat.get("execution_steps") if isinstance(chat.get("execution_steps"), list) else []
            self._persist_initial_execution_steps_if_needed(store, chat_id, steps)

    def _capture_close_write_batch(self):
        data = copy.deepcopy(self._state_data())
        active_id = str(self.active_chat_id or self.current_chat_id or "").strip()
        active = None
        if active_id:
            active = {**data["active_chat"], "id": active_id,
                      "turns": self.active_session_turns,
                      "execution_steps": self._current_chat_state.get("execution_steps", [])}
        rows = [chat for chat in ([active] if active else []) + list(self.archived_chats)
                if isinstance(chat, dict) and chat.get("id") and not self._agy_terminal_write_rejected(chat["id"])]
        return (
            self.chat_store if self._chat_store_enabled else None,
            self.state_path, data, copy.deepcopy(tuple(rows)), dict(self._chat_turn_dirty_from),
            self._close_publication_pair, self._close_pair_id,
            tuple(sorted(getattr(self, "_pending_remote_finals", ()))),
            tuple((uuid.uuid4().hex, copy.deepcopy(read))
                  for read in getattr(self, "_pending_answer_reads", {}).values()),
            tuple(getattr(self, "_close_unattempted_resends", ())),
        )

    @staticmethod
    def _persist_close_turn_facts(store, pair, owner, title, index, turn):
        if turn.get("request_status") == "failed":
            try:
                store.commit_attention_notification(pair_id=pair, chat_id=owner, kind="failed",
                    operation_id=str(turn.get("client_send_id") or f"turn-{index}"),
                    text=str(turn.get("request_error") or "请求失败"))
            except (ValueError, RuntimeError):
                pass
        for role, kind, field in (("user", "question", "question"), ("assistant", "final", "answer_md")):
            text = str(turn.get(field) or "").strip()
            if not text or (role == "assistant" and (text == REQUESTING_TEXT or _unfinished_agy_answer(turn))):
                continue
            try:
                message_id = store.resolve_canonical_message_by_turn(owner, role=role, turn_index=index)
            except ValueError as exc:
                if str(exc) != "EVENT_ID_CONFLICT":
                    store.quarantine(str(exc), {"chat_id": owner, "turn_index": index, "role": role})
                continue
            try:
                store.commit_message_execution_projection(pair_id=pair, domain="events", chat_id=owner,
                                                           message_id=message_id, projection_kind=kind)
            except ValueError as exc:
                if str(exc) != "EVENT_ID_CONFLICT":
                    store.quarantine(str(exc), {"chat_id": owner, "turn_index": index, "role": role})
                    continue
            notification = ("assistant_final" if role == "assistant" and turn.get("request_status") == "done"
                            and allows_final_notification(turn, pair) else "user_message" if role == "user" and turn.get("question_origin") == "mc" else "")
            if notification:
                try:
                    store.commit_message_notification_fact(pair_id=pair, domain="events", chat_id=owner,
                        message_id=message_id, notification_kind=notification, text=text, chat_title=title,
                        origin_client="mc")
                except ValueError as exc:
                    if str(exc) != "EVENT_ID_CONFLICT":
                        store.quarantine(str(exc), {"chat_id": owner, "turn_index": index, "role": role})

    @staticmethod
    def _write_close_batch(batch):
        store, path, data, rows, dirty, publication_pair, pair, pending_finals, reads, releases = batch
        if store is not None:
            for op_id, owner, revision in releases:
                store.release_unattempted_clear_resend(op_id, chat_id=owner, revision=revision)
            titles = {chat["id"]: str(chat.get("title") or "").strip() for chat in rows}
            publish = store.v2_writes_enabled
            projected = set()
            for chat in rows:
                owner = chat["id"]
                store.upsert_chat(chat)
                turns = chat.get("turns")
                if isinstance(turns, list):
                    start = dirty.get(owner)
                    if start is None and turns and store.count_turns(owner) == 0:
                        start = 0
                    if start is not None:
                        start = min(start, len(turns))
                        store.replace_turns_from(owner, turns[start:], start_index=start)
                        if publish and publication_pair:
                            for index in range(start, len(turns)):
                                ChatHistoryMixin._persist_close_turn_facts(
                                    store, publication_pair, owner, titles[owner], index, turns[index])
                                projected.add((owner, index))
                steps = chat.get("execution_steps")
                if steps and not store.load_recent_execution_steps(owner, limit=1)[0]:
                    store.replace_execution_steps(owner, steps)
            for operation_id, read in reads:
                owner = read["chat_id"]
                identity = store.readable_answer_for_turn(owner, turn_index=read["turn_index"],
                    turn=read["frozen_turn"], generation=read["generation"])
                if identity:
                    store.mark_chat_read(pair_id=read["pair_id"], chat_id=owner, generation=read["generation"],
                        message_id=identity["message_id"], answer_seq=identity["answer_seq"], operation_id=operation_id)
            if publish:
                for owner, index in pending_finals:
                    if (owner, index) in projected:
                        continue
                    turns = store.load_turns(owner)
                    if 0 <= index < len(turns) and turns[index].get("request_status") == "done":
                        ChatHistoryMixin._persist_close_turn_facts(
                            store, publication_pair or pair, owner, titles.get(owner, ""), index, turns[index])
        temporary = path.with_suffix(path.suffix + ".closing")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def _persist_initial_execution_steps_if_needed(self, store, chat_id: str, steps: list) -> None:
        if not steps:
            return
        normalized = str(chat_id or "").strip()
        if not normalized:
            return
        has_steps = False
        load_recent = getattr(store, "load_recent_execution_steps", None)
        if callable(load_recent):
            try:
                total, _rows = load_recent(normalized, limit=1)
                has_steps = int(total or 0) > 0
            except Exception:
                has_steps = False
        else:
            load_steps = getattr(store, "load_execution_steps", None)
            if callable(load_steps):
                try:
                    has_steps = bool(load_steps(normalized))
                except Exception:
                    has_steps = False
        if has_steps:
            return
        replace_steps = getattr(store, "replace_execution_steps", None)
        if callable(replace_steps):
            replace_steps(normalized, list(steps or []))

    def _mark_chat_turns_dirty(self, chat_id: str | None = None, start_index: int = 0) -> None:
        normalized = str(chat_id or self.active_chat_id or self.current_chat_id or "").strip()
        if not normalized:
            return
        self._invalidate_remote_state_cache()
        try:
            index = max(0, int(start_index))
        except Exception:
            index = 0
        previous = self._chat_turn_dirty_from.get(normalized)
        self._chat_turn_dirty_from[normalized] = index if previous is None else min(previous, index)

    def _persist_dirty_chat_turns(self, store, chat_id: str, turns: list[dict], *,
                                  attachment_scope: tuple = ()) -> None:
        normalized = str(chat_id or "").strip()
        if not normalized or normalized in getattr(self, "_shortcut_clear_pending", {}):
            return
        dirty_from = self._chat_turn_dirty_from.get(normalized)
        if dirty_from is None:
            count_turns = getattr(store, "count_turns", None)
            if callable(count_turns):
                try:
                    if int(count_turns(normalized) or 0) == 0 and turns:
                        dirty_from = 0
                except Exception:
                    dirty_from = None
        if dirty_from is None:
            if getattr(self, "_pending_answer_reads", None):
                self._flush_pending_answer_reads(normalized)
            return
        start_index = max(0, min(int(dirty_from), len(turns or [])))
        replace_from = getattr(store, "replace_turns_from", None)
        if callable(replace_from):
            options = {"attachment_scope": attachment_scope} if attachment_scope else {}
            replace_from(normalized, list(turns or [])[start_index:], start_index=start_index, **options)
        else:
            store.replace_turns(normalized, turns or [])
        if getattr(self, "_pending_answer_reads", None):
            self._flush_pending_answer_reads(normalized)
        # Canonical message rows now exist. Deterministic event IDs make this safe
        # across repeated saves and ensure every provider path projects exactly once.
        transport = getattr(self, "_remote_nats_transport", None)
        if (transport is not None and int(getattr(transport, "protocol_version", 1) or 1) >= 2
                and getattr(store, "v2_writes_enabled", False)):
            chat_summary = (
                self._current_chat_state
                if str((self._current_chat_state or {}).get("id") or "").strip() == normalized
                else self._chat_summary_by_id(normalized)
            )
            chat_title = str((chat_summary or {}).get("title") or "").strip()
            for index in range(start_index, len(turns or [])):
                turn = turns[index] if isinstance(turns[index], dict) else {}
                if str(turn.get("request_status") or "") == "failed":
                    self._push_remote_attention(normalized, "failed",
                        str(turn.get("client_send_id") or f"turn-{index}"),
                        str(turn.get("request_error") or "\u8bf7\u6c42\u5931\u8d25"))
                for role, kind, content_key in (("user", "question", "question"),
                                                ("assistant", "final", "answer_md")):
                    content = str(turn.get(content_key) or "")
                    if not content or (role == "assistant" and (content == REQUESTING_TEXT or _unfinished_agy_answer(turn))):
                        continue
                    try:
                        message_id = store.resolve_canonical_message_by_turn(
                            normalized, role=role, turn_index=index)
                        store.commit_message_execution_projection(
                            pair_id=transport.subjects.pair_id, domain="events",
                            chat_id=normalized, message_id=message_id,
                            projection_kind=kind)
                        if (role == "assistant" and turn.get("request_status") == "done"
                                and allows_final_notification(turn, transport.subjects.pair_id)):
                            store.commit_message_notification_fact(
                                pair_id=transport.subjects.pair_id, domain="events",
                                chat_id=normalized, message_id=message_id,
                                notification_kind="assistant_final", text=content,
                                chat_title=chat_title, origin_client="mc")
                        if role == "user" and str(turn.get("question_origin") or "").strip() == "mc":
                            store.commit_message_notification_fact(
                                pair_id=transport.subjects.pair_id,
                                domain="events",
                                chat_id=normalized,
                                message_id=message_id,
                                notification_kind="user_message",
                                text=content,
                                chat_title=chat_title,
                                origin_client="mc",
                            )
                    except ValueError as exc:
                        if str(exc) != "EVENT_ID_CONFLICT":
                            store.quarantine(str(exc), {"chat_id": normalized,
                                                       "turn_index": index, "role": role})
            loop = getattr(transport, "_loop", None)
            if loop is not None and loop.is_running():
                asyncio.run_coroutine_threadsafe(transport.drain_outbox(), loop)
        self._chat_turn_dirty_from.pop(normalized, None)
        self._call_after_if_alive(self._refresh_chat_read_row, normalized)

    def _refresh_chat_read_row(self, owner):
        self._invalidate_remote_history_list_cache()
        self._upsert_history_row(owner, allow_reorder=False)

    def _chat_summary_by_id(self, chat_id: str) -> dict | None:
        normalized = str(chat_id or "").strip()
        if not normalized:
            return None
        for chat in self.archived_chats:
            if isinstance(chat, dict) and str(chat.get("id") or "").strip() == normalized:
                return chat
        return None

    def _hydrate_chat_from_store(self, chat: dict | None, *, include_execution_steps: bool = True) -> dict | None:
        if not isinstance(chat, dict):
            return None
        if not getattr(self, "_chat_store_enabled", False):
            return chat
        chat_id = str(chat.get("id") or "").strip()
        store = getattr(self, "chat_store", None)
        if not include_execution_steps and chat_id and not isinstance(chat.get("execution_steps"), list) and hasattr(store, "load_recent_execution_steps"):
            try:
                known = getattr(self, "_execution_known_tail", None)
                if known and known[0][0] == chat_id and known[0][-1] == self._safe_int(chat.get("revision"), 1):
                    tail = known[1]
                else:
                    _, tail = store.load_recent_execution_steps(
                        chat_id, limit=EXECUTION_LIST_DEFAULT_VISIBLE_ROWS + 1,
                        include_total=False, visible_only=True)
                chat["execution_steps"] = list(tail)
            except (sqlite3.Error, OSError):
                pass
        turns = chat.get("turns")
        if isinstance(turns, list) and (
            not include_execution_steps or isinstance(chat.get("execution_steps"), list)
        ):
            return chat
        chat_id = str(chat.get("id") or "").strip()
        store = getattr(self, "chat_store", None)
        if not chat_id or store is None:
            return chat
        try:
            if isinstance(turns, list):
                if include_execution_steps and not isinstance(chat.get("execution_steps"), list):
                    chat["execution_steps"] = store.load_execution_steps(chat_id)
                return chat
            loaded = store.load_chat(chat_id, include_execution_steps=include_execution_steps)
        except (sqlite3.Error, OSError):
            return chat
        if not isinstance(loaded, dict):
            return chat
        # Summary entries may already have live edits. Fill absent durable fields
        # without invalidating references held by event handlers or the history UI.
        for key, value in loaded.items():
            chat.setdefault(key, value)
        if not isinstance(turns, list):
            chat["turns"] = loaded.get("turns", [])
        self._prune_kimi_interaction_errors(chat)
        return chat

    def _load_state(self):
        self.hide_terminal_chats = False
        if not self.state_path.exists():
            if getattr(self, "_chat_store_enabled", False):
                self.archived_chats = self.chat_store.list_chat_summaries()
            return
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return
        self.selected_model = str(data.get("selected_model_id") or STARTUP_DEFAULT_MODEL_ID)
        if not bindings.app.is_visible_model_id(self.selected_model):
            self.selected_model = STARTUP_DEFAULT_MODEL_ID
        self.codex_answer_english_filter_enabled = bool(data.get("codex_answer_english_filter_enabled", False))
        self.hide_terminal_chats = bool(data.get("hide_terminal_chats", False))
        version = data.get("codex_approval_defaults_version")
        version = version if type(version) is int and version >= 1 else 0
        self.codex_approval_defaults_version = max(1, version)
        self.codex_approval_policy = normalize_codex_approval_mode(
            data.get("codex_approval_policy") if version and data.get("codex_approval_policy")
            else DESKTOP_DEFAULT_CODEX_APPROVAL_MODE
        )
        approval_changed = not version or data.get("codex_approval_policy") != self.codex_approval_policy
        self.codex_sandbox = normalize_codex_sandbox(data["codex_sandbox"]) if "codex_sandbox" in data else codex_approval_parameters(self.codex_approval_policy)["sandbox"]
        approval_changed = approval_changed or data.get("codex_sandbox") != self.codex_sandbox

        use_chat_store = bool(getattr(self, "_chat_store_enabled", False))
        if use_chat_store and getattr(self, "chat_store", None) is not None:
            self.archived_chats = self.chat_store.list_chat_summaries()
        else:
            archived = data.get("archived_chats")
            if isinstance(archived, list):
                self.archived_chats = archived
            else:
                legacy = data.get("chats")
                if isinstance(legacy, list):
                    self.archived_chats = legacy
        changed = approval_changed
        if not use_chat_store:
            for chat in self.archived_chats:
                if isinstance(chat, dict) and self._normalize_archived_chat(chat):
                    changed = True

        active_turns = data.get("active_session_turns")
        if isinstance(active_turns, list) and not use_chat_store:
            self.active_session_turns = active_turns
        active_chat = data.get("active_chat")
        if isinstance(active_chat, dict):
            self._current_chat_state = active_chat
            if self._normalize_detail_panel_fields(self._current_chat_state):
                changed = True
            if not isinstance(active_turns, list):
                chat_turns = active_chat.get("turns")
                if isinstance(chat_turns, list):
                    self.active_session_turns = chat_turns
            self.active_chat_id = str(active_chat.get("id") or self.active_chat_id or "").strip()
        self.active_chat_id = str(data.get("active_chat_id") or self.active_chat_id or "").strip()
        if use_chat_store and getattr(self, "chat_store", None) is not None:
            if self.active_chat_id:
                self.active_session_turns = self.chat_store.load_turns(self.active_chat_id)
                summary = self._chat_summary_by_id(self.active_chat_id) or {}
                current = self._current_chat_state if isinstance(self._current_chat_state, dict) else {}
                merged = copy.deepcopy(summary)
                merged.update(current)
                for field in (*NATIVE_CHAT_FIELDS, *NATIVE_USAGE_FIELDS):
                    if field in summary:
                        merged[field] = copy.deepcopy(summary[field])
                merged["id"] = self.active_chat_id
                merged["turns"] = self.active_session_turns
                # Store metadata is the durable owner record.  A stale global
                # state snapshot must not replace its committed model.
                owner_model_source = {"model": summary.get("model"), "turns": self.active_session_turns}
                merged["model"] = self._model_for_chat_selection(owner_model_source) or STARTUP_DEFAULT_MODEL_ID
                if summary.get("model") != merged["model"]:
                    changed = True
                self._current_chat_state = merged
            elif isinstance(active_turns, list) and active_turns:
                self.active_session_turns = active_turns
                current = self._current_chat_state if isinstance(self._current_chat_state, dict) else {}
                if not current:
                    current = {}
                current["turns"] = self.active_session_turns
                self._current_chat_state = current
            else:
                self.active_session_turns = []
        self.active_openclaw_session_key = str(data.get("active_openclaw_session_key") or DEFAULT_OPENCLAW_SESSION_KEY).strip() or DEFAULT_OPENCLAW_SESSION_KEY
        self.active_openclaw_session_id = str(data.get("active_openclaw_session_id") or "").strip()
        self.active_openclaw_session_file = str(data.get("active_openclaw_session_file") or "").strip()
        try:
            self.active_openclaw_sync_offset = max(int(data.get("active_openclaw_sync_offset") or 0), 0)
        except Exception:
            self.active_openclaw_sync_offset = 0
        self.active_openclaw_last_event_id = str(data.get("active_openclaw_last_event_id") or "").strip()
        try:
            self.active_openclaw_last_synced_at = float(data.get("active_openclaw_last_synced_at") or 0.0)
        except Exception:
            self.active_openclaw_last_synced_at = 0.0
        self.active_codex_thread_id = str(data.get("active_codex_thread_id") or "").strip()
        self.active_codex_turn_id = str(data.get("active_codex_turn_id") or "").strip()
        self.active_codex_turn_active = bool(data.get("active_codex_turn_active", False))
        self.active_codex_pending_prompt = str(data.get("active_codex_pending_prompt") or "").strip()
        pending_request = data.get("active_codex_pending_request")
        self.active_codex_pending_request = pending_request if isinstance(pending_request, dict) else None
        request_queue = data.get("active_codex_request_queue")
        self.active_codex_request_queue = request_queue if isinstance(request_queue, list) else []
        thread_flags = data.get("active_codex_thread_flags")
        self.active_codex_thread_flags = thread_flags if isinstance(thread_flags, list) else []
        self.active_codex_latest_assistant_text = str(data.get("active_codex_latest_assistant_text") or "").strip()
        self.active_codex_latest_assistant_phase = str(data.get("active_codex_latest_assistant_phase") or "").strip()
        self.active_kimi_session_id = str(data.get("active_kimi_session_id") or "").strip()
        self.active_kimi_turn_id = str(data.get("active_kimi_turn_id") or "").strip()
        self.active_kimi_turn_active = bool(data.get("active_kimi_turn_active", False))
        self.active_kimi_pending_prompt = str(data.get("active_kimi_pending_prompt") or "").strip()
        kimi_request_queue = data.get("active_kimi_request_queue")
        self.active_kimi_request_queue = kimi_request_queue if isinstance(kimi_request_queue, list) else []
        if isinstance(self._current_chat_state, dict):
            self._current_chat_state["kimi_session_id"] = self.active_kimi_session_id
            self._current_chat_state["kimi_turn_id"] = self.active_kimi_turn_id
            self._current_chat_state["kimi_turn_active"] = self.active_kimi_turn_active
            self._current_chat_state["kimi_pending_prompt"] = self.active_kimi_pending_prompt
            self._current_chat_state["kimi_request_queue"] = self.active_kimi_request_queue
        self.active_claudecode_session_id = str(data.get("active_claudecode_session_id") or "").strip()
        self.active_session_started_at = float(data.get("active_session_started_at") or 0.0)
        self.realtime_call_role = str(data.get("realtime_call_role") or DEFAULT_REALTIME_CALL_ROLE).strip() or DEFAULT_REALTIME_CALL_ROLE
        try:
            self.realtime_call_speech_rate = int(min(max(int(data.get("realtime_call_speech_rate", DEFAULT_REALTIME_CALL_SPEECH_RATE)), -50), 100))
        except Exception:
            self.realtime_call_speech_rate = DEFAULT_REALTIME_CALL_SPEECH_RATE
        self.remote_control_token = str(data.get("remote_control_token") or self.remote_control_token or "").strip()
        self.remote_control_domain = str(data.get("remote_control_domain") or self.remote_control_domain or "").strip()
        self.remote_control_host = str(data.get("remote_control_host") or self.remote_control_host or "127.0.0.1").strip() or "127.0.0.1"
        try:
            self.remote_control_port = int(
                data.get("remote_control_port")
                or self.remote_control_port
                or DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT
            )
        except Exception:
            self.remote_control_port = DEFAULT_REMOTE_CLOUDFLARED_ORIGIN_PORT
        self.remote_control_autostart = bool(data.get("remote_control_autostart", self.remote_control_autostart))
        notes_state = data.get("notes_ui_state")
        if isinstance(notes_state, dict):
            self._current_notes_state = notes_state
        if not bindings.app.is_visible_model_id(self.selected_model):
            self.selected_model = STARTUP_DEFAULT_MODEL_ID
        if self.active_session_turns and not self.active_chat_id:
            self.active_chat_id = str(uuid.uuid4())
        if self.active_session_turns and not self.active_openclaw_session_id:
            if any(is_openclaw_model(str(turn.get("model") or "")) for turn in self.active_session_turns):
                self.active_openclaw_session_id = self._make_openclaw_session_id(self.active_chat_id)
        if self.active_chat_id and not self.current_chat_id:
            self.current_chat_id = self.active_chat_id
        if self.active_chat_id and not self._current_chat_state.get("id"):
            self._current_chat_state["id"] = self.active_chat_id
        if isinstance(self._current_chat_state, dict):
            self._current_chat_state["turns"] = self.active_session_turns
            if self._normalize_detail_panel_fields(self._current_chat_state):
                changed = True
            self._prune_kimi_interaction_errors(self._current_chat_state)
        for chat in self.archived_chats:
            if isinstance(chat, dict):
                self._prune_kimi_interaction_errors(chat)
        self._rebuild_kimi_runtime_state()
        if self._kimi_pending_owners() and self._kimi_client is None:
            self._recover_kimi_pending_owners()
        self._sort_archived_chats()
        if changed:
            self._defer_chat_state_save()

    def _capture_notes_ui_state(self, capture_notes_editor=False):
        if hasattr(self, "notes_controller"):
            capture = getattr(self.notes_controller, "capture_editor_state", None)
            if capture_notes_editor and callable(capture):
                capture()
            to_state = getattr(self.notes_controller, "to_state_dict", None)
            if callable(to_state):
                try:
                    self._current_notes_state = to_state(capture_editor=bool(capture_notes_editor))
                except TypeError:
                    self._current_notes_state = to_state()

    def _save_state(self, *, persist_chat_history: bool = True, capture_notes_editor: bool = False):
        if getattr(self, "_close_started", False):
            return
        self._capture_notes_ui_state(capture_notes_editor)
        use_chat_store = bool(getattr(self, "_chat_store_enabled", False))
        if use_chat_store and persist_chat_history:
            self._persist_chat_history_to_store()
        self._write_state_data(self._state_data())

    def _state_data(self):
        use_chat_store = bool(getattr(self, "_chat_store_enabled", False))
        active_chat = (
            self._slim_active_chat_state()
            if use_chat_store
            else (
                copy.deepcopy(self._current_chat_state)
                if isinstance(getattr(self, "_current_chat_state", None), dict)
                else {
                    "id": self.active_chat_id or self.current_chat_id or "",
                    "turns": copy.deepcopy(self.active_session_turns),
                }
            )
        )
        data = {
            "selected_model_id": self.selected_model,
            "active_chat": active_chat,
            "active_chat_id": self.active_chat_id,
            "active_openclaw_session_key": self.active_openclaw_session_key,
            "active_openclaw_session_id": self.active_openclaw_session_id,
            "active_openclaw_session_file": self.active_openclaw_session_file,
            "active_openclaw_sync_offset": self.active_openclaw_sync_offset,
            "active_openclaw_last_event_id": self.active_openclaw_last_event_id,
            "active_openclaw_last_synced_at": self.active_openclaw_last_synced_at,
            "active_codex_thread_id": self.active_codex_thread_id,
            "active_codex_turn_id": self.active_codex_turn_id,
            "active_codex_turn_active": self.active_codex_turn_active,
            "active_codex_pending_prompt": self.active_codex_pending_prompt,
            "active_codex_pending_request": self.active_codex_pending_request,
            "active_codex_request_queue": self.active_codex_request_queue,
            "active_codex_thread_flags": self.active_codex_thread_flags,
            "active_codex_latest_assistant_text": self.active_codex_latest_assistant_text,
            "active_codex_latest_assistant_phase": self.active_codex_latest_assistant_phase,
            "active_kimi_session_id": self.active_kimi_session_id,
            "active_kimi_turn_id": self.active_kimi_turn_id,
            "active_kimi_turn_active": self.active_kimi_turn_active,
            "active_kimi_pending_prompt": self.active_kimi_pending_prompt,
            "active_kimi_request_queue": self.active_kimi_request_queue,
            "active_claudecode_session_id": self.active_claudecode_session_id,
            "codex_answer_english_filter_enabled": self.codex_answer_english_filter_enabled,
            "hide_terminal_chats": self.hide_terminal_chats,
            "codex_sandbox": normalize_codex_sandbox(getattr(self, "codex_sandbox", DEFAULT_CODEX_SANDBOX)),
            "codex_approval_policy": normalize_codex_approval_mode(getattr(self, "codex_approval_policy", DEFAULT_CODEX_APPROVAL_MODE)),
            "codex_approval_defaults_version": max(1, getattr(self, "codex_approval_defaults_version", 1)),
            "active_session_started_at": self.active_session_started_at,
            "realtime_call_role": self.realtime_call_role,
            "realtime_call_speech_rate": self.realtime_call_speech_rate,
            "remote_control_token": self.remote_control_token,
            "remote_control_domain": self.remote_control_domain,
            "remote_control_host": self.remote_control_host,
            "remote_control_port": self.remote_control_port,
            "remote_control_autostart": self.remote_control_autostart,
            "notes_ui_state": self._current_notes_state,
        }
        if not use_chat_store:
            data["archived_chats"] = self.archived_chats
            data["active_session_turns"] = self.active_session_turns
        elif not (self.active_chat_id or self.current_chat_id or self.active_session_turns):
            data["active_session_turns"] = []
        return data

    def _write_state_data(self, data):
        try:
            self.state_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _is_timestamp_like_archive_title(self, title: str) -> bool:
        text = str(title or "").strip()
        if not text:
            return False
        if re.fullmatch(r"\d{9,}(?:\.\d+)?", text):
            return True
        normalized = re.sub(r"\s+", " ", text)
        patterns = [
            r"\d{4}[-/]\d{1,2}[-/]\d{1,2}(?: \d{1,2}:\d{2}(?::\d{2})?)?",
            r"\d{1,2}[-/]\d{1,2}(?: \d{1,2}:\d{2}(?::\d{2})?)?",
            r"\d{1,2}:\d{2}(?::\d{2})?",
        ]
        return any(re.fullmatch(p, normalized) for p in patterns)

    @staticmethod
    def _normalize_detail_panel_fields(chat: dict) -> bool:
        changed = False
        detail_panel_mode = str(chat.get("detail_panel_mode") or "").strip()
        if detail_panel_mode not in {"answers", "execution"}:
            chat["detail_panel_mode"] = "answers"
            changed = True
        if not isinstance(chat.get("execution_steps"), list):
            chat["execution_steps"] = []
            changed = True
        return changed

    def _normalize_archived_chat(self, chat: dict) -> bool:
        changed = self._normalize_detail_panel_fields(chat)
        title_manual = chat.get("title_manual")
        if not isinstance(title_manual, bool):
            title_manual = bool(title_manual)
            chat["title_manual"] = title_manual
            changed = True
        title_source = str(chat.get("title_source") or "").strip() or (
            "manual" if title_manual else "default"
        )
        if chat.get("title_source") != title_source:
            chat["title_source"] = title_source
            changed = True
        title_updated_at = float(chat.get("title_updated_at") or chat.get("updated_at") or chat.get("created_at") or 0.0)
        if float(chat.get("title_updated_at") or 0.0) != title_updated_at:
            chat["title_updated_at"] = title_updated_at
            changed = True
        title_revision = int(chat.get("title_revision") or 1)
        if int(chat.get("title_revision") or 0) != title_revision:
            chat["title_revision"] = title_revision
            changed = True
        if not isinstance(chat.get("pinned"), bool):
            chat["pinned"] = bool(chat.get("pinned"))
            changed = True
        title = str(chat.get("title") or "").strip()
        if (not title_manual) and ((not title) or self._is_timestamp_like_archive_title(title)):
            chat["title"] = self._summarize_last_turn_locally(chat.get("turns") or [])
            changed = True
        return changed

    @staticmethod
    def _title_source_question(turns) -> str:
        for turn in turns or []:
            if not isinstance(turn, dict):
                continue
            question = str(turn.get("question") or "").strip()
            if question:
                return question
        return ""

    @staticmethod
    def _compact_first_question_title(text: str, max_length: int = 18) -> str:
        rules = bindings.app.load_chat_title_rules()
        normalized = str(text or "").replace("\r", " ").replace("\n", " ")
        normalized = re.sub(r"\s+", " ", normalized).strip()

        def _prefix_pattern(values: list[str]):
            if not values:
                return None
            return re.compile(rf"^(?:{'|'.join(re.escape(value) for value in values)})\s*")

        def _suffix_pattern(values: list[str]):
            if not values:
                return None
            return re.compile(rf"(?:{'|'.join(re.escape(value) for value in values)})[？?]*$")

        leading_pattern = _prefix_pattern(rules["leading_phrases"])
        if leading_pattern:
            normalized = leading_pattern.sub("", normalized)
        action_pattern = _prefix_pattern(rules["action_prefixes"])
        if action_pattern:
            normalized = action_pattern.sub("", normalized)
        what_is_pattern = _prefix_pattern(rules["what_is_prefixes"])
        if what_is_pattern:
            normalized = what_is_pattern.sub("", normalized)
        question_pattern = _suffix_pattern(rules["question_suffixes"])
        if question_pattern:
            normalized = question_pattern.sub("", normalized)
        if rules["trailing_punctuation"]:
            normalized = re.sub(
                rf"[{''.join(re.escape(value) for value in rules['trailing_punctuation'])}]+$",
                "",
                normalized,
            )
        normalized = normalized.strip()
        semantic_patterns = (
            (("好吃", "美食", "餐厅", "菜谱", "吃什么"), "美食推荐"),
            (("旅游", "景点", "行程", "攻略"), "旅行攻略"),
            (("简历", "求职", "面试"), "求职准备"),
            (("自动化", "测试"), "自动化测试"),
            (("笔记", "同步"), "笔记同步"),
        )
        for keywords, replacement in semantic_patterns:
            if any(keyword in normalized for keyword in keywords):
                normalized = replacement
                break
        return normalized[:max_length].strip() if normalized else ""

    @staticmethod
    def _is_default_chat_title(title: str) -> bool:
        text = str(title or "").strip()
        if text in {EMPTY_CURRENT_CHAT_TITLE, LEGACY_EMPTY_CURRENT_CHAT_TITLE}:
            return True
        if re.fullmatch(rf"{re.escape(EMPTY_CURRENT_CHAT_TITLE)}\d+", text):
            return True
        if re.fullmatch(rf"{re.escape(LEGACY_EMPTY_CURRENT_CHAT_TITLE)}\d+", text):
            return True
        return (
            text == "新聊天"
            or text == EMPTY_CURRENT_CHAT_TITLE
            or bool(re.fullmatch(rf"{re.escape(EMPTY_CURRENT_CHAT_TITLE)}\d+", text))
        )

    def _existing_chat_titles(self, *, exclude_chat_id: str = "") -> set[str]:
        excluded = str(exclude_chat_id or "").strip()
        existing_titles = {
            str(chat.get("title") or "").strip()
            for chat in self.archived_chats
            if isinstance(chat, dict) and str(chat.get("id") or "").strip() != excluded
        }
        current_id = str((self._current_chat_state or {}).get("id") or "").strip()
        current_title = str((self._current_chat_state or {}).get("title") or "").strip()
        if current_title and current_id != excluded:
            existing_titles.add(current_title)
        store = getattr(self, "chat_store", None)
        list_summaries = getattr(store, "list_chat_summaries", None)
        if bool(getattr(self, "_chat_store_enabled", False)) and not callable(list_summaries):
            raise RuntimeError("Live chat titles cannot be enumerated authoritatively")
        if bool(getattr(self, "_chat_store_enabled", False)):
            for chat in list_summaries():
                if not isinstance(chat, dict) or str(chat.get("id") or "").strip() == excluded:
                    continue
                durable_title = str(chat.get("title") or "").strip()
                if durable_title:
                    existing_titles.add(durable_title)
        return {title for title in existing_titles if title}

    def _unique_chat_title(self, title: str, *, exclude_chat_id: str = "") -> str:
        base = str(title or "").strip() or EMPTY_CURRENT_CHAT_TITLE
        existing_titles = self._existing_chat_titles(exclude_chat_id=exclude_chat_id)
        if base not in existing_titles:
            return base
        index = 1
        while f"{base}{index}" in existing_titles:
            index += 1
        return f"{base}{index}"

    def _next_default_chat_title(self) -> str:
        return self._unique_chat_title(EMPTY_CURRENT_CHAT_TITLE)

    @staticmethod
    def _cd_command_title(question: str) -> str:
        text = str(question or "").strip()
        match = re.match(r"^(?:cd|chdir)\s+(.+)$", text, flags=re.IGNORECASE)
        if not match:
            return ""
        target = match.group(1).strip()
        if target.lower().startswith("/d "):
            target = target[3:].strip()
        target = target.strip().strip('"').strip("'").strip()
        if not target:
            return ""
        try:
            name = Path(target).name
            if not name and Path(target).anchor:
                name = Path(target).anchor.rstrip("\\/")
        except Exception:
            name = ""
        return str(name or "").strip()

    def _bump_chat_title_revision(self, chat: dict, source: str, updated_at: float | None = None, title: str | None = None) -> None:
        timestamp = float(updated_at or bindings.app.time.time())
        next_title = str(title if title is not None else chat.get("title") or "").strip() or EMPTY_CURRENT_CHAT_TITLE
        chat["title"] = next_title
        chat["title_manual"] = source == "manual"
        chat["title_source"] = source
        chat["title_updated_at"] = timestamp
        chat["title_revision"] = int(chat.get("title_revision") or 0) + 1

    @staticmethod
    def _title_source_priority(source: str) -> int:
        normalized = str(source or "").strip().lower()
        if normalized == "manual":
            return 2
        if normalized == "auto":
            return 1
        return 0

    def _generate_first_question_title(self, question: str) -> str:
        prompt = str(question or "").strip()
        if not prompt:
            return ""
        for _ in range(3):
            try:
                client = bindings.app.ChatClient(
                    api_key=bindings.app.openrouter_api_key_for_app(),
                    model="doubao-2.0-mini",
                    timeout=15,
                )
                title = client.generate_chat_title(prompt).strip()[:40]
            except Exception:
                title = ""
            if title:
                compact = self._compact_first_question_title(title, 12)
                if re.search(r"(?:是一个|是一种|是指|指的是)", compact):
                    compact = self._compact_first_question_title(prompt, 12)
                return compact or title
        return ""

    def _apply_generated_first_question_title(
        self, chat_id: str, question: str, title: str, expected_title_revision: int
    ) -> None:
        resolved_chat_id = str(chat_id or "").strip()
        if not resolved_chat_id or not title:
            return
        if resolved_chat_id in {self.active_chat_id, self.current_chat_id}:
            chat = self._current_chat_state
        else:
            chat = self._find_archived_chat(resolved_chat_id)
        if not isinstance(chat, dict):
            return
        try:
            expected_revision = int(expected_title_revision)
            current_revision = int(chat.get("title_revision"))
        except (TypeError, ValueError):
            return
        if current_revision != expected_revision:
            return
        if bool(chat.get("title_manual")) or str(chat.get("title_source") or "").strip() == "manual":
            return
        current_title = str(chat.get("title") or "").strip()
        current_source = str(chat.get("title_source") or "").strip()
        if not self._is_default_chat_title(current_title) or current_source not in {"", "default"}:
            return
        if not self._compact_first_question_title(question, 120):
            return
        title_fields = (
            "title", "title_manual", "title_source", "title_updated_at", "title_revision",
            "first_question_auto_title_scheduled",
        )
        missing = object()
        before_title = {key: chat.get(key, missing) for key in title_fields}
        self._bump_chat_title_revision(chat, "auto", title=self._unique_chat_title(title, exclude_chat_id=resolved_chat_id))
        store = getattr(self, "chat_store", None)
        persisted = self._slim_active_chat_state() if chat is self._current_chat_state else chat

        def rollback_generated_title() -> None:
            for key, value in before_title.items():
                if value is missing:
                    chat.pop(key, None)
                else:
                    chat[key] = value
            chat["first_question_auto_title_scheduled"] = False

        try:
            if (
                not bool(getattr(self, "_chat_store_enabled", False))
                or not callable(getattr(store, "upsert_chat", None))
                or not callable(getattr(store, "load_chat", None))
            ):
                raise RuntimeError("Generated title cannot be durably committed")
            store.upsert_chat(persisted)
            committed = store.load_chat(resolved_chat_id, include_execution_steps=False)
            if (
                not isinstance(committed, dict)
                or str(committed.get("id") or "").strip() != resolved_chat_id
                or str(committed.get("title") or "").strip() != str(chat.get("title") or "").strip()
                or str(committed.get("title_source") or "").strip() != "auto"
                or int(committed.get("title_revision") or 0) != int(chat.get("title_revision") or 0)
            ):
                raise RuntimeError("Generated title durable commit could not be confirmed")
        except Exception:
            rollback_generated_title()
            try:
                if callable(getattr(store, "upsert_chat", None)):
                    store.upsert_chat(self._slim_active_chat_state() if chat is self._current_chat_state else chat)
            except Exception:
                pass
            return
        self._upsert_history_row(resolved_chat_id)
        self._push_remote_history_changed(resolved_chat_id)

    def _schedule_first_question_auto_title(self, chat_id: str, question: str) -> None:
        resolved_chat_id = str(chat_id or self.active_chat_id or self.current_chat_id or "").strip()
        normalized_question = str(question or "").strip()
        if not resolved_chat_id or not normalized_question:
            return
        if resolved_chat_id in {self.active_chat_id, self.current_chat_id}:
            chat = self._current_chat_state
        else:
            chat = self._find_archived_chat(resolved_chat_id)
        if not isinstance(chat, dict):
            return
        if bool(chat.get("title_manual")) or str(chat.get("title_source") or "").strip() == "manual":
            return
        if not self._is_default_chat_title(str(chat.get("title") or "")):
            return
        expected_title_revision = int(chat.get("title_revision") or 0)
        if chat.get("model") == AGY_MODEL_ID:
            title = self._compact_first_question_title(normalized_question, 18)
            if title:
                self._apply_generated_first_question_title(
                    resolved_chat_id, normalized_question, title, expected_title_revision
                )
            return
        command_title = self._cd_command_title(normalized_question)
        if command_title:
            self._apply_generated_first_question_title(
                resolved_chat_id,
                normalized_question,
                command_title,
                expected_title_revision,
            )
            return
        if chat.get("first_question_auto_title_scheduled"):
            return
        chat["first_question_auto_title_scheduled"] = True

        def _reset_schedule() -> None:
            target = self._current_chat_state if resolved_chat_id in {self.active_chat_id, self.current_chat_id} else self._find_archived_chat(resolved_chat_id)
            if isinstance(target, dict) and int(target.get("title_revision") or 0) == expected_title_revision:
                target["first_question_auto_title_scheduled"] = False

        def _worker() -> None:
            title = self._generate_first_question_title(normalized_question)
            if not title:
                bindings.app.wx_call_after_if_alive(_reset_schedule)
                return
            bindings.app.wx_call_after_if_alive(
                self._apply_generated_first_question_title,
                resolved_chat_id,
                normalized_question,
                title,
                expected_title_revision,
            )

        bindings.app.threading.Thread(target=_worker, daemon=True).start()

    def _sort_archived_chats(self):
        self.archived_chats.sort(
            key=lambda c: (
                0 if c.get("pinned") else 1,
                -float(c.get("updated_at") or c.get("created_at") or 0.0),
                -float(c.get("created_at") or 0.0),
            )
        )

    def _current_history_id(self) -> str:
        return str(
            self.active_chat_id
            or self.current_chat_id
            or (self._current_chat_state.get("id") if isinstance(self._current_chat_state, dict) else "")
            or ""
        ).strip()

    def _current_history_title(self) -> str:
        title = str((self._current_chat_state or {}).get("title") or "").strip()
        title_manual = bool((self._current_chat_state or {}).get("title_manual"))
        if title_manual and title:
            return title
        if self._is_default_chat_title(title):
            return EMPTY_CURRENT_CHAT_TITLE
        return title or EMPTY_CURRENT_CHAT_TITLE

    def _append_terminal_history_filter_item(self, menu):
        item = menu.AppendCheckItem(int(self._terminal_history_filter_menu_id), "隐藏终端聊天")
        item.Check(bool(getattr(self, "hide_terminal_chats", False)))
        return item

    def _toggle_terminal_history_filter(self, event):
        self.hide_terminal_chats = event.IsChecked()
        self._terminal_history_filter_item.Check(self.hide_terminal_chats)
        self._save_state(persist_chat_history=False)
        self._refresh_history()

    def _history_chat_is_visible(self, chat_id, chat):
        # Native discovery creates its own deterministic owner ID. A replay
        # onto an existing MC chat retains the MC owner and adds the mapping.
        return not getattr(self, "hide_terminal_chats", False) or chat.get("native_identity") != chat_id

    def _history_chats_by_id(self):
        chats = {}
        for chat in self.archived_chats:
            if isinstance(chat, dict) and (chat_id := str(chat.get("id") or "").strip()):
                chats.setdefault(chat_id, chat)
        if current_id := self._current_history_id():
            chats[current_id] = self._current_chat_state or {}
        return chats

    def _visible_history_chats_in_order(self):
        rows = [(chat_id, chat) for chat_id, chat in self._history_chats_by_id().items()
                if self._history_chat_is_visible(chat_id, chat)]
        return sorted(rows, key=self._history_chat_sort_key)

    def _refresh_history(self, keep_id=None):
        current_id = self._current_history_id()
        selected_id = self.history_list_model.selected_id() if hasattr(self, "history_list_model") else ""
        selection_deferred = getattr(self, "_chat_navigation_history_selection_deferred", False)
        target = selected_id if selection_deferred else str(
            keep_id if keep_id is not None else selected_id or self.view_history_id or ""
        ).strip()
        chats = self._visible_history_chats_in_order()
        ids = [chat_id for chat_id, _chat in chats]
        unread_ids = self._unread_history_ids(ids)
        labels = [self._history_chat_label(chat, is_current=chat_id == current_id, unread=chat_id in unread_ids)
                  for chat_id, chat in chats]
        selected_idx = None
        if target in ids:
            selected_idx = ids.index(target)
        elif labels and (not selection_deferred or selected_id):
            selected_idx = 0
        selected_id = ids[selected_idx] if selected_idx is not None and 0 <= selected_idx < len(ids) else ""
        if hasattr(self, "history_list_model"):
            changed = self.history_list_model.replace_visible_page(list(zip(ids, labels)), selected_id=selected_id)
            self.history_ids = list(self.history_list_model.visible_ids)
        else:
            changed = self._replace_listbox_items_if_changed(self.history_list, labels, selected_idx)
            self.history_ids = ids
            if not changed and selected_idx is not None:
                try:
                    if self.history_list.GetSelection() != selected_idx:
                        self.history_list.SetSelection(selected_idx)
                except Exception:
                    pass
        if changed:
            self._request_listbox_repaint(self.history_list)

    def _unread_history_ids(self, chat_ids: list[str]) -> set[str]:
        lookup = getattr(getattr(self, "chat_store", None), "list_unread_chat_ids", None)
        return lookup(chat_ids, pair_id=self._read_pair_id()) if lookup and chat_ids else set()

    def _history_chat_label(self, chat: dict, *, is_current: bool = False, unread: bool = False) -> str:
        title = self._current_history_title() if is_current else str((chat or {}).get("title") or "新聊天")
        if self._is_default_chat_title(title):
            title = EMPTY_CURRENT_CHAT_TITLE
        if not is_current and (chat or {}).get("pinned"):
            title = f"[置顶] {title}"
        return f"[未读] {title}" if unread else title

    @staticmethod
    def _history_chat_sort_key(row) -> tuple:
        chat_id, chat = row
        if not isinstance(chat, dict):
            return (9, 0, 0, chat_id)
        pinned_rank = 0 if chat.get("pinned") else 1
        updated = float(chat.get("updated_at") or chat.get("created_at") or 0.0)
        created = float(chat.get("created_at") or 0.0)
        return (pinned_rank, -updated, -created, chat_id)

    def _history_row_for_chat_id(self, chat_id: str) -> tuple[str, str] | None:
        chat_id = str(chat_id or "").strip()
        if not chat_id:
            return None
        is_current = chat_id == self._current_history_id()
        chat = self._current_chat_state if is_current else self._find_archived_chat(chat_id)
        if not isinstance(chat, dict) or not self._history_chat_is_visible(chat_id, chat):
            return None
        unread = chat_id in self._unread_history_ids([chat_id])
        return chat_id, self._history_chat_label(chat, is_current=is_current, unread=unread)

    def _desired_history_index(self, chat_id: str) -> int:
        chat_id = str(chat_id or "").strip()
        ids = list(getattr(self, "history_ids", []) or [])
        if chat_id and chat_id not in ids:
            ids.append(chat_id)
        chats = self._history_chats_by_id()
        target_key = self._history_chat_sort_key((chat_id, chats.get(chat_id)))
        return sum(self._history_chat_sort_key((item_id, chats.get(item_id))) < target_key for item_id in ids)

    def _upsert_history_row(self, chat_id: str, *, allow_reorder: bool = True, message_fact: bool = False) -> bool:
        if not hasattr(self, "history_list_model"):
            self._refresh_history(chat_id)
            return True
        row = self._history_row_for_chat_id(chat_id)
        if row is None:
            if chat_id in self.history_list_model.labels_by_id:
                self._refresh_history()
                return True
            return False
        item_id, label = row
        selected_id = self.history_list_model.selected_id()
        changed = False
        if item_id not in self.history_list_model.labels_by_id:
            index = self._desired_history_index(item_id) if allow_reorder else len(self.history_ids)
            changed = self.history_list_model.insert(item_id, label, index)
        else:
            changed = self.history_list_model.update_label(item_id, label)
            if allow_reorder and (message_fact or not (self._navigation_quiet_active() or self._primary_navigation_control_is_recently_active())):
                changed = self.history_list_model.move(item_id, self._desired_history_index(item_id)) or changed
            elif allow_reorder:
                self._pending_history_reorder = True
                self._schedule_idle_ui_refresh()
        self.history_ids = list(self.history_list_model.visible_ids)
        if selected_id:
            self.history_list_model.set_selection_by_id(selected_id)
        if changed:
            self._request_listbox_repaint(self.history_list)
        return changed

    def _select_history_row_if_present(self, chat_id: str) -> bool:
        chat_id = str(chat_id or "").strip()
        if not chat_id:
            return False
        if hasattr(self, "history_list_model"):
            return self.history_list_model.set_selection_by_id(chat_id)
        try:
            idx = list(getattr(self, "history_ids", []) or []).index(chat_id)
        except ValueError:
            return False
        try:
            if self.history_list.GetSelection() != idx:
                self.history_list.SetSelection(idx)
        except Exception:
            return False
        return True

    def _refresh_visible_history_chat(self, chat_id: str) -> None:
        if self.view_mode != "history":
            return
        if str(self.view_history_id or "").strip() != str(chat_id or "").strip():
            return
        if self._background_ui_mutations_blocked():
            target_id = str(chat_id or "").strip()
            if target_id:
                self._background_history_dirty_ids.add(target_id)
            self._mark_background_answer_list_dirty()
            return
        self._refresh_history(chat_id)
        self._refresh_answer_list_preserving_selection(refresh_execution=self._detail_panel_mode() != "execution")

    def _ensure_active_chat_id(self) -> str:
        if not self.active_chat_id:
            self.active_chat_id = str(self.current_chat_id or "").strip() or str(uuid.uuid4())
        return self.active_chat_id

    def _defer_codex_state_save(self) -> None:
        active_idx = self._active_turn_index_value()
        if active_idx >= 0:
            self._mark_chat_turns_dirty(start_index=active_idx)
        self._codex_background_flush_dirty = True
        if getattr(self, "_codex_background_flush_scheduled", False):
            return
        self._codex_background_flush_scheduled = True
        timer = self._call_later_if_alive(CODEX_BACKGROUND_FLUSH_DELAY_MS, self._flush_codex_background_updates)
        if timer is None and not self._call_after_if_alive(self._flush_codex_background_updates):
            self._flush_codex_background_updates()

    def _flush_codex_background_updates(self) -> None:
        self._codex_background_flush_scheduled = False
        if getattr(self, "_close_started", False):
            return
        if not getattr(self, "_codex_background_flush_dirty", False):
            return
        if getattr(self, "_kimi_execution_write_counts", None):
            return
        self._codex_background_flush_dirty = False
        self._save_state()
        self._flush_pending_remote_finals()

    def _defer_chat_state_save(self) -> None:
        self._chat_state_flush_dirty = True
        if getattr(self, "_chat_state_flush_scheduled", False):
            return
        self._chat_state_flush_scheduled = True
        timer = self._call_later_if_alive(300, self._flush_chat_state_save)
        if timer is None and not self._call_after_if_alive(self._flush_chat_state_save):
            self._flush_chat_state_save()

    def _flush_chat_state_save(self) -> None:
        self._chat_state_flush_scheduled = False
        if getattr(self, "_close_started", False):
            return
        if not getattr(self, "_chat_state_flush_dirty", False):
            return
        if getattr(self, "_kimi_execution_write_counts", None) or getattr(self, "_agy_terminal_writes", 0):
            return
        self._chat_state_flush_dirty = False
        self._save_state()
        self._flush_pending_remote_finals()

    def _load_chat_as_current(self, chat: dict) -> None:
        if getattr(self, "_chat_store_enabled", False):
            self._current_chat_state = dict(chat or {})
            self.active_session_turns = list((chat or {}).get("turns") or [])
            self._current_chat_state["turns"] = self.active_session_turns
            execution_steps = (chat or {}).get("execution_steps")
            if isinstance(execution_steps, list):
                self._current_chat_state["execution_steps"] = list(execution_steps)
        else:
            self._current_chat_state = copy.deepcopy(chat or {})
            self.active_session_turns = copy.deepcopy((chat or {}).get("turns") or [])
        title_manual = self._current_chat_state.get("title_manual")
        if isinstance(title_manual, str):
            title_manual = title_manual.strip().lower() in {"1", "true", "yes", "y", "on"}
        else:
            title_manual = bool(title_manual)
        self._current_chat_state["title_manual"] = title_manual
        self.current_chat_id = str(chat.get("id") or "").strip() or str(uuid.uuid4())
        self.active_chat_id = self.current_chat_id
        self._current_chat_state["turns"] = self.active_session_turns
        self.active_session_started_at = float(chat.get("created_at") or bindings.app.time.time())
        self.active_turn_idx = len(self.active_session_turns) - 1
        self.selected_model = self._model_for_chat_selection(self._current_chat_state) or STARTUP_DEFAULT_MODEL_ID
        self._current_chat_state["model"] = self.selected_model
        self.model_combo.SetValue(bindings.app.model_display_name(self.selected_model))
        self._sync_codex_speed_combo_from_chat(self._current_chat_state)
        self.active_codex_thread_id = ""
        self.active_codex_turn_id = ""
        self.active_codex_turn_active = False
        self.active_codex_pending_prompt = ""
        self.active_codex_pending_request = None
        self.active_codex_request_queue = []
        self.active_codex_thread_flags = []
        self.active_codex_latest_assistant_text = ""
        self.active_codex_latest_assistant_phase = ""
        self._reset_active_kimi_session_state()
        self._reset_answer_visible_row_limit()
        self._save_state()

    def _summarize_title(self, turns, api_key: str) -> str:
        if not turns:
            return "新聊天"
        title = self._summarize_last_turn_locally(turns)
        return title.strip()[:40] or "新聊天"

    def _title_source_turns(self, turns):
        first_question = self._title_source_question(turns)
        if not first_question:
            return []
        return [{"question": first_question, "answer_md": "", "model": ""}]

    def _build_title_transcript(self, turns) -> str:
        first_question = self._title_source_question(turns)
        if not first_question:
            return ""
        return f"首轮用户提问：{first_question}".strip()

    def _summarize_last_turn_locally(self, turns) -> str:
        first_question = self._title_source_question(turns)
        title = self._compact_first_question_title(first_question, 18)
        return title or "新聊天"

    def _summarize_recent_topic(self, turns, api_key: str) -> str:
        if not turns:
            return EMPTY_CURRENT_CHAT_TITLE
        return self._summarize_last_turn_locally(turns)

    def _apply_archived_title(self, chat_id: str, title: str) -> None:
        if not title:
            return
        c = self._find_archived_chat(chat_id)
        if not c:
            return
        if c.get("title_manual"):
            return
        c["title"] = title.strip()[:40] or c.get("title") or "新聊天"
        self._save_state()
        self._refresh_history(chat_id)

    def _schedule_async_archive_rename(self, chat_id: str, turns_snapshot: list[dict], model_snapshot: str) -> None:
        return

    def _archive_active_session(
        self,
        quick_title: bool = False,
        schedule_async_rename: bool = False,
        save_after_archive: bool = True,
        flush_before_archive: bool = True,
        refresh_lifecycle_after_archive: bool = True,
    ):
        if flush_before_archive:
            self._flush_chat_state_save()
        owner_id = str(self.active_chat_id or self.current_chat_id or (self._current_chat_state or {}).get("id") or "").strip()
        if not self.active_session_turns and not owner_id:
            return None
        use_chat_store = bool(getattr(self, "_chat_store_enabled", False) and getattr(self, "chat_store", None) is not None)
        turn_count = len(self.active_session_turns)
        if use_chat_store:
            self._defer_chat_state_save()
            turns_snapshot = list(self.active_session_turns)
        else:
            turns_snapshot = copy.deepcopy(self.active_session_turns)
        model_snapshot = self._model_for_chat_selection(self._current_chat_state) or STARTUP_DEFAULT_MODEL_ID
        api_key = bindings.app.openrouter_api_key_for_app() if (not quick_title) else ""
        title_manual = self._current_chat_state.get("title_manual")
        if isinstance(title_manual, str):
            title_manual = title_manual.strip().lower() in {"1", "true", "yes", "y", "on"}
        else:
            title_manual = bool(title_manual)
        title = str(self._current_chat_state.get("title") or "").strip()
        if not title:
            title = self._next_default_chat_title()
        created = self.active_session_started_at or bindings.app.time.time()
        archived_id = next(
            (
                normalized
                for candidate in (self.active_chat_id, self.current_chat_id)
                if (normalized := str(candidate or "").strip())
            ),
            str(uuid.uuid4()),
        )
        archived = {
            "id": archived_id,
            "title": title,
            "title_manual": title_manual,
            "title_source": str(self._current_chat_state.get("title_source") or ("manual" if title_manual else "default")),
            "title_updated_at": float(self._current_chat_state.get("title_updated_at") or bindings.app.time.time()),
            "title_revision": int(self._current_chat_state.get("title_revision") or 1),
            "pinned": False,
            "model": model_snapshot,
            "created_at": created,
            "updated_at": float(self._current_chat_state.get("updated_at") or created or bindings.app.time.time()),
            "turn_count": turn_count,
            "source_chat_id": self.active_chat_id or self.current_chat_id or "",
            "openclaw_session_key": self.active_openclaw_session_key,
            "openclaw_session_id": self.active_openclaw_session_id,
            "openclaw_session_file": self.active_openclaw_session_file,
            "openclaw_sync_offset": self.active_openclaw_sync_offset,
            "openclaw_last_event_id": self.active_openclaw_last_event_id,
            "openclaw_last_synced_at": self.active_openclaw_last_synced_at,
            "codex_thread_id": self.active_codex_thread_id,
            "codex_turn_id": self.active_codex_turn_id,
            "codex_context_generation": int(self._current_chat_state.get("codex_context_generation") or 0),
            "codex_turn_active": self.active_codex_turn_active,
            "codex_pending_prompt": self.active_codex_pending_prompt,
            "codex_pending_request": self.active_codex_pending_request,
            "codex_request_queue": copy.deepcopy(self.active_codex_request_queue),
            "codex_thread_flags": copy.deepcopy(self.active_codex_thread_flags),
            "codex_latest_assistant_text": self.active_codex_latest_assistant_text,
            "codex_latest_assistant_phase": self.active_codex_latest_assistant_phase,
            "codex_service_tier": self._codex_service_tier_for_chat(self._current_chat_state),
            "kimi_session_id": self.active_kimi_session_id,
            "kimi_turn_id": self.active_kimi_turn_id,
            "kimi_turn_active": self.active_kimi_turn_active,
            "kimi_pending_prompt": self.active_kimi_pending_prompt,
            "kimi_request_queue": copy.deepcopy(self.active_kimi_request_queue),
            "claudecode_session_id": self.active_claudecode_session_id,
            "detail_panel_mode": str(self._current_chat_state.get("detail_panel_mode") or "").strip() or "answers",
            "execution_steps": (
                list(self._current_chat_state.get("execution_steps"))
                if use_chat_store and isinstance(self._current_chat_state.get("execution_steps"), list)
                else (
                    copy.deepcopy(self._current_chat_state.get("execution_steps"))
                    if isinstance(self._current_chat_state.get("execution_steps"), list)
                    else []
                )
            ),
        }
        archived["turns"] = turns_snapshot
        for field in ("kimi_interaction_errors", "kimi_interaction_error"):
            if field in self._current_chat_state:
                archived[field] = copy.deepcopy(self._current_chat_state[field])
        for field in (*AGY_CHAT_FIELDS, *NATIVE_CHAT_FIELDS, *NATIVE_USAGE_FIELDS, "codex_account_id", "codex_context_generation",
                      "codex_terminal_thread", "codex_terminal_host_id", *CODEX_STORAGE_FIELDS,
                      "kimi_account_id", "kimi_verified_account_id", "kimi_information_generation", "kimi_storage_id"):
            if field in self._current_chat_state:
                archived[field] = copy.deepcopy(self._current_chat_state[field])
        existing_matches = [
            (idx, existing)
            for idx, existing in enumerate(self.archived_chats)
            if isinstance(existing, dict) and str(existing.get("id") or "").strip() == archived_id
        ]
        if not existing_matches:
            self.archived_chats.append(archived)
        else:
            existing_index, existing = existing_matches[0]
            preserved = dict(existing)
            preserved.update(archived)
            archived = preserved
            archived["pinned"] = bool(existing.get("pinned"))
            self.archived_chats = [
                archived if idx == existing_index else existing_chat
                for idx, existing_chat in enumerate(self.archived_chats)
                if idx == existing_index
                or not (
                    isinstance(existing_chat, dict)
                    and str(existing_chat.get("id") or "").strip() == archived_id
                )
            ]
        if not use_chat_store:
            self._mark_chat_turns_dirty(archived_id, 0)
        self._sort_archived_chats()

        self.active_session_turns = []
        self._current_chat_state["turns"] = self.active_session_turns
        self.active_session_started_at = 0.0
        self.active_turn_idx = -1
        self.active_chat_id = ""
        self.active_openclaw_session_key = DEFAULT_OPENCLAW_SESSION_KEY
        self.active_openclaw_session_id = ""
        self.active_openclaw_session_file = ""
        self.active_openclaw_sync_offset = 0
        self.active_openclaw_last_event_id = ""
        self.active_openclaw_last_synced_at = 0.0
        self._reset_active_codex_session_state()
        self._reset_active_kimi_session_state()
        self.active_claudecode_session_id = ""
        self._clear_active_claudecode_client()
        self.view_mode = "active"
        self.view_history_id = None
        if save_after_archive:
            self._save_state()
        if refresh_lifecycle_after_archive:
            self._refresh_openclaw_sync_lifecycle()
        if schedule_async_rename:
            self._schedule_async_archive_rename(str(archived["id"]), turns_snapshot, model_snapshot)
        return archived

    def _read_pair_id(self):
        return str(getattr(getattr(getattr(self, "_remote_nats_transport", None), "subjects", None), "pair_id", None) or getattr(self, "remote_control_pair_id", "default"))

    def _chat_read_state(self, chat_id):
        store = getattr(self, "chat_store", None)
        if not chat_id or store is None or not hasattr(store, "get_chat_read_state"):
            return None
        try:
            return store.get_chat_read_state(chat_id, pair_id=self._read_pair_id())
        except ValueError:
            return None

    def _selected_read_target(self, index=None):
        if getattr(self, "_chat_navigation_answer_rows_deferred", False):
            return None
        index = self.answer_list.GetSelection() if index is None else index
        if index < 0 or index >= len(self.answer_meta) or self.answer_meta[index][0] != "answer":
            return None
        owner = self._visible_answer_owner_chat_id()
        store = getattr(self, "chat_store", None)
        if store is None or not hasattr(store, "readable_answer"):
            return None
        target = store.readable_answer(owner, turn_index=self.answer_meta[index][1])
        if target:
            return {**target, "chat_id": owner}
        turn_index = self.answer_meta[index][1]
        turns = self._get_view_turns()
        if 0 <= turn_index < len(turns):
            turn = turns[turn_index]
            if turn.get("request_status") == "done" and str(turn.get("answer_md") or "").strip():
                state = self._chat_read_state(owner)
                if state:
                    return {"chat_id": owner, "generation": state["generation"],
                            "turn_index": turn_index, "frozen_turn": copy.deepcopy(turn)}
        return None

    def _on_chat_read_changed(self, state):
        if getattr(self, "_closing", False):
            return
        self._invalidate_remote_history_list_cache()
        self._invalidate_remote_state_cache()
        owner = str(state.get("chat_id") or "")
        self._upsert_history_row(owner, allow_reorder=False)

    def _confirm_answer_read(self, target):
        if not target:
            return
        try:
            if "frozen_turn" in target:
                identity = self.chat_store.readable_answer_for_turn(target["chat_id"],
                    turn_index=target["turn_index"], turn=target["frozen_turn"], generation=target["generation"])
                if identity:
                    target = {**identity, "chat_id": target["chat_id"],
                              "pair_id": target.get("pair_id") or self._read_pair_id()}
                else:
                    pending = getattr(self, "_pending_answer_reads", None)
                    if pending is None:
                        pending = self._pending_answer_reads = {}
                    pending[(target["chat_id"], target["generation"], target["turn_index"])] = {
                        **copy.deepcopy(target), "pair_id": self._read_pair_id()}
                    self._defer_chat_state_save()
                    return
            state = self.chat_store.mark_chat_read(pair_id=target.get("pair_id") or self._read_pair_id(), chat_id=target["chat_id"],
                generation=target["generation"], message_id=target["message_id"],
                answer_seq=target["answer_seq"], operation_id=uuid.uuid4().hex)
            self._on_chat_read_changed(state)
            transport = getattr(self, "_remote_nats_transport", None)
            loop = getattr(transport, "_loop", None)
            if loop is not None and loop.is_running():
                asyncio.run_coroutine_threadsafe(transport.drain_outbox(), loop)
        except (ValueError, RuntimeError) as exc:
            self.SetStatusText(f"已读确认失败：{exc}")

    def _flush_pending_answer_reads(self, chat_id):
        pending = getattr(self, "_pending_answer_reads", {})
        for key, frozen in list(pending.items()):
            if frozen["chat_id"] != chat_id:
                continue
            identity = self.chat_store.readable_answer_for_turn(chat_id,
                turn_index=frozen["turn_index"], turn=frozen["frozen_turn"],
                generation=frozen["generation"])
            # Persistence completed. A missing/mismatched target is stale, not
            # permission to read a replacement or a later answer.
            pending.pop(key, None)
            if identity:
                self._confirm_answer_read({**identity, "chat_id": chat_id,
                                           "pair_id": frozen["pair_id"]})

    def _invalidate_answer_mouse_gesture(self):
        self._answer_mouse_press = None
        self._answer_mouse_click = None

    def _on_answer_mouse_down(self, event):
        self._invalidate_answer_mouse_gesture()
        if getattr(self, "_chat_navigation_answer_rows_deferred", False):
            self._cancel_chat_navigation_focus()
            return
        self._answer_mouse_press = self._answer_mouse_row(event)
        event.Skip()

    def _answer_mouse_row(self, event):
        index = self.answer_list.HitTest(event.GetPosition())
        if 0 <= index < len(self.answer_meta):
            return self._visible_answer_owner_chat_id(), index, self.answer_meta[index]
        return None

    def _on_answer_mouse_read(self, event):
        pressed = getattr(self, "_answer_mouse_press", None)
        self._answer_mouse_press = None
        self._answer_mouse_click = None
        event.Skip()  # Let the native list box release mouse capture.
        if getattr(self, "_chat_navigation_answer_rows_deferred", False):
            self._cancel_chat_navigation_focus()
            return
        if pressed is None or pressed != self._answer_mouse_row(event):
            return
        self._answer_mouse_click = pressed
        self._confirm_answer_read(self._selected_read_target(pressed[1]))

    def _on_answer_mouse_double_click(self, event):
        clicked = getattr(self, "_answer_mouse_click", None)
        self._answer_mouse_click = None
        if getattr(self, "_chat_navigation_answer_rows_deferred", False):
            self._cancel_chat_navigation_focus()
            return
        if clicked is not None and clicked == self._answer_mouse_row(event):
            # Handle activation with the validated row before native processing.
            if self.answer_list.GetSelection() != clicked[1]:
                self.answer_list.SetSelection(clicked[1])
            self._on_answer_activate(event)

    def _on_history_key_down(self, event):
        if self._on_any_key_down_escape_minimize(event):
            return
        if self._handle_input_focus_space_shortcut(event):
            return
        self._touch_navigation_quiet_window(event)
        if self._handle_ctrl_history_navigation(event):
            return
        if self._handle_primary_tab_navigation(event):
            return
        key = event.GetKeyCode()
        if (getattr(self, "_chat_navigation_history_selection_deferred", False)
                and key not in (wx.WXK_CONTROL, wx.WXK_SHIFT, wx.WXK_ALT)):
            self._cancel_chat_navigation_focus()
            self._flush_chat_navigation_history_selection()
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            self._activate_selected_history()
            return
        if key == wx.WXK_F2:
            self._history_rename(None)
            return
        if self._is_context_menu_key(key):
            self._show_history_menu()
            return
        if key == wx.WXK_DELETE:
            self._history_delete(None)
            return
        event.Skip()

    def _on_history_char(self, event):
        key = event.GetKeyCode()
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            self._activate_selected_history()
            return
        event.Skip()

    def _on_history_selected(self, event):
        # A native selection event expresses the user's choice of a row.
        if getattr(self, "_chat_navigation_history_selection_deferred", False):
            self._chat_navigation_history_selection_deferred = False
            self._cancel_chat_navigation_focus()
        event.Skip()

    def _on_history_mouse_down(self, event):
        if getattr(self, "_chat_navigation_history_selection_deferred", False):
            if self.history_list.HitTest(event.GetPosition()) != wx.NOT_FOUND:
                self._chat_navigation_history_selection_deferred = False
            self._cancel_chat_navigation_focus()
        event.Skip()

    def _on_history_context(self, event):
        position = event.GetPosition()
        if position == wx.DefaultPosition:
            self._cancel_chat_navigation_focus()
            self._flush_chat_navigation_history_selection()
            self._show_history_menu()
            return
        index = self.history_list.HitTest(self.history_list.ScreenToClient(position))
        chat_id = self.history_ids[index] if 0 <= index < len(self.history_ids) else None
        self._cancel_chat_navigation_focus()
        if chat_id:
            self._show_history_menu(chat_id)

    def _model_for_chat_selection(self, chat: dict | None) -> str:
        model = bindings.app.normalize_model_id(str((chat or {}).get("model") or "").strip(), default="")
        if model:
            return model
        turns = (chat or {}).get("turns")
        if isinstance(turns, list):
            for turn in reversed(turns):
                turn_model = bindings.app.normalize_model_id(str((turn or {}).get("model") or "").strip(), default="")
                if turn_model:
                    return turn_model
        return ""

    def _apply_selected_chat_model_to_combo(self, chat: dict | None) -> None:
        model = self._model_for_chat_selection(chat) or STARTUP_DEFAULT_MODEL_ID
        if isinstance(chat, dict) and chat.get("model") != model:
            chat["model"] = model
            self._defer_chat_state_save()
        display = bindings.app.model_display_name(model)
        self.selected_model = model
        if bindings.app.threading.current_thread() is bindings.app.threading.main_thread() and self.model_combo.GetValue() != display:
            self.model_combo.SetValue(display)
        if bindings.app.threading.current_thread() is bindings.app.threading.main_thread():
            self._sync_codex_speed_combo_from_chat(chat)

    def _show_history_chat(self, chat_id: str, *, focus_answer_list: bool = True,
                           preserve_panel=False, sync_history_selection=True) -> bool:
        self._request_native_history_refresh()
        selected_id = str(chat_id or "").strip()
        if not selected_id:
            return False
        if sync_history_selection and getattr(self, "_chat_navigation_history_selection_deferred", False):
            self._chat_navigation_history_selection_deferred = False
            self._cancel_chat_navigation_focus()
        self._flush_relevant_execution_deltas_for_switch()
        current_state_id = (
            str((self._current_chat_state or {}).get("id") or "").strip()
            if isinstance(getattr(self, "_current_chat_state", None), dict)
            else ""
        )
        current_ids = {
            str(self.active_chat_id or "").strip(),
            str(self.current_chat_id or "").strip(),
            current_state_id,
        }
        current_ids.discard("")
        if selected_id in current_ids:
            self.view_mode = "active"
            self.view_history_id = None
            self._apply_selected_chat_model_to_combo(self._current_chat_state)
            if native_status := self._current_chat_state.get("native_history_status"):
                self.SetStatusText(native_status)
            if preserve_panel:
                self._render_answer_list(preserve_panel=True)
            else:
                self._render_answer_list()
            if sync_history_selection and not self._select_history_row_if_present(selected_id):
                self._mark_history_list_dirty(selected_id)
            self._defer_chat_state_save()
            if focus_answer_list:
                if not self._focus_latest_answer_immediately():
                    self.answer_list.SetFocus()
            return True
        chat = self._hydrate_chat_from_store(
            self._find_archived_chat(selected_id),
            include_execution_steps=False,
        )
        if not chat:
            return False
        self.view_mode = "history"
        self.view_history_id = selected_id
        self._apply_selected_chat_model_to_combo(chat)
        if preserve_panel:
            self._render_answer_list(preserve_panel=True)
        else:
            self._render_answer_list()
        if sync_history_selection and not self._select_history_row_if_present(selected_id):
            self._mark_history_list_dirty(selected_id)
        self._defer_chat_state_save()
        self.SetStatusText(str(chat.get("native_history_status") or "已切换到历史聊天"))
        if focus_answer_list:
            if not self._focus_latest_answer_immediately():
                self.answer_list.SetFocus()
        self._call_after_if_alive(self._show_pending_kimi_interaction)
        return True

    def _enter_history_view(self, chat_id: str, *, focus_answer_list: bool = True) -> bool:
        return self._show_history_chat(chat_id, focus_answer_list=focus_answer_list)

    def _enter_active_view(self, *, focus_answer_list: bool = False, keep_history_id=None) -> None:
        self.view_mode = "active"
        self.view_history_id = None
        self._render_answer_list()
        if keep_history_id:
            self._select_history_row_if_present(str(keep_history_id))
        if focus_answer_list:
            if not self._focus_latest_answer_immediately():
                self.answer_list.SetFocus()

    def _focus_latest_answer_immediately(self, *, include_question=False, reveal_answers=False) -> bool:
        if not hasattr(self, "answer_list"):
            return False
        metas = getattr(self, "answer_meta", []) or []
        index = self._latest_answer_message_row(metas) if include_question else next(
            (i for i in range(len(metas) - 1, -1, -1) if metas[i][0] in {"answer", "partial_answer"}), None
        )
        if index is not None:
            try:
                if self.answer_list.GetSelection() != index:
                    self.answer_list.SetSelection(index)
                if reveal_answers:
                    # Revealing the list can move native focus. Select its
                    # destination before changing the panel.
                    self._apply_detail_panel_mode("answers")
                if not self.answer_list.HasFocus():
                    self.answer_list.SetFocus()
                return True
            except Exception:
                return False
        if include_question:
            return False
        try:
            if self.answer_list.GetCount() > 0:
                self.answer_list.SetSelection(self.answer_list.GetCount() - 1)
            self.answer_list.SetFocus()
            return True
        except Exception:
            return False

    def _activate_selected_history(self) -> bool:
        if getattr(self, "_chat_navigation_history_selection_deferred", False):
            self._cancel_chat_navigation_focus()
            self._flush_chat_navigation_history_selection()
        if self.history_list.GetCount() == 0:
            return False
        idx = self.history_list.GetSelection()
        if idx == wx.NOT_FOUND:
            idx = 0
            self.history_list.SetSelection(0)
        if idx < 0 or idx >= len(self.history_ids):
            return False

        return self._show_history_chat(self.history_ids[idx])

    def _load_history_selection(self):
        return self._activate_selected_history()

    def _find_archived_chat(self, chat_id):
        normalized = str(chat_id or "").strip()
        if not normalized:
            return None
        for c in self.archived_chats:
            if isinstance(c, dict) and str(c.get("id") or "").strip() == normalized:
                return c
        return None

    def _get_all_chat_ids_in_order(self):
        """Return the same pinned and recency order shown by the history list."""
        return [chat_id for chat_id, _chat in self._visible_history_chats_in_order()]

    def _adjacent_history_chat_id(self, direction: int):
        """Get the adjacent chat ID in the specified direction (1 for next, -1 for previous)."""
        all_ids = self._get_all_chat_ids_in_order()
        anchor_id = str(self.view_history_id or "").strip() if self.view_mode == "history" else ""
        if not anchor_id:
            anchor_id = self.current_chat_id or self.active_chat_id
        if hasattr(self, "history_list") and (not anchor_id or (
            getattr(self, "hide_terminal_chats", False) and anchor_id not in all_ids
        )):
            anchor_id = self._selected_history_chat_id() or ""
        if len(all_ids) <= 1 or anchor_id not in all_ids:
            return None
        current_idx = all_ids.index(anchor_id)
        target_idx = current_idx + direction
        if target_idx < 0 or target_idx >= len(all_ids):
            return None
        return all_ids[target_idx]

    def _navigate_history_chats(self, direction: int) -> bool:
        self._release_execution_focus_lease()
        chat_id = self._adjacent_history_chat_id(direction)
        chat = self._history_chat_for_id(chat_id) if chat_id else None
        if chat is None:
            return False
        focus = wx.Window.FindFocus()
        title = str(chat.get("title") or "新聊天")
        self._cancel_chat_navigation_focus(stop_speech=False)
        self._invalidate_answer_mouse_gesture()
        self._chat_navigation_answer_rows_deferred = True
        self._chat_navigation_history_selection_deferred = True
        self._announce_chat_navigation(chat_id, title, focus)
        if not self._show_history_chat(
            chat_id, focus_answer_list=False, preserve_panel=True,
            sync_history_selection=False,
        ):
            self._cancel_chat_navigation_focus()
            return False
        return True

    def _switch_current_chat(self, chat_id: str) -> bool:
        """Switch to a different chat."""
        chat_id = str(chat_id or "").strip()
        if chat_id == str(self.current_chat_id or "").strip():
            return True
        self._flush_relevant_execution_deltas_for_switch()
        chat = self._hydrate_chat_from_store(self._find_archived_chat(chat_id), include_execution_steps=False)
        if not chat:
            return False
        previous_interaction_error = self._current_chat_state.get("kimi_interaction_error")
        # Empty chats are durable owners too.
        if str(self.active_chat_id or self.current_chat_id or "").strip():
            self._archive_active_session(quick_title=True, schedule_async_rename=True)
        # Load the selected chat
        turns = chat.get("turns") or []
        use_chat_store = bool(getattr(self, "_chat_store_enabled", False) and getattr(self, "chat_store", None) is not None)
        self.active_session_turns = list(turns) if use_chat_store else copy.deepcopy(turns)
        self.active_session_started_at = float(chat.get("created_at") or bindings.app.time.time())
        self.active_chat_id = str(chat.get("source_chat_id") or chat.get("id") or "").strip() or str(uuid.uuid4())
        self.active_openclaw_session_key = str(chat.get("openclaw_session_key") or DEFAULT_OPENCLAW_SESSION_KEY).strip() or DEFAULT_OPENCLAW_SESSION_KEY
        self.active_openclaw_session_id = str(chat.get("openclaw_session_id") or "").strip()
        self.active_openclaw_session_file = str(chat.get("openclaw_session_file") or "").strip()
        try:
            self.active_openclaw_sync_offset = max(int(chat.get("openclaw_sync_offset") or 0), 0)
        except Exception:
            self.active_openclaw_sync_offset = 0
        self.active_openclaw_last_event_id = str(chat.get("openclaw_last_event_id") or "").strip()
        try:
            self.active_openclaw_last_synced_at = float(chat.get("openclaw_last_synced_at") or 0.0)
        except Exception:
            self.active_openclaw_last_synced_at = 0.0
        self.active_codex_thread_id = str(chat.get("codex_thread_id") or "").strip()
        self.active_codex_turn_id = str(chat.get("codex_turn_id") or "").strip()
        self.active_codex_turn_active = bool(chat.get("codex_turn_active", False))
        self.active_codex_pending_prompt = str(chat.get("codex_pending_prompt") or "").strip()
        pending_request = chat.get("codex_pending_request")
        self.active_codex_pending_request = pending_request if isinstance(pending_request, dict) else None
        request_queue = chat.get("codex_request_queue")
        self.active_codex_request_queue = request_queue if isinstance(request_queue, list) else []
        thread_flags = chat.get("codex_thread_flags")
        self.active_codex_thread_flags = thread_flags if isinstance(thread_flags, list) else []
        self.active_codex_latest_assistant_text = str(chat.get("codex_latest_assistant_text") or "").strip()
        self.active_codex_latest_assistant_phase = str(chat.get("codex_latest_assistant_phase") or "").strip()
        self.active_kimi_session_id = str(chat.get("kimi_session_id") or "").strip()
        self.active_kimi_turn_id = str(chat.get("kimi_turn_id") or "").strip()
        self.active_kimi_turn_active = bool(chat.get("kimi_turn_active", False))
        self.active_kimi_pending_prompt = str(chat.get("kimi_pending_prompt") or "").strip()
        kimi_request_queue = chat.get("kimi_request_queue")
        self.active_kimi_request_queue = kimi_request_queue if isinstance(kimi_request_queue, list) else []
        self.active_claudecode_session_id = str(chat.get("claudecode_session_id") or "").strip()
        self.current_chat_id = chat_id
        if use_chat_store:
            self._current_chat_state = dict(chat)
            self._current_chat_state["turns"] = self.active_session_turns
            if not isinstance(self._current_chat_state.get("execution_steps"), list):
                self._current_chat_state["execution_steps"] = []
        else:
            self._current_chat_state = copy.deepcopy(chat)
        self._prune_kimi_interaction_errors(self._current_chat_state)
        self._rebuild_kimi_runtime_state()
        if (not self.active_openclaw_session_id) and any(is_openclaw_model(str(turn.get("model") or "")) for turn in self.active_session_turns):
            self.active_openclaw_session_id = self._make_openclaw_session_id(self.active_chat_id)
        self.active_turn_idx = len(self.active_session_turns) - 1
        resolved_model = self._model_for_chat_selection(self._current_chat_state) or STARTUP_DEFAULT_MODEL_ID
        self.selected_model = resolved_model
        self._current_chat_state["model"] = resolved_model
        if bindings.app.threading.current_thread() is bindings.app.threading.main_thread():
            self.model_combo.SetValue(bindings.app.model_display_name(self.selected_model))
            self._sync_codex_speed_combo_from_chat(self._current_chat_state)
        # Remove from archived chats since it's now active
        self.archived_chats = [
            c for c in self.archived_chats
            if str((c or {}).get("id") or "").strip() != chat_id
        ]
        self._reset_answer_visible_row_limit()
        self._reset_execution_visible_row_limit()
        self._render_answer_list()
        self._refresh_openclaw_sync_lifecycle()
        self._refresh_history(chat_id)
        self._save_state()
        if self._current_chat_state.get("kimi_interaction_error"):
            self.SetStatusText(str(self._current_chat_state["kimi_interaction_error"]))
        elif previous_interaction_error and self.GetStatusBar().GetStatusText() == previous_interaction_error:
            self.SetStatusText("就绪")
        if self._kimi_interactions:
            self._call_after_if_alive(self._show_pending_kimi_interaction)
        return True

    def _selected_history_chat_id(self):
        idx = self.history_list.GetSelection()
        return self.history_ids[idx] if 0 <= idx < len(self.history_ids) else None

    def _show_history_menu(self, chat_id=None):
        target_id = chat_id or self._selected_history_chat_id()
        c = self._history_chat_for_id(target_id)
        if not c:
            return
        m = wx.Menu()
        i_del = wx.NewIdRef()
        i_pin = wx.NewIdRef()
        i_clr = wx.NewIdRef()
        i_ren = wx.NewIdRef()
        m.Append(i_del, "从历史隐藏聊天")
        m.Append(i_pin, "取消置顶" if c.get("pinned") else "置顶聊天")
        m.Append(i_clr, "隐藏所有非置顶聊天")
        m.Append(i_ren, "重命名聊天")
        m.Bind(wx.EVT_MENU, lambda event: self._history_delete(event, chat_id=target_id), id=i_del)
        m.Bind(wx.EVT_MENU, lambda event: self._history_pin(event, chat_id=target_id), id=i_pin)
        m.Bind(wx.EVT_MENU, self._history_clear_non_pinned, id=i_clr)
        m.Bind(wx.EVT_MENU, lambda event: self._history_rename(event, chat_id=target_id), id=i_ren)
        if self._native_history_reference(c):
            native_delete = m.Append(wx.ID_ANY, "删除原始会话")
            m.Bind(wx.EVT_MENU, lambda _event: self._history_delete_native(target_id), native_delete)
        if c.get("agy_session_id") and c.get("native_title"):
            native_title = m.Append(wx.ID_ANY, "查看 AGY 原生标题")
            m.Bind(wx.EVT_MENU, lambda _event: wx.MessageBox(
                str(c["native_title"]), "AGY 原生标题", wx.OK, self), native_title)
        if command := c.get("native_open_command"):
            copy_command = m.Append(wx.ID_ANY, "复制原生 Codex 打开命令")
            m.Bind(wx.EVT_MENU, lambda _event: self._copy_native_open_command(command), copy_command)
        try:
            self.PopupMenu(m)
        finally:
            m.Destroy()

    def _copy_native_open_command(self, command: str) -> None:
        if wx.TheClipboard.Open():
            try:
                wx.TheClipboard.SetData(wx.TextDataObject(command))
            finally:
                wx.TheClipboard.Close()
            self.SetStatusText("已复制原生 Codex 打开命令")

    def _history_chat_for_id(self, chat_id: str) -> dict | None:
        normalized = str(chat_id or "").strip()
        if not normalized:
            return None
        if normalized in {str(self.active_chat_id or "").strip(), str(self.current_chat_id or "").strip()}:
            if isinstance(getattr(self, "_current_chat_state", None), dict):
                return self._current_chat_state
        return self._find_archived_chat(normalized)

    def _delete_chat_from_store(self, chat_id: str, *, native_id: str = "") -> None:
        store = getattr(self, "chat_store", None)
        if not (getattr(self, "_chat_store_enabled", False) and store is not None):
            return
        delete = getattr(store, "delete_chat", None)
        if callable(delete):
            if native_id:
                delete(chat_id, native_id=native_id)
            else:
                delete(chat_id)

    def _handle_ctrl_history_navigation(self, event) -> bool:
        key = event.GetKeyCode()
        ctrl_down = getattr(event, "ControlDown", None)
        alt_down = getattr(event, "AltDown", None)
        if key not in (wx.WXK_UP, wx.WXK_DOWN):
            return False
        if not (callable(ctrl_down) and ctrl_down()):
            return False
        if callable(alt_down) and alt_down():
            return False
        if bool(getattr(event, "ShiftDown", lambda: False)()):
            return False
        self._handle_vertical_chat_shortcut(-1 if key == wx.WXK_UP else 1)
        return True

    def _open_chat_root(self) -> None:
        try:
            self.input_edit.SetFocus()
        except Exception:
            pass

    def _history_delete(self, _, *, chat_id=None):
        cid = chat_id or self._selected_history_chat_id()
        if not cid or self._history_chat_for_id(cid) is None:
            return
        if self._clear_pending_for(cid):
            self.SetStatusText("正在清空此聊天的上下文，请稍后删除")
            return
        self._flush_chat_state_save()
        if not self._confirm("确定从历史隐藏该聊天吗？原始会话将保留。") or self._clear_pending_for(cid):
            return
        status, result = self._hide_history_chat_ui(cid)
        if status != 200:
            self.SetStatusText(result["message"])

    def _history_delete_native(self, owner):
        status, detail = self._native_delete_target_ui(owner)
        if status != 200:
            self.SetStatusText(detail["message"])
            return
        title = "刷新确认原始会话删除" if detail["manual"] else "删除原始会话"
        dialog = wx.MessageDialog(self, detail["description"], title, wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING)
        dialog.SetYesNoLabels("刷新确认" if detail["manual"] else "永久删除", "取消")
        try:
            confirmed = dialog.ShowModal() == wx.ID_YES
        finally:
            dialog.Destroy()
        if confirmed:
            status, result = self._queue_native_delete_ui(owner, detail["target"], True, local=True)
            self.SetStatusText("正在确认原始会话删除" if status == 202 else result["message"])

    def _discard_deleted_chat_view(self, cid):
        normalized_cid = str(cid or "").strip()
        self.archived_chats = [
            c for c in self.archived_chats
            if str((c or {}).get("id") or "").strip() != normalized_cid
        ]
        if str(self.view_history_id or "").strip() == normalized_cid:
            self.view_mode = "active"
            self.view_history_id = None
        if normalized_cid in {
            str(self.current_chat_id or "").strip(),
            str(self.active_chat_id or "").strip(),
        }:
            self.current_chat_id = ""
            self.active_chat_id = ""
            self.active_session_turns = []
            self._current_chat_state = {"id": "", "turns": self.active_session_turns}

    def _cleanup_chat_detail_pages(self, chat: dict) -> None:
        items = list(chat.get("turns") or []) + list(chat.get("execution_steps") or [])
        for item in items:
            if not isinstance(item, dict):
                continue
            for k in ("question_detail_page_path", "answer_detail_page_path", "detail_page_path"):
                raw = str(item.get(k) or "").strip()
                if not raw:
                    continue
                try:
                    p = Path(raw)
                    if not p.exists():
                        continue
                    # Only remove generated pages under the app detail_pages directory.
                    if not p.resolve().is_relative_to(self.detail_pages_dir.resolve()):
                        continue
                    p.unlink(missing_ok=True)
                except Exception:
                    continue

    def _history_pin(self, _, *, chat_id=None):
        c = self._history_chat_for_id(chat_id or self._selected_history_chat_id())
        if not c:
            return
        c["pinned"] = not bool(c.get("pinned"))
        self._save_state()
        self._refresh_history(c.get("id"))
        self._push_remote_history_changed(c.get("id"))

    def _history_clear_non_pinned(self, _):
        self._flush_chat_state_save()
        if not self._confirm("确定隐藏所有非置顶聊天吗？原始会话将保留。"):
            return
        candidates = {str(c["id"]).strip(): self._history_hide_identity(c)
                      for c in self.archived_chats if not c.get("pinned") and c.get("id")}
        removed = {owner: identity for owner, identity in candidates.items() if identity is not None}
        if getattr(self, "_chat_store_enabled", False):
            try:
                self.chat_store.delete_chats(list(removed), native_ids=removed)
            except (OSError, sqlite3.Error):
                self.SetStatusText("无法保存聊天隐藏状态，请稍后重试。")
                return
        self.archived_chats = [c for c in self.archived_chats if str(c.get("id") or "").strip() not in removed]
        if str(self.view_history_id or "").strip() in removed:
            self.view_mode = "active"
            self.view_history_id = None
        self._save_state()
        self._refresh_history()
        for owner in removed:
            self._push_remote_history_changed(owner, removed=True)
        self._render_answer_list()
        if len(removed) != len(candidates):
            self.SetStatusText("部分聊天仍在处理，请稍后隐藏。")

    def _history_rename(self, _, *, chat_id=None):
        c = self._history_chat_for_id(chat_id or self._selected_history_chat_id())
        if not c:
            return
        d = bindings.app.RenameDialog(self, str(c.get("title") or ""))
        if d.ShowModal() == wx.ID_OK:
            name = d.get_value()
            if name:
                queued = self._queue_native_title_rename(c, name, local=True)
                if queued is not None:
                    self.SetStatusText("正在确认原生聊天标题" if queued else "聊天已变更，请重新打开后改名。")
                    d.Destroy()
                    return
                before = copy.deepcopy(c)
                self._bump_chat_title_revision(c, "manual", title=name)
                store = getattr(self, "chat_store", None)
                try:
                    if not bool(getattr(self, "_chat_store_enabled", False)) or not callable(getattr(store, "upsert_chat", None)):
                        raise RuntimeError("Manual title cannot be durably committed")
                    store.upsert_chat(self._slim_active_chat_state() if c is self._current_chat_state else c)
                except Exception:
                    c.clear()
                    c.update(before)
                    d.Destroy()
                    return
                self._upsert_history_row(c.get("id"), allow_reorder=False)
                if c.get("agy_session_id"):
                    self._push_remote_title_changed(c)
                else:
                    self._push_remote_history_changed(c.get("id"))
        d.Destroy()
