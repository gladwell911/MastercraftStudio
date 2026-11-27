import main
import pytest
import time
import wx
from codex_client import CodexEvent
from kimi_server_client import KimiEvent, KimiServerClient, KimiServerError, event_to_payload
from kimi_test_support import (
    TEST_TURN_ID,
    _ImmediateThread,
    _active_chat_id,
    _retry_kimi_interaction_menu,
    _sdk_push,
    _setup_kimi_frame,
    _submit,
)


def test_tool_combo_contains_kimi(frame):
    choices = list(frame.tool_combo.GetItems())
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


@pytest.mark.parametrize("navigation_quiet", [False, True])
def test_turn_completed_reenables_new_chat_and_plays_sound(frame, monkeypatch, navigation_quiet):
    monkeypatch.setattr(wx, "MessageBox", lambda message, *_args, **_kwargs: pytest.fail(str(message)))
    fake = _setup_kimi_frame(frame, monkeypatch)
    played = {"n": 0}
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: played.__setitem__("n", played["n"] + 1))
    _submit(frame, "问题")
    session_id = fake.created_sessions[0]["session_id"]
    assert frame.is_running is True

    turn = frame.active_session_turns[-1]
    turn["created_at"] = time.time() - 1800
    turn["answer_md"] = "答案"
    frame._render_answer_list(refresh_execution=False)
    assert frame._find_answer_row_index(len(frame.active_session_turns) - 1) >= 0
    selected = next(index for index, meta in enumerate(frame.answer_meta) if meta[0] == "question")
    frame.answer_list.SetSelection(selected)
    selected_id = frame._answer_row_id(frame.answer_meta[selected])
    rendered = []
    render = frame._render_answer_list
    monkeypatch.setattr(frame, "_render_answer_list", lambda *args, **kwargs: (rendered.append(True), render(*args, **kwargs))[1])

    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id=TEST_TURN_ID))
    fake.push_event(
        KimiEvent(type="agent_message_delta", thread_id=session_id, turn_id=TEST_TURN_ID, text="答案", display_kind="assistant")
    )
    assert not rendered
    frame._navigation_quiet_until = time.monotonic() + 30 if navigation_quiet else 0
    monkeypatch.setattr(frame, "_schedule_navigation_quiet_flush", lambda: None)
    fake.push_event(
        KimiEvent(type="turn_completed", thread_id=session_id, turn_id=TEST_TURN_ID, status="completed")
    )

    assert frame.is_running is False
    assert frame._active_request_count == 0
    assert frame.new_chat_button.IsEnabled()
    assert played["n"] == 1
    assert frame.active_kimi_turn_active is False
    assert frame.active_session_turns[-1]["answer_md"] == "答案"
    turn = frame.active_session_turns[-1]
    assert turn["answer_at"] >= turn["created_at"]
    completed_at = turn["answer_at"]
    fake.push_event(KimiEvent(type="turn_completed", thread_id=session_id, turn_id=TEST_TURN_ID, status="completed"))
    assert turn["answer_at"] == completed_at
    if navigation_quiet:
        assert not any(meta[0] == "time" and meta[3] == "assistant" for meta in frame.answer_meta)
    owner = frame._answer_refresh_deadline_owner
    if owner is not None:
        frame._flush_accepted_answer_refresh(owner)
    ai = next(index for index, meta in enumerate(frame.answer_meta) if meta[0] == "ai")
    assert frame.answer_meta[ai - 1][0] == "time"
    assert frame.answer_meta[ai - 1][3] == "assistant"
    assert frame.answer_list.GetString(ai - 1) == main.wechat_time_label(completed_at, time.time())
    assert frame._answer_row_id(frame.answer_meta[frame.answer_list.GetSelection()]) == selected_id
    assert frame._accepted_answer_is_visible(turn)


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


def test_kimi_accepted_answer_notifies_and_saves_before_ui_failure(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "synthetic question")
    sid = fake.created_sessions[0]["session_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=sid, turn_id="10"))
    fake.push_event(KimiEvent(type="agent_message_delta", thread_id=sid, turn_id="10", text="synthetic answer", display_kind="assistant"))
    played, saved = [], []
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: played.append(True))
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: saved.append(True))
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic UI failure")
    monkeypatch.setattr(frame, "_request_kimi_chat_information", fail)
    completion = main.CodexEvent(type="turn_completed", thread_id=sid, turn_id="10", status="completed")
    with pytest.raises(RuntimeError, match="synthetic UI"):
        frame._on_kimi_event(completion)
    assert frame.active_session_turns[0]["request_status"] == "done"
    assert played == [True]
    assert saved
    frame._on_kimi_event(completion)
    assert played == [True]


