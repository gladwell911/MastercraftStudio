"""Chat information and provider context usage."""

import logging
import math
import wx
from agy_acp import AGY_MODEL_ID
from agy_information import agy_quota_rows, agy_session_rows, agy_usage_owner, read_agy_quota
from chat_information import ChatInformationSubscriptions, format_quota_reset_time, quota_reset_passed
from claudecode_client import is_claudecode_model
from codex_client import codex_model_label_for_model, is_codex_model
from context_usage import (
    context_usage_from_dict, format_cache_hit, format_context_usage_label,
    format_session_total_tokens, native_token_count,
)
from kimi_server_client import KimiServerError, is_kimi_model, kimi_snapshot_total_tokens
from kimi_server_v2 import KimiUsageQueryError
from mc_frame_dialogs import ChatInformationDialog
from mc_frame_settings import DEFAULT_MODEL_ID, REQUESTING_TEXT
from openclaw_client import is_openclaw_model

import mc_frame_bindings as bindings


_USAGE_LOG = logging.getLogger(__name__)


class ChatInformationMixin:
    def _visible_chat_information_owner(self) -> tuple[dict, str] | None:
        chat = self._current_chat_state if self.view_mode != "history" else self._find_archived_chat(self.view_history_id)
        if not isinstance(chat, dict):
            return None
        turn_owner = chat if self.view_mode == "history" else {**chat, "turns": self.active_session_turns}
        model = self._model_for_chat_selection(turn_owner) or bindings.app.normalize_model_id(self.selected_model, default="")
        if not (is_codex_model(model) or is_kimi_model(model) or model == AGY_MODEL_ID):
            return None
        return chat, model

    def _chat_information_owner_for_id(self, chat_id: str) -> tuple[dict, str] | None:
        chat = self._current_chat_state
        if not isinstance(chat, dict) or str(chat.get("id") or "") != chat_id:
            chat = self._find_archived_chat(chat_id)
        if not isinstance(chat, dict):
            return None
        model = self._model_for_chat_selection(chat)
        return (chat, model) if is_codex_model(model) or is_kimi_model(model) else None

    def _information_subscriptions(self) -> ChatInformationSubscriptions:
        subscriptions = getattr(self, "_scoped_chat_information", None)
        if subscriptions is None:
            subscriptions = ChatInformationSubscriptions(self)
            self._scoped_chat_information = subscriptions
        return subscriptions

    def _remote_api_chat_information_ui(self, payload: dict) -> tuple[int, dict]:
        return self._information_subscriptions().command(payload)

    def _chat_information_identity(self, chat: dict, model: str) -> tuple[str, str, str, str]:
        if model == AGY_MODEL_ID:
            owner = "|".join(str(value or "") for value in agy_usage_owner(chat)[1:])
            return (str(chat.get("id") or ""), model, self._agy_session_id_for_chat(chat),
                    owner)
        native_id = self._codex_thread_id_for_chat(chat) if is_codex_model(model) else self._kimi_session_id_for_chat(chat)
        account_id = str(chat.get("codex_account_id") or "").strip() if is_codex_model(model) else ""
        return (str(chat.get("id") or ""), model, native_id, account_id)

    def _on_chat_information_menu_open(self, event) -> None:
        self._chat_information_previous_focus = wx.Window.FindFocus()
        item = self.GetMenuBar().FindItemById(int(self._chat_information_menu_id))
        if item is not None:
            item.Enable(self._visible_chat_information_owner() is not None)
        with self._kimi_owner_lock:
            self._kimi_retry_interaction_menu_item.Enable(
                self._visible_kimi_interaction("retryable", "uncertain") is not None
            )
        event.Skip()

    def _chat_information_rows(self, chat: dict, model: str) -> list[str]:
        if model == AGY_MODEL_ID:
            turns = self.active_session_turns if chat is self._current_chat_state else chat.get("turns") or []
            dialog = self._chat_information_dialog
            scope = (self._chat_information_identity(chat, model), self._read_pair_id())
            result = (getattr(dialog, "agy_quota_result", None)
                      if dialog is not None and getattr(dialog, "agy_quota_scope", None) == scope else None)
            return agy_session_rows(chat, turns) + agy_quota_rows(result)
        self._validate_information_cache_owner(chat, "codex" if is_codex_model(model) else "kimi")
        usage = context_usage_from_dict(chat.get("context_usage"))
        turns = chat.get("turns") if isinstance(chat.get("turns"), list) else []
        if turns:
            usage = self._pending_context_usage_for_chat(chat, len(turns) - 1) or usage
        if (is_codex_model(model) and usage is not None and usage.source == "codex") or (is_kimi_model(model) and usage is not None and usage.source == "kimi"):
            if usage.context_window > 0:
                percent = usage.used_tokens / usage.context_window * 100
                context_row = f"当前上下文：已用 {percent:.1f}%（{usage.used_tokens:,} / {usage.context_window:,} token）"
            else:
                context_row = f"当前上下文：{usage.used_tokens:,} token，窗口未知"
        else:
            context_row = "当前上下文：查询失败" if is_kimi_model(model) and chat.get("kimi_context_error") else "当前上下文：暂不可用"
        provider = "codex" if is_codex_model(model) else "kimi"
        total = native_token_count(chat.get(f"{provider}_session_total_tokens"))
        total_row = f"会话累计 token：{total:,}" if isinstance(total, int) and total >= 0 else (
            "会话累计 token：查询失败" if is_kimi_model(model) and chat.get("kimi_snapshot_error") else "会话累计 token：暂不可用")
        rows = [context_row, total_row]
        for index, stamp in ((0, usage.updated_at if usage else None),
                             (1, chat.get(f"{provider}_total_observed_at"))):
            error = chat.get(f"{provider}_context_error" if index == 0 else f"{provider}_snapshot_error")
            if stamp and (index == 0 and usage is not None or index == 1 and isinstance(total, int)):
                when = bindings.app.datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M:%S")
                rows[index] += f"（缓存，上次观测于 {when}{'，查询失败' if error else ''}）"
            elif error:
                rows[index] = ("当前上下文" if index == 0 else "会话累计 token") + "：查询失败"
        if isinstance(total, int) and f"{provider}_total_observed_at" in chat and not chat.get(f"{provider}_total_observed_at"):
            rows[1] += "（缓存，观测时间未知）"
        hits = chat.get(f"{provider}_cache_hits")
        hits = hits if isinstance(hits, dict) else {}
        rows.extend([
            f"缓存命中：{format_cache_hit(hits.get('last'))}",
            f"累计缓存命中：{format_cache_hit(hits.get('total'))}",
        ])
        if is_codex_model(model):
            owner_id = str(chat.get("codex_account_id") or "").strip()
            if str(chat.get("codex_chat_information_account_owner") or "").strip() == owner_id:
                rows.append(str(chat.get("codex_account_label") or "Codex 账号：未查询"))
                status = chat.get("codex_weekly_quota_status")
                quota = chat.get("codex_weekly_quota")
                if status is not None:
                    rows.append(f"Codex 周额度：{status}")
                elif quota is not None:
                    rows.append(self._codex_weekly_quota_label(quota))
                else:
                    rows.append("Codex 周额度：暂不可用")
            else:
                rows.extend(["Codex 账号：未查询", "Codex 周额度：暂不可用"])
        else:
            owner_matches = chat.get("kimi_quota_owner") == chat.get("kimi_verified_account_id")
            quota = chat.get("kimi_quota_payload") if owner_matches else None
            labels = self._kimi_quota_labels(quota, failed=bool(chat.get("kimi_quota_error"))) if owner_matches and (quota is not None or chat.get("kimi_quota_error")) else None
            if labels is not None and chat.get("kimi_quota_updated_at"):
                stamp = bindings.app.datetime.fromtimestamp(chat["kimi_quota_updated_at"]).strftime("%Y-%m-%d %H:%M")
                labels = [f"{label}（上次更新于 {stamp}）" for label in labels]
            rows.extend(labels if labels is not None else ["Kimi 五小时额度：暂不可用", "Kimi 七日额度：暂不可用"])
        return rows

    def _information_cache_owner(self, chat: dict, provider: str) -> tuple:
        native = self._codex_thread_id_for_chat(chat) if provider == "codex" else self._kimi_session_id_for_chat(chat)
        account = str(chat.get(f"{provider}_account_id") or "")
        if provider == "kimi":
            account += ":" + str(chat.get("kimi_verified_account_id") or "")
        generation = chat.get("codex_context_generation" if provider == "codex" else "kimi_information_generation") or 0
        return (str(chat.get("id") or ""), native, account, int(generation))

    def _validate_information_cache_owner(self, chat: dict, provider: str) -> None:
        owner = chat.get(f"{provider}_usage_cache_owner")
        if owner is None or tuple(owner) == self._information_cache_owner(chat, provider):
            return
        chat.pop(f"{provider}_usage_cache_owner", None)
        if provider == "codex":
            self._clear_codex_context_usage_for_thread_change(chat)
        else:
            self._clear_kimi_information_for_session_change(chat)

    @staticmethod
    def _kimi_quota_labels(payload: dict | None, *, failed: bool = False) -> list[str]:
        names = ("Kimi 五小时额度", "Kimi 七日额度")
        if isinstance(payload, dict) and payload.get("kind") in {"not_applicable", "unauthenticated"}:
            state = "此登录方式不适用" if payload["kind"] == "not_applicable" else "未登录"
            return [f"{name}：{state}" for name in names]
        if isinstance(payload, dict) and payload.get("kind") == "error":
            error = payload.get("error")
            code = str(error.get("code") if isinstance(error, dict) else "").lower()
            if code == "unauthorized":
                return [f"{name}：未登录" for name in names]
        if failed or not isinstance(payload, dict) or payload.get("kind") == "error":
            return [f"{name}：查询失败" for name in names]
        if payload.get("kind") != "ok":
            return [f"{name}：服务端未提供" for name in names]
        quota = payload.get("quota")
        usages = quota.get("usages") if isinstance(quota, dict) else None
        if not isinstance(usages, dict):
            usages = {}
        result = []
        for name, key in zip(names, ("limit5h", "limit7d")):
            window = usages.get(key)
            if not isinstance(window, dict):
                result.append(f"{name}：服务端未提供")
                continue
            raw_reset = window.get("resetAt")
            if isinstance(raw_reset, str) and quota_reset_passed(raw_reset):
                result.append(f"{name}：已过期")
                continue
            ratio = window.get("usedRatio")
            if isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or not math.isfinite(ratio) or not 0 <= ratio <= 1:
                result.append(f"{name}：使用比例暂不可用")
                continue
            label = f"{name}：已用 {ratio * 100:.1f}%"
            reset = format_quota_reset_time(raw_reset) if isinstance(raw_reset, str) else None
            label += f"，本地重置 {reset}" if reset is not None else "，重置时间暂不可用"
            result.append(label)
        return result

    def _refresh_chat_information(self, chat: dict | None = None) -> None:
        if isinstance(chat, dict):
            self._publish_remote_session_usage(chat)
        scoped = getattr(self, "_scoped_chat_information", None)
        if scoped is not None:
            scoped.changed(chat)
        owner = self._visible_chat_information_owner()
        if owner is None:
            return
        visible_chat, model = owner
        if chat is not None and chat is not visible_chat:
            return
        if hasattr(self, "answer_meta"):
            self._refresh_context_usage_header_rows()
        dialog = getattr(self, "_chat_information_dialog", None)
        if dialog is None or dialog.IsBeingDeleted():
            return
        if self._chat_information_identity(visible_chat, model) != dialog.identity:
            return
        dialog.set_rows(self._chat_information_rows(visible_chat, model))

    def _show_chat_information(self) -> bool:
        owner = self._visible_chat_information_owner()
        if owner is None:
            return False
        chat, model = owner
        dialog = self._chat_information_dialog
        identity = self._chat_information_identity(chat, model)
        if dialog is not None and not dialog.IsBeingDeleted() and dialog.identity != identity:
            dialog.focus_target = None
            dialog.Close()
            dialog = None
        if dialog is None or dialog.IsBeingDeleted():
            focus_target = self._chat_information_previous_focus or wx.Window.FindFocus()
            self._chat_information_previous_focus = None
            dialog = ChatInformationDialog(self, identity, focus_target)
            self._chat_information_dialog = dialog
            if is_kimi_model(model):
                if chat.get("kimi_quota_payload") is not None and not chat.get("kimi_quota_updated_at"):
                    chat.pop("kimi_quota_payload", None)
                    chat.pop("kimi_quota_error", None)
        dialog.set_rows(self._chat_information_rows(chat, model))
        dialog.Show()
        dialog.Raise()
        dialog.information_list.SetFocus()
        if is_codex_model(model):
            self._request_codex_chat_information(chat, model)
        elif is_kimi_model(model):
            self._request_kimi_chat_information(chat, model, visible_only=True)
            self._request_kimi_quota(chat, model)
        elif model == AGY_MODEL_ID:
            self._request_agy_quota(chat)
        return True

    def _refresh_agy_chat_information(self, chat: dict) -> None:
        dialog = self._chat_information_dialog
        if dialog is None or dialog.IsBeingDeleted():
            return
        owner = self._visible_chat_information_owner()
        if (owner is not None and owner[0] is chat
                and dialog.identity == self._chat_information_identity(*owner)):
            dialog.set_rows(self._chat_information_rows(chat, AGY_MODEL_ID))

    def _request_agy_quota(self, chat: dict) -> None:
        dialog = self._chat_information_dialog
        identity = self._chat_information_identity(chat, AGY_MODEL_ID)
        pair_id = self._read_pair_id()
        scope = (identity, pair_id)
        if dialog is None or dialog.IsBeingDeleted() or dialog.identity != identity:
            return
        same_scope = getattr(dialog, "agy_quota_scope", None) == scope
        if same_scope and getattr(dialog, "agy_quota_request", None) is not None:
            return
        request = dialog.agy_quota_request = object()
        dialog.agy_quota_scope = scope
        if not same_scope:
            dialog.agy_quota_result = None
        self._refresh_agy_chat_information(chat)

        def apply(result):
            if (self._chat_information_dialog is not dialog or dialog.IsBeingDeleted()
                    or dialog.agy_quota_request is not request):
                return
            dialog.agy_quota_request = None
            owner = self._visible_chat_information_owner()
            if (owner is None or self._chat_information_identity(*owner) != identity
                    or dialog.identity != identity or self._read_pair_id() != pair_id):
                return
            dialog.agy_quota_result = result
            self._refresh_agy_chat_information(owner[0])

        def read():
            self._call_after_if_alive(apply, read_agy_quota())

        bindings.app.threading.Thread(target=read, daemon=True, name="agy-chat-quota").start()

    def _request_codex_chat_information(self, chat: dict, model: str, *, context_only: bool = False) -> None:
        scoped = getattr(self, "_scoped_chat_information", None)
        if scoped is not None and scoped.request(chat, model, context_only=context_only):
            return
        self._validate_information_cache_owner(chat, "codex")
        dialog = getattr(self, "_chat_information_dialog", None)
        if dialog is None or dialog.IsBeingDeleted():
            return
        identity = self._chat_information_identity(chat, model)
        if dialog.identity != identity or not identity[0]:
            return
        if (self._chat_information_request is not None
                and self._chat_information_request[2] == identity
                and bindings.app.time.monotonic() - getattr(self, "_codex_information_started", 0) < 30):
            if not context_only and getattr(self, "_codex_information_context_only", False):
                self._codex_information_full_refresh = (identity, dialog)
            return
        self._codex_information_full_refresh = None
        self._codex_information_started = bindings.app.time.monotonic()
        self._codex_information_context_only = context_only
        self._codex_information_revision = int(chat.get("codex_usage_revision") or 0)
        self._codex_information_context_generation = int(chat.get("codex_context_generation") or 0)
        self._chat_information_request_generation += 1
        generation = self._chat_information_request_generation
        try:
            client = self._get_or_create_codex_client(identity[0], model)
        except Exception:
            self._chat_information_request = (None, generation, identity)
            self._fail_codex_chat_information_request(generation, identity)
            return
        self._chat_information_request = (None, generation, identity)
        native_scope = self._codex_native_scope(chat)

        def _read() -> None:
            try:
                client.start()
                client.read_chat_information(chat_id=identity[0], model=model,
                                             identity=list(identity), generation=generation,
                                             context_only=context_only, **native_scope)
            except Exception:
                self._call_after_if_alive(self._fail_codex_chat_information_request, generation, identity)

        bindings.app.threading.Thread(target=_read, daemon=True, name="codex-chat-information").start()

    def _fail_codex_chat_information_request(self, generation: int, identity: tuple[str, str, str, str]) -> None:
        subscriptions = getattr(self, "_scoped_chat_information", None)
        if subscriptions is not None:
            pending = subscriptions.requests.get(identity[0])
            if pending and pending["generation"] == generation:
                subscriptions.apply_codex(identity[0], {}, {"generation": generation, "identity": list(identity),
                    "context_only": pending["context_only"], "usage_error": True,
                    "account_error": True, "rate_limits_error": True})
                return
        request = self._chat_information_request
        dialog = getattr(self, "_chat_information_dialog", None)
        owner = self._visible_chat_information_owner()
        if (request is None or request[1:] != (generation, identity)
                or dialog is None or dialog.IsBeingDeleted() or owner is None
                or dialog.identity != identity or self._chat_information_identity(*owner) != identity):
            return
        chat = owner[0]
        chat["codex_context_error"] = True
        chat["codex_snapshot_error"] = True
        if not getattr(self, "_codex_information_context_only", False):
            chat["codex_account_label"] = "Codex 账号：查询失败"
            chat["codex_weekly_quota_status"] = "查询失败"
            chat.pop("codex_weekly_quota", None)
            chat["codex_chat_information_account_owner"] = identity[3]
            self._mark_chat_metadata_dirty(chat)
            self._defer_codex_state_save()
        self._chat_information_request = None
        self._refresh_chat_information(chat)
        self._resume_codex_information_full_refresh(chat, identity)

    def _resume_codex_information_full_refresh(self, chat: dict, identity: tuple) -> None:
        pending = getattr(self, "_codex_information_full_refresh", None)
        self._codex_information_full_refresh = None
        dialog = getattr(self, "_chat_information_dialog", None)
        if (pending is not None and pending == (identity, dialog)
                and dialog is not None and not dialog.IsBeingDeleted()
                and dialog.identity == identity and self._chat_information_identity(chat, identity[1]) == identity):
            self._request_codex_chat_information(chat, identity[1])

    def _apply_codex_chat_information(self, chat_id: str, message: dict, payload: dict, *, scoped: dict | None = None) -> None:
        request = self._chat_information_request if scoped is None else (None, scoped["generation"], scoped["identity"])
        dialog = getattr(self, "_chat_information_dialog", None)
        owner = self._visible_chat_information_owner() if scoped is None else self._chat_information_owner_for_id(chat_id)
        if request is None or owner is None or (scoped is None and (dialog is None or dialog.IsBeingDeleted())):
            return
        request_id, generation, identity = request
        chat, model = owner
        if ((request_id is not None and str(message.get("id") or "") != request_id) or chat_id != identity[0]
                or self._chat_information_identity(chat, model) != identity or (scoped is None and dialog.identity != identity)
                or list(payload.get("identity") or []) != list(identity)
                or payload.get("generation") != generation):
            return
        if scoped is None:
            self._chat_information_request = None
        native = payload.get("native_usage") or {}
        revision = scoped["revision"] if scoped else getattr(self, "_codex_information_revision", 0)
        context_generation = scoped["owner"][3] if scoped else getattr(self, "_codex_information_context_generation", 0)
        if (int(chat.get("codex_usage_revision") or 0) == revision
                and int(chat.get("codex_context_generation") or 0) == context_generation):
            usage = native.get("context_usage")
            try:
                observed_at = bindings.app.datetime.fromisoformat(str(native.get("observed_at")).replace("Z", "+00:00")).timestamp()
            except (TypeError, ValueError, OverflowError):
                observed_at = None
            if isinstance(usage, dict):
                if observed_at is not None:
                    usage["updated_at"] = observed_at
                self._set_chat_context_usage(chat, usage)
            hits = native.get("cache_hits")
            if isinstance(hits, dict):
                chat["codex_cache_hits"] = hits
                chat["codex_usage_cache_owner"] = self._information_cache_owner(chat, "codex")
            total = native.get("session_total_tokens")
            if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
                chat["codex_session_total_tokens"] = total
                chat["codex_total_observed_at"] = observed_at
                chat["codex_usage_cache_owner"] = self._information_cache_owner(chat, "codex")
            chat["codex_context_error"] = bool(payload.get("usage_error"))
            chat["codex_snapshot_error"] = bool(payload.get("usage_error"))
            if isinstance(hits, dict) or isinstance(total, int):
                self._mark_chat_metadata_dirty(chat)
                self._defer_codex_state_save()
        if payload.get("context_only"):
            self._refresh_chat_information(chat)
            if scoped is None:
                self._resume_codex_information_full_refresh(chat, identity)
            return
        account_resp = payload.get("account") if isinstance(payload.get("account"), dict) else {}
        account = account_resp.get("account") if isinstance(account_resp.get("account"), dict) else None
        if payload.get("account_error"):
            account_label = "Codex 账号：查询失败"
        elif account is None:
            account_label = "Codex 账号：未登录"
        else:
            account_label = f"Codex 账号：{self._format_codex_account_status(account_resp)}"
        account_type = str((account or {}).get("type") or "").lower()
        rate_limits = payload.get("rate_limits")
        weekly_quota = None
        if account is None and not payload.get("account_error"):
            quota_status = "未登录"
        elif account_type in {"apikey", "api_key", "api-key"}:
            quota_status = "API key 账号不适用"
        elif payload.get("rate_limits_error") or payload.get("account_error") or not isinstance(rate_limits, dict):
            quota_status = "查询失败"
        else:
            weekly_quota = self._codex_weekly_quota_window(rate_limits)
            quota_status = None if weekly_quota is not None else "周窗口暂不可用"
        new_account_id = str((account or {}).get("id") or (account or {}).get("email") or "").strip()
        account_identity_changed = bool(not payload.get("account_error") and new_account_id != identity[3])
        changed = (chat.get("codex_account_label") != account_label
                   or str(chat.get("codex_chat_information_account_owner") or "").strip() != identity[3]
                   or account_identity_changed
                   or chat.get("codex_weekly_quota_status") != quota_status
                   or chat.get("codex_weekly_quota") != weekly_quota)
        chat["codex_weekly_quota_status"] = quota_status
        chat["codex_weekly_quota"] = weekly_quota
        if changed:
            if account_identity_changed:
                if identity[3]:
                    self._clear_codex_context_usage_for_thread_change(chat)
                    chat["codex_thread_id"] = ""
                    chat["codex_turn_id"] = ""
                    chat["codex_turn_active"] = False
                    if chat is self._current_chat_state:
                        self.active_codex_thread_id = ""
                        self.active_codex_turn_id = ""
                        self.active_codex_turn_active = False
                chat["codex_account_id"] = new_account_id
                if not identity[3] and chat.get("codex_usage_cache_owner"):
                    chat["codex_usage_cache_owner"] = self._information_cache_owner(chat, "codex")
                if dialog is not None and not dialog.IsBeingDeleted() and dialog.identity[0] == chat_id:
                    dialog.identity = self._chat_information_identity(chat, model)
            chat["codex_account_label"] = account_label
            chat["codex_chat_information_account_owner"] = str(chat.get("codex_account_id") or "").strip()
            self._mark_chat_metadata_dirty(chat)
            self._defer_codex_state_save()
        self._refresh_chat_information(chat)

    def _request_kimi_chat_information(self, chat: dict, model: str, *, visible_only: bool = False,
                                       context_only: bool = False, scoped: bool = False,
                                       usage_only: bool = False) -> None:
        subscriptions = getattr(self, "_scoped_chat_information", None)
        if not (scoped or usage_only) and subscriptions is not None and subscriptions.request(chat, model, context_only=context_only):
            return
        self._validate_information_cache_owner(chat, "kimi")
        identity = self._chat_information_identity(chat, model)
        if not identity[0] or not identity[2]:
            return
        dialog = getattr(self, "_chat_information_dialog", None)
        if visible_only and (dialog is None or dialog.IsBeingDeleted() or dialog.identity != identity):
            return
        pending = self._kimi_information_requests.get(identity[0])
        if (isinstance(pending, dict) and pending.get("identity") == identity
                and bindings.app.time.monotonic() - pending.get("started", 0) < 30):
            if usage_only:
                pending["full_refresh_usage"] = True
            if not context_only and pending.get("context_only"):
                pending["full_refresh_dialog"] = dialog
            return
        kinds = {"status"} if context_only else {"status", "snapshot"}
        self._kimi_information_generation += 1
        generation = self._kimi_information_generation
        self._kimi_information_requests[identity[0]] = {
            "generation": generation, "identity": identity,
            "context_revision": int(chat.get("kimi_context_revision") or 0),
            "visible_only": visible_only, "remaining": kinds.copy(),
            "context_only": context_only,
            "started": bindings.app.time.monotonic(), "dialog": dialog if visible_only else None,
            "cache_owner": self._information_cache_owner(chat, "kimi"),
            "scoped": scoped,
            "usage_only": usage_only,
        }

        def _read() -> None:
            try:
                client = self._ensure_kimi_client()
                client.start()
            except Exception as exc:
                _USAGE_LOG.warning("Kimi usage query failed stage=start category=%s", type(exc).__name__)
                for kind in kinds:
                    self._call_after_if_alive(self._apply_kimi_chat_information_result,
                                              identity[0], generation, identity, kind, None, True)
                return
            if getattr(client, "protocol_version", 1) == 2:
                getters = (("status", "get_v2_usage_context"), ("snapshot", "get_v2_usage_totals"))
            else:
                getters = (("status", "get_status"), ("snapshot", "get_snapshot"))
            for kind, method in getters:
                if kind not in kinds:
                    continue
                try:
                    payload = getattr(client, method)(identity[2])
                    failed = False
                except KimiUsageQueryError as exc:
                    _USAGE_LOG.warning("Kimi usage query failed query=%s stage=%s pages=%d category=%s",
                                      kind, exc.stage, exc.pages, exc.category)
                    payload, failed = None, True
                except Exception as exc:
                    _USAGE_LOG.warning("Kimi usage query failed stage=%s category=%s", kind, type(exc).__name__)
                    payload, failed = None, True
                self._call_after_if_alive(self._apply_kimi_chat_information_result,
                                          identity[0], generation, identity, kind, payload, failed)

        bindings.app.threading.Thread(target=_read, daemon=True).start()

    def _request_kimi_quota(self, chat: dict, model: str, *, scoped: bool = False) -> None:
        subscriptions = getattr(self, "_scoped_chat_information", None)
        if not scoped and subscriptions is not None and subscriptions.interested(str(chat.get("id") or "")):
            subscriptions.request(chat, model)
            return
        dialog = getattr(self, "_chat_information_dialog", None)
        identity = self._chat_information_identity(chat, model)
        if not identity[0] or (not scoped and (dialog is None or dialog.IsBeingDeleted() or dialog.identity != identity)):
            return
        legacy = getattr(self, "_kimi_quota_request", None)
        if scoped and legacy is not None and legacy[1] == identity:
            requests = getattr(self, "_scoped_kimi_quota_requests", None)
            if requests is None:
                requests = self._scoped_kimi_quota_requests = {}
            requests[identity[0]] = (legacy[0], identity, self._information_cache_owner(chat, "kimi"))
            self._kimi_quota_request = None
            return
        self._kimi_quota_generation = getattr(self, "_kimi_quota_generation", 0) + 1
        generation = self._kimi_quota_generation
        if scoped:
            requests = getattr(self, "_scoped_kimi_quota_requests", None)
            if requests is None:
                requests = self._scoped_kimi_quota_requests = {}
            requests[identity[0]] = (generation, identity, self._information_cache_owner(chat, "kimi"))
        else:
            self._kimi_quota_request = (generation, identity)

        def _read() -> None:
            try:
                client = self._ensure_kimi_client()
                client.start()
                auth = client.get_auth()
                provider = auth.get("managed_provider") if isinstance(auth, dict) else None
                if not isinstance(provider, dict):
                    payload = {"kind": "not_applicable"}
                elif str(provider.get("status") or "").lower() in {"unauthenticated", "expired", "revoked"}:
                    payload = {"kind": "unauthenticated"}
                elif str(provider.get("status") or "").lower() != "authenticated":
                    payload = {"kind": "error"}
                else:
                    user = client.get_oauth_userinfo()
                    info = user.get("userInfo") if isinstance(user, dict) and user.get("kind") == "ok" else None
                    user_id = str(info.get("userId") or "").strip() if isinstance(info, dict) else ""
                    if not user_id:
                        payload = {"kind": "error"}
                    else:
                        usage = client.get_oauth_usage()
                        confirm = client.get_oauth_userinfo()
                        confirmed_info = confirm.get("userInfo") if isinstance(confirm, dict) and confirm.get("kind") == "ok" else None
                        confirmed_id = str(confirmed_info.get("userId") or "").strip() if isinstance(confirmed_info, dict) else ""
                        confirmed_auth = client.get_auth()
                        confirmed_provider = confirmed_auth.get("managed_provider") if isinstance(confirmed_auth, dict) else None
                        auth_ok = isinstance(confirmed_provider, dict) and str(confirmed_provider.get("status") or "").lower() == "authenticated"
                        payload = dict(usage, _account_id=user_id) if user_id == confirmed_id and auth_ok else {"kind": "error"}
                failed = False
            except KimiServerError as exc:
                payload = {"kind": "error", "error": {"code": "unauthorized"}} if exc.status_code == 401 else None
                failed = payload is None
            except Exception:
                payload, failed = None, True
            self._call_after_if_alive(self._apply_kimi_quota, generation, identity, payload, failed, scoped)

        bindings.app.threading.Thread(target=_read, daemon=True, name="kimi-chat-quota").start()

    def _apply_kimi_quota(self, generation: int, identity: tuple[str, str, str, str],
                          payload: dict | None, failed: bool, scoped: bool = False) -> None:
        shared = getattr(self, "_scoped_kimi_quota_requests", {}).get(identity[0])
        if not scoped and shared is not None and shared[:2] == (generation, identity):
            scoped = True
        dialog = getattr(self, "_chat_information_dialog", None)
        owner = self._chat_information_owner_for_id(identity[0]) if scoped else self._visible_chat_information_owner()
        request = getattr(self, "_scoped_kimi_quota_requests", {}).get(identity[0]) if scoped else getattr(self, "_kimi_quota_request", None)
        if (request is None or request[:2] != (generation, identity)
                or (not scoped and (dialog is None or dialog.IsBeingDeleted() or dialog.identity != identity))
                or owner is None or self._chat_information_identity(*owner) != identity):
            return
        if scoped:
            if (not self._information_subscriptions().interested(identity[0])
                    or request[2] != self._information_cache_owner(owner[0], "kimi")):
                return
            self._scoped_kimi_quota_requests.pop(identity[0], None)
        else:
            self._kimi_quota_request = None
        chat = owner[0]
        old_rows = self._chat_information_rows(chat, owner[1])
        account_id = str((payload or {}).get("_account_id") or "")
        previous_account_id = str(chat.get("kimi_verified_account_id") or "")
        kind = (payload or {}).get("kind")
        quota_error = (payload or {}).get("error") or {}
        confirmed_logout = kind == "unauthenticated" or (isinstance(quota_error, dict) and quota_error.get("code") == "unauthorized")
        if not account_id and not confirmed_logout and (failed or kind == "error"):
            account_id = previous_account_id
        if kind == "not_applicable":
            account_id = previous_account_id
        verified_changed = str(chat.get("kimi_verified_account_id") or "") != account_id
        if account_id != str(chat.get("kimi_quota_owner") or ""):
            chat.pop("kimi_quota_payload", None)
            chat.pop("kimi_quota_updated_at", None)
        chat["kimi_verified_account_id"] = account_id
        if not previous_account_id and account_id and chat.get("kimi_usage_cache_owner"):
            chat["kimi_usage_cache_owner"] = self._information_cache_owner(chat, "kimi")
            pending = self._kimi_information_requests.get(identity[0])
            if (isinstance(pending, dict) and pending.get("identity") == identity
                    and not (pending.get("scoped") or pending.get("scoped_observed"))):
                pending["cache_owner"] = self._information_cache_owner(chat, "kimi")
        if (verified_changed or chat.get("kimi_quota_payload") != payload or bool(chat.get("kimi_quota_error")) != failed
                or chat.get("kimi_quota_owner") != account_id):
            chat["kimi_quota_payload"] = payload
            chat["kimi_quota_error"] = failed
            chat["kimi_quota_owner"] = account_id
        if not failed and isinstance(payload, dict) and payload.get("kind") == "ok":
            chat["kimi_quota_updated_at"] = bindings.app.time.time()
        else:
            chat.pop("kimi_quota_updated_at", None)
        if self._chat_information_rows(chat, owner[1]) != old_rows:
            self._refresh_chat_information(chat)
        if scoped:
            subscriptions = self._information_subscriptions()
            pending = subscriptions.requests.get(identity[0])
            if pending is not None:
                pending["quota_done"] = True
                if pending.get("usage_done"):
                    subscriptions.finished(identity[0], pending)

    def _apply_kimi_chat_information_result(
        self, chat_id: str, generation: int, identity: tuple[str, str, str, str],
        kind: str, payload: dict | None, failed: bool,
    ) -> None:
        request = self._kimi_information_requests.get(chat_id)
        if not isinstance(request, dict) or request.get("generation") != generation or request.get("identity") != identity:
            return
        if (isinstance(payload, dict) and "socket_generation" in payload
                and not self._current_kimi_usage_socket(identity[2], payload)):
            failed, payload = True, None
        background_usage = request.get("usage_only") or request.get("full_refresh_usage")
        if (not background_usage and (request.get("scoped") or request.get("scoped_observed"))
                and not self._information_subscriptions().interested(chat_id)):
            self._kimi_information_requests.pop(chat_id, None)
            self._information_subscriptions().requests.pop(chat_id, None)
            return
        chat, _is_current = self._kimi_target_chat(chat_id)
        if not isinstance(chat, dict):
            return
        self._validate_information_cache_owner(chat, "kimi")
        if (self._chat_information_identity(chat, identity[1]) != identity
                or self._kimi_information_requests.get(chat_id) is not request
                or tuple(request.get("cache_owner", self._information_cache_owner(chat, "kimi"))) != self._information_cache_owner(chat, "kimi")):
            if request.get("scoped") or request.get("scoped_observed"):
                if self._kimi_information_requests.get(chat_id) is request:
                    self._kimi_information_requests.pop(chat_id, None)
                subscriptions = self._information_subscriptions()
                merger = subscriptions.requests.get(chat_id)
                if (merger is not None and merger.get("identity") == identity
                        and merger.get("owner") == tuple(request.get("cache_owner") or ())):
                    subscriptions.requests.pop(chat_id, None)
                owner = self._chat_information_owner_for_id(chat_id)
                if owner is not None and subscriptions.interested(chat_id):
                    subscriptions.request(*owner)
            return
        if request.get("visible_only") and not background_usage:
            dialog = getattr(self, "_chat_information_dialog", None)
            if (dialog is None or dialog.IsBeingDeleted() or dialog.identity != identity
                    or request.get("dialog", dialog) is not dialog):
                self._kimi_information_requests.pop(chat_id, None)
                return
        changed = False
        if kind == "status" and int(chat.get("kimi_context_revision") or 0) == request.get("context_revision"):
            usage = None if failed else self._kimi_context_usage_from_values({
                "context_tokens": (payload or {}).get("context_tokens", (payload or {}).get("contextTokens")),
                "max_context_tokens": (payload or {}).get("max_context_tokens", (payload or {}).get("maxContextTokens")),
            })
            if usage is not None:
                if "socket_generation" in (payload or {}):
                    usage["protocol"] = 2
                changed = self._set_chat_context_usage(chat, usage)
            if bool(chat.get("kimi_context_error")) != bool(failed):
                chat["kimi_context_error"] = bool(failed)
                changed = True
        elif kind == "snapshot":
            if not failed and isinstance(payload, dict) and "socket_generation" in payload:
                changed = self._set_kimi_v2_usage_totals(chat, payload)
                total = None
            else:
                total = None if failed else kimi_snapshot_total_tokens(payload)
            if total is not None and chat.get("kimi_session_total_tokens") != total:
                chat["kimi_session_total_tokens"] = total
                changed = True
            if total is not None:
                chat["kimi_total_observed_at"] = bindings.app.time.time()
                chat["kimi_usage_cache_owner"] = self._information_cache_owner(chat, "kimi")
                changed = True
            if bool(chat.get("kimi_snapshot_error")) != bool(failed):
                chat["kimi_snapshot_error"] = bool(failed)
                changed = True
        remaining = request.get("remaining")
        if isinstance(remaining, set):
            remaining.discard(kind)
            if not remaining:
                self._kimi_information_requests.pop(chat_id, None)
                pending_dialog = request.get("full_refresh_dialog")
                dialog = getattr(self, "_chat_information_dialog", None)
                if request.get("full_refresh_usage"):
                    self._request_kimi_chat_information(chat, identity[1], usage_only=True)
                elif (pending_dialog is not None and pending_dialog is dialog
                        and not dialog.IsBeingDeleted() and dialog.identity == identity):
                    self._request_kimi_chat_information(chat, identity[1], visible_only=True)
                subscriptions = getattr(self, "_scoped_chat_information", None)
                if subscriptions is not None:
                    pending = subscriptions.requests.get(chat_id)
                    if (request.get("scoped") or request.get("scoped_observed")) and pending is not None:
                        pending["usage_done"] = True
                        if pending["context_only"] or pending.get("quota_done"):
                            subscriptions.finished(chat_id, pending)
        if changed:
            self._refresh_chat_information(chat)
            self._mark_chat_metadata_dirty(chat)
            self._defer_codex_state_save()

    def _current_kimi_usage_socket(self, session_id: str, payload: dict) -> bool:
        client = self._kimi_client
        return bool(client and client.is_current_v2_usage(
            session_id, payload.get("socket_generation"), payload.get("usage_client"),
        ) and ("scan_generation" not in payload or client.is_current_v2_usage_scan(
            session_id, payload["scan_generation"],
        )))

    def _set_kimi_v2_usage_totals(self, chat: dict, payload: dict) -> bool:
        socket = (payload["usage_client"], payload["socket_generation"])
        stamp = (socket, payload["serial"])
        previous = chat.get("kimi_v2_usage_stamp")
        if previous is not None and previous[0] == socket and previous[1] > stamp[1]:
            return False
        chat["kimi_v2_usage_stamp"] = stamp
        total = native_token_count(payload.get("session_total_tokens"))
        hits = payload.get("cache_hits")
        owner = self._information_cache_owner(chat, "kimi")
        changed = (total != chat.get("kimi_session_total_tokens")
                   or hits != chat.get("kimi_cache_hits") or owner != chat.get("kimi_usage_cache_owner")
                   or bool(chat.get("kimi_snapshot_error")))
        if changed:
            chat["kimi_session_total_tokens"] = total
            chat["kimi_cache_hits"] = hits
            chat["kimi_usage_cache_owner"] = owner
            chat["kimi_total_observed_at"] = bindings.app.time.time()
            chat["kimi_snapshot_error"] = False
        return changed

    def _apply_live_kimi_usage(self, chat_id: str, session_id: str, kind: str, payload: dict, owner: tuple) -> None:
        if not isinstance(payload, dict) or not self._current_kimi_usage_socket(session_id, payload):
            return
        target = self._chat_information_owner_for_id(chat_id)
        if target is None or not is_kimi_model(target[1]):
            return
        chat, model = target
        if (self._chat_information_identity(chat, model)[2] != session_id
                or self._information_cache_owner(chat, "kimi") != owner):
            return
        self._validate_information_cache_owner(chat, "kimi")
        changed = False
        if kind == "status":
            usage = self._kimi_context_usage_from_values(payload)
            if usage is not None:
                usage["protocol"] = 2
            previous = chat.get("context_usage")
            if usage is not None and (not isinstance(previous, dict)
                    or chat.get("kimi_context_error")
                    or any(previous.get(key) != value for key, value in usage.items() if key != "updated_at")):
                chat["kimi_context_revision"] = int(chat.get("kimi_context_revision") or 0) + 1
                changed = self._set_chat_context_usage(chat, usage)
        elif kind == "snapshot":
            changed = self._set_kimi_v2_usage_totals(chat, payload)
        elif kind == "snapshot_invalidated" and not chat.get("kimi_snapshot_error"):
            chat["kimi_snapshot_error"] = True
            changed = True
        if changed:
            self._refresh_chat_information(chat)
            self._mark_chat_metadata_dirty(chat)
            self._defer_codex_state_save()

    @staticmethod
    def _format_codex_account_status(account_resp: dict) -> str:
        account = account_resp.get("account") if isinstance(account_resp, dict) else None
        if not isinstance(account, dict):
            return "未登录或账号不可用"
        account_type = str(account.get("type") or "").strip()
        if account_type == "chatgpt":
            email = str(account.get("email") or "").strip()
            plan = str(account.get("planType") or "").strip()
            suffix = f"，{plan}" if plan else ""
            return f"ChatGPT：{email or '未知账号'}{suffix}"
        if account_type:
            return account_type
        return "未知"

    @staticmethod
    def _format_codex_rate_limit_status(rate_limits_resp: dict) -> str:
        if not isinstance(rate_limits_resp, dict):
            return "未知"
        limits = rate_limits_resp.get("rateLimits")
        if not isinstance(limits, dict):
            return "未知"
        name = str(limits.get("limitName") or limits.get("limitId") or "Codex").strip()
        primary = limits.get("primary") if isinstance(limits.get("primary"), dict) else {}
        percent = primary.get("percentUsed") if isinstance(primary, dict) else None
        if isinstance(percent, (int, float)):
            return f"{name}：{percent:.0f}% 已用"
        return name or "未知"

    @staticmethod
    def _codex_weekly_quota_window(response: dict) -> dict | None:
        limits = response.get("rateLimits")
        by_id = response.get("rateLimitsByLimitId")
        if isinstance(by_id, dict) and isinstance(by_id.get("codex"), dict):
            limits = by_id["codex"]
        if not isinstance(limits, dict):
            return None
        limit_id = str(limits.get("limitId") or limits.get("limitName") or "codex").lower()
        if limit_id != "codex":
            return None
        windows = [limits.get("primary"), limits.get("secondary")]
        weekly = next((item for item in windows if isinstance(item, dict)
                       and item.get("windowDurationMins") == 10080), None)
        if weekly is None:
            return None
        return {"usedPercent": weekly.get("usedPercent"), "resetsAt": weekly.get("resetsAt")}

    @staticmethod
    def _codex_weekly_quota_label(weekly: dict) -> str:
        if quota_reset_passed(weekly.get("resetsAt")):
            return "Codex 周额度：已过期"
        used = weekly.get("usedPercent")
        if isinstance(used, bool) or not isinstance(used, (int, float)) or not 0 <= used <= 100:
            remaining = "剩余比例暂不可用"
        else:
            remaining = f"剩余 {100 - used:.1f}%"
        reset = format_quota_reset_time(weekly.get("resetsAt"))
        reset_text = f"本地重置 {reset}" if reset is not None else "重置时间暂不可用"
        return f"Codex 周额度：{remaining}，{reset_text}"

    @staticmethod
    def _codex_thread_status_from_response(thread_resp: dict) -> tuple[str, list[str]]:
        thread = thread_resp.get("thread") if isinstance(thread_resp, dict) else {}
        status = thread.get("status") if isinstance(thread, dict) and isinstance(thread.get("status"), dict) else {}
        return str(status.get("type") or "").strip(), list(status.get("activeFlags") or [])

    def _active_chat_context_usage(self):
        chat = self._current_chat_state if self.view_mode != "history" else self._find_archived_chat(self.view_history_id)
        usage = (chat or {}).get("context_usage") if isinstance(chat, dict) else None
        normalized = context_usage_from_dict(usage)
        if normalized is not None:
            return normalized
        return None

    def _pending_context_usage_for_chat(self, chat: dict | None, turn_idx: int):
        if not isinstance(chat, dict):
            return None
        pending = self._pending_context_usage_by_turn.get(self._context_usage_pending_key_from_chat(chat, turn_idx))
        turns = chat.get("turns") if isinstance(chat.get("turns"), list) else []
        model = ""
        if 0 <= turn_idx < len(turns) and isinstance(turns[turn_idx], dict):
            model = str(turns[turn_idx].get("model") or "").strip()
        if not self._pending_context_usage_matches_model(pending, model):
            return None
        return context_usage_from_dict(pending) if isinstance(pending, dict) else None

    def _pending_context_usage_matches_model(self, pending, model: str) -> bool:
        if not isinstance(pending, dict):
            return False
        source = str(pending.get("source") or "").strip()
        model_text = str(model or "").strip()
        if is_codex_model(model_text):
            return source == "codex"
        if is_kimi_model(model_text):
            return source == "kimi"
        if is_openclaw_model(model_text):
            return source == "openclaw"
        if is_claudecode_model(model_text):
            return source == "claudecode"
        return source not in {"codex", "kimi", "openclaw", "claudecode"}

    def _is_authoritative_context_usage_model(self, model: str) -> bool:
        text = str(model or "").strip()
        return is_openclaw_model(text) or is_codex_model(text) or is_kimi_model(text) or is_claudecode_model(text)

    def _turns_require_authoritative_context_usage(self, turns: list[dict]) -> bool:
        for turn in reversed(turns or []):
            if not isinstance(turn, dict):
                continue
            model = str(turn.get("model") or "").strip()
            if model:
                return self._is_authoritative_context_usage_model(model)
        return False

    def _history_context_fallback_model(self, chat, turns) -> str:
        if isinstance(chat, dict):
            model = str(chat.get("model") or "").strip()
            if model:
                return model
        for turn in reversed(turns or []):
            if not isinstance(turn, dict):
                continue
            model = str(turn.get("model") or "").strip()
            if model:
                return model
        return str(self.selected_model or "").strip() or DEFAULT_MODEL_ID

    def _active_chat_current_model(self) -> str:
        chat = self._current_chat_state if self.view_mode != "history" else self._find_archived_chat(self.view_history_id)
        turns = self._get_view_turns()
        if turns and isinstance(chat, dict):
            pending = self._pending_context_usage_for_chat(chat, len(turns) - 1)
            if pending is not None and str(pending.model or "").strip():
                return str(pending.model or "").strip()
        usage = (chat or {}).get("context_usage") if isinstance(chat, dict) else None
        normalized = context_usage_from_dict(usage)
        if normalized is not None and str(normalized.model or "").strip():
            return str(normalized.model or "").strip()
        for turn in reversed(turns or []):
            if not isinstance(turn, dict):
                continue
            model = str(turn.get("model") or "").strip()
            if model:
                configured_model = codex_model_label_for_model(model)
                if configured_model:
                    return configured_model
                if is_codex_model(model):
                    configured = bindings.app.read_codex_cli_model_label(self._workspace_dir_for_codex())
                    if configured:
                        return configured
                return model
        if isinstance(chat, dict):
            model = str(chat.get("model") or "").strip()
            if model:
                configured_model = codex_model_label_for_model(model)
                if configured_model:
                    return configured_model
                if is_codex_model(model):
                    configured = bindings.app.read_codex_cli_model_label(self._workspace_dir_for_codex())
                    if configured:
                        return configured
                return model
        selected = str(self.selected_model or "").strip() or DEFAULT_MODEL_ID
        configured_model = codex_model_label_for_model(selected)
        if configured_model:
            return configured_model
        if is_codex_model(selected):
            configured = bindings.app.read_codex_cli_model_label(self._workspace_dir_for_codex())
            if configured:
                return configured
        return selected

    def _append_context_usage_row(self, rows: list[str] | None = None, metas: list[tuple] | None = None) -> None:
        label = self._answer_usage_header_label()
        meta = ("context_usage", -1, label, "")
        if rows is not None and metas is not None:
            rows.append(label)
            metas.append(meta)
            return
        self.answer_list.Append(label)
        self.answer_meta.append(meta)

    def _append_current_model_row(self, rows: list[str] | None = None, metas: list[tuple] | None = None) -> None:
        return

    def _answer_usage_header_label(self) -> str:
        owner = self._visible_chat_information_owner()
        if owner is None or owner[1] == AGY_MODEL_ID:
            return format_context_usage_label(self._active_chat_context_usage())
        return format_session_total_tokens(self._session_total_tokens(*owner))

    def _session_total_tokens(self, chat: dict, model: str) -> int | None:
        provider = "codex" if is_codex_model(model) else "kimi"
        cached_owner = chat.get(f"{provider}_usage_cache_owner")
        if cached_owner is not None and tuple(cached_owner) != self._information_cache_owner(chat, provider):
            return None
        return native_token_count(chat.get(f"{provider}_session_total_tokens"))

    def _should_show_context_usage_row(self, label: str | None = None, turns: list[dict] | None = None) -> bool:
        owner = self._visible_chat_information_owner()
        if owner is not None and owner[1] != AGY_MODEL_ID:
            return True
        usage_label = str(label if label is not None else format_context_usage_label(self._active_chat_context_usage()) or "")
        if usage_label != "暂无":
            return True
        view_turns = turns if isinstance(turns, list) else self._get_view_turns()
        if not view_turns:
            return True
        if self.view_mode == "history":
            return False
        for turn in reversed(view_turns):
            model = str((turn or {}).get("model") or "").strip()
            if is_claudecode_model(model):
                return True
            if is_codex_model(model):
                return str((turn or {}).get("answer_md") or "") == REQUESTING_TEXT or str((turn or {}).get("request_status") or "") == "pending"
            if model:
                return False
        return False

    def _refresh_context_usage_header_rows(self) -> bool:
        if getattr(self, "_chat_navigation_answer_rows_deferred", False):
            return False
        if not hasattr(self, "answer_list"):
            return False
        handled = False
        changed = False
        context_label = self._answer_usage_header_label()
        labels = {
            "context_usage": context_label,
        }
        for row, meta in enumerate(list(self.answer_meta)):
            if row >= self.answer_list.GetCount():
                break
            kind = meta[0] if meta else ""
            if kind not in labels:
                continue
            handled = True
            label = labels[kind]
            current = ""
            try:
                current = self.answer_list.GetString(row)
            except Exception:
                current = ""
            if current == label and len(meta) >= 3 and meta[2] == label:
                continue
            selected_idx = self.answer_list.GetSelection()
            try:
                if hasattr(self, "answer_list_model"):
                    self.answer_list_model.update_label(self._answer_row_id(meta), label)
                else:
                    self.answer_list.SetString(row, label)
                if selected_idx != wx.NOT_FOUND and self.answer_list.GetSelection() != selected_idx:
                    self.answer_list.SetSelection(selected_idx)
            except Exception:
                continue
            self.answer_meta[row] = (kind, -1, label, "")
            changed = True
        if not handled and self._should_show_context_usage_row(context_label):
            meta = ("context_usage", -1, context_label, "")
            selected_idx = self.answer_list.GetSelection()
            if hasattr(self, "answer_list_model"):
                self.answer_list_model.insert(self._answer_row_id(meta), context_label, 0)
            else:
                self.answer_list.Insert(context_label, 0)
            self.answer_meta.insert(0, meta)
            if self._active_answer_row_index >= 0:
                self._active_answer_row_index += 1
            if selected_idx != wx.NOT_FOUND:
                try:
                    self.answer_list.SetSelection(selected_idx + 1)
                except Exception:
                    pass
            handled = True
            changed = True
        if changed:
            self._request_listbox_repaint(self.answer_list)
        return handled

    @staticmethod
    def _context_usage_payload_changed(previous, current) -> bool:
        return context_usage_from_dict(previous) != context_usage_from_dict(current)

    def _set_pending_context_usage_for_turn(self, chat: dict, turn_idx: int, usage) -> bool:
        key = self._context_usage_pending_key_from_chat(chat, turn_idx)
        previous = self._pending_context_usage_by_turn.get(key)
        if not self._context_usage_payload_changed(previous, usage):
            return False
        self._pending_context_usage_by_turn[key] = usage
        self._refresh_chat_information(chat)
        return True

    def _rebind_chat_information_after_thread_change(self, chat: dict) -> None:
        dialog = getattr(self, "_chat_information_dialog", None)
        owner = self._visible_chat_information_owner()
        if dialog is None or dialog.IsBeingDeleted() or owner is None or owner[0] is not chat:
            return
        if dialog.identity[0] != str(chat.get("id") or ""):
            return
        dialog.identity = self._chat_information_identity(chat, owner[1])
        dialog.set_rows(self._chat_information_rows(chat, owner[1]))
        self._refresh_context_usage_header_rows()

    def _set_chat_context_usage(self, chat: dict, usage) -> bool:
        if not isinstance(chat, dict) or usage is None:
            return False
        if hasattr(usage, "to_dict"):
            usage = usage.to_dict()
        if isinstance(usage, dict):
            source = usage.get("source")
            if source in {"codex", "kimi"}:
                chat[f"{source}_usage_cache_owner"] = self._information_cache_owner(chat, source)
            if source in {"codex", "kimi"}:
                chat[f"{source}_context_error"] = False
            previous = chat.get("context_usage")
            previous = previous if isinstance(previous, dict) else {}
            if (not self._context_usage_payload_changed(previous, usage)
                    and previous.get("protocol") == usage.get("protocol")):
                return False
            chat["context_usage"] = usage
            self._mark_chat_metadata_dirty(chat)
            self._invalidate_remote_state_cache()
            self._refresh_chat_information(chat)
            return True
        return False

    def _refresh_context_usage_after_done(self, target_chat: dict, target_turns: list, turn_idx: int, used_model: str) -> None:
        # The older Kimi runtime supplies context through status and cumulative
        # usage through a separate snapshot. This read leaves quota untouched.
        client = getattr(self, "_kimi_client", None)
        if is_kimi_model(used_model) and client is not None and getattr(client, "protocol_version", 1) == 1:
            self._request_kimi_chat_information(target_chat, used_model, usage_only=True)
        pending = self._pending_context_usage_by_turn.pop(self._context_usage_pending_key_from_chat(target_chat, turn_idx), None)
        if pending and self._pending_context_usage_matches_model(pending, used_model):
            self._set_chat_context_usage(target_chat, pending)
            return
        if pending:
            self._pending_context_usage_by_turn[self._context_usage_pending_key_from_chat(target_chat, turn_idx)] = pending
        if self._is_authoritative_context_usage_model(used_model):
            if isinstance(target_chat, dict):
                current = target_chat.get("context_usage")
                current = current if isinstance(current, dict) else {}
                owner = target_chat.get("kimi_usage_cache_owner")
                keep_v2 = (is_kimi_model(used_model) and current.get("source") == "kimi"
                           and current.get("protocol") == 2 and owner is not None
                           and tuple(owner) == self._information_cache_owner(target_chat, "kimi"))
                if not keep_v2:
                    target_chat.pop("context_usage", None)
            return
        self._schedule_context_usage_estimate(target_chat, target_turns, turn_idx, used_model)

    def _schedule_context_usage_estimate(self, target_chat: dict, target_turns: list, turn_idx: int, used_model: str) -> None:
        if not isinstance(target_chat, dict):
            return
        chat_id = str(target_chat.get("id") or self.active_chat_id or self.current_chat_id or "").strip()
        if not chat_id:
            return
        key = (chat_id, int(turn_idx))
        with self._context_usage_estimate_lock:
            if key in self._context_usage_estimate_keys:
                return
            self._context_usage_estimate_keys.add(key)
        turns_snapshot = list(target_turns or [])
        model = str(used_model or "").strip()

        def _worker() -> None:
            try:
                usage = bindings.app.estimate_turns_tokens(turns_snapshot, model=model)
            except Exception:
                usage = None
            self._call_after_if_alive(self._apply_context_usage_estimate, key, usage)

        bindings.app.threading.Thread(target=_worker, daemon=True).start()

    def _apply_context_usage_estimate(self, key: tuple[str, int], usage) -> None:
        with self._context_usage_estimate_lock:
            self._context_usage_estimate_keys.discard(key)
        if usage is None:
            return
        chat_id, turn_idx = key
        chat = self._current_chat_state if chat_id in {self.active_chat_id, self.current_chat_id} else self._find_archived_chat(chat_id)
        if not isinstance(chat, dict):
            return
        turns = chat.get("turns") if isinstance(chat.get("turns"), list) else []
        if turns and int(turn_idx) >= len(turns):
            return
        if self._set_chat_context_usage(chat, usage):
            self._defer_chat_state_save()

    def _context_usage_pending_key(self, chat_id: str, turn_idx: int) -> tuple[str, int]:
        key_chat_id = str(chat_id or self.active_chat_id or self.current_chat_id or "").strip()
        return (key_chat_id, int(turn_idx))

    def _context_usage_pending_key_from_chat(self, chat: dict, turn_idx: int) -> tuple[str, int]:
        chat_id = str((chat or {}).get("id") or "").strip() if isinstance(chat, dict) else ""
        return self._context_usage_pending_key(chat_id, turn_idx)
