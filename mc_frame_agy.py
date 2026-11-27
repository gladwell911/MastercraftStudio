"""Workspace selection, session ownership, and chat delivery for AGY."""

from dataclasses import dataclass, field
import sqlite3
import subprocess
import threading
import time
import uuid
from pathlib import Path

import wx
import agy_settings

from agy_acp import (
    AGY_MODEL_ID, AgyAcpClient, AgyError, AgyResultUnknown, find_agy_executable,
    read_prompt_attachments, selected_options, validate_workspace,
)
from agy_execution import AgyExecution, interaction_questions, interaction_result
from agy_information import agy_context_tokens, agy_usage_owner
from codex_client import is_codex_model
from mc_frame_settings import REQUESTING_TEXT
from mc_frame_tool_settings import EFFORT_NAMES


from native_history import NativeSessionIdentity


AGY_CHAT_SCOPE_FIELDS = ("model", "agy_session_id", "agy_native_owner", "native_identity",
                         "native_account", "agy_workspace")
HANDOFF_UNKNOWN = "AGY 首次提交的结果待确认。请同步原 AGY 会话以核对这次提问。"
HANDOFF_OVERFLOW = "完整交接内容超出 AGY 的输入上限，本次未发送。原聊天、草稿和附件已保留。"


@dataclass
class AgyHandoff:
    source_id: str
    draft: str
    attachments: list
    cutoff: float
    completed_indexes: frozenset | None
    cancel: threading.Event = field(default_factory=threading.Event)
    preparing: bool = True
    run: object = None
    worker: threading.Thread | None = None


def _agy_update_ids(update):
    ids = {}
    kind = update.get("sessionUpdate")
    turn_id, message_id = update.get("turnId"), update.get("messageId")
    if isinstance(turn_id, str) and turn_id and kind in {
        "user_message_chunk", "agent_message_chunk", "agent_thought_chunk", "tool_call", "tool_call_update",
    }:
        ids["agy_native_turn_id"] = turn_id
    if isinstance(message_id, str) and message_id:
        if kind == "user_message_chunk":
            ids["provider_user_message_id"] = message_id
        elif kind == "agent_message_chunk":
            ids["agy_observed_message_id"] = message_id
    return ids


def _agy_terminal_values(native_ids, answer, reason, pair_id, statistics):
    cancelled = reason == "cancelled"
    error = "AGY 回合已停止。" if cancelled else ""
    terminal = dict(native_ids, agy_native_terminal_reason=reason, terminal_notification_pair=pair_id,
                    request_status="cancelled" if cancelled else "done",
                    request_error=error, request_recovered_after_restart=False,
                    answer_md=error if cancelled else (answer if answer.strip() else "未返回任何内容。"))
    terminal.update(statistics)
    if not cancelled:
        terminal["answer_at"] = time.time()
        if native_ids.get("agy_observed_message_id"):
            terminal["provider_message_id"] = native_ids["agy_observed_message_id"]
    return terminal


