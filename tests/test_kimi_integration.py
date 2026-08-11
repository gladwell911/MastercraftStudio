import time

import wx

import main
from codex_client import CodexEvent
from kimi_server_client import KimiEvent, event_to_payload

TEST_SESSION_ID = "session-test-1"
TEST_TURN_ID = "1"


class _ImmediateThread:
    def __init__(self, target=None, args=None, kwargs=None, daemon=None):
        self._target = target
        self._args = args or ()
        self._kwargs = kwargs or {}

    def start(self):
        if self._target:
            self._target(*self._args, **self._kwargs)

    def join(self, timeout=None):
        return None

    def is_alive(self):
        return False


class FakeKimiServerClient:
    """Records calls and lets tests push events through the on_message callback."""

    def __init__(self, on_message=None, on_exit=None, **_kwargs):
        self.on_message = on_message
        self.on_exit = on_exit
        self.started = 0
        self.closed = 0
        self.created_sessions = []
        self.subscribe_calls = []
        self.submitted = []
        self.steer_calls = []
        self.steer_result = True
        self.abort_calls = []
        self.approval_answers = []
        self.list_approval_calls = 0
        self.status_by_session = {}
        self.session_exists_result = True
        self.pending_messages = []
        self._prompt_counter = 0
        self._session_counter = 0

    def start(self):
        self.started += 1

    def close(self):
        self.closed += 1

    def create_session(self, *, cwd, model="", title="", permission_mode="auto", goal_objective=""):
        self._session_counter += 1
        session_id = f"session-fake-{self._session_counter}"
        self.created_sessions.append(
            {
                "session_id": session_id,
                "cwd": cwd,
                "model": model,
                "title": title,
                "permission_mode": permission_mode,
            }
        )
        return session_id

    def subscribe(self, session_ids):
        self.subscribe_calls.append(list(session_ids))

    def session_exists(self, session_id):
        known = any(entry["session_id"] == session_id for entry in self.created_sessions)
        return bool(known and self.session_exists_result)

    def submit_prompt(self, session_id, content_blocks):
        self._prompt_counter += 1
        prompt_id = f"prompt-{self._prompt_counter}"
        self.submitted.append(
            {"session_id": session_id, "blocks": list(content_blocks or []), "prompt_id": prompt_id}
        )
        return prompt_id

    def steer_prompts(self, session_id, prompt_ids):
        self.steer_calls.append((session_id, [str(p) for p in prompt_ids]))
        return self.steer_result

    def abort(self, session_id, prompt_id=""):
        self.abort_calls.append((session_id, prompt_id))

    def get_status(self, session_id):
        return dict(self.status_by_session.get(session_id) or {"context_tokens": 128, "max_context_tokens": 2048})

    def list_approvals(self, session_id):
        self.list_approval_calls += 1
        return [{"id": "approval-from-list"}]

    def answer_approval(self, session_id, approval_id, decision, **_kwargs):
        self.approval_answers.append((session_id, approval_id, decision))

    def drain_pending_messages(self, limit=100):
        drained = self.pending_messages[:limit]
        del self.pending_messages[:limit]
        return drained

    # test helpers ------------------------------------------------------

    def push_event(self, event: KimiEvent):
        assert self.on_message is not None
        self.on_message({"type": "event", "payload": {"event": event_to_payload(event)}})


def _make_fake_client(monkeypatch):
    fake = FakeKimiServerClient()

    def factory(on_message=None, on_exit=None, **_kwargs):
        fake.on_message = on_message
        fake.on_exit = on_exit
        return fake

    monkeypatch.setattr(main, "KimiServerClient", factory)
    return fake


