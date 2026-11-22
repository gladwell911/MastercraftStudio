"""Kimi startup, session ownership, and transcript recovery."""

from __future__ import annotations

import uuid
import wx
from codex_client import CodexEvent
from context_usage import context_usage_from_dict, format_context_usage_label
from kimi_server_client import (
    DEFAULT_KIMI_MODEL,
    event_from_payload as kimi_event_from_payload,
    event_to_payload as kimi_event_to_payload,
    is_kimi_model,
)
from mc_frame_settings import REQUESTING_TEXT
from pathlib import Path

import mc_frame_bindings as bindings


class KimiSessionsMixin:
    @staticmethod
    def _parse_kimi_local_command(question: str) -> dict | None:
        return bindings.app.ChatFrame._parse_codex_local_command(question)

    @staticmethod
    def _kimi_supported_local_commands() -> tuple[tuple[str, str], ...]:
        return (
            ("status", "显示模型、会话、turn 和上下文状态"),
            ("help", "列出本程序支持的 Kimi Code 斜杠命令"),
            ("new", "开始一个新的本地聊天"),
            ("clear", "清除当前聊天关联的 Kimi Code 会话状态"),
            ("stop", "中断当前活跃 Kimi Code turn"),
        )

    def _build_kimi_help_markdown(self) -> str:
        lines = [
            "## Kimi Code 斜杠命令",
            "",
            "本程序会识别所有以 `/` 开头的 Kimi Code 输入。以下命令已在桌面端实现：",
            "",
        ]
        lines.extend(f"- `/{name}`：{description}" for name, description in self._kimi_supported_local_commands())
        lines.extend(
            [
                "",
                "其他 `/...` 命令会被识别为 Kimi Code 斜杠命令，但当前桌面端暂不支持，不会被当成普通聊天发送。",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _build_kimi_unsupported_command_markdown(command: str, args: str = "") -> str:
        suffix = f" {args}" if str(args or "").strip() else ""
        return (
            "## Kimi Code 斜杠命令暂不支持\n\n"
            f"`/{command}{suffix}` 已被识别为 Kimi Code 斜杠命令，但当前桌面端还没有对应实现。\n\n"
            "为避免误操作，这条输入不会作为普通聊天内容发送给 Kimi Code。"
        )

    def _kimi_session_id_for_chat(self, chat: dict | None) -> str:
        session_id = str((chat or {}).get("kimi_session_id") or "").strip()
        if session_id:
            return session_id
        if chat is self._current_chat_state:
            return str(self.active_kimi_session_id or "").strip()
        return ""

    def _kimi_turn_id_for_chat(self, chat: dict | None) -> str:
        turn_id = str((chat or {}).get("kimi_turn_id") or "").strip()
        if turn_id:
            return turn_id
        if chat is self._current_chat_state:
            return str(self.active_kimi_turn_id or "").strip()
        return ""

    def _workspace_dir_for_kimi(self) -> str:
        return str(Path(bindings.app.__file__).resolve().parent)

    def _finish_kimi_startup_failure(self, chat_id: str, turn_idx: int, request, submission_lock,
                                     message: str, model: str) -> None:
        with self._kimi_owner_lock:
            if self._kimi_submission_locks.get(chat_id) is submission_lock:
                self._finish_model_startup_failure(chat_id, turn_idx, request, message, model)

    def _apply_kimi_final_answer_to_turn(self, turn: dict, text: str) -> bool:
        return self._apply_codex_final_answer_to_turn(turn, text)

    def _build_kimi_history_recovery_prompt(self, history_turns: list[dict], question: str) -> str:
        clean_question = str(question or "").strip()
        transcript_parts = []
        for turn in history_turns or []:
            if not isinstance(turn, dict):
                continue
            prior_question = str(turn.get("question") or "").strip()
            prior_answer = str(turn.get("answer_md") or "").strip()
            if prior_answer == REQUESTING_TEXT:
                prior_answer = ""
            if prior_question:
                transcript_parts.append(f"用户：{prior_question}")
            if prior_answer:
                transcript_parts.append(f"Kimi Code：{prior_answer}")
        if not transcript_parts:
            return clean_question
        transcript = "\n".join(transcript_parts)
        return (
            "下面是当前聊天在本地保存的历史记录，请把它当作本次会话上下文继续：\n"
            f"{transcript}\n\n"
            "请基于以上上下文继续回答下面这个新问题：\n"
            f"{clean_question}"
        )

    def _kimi_should_steer_turn(self, target_chat: dict, is_current_target: bool) -> bool:
        if not isinstance(target_chat, dict):
            return False
        chat_id = str(target_chat.get("id") or (self.active_chat_id if is_current_target else "") or "").strip()
        if isinstance(self._kimi_active_turns.get(chat_id), dict):
            return True
        queue = target_chat.get("kimi_request_queue")
        return bool(is_current_target and self.active_kimi_turn_active and not queue)

    def _kimi_target_chat(self, chat_id: str) -> tuple[dict | None, bool]:
        normalized = str(chat_id or "").strip()
        is_current_target = normalized in {self.active_chat_id, self.current_chat_id, ""}
        if is_current_target:
            return self._current_chat_state, True
        chat = self._find_archived_chat(normalized)
        return (chat if isinstance(chat, dict) else None), False

    def _ensure_kimi_client(self) -> bindings.app.KimiServerClient:
        with self._kimi_client_lock:
            client = self._kimi_client
            if client is None:
                client = bindings.app.KimiServerClient(
                    on_message=self._on_kimi_client_message,
                    on_exit=self._on_kimi_client_exit,
                )
                self._kimi_client = client
            return client

    def _start_kimi_client_with_retry(self, client, *, deadline: float | None = None) -> None:
        first_error: Exception | None = None
        for attempt in range(max(1, int(self._kimi_reconcile_attempts))):
            if deadline is not None and bindings.app.time.monotonic() >= deadline:
                break
            try:
                self._kimi_call_with_deadline(client.start, deadline=deadline)
                return
            except Exception as exc:
                if first_error is None:
                    first_error = exc
                if attempt + 1 < max(1, int(self._kimi_reconcile_attempts)):
                    delay = min(float(self._kimi_reconcile_backoff) * (2 ** attempt), 2.0)
                    if deadline is not None:
                        delay = min(delay, max(0.0, deadline - bindings.app.time.monotonic()))
                    if delay:
                        bindings.app.time.sleep(delay)
        if first_error is not None:
            raise first_error
        raise TimeoutError("Kimi recovery deadline exhausted")

    @staticmethod
    def _kimi_call_with_deadline(func, *args, deadline: float | None = None, **kwargs):
        """Pass the shared recovery budget to blocking client calls.

        Test doubles and older injected clients may not expose ``timeout``;
        retain compatibility for those while the production client enforces it.
        """
        if deadline is None:
            return func(*args, **kwargs)
        remaining = deadline - bindings.app.time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Kimi recovery deadline exhausted")
        try:
            return func(*args, timeout=max(0.05, remaining), **kwargs)
        except TypeError as exc:
            if "timeout" not in str(exc):
                raise
            return func(*args, **kwargs)

    def _iter_kimi_chats(self):
        seen: set[str] = set()
        current = getattr(self, "_current_chat_state", None)
        if isinstance(current, dict):
            chat_id = str(current.get("id") or self.active_chat_id or self.current_chat_id or "").strip()
            if chat_id:
                seen.add(chat_id)
                yield chat_id, current, self.active_session_turns
        for chat in getattr(self, "archived_chats", []) or []:
            if not isinstance(chat, dict):
                continue
            chat_id = str(chat.get("id") or "").strip()
            if not chat_id or chat_id in seen:
                continue
            if (
                not isinstance(chat.get("turns"), list)
                and (bool(chat.get("kimi_turn_active")) or bool(chat.get("kimi_request_queue")))
            ):
                hydrated = self._hydrate_chat_from_store(chat, include_execution_steps=False)
                if isinstance(hydrated, dict):
                    chat = hydrated
            turns = chat.get("turns") if isinstance(chat.get("turns"), list) else []
            yield chat_id, chat, turns

    def _rebuild_kimi_runtime_state(self) -> None:
        """Recreate prompt ownership after load/chat switches from persisted turns."""
        if not hasattr(self, "_kimi_owner_lock"):
            return
        with self._kimi_owner_lock:
            # Persisted pending turns are authoritative. Copy-and-grow keeps
            # deleted chats alive as ghost owners after /clear or navigation.
            owners = {
                key: dict(value) for key, value in self._kimi_prompt_owners.items()
                if isinstance(value, dict) and not bool(value.get("landed"))
            }
            active = {
                str(value.get("chat_id") or "").strip(): dict(value)
                for value in owners.values()
                if str(value.get("role") or "") == "active" and str(value.get("chat_id") or "").strip()
            }
            for chat_id, chat, turns in self._iter_kimi_chats():
                queue = chat.get("kimi_request_queue") if isinstance(chat.get("kimi_request_queue"), list) else []
                queued_prompts = {
                    str(entry.get("prompt_id") or "").strip()
                    for entry in queue
                    if isinstance(entry, dict) and str(entry.get("prompt_id") or "").strip()
                }
                for idx, turn in enumerate(turns or []):
                    if not isinstance(turn, dict):
                        continue
                    prompt_id = str(turn.get("kimi_prompt_id") or "").strip()
                    session_id = str(turn.get("kimi_session_id") or chat.get("kimi_session_id") or "").strip()
                    if not prompt_id and str(turn.get("request_status") or "").strip() == "pending":
                        # Older archives predate prompt ownership.  Keep a
                        # durable placeholder so the session coordinator can
                        # migrate it from an unambiguous REST user boundary.
                        prompt_id = f"__legacy__:{chat_id}:{idx}"
                    if str(turn.get("request_status") or "").strip() != "pending":
                        owner_prompt_id = str(turn.get("kimi_owner_prompt_id") or prompt_id).strip()
                        owner_key = self._kimi_owner_key(chat_id, session_id, owner_prompt_id)
                        self._add_kimi_tombstone(owner_key)
                        self._kimi_completed_identities.add(
                            (
                                session_id,
                                owner_prompt_id,
                                str(turn.get("kimi_turn_id") or "").strip(),
                                "main",
                            )
                        )
                        owners.pop(self._kimi_owner_key(chat_id, session_id, prompt_id), None)
                        continue
                    if not prompt_id or not session_id:
                        continue
                    existing = owners.get(self._kimi_owner_key(chat_id, session_id, prompt_id))
                    generation = int((existing or {}).get("generation") or 0) if isinstance(existing, dict) else 0
                    if generation <= 0:
                        self._kimi_owner_generation += 1
                        generation = self._kimi_owner_generation
                    owner = {
                        "chat_id": chat_id,
                        "session_id": session_id,
                        "turn_id": str(turn.get("kimi_turn_id") or "").strip(),
                        "turn_idx": idx,
                        "prompt_id": prompt_id,
                        "question": str(turn.get("kimi_prompt_question") or turn.get("question") or "").strip(),
                        "model": str(turn.get("model") or "").strip(),
                        "role": str(turn.get("kimi_prompt_role") or ("queued" if prompt_id in queued_prompts else "active")),
                        "owner_prompt_id": str(turn.get("kimi_owner_prompt_id") or prompt_id).strip(),
                        "generation": generation,
                        "landed": True,
                        "legacy": prompt_id.startswith(("__legacy__:", "__unresolved__:")),
                        "exact_prompt_id": bool(turn.get("kimi_prompt_exact")),
                        "candidate_alias": bool(turn.get("kimi_candidate_alias")) or prompt_id.startswith("__unresolved__:"),
                        "created_at": turn.get("created_at") or turn.get("requested_at") or 0,
                    }
                    owners[self._kimi_owner_key(chat_id, session_id, prompt_id)] = owner
                    if owner["role"] == "active":
                        active[chat_id] = dict(owner)
            self._kimi_prompt_owners = owners
            self._kimi_active_turns = active
            valid_sessions = {key[1] for key in owners}
            valid_prompt_pairs = {(key[1], key[2]) for key in owners}
            self._kimi_idle_proofs = {key: value for key, value in self._kimi_idle_proofs.items() if key in owners}
            # HTTP submission and event delivery can overlap a chat switch.
            # Keep intent/turn early buckets and unresolved submissions until
            # their own owner lands or is explicitly cleared; pruning them by
            # the currently persisted session loses the only join evidence.
            self._kimi_incomplete_sessions.intersection_update(valid_sessions)
            self._kimi_incomplete_streams = {
                key for key in self._kimi_incomplete_streams if key[1] in valid_sessions
            }
            if len(self._kimi_completed_identities) > 512:
                self._kimi_completed_identities = set(list(self._kimi_completed_identities)[-256:])

    @staticmethod
    def _kimi_owner_key(chat_id: str, session_id: str, prompt_id: str) -> tuple[str, str, str]:
        return (
            str(chat_id or "").strip(),
            str(session_id or "").strip(),
            str(prompt_id or "").strip(),
        )

    def _kimi_owner_key_for(self, owner: dict) -> tuple[str, str, str]:
        return self._kimi_owner_key(
            owner.get("chat_id"),
            owner.get("session_id"),
            owner.get("prompt_id"),
        )

    def _kimi_submission_lock(self, session_or_chat: str) -> bindings.app.threading.Lock:
        key = str(session_or_chat or "").strip()
        with self._kimi_owner_lock:
            lock = self._kimi_submission_locks.get(key)
            if lock is None:
                lock = bindings.app.threading.Lock()
                self._kimi_submission_locks[key] = lock
            return lock

    def _add_kimi_tombstone(self, key: tuple[str, str, str]) -> None:
        if not all(key):
            return
        if key not in self._kimi_terminal_tombstones:
            self._kimi_terminal_tombstones.add(key)
            self._kimi_tombstone_order.append(key)
        while len(self._kimi_tombstone_order) > self._kimi_replay_window:
            expired = self._kimi_tombstone_order.popleft()
            self._kimi_terminal_tombstones.discard(expired)

    def _find_kimi_prompt_owner(
        self,
        prompt_id: str,
        *,
        session_id: str = "",
        chat_id: str = "",
        include_unlanded: bool = False,
    ) -> dict | None:
        prompt_id = str(prompt_id or "").strip()
        session_id = str(session_id or "").strip()
        chat_id = str(chat_id or "").strip()
        if not prompt_id:
            return None
        with self._kimi_owner_lock:
            matches = [
                owner for key, owner in self._kimi_prompt_owners.items()
                if isinstance(owner, dict)
                and key[2] == prompt_id
                and (not session_id or key[1] == session_id)
                and (not chat_id or key[0] == chat_id)
                and (include_unlanded or owner.get("landed") is not False)
            ]
        return dict(matches[0]) if len(matches) == 1 else None

    def _register_kimi_prompt_owner(self, owner: dict, *, submission_lock=None) -> bool:
        prompt_id = str(owner.get("prompt_id") or "").strip()
        session_id = str(owner.get("session_id") or "").strip()
        chat_id = str(owner.get("chat_id") or "").strip()
        if not prompt_id or not session_id or not chat_id:
            return False
        with self._kimi_owner_lock:
            if submission_lock is not None and self._kimi_submission_locks.get(chat_id) is not submission_lock:
                return False
            key = self._kimi_owner_key(chat_id, session_id, prompt_id)
            existing = self._kimi_prompt_owners.get(key)
            stored = dict(existing) if isinstance(existing, dict) else {}
            stored.update(owner)
            if not int(stored.get("generation") or 0):
                self._kimi_owner_generation += 1
                stored["generation"] = self._kimi_owner_generation
            owner["generation"] = int(stored.get("generation") or 0)
            self._kimi_idle_proofs.pop(key, None)
            self._kimi_terminal_tombstones.discard(key)
            self._kimi_prompt_owners[key] = stored
        return True

    def _replay_kimi_early_events(self, owner: dict) -> None:
        prompt_id = str(owner.get("prompt_id") or "").strip()
        session_id = str(owner.get("session_id") or "").strip()
        if not prompt_id:
            return
        early: list[CodexEvent] = []
        with self._kimi_owner_lock:
            key = self._kimi_owner_key(owner.get("chat_id"), session_id, prompt_id)
            stored = self._kimi_prompt_owners.get(key)
            if isinstance(stored, dict):
                stored["landed"] = True
                owner = dict(stored)
            early = self._kimi_early_events.pop((session_id, prompt_id), [])
            intent_id = str(owner.get("submission_intent_id") or "").strip()
            if intent_id:
                early.extend(
                    self._kimi_early_events.pop((session_id, f"__intent__:{intent_id}"), [])
                )
        for event in early:
            chat_id = str(owner.get("chat_id") or "").strip()
            self._dispatch_kimi_event_to_ui(chat_id, event)
        if session_id and session_id in self._kimi_recovery_intents:
            self._recover_kimi_pending_owners(
                session_ids={session_id},
                original_error=self._kimi_recovery_intents.get(session_id, ""),
            )

    def _buffer_kimi_early_event(self, event: CodexEvent) -> bool:
        """Buffer only events for a known, not-yet-landed prompt owner.

        Buckets are bounded by the number of known owners.  Their contents are
        intentionally not truncated: dropping the head of an answer stream
        corrupts the eventual final response.
        """
        prompt_id = self._kimi_event_prompt_id(event)
        session_id = self._event_thread_id(event)
        owner = None
        if prompt_id:
            owner = self._find_kimi_prompt_owner(
                prompt_id,
                session_id=session_id,
                include_unlanded=True,
            )
            if not isinstance(owner, dict):
                owner = self._claim_kimi_pending_submission(session_id, prompt_id)
        with self._kimi_owner_lock:
            intents = list(self._kimi_pending_submissions.get(session_id) or [])
            if isinstance(owner, dict) and self._kimi_owner_key_for(owner) in self._kimi_terminal_tombstones:
                return True
            if isinstance(owner, dict) and owner.get("landed") is not False:
                return False
            if prompt_id:
                # A prompt-bearing event can arrive before either of several
                # concurrent HTTP submissions has returned.  Its prompt id is
                # already an unambiguous future replay bucket.
                if not isinstance(owner, dict) and not any(not intent.get("prompt_id") for intent in intents):
                    return False
                key = (session_id, prompt_id)
            else:
                if len(intents) == 1:
                    intent_id = str(intents[0].get("submission_intent_id") or "").strip()
                    if not intent_id:
                        return False
                    key = (session_id, f"__intent__:{intent_id}")
                elif len(intents) > 1 and self._event_turn_id(event):
                    # An unresolved POST can overlap a later same-session
                    # submission.  A prompt-less frame cannot select either
                    # intent yet, but its server turn remains a stable future
                    # join key once any prompt-bearing frame arrives.
                    key = (session_id, f"__turn__:{self._event_turn_id(event)}")
                else:
                    return False
            if key not in self._kimi_early_events and len(self._kimi_early_events) >= self._kimi_early_event_key_limit:
                # This can only be an unlanded, locally submitted owner.  Keep
                # recovery intent instead of accepting an unbounded new key.
                self._kimi_recovery_intents.setdefault(session_id, "Kimi early-event buffer capacity reached")
                return True
            self._kimi_early_events.setdefault(key, []).append(event)
        return True

    def _claim_kimi_pending_submission(self, session_id: str, prompt_id: str) -> dict | None:
        session_id = str(session_id or "").strip()
        prompt_id = str(prompt_id or "").strip()
        if not session_id or not prompt_id:
            return None
        with self._kimi_owner_lock:
            intents = [
                intent for intent in self._kimi_pending_submissions.get(session_id, [])
                if not intent.get("prompt_id") or intent["prompt_id"] == prompt_id
            ]
            if len(intents) != 1:
                return None
            owner = dict(intents[0])
            submission_lock = self._kimi_submission_locks.get(owner.get("chat_id"))
            if submission_lock is None:
                return None
            owner.update(
                {
                    "session_id": session_id,
                    "prompt_id": prompt_id,
                    "owner_prompt_id": prompt_id,
                    "role": "active",
                    "landed": False,
                }
            )
            return owner if self._register_kimi_prompt_owner(owner, submission_lock=submission_lock) else None

    def _kimi_owner_for_event(self, event: CodexEvent) -> dict | None:
        prompt_id = self._kimi_event_prompt_id(event)
        if prompt_id:
            return self._find_kimi_prompt_owner(
                prompt_id,
                session_id=self._event_thread_id(event),
            )
        session_id = self._event_thread_id(event)
        if session_id:
            # Missing-prompt events may continue the one already-active owner;
            # they never activate or settle an independent queued owner.
            with self._kimi_owner_lock:
                matches = [
                    dict(owner) for key, owner in self._kimi_prompt_owners.items()
                    if isinstance(owner, dict)
                    and key[1] == session_id
                    and str(owner.get("role") or "active") == "active"
                    and owner.get("landed") is not False
                    and key not in self._kimi_terminal_tombstones
                    and (
                        not self._event_turn_id(event)
                        or not str(owner.get("turn_id") or "").strip()
                        or str(owner.get("turn_id") or "").strip() == self._event_turn_id(event)
                    )
                ]
            if len(matches) == 1:
                return matches[0]
        return None

    def _kimi_pending_owners(self, session_ids: set[str] | None = None) -> list[dict]:
        owners: list[dict] = []
        wanted = {str(value or "").strip() for value in (session_ids or set()) if str(value or "").strip()}
        with self._kimi_owner_lock:
            for owner in self._kimi_prompt_owners.values():
                if not isinstance(owner, dict):
                    continue
                if wanted and str(owner.get("session_id") or "").strip() not in wanted:
                    continue
                if owner.get("landed") is False:
                    owners.append(dict(owner))
                    continue
                chat_id = str(owner.get("chat_id") or "").strip()
                target_chat, is_current = self._kimi_target_chat(chat_id)
                turns = self.active_session_turns if is_current else ((target_chat or {}).get("turns") if isinstance(target_chat, dict) else [])
                idx = owner.get("turn_idx")
                if isinstance(turns, list) and isinstance(idx, int) and 0 <= idx < len(turns):
                    turn = turns[idx]
                    if isinstance(turn, dict) and str(turn.get("request_status") or "").strip() == "pending":
                        owners.append(dict(owner))
        return owners

    def _remember_kimi_active_turn(self, chat_id: str, payload: dict) -> None:
        normalized = str(chat_id or "").strip()
        if not normalized:
            return
        with self._kimi_owner_lock:
            metadata: dict = {}
            existing = self._kimi_active_turns.get(normalized)
            if isinstance(existing, dict):
                metadata = dict(existing)
            turn_idx = payload.get("turn_idx")
            if isinstance(turn_idx, int):
                metadata["turn_idx"] = turn_idx
            for key in ("turn_id", "session_id", "prompt_id", "model"):
                value = str(payload.get(key) or "").strip()
                if value:
                    metadata[key] = value
            if metadata:
                self._kimi_active_turns[normalized] = metadata

    def _clear_kimi_active_turn(self, chat_id: str, turn_idx=None, turn_id: str | None = None) -> None:
        normalized = str(chat_id or "").strip()
        if not normalized:
            return
        with self._kimi_owner_lock:
            metadata = self._kimi_active_turns.get(normalized)
            if not isinstance(metadata, dict):
                return
            if isinstance(turn_idx, int) and metadata.get("turn_idx") != turn_idx:
                return
            turn_id_value = str(turn_id or "").strip()
            if turn_id_value and str(metadata.get("turn_id") or "").strip() != turn_id_value:
                return
            self._kimi_active_turns.pop(normalized, None)

    def _start_kimi_worker_for_turn(self, chat_id: str, turn_idx: int, question: str, model: str) -> None:
        def _worker() -> None:
            self._run_kimi_turn_worker(chat_id, turn_idx, question, model, from_recovery=False)

        bindings.app.threading.Thread(target=_worker, daemon=True).start()

    def _start_kimi_local_command_worker_for_turn(self, chat_id: str, turn_idx: int, command: str, args: str, model: str) -> None:
        normalized_command = str(command or "").strip().lower()
        clear_answer = ""
        stop_owner = None
        if normalized_command == "clear":
            # Retire the old session before the send entry can accept another prompt.
            target_chat, _turns, _is_current = self._chat_target_for_request(chat_id)
            clear_answer = self._handle_kimi_clear_command(target_chat, turn_idx, persist=False)
        elif normalized_command == "stop":
            with self._kimi_owner_lock:
                stop_owner = dict(self._kimi_active_turns.get(chat_id) or {})
        bindings.app.threading.Thread(
            target=self._run_kimi_local_command_worker,
            args=(chat_id, turn_idx, command, args, model, stop_owner, clear_answer),
            daemon=True,
        ).start()

    def _run_kimi_local_command_worker(self, chat_id: str, turn_idx: int, command: str, args: str,
                                       model: str, stop_owner: dict | None = None, clear_answer: str = "") -> None:
        try:
            chat_id = str(chat_id or "").strip()
            target_chat, _target_turns, is_current_target = self._chat_target_for_request(chat_id)
            if not isinstance(target_chat, dict):
                target_chat = self._current_chat_state if is_current_target else {}
            normalized_command = str(command or "").strip().lower()
            if normalized_command == "status":
                client = None
                if self._kimi_session_id_for_chat(target_chat):
                    client = self._ensure_kimi_client()
                    client.start()
                answer = self._build_kimi_status_markdown(client, target_chat, model)
            elif normalized_command == "help":
                answer = self._build_kimi_help_markdown()
            elif normalized_command == "new":
                answer = "## Kimi Code 新聊天\n\n已请求开始新聊天。"
                self._call_after_if_alive(self._on_new_chat_clicked, None)
            elif normalized_command == "clear":
                self._save_state()
                answer = clear_answer
            elif normalized_command == "stop":
                client = None
                if self._kimi_session_id_for_chat(target_chat):
                    client = self._ensure_kimi_client()
                    client.start()
                answer = self._handle_kimi_stop_command(client, target_chat, stop_owner, chat_id)
            else:
                answer = self._build_kimi_unsupported_command_markdown(normalized_command or command, args)
            self._call_after_if_alive(self._on_done, turn_idx, answer, "", model, "", chat_id)
        except Exception as exc:
            self._call_after_if_alive(self._on_done, turn_idx, "", str(exc), model, "", chat_id)

    def _handle_kimi_clear_command(self, chat: dict, command_turn_idx: int | None = None, *, persist: bool = True) -> str:
        with self._kimi_owner_lock:
            cleared_session = self._kimi_session_id_for_chat(chat)
            self._retire_kimi_chat_runtime(chat, cleared_session)
            self._clear_kimi_information_for_session_change(chat, refresh=False)
            chat["kimi_session_id"] = ""
            chat["kimi_turn_id"] = ""
            chat["kimi_turn_active"] = False
            chat["kimi_pending_prompt"] = ""
            chat["kimi_request_queue"] = []
            turns = chat.get("turns")
            earlier_turns = turns[:command_turn_idx] if isinstance(turns, list) else []
            for turn in earlier_turns:
                if isinstance(turn, dict):
                    if (is_kimi_model(turn.get("model")) and not turn.get("local_command")
                            and turn.get("request_status") in {"pending", "running"}):
                        self._mark_turn_request_failed(turn, "Kimi Code 会话已清理。")
                        if turn.get("answer_md") == REQUESTING_TEXT:
                            turn["answer_md"] = turn["request_error"]
                    turn.pop("kimi_prompt_id", None)
                    turn.pop("kimi_owner_prompt_id", None)
                    turn.pop("kimi_prompt_role", None)
                    turn.pop("kimi_candidate_alias", None)
                    turn.pop("kimi_turn_id", None)
                    turn.pop("kimi_session_id", None)
                    turn.pop("kimi_unresolved_submission", None)
                    turn.pop("kimi_prompt_exact", None)
                    if is_kimi_model(turn.get("model")):
                        turn.pop("request_resume_token", None)
            if earlier_turns:
                self._mark_chat_turns_dirty(str(chat.get("id") or ""))
            if chat is self._current_chat_state:
                self._reset_active_kimi_session_state()
        self._refresh_kimi_information_for_session_change(chat)
        self._publish_kimi_information_session_change(chat)
        if persist:
            self._save_state()
        return "## Kimi Code 清理\n\n已清除当前聊天关联的 Kimi Code 会话状态。聊天记录不会被删除。"

    def _retire_kimi_chat_runtime(self, chat: dict, cleared_session: str) -> None:
        chat_id = str((chat or {}).get("id") or self.active_chat_id or self.current_chat_id or "").strip()
        if chat_id:
            self._kimi_execution_boundaries.pop(chat_id, None)
            for key in list(self._execution_delta_buffer):
                if key[0] == chat_id and len(key) > 6 and key[6] == cleared_session:
                    self._discard_execution_delta(key)
            with self._kimi_owner_lock:
                # Retire the lock and its session together before admitting
                # the first send in the cleared conversation.
                self._kimi_submission_locks.pop(chat_id, None)
                submitting_session = self._kimi_submission_sessions.pop(chat_id, None)
                self._kimi_active_turns.pop(chat_id, None)
                retired_sessions = {session for session in (cleared_session, submitting_session) if session}
                for interaction_key, request in list(self._kimi_interactions.items()):
                    if request.get("chat_id") == chat_id and request["state"] != "ended":
                        self._end_kimi_interaction(interaction_key, request)
                        self._call_after_if_alive(self._close_ended_kimi_dialog, interaction_key)
                        self._call_after_if_alive(self._finish_kimi_interaction_feedback, interaction_key, chat_id, False)
                doomed = [key for key in self._kimi_prompt_owners if key[0] == chat_id]
                doomed_owner_records = [
                    self._kimi_prompt_owners.get(key) for key in doomed
                ]
                for key in doomed:
                    self._add_kimi_tombstone(key)
                    self._kimi_prompt_owners.pop(key, None)
                    self._kimi_idle_proofs.pop(key, None)
                    self._kimi_early_events.pop((key[1], key[2]), None)
                doomed_intents = {
                    str(owner.get("submission_intent_id") or "").strip()
                    for owner in doomed_owner_records
                    if isinstance(owner, dict) and str(owner.get("submission_intent_id") or "").strip()
                }
                for early_key in list(self._kimi_early_events):
                    early_session, early_identity = early_key
                    if early_session in retired_sessions or early_identity.removeprefix("__intent__:") in doomed_intents:
                        self._kimi_early_events.pop(early_key, None)
                for key in list(self._kimi_turn_answer_parts):
                    if key and key[0] == chat_id:
                        self._kimi_turn_answer_parts.pop(key, None)
                for key in list(self._kimi_turn_answer_segments):
                    if key and key[0] == chat_id:
                        self._kimi_turn_answer_segments.pop(key, None)
                for session in retired_sessions:
                    self._kimi_pending_submissions.pop(session, None)
                    self._kimi_recovery_intents.pop(session, None)
                    self._kimi_recovery_requested_generation.pop(session, None)
                    self._kimi_incomplete_sessions.discard(session)
                if retired_sessions:
                    self._kimi_incomplete_streams = {
                        key for key in self._kimi_incomplete_streams if key[1] not in retired_sessions
                    }

    def _clear_kimi_information_for_session_change(self, chat: dict, *, refresh: bool = True) -> None:
        usage = context_usage_from_dict(chat.get("context_usage"))
        if usage is not None and usage.source == "kimi":
            chat["context_usage"] = None
        chat["kimi_session_total_tokens"] = None
        chat.pop("kimi_total_observed_at", None)
        chat.pop("kimi_usage_cache_owner", None)
        chat["kimi_context_error"] = False
        chat["kimi_snapshot_error"] = False
        chat["kimi_information_generation"] = int(chat.get("kimi_information_generation") or 0) + 1
        chat_id = str(chat.get("id") or "").strip()
        self._kimi_information_requests.pop(chat_id, None)
        for key in list(self._pending_context_usage_by_turn):
            if key[0] == chat_id:
                self._pending_context_usage_by_turn.pop(key, None)
        if refresh:
            self._refresh_kimi_information_for_session_change(chat)

    def _refresh_kimi_information_for_session_change(self, chat: dict) -> None:
        if bindings.app.threading.current_thread() is bindings.app.threading.main_thread():
            self._refresh_chat_information(chat)
        else:
            self._call_after_if_alive(self._refresh_chat_information, chat)

    def _publish_kimi_information_session_change(self, chat: dict) -> None:
        if bindings.app.threading.current_thread() is not bindings.app.threading.main_thread():
            self._call_after_if_alive(self._publish_kimi_information_session_change, chat)
            return
        if getattr(self, "_scoped_chat_information", None) is not None:
            self._information_subscriptions().changed(chat)
        dialog = getattr(self, "_chat_information_dialog", None)
        owner = self._visible_chat_information_owner()
        if dialog is None or dialog.IsBeingDeleted() or owner is None or owner[0] is not chat:
            return
        if dialog.identity[0] != str(chat.get("id") or ""):
            return
        dialog.identity = self._chat_information_identity(chat, owner[1])
        chat.pop("kimi_quota_payload", None)
        chat.pop("kimi_quota_updated_at", None)
        chat.pop("kimi_quota_owner", None)
        chat.pop("kimi_verified_account_id", None)
        dialog.set_rows(self._chat_information_rows(chat, owner[1]))
        self._request_kimi_quota(chat, owner[1])

    def _handle_kimi_stop_command(self, client, chat: dict, owner: dict | None, chat_id: str) -> str:
        session_id = self._kimi_session_id_for_chat(chat)
        with self._kimi_owner_lock:
            current = dict(self._kimi_active_turns.get(chat_id) or {})
        metadata = current if owner is None else owner
        prompt_id = str(metadata.get("prompt_id") or "").strip()
        heading = "## Kimi Code 中断\n\n"
        if getattr(client, "protocol_version", 1) == 2:
            fields = ("session_id", "prompt_id", "turn_id", "generation")
            if (not prompt_id or not metadata.get("turn_id")
                    or metadata.get("role") != "active"
                    or metadata.get("session_id") != session_id
                    or any(metadata.get(field) != current.get(field) for field in fields)):
                return heading + "当前没有可中断的 Kimi Code 任务。"
            client.abort(session_id, prompt_id)
            self._recover_kimi_pending_owners(session_ids={session_id})
            return heading + "服务器已受理中断请求，正在等待任务终止。"
        if not session_id or not (chat.get("kimi_turn_active") or metadata):
            return heading + "当前没有可中断的 Kimi Code turn。"
        if client is None or not hasattr(client, "abort"):
            return heading + "当前 Kimi Code 客户端不支持中断。"
        client.abort(session_id, prompt_id)
        return heading + f"已请求中断会话 `{session_id}` 的当前 turn。"

    def _build_kimi_status_markdown(self, client, chat: dict, model: str) -> str:
        session_id = self._kimi_session_id_for_chat(chat)
        turn_id = self._kimi_turn_id_for_chat(chat)
        turn_active = bool((chat or {}).get("kimi_turn_active")) or (chat is self._current_chat_state and bool(self.active_kimi_turn_active))
        queue = (chat or {}).get("kimi_request_queue")
        queue_count = len(queue) if isinstance(queue, list) else 0
        server_status = "未查询"
        if client is not None and session_id:
            try:
                status = client.get_status(session_id)
                context_tokens = status.get("context_tokens")
                max_tokens = status.get("max_context_tokens")
                if isinstance(context_tokens, (int, float)) and isinstance(max_tokens, (int, float)):
                    server_status = f"已连接，上下文 {int(context_tokens)}/{int(max_tokens)}"
                else:
                    server_status = "已连接"
            except Exception as exc:
                server_status = f"查询失败：{exc}"
        usage = format_context_usage_label(context_usage_from_dict((chat or {}).get("context_usage")))
        lines = [
            "## Kimi Code 状态",
            "",
            f"- 模型：{bindings.app.model_display_name(model) or model or DEFAULT_KIMI_MODEL}",
            f"- 工作目录：{self._workspace_dir_for_kimi()}",
            f"- 会话：{session_id or '未创建'}",
            f"- turn：{turn_id or '无'}（{'活跃' if turn_active else '空闲'}）",
            f"- 排队请求：{queue_count} 条",
            f"- 上下文：{usage or '未知'}",
            f"- 服务器：{server_status}",
        ]
        return "\n".join(lines)

    def _run_kimi_turn_worker(self, chat_id: str, turn_idx: int, question: str, model: str, from_recovery: bool = False) -> None:
        startup_request = self._capture_model_startup_request(chat_id, turn_idx)
        if startup_request is None:
            return
        submission_intent = None
        client_chat_id = str(chat_id or self.active_chat_id or self.current_chat_id or "").strip()
        if not client_chat_id:
            client_chat_id = self._ensure_active_chat_id()
        session_id = ""
        submission_lock = None

        def discard_stale_submission(prompt_id="") -> bool:
            with self._kimi_owner_lock:
                if (self._model_startup_request_is_current(chat_id, turn_idx, startup_request)
                        and self._kimi_submission_locks.get(client_chat_id) is submission_lock):
                    return False
                pending = self._kimi_pending_submissions.get(session_id, [])
                pending[:] = [intent for intent in pending if intent is not submission_intent]
                if not pending:
                    self._kimi_pending_submissions.pop(session_id, None)
                if prompt_id:
                    self._add_kimi_tombstone((client_chat_id, session_id, str(prompt_id)))
            return True

        try:
            submission_lock = self._kimi_submission_lock(client_chat_id)
            submission_lock.acquire()
            if discard_stale_submission():
                return
            target_chat, target_turns, _ = self._chat_target_for_request(client_chat_id)
            if (not isinstance(target_chat, dict) or not 0 <= turn_idx < len(target_turns)
                    or target_turns[turn_idx] is not startup_request[0]):
                return
            turn = startup_request[0]
            with self._kimi_owner_lock:
                known_active = self._kimi_active_turns.get(client_chat_id) or {}
                session_id = str(self._kimi_submission_sessions.get(client_chat_id)
                                 or known_active.get("session_id")
                                 or target_chat.get("kimi_session_id") or "").strip()
            attachments = turn.get("attachments")
            turn_attachments = [
                item for item in attachments
                if isinstance(item, dict) and item.get("status") == "success"
            ] if isinstance(attachments, list) else []
            existing_queue = target_chat.get("kimi_request_queue") or []
            history_turns = target_turns[:turn_idx]
            client = self._ensure_kimi_client()
            self._start_kimi_client_with_retry(client)
            if discard_stale_submission():
                return
            if session_id:
                exists_error: Exception | None = None
                for attempt in range(max(1, int(self._kimi_reconcile_attempts))):
                    try:
                        if not client.session_exists(session_id):
                            session_id = ""
                        exists_error = None
                        break
                    except Exception as exc:
                        if exists_error is None:
                            exists_error = exc
                        if attempt + 1 < max(1, int(self._kimi_reconcile_attempts)):
                            bindings.app.time.sleep(min(float(self._kimi_reconcile_backoff) * (2 ** attempt), 2.0))
                if exists_error is not None:
                    raise exists_error
            if session_id:
                try:
                    client.subscribe([session_id])
                except Exception:
                    # The client retains the requested subscription and owns
                    # bounded websocket recovery; REST submission can proceed.
                    pass
                send_question = str(question or "")
            else:
                send_question = self._build_kimi_history_recovery_prompt(history_turns, question) if history_turns else str(question or "")
                session_id = client.create_session(
                    cwd=self._workspace_dir_for_kimi(),
                    model=model,
                    title=str((target_chat.get("title") if isinstance(target_chat, dict) else "") or "kimi chat"),
                )
            if not self._model_startup_request_is_current(chat_id, turn_idx, startup_request):
                return
            with self._kimi_owner_lock:
                if self._kimi_submission_locks.get(client_chat_id) is not submission_lock:
                    return
                self._kimi_submission_sessions[client_chat_id] = session_id
            content_blocks = self._build_kimi_content_blocks(send_question, turn_attachments)
            exact_prompt_id = uuid.uuid4().hex if getattr(client, "protocol_version", 1) == 2 else ""
            with self._kimi_owner_lock:
                active_owner = dict(self._kimi_active_turns.get(client_chat_id) or {})
                existing_queue = existing_queue or any(
                    key[:2] == (client_chat_id, session_id) and owner.get("role") == "queued"
                    for key, owner in self._kimi_prompt_owners.items()
                )
            should_steer = bool(
                active_owner
                and str(active_owner.get("session_id") or "").strip() == session_id
                and str(active_owner.get("prompt_id") or "").strip()
                and str(active_owner.get("role") or "active") == "active"
            )
            submission_intent = {
                "submission_intent_id": uuid.uuid4().hex,
                "chat_id": client_chat_id,
                "session_id": session_id,
                "turn_idx": int(turn_idx),
                "turn_id": "",
                "question": str(question or "").strip(),
                "model": str(model or ""),
                "landed": False,
                "prompt_id": exact_prompt_id,
                "exact_prompt_id": bool(exact_prompt_id),
                "created_at": bindings.app.time.time(),
            }
            with self._kimi_owner_lock:
                if self._kimi_submission_locks.get(client_chat_id) is not submission_lock:
                    return
                if not should_steer and not existing_queue:
                    self._kimi_active_turns[client_chat_id] = {
                        "turn_idx": int(turn_idx), "session_id": session_id, "model": str(model or ""),
                    }
                for proof_key in list(self._kimi_idle_proofs):
                    if proof_key[1] == session_id:
                        self._kimi_idle_proofs.pop(proof_key, None)
                self._kimi_pending_submissions.setdefault(session_id, []).append(submission_intent)
                target_chat["kimi_idle_verified"] = None
            try:
                if exact_prompt_id:
                    prompt_id = client.submit_prompt(session_id, content_blocks, prompt_id=exact_prompt_id)
                else:
                    prompt_id = client.submit_prompt(session_id, content_blocks)
            except Exception as exc:
                if not bool(getattr(exc, "result_unknown", False)):
                    raise
                if discard_stale_submission(exact_prompt_id):
                    return
                prompt_id = exact_prompt_id or f"__unresolved__:{submission_intent['submission_intent_id']}"
                unresolved_owner = {
                    **submission_intent,
                    "prompt_id": prompt_id,
                    "owner_prompt_id": prompt_id,
                    "role": "unresolved",
                    "legacy": not bool(exact_prompt_id),
                    "unresolved_error": str(exc),
                }
                with self._kimi_owner_lock:
                    if not self._register_kimi_prompt_owner(unresolved_owner, submission_lock=submission_lock):
                        discard_stale_submission(exact_prompt_id)
                        return
                    if not should_steer and not existing_queue:
                        self._kimi_active_turns[client_chat_id] = dict(unresolved_owner)
                self._call_after_if_alive(
                    self._apply_kimi_startup_state,
                    client_chat_id,
                    turn_idx,
                    startup_request,
                    submission_lock,
                    {
                        **unresolved_owner,
                        "active": True,
                        "prompt_role": "unresolved",
                        "unresolved": True,
                    },
                )
                self._recover_kimi_pending_owners(
                    session_ids={session_id},
                    original_error=str(exc),
                )
                return
            if discard_stale_submission(prompt_id):
                return
            queued_entry = None
            prompt_role = "active"
            owner_prompt_id = str(prompt_id or "").strip()
            if existing_queue and not should_steer:
                prompt_role = "queued"
                queued_entry = {
                    "prompt_id": str(prompt_id or "").strip(),
                    "question": str(question or ""),
                    "turn_idx": int(turn_idx),
                    "model": str(model or ""),
                    "queued_at": bindings.app.time.time(),
                }
            if should_steer and str(prompt_id or "").strip():
                steer_result = client.steer_prompts(session_id, [prompt_id])
                if steer_result is True:
                    prompt_role = "alias"
                    owner_prompt_id = str(active_owner.get("owner_prompt_id") or active_owner.get("prompt_id") or "").strip()
                elif steer_result is False:
                    prompt_role = "queued"
                    queued_entry = {
                        "prompt_id": str(prompt_id or "").strip(),
                        "question": str(question or ""),
                        "turn_idx": int(turn_idx),
                        "model": str(model or ""),
                        "queued_at": bindings.app.time.time(),
                    }
                else:
                    prompt_role = "unresolved"
                    # A lost steer response may already have attached this
                    # prompt to the active server turn. Retain that candidate
                    # alias relationship until REST proves alias or queue.
                    owner_prompt_id = str(active_owner.get("owner_prompt_id") or active_owner.get("prompt_id") or "").strip()
            owner = {
                "chat_id": client_chat_id,
                "session_id": session_id,
                "turn_idx": int(turn_idx),
                "turn_id": "",
                "prompt_id": str(prompt_id or "").strip(),
                "question": str(question or "").strip(),
                "owner_prompt_id": owner_prompt_id,
                "role": prompt_role,
                "model": str(model or ""),
                "landed": False,
                "submission_intent_id": submission_intent["submission_intent_id"],
                "created_at": submission_intent["created_at"],
                "candidate_alias": prompt_role == "unresolved" and bool(should_steer),
                "exact_prompt_id": bool(exact_prompt_id),
            }
            if discard_stale_submission(prompt_id):
                return
            with self._kimi_owner_lock:
                if not self._register_kimi_prompt_owner(owner, submission_lock=submission_lock):
                    if discard_stale_submission(prompt_id):
                        return
                pending_submissions = self._kimi_pending_submissions.get(session_id, [])
                if submission_intent in pending_submissions:
                    pending_submissions.remove(submission_intent)
                if not pending_submissions:
                    self._kimi_pending_submissions.pop(session_id, None)
                if prompt_role == "active":
                    metadata = self._kimi_active_turns.get(client_chat_id)
                    if isinstance(metadata, dict):
                        metadata.update(owner)
            self._call_after_if_alive(
                self._apply_kimi_startup_state,
                client_chat_id,
                turn_idx,
                startup_request,
                submission_lock,
                {
                    "session_id": session_id,
                    "prompt_id": str(prompt_id or "").strip(),
                    "question": str(question or "").strip(),
                    "turn_idx": int(turn_idx),
                    "active": True,
                    "model": str(model or ""),
                    "queued_entry": queued_entry,
                    "prompt_role": prompt_role,
                    "owner_prompt_id": owner_prompt_id,
                    "candidate_alias": bool(owner.get("candidate_alias")),
                    "exact_prompt_id": bool(exact_prompt_id),
                    "unresolved": prompt_role == "unresolved",
                },
            )
            if prompt_role == "unresolved" and exact_prompt_id:
                self._recover_kimi_pending_owners(session_ids={session_id})
        except Exception as exc:
            with self._kimi_owner_lock:
                pending = self._kimi_pending_submissions.get(session_id, [])
                pending[:] = [
                    intent for intent in pending
                    if intent is not submission_intent
                ]
                if not pending:
                    self._kimi_pending_submissions.pop(session_id, None)
            self._call_after_if_alive(
                self._finish_kimi_startup_failure, client_chat_id, turn_idx, startup_request, submission_lock, str(exc), model,
            )
        finally:
            if submission_lock is not None:
                submission_lock.release()

    def _apply_kimi_startup_state(self, chat_id: str, turn_idx: int, request, submission_lock, payload: dict) -> None:
        session_id = str(payload.get("session_id") or "").strip()
        turn_id = str(payload.get("turn_id") or "").strip()
        prompt_id = str(payload.get("prompt_id") or "").strip()
        with self._kimi_owner_lock:
            if (not self._model_startup_request_is_current(chat_id, turn_idx, request)
                    or self._kimi_submission_locks.get(chat_id) is not submission_lock):
                return
            target_chat, is_current_target = self._kimi_target_chat(chat_id)
            if not isinstance(target_chat, dict):
                return
            session_changed = "session_id" in payload and str(target_chat.get("kimi_session_id") or "").strip() != session_id
            if "session_id" in payload:
                if session_changed:
                    self._clear_kimi_information_for_session_change(target_chat, refresh=False)
                target_chat["kimi_session_id"] = session_id
                if is_current_target:
                    self.active_kimi_session_id = session_id
            if "turn_id" in payload:
                target_chat["kimi_turn_id"] = turn_id
                if is_current_target:
                    self.active_kimi_turn_id = turn_id
            if "active" in payload:
                active = bool(payload.get("active"))
                target_chat["kimi_turn_active"] = active
                if is_current_target:
                    self.active_kimi_turn_active = active
                if active and str(payload.get("prompt_role") or "active") == "active":
                    self._remember_kimi_active_turn(chat_id, payload)
                elif not active:
                    self._clear_kimi_active_turn(chat_id)
            queued_entry = payload.get("queued_entry")
            if isinstance(queued_entry, dict) and queued_entry:
                queue = target_chat.get("kimi_request_queue")
                if not isinstance(queue, list):
                    queue = []
                    target_chat["kimi_request_queue"] = queue
                queue.append(dict(queued_entry))
                if is_current_target:
                    self.active_kimi_request_queue = queue
            target_turns = self.active_session_turns if is_current_target else target_chat.get("turns")
            turn = request[0]
            if session_id:
                turn["kimi_session_id"] = session_id
            if prompt_id:
                turn["kimi_prompt_id"] = prompt_id
                turn["kimi_prompt_question"] = str(payload.get("question") or turn.get("question") or "").strip()
                turn["kimi_prompt_role"] = str(payload.get("prompt_role") or "active")
                turn["kimi_owner_prompt_id"] = str(payload.get("owner_prompt_id") or prompt_id).strip()
                turn["kimi_candidate_alias"] = bool(payload.get("candidate_alias"))
                turn["kimi_unresolved_submission"] = bool(payload.get("unresolved"))
                turn["kimi_prompt_exact"] = bool(payload.get("exact_prompt_id"))
            if "turn_id" in payload:
                turn["kimi_turn_id"] = turn_id
            if session_id or turn_id:
                turn["request_resume_token"] = {"session_id": session_id, "turn_id": turn_id}
            self._mark_chat_turns_dirty(None if is_current_target else chat_id, turn_idx)

        # Persistence and native UI updates can wait without holding up other chats.
        if session_changed:
            self._refresh_kimi_information_for_session_change(target_chat)
        if "session_id" in payload:
            self._publish_kimi_information_session_change(target_chat)
        if prompt_id:
            if payload.get("prompt_role") == "alias":
                key = self._kimi_owner_key(chat_id, session_id, prompt_id)
                self._settle_kimi_alias_from_parent(turn, target_turns, key, payload["owner_prompt_id"])
            if payload.get("unresolved") and payload.get("exact_prompt_id"):
                self._save_state()
            self._replay_kimi_early_events({**payload, "chat_id": str(chat_id or "")})
        if not is_current_target:
            self._refresh_visible_history_chat(str(chat_id or "").strip())
        self._defer_codex_state_save()
        if is_current_target and self._kimi_interactions:
            self._call_after_if_alive(self._show_pending_kimi_interaction)

    def _on_kimi_client_message(self, message: dict) -> None:
        if not isinstance(message, dict):
            return
        message_type = str(message.get("type") or "").strip()
        if message_type == "messages_pending":
            client = self._kimi_client
            if client is None or not hasattr(client, "drain_pending_messages"):
                return
            try:
                messages = client.drain_pending_messages()
            except Exception:
                return
            for drained in messages:
                self._on_kimi_client_message(drained)
            return
        if message_type == "transport_error":
            payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
            error_text = str(payload.get("error") or "Kimi Code 连接中断").strip()
            session_ids = {
                str(value or "").strip()
                for value in (payload.get("session_ids") or [])
                if str(value or "").strip()
            }
            if not session_ids:
                session_ids = {
                    str(owner.get("session_id") or "").strip()
                    for owner in self._kimi_pending_owners()
                    if str(owner.get("session_id") or "").strip()
                }
            with self._kimi_owner_lock:
                self._kimi_incomplete_sessions.update(session_ids)
                for owner in self._kimi_pending_owners(session_ids or None):
                    owner_id = str(owner.get("owner_prompt_id") or owner.get("prompt_id") or "").strip()
                    if owner_id:
                        self._kimi_incomplete_streams.add((
                            str(owner.get("chat_id") or ""),
                            str(owner.get("session_id") or ""),
                            owner_id,
                            "main",
                            "__transport__",
                        ))
            self._recover_kimi_pending_owners(
                session_ids=session_ids or None,
                original_error=error_text,
            )
            return
        if message_type in {"transport_recovered", "resync_required"}:
            payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
            session_ids = {
                str(value or "").strip()
                for value in (payload.get("session_ids") or [])
                if str(value or "").strip()
            }
            if message_type == "resync_required":
                with self._kimi_owner_lock:
                    self._kimi_incomplete_sessions.update(session_ids)
                    for owner in self._kimi_pending_owners(session_ids or None):
                        owner_id = str(owner.get("owner_prompt_id") or owner.get("prompt_id") or "").strip()
                        if owner_id:
                            self._kimi_incomplete_streams.add((
                                str(owner.get("chat_id") or ""),
                                str(owner.get("session_id") or ""), owner_id, "main", "__transport__"
                            ))
            self._recover_kimi_pending_owners(session_ids=session_ids or None)
            return
        if message_type != "event":
            return
        payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
        event_payload = payload.get("event") if isinstance(payload.get("event"), dict) else payload
        try:
            kimi_event = kimi_event_from_payload(event_payload)
        except Exception:
            return
        event = CodexEvent(**kimi_event_to_payload(kimi_event))
        if (event.data or {}).get("source_kind") == "interaction":
            self._receive_kimi_interaction(event)
            return
        prompt_id = self._kimi_event_prompt_id(event)
        if self._buffer_kimi_early_event(event):
            return
        if prompt_id:
            owner = self._find_kimi_prompt_owner(
                prompt_id,
                session_id=self._event_thread_id(event),
                include_unlanded=True,
            )
            if not isinstance(owner, dict):
                # Unknown/late external prompts are never allowed to grow the
                # early-event cache or attach to a visible chat by session.
                return
            if self._kimi_owner_key_for(owner) in self._kimi_terminal_tombstones:
                return
        chat_id = self._resolve_kimi_event_chat_id(event)
        if not chat_id:
            return
        self._dispatch_kimi_event_to_ui(chat_id, event)

    def _apply_kimi_transport_error(self, message: str) -> None:
        self._recover_kimi_pending_owners(original_error=str(message or "Kimi Code 连接中断"))

    @staticmethod
    def _kimi_message_text(message: dict) -> str:
        if bindings.app.ChatFrame._kimi_private_reasoning(message):
            return ""
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        parts = []
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if bindings.app.ChatFrame._kimi_private_reasoning(block):
                    continue
                if str(block.get("type") or "").strip() not in {"", "text", "output_text"}:
                    continue
                text = str(block.get("text") or block.get("output_text") or "")
                if text:
                    parts.append(text)
        return "".join(parts).strip()

    @staticmethod
    def _kimi_is_user_prompt(message: dict) -> bool:
        """Only an explicit structured injection may bypass a user boundary."""
        if not isinstance(message, dict) or str(message.get("role") or "").strip() != "user":
            return False
        metadata = message.get("metadata")
        origin = (metadata["origin"] if isinstance(metadata, dict) and "origin" in metadata
                  else message.get("origin"))
        return not (isinstance(origin, dict) and origin.get("kind") == "injection")

    @staticmethod
    def _kimi_message_thinking(message: dict) -> list[str]:
        """Return the thinking block texts of a transcript message.

        Blocks marked private / non-disclosable are skipped, mirroring
        ``_kimi_private_reasoning`` semantics for both block and message
        metadata.
        """
        if not isinstance(message, dict):
            return []
        content = message.get("content")
        if not isinstance(content, list):
            return []
        message_private = bindings.app.ChatFrame._kimi_private_reasoning(message)
        texts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if str(block.get("type") or "").strip() != "thinking":
                continue
            if message_private or bindings.app.ChatFrame._kimi_private_reasoning(block):
                continue
            text = str(block.get("text") or block.get("thinking") or "").strip()
            if text:
                texts.append(text)
        return texts

    @staticmethod
    def _kimi_thinking_excerpt(text: str, limit: int = 80) -> str:
        """First non-empty line of a thinking block, trimmed, ellipsis on truncation."""
        for line in str(text or "").splitlines():
            stripped = line.strip()
            if stripped:
                if len(stripped) <= limit:
                    return stripped
                return stripped[: max(1, limit - 1)].rstrip() + "…"
        return ""

    @staticmethod
    def _kimi_oldest_message_id(rows: list[dict]) -> str:
        candidates = [
            (str(row.get("created_at") or ""), idx, str(row.get("id") or "").strip())
            for idx, row in enumerate(rows)
            if isinstance(row, dict) and str(row.get("id") or "").strip()
        ]
        if not candidates:
            return ""
        if all(created for created, _idx, _mid in candidates):
            _created, _idx, message_id = min(candidates, key=lambda item: (item[0], item[1]))
            return message_id
        # No timestamps: the endpoint returns newest-first.
        return candidates[-1][2]

    def _kimi_messages_back_to_boundary(
        self,
        client,
        session_id: str,
        prompt_id: str,
        *,
        messages: list[dict] | None = None,
        max_pages: int = 10,
        deadline: float | None = None,
    ) -> list[dict]:
        """Accumulate transcript pages (oldest-first) until the boundary user message is included.

        The messages endpoint only returns the newest window, so an older
        boundary prompt scrolls out; page back with the ``before_id`` cursor
        until it is found again (or the page cap is reached).
        """
        prompt_id = str(prompt_id or "").strip()
        if messages is None:
            rows = list(self._kimi_call_with_deadline(client.list_messages, session_id, deadline=deadline))
        else:
            rows = [dict(row) for row in messages if isinstance(row, dict)]
        pages = 1
        while prompt_id and pages < max(1, int(max_pages)):
            if any(
                isinstance(row, dict)
                and self._kimi_is_user_prompt(row)
                and str(row.get("id") or "").strip() == prompt_id
                for row in rows
            ):
                break
            oldest_id = self._kimi_oldest_message_id(rows)
            if not oldest_id:
                break
            older = list(
                self._kimi_call_with_deadline(
                    client.list_messages,
                    session_id,
                    deadline=deadline,
                    before_id=oldest_id,
                )
            )
            if not older:
                break
            seen_ids = {str(row.get("id") or "").strip() for row in rows if isinstance(row, dict)}
            rows = [
                dict(row)
                for row in older
                if isinstance(row, dict) and str(row.get("id") or "").strip() not in seen_ids
            ] + rows
            pages += 1
        return rows

    def _kimi_thinking_sync_due(self, session_id: str) -> bool:
        """Per-session throttle so REST thinking fetches stay cheap.

        Optimistically stamps the throttle before the fetch runs: a fetch that
        fails immediately still waits a full interval before retrying, which
        is the intended back-off behavior.
        """
        session_id = str(session_id or "").strip()
        if not session_id:
            return False
        now = bindings.app.time.monotonic()
        with self._kimi_owner_lock:
            last = self._kimi_thinking_sync_at.get(session_id, 0.0)
            if now - last < float(self._kimi_thinking_sync_interval):
                return False
            self._kimi_thinking_sync_at[session_id] = now
        return True

    def _kimi_rest_answer_for_prompt(self, messages: list[dict], prompt_id: str) -> str:
        """Return only the final assistant text inside one user-message boundary."""
        answer, _closed = self._kimi_rest_answer_boundary(messages, prompt_id)
        return answer

    def _kimi_rest_answer_boundary(self, messages: list[dict], prompt_id: str) -> tuple[str, bool]:
        """Return the answer and whether a later user message closed its boundary."""
        rows = [dict(item) for item in messages or [] if isinstance(item, dict)]
        if rows and all(str(row.get("created_at") or "").strip() for row in rows):
            rows = [row for _idx, row in sorted(enumerate(rows), key=lambda pair: (str(pair[1].get("created_at")), pair[0]))]
        boundary = -1
        for idx, row in enumerate(rows):
            if self._kimi_is_user_prompt(row) and str(row.get("id") or "").strip() == prompt_id:
                boundary = idx
                break
        if boundary < 0:
            return "", False
        answer = ""
        closed = False
        for row in rows[boundary + 1:]:
            role = str(row.get("role") or "").strip()
            if self._kimi_is_user_prompt(row):
                closed = True
                break
            if role == "assistant":
                content = row.get("content")
                if isinstance(content, list) and any(isinstance(block, dict) and block.get("type") == "tool_use" for block in content):
                    continue
                text = self._kimi_message_text(row)
                if text:
                    answer = text
        return answer, closed

    def _recover_kimi_pending_owners(
        self,
        *,
        session_ids: set[str] | None = None,
        original_error: str = "",
    ) -> None:
        requested = {
            str(value or "").strip()
            for value in (session_ids or set())
            if str(value or "").strip()
        }
        owners = self._kimi_pending_owners(requested or None)
        sessions = requested or {
            str(owner.get("session_id") or "").strip()
            for owner in owners
            if str(owner.get("session_id") or "").strip()
        }
        for session_id in sessions:
            with self._kimi_owner_lock:
                requested_generation = self._kimi_recovery_requested_generation.get(session_id, 0) + 1
                self._kimi_recovery_requested_generation[session_id] = requested_generation
                if original_error:
                    self._kimi_recovery_intents[session_id] = str(original_error)
                else:
                    self._kimi_recovery_intents.setdefault(session_id, "")
                if session_id in self._kimi_recovery_workers:
                    continue
                self._kimi_recovery_workers.add(session_id)
            bindings.app.threading.Thread(
                target=self._reconcile_kimi_session_worker,
                args=(session_id,),
                daemon=True,
            ).start()

    def _reconcile_kimi_session_worker(self, session_id: str, generation: int | None = None) -> None:
        """One recovery coordinator per session, shared by every owner."""
        handled_generation = int(generation or 0)
        try:
            while True:
                with self._kimi_owner_lock:
                    handled_generation = max(
                        handled_generation,
                        self._kimi_recovery_requested_generation.get(session_id, 0),
                    )
                    original_error = self._kimi_recovery_intents.get(session_id, "")
                owners = self._kimi_pending_owners({session_id})
                if not owners:
                    return
                grouped: dict[tuple[str, str, str], dict] = {}
                for owner in owners:
                    group_key = (
                        str(owner.get("chat_id") or "").strip(),
                        session_id,
                        str((owner.get("owner_prompt_id") if owner.get("role") == "alias" else None)
                            or owner.get("prompt_id") or "").strip(),
                    )
                    existing = grouped.get(group_key)
                    if existing is None or (
                        str(owner.get("role") or "") == "active"
                        and str(existing.get("role") or "") != "active"
                    ):
                        grouped[group_key] = owner
                owner_budget = max(
                    5.0,
                    sum(
                        min(float(self._kimi_reconcile_backoff) * (2 ** attempt), 2.0)
                        for attempt in range(max(1, int(self._kimi_reconcile_attempts)))
                    ) + 1.0,
                )
                monitoring = False
                for owner in grouped.values():
                    owner_with_deadline = dict(owner)
                    owner_with_deadline["_recovery_deadline"] = bindings.app.time.monotonic() + owner_budget
                    outcome = self._reconcile_kimi_owner_worker(owner_with_deadline, original_error)
                    monitoring = monitoring or outcome == "monitor"
                remaining = self._kimi_pending_owners({session_id})
                if not remaining:
                    return
                with self._kimi_owner_lock:
                    requested_generation = self._kimi_recovery_requested_generation.get(session_id, 0)
                if requested_generation <= handled_generation:
                    # A healthy busy session is event-driven.  Keep owners
                    # pending without a short failure countdown.
                    return
        finally:
            handoff_generation = 0
            with self._kimi_owner_lock:
                self._kimi_recovery_workers.discard(session_id)
                if not self._kimi_pending_owners({session_id}):
                    self._kimi_recovery_intents.pop(session_id, None)
                    self._kimi_recovery_requested_generation.pop(session_id, None)
                elif self._kimi_recovery_requested_generation.get(session_id, 0) > handled_generation:
                    handoff_generation = self._kimi_recovery_requested_generation[session_id]
                    self._kimi_recovery_workers.add(session_id)
            if handoff_generation:
                bindings.app.threading.Thread(
                    target=self._reconcile_kimi_session_worker,
                    args=(session_id,),
                    daemon=True,
                ).start()

    def _reconcile_kimi_owner_worker(
        self,
        owner: dict,
        original_error: str = "",
    ) -> str:
        session_id = str(owner.get("session_id") or "").strip()
        prompt_id = str(owner.get("prompt_id") or "").strip()
        deadline_value = owner.get("_recovery_deadline")
        deadline = float(deadline_value) if isinstance(deadline_value, (int, float)) else None
        last_error = ""
        last_was_exception = False
        max_attempts = max(1, int(self._kimi_reconcile_attempts))
        attempt = 0
        terminal_wait = False
        while True:
                if deadline is not None and bindings.app.time.monotonic() >= deadline:
                    break
                if attempt or terminal_wait:
                    if terminal_wait:
                        # Idle-before-persist race: the session already ended
                        # but the assistant text has not landed yet. Poll
                        # gently within the recovery deadline instead of
                        # burning exponential retry attempts. (terminal_wait
                        # is only ever set when a deadline exists.)
                        delay = min(0.5, max(0.0, deadline - bindings.app.time.monotonic()))
                    else:
                        delay = min(float(self._kimi_reconcile_backoff) * (2 ** (attempt - 1)), 2.0)
                        if deadline is not None:
                            delay = min(delay, max(0.0, deadline - bindings.app.time.monotonic()))
                    if delay:
                        bindings.app.time.sleep(delay)
                if deadline is not None and bindings.app.time.monotonic() >= deadline:
                    break
                try:
                    client = self._ensure_kimi_client()
                    self._start_kimi_client_with_retry(client, deadline=deadline)
                    self._kimi_call_with_deadline(
                        client.subscribe,
                        [session_id],
                        deadline=deadline,
                    )
                    if getattr(client, "protocol_version", 1) == 2:
                        if bool(owner.get("legacy")) or str(owner.get("role") or "") == "unresolved":
                            messages = self._kimi_call_with_deadline(client.recovery_messages_v2, session_id, deadline=deadline)
                            known = any(str(row.get("id") or "") == prompt_id and self._kimi_is_user_prompt(row)
                                        for row in messages)
                            if not known and not owner.get("exact_prompt_id"):
                                question = str(owner.get("question") or "").strip()
                                matches = [row for row in messages if self._kimi_is_user_prompt(row)
                                           and self._kimi_message_text(row) == question]
                                if len(matches) != 1:
                                    last_error = "Kimi V2 prompt attribution is missing or ambiguous"
                                    last_was_exception = False
                                    break
                            migrated = self._migrate_legacy_kimi_owner(owner, messages)
                            if not isinstance(migrated, dict):
                                last_error = "Kimi V2 transcript has no authoritative identity for this prompt"
                                last_was_exception = False
                                if owner.get("exact_prompt_id") and deadline is not None:
                                    terminal_wait = True
                                    continue
                                break
                            owner = migrated
                            prompt_id = str(owner.get("owner_prompt_id") if owner.get("role") == "alias" else owner.get("prompt_id") or "").strip()
                        event = self._kimi_call_with_deadline(client.reconcile_v2, session_id, prompt_id, deadline=deadline)
                        if event is not None and event.type == "thread_status_changed" and event.status in {"running", "queued", "blocked"}:
                            return "monitor"
                        if event is not None:
                            event.data["owner_generation"] = int(owner.get("generation") or 0)
                            self._call_after_if_alive(self._on_kimi_event, CodexEvent(**kimi_event_to_payload(event)))
                            return "done"
                        last_error = "Kimi V2 transcript has no complete authoritative answer for this prompt"
                        last_was_exception = False
                        if deadline is not None:
                            terminal_wait = True
                            continue
                        attempt += 1
                        if attempt >= max_attempts:
                            break
                        continue
                    status = self._kimi_call_with_deadline(
                        client.get_status,
                        session_id,
                        deadline=deadline,
                    )
                    busy = status.get("busy")
                    terminal_state = str(status.get("status") or status.get("state") or "").strip().lower()
                    if terminal_state in {"failed", "interrupted", "aborted", "cancelled", "canceled"}:
                        failure = (
                            original_error
                            or str(status.get("error") or status.get("message") or status.get("reason") or "").strip()
                            or ("Kimi Code 已中断。" if terminal_state in {"interrupted", "aborted", "cancelled", "canceled"} else "Kimi Code 执行失败。")
                        )
                        self._call_after_if_alive(
                            self._apply_kimi_error,
                            str(owner.get("chat_id") or "").strip(),
                            failure,
                            owner.get("turn_idx"),
                            str(owner.get("turn_id") or "").strip() or None,
                            str(owner.get("prompt_id") or "").strip() or None,
                            int(owner.get("generation") or 0) or None,
                        )
                        return "done"
                    terminal = terminal_state in {"completed", "done", "idle"}
                    messages = self._kimi_call_with_deadline(
                        client.list_messages,
                        session_id,
                        deadline=deadline,
                    )
                    if bool(owner.get("legacy")) or str(owner.get("role") or "") == "unresolved":
                        migrated = self._migrate_legacy_kimi_owner(owner, messages)
                        if not isinstance(migrated, dict):
                            last_error = "Kimi Code could not unambiguously migrate the archived prompt"
                            last_was_exception = False
                            attempt += 1
                            if attempt >= max_attempts:
                                break
                            continue
                        owner = migrated
                        prompt_id = str(owner.get("prompt_id") or "").strip()
                    boundary_prompt_id = self._kimi_reconciliation_boundary(owner)
                    rows = self._kimi_messages_back_to_boundary(
                        client,
                        session_id,
                        boundary_prompt_id,
                        messages=messages,
                        deadline=deadline,
                    )
                    if self._kimi_thinking_sync_due(session_id):
                        try:
                            # Use the fully paged rows before delivering small
                            # projection batches to the UI.
                            self._schedule_kimi_thinking_sync(
                                str(owner.get("chat_id") or "").strip(),
                                owner,
                                rows,
                            )
                        except Exception:
                            # Fetch/shape failure: keep last good rows and
                            # retry on the next monitor iteration.
                            pass
                    answer, boundary_closed = self._kimi_rest_answer_boundary(rows, boundary_prompt_id)
                    if busy is not False and not terminal and not boundary_closed:
                        return "monitor"
                    if not answer:
                        last_error = "Kimi Code transcript has no final answer for this prompt"
                        last_was_exception = False
                        if deadline is not None:
                            # Terminal (or boundary closed) without persisted
                            # answer text: keep monitoring until the recovery
                            # deadline before surfacing the error.
                            terminal_wait = True
                            continue
                        attempt += 1
                        if attempt >= max_attempts:
                            break
                        continue
                    data = {
                        "prompt_id": prompt_id,
                        "agent_id": "main",
                        "source_kind": "rest.reconciled",
                        "idle_verified": busy is False,
                        "terminal_verified": terminal,
                        "stream_complete": True,
                        "turn_idx": owner.get("turn_idx"),
                        "owner_generation": int(owner.get("generation") or 0),
                    }
                    event = CodexEvent(
                        type="turn_completed",
                        thread_id=session_id,
                        turn_id=str(owner.get("turn_id") or "").strip(),
                        text=answer,
                        status="completed",
                        data=data,
                    )
                    self._call_after_if_alive(self._on_kimi_event, event)
                    return "done"
                except Exception as exc:
                    last_error = str(exc)
                    last_was_exception = True
                    if terminal_wait:
                        # A transient failure during the gentle terminal poll
                        # must not burn retry attempts or drop the poll mode;
                        # the recovery deadline bounds the wait.
                        continue
                    attempt += 1
                    if attempt >= max_attempts:
                        break
        fallback_error = (
            "Kimi recovery deadline exhausted"
            if deadline is not None and bindings.app.time.monotonic() >= deadline
            else "Kimi Code 未返回任何内容。"
        )
        failure = original_error or last_error or fallback_error
        self._call_after_if_alive(
            self._apply_kimi_error,
            str(owner.get("chat_id") or "").strip(),
            failure,
            owner.get("turn_idx"),
            str(owner.get("turn_id") or "").strip() or None,
            str(owner.get("prompt_id") or "").strip() or None,
            int(owner.get("generation") or 0) or None,
        )
        return "done"

    def _kimi_reconciliation_boundary(self, owner: dict) -> str:
        owner_prompt_id = str(owner.get("owner_prompt_id") or owner.get("prompt_id") or "").strip()
        chat_id = str(owner.get("chat_id") or "").strip()
        session_id = str(owner.get("session_id") or "").strip()
        with self._kimi_owner_lock:
            aliases = [
                candidate for candidate in self._kimi_prompt_owners.values()
                if isinstance(candidate, dict)
                and str(candidate.get("chat_id") or "").strip() == chat_id
                and str(candidate.get("session_id") or "").strip() == session_id
                and str(candidate.get("owner_prompt_id") or candidate.get("prompt_id") or "").strip() == owner_prompt_id
            ]
        if not aliases:
            return str(owner.get("prompt_id") or "").strip()
        latest = max(aliases, key=lambda item: int(item.get("generation") or 0))
        return str(latest.get("prompt_id") or owner.get("prompt_id") or "").strip()

    def _migrate_legacy_kimi_owner(self, owner: dict, messages: list[dict]) -> dict | None:
        question = str(owner.get("question") or "").strip()
        rows = [row for row in messages or [] if isinstance(row, dict)]
        if rows and all(str(row.get("created_at") or "").strip() for row in rows):
            rows.sort(key=lambda row: str(row["created_at"]))
        known_prompt_id = str(owner.get("prompt_id") or "").strip()
        matched = next((row for row in rows if str(row.get("id") or "").strip() == known_prompt_id
                        and known_prompt_id and self._kimi_is_user_prompt(row)), None)
        if matched is None and question and not owner.get("exact_prompt_id"):
            matches = [
                row for row in rows if self._kimi_is_user_prompt(row)
                and self._kimi_message_text(row) == question and str(row.get("id") or "").strip()
            ]
            if len(matches) == 1:
                matched = matches[0]
            elif len(matches) > 1:
                owner_created_at = self._kimi_timestamp_value(owner.get("created_at"))
                timed = [
                    (abs(message_created_at - owner_created_at), row) for row in matches
                    if owner_created_at is not None
                    and (message_created_at := self._kimi_timestamp_value(row.get("created_at"))) is not None
                    and abs(message_created_at - owner_created_at) <= 300.0
                ]
                timed.sort(key=lambda item: item[0])
                if timed and (len(timed) == 1 or timed[0][0] < timed[1][0]):
                    matched = timed[0][1]
        if not isinstance(matched, dict):
            return None
        prompt_id = str(matched.get("id") or "").strip()
        migrated = dict(owner)
        old_key = self._kimi_owner_key_for(owner)
        role = "active"
        owner_prompt_id = prompt_id
        if owner.get("exact_prompt_id"):
            parent = str(matched.get("owner_prompt_id") or "").strip()
            if parent:
                role, owner_prompt_id = "alias", parent
            elif matched.get("prompt_status") == "queued":
                role = "queued"
            elif owner.get("candidate_alias") and not matched.get("independent_turn"):
                # A steer may already have left the queue while its structured
                # user frame is still being committed. Wait for either proof.
                return None
        elif bool(owner.get("candidate_alias")):
            candidate_owner_id = str(owner.get("owner_prompt_id") or "").strip()
            positions = {
                str(row.get("id") or "").strip(): idx
                for idx, row in enumerate(rows)
                if self._kimi_is_user_prompt(row) and str(row.get("id") or "").strip()
            }
            start = positions.get(candidate_owner_id)
            current = positions.get(prompt_id)
            assistant_between = False
            if isinstance(start, int) and isinstance(current, int) and start < current:
                assistant_between = any(
                    isinstance(row, dict) and str(row.get("role") or "").strip() == "assistant"
                    for row in rows[start + 1:current]
                )
            if isinstance(start, int) and isinstance(current, int) and start < current and not assistant_between:
                role = "alias"
                owner_prompt_id = candidate_owner_id
            else:
                role = "queued"
        migrated.update({
            "prompt_id": prompt_id,
            "owner_prompt_id": owner_prompt_id,
            "role": role,
            "legacy": False,
            "landed": False,
            "candidate_alias": False,
        })
        new_key = self._kimi_owner_key_for(migrated)
        with self._kimi_owner_lock:
            self._kimi_prompt_owners.pop(old_key, None)
            self._kimi_prompt_owners[new_key] = dict(migrated)
            pending = self._kimi_pending_submissions.get(str(owner.get("session_id") or "").strip(), [])
            intent_id = str(owner.get("submission_intent_id") or "").strip()
            if intent_id:
                pending[:] = [
                    intent for intent in pending
                    if str(intent.get("submission_intent_id") or "").strip() != intent_id
                ]
            if not pending:
                self._kimi_pending_submissions.pop(str(owner.get("session_id") or "").strip(), None)
        if wx.IsMainThread():
            self._apply_kimi_owner_migration(dict(migrated))
        else:
            self._call_after_if_alive(self._apply_kimi_owner_migration, dict(migrated))
        return migrated

    def _apply_kimi_owner_migration(self, migrated: dict) -> None:
        """Commit recovered owner fields on the UI thread after revalidation."""
        key = self._kimi_owner_key_for(migrated)
        with self._kimi_owner_lock:
            current = self._kimi_prompt_owners.get(key)
            if not isinstance(current, dict) or int(current.get("generation") or 0) != int(migrated.get("generation") or 0):
                return
        target_chat, is_current = self._kimi_target_chat(str(migrated.get("chat_id") or ""))
        turns = self.active_session_turns if is_current else ((target_chat or {}).get("turns") if isinstance(target_chat, dict) else [])
        idx = migrated.get("turn_idx")
        if isinstance(turns, list) and isinstance(idx, int) and 0 <= idx < len(turns) and isinstance(turns[idx], dict):
            turn = turns[idx]
            if turn.get("request_status") != "pending":
                return
            turn["kimi_prompt_id"] = key[2]
            turn["kimi_owner_prompt_id"] = str(migrated.get("owner_prompt_id") or "")
            turn["kimi_prompt_role"] = str(migrated.get("role") or "")
            turn["kimi_candidate_alias"] = bool(migrated.get("candidate_alias"))
            turn["kimi_unresolved_submission"] = False
            if migrated.get("role") == "alias":
                self._settle_kimi_alias_from_parent(turn, turns, key, migrated["owner_prompt_id"])
            elif migrated.get("role") == "active":
                with self._kimi_owner_lock:
                    active = self._kimi_active_turns.get(key[0])
                    if not active or active.get("prompt_id") == key[2]:
                        self._kimi_active_turns[key[0]] = dict(migrated)
            self._mark_chat_turns_dirty(None if is_current else str(migrated.get("chat_id") or ""), idx)
            # Migration changes the durable prompt identity.  Do not leave it
            # only in RAM until a later unrelated monitor tick.
            self._save_state()
            self._replay_kimi_early_events(migrated)

    def _settle_kimi_alias_from_parent(self, turn: dict, turns: list, key: tuple[str, str, str],
                                     parent_prompt_id: str) -> None:
        parent = next((row for row in turns if isinstance(row, dict) and row.get("kimi_session_id") == key[1]
                       and row.get("kimi_prompt_id") == parent_prompt_id), None)
        if parent is None or parent.get("request_status") not in {"done", "failed"}:
            return
        if parent["request_status"] == "done":
            answer_identity = parent.get("answer_identity")
            if not answer_identity:
                return
            turn.update(answer_md="", answer_identity=answer_identity, answer_alias_of=answer_identity,
                        suppress_empty_answer_row=True)
            self._mark_turn_request_done(turn)
        else:
            error = str(parent.get("request_error") or parent.get("answer_md") or "")
            turn["answer_md"] = error
            self._mark_turn_request_failed(turn, error)
        with self._kimi_owner_lock:
            self._add_kimi_tombstone(key)
            self._kimi_prompt_owners.pop(key, None)
            self._kimi_early_events.pop((key[1], key[2]), None)

    @staticmethod
    def _kimi_timestamp_value(value) -> float | None:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            pass
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return bindings.app.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError, OverflowError):
            return None

    def _reset_active_kimi_session_state(self) -> None:
        self.active_kimi_session_id = ""
        self.active_kimi_turn_id = ""
        self.active_kimi_turn_active = False
        self.active_kimi_pending_prompt = ""
        self.active_kimi_request_queue = []

    def _write_active_kimi_session_state_to_chat(self, chat: dict) -> None:
        chat["kimi_session_id"] = self.active_kimi_session_id
        chat["kimi_turn_id"] = self.active_kimi_turn_id
        chat["kimi_turn_active"] = self.active_kimi_turn_active
        chat["kimi_pending_prompt"] = self.active_kimi_pending_prompt
        chat["kimi_request_queue"] = self.active_kimi_request_queue
