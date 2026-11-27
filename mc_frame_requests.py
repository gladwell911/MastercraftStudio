"""Chat submission, provider dispatch, and clear operations."""

import copy
import uuid
import wx
from agy_acp import AGY_MODEL_ID, AgyError
from chat_client import is_openrouter_claude_model, read_openrouter_claude_attachments
from chat_store import ChatStore
from claudecode_client import DEFAULT_CLAUDECODE_MODEL, is_claudecode_model
from codex_client import is_codex_model
from kimi_server_client import is_kimi_model
from mc_frame_presentation import _finite_timestamp
from mc_frame_settings import DEFAULT_MODEL_ID, MODEL_IDS, REQUESTING_TEXT, STARTUP_DEFAULT_MODEL_ID
from openclaw_client import DEFAULT_OPENCLAW_SESSION_KEY, is_openclaw_model
from pathlib import Path

from native_history import CODEX_STORAGE_FIELDS, NATIVE_CHAT_FIELDS

import mc_frame_bindings as bindings


class ChatRequestsMixin:
    def _resolve_current_model(self) -> str:
        # Always honor the model currently shown in combobox for each send.
        if bindings.app.threading.current_thread() is not bindings.app.threading.main_thread():
            cached = str(self.selected_model or "").strip()
            return bindings.app.normalize_model_id(cached)
        if self._current_tool() in {"codex", "agy", "kimi"}:
            return self._tool_route
        current = (self.model_combo.GetStringSelection() or "").strip()
        resolved = bindings.app.normalize_model_id(current, default="")
        if resolved:
            return resolved
        try:
            sel = self.model_combo.GetSelection()
            if sel != wx.NOT_FOUND:
                current = (self.model_combo.GetString(sel) or "").strip()
                resolved = bindings.app.normalize_model_id(current, default="")
                if resolved:
                    return resolved
        except Exception:
            pass
        current = (self.model_combo.GetValue() or "").strip()
        resolved = bindings.app.normalize_model_id(current, default="")
        if resolved:
            return resolved
        if self.selected_model in MODEL_IDS and not bindings.app.is_visible_model_id(self.selected_model):
            return self.selected_model
        resolved = bindings.app.normalize_model_id(self.selected_model, default="")
        if resolved:
            return resolved
        return DEFAULT_MODEL_ID

    def _mark_turn_request_pending(self, turn: dict, model: str, question: str) -> None:
        if "question_order" not in turn:
            turn["question_order"] = self._next_answer_message_order()
        now = bindings.app.time.time()
        turn["request_status"] = "pending"
        turn["request_model"] = str(model or "")
        turn["request_question"] = str(question or "")
        turn["request_started_at"] = now
        turn["request_last_attempt_at"] = now
        turn["request_attempt_count"] = 1
        turn["request_recoverable"] = True
        turn["request_recovery_mode"] = "resume" if (is_codex_model(model) or is_kimi_model(model) or is_claudecode_model(model)) else "retry"
        turn["request_resume_token"] = self._request_resume_token_for_model(model)
        turn["request_error"] = ""
        turn["request_recovered_after_restart"] = False

    def _request_resume_token_for_model(self, model: str) -> dict:
        if is_codex_model(model):
            return {
                "thread_id": str(self.active_codex_thread_id or "").strip(),
                "turn_id": str(self.active_codex_turn_id or "").strip(),
            }
        if is_kimi_model(model):
            return {
                "session_id": str(self.active_kimi_session_id or "").strip(),
                "turn_id": str(self.active_kimi_turn_id or "").strip(),
            }
        if is_claudecode_model(model):
            return {"session_id": str(self.active_claudecode_session_id or "").strip()}
        return {}

    def _mark_turn_request_done(self, turn: dict) -> None:
        if str(turn.get("request_status") or "") != "done" and "answer_order" not in turn:
            turn["answer_order"] = self._next_answer_message_order()
        if str(turn.get("request_status") or "") != "done" and _finite_timestamp(turn.get("answer_at")) is None:
            turn["answer_at"] = bindings.app.time.time()
        turn["request_status"] = "done"
        turn["request_error"] = ""
        turn["request_recovered_after_restart"] = False
        self._schedule_accepted_answer_refresh(str(self.active_chat_id or self.current_chat_id or ""), turn)

    def _mark_turn_request_failed(self, turn: dict, error: str) -> None:
        turn["request_status"] = "failed"
        turn["request_error"] = str(error or "")
        turn["request_recovered_after_restart"] = False

    def _mark_turn_request_cancelled(self, turn: dict, detail: str) -> None:
        turn["request_status"] = "cancelled"
        turn["request_error"] = detail
        turn["request_recovered_after_restart"] = False

    def _capture_model_startup_request(self, chat_id: str, turn_idx: int):
        _, turns, _ = self._chat_target_for_request(chat_id)
        if not isinstance(turns, list) or not 0 <= turn_idx < len(turns):
            return None
        turn = turns[turn_idx]
        if not isinstance(turn, dict):
            return None
        return (turn, turn.get("request_started_at"), turn.get("request_attempt_count"),
                turn.get("codex_start_generation", turn.get("codex_context_generation")))

    def _model_startup_request_is_current(self, chat_id: str, turn_idx: int, request) -> bool:
        if request is None:
            return False
        current = self._capture_model_startup_request(chat_id, turn_idx)
        return bool(current is not None and current[0] is request[0]
                    and current[1:] == request[1:]
                    and str(current[0].get("request_status") or "") in {"pending", "running"})

    def _finish_model_startup_failure(self, chat_id: str, turn_idx: int, request,
                                      message: str, model: str) -> None:
        if not self._model_startup_request_is_current(chat_id, turn_idx, request):
            return
        if is_kimi_model(model):
            self._clear_kimi_active_turn(chat_id, turn_idx)
        self._on_done(turn_idx, "", message, model, "", chat_id)
        if is_codex_model(model):
            self._schedule_next_codex_tool_turn(chat_id)

    def _set_active_claudecode_client(self, client, chat_id: str) -> None:
        with self._active_claudecode_client_lock:
            self._active_claudecode_client = client
            self._active_claudecode_chat_id = str(chat_id or "").strip()

    def _clear_active_claudecode_client(self, expected_client=None) -> None:
        with self._active_claudecode_client_lock:
            if expected_client is not None and self._active_claudecode_client is not expected_client:
                return
            self._active_claudecode_client = None
            self._active_claudecode_chat_id = ""

    def _send_active_claudecode_input(self, chat_id: str, text: str) -> bool:
        target_chat_id = str(chat_id or "").strip()
        with self._active_claudecode_client_lock:
            client = self._active_claudecode_client
            if client is None or self._active_claudecode_chat_id != target_chat_id:
                return False
            client.send_user_input(text)
            return True

    def _start_claudecode_worker_for_turn(
        self,
        chat_id: str,
        turn_idx: int,
        question: str,
        session_id: str,
        model: str = DEFAULT_CLAUDECODE_MODEL,
    ) -> None:
        resolved_model = str(model or DEFAULT_CLAUDECODE_MODEL).strip() or DEFAULT_CLAUDECODE_MODEL
        owns_turn = self._bind_provider_turn(chat_id, turn_idx)

        def deliver(callback, *args):
            self._post_provider_turn(owns_turn, callback, *args)

        def _worker() -> None:
            client = None
            try:
                if not owns_turn():
                    return
                client = bindings.app.ClaudeCodeClient(full_auto=True, cli_manager=self._cli_agent_manager)

                def is_current_target() -> bool:
                    return str(chat_id or "").strip() in {str(self.active_chat_id or "").strip(), str(self.current_chat_id or "").strip(), ""}

                def target_chat_state() -> dict | None:
                    if is_current_target():
                        return self._current_chat_state
                    chat = self._find_archived_chat(chat_id)
                    return chat if isinstance(chat, dict) else None

                def sync_session_id(new_session_id: str) -> None:
                    value = str(new_session_id or "").strip()
                    if not value:
                        return
                    chat = target_chat_state()
                    if isinstance(chat, dict):
                        chat["claudecode_session_id"] = value
                    if is_current_target():
                        self.active_claudecode_session_id = value

                if owns_turn() and is_current_target():
                    # 保存客户端引用，以便在用户发送消息时使用
                    self._set_active_claudecode_client(client, chat_id)

                def on_delta(delta):
                    deliver(self._on_delta, turn_idx, delta, chat_id)

                def on_user_input(params: dict) -> str:
                    """处理用户输入请求"""
                    from claudecode_remote_protocol import format_remote_user_input_request, parse_remote_user_input_reply

                    # 格式化请求消息
                    request_msg = format_remote_user_input_request(params)

                    # 显示请求消息
                    deliver(self._on_delta, turn_idx, f"\n\n【Claude Code 需要你的输入】\n{request_msg}\n\n", chat_id)

                    # 显示交互式界面（如果有回调）
                    if is_current_target() and hasattr(self, '_show_claudecode_user_input'):
                        deliver(self._show_claudecode_user_input, turn_idx, params, client)

                    # 返回空字符串，让 Claude Code 等待 stdin
                    # 用户会通过发送消息来提供输入，该消息会被拦截并写入 stdin
                    return ""

                def on_approval(params: dict) -> str:
                    """处理批准请求"""
                    from claudecode_remote_protocol import format_remote_approval_request, parse_remote_approval_reply

                    # 格式化请求消息
                    request_msg = format_remote_approval_request(params)

                    # 显示请求消息
                    deliver(self._on_delta, turn_idx, f"\n\n【Claude Code 需要批准】\n{request_msg}\n\n", chat_id)

                    # 显示交互式界面（如果有回调）
                    if is_current_target() and hasattr(self, '_show_claudecode_approval'):
                        deliver(self._show_claudecode_approval, turn_idx, params, client)

                    # 返回空字符串，让 Claude Code 等待 stdin
                    # 用户会通过发送消息来提供批准，该消息会被拦截并写入 stdin
                    return ""

                full_text, new_session_id = client.stream_chat(
                    question,
                    session_id=session_id,
                    on_delta=on_delta,
                    on_user_input=on_user_input,
                    on_approval=on_approval
                )
                if new_session_id:
                    deliver(sync_session_id, new_session_id)
                last_context_usage = getattr(client, "last_context_usage", None)
                if last_context_usage and owns_turn():
                    self._pending_context_usage_by_turn[self._context_usage_pending_key(chat_id, turn_idx)] = last_context_usage
                deliver(self._on_done, turn_idx, full_text, "", resolved_model, "", chat_id)
            except Exception as exc:
                error_msg = str(exc)
                deliver(self._on_done, turn_idx, "", error_msg, resolved_model, "", chat_id)
            finally:
                # 清除客户端引用
                if client is not None:
                    self._clear_active_claudecode_client(client)

        bindings.app.threading.Thread(target=_worker, daemon=True).start()

    def _bind_provider_turn(self, owner, index):
        chat, turns, _current = self._chat_target_for_request(owner)
        owner = str(owner or (chat or {}).get("id") or self.active_chat_id or "").strip()
        turn = turns[index] if 0 <= index < len(turns) else None
        token = turn.setdefault("_provider_request_id", uuid.uuid4().hex) if isinstance(turn, dict) else None
        generation = getattr(self, "_shortcut_clear_revisions", {}).get(owner)

        def owns_turn():
            if generation != getattr(self, "_shortcut_clear_revisions", {}).get(owner):
                return False
            target, current_turns, _active = self._chat_target_for_request(owner)
            return isinstance(target, dict) and (token is None or (
                index < len(current_turns) and isinstance(current_turns[index], dict)
                and current_turns[index].get("_provider_request_id") == token))

        return owns_turn

    def _post_provider_turn(self, owns_turn, callback, *args):
        def apply():
            if owns_turn():
                callback(*args)
        self._call_after_if_alive(apply)

    def _launch_generic_worker(self, args, *, prepared_attachments=None):
        owns_turn = self._bind_provider_turn(args[5], args[1])
        options = {"owns_turn": owns_turn}
        if prepared_attachments:
            options["prepared_attachments"] = prepared_attachments
        worker = bindings.app.threading.Thread(target=self._worker, args=args, kwargs=options, daemon=True)
        return worker.start()

    def _update_busy_state(self) -> None:
        busy = bool(self._active_request_count or self.is_running)
        self.is_running = busy
        self.new_chat_button.Enable()
        if busy:
            self._set_input_hint_sent()
        else:
            self._set_input_hint_idle()

    def _add_system_message_to_chat(self, text: str) -> None:
        message = str(text or "")
        if not message:
            return
        turn = {
            "question": "",
            "answer_md": message,
            "model": "system",
            "created_at": bindings.app.time.time(),
        }
        self._apply_nonrecoverable_turn_metadata(turn, "system", "")
        self.active_session_turns.append(turn)
        self.active_turn_idx = len(self.active_session_turns) - 1
        self._mark_chat_turns_dirty(start_index=self.active_turn_idx)

    def _trigger_send(self):
        return self._request_question_submit()

    def _request_question_submit(self, *, require_focus: bool = True, show_empty_warning: bool = False) -> bool:
        if getattr(self, "_question_submit_in_progress", False):
            return False
        focus = wx.Window.FindFocus()
        if require_focus and not self.input_edit.HasFocus() and focus is not self.send_button:
            return False
        if not self.input_edit.IsEnabled() or not self.send_button.IsEnabled():
            return False
        raw_value = str(self.input_edit.GetValue() or "")
        if not raw_value.strip() and not list(getattr(self, "_pending_input_attachments", []) or []):
            if show_empty_warning:
                wx.MessageBox("请输入问题，输入框内容为空", "提示", wx.OK | wx.ICON_WARNING)
            return False
        snapshot = self._capture_answer_continue_state()
        snapshot["input_selection"] = self.input_edit.GetSelection()
        self._question_submit_in_progress = True
        try:
            ok, message = self._submit_question(raw_value.strip(), source="local")
        except Exception as exc:
            ok, message = False, str(exc)
        finally:
            self._question_submit_in_progress = False
        if ok:
            return True
        self._restore_answer_continue_state(snapshot)
        self.input_edit.ChangeValue(raw_value)
        self.input_edit.SetSelection(*snapshot["input_selection"])
        if message:
            wx.MessageBox(message, "Error", wx.OK | wx.ICON_WARNING)
        return False

    def _post_send_click(self):
        if not self.send_button.IsEnabled():
            return
        ev = wx.CommandEvent(wx.wxEVT_BUTTON, self.send_button.GetId())
        ev.SetEventObject(self.send_button)
        wx.PostEvent(self.send_button, ev)

    def _trigger_new_chat(self):
        if self.new_chat_button.IsEnabled():
            ev = wx.CommandEvent(wx.wxEVT_BUTTON, self.new_chat_button.GetId())
            ev.SetEventObject(self.new_chat_button)
            wx.PostEvent(self.new_chat_button, ev)

    def _visible_clear_owner(self) -> str:
        if getattr(self, "view_mode", "") == "history" and self.view_history_id:
            return str(self.view_history_id).strip()
        return str(getattr(self, "active_chat_id", "") or getattr(self, "current_chat_id", "") or "").strip()

    def _clear_pending_for(self, owner: str) -> bool:
        owner = str(owner or "").strip()
        return owner in getattr(self, "_shortcut_clear_pending", {}) or owner in getattr(self, "_native_delete_pending", ())

    def _trigger_shortcut_clear(self) -> bool:
        if getattr(self, "_closing", False) or not self.new_chat_button.IsEnabled():
            return False
        owner = self._visible_clear_owner() or self._ensure_active_chat_id()
        pending = getattr(self, "_shortcut_clear_pending", None)
        if pending is None:
            pending = self._shortcut_clear_pending = {}
        if self._clear_pending_for(owner):
            return False
        target, turns, current = self._chat_target_for_request(owner)
        if isinstance(target, dict) and target.get("model") == AGY_MODEL_ID:
            self.SetStatusText("请新建 AGY 聊天以开始新的上下文")
            return False
        store = getattr(self, "chat_store", None)
        if (not isinstance(target, dict) or store is None
                or not callable(getattr(store, "begin_clear_operation", None))):
            return False
        request_id = f"local-key-{uuid.uuid4().hex}"
        pending[owner] = request_id
        self._play_send_sound()
        summary = copy.deepcopy({key: value for key, value in target.items()
                                 if key not in {"turns", "execution_steps"}})
        pending_turns = copy.deepcopy(turns) if current or "turns" in target else None
        revision = ChatStore._clear_revision_value(summary.get("revision"))
        pair_id = self._read_pair_id()

        def clear():
            operation, claimed, failed, missing_owner = None, False, False, False
            try:
                operation = store.begin_clear_operation(
                    owner, idempotency_key=request_id, pair_id=pair_id,
                    pending_chat=summary, pending_turns=pending_turns,
                    expected_revision=revision, allow_new_owner=current, play_resend_sound=False,
                )
                if operation.get("snapshot") and not getattr(self, "_closing", False):
                    claimed = store.claim_clear_resend_dispatch(operation["operation_id"],
                        chat_id=owner, revision=operation["revision"]) is not None
                    failed = not claimed
            except Exception as exc:
                failed = True
                missing_owner = isinstance(exc, KeyError) and exc.args == ("CHAT_NOT_FOUND",)
            self._call_after_if_alive(self._finish_shortcut_clear, owner, request_id,
                                      operation, claimed, failed, missing_owner)

        bindings.app.threading.Thread(target=clear, name="mc-keyboard-clear", daemon=True).start()
        return True

    def _finish_shortcut_clear(self, owner, request_id, operation, claimed, failed, missing_owner=False):
        if self._shortcut_clear_pending.get(owner) != request_id:
            return
        if claimed and getattr(self, "_closing", False):
            releases = getattr(self, "_close_unattempted_resends", ())
            self._close_unattempted_resends = (*releases, (operation["operation_id"], owner, operation["revision"]))
        self._shortcut_clear_pending.pop(owner)
        if getattr(self, "_close_started", False):
            self._schedule_close_progress()
        if operation is None:
            if missing_owner:
                self._discard_deleted_chat_view(owner)
                self._refresh_history()
                self._render_answer_list()
            message = "无法安全清空上下文，请重试"
            self.SetStatusText(message)
            self._speak_text_via_screen_reader(message)
            return
        target, _turns, is_current = self._chat_target_for_request(owner)
        if not isinstance(target, dict):
            return
        generations = getattr(self, "_shortcut_clear_revisions", None)
        if generations is None:
            generations = self._shortcut_clear_revisions = {}
        generations[owner] = operation["revision"]
        self._record_clear_revision(target, operation)
        if is_current:
            self._apply_active_clear_projection(owner, bindings.app.time.time())
        else:
            self._clear_context_chat_state(target, owner, bindings.app.time.time())
            self._mark_chat_turns_dirty(owner, 0)
            self._defer_chat_state_save()
            self._push_remote_history_changed(owner)
            self._push_remote_state(owner)
        visible = self._visible_clear_owner() == owner
        if not getattr(self, "_closing", False):
            if claimed:
                failed = not self._submit_clear_snapshot_offscreen(operation, operation["snapshot"], preclaimed=True)
            if visible:
                self._answer_list_tail_notice = "以开启新会话"
                self._answer_list_tail_notice_chat_id = owner
                self._render_answer_list()
            if failed:
                self.SetStatusText("已清空上下文，自动重发失败")
                if visible:
                    self._offer_clear_operation_recovery(operation["operation_id"])
            else:
                self.SetStatusText("已清空上下文")

    def _clear_context_and_start_new_chat(self, auto_resend_first: bool = False, *, request_id: str = "") -> bool:
        if getattr(self, "_closing", False) or not self.new_chat_button.IsEnabled():
            return False
        visible_history_id = str(getattr(self, "view_history_id", "") or "").strip()
        if getattr(self, "view_mode", "") == "history" and visible_history_id:
            current_ids = {str(self.active_chat_id or "").strip(), str(self.current_chat_id or "").strip()}
            current_ids.discard("")
            if visible_history_id not in current_ids:
                return self._clear_context_for_chat_id(
                    visible_history_id, auto_resend_first=auto_resend_first, request_id=request_id
                ) == "cleared"
        now = bindings.app.time.time()
        if not self.active_chat_id:
            self.active_chat_id = self._ensure_active_chat_id()
        if not self.current_chat_id:
            self.current_chat_id = self.active_chat_id
        first_payload = None
        for t in self.active_session_turns or []:
            if not ChatStore._eligible_clear_payload(t):
                continue
            first_payload = copy.deepcopy(t)
            break
        try:
            clear_operation = self._begin_durable_clear_operation(
                self.active_chat_id, request_id=request_id or f"local-{uuid.uuid4().hex}"
            )
        except Exception:
            self.SetStatusText("Unable to clear context safely")
            return False
        if clear_operation is not None and clear_operation.get("idempotent_replay"):
            self._reconcile_clear_operation_projection(clear_operation)
            return True
        if clear_operation is not None:
            self._record_clear_revision(self._current_chat_state, clear_operation)
            first_payload = copy.deepcopy(clear_operation.get("snapshot"))
        self._apply_active_clear_projection(self.active_chat_id, now)
        if auto_resend_first and first_payload:
            dispatched = self._dispatch_clear_operation_resend(clear_operation, first_payload)
            if not dispatched and clear_operation is not None:
                self._offer_clear_operation_recovery(clear_operation["operation_id"])
        return True

    def _apply_active_clear_projection(self, owner: str, now: float) -> None:
        if self._visible_clear_owner() == owner:
            self._invalidate_answer_mouse_gesture()
        self._retire_kimi_chat_runtime(
            self._current_chat_state, str(self.active_kimi_session_id or self._current_chat_state.get("kimi_session_id") or "").strip()
        )
        self._clear_codex_context_usage_for_thread_change(self._current_chat_state)
        self.active_session_turns = []
        self.active_session_started_at = now
        self.active_turn_idx = -1
        self.is_running = False
        self._active_request_count = 0
        self._update_busy_state()
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
        self._pending_context_usage_by_turn = {}
        self._codex_early_context_usage_owner = {}
        self._current_chat_state["id"] = self.active_chat_id
        self._current_chat_state["turns"] = self.active_session_turns
        self._current_chat_state["updated_at"] = now
        self._current_chat_state["openclaw_session_key"] = self.active_openclaw_session_key
        self._current_chat_state["openclaw_session_id"] = self.active_openclaw_session_id
        self._current_chat_state["openclaw_session_file"] = self.active_openclaw_session_file
        self._current_chat_state["openclaw_sync_offset"] = self.active_openclaw_sync_offset
        self._current_chat_state["openclaw_last_event_id"] = self.active_openclaw_last_event_id
        self._current_chat_state["openclaw_last_synced_at"] = self.active_openclaw_last_synced_at
        self._write_active_codex_session_state_to_chat(self._current_chat_state)
        self._write_active_kimi_session_state_to_chat(self._current_chat_state)
        self._current_chat_state["claudecode_session_id"] = self.active_claudecode_session_id
        self._current_chat_state["context_usage"] = None
        self._current_chat_state["execution_steps"] = []
        if self._visible_clear_owner() == owner and not getattr(self, "_closing", False):
            self._answer_list_tail_notice = "以开启新会话"
            self._answer_list_tail_notice_chat_id = owner
            self._render_answer_list()
        self._defer_chat_state_save()
        self._mark_openclaw_lifecycle_dirty()
        self._push_remote_history_changed(self.active_chat_id)
        self._push_remote_state(self.active_chat_id)
        self.SetStatusText("已清空上下文")
    def _begin_durable_clear_operation(self, chat_id: str, *, request_id: str) -> dict | None:
        if self._clear_pending_for(chat_id):
            raise RuntimeError("CLEAR_PENDING")
        store = getattr(self, "chat_store", None)
        if not (getattr(self, "_chat_store_enabled", False) and store is not None and hasattr(store, "begin_clear_operation")):
            return None
        transport = getattr(self, "_remote_nats_transport", None)
        pair_id = self._read_pair_id()
        if hasattr(store, "load_chat"):
            target, turns, _is_current = self._chat_target_for_request(chat_id)
            if not isinstance(target, dict):
                raise KeyError("CHAT_NOT_FOUND")
            store.upsert_chat(target)
            store.replace_turns(chat_id, turns)
        return store.begin_clear_operation(chat_id, idempotency_key=request_id, pair_id=pair_id)

    @staticmethod
    def _record_clear_revision(target, operation):
        current = ChatStore._clear_revision_value(target.get("revision")) or 0
        revision = ChatStore._clear_revision_value(operation.get("revision")) or 0
        if revision >= current:
            target["revision"] = revision

    def _reconcile_clear_operation_projection(self, operation: dict) -> None:
        if not isinstance(operation, dict) or operation.get("state") == "superseded":
            return
        owner = str(operation.get("chat_id") or "").strip()
        target, turns, is_current = self._chat_target_for_request(owner)
        if not isinstance(target, dict):
            return
        revision = ChatStore._clear_revision_value(operation.get("revision"))
        if revision is None or revision <= (ChatStore._clear_revision_value(target.get("revision")) or 0):
            return
        self._record_clear_revision(target, operation)
        owned = [turn for turn in turns if isinstance(turn, dict)
                 and turn.get("clear_operation_id") == operation.get("operation_id")
                 and turn.get("clear_revision") == revision]
        self._clear_context_chat_state(target, owner, bindings.app.time.time())
        target["turns"] = owned
        if is_current:
            self.active_session_turns = owned
            self.active_turn_idx = len(owned) - 1
            self._current_chat_state["turns"] = owned

    def _dispatch_clear_operation_resend(self, operation: dict | None, payload: dict) -> bool:
        store = getattr(self, "chat_store", None)
        op_id = str((operation or {}).get("operation_id") or "").strip()
        owner = str((operation or {}).get("chat_id") or "").strip()
        revision = ChatStore._clear_revision_value((operation or {}).get("revision"))
        if (
            not op_id or not owner or revision is None or store is None
            or not hasattr(store, "get_clear_operation")
            or not hasattr(store, "claim_clear_resend_dispatch")
        ):
            return False
        try:
            persisted = store.get_clear_operation(op_id)
        except Exception:
            return False
        if (
            not isinstance(persisted, dict)
            or str(persisted.get("operation_id") or "").strip() != op_id
            or str(persisted.get("chat_id") or "").strip() != owner
            or ChatStore._clear_revision_value(persisted.get("revision")) != revision
        ):
            return False
        snapshot = copy.deepcopy(persisted.get("snapshot"))
        if not isinstance(snapshot, dict) or payload != snapshot or not ChatStore._eligible_clear_payload(snapshot):
            return False
        if owner not in {str(self.active_chat_id or "").strip(), str(self.current_chat_id or "").strip()}:
            return self._submit_clear_snapshot_offscreen(persisted, snapshot)
        if store.claim_clear_resend_dispatch(op_id, chat_id=owner, revision=revision) is None:
            return False
        previous_attachments = list(getattr(self, "_pending_input_attachments", []) or [])
        previous_operation = str(getattr(self, "_clear_resend_operation_id", "") or "")
        previous_revision = getattr(self, "_clear_resend_revision", None)
        ok = False
        previous_turn_count = len(self.active_session_turns)
        try:
            self._clear_resend_operation_id = op_id
            self._clear_resend_revision = revision
            self._pending_input_attachments = []
            ok, _message = self._submit_question(
                str(snapshot.get("question") or "").strip() or str(snapshot.get("text") or "").strip(), source="local",
                model=str(snapshot.get("model") or "") or None,
                chat_id=owner,
            )
            if ok:
                target = self._current_chat_state if owner in {self.active_chat_id, self.current_chat_id} else self._find_archived_chat(owner)
                turns = (target or {}).get("turns") if isinstance(target, dict) else None
                if isinstance(turns, list) and turns and isinstance(turns[-1], dict):
                    turns[-1]["clear_operation_id"] = op_id
                    turns[-1]["clear_revision"] = revision
        except Exception:
            if len(self.active_session_turns) > previous_turn_count:
                del self.active_session_turns[previous_turn_count:]
                self.active_turn_idx = len(self.active_session_turns) - 1
                self._mark_chat_turns_dirty(start_index=previous_turn_count)
                self._defer_chat_state_save()
            try:
                store.transition_clear_operation(op_id, "resend_blocked", failure_code="PROVIDER_REJECTED")
            except Exception:
                pass
            return False
        finally:
            self._pending_input_attachments = previous_attachments
            self._clear_resend_operation_id = previous_operation
            self._clear_resend_revision = previous_revision
        if not ok:
            store.transition_clear_operation(op_id, "resend_blocked", failure_code="PROVIDER_REJECTED")
        return bool(ok)

    def _submit_clear_snapshot_offscreen(self, operation: dict, snapshot: dict, *, preclaimed=False) -> bool:
        """Dispatch without changing the visible chat, selection, or draft."""
        owner = str(operation.get("chat_id") or "").strip()
        op_id = str(operation.get("operation_id") or "").strip()
        store = getattr(self, "chat_store", None)
        target, _turns, is_current = self._chat_target_for_request(owner)
        if not owner or not op_id or store is None:
            return False
        if not isinstance(target, dict):
            store.transition_clear_operation(op_id, "resend_blocked", failure_code="OWNER_UNAVAILABLE")
            return False
        revision = ChatStore._clear_revision_value(operation.get("revision"))
        if revision is None or (not preclaimed and store.claim_clear_resend_dispatch(
            op_id, chat_id=owner, revision=revision
        ) is None):
            return False
        question = str(snapshot.get("question") or "").strip() or str(snapshot.get("text") or "").strip()
        model = bindings.app.normalize_model_id(snapshot.get("model") or target.get("model") or DEFAULT_MODEL_ID)
        attachments = []
        allowed = {
            "origin", "question_origin", "provider", "provider_kind",
            "provider_message_id", "canonical_turn_id", "canonical_message_id",
            "codex_service_tier",
        }
        turn = {key: copy.deepcopy(snapshot[key]) for key in allowed if key in snapshot}
        turn.update({"question": question, "answer_md": REQUESTING_TEXT, "model": model,
                     "attachments": attachments, "created_at": bindings.app.time.time(),
                     "clear_operation_id": op_id, "clear_revision": revision})
        turns = target.setdefault("turns", [])
        if not isinstance(turns, list):
            turns = []
            target["turns"] = turns
        turn_idx = len(turns)
        turns.append(turn)
        previous_busy = (self.is_running, self._active_request_count) if is_current else None
        if is_current:
            self.active_turn_idx = turn_idx
            self.is_running = True
            self._active_request_count = max(1, int(self._active_request_count or 0))
        self._mark_turn_request_pending(turn, model, question)
        target["updated_at"] = bindings.app.time.time()
        self._mark_chat_turns_dirty(owner, turn_idx)
        self._defer_chat_state_save()
        worker_question = question
        if attachments and not is_codex_model(model) and not is_kimi_model(model):
            context = self._build_cli_attachment_context(attachments)
            worker_question = f"{question}\n\n{context}".strip() if question else context
        try:
            if is_codex_model(model):
                self._start_codex_worker_for_turn(owner, turn_idx, question, model)
            elif is_kimi_model(model):
                self._start_kimi_worker_for_turn(owner, turn_idx, question, model)
            elif is_claudecode_model(model):
                self._start_claudecode_worker_for_turn(
                    owner, turn_idx, worker_question,
                    str(target.get("claudecode_session_id") or ""), model)
            else:
                self._launch_generic_worker((bindings.app.openrouter_api_key_for_app(), turn_idx,
                                             worker_question, model, False, owner))
            if not preclaimed:
                self._play_clear_resend_accepted_sound(op_id, owner, revision)
        except Exception:
            if 0 <= turn_idx < len(turns) and turns[turn_idx] is turn:
                turns.pop(turn_idx)
                if is_current:
                    self.active_turn_idx = len(turns) - 1
                    self.is_running, self._active_request_count = previous_busy
                self._mark_chat_turns_dirty(owner, turn_idx)
                self._defer_chat_state_save()
            store.transition_clear_operation(op_id, "resend_blocked", failure_code="PROVIDER_REJECTED")
            return False
        return True

    def _play_clear_resend_accepted_sound(self, operation_id: str, chat_id: str,
                                          revision: int) -> bool:
        """Consume the durable grant before playing an accepted-resend sound."""
        op_id = str(operation_id or "").strip()
        store = getattr(self, "chat_store", None)
        owner = str(chat_id or "").strip()
        if not op_id or not owner or store is None or not hasattr(store, "consume_clear_resend_sound"):
            return False
        try:
            granted = store.consume_clear_resend_sound(op_id, chat_id=owner, revision=revision)
            if granted is None:
                return False
            self._play_send_sound()
        except Exception:
            return False
        return True

    def recover_clear_operation(self, operation_id: str, choice: str) -> bool:
        store = getattr(self, "chat_store", None)
        operation = store.get_clear_operation(operation_id) if store is not None else None
        if not operation or operation.get("state") != "resend_blocked":
            return False
        normalized = str(choice or "").strip().lower()
        if normalized == "cancel":
            store.transition_clear_operation(operation_id, "completed_clear_only")
            self._pending_clear_recoveries = [
                item for item in (getattr(self, "_pending_clear_recoveries", []) or [])
                if isinstance(item, dict)
                and str(item.get("operation_id") or "") != str(operation_id)
            ]
            return True
        return False

    def _reconcile_clear_operation_for_owner(self, chat_id: str, *, surface_recovery: bool = False) -> dict | None:
        owner = str(chat_id or "").strip()
        store = getattr(self, "chat_store", None)
        if not owner or store is None or not hasattr(store, "active_clear_operation"):
            return None
        operation = store.active_clear_operation(owner)
        if not operation:
            return None
        operation_id = str(operation.get("operation_id") or "").strip()
        if hasattr(store, "reconcile_completed_clear_operation"):
            revision = operation.get("revision")
            reconciled = store.reconcile_completed_clear_operation(
                operation_id, chat_id=owner, revision=revision
            )
            if reconciled is not None:
                self._pending_clear_recoveries = [
                    item for item in (getattr(self, "_pending_clear_recoveries", []) or [])
                    if isinstance(item, dict)
                    and str(item.get("operation_id") or "") != operation_id
                ]
                return None
        pending_ids = {
            str(item.get("operation_id") or "")
            for item in (getattr(self, "_pending_clear_recoveries", []) or [])
            if isinstance(item, dict)
        }
        if operation_id in pending_ids and operation.get("state") in {"requested", "clear_acknowledged"}:
            self._pending_clear_recoveries = [
                item for item in (getattr(self, "_pending_clear_recoveries", []) or [])
                if isinstance(item, dict)
                and str(item.get("operation_id") or "") != operation_id
            ]
            self._dispatch_clear_operation_resend(operation, operation.get("snapshot") or {})
            return store.active_clear_operation(owner)
        if surface_recovery and operation.get("state") == "resend_blocked":
            self._pending_clear_recoveries = [
                item for item in (getattr(self, "_pending_clear_recoveries", []) or [])
                if isinstance(item, dict)
                and str(item.get("operation_id") or "") != operation_id
            ]
            self._offer_clear_operation_recovery(operation_id)
            return store.active_clear_operation(owner)
        return operation

    def _accept_clear_operation_result(self, turn: dict, chat_id: str) -> bool:
        operation_id = str((turn or {}).get("clear_operation_id") or "").strip()
        if not operation_id:
            return True
        store = getattr(self, "chat_store", None)
        operation = store.get_clear_operation(operation_id) if store is not None else None
        owner = str(chat_id or "").strip()
        if (
            operation
            and operation.get("state") == "resend_dispatched"
            and str(operation.get("chat_id") or "").strip() == owner
            and operation.get("revision") == turn.get("clear_revision")
        ):
            return True
        if store is not None:
            store.quarantine("SUPERSEDED_CLEAR_RESULT", {"operation_id": operation_id, "chat_id": str(chat_id or "")})
        return False

    def _complete_clear_operation_turn(self, turn: dict, *, failed: bool) -> None:
        operation_id = str((turn or {}).get("clear_operation_id") or "").strip()
        store = getattr(self, "chat_store", None)
        if operation_id and store is not None:
            try:
                store.transition_clear_operation(operation_id, "resend_blocked" if failed else "completed_with_resend",
                                                 failure_code="PROVIDER_FAILURE" if failed else "")
            except (KeyError, ValueError):
                pass

    def _offer_clear_operation_recovery(self, operation_id: str) -> None:
        """Expose a blocked resend without offering an unsafe redispatch."""
        focus = wx.Window.FindFocus()
        dialog = wx.MessageDialog(
            self,
            "The first message was not confirmed as sent. Automatic resend is blocked to avoid a duplicate.",
            "Clear context resend recovery",
            wx.OK | wx.ICON_WARNING,
        )
        try:
            result = dialog.ShowModal()
        finally:
            dialog.Destroy()
            if focus is not None and hasattr(focus, "SetFocus"):
                focus.SetFocus()

    def _clear_context_for_chat_id(self, chat_id: str, *, auto_resend_first: bool = False,
                                   request_id: str = "") -> str:
        if getattr(self, "_closing", False):
            return "unavailable"
        target_id = str(chat_id or "").strip()
        if not target_id:
            return "missing"
        if target_id in getattr(self, "_native_delete_pending", ()):
            return "unavailable"
        current_ids = {str(self.active_chat_id or "").strip(), str(self.current_chat_id or "").strip()}
        current_ids.discard("")
        target, _, _ = self._chat_target_for_request(target_id)
        if isinstance(target, dict) and target.get("model") == AGY_MODEL_ID:
            return "unavailable"
        if target_id in current_ids:
            try:
                cleared = self._clear_context_and_start_new_chat(
                    auto_resend_first=auto_resend_first, request_id=request_id
                )
            except TypeError as exc:
                if "unexpected keyword argument" not in str(exc):
                    raise
                cleared = self._clear_context_and_start_new_chat()
            return "cleared" if cleared else "unavailable"
        target_chat = self._find_archived_chat(target_id)
        if not isinstance(target_chat, dict):
            return "not_found"
        try:
            operation = self._begin_durable_clear_operation(
                target_id, request_id=request_id or f"clear-{uuid.uuid4().hex}"
            )
        except Exception:
            return "unavailable"
        if operation is not None and operation.get("idempotent_replay"):
            self._reconcile_clear_operation_projection(operation)
            return "cleared"
        if operation is not None:
            self._record_clear_revision(target_chat, operation)
        self._clear_context_chat_state(target_chat, target_id, bindings.app.time.time())
        self._mark_chat_turns_dirty(target_id, 0)
        if getattr(self, "_chat_store_enabled", False) and getattr(self, "chat_store", None) is not None:
            try:
                self.chat_store.replace_turns(target_id, [])
                self.chat_store.replace_execution_steps(target_id, [])
            except Exception:
                pass
        self._defer_codex_state_save()
        self._push_remote_history_changed(target_id)
        self._push_remote_state(target_id)
        if getattr(self, "view_mode", "") == "history" and str(getattr(self, "view_history_id", "") or "").strip() == target_id:
            self._answer_list_tail_notice = "以开启新会话"
            self._answer_list_tail_notice_chat_id = target_id
            self._render_answer_list()
        self.SetStatusText("已清空上下文")
        if auto_resend_first and operation is not None and operation.get("snapshot"):
            dispatched = self._dispatch_clear_operation_resend(operation, operation["snapshot"])
            if not dispatched and str(getattr(self, "view_history_id", "") or "").strip() == target_id:
                self._offer_clear_operation_recovery(operation["operation_id"])
        return "cleared"

    def _clear_context_chat_state(self, chat: dict, chat_id: str, updated_at: float) -> None:
        if self._visible_clear_owner() == chat_id:
            self._invalidate_answer_mouse_gesture()
        self._retire_kimi_chat_runtime(chat, str(chat.get("kimi_session_id") or "").strip())
        self._clear_codex_context_usage_for_thread_change(chat)
        if chat.get("codex_thread_id"):
            for field in (*CODEX_STORAGE_FIELDS, *NATIVE_CHAT_FIELDS):
                chat.pop(field, None)
        chat["id"] = str(chat.get("id") or chat_id or "").strip()
        chat["turns"] = []
        chat["updated_at"] = updated_at
        chat["openclaw_session_key"] = DEFAULT_OPENCLAW_SESSION_KEY
        chat["openclaw_session_id"] = ""
        chat["openclaw_session_file"] = ""
        chat["openclaw_sync_offset"] = 0
        chat["openclaw_last_event_id"] = ""
        chat["openclaw_last_synced_at"] = 0.0
        chat["codex_thread_id"] = ""
        chat["codex_turn_id"] = ""
        chat["codex_turn_active"] = False
        chat["codex_pending_prompt"] = ""
        chat["codex_pending_request"] = None
        chat["codex_request_queue"] = []
        chat["codex_thread_flags"] = []
        chat["codex_latest_assistant_text"] = ""
        chat["codex_latest_assistant_phase"] = ""
        chat["kimi_session_id"] = ""
        chat["kimi_turn_id"] = ""
        chat["kimi_turn_active"] = False
        chat["kimi_pending_prompt"] = ""
        chat["kimi_request_queue"] = []
        chat["claudecode_session_id"] = ""
        chat["context_usage"] = None
        chat["execution_steps"] = []

    def _prompt_and_create_named_chat(self) -> bool:
        if not self.new_chat_button.IsEnabled():
            return False
        dlg = wx.TextEntryDialog(self, "请输入新聊天的名称：", "新建聊天")
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return False
            title = str(dlg.GetValue() or "").strip()
        finally:
            dlg.Destroy()
        if not title:
            self.SetStatusText("已取消新聊天命名")
            return False
        self._on_new_chat_clicked(None, title=title, title_manual=True)
        return True

    def _on_send_clicked(self, _):
        return self._request_question_submit(require_focus=False, show_empty_warning=True)

    def _submit_archived_remote_question(self, chat: dict, question: str, model: str, *,
                                         client_send_id: str = "", attachments=None,
                                         attachment_scope: tuple = (), active: bool = False,
                                         prepared_attachments=None, source: str = "rc",
                                         expected_kimi_prompt_id: str = "", prepared_chat=None) -> tuple[bool, str]:
        """Accept a remote turn for the resolved chat without changing the local draft."""
        owner_id = str((chat or {}).get("id") or "").strip()
        if self._clear_pending_for(owner_id):
            return False, "正在清空此聊天的上下文"
        resolved_model = bindings.app.normalize_model_id(model or "", default="")
        if (is_codex_model(resolved_model) or is_kimi_model(resolved_model)) and owner_id in getattr(self, "_native_history_reserved", ()):
            return False, "正在同步原生聊天历史，请稍后重试"
        text = str(question or "").strip()
        if not owner_id or not resolved_model or not text:
            return False, "invalid archived chat submission"
        store = getattr(self, "chat_store", None)
        if getattr(self, "_chat_store_enabled", False) and store is not None:
            if self._reconcile_clear_operation_for_owner(owner_id, surface_recovery=False) is not None:
                return False, "Clear/resend recovery is still pending for this chat."
            has_live_turns = active or ((source == "terminal" or attachments or chat.get("codex_terminal_thread")
                                         or (is_kimi_model(resolved_model) and self._kimi_should_steer_turn(chat, active)))
                                        and isinstance(chat.get("turns"), list))
            loaded = prepared_chat
            if not has_live_turns and source != "terminal":
                loaded = store.load_chat(owner_id, include_execution_steps=False)
            if source == "terminal" and not has_live_turns and not isinstance(loaded, dict):
                return False, "找不到原生聊天的已保存状态。"
            if isinstance(loaded, dict):
                # Preserve the object held by archived_chats while replacing
                # summary-only metadata with this owner's complete durable state.
                chat.clear()
                chat.update(loaded)
        if resolved_model == AGY_MODEL_ID:
            try:
                self._validate_agy_submission(chat)
            except AgyError as exc:
                return False, str(exc)
        turns = self.active_session_turns if active else chat.get("turns")
        if not isinstance(turns, list):
            turns = []
        chat["turns"] = turns
        previous_model = self._model_for_chat_selection(chat) or STARTUP_DEFAULT_MODEL_ID
        turn_idx = len(turns)
        turn = {
            "question": text,
            "answer_md": REQUESTING_TEXT,
            "model": resolved_model,
            "created_at": bindings.app.time.time(),
            "question_origin": source,
            "client_send_id": client_send_id,
        }
        if source == "terminal":
            turn["terminal_notification_pair"] = self._read_pair_id()
        if expected_kimi_prompt_id:
            turn["kimi_expected_prompt_id"] = expected_kimi_prompt_id
        if attachments:
            turn["attachments"] = attachments
            turn["remote_attachment_source"] = list(attachment_scope[:2])
        previous_updated_at = chat.get("updated_at")
        turns.append(turn)
        chat["model"] = resolved_model
        self._mark_turn_request_pending(turn, resolved_model, text)
        try:
            if is_codex_model(resolved_model):
                started = self._start_codex_worker_for_turn(owner_id, turn_idx, text, resolved_model)
            elif is_kimi_model(resolved_model):
                started = self._start_kimi_worker_for_turn(owner_id, turn_idx, text, resolved_model)
            elif resolved_model == AGY_MODEL_ID:
                started = self._start_agy_worker_for_turn(
                    owner_id, turn_idx, text, resolved_model, prepared_attachments=prepared_attachments,
                )
            elif is_claudecode_model(resolved_model):
                started = self._start_claudecode_worker_for_turn(
                    owner_id, turn_idx, text, str(chat.get("claudecode_session_id") or ""), resolved_model
                )
            else:
                started = self._launch_generic_worker(
                    (bindings.app.openrouter_api_key_for_app(), turn_idx, text, resolved_model, False, owner_id),
                    prepared_attachments=prepared_attachments,
                )
            if started is False:
                raise RuntimeError("Provider rejected submission")
        except Exception as exc:
            del turns[turn_idx:]
            chat["model"] = previous_model
            if previous_updated_at is None:
                chat.pop("updated_at", None)
            else:
                chat["updated_at"] = previous_updated_at
            return False, str(exc)
        if active:
            self.active_turn_idx = turn_idx
            self.is_running = True
            self._active_request_count = max(1, int(getattr(self, "_active_request_count", 0) or 0))
            if self.view_mode == "active" and not self._append_submitted_question_to_answer_list(turn_idx, turn):
                self._render_answer_list()
        self._play_send_sound()
        self._record_chat_activity(chat, turn["created_at"])
        self._mark_chat_turns_dirty(owner_id, turn_idx)
        if attachment_scope:
            store.upsert_chat(chat)
            self._persist_dirty_chat_turns(store, owner_id, turns, attachment_scope=attachment_scope)
        self._defer_chat_state_save()
        if turn_idx == 0:
            self._schedule_first_question_auto_title(owner_id, text)
        return True, ""

    def _preflight_attachment_draft(self, raw_question, source, model, chat_id, client_send_id,
                                    attachments, *, agy_context=None, tool_settings=None) -> tuple[bool, str]:
        if getattr(self, "_attachment_preflight", None) is not None:
            return False, "正在检查附件，请稍候"
        expected_state = self._current_chat_state
        expected_owner = (self.active_chat_id, self.current_chat_id, self.view_mode, self.view_history_id)
        expected_model = self._resolve_current_model()
        expected_draft = self.input_edit.GetValue()
        attachments = copy.deepcopy(attachments)
        token = object()
        self._attachment_preflight = token

        def finish(files, prepared, error):
            try:
                if self._attachment_preflight is not token:
                    return
                self._attachment_preflight = None
                if (getattr(self, "_closing", False) or self._current_chat_state is not expected_state
                        or expected_owner != (self.active_chat_id, self.current_chat_id, self.view_mode, self.view_history_id)
                        or self._resolve_current_model() != expected_model or self.input_edit.GetValue() != expected_draft
                        or list(self._pending_input_attachments) != attachments):
                    return
                if not error:
                    ok, error = self._submit_question(
                        raw_question, source, model, chat_id, client_send_id=client_send_id,
                        _verified_attachments=(files, prepared),
                        _tool_settings=tool_settings,
                    )
                    if ok:
                        return
                if error:
                    wx.MessageBox(error, "附件发送失败", wx.OK | wx.ICON_WARNING)
            finally:
                if agy_context is not None and prepared is not None:
                    prepared.close_unused()

        def worker():
            try:
                files, failed = self._normalize_outgoing_attachments(attachments)
                if failed:
                    names = "、".join(item["name"] for item in failed)
                    raise ValueError(f"以下附件无法读取，请移除或修复后重试：{names}")
                prepared = (self._prepare_agy_attachments(agy_context, files) if agy_context is not None
                            else read_openrouter_claude_attachments(model, files))
            except Exception as exc:
                self._call_after_if_alive(finish, [], None, str(exc) or "无法读取附件")
                return
            queued = self._call_after_if_alive(finish, files, prepared, "")
            if queued is False and agy_context is not None:
                prepared.close_unused()

        try:
            bindings.app.threading.Thread(target=worker, daemon=True, name="attachment-preflight").start()
        except Exception:
            self._attachment_preflight = None
            return False, "无法开始检查附件"
        return True, ""

    def _submit_question(self, question: str, source: str = "local", model: str | None = None, chat_id: str = "", *,
                         client_send_id: str = "", _verified_attachments=None, _tool_settings=None) -> tuple[bool, str]:
        if getattr(self, "_closing", False):
            return False, "程序正在关闭"
        if self._clear_pending_for(chat_id or self._visible_clear_owner()):
            return False, "正在清空此聊天的上下文"
        automatic_clear_resend = bool(str(getattr(self, "_clear_resend_operation_id", "") or "").strip())
        tool_settings = _tool_settings
        if tool_settings is None and source == "local" and model is None:
            tool_settings = self._pending_tool_settings()
        question_origin = "mc" if str(source or "").strip() == "local" else "rc"
        raw_question = str(question or "")
        q = self._strip_attachment_markers(raw_question)
        requested_attachments = list(getattr(self, "_pending_input_attachments", []) or [])
        candidate, candidate_turns, _ = self._chat_target_for_request(chat_id or self._visible_clear_owner())
        selected_tool = (tool_settings or {}).get("tool")
        if (source == "local" and selected_tool and selected_tool != "agy"
                and ((candidate or {}).get("model") == AGY_MODEL_ID or (candidate or {}).get("agy_workspace"))):
            return False, "此聊天使用 AGY。请选回 AGY，或新建聊天后使用所选工具。"
        if (candidate or {}).get("agy_workspace") and not model:
            model = AGY_MODEL_ID
        resolved_model = bindings.app.normalize_model_id(model or self._resolve_current_model() or "")
        if (tool_settings and tool_settings.get("tool") == "agy"
                and is_codex_model((candidate or {}).get("model"))):
            return self._start_agy_handoff(candidate, candidate_turns, q, tool_settings, requested_attachments)
        agy_queued = False
        if resolved_model == AGY_MODEL_ID:
            try:
                local_control = source == "local" and q and not requested_attachments and (candidate or {}).get("id")
                if tool_settings and tool_settings.get("tool") == "agy" and q not in {"/stop", "/reply"}:
                    tool_settings, agy_queued = self._agy_desktop_submission(candidate or {}, candidate_turns, tool_settings)
                if local_control and not agy_queued:
                    control = self._handle_agy_control(candidate, q, local=True)
                    if control is not None:
                        ok, message = control
                        if ok:
                            self.input_edit.ChangeValue("")
                            if message:
                                self.SetStatusText(message)
                        return control
                if requested_attachments and _verified_attachments is None and not agy_queued:
                    return self._preflight_attachment_draft(
                        raw_question, source, resolved_model, chat_id, client_send_id, requested_attachments,
                        agy_context=self._agy_attachment_context(candidate or {}, candidate_turns),
                        tool_settings=tool_settings,
                    )
                self._validate_agy_submission(candidate or {}, queued=agy_queued)
            except AgyError as exc:
                return False, str(exc)
        if requested_attachments and is_openrouter_claude_model(resolved_model) and _verified_attachments is None:
            return self._preflight_attachment_draft(
                raw_question, source, resolved_model, chat_id, client_send_id, requested_attachments,
            )
        prepared_attachments = None
        if _verified_attachments is not None:
            success_attachments, prepared_attachments = _verified_attachments
            failed_attachments = []
        else:
            success_attachments, failed_attachments = self._normalize_outgoing_attachments(requested_attachments)
        outgoing_attachments = success_attachments + failed_attachments
        display_question = q
        if failed_attachments:
            failed_names = "、".join(
                str(item.get("name") or "").strip() for item in failed_attachments
                if str(item.get("name") or "").strip()
            ) or "未知附件"
            # Keep the draft and the attachment list; nothing is submitted
            # until the user removes or repairs the unreadable files.
            return False, f"以下附件无法读取，本次未发送，请移除或修复后重试：{failed_names}"
        if not q and not outgoing_attachments:
            return False, "请输入问题，输入框内容为空"
        if (is_kimi_model(resolved_model) and tool_settings and self._kimi_settings_catalog
                and not tool_settings.get("model")
                and (outgoing_attachments or not self._parse_kimi_local_command(q))):
            return False, "Kimi Code 没有可用的默认模型，请选择模型或检查 Kimi CLI 配置"
        self._answer_list_tail_notice = ""
        self._answer_list_tail_notice_chat_id = ""

        selected_history_id = ""
        if (not chat_id) and self.view_mode == "history":
            selected_history_id = str(self.view_history_id or "").strip()
        submit_owner = str(
            chat_id or selected_history_id or self.active_chat_id or self.current_chat_id or ""
        ).strip()
        if is_codex_model(resolved_model) and submit_owner in getattr(self, "_native_history_reserved", ()):
            return False, "正在同步原生 Codex 历史，请稍后重试"
        if is_kimi_model(resolved_model) and submit_owner in getattr(self, "_native_history_reserved", ()):
            return False, "正在同步原生 Kimi Code 历史，请稍后重试"
        if not submit_owner:
            self._ensure_active_chat_id()
            submit_owner = str(self.active_chat_id or self.current_chat_id or "").strip()
        store = getattr(self, "chat_store", None)
        if (
            submit_owner and not str(getattr(self, "_clear_resend_operation_id", "") or "")
            and getattr(self, "_chat_store_enabled", False) and store is not None
            and self._reconcile_clear_operation_for_owner(submit_owner, surface_recovery=True) is not None
        ):
            return False, "Clear/resend recovery is still pending for this chat."
        current_state_owner = str((self._current_chat_state or {}).get("id") or "").strip()
        if is_claudecode_model(resolved_model) and current_state_owner == submit_owner:
            previous_committed_model = self._model_for_chat_selection(self._current_chat_state) or STARTUP_DEFAULT_MODEL_ID
            try:
                continued = self._send_active_claudecode_input(submit_owner, q)
            except Exception as exc:
                self.selected_model = previous_committed_model
                self.model_combo.SetValue(bindings.app.model_display_name(previous_committed_model))
                return False, str(exc)
            if continued:
                self.selected_model = resolved_model
                self._current_chat_state["model"] = resolved_model
                if bindings.app.threading.current_thread() is bindings.app.threading.main_thread():
                    self.model_combo.SetValue(bindings.app.model_display_name(resolved_model))
                self._defer_chat_state_save()
                if not any(str((item or {}).get("question") or "").strip() for item in self.active_session_turns):
                    self._schedule_first_question_auto_title(submit_owner, q)
                self.input_edit.SetValue("")
                self.input_edit.SetFocus()
                return True, ""
        active_owner = str(self.active_chat_id or self.current_chat_id or "").strip()
        if submit_owner and submit_owner != active_owner:
            if self._find_archived_chat(submit_owner):
                if not self._switch_current_chat(submit_owner):
                    return False, "载入历史聊天失败"
            elif chat_id:
                self.active_chat_id = submit_owner
                self.current_chat_id = submit_owner
        # Fence later persistence, title and provider work to the owner that
        # was resolved above, including submissions originating in history.
        chat_id = submit_owner
        previous_committed_model = self._model_for_chat_selection(self._current_chat_state) or STARTUP_DEFAULT_MODEL_ID
        previous_activity = self._current_chat_state.get("updated_at")

        def rollback_rejected_start(turn_idx: int, exc: Exception) -> tuple[bool, str]:
            if len(self.active_session_turns) > turn_idx:
                del self.active_session_turns[turn_idx:]
            self.active_turn_idx = len(self.active_session_turns) - 1
            self._current_chat_state["model"] = previous_committed_model
            self._current_chat_state["updated_at"] = previous_activity
            self.selected_model = previous_committed_model
            self.model_combo.SetValue(bindings.app.model_display_name(previous_committed_model))
            self.is_running = False
            self._active_request_count = 0
            self._pending_input_attachments = requested_attachments
            self.input_edit.SetValue(raw_question)
            self._defer_chat_state_save()
            self._render_answer_list()
            return False, str(exc)
        if not self.active_session_started_at:
            self.active_session_started_at = bindings.app.time.time()
        self.selected_model = resolved_model
        self.model_combo.SetValue(bindings.app.model_display_name(resolved_model))
        if tool_settings:
            self._refresh_tool_setting_choices(tool_settings)
            self._codex_settings_explicit = tool_settings.get("explicit", False)
        self._current_chat_state["id"] = self.active_chat_id or self.current_chat_id or ""
        self._current_chat_state["model"] = resolved_model
        self._current_chat_state["turns"] = self.active_session_turns
        self._current_chat_state.setdefault("title", self._next_default_chat_title())
        self._current_chat_state.setdefault("title_manual", False)
        self._current_chat_state.setdefault("title_source", "default")
        self._current_chat_state.setdefault("title_updated_at", bindings.app.time.time())
        self._current_chat_state.setdefault("title_revision", 1)
        codex_service_tier = self._current_codex_service_tier() if is_codex_model(resolved_model) else ""
        if is_codex_model(resolved_model):
            self._current_chat_state["codex_service_tier"] = codex_service_tier
            if tool_settings is None:
                self._sync_codex_speed_combo_from_chat(self._current_chat_state)
        worker_question = q
        if (success_attachments and prepared_attachments is None
                and not is_codex_model(resolved_model) and not is_kimi_model(resolved_model)):
            attachment_context = self._build_cli_attachment_context(success_attachments)
            worker_question = f"{q}\n\n{attachment_context}".strip() if q else attachment_context
        codex_local_command = self._parse_codex_local_command(q) if is_codex_model(resolved_model) and not outgoing_attachments else None
        if codex_local_command:
            command_name = str(codex_local_command.get("name") or "").strip()
            command_args = str(codex_local_command.get("args") or "").strip()
            if source == "local" and self._current_chat_state.get("codex_terminal_thread"):
                if command_name == "stop":
                    ok, message = self._stop_shared_codex_turn(self._current_chat_state, self.active_codex_turn_id)
                    if ok:
                        self.input_edit.SetValue("")
                        self.SetStatusText(message)
                    return ok, message
                if command_name == "reply":
                    pending = self._codex_terminal_pending(chat_id)
                    if pending:
                        self.input_edit.SetValue("")
                        self._show_codex_terminal_request(chat_id, pending["interaction_id"])
                        return True, ""
                    return False, "此聊天没有待处理的 Codex 请求。"
            now = bindings.app.time.time()
            turn_idx = len(self.active_session_turns)
            turn = {
                "question": display_question,
                "answer_md": REQUESTING_TEXT,
                "model": resolved_model,
                "created_at": now,
                "question_origin": question_origin,
                "client_send_id": client_send_id,
                "local_command": command_name,
                "local_command_args": command_args,
                "codex_service_tier": codex_service_tier,
            }
            self.active_session_turns.append(turn)
            self.active_turn_idx = turn_idx
            self._mark_chat_turns_dirty(start_index=turn_idx)
            self._reset_answer_visible_row_limit()
            self._reset_current_turn_execution_view()
            self._mark_turn_request_pending(turn, resolved_model, command_name)
            self.is_running = True
            self._active_request_count = max(1, int(getattr(self, "_active_request_count", 0) or 0))
            self.input_edit.SetValue("")
            self.input_edit.SetFocus()
            self._pending_input_attachments = []
            self._defer_chat_state_save()
            self._play_send_sound()
            self.SetStatusText(f"正在执行 Codex 命令：/{command_name}")
            self.view_mode = "active"
            self.view_history_id = None
            self._active_answer_row_index = -1
            if not self._append_submitted_question_to_answer_list(turn_idx, turn):
                if self._detail_panel_mode() == "execution":
                    self._render_answer_list_compat(refresh_execution=False)
                else:
                    self._render_answer_list()
            if source == "local":
                try:
                    started = self._start_codex_local_command_worker_for_turn(
                        chat_id or self.active_chat_id or self.current_chat_id or "",
                        turn_idx,
                        command_name,
                        command_args,
                        resolved_model,
                    )
                    if started is False:
                        raise RuntimeError("Provider rejected submission")
                except Exception as exc:
                    return rollback_rejected_start(turn_idx, exc)
            if turn_idx == 0:
                self._schedule_first_question_auto_title(chat_id or self.active_chat_id, display_question)
            self._record_chat_activity(self._current_chat_state, turn["created_at"])
            return True, ""
        kimi_local_command = self._parse_kimi_local_command(q) if is_kimi_model(resolved_model) and not outgoing_attachments else None
        if kimi_local_command:
            command_name = str(kimi_local_command.get("name") or "").strip()
            command_args = str(kimi_local_command.get("args") or "").strip()
            now = bindings.app.time.time()
            turn_idx = len(self.active_session_turns)
            turn = {
                "question": display_question,
                "answer_md": REQUESTING_TEXT,
                "model": resolved_model,
                "created_at": now,
                "question_origin": question_origin,
                "client_send_id": client_send_id,
                "local_command": command_name,
                "local_command_args": command_args,
            }
            self.active_session_turns.append(turn)
            self.active_turn_idx = turn_idx
            self._mark_chat_turns_dirty(start_index=turn_idx)
            self._reset_answer_visible_row_limit()
            self._reset_current_turn_execution_view()
            self._mark_turn_request_pending(turn, resolved_model, command_name)
            self.is_running = True
            self._active_request_count = max(1, int(getattr(self, "_active_request_count", 0) or 0))
            self.input_edit.SetValue("")
            self.input_edit.SetFocus()
            self._pending_input_attachments = []
            self._defer_chat_state_save()
            self._play_send_sound()
            self.SetStatusText(f"正在执行 Kimi Code 命令：/{command_name}")
            self.view_mode = "active"
            self.view_history_id = None
            self._active_answer_row_index = -1
            if not self._append_submitted_question_to_answer_list(turn_idx, turn):
                if self._detail_panel_mode() == "execution":
                    self._render_answer_list_compat(refresh_execution=False)
                else:
                    self._render_answer_list()
            if source == "local":
                try:
                    started = self._start_kimi_local_command_worker_for_turn(
                        chat_id or self.active_chat_id or self.current_chat_id or "",
                        turn_idx,
                        command_name,
                        command_args,
                        resolved_model,
                    )
                    if started is False:
                        raise RuntimeError("Provider rejected submission")
                except Exception as exc:
                    return rollback_rejected_start(turn_idx, exc)
            if turn_idx == 0:
                self._schedule_first_question_auto_title(chat_id or self.active_chat_id, display_question)
            self._record_chat_activity(self._current_chat_state, turn["created_at"])
            return True, ""
        if (not success_attachments) and (not q):
            now = bindings.app.time.time()
            turn = {
                "question": display_question,
                "answer_md": "",
                "model": resolved_model,
                "created_at": now,
                "question_origin": question_origin,
                "client_send_id": client_send_id,
                "attachments": outgoing_attachments,
                "suppress_empty_answer_row": True,
            }
            self.active_session_turns.append(turn)
            self.active_turn_idx = len(self.active_session_turns) - 1
            self._mark_chat_turns_dirty(start_index=self.active_turn_idx)
            self._reset_answer_visible_row_limit()
            self._reset_current_turn_execution_view()
            self._pending_input_attachments = []
            self.input_edit.SetValue("")
            self.input_edit.SetFocus()
            self._defer_chat_state_save()
            self.view_mode = "active"
            self.view_history_id = None
            self._active_answer_row_index = -1
            if not self._append_submitted_question_to_answer_list(self.active_turn_idx, turn):
                if self._detail_panel_mode() == "execution":
                    self._render_answer_list_compat(refresh_execution=False)
                else:
                    self._render_answer_list()
            self._record_chat_activity(self._current_chat_state, turn["created_at"])
            return True, ""
        if is_openclaw_model(resolved_model):
            self._ensure_active_chat_id()
            openclaw_session_id = self._openclaw_session_id_for_active_chat()
            now = bindings.app.time.time()
            turn = {
                "question": display_question,
                "answer_md": "",
                "model": resolved_model,
                "created_at": now,
                "origin": "local" if source == "local" else source,
                "question_origin": question_origin,
                "client_send_id": client_send_id,
                "attachments": outgoing_attachments,
                "openclaw_session_id": openclaw_session_id,
            }
            self.active_session_turns.append(turn)
            self.active_turn_idx = len(self.active_session_turns) - 1
            self._mark_chat_turns_dirty(start_index=self.active_turn_idx)
            self._reset_answer_visible_row_limit()
            self._reset_current_turn_execution_view()
            self._mark_turn_request_pending(turn, resolved_model, worker_question)
            self.is_running = True
            self.input_edit.SetValue("")
            self.input_edit.SetFocus()
            self._pending_input_attachments = []
            self._defer_chat_state_save()
            self._play_send_sound()
            self.SetStatusText("已发送")
            self.view_mode = "active"
            self.view_history_id = None
            self._active_answer_row_index = -1
            self._refresh_openclaw_sync_lifecycle()
            self._append_submitted_question_to_answer_list(self.active_turn_idx, turn)
            self._launch_generic_worker(("", self.active_turn_idx, worker_question, resolved_model,
                                         False, chat_id or self.active_chat_id, openclaw_session_id))
            if self.active_turn_idx == 0:
                self._schedule_first_question_auto_title(chat_id or self.active_chat_id, display_question)
            self._record_chat_activity(self._current_chat_state, turn["created_at"])
            return True, ""

        turn_idx = len(self.active_session_turns)
        now = bindings.app.time.time()
        turn = {
            "question": display_question,
            "answer_md": REQUESTING_TEXT,
            "model": resolved_model,
            "created_at": now,
            "question_origin": question_origin,
            "client_send_id": client_send_id,
            "attachments": outgoing_attachments,
        }
        if automatic_clear_resend:
            turn["clear_operation_id"] = str(self._clear_resend_operation_id)
            turn["clear_revision"] = getattr(self, "_clear_resend_revision", None)
        if is_codex_model(resolved_model):
            turn["codex_service_tier"] = codex_service_tier
        if tool_settings and tool_settings["tool"] != "other":
            turn["tool_settings"] = tool_settings
        self.active_session_turns.append(turn)
        self.active_turn_idx = turn_idx
        self._mark_chat_turns_dirty(start_index=turn_idx)
        self._reset_answer_visible_row_limit()
        self._reset_current_turn_execution_view()
        self._mark_turn_request_pending(turn, resolved_model, worker_question)
        if is_claudecode_model(resolved_model):
            self.active_claudecode_session_id = str(self.active_claudecode_session_id or "").strip()
        self.is_running = True
        self._active_request_count = max(1, int(getattr(self, "_active_request_count", 0) or 0))
        self.input_edit.SetValue("")
        self.input_edit.SetFocus()
        self._pending_input_attachments = []
        self._defer_chat_state_save()
        if not automatic_clear_resend:
            self._play_send_sound()
        self.SetStatusText("已排队，将在当前回合结束后发送" if agy_queued else "已发送")
        self.view_mode = "active"
        self.view_history_id = None
        self._active_answer_row_index = -1
        self._refresh_openclaw_sync_lifecycle()
        if not self._append_submitted_question_to_answer_list(turn_idx, turn):
            if self._detail_panel_mode() == "execution":
                self._render_answer_list_compat(refresh_execution=False)
            else:
                self._render_answer_list()
        # The transport that submitted the turn must not choose its provider.
        # Remote NATS requests carry the same resolved model IDs as desktop
        # submissions, so CLI-backed models need their dedicated workers too.
        try:
            if is_codex_model(resolved_model):
                started = self._start_codex_worker_for_turn(chat_id or self.active_chat_id or self.current_chat_id or "", turn_idx, q, resolved_model)
            elif is_kimi_model(resolved_model):
                started = self._start_kimi_worker_for_turn(chat_id or self.active_chat_id or self.current_chat_id or "", turn_idx, q, resolved_model)
            elif resolved_model == AGY_MODEL_ID:
                if agy_queued:
                    turn["request_status"] = "queued"
                    self._launch_next_agy_tool_turn(chat_id)
                    started = True
                else:
                    started = self._start_agy_worker_for_turn(
                        chat_id or self.active_chat_id or self.current_chat_id or "", turn_idx, q, resolved_model,
                        prepared_attachments=prepared_attachments,
                    )
            elif is_claudecode_model(resolved_model):
                started = self._start_claudecode_worker_for_turn(
                    chat_id or self.active_chat_id or self.current_chat_id or "",
                    turn_idx,
                    worker_question,
                    self.active_claudecode_session_id,
                    resolved_model,
                )
            else:
                args = (bindings.app.openrouter_api_key_for_app(), turn_idx, worker_question, resolved_model,
                        False, chat_id or self.active_chat_id or self.current_chat_id or "")
                if prepared_attachments:
                    args += ("", prepared_attachments)
                started = self._launch_generic_worker(args)
            if started is False:
                raise RuntimeError("Provider rejected submission")
        except Exception as exc:
            return rollback_rejected_start(turn_idx, exc)
        if turn_idx == 0:
            self._schedule_first_question_auto_title(chat_id or self.active_chat_id, display_question)
        if automatic_clear_resend:
            self._play_clear_resend_accepted_sound(
                str(self._clear_resend_operation_id),
                str(chat_id or self.active_chat_id or self.current_chat_id or ""),
                getattr(self, "_clear_resend_revision", None),
            )
        self._record_chat_activity(self._current_chat_state, turn["created_at"])
        return True, ""

    def _is_model_endpoint_unavailable_error(self, model: str, error_text: str) -> bool:
        checker = getattr(bindings.app.ChatClient, "is_no_endpoint_error", None)
        if callable(checker):
            return bool(checker(error_text, model=model))
        txt = str(error_text or "")
        return ("HTTP 404" in txt) and ("No endpoints found for" in txt) and (model in txt)

    def _candidate_fallback_models(self, failed_model: str) -> list[str]:
        candidates = []
        if failed_model.startswith("deepseek/") and failed_model != "deepseek/deepseek-r1-0528":
            candidates.append("deepseek/deepseek-r1-0528")
        if failed_model != DEFAULT_MODEL_ID:
            candidates.append(DEFAULT_MODEL_ID)
        out = []
        for m in candidates:
            if m in MODEL_IDS and m not in out:
                out.append(m)
        return out

    def _chat_target_for_request(self, chat_id: str) -> tuple[dict | None, list, bool]:
        normalized = str(chat_id or "").strip()
        current_ids = {
            str(self.active_chat_id or "").strip(),
            str(self.current_chat_id or "").strip(),
            str((self._current_chat_state or {}).get("id") or "").strip() if isinstance(getattr(self, "_current_chat_state", None), dict) else "",
            "",
        }
        if normalized in current_ids:
            if not isinstance(getattr(self, "_current_chat_state", None), dict):
                self._current_chat_state = {}
            turns = self.active_session_turns if isinstance(self.active_session_turns, list) else []
            return self._current_chat_state, turns, True
        if normalized:
            chat = self._find_archived_chat(normalized)
            if isinstance(chat, dict):
                turns = chat.get("turns") if isinstance(chat.get("turns"), list) else []
                return chat, turns, False
        return None, [], False

    def _claudecode_session_id_for_worker_chat(self, target_chat: dict | None, is_current_target: bool) -> str:
        if is_current_target:
            return str(self.active_claudecode_session_id or "").strip()
        if isinstance(target_chat, dict):
            return str(target_chat.get("claudecode_session_id") or "").strip()
        return ""

    def _sync_claudecode_session_id_for_worker_chat(self, target_chat: dict | None, is_current_target: bool, session_id: str) -> None:
        value = str(session_id or "").strip()
        if not value:
            return
        if isinstance(target_chat, dict):
            target_chat["claudecode_session_id"] = value
        if is_current_target:
            self.active_claudecode_session_id = value

    def _worker(
        self,
        api_key: str,
        turn_idx: int,
        question: str,
        model: str,
        from_recovery: bool = False,
        chat_id: str = "",
        openclaw_session_id: str = "",
        prepared_attachments=None,
        *,
        owns_turn=None,
    ):
        full = ""
        err = ""
        used_model = model
        fallback_msg = ""
        target_chat, target_turns, is_current_target = self._chat_target_for_request(chat_id)
        if target_chat is None:
            return
        request_owner = str(chat_id or target_chat.get("id") or self.active_chat_id or "").strip()
        owner_is_current = owns_turn or self._bind_provider_turn(request_owner, turn_idx)
        if not owner_is_current():
            return

        def deliver_owned(callback, *args):
            self._post_provider_turn(owner_is_current, callback, *args)

        history_turns = target_turns[:turn_idx] if turn_idx > 0 else []
        outgoing = target_turns[turn_idx].get("attachments") if 0 <= turn_idx < len(target_turns) else None
        has_claude_attachments = bool(is_openrouter_claude_model(model) and (prepared_attachments or outgoing))
        skip_done = False
        try:
            if is_openclaw_model(model):
                session_id = self._openclaw_session_id_for_worker_chat(chat_id, target_chat, is_current_target, openclaw_session_id)
                c = bindings.app.OpenClawClient(model=model, cli_manager=self._cli_agent_manager)
                c.stream_chat(question, session_id=session_id)
                last_context_usage = getattr(c, "last_context_usage", None)
                if last_context_usage and owner_is_current():
                    self._pending_context_usage_by_turn[self._context_usage_pending_key(chat_id, turn_idx)] = last_context_usage
                full = ""
            elif is_codex_model(model):
                self._run_codex_turn_worker(chat_id, turn_idx, question, model, from_recovery=from_recovery)
                skip_done = True
                full = ""
            elif is_claudecode_model(model):
                def on_delta(d):
                    deliver_owned(self._on_delta, turn_idx, d, request_owner)

                def on_user_input(params: dict) -> str:
                    """处理用户输入请求"""
                    from claudecode_remote_protocol import format_remote_user_input_request
                    request_msg = format_remote_user_input_request(params)
                    deliver_owned(self._on_delta, turn_idx, f"\n\n【Claude Code 需要你的输入】\n{request_msg}\n\n", request_owner)
                    return ""

                def on_approval(params: dict) -> str:
                    """处理批准请求"""
                    from claudecode_remote_protocol import format_remote_approval_request
                    request_msg = format_remote_approval_request(params)
                    deliver_owned(self._on_delta, turn_idx, f"\n\n【Claude Code 需要批准】\n{request_msg}\n\n", request_owner)
                    return ""

                client = bindings.app.ClaudeCodeClient(full_auto=True, cli_manager=self._cli_agent_manager)
                full, new_session_id = client.stream_chat(
                    question,
                    session_id=self._claudecode_session_id_for_worker_chat(target_chat, is_current_target),
                    on_delta=on_delta,
                    on_user_input=on_user_input,
                    on_approval=on_approval
                )
                if new_session_id and owner_is_current():
                    self._sync_claudecode_session_id_for_worker_chat(target_chat, is_current_target, new_session_id)
                last_context_usage = getattr(client, "last_context_usage", None)
                if last_context_usage and owner_is_current():
                    self._pending_context_usage_by_turn[self._context_usage_pending_key(chat_id, turn_idx)] = last_context_usage
            else:
                def on_delta(d):
                    deliver_owned(self._on_delta, turn_idx, d, request_owner)

                c = bindings.app.ChatClient(api_key=api_key, model=model)
                if has_claude_attachments:
                    attachments = prepared_attachments or read_openrouter_claude_attachments(model, outgoing)
                    full = c.stream_chat(question, on_delta, history_turns=history_turns, attachments=attachments)
                else:
                    full = c.stream_chat(question, on_delta, history_turns=history_turns)
                if c.last_context_usage and owner_is_current():
                    self._pending_context_usage_by_turn[self._context_usage_pending_key(chat_id, turn_idx)] = c.last_context_usage
        except Exception as e:
            err = str(e)
            if (not has_claude_attachments and not is_openclaw_model(model) and not is_codex_model(model)
                    and self._is_model_endpoint_unavailable_error(model, err)):
                for fb_model in self._candidate_fallback_models(model):
                    try:
                        c = bindings.app.ChatClient(api_key=api_key, model=fb_model)
                        full = c.stream_chat(question, on_delta, history_turns=history_turns)
                        if c.last_context_usage and owner_is_current():
                            self._pending_context_usage_by_turn[self._context_usage_pending_key(chat_id, turn_idx)] = c.last_context_usage
                        used_model = fb_model
                        err = ""
                        fallback_msg = f"模型 {model} 当前不可用，已回退到 {fb_model}"
                        break
                    except Exception as fb_e:
                        err = str(fb_e)
        if skip_done and not err:
            return
        deliver_owned(self._on_done, turn_idx, full, err, used_model, fallback_msg, request_owner)

    def _load_project_folder(self, _event=None):
        dlg = wx.DirDialog(
            self,
            "选择项目文件夹",
            defaultPath=self.active_project_folder or str(Path.cwd()),
            style=wx.DD_DEFAULT_STYLE | wx.DD_DIR_MUST_EXIST,
        )
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return
            project_folder = str(dlg.GetPath() or "").strip()
        finally:
            dlg.Destroy()
        if not project_folder:
            return
        self.active_project_folder = project_folder
        folder_name = Path(project_folder).name or project_folder
        self._add_system_message_to_chat(f"已载入项目文件夹：{project_folder}")
        self._defer_chat_state_save()
        self._render_answer_list()
        self.SetStatusText(f"已载入项目：{folder_name}")

    def _on_new_chat_clicked(self, _, title: str = "", title_manual: bool = False, *, agy_workspace=None):
        if getattr(self, "_closing", False):
            return False
        if agy_workspace is None and self._resolve_current_model() == AGY_MODEL_ID:
            return self._start_desktop_agy_chat(title, title_manual)
        manual_title = str(title or "").strip()
        manual = bool(title_manual and manual_title)
        store = getattr(self, "chat_store", None)
        required_store_methods = ("list_chat_summaries", "upsert_chat", "load_chat", "delete_chat")
        if not bool(getattr(self, "_chat_store_enabled", False)) or store is None or any(
            not callable(getattr(store, name, None)) for name in required_store_methods
        ):
            return False
        restore_keys = [
            key for key in self.__dict__
            if key.startswith("active_") or key in {
                "current_chat_id", "selected_model", "view_mode", "view_history_id",
                "_pending_context_usage_by_turn",
                "_active_claudecode_client", "_chat_state_flush_dirty", "_history_list_dirty",
                "_pending_history_keep_id", "_openclaw_lifecycle_dirty",
            }
            if key != "active_session_turns"
        ]
        original_turns = self.active_session_turns
        original_current_state = self._current_chat_state
        original_current_contents = dict(original_current_state)
        original_archived = self.archived_chats
        original_archived_entries = list(original_archived)
        original_archived_contents = [dict(item) if isinstance(item, dict) else item for item in original_archived_entries]
        snapshot = {}
        for key in restore_keys:
            value = getattr(self, key)
            snapshot[key] = value if isinstance(value, (dict, list, set, tuple)) else copy.deepcopy(value)
        input_value = self.input_edit.GetValue()
        focused_control = wx.Window.FindFocus()
        model_value = self.model_combo.GetValue()
        try:
            allocated_title = self._unique_chat_title(manual_title) if manual else self._next_default_chat_title()
        except Exception:
            return False

        def rollback_new_chat(new_chat_id: str = "") -> bool:
            if new_chat_id:
                try:
                    store.delete_chat(new_chat_id)
                except Exception:
                    pass
            for key, value in snapshot.items():
                setattr(self, key, value)
            original_current_state.clear()
            original_current_state.update(original_current_contents)
            for item, contents in zip(original_archived_entries, original_archived_contents):
                if isinstance(item, dict) and isinstance(contents, dict):
                    item.clear()
                    item.update(contents)
            original_archived[:] = original_archived_entries
            self.active_session_turns = original_turns
            self._current_chat_state = original_current_state
            self._current_chat_state["turns"] = original_turns
            self.archived_chats = original_archived
            self.input_edit.ChangeValue(input_value)
            self.model_combo.SetValue(model_value)
            if focused_control is not None:
                focused_control.SetFocus()
            return False

        previous_codex_service_tier = self._current_codex_service_tier()
        self.view_mode = "active"
        self.view_history_id = None
        self._pending_context_usage_by_turn = {}
        self._codex_early_context_usage_owner = {}
        self._clear_active_claudecode_client()
        archived = self._archive_active_session(
            quick_title=True,
            schedule_async_rename=True,
            save_after_archive=False,
            flush_before_archive=False,
            refresh_lifecycle_after_archive=False,
        )
        self._reset_active_codex_session_state()
        self._reset_active_kimi_session_state()
        self.current_chat_id = ""
        self.active_chat_id = ""
        now = bindings.app.time.time()
        self._current_chat_state = {
            "id": "",
            "title": allocated_title,
            "title_manual": manual,
            "title_source": "manual" if manual else "default",
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
        self.active_chat_id = self._ensure_active_chat_id()
        self.current_chat_id = self.active_chat_id
        self._current_chat_state["id"] = self.active_chat_id
        model = AGY_MODEL_ID if agy_workspace else bindings.app.normalize_model_id(self._resolve_current_model() or DEFAULT_MODEL_ID)
        if agy_workspace:
            self._current_chat_state.update(agy_workspace)
            self.model_combo.SetValue(bindings.app.model_display_name(model))
        self.selected_model = model
        self._current_chat_state["model"] = model
        self._write_active_codex_session_state_to_chat(self._current_chat_state)
        self._write_active_kimi_session_state_to_chat(self._current_chat_state)
        if is_codex_model(model):
            self._current_chat_state["codex_service_tier"] = previous_codex_service_tier
        self._sync_codex_speed_combo_from_chat(self._current_chat_state)
        if is_openclaw_model(self.selected_model):
            self._openclaw_session_id_for_active_chat()
        try:
            store.upsert_chat(self._slim_active_chat_state())
            committed = store.load_chat(self.active_chat_id, include_execution_steps=False)
            if (
                not isinstance(committed, dict)
                or str(committed.get("id") or "").strip() != self.active_chat_id
                or str(committed.get("title") or "").strip() != str(self._current_chat_state.get("title") or "").strip()
                or not str(committed.get("model") or "").strip()
                or str(committed.get("model") or "").strip() != model
                or str(committed.get("title_source") or "").strip() != str(self._current_chat_state.get("title_source") or "").strip()
                or bool(committed.get("title_manual")) != bool(self._current_chat_state.get("title_manual"))
                or int(committed.get("title_revision") or 0) != int(self._current_chat_state.get("title_revision") or 0)
                or float(committed.get("title_updated_at") or 0) != float(self._current_chat_state.get("title_updated_at") or 0)
                or float(committed.get("created_at") or 0) != float(self._current_chat_state.get("created_at") or 0)
                or list(committed.get("turns") or [])
            ):
                return rollback_new_chat(self.active_chat_id)
        except Exception:
            return rollback_new_chat(self.active_chat_id)
        if getattr(self, "_chat_navigation_history_selection_deferred", False):
            self._chat_navigation_history_selection_deferred = False
            self._cancel_chat_navigation_focus()
        self._upsert_history_row(self.active_chat_id)
        self._select_history_row_if_present(self.active_chat_id)
        self._render_answer_list()
        self.input_edit.SetFocus()
        self.SetStatusText("已开始新聊天")
        self._defer_chat_state_save()
        self._mark_openclaw_lifecycle_dirty()
        self._push_remote_history_changed(self.active_chat_id)
        return True