def test_v2_real_client_transcript_to_mc_final_and_rest_replay(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "synthetic question")
    sid, prompt = fake.created_sessions[0]["session_id"], fake.submitted[0]["prompt_id"]
    played = []
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: played.append(True))
    transport = KimiServerClient(start_reader_thread=False)
    transport._handle_ws_message({"type": "server_hello", "payload": {"protocol_version": 2}})
    turn = {"kind": "turn", "turnId": "v2-turn", "triggerPromptId": prompt, "ordinal": 0,
            "origin": {"kind": "user"}, "state": "completed", "steps": [
                {"kind": "step", "stepId": "st", "turnId": "v2-turn", "ordinal": 0,
                 "state": "completed", "frames": [{"kind": "text", "role": "assistant", "frameId": "f", "text": "synthetic final"}]}]}
    message = {"type": "transcript.reset", "session_id": sid, "payload": {
        "agent_id": "main", "seq": 5, "snapshot": {"items": [turn]}}}
    for _ in range(2):
        transport._handle_ws_message(message)
        for row in transport.drain_pending_messages(100):
            if row["type"] == "event":
                fake.push_event(KimiEvent(**row["payload"]["event"]))
    assert frame.active_session_turns[0]["answer_md"] == "synthetic final"
    assert frame.active_session_turns[0]["request_status"] == "done"
    # A REST recovery result uses the same authoritative prompt identity.
    fake.push_event(transport._v2_final_event(sid, turn, "synthetic final"))
    assert played == [True]


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
    transport = KimiServerClient()
    _sdk_push(frame, transport, "session-bg", "turn.step.started", 1, chat_id="chat-bg", turnId="bg-turn", step=1, stepId="background-step")
    _sdk_push(frame, transport, "session-bg", "thinking.delta", 1, chat_id="chat-bg", turnId="bg-turn", offset=0, delta="The")
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
    assert [step["list_text"] for step in background_steps if step.get("kimi_summary")] == ["The"]
    assert rendered["n"] == 0
    assert refreshed["n"] == 0
    frame.view_mode = "history"
    frame.view_history_id = "chat-bg"
    frame._current_chat_state["detail_panel_mode"] = "execution"
    frame._rebuild_execution_list_from_state()
    assert "The" in [frame.execution_list.GetString(idx) for idx in range(frame.execution_list.GetCount())]


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


