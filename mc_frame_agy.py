"""Workspace selection, session ownership, and chat delivery for AGY."""

from dataclasses import dataclass, field
import threading
import time
import uuid

import wx

from agy_acp import (
    AGY_MODEL_ID, AgyAcpClient, AgyError, AgyResultUnknown, find_agy_executable,
    read_prompt_attachments, selected_options, validate_workspace,
)
from agy_execution import AgyExecution, interaction_questions, interaction_result


from native_history import NativeSessionIdentity


@dataclass
class AgyRun:
    turn_idx: int
    owns_turn: object
    scope: tuple
    turn_id: str = field(default_factory=lambda: "agy-" + uuid.uuid4().hex)
    action_id: str = ""
    client: AgyAcpClient | None = None
    prompt: object = None
    worker: threading.Thread | None = None
    execution: AgyExecution = field(default_factory=AgyExecution)
    interactions: dict = field(default_factory=dict)
    accepted_actions: dict = field(default_factory=dict)
    dialog: wx.Dialog | None = None

    def __post_init__(self):
        self.action_id = self.turn_id


@dataclass
class AgyPreparedAttachments:
    scope: tuple
    client: AgyAcpClient
    blocks: list
    fresh_client: bool
    _available: bool = True
    _lock: object = field(default_factory=threading.Lock)

    def adopt(self, scope):
        with self._lock:
            if not self._available or scope != self.scope or not self.client.alive:
                raise AgyError("AGY 会话已变化，请重新发送附件")
            self._available = False

    def close_unused(self):
        with self._lock:
            close = self._available and self.fresh_client
            self._available = False
        if close:
            self.client.close()


