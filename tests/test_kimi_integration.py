import json
import time
from pathlib import Path

import pytest
import wx

import main
from codex_client import CodexEvent
from kimi_server_client import KimiEvent, event_to_payload, map_session_event

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
    # assistant delta 仅用于最终回答，不能把原始文本暴露为执行过程。
    steps = frame._current_chat_state.get("execution_steps") or []
    assert not any("从前有座山。" in str(step.get("detail_text") or "") for step in steps)


def test_thinking_status_interleaving_creates_one_chinese_execution_step(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "分析一下")
    session_id = fake.created_sessions[0]["session_id"]
    source_data = {"source_kind": "thinking.delta", "agent_id": "main", "offset": 0}

    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id=TEST_TURN_ID))
    fake.push_event(KimiEvent(type="agent_message_delta", thread_id=session_id, turn_id=TEST_TURN_ID, text="The", raw_text="The", display_kind="thinking", data=source_data))
    fake.push_event(KimiEvent(type="thread_status_changed", thread_id=session_id, turn_id=TEST_TURN_ID, status="streaming"))
    fake.push_event(KimiEvent(type="agent_message_delta", thread_id=session_id, turn_id=TEST_TURN_ID, text=" user", raw_text=" user", display_kind="thinking", data={**source_data, "offset": 3}))
    fake.push_event(KimiEvent(type="agent_message_delta", thread_id=session_id, turn_id=TEST_TURN_ID, text=" user", raw_text=" user", display_kind="thinking", data={**source_data, "offset": 3}))
    fake.push_event(KimiEvent(type="agent_message_delta", thread_id=session_id, turn_id=TEST_TURN_ID, text="The", raw_text="The", display_kind="thinking", data=source_data))
    fake.push_event(KimiEvent(type="agent_message_delta", thread_id=session_id, turn_id=TEST_TURN_ID, text="progress", raw_text="progress", display_kind="commentary", data={"source_kind": "tool.progress", "agent_id": "main"}))
    fake.push_event(KimiEvent(type="agent_message_delta", thread_id=session_id, turn_id=TEST_TURN_ID, text="答案", raw_text="答案", display_kind="assistant", data={"source_kind": "assistant.delta", "offset": 0}))

    steps = frame._current_chat_state.get("execution_steps") or []
    summaries = [step.get("list_text") for step in steps if step.get("kimi_summary")]
    assert summaries == ["正在分析问题", "正在处理任务", "正在整理回答"]
    assert [step["detail_text"] for step in steps if step.get("kimi_summary")] == ["正在分析问题", "progress", "正在整理回答"]
    assert not frame._execution_delta_buffer
    assert frame._kimi_turn_answer_parts[(_active_chat_id(frame), session_id, TEST_TURN_ID)] == ["答案"]


