"""Background native discovery and application history publication."""

import logging
import sqlite3
import threading
import time

import wx

from agy_acp import AgyError, find_agy_executable
from agy_history import list_agy_sessions, read_agy_session
from native_history import NATIVE_CHAT_FIELDS


_LOG = logging.getLogger(__name__)
_REFRESH_SECONDS = 60


class NativeHistoryMixin:
    def _native_history_discoverers(self):
        executable = find_agy_executable()
        if executable:
            pair = self._read_pair_id()
            yield (
                lambda: list_agy_sessions(executable, pair),
                lambda session: read_agy_session(executable, session),
            )

    def _start_native_history_refresh(self) -> None:
        self._native_history_worker = None
        self._native_history_last_scan = 0.0
        self._native_history_reserved = set()
        self._native_history_timer = None
        self._poll_native_history()

    def _poll_native_history(self) -> None:
        if getattr(self, "_closing", False) or not self._is_ui_alive():
            return
        self._request_native_history_refresh()
        self._native_history_timer = wx.CallLater(_REFRESH_SECONDS * 1000, self._poll_native_history)

    def _stop_native_history_refresh(self) -> None:
        if self._native_history_timer is not None:
            self._native_history_timer.Stop()
            self._native_history_timer = None

    def _request_native_history_refresh(self) -> None:
        if (not getattr(self, "_chat_store_enabled", False)
                or not hasattr(self, "_native_history_reserved")
                or getattr(self, "_closing", False)):
            return
        worker = self._native_history_worker
        if (worker is not None and worker.is_alive()) or time.monotonic() - self._native_history_last_scan < 3:
            return
        discoverers = list(self._native_history_discoverers())
        if not discoverers:
            return
        self._native_history_last_scan = time.monotonic()
        self._native_history_worker = threading.Thread(
            target=self._scan_native_history, args=(discoverers,), name="native-history", daemon=True,
        )
        self._native_history_worker.start()

    def _native_history_authority_ui(self, session, chat_id, *, reserve=False, expected=None):
        if getattr(self, "_closing", False) or chat_id in self._native_history_reserved:
            return 409, {}
        if session.identity.provider == "agy" and (
                chat_id in self._agy_busy or self._agy_new_chat_pending or self._agy_remote_creations):
            return 409, {}
        if chat_id in getattr(self, "_shortcut_clear_pending", {}):
            return 409, {}
        chat = (self._current_chat_state if chat_id == self.active_chat_id
                else self._chat_summary_by_id(chat_id)) or {}
        if chat.get("codex_turn_active") or chat.get("kimi_turn_active"):
            return 409, {}
        turns = chat.get("turns") or []
        latest = turns[-1] if turns else {}
        if latest.get("request_status") == "pending":
            return 409, {}
        authority = (chat.get("id"), chat.get("updated_at"), chat.get("revision"), len(turns),
                     latest.get("request_generation"), latest.get("request_status"))
        if reserve:
            if authority != expected:
                return 409, {}
            self._native_history_reserved.add(chat_id)
        return 200, authority

    def _scan_native_history(self, discoverers) -> None:
        for list_sessions, read_session in discoverers:
            try:
                seen = set()
                for session in list_sessions():
                    if getattr(self, "_closing", False):
                        return
                    if session.identity in seen:
                        continue
                    seen.add(session.identity)
                    owner, replayed_at = self.chat_store.native_history_target(session)
                    if not owner:
                        continue
                    status, authority = self._run_remote_ui_route(
                        lambda: self._native_history_authority_ui(session, owner))
                    if status != 200:
                        continue
                    revision = self.chat_store.get_clear_reconciliation_authority(owner)["revision"]
                    if not session.updated_at or session.updated_at > replayed_at:
                        read_session(session)
                    status, _ = self._run_remote_ui_route(
                        lambda: self._native_history_authority_ui(session, owner, reserve=True, expected=authority))
                    if status != 200:
                        continue
                    changed = None
                    turns = None
                    try:
                        changed = self.chat_store.merge_native_history(
                            session, expected_chat_id=owner, expected_revision=revision)
                        if changed is not None and session.turns:
                            turns = self.chat_store.load_turns(owner)
                    finally:
                        self._call_after_if_alive(self._publish_native_history_ui, owner, changed, turns, session)
            except (AgyError, sqlite3.Error, OSError) as exc:
                _LOG.info("Native session discovery deferred (%s)", type(exc).__name__)

    def _publish_native_history_ui(self, owner, changed, turns, session) -> tuple[int, dict]:
        self._native_history_reserved.discard(owner)
        if changed is None or getattr(self, "_closing", False):
            return 200, {}
        active = owner == self.active_chat_id
        target = self._current_chat_state if active else self._chat_summary_by_id(owner)
        prior_count = int((target or {}).get("turn_count") or len((target or {}).get("turns") or []))
        if session.identity.provider == "agy" and changed["turn_count"] > prior_count:
            client = self._agy_clients.pop(owner, None)
            if client is not None:
                threading.Thread(target=client.close, name="agy-idle-history-close", daemon=True).start()
        if target is None:
            self.archived_chats.append(dict(changed))
        else:
            if (target.get("title_source") == "native" and not target.get("title_manual")
                    and changed["title_revision"] > int(target.get("title_revision") or 1)):
                target.update({field: changed[field] for field in (
                    "title", "title_updated_at", "title_revision",
                )})
            for field in session.chat_fields:
                target.setdefault(field, changed[field])
            target.update({field: changed[field] for field in NATIVE_CHAT_FIELDS if field in changed})
            target["updated_at"] = max(target.get("updated_at", 0), changed["updated_at"])
            target["turn_count"] = changed["turn_count"]
            if turns is not None and isinstance(target.get("turns"), list) and len(turns) > len(target["turns"]):
                target["turns"].extend(turns[len(target["turns"]):])
                target.pop("execution_steps", None)
                if active:
                    self.active_turn_idx = len(self.active_session_turns) - 1
                if (active and self.view_mode == "active") or self.view_history_id == owner:
                    if not self._navigation_quiet_active():
                        self._render_answer_list(preserve_panel=True)
        self._sort_archived_chats()
        self._mark_history_list_dirty()
        self._push_remote_history_changed(owner)
        return 200, {}
