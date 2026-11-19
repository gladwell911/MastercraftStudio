"""Kimi events, visible output, and user interactions."""

import copy
import hashlib
import json
import wx
from codex_client import CodexEvent
from collections import deque
from kimi_server_client import DEFAULT_KIMI_MODEL, KimiServerError, is_kimi_model
from mc_frame_settings import (
    CODEX_UI_EVENT_BATCH_DELAY_MS,
    CODEX_UI_EVENT_BATCH_SIZE,
    CODEX_UI_INTERACTIVE_EVENT_BATCH_SIZE,
    REQUESTING_TEXT,
)

import mc_frame_bindings as bindings


class KimiEventsMixin:
    def _redact_kimi_answer_stream(self, chat_id: str, session_id: str, agent_id: str, item_id: str) -> None:
        for key, segments in self._kimi_turn_answer_segments.items():
            if key[0] != chat_id or key[1] != session_id or key[3] != agent_id or key[4] != item_id:
                continue
            segments.clear()
            # Legacy aliases share this list. Remove protected bytes from the
            # cache as well as the segmented answer consumer.
            parts = self._kimi_turn_answer_parts.get(key[:4])
            if isinstance(parts, list):
                parts[:] = [text for other, values in self._kimi_turn_answer_segments.items()
                            if other[:4] == key[:4] for text in values.values()]

    def _redact_kimi_narrative(self, chat_id: str, previous: dict) -> bool:
        safe = dict(previous, private_reasoning=True, narrative_snapshot=True,
                    list_text="", kimi_summary="", detail_text="", raw_text="", text="",
                    source_detail={}, source_detail_history=[], diagnostic_history=[])
        item_id = str(previous.get("item_id") or "")
        session_id = str(previous.get("session_id") or previous.get("thread_id") or "")
        agent_id = str(previous.get("agent_id") or "main")
        self._redact_kimi_answer_stream(chat_id, session_id, agent_id, item_id)
        for key, state in self._execution_delta_buffer.items():
            if (key[:3] != (chat_id, str(previous.get("turn_id") or ""), item_id)
                    or (key[4] or "main") != agent_id):
                continue
            state.update(parts=[], segments={}, conflicts=[], private_reasoning=True, pending_gap=False)
            base = state.get("event")
            if isinstance(base, CodexEvent):
                base = copy.copy(base)
                base.text = base.raw_text = ""
                base.data = {**base.data, "non_disclosable": True}
                state["event"] = base
        store = getattr(self, "chat_store", None) if getattr(self, "_chat_store_enabled", False) else None
        if store is not None and previous.get("logical_key") and previous.get("logical_scope"):
            store.apply_execution_fragment(chat_id, previous["logical_key"], previous["logical_scope"],
                                           fragment_id="", offset=0, text="", projection_seed=safe)
        return self._append_execution_entry_to_chat(chat_id, safe, save_state=True)

    def _kimi_sync_thinking_rows(self, chat_id: str, owner: dict, messages: list[dict]) -> bool:
        """Complete owned public streams from the main-agent REST transcript.

        SDK REST messages have no step ID. Within a complete prompt boundary,
        assistant order is the step order; text/tool_use explicitly identifies
        commentary. No prose matching or subagent-to-main fallback is used.
        """
        try:
            chat_id = str(chat_id or "").strip()
            session_id = str((owner or {}).get("session_id") or "").strip()
            turn_idx = (owner or {}).get("turn_idx")
            if not chat_id or not session_id or not isinstance(turn_idx, int) or turn_idx < 0:
                return False
            chat = self._chat_state_for_execution_steps(chat_id)
            turns = chat.get("turns", []) if isinstance(chat, dict) else []
            if turn_idx >= len(turns) or not isinstance(turns[turn_idx], dict):
                return False
            turn = turns[turn_idx]
            revision = self._safe_int(chat.get("revision", 1), 1)
            turn_id = str(owner.get("turn_id") or turn.get("kimi_turn_id") or turn_idx)
            prompt_id = str(owner.get("owner_prompt_id") or owner.get("prompt_id") or "").strip()
            if not prompt_id:
                return False
            if (str(turn.get("kimi_session_id") or "") != session_id
                    or (owner.get("turn_id") and str(turn.get("kimi_turn_id") or "") != str(owner["turn_id"]))
                    or prompt_id != str(turn.get("kimi_owner_prompt_id") or turn.get("kimi_prompt_id") or "")
                    or (owner.get("revision") is not None and self._safe_int(owner["revision"], -1) != revision)):
                return False
            synced_key = (chat_id, f"{revision}:{session_id}:{turn_id}")
            with self._kimi_owner_lock:
                synced = self._kimi_thinking_synced.setdefault(synced_key, set())
            rows = [row for row in (messages or []) if isinstance(row, dict)]
            if rows and all(str(row.get("created_at") or "").strip() for row in rows):
                rows = [row for _idx, row in sorted(enumerate(rows), key=lambda pair: (str(pair[1].get("created_at")), pair[0]))]
            # Steering aliases share the same native turn and step numbering.
            # Start from the owning prompt, never restart ordinal 1 at an alias.
            boundary_id = prompt_id
            with self._kimi_owner_lock:
                aliases = {str(candidate.get("prompt_id") or "") for candidate in self._kimi_prompt_owners.values()
                           if candidate.get("chat_id") == chat_id and candidate.get("session_id") == session_id
                           and str(candidate.get("owner_prompt_id") or candidate.get("prompt_id") or "") == prompt_id}
            # Finalization retires live owners; accepted alias relationships
            # remain on their persisted turns for an already queued REST sync.
            aliases.update(str(candidate.get("kimi_prompt_id") or "") for candidate in turns
                           if isinstance(candidate, dict) and candidate.get("kimi_session_id") == session_id
                           and str(candidate.get("kimi_owner_prompt_id") or candidate.get("kimi_prompt_id") or "") == prompt_id
                           and candidate.get("kimi_prompt_role") == "alias")
            boundary = next((index for index, row in enumerate(rows)
                             if self._kimi_is_user_prompt(row) and str(row.get("id") or "") == boundary_id), None)
            if boundary is None:
                return False
            rows = rows[boundary + 1:]
            changed = False
            step_number = 0
            for message in rows:
                if self._kimi_is_user_prompt(message):
                    if str(message.get("id") or "") in aliases:
                        continue
                    break
                if message.get("role") != "assistant":
                    continue
                step_number += 1
                message_id = str(message.get("id") or "").strip()
                if not message_id or message.get("session_id", session_id) != session_id:
                    continue
                content = message.get("content") if isinstance(message.get("content"), list) else []
                commentary = any(isinstance(block, dict) and block.get("type") == "tool_use" for block in content)
                if commentary:
                    for key, state in list(self._execution_delta_buffer.items()):
                        base = state.get("event")
                        if (key[0] == chat_id and key[3] == "assistant" and isinstance(base, CodexEvent)
                                and self._event_thread_id(base) == session_id and self._event_turn_id(base) == turn_id
                                and self._kimi_event_agent_id(base) == "main" and base.data.get("step_number") == step_number):
                            tool_boundary = copy.copy(base)
                            tool_boundary.data = {**base.data, "source_kind": "tool.call.started"}
                            self._promote_kimi_interim_assistant(chat_id, tool_boundary)
                narratives = [(index, "thinking", str(block.get("text") or block.get("thinking") or "").strip())
                              for index, block in enumerate(content) if isinstance(block, dict)
                              and block.get("type") == "thinking" and not self._kimi_private_reasoning(block)]
                if commentary:
                    narratives.append(("commentary", "commentary", self._kimi_message_text(message)))
                for kind in ("thinking", "commentary"):
                    blocks = [(index, text) for index, block_kind, text in narratives if block_kind == kind and text]
                    candidates = [step for step in chat.get("execution_steps", []) if isinstance(step, dict)
                                  and str(step.get("session_id") or step.get("thread_id") or "") == session_id
                                  and str(step.get("turn_id") or "") == turn_id and step.get("revision", revision) == revision
                                  and str(step.get("agent_id") or "main") == "main" and step.get("display_kind") == kind
                                  and (step.get("rest_message_id") == message_id or step.get("item_id") == message_id
                                       or step.get("kimi_step_number") == step_number)]
                    aggregate = len(candidates) == 1 and (
                        kind == "commentary" or candidates[0].get("kimi_rest_aggregate")
                        or bool(candidates[0].get("kimi_step_id"))
                    )
                    protected_indexes = {
                        index for index, block in enumerate(content) if isinstance(block, dict)
                        and block.get("type") in ({"thinking"} if kind == "thinking" else {"text", "output_text", ""})
                        and self._kimi_private_reasoning(block)
                    }
                    message_private = self._kimi_private_reasoning(message)
                    # Withdraw disclosure before dedupe. An aggregate cannot
                    # separate its protected block; individually keyed REST
                    # thinking rows keep their unaffected public siblings.
                    for previous in candidates:
                        block_index = previous.get("kimi_rest_block_index")
                        if block_index is None and kind == "thinking":
                            suffix = str(previous.get("item_id") or "").removeprefix(f"kimi-thinking:{message_id}:")
                            block_index = int(suffix) if suffix.isdigit() else None
                        if message_private or (protected_indexes and (
                                aggregate or block_index in protected_indexes or block_index is None)):
                            changed = self._redact_kimi_narrative(chat_id, previous) or changed
                    if message_private:
                        continue
                    if aggregate:
                        blocks = [(kind, "\n".join(text for _index, text in blocks))] if blocks else []
                    for block_index, text in blocks:
                        dedupe_key = f"{message_id}:{block_index}"
                        if dedupe_key in synced:
                            continue
                        previous = candidates[0] if aggregate else next((step for step in candidates
                            if step.get("kimi_rest_block_index") == block_index
                            or step.get("item_id") == f"kimi-{kind}:{message_id}:{block_index}"), {})
                        # Redaction replaces the row in the chat; do not reuse
                        # the stale public candidate captured above.
                        previous = next((step for step in chat.get("execution_steps", [])
                                         if previous and step.get("item_id") == previous.get("item_id")
                                         and step.get("logical_key") == previous.get("logical_key")), previous)
                        if previous.get("private_reasoning"):
                            synced.add(dedupe_key)
                            continue
                        excerpt = self._kimi_thinking_excerpt(text)
                        entry = {
                            **previous, "event_type": f"{kind}_synced", "display_kind": kind,
                            "list_text": excerpt, "detail_text": text, "kimi_summary": excerpt,
                            "raw_text": text, "text": text, "narrative_snapshot": True,
                            "thread_id": session_id, "session_id": session_id, "turn_idx": turn_idx,
                            "turn_id": turn_id, "revision": revision, "provider": "kimi", "agent_id": "main",
                            "item_id": previous.get("item_id") or f"kimi-{kind}:{message_id}:{block_index}",
                            "source_kind": f"rest.{kind}", "status": "completed", "rest_message_id": message_id,
                            "kimi_step_number": step_number, "kimi_rest_block_index": block_index,
                            "kimi_rest_aggregate": bool(aggregate or kind == "commentary"),
                        }
                        appended = self._append_execution_entry_to_chat(chat_id, entry, save_state=True)
                        if appended:
                            changed = True
                        elif not any(step.get("item_id") == entry["item_id"]
                                     and step.get("rest_message_id") == message_id and step.get("status") == "completed"
                                     and step.get("display_kind") == kind and step.get("detail_text") == text
                                     and step.get("raw_text") == text and step.get("list_text") == excerpt
                                     and step.get("revision", revision) == revision
                                     and str(step.get("session_id") or "") == session_id
                                     and str(step.get("turn_id") or "") == turn_id
                                     and str(step.get("agent_id") or "main") == "main"
                                     and step.get("provider") == "kimi" and step.get("source_kind") == f"rest.{kind}"
                                     for step in chat.get("execution_steps", []) if isinstance(step, dict)):
                            continue
                        for buffer_key in list(self._execution_delta_buffer):
                            if (buffer_key[:3] == (chat_id, turn_id, entry["item_id"])
                                    and buffer_key[3] == kind and buffer_key[4] in {"", "main"}):
                                self._execution_delta_buffer.pop(buffer_key, None)
                        synced.add(dedupe_key)
            return changed
        except Exception:
            # The sync is marshalled to the UI thread; never surface a fetch /
            # shape failure as an unhandled wx event-loop exception.
            return False

    def _on_kimi_client_exit(self, returncode) -> None:
        if bindings.app.threading.current_thread() is not bindings.app.threading.main_thread():
            self._call_after_if_alive(self._on_kimi_client_exit, returncode)
            return
        # A zero exit code is only benign when the application initiated it or
        # there is no unfinished owner.  Otherwise the event transport has
        # vanished and pending chats still need transcript recovery.
        if returncode is None:
            return
        if returncode == 0 and not self._kimi_pending_owners():
            return
        self._recover_kimi_pending_owners(
            original_error=f"Kimi Code 服务进程已退出（代码 {returncode}）"
        )

    def _apply_kimi_event_scope(self, chat_id: str, event: CodexEvent) -> None:
        if not self._kimi_event_is_authoritative(event):
            return
        normalized = str(chat_id or "").strip()
        owner = self._kimi_owner_for_event(event)
        metadata = owner or self._kimi_active_turns.get(normalized)
        if not isinstance(metadata, dict):
            metadata = {}
        data = dict(event.data or {}) if isinstance(event.data, dict) else {}
        event_session_id = self._event_thread_id(event)
        event_turn_id = self._event_turn_id(event)
        turn_early_events: list[CodexEvent] = []
        if isinstance(owner, dict) and event_turn_id and not str(owner.get("turn_id") or "").strip():
            # Exact prompt ownership may associate a missing-start event with
            # its turn, but this is not a synthetic turn.started transition.
            owner["turn_id"] = event_turn_id
            with self._kimi_owner_lock:
                key = self._kimi_owner_key_for(owner)
                if key in self._kimi_prompt_owners:
                    self._kimi_prompt_owners[key]["turn_id"] = event_turn_id
                turn_early_events = self._kimi_early_events.pop(
                    (event_session_id, f"__turn__:{event_turn_id}"),
                    [],
                )
            metadata = owner
            target_chat, is_current = self._kimi_target_chat(normalized)
            turns = self.active_session_turns if is_current else ((target_chat or {}).get("turns") if isinstance(target_chat, dict) else [])
            idx = owner.get("turn_idx")
            if isinstance(turns, list) and isinstance(idx, int) and 0 <= idx < len(turns) and isinstance(turns[idx], dict):
                turns[idx]["kimi_turn_id"] = event_turn_id
                turns[idx]["request_resume_token"] = {
                    "session_id": event_session_id or str(owner.get("session_id") or ""),
                    "turn_id": event_turn_id,
                }
                if isinstance(target_chat, dict):
                    target_chat["kimi_turn_id"] = event_turn_id
                if is_current:
                    self.active_kimi_turn_id = event_turn_id
                self._mark_chat_turns_dirty(None if is_current else normalized, idx)
            # These frames preceded the prompt-bearing join event.  Replay
            # them synchronously on the UI thread before processing the join
            # event itself so offsets and terminal ordering remain causal.
            for early_event in turn_early_events:
                self._on_kimi_event_for_chat(normalized, early_event)
        meta_session_id = str(metadata.get("session_id") or "").strip()
        meta_turn_id = str(metadata.get("turn_id") or "").strip()
        if "turn_idx" not in data and isinstance(metadata.get("turn_idx"), int):
            stamp = (
                (not event_session_id or not meta_session_id or event_session_id == meta_session_id)
                and (not event_turn_id or not meta_turn_id or event_turn_id == meta_turn_id)
            )
            if stamp and event_turn_id:
                # turn_id 已能定位到另一个本地 turn 时不盖章：
                # 活跃 turn 的事件不能被归到 turn 进行中又提交的新请求上。
                target_chat, is_current_target = self._kimi_target_chat(normalized)
                turns = (
                    self.active_session_turns
                    if is_current_target
                    else ((target_chat or {}).get("turns") if isinstance(target_chat, dict) else [])
                )
                for idx, turn in enumerate(turns or []):
                    if not isinstance(turn, dict):
                        continue
                    turn_session_id = str(turn.get("kimi_session_id") or "").strip()
                    if (
                        str(turn.get("kimi_turn_id") or "").strip() == event_turn_id
                        and (not event_session_id or turn_session_id == event_session_id)
                        and idx != metadata.get("turn_idx")
                    ):
                        stamp = False
                        break
            if stamp:
                data["turn_idx"] = metadata.get("turn_idx")
        model = str(metadata.get("model") or "").strip()
        if model and "model" not in data:
            data["model"] = model
        if data:
            event.data = data

    def _known_kimi_event_chat_id(self, event: CodexEvent) -> str:
        turn_id = self._event_turn_id(event)
        session_id = self._event_thread_id(event)
        candidates = []
        active_id = str(self.active_chat_id or self.current_chat_id or "").strip()
        if active_id:
            candidates.append(
                {
                    "id": active_id,
                    "kimi_session_id": str(getattr(self, "active_kimi_session_id", "") or "").strip(),
                    "kimi_turn_id": str(getattr(self, "active_kimi_turn_id", "") or "").strip(),
                    "turns": self.active_session_turns if isinstance(self.active_session_turns, list) else [],
                }
            )
        if isinstance(getattr(self, "_current_chat_state", None), dict):
            candidates.append(self._current_chat_state)
        candidates.extend(chat for chat in (self.archived_chats or []) if isinstance(chat, dict))

        if session_id:
            session_matches: set[str] = set()
            session_turn_matches: set[str] = set()
            for chat in candidates:
                chat_id = str(chat.get("id") or "").strip()
                if not chat_id:
                    continue
                turns = chat.get("turns") if isinstance(chat.get("turns"), list) else []
                chat_has_session = str(chat.get("kimi_session_id") or "").strip() == session_id
                for turn in turns:
                    if not isinstance(turn, dict):
                        continue
                    if str(turn.get("kimi_session_id") or "").strip() == session_id:
                        chat_has_session = True
                        if turn_id and str(turn.get("kimi_turn_id") or "").strip() == turn_id:
                            session_turn_matches.add(chat_id)
                if chat_has_session:
                    session_matches.add(chat_id)
                    if turn_id and str(chat.get("kimi_turn_id") or "").strip() == turn_id:
                        session_turn_matches.add(chat_id)
            if turn_id and len(session_turn_matches) == 1:
                return next(iter(session_turn_matches))
            event_type = str(getattr(event, "type", "") or "").strip()
            if (not turn_id or event_type == "turn_started") and len(session_matches) == 1:
                return next(iter(session_matches))
            return ""

        if turn_id:
            turn_matches: set[tuple[str, int, str]] = set()
            root_matches: set[str] = set()
            for chat in candidates:
                chat_id = str(chat.get("id") or "").strip()
                if not chat_id:
                    continue
                if str(chat.get("kimi_turn_id") or "").strip() == turn_id:
                    root_matches.add(chat_id)
                turns = chat.get("turns") if isinstance(chat.get("turns"), list) else []
                for idx, turn in enumerate(turns):
                    if isinstance(turn, dict) and str(turn.get("kimi_turn_id") or "").strip() == turn_id:
                        turn_matches.add((chat_id, idx, str(turn.get("kimi_session_id") or "").strip()))
            for chat_id in root_matches:
                if not any(match_chat_id == chat_id for match_chat_id, _idx, _session_id in turn_matches):
                    turn_matches.add((chat_id, -1, ""))
            if len(turn_matches) == 1:
                return next(iter(turn_matches))[0]

        return ""

    def _resolve_kimi_event_chat_id(self, event: CodexEvent) -> str:
        owner = self._kimi_owner_for_event(event)
        if isinstance(owner, dict):
            return str(owner.get("chat_id") or "").strip()
        known_chat_id = self._known_kimi_event_chat_id(event)
        if known_chat_id:
            return known_chat_id
        session_id = self._event_thread_id(event)
        if session_id:
            event_type = str(getattr(event, "type", "") or "").strip()
            event_turn_id = self._event_turn_id(event)
            active_matches = {
                str(chat_id or "").strip()
                for chat_id, metadata in self._kimi_active_turns.items()
                if (
                    isinstance(metadata, dict)
                    and str(metadata.get("session_id") or "").strip() == session_id
                    and (
                        not event_turn_id
                        or str(metadata.get("turn_id") or "").strip() == event_turn_id
                        or not str(metadata.get("turn_id") or "").strip()
                    )
                )
            }
            active_matches.discard("")
            if len(active_matches) == 1:
                return next(iter(active_matches))
            return ""
        if not self._event_turn_id(event) and len(self._kimi_active_turns) == 1:
            return str(next(iter(self._kimi_active_turns)) or "").strip()
        return ""

    def _kimi_event_is_compatible_with_chat(self, chat: dict | None, event: CodexEvent) -> bool:
        session_id = self._event_thread_id(event)
        if not session_id:
            return True
        known_sessions = {str((chat or {}).get("kimi_session_id") or "").strip()}
        turns = (chat or {}).get("turns") if isinstance((chat or {}).get("turns"), list) else []
        known_sessions.update(
            str(turn.get("kimi_session_id") or "").strip()
            for turn in turns
            if isinstance(turn, dict)
        )
        if chat is getattr(self, "_current_chat_state", None):
            known_sessions.add(str(getattr(self, "active_kimi_session_id", "") or "").strip())
        known_sessions.discard("")
        if not known_sessions:
            return True
        return session_id in known_sessions

    def _kimi_event_scoped_turn_index(self, turns: list, event: CodexEvent) -> int:
        owner = self._kimi_owner_for_event(event)
        if isinstance(owner, dict) and isinstance(owner.get("turn_idx"), int):
            idx = owner["turn_idx"]
            if isinstance(turns, list) and 0 <= idx < len(turns):
                return idx
        data_idx = self._event_data_turn_idx_value(event)
        session_id = self._event_thread_id(event)
        turn_id = self._event_turn_id(event)
        if session_id and turn_id:
            matches = [
                idx
                for idx, turn in enumerate(turns or [])
                if (
                    isinstance(turn, dict)
                    and str(turn.get("kimi_session_id") or "").strip() == session_id
                    and str(turn.get("kimi_turn_id") or "").strip() == turn_id
                )
            ]
            if len(matches) == 1:
                return matches[0]
            if isinstance(turns, list) and 0 <= data_idx < len(turns):
                turn = turns[data_idx]
                if (
                    isinstance(turn, dict)
                    and str(turn.get("kimi_session_id") or "").strip() == session_id
                    and str(turn.get("kimi_turn_id") or "").strip() in {"", turn_id}
                ):
                    return data_idx
            return -1
        if session_id:
            matches = [
                idx
                for idx, turn in enumerate(turns or [])
                if isinstance(turn, dict) and str(turn.get("kimi_session_id") or "").strip() == session_id
            ]
            if len(matches) == 1:
                return matches[0]
            if 0 <= data_idx < len(turns or []) and data_idx in matches:
                return data_idx
            return -1
        if turn_id:
            matches = [
                idx
                for idx, turn in enumerate(turns or [])
                if isinstance(turn, dict) and str(turn.get("kimi_turn_id") or "").strip() == turn_id
            ]
            return matches[0] if len(matches) == 1 else -1
        if isinstance(turns, list) and 0 <= data_idx < len(turns):
            return data_idx
        return -1

    def _kimi_event_turn_index(self, turns: list, event: CodexEvent) -> int:
        scoped_idx = self._kimi_event_scoped_turn_index(turns, event)
        if scoped_idx >= 0:
            return scoped_idx
        if not self._event_thread_id(event) and not self._event_turn_id(event):
            return len(turns) - 1 if isinstance(turns, list) and turns else -1
        return -1

    def _active_kimi_event_target_index(self, event: CodexEvent) -> int:
        scoped_idx = self._kimi_event_scoped_turn_index(self.active_session_turns, event)
        if scoped_idx >= 0:
            return scoped_idx
        if not self._event_thread_id(event) and not self._event_turn_id(event) and 0 <= self.active_turn_idx < len(self.active_session_turns):
            return self.active_turn_idx
        if not self._event_thread_id(event) and not self._event_turn_id(event):
            return len(self.active_session_turns) - 1 if isinstance(self.active_session_turns, list) and self.active_session_turns else -1
        return -1

    def _accumulate_kimi_answer_delta(self, chat_id: str, event: CodexEvent) -> None:
        text = str(getattr(event, "text", "") or getattr(event, "raw_text", "") or "")
        turn_id = self._event_turn_id(event)
        session_id = self._event_thread_id(event)
        metadata = self._kimi_owner_for_event(event) or self._kimi_active_turns.get(str(chat_id or "").strip())
        if isinstance(metadata, dict):
            if not turn_id:
                turn_id = str(metadata.get("turn_id") or "").strip()
            if not session_id:
                session_id = str(metadata.get("session_id") or "").strip()
        owner_key = ""
        if isinstance(metadata, dict):
            owner_key = str(metadata.get("owner_prompt_id") or metadata.get("prompt_id") or "").strip()
        owner_key = owner_key or turn_id
        if not owner_key:
            return
        key = (str(chat_id or ""), session_id, owner_key, self._kimi_event_agent_id(event))
        event_data = event.data if isinstance(event.data, dict) else {}
        stream = str(getattr(event, "item_id", "") or event_data.get("source_kind")
                     or getattr(event, "display_kind", "") or "assistant").strip()
        private_stream = self._kimi_private_reasoning(event_data) or any(
            buffer_key[:3] == (str(chat_id or ""), turn_id, stream) and state.get("private_reasoning")
            for buffer_key, state in self._execution_delta_buffer.items()
        )
        target_chat = self._chat_state_for_execution_steps(chat_id) or {}
        private_stream = private_stream or any(
            step.get("private_reasoning") and step.get("item_id") == stream
            and str(step.get("session_id") or step.get("thread_id") or "") == session_id
            and str(step.get("turn_id") or "") == turn_id
            for step in target_chat.get("execution_steps", []) if isinstance(step, dict)
        )
        if private_stream:
            self._kimi_turn_answer_segments.setdefault((*key, stream), {})
            self._redact_kimi_answer_stream(str(chat_id or ""), session_id, key[3], stream)
            return
        if not text:
            return
        offset = event_data.get("offset")
        if isinstance(offset, int):
            segment_key = (*key, stream)
            self._kimi_turn_answer_segments.setdefault(segment_key, {}).setdefault(offset, text)
            parts = self._kimi_turn_answer_parts.setdefault(key, [])
            if text not in parts or not parts:
                parts.append(text)
        else:
            parts = self._kimi_turn_answer_parts.setdefault(key, [])
            parts.append(text)
        # Transitional compatibility for callers/tests that inspect the
        # historical (chat, session, turn) buffer key.  Both keys share the
        # same list and authoritative consumption removes both.
        if turn_id:
            self._kimi_turn_answer_parts[(str(chat_id or ""), session_id, turn_id)] = parts

    def _kimi_answer_parts(
        self,
        chat_id: str,
        session_id: str = "",
        owner_key: str = "",
        *,
        consume: bool = False,
    ) -> str:
        normalized_chat = str(chat_id or "")
        normalized_session = str(session_id or "").strip()
        normalized_owner = str(owner_key or "").strip()
        parts: list[str] = []
        matching_keys = []
        matching_list_ids: set[int] = set()
        segmented_bases = {segment_key[:4] for segment_key in self._kimi_turn_answer_segments}
        for key in list(self._kimi_turn_answer_parts.keys()):
            if len(key) != 4:
                continue
            key_chat, key_session, key_owner, key_agent = key
            if key_chat != normalized_chat:
                continue
            if normalized_session and key_session != normalized_session:
                continue
            if normalized_owner and key_owner != normalized_owner:
                continue
            if key_agent != "main":
                continue
            matching_keys.append(key)
        if not normalized_session and normalized_owner:
            sessions = {key_session for _key_chat, key_session, _key_owner, _key_agent in matching_keys}
            if len(sessions) > 1:
                return ""
        for key in matching_keys:
            values = self._kimi_turn_answer_parts.get(key, [])
            matching_list_ids.add(id(values))
            if key not in segmented_bases:
                parts.extend(str(value or "") for value in values)
            if consume:
                self._kimi_turn_answer_parts.pop(key, None)
        segment_keys = []
        for key in list(self._kimi_turn_answer_segments.keys()):
            key_chat, key_session, key_owner, key_agent, _key_stream = key
            if key_chat != normalized_chat:
                continue
            if normalized_session and key_session != normalized_session:
                continue
            if normalized_owner and key_owner != normalized_owner:
                continue
            if key_agent != "main":
                continue
            segment_keys.append(key)
        # Insertion order is the first-seen protocol stream order. Stream ids
        # are opaque and must not be sorted lexically.
        for key in segment_keys:
            segments = self._kimi_turn_answer_segments.get(key, {})
            assembled = ""
            cursor = 0
            complete = True
            for offset, fragment in sorted(segments.items()):
                if offset > cursor:
                    complete = False
                    break
                overlap = max(0, cursor - offset)
                if overlap < len(fragment):
                    assembled += fragment[overlap:]
                    cursor = offset + len(fragment)
            if complete:
                parts.append(assembled)
            else:
                self._kimi_incomplete_sessions.add(key[1])
                self._kimi_incomplete_streams.add(key)
            if consume:
                self._kimi_turn_answer_segments.pop(key, None)
        if consume:
            for key in list(self._kimi_turn_answer_parts.keys()):
                if len(key) == 3 and id(self._kimi_turn_answer_parts.get(key)) in matching_list_ids:
                    self._kimi_turn_answer_parts.pop(key, None)
        return "".join(parts)

    def _pop_kimi_answer_parts(self, chat_id: str, session_id: str = "", owner_key: str = "") -> str:
        return self._kimi_answer_parts(chat_id, session_id, owner_key, consume=True)

    def _kimi_context_usage_payload(self, event: CodexEvent) -> dict | None:
        usage = event.usage if isinstance(getattr(event, "usage", None), dict) else {}
        model = str(event.data.get("model") or "").strip() if isinstance(event.data, dict) else ""
        return self._kimi_context_usage_from_values(usage, model=model)

    @staticmethod
    def _kimi_context_usage_from_values(usage: dict, *, model: str = "") -> dict | None:
        used = usage.get("context_tokens")
        window = usage.get("max_context_tokens")
        if isinstance(used, bool) or not isinstance(used, (int, float)) or used < 0:
            return None
        if isinstance(window, bool) or not isinstance(window, (int, float)) or window <= 0:
            window = 0
        return {
            "used_tokens": int(used),
            "context_window": int(window),
            "source": "kimi",
            "exact": bool(window),
            "fresh": True,
            "model": model,
            "updated_at": bindings.app.time.time(),
        }

    def _apply_kimi_turn_started(self, chat_id: str, target_chat: dict, target_turns: list, event: CodexEvent, is_current_target: bool) -> None:
        event_turn_id = self._event_turn_id(event)
        normalized = str(chat_id or "").strip()
        metadata = self._kimi_owner_for_event(event)
        if not isinstance(metadata, dict):
            session_id = self._event_thread_id(event)
            prompt_text = str(getattr(event, "text", "") or "").strip()
            # A start without promptId is claimable only when session, prompt
            # text and one pending candidate agree.  Repeated questions remain
            # unresolved for transcript-bound REST reconciliation.
            if not session_id or not prompt_text:
                return
            candidates = [
                owner for owner in self._kimi_pending_owners({session_id})
                if str(owner.get("role") or "").strip() != "alias"
                and str(owner.get("question") or "").strip() == prompt_text
            ]
            metadata = dict(candidates[0]) if len(candidates) == 1 else {}
        else:
            metadata = dict(metadata)
        if not metadata:
            return
        if event_turn_id:
            metadata["turn_id"] = event_turn_id
        existing_active = self._kimi_active_turns.get(normalized)
        if (
            isinstance(existing_active, dict)
            and self._kimi_owner_key(
                normalized,
                existing_active.get("session_id"),
                existing_active.get("prompt_id"),
            ) != self._kimi_owner_key_for(metadata)
            and str(existing_active.get("role") or "active") in {"active", "alias"}
        ):
            return
        queue = target_chat.get("kimi_request_queue") if isinstance(target_chat, dict) else []
        turn_idx = metadata.get("turn_idx") if isinstance(metadata.get("turn_idx"), int) else -1
        prompt_id = str(metadata.get("prompt_id") or "").strip()
        if isinstance(queue, list) and prompt_id:
            queue[:] = [
                entry for entry in queue
                if not (isinstance(entry, dict) and str(entry.get("prompt_id") or "").strip() == prompt_id)
            ]
            if is_current_target:
                self.active_kimi_request_queue = queue
        metadata["role"] = "active"
        metadata["owner_prompt_id"] = prompt_id or str(metadata.get("owner_prompt_id") or "").strip()
        metadata["landed"] = True
        if normalized:
            self._kimi_active_turns[normalized] = metadata
        session_id = self._event_thread_id(event) or str(metadata.get("session_id") or "").strip()
        if session_id:
            metadata["session_id"] = session_id
            if normalized:
                self._kimi_active_turns[normalized] = metadata
        if isinstance(target_chat, dict):
            if session_id:
                if str(target_chat.get("kimi_session_id") or "").strip() != session_id:
                    self._clear_kimi_information_for_session_change(target_chat)
                target_chat["kimi_session_id"] = session_id
                self._publish_kimi_information_session_change(target_chat)
            if event_turn_id:
                target_chat["kimi_turn_id"] = event_turn_id
            target_chat["kimi_turn_active"] = True
            if not is_current_target:
                self._mark_history_list_dirty()
        if is_current_target:
            if session_id:
                self.active_kimi_session_id = session_id
            if event_turn_id:
                self.active_kimi_turn_id = event_turn_id
            self.active_kimi_turn_active = True
        if isinstance(target_turns, list) and 0 <= turn_idx < len(target_turns):
            turn = target_turns[turn_idx]
            if isinstance(turn, dict):
                if event_turn_id:
                    turn["kimi_turn_id"] = event_turn_id
                if prompt_id:
                    turn["kimi_prompt_role"] = "active"
                    turn["kimi_owner_prompt_id"] = prompt_id
                if session_id:
                    turn["kimi_session_id"] = session_id
                if session_id or event_turn_id:
                    turn["request_resume_token"] = {"session_id": session_id, "turn_id": event_turn_id}
                self._mark_chat_turns_dirty(None if is_current_target else normalized, turn_idx)
        if prompt_id:
            self._register_kimi_prompt_owner(metadata)

    def _finalize_kimi_turn_state(self, chat_id: str, target_chat: dict, target_turns: list, target_idx: int, event: CodexEvent) -> list[int]:
        if not self._kimi_event_is_authoritative(event):
            return []
        status = str(getattr(event, "status", "") or "completed").strip() or "completed"
        event_session_id = self._event_thread_id(event)
        event_turn_id = self._event_turn_id(event)
        prompt_id = self._kimi_event_prompt_id(event)
        source_kind = str((event.data or {}).get("source_kind") or "") if isinstance(event.data, dict) else ""
        owner = self._kimi_owner_for_event(event)
        if not isinstance(owner, dict):
            metadata = self._kimi_active_turns.get(str(chat_id or "").strip())
            if isinstance(metadata, dict):
                session_matches = not event_session_id or str(metadata.get("session_id") or "").strip() == event_session_id
                turn_matches = not event_turn_id or str(metadata.get("turn_id") or "").strip() in {"", event_turn_id}
                if session_matches and turn_matches:
                    owner = dict(metadata)
        if not isinstance(owner, dict) and 0 <= target_idx < len(target_turns):
            candidate = target_turns[target_idx]
            matches = [
                idx for idx, turn in enumerate(target_turns)
                if isinstance(turn, dict)
                and (not event_session_id or str(turn.get("kimi_session_id") or "").strip() == event_session_id)
                and (not event_turn_id or str(turn.get("kimi_turn_id") or "").strip() == event_turn_id)
            ]
            if (
                isinstance(candidate, dict)
                and str(candidate.get("request_status") or "").strip() == "pending"
                and len(matches) == 1
                and matches[0] == target_idx
            ):
                owner = {
                    "chat_id": str(chat_id or "").strip(),
                    "session_id": event_session_id,
                    "turn_id": event_turn_id,
                    "turn_idx": target_idx,
                    "prompt_id": str(candidate.get("kimi_prompt_id") or "").strip(),
                    "owner_prompt_id": str(candidate.get("kimi_owner_prompt_id") or candidate.get("kimi_prompt_id") or event_turn_id).strip(),
                    "role": str(candidate.get("kimi_prompt_role") or "active"),
                }
        if not isinstance(owner, dict):
            return []
        owner_prompt_id = str((owner.get("owner_prompt_id") if owner.get("role") == "alias" else None)
                              or owner.get("prompt_id") or prompt_id).strip()
        owner_key = self._kimi_owner_key_for(owner)
        event_data = event.data if isinstance(event.data, dict) else {}
        event_generation = int(event_data.get("owner_generation") or 0)
        if event_generation and event_generation != int(owner.get("generation") or 0):
            return []
        answer_identity = "kimi:" + hashlib.sha256((event_session_id + ":" + owner_prompt_id).encode()).hexdigest()
        identity = (owner_key[1], owner_key[2], event_turn_id, self._kimi_event_agent_id(event))
        with self._kimi_owner_lock:
            if owner_key in self._kimi_terminal_tombstones or identity in self._kimi_completed_identities:
                self._record_kimi_completion_stage(answer_identity, "deduplicated")
                return []
            stored_owner = self._kimi_prompt_owners.get(owner_key)
            if source_kind == "turn.ended" and isinstance(stored_owner, dict):
                stored_owner["turn_ended_seen"] = True
        if source_kind == "prompt.completed":
            if str(owner.get("role") or "") == "alias":
                return []
            event_data = event.data if isinstance(event.data, dict) else {}
            owner_generation = int(owner.get("generation") or 0)
            reconciled_proof = (
                bool(event_data.get("idle_verified") or event_data.get("terminal_verified"))
                and int(event_data.get("owner_generation") or 0) == owner_generation
            )
            with self._kimi_owner_lock:
                stored_owner = self._kimi_prompt_owners.get(owner_key)
                turn_ended_seen = bool((stored_owner or {}).get("turn_ended_seen"))
                cached_proof = self._kimi_idle_proofs.get(owner_key) == owner_generation
            if turn_ended_seen:
                return []
            if not reconciled_proof and not cached_proof:
                self._recover_kimi_pending_owners(session_ids={event_session_id} if event_session_id else None)
                return []
        # REST reconciliation is a complete transcript boundary.  It must
        # replace, never be replaced by, a stale partial WebSocket buffer.
        if bool(event_data.get("stream_complete")) and source_kind in {"rest.reconciled", "transcript.terminal"}:
            final_text = str(getattr(event, "text", "") or "").strip()
        else:
            final_text = self._kimi_answer_parts(chat_id, event_session_id, owner_prompt_id)
        # Assembly detects offset gaps. Check completeness only after that
        # detection so another non-empty stream cannot publish a partial answer.
        with self._kimi_owner_lock:
            stream_incomplete = any(
                key[0] == str(chat_id or "")
                and key[1] == event_session_id
                and key[2] == owner_prompt_id
                for key in self._kimi_incomplete_streams
            )
        if status == "completed" and stream_incomplete and not bool(event_data.get("stream_complete")):
            self._recover_kimi_pending_owners(
                session_ids={event_session_id} if event_session_id else None
            )
            return []
        if not final_text:
            final_text = str(getattr(event, "text", "") or "").strip()
        queue = target_chat.get("kimi_request_queue") if isinstance(target_chat, dict) else []
        covered = []
        with self._kimi_owner_lock:
            owner_snapshot = list(self._kimi_prompt_owners.values())
        for candidate in owner_snapshot:
            if not isinstance(candidate, dict):
                continue
            if str(candidate.get("chat_id") or "").strip() != str(chat_id or "").strip():
                continue
            if str(candidate.get("session_id") or "").strip() != event_session_id:
                continue
            candidate_owner = str((candidate.get("owner_prompt_id") if candidate.get("role") == "alias" else None)
                                  or candidate.get("prompt_id") or "").strip()
            if candidate_owner != owner_prompt_id:
                continue
            idx = candidate.get("turn_idx")
            if isinstance(idx, int) and idx not in covered:
                covered.append(idx)
        if not covered and isinstance(owner.get("turn_idx"), int):
            covered.append(owner["turn_idx"])
        if str(owner.get("role") or "") == "queued" and isinstance(queue, list):
            queue[:] = [
                entry for entry in queue
                if not (isinstance(entry, dict) and str(entry.get("prompt_id") or "").strip() == str(owner.get("prompt_id") or "").strip())
            ]
            if target_chat is self._current_chat_state:
                self.active_kimi_request_queue = queue
        finalized: list[int] = []
        accepted: list[int] = []
        primary_idx = next((candidate.get("turn_idx") for candidate in owner_snapshot
                            if isinstance(candidate, dict) and candidate.get("prompt_id") == owner_prompt_id
                            and candidate.get("chat_id") == chat_id and candidate.get("session_id") == event_session_id), owner.get("turn_idx"))
        for idx in covered:
            if not isinstance(idx, int) or idx < 0 or idx >= len(target_turns):
                continue
            turn = target_turns[idx]
            if not isinstance(turn, dict):
                continue
            if not self._accept_clear_operation_result(turn, chat_id):
                continue
            accepted.append(idx)
            if status == "completed":
                has_final_text = bool(final_text and final_text != REQUESTING_TEXT)
                if not has_final_text:
                    self._recover_kimi_pending_owners(session_ids={event_session_id} if event_session_id else None)
                    return []
                was_done = str(turn.get("request_status") or "") == "done"
                turn["answer_identity"] = answer_identity
                if idx == primary_idx:
                    self._apply_kimi_final_answer_to_turn(turn, final_text)
                else:
                    turn["answer_md"] = ""
                    turn["answer_alias_of"] = answer_identity
                    turn["suppress_empty_answer_row"] = True
                if not was_done:
                    self._record_chat_activity(target_chat, bindings.app.time.time())
                self._mark_turn_request_done(turn)
                turn["request_error"] = ""
                turn["request_recovered_after_restart"] = False
                self._complete_clear_operation_turn(turn, failed=False)
            else:
                # The protocol error is authoritative; never disguise it with a
                # partial assistant stream.
                if status in {"cancelled", "interrupted", "aborted", "canceled"}:
                    error_text = "Kimi Code 已中断。"
                else:
                    error_text = str(getattr(event, "text", "") or "").strip() or "Kimi Code 执行失败。"
                turn["answer_md"] = error_text
                self._mark_turn_request_failed(turn, error_text)
                self._complete_clear_operation_turn(turn, failed=True)
            finalized.append(idx)
        if not accepted:
            return []
        self._pop_kimi_answer_parts(chat_id, event_session_id, owner_prompt_id)
        with self._kimi_owner_lock:
            self._kimi_completed_identities.add(identity)
            if len(self._kimi_completed_identities) > 512:
                self._kimi_completed_identities = set(list(self._kimi_completed_identities)[-256:])
            for owned_prompt, candidate in list(self._kimi_prompt_owners.items()):
                if (
                    isinstance(candidate, dict)
                    and str(candidate.get("chat_id") or "").strip() == owner_key[0]
                    and str(candidate.get("session_id") or "").strip() == owner_key[1]
                    and ((candidate.get("owner_prompt_id") if candidate.get("role") == "alias" else None)
                         or candidate.get("prompt_id")) == owner_prompt_id
                ):
                    self._add_kimi_tombstone(owned_prompt)
                    self._kimi_idle_proofs.pop(owned_prompt, None)
                    self._kimi_early_events.pop((owned_prompt[1], owned_prompt[2]), None)
                    self._kimi_prompt_owners.pop(owned_prompt, None)
        group_active_owner = next(
            (
                candidate for candidate in owner_snapshot
                if isinstance(candidate, dict)
                and str(candidate.get("chat_id") or "").strip() == str(chat_id or "").strip()
                and str(candidate.get("session_id") or "").strip() == event_session_id
                and str(candidate.get("owner_prompt_id") or candidate.get("prompt_id") or "").strip() == owner_prompt_id
                and str(candidate.get("role") or "") == "active"
            ),
            owner,
        )
        self._clear_kimi_active_turn(
            chat_id,
            group_active_owner.get("turn_idx"),
            str(group_active_owner.get("turn_id") or event_turn_id or "").strip() or None,
        )
        pending = [
            candidate for candidate in self._kimi_pending_owners({event_session_id} if event_session_id else None)
            if str(candidate.get("chat_id") or "").strip() == str(chat_id or "").strip()
        ]
        remaining_active = self._kimi_active_turns.get(str(chat_id or "").strip())
        target_chat["kimi_turn_active"] = bool(pending or isinstance(remaining_active, dict))
        if bool(event_data.get("stream_complete")) and event_session_id:
            with self._kimi_owner_lock:
                if not self._kimi_pending_owners({event_session_id}):
                    self._kimi_incomplete_sessions.discard(event_session_id)
                self._kimi_incomplete_streams = {
                    key for key in self._kimi_incomplete_streams
                    if not (key[0] == str(chat_id or "") and key[1] == event_session_id and key[2] == owner_prompt_id)
                }
        # Accepted state and notification survive failures in either foreground
        # or background presentation below this common boundary.
        self._mark_chat_turns_dirty(chat_id, min(finalized))
        self._defer_chat_state_save()
        if status == "completed" and any(str(target_turns[i].get("answer_md") or "").strip() for i in finalized):
            self._notify_kimi_answer_completed(answer_identity)
        return finalized

    def _record_kimi_completion_stage(self, identity: str, stage: str) -> None:
        if bindings.app.os.getenv("MC_KIMI_COMPLETION_DIAGNOSTICS", "").strip() != "1":
            return
        records = getattr(self, "_kimi_completion_diagnostics", None)
        if records is None:
            records = deque(maxlen=128)
            self._kimi_completion_diagnostics = records
        records.append({"answer": identity, "stage": stage})

    def _notify_kimi_answer_completed(self, identity: str) -> None:
        self._record_kimi_completion_stage(identity, "accepted")
        try:
            self._play_finish_sound()
            self._record_kimi_completion_stage(identity, "sound_called")
        except Exception:
            # Audio device failures cannot undo or prevent answer persistence.
            self._record_kimi_completion_stage(identity, "sound_failed")

    def _apply_kimi_error(
        self,
        chat_id: str,
        message: str,
        turn_idx=None,
        turn_id: str | None = None,
        prompt_id: str | None = None,
        owner_generation: int | None = None,
    ) -> None:
        target_chat, is_current_target = self._kimi_target_chat(chat_id)
        if not isinstance(target_chat, dict):
            return
        target_turns = self.active_session_turns if is_current_target else target_chat.get("turns")
        if not isinstance(target_turns, list):
            return
        if prompt_id and owner_generation:
            with self._kimi_owner_lock:
                current = [
                    candidate for candidate in self._kimi_prompt_owners.values()
                    if isinstance(candidate, dict)
                    and str(candidate.get("chat_id") or "").strip() == str(chat_id or "").strip()
                    and str(candidate.get("prompt_id") or "").strip() == str(prompt_id or "").strip()
                ]
            if not any(int(candidate.get("generation") or 0) == int(owner_generation) for candidate in current):
                return
        owner = None
        if prompt_id:
            owner = self._find_kimi_prompt_owner(
                str(prompt_id),
                chat_id=str(chat_id or ""),
                include_unlanded=True,
            )
        if isinstance(turn_idx, int):
            target_idx = turn_idx if 0 <= turn_idx < len(target_turns) else -1
        else:
            target_idx = -1
        if target_idx < 0:
            turn_id_value = str(turn_id or "").strip()
            if turn_id_value:
                for idx, turn in enumerate(target_turns):
                    if isinstance(turn, dict) and str(turn.get("kimi_turn_id") or "").strip() == turn_id_value:
                        target_idx = idx
                        break
        if target_idx < 0:
            metadata = owner or self._kimi_active_turns.get(str(chat_id or "").strip())
            if isinstance(metadata, dict) and isinstance(metadata.get("turn_idx"), int):
                candidate = metadata.get("turn_idx")
                if 0 <= candidate < len(target_turns):
                    target_idx = candidate
        if target_idx < 0 and is_current_target and 0 <= self.active_turn_idx < len(target_turns):
            turn = target_turns[self.active_turn_idx]
            if (
                isinstance(turn, dict)
                and str(turn.get("request_status") or "").strip() == "pending"
                and is_kimi_model(str(turn.get("model") or ""))
            ):
                target_idx = self.active_turn_idx
        if target_idx < 0 or target_idx >= len(target_turns):
            return
        turn = target_turns[target_idx]
        if not isinstance(turn, dict):
            return
        if not isinstance(owner, dict):
            with self._kimi_owner_lock:
                owner = next(
                    (
                        dict(candidate) for candidate in self._kimi_prompt_owners.values()
                        if isinstance(candidate, dict)
                        and str(candidate.get("chat_id") or "").strip() == str(chat_id or "").strip()
                        and candidate.get("turn_idx") == target_idx
                    ),
                    None,
                )
        covered = [target_idx]
        owner_prompt_id = ""
        session_id = ""
        group_active_owner = None
        if isinstance(owner, dict):
            owner_prompt_id = str((owner.get("owner_prompt_id") if owner.get("role") == "alias" else None)
                                  or owner.get("prompt_id") or "").strip()
            session_id = str(owner.get("session_id") or "").strip()
            with self._kimi_owner_lock:
                covered = sorted({
                    int(candidate.get("turn_idx"))
                    for candidate in self._kimi_prompt_owners.values()
                    if isinstance(candidate, dict)
                    and str(candidate.get("chat_id") or "").strip() == str(chat_id or "").strip()
                    and str(candidate.get("session_id") or "").strip() == session_id
                    and ((candidate.get("owner_prompt_id") if candidate.get("role") == "alias" else None)
                             or candidate.get("prompt_id")) == owner_prompt_id
                    and isinstance(candidate.get("turn_idx"), int)
                }) or [target_idx]
                group_active_owner = next(
                    (
                        dict(candidate) for candidate in self._kimi_prompt_owners.values()
                        if isinstance(candidate, dict)
                        and str(candidate.get("chat_id") or "").strip() == str(chat_id or "").strip()
                        and str(candidate.get("session_id") or "").strip() == session_id
                        and ((candidate.get("owner_prompt_id") if candidate.get("role") == "alias" else None)
                             or candidate.get("prompt_id")) == owner_prompt_id
                        and str(candidate.get("role") or "") == "active"
                    ),
                    None,
                )
        error_text = str(message or "").strip() or "Kimi Code 请求失败"
        accepted: list[int] = []
        for idx in covered:
            if not (0 <= idx < len(target_turns)) or not isinstance(target_turns[idx], dict):
                continue
            failed_turn = target_turns[idx]
            if not self._accept_clear_operation_result(failed_turn, chat_id):
                continue
            accepted.append(idx)
            self._mark_turn_request_failed(failed_turn, error_text)
            failed_turn["answer_md"] = error_text
            self._complete_clear_operation_turn(failed_turn, failed=True)
            self._mark_chat_turns_dirty(None if is_current_target else chat_id, idx)
        if not accepted:
            return
        if isinstance(owner, dict):
            queue = target_chat.get("kimi_request_queue")
            with self._kimi_owner_lock:
                for key, candidate in list(self._kimi_prompt_owners.items()):
                    if (
                        isinstance(candidate, dict)
                        and str(candidate.get("chat_id") or "").strip() == str(chat_id or "").strip()
                        and str(candidate.get("session_id") or "").strip() == session_id
                        and ((candidate.get("owner_prompt_id") if candidate.get("role") == "alias" else None)
                             or candidate.get("prompt_id")) == owner_prompt_id
                    ):
                        self._add_kimi_tombstone(key)
                        self._kimi_idle_proofs.pop(key, None)
                        self._kimi_early_events.pop((key[1], key[2]), None)
                        self._kimi_prompt_owners.pop(key, None)
                if not any(
                    isinstance(candidate, dict) and str(candidate.get("session_id") or "").strip() == session_id
                    for candidate in self._kimi_prompt_owners.values()
                ):
                    self._kimi_recovery_intents.pop(session_id, None)
            self._pop_kimi_answer_parts(str(chat_id or ""), session_id, owner_prompt_id)
            if isinstance(queue, list):
                doomed = {
                    str(target_turns[idx].get("kimi_prompt_id") or "").strip()
                    for idx in accepted
                    if 0 <= idx < len(target_turns) and isinstance(target_turns[idx], dict)
                }
                queue[:] = [
                    entry for entry in queue
                    if not (isinstance(entry, dict) and str(entry.get("prompt_id") or "").strip() in doomed)
                ]
                if is_current_target:
                    self.active_kimi_request_queue = queue
        active_count = sum(
            1 for candidate in target_turns
            if isinstance(candidate, dict)
            and str(candidate.get("request_status") or "").strip() == "pending"
            and is_kimi_model(str(candidate.get("model") or ""))
            and (not session_id or str(candidate.get("kimi_session_id") or target_chat.get("kimi_session_id") or "").strip() == session_id)
        )
        target_chat["kimi_turn_active"] = active_count > 0
        if is_current_target:
            self.active_kimi_turn_active = active_count > 0
            if active_count > 0:
                self.is_running = True
                self._active_request_count = active_count
            else:
                self.is_running = False
                self._active_request_count = 0
                self.new_chat_button.Enable()
                self._set_input_hint_idle()
        if isinstance(group_active_owner, dict):
            self._clear_kimi_active_turn(
                chat_id,
                group_active_owner.get("turn_idx"),
                str(group_active_owner.get("turn_id") or "").strip() or None,
            )
        else:
            self._clear_kimi_active_turn(chat_id, target_idx, turn.get("kimi_turn_id"))
        if not is_current_target:
            self._refresh_visible_history_chat(str(chat_id or "").strip())
        self._defer_codex_state_save()

    def _receive_kimi_interaction(self, event: CodexEvent) -> None:
        session_id = self._event_thread_id(event)
        interaction_id = str(event.request_id or "").strip()
        if not session_id or not interaction_id:
            return
        key = (session_id, interaction_id)
        if event.type == "server_request_ended":
            with self._kimi_owner_lock:
                request = self._kimi_interactions.get(key, {})
                if request.get("state") == "ended":
                    return
                self._end_kimi_interaction(key, request)
            self._call_after_if_alive(self._close_ended_kimi_dialog, key)
            chat_id = request.get("chat_id")
            if chat_id:
                intended = request.get("intended_outcome")
                outcome = (event.data or {}).get("outcome")
                self._call_after_if_alive(self._finish_kimi_interaction_feedback, key, chat_id,
                                          bool(intended and outcome == intended))
            return
        with self._kimi_owner_lock:
            chat_id = self._resolve_kimi_event_chat_id(event)
            if not chat_id:
                return
            previous = self._kimi_interactions.get(key)
            if previous is None:
                self._kimi_interactions[key] = {
                    "state": "pending", "chat_id": chat_id, "kind": event.method, "params": event.params or {},
                    "agent_id": self._kimi_event_agent_id(event),
                }
            elif (previous["state"] == "uncertain" and previous["chat_id"] == chat_id
                  and (event.origin_timestamp or 0) > previous["uncertain_since"]):
                # The server generated another pending frame after the lost
                # response. This enables a manual retry without resending.
                previous["state"] = "retryable"
                error = previous["error"]
            else:
                return
        if previous is None:
            self._call_after_if_alive(self._offer_kimi_interaction, key)
        else:
            self._call_after_if_alive(self._report_kimi_interaction_error, key, chat_id, error, True)

    def _end_kimi_interaction(self, key: tuple[str, str], request: dict) -> None:
        # The caller holds the owner lock. Keep terminal identities in completion order.
        request["state"] = "ended"
        self._kimi_interactions.pop(key, None)
        self._kimi_interactions[key] = {"state": "ended"}
        ended = [item for item, value in self._kimi_interactions.items() if value["state"] == "ended"]
        for item in ended[:-self._kimi_replay_window]:
            self._kimi_interactions.pop(item)

    def _offer_kimi_interaction(self, key: tuple[str, str]) -> None:
        with self._kimi_owner_lock:
            request = self._kimi_interactions.get(key)
            if not request or request["state"] != "pending":
                return
            chat_id = request["chat_id"]
        self._push_remote_attention(chat_id, "action_required", "/".join(key), "Kimi Code 需要您的确认或输入")
        self._show_pending_kimi_interaction()

    def _close_ended_kimi_dialog(self, key: tuple[str, str]) -> None:
        active = self._kimi_interaction_dialog
        if active and active[0] == key and active[1].IsModal():
            active[1].EndModal(wx.ID_CANCEL)

    def _visible_kimi_interaction(self, *states: str):
        chat_id = str(self.active_chat_id or self.current_chat_id or "").strip()
        session_id = self._kimi_session_id_for_chat(self._current_chat_state)
        return next(((key, request) for key, request in self._kimi_interactions.items()
                     if key[0] == session_id and request["state"] in states and request["chat_id"] == chat_id), None)

    def _show_pending_kimi_interaction(self, *, retry: bool = False) -> None:
        if self._kimi_interaction_dialog:
            return
        with self._kimi_owner_lock:
            states = ("retryable", "uncertain") if retry else ("pending",)
            pending = self._visible_kimi_interaction(*states)
            if not pending:
                return
            key, request = pending
            recheck = request["state"] == "uncertain"
            expired = self._kimi_interaction_expired(request)
            if expired:
                self._end_kimi_interaction(key, request)
            else:
                request["state"] = "rechecking" if recheck else "showing"
                if retry:
                    # Let the next menu invocation reach another unresolved request.
                    self._kimi_interactions[key] = self._kimi_interactions.pop(key)
        if expired:
            self._finish_kimi_interaction_feedback(key, request["chat_id"], False)
            self._call_after_if_alive(self._show_pending_kimi_interaction)
            return
        if recheck:
            bindings.app.threading.Thread(target=self._recheck_kimi_interaction_worker, args=(key, request),
                             daemon=True).start()
            return
        params = request["params"]
        kind = request["kind"]
        if kind == "approval":
            tool = str(params.get("tool_name") or "").strip()
            action = str(params.get("action") or "").strip()
            display = params.get("tool_input_display")
            if display is not None and not isinstance(display, str):
                display = json.dumps(display, ensure_ascii=False)
            questions = [{"id": "approval", "header": "Kimi Code 批准请求",
                          "question": "\n".join(part for part in (tool, action, display) if part),
                          "options": [{"label": "批准", "value": "approved"},
                                      {"label": "拒绝", "value": "rejected"}]}]
            dialog = bindings.app.CodexUserInputDialog(self, questions)
            dialog.SetTitle("Kimi Code 批准请求")
        else:
            questions = [question for question in params.get("questions") or []
                         if isinstance(question, dict) and question.get("id")]
            dialog = bindings.app.CodexUserInputDialog(self, questions, kimi_question=True)
        self._kimi_interaction_dialog = (key, dialog)
        try:
            with self._kimi_owner_lock:
                showing = request["state"] == "showing"
            accepted = showing and dialog.ShowModal() == wx.ID_OK
            if kind == "approval":
                decision = dialog.get_answers().get("approval", ["cancelled"])[0] if accepted else "cancelled"
                response = {"kind": kind, "decision": decision}
            elif accepted:
                response = {"kind": kind, "answers": dialog.get_kimi_answers()}
            else:
                response = {"kind": kind, "dismissed": True}
        finally:
            self._kimi_interaction_dialog = None
            dialog.Destroy()
        with self._kimi_owner_lock:
            showing = request["state"] == "showing"
            expired = showing and self._kimi_interaction_expired(request)
            should_send = showing and not expired
            if expired:
                self._end_kimi_interaction(key, request)
            elif should_send:
                request["state"] = "responding"
                request["intended_outcome"] = (response.get("decision") if kind == "approval"
                                               else ("dismissed" if response.get("dismissed") else "answered"))
        if expired:
            self._finish_kimi_interaction_feedback(key, request["chat_id"], False)
        elif should_send:
            bindings.app.threading.Thread(target=self._answer_kimi_interaction_worker, args=(key, request, response),
                             daemon=True).start()
        self._call_after_if_alive(self._show_pending_kimi_interaction)

    def _kimi_interaction_expired(self, request: dict) -> bool:
        expires = self._kimi_timestamp_value((request.get("params") or {}).get("expires_at"))
        return expires is not None and expires <= bindings.app.time.time()

    def _answer_kimi_interaction_worker(self, key: tuple[str, str], request: dict, response: dict) -> None:
        client = None
        try:
            client = self._ensure_kimi_client()
            client.start()
            with self._kimi_owner_lock:
                if self._kimi_interactions.get(key) is not request or request["state"] != "responding":
                    return
                expired = self._kimi_interaction_expired(request)
            if not expired:
                client.answer_interaction(*key, response)
        except Exception as exc:
            # Fixed runtime codes for missing session, missing interaction and already ended.
            if getattr(exc, "status_code", None) in {40401, 40404, 40902}:
                self._complete_kimi_interaction_attempt(key, request, "responding", "ended")
            elif getattr(exc, "result_unknown", False) and client is not None:
                self._recheck_kimi_interaction_worker(key, request, client, previous_state="responding", error=str(exc))
            else:
                self._complete_kimi_interaction_attempt(key, request, "responding", "retryable", error=str(exc))
        else:
            self._complete_kimi_interaction_attempt(key, request, "responding", "ended", success=not expired)

    def _recheck_kimi_interaction_worker(self, key: tuple[str, str], request: dict, client=None, *,
                                         previous_state: str = "rechecking", error: str = "") -> None:
        try:
            if client is None:
                client = self._ensure_kimi_client()
                client.start()
            with self._kimi_owner_lock:
                if self._kimi_interactions.get(key) is not request or request["state"] != previous_state:
                    return
            if self._kimi_interaction_expired(request):
                self._complete_kimi_interaction_attempt(key, request, previous_state, "ended")
                return
            server_state = client.interaction_state(*key, request["agent_id"])
        except (KimiServerError, OSError) as exc:
            server_state = ""
            error = error or str(exc)
        if server_state in {"approved", "rejected", "cancelled", "answered", "dismissed"}:
            self._complete_kimi_interaction_attempt(key, request, previous_state, "ended",
                                                   success=server_state == request.get("intended_outcome"))
        elif server_state == "pending":
            self._complete_kimi_interaction_attempt(key, request, previous_state, "retryable",
                                                   error="服务端确认 Kimi Code 请求仍待回复")
        else:
            self._complete_kimi_interaction_attempt(key, request, previous_state, "uncertain",
                                                   error=error or "服务端暂未确认回复结果")

    def _complete_kimi_interaction_attempt(self, key, request, previous_state, state, *,
                                           success=False, error="") -> None:
        with self._kimi_owner_lock:
            if self._kimi_interactions.get(key) is not request or request["state"] != previous_state:
                return
            if state != "ended" and self._kimi_interaction_expired(request):
                state = "ended"
            if state == "ended":
                self._end_kimi_interaction(key, request)
            else:
                request.update(state=state, error=error, uncertain_since=bindings.app.time.time())
        if state == "ended":
            self._call_after_if_alive(self._finish_kimi_interaction_feedback, key, request["chat_id"], success)
        else:
            self._call_after_if_alive(self._report_kimi_interaction_error, key, request["chat_id"],
                                      error, state == "retryable")

    def _report_kimi_interaction_error(self, key: tuple[str, str], chat_id: str, error: str,
                                      retryable: bool) -> None:
        message = f"回复 Kimi Code 请求失败：{error}。可从应用菜单重试 Kimi 请求。"
        if not retryable:
            message = f"Kimi Code 回复结果待确认：{error}。可从应用菜单重试 Kimi 请求以查询状态。"
        with self._kimi_owner_lock:
            request = self._kimi_interactions.get(key)
            expected_state = "retryable" if retryable else "uncertain"
            if (not request or request["state"] != expected_state or request["chat_id"] != chat_id
                    or request.get("error") != error or self._kimi_interaction_expired(request)):
                return
            self._push_remote_attention(chat_id, "action_required", "/".join(key) + "/error", message)
            self._set_kimi_interaction_feedback(key, chat_id, error=message)

    def _finish_kimi_interaction_feedback(self, key: tuple[str, str], chat_id: str, success: bool) -> None:
        self._set_kimi_interaction_feedback(key, chat_id, status="已回复 Kimi Code 请求。" if success else "Kimi Code 请求已结束。")

    def _set_kimi_interaction_feedback(self, key: tuple[str, str], chat_id: str, *,
                                       error: str = "", status: str = "") -> None:
        target_chat, is_current = self._kimi_target_chat(chat_id)
        if target_chat is None:
            return
        errors = target_chat.setdefault("kimi_interaction_errors", {})
        previous = target_chat.get("kimi_interaction_error")
        errors.pop("/".join(key), None)
        if error:
            errors["/".join(key)] = error
        current = next(reversed(errors.values()), "")
        if current:
            target_chat["kimi_interaction_error"] = current
        else:
            target_chat.pop("kimi_interaction_errors", None)
            target_chat.pop("kimi_interaction_error", None)
        if is_current and (error or previous != current or not current):
            self.SetStatusText(current or status)
        self._defer_codex_state_save()

    def _prune_kimi_interaction_errors(self, chat: dict) -> None:
        errors = chat.get("kimi_interaction_errors")
        if not errors and not chat.get("kimi_interaction_error"):
            return
        chat_id = str(chat.get("source_chat_id") or chat.get("id") or "").strip()
        session_id = self._kimi_session_id_for_chat(chat)
        with self._kimi_owner_lock:
            live_keys = {
                "/".join(key) for key, request in self._kimi_interactions.items()
                if key[0] == session_id and request.get("chat_id") == chat_id
                and request["state"] != "ended" and not self._kimi_interaction_expired(request)
            }
        remaining = {key: value for key, value in errors.items() if key in live_keys} if isinstance(errors, dict) else {}
        if remaining:
            chat["kimi_interaction_errors"] = remaining
            chat["kimi_interaction_error"] = next(reversed(remaining.values()))
        else:
            chat.pop("kimi_interaction_errors", None)
            chat.pop("kimi_interaction_error", None)

    def _handle_kimi_request_dialog(self, request: dict) -> None:
        self._push_remote_attention(str(request.get("chat_id") or ""), "action_required",
            str(request.get("request_id") or ""), "\u9700\u8981\u60a8\u7684\u786e\u8ba4\u6216\u8f93\u5165")
        params = request.get("params") if isinstance(request.get("params"), dict) else {}
        session_id = str(request.get("session_id") or "").strip()
        chat_id = str(request.get("chat_id") or "").strip()
        approval_id = str(
            params.get("approvalId") or params.get("approval_id") or params.get("id") or request.get("request_id") or ""
        ).strip()
        description = str(
            params.get("description") or params.get("message") or params.get("title") or params.get("tool") or ""
        ).strip() or "Kimi Code 请求批准一个操作。"
        questions = [
            {
                "id": "approval",
                "header": "Kimi Code 批准请求",
                "question": description,
                "options": [
                    {"label": "批准", "value": "approved"},
                    {"label": "拒绝", "value": "rejected"},
                ],
            }
        ]
        dlg = bindings.app.CodexUserInputDialog(self, questions)
        try:
            accepted = dlg.ShowModal() == wx.ID_OK
            answers = dlg.get_answers() if accepted else {}
        finally:
            dlg.Destroy()
        decision = "cancelled"
        if accepted:
            values = answers.get("approval") if isinstance(answers, dict) else []
            if isinstance(values, list) and values:
                decision = str(values[0] or "").strip() or "approved"
            else:
                decision = "approved"
        bindings.app.threading.Thread(
            target=self._answer_kimi_approval_worker,
            args=(session_id, approval_id, decision, chat_id),
            daemon=True,
        ).start()

    def _answer_kimi_approval_worker(self, session_id: str, approval_id: str, decision: str, chat_id: str) -> None:
        try:
            client = self._ensure_kimi_client()
            client.start()
            resolved_approval_id = str(approval_id or "").strip()
            if not resolved_approval_id and session_id:
                approvals = client.list_approvals(session_id)
                if approvals:
                    first = approvals[0]
                    if isinstance(first, dict):
                        resolved_approval_id = str(first.get("id") or first.get("approval_id") or "").strip()
            if not session_id or not resolved_approval_id:
                return
            client.answer_approval(session_id, resolved_approval_id, decision)
        except Exception as exc:
            self._call_after_if_alive(self._apply_kimi_error, chat_id, f"回复 Kimi Code 批准请求失败：{exc}")

    def _dispatch_kimi_event_to_ui(self, chat_id: str, event: CodexEvent) -> None:
        if not event:
            return
        if not self._is_ui_alive():
            return
        should_schedule = False
        with self._kimi_ui_event_lock:
            self._pending_kimi_ui_events.append((str(chat_id or "").strip(), event))
            if not self._kimi_ui_event_flush_scheduled:
                self._kimi_ui_event_flush_scheduled = True
                should_schedule = True
        if should_schedule and not self._call_after_if_alive(self._drain_kimi_ui_events):
            with self._kimi_ui_event_lock:
                self._kimi_ui_event_flush_scheduled = False

    def _drain_kimi_ui_events(self) -> None:
        batch_size = self._kimi_ui_event_batch_size()
        with self._measure_ui_operation("kimi_ui_event_drain", batch_size=batch_size):
            with self._kimi_ui_event_lock:
                batch = self._pending_kimi_ui_events[:batch_size]
                del self._pending_kimi_ui_events[: len(batch)]
                has_more = bool(self._pending_kimi_ui_events)
                self._kimi_ui_event_flush_scheduled = has_more
            self._background_ui_update_depth += 1
            self._codex_ui_batch_depth += 1
            try:
                for queued_chat_id, queued_event in batch:
                    self._on_kimi_event_for_chat(queued_chat_id, queued_event)
            finally:
                self._background_ui_update_depth = max(0, self._background_ui_update_depth - 1)
                self._codex_ui_batch_depth = max(0, self._codex_ui_batch_depth - 1)
                self._flush_deferred_execution_list_updates()
                if not self._navigation_quiet_active():
                    self._flush_pending_background_ui_updates()
                self._start_execution_step_persist_worker()
            if has_more:
                if self._wx_main_loop_running():
                    self._kimi_ui_event_drain_timer = self._call_later_if_alive(
                        CODEX_UI_EVENT_BATCH_DELAY_MS,
                        self._drain_kimi_ui_events,
                    )
                else:
                    self._kimi_ui_event_drain_timer = None
                if (
                    self._kimi_ui_event_drain_timer is None
                    and not self._call_after_if_alive(self._drain_kimi_ui_events)
                ):
                    with self._kimi_ui_event_lock:
                        self._kimi_ui_event_flush_scheduled = False
            else:
                self._kimi_ui_event_drain_timer = None

    def _kimi_ui_event_batch_size(self) -> int:
        if self._primary_navigation_control_has_focus():
            return max(1, min(CODEX_UI_INTERACTIVE_EVENT_BATCH_SIZE, CODEX_UI_EVENT_BATCH_SIZE))
        return CODEX_UI_EVENT_BATCH_SIZE

    def _maybe_trigger_kimi_thinking_sync(self, chat_id: str, event: CodexEvent) -> None:
        """Mid-turn channel: throttle-gated, event-driven REST thinking sync.

        The recovery worker alone exits mid-turn in a healthy session, so
        thinking rows would only appear at completion. Each activity-bearing
        Kimi protocol event during a turn with a pending active owner may
        spawn a lightweight daemon fetch; the shared 2s per-session throttle
        keeps REST load negligible.
        """
        try:
            if not self._kimi_protocol_event(event):
                return
            data = event.data if isinstance(event.data, dict) else {}
            source_kind = str(data.get("source_kind") or "").strip()
            if not (
                source_kind.startswith(("tool.", "shell.", "subagent.", "turn.step"))
                or source_kind in {"thinking.delta", "assistant.delta"}
            ):
                return
            session_id = str(self._event_thread_id(event) or "").strip()
            chat_id = str(chat_id or "").strip()
            if not session_id or not chat_id:
                return
            with self._kimi_owner_lock:
                active = self._kimi_active_turns.get(chat_id)
            if not isinstance(active, dict):
                return
            if str(active.get("session_id") or "").strip() != session_id:
                return
            if not self._kimi_thinking_sync_due(session_id):
                return
            turn_idx = active.get("turn_idx")
            if not isinstance(turn_idx, int) or turn_idx < 0:
                # Without a valid turn index the sync would silently no-op.
                return
            target_chat = self._chat_state_for_execution_steps(chat_id) or {}
            turns = target_chat.get("turns", [])
            turn = turns[turn_idx] if turn_idx < len(turns) and isinstance(turns[turn_idx], dict) else {}
            owner = {
                **active,
                "chat_id": chat_id,
                "session_id": session_id,
                "turn_idx": turn_idx,
                "turn_id": str(active.get("turn_id") or "").strip(),
                "prompt_id": str(active.get("prompt_id") or turn.get("kimi_prompt_id") or ""),
                "owner_prompt_id": str(active.get("owner_prompt_id") or turn.get("kimi_owner_prompt_id") or turn.get("kimi_prompt_id") or ""),
                "revision": self._safe_int(target_chat.get("revision", 1), 1),
            }
        except Exception:
            return
        bindings.app.threading.Thread(
            target=self._kimi_thinking_fetch_worker,
            args=(owner,),
            daemon=True,
        ).start()

    def _kimi_thinking_fetch_worker(self, owner: dict) -> None:
        """Daemon fetch for the mid-turn channel: REST off the UI thread, then
        marshal the sync back onto it. Failures stay silent; the next throttled
        event retries."""
        try:
            client = self._kimi_client
            if client is None:
                return
            session_id = str((owner or {}).get("session_id") or "").strip()
            chat_id = str((owner or {}).get("chat_id") or "").strip()
            if not session_id or not chat_id:
                return
            timeout = getattr(client, "rest_timeout", None) or 5.0
            try:
                messages = client.list_messages(session_id, timeout=timeout)
            except TypeError as exc:
                if "timeout" not in str(exc):
                    raise
                messages = client.list_messages(session_id)
            if not isinstance(messages, list) or not messages:
                return
            messages = self._kimi_messages_back_to_boundary(
                client, session_id, str(owner.get("owner_prompt_id") or owner.get("prompt_id") or ""), messages=messages,
                deadline=bindings.app.time.monotonic() + float(timeout),
            )
            self._call_after_if_alive(self._kimi_sync_thinking_rows, chat_id, owner, messages)
        except Exception:
            pass

    def _on_kimi_event(self, event: CodexEvent) -> None:
        chat_id = self._resolve_kimi_event_chat_id(event)
        if not chat_id:
            return
        if bindings.app.threading.current_thread() is not bindings.app.threading.main_thread():
            self._dispatch_kimi_event_to_ui(chat_id, event)
            return
        self._on_kimi_event_for_chat(chat_id, event)

    def _on_kimi_event_for_chat(self, chat_id: str, event: CodexEvent) -> None:
        if not isinstance(event, CodexEvent):
            return
        chat_id = str(chat_id or "").strip()
        event_type = str(getattr(event, "type", "") or "").strip()
        event_turn_id = self._event_turn_id(event)
        if not chat_id:
            chat_id = self._resolve_kimi_event_chat_id(event)
            if not chat_id:
                return
        is_current_chat = chat_id in {self.active_chat_id, self.current_chat_id, "", None}
        identity_chat = self._current_chat_state if is_current_chat else self._find_archived_chat(chat_id)
        if isinstance(identity_chat, dict) and not self._kimi_event_is_compatible_with_chat(identity_chat, event):
            return
        authoritative = self._kimi_event_is_authoritative(event)
        if authoritative:
            self._apply_kimi_event_scope(chat_id, event)
        self._promote_kimi_interim_assistant(chat_id, event)
        self._maybe_trigger_kimi_thinking_sync(chat_id, event)
        event_turn_id = self._event_turn_id(event)
        silent_notification = (
            event_type == "notification"
            and str(getattr(event, "display_kind", "") or "").strip() in {"session", "unmapped"}
        )
        execution_entry = None
        if event_type not in {"agent_message_delta", "thread_status_changed"} and not silent_notification:
            execution_entry = self._build_execution_entry(event)
        delta_kind = str(getattr(event, "display_kind", "") or "").strip()
        if event_type == "agent_message_delta" and delta_kind == "assistant" and authoritative:
            # Thinking and assistant prose are separate streams. Keep the
            # latter until a tool boundary can identify interim commentary.
            if self._kimi_protocol_event(event):
                self._flush_execution_delta(chat_id, event_turn_id or None, display_kind="thinking")
                execution_entry = self._build_execution_entry(event)
            else:
                self._flush_execution_delta(chat_id, event_turn_id or None, display_kind="thinking")
            self._buffer_execution_delta(chat_id, event)
            self._accumulate_kimi_answer_delta(chat_id, event)
        elif event_type == "agent_message_delta" and delta_kind == "assistant" and not authoritative:
            # Explicit subagent prose is execution progress. It must neither
            # enter the hidden final-answer buffer nor disappear from F1.
            execution_entry = self._build_execution_entry(event)
        if event_type == "notification" and str(getattr(event, "subtype", "") or "") == "event.session.work_changed":
            protocol = (event.data or {}).get("protocol") if isinstance(event.data, dict) else {}
            if isinstance(protocol, dict) and "busy" in protocol:
                identity_chat = self._current_chat_state if is_current_chat else self._find_archived_chat(chat_id)
                if isinstance(identity_chat, dict):
                    proof = None
                    session_id = self._event_thread_id(event)
                    with self._kimi_owner_lock:
                        active_owner = self._kimi_active_turns.get(chat_id)
                        if (
                            protocol.get("busy") is False
                            and authoritative
                            and isinstance(active_owner, dict)
                            and (not session_id or str(active_owner.get("session_id") or "").strip() == session_id)
                        ):
                            key = self._kimi_owner_key_for(active_owner)
                            generation = int(active_owner.get("generation") or 0)
                            self._kimi_idle_proofs[key] = generation
                            proof = {"owner_key": list(key), "generation": generation}
                    identity_chat["kimi_idle_verified"] = proof
        context_usage = self._kimi_context_usage_payload(event) if event_type == "thread_status_changed" else None
        if context_usage is not None and isinstance(identity_chat, dict):
            current_session_id = str(identity_chat.get("kimi_session_id") or "").strip()
            if not current_session_id or self._event_thread_id(event) != current_session_id:
                context_usage = None
        if not is_current_chat:
            if event_type == "agent_message_delta":
                if delta_kind == "thinking":
                    self._buffer_execution_delta(chat_id, event)
                    self._flush_execution_delta(chat_id, event_turn_id or None, display_kind="thinking", retain=True)
                elif delta_kind == "commentary":
                    self._append_kimi_tool_diagnostic(chat_id, event)
                elif execution_entry:
                    self._append_execution_entry_to_chat(chat_id, execution_entry, save_state=False)
                return
            if event_type != "thread_status_changed" and not silent_notification:
                self._flush_execution_delta(chat_id, event_turn_id or None)
            target_chat = self._find_archived_chat(chat_id)
            if not isinstance(target_chat, dict):
                return
            target_turns = target_chat.get("turns") if isinstance(target_chat.get("turns"), list) else []
            target_idx = self._kimi_event_turn_index(target_turns, event)
            if context_usage is not None and target_idx >= 0:
                target_chat["kimi_context_revision"] = int(target_chat.get("kimi_context_revision") or 0) + 1
                turn = target_turns[target_idx] if target_idx < len(target_turns) and isinstance(target_turns[target_idx], dict) else {}
                completed_turn = str(turn.get("request_status") or "").strip() == "done"
                if completed_turn:
                    self._pending_context_usage_by_turn.pop(self._context_usage_pending_key_from_chat(target_chat, target_idx), None)
                    changed = self._set_chat_context_usage(target_chat, context_usage)
                else:
                    changed = self._set_pending_context_usage_for_turn(target_chat, target_idx, context_usage)
                if changed and completed_turn:
                    self._defer_codex_state_save()
                return
            if event_type == "thread_status_changed":
                return
            if execution_entry:
                self._append_execution_entry_to_chat(chat_id, execution_entry, save_state=False)
            if event_type == "turn_started" and authoritative:
                self._apply_kimi_turn_started(chat_id, target_chat, target_turns, event, is_current_target=False)
                self._defer_codex_state_save()
                return
            if event_type == "server_request" and str(event.method or "") == "approval":
                target_chat["kimi_turn_active"] = True
                self._mark_history_list_dirty()
                if target_idx >= 0:
                    self._mark_chat_turns_dirty(chat_id, target_idx)
                self._refresh_visible_history_chat(chat_id)
                self._defer_codex_state_save()
                return
            if event_type == "error" and authoritative:
                self._apply_kimi_error(
                    chat_id,
                    str(event.text or "Kimi Code 错误"),
                    target_idx if target_idx >= 0 else None,
                    event_turn_id or None,
                    self._kimi_event_prompt_id(event) or None,
                )
                return
            if target_idx >= 0 and target_idx < len(target_turns):
                turn = target_turns[target_idx]
                if isinstance(turn, dict):
                    if event_type == "turn_completed":
                        status = str(getattr(event, "status", "") or "completed").strip() or "completed"
                        finalized = self._finalize_kimi_turn_state(chat_id, target_chat, target_turns, target_idx, event)
                        successful = [
                            idx for idx in finalized
                            if 0 <= idx < len(target_turns)
                            and isinstance(target_turns[idx], dict)
                            and str(target_turns[idx].get("request_status") or "").strip() == "done"
                            and bool(str(target_turns[idx].get("answer_md") or "").strip())
                        ]
                        if finalized:
                            self._mark_chat_turns_dirty(chat_id, min(finalized))
                            self._refresh_visible_history_chat(chat_id)
                            self._request_kimi_chat_information(target_chat, str(turn.get("model") or DEFAULT_KIMI_MODEL), visible_only=True)
                            self._request_kimi_quota(target_chat, str(turn.get("model") or DEFAULT_KIMI_MODEL))
                        if successful:
                            for finalized_idx in finalized:
                                finalized_turn = target_turns[finalized_idx]
                                if (
                                    status == "completed"
                                    and isinstance(finalized_turn, dict)
                                    and str(finalized_turn.get("request_status") or "").strip() == "done"
                                ):
                                    self._refresh_context_usage_after_done(
                                        target_chat,
                                        target_turns,
                                        finalized_idx,
                                        str(finalized_turn.get("model") or DEFAULT_KIMI_MODEL),
                                    )
            self._defer_codex_state_save()
            return
        if event_type == "agent_message_delta":
            if delta_kind == "thinking":
                self._buffer_execution_delta(chat_id, event)
                self._flush_execution_delta(chat_id, event_turn_id or None, display_kind="thinking", retain=True)
            elif delta_kind == "commentary":
                self._append_kimi_tool_diagnostic(chat_id, event)
            elif execution_entry:
                self._append_execution_entry_to_chat(chat_id, execution_entry, save_state=False)
            return
        if event_type != "thread_status_changed" and not silent_notification:
            self._flush_execution_delta(chat_id, event_turn_id or None)
        if execution_entry:
            self._append_execution_entry_to_chat(chat_id, execution_entry, save_state=False)
        if context_usage is not None:
            target_idx = self._active_kimi_event_target_index(event)
            if target_idx < 0:
                target_idx = self.active_turn_idx if 0 <= self.active_turn_idx < len(self.active_session_turns) else (len(self.active_session_turns) - 1)
            if target_idx >= 0:
                self._current_chat_state["kimi_context_revision"] = int(self._current_chat_state.get("kimi_context_revision") or 0) + 1
                turn = self.active_session_turns[target_idx] if target_idx < len(self.active_session_turns) and isinstance(self.active_session_turns[target_idx], dict) else {}
                completed_turn = str(turn.get("request_status") or "").strip() == "done"
                if completed_turn:
                    self._pending_context_usage_by_turn.pop(self._context_usage_pending_key_from_chat(self._current_chat_state, target_idx), None)
                    changed = self._set_chat_context_usage(self._current_chat_state, context_usage)
                else:
                    changed = self._set_pending_context_usage_for_turn(self._current_chat_state, target_idx, context_usage)
                if changed and completed_turn:
                    self._defer_codex_state_save()
            return
        if event_type == "thread_status_changed":
            return
        if event_type == "turn_started" and authoritative:
            self._apply_kimi_turn_started(chat_id, self._current_chat_state, self.active_session_turns, event, is_current_target=True)
            self._defer_codex_state_save()
            return
        if event_type == "server_request":
            if str(event.method or "") == "approval":
                event_data = event.data if isinstance(getattr(event, "data", None), dict) else {}
                self._handle_kimi_request_dialog(
                    {
                        "chat_id": chat_id,
                        "session_id": self._event_thread_id(event) or str(event_data.get("session_id") or ""),
                        "request_id": event.request_id,
                        "method": event.method,
                        "params": event.params,
                    }
                )
            self._defer_chat_state_save()
            return
        if event_type == "subagent_result":
            # Subagent terminal output is execution progress only.  It never
            # owns or publishes the user's answer row.
            self._defer_codex_state_save()
            return
        if event_type == "error" and authoritative:
            self._apply_kimi_error(
                chat_id,
                str(event.text or "Kimi Code 错误"),
                None,
                event_turn_id or None,
                self._kimi_event_prompt_id(event) or None,
            )
            return
        if event_type == "turn_completed":
            status = str(getattr(event, "status", "") or "completed").strip() or "completed"
            target_idx = self._active_kimi_event_target_index(event)
            finalized = self._finalize_kimi_turn_state(chat_id, self._current_chat_state, self.active_session_turns, target_idx, event)
            if not finalized:
                self._defer_chat_state_save()
                return
            self._request_kimi_chat_information(self._current_chat_state, str(self._current_chat_state.get("model") or DEFAULT_KIMI_MODEL), visible_only=True)
            self._request_kimi_quota(self._current_chat_state, str(self._current_chat_state.get("model") or DEFAULT_KIMI_MODEL))
            self._request_execution_list_sync(self._current_chat_state)
            still_active = bool(self._current_chat_state.get("kimi_turn_active"))
            self.active_kimi_turn_active = still_active
            ui_idx = target_idx if target_idx in finalized else finalized[0]
            turn = {}
            if 0 <= ui_idx < len(self.active_session_turns) and isinstance(self.active_session_turns[ui_idx], dict):
                turn = self.active_session_turns[ui_idx]
                if status == "completed" and str(turn.get("request_status") or "").strip() == "done":
                    self._refresh_context_usage_after_done(self._current_chat_state, self.active_session_turns, ui_idx, str(turn.get("model") or DEFAULT_KIMI_MODEL))
                if self._background_ui_mutations_blocked():
                    self._mark_background_answer_list_dirty()
                else:
                    for finalized_idx in finalized:
                        self._update_active_answer_row(finalized_idx)
                self._mark_chat_turns_dirty(start_index=ui_idx)
            successful = [
                idx for idx in finalized
                if 0 <= idx < len(self.active_session_turns)
                and isinstance(self.active_session_turns[idx], dict)
                and str(self.active_session_turns[idx].get("request_status") or "").strip() == "done"
                and bool(str(self.active_session_turns[idx].get("answer_md") or "").strip())
            ]
            if finalized and not still_active:
                self.is_running = False
                self._active_request_count = 0
                self.new_chat_button.Enable()
                self._set_input_hint_idle()
            self._defer_chat_state_save()
            if finalized and self.view_mode == "active":
                if self._background_ui_mutations_blocked():
                    self._mark_background_answer_list_dirty()
                elif self._find_answer_row_index(ui_idx) < 0:
                    if self._append_completed_answer_to_answer_list(ui_idx, turn):
                        self._focus_latest_answer()
                    else:
                        self._refresh_answer_list_preserving_selection(refresh_execution=self._detail_panel_mode() != "execution")
            return
        self._defer_codex_state_save()
