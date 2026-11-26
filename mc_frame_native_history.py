"""Background native discovery and application history publication."""

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
import sqlite3
import threading
import time

import wx

from agy_acp import AgyError, find_agy_executable
from agy_history import list_agy_sessions, read_agy_session
from codex_client import is_codex_model
from codex_history import CodexHistoryReader, codex_history_homes
from codex_worker_client import codex_worker_workspace
from kimi_history import KimiHistoryReader, kimi_history_storage
from kimi_server_client import is_kimi_model
from kimi_runtime import runtime_path as kimi_runtime_path
from kimi_server_client import KimiServerClient, KimiServerError
from native_history import NATIVE_CHAT_FIELDS, TITLE_FIELDS, NativeTitleRejected


_LOG = logging.getLogger(__name__)
_REFRESH_SECONDS = 60
_NATIVE_TITLE_FIELDS = ("native_title", "native_title_revision", "native_title_observed_at")
_RENAME_UNKNOWN = "原生改名结果尚未确认，已保留上次确认的标题，将重新读取。"


class NativeHistoryMixin:
    def _native_history_discoverers(self):
        for home, private in codex_history_homes():
            reader = CodexHistoryReader(home, private, self._read_pair_id(), self.chat_store.list_chat_summaries)
            yield reader.list_sessions, reader.read_session

        executable = find_agy_executable()
        if executable:
            pair = self._read_pair_id()
            yield (
                lambda: list_agy_sessions(executable, pair),
                lambda session: read_agy_session(executable, session),
            )

        try:
            available = kimi_runtime_path().is_file()
        except (RuntimeError, OSError):
            available = False
        if available:
            self._native_history_kimi_client = KimiServerClient()
            reader = KimiHistoryReader(
                self._native_history_kimi_client, self._read_pair_id(), self._kimi_is_user_prompt,
            )
            yield reader.list_sessions, reader.read_session

    def _start_native_history_refresh(self) -> None:
        self._native_history_worker = None
        self._native_history_kimi_client = None
        self._native_history_last_scan = 0.0
        self._native_history_reserved = set()
        self._native_history_timer = None
        self._native_title_recheck = None
        self._native_rename_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="native-rename")
        self._native_title_pending = {}
        self._poll_native_history()

    def _poll_native_history(self) -> None:
        if getattr(self, "_closing", False) or not self._is_ui_alive():
            return
        self._request_native_history_refresh()
        self._native_history_timer = wx.CallLater(_REFRESH_SECONDS * 1000, self._poll_native_history)

    def _stop_native_history_refresh(self) -> None:
        self._native_rename_executor.shutdown(wait=False, cancel_futures=True)
        if self._native_title_recheck is not None:
            self._native_title_recheck.Stop()
            self._native_title_recheck = None
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

    @staticmethod
    def _native_title_scope(chat, provider):
        fields = {
            "codex": ("codex_thread_id", "codex_home", "codex_storage_id"),
            "kimi": ("kimi_session_id", "kimi_storage_id"),
            "agy": ("agy_session_id", "agy_native_owner"),
        }[provider]
        return {field: chat.get(field) for field in (*fields, "native_identity", "native_account", "native_cwd")}

    def _queue_native_title_rename(self, chat, title, *, local=False):
        model = chat.get("model")
        provider = ("codex" if is_codex_model(model) and chat.get("codex_thread_id") else
                    "kimi" if is_kimi_model(model) and chat.get("kimi_session_id") else "")
        if not provider:
            return None
        owner = chat["id"]
        scope = self._native_title_scope(chat, provider)
        revision = self.chat_store.get_clear_reconciliation_authority(owner)["revision"]
        snapshot = self.chat_store.load_chat_summary(owner)
        if snapshot and all(snapshot.get(key) == value for key, value in scope.items()):
            self._native_title_pending[owner] = self._native_title_pending.get(owner, 0) + 1
            return self._native_rename_executor.submit(
                self._rename_native_title, owner, provider, scope, revision, title, local, self._read_pair_id(),
                self._kimi_client if provider == "kimi" else None,
            )
        return False

    def _rename_native_title(self, owner, provider, scope, revision, title, local, pair, kimi_client):
        status, body = 503, {"accepted": False, "error": _RENAME_UNKNOWN}
        try:
            current = self.chat_store.load_chat_summary(owner)
            if (not current or self.chat_store.get_clear_reconciliation_authority(owner)["revision"] != revision
                    or self._native_title_scope(current, provider) != scope):
                raise NativeTitleRejected("聊天已变更，请重新打开后改名。")
            cwd, account = scope["native_cwd"], scope["native_account"]
            if provider == "codex":
                home = Path(scope["codex_home"]) if scope["codex_home"] else codex_worker_workspace() / ".codex-home"
                reader = CodexHistoryReader(home, not current.get("codex_user_home"), pair, lambda: [])
                try:
                    confirmed = reader.rename_session(scope["codex_thread_id"], cwd, account, title)
                finally:
                    reader.client.close()
            else:
                client = kimi_client or KimiServerClient()
                try:
                    reader = KimiHistoryReader(client, pair, self._kimi_is_user_prompt)
                    confirmed = reader.rename_session(scope["kimi_session_id"], cwd, scope["kimi_storage_id"], account, title)
                finally:
                    if kimi_client is None:
                        client.close()
            if confirmed:
                status, body = self._run_remote_ui_route(
                    lambda: self._commit_native_rename_ui(owner, scope, revision, provider, confirmed, time.time()),
                )
        except NativeTitleRejected as exc:
            status, body = 409, {"accepted": False, "error": str(exc)}
        except (RuntimeError, TimeoutError, ValueError, OSError, sqlite3.Error) as exc:
            _LOG.info("Native rename could not be confirmed (%s)", type(exc).__name__)
        finally:
            self._run_remote_ui_route(lambda: self._native_rename_finished_ui(owner, status == 200, body, local))
        return status, body

    def _commit_native_rename_ui(self, owner, scope, revision, provider, title, observed_at):
        committed = self.chat_store.apply_native_session_title(
            owner, scope, revision, provider, title, observed_at, app_manual=True,
        )
        if committed:
            self._publish_title_update(owner, committed)
            return 200, self._rename_title_response(committed)
        return 409, {"accepted": False, "error": "聊天已变更，请重新打开确认原生标题。"}

    def _observe_native_title_ui(self, owner, provider, sid, title, observed_at, *,
                                 explicit=True, native_identity=None):
        if not title or getattr(self, "_closing", False) or owner in self._native_title_pending:
            return
        chat = self._history_chat_for_id(owner)
        if chat is None or chat.get("id") != owner:
            return
        scope = self._native_title_scope(chat, provider)
        session_field = {"codex": "codex_thread_id", "kimi": "kimi_session_id", "agy": "agy_session_id"}[provider]
        if (sid != scope[session_field]
                or (native_identity is not None and native_identity != scope["native_identity"])
                or (provider == "kimi" and scope["kimi_storage_id"]
                    and scope["kimi_storage_id"] != kimi_history_storage())):
            return
        try:
            revision = self.chat_store.get_clear_reconciliation_authority(owner)["revision"]
            committed = self.chat_store.apply_native_session_title(
                owner, scope, revision, provider, title, observed_at, explicit=explicit,
            )
        except (sqlite3.Error, OSError):
            return
        if committed:
            self._publish_title_update(owner, committed)

    @staticmethod
    def _rename_title_response(chat):
        return {"accepted": True, "chat_id": chat["id"], "title": chat["title"],
                "title_source": chat["title_source"], "title_revision": chat["title_revision"],
                "title_updated_at": chat["title_updated_at"]}

    def _native_rename_finished_ui(self, owner, committed, body, local):
        pending = self._native_title_pending.get(owner, 0)
        if pending > 1:
            self._native_title_pending[owner] = pending - 1
        else:
            self._native_title_pending.pop(owner, None)
        if not committed:
            if self._native_title_recheck is None:
                self._native_title_recheck = wx.CallLater(3100, self._recheck_native_title)
            else:
                self._native_title_recheck.Restart()
        if local and pending <= 1:
            self.SetStatusText("聊天标题已更新" if committed else body.get("error", _RENAME_UNKNOWN))
        return 200, {}

    def _recheck_native_title(self):
        self._native_title_recheck = None
        self._request_native_history_refresh()

    def _publish_title_update(self, owner, committed):
        chat = self._history_chat_for_id(owner)
        if chat is None or chat.get("id") != owner or getattr(self, "_closing", False):
            return
        before = (chat.get("title_revision"), chat.get("native_title_revision"))
        self._merge_published_title(chat, committed)
        if before != (chat.get("title_revision"), chat.get("native_title_revision")):
            self._upsert_history_row(owner, allow_reorder=False)
            self._push_remote_title_changed(chat)

    def _push_remote_title_changed(self, chat):
        body = self._rename_title_response(chat)
        if chat.get("agy_session_id"):
            body.update(agy_native_title=chat.get("native_title", ""),
                        agy_native_title_revision=chat.get("native_title_revision", 0))
        self._push_remote_history_changed(chat["id"], title_update=body)

    @staticmethod
    def _merge_published_title(chat, committed):
        if int(committed.get("title_revision") or 0) > int(chat.get("title_revision") or 0):
            chat.update({field: committed[field] for field in TITLE_FIELDS})
        if int(committed.get("native_title_revision") or 0) > int(chat.get("native_title_revision") or 0):
            chat.update({field: committed[field] for field in _NATIVE_TITLE_FIELDS})

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
        if ((chat.get("codex_turn_active") and chat_id in getattr(self, "_codex_clients", {}))
                or chat.get("kimi_turn_active")):
            return 409, {}
        # A managed turn must reach its canonical row before history can compare it.
        if chat_id in self._chat_turn_dirty_from:
            return 409, {}
        turns = chat.get("turns") or []
        latest = turns[-1] if turns else {}
        if latest.get("request_status") == "pending" and not latest.get("terminal_notification_pair"):
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
            if getattr(self, "_closing", False):
                return
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
                    observed_between = None
                    if not session.updated_at or session.updated_at > replayed_at:
                        started_at = time.time()
                        read_session(session)
                        observed_between = (started_at, time.time())
                    status, _ = self._run_remote_ui_route(
                        lambda: self._native_history_authority_ui(session, owner, reserve=True, expected=authority))
                    if status != 200:
                        continue
                    changed = None
                    turns = None
                    try:
                        changed = self.chat_store.merge_native_history(
                            session, expected_chat_id=owner, expected_revision=revision,
                            merge_title=owner not in self._native_title_pending,
                            observed_between=observed_between)
                        if changed is not None and session.turns:
                            turns = self.chat_store.load_turns(owner)
                    finally:
                        self._call_after_if_alive(self._publish_native_history_ui, owner, changed, turns, session)
            except (AgyError, KimiServerError, sqlite3.Error, OSError) as exc:
                _LOG.info("Native session discovery deferred (%s)", type(exc).__name__)

    def _publish_native_history_ui(self, owner, changed, turns, session) -> tuple[int, dict]:
        self._native_history_reserved.discard(owner)
        if changed is None or getattr(self, "_closing", False):
            return 200, {}
        active = owner == self.active_chat_id
        target = self._current_chat_state if active else self._chat_summary_by_id(owner)
        prior_count = int((target or {}).get("turn_count") or len((target or {}).get("turns") or []))
        recovered = changed.pop("_recovered_terminal_turns", [])
        title_only = bool(target) and not recovered and changed["turn_count"] == prior_count and all(
            target.get(field) == changed.get(field) for field in NATIVE_CHAT_FIELDS
            if field not in (*_NATIVE_TITLE_FIELDS, "native_updated_at", "native_replayed_at", "native_notification_baseline")
        )
        prior_titles = ((target or {}).get("title_revision"), (target or {}).get("native_title_revision"))
        if session.identity.provider == "agy" and changed["turn_count"] > prior_count:
            client = self._agy_clients.pop(owner, None)
            if client is not None:
                threading.Thread(target=client.close, name="agy-idle-history-close", daemon=True).start()
        if target is None:
            self.archived_chats.append(dict(changed))
        else:
            self._merge_published_title(target, changed)
            for field in session.chat_fields:
                target.setdefault(field, changed[field])
            target.update({field: changed[field] for field in NATIVE_CHAT_FIELDS
                           if field in changed and field not in _NATIVE_TITLE_FIELDS})
            target["updated_at"] = max(target.get("updated_at", 0), changed["updated_at"])
            target["turn_count"] = changed["turn_count"]
            cached = target.get("turns")
            if turns is not None and isinstance(cached, list):
                for index in recovered:
                    if index < len(cached):
                        cached[index] = turns[index]
                appended = len(turns) > len(cached)
                cached.extend(turns[len(cached):])
            else:
                appended = False
            if recovered or appended:
                target.pop("execution_steps", None)
                if recovered and session.identity.provider == "codex" and not changed.get("codex_turn_active"):
                    target["codex_turn_active"] = False
                    if active:
                        self.active_codex_turn_active = False
                if active:
                    self.active_turn_idx = len(self.active_session_turns) - 1
                if (active and self.view_mode == "active") or self.view_history_id == owner:
                    if not self._navigation_quiet_active():
                        self._render_answer_list(preserve_panel=True)
        self._sort_archived_chats()
        self._upsert_history_row(owner, allow_reorder=target is None or bool(recovered)
                                 or changed["turn_count"] > prior_count)
        if title_only:
            if prior_titles != (target.get("title_revision"), target.get("native_title_revision")):
                self._push_remote_title_changed(target)
        else:
            self._push_remote_history_changed(owner)
        return 200, {}
