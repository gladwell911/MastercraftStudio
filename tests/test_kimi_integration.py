import json
import time
from pathlib import Path

import pytest
import wx

import main
from codex_client import CodexEvent
from kimi_server_client import KimiEvent, KimiServerError, event_to_payload, map_session_event

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
        self.messages_by_session = {}
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

    def list_messages(self, session_id):
        return list(self.messages_by_session.get(session_id) or [])

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
    frame._kimi_reconcile_backoff = 0
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


def test_initial_kimi_client_start_failure_is_retried_before_failing_turn(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    calls = {"n": 0}

    def flaky_start():
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("first startup failure")

    fake.start = flaky_start

    _submit(frame, "retry startup")

    assert calls["n"] == 2
    assert fake.submitted
    assert frame.active_session_turns[-1]["request_status"] == "pending"


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
        "chat-current": {"turn_idx": 0, "session_id": "session-current", "prompt_id": "prompt-current"},
        "chat-background": {"turn_idx": 0, "turn_id": "0", "session_id": "session-background", "prompt_id": "prompt-background"},
    }
    frame._register_kimi_prompt_owner({
        "chat_id": "chat-current", "session_id": "session-current", "prompt_id": "prompt-current",
        "owner_prompt_id": "prompt-current", "turn_idx": 0, "role": "active", "landed": True,
    })
    frame._register_kimi_prompt_owner({
        "chat_id": "chat-background", "session_id": "session-background", "prompt_id": "prompt-background",
        "owner_prompt_id": "prompt-background", "turn_idx": 0, "turn_id": "0", "role": "active", "landed": True,
    })
    monkeypatch.setattr(frame, "_refresh_context_usage_after_done", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: None)
    fake.on_message = frame._on_kimi_client_message
    frame._kimi_client = fake

    current_started = CodexEvent(type="turn_started", thread_id="session-current", turn_id="0")
    assert frame._resolve_kimi_event_chat_id(current_started) == "chat-current"
    fake.push_event(KimiEvent(type="turn_started", thread_id="session-current", turn_id="0", data={"prompt_id": "prompt-current"}))
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
def test_completed_without_answer_stays_recoverable_and_does_not_mark_done(frame, monkeypatch, empty_answer):
    fake = _setup_kimi_frame(frame, monkeypatch)
    monkeypatch.setattr(frame, "_refresh_context_usage_after_done", lambda *args, **kwargs: None)
    _submit(frame, "question")
    session_id = fake.created_sessions[0]["session_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id="0"))
    frame.active_session_turns[-1]["answer_md"] = empty_answer

    fake.push_event(KimiEvent(type="turn_completed", thread_id=session_id, turn_id="0", status="completed"))

    turn = frame.active_session_turns[-1]
    assert turn["request_status"] == "pending"
    assert turn.get("answer_md") == empty_answer
    assert not turn.get("request_error")


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


def test_out_of_order_absolute_offsets_are_assembled_before_publish(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "offsets")
    session_id = fake.created_sessions[0]["session_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id="4"))
    fake.push_event(
        KimiEvent(
            type="agent_message_delta",
            thread_id=session_id,
            turn_id="4",
            item_id="answer-1",
            text="world",
            display_kind="assistant",
            data={"agent_id": "main", "offset": 6},
        )
    )
    fake.push_event(
        KimiEvent(
            type="agent_message_delta",
            thread_id=session_id,
            turn_id="4",
            item_id="answer-1",
            text="hello ",
            display_kind="assistant",
            data={"agent_id": "main", "offset": 0},
        )
    )
    fake.push_event(
        KimiEvent(type="turn_completed", thread_id=session_id, turn_id="4", status="completed")
    )

    turn = frame.active_session_turns[-1]
    assert turn["request_status"] == "done"
    assert turn["answer_md"] == "hello world"


def test_transport_interruption_blocks_partial_stream_completion_until_rest(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    monkeypatch.setattr(frame, "_recover_kimi_pending_owners", lambda **_kwargs: None)
    _submit(frame, "must be complete")
    session_id = fake.created_sessions[0]["session_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id="5"))
    fake.push_event(
        KimiEvent(
            type="agent_message_delta",
            thread_id=session_id,
            turn_id="5",
            item_id="answer-1",
            text="partial",
            display_kind="assistant",
            data={"agent_id": "main", "offset": 0},
        )
    )
    frame._on_kimi_client_message(
        {"type": "transport_error", "payload": {"error": "reset", "session_ids": [session_id]}}
    )
    fake.push_event(
        KimiEvent(type="turn_completed", thread_id=session_id, turn_id="5", status="completed")
    )

    turn = frame.active_session_turns[-1]
    assert turn["request_status"] == "pending"
    assert turn["answer_md"] == main.REQUESTING_TEXT


def test_subagent_terminal_never_publishes_answer_or_finish_sound(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    played = {"n": 0}
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: played.__setitem__("n", played["n"] + 1))
    _submit(frame, "main task")
    session_id = fake.created_sessions[0]["session_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id="7", data={"agent_id": "main"}))

    fake.push_event(KimiEvent(type="agent_message_delta", thread_id=session_id, turn_id="7", text="sub answer", display_kind="assistant", data={"agent_id": "agent-1", "source_kind": "assistant.delta"}))
    fake.push_event(KimiEvent(type="error", thread_id=session_id, turn_id="7", text="sub failed", data={"agent_id": "agent-1", "source_kind": "error"}))
    fake.push_event(KimiEvent(type="subagent_result", thread_id=session_id, turn_id="7", text="sub done", data={"agent_id": "agent-1"}))
    fake.push_event(KimiEvent(type="turn_completed", thread_id=session_id, turn_id="7", status="completed", data={"agent_id": "agent-1", "source_kind": "turn.ended"}))

    turn = frame.active_session_turns[-1]
    assert turn["request_status"] == "pending"
    assert turn["answer_md"] == main.REQUESTING_TEXT
    assert played["n"] == 0

    fake.push_event(KimiEvent(type="agent_message_delta", thread_id=session_id, turn_id="7", text="final", display_kind="assistant", data={"agent_id": "main", "source_kind": "assistant.delta"}))
    fake.push_event(KimiEvent(type="turn_completed", thread_id=session_id, turn_id="7", status="completed", data={"agent_id": "main", "source_kind": "turn.ended"}))
    assert turn["request_status"] == "done"
    assert turn["answer_md"] == "final"
    assert played["n"] == 1


def test_subagent_event_cannot_claim_or_stamp_main_owner_turn(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "main task")
    session_id = fake.created_sessions[0]["session_id"]
    prompt_id = fake.submitted[0]["prompt_id"]

    fake.push_event(
        KimiEvent(
            type="item_started",
            thread_id=session_id,
            turn_id="subagent-turn",
            data={"agent_id": "agent-1", "prompt_id": prompt_id},
        )
    )

    owner = frame._find_kimi_prompt_owner(prompt_id, session_id=session_id)
    assert owner["turn_id"] == ""
    assert not frame.active_session_turns[-1].get("kimi_turn_id")


def test_promptless_early_event_with_multiple_intents_replays_by_turn_join(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "owner")
    session_id = fake.created_sessions[0]["session_id"]
    prompt_id = fake.submitted[0]["prompt_id"]
    frame._kimi_pending_submissions[session_id] = [
        {"submission_intent_id": "i1"},
        {"submission_intent_id": "i2"},
    ]

    fake.push_event(
        KimiEvent(
            type="agent_message_delta",
            thread_id=session_id,
            turn_id="44",
            item_id="answer-1",
            text="early",
            display_kind="assistant",
            data={"agent_id": "main", "offset": 0},
        )
    )
    assert (session_id, "__turn__:44") in frame._kimi_early_events

    fake.push_event(
        KimiEvent(
            type="turn_started",
            thread_id=session_id,
            turn_id="44",
            data={"agent_id": "main", "prompt_id": prompt_id},
        )
    )

    assert (session_id, "__turn__:44") not in frame._kimi_early_events
    assert frame._kimi_answer_parts(_active_chat_id(frame), session_id, prompt_id) == "early"


def test_unfinalized_current_completion_does_not_touch_answer_list(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "keep hidden")
    session_id = fake.created_sessions[0]["session_id"]
    prompt_id = fake.submitted[0]["prompt_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id="7"))
    answer_rows = [frame.answer_list.GetString(idx) for idx in range(frame.answer_list.GetCount())]
    assert frame._find_answer_row_index(len(frame.active_session_turns) - 1) < 0
    updated = []
    dirtied = []
    monkeypatch.setattr(frame, "_update_active_answer_row", updated.append)
    monkeypatch.setattr(frame, "_mark_background_answer_list_dirty", lambda: dirtied.append(True))
    monkeypatch.setattr(frame, "_recover_kimi_pending_owners", lambda **_kwargs: None)

    fake.push_event(
        KimiEvent(
            type="turn_completed",
            thread_id=session_id,
            turn_id="7",
            status="completed",
            data={"agent_id": "agent-1", "source_kind": "turn.ended"},
        )
    )
    fake.push_event(
        KimiEvent(
            type="turn_completed",
            thread_id=session_id,
            status="completed",
            data={"agent_id": "main", "source_kind": "prompt.completed", "prompt_id": prompt_id},
        )
    )

    assert frame.active_session_turns[-1]["request_status"] == "pending"
    assert [frame.answer_list.GetString(idx) for idx in range(frame.answer_list.GetCount())] == answer_rows
    assert frame._find_answer_row_index(len(frame.active_session_turns) - 1) < 0
    assert updated == []
    assert dirtied == []


def test_unfinalized_background_completion_does_not_refresh_answer_state(frame, monkeypatch):
    _setup_kimi_frame(frame, monkeypatch)
    turn = {
        "question": "background",
        "answer_md": main.REQUESTING_TEXT,
        "model": "kimi/main",
        "request_status": "pending",
        "kimi_session_id": "session-bg",
        "kimi_turn_id": "turn-bg",
        "kimi_prompt_id": "prompt-bg",
    }
    chat = {
        "id": "chat-bg",
        "turns": [turn],
        "kimi_session_id": "session-bg",
        "kimi_turn_id": "turn-bg",
        "kimi_turn_active": True,
        "execution_steps": [],
    }
    frame.archived_chats = [chat]
    owner = {
        "chat_id": "chat-bg",
        "session_id": "session-bg",
        "turn_id": "turn-bg",
        "turn_idx": 0,
        "prompt_id": "prompt-bg",
        "owner_prompt_id": "prompt-bg",
        "role": "active",
        "question": "background",
        "landed": True,
    }
    frame._kimi_prompt_owners = {"prompt-bg": owner}
    frame._kimi_active_turns = {"chat-bg": owner}
    refreshed = []
    dirtied = []
    monkeypatch.setattr(frame, "_append_execution_entry_to_chat", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_refresh_visible_history_chat", refreshed.append)
    monkeypatch.setattr(frame, "_mark_chat_turns_dirty", lambda *args, **kwargs: dirtied.append((args, kwargs)))
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)

    frame._on_kimi_event_for_chat(
        "chat-bg",
        CodexEvent(
            type="turn_completed",
            thread_id="session-bg",
            turn_id="turn-bg",
            status="completed",
            data={"agent_id": "agent-1", "source_kind": "turn.ended", "prompt_id": "prompt-bg"},
        ),
    )

    assert turn["request_status"] == "pending"
    assert refreshed == []
    assert dirtied == []


def test_prompt_completed_fallback_waits_for_verified_idle_and_is_exactly_once(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    played = {"n": 0}
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: played.__setitem__("n", played["n"] + 1))
    monkeypatch.setattr(frame, "_recover_kimi_pending_owners", lambda **_kwargs: None)
    _submit(frame, "fallback")
    session_id = fake.created_sessions[0]["session_id"]
    prompt_id = fake.submitted[0]["prompt_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id="8", data={"agent_id": "main"}))
    fake.push_event(KimiEvent(type="agent_message_delta", thread_id=session_id, turn_id="8", text="whole answer", display_kind="assistant", data={"agent_id": "main", "source_kind": "assistant.delta"}))
    completion = KimiEvent(type="turn_completed", thread_id=session_id, status="completed", data={"agent_id": "main", "source_kind": "prompt.completed", "prompt_id": prompt_id})

    fake.push_event(completion)
    assert frame.active_session_turns[-1]["request_status"] == "pending"
    assert played["n"] == 0

    fake.push_event(KimiEvent(type="notification", thread_id=session_id, subtype="event.session.work_changed", display_kind="session", data={"agent_id": "main", "protocol": {"busy": False}}))
    fake.push_event(completion)
    fake.push_event(completion)
    assert frame.active_session_turns[-1]["request_status"] == "done"
    assert frame.active_session_turns[-1]["answer_md"] == "whole answer"
    assert played["n"] == 1


def test_first_turn_scoped_event_claims_unique_pending_owner_without_turn_started(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "missing start")
    session_id = fake.created_sessions[0]["session_id"]

    fake.push_event(KimiEvent(type="agent_message_delta", thread_id=session_id, turn_id="99", text="recovered answer", display_kind="assistant", data={"agent_id": "main", "source_kind": "assistant.delta"}))
    fake.push_event(KimiEvent(type="turn_completed", thread_id=session_id, turn_id="99", status="completed", data={"agent_id": "main", "source_kind": "turn.ended"}))

    turn = frame.active_session_turns[-1]
    assert turn["kimi_turn_id"] == "99"
    assert turn["request_status"] == "done"
    assert turn["answer_md"] == "recovered answer"


def test_rest_transcript_boundary_uses_prompt_user_message_not_last_assistant(frame):
    messages = [
        {"id": "p-old", "role": "user", "created_at": "2026-01-01T00:00:00Z", "content": [{"type": "text", "text": "old"}]},
        {"id": "a-old", "role": "assistant", "created_at": "2026-01-01T00:00:01Z", "content": [{"type": "text", "text": "old answer"}]},
        {"id": "p-target", "role": "user", "created_at": "2026-01-01T00:00:02Z", "content": [{"type": "text", "text": "target"}]},
        {"id": "a-target", "role": "assistant", "created_at": "2026-01-01T00:00:03Z", "content": [{"type": "text", "text": "target answer"}]},
        {"id": "p-next", "role": "user", "created_at": "2026-01-01T00:00:04Z", "content": [{"type": "text", "text": "next"}]},
        {"id": "a-next", "role": "assistant", "created_at": "2026-01-01T00:00:05Z", "content": [{"type": "text", "text": "next answer"}]},
    ]
    assert frame._kimi_rest_answer_for_prompt(list(reversed(messages)), "p-target") == "target answer"


def test_transport_recovery_busy_is_event_driven_and_keeps_exact_owner_pending(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "recover me")
    session_id = fake.created_sessions[0]["session_id"]
    prompt_id = fake.submitted[0]["prompt_id"]
    calls = {"status": 0}

    def status(_session_id):
        calls["status"] += 1
        return {"busy": calls["status"] < 3}

    fake.get_status = status
    fake.messages_by_session[session_id] = [
        {"id": prompt_id, "role": "user", "created_at": "2026-01-01T00:00:00Z", "content": [{"type": "text", "text": "recover me"}]},
        {"id": "assistant-1", "role": "assistant", "created_at": "2026-01-01T00:00:01Z", "content": [{"type": "text", "text": "recovered"}]},
    ]

    frame._on_kimi_client_message({"type": "transport_error", "payload": {"error": "socket reset"}})

    turn = frame.active_session_turns[-1]
    assert calls["status"] == 1
    assert turn["request_status"] == "pending"
    assert turn["answer_md"] == main.REQUESTING_TEXT


def test_transport_recovery_healthy_busy_does_not_use_short_cutoff(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    frame._kimi_reconcile_attempts = 3
    _submit(frame, "will fail")
    fake.get_status = lambda _session_id: {"busy": True}

    frame._on_kimi_client_message({"type": "transport_error", "payload": {"error": "original socket reset"}})

    turn = frame.active_session_turns[-1]
    assert turn["request_status"] == "pending"
    assert not turn.get("request_error")


def test_resync_busy_never_publishes_partial_rest_answer_or_fails_long_task(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    frame._kimi_reconcile_attempts = 2
    _submit(frame, "still running")
    session_id = fake.created_sessions[0]["session_id"]
    prompt_id = fake.submitted[0]["prompt_id"]
    fake.get_status = lambda _session_id: {"busy": True}
    fake.messages_by_session[session_id] = [
        {"id": prompt_id, "role": "user", "content": "still running"},
        {"id": "partial", "role": "assistant", "content": "unfinished fragment"},
    ]

    frame._on_kimi_client_message(
        {"type": "resync_required", "payload": {"session_ids": [session_id]}}
    )

    turn = frame.active_session_turns[-1]
    assert turn["request_status"] == "pending"
    assert turn["answer_md"] != "unfinished fragment"
    assert not turn.get("request_error")


def test_rest_terminal_proof_does_not_forge_idle_and_failed_status_wins_over_partial(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "terminal proof")
    session_id = fake.created_sessions[0]["session_id"]
    prompt_id = fake.submitted[0]["prompt_id"]
    owner = frame._find_kimi_prompt_owner(prompt_id, session_id=session_id)
    assert owner is not None
    fake.get_status = lambda _session_id: {"status": "completed"}
    fake.messages_by_session[session_id] = [
        {"id": prompt_id, "role": "user", "content": "terminal proof"},
        {"id": "final", "role": "assistant", "content": "final answer"},
    ]
    reconciled = []
    original_on_kimi_event = frame._on_kimi_event
    monkeypatch.setattr(frame, "_on_kimi_event", reconciled.append)

    frame._reconcile_kimi_owner_worker(owner)

    assert len(reconciled) == 1
    assert reconciled[0].data["terminal_verified"] is True
    assert reconciled[0].data["idle_verified"] is False

    monkeypatch.setattr(frame, "_on_kimi_event", original_on_kimi_event)
    # Reuse the still-pending owner and prove that a failed REST terminal
    # cannot publish the assistant fragment as a successful final answer.
    fake.get_status = lambda _session_id: {"status": "failed", "error": "server terminal failure"}
    fake.messages_by_session[session_id] = [
        {"id": prompt_id, "role": "user", "content": "terminal proof"},
        {"id": "partial", "role": "assistant", "content": "partial output"},
    ]
    frame._kimi_recovery_workers.clear()
    frame._on_kimi_client_message(
        {"type": "transport_error", "payload": {"error": "socket reset"}}
    )
    turn = frame.active_session_turns[-1]
    assert turn["request_status"] == "failed"
    assert turn["answer_md"] != "partial output"
    assert turn["request_error"] == "socket reset"


def test_transport_recovery_enumerates_active_and_queued_prompt_owners(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    fake.steer_result = False
    _submit(frame, "active")
    session_id = fake.created_sessions[0]["session_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id="10"))
    _submit(frame, "queued")
    expected = {entry["prompt_id"] for entry in fake.submitted}
    reconciled = []
    monkeypatch.setattr(
        frame,
        "_reconcile_kimi_owner_worker",
        lambda owner, original_error="": reconciled.append(
            (owner["prompt_id"], owner["role"], original_error)
        ),
    )

    frame._on_kimi_client_message(
        {"type": "transport_error", "payload": {"error": "socket reset"}}
    )

    assert {prompt_id for prompt_id, _role, _error in reconciled} == expected
    assert {role for _prompt_id, role, _error in reconciled} == {"active", "queued"}
    assert {error for _prompt_id, _role, error in reconciled} == {"socket reset"}

    frame._kimi_recovery_workers.clear()
    reconciled.clear()
    frame._on_kimi_client_message(
        {"type": "resync_required", "payload": {"session_ids": [session_id]}}
    )
    assert {prompt_id for prompt_id, _role, _error in reconciled} == expected
    assert {error for _prompt_id, _role, error in reconciled} == {"socket reset"}

    frame._kimi_recovery_workers.clear()
    reconciled.clear()
    frame._on_kimi_client_exit(9)
    assert {prompt_id for prompt_id, _role, _error in reconciled} == expected
    assert all("代码 9" in error for _prompt_id, _role, error in reconciled)


def test_prompt_events_buffer_until_owner_is_persisted_then_replay_in_order(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    deferred = []
    original_call_after = frame._call_after_if_alive

    def defer_owner_landing(fn, *args, **kwargs):
        if getattr(fn, "__name__", "") == "_apply_kimi_thread_state":
            deferred.append((fn, args, kwargs))
            return None
        return original_call_after(fn, *args, **kwargs)

    monkeypatch.setattr(frame, "_call_after_if_alive", defer_owner_landing)
    _submit(frame, "early events")
    session_id = fake.created_sessions[0]["session_id"]
    prompt_id = fake.submitted[0]["prompt_id"]
    assert len(deferred) == 1

    fake.push_event(
        KimiEvent(
            type="agent_message_delta",
            thread_id=session_id,
            turn_id="77",
            text="early answer",
            display_kind="assistant",
            data={"agent_id": "main", "prompt_id": prompt_id},
        )
    )
    fake.push_event(
        KimiEvent(
            type="turn_completed",
            thread_id=session_id,
            turn_id="77",
            status="completed",
            data={"agent_id": "main", "source_kind": "turn.ended", "prompt_id": prompt_id},
        )
    )
    assert len(frame._kimi_early_events[(session_id, prompt_id)]) == 2
    assert frame.active_session_turns[-1]["request_status"] == "pending"

    fn, args, kwargs = deferred.pop()
    fn(*args, **kwargs)

    turn = frame.active_session_turns[-1]
    assert turn["kimi_turn_id"] == "77"
    assert turn["request_status"] == "done"
    assert turn["answer_md"] == "early answer"
    assert (session_id, prompt_id) not in frame._kimi_early_events


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
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id="11", data={"prompt_id": queue[0]["prompt_id"]}))
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


def test_completed_active_owner_plays_sound_once_while_queue_remains(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    fake.steer_result = False
    played = {"n": 0}
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: played.__setitem__("n", played["n"] + 1))
    _submit(frame, "active")
    session_id = fake.created_sessions[0]["session_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id="10"))
    _submit(frame, "queued")

    fake.push_event(
        KimiEvent(
            type="agent_message_delta",
            thread_id=session_id,
            turn_id="10",
            text="answer",
            display_kind="assistant",
        )
    )
    completion = KimiEvent(
        type="turn_completed", thread_id=session_id, turn_id="10", status="completed"
    )
    fake.push_event(completion)
    fake.push_event(completion)

    assert frame.active_session_turns[0]["request_status"] == "done"
    assert frame.active_session_turns[1]["request_status"] == "pending"
    assert len(frame._current_chat_state.get("kimi_request_queue") or []) == 1
    assert frame.is_running is True
    assert played["n"] == 1


def test_ambiguous_submit_preserves_unresolved_owner_for_rest_reconciliation(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    monkeypatch.setattr(frame, "_recover_kimi_pending_owners", lambda **_kwargs: None)

    def ambiguous_submit(_session_id, _content_blocks):
        raise KimiServerError("response lost", result_unknown=True)

    fake.submit_prompt = ambiguous_submit
    _submit(frame, "possibly accepted")

    turn = frame.active_session_turns[-1]
    assert turn["request_status"] == "pending"
    assert turn["kimi_prompt_role"] == "unresolved"
    assert turn["kimi_unresolved_submission"] is True
    assert not turn.get("request_error")
    assert len(frame._kimi_pending_owners()) == 1


def test_unknown_steer_result_is_unresolved_and_never_guessed_as_queued(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "active")
    session_id = fake.created_sessions[0]["session_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id="10"))
    fake.steer_result = None
    monkeypatch.setattr(frame, "_recover_kimi_pending_owners", lambda **_kwargs: None)

    _submit(frame, "ambiguous steer")

    prompt_id = fake.submitted[-1]["prompt_id"]
    owner = frame._find_kimi_prompt_owner(prompt_id, session_id=session_id)
    assert owner["role"] == "unresolved"
    assert frame.active_session_turns[-1]["kimi_prompt_role"] == "unresolved"
    assert not frame._current_chat_state.get("kimi_request_queue")


def test_two_queued_prompts_activate_and_complete_by_exact_prompt_text(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    fake.steer_result = False
    _submit(frame, "active question")
    session_id = fake.created_sessions[0]["session_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id="10", text="active question"))
    _submit(frame, "queued one")
    _submit(frame, "queued two")

    assert [entry["question"] for entry in frame._current_chat_state["kimi_request_queue"]] == [
        "queued one",
        "queued two",
    ]
    assert [frame._find_kimi_prompt_owner(item["prompt_id"], session_id=session_id)["question"] for item in fake.submitted] == [
        "active question",
        "queued one",
        "queued two",
    ]
    assert [turn["kimi_prompt_question"] for turn in frame.active_session_turns] == [
        "active question",
        "queued one",
        "queued two",
    ]

    fake.push_event(KimiEvent(type="turn_completed", thread_id=session_id, turn_id="10", status="completed", text="active answer"))
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id="11", text="queued one"))
    assert frame.active_session_turns[1]["kimi_turn_id"] == "11"
    assert not frame.active_session_turns[2].get("kimi_turn_id")
    assert [entry["question"] for entry in frame._current_chat_state["kimi_request_queue"]] == ["queued two"]
    fake.push_event(KimiEvent(type="turn_completed", thread_id=session_id, turn_id="11", status="completed", text="answer one"))

    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id="12", text="queued two"))
    assert frame.active_session_turns[2]["kimi_turn_id"] == "12"
    assert frame._current_chat_state["kimi_request_queue"] == []
    fake.push_event(KimiEvent(type="turn_completed", thread_id=session_id, turn_id="12", status="completed", text="answer two"))

    assert [turn["answer_md"] for turn in frame.active_session_turns] == [
        "active answer",
        "answer one",
        "answer two",
    ]
    assert [turn["request_status"] for turn in frame.active_session_turns] == ["done", "done", "done"]


def test_duplicate_queued_prompt_text_does_not_guess_owner(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    fake.steer_result = False
    _submit(frame, "active question")
    session_id = fake.created_sessions[0]["session_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id="20", text="active question"))
    _submit(frame, "same queued text")
    _submit(frame, "same queued text")
    fake.push_event(KimiEvent(type="turn_completed", thread_id=session_id, turn_id="20", status="completed", text="active answer"))

    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id="21", text="same queued text"))

    assert [turn.get("kimi_turn_id", "") for turn in frame.active_session_turns[1:]] == ["", ""]
    assert [entry["question"] for entry in frame._current_chat_state["kimi_request_queue"]] == [
        "same queued text",
        "same queued text",
    ]


def test_exact_queued_prompt_terminal_removes_only_its_queue_entry(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    fake.steer_result = False
    _submit(frame, "active")
    session_id = fake.created_sessions[0]["session_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id="10"))
    _submit(frame, "queued")
    queued_prompt = fake.submitted[-1]["prompt_id"]

    fake.push_event(
        KimiEvent(
            type="turn_completed",
            thread_id=session_id,
            text="queued answer",
            status="completed",
            data={
                "agent_id": "main",
                "source_kind": "rest.reconciled",
                "prompt_id": queued_prompt,
                "idle_verified": True,
                "owner_generation": frame._find_kimi_prompt_owner(queued_prompt, session_id=session_id)["generation"],
            },
        )
    )

    assert frame.active_session_turns[0]["request_status"] == "pending"
    assert frame.active_session_turns[1]["request_status"] == "done"
    assert frame.active_session_turns[1]["answer_md"] == "queued answer"
    assert frame._current_chat_state.get("kimi_request_queue") == []
    assert frame.active_kimi_turn_active is True


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


def test_load_state_rebuilds_pending_owner_and_starts_recovery(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "resume after restart")
    session_id = fake.created_sessions[0]["session_id"]
    prompt_id = fake.submitted[0]["prompt_id"]
    frame.archived_chats.append(
        {
            "id": "chat-archived-pending",
            "title": "archived pending",
            "kimi_session_id": "session-archived",
            "kimi_turn_active": True,
            "turns": [
                {
                    "question": "restore archived too",
                    "answer_md": main.REQUESTING_TEXT,
                    "model": "kimi/main",
                    "request_status": "pending",
                    "kimi_session_id": "session-archived",
                    "kimi_prompt_id": "prompt-archived",
                }
            ],
            "execution_steps": [],
        }
    )
    frame._save_state()
    frame._kimi_client = None
    frame._kimi_prompt_owners = {}
    frame._kimi_active_turns = {}
    recovered = []
    monkeypatch.setattr(
        frame,
        "_recover_kimi_pending_owners",
        lambda **kwargs: recovered.append((frame._kimi_pending_owners(), kwargs)),
    )

    frame._load_state()

    assert len(recovered) == 1
    owners, kwargs = recovered[0]
    assert kwargs == {}
    assert {
        (owner["chat_id"], owner["session_id"], owner["prompt_id"], owner["role"])
        for owner in owners
    } == {
        (_active_chat_id(frame), session_id, prompt_id, "active"),
        ("chat-archived-pending", "session-archived", "prompt-archived", "active"),
    }


def test_switch_current_chat_restores_all_five_kimi_runtime_fields(frame, monkeypatch):
    _setup_kimi_frame(frame, monkeypatch)
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    frame.active_chat_id = "chat-a"
    frame.current_chat_id = "chat-a"
    frame.active_session_turns = [{"question": "a", "answer_md": "done", "model": "kimi/main", "request_status": "done"}]
    frame._current_chat_state = {"id": "chat-a", "title": "A", "turns": frame.active_session_turns}
    frame.archived_chats = [
        {
            "id": "chat-b",
            "title": "B",
            "model": "kimi/main",
            "turns": [{"question": "b", "answer_md": main.REQUESTING_TEXT, "model": "kimi/main", "request_status": "pending"}],
            "kimi_session_id": "session-b",
            "kimi_turn_id": "turn-b",
            "kimi_turn_active": True,
            "kimi_pending_prompt": "pending-b",
            "kimi_request_queue": [{"prompt_id": "queued-b", "turn_idx": 0}],
            "created_at": 1.0,
            "updated_at": 1.0,
        }
    ]

    assert frame._switch_current_chat("chat-b") is True

    assert frame.active_kimi_session_id == "session-b"
    assert frame.active_kimi_turn_id == "turn-b"
    assert frame.active_kimi_turn_active is True
    assert frame.active_kimi_pending_prompt == "pending-b"
    assert frame.active_kimi_request_queue == [{"prompt_id": "queued-b", "turn_idx": 0}]


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


def test_prompt_owner_key_is_isolated_by_chat_and_session(frame):
    for chat_id, session_id, turn_idx in (("chat-a", "session-a", 0), ("chat-b", "session-b", 1)):
        frame._register_kimi_prompt_owner({
            "chat_id": chat_id, "session_id": session_id, "prompt_id": "same-prompt",
            "owner_prompt_id": "same-prompt", "turn_idx": turn_idx,
            "role": "active", "landed": True,
        })

    assert len([key for key in frame._kimi_prompt_owners if key[2] == "same-prompt"]) == 2
    assert frame._find_kimi_prompt_owner("same-prompt") is None
    assert frame._find_kimi_prompt_owner("same-prompt", session_id="session-a")["chat_id"] == "chat-a"
    assert frame._find_kimi_prompt_owner("same-prompt", session_id="session-b")["chat_id"] == "chat-b"


def test_rebuild_merges_unlanded_owner_from_another_chat(frame):
    frame._register_kimi_prompt_owner({
        "chat_id": "chat-b", "session_id": "session-b", "prompt_id": "pending-submit",
        "owner_prompt_id": "pending-submit", "turn_idx": 0,
        "role": "active", "landed": False,
    })
    frame.active_chat_id = "chat-a"
    frame.current_chat_id = "chat-a"
    frame.active_session_turns = []
    frame._current_chat_state = {"id": "chat-a", "turns": []}
    frame.archived_chats = []

    frame._rebuild_kimi_runtime_state()

    assert frame._find_kimi_prompt_owner(
        "pending-submit", session_id="session-b", chat_id="chat-b", include_unlanded=True
    )["landed"] is False


def test_new_submission_clears_previous_owner_idle_proof(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    monkeypatch.setattr(frame, "_recover_kimi_pending_owners", lambda **_kwargs: None)
    _submit(frame, "first")
    session_id = fake.created_sessions[0]["session_id"]
    first_prompt = fake.submitted[0]["prompt_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id="1"))
    fake.push_event(KimiEvent(
        type="notification", thread_id=session_id, subtype="event.session.work_changed",
        display_kind="session", data={"protocol": {"busy": False}},
    ))
    first_owner = frame._find_kimi_prompt_owner(first_prompt, session_id=session_id)
    assert frame._kimi_idle_proofs[frame._kimi_owner_key_for(first_owner)] == first_owner["generation"]

    _submit(frame, "steered follow-up")

    assert frame._kimi_owner_key_for(first_owner) not in frame._kimi_idle_proofs
    fake.push_event(KimiEvent(
        type="turn_completed", thread_id=session_id, status="completed", text="partial",
        data={"agent_id": "main", "source_kind": "prompt.completed", "prompt_id": first_prompt},
    ))
    assert frame.active_session_turns[0]["request_status"] == "pending"


def test_late_prompt_completed_is_blocked_by_owner_tombstone(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    played = {"count": 0}
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: played.__setitem__("count", played["count"] + 1))
    _submit(frame, "answer once")
    session_id = fake.created_sessions[0]["session_id"]
    prompt_id = fake.submitted[0]["prompt_id"]
    owner = frame._find_kimi_prompt_owner(prompt_id, session_id=session_id)
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id="1"))
    fake.push_event(KimiEvent(
        type="turn_completed", thread_id=session_id, turn_id="1", text="authoritative",
        status="completed", data={"agent_id": "main", "source_kind": "turn.ended", "prompt_id": prompt_id},
    ))
    fake.push_event(KimiEvent(
        type="turn_completed", thread_id=session_id, text="late fallback", status="completed",
        data={"agent_id": "main", "source_kind": "prompt.completed", "prompt_id": prompt_id,
              "idle_verified": True, "owner_generation": owner["generation"]},
    ))

    assert frame.active_session_turns[0]["answer_md"] == "authoritative"
    assert played["count"] == 1


def test_duplicate_queued_questions_are_not_claimed_without_prompt_id(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    fake.steer_result = False
    _submit(frame, "active")
    session_id = fake.created_sessions[0]["session_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id="1"))
    _submit(frame, "duplicate")
    _submit(frame, "duplicate")
    fake.push_event(KimiEvent(type="turn_completed", thread_id=session_id, turn_id="1", text="done", status="completed"))

    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id="2", text="duplicate"))

    assert len(frame._current_chat_state["kimi_request_queue"]) == 2
    assert all(turn["request_status"] == "pending" for turn in frame.active_session_turns[1:])


def test_early_answer_fragments_are_not_truncated_at_legacy_bucket_limit(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    deferred = []
    original_call_after = frame._call_after_if_alive

    def defer_landing(fn, *args, **kwargs):
        if getattr(fn, "__name__", "") == "_apply_kimi_thread_state":
            deferred.append((fn, args, kwargs))
            return None
        return original_call_after(fn, *args, **kwargs)

    monkeypatch.setattr(frame, "_call_after_if_alive", defer_landing)
    _submit(frame, "long early stream")
    session_id = fake.created_sessions[0]["session_id"]
    prompt_id = fake.submitted[0]["prompt_id"]
    for idx in range(40):
        fake.push_event(KimiEvent(
            type="agent_message_delta", thread_id=session_id, turn_id="9", text=str(idx),
            display_kind="assistant", data={"agent_id": "main", "prompt_id": prompt_id},
        ))
    assert len(frame._kimi_early_events[(session_id, prompt_id)]) == 40

    fn, args, kwargs = deferred.pop()
    fn(*args, **kwargs)
    fake.push_event(KimiEvent(
        type="turn_completed", thread_id=session_id, turn_id="9", status="completed",
        data={"agent_id": "main", "source_kind": "turn.ended", "prompt_id": prompt_id},
    ))

    assert frame.active_session_turns[0]["answer_md"] == "".join(str(idx) for idx in range(40))


def test_recovery_uses_one_coordinator_per_session(frame, monkeypatch):
    created = []

    class _DeferredThread:
        def __init__(self, target=None, args=None, kwargs=None, daemon=None):
            created.append((target, args or (), kwargs or {}, daemon))

        def start(self):
            return None

    monkeypatch.setattr(main.threading, "Thread", _DeferredThread)
    for chat_id, prompt_id in (("chat-a", "prompt-a"), ("chat-b", "prompt-b")):
        frame._register_kimi_prompt_owner(
            {
                "chat_id": chat_id,
                "session_id": "shared-session",
                "prompt_id": prompt_id,
                "owner_prompt_id": prompt_id,
                "turn_idx": 0,
                "role": "active",
                "landed": False,
            }
        )

    frame._recover_kimi_pending_owners(
        session_ids={"shared-session"}, original_error="socket reset"
    )
    frame._recover_kimi_pending_owners(
        session_ids={"shared-session"}, original_error="socket reset again"
    )

    assert len(created) == 1
    assert created[0][0] == frame._reconcile_kimi_session_worker
    assert created[0][1] == ("shared-session",)
    assert frame._kimi_recovery_workers == {"shared-session"}
    assert frame._kimi_recovery_intents["shared-session"] == "socket reset again"


def test_authoritative_error_atomically_cleans_alias_state(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "active")
    session_id = fake.created_sessions[0]["session_id"]
    first_prompt = fake.submitted[0]["prompt_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id="10"))
    _submit(frame, "steered alias")
    second_prompt = fake.submitted[1]["prompt_id"]
    owner_keys = {
        frame._kimi_owner_key_for(
            frame._find_kimi_prompt_owner(prompt_id, session_id=session_id)
        )
        for prompt_id in (first_prompt, second_prompt)
    }
    frame._current_chat_state["kimi_request_queue"] = [
        {"prompt_id": second_prompt, "turn_idx": 1}
    ]
    frame.active_kimi_request_queue = frame._current_chat_state["kimi_request_queue"]
    for key in owner_keys:
        frame._kimi_idle_proofs[key] = frame._kimi_prompt_owners[key]["generation"]
        frame._kimi_early_events[(key[1], key[2])] = [CodexEvent(type="agent_message_delta")]
    frame._kimi_recovery_intents[session_id] = "socket reset"

    frame._apply_kimi_error(
        _active_chat_id(frame), "authoritative failure", turn_idx=0, prompt_id=first_prompt
    )

    assert [turn["request_status"] for turn in frame.active_session_turns] == ["failed", "failed"]
    assert [turn["answer_md"] for turn in frame.active_session_turns] == [
        "authoritative failure",
        "authoritative failure",
    ]
    assert owner_keys <= frame._kimi_terminal_tombstones
    assert all(key not in frame._kimi_prompt_owners for key in owner_keys)
    assert all(key not in frame._kimi_idle_proofs for key in owner_keys)
    assert all((key[1], key[2]) not in frame._kimi_early_events for key in owner_keys)
    assert frame._current_chat_state["kimi_request_queue"] == []
    assert session_id not in frame._kimi_recovery_intents


def test_legacy_owner_migration_uses_time_to_disambiguate_repeated_question(frame):
    frame.active_chat_id = "chat-legacy"
    frame.current_chat_id = "chat-legacy"
    frame.active_session_turns = [
        {
            "question": "repeated",
            "answer_md": main.REQUESTING_TEXT,
            "model": "kimi/main",
            "request_status": "pending",
            "created_at": 1000.0,
            "kimi_session_id": "session-legacy",
        }
    ]
    frame._current_chat_state = {"id": "chat-legacy", "turns": frame.active_session_turns}
    owner = {
        "chat_id": "chat-legacy",
        "session_id": "session-legacy",
        "prompt_id": "__legacy__:chat-legacy:0",
        "owner_prompt_id": "__legacy__:chat-legacy:0",
        "turn_idx": 0,
        "question": "repeated",
        "created_at": 1000.0,
        "role": "active",
        "landed": True,
        "legacy": True,
    }
    frame._register_kimi_prompt_owner(owner)

    assert frame._migrate_legacy_kimi_owner(
        owner,
        [
            {"id": "prompt-before", "role": "user", "content": "repeated", "created_at": 999.0},
            {"id": "prompt-after", "role": "user", "content": "repeated", "created_at": 1001.0},
        ],
    ) is None

    migrated = frame._migrate_legacy_kimi_owner(
        owner,
        [
            {"id": "prompt-old", "role": "user", "content": "repeated", "created_at": 800.0},
            {"id": "prompt-exact", "role": "user", "content": "repeated", "created_at": 1002.0},
        ],
    )

    assert migrated["prompt_id"] == "prompt-exact"
    assert migrated["legacy"] is False
    assert frame.active_session_turns[0]["kimi_prompt_id"] == "prompt-exact"
    assert frame._find_kimi_prompt_owner(
        "prompt-exact", session_id="session-legacy", chat_id="chat-legacy"
    )["turn_idx"] == 0


def test_subagent_assistant_delta_is_kept_in_execution_progress(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "main task")
    session_id = fake.created_sessions[0]["session_id"]
    fake.push_event(KimiEvent(
        type="agent_message_delta", thread_id=session_id, turn_id="7",
        text="subagent prose", display_kind="assistant",
        data={"agent_id": "agent-1", "source_kind": "assistant.delta"},
    ))

    assert any(
        "subagent prose" in str(step)
        for step in frame._current_chat_state.get("execution_steps", [])
    )
    assert frame.active_session_turns[0]["answer_md"] == main.REQUESTING_TEXT


def test_non_main_idle_does_not_authorize_prompt_completed(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    recoveries = []
    monkeypatch.setattr(frame, "_recover_kimi_pending_owners", lambda **kwargs: recoveries.append(kwargs))
    _submit(frame, "main task")
    session_id = fake.created_sessions[0]["session_id"]
    prompt_id = fake.submitted[0]["prompt_id"]
    fake.push_event(KimiEvent(
        type="notification", thread_id=session_id,
        subtype="event.session.work_changed", display_kind="session",
        data={"agent_id": "agent-1", "protocol": {"busy": False}},
    ))
    fake.push_event(KimiEvent(
        type="turn_completed", thread_id=session_id, text="partial", status="completed",
        data={"agent_id": "main", "source_kind": "prompt.completed", "prompt_id": prompt_id},
    ))

    assert frame.active_session_turns[0]["request_status"] == "pending"
    assert recoveries


def test_failed_terminal_never_plays_finish_sound(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    played = []
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: played.append(True))
    _submit(frame, "main task")
    session_id = fake.created_sessions[0]["session_id"]
    prompt_id = fake.submitted[0]["prompt_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id="9", data={"agent_id": "main", "prompt_id": prompt_id}))
    fake.push_event(KimiEvent(
        type="turn_completed", thread_id=session_id, turn_id="9", text="failed",
        status="failed", data={"agent_id": "main", "source_kind": "turn.ended", "prompt_id": prompt_id},
    ))

    assert frame.active_session_turns[0]["request_status"] == "failed"
    assert played == []


def test_answer_streams_use_first_seen_order_and_gap_blocks_completion(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    monkeypatch.setattr(frame, "_recover_kimi_pending_owners", lambda **_kwargs: None)
    _submit(frame, "main task")
    session_id = fake.created_sessions[0]["session_id"]
    prompt_id = fake.submitted[0]["prompt_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id="10", data={"agent_id": "main", "prompt_id": prompt_id}))
    for item_id, offset, text in (("z-stream", 0, "first"), ("a-stream", 2, "gap")):
        fake.push_event(KimiEvent(
            type="agent_message_delta", thread_id=session_id, turn_id="10",
            item_id=item_id, text=text, display_kind="assistant",
            data={"agent_id": "main", "source_kind": "assistant.delta", "prompt_id": prompt_id, "offset": offset},
        ))
    fake.push_event(KimiEvent(
        type="turn_completed", thread_id=session_id, turn_id="10", status="completed",
        data={"agent_id": "main", "source_kind": "turn.ended", "prompt_id": prompt_id},
    ))

    assert frame.active_session_turns[0]["request_status"] == "pending"
    assert frame._kimi_answer_parts(_active_chat_id(frame), session_id, prompt_id).startswith("first")


def test_clear_removes_owner_buffers_and_recovery_state(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "main task")
    session_id = fake.created_sessions[0]["session_id"]
    prompt_id = fake.submitted[0]["prompt_id"]
    chat_id = _active_chat_id(frame)
    owner = frame._find_kimi_prompt_owner(prompt_id, session_id=session_id)
    key = frame._kimi_owner_key_for(owner)
    frame._kimi_early_events[(session_id, prompt_id)] = []
    frame._kimi_recovery_intents[session_id] = "recover"
    frame._kimi_turn_answer_parts[(chat_id, session_id, prompt_id, "main")] = ["partial"]

    frame._handle_kimi_clear_command(frame._current_chat_state)

    assert key not in frame._kimi_prompt_owners
    assert (session_id, prompt_id) not in frame._kimi_early_events
    assert session_id not in frame._kimi_recovery_intents
    assert not any(key_[0] == chat_id for key_ in frame._kimi_turn_answer_parts)


@pytest.mark.parametrize(
    ("between", "expected_role", "expected_owner"),
    [
        ([], "alias", "active-prompt"),
        ([{"id": "answer-1", "role": "assistant", "content": "done", "created_at": "2026-01-01T00:00:01Z"}], "queued", "follow-up"),
    ],
)
def test_ambiguous_steer_migration_resolves_alias_or_queue(frame, between, expected_role, expected_owner):
    owner = {
        "chat_id": _active_chat_id(frame), "session_id": "session-1",
        "prompt_id": "follow-up", "owner_prompt_id": "active-prompt",
        "turn_idx": 0, "question": "follow up", "role": "unresolved",
        "candidate_alias": True, "landed": True, "generation": 8,
        "created_at": "2026-01-01T00:00:02Z",
    }
    frame._register_kimi_prompt_owner(owner)
    messages = [
        {"id": "active-prompt", "role": "user", "content": "first", "created_at": "2026-01-01T00:00:00Z"},
        *between,
        {"id": "follow-up", "role": "user", "content": "follow up", "created_at": "2026-01-01T00:00:02Z"},
    ]

    migrated = frame._migrate_legacy_kimi_owner(owner, messages)

    assert migrated["role"] == expected_role
    assert migrated["owner_prompt_id"] == expected_owner


def test_late_recovery_failure_generation_cannot_overwrite_done_turn(frame):
    frame.active_session_turns = [{
        "question": "done", "answer_md": "final", "model": "kimi/main",
        "request_status": "done", "kimi_session_id": "session-1",
        "kimi_prompt_id": "prompt-1",
    }]
    frame._current_chat_state["turns"] = frame.active_session_turns

    frame._apply_kimi_error(
        _active_chat_id(frame), "late failure", 0, None, "prompt-1", 7,
    )

    assert frame.active_session_turns[0]["request_status"] == "done"
    assert frame.active_session_turns[0]["answer_md"] == "final"


def test_recovery_deadline_helper_passes_remaining_budget(frame, monkeypatch):
    observed = []
    monkeypatch.setattr(main.time, "monotonic", lambda: 10.0)

    result = frame._kimi_call_with_deadline(
        lambda value, timeout=None: observed.append(timeout) or value,
        "ok",
        deadline=12.5,
    )

    assert result == "ok"
    assert observed == [2.5]