class AgySessionsMixin:
    @staticmethod
    def _agy_session_id_for_chat(chat):
        return str(chat.get("agy_session_id") or "")

    @staticmethod
    def _agy_chat_scope(chat):
        return tuple(chat.get(key) for key in
                     ("model", "agy_session_id", "agy_native_owner", "native_identity", "native_account", "agy_workspace"))

    def _agy_run_for_chat(self, chat):
        if not isinstance(chat, dict):
            return None
        run = self._agy_busy.get(chat.get("id"))
        return run if run and run.scope == self._agy_chat_scope(chat) and run.owns_turn() else None

    def _agy_workspace_preference_key(self) -> str:
        return f"agy-workspace:{self._read_pair_id()}"

    def _agy_workspace_descriptor(self) -> dict:
        stored = self.chat_store.get_meta(self._agy_workspace_preference_key())
        try:
            cwd = validate_workspace(stored) if stored else ""
        except AgyError:
            cwd = ""
        return {
            "version": 1, "cwd": cwd, "pair_id": self._read_pair_id(),
            "available": bool(find_agy_executable()),
        }

    def _prepare_agy_workspace(self, cwd: str) -> tuple[AgyAcpClient, dict]:
        cwd = validate_workspace(cwd)
        client = self._open_agy_client(cwd)
        return client, {
            "agy_workspace": cwd,
            "agy_session_id": client.session_id,
            "agy_native_owner": client.native_owner(self._read_pair_id()),
            "native_identity": self._agy_native_identity(client, client.session_id),
            "native_account": client.native_account(),
            "native_cwd": cwd,
            "agy_options": client.options,
            "agy_config": {},
        }

    def _agy_native_identity(self, client: AgyAcpClient, session_id: str) -> str:
        return NativeSessionIdentity(self._read_pair_id(), "agy", client.native_storage(), session_id).chat_id

    def _open_agy_client(self, cwd: str, *, session_id: str = "", owner: str = "",
                         native_identity: str = "", chat_id: str = "") -> AgyAcpClient:
        executable = find_agy_executable()
        if not executable:
            raise AgyError("此电脑尚未安装 AGY，请安装或设置 MC_AGY_EXECUTABLE")
        client = AgyAcpClient(executable, cwd)
        try:
            client.initialize()
            if session_id and (
                owner != client.native_owner(self._read_pair_id())
                or (native_identity and self._agy_native_identity(client, session_id) != native_identity)
            ):
                raise AgyError("此聊天属于原运行电脑或 AGY 登录账号，请恢复原来的账号")
            if session_id:
                client.check_original_session_busy(session_id)
            if chat_id:
                self._watch_agy_title(client, chat_id)
            client.open_session(session_id)
            return client
        except BaseException:
            client.close()
            raise

    def _watch_agy_title(self, client, chat_id):
        def receive(session_id, update):
            title = update.get("title")
            if isinstance(title, str):
                self._call_after_if_alive(
                    self._observe_native_title_ui, chat_id, "agy", session_id, title,
                    time.time(), native_identity=self._agy_native_identity(client, session_id),
                )
        client.on_session_info = receive

    def _remember_agy_workspace(self, client: AgyAcpClient, chat_id: str, cwd: str) -> None:
        self._watch_agy_title(client, chat_id)
        self.chat_store.set_meta(self._agy_workspace_preference_key(), cwd)
        self._agy_clients[chat_id] = client

    def _choose_agy_workspace(self, title: str, title_manual: bool) -> bool:
        if self._agy_new_chat_pending:
            return False
        if not find_agy_executable():
            self.SetStatusText("此电脑尚未安装 AGY，请安装或设置 MC_AGY_EXECUTABLE")
            return False
        descriptor = self._agy_workspace_descriptor()
        with wx.DirDialog(self, "选择 AGY 项目目录", defaultPath=descriptor["cwd"],
                          style=wx.DD_DEFAULT_STYLE | wx.DD_DIR_MUST_EXIST) as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return False
            cwd = dialog.GetPath()
        self._agy_new_chat_pending = True
        self.SetStatusText("正在打开 AGY 项目")

        def finish(result, error):
            self._agy_new_chat_pending = False
            if error:
                self.SetStatusText(error)
                return
            client, fields = result
            committed = False
            try:
                fields["agy_config"] = self._choose_agy_configuration(fields["agy_options"])
                if fields["agy_config"] is None:
                    return
                committed = self._on_new_chat_clicked(None, title=title, title_manual=title_manual,
                                                      agy_workspace=fields)
                if committed:
                    cwd = fields["agy_workspace"]
                    self._remember_agy_workspace(client, self.active_chat_id, cwd)
                    self.SetStatusText(f"已开始 AGY 聊天，项目目录：{cwd}")
            finally:
                if not committed:
                    client.close()

        def prepare():
            try:
                result = self._prepare_agy_workspace(cwd)
            except AgyError as exc:
                self._call_after_if_alive(finish, None, str(exc))
            else:
                if not self._call_after_if_alive(finish, result, ""):
                    result[0].close()

        threading.Thread(target=prepare, name="agy-new-chat", daemon=True).start()
        return True

    def _choose_agy_configuration(self, options: list) -> dict | None:
        if not options:
            return {}
        choices = []
        with wx.Dialog(self, title="AGY 会话配置") as dialog:
            layout = wx.BoxSizer(wx.VERTICAL)
            for field in options:
                layout.Add(wx.StaticText(dialog, label=field["label"]), 0, wx.ALL, 5)
                selection = wx.Choice(dialog, choices=[value["label"] for value in field["options"]])
                selection.SetName(field["label"])
                values = [value["value"] for value in field["options"]]
                if field["current"] in values:
                    selection.SetSelection(values.index(field["current"]))
                layout.Add(selection, 0, wx.EXPAND | wx.ALL, 5)
                choices.append((field, selection))
            layout.Add(dialog.CreateSeparatedButtonSizer(wx.OK | wx.CANCEL), 0, wx.EXPAND | wx.ALL, 10)
            dialog.SetSizerAndFit(layout)
            choices[0][1].SetFocus()
            if dialog.ShowModal() != wx.ID_OK:
                return None
            return {field["id"]: field["options"][choice.GetSelection()]["value"]
                    for field, choice in choices if choice.GetSelection() != wx.NOT_FOUND
                    and field["options"][choice.GetSelection()]["value"] != field["current"]}

    def _remote_api_agy_configure_ui(self, payload: dict) -> tuple[int, dict]:
        chat_id = str(payload.get("chat_id") or "")
        chat, turns, _ = self._chat_target_for_request(chat_id)
        if not chat_id or not isinstance(chat, dict) or chat.get("model") != AGY_MODEL_ID:
            return 404, {"accepted": False, "message": "找不到此电脑上的 AGY 聊天"}
        if turns or chat.get("turn_count") or chat_id in self._agy_busy:
            return 409, {"accepted": False, "message": "此 AGY 聊天已经开始执行"}
        try:
            config = selected_options(chat.get("agy_options") or [], payload.get("agy_config"))
        except AgyError as exc:
            return 400, {"accepted": False, "message": str(exc)}
        chat["agy_config"] = config
        self.chat_store.upsert_chat(chat)
        return 200, {"accepted": True, "chat_id": chat_id}

    def _validate_agy_submission(self, chat: dict) -> str:
        chat_id = str(chat.get("id") or "")
        if chat_id in self._native_history_reserved:
            raise AgyError("此 AGY 聊天正在更新原生历史，请稍后重试")
        if chat_id in self._agy_busy:
            raise AgyError("此 AGY 聊天正在执行，请等待当前回答完成")
        if not chat.get("agy_session_id"):
            raise AgyError("请先为 AGY 新建聊天并选择项目目录")
        return validate_workspace(str(chat.get("agy_workspace") or ""))

    def _agy_attachment_context(self, chat, turns):
        cwd = self._validate_agy_submission(chat)
        client = self._agy_clients.get(chat["id"]) if not turns else None
        return cwd, self._agy_chat_scope(chat), client if client and client.alive else None

    def _prepare_agy_attachments(self, context, files):
        cwd, scope, client = context
        _, session_id, owner, native_identity, _, _ = scope
        fresh = client is None
        if fresh:
            client = self._open_agy_client(cwd, session_id=session_id, owner=owner, native_identity=native_identity)
        try:
            return AgyPreparedAttachments(scope, client, read_prompt_attachments(client.capabilities, files), fresh)
        except BaseException:
            if fresh:
                client.close()
            raise

    def _start_agy_worker_for_turn(self, chat_id: str, turn_idx: int, question: str, model: str, *,
                                   prepared_attachments=None) -> None:
        chat, turns, _ = self._chat_target_for_request(chat_id)
        cwd = str(chat["agy_workspace"])
        session_id = self._agy_session_id_for_chat(chat)
        owner = str(chat.get("agy_native_owner") or "")
        native_identity = str(chat.get("native_identity") or "")
        options = chat.get("agy_options") or []
        config = dict(chat.get("agy_config") or {})
        turn = turns[turn_idx]
        turn["request_recoverable"] = False
        turn["request_resume_token"] = {"session_id": session_id}
        run = AgyRun(turn_idx, self._bind_provider_turn(chat_id, turn_idx), self._agy_chat_scope(chat))
        if prepared_attachments is not None:
            prepared_attachments.adopt(run.scope)
        self._agy_busy[chat_id] = run
        client = self._agy_clients.get(chat_id)
        full = []
        batch = []
        lock = threading.Lock()
        timer = None

        def flush():
            nonlocal timer
            with lock:
                if batch:
                    self._call_after_if_alive(self._deliver_agy_events, chat_id, run, tuple(batch))
                    batch.clear()
                timer = None

        def enqueue(kind, value):
            nonlocal timer
            with lock:
                batch.append((kind, value))
                if timer is None:
                    timer = threading.Timer(0.075, flush)
                    timer.daemon = True
                    timer.start()

        def on_update(update):
            if not isinstance(update, dict):
                return
            kind = update.get("sessionUpdate")
            native_ids = {}
            turn_id, message_id = update.get("turnId"), update.get("messageId")
            if isinstance(turn_id, str) and turn_id and kind in {
                "user_message_chunk", "agent_message_chunk", "agent_thought_chunk", "tool_call", "tool_call_update",
            }:
                native_ids["agy_native_turn_id"] = turn_id
            if isinstance(message_id, str) and message_id:
                if kind == "user_message_chunk":
                    native_ids["provider_user_message_id"] = message_id
                elif kind == "agent_message_chunk":
                    native_ids["agy_observed_message_id"] = message_id
            if native_ids:
                enqueue("native_ids", native_ids)
            if kind == "agent_message_chunk":
                content = update.get("content") or {}
                text = content.get("text") if isinstance(content, dict) else None
                if isinstance(text, str) and text:
                    full.append(text)
                    enqueue("delta", text)
            else:
                enqueue("update", update)

        def on_interaction(interaction_id, method, params):
            enqueue("interaction", ("agy-" + interaction_id, method, params))

        def finish(active_client, answer, reason, error, result_unknown):
            current_chat, current_turns, _ = self._chat_target_for_request(chat_id)
            owns = self._agy_run_for_chat(current_chat) is run
            if self._agy_busy.get(chat_id) is run:
                self._agy_busy.pop(chat_id)
            self._dismiss_agy_dialog(run)
            if active_client is not None:
                if owns:
                    self._agy_clients[chat_id] = active_client
                else:
                    active_client.close()
            if owns:
                if reason == "cancelled":
                    current_turns[turn_idx].update(request_status="cancelled", request_error="AGY 回合已停止。")
                    error = "AGY 回合已停止。"
                elif reason != "end_turn" and not error:
                    error = f"AGY 回合已结束：{reason or '缺少终态'}"
                turn = current_turns[turn_idx]
                if not error and reason == "end_turn" and turn.get("agy_observed_message_id"):
                    turn["provider_message_id"] = turn["agy_observed_message_id"]
                if result_unknown:
                    current_chat["native_history_status"] = error
                    current_chat["native_unresolved_turn"] = {"index": turn_idx, "message": error}
                self._invalidate_remote_state_cache()
                self._on_done(turn_idx, answer, error, model, "", chat_id, result_unknown=result_unknown)
                if error and self._visible_clear_owner() == chat_id:
                    self.SetStatusText(error)

        def work():
            active_client = prepared_attachments.client if prepared_attachments is not None else client
            reason = error = ""
            result_unknown = False
            try:
                workspace = validate_workspace(cwd)
                # Idle ACP processes do not see work accepted by a separate terminal.
                if prepared_attachments is not None and client is not None and client is not active_client:
                    client.close()
                elif prepared_attachments is None and turn_idx > 0 and active_client is not None:
                    active_client.close()
                    active_client = None
                if active_client is None or not active_client.alive:
                    if prepared_attachments is not None:
                        raise AgyError("用于检查附件的 AGY 连接已退出，请重新发送")
                    active_client = self._open_agy_client(
                        workspace, session_id=session_id, owner=owner, native_identity=native_identity, chat_id=chat_id)
                elif (active_client.session_id != session_id
                      or active_client.native_owner(self._read_pair_id()) != owner
                      or (native_identity and self._agy_native_identity(active_client, session_id) != native_identity)):
                    raise AgyError("AGY 原会话归属已变化，请重新打开聊天")
                else:
                    active_client.check_original_session_busy(session_id)
                run.client = active_client
                self._watch_agy_title(active_client, chat_id)
                active_client.configure(config, options)
                if getattr(self, "_closing", False):
                    raise AgyError("MC 正在退出")
                run.prompt = active_client.begin_prompt(
                    question, on_update, on_interaction,
                    attachments=prepared_attachments.blocks if prepared_attachments is not None else (),
                )
                reason = run.prompt.wait()
            except AgyError as exc:
                result_unknown = isinstance(exc, AgyResultUnknown)
                error = str(exc)
                if active_client is not None:
                    active_client.close()
                    active_client = None
            finally:
                with lock:
                    pending_timer = timer
                if pending_timer is not None:
                    pending_timer.cancel()
                flush()
                if not self._call_after_if_alive(finish, active_client, "".join(full), reason, error, result_unknown):
                    if active_client is not None:
                        active_client.close()

        try:
            run.worker = threading.Thread(target=work, name="agy-prompt", daemon=True)
            run.worker.start()
        except Exception:
            self._agy_busy.pop(chat_id, None)
            if prepared_attachments is not None and prepared_attachments.fresh_client:
                prepared_attachments.client.close()
            raise

    def _deliver_agy_events(self, chat_id, run, batch):
        chat, turns, _ = self._chat_target_for_request(chat_id)
        if self._agy_run_for_chat(chat) is not run:
            return
        delta = "".join(value for kind, value in batch if kind == "delta")
        steps = {}
        native_ids = {}
        for kind, value in batch:
            if kind == "interaction":
                self._receive_agy_interaction(chat_id, run, *value)
            elif kind == "native_ids":
                native_ids.update(value)
            elif kind == "update" and (step := run.execution.step(value)):
                steps[step[0]] = step
        turn = turns[run.turn_idx]
        if any(turn.get(key) != value for key, value in native_ids.items()):
            turn.update(native_ids)
            self._mark_chat_turns_dirty(chat_id, run.turn_idx)
            self._defer_chat_state_save()
        for step in steps.values():
            self._append_agy_step(chat_id, run, *step)
        if delta:
            self._deliver_agy_delta(chat_id, run.turn_idx, delta)

    def _append_agy_step(self, chat_id, run, item_id, kind, label, details):
        self._append_execution_entry_to_chat(chat_id, {
            "provider": "agy", "thread_id": run.scope[1], "turn_id": run.turn_id, "turn_idx": run.turn_idx,
            "item_id": item_id, "event_type": kind, "display_kind": kind, "list_text": label, "detail_text": details,
            "narrative_snapshot": True,
        }, save_state=False)

    def _handle_agy_control(self, chat, text, *, expected="", send_id="", local=False):
        run = self._agy_run_for_chat(chat)
        chat_id = chat["id"]
        if not run:
            if expected or text in {"/stop", "/reply"}:
                return False, "此 AGY 回合已结束或已更换。"
            return None
        if local and text == "/reply":
            pending = next(iter(run.interactions), "")
            if not pending:
                return False, "此 AGY 聊天没有待处理的请求。"
            self._show_agy_interaction(chat_id, pending)
            return True, ""
        if send_id and send_id in run.accepted_actions:
            return ((True, "") if run.accepted_actions[send_id] == (expected, text)
                    else (False, "此 AGY 提交标识已经使用。"))
        if not local and expected != run.action_id:
            return False, "此 AGY 回合已结束或已更换，请刷新后重试。"
        if run.client is None:
            return False, "正在连接 AGY 回合，请稍后重试。"
        try:
            cancelled = text == "/stop"
            accepted = (run.client.cancel_prompt(run.prompt) if cancelled
                        else run.client.steer_prompt(run.prompt, text))
        except AgyError as exc:
            return False, f"AGY 操作结果未知：{exc}"
        if not accepted:
            return False, "此 AGY 回合已结束、正在停止或已更换。"
        if send_id:
            run.accepted_actions[send_id] = (expected, text)
        run.interactions.clear()
        self._dismiss_agy_dialog(run)
        if not cancelled:
            run.action_id = "agy-" + uuid.uuid4().hex
            self._append_agy_step(chat_id, run, run.action_id, "followup", "追加指令：" + text[:150], text)
        self._update_agy_remote_state(chat_id)
        return True, "已请求停止 AGY。" if cancelled else "已向 AGY 追加指令。"

    def _update_agy_remote_state(self, chat_id):
        self._invalidate_remote_state_cache()
        self._push_remote_state(chat_id)

    def _receive_agy_interaction(self, chat_id, run, interaction_id, method, params):
        if not run.client or not run.client.interaction_active(run.prompt, interaction_id[4:]):
            return
        prompt, questions = interaction_questions(method, params)
        if not questions:
            try:
                run.client.decline_interaction(run.prompt, interaction_id[4:], method)
            except AgyError as exc:
                if self._visible_clear_owner() == chat_id:
                    self.SetStatusText(str(exc))
            return
        run.interactions[interaction_id] = (method, prompt, questions)
        self._push_remote_attention(chat_id, "action_required", interaction_id, "AGY 需要您的确认或输入")
        self._update_agy_remote_state(chat_id)
        if self.IsShownOnScreen() and self._is_foreground_window() and self._visible_clear_owner() == chat_id:
            self._call_after_if_alive(self._show_agy_interaction, chat_id, interaction_id, only_foreground=True)

    def _agy_interaction_payload(self, chat_id, run):
        if not run or not run.interactions:
            return None
        interaction_id, (method, prompt, questions) = next(iter(run.interactions.items()))
        user_input = method == "elicitation/create"
        public_questions = [{key: value for key, value in question.items() if key not in {"values", "value_type"}}
                            for question in questions]
        return {
            "type": "request", "chat_id": chat_id, "request_kind": "user_input" if user_input else "approval",
            "prompt": prompt, "event_id": interaction_id,
            "body": {"request_id": 0, "method": method, "interaction_id": interaction_id,
                     "params": {"questions" if user_input else "options": public_questions if user_input
                                else public_questions[0]["options"]}},
        }

    def _reply_agy_interaction(self, chat_id, interaction_id, *, text="", answers=None):
        chat, _, _ = self._chat_target_for_request(chat_id)
        run = self._agy_run_for_chat(chat)
        request = run.interactions.get(interaction_id) if run else None
        if not request or interaction_id != next(iter(run.interactions)):
            return False, "此 AGY 请求已处理或过期。"
        method, _, questions = request
        try:
            response = interaction_result(method, questions, text, answers)
        except ValueError as exc:
            return False, str(exc)
        try:
            accepted = run.client.reply_interaction(run.prompt, interaction_id[4:], response)
        except AgyError as exc:
            run.interactions.pop(interaction_id, None)
            self._dismiss_agy_dialog(run)
            self._update_agy_remote_state(chat_id)
            return False, f"AGY 回复结果未知：{exc}"
        run.interactions.pop(interaction_id, None)
        self._dismiss_agy_dialog(run)
        self._update_agy_remote_state(chat_id)
        return (True, "已回复 AGY。") if accepted else (False, "此 AGY 请求已处理或过期。")

    def _show_agy_interaction(self, chat_id, interaction_id, *, only_foreground=False):
        if only_foreground and not self._is_foreground_window():
            return
        chat, _, _ = self._chat_target_for_request(chat_id)
        run = self._agy_run_for_chat(chat)
        if (not run or run.dialog is not None or self._visible_clear_owner() != chat_id
                or interaction_id != next(iter(run.interactions), None)):
            return
        method, prompt, questions = run.interactions[interaction_id]
        approval = method == "session/request_permission"
        with wx.Dialog(self, title="AGY 工具审批" if approval else "AGY 补充问题") as dialog:
            layout = wx.BoxSizer(wx.VERTICAL)
            heading = wx.StaticText(dialog, label=prompt)
            heading.Wrap(540)
            layout.Add(heading, 0, wx.EXPAND | wx.ALL, 8)
            controls = []
            for question in questions:
                label = question.get("header") or question["question"]
                if not approval:
                    layout.Add(wx.StaticText(dialog, label=label), 0, wx.EXPAND | wx.ALL, 8)
                    if question["question"]:
                        layout.Add(wx.StaticText(dialog, label=question["question"]), 0, wx.EXPAND | wx.ALL, 8)
                options = question["options"]
                if options:
                    control = wx.Choice(dialog, choices=[option["label"] for option in options], name=label)
                else:
                    control = wx.TextCtrl(dialog, name=label)
                controls.append((question["id"], options, control))
                layout.Add(control, 0, wx.EXPAND | wx.ALL, 8)
            layout.Add(dialog.CreateSeparatedButtonSizer(wx.OK | wx.CANCEL), 0, wx.EXPAND | wx.ALL, 8)
            dialog.SetSizerAndFit(layout)
            controls[0][2].SetFocus()
            run.dialog = dialog
            try:
                while dialog.ShowModal() == wx.ID_OK:
                    answers = {}
                    for key, options, control in controls:
                        if not options:
                            answers[key] = control.GetValue()
                        elif (index := control.GetSelection()) != wx.NOT_FOUND:
                            answers[key] = options[index]["value"]
                    ok, message = self._reply_agy_interaction(
                        chat_id, interaction_id, text=answers.get("approval", ""), answers=answers,
                    )
                    self.SetStatusText(message)
                    if ok or interaction_id not in run.interactions:
                        break
                    wx.MessageBox(message, "AGY", wx.OK | wx.ICON_WARNING, parent=dialog)
            finally:
                run.dialog = None
                next_request = next(iter(run.interactions), None)
                if next_request and next_request != interaction_id:
                    self._call_after_if_alive(self._show_agy_interaction, chat_id, next_request)

    @staticmethod
    def _dismiss_agy_dialog(run):
        if run.dialog is not None and run.dialog.IsModal():
            run.dialog.EndModal(wx.ID_CANCEL)

    def _deliver_agy_delta(self, chat_id: str, turn_idx: int, delta: str) -> None:
        _, turns, _ = self._chat_target_for_request(chat_id)
        if not 0 <= turn_idx < len(turns) or turns[turn_idx].get("request_status") != "pending":
            return
        self._on_delta_for_chat(turn_idx, delta, chat_id)
        if chat_id in {self.active_chat_id, self.current_chat_id} and self.view_mode == "active":
            if not self._navigation_quiet_active() and self._find_answer_row_index(turn_idx) < 0:
                self._append_completed_answer_to_answer_list(turn_idx, self.active_session_turns[turn_idx])
        self._invalidate_remote_state_cache()
        self._push_remote_state(chat_id)

    def _close_agy_clients(self) -> None:
        runs = tuple(self._agy_busy.values())
        clients = set(self._agy_clients.values())
        clients.update(run.client for run in runs if run.client is not None)
        self._agy_clients.clear()
        for client in clients:
            client.close()
        for run in runs:
            if run.prompt is not None and run.worker is not None:
                # Close can end an admitted prompt. Give its final identity
                # batch and typed result time to cross the callback boundary.
                run.worker.join(timeout=.5)
