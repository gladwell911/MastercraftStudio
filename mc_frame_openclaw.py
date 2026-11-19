"""OpenClaw sessions and transcript synchronization."""

import re
import uuid
from mc_frame_presentation import remove_emojis
from mc_frame_settings import REQUESTING_TEXT
from openclaw_client import (
    DEFAULT_OPENCLAW_AGENT,
    DEFAULT_OPENCLAW_SESSION_KEY,
    OpenClawSyncEvent,
    is_openclaw_model,
    load_session_pointer,
    load_session_pointer_by_session_id,
    normalize_openclaw_text,
    read_session_events,
)
from pathlib import Path

import mc_frame_bindings as bindings


class OpenClawSessionsMixin:
    def _make_openclaw_session_id(self, chat_id: str) -> str:
        clean = re.sub(r"[^a-zA-Z0-9_-]", "-", str(chat_id or "").strip())
        clean = clean.strip("-") or uuid.uuid4().hex
        return f"zgwd-{clean}"

    def _ensure_active_openclaw_session_id(self) -> str:
        if not self.active_openclaw_session_id:
            self.active_openclaw_session_id = self._make_openclaw_session_id(self._ensure_active_chat_id())
        return self.active_openclaw_session_id

    def _openclaw_session_id_for_active_chat(self) -> str:
        session_id = self._ensure_active_openclaw_session_id()
        if isinstance(self._current_chat_state, dict):
            self._current_chat_state["openclaw_session_key"] = self.active_openclaw_session_key
            self._current_chat_state["openclaw_session_id"] = session_id
        return session_id

    def _has_openclaw_turns(self, turns: list[dict] | None = None) -> bool:
        for turn in turns or self.active_session_turns:
            if is_openclaw_model(str(turn.get("model") or "")):
                return True
        return False

    def _is_openclaw_sync_target_active(self) -> bool:
        return bool(
            self.active_openclaw_session_id
            or self.active_openclaw_session_file
            or self._has_openclaw_turns()
        )

    def _has_archived_openclaw_sync_targets(self) -> bool:
        for chat in self.archived_chats:
            if not isinstance(chat, dict):
                continue
            if str(chat.get("openclaw_session_id") or chat.get("openclaw_session_file") or "").strip():
                return True
            turns = chat.get("turns")
            if isinstance(turns, list) and self._has_openclaw_turns(turns):
                return True
        return False

    def _reset_openclaw_sync_progress(self, clear_session_file: bool = False) -> None:
        self.active_openclaw_sync_offset = 0
        self.active_openclaw_last_event_id = ""
        self.active_openclaw_last_synced_at = 0.0
        if clear_session_file:
            self.active_openclaw_session_file = ""

    def _seek_openclaw_sync_to_current_tail(self) -> None:
        session_file = str(self.active_openclaw_session_file or "").strip()
        if not session_file:
            sessions_json = bindings.app.resolve_openclaw_sessions_dir(DEFAULT_OPENCLAW_AGENT) / "sessions.json"
            pointer = self._resolve_active_openclaw_session_pointer(sessions_json)
            if pointer is not None:
                self.active_openclaw_session_id = str(pointer.session_id or self.active_openclaw_session_id).strip()
                session_file = str(pointer.session_file or "").strip()
                self.active_openclaw_session_file = session_file
        offset = 0
        if session_file:
            try:
                offset = Path(session_file).stat().st_size
            except Exception:
                offset = 0
        self.active_openclaw_sync_offset = offset
        self.active_openclaw_last_event_id = ""
        self.active_openclaw_last_synced_at = bindings.app.time.time()

    def _resolve_active_openclaw_session_pointer(self, sessions_json: Path):
        session_id = str(self.active_openclaw_session_id or "").strip()
        if session_id:
            pointer = load_session_pointer_by_session_id(sessions_json, session_id)
            if pointer is not None:
                return pointer
        if not session_id:
            return load_session_pointer(sessions_json, self.active_openclaw_session_key or DEFAULT_OPENCLAW_SESSION_KEY)
        return None

    def _refresh_openclaw_sync_lifecycle(self, force_replay: bool = False) -> None:
        if not self._is_openclaw_sync_target_active() and not self._has_archived_openclaw_sync_targets():
            self._stop_openclaw_sync()
            return
        if force_replay:
            self._reset_openclaw_sync_progress(clear_session_file=True)
        self._start_openclaw_sync()

    def _start_openclaw_sync(self) -> None:
        thread = self._openclaw_sync_thread
        if thread and thread.is_alive():
            return
        self._openclaw_sync_stop.clear()
        self._openclaw_sync_thread = bindings.app.threading.Thread(target=self._openclaw_sync_loop, daemon=True)
        self._openclaw_sync_thread.start()

    def _stop_openclaw_sync(self) -> None:
        self._openclaw_sync_stop.set()
        thread = self._openclaw_sync_thread
        if thread and thread.is_alive():
            thread.join(timeout=1.5)
        self._openclaw_sync_thread = None

    def _openclaw_sync_loop(self) -> None:
        while not self._openclaw_sync_stop.is_set():
            try:
                self._sync_openclaw_once()
            except Exception:
                pass
            if self._openclaw_sync_stop.wait(1.0):
                break

    def _sync_openclaw_once(self) -> None:
        sessions_json = bindings.app.resolve_openclaw_sessions_dir(DEFAULT_OPENCLAW_AGENT) / "sessions.json"
        if self._is_openclaw_sync_target_active():
            self._sync_active_openclaw_once(sessions_json)
        self._sync_archived_openclaw_targets_once(sessions_json)

    def _sync_active_openclaw_once(self, sessions_json: Path) -> None:
        pointer = None
        stored_session_file = str(self.active_openclaw_session_file or "").strip()
        session_file = stored_session_file
        session_id = str(self.active_openclaw_session_id or "").strip()
        updated_at = 0.0
        if session_file and (not Path(session_file).exists()):
            pointer = self._resolve_active_openclaw_session_pointer(sessions_json)
            if pointer is not None:
                session_file = str(pointer.session_file or "").strip()
                session_id = str(pointer.session_id or session_id).strip()
                updated_at = float(pointer.updated_at or 0.0)
        if not session_file:
            pointer = self._resolve_active_openclaw_session_pointer(sessions_json)
            if pointer is None:
                return
            session_file = str(pointer.session_file or "").strip()
            session_id = str(pointer.session_id or session_id).strip()
            updated_at = float(pointer.updated_at or 0.0)
        if not session_file:
            return

        with self._openclaw_sync_lock:
            previous_file = self.active_openclaw_session_file
            previous_session_id = self.active_openclaw_session_id
            previous_offset = int(self.active_openclaw_sync_offset or 0)
        file_changed = previous_file != session_file
        session_changed = bool(session_id and previous_session_id and (previous_session_id != session_id))
        needs_replay = file_changed or session_changed
        offset = 0 if needs_replay else previous_offset
        new_offset, events = read_session_events(session_file, offset=offset)
        if not events and not needs_replay and new_offset == previous_offset:
            return
        bindings.app.wx_call_after_if_alive(
            self._apply_openclaw_sync_batch,
                {
                    "session_id": session_id,
                    "session_file": session_file,
                    "offset": new_offset,
                    "updated_at": updated_at,
                    "previous_file": previous_file,
                    "file_changed": file_changed,
                    "session_changed": session_changed,
                },
                events,
        )

    def _sync_archived_openclaw_targets_once(self, sessions_json: Path) -> None:
        for chat in list(self.archived_chats):
            if not isinstance(chat, dict):
                continue
            turns = chat.get("turns") if isinstance(chat.get("turns"), list) else []
            session_id = str(chat.get("openclaw_session_id") or "").strip()
            session_file = str(chat.get("openclaw_session_file") or "").strip()
            if not session_id and not session_file and not self._has_openclaw_turns(turns):
                continue
            if not session_id:
                chat_id = str(chat.get("source_chat_id") or chat.get("id") or "").strip()
                session_id = self._make_openclaw_session_id(chat_id)
                chat["openclaw_session_id"] = session_id
            updated_at = 0.0
            if session_file and not Path(session_file).exists():
                session_file = ""
            if not session_file:
                pointer = load_session_pointer_by_session_id(sessions_json, session_id)
                if pointer is None:
                    continue
                session_file = str(pointer.session_file or "").strip()
                session_id = str(pointer.session_id or session_id).strip()
                updated_at = float(pointer.updated_at or 0.0)
            if not session_file:
                continue
            try:
                previous_offset = max(int(chat.get("openclaw_sync_offset") or 0), 0)
            except Exception:
                previous_offset = 0
            previous_file = str(chat.get("openclaw_session_file") or "").strip()
            previous_session_id = str(chat.get("openclaw_session_id") or "").strip()
            file_changed = previous_file != session_file
            session_changed = bool(session_id and previous_session_id and previous_session_id != session_id)
            offset = 0 if (file_changed or session_changed) else previous_offset
            new_offset, events = read_session_events(session_file, offset=offset)
            if not events and not file_changed and not session_changed and new_offset == previous_offset:
                continue
            bindings.app.wx_call_after_if_alive(
                self._apply_openclaw_sync_batch,
                {
                    "chat_id": str(chat.get("id") or "").strip(),
                    "session_id": session_id,
                    "session_file": session_file,
                    "offset": new_offset,
                    "updated_at": updated_at,
                    "previous_file": previous_file,
                    "file_changed": file_changed,
                    "session_changed": session_changed,
                },
                events,
            )

    def _apply_openclaw_sync_batch(self, sync_state: dict, events: list[OpenClawSyncEvent]) -> None:
        changed = False
        had_input_events = bool(events)
        chat_id = str(sync_state.get("chat_id") or "").strip()
        active_ids = {str(self.active_chat_id or "").strip(), str(self.current_chat_id or "").strip(), ""}
        is_active_target = chat_id in active_ids
        target_chat = self._current_chat_state if is_active_target else self._find_archived_chat(chat_id)
        if not isinstance(target_chat, dict):
            return
        target_turns = self.active_session_turns if is_active_target else target_chat.get("turns")
        if not isinstance(target_turns, list):
            target_turns = []
            target_chat["turns"] = target_turns
        events = self._filter_openclaw_initial_sync_events(sync_state, events, target_turns)
        events = self._filter_openclaw_events_for_local_turns(events, target_turns)
        file_changed = bool(sync_state.get("file_changed"))
        session_changed = bool(sync_state.get("session_changed"))
        current_offset = int(self.active_openclaw_sync_offset or 0) if is_active_target else int(target_chat.get("openclaw_sync_offset") or 0)
        next_offset = int(sync_state.get("offset") or current_offset or 0)
        if not events and not file_changed and not session_changed:
            if next_offset != current_offset:
                target_chat["openclaw_sync_offset"] = next_offset
                if is_active_target:
                    self.active_openclaw_sync_offset = next_offset
            return
        if not had_input_events and not file_changed and not session_changed and next_offset == current_offset:
            return
        had_prior_sync = bool(
            self.active_openclaw_last_synced_at or self.active_openclaw_session_file
        ) if is_active_target else bool(target_chat.get("openclaw_last_synced_at") or target_chat.get("openclaw_session_file"))
        assistant_changed = False
        if file_changed or session_changed:
            if is_active_target:
                self.active_openclaw_session_file = str(sync_state.get("session_file") or "").strip()
                self.active_openclaw_sync_offset = 0
                self.active_openclaw_last_event_id = ""
            target_chat["openclaw_session_file"] = str(sync_state.get("session_file") or "").strip()
            target_chat["openclaw_sync_offset"] = 0
            target_chat["openclaw_last_event_id"] = ""
        session_id = str(sync_state.get("session_id") or "").strip()
        if session_id:
            target_chat["openclaw_session_id"] = session_id
            if is_active_target:
                self.active_openclaw_session_id = session_id
        session_file = str(sync_state.get("session_file") or target_chat.get("openclaw_session_file") or "").strip()
        target_chat["openclaw_session_file"] = session_file
        if is_active_target:
            self.active_openclaw_session_file = session_file
        for event in events:
            result = self._apply_openclaw_sync_event(event, target_turns, chat_id if chat_id else self.active_chat_id, is_active_target)
            if result == "visible":
                changed = True
                if event.role == "assistant":
                    assistant_changed = True
            if result:
                self._mark_chat_turns_dirty(chat_id if chat_id else self.active_chat_id, 0)
        sync_offset = next_offset
        target_chat["openclaw_sync_offset"] = sync_offset
        target_chat["openclaw_last_synced_at"] = bindings.app.time.time()
        if is_active_target:
            self.active_openclaw_sync_offset = sync_offset
            self.active_openclaw_last_synced_at = target_chat["openclaw_last_synced_at"]
        if events:
            last_event_id = str(events[-1].event_id or target_chat.get("openclaw_last_event_id") or "")
            target_chat["openclaw_last_event_id"] = last_event_id
            if is_active_target:
                self.active_openclaw_last_event_id = last_event_id
        if changed:
            should_render = assistant_changed
            self._record_chat_activity(target_chat, bindings.app.time.time())
            if is_active_target and self.view_mode == "active" and should_render:
                self._render_answer_list()
            self.SetStatusText("已同步 OpenClaw 主会话")
            if is_active_target and assistant_changed and had_prior_sync:
                self._play_finish_sound()
        self._save_state()

    def _filter_openclaw_initial_sync_events(
        self,
        sync_state: dict,
        events: list[OpenClawSyncEvent],
        target_turns: list[dict],
    ) -> list[OpenClawSyncEvent]:
        if not events or not bool(sync_state.get("file_changed")):
            return events
        previous_file = str(sync_state.get("previous_file") or "").strip()
        if previous_file:
            return events
        local_created_times = []
        for turn in target_turns:
            if not isinstance(turn, dict):
                continue
            if not is_openclaw_model(str(turn.get("model") or "")):
                continue
            if str(turn.get("origin") or turn.get("question_origin") or "").strip() == "openclaw-sync":
                continue
            try:
                created_at = float(turn.get("created_at") or 0.0)
            except Exception:
                created_at = 0.0
            if created_at > 0:
                local_created_times.append(created_at)
        if not local_created_times:
            return events
        cutoff = min(local_created_times) - 30.0
        return [event for event in events if float(event.timestamp or 0.0) >= cutoff]

    def _filter_openclaw_events_for_local_turns(
        self,
        events: list[OpenClawSyncEvent],
        target_turns: list[dict],
    ) -> list[OpenClawSyncEvent]:
        if not events:
            return events
        local_turn_indexes = [
            idx
            for idx, turn in enumerate(target_turns)
            if self._is_local_openclaw_turn(turn)
        ]
        if not local_turn_indexes:
            return events
        has_user_events = any(event.role == "user" for event in events)
        filtered: list[OpenClawSyncEvent] = []
        awaiting_answer_idx: int | None = None
        for event in events:
            if event.role == "user":
                match_idx = self._matching_local_openclaw_user_turn(event, target_turns, local_turn_indexes)
                if match_idx is None:
                    awaiting_answer_idx = None
                    continue
                filtered.append(event)
                awaiting_answer_idx = match_idx
                continue
            if event.role != "assistant":
                continue
            metadata_idx = self._matching_local_openclaw_assistant_metadata_turn(event, target_turns, local_turn_indexes)
            if metadata_idx is not None:
                filtered.append(event)
                continue
            if awaiting_answer_idx is not None and self._can_openclaw_turn_accept_assistant(target_turns[awaiting_answer_idx], event):
                filtered.append(event)
                awaiting_answer_idx = None
                continue
            if not has_user_events:
                match_idx = self._pending_local_openclaw_answer_turn(event, target_turns, local_turn_indexes)
                if match_idx is not None:
                    filtered.append(event)
        return filtered

    def _is_local_openclaw_turn(self, turn: dict) -> bool:
        if not isinstance(turn, dict):
            return False
        if not is_openclaw_model(str(turn.get("model") or "")):
            return False
        if not str(turn.get("question") or "").strip():
            return False
        origin = str(turn.get("origin") or turn.get("question_origin") or "").strip()
        return origin != "openclaw-sync"

    def _matching_local_openclaw_user_turn(
        self,
        event: OpenClawSyncEvent,
        target_turns: list[dict],
        local_turn_indexes: list[int],
    ) -> int | None:
        normalized = self._normalized_turn_text(event.text)
        event_ts = float(event.timestamp or bindings.app.time.time())
        for idx in local_turn_indexes:
            turn = target_turns[idx]
            if self._normalized_turn_text(str(turn.get("question") or "")) != normalized:
                continue
            existing_id = str(turn.get("question_external_event_id") or "").strip()
            if existing_id and existing_id != str(event.event_id or "").strip():
                continue
            try:
                created_at = float(turn.get("created_at") or 0.0)
            except Exception:
                created_at = 0.0
            if created_at and abs(created_at - event_ts) > 120:
                continue
            return idx
        return None

    def _matching_local_openclaw_assistant_metadata_turn(
        self,
        event: OpenClawSyncEvent,
        target_turns: list[dict],
        local_turn_indexes: list[int],
    ) -> int | None:
        normalized = self._normalized_turn_text(event.text)
        event_ts = float(event.timestamp or bindings.app.time.time())
        for idx in reversed(local_turn_indexes):
            turn = target_turns[idx]
            existing_id = str(turn.get("answer_external_event_id") or "").strip()
            if existing_id and existing_id != str(event.event_id or "").strip():
                continue
            answer = self._normalized_turn_text(str(turn.get("answer_md") or ""))
            if answer != normalized:
                continue
            try:
                created_at = float(turn.get("created_at") or 0.0)
            except Exception:
                created_at = 0.0
            if created_at and abs(created_at - event_ts) > 120:
                continue
            return idx
        return None

    def _pending_local_openclaw_answer_turn(
        self,
        event: OpenClawSyncEvent,
        target_turns: list[dict],
        local_turn_indexes: list[int],
    ) -> int | None:
        event_ts = float(event.timestamp or bindings.app.time.time())
        for idx in reversed(local_turn_indexes):
            turn = target_turns[idx]
            if not self._can_openclaw_turn_accept_assistant(turn, event):
                continue
            try:
                created_at = float(turn.get("created_at") or 0.0)
            except Exception:
                created_at = 0.0
            if created_at and event_ts < created_at - 30:
                continue
            return idx
        return None

    def _can_openclaw_turn_accept_assistant(self, turn: dict, event: OpenClawSyncEvent) -> bool:
        if not isinstance(turn, dict):
            return False
        existing_id = str(turn.get("answer_external_event_id") or "").strip()
        if existing_id and existing_id != str(event.event_id or "").strip():
            return False
        answer_md = str(turn.get("answer_md") or "")
        return answer_md in {"", REQUESTING_TEXT} or ((not existing_id) and answer_md.startswith("OpenClaw "))

    def _apply_openclaw_sync_event(
        self,
        event: OpenClawSyncEvent,
        target_turns: list[dict] | None = None,
        chat_id: str = "",
        is_active_target: bool = True,
    ) -> str:
        turns = target_turns if isinstance(target_turns, list) else self.active_session_turns
        event_id = str(event.event_id or "").strip()
        if event_id and self._has_openclaw_event_id(event_id, turns):
            return ""
        text = remove_emojis(normalize_openclaw_text(event.text))
        if not text:
            return ""
        if event.role == "user":
            merged = self._merge_openclaw_user_event(text, event, turns)
            if merged:
                return merged
            turns.append(
                {
                    "question": text,
                    "answer_md": "",
                    "model": "openclaw/main",
                    "created_at": float(event.timestamp or bindings.app.time.time()),
                    "origin": "openclaw-sync",
                    "external_event_id": event_id,
                    "external_role": "user",
                    "external_timestamp": float(event.timestamp or bindings.app.time.time()),
                    "question_origin": "openclaw-sync",
                    "question_external_event_id": event_id,
                    "question_external_timestamp": float(event.timestamp or bindings.app.time.time()),
                }
            )
            self._apply_nonrecoverable_turn_metadata(turns[-1], "openclaw/main", text)
            if is_active_target:
                self.active_turn_idx = len(turns) - 1
            if len([turn for turn in turns if str((turn or {}).get("question") or "").strip()]) == 1:
                self._schedule_first_question_auto_title(chat_id or self.active_chat_id or self.current_chat_id, text)
            return "visible"

        merged = self._merge_openclaw_assistant_event(text, event, turns, is_active_target)
        if merged:
            return merged
        turns.append(
            {
                "question": "",
                "answer_md": text,
                "model": "openclaw/main",
                "created_at": float(event.timestamp or bindings.app.time.time()),
                "origin": "openclaw-sync",
                "external_event_id": event_id,
                "external_role": "assistant",
                "external_timestamp": float(event.timestamp or bindings.app.time.time()),
                "answer_origin": "openclaw-sync",
                "answer_external_event_id": event_id,
                "answer_external_timestamp": float(event.timestamp or bindings.app.time.time()),
            }
        )
        self._apply_nonrecoverable_turn_metadata(turns[-1], "openclaw/main", "")
        if is_active_target:
            self.active_turn_idx = len(turns) - 1
        return "visible"

    def _has_openclaw_event_id(self, event_id: str, turns: list[dict] | None = None) -> bool:
        eid = str(event_id or "").strip()
        if not eid:
            return False
        for turn in turns if isinstance(turns, list) else self.active_session_turns:
            if eid in {
                str(turn.get("external_event_id") or "").strip(),
                str(turn.get("question_external_event_id") or "").strip(),
                str(turn.get("answer_external_event_id") or "").strip(),
            }:
                return True
        return False

    def _normalized_turn_text(self, text: str) -> str:
        return normalize_openclaw_text(text)

    def _apply_nonrecoverable_turn_metadata(self, turn: dict, model: str, question: str) -> None:
        now = bindings.app.time.time()
        turn["request_status"] = "done"
        turn["request_model"] = str(model or "")
        turn["request_question"] = str(question or "")
        turn["request_started_at"] = now
        turn["request_last_attempt_at"] = now
        turn["request_attempt_count"] = 0
        turn["request_recoverable"] = False
        turn["request_recovery_mode"] = "retry"
        turn["request_resume_token"] = {}
        turn["request_error"] = ""
        turn["request_recovered_after_restart"] = False

    def _merge_openclaw_user_event(self, text: str, event: OpenClawSyncEvent, turns: list[dict] | None = None) -> str:
        target_turns = turns if isinstance(turns, list) else self.active_session_turns
        normalized = self._normalized_turn_text(text)
        event_ts = float(event.timestamp or bindings.app.time.time())
        for idx in range(len(target_turns) - 1, -1, -1):
            turn = target_turns[idx]
            question = self._normalized_turn_text(str(turn.get("question") or ""))
            if not question:
                continue
            if question != normalized:
                continue
            existing_id = str(turn.get("question_external_event_id") or "").strip()
            created_at = float(turn.get("created_at") or 0.0)
            if existing_id == str(event.event_id or "").strip():
                return ""
            if existing_id:
                continue
            if abs(created_at - event_ts) > 30:
                continue
            turn["question_external_event_id"] = str(event.event_id or "").strip()
            turn["question_external_timestamp"] = event_ts
            turn["external_event_id"] = str(event.event_id or "").strip()
            turn["external_role"] = "user"
            turn["external_timestamp"] = event_ts
            if "question_origin" not in turn:
                turn["question_origin"] = str(turn.get("origin") or "local")
            if "request_status" not in turn:
                self._apply_nonrecoverable_turn_metadata(turn, "openclaw/main", str(turn.get("question") or text))
            return "metadata"
        return ""

    def _merge_openclaw_assistant_event(
        self,
        text: str,
        event: OpenClawSyncEvent,
        turns: list[dict] | None = None,
        is_active_target: bool = True,
    ) -> str:
        target_turns = turns if isinstance(turns, list) else self.active_session_turns
        normalized = self._normalized_turn_text(text)
        event_ts = float(event.timestamp or bindings.app.time.time())
        event_id = str(event.event_id or "").strip()
        for idx in range(len(target_turns) - 1, -1, -1):
            turn = target_turns[idx]
            answer_md = str(turn.get("answer_md") or "")
            answer = self._normalized_turn_text("" if answer_md == REQUESTING_TEXT else answer_md)
            existing_id = str(turn.get("answer_external_event_id") or "").strip()
            created_at = float(turn.get("created_at") or 0.0)
            if existing_id == event_id:
                return ""
            if answer_md in {"", REQUESTING_TEXT} or ((not existing_id) and answer_md.startswith("OpenClaw ")):
                turn["answer_md"] = text
                turn["answer_origin"] = "openclaw-sync"
                turn["answer_external_event_id"] = event_id
                turn["answer_external_timestamp"] = event_ts
                turn["external_event_id"] = event_id
                turn["external_role"] = "assistant"
                turn["external_timestamp"] = event_ts
                if not turn.get("created_at"):
                    turn["created_at"] = event_ts
                if "request_status" not in turn:
                    self._apply_nonrecoverable_turn_metadata(turn, "openclaw/main", "")
                if is_active_target:
                    self.active_turn_idx = idx
                return "visible"
            if answer == normalized and (not existing_id) and abs(created_at - event_ts) <= 60:
                turn["answer_external_event_id"] = event_id
                turn["answer_external_timestamp"] = event_ts
                turn["external_event_id"] = event_id
                turn["external_role"] = "assistant"
                turn["external_timestamp"] = event_ts
                if "request_status" not in turn:
                    self._apply_nonrecoverable_turn_metadata(turn, "openclaw/main", str(turn.get("question") or ""))
                if is_active_target:
                    self.active_turn_idx = idx
                return "metadata"
        return ""

    def _send_openclaw_hidden_message(self, text: str) -> None:
        message = str(text or "").strip()
        if not message:
            return
        model = self._resolve_current_model()
        if not is_openclaw_model(model):
            return
        self.selected_model = model
        self.model_combo.SetValue(model)
        self._ensure_active_chat_id()
        self._ensure_active_openclaw_session_id()
        if not self.active_session_started_at:
            self.active_session_started_at = bindings.app.time.time()
        self.is_running = True
        self.new_chat_button.Disable()
        self._set_input_hint_idle()
        self._save_state()
        self._refresh_openclaw_sync_lifecycle()
        t = bindings.app.threading.Thread(target=self._worker, args=("", -1, message, model), daemon=True)
        t.start()

    def _openclaw_session_id_for_worker_chat(self, chat_id: str, target_chat: dict | None, is_current_target: bool, explicit_session_id: str = "") -> str:
        session_id = str(explicit_session_id or "").strip()
        if session_id:
            return session_id
        if is_current_target:
            return self._ensure_active_openclaw_session_id()
        if isinstance(target_chat, dict):
            session_id = str(target_chat.get("openclaw_session_id") or "").strip()
            if not session_id:
                session_id = self._make_openclaw_session_id(chat_id)
                target_chat["openclaw_session_key"] = str(target_chat.get("openclaw_session_key") or DEFAULT_OPENCLAW_SESSION_KEY).strip() or DEFAULT_OPENCLAW_SESSION_KEY
                target_chat["openclaw_session_id"] = session_id
            return session_id
        return ""