@dataclass
class AgyRun:
    turn_idx: int
    owns_turn: object
    scope: tuple
    settings: dict | None = None
    turn_id: str = field(default_factory=lambda: "agy-" + uuid.uuid4().hex)
    action_id: str = ""
    client: AgyAcpClient | None = None
    prompt: object = None
    worker: threading.Thread | None = None
    execution: AgyExecution = field(default_factory=AgyExecution)
    interactions: dict = field(default_factory=dict)
    accepted_actions: dict = field(default_factory=dict)
    dialog: wx.Dialog | None = None
    terminal_rejected: bool = False
    saved_chat: dict | None = None

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
    def _start_agy_handoff(self, source, visible_turns, question, settings, attachments):
        source_id = source["id"]
        pending = getattr(self, "_agy_handoff", None)
        if pending is not None:
            if pending.source_id == source_id and question in {"/stop", "/reply"}:
                run = pending.run
                if question == "/reply":
                    interaction_id = next(iter(run.interactions), None) if run else None
                    if not interaction_id:
                        return False, "此 AGY 接续没有待处理的请求。"
                    self._show_agy_interaction(source_id, interaction_id, handoff=run)
                else:
                    pending.cancel.set()
                    if run and run.prompt:
                        try:
                            run.client.cancel_prompt(run.prompt)
                        except AgyError as exc:
                            return False, f"AGY 停止结果待确认：{exc}"
                    self.SetStatusText("已请求停止 AGY 接续")
                self.input_edit.ChangeValue(pending.draft)
                return True, ""
            return False, "正在确认 AGY 首次提问，请等待结果。需要停止时可输入 /stop。"
        if not question and not attachments:
            return False, "请输入问题，输入框内容为空"
        if source_id in self._native_history_reserved:
            return False, "正在同步原生 Codex 历史，请稍后重试"
        now = time.time()
        completed = (frozenset(index for index, turn in enumerate(visible_turns)
                               if turn.get("request_status") in (None, "", "done")
                               and not turn.get("terminal_final_pending"))
                     if visible_turns or isinstance(source.get("turns"), list) else None)
        pending = AgyHandoff(source_id, self.input_edit.GetValue(), [dict(item) for item in attachments],
                             now, completed)
        source_scope = {key: source.get(key) for key in ("model", "native_cwd", "codex_thread_id")}
        chat = {
            "id": str(uuid.uuid4()), "model": AGY_MODEL_ID,
            "title_source": "default", "title_revision": 1, "title_updated_at": now,
            "created_at": now, "updated_at": now, "agy_desktop_settings": True,
            "agy_handoff_source_id": source_id,
        }
        turn = {"question": question, "answer_md": REQUESTING_TEXT, "model": AGY_MODEL_ID,
                "created_at": now, "tool_settings": dict(settings), "question_origin": "mc"}
        self._mark_turn_request_pending(turn, AGY_MODEL_ID, question)
        turn.update(_provider_request_id=uuid.uuid4().hex, request_recoverable=False)
        self._agy_handoff = pending
        self.SetStatusText("正在提交 AGY 接续，等待首次提问结果")
        try:
            pending.worker = threading.Thread(target=self._submit_agy_handoff,
                                              args=(pending, chat, turn, source_scope), name="agy-handoff", daemon=True)
            pending.worker.start()
        except RuntimeError as exc:
            self._agy_handoff = None
            return False, f"无法开始 AGY 接续：{exc}"
        return True, ""

    def _read_agy_handoff_source(self, pending, source, question):
        if self.chat_store.unresolved_agy_handoff(pending.source_id):
            raise AgyError(HANDOFF_UNKNOWN)
        durable = self.chat_store.load_chat(pending.source_id, include_execution_steps=False)
        if not durable or (source["codex_thread_id"] and source["codex_thread_id"] != durable.get("codex_thread_id")):
            raise AgyError("原 Codex 聊天已变更，本次未发送。")
        cwd = str(durable.get("native_cwd") or source["native_cwd"] or "")
        if not cwd or not Path(cwd).is_absolute():
            raise AgyError("找不到原 Codex 聊天的实际项目目录，本次未发送。")
        try:
            cwd = validate_workspace(cwd)
        except AgyError as exc:
            raise AgyError(f"原 Codex 项目目录不可用，本次未发送。{exc}") from exc
        turns = []
        for index, turn in enumerate(durable.get("turns", ())):
            if pending.completed_indexes is not None and index not in pending.completed_indexes:
                continue
            answer_at = turn.get("answer_at")
            if isinstance(answer_at, (int, float)) and answer_at > pending.cutoff:
                continue
            if (is_codex_model(turn.get("model") or source["model"]) and not turn.get("local_command")
                    and turn.get("request_status") in (None, "", "done")
                    and not turn.get("terminal_final_pending") and not turn.get("request_error")
                    and str(turn.get("question") or "").strip()
                    and str(turn.get("answer_md") or "").strip()
                    and turn["answer_md"] != REQUESTING_TEXT):
                turns.append(turn)
        sections = ["以下是已完成的 Codex 历史对话。"]
        for index, role in self._ordered_answer_message_keys(turns):
            label, key = ("用户", "question") if role == "user" else ("Codex", "answer_md")
            sections.append(f"{label}：\n{turns[index][key]}")
        sections.append(f"本次提问：\n{question}")
        return cwd, "\n\n".join(sections)

    def _submit_agy_handoff(self, pending, chat, turn, source):
        client = None
        saved = settled_turn = None
        staged = False
        error = ""
        answer = []
        ids = {}
        context = None
        pair_id = self._read_pair_id()

        def update(event):
            nonlocal context
            ids.update(_agy_update_ids(event))
            if event.get("sessionUpdate") == "usage_update":
                context = agy_context_tokens(event) or context
            elif event.get("sessionUpdate") == "agent_message_chunk":
                content = event.get("content")
                if isinstance(content, dict) and isinstance(content.get("text"), str):
                    answer.append(content["text"])

        try:
            cwd, turn["agy_handoff_prompt"] = self._read_agy_handoff_source(pending, source, turn["question"])
            chat["title"] = self._next_default_chat_title()
            files, failed = self._normalize_outgoing_attachments(pending.attachments)
            if failed:
                names = "、".join(item["name"] for item in failed)
                raise AgyError(f"以下附件无法读取，本次未发送：{names}")
            client, fields = self._prepare_agy_workspace(cwd)
            chat.update(fields)
            options = fields["agy_options"]
            config = self._agy_config_for_tool_settings(options, turn["tool_settings"])
            client.configure_desktop(config, options)
            chat.update(agy_config=config, agy_options=client.options)
            effective = self._agy_prior_tool_settings(dict(chat, agy_desktop_settings=False))
            turn["tool_settings"].update(effective)
            turn["attachments"] = files
            blocks = read_prompt_attachments(client.capabilities, files)
            if pending.cancel.is_set() or getattr(self, "_closing", False):
                raise AgyError("已取消 AGY 接续，原草稿和附件已保留。")
            unknown_chat = dict(chat, native_history_status=HANDOFF_UNKNOWN,
                                native_unresolved_turn={"index": 0, "message": HANDOFF_UNKNOWN})
            turn.update(request_resume_token={"session_id": client.session_id}, agy_session_id=client.session_id)
            unknown_turn = dict(turn, answer_md="", request_status="unknown", request_error=HANDOFF_UNKNOWN,
                                terminal_notification_pair=pair_id)
            self.chat_store.stage_agy_handoff(unknown_chat, unknown_turn)
            staged = True
            pending.preparing = False
            run = AgyRun(0, lambda: True, self._agy_chat_scope(chat), turn["tool_settings"], client=client)
            run.worker = pending.worker
            pending.run = run
            interaction_lock = threading.Lock()
            early_interactions = []

            def interact(*args):
                with interaction_lock:
                    if run.prompt is None:
                        early_interactions.append(args)
                        return
                self._call_after_if_alive(self._receive_agy_handoff_interaction, pending, run, *args)

            if pending.cancel.is_set() or getattr(self, "_closing", False):
                raise AgyError("已取消 AGY 接续，原草稿和附件已保留。")
            native_prompt = client.begin_prompt(turn["agy_handoff_prompt"], update, interact, attachments=blocks)
            with interaction_lock:
                run.prompt = native_prompt
                for args in early_interactions:
                    self._call_after_if_alive(self._receive_agy_handoff_interaction, pending, run, *args)
                early_interactions.clear()
            if pending.cancel.is_set():
                try:
                    client.cancel_prompt(run.prompt)
                except AgyError as exc:
                    raise AgyResultUnknown(HANDOFF_UNKNOWN) from exc
            reason = run.prompt.wait()
            if reason in {"context_length_exceeded", "prompt_too_long", "input_too_long"}:
                raise AgyError(HANDOFF_OVERFLOW)
            if reason in {"cancelled", "refusal"}:
                raise AgyError("AGY 首次提问已取消或被拒绝，原草稿和附件已保留。")
            if reason != "end_turn":
                raise AgyResultUnknown(f"{HANDOFF_UNKNOWN} 原生结束原因：{reason}")
            statistics = {"agy_usage_owner": agy_usage_owner(chat), "agy_prompt_usage": run.prompt.usage,
                          "agy_usage_partial": False}
            if context is not None:
                statistics["agy_context_tokens"] = context
            turn.update(_agy_terminal_values(ids, "".join(answer), reason, pair_id, statistics))
            saved, settled_turn = chat, turn
        except AgyResultUnknown as exc:
            unknown_turn.update(ids, agy_partial_answer_md="".join(answer))
            error = f"{HANDOFF_UNKNOWN} {exc}" if str(exc) != HANDOFF_UNKNOWN else HANDOFF_UNKNOWN
            unknown_chat.update(native_history_status=error)
            saved, settled_turn = unknown_chat, unknown_turn
        except (OSError, sqlite3.Error, AgyError) as exc:
            detail = str(getattr(exc, "rpc_message", "")).casefold()
            if any(marker in detail for marker in ("context length", "context window", "context_length_exceeded",
                                                   "input token count", "prompt is too long", "request too large")):
                error = HANDOFF_OVERFLOW
            else:
                error = str(exc) if isinstance(exc, AgyError) else f"无法准备 AGY 接续，本次未发送：{exc}"
        finally:
            try:
                if client is not None:
                    self.chat_store.settle_agy_handoff(saved or chat, settled_turn)
            except sqlite3.Error:
                saved = None
                error = HANDOFF_UNKNOWN if staged else "无法保存 AGY 接续，本次未发送。"
            finally:
                accepted_client = client if saved is chat else None
                try:
                    if client is not None and accepted_client is None:
                        client.close()
                finally:
                    posted = self._call_after_if_alive(self._finish_agy_handoff, pending, saved, error,
                                                      accepted_client)
                    if not posted and accepted_client is not None:
                        accepted_client.close()

    def _finish_agy_handoff(self, pending, saved, error, client):
        if self._agy_handoff is not pending:
            if client is not None:
                client.close()
            return
        self._agy_handoff = None
        if pending.run:
            self._dismiss_agy_dialog(pending.run)
        if saved:
            chat_id = saved["id"]
            if client is not None:
                self._watch_agy_title(client, chat_id)
                self._agy_clients[chat_id] = client
            self.archived_chats.append(saved)
            if (self._visible_clear_owner() == pending.source_id and self._current_tool() == "agy"
                    and self.input_edit.GetValue() == pending.draft
                    and self._pending_input_attachments == pending.attachments):
                self.view_mode = "active"
                self.view_history_id = None
                if self._switch_current_chat(chat_id):
                    self.input_edit.ChangeValue("")
                    self.input_edit.SetFocus()
                    self._pending_input_attachments = []
            self._upsert_history_row(chat_id)
            self._invalidate_remote_state_cache()
            self._push_remote_history_changed(chat_id)
            self._play_send_sound()
        self.SetStatusText(error or "已完成 AGY 接续")

    def _receive_agy_handoff_interaction(self, pending, run, interaction_id, method, params):
        if self._agy_handoff is not pending or not run.client.interaction_active(run.prompt, interaction_id):
            return
        prompt, questions = interaction_questions(method, params)
        if not questions:
            try:
                run.client.decline_interaction(run.prompt, interaction_id, method)
            except AgyError as exc:
                self.SetStatusText(str(exc))
            return
        interaction_id = "agy-" + interaction_id
        run.interactions[interaction_id] = (method, prompt, questions)
        self._show_agy_interaction(pending.source_id, interaction_id, handoff=run)

    @staticmethod
    def _agy_session_id_for_chat(chat):
        return str(chat.get("agy_session_id") or "")

    @staticmethod
    def _agy_chat_scope(chat):
        return tuple(chat.get(key) for key in AGY_CHAT_SCOPE_FIELDS)

    def _agy_run_for_chat(self, chat):
        if not isinstance(chat, dict):
            return None
        run = self._agy_busy.get(chat.get("id"))
        return run if run and run.scope == self._agy_chat_scope(chat) and run.owns_turn() else None

    def _agy_terminal_write_rejected(self, owner):
        run = self._agy_busy.get(owner)
        return run is not None and run.terminal_rejected

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

    def _agy_tool_settings_options(self):
        chat = self._visible_codex_speed_chat()
        options = chat.get("agy_options") or getattr(self, "_agy_settings_catalog", ())
        return agy_settings.control_options(options, EFFORT_NAMES)

    def _request_agy_settings_catalog(self):
        if getattr(self, "_agy_settings_catalog", None) or getattr(self, "_agy_catalog_pending", False):
            return
        executable = find_agy_executable()
        if not executable:
            return
        self._agy_catalog_pending = True

        def finish(options, error):
            self._agy_catalog_pending = False
            if getattr(self, "_agy_settings_catalog", None):
                return
            self._agy_settings_catalog = options
            if self._current_tool() == "agy":
                if error:
                    self.SetStatusText(f"无法读取 AGY 模型目录：{error}")
                else:
                    self._refresh_tool_setting_choices({key: value for key, value in self._pending_tool_settings().items()
                                                        if value})

        def read():
            try:
                options = agy_settings.read_models(executable, EFFORT_NAMES)
            except (OSError, subprocess.SubprocessError, AgyError) as exc:
                self._call_after_if_alive(finish, [], str(exc))
            else:
                self._call_after_if_alive(finish, options, "")

        threading.Thread(target=read, name="agy-models", daemon=True).start()

    def _agy_config_for_tool_settings(self, options, settings):
        return agy_settings.native_config(options, settings, EFFORT_NAMES)

    @staticmethod
    def _agy_prior_tool_settings(chat):
        if not chat or chat.get("agy_desktop_settings"):
            return {}
        config = chat.get("agy_config") or {}
        return {agy_settings.FIELDS[option["category"]]: config.get(option["id"], option["current"])
                for option in chat.get("agy_options") or () if option["category"] in agy_settings.FIELDS}

    def _start_desktop_agy_chat(self, title: str, title_manual: bool) -> bool:
        if self._agy_new_chat_pending:
            return False
        if not find_agy_executable():
            self.SetStatusText("此电脑尚未安装 AGY，请安装或设置 MC_AGY_EXECUTABLE")
            return False
        cwd = self._workspace_dir_for_codex()
        prior = self._visible_clear_owner()
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
                if self._visible_clear_owner() != prior or self._current_tool() != "agy":
                    return
                settings = {key: value for key, value in self._pending_tool_settings().items() if value}
                self._agy_settings_catalog = fields["agy_options"]
                fields["agy_desktop_settings"] = True
                committed = self._on_new_chat_clicked(None, title=title, title_manual=title_manual,
                                                      agy_workspace=fields)
                if committed:
                    cwd = fields["agy_workspace"]
                    self._remember_agy_workspace(client, self.active_chat_id, cwd)
                    self._refresh_tool_setting_choices(settings)
                    try:
                        self._agy_config_for_tool_settings(fields["agy_options"], settings)
                    except AgyError as exc:
                        self.SetStatusText(str(exc))
                    else:
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

    def _validate_agy_submission(self, chat: dict, *, queued=False) -> str:
        if chat.get("agy_handoff_source_id") and chat.get("native_unresolved_turn"):
            raise AgyError(HANDOFF_UNKNOWN)
        chat_id = str(chat.get("id") or "")
        if chat_id in self._native_history_reserved:
            raise AgyError("此 AGY 聊天正在更新原生历史，请稍后重试")
        if not queued and chat_id in self._agy_busy:
            raise AgyError("此 AGY 聊天正在执行，请等待当前回答完成")
        if not chat.get("agy_session_id"):
            raise AgyError("请先新建 AGY 聊天")
        return validate_workspace(str(chat.get("agy_workspace") or ""))

    def _agy_desktop_submission(self, chat, turns, settings):
        options = chat.get("agy_options") or []
        categories = {option["category"] for option in options}
        if not chat.get("agy_desktop_settings") and not agy_settings.FIELDS.keys() <= categories:
            return None, False
        self._agy_config_for_tool_settings(options, settings)
        run = self._agy_run_for_chat(chat)
        previous = run.settings if run and run.settings is not None else self._agy_prior_tool_settings(chat)
        agy_turns = [turn for turn in turns if isinstance(turn, dict) and turn.get("model") == AGY_MODEL_ID]
        queued = bool(agy_turns and agy_turns[-1].get("request_status") == "queued")
        if queued and not run:
            previous_turn = next((turn for turn in reversed(agy_turns) if turn.get("request_status") != "queued"), {})
            if previous_turn.get("request_status") in {"pending", "running", "unknown"}:
                raise AgyError("原 AGY 回合的结果待确认，已排队的问题保留。请先同步原生历史。")
        changed = any(settings.get(key) != previous.get(key) for key in ("model", "effort"))
        return settings, queued or bool(run and changed)

    def _launch_next_agy_tool_turn(self, chat_id):
        chat, turns, current = self._chat_target_for_request(chat_id)
        if (not chat or getattr(self, "_closing", False) or self._clear_pending_for(chat_id)
                or chat_id in self._agy_busy):
            return
        for index, turn in enumerate(turns):
            if turn.get("model") != AGY_MODEL_ID or turn.get("request_status") != "queued":
                continue
            turn["request_status"] = "pending"
            self._mark_chat_turns_dirty(chat_id, index)
            if current:
                self.is_running = True
                self._active_request_count = 1
                self.SetStatusText("正在发送排队的问题")
            try:
                self._start_agy_worker_for_turn(chat_id, index, turn["request_question"], AGY_MODEL_ID)
            except (AgyError, RuntimeError) as exc:
                self._on_done(index, "", str(exc), AGY_MODEL_ID, "", chat_id)
            self._defer_chat_state_save()
            return

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
        settings = turn.get("tool_settings")
        turn["request_recoverable"] = False
        turn["request_resume_token"] = {"session_id": session_id}
        run = AgyRun(turn_idx, self._bind_provider_turn(chat_id, turn_idx), self._agy_chat_scope(chat),
                     settings or self._agy_prior_tool_settings(chat))
        if prepared_attachments is not None:
            prepared_attachments.adopt(run.scope)
        self._agy_busy[chat_id] = run
        store = self.chat_store
        durable_scope = dict(zip(AGY_CHAT_SCOPE_FIELDS, run.scope))
        revision = max(1, int(chat.get("revision") or 1))
        pair_id = self._read_pair_id()
        submitted_turn = dict(turn)
        observed_ids = {}
        context = None
        usage_owner = agy_usage_owner(chat)
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
            nonlocal context
            if not isinstance(update, dict):
                return
            kind = update.get("sessionUpdate")
            if kind == "usage_update":
                context = agy_context_tokens(update) or context
            native_ids = _agy_update_ids(update)
            if native_ids:
                observed_ids.update(native_ids)
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

        def finish(active_client, answer, reason, error, result_unknown, terminal):
            current_chat, current_turns, _ = self._chat_target_for_request(chat_id)
            owns = self._agy_run_for_chat(current_chat) is run
            if self._agy_busy.get(chat_id) is run:
                self._agy_busy.pop(chat_id)
            self._dismiss_agy_dialog(run)
            if run.terminal_rejected:
                if active_client is not None:
                    active_client.close()
                if owns:
                    self._restore_after_rejected_agy_terminal(chat_id, current_chat, current_turns, run.saved_chat)
                return
            if active_client is not None:
                if owns:
                    self._agy_clients[chat_id] = active_client
                else:
                    active_client.close()
            if owns:
                if terminal:
                    current_turns[turn_idx].update(
                        agy_native_terminal_reason=reason, terminal_notification_pair=pair_id,
                    )
                    if reason == "end_turn":
                        current_turns[turn_idx]["answer_at"] = terminal["answer_at"]
                    else:
                        current_turns[turn_idx].update(request_status="cancelled", request_error=terminal["request_error"])
                        error = terminal["request_error"]
                elif reason != "end_turn" and not error:
                    error = f"AGY 回合已结束：{reason or '缺少终态'}"
                turn = current_turns[turn_idx]
                if terminal:
                    turn.update({key: terminal[key] for key in (
                        "agy_usage_owner", "agy_context_tokens", "agy_prompt_usage", "agy_usage_partial",
                    ) if key in terminal})
                elif reason and run.prompt is not None:
                    turn.update(agy_usage_owner=usage_owner, agy_prompt_usage=run.prompt.usage,
                                agy_usage_partial=run.prompt.request_count > 1)
                if not error and reason == "end_turn" and turn.get("agy_observed_message_id"):
                    turn["provider_message_id"] = turn["agy_observed_message_id"]
                if result_unknown:
                    if not reason and answer.strip():
                        turn["agy_partial_answer_md"] = answer
                    current_chat["native_history_status"] = error
                    current_chat["native_unresolved_turn"] = {"index": turn_idx, "message": error}
                self._invalidate_remote_state_cache()
                self._on_done(turn_idx, answer, error, model, "", chat_id, result_unknown=result_unknown)
                self._refresh_agy_chat_information(current_chat)
                if error and self._visible_clear_owner() == chat_id:
                    self.SetStatusText(error)
                if not result_unknown:
                    self._launch_next_agy_tool_turn(chat_id)

        def work():
            active_client = prepared_attachments.client if prepared_attachments is not None else client
            reason = error = ""
            result_unknown = False
            terminal = None
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
                if settings:
                    active_options = active_client.options or options
                    active_client.configure_desktop(self._agy_config_for_tool_settings(active_options, settings),
                                                    active_options)
                else:
                    active_client.configure(config, options)
                if getattr(self, "_closing", False):
                    raise AgyError("MC 正在退出")
                attachments = (prepared_attachments.blocks if prepared_attachments is not None else
                               read_prompt_attachments(active_client.capabilities, turn["attachments"])
                               if settings and turn.get("attachments") else ())
                run.prompt = active_client.begin_prompt(
                    question, on_update, on_interaction, attachments=attachments,
                )
                reason = run.prompt.wait()
                if reason in ("end_turn", "cancelled"):
                    statistics = {"agy_usage_owner": usage_owner, "agy_prompt_usage": run.prompt.usage,
                                  "agy_usage_partial": run.prompt.request_count > 1}
                    if context is not None:
                        statistics["agy_context_tokens"] = context
                    terminal = self._commit_agy_terminal(
                        store, chat_id, run, durable_scope, revision,
                        submitted_turn, observed_ids, "".join(full), reason, pair_id, statistics,
                    )
            except AgyError as exc:
                result_unknown = isinstance(exc, AgyResultUnknown)
                error = str(exc)
                if active_client is not None:
                    active_client.close()
                    active_client = None
            finally:
                if reason in ("end_turn", "cancelled") and terminal is None and not run.terminal_rejected:
                    error = ("AGY 已完成，但本地保存未确认。" if reason == "end_turn"
                             else "AGY 已取消，但本地保存未确认。")
                    result_unknown = True
                with lock:
                    pending_timer = timer
                if pending_timer is not None:
                    pending_timer.cancel()
                flush()
                if not self._call_after_if_alive(finish, active_client, "".join(full), reason, error,
                                                result_unknown, terminal):
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

    def _restore_after_rejected_agy_terminal(self, chat_id, chat, turns, saved):
        self._chat_turn_dirty_from.pop(chat_id, None)
        visible = self._visible_clear_owner() == chat_id
        active = chat_id == self.active_chat_id
        if saved is None:
            self._discard_deleted_chat_view(chat_id)
        else:
            turns[:] = saved["turns"]
            chat.clear()
            chat.update(saved, turns=turns)
        if active:
            self.is_running = False
            self._active_request_count = 0
            self.new_chat_button.Enable()
            self._set_input_hint_idle()
        self._invalidate_remote_state_cache()
        if visible:
            self._render_answer_list()
            self.SetStatusText("原聊天已变更，请查看已保存的记录。")

    def _commit_agy_terminal(self, store, chat_id, run, scope, revision, turn, native_ids, answer, reason, pair_id,
                             statistics):
        terminal = _agy_terminal_values(native_ids, answer, reason, pair_id, statistics)
        with self._close_admission_lock:
            if getattr(self, "_close_producer_fenced", False):
                return None
            self._agy_terminal_writes += 1
        try:
            for _ in range(2):
                try:
                    if store.commit_agy_terminal(chat_id, run.turn_idx, scope, revision, turn, terminal):
                        return terminal
                    run.terminal_rejected = True
                    run.saved_chat = store.load_chat(chat_id, include_execution_steps=False)
                    return None
                except sqlite3.OperationalError:
                    continue
                except (sqlite3.Error, ValueError):
                    return None
            return None
        finally:
            with self._close_admission_lock:
                self._agy_terminal_writes -= 1
            self._schedule_close_progress()

    def _deliver_agy_events(self, chat_id, run, batch):
        chat, turns, _ = self._chat_target_for_request(chat_id)
        if self._agy_run_for_chat(chat) is not run:
            return
        delta = "".join(value for kind, value in batch if kind == "delta")
        steps = {}
        native_ids = {}
        context = None
        for kind, value in batch:
            if kind == "interaction":
                self._receive_agy_interaction(chat_id, run, *value)
            elif kind == "native_ids":
                native_ids.update(value)
            elif kind == "update":
                if value.get("sessionUpdate") == "usage_update":
                    context = agy_context_tokens(value) or context
                if step := run.execution.step(value):
                    steps[step[0]] = step
        turn = turns[run.turn_idx]
        changes = dict(native_ids)
        if context is not None:
            changes.update(agy_usage_owner=agy_usage_owner(chat), agy_context_tokens=context)
        if any(turn.get(key) != value for key, value in changes.items()):
            turn.update(changes)
            self._mark_chat_turns_dirty(chat_id, run.turn_idx)
            self._defer_chat_state_save()
            if context is not None:
                self._refresh_agy_chat_information(chat)
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

    def _reply_agy_interaction(self, chat_id, interaction_id, *, text="", answers=None, handoff=None):
        chat, _, _ = self._chat_target_for_request(chat_id)
        run = handoff or self._agy_run_for_chat(chat)
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
            if handoff is None:
                self._update_agy_remote_state(chat_id)
            return False, f"AGY 回复结果未知：{exc}"
        run.interactions.pop(interaction_id, None)
        self._dismiss_agy_dialog(run)
        if handoff is None:
            self._update_agy_remote_state(chat_id)
        return (True, "已回复 AGY。") if accepted else (False, "此 AGY 请求已处理或过期。")

    def _show_agy_interaction(self, chat_id, interaction_id, *, only_foreground=False, handoff=None):
        if only_foreground and not self._is_foreground_window():
            return
        chat, _, _ = self._chat_target_for_request(chat_id)
        run = handoff or self._agy_run_for_chat(chat)
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
                        chat_id, interaction_id, text=answers.get("approval", ""), answers=answers, handoff=handoff,
                    )
                    self.SetStatusText(message)
                    if ok or interaction_id not in run.interactions:
                        break
                    wx.MessageBox(message, "AGY", wx.OK | wx.ICON_WARNING, parent=dialog)
            finally:
                run.dialog = None
                next_request = next(iter(run.interactions), None)
                if next_request and next_request != interaction_id:
                    self._call_after_if_alive(self._show_agy_interaction, chat_id, next_request, handoff=handoff)
                elif handoff and next_request == interaction_id:
                    try:
                        run.client.cancel_prompt(run.prompt)
                    except AgyError as exc:
                        self.SetStatusText(f"AGY 停止结果待确认：{exc}")

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
        pending = getattr(self, "_agy_handoff", None)
        if pending:
            pending.cancel.set()
            if pending.run:
                runs += (pending.run,)
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