def _setup_kimi_frame(frame, monkeypatch):
    fake = _make_fake_client(monkeypatch)
    monkeypatch.setattr(main.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(main.wx, "CallLater", lambda _delay, fn, *a, **k: (fn(*a, **k), True)[1])
    frame._refresh_openclaw_sync_lifecycle = lambda force_replay=False: None
    frame._play_send_sound = lambda: None
    frame._schedule_first_question_auto_title = lambda *a, **k: None
    frame.model_combo.SetValue("Kimi Code")
    frame.selected_model = "kimi/main"
    return fake


def _submit(frame, text):
    frame.input_edit.SetValue(text)
    frame._on_send_clicked(None)


def _active_chat_id(frame):
    return str(frame.active_chat_id or frame.current_chat_id or "").strip()


def test_model_combo_contains_kimi(frame):
    choices = [frame.model_combo.GetString(i) for i in range(frame.model_combo.GetCount())]
    assert "Kimi Code" in choices
    assert "kimi/main" not in choices
    assert main.model_id_from_display_name("Kimi Code") == "kimi/main"
    assert main.model_display_name("kimi/main") == "Kimi Code"


def test_submit_routes_kimi_model_to_kimi_path(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    monkeypatch.setattr(
        frame, "_start_codex_worker_for_turn", lambda *a, **k: pytest.fail("kimi 模型不应走 codex 路径")
    )
    monkeypatch.setattr(
        frame, "_start_claudecode_worker_for_turn", lambda *a, **k: pytest.fail("kimi 模型不应走 claudecode 路径")
    )
    monkeypatch.setattr(frame, "_worker", lambda *a, **k: pytest.fail("kimi 模型不应走 openrouter 路径"))

    _submit(frame, "你好 kimi")

    assert len(fake.submitted) == 1
    assert fake.submitted[0]["blocks"] == [{"type": "text", "text": "你好 kimi"}]
    assert len(fake.created_sessions) == 1
    assert fake.created_sessions[0]["model"] == "kimi/main"
    assert fake.created_sessions[0]["cwd"] == frame._workspace_dir_for_kimi()
    turn = frame.active_session_turns[-1]
    assert turn["answer_md"] == main.REQUESTING_TEXT
    assert turn["kimi_session_id"] == fake.created_sessions[0]["session_id"]
    assert frame.active_kimi_session_id == fake.created_sessions[0]["session_id"]
    assert frame.is_running is True


def test_start_turn_creates_session_once_per_chat(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)

    _submit(frame, "第一个问题")
    first_chat_id = _active_chat_id(frame)
    session_id = fake.created_sessions[0]["session_id"]
    # turn 进行中再次提问：复用会话并尝试 steer
    _submit(frame, "追问")

    assert len(fake.created_sessions) == 1
    assert fake.submitted[-1]["session_id"] == session_id
    assert fake.steer_calls == [(session_id, [fake.submitted[-1]["prompt_id"]])]

    # 另一个聊天得到自己的会话
    frame._on_new_chat_clicked(None)
    _submit(frame, "新聊天的问题")

    assert len(fake.created_sessions) == 2
    assert fake.created_sessions[1]["session_id"] != session_id
    assert fake.submitted[-1]["session_id"] == fake.created_sessions[1]["session_id"]
    matches = [chat for chat in frame.archived_chats if chat.get("id") == first_chat_id]
    assert any(chat.get("kimi_session_id") == session_id for chat in matches)


def test_turn_events_render_execution_list(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "帮我看下代码")
    session_id = fake.created_sessions[0]["session_id"]

    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id=TEST_TURN_ID))
    fake.push_event(
        KimiEvent(
            type="item_started",
            thread_id=session_id,
            turn_id=TEST_TURN_ID,
            item_id="call-1",
            title="列出目录",
            command="ls",
            display_kind="command",
        )
    )
    fake.push_event(
        KimiEvent(
            type="item_completed",
            thread_id=session_id,
            turn_id=TEST_TURN_ID,
            item_id="call-1",
            title="列出目录",
            command="ls",
            exit_code=0,
            display_kind="command",
        )
    )

    steps = frame._current_chat_state.get("execution_steps") or []
    kinds = [str(step.get("display_kind") or "") for step in steps]
    assert "status" in kinds  # turn_started
    assert "command" in kinds
    commands = [str(step.get("command") or "") for step in steps]
    assert "ls" in commands


def test_delta_then_final_answer_updates_answer_list(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "讲个笑话")
    session_id = fake.created_sessions[0]["session_id"]

    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id=TEST_TURN_ID))
    fake.push_event(
        KimiEvent(type="agent_message_delta", thread_id=session_id, turn_id=TEST_TURN_ID, text="从前有", display_kind="assistant")
    )
    fake.push_event(
        KimiEvent(type="agent_message_delta", thread_id=session_id, turn_id=TEST_TURN_ID, text="座山。", display_kind="assistant")
    )
    fake.push_event(
        KimiEvent(type="turn_completed", thread_id=session_id, turn_id=TEST_TURN_ID, status="completed")
    )

    turn = frame.active_session_turns[-1]
    assert turn["answer_md"] == "从前有座山。"
    assert turn["request_status"] == "done"
    # assistant delta 也应作为 commentary 进入执行过程列表
    steps = frame._current_chat_state.get("execution_steps") or []
    commentary = [step for step in steps if str(step.get("display_kind") or "") == "commentary"]
    assert any("从前有座山。" in str(step.get("detail_text") or "") for step in commentary)


