"""Background native discovery and application history publication."""

import logging
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
import sqlite3
import threading
import time

import wx

from agy_acp import AgyError, find_agy_executable
from agy_history import list_agy_sessions, read_agy_session, verify_agy_session_absent
from codex_client import is_codex_model
from codex_history import CodexHistoryReader, codex_history_homes
from codex_worker_client import codex_worker_workspace
from kimi_history import KimiHistoryReader, kimi_history_storage
from kimi_server_client import is_kimi_model
from kimi_runtime import runtime_path as kimi_runtime_path
from kimi_server_client import KimiServerClient, KimiServerError
from native_history import (
    NATIVE_CHAT_FIELDS, TITLE_FIELDS, NativeDeletionRejected, NativeSessionIdentity, NativeTitleRejected,
)


_LOG = logging.getLogger(__name__)
_REFRESH_SECONDS = 60
_NATIVE_TITLE_FIELDS = ("native_title", "native_title_revision", "native_title_observed_at")
_RENAME_UNKNOWN = "原生改名结果尚未确认，已保留上次确认的标题，将重新读取。"
_DELETE_UNKNOWN = "尚未确认原始会话已删除。当前聊天已保留，请刷新后重试。"
_AGY_MANUAL_DELETE = ("请在来源电脑的原 AGY 项目目录运行 agy，输入 /resume，选中指定会话后按 F4，"
                      "并按原生提示确认。完成后在此选择刷新确认。")
_PROVIDER_NAMES = {"codex": "Codex", "kimi": "Kimi Code", "agy": "AGY"}