def test_startup_failure_callback_cannot_fail_reused_turn_index(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    callbacks = []
    monkeypatch.setattr(frame, "_call_after_if_alive", lambda fn, *a, **kw: callbacks.append((fn, a, kw)))
    fake.start = lambda: (_ for _ in ()).throw(ConnectionError("old startup failed"))
    _submit(frame, "old request")
    old_turn = frame.active_session_turns[0]
    replacement = dict(old_turn, question="new request", answer_md=main.REQUESTING_TEXT, request_status="pending")
    frame.active_session_turns[:] = [replacement]
    for fn, args, kwargs in callbacks:
        fn(*args, **kwargs)
    assert replacement["request_status"] == "pending"
    assert replacement["answer_md"] == main.REQUESTING_TEXT


def test_startup_exhaustion_ends_turn_and_next_send_submits(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    fake.start = lambda: (_ for _ in ()).throw(ConnectionError("controlled startup exhausted"))
    _submit(frame, "failed request")
    assert frame.active_session_turns[-1]["request_status"] == "failed"
    assert "controlled startup exhausted" in frame.active_session_turns[-1]["answer_md"]
    assert not frame.is_running
    fake.start = lambda: None
    _submit(frame, "recovered request")
    assert len(fake.submitted) == 1
    assert fake.submitted[0]["blocks"][0]["text"].endswith("recovered request")
    assert frame.active_session_turns[-1]["request_status"] == "pending"


def test_kimi_successful_startup_callback_rejects_reused_turn(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    callbacks = []
    monkeypatch.setattr(frame, "_call_after_if_alive", lambda fn, *a, **kw: callbacks.append((fn,a,kw)))
    _submit(frame, "old request")
    replacement = dict(frame.active_session_turns[0], question="new request", request_status="pending")
    frame.active_session_turns[:] = [replacement]
    for fn, a, kw in callbacks:
        fn(*a, **kw)
    assert not replacement.get("kimi_session_id")
    assert not frame.active_kimi_session_id


def test_model_startup_callback_cannot_revive_terminal_turn(frame, monkeypatch):
    _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "request")
    chat_id = _active_chat_id(frame)
    request = frame._capture_model_startup_request(chat_id, 0)
    turn = frame.active_session_turns[0]
    turn["request_status"] = "done"
    turn["answer_md"] = "final"
    frame._finish_model_startup_failure(chat_id, 0, request, "late failure", "kimi/main")
    assert turn["request_status"] == "done"
    assert turn["answer_md"] == "final"


@pytest.mark.parametrize("offscreen", [False, True])
def test_durable_clear_resend_retires_old_runtime_and_completes(frame, monkeypatch, offscreen):
    fake = _setup_kimi_frame(frame, monkeypatch)
    frame._chat_store_enabled = True
    _submit(frame, "first question")
    chat_id = _active_chat_id(frame)
    old_session = fake.submitted[-1]["session_id"]
    old_prompt = fake.submitted[-1]["prompt_id"]
    old_owner = frame._find_kimi_prompt_owner(old_prompt, session_id=old_session)
    old_key = frame._kimi_owner_key_for(old_owner)
    frame._kimi_recovery_intents[old_session] = "recover"
    frame._kimi_turn_answer_parts[(chat_id, old_session, old_prompt, "main")] = ["stale"]
    frame._persist_chat_history_to_store()
    if offscreen:
        frame._on_new_chat_clicked(None)
        _submit(frame, "other chat")
        other_id = _active_chat_id(frame)
        other_owner = dict(frame._kimi_active_turns[other_id])
        assert frame._clear_context_for_chat_id(chat_id, auto_resend_first=True) == "cleared"
    else:
        assert frame._clear_context_and_start_new_chat(auto_resend_first=True)
    new_session = fake.submitted[-1]["session_id"]
    assert new_session != old_session
    assert not fake.steer_calls
    assert old_key not in frame._kimi_prompt_owners
    assert old_session not in frame._kimi_recovery_intents
    fake.push_event(KimiEvent(type="turn_started", thread_id=new_session, turn_id="new-turn"))
    fake.push_event(KimiEvent(type="agent_message_delta", thread_id=new_session,
                             turn_id="new-turn", text="fresh answer", display_kind="assistant"))
    fake.push_event(KimiEvent(type="turn_completed", thread_id=new_session,
                             turn_id="new-turn", status="completed"))
    chat = frame._find_archived_chat(chat_id) if offscreen else frame._current_chat_state
    assert chat["turns"][0]["answer_md"] == "fresh answer"
    assert chat["turns"][0]["request_status"] == "done"
    operation = frame.chat_store.get_clear_operation(chat["turns"][0]["clear_operation_id"])
    assert operation["state"] == "completed_with_resend"
    fake.push_event(KimiEvent(type="turn_completed", thread_id=old_session,
                             turn_id="old-turn", status="completed", text="stale answer"))
    assert chat["turns"][0]["answer_md"] == "fresh answer"
    if offscreen:
        assert _active_chat_id(frame) == other_id
        assert frame._kimi_active_turns[other_id] == other_owner


def test_startup_expired_deadline_reports_timeout(frame, monkeypatch):
    monkeypatch.setattr(main.time, "monotonic", lambda: 10.0)
    with pytest.raises(TimeoutError, match="deadline exhausted"):
        frame._start_kimi_client_with_retry(object(), deadline=9.0)


def test_provider_error_with_reset_text_is_terminal_not_transport_retry(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "controlled provider failure")
    session_id = fake.created_sessions[0]["session_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id=TEST_TURN_ID))
    fake.push_event(KimiEvent(type="error", thread_id=session_id, turn_id=TEST_TURN_ID,
                             text="provider failed with 10054", subtype="provider_failure"))
    assert frame.active_session_turns[-1]["request_status"] == "failed"
    assert "10054" in frame.active_session_turns[-1]["request_error"]
    assert len(fake.submitted) == 1


def test_overlapping_kimi_followup_uses_origin_chat_after_switch(frame, monkeypatch, tmp_path):
    real_thread = main.threading.Thread
    fake = _setup_kimi_frame(frame, monkeypatch)
    chat = {"id": "chat-a", "title": "A"}
    a_file = tmp_path / "A-attachment.txt"
    b_file = tmp_path / "B-attachment.txt"
    a_file.write_text("A file", encoding="utf-8")
    b_file.write_text("B file", encoding="utf-8")
    turns = [
        {"question": "A-history", "request_status": "pending", "model": "kimi/main"},
        {"question": "A-followup", "request_status": "pending", "model": "kimi/main",
         "attachments": [{"status": "success", "path": str(a_file)}]},
    ]
    frame._current_chat_state = chat
    frame.active_chat_id = frame.current_chat_id = chat["id"]
    frame.active_session_turns = turns
    frame.active_kimi_session_id = ""
    archived_chat = {**chat, "turns": list(turns)}
    monkeypatch.setattr(frame, "_find_archived_chat", lambda chat_id: archived_chat if chat_id == chat["id"] else None)
    frame.archived_chats.append(archived_chat)
    callbacks = []
    real_call_after = frame._call_after_if_alive

    def marshal(fn, *args):
        if main.threading.current_thread() is main.threading.main_thread():
            return real_call_after(fn, *args)
        callbacks.append((fn, args))
        return True

    monkeypatch.setattr(frame, "_call_after_if_alive", marshal)
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)
    first_started = main.threading.Event()
    second_waiting = main.threading.Event()
    release_first = main.threading.Event()
    create_session = fake.create_session
    submission_lock = frame._kimi_submission_lock

    def create(**kwargs):
        first_started.set()
        assert release_first.wait(5)
        return create_session(**kwargs)

    class ObservedLock:
        def __init__(self, lock):
            self.lock = lock

        def acquire(self):
            if main.threading.current_thread().name == "kimi-followup":
                second_waiting.set()
            return self.lock.acquire()

        def release(self):
            self.lock.release()

    monkeypatch.setattr(fake, "create_session", create)
    frame._kimi_submission_locks[chat["id"]] = ObservedLock(submission_lock(chat["id"]))
    first = real_thread(target=frame._run_kimi_turn_worker, args=(chat["id"], 0, "A-history", "kimi/main"))
    second = real_thread(name="kimi-followup", target=frame._run_kimi_turn_worker,
                         args=(chat["id"], 1, "A-followup", "kimi/main"))
    first.start()
    try:
        assert first_started.wait(5)
        second.start()
        assert second_waiting.wait(5)
        frame._current_chat_state = {"id": "chat-b"}
        frame.active_chat_id = frame.current_chat_id = "chat-b"
        frame.active_session_turns = [
            {"question": "B-history"},
            {"attachments": [{"status": "success", "path": str(b_file)}]},
        ]
    finally:
        release_first.set()
        first.join(5)
        if second.ident is not None:
            second.join(5)

    assert not first.is_alive() and not second.is_alive()
    assert len(fake.submitted) == 2
    assert [post["session_id"] for post in fake.submitted] == ["session-fake-1", "session-fake-1"]
    assert "A-followup" in str(fake.submitted[1]["blocks"])
    assert "A-attachment" in str(fake.submitted[1]["blocks"])
    assert "B-history" not in str(fake.submitted)
    assert "B-attachment" not in str(fake.submitted)
    assert fake.steer_calls == [("session-fake-1", ["prompt-2"])]
    assert {key[0] for key in frame._kimi_prompt_owners} == {"chat-a"}
    for callback, args in callbacks:
        callback(*args)
    fake.push_event(KimiEvent(type="turn_started", thread_id="session-fake-1", turn_id="turn-a",
                              data={"prompt_id": "prompt-1"}))
    fake.push_event(KimiEvent(type="turn_completed", thread_id="session-fake-1", turn_id="turn-a",
                              status="completed", text="A answer", data={"prompt_id": "prompt-1"}))
    b_turns = frame.active_session_turns
    assert all(not turn.get("answer_md") for turn in b_turns)
    assert turns[0]["answer_md"] == "A answer"
    assert turns[1]["answer_alias_of"] == turns[0]["answer_identity"]
    frame._save_state()
    assert frame.chat_store.load_turns(chat["id"])[0]["answer_md"] == "A answer"


def test_legacy_known_kimi_prompt_id_takes_priority_over_closer_matching_text(frame):
    owner = {"chat_id": "c", "session_id": "s", "prompt_id": "older",
             "question": "same", "created_at": 104, "turn_idx": 0}
    messages = [{"role": "user", "id": "older", "created_at": 100, "content": "same"},
                {"role": "user", "id": "nearer", "created_at": 104, "content": "same"}]
    assert frame._migrate_legacy_kimi_owner(owner, messages)["prompt_id"] == "older"


def test_kimi_clear_between_generation_check_and_publish_cannot_leak_session(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    original_is_current = frame._model_startup_request_is_current
    cleared = []

    def interleave(chat_id, turn_idx, request):
        is_current = original_is_current(chat_id, turn_idx, request)
        if is_current and not cleared and fake.created_sessions:
            frame.new_chat_button.Enable()
            cleared.append(chat_id)
            assert frame._clear_context_and_start_new_chat(auto_resend_first=True)
        return is_current

    monkeypatch.setattr(frame, "_model_startup_request_is_current", interleave)
    _submit(frame, "old generation")
    assert cleared == [_active_chat_id(frame)]
    assert frame._kimi_submission_sessions[cleared[0]] == "session-fake-2"
    assert [entry["session_id"] for entry in fake.created_sessions] == ["session-fake-1", "session-fake-2"]
    assert [entry["session_id"] for entry in fake.submitted] == ["session-fake-2"]


def test_slash_clear_acceptance_retires_before_delayed_worker(frame, monkeypatch):
    native_thread = main.threading.Thread
    fake = _setup_kimi_frame(frame, monkeypatch)
    waiting = main.threading.Event()
    release = main.threading.Event()
    completion = []
    clear_threads = []
    call_after = frame._call_after_if_alive

    def delay_clear_worker(*, target, args=(), daemon=None, **kwargs):
        if target != frame._run_kimi_local_command_worker or args[2] != "clear":
            return _ImmediateThread(target=target, args=args, daemon=daemon, **kwargs)

        def run_clear():
            waiting.set()
            if release.wait(5):
                target(*args)

        worker = native_thread(target=run_clear, daemon=daemon)
        clear_threads.append(worker)
        return worker

    def queue_worker_completion(callback, *args, **kwargs):
        if callback == frame._on_done and main.threading.current_thread() in clear_threads:
            completion.append((callback, args))
            return True
        return call_after(callback, *args, **kwargs)

    monkeypatch.setattr(main.threading, "Thread", delay_clear_worker)
    monkeypatch.setattr(frame, "_call_after_if_alive", queue_worker_completion)
    _submit(frame, "first question")
    old_session = fake.submitted[0]["session_id"]
    _submit(frame, "/clear")
    try:
        assert waiting.wait(5)
        assert frame.active_kimi_session_id == ""
        assert frame.active_session_turns[1]["request_status"] == "pending"
        _submit(frame, "next question")
        chat_id = _active_chat_id(frame)
        new_session = fake.submitted[1]["session_id"]
        assert new_session != old_session
        new_owner = dict(frame._kimi_active_turns[chat_id])
        assert new_owner["prompt_id"] == fake.submitted[1]["prompt_id"]
    finally:
        release.set()
        for worker in clear_threads:
            worker.join(5)
    assert len(completion) == 1
    callback, args = completion[0]
    callback(*args)
    assert frame._kimi_active_turns[chat_id] == new_owner
    assert frame.active_kimi_session_id == new_session
    assert len(fake.submitted) == 2
    assert frame.active_session_turns[2]["request_status"] == "pending"
    assert "Kimi Code 清理" in frame.active_session_turns[1]["answer_md"]
    frame._save_state()
    assert "Kimi Code 清理" in frame.chat_store.load_turns(chat_id)[1]["answer_md"]


def test_kimi_startup_save_does_not_hold_another_chats_submission_lock(frame, monkeypatch):
    native_thread = main.threading.Thread
    fake = _setup_kimi_frame(frame, monkeypatch)
    fake.protocol_version = 2
    saving = main.threading.Event()
    other_chat_entered = main.threading.Event()
    save_observations = []
    save = frame._save_state

    def acquire_other_chat():
        if saving.wait(5):
            with frame._kimi_submission_lock("other-chat"):
                other_chat_entered.set()

    def slow_unresolved_save(*args, **kwargs):
        if not saving.is_set() and any(turn.get("kimi_unresolved_submission") for turn in frame.active_session_turns):
            saving.set()
            save_observations.append(other_chat_entered.wait(2))
        return save(*args, **kwargs)

    def lose_response(_session_id, _blocks, *, prompt_id):
        raise KimiServerError("response lost", result_unknown=True)

    monkeypatch.setattr(fake, "submit_prompt", lose_response)
    monkeypatch.setattr(frame, "_recover_kimi_pending_owners", lambda **_kwargs: None)
    monkeypatch.setattr(frame, "_save_state", slow_unresolved_save)
    worker = native_thread(target=acquire_other_chat)
    worker.start()
    try:
        _submit(frame, "question with a lost POST response")
    finally:
        saving.set()
        worker.join(5)
    assert save_observations == [True]
    assert frame.active_session_turns[0]["kimi_unresolved_submission"] is True


def test_kimi_retry_expiry_during_modal_preserves_other_feedback(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "start")
    chat_id = _active_chat_id(frame)
    session_id = frame.active_kimi_session_id
    key = (session_id, "expiring")
    other_key = (session_id, "other")
    now = [100.0]
    monkeypatch.setattr(main.time, "time", lambda: now[0])
    for interaction_key, params in ((key, {"expires_at": 101}), (other_key, {})):
        request = {"state": "responding", "chat_id": chat_id, "kind": "approval", "params": params}
        frame._kimi_interactions[interaction_key] = request
        frame._complete_kimi_interaction_attempt(
            interaction_key, request, "responding", "retryable", error=interaction_key[1],
        )

    def expire_in_modal(_dialog):
        now[0] = 102.0
        return wx.ID_OK

    monkeypatch.setattr(main.CodexUserInputDialog, "ShowModal", expire_in_modal)
    monkeypatch.setattr(fake, "answer_interaction", lambda *_args: pytest.fail("expired request posted"), raising=False)
    _retry_kimi_interaction_menu(frame)
    assert frame._kimi_interactions[key]["state"] == "ended"
    assert list(frame._current_chat_state["kimi_interaction_errors"]) == ["/".join(other_key)]
    assert "other" in frame.GetStatusBar().GetStatusText()
    _submit(frame, "/clear")
    assert frame._kimi_interactions[other_key]["state"] == "ended"
    assert "kimi_interaction_errors" not in frame._current_chat_state
    assert "重试 Kimi 请求" not in frame.GetStatusBar().GetStatusText()
    frame._on_chat_information_menu_open(wx.MenuEvent(wx.wxEVT_MENU_OPEN))
    assert not frame.GetMenuBar().FindItemById(frame._kimi_retry_interaction_menu_id).IsEnabled()


def test_kimi_retry_menu_reaches_each_uncertain_request_after_start_failure(frame, monkeypatch, tmp_path):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "start")
    chat_id = _active_chat_id(frame)
    session_id = frame.active_kimi_session_id
    queries = []
    for interaction_id in ("first", "second"):
        frame._kimi_interactions[(session_id, interaction_id)] = {
            "state": "uncertain", "chat_id": chat_id, "agent_id": "main", "params": {},
        }

    def query(*args):
        queries.append(args)
        return ""

    monkeypatch.setattr(fake, "interaction_state", query, raising=False)
    monkeypatch.setattr(fake, "answer_interaction", lambda *_args: pytest.fail("recheck posted an answer"), raising=False)
    broken = KimiServerClient(launch_command=[str(tmp_path / "missing.exe")])
    with monkeypatch.context() as startup:
        startup.setattr(frame, "_ensure_kimi_client", lambda: broken)
        _retry_kimi_interaction_menu(frame)
    assert frame._kimi_interactions[(session_id, "first")]["state"] == "uncertain"
    assert "重试 Kimi 请求" in frame.GetStatusBar().GetStatusText()
    for _ in range(3):
        _retry_kimi_interaction_menu(frame)
    assert queries == [
        (session_id, "second", "main"), (session_id, "first", "main"), (session_id, "second", "main"),
    ]
