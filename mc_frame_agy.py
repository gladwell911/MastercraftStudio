"""Workspace selection, session ownership, and chat delivery for AGY."""

import threading
import time

import wx

from agy_acp import AGY_MODEL_ID, AgyAcpClient, AgyError, find_agy_executable, selected_options, validate_workspace


from native_history import NativeSessionIdentity


class AgySessionsMixin:
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
                         native_identity: str = "") -> AgyAcpClient:
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
            client.open_session(session_id)
            return client
        except BaseException:
            client.close()
            raise

    def _remember_agy_workspace(self, client: AgyAcpClient, chat_id: str, cwd: str) -> None:
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

    def _start_agy_worker_for_turn(self, chat_id: str, turn_idx: int, question: str, model: str) -> None:
        chat, turns, _ = self._chat_target_for_request(chat_id)
        cwd = str(chat["agy_workspace"])
        session_id = str(chat["agy_session_id"])
        owner = str(chat.get("agy_native_owner") or "")
        native_identity = str(chat.get("native_identity") or "")
        options = chat.get("agy_options") or []
        config = dict(chat.get("agy_config") or {})
        turn = turns[turn_idx]
        turn["request_recoverable"] = False
        turn["request_resume_token"] = {"session_id": session_id}
        owns_turn = self._bind_provider_turn(chat_id, turn_idx)
        generation = object()
        self._agy_busy[chat_id] = generation
        client = self._agy_clients.get(chat_id)
        updates = []
        lock = threading.Lock()
        last_delivery = 0.0
        timer = None

        def flush():
            nonlocal timer, last_delivery
            with lock:
                pending = "".join(updates)
                updates.clear()
                timer = None
                last_delivery = time.monotonic()
            if pending:
                self._post_provider_turn(owns_turn, self._deliver_agy_delta, chat_id, turn_idx, pending)

        def on_update(update):
            nonlocal timer
            content = update.get("content")
            if update.get("sessionUpdate") != "agent_message_chunk" or not isinstance(content, dict):
                return
            text = content.get("text")
            if not isinstance(text, str) or not text:
                return
            full.append(text)
            with lock:
                updates.append(text)
                if timer is None:
                    timer = threading.Timer(max(0, 0.1 - (time.monotonic() - last_delivery)), flush)
                    timer.daemon = True
                    timer.start()

        def finish(active_client, answer, error):
            if self._agy_busy.get(chat_id) is not generation:
                if active_client is not None:
                    active_client.close()
                return
            self._agy_busy.pop(chat_id)
            if active_client is not None:
                self._agy_clients[chat_id] = active_client
            if owns_turn():
                self._on_done(turn_idx, answer, error, model, "", chat_id)

        full = []

        def work():
            active_client = client
            error = ""
            try:
                validate_workspace(cwd)
                # An independently run terminal can advance the persisted
                # transcript while an idle ACP process still has the old copy.
                if turn_idx > 0 and active_client is not None:
                    active_client.close()
                    active_client = None
                if active_client is None or not active_client.alive:
                    active_client = self._open_agy_client(
                        cwd, session_id=session_id, owner=owner, native_identity=native_identity)
                elif (active_client.session_id != session_id
                      or active_client.native_owner(self._read_pair_id()) != owner
                      or (native_identity and self._agy_native_identity(active_client, session_id) != native_identity)):
                    raise AgyError("AGY 原会话归属已变化，请重新打开聊天")
                active_client.configure(config, options)
                reason = active_client.prompt(question, on_update)
                if reason != "end_turn":
                    raise AgyError(f"AGY 回合已结束：{reason or '缺少终态'}")
            except AgyError as exc:
                error = str(exc)
                if active_client is not None:
                    active_client.close()
                    active_client = None
            finally:
                with lock:
                    pending_timer = timer
                if pending_timer is not None:
                    pending_timer.cancel()
                    pending_timer.join()
                flush()
                if not self._call_after_if_alive(finish, active_client, "".join(full), error):
                    if active_client is not None:
                        active_client.close()

        threading.Thread(target=work, name="agy-prompt", daemon=True).start()

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
        clients = list(self._agy_clients.values())
        self._agy_clients.clear()
        for client in clients:
            client.close()