class NativeHistoryMixin:
    def _native_history_reference(self, chat):
        pair = self._read_pair_id()
        provider = session_id = storage = ""
        if is_codex_model(chat.get("model")) and chat.get("codex_thread_id"):
            provider, session_id = "codex", chat["codex_thread_id"]
            home = chat.get("codex_home") or codex_worker_workspace() / ".codex-home"
            storage = chat.get("codex_storage_id") or os.path.normcase(str(Path(home).resolve()))
        elif is_kimi_model(chat.get("model")) and chat.get("kimi_session_id"):
            provider, session_id = "kimi", chat["kimi_session_id"]
            storage = chat.get("kimi_storage_id") or os.path.normcase(str((Path.home() / ".kimi-code").resolve()))
        elif chat.get("agy_session_id"):
            provider, session_id = "agy", chat["agy_session_id"]
        identity = chat.get("native_identity")
        if storage:
            derived = NativeSessionIdentity(pair, provider, storage, session_id).chat_id
            if identity and identity != derived:
                return {}
            identity = derived
        return {"pair_id": pair, "provider": provider, "session_id": session_id,
                "identity": identity} if provider and identity else {}

    def _hide_history_chat_ui(self, owner):
        chat = self._history_chat_for_id(owner)
        if chat is None:
            if self.chat_store.get_meta(f"deleted-chat:{owner}"):
                return 200, {"accepted": True}
            return 404, {"accepted": False, "message": "找不到要隐藏的聊天。"}
        self._flush_chat_state_save()
        native_id = self._history_hide_identity(chat)
        if native_id is None:
            return 409, {"accepted": False, "message": "正在确认原始会话，请稍后隐藏。"}
        try:
            self._delete_chat_from_store(owner, native_id=native_id)
        except (sqlite3.Error, OSError):
            return 503, {"accepted": False, "message": "无法保存聊天隐藏状态，请稍后重试。"}
        self._remove_history_chat_view_ui(chat)
        return 200, {"accepted": True}

    def _history_hide_identity(self, chat):
        owner = str(chat["id"]).strip()
        if self._clear_pending_for(owner):
            return None
        reference = self._native_history_reference(chat)
        if self._native_session_busy(chat, owner):
            return None
        if reference:
            return reference["identity"]
        if any(chat.get(field) for field in ("codex_thread_id", "kimi_session_id", "agy_session_id")):
            return None
        return ""

    def _native_session_busy(self, chat, owner):
        if (owner in getattr(self, "_native_title_pending", {})
                or owner in getattr(self, "_native_history_reserved", ())
                or owner in self._agy_busy
                or chat.get("codex_turn_active") or chat.get("kimi_turn_active")):
            return True
        model = chat.get("model")
        return (bool(chat.get("agy_session_id")) or is_codex_model(model) or is_kimi_model(model)) and any(
            turn.get("request_status") == "pending" for turn in chat.get("turns", ()))

    def _native_delete_target_ui(self, owner):
        chat = self._history_chat_for_id(owner)
        reference = self._native_history_reference(chat or {})
        if not reference:
            return 409, {"accepted": False, "message": "尚未确认原始会话身份，请刷新历史。"}
        provider = reference["provider"]
        source = "笔记本" if reference["pair_id"] == "laptop" else "台式机"
        cwd = chat.get("native_cwd") or chat.get("agy_workspace")
        detail = (f"来源 {source}（{reference['pair_id']}）\n"
                  f"提供方 {_PROVIDER_NAMES[provider]}\n"
                  f"会话 {chat.get('title') or owner}\n"
                  f"原始会话 ID {reference['session_id']}")
        if cwd:
            detail += f"\n工作目录 {cwd}"
        detail += "\n\n永久删除原始会话及历史，无法撤销。"
        if provider == "agy":
            detail += "\n\n" + _AGY_MANUAL_DELETE
        return 200, {"target": reference, "description": detail, "manual": provider == "agy"}

    def _queue_native_delete_ui(self, owner, target, confirmed, *, local=False):
        if getattr(self, "_closing", False):
            return 503, {"accepted": False, "error": "ui_unavailable"}
        chat = self._history_chat_for_id(owner)
        reference = self._native_history_reference(chat or {})
        if confirmed is not True or not reference or target != reference:
            return 409, {"accepted": False, "message": "原始会话身份未确认，请重新打开删除操作。"}
        provider = reference["provider"]
        if (not getattr(self, "_chat_store_enabled", False) or self._clear_pending_for(owner)
                or self._native_session_busy(chat, owner)):
            return 409, {"accepted": False, "message": "原始会话正在使用，请在当前回合结束后重试。"}
        self._flush_chat_state_save()
        stored = self.chat_store.load_chat_summary(owner)
        if not stored or self._native_history_reference(stored) != reference:
            return 409, {"accepted": False, "message": "原始会话已变更，请刷新后重试。"}
        self._native_history_reserved.add(owner)
        self._native_delete_pending.add(owner)
        return 202, {"_native_delete": self._native_rename_executor.submit(
            self._delete_native_session, owner, reference, stored,
            self._kimi_client if provider == "kimi" else None, local,
        )}

    def _delete_native_session(self, owner, reference, stored, kimi_client, local):
        status, message = 503, _DELETE_UNKNOWN
        try:
            if self._delete_or_verify_provider_session(reference, stored, kimi_client):
                self.chat_store.delete_chat(owner, native_id=reference["identity"], native_deleted=True)
                status, message = 200, "已确认删除原始会话。"
        except NativeDeletionRejected as exc:
            status, message = 409, str(exc)
        except (RuntimeError, OSError, ValueError, sqlite3.Error) as exc:
            _LOG.info("Native deletion could not be confirmed (%s)", type(exc).__name__)
        return self._run_remote_ui_route(
            lambda: self._finish_native_delete_ui(owner, reference, status, message, local))

    def _delete_or_verify_provider_session(self, reference, chat, kimi_client):
        pair, provider, sid = (reference[field] for field in ("pair_id", "provider", "session_id"))
        cwd, account = chat.get("native_cwd"), chat.get("native_account")
        if provider == "agy":
            return verify_agy_session_absent(find_agy_executable(), pair, sid, reference["identity"], account, cwd)
        if provider == "codex":
            home = chat.get("codex_home") or codex_worker_workspace() / ".codex-home"
            home = Path(home).resolve()
            reader = CodexHistoryReader(home, not chat.get("codex_user_home"), pair, lambda: [])
            try:
                if NativeSessionIdentity(pair, provider, reader.storage_id, sid).chat_id != reference["identity"]:
                    raise NativeDeletionRejected("Codex 原始存储已变更，请刷新后重试。")
                return reader.delete_session(sid, cwd, account)
            finally:
                reader.client.close()
        client = kimi_client or KimiServerClient()
        try:
            return KimiHistoryReader(client, pair, self._kimi_is_user_prompt).delete_session(
                sid, cwd, chat.get("kimi_storage_id"), account)
        finally:
            if kimi_client is None:
                client.close()

    def _finish_native_delete_ui(self, owner, target, status, message, local):
        try:
            if status == 200:
                chat = self._history_chat_for_id(owner)
                if self._native_history_reference(chat or {}) != target:
                    return 409, {"accepted": False, "message": "聊天已变更，请刷新核对原始会话。"}
                agy_client = self._agy_clients.pop(owner, None)
                if agy_client is not None:
                    threading.Thread(target=agy_client.close, name="agy-delete-close", daemon=True).start()
                self._remove_history_chat_view_ui(chat)
        except (sqlite3.Error, OSError):
            status, message = 503, _DELETE_UNKNOWN
        finally:
            self._native_delete_pending.discard(owner)
            self._native_history_reserved.discard(owner)
        if local:
            self.SetStatusText(message)
        return status, {"accepted": status == 200, "message": message}

    def _remove_history_chat_view_ui(self, chat):
        owner = str(chat["id"]).strip()
        self._cleanup_chat_detail_pages(chat)
        self._discard_deleted_chat_view(owner)
        self._save_state()
        self._refresh_history()
        self._push_remote_history_changed(owner, removed=True)
        self._render_answer_list()

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
        self._native_delete_pending = set()
        self._poll_native_history()

    def _poll_native_history(self) -> None:
        if getattr(self, "_closing", False) or not self._is_ui_alive():
            return
        self._request_native_history_refresh()
        self._native_history_timer = wx.CallLater(_REFRESH_SECONDS * 1000, self._poll_native_history)

    def _stop_native_history_refresh(self) -> None:
        self._native_rename_executor.shutdown(wait=False)
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
        if getattr(self, "_closing", False):
            return False
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
        if not committed and not getattr(self, "_closing", False):
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
        if chat is None or chat.get("id") != owner:
            return
        before = (chat.get("title_revision"), chat.get("native_title_revision"))
        self._merge_published_title(chat, committed)
        if getattr(self, "_closing", False):
            return
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
            chat.update(native_title=committed["native_title"], native_title_revision=committed["native_title_revision"])
        if (committed.get("native_title_observed_at") or 0) > (chat.get("native_title_observed_at") or 0):
            chat["native_title_observed_at"] = committed["native_title_observed_at"]

    def _native_history_authority_ui(self, session, chat_id, *, reserve=False, expected=None):
        if getattr(self, "_closing", False) or chat_id in self._native_history_reserved:
            return 409, {}
        handoff = getattr(self, "_agy_handoff", None)
        if session.identity.provider == "agy" and handoff and handoff.preparing:
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
        if (changed is None or getattr(self, "_closing", False)
                or self.chat_store.get_meta(f"deleted-chat:{owner}")):
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