def test_turn_completed_reenables_new_chat_and_plays_sound(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    played = {"n": 0}
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: played.__setitem__("n", played["n"] + 1))
    _submit(frame, "问题")
    session_id = fake.created_sessions[0]["session_id"]
    assert frame.is_running is True

    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id=TEST_TURN_ID))
    fake.push_event(
        KimiEvent(type="agent_message_delta", thread_id=session_id, turn_id=TEST_TURN_ID, text="答案", display_kind="assistant")
    )
    fake.push_event(
        KimiEvent(type="turn_completed", thread_id=session_id, turn_id=TEST_TURN_ID, status="completed")
    )

    assert frame.is_running is False
    assert frame._active_request_count == 0
    assert frame.new_chat_button.IsEnabled()
    assert played["n"] == 1
    assert frame.active_kimi_turn_active is False
    assert frame.active_session_turns[-1]["answer_md"] == "答案"


def test_interrupt_via_stop_command(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "长任务")
    session_id = fake.created_sessions[0]["session_id"]
    prompt_id = fake.submitted[-1]["prompt_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id=TEST_TURN_ID))

    _submit(frame, "/stop")

    assert fake.abort_calls == [(session_id, prompt_id)]
    stop_turn = frame.active_session_turns[-1]
    assert "Kimi Code 中断" in str(stop_turn["answer_md"] or "")

    fake.push_event(
        KimiEvent(type="turn_completed", thread_id=session_id, turn_id=TEST_TURN_ID, status="interrupted")
    )
    interrupted_turn = frame.active_session_turns[0]
    assert interrupted_turn["request_status"] == "failed"
    assert "已中断" in str(interrupted_turn["answer_md"] or "")
    assert frame.is_running is False
    assert frame.new_chat_button.IsEnabled()


def test_new_and_clear_commands_drop_session(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "问题一")
    session_id = fake.created_sessions[0]["session_id"]
    assert frame.active_kimi_session_id == session_id

    _submit(frame, "/clear")
    assert frame.active_kimi_session_id == ""
    assert frame._current_chat_state.get("kimi_session_id") == ""
    clear_turn = frame.active_session_turns[-1]
    assert "Kimi Code 清理" in str(clear_turn["answer_md"] or "")

    _submit(frame, "问题二")
    assert len(fake.created_sessions) == 2
    assert fake.submitted[-1]["session_id"] == fake.created_sessions[1]["session_id"]

    previous_chat_id = _active_chat_id(frame)
    _submit(frame, "/new")
    assert _active_chat_id(frame) != previous_chat_id
    # /new 的回答归属于旧聊天（与 codex 行为一致），新聊天从空白开始
    matches = [chat for chat in frame.archived_chats if chat.get("id") == previous_chat_id]
    assert any(
        "Kimi Code 新聊天" in str((turn or {}).get("answer_md") or "")
        for chat in matches
        for turn in (chat.get("turns") or [])
    )


def test_status_command(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "问题")
    session_id = fake.created_sessions[0]["session_id"]
    fake.status_by_session[session_id] = {"context_tokens": 100, "max_context_tokens": 1000}

    _submit(frame, "/status")

    status_turn = frame.active_session_turns[-1]
    answer = str(status_turn["answer_md"] or "")
    assert "## Kimi Code 状态" in answer
    assert session_id in answer
    assert "100/1000" in answer


def test_prompt_during_active_turn_steers(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "第一个问题")
    session_id = fake.created_sessions[0]["session_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id=TEST_TURN_ID))

    _submit(frame, "补充要求")

    assert len(fake.steer_calls) == 1
    assert fake.steer_calls[0][0] == session_id
    assert fake.steer_calls[0][1] == [fake.submitted[-1]["prompt_id"]]
    assert not frame._current_chat_state.get("kimi_request_queue")

    # steer 成功后，合并 turn 的最终答案同时完成两个本地请求行
    fake.push_event(
        KimiEvent(type="agent_message_delta", thread_id=session_id, turn_id=TEST_TURN_ID, text="合并答案", display_kind="assistant")
    )
    fake.push_event(
        KimiEvent(type="turn_completed", thread_id=session_id, turn_id=TEST_TURN_ID, status="completed")
    )
    assert frame.active_session_turns[0]["request_status"] == "done"
    assert frame.active_session_turns[1]["request_status"] == "done"
    assert frame.active_session_turns[1]["answer_md"] == "合并答案"
    assert frame.is_running is False


def test_steer_rejected_queues_and_flushes_on_next_turn(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    fake.steer_result = False
    _submit(frame, "第一个问题")
    session_id = fake.created_sessions[0]["session_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id="10"))

    _submit(frame, "排队的问题")

    queue = frame._current_chat_state.get("kimi_request_queue") or []
    assert len(queue) == 1
    assert queue[0]["prompt_id"] == fake.submitted[-1]["prompt_id"]
    assert queue[0]["turn_idx"] == 1

    # 第一个 turn 结束：排队条目保留（服务端会自动执行），本地 turn 0 收尾
    fake.push_event(
        KimiEvent(type="turn_completed", thread_id=session_id, turn_id="10", status="completed", text="答案一")
    )
    assert frame.active_session_turns[0]["request_status"] == "done"
    assert frame.active_session_turns[1]["request_status"] == "pending"
    assert len(frame._current_chat_state.get("kimi_request_queue") or []) == 1

    # 服务端开始执行排队 prompt：队列冲刷，turn 归属到本地 turn 1
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id="11"))
    assert not frame._current_chat_state.get("kimi_request_queue")
    assert frame.active_session_turns[1]["kimi_turn_id"] == "11"

    fake.push_event(
        KimiEvent(type="agent_message_delta", thread_id=session_id, turn_id="11", text="答案二", display_kind="assistant")
    )
    fake.push_event(
        KimiEvent(type="turn_completed", thread_id=session_id, turn_id="11", status="completed")
    )
    assert frame.active_session_turns[1]["answer_md"] == "答案二"
    assert frame.active_session_turns[1]["request_status"] == "done"


def test_approval_request_opens_dialog_and_replies(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "需要批准的操作")
    session_id = fake.created_sessions[0]["session_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id=TEST_TURN_ID))

    class _Dialog:
        shown = {"questions": None}

        def __init__(self, _parent, questions):
            _Dialog.shown["questions"] = questions

        def ShowModal(self):
            return wx.ID_OK

        def get_answers(self):
            return {"approval": ["approved"]}

        def Destroy(self):
            pass

    monkeypatch.setattr(main, "CodexUserInputDialog", _Dialog)

    fake.push_event(
        KimiEvent(
            type="server_request",
            thread_id=session_id,
            turn_id=TEST_TURN_ID,
            method="approval",
            params={"approvalId": "approval-9", "description": "运行 pytest"},
        )
    )

    assert fake.approval_answers == [(session_id, "approval-9", "approved")]
    questions = _Dialog.shown["questions"]
    assert questions and "运行 pytest" in str(questions[0].get("question") or "")


def test_state_persists_kimi_fields(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "问题")
    session_id = fake.created_sessions[0]["session_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id=TEST_TURN_ID))
    frame._save_state()

    saved_active_kimi = frame.active_kimi_session_id
    saved_turn_id = frame.active_kimi_turn_id
    frame.active_kimi_session_id = ""
    frame.active_kimi_turn_id = ""
    frame.active_kimi_turn_active = False
    frame._load_state()

    assert frame.active_kimi_session_id == saved_active_kimi
    assert frame.active_kimi_turn_id == saved_turn_id
    assert frame.active_kimi_turn_active is True

    # 归档快照保留 kimi 字段
    chat_id = _active_chat_id(frame)
    frame._on_new_chat_clicked(None)
    matches = [chat for chat in frame.archived_chats if chat.get("id") == chat_id]
    assert any(chat.get("kimi_session_id") == session_id for chat in matches)
    assert any(chat.get("kimi_turn_id") == TEST_TURN_ID for chat in matches)


def test_session_not_found_recovery_primes_history(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "第一个问题")
    session_id = fake.created_sessions[0]["session_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id=TEST_TURN_ID))
    fake.push_event(
        KimiEvent(type="turn_completed", thread_id=session_id, turn_id=TEST_TURN_ID, status="completed", text="第一个答案")
    )
    assert frame.active_session_turns[0]["request_status"] == "done"

    # 服务端丢失会话：下次提问应重建会话并用本地历史做 priming
    fake.session_exists_result = False
    _submit(frame, "第二个问题")

    assert len(fake.created_sessions) == 2
    new_session_id = fake.created_sessions[1]["session_id"]
    assert fake.submitted[-1]["session_id"] == new_session_id
    text_block = fake.submitted[-1]["blocks"][0]
    assert text_block["type"] == "text"
    assert "本地保存的历史记录" in text_block["text"]
    assert "第一个问题" in text_block["text"]
    assert "第二个问题" in text_block["text"]
    assert frame.active_kimi_session_id == new_session_id


def test_error_event_marks_turn_failed(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "问题")
    session_id = fake.created_sessions[0]["session_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id=TEST_TURN_ID))

    fake.push_event(
        KimiEvent(type="error", thread_id=session_id, turn_id=TEST_TURN_ID, text="模型服务不可用")
    )

    turn = frame.active_session_turns[-1]
    assert turn["request_status"] == "failed"
    assert "模型服务不可用" in str(turn.get("request_error") or "")
    assert frame.is_running is False
    assert frame.new_chat_button.IsEnabled()


def test_client_close_on_frame_close(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "问题")
    assert frame._kimi_client is fake

    class _CloseEvent:
        def Skip(self):
            pass

    frame._on_close(_CloseEvent())

    assert fake.closed == 1


def test_events_for_non_visible_chat_do_not_repaint(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    archived_turns = [
        {
            "question": "后台问题",
            "answer_md": main.REQUESTING_TEXT,
            "model": "kimi/main",
            "created_at": 1.0,
            "kimi_session_id": "session-bg",
            "kimi_turn_id": "bg-turn",
            "request_status": "pending",
        }
    ]
    frame.archived_chats = [
        {
            "id": "chat-bg",
            "title": "background",
            "turns": archived_turns,
            "created_at": 1.0,
            "updated_at": 1.0,
            "kimi_session_id": "session-bg",
            "kimi_turn_id": "bg-turn",
            "kimi_turn_active": True,
            "execution_steps": [],
        }
    ]
    frame._kimi_active_turns["chat-bg"] = {"turn_idx": 0, "turn_id": "bg-turn", "session_id": "session-bg", "model": "kimi/main"}
    rendered = {"n": 0}
    refreshed = {"n": 0}
    monkeypatch.setattr(frame, "_render_answer_list", lambda: rendered.__setitem__("n", rendered["n"] + 1))
    monkeypatch.setattr(frame, "_refresh_history", lambda *a, **k: refreshed.__setitem__("n", refreshed["n"] + 1))
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    fake.on_message = frame._on_kimi_client_message
    frame._kimi_client = fake
    fake.push_event(
        KimiEvent(type="agent_message_delta", thread_id="session-bg", turn_id="bg-turn", text="后台答案", display_kind="assistant")
    )
    fake.push_event(
        KimiEvent(type="turn_completed", thread_id="session-bg", turn_id="bg-turn", status="completed")
    )

    assert archived_turns[0]["answer_md"] == "后台答案"
    assert archived_turns[0]["request_status"] == "done"
    assert rendered["n"] == 0
    assert refreshed["n"] == 0


def test_help_excludes_compact_and_compact_is_unsupported(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    help_text = frame._build_kimi_help_markdown()
    assert "compact" not in help_text.lower()
    for name in ("stop", "new", "clear", "status", "help"):
        assert f"/{name}" in help_text

    _submit(frame, "/compact")
    turn = frame.active_session_turns[-1]
    answer = str(turn["answer_md"] or "")
    assert "暂不支持" in answer
    # /compact 不会作为普通聊天发给服务端
    assert not any("compact" in str(block.get("text") or "").lower() for entry in fake.submitted for block in entry["blocks"])


def test_build_kimi_content_blocks_image_base64(frame, tmp_path):
    image = tmp_path / "shot.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    blocks = frame._build_kimi_content_blocks(
        "看图",
        [{"kind": "image", "path": str(image), "status": "success"}],
    )
    assert blocks[0] == {"type": "text", "text": "看图"}
    image_block = blocks[1]
    assert image_block["type"] == "image"
    assert image_block["source"]["kind"] == "base64"
    assert image_block["source"]["media_type"] == "image/png"
    import base64 as _base64

    assert _base64.b64decode(image_block["source"]["data"]) == b"\x89PNG\r\n\x1a\nfake"


def test_messages_pending_drains_through_client(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "问题")
    session_id = fake.created_sessions[0]["session_id"]
    fake.pending_messages.append(
        {
            "type": "event",
            "payload": {
                "event": event_to_payload(
                    KimiEvent(type="turn_started", thread_id=session_id, turn_id=TEST_TURN_ID)
                )
            },
        }
    )
    fake.pending_messages.append(
        {
            "type": "event",
            "payload": {
                "event": event_to_payload(
                    KimiEvent(
                        type="turn_completed",
                        thread_id=session_id,
                        turn_id=TEST_TURN_ID,
                        status="completed",
                        text="完成",
                    )
                )
            },
        }
    )

    fake.on_message({"type": "messages_pending"})

    assert not fake.pending_messages
    turn = frame.active_session_turns[-1]
    assert turn["request_status"] == "done"
    assert turn["answer_md"] == "完成"
    assert frame.active_kimi_turn_id == TEST_TURN_ID