def test_structured_kimi_events_show_chinese_summaries_and_keep_details(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "执行任务")
    session_id = fake.created_sessions[0]["session_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id=TEST_TURN_ID))
    events = [
        ("search", "Search source tree", "Glob"),
        ("file", "Read README.md", "Read"),
        ("command", "Run tests", "Shell"),
        ("agent", "Delegate review", "Agent"),
    ]
    for idx, (kind, title, name) in enumerate(events):
        fake.push_event(
            KimiEvent(
                type="item_started",
                thread_id=session_id,
                turn_id=TEST_TURN_ID,
                item_id=f"tool-{idx}",
                title=title,
                command="pytest -q" if kind == "command" else "",
                display_kind=kind,
                data={"source_kind": "subagent.started" if kind == "agent" else "tool.call.started", "tool": {"name": name}},
            )
        )

    steps = [step for step in frame._current_chat_state["execution_steps"] if step.get("kimi_summary")]
    assert [step["list_text"] for step in steps] == ["正在搜索内容", "正在读取文件", "正在执行测试", "正在调用子任务"]
    assert [step["detail_text"] for step in steps] == ["开始执行：Search source tree", "开始执行：Read README.md", "开始执行：Run tests\n命令：pytest -q", "开始执行：Delegate review"]


def test_real_fixture_status_thinking_and_tool_result_produce_primary_chinese_steps(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "读取 fixture")
    session_id = fake.created_sessions[0]["session_id"]
    fixture_path = Path(__file__).parent / "fixtures" / "kimi_server_events.jsonl"
    messages = [json.loads(line)["msg"] for line in fixture_path.read_text(encoding="utf-8").splitlines()]
    selected = []
    for message in messages:
        body = message.get("payload") or {}
        if body.get("turnId") != 1 or message.get("type") not in {"thinking.delta", "agent.status.updated", "tool.call.started", "tool.result"}:
            continue
        copied = json.loads(json.dumps(message))
        copied["session_id"] = session_id
        copied["payload"]["sessionId"] = session_id
        event = map_session_event(copied)
        if event is not None:
            frame._on_kimi_event_for_chat(_active_chat_id(frame), main.CodexEvent(**event_to_payload(event)))
        if message.get("type") == "tool.result":
            break

    steps = [step for step in frame._current_chat_state["execution_steps"] if step.get("kimi_summary")]
    assert [step["list_text"] for step in steps] == ["正在分析问题", "正在搜索内容"]
    assert "Searching *.md" in steps[-1]["detail_text"]


def test_mapped_assistant_delta_whitespace_is_preserved_in_answer_and_execution_summary(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "保留空格")
    session_id = fake.created_sessions[0]["session_id"]
    frame._on_kimi_event_for_chat(
        _active_chat_id(frame), main.CodexEvent(type="turn_started", thread_id=session_id, turn_id=TEST_TURN_ID)
    )
    for offset, delta in ((0, " leading"), (8, " trailing ")):
        event = map_session_event(
            {
                "type": "assistant.delta",
                "session_id": session_id,
                "offset": offset,
                "payload": {"type": "assistant.delta", "turnId": TEST_TURN_ID, "agentId": "main", "delta": delta},
            }
        )
        frame._on_kimi_event_for_chat(_active_chat_id(frame), main.CodexEvent(**event_to_payload(event)))
    frame._on_kimi_event_for_chat(
        _active_chat_id(frame), main.CodexEvent(type="turn_completed", thread_id=session_id, turn_id=TEST_TURN_ID, status="completed")
    )

    assert frame.active_session_turns[-1]["answer_md"] == " leading trailing "
    steps = [step for step in frame._current_chat_state["execution_steps"] if step.get("kimi_summary")]
    assert [step["list_text"] for step in steps] == ["正在整理回答"]
    assert [step["detail_text"] for step in steps] == ["正在整理回答"]


def test_interleaved_same_turn_id_routes_by_session(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    current_turn = {
        "question": "current",
        "answer_md": main.REQUESTING_TEXT,
        "model": "kimi/main",
        "request_status": "pending",
    }
    background_turn = {
        "question": "background",
        "answer_md": main.REQUESTING_TEXT,
        "model": "kimi/main",
        "request_status": "pending",
        "kimi_session_id": "session-background",
        "kimi_turn_id": "0",
    }
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_session_turns = [current_turn]
    frame.active_turn_idx = 0
    frame.active_kimi_session_id = ""
    frame.active_kimi_turn_id = ""
    frame.active_kimi_turn_active = True
    frame._current_chat_state = {
        "id": "chat-current",
        "turns": frame.active_session_turns,
        "kimi_turn_active": True,
    }
    frame.archived_chats = [{
        "id": "chat-background",
        "title": "background",
        "turns": [background_turn],
        "kimi_session_id": "session-background",
        "kimi_turn_id": "0",
        "kimi_turn_active": True,
        "execution_steps": [],
    }]
    frame._kimi_active_turns = {
        "chat-current": {"turn_idx": 0, "session_id": "session-current"},
        "chat-background": {"turn_idx": 0, "turn_id": "0", "session_id": "session-background"},
    }
    monkeypatch.setattr(frame, "_refresh_context_usage_after_done", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: None)
    fake.on_message = frame._on_kimi_client_message
    frame._kimi_client = fake

    current_started = CodexEvent(type="turn_started", thread_id="session-current", turn_id="0")
    assert frame._resolve_kimi_event_chat_id(current_started) == "chat-current"
    fake.push_event(KimiEvent(type="turn_started", thread_id="session-current", turn_id="0"))
    assert current_turn["kimi_session_id"] == "session-current"
    assert current_turn["kimi_turn_id"] == "0"
    assert frame.active_kimi_session_id == "session-current"

    events = [
        CodexEvent(type="agent_message_delta", thread_id="session-background", turn_id="0", text="background answer", display_kind="assistant"),
        CodexEvent(type="agent_message_delta", thread_id="session-current", turn_id="0", text="current answer", display_kind="assistant"),
        CodexEvent(type="turn_completed", thread_id="session-current", turn_id="0", status="completed"),
        CodexEvent(type="turn_completed", thread_id="session-background", turn_id="0", status="completed"),
    ]
    for event in events:
        frame._on_kimi_event(event)

    assert current_turn["answer_md"] == "current answer"
    assert current_turn["request_status"] == "done"
    assert background_turn["answer_md"] == "background answer"
    assert background_turn["request_status"] == "done"
    assert frame._resolve_kimi_event_chat_id(CodexEvent(type="turn_completed", turn_id="0")) == ""


def test_sessionless_turn_fallback_requires_one_turn_occurrence(frame):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_session_turns = [
        {"kimi_session_id": "session-a", "kimi_turn_id": "7"},
        {"kimi_session_id": "session-b", "kimi_turn_id": "7"},
    ]
    frame._current_chat_state = {"id": "chat-current", "turns": frame.active_session_turns}
    frame._kimi_active_turns = {"chat-current": {"turn_idx": 1, "session_id": "session-b", "turn_id": "7"}}
    event = CodexEvent(type="turn_completed", turn_id="7")

    assert frame._kimi_event_scoped_turn_index(frame.active_session_turns, event) == -1
    assert frame._resolve_kimi_event_chat_id(event) == ""

    frame.active_session_turns[1]["kimi_turn_id"] = "8"
    frame._kimi_active_turns["chat-current"]["turn_id"] = "8"
    assert frame._kimi_event_scoped_turn_index(frame.active_session_turns, event) == 0
    assert frame._resolve_kimi_event_chat_id(event) == "chat-current"


def test_same_chat_reused_turn_id_keeps_sessions_isolated(frame, monkeypatch):
    old_turn = {
        "answer_md": main.REQUESTING_TEXT,
        "model": "kimi/main",
        "request_status": "pending",
        "kimi_session_id": "session-old",
        "kimi_turn_id": "0",
    }
    new_turn = {
        "answer_md": main.REQUESTING_TEXT,
        "model": "kimi/main",
        "request_status": "pending",
        "kimi_session_id": "session-new",
        "kimi_turn_id": "0",
    }
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_session_turns = [old_turn, new_turn]
    frame.active_turn_idx = 1
    frame.active_kimi_session_id = "session-new"
    frame.active_kimi_turn_id = "0"
    frame.active_kimi_turn_active = True
    frame.is_running = True
    frame._current_chat_state = {
        "id": "chat-current",
        "turns": frame.active_session_turns,
        "kimi_session_id": "session-new",
        "kimi_turn_id": "0",
        "kimi_turn_active": True,
    }
    frame._kimi_active_turns = {
        "chat-current": {"turn_idx": 1, "session_id": "session-new", "turn_id": "0"}
    }
    monkeypatch.setattr(frame, "_refresh_context_usage_after_done", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: None)

    frame._on_kimi_event(CodexEvent(type="agent_message_delta", thread_id="session-old", turn_id="0", text="old answer", display_kind="assistant"))
    frame._on_kimi_event(CodexEvent(type="agent_message_delta", thread_id="session-new", turn_id="0", text="new answer", display_kind="assistant"))
    frame._on_kimi_event(CodexEvent(type="turn_completed", thread_id="session-old", turn_id="0", status="completed"))

    assert old_turn["answer_md"] == "old answer"
    assert old_turn["request_status"] == "done"
    assert new_turn["answer_md"] == main.REQUESTING_TEXT
    assert new_turn["request_status"] == "pending"
    assert frame._kimi_active_turns["chat-current"]["session_id"] == "session-new"
    assert frame.active_kimi_turn_active is True
    assert frame.is_running is True

    frame._on_kimi_event(CodexEvent(type="turn_completed", thread_id="session-new", turn_id="0", status="completed"))
    assert new_turn["answer_md"] == "new answer"
    assert new_turn["request_status"] == "done"
    assert "chat-current" not in frame._kimi_active_turns


@pytest.mark.parametrize("empty_answer", [main.REQUESTING_TEXT, "", None])
def test_completed_without_answer_does_not_mark_placeholder_done(frame, monkeypatch, empty_answer):
    fake = _setup_kimi_frame(frame, monkeypatch)
    monkeypatch.setattr(frame, "_refresh_context_usage_after_done", lambda *args, **kwargs: None)
    _submit(frame, "question")
    session_id = fake.created_sessions[0]["session_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id="0"))
    frame.active_session_turns[-1]["answer_md"] = empty_answer

    fake.push_event(KimiEvent(type="turn_completed", thread_id=session_id, turn_id="0", status="completed"))

    turn = frame.active_session_turns[-1]
    assert turn["request_status"] == "failed"
    assert turn["answer_md"] != main.REQUESTING_TEXT
    assert "未返回任何内容" in turn["request_error"]


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
        KimiEvent(
            type="agent_message_delta",
            thread_id="session-bg",
            turn_id="bg-turn",
            text="The",
            raw_text="The",
            display_kind="thinking",
            data={"source_kind": "thinking.delta", "offset": 0},
        )
    )
    fake.push_event(KimiEvent(type="thread_status_changed", thread_id="session-bg", turn_id="bg-turn", status="streaming"))
    fake.push_event(
        KimiEvent(type="agent_message_delta", thread_id="session-bg", turn_id="bg-turn", text="后台答案", display_kind="assistant")
    )
    fake.push_event(
        KimiEvent(type="turn_completed", thread_id="session-bg", turn_id="bg-turn", status="completed")
    )

    assert archived_turns[0]["answer_md"] == "后台答案"
    assert archived_turns[0]["request_status"] == "done"
    background_steps = frame.archived_chats[0]["execution_steps"]
    assert [step["list_text"] for step in background_steps if step.get("kimi_summary")] == ["正在分析问题"]
    assert rendered["n"] == 0
    assert refreshed["n"] == 0
    frame.view_mode = "history"
    frame.view_history_id = "chat-bg"
    frame._current_chat_state["detail_panel_mode"] = "execution"
    frame._rebuild_execution_list_from_state()
    assert "正在分析问题" in [frame.execution_list.GetString(idx) for idx in range(frame.execution_list.GetCount())]


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
