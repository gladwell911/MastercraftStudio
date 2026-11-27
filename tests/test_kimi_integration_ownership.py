import main
import pytest
from types import SimpleNamespace
from codex_client import CodexEvent
from kimi_history import KimiHistoryReader
from kimi_server_client import KimiEvent, KimiServerClient, KimiServerError
from kimi_test_support import (
    TEST_TURN_ID,
    _active_chat_id,
    _setup_kimi_frame,
    _submit,
    running_kimi_control_server,
)
from native_history import NativeSessionIdentity, NativeSessionSnapshot


def test_interleaved_same_turn_id_routes_by_session(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    sounds = []
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: sounds.append("reply"))
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

    frame._on_kimi_event(events[-1])

    assert current_turn["answer_md"] == "current answer"
    assert current_turn["request_status"] == "done"
    assert background_turn["answer_md"] == "background answer"
    assert background_turn["request_status"] == "done"
    assert sounds == ["reply", "reply"]
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


def test_prompt_events_buffer_until_owner_is_persisted_then_replay_in_order(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    deferred = []
    original_call_after = frame._call_after_if_alive

    def defer_owner_landing(fn, *args, **kwargs):
        if getattr(fn, "__name__", "") in {"_apply_kimi_startup_state", "_apply_kimi_thread_state"}:
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
    assert interrupted_turn["request_status"] == "cancelled"
    assert "已中断" in str(interrupted_turn["answer_md"] or "")
    assert frame.is_running is False
    assert frame.new_chat_button.IsEnabled()


def test_native_history_distinguishes_cancelled_from_failed():
    items = [
        {"kind": "turn", "turnId": "1", "triggerPromptId": "prompt-1", "state": "cancelled"},
        {"kind": "turn", "turnId": "2", "triggerPromptId": "prompt-2", "state": "failed",
         "error": "tool failed"},
    ]
    client = SimpleNamespace(get_transcript=lambda *_args, **_kwargs: {"items": items})
    reader = KimiHistoryReader(client, "default", lambda _item: True)
    identity = NativeSessionIdentity("default", "kimi", "qa-home", "qa-session")
    snapshot = NativeSessionSnapshot(identity, "qa-account", "kimi/main", "qa-cwd", "qa-title")

    reader.read_session(snapshot)

    assert [(row["request_status"], row["answer_md"]) for row in snapshot.turns] == [
        ("cancelled", "Kimi Code 已中断。"), ("failed", "tool failed"),
    ]


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
    assert frame.active_session_turns[1]["answer_md"] == ""
    assert frame.active_session_turns[1]["answer_alias_of"] == frame.active_session_turns[0]["answer_identity"]
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


@pytest.mark.parametrize("first_source", ["reset", "rest"])
def test_v2_steered_rest_and_reset_complete_alias_once(frame, monkeypatch, first_source):
    fake = _setup_kimi_frame(frame, monkeypatch)
    played = []
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: played.append(True))
    _submit(frame, "owner request")
    sid, prompt = fake.created_sessions[0]["session_id"], fake.submitted[0]["prompt_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=sid, turn_id="steered-turn"))
    _submit(frame, "follow-up request")
    assert len(fake.steer_calls) == 1
    transport = KimiServerClient(start_reader_thread=False)
    transport.protocol_version = 2
    snapshot = {"items": [{"kind": "turn", "turnId": "steered-turn", "triggerPromptId": prompt,
        "state": "completed", "steps": [{"stepId": "st", "ordinal": 1,
            "state": "interrupted", "endReason": "steered", "frames": [
                {"kind": "text", "role": "assistant", "frameId": "old", "text": "pre-steer partial"},
                {"kind": "text", "role": "user", "frameId": "user", "text": "follow-up request"},
                {"kind": "thinking", "frameId": "thought", "text": "reasoning"},
                {"kind": "text", "role": "assistant", "frameId": "final", "text": "public steered final"}]}]}]}
    monkeypatch.setattr(transport, "get_transcript", lambda *a, **k: {**snapshot, "has_more": False})
    reset = {"type": "transcript.reset", "session_id": sid, "payload": {
        "agent_id": "main", "seq": 5, "snapshot": snapshot}}
    try:
        if first_source == "rest":
            fake.push_event(transport.reconcile_v2(sid, prompt))
        else:
            transport._handle_ws_message(reset)
            for row in transport.drain_pending_messages(100):
                if row["type"] == "event":
                    fake.push_event(KimiEvent(**row["payload"]["event"]))
        frame._flush_chat_state_save()
        chat_id = _active_chat_id(frame)
        stored = frame.chat_store.load_chat(chat_id)["turns"]
        assert [t["request_status"] for t in stored] == ["done", "done"]
        assert [t["answer_md"] for t in stored] == ["public steered final", ""]
        assert stored[1]["answer_alias_of"] == stored[0]["answer_identity"]
        read_state = frame.chat_store.get_chat_read_state(chat_id)
        for _ in range(2):
            transport._handle_ws_message(reset)
            for row in transport.drain_pending_messages(100):
                if row["type"] == "event":
                    fake.push_event(KimiEvent(**row["payload"]["event"]))
            fake.push_event(transport.reconcile_v2(sid, prompt))
        frame._flush_chat_state_save()
        assert frame.chat_store.load_chat(chat_id)["turns"] == stored
        assert frame.chat_store.get_chat_read_state(chat_id) == read_state
        assert played == [True]
        assert len(fake.submitted) == 2
    finally:
        transport.close()


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


@pytest.mark.parametrize("result_unknown", [False, True])
def test_clear_resend_rejects_old_submit_return_before_runtime_registration(frame, monkeypatch, result_unknown):
    fake = _setup_kimi_frame(frame, monkeypatch)
    frame._chat_store_enabled = True
    _submit(frame, "first question")
    old_session = fake.submitted[-1]["session_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=old_session, turn_id="first-turn"))
    fake.push_event(KimiEvent(type="agent_message_delta", thread_id=old_session,
                             turn_id="first-turn", text="first answer", display_kind="assistant"))
    fake.push_event(KimiEvent(type="turn_completed", thread_id=old_session,
                             turn_id="first-turn", status="completed"))
    frame._persist_chat_history_to_store()
    chat_id = _active_chat_id(frame)
    original_submit = fake.submit_prompt
    observed = {}
    def held_submit(session_id, blocks):
        prompt_id = original_submit(session_id, blocks)
        if session_id != old_session:
            return prompt_id
        # Keep the old HTTP result outstanding while the actual clear/resend
        # runs through the other session lock and lands its new owner.
        observed["old_prompt"] = prompt_id
        assert frame._clear_context_and_start_new_chat(auto_resend_first=True)
        observed["new_owner"] = dict(frame._kimi_active_turns[chat_id])
        observed["new_turn"] = dict(frame.active_session_turns[0])
        if result_unknown:
            raise KimiServerError("old response lost", result_unknown=True)
        return prompt_id
    monkeypatch.setattr(fake, "submit_prompt", held_submit)
    monkeypatch.setattr(frame, "_recover_kimi_pending_owners", lambda **_kwargs: pytest.fail("retired request cannot recover"))
    _submit(frame, "old outstanding request")
    assert frame._kimi_active_turns[chat_id] == observed["new_owner"]
    assert frame.active_session_turns[0] == observed["new_turn"]
    assert not any(key[1] == old_session for key in frame._kimi_prompt_owners)
    assert old_session not in frame._kimi_pending_submissions
    new_session = observed["new_owner"]["session_id"]
    assert new_session != old_session
    fake.push_event(KimiEvent(type="turn_started", thread_id=new_session, turn_id="fresh-turn"))
    fake.push_event(KimiEvent(type="agent_message_delta", thread_id=new_session,
                             turn_id="fresh-turn", text="fresh final", display_kind="assistant"))
    fake.push_event(KimiEvent(type="turn_completed", thread_id=new_session,
                             turn_id="fresh-turn", status="completed"))
    frame._navigation_quiet_until = 0
    frame._flush_accepted_answer_refresh(frame._answer_refresh_deadline_owner)
    assert frame.active_session_turns[0]["request_status"] == "done"
    assert frame.active_session_turns[0]["answer_md"] == "fresh final"
    assert sum("fresh final" in row for row in frame.answer_list.GetStrings()) == 1


@pytest.mark.parametrize("aborted", [True, False], ids=["accepted", "refused"])
def test_v2_stop_exact_prompt_http_and_cancelled_terminal(frame, monkeypatch, running_kimi_control_server, aborted):
    fake = _setup_kimi_frame(frame, monkeypatch)
    client, requests_seen, _, abort_results = running_kimi_control_server
    abort_results.append(aborted)
    fake.protocol_version = 2
    monkeypatch.setattr(frame, "_recover_kimi_pending_owners", lambda **_kwargs: None)
    fake.abort = client.abort
    posted = []

    def submit(session, blocks, *, prompt_id):
        posted.append((session, prompt_id))
        return prompt_id

    fake.submit_prompt = submit
    _submit(frame, "task waiting to start")
    sid, pid = posted[0]
    _submit(frame, "/stop")
    assert requests_seen == []
    fake.push_event(KimiEvent(type="turn_started", thread_id=sid, turn_id="t1", data={"prompt_id": pid}))
    _submit(frame, "/stop")
    assert requests_seen == [(f"/api/v1/sessions/{sid}/prompts/{pid}:abort", None)]
    stop_turn = frame.active_session_turns[-1]
    if aborted:
        assert "已受理" in stop_turn["answer_md"]
    else:
        assert stop_turn["request_status"] == "failed"
        assert "did not accept" in stop_turn["answer_md"]
    assert frame.active_session_turns[0]["request_status"] == "pending"
    expected_answer = "Kimi Code 已中断。" if aborted else "natural answer"
    fake.push_event(KimiEvent(type="turn_completed", thread_id=sid, turn_id="t1",
                              status="cancelled" if aborted else "completed",
                              text="" if aborted else expected_answer,
                              data={"prompt_id": pid, "source_kind": "transcript.terminal", "stream_complete": True}))
    assert frame.active_session_turns[0]["answer_md"] == expected_answer
    expected_status = "cancelled" if aborted else "done"
    assert frame.active_session_turns[0]["request_status"] == expected_status
    owner = _active_chat_id(frame)
    frame._persist_chat_history_to_store()
    assert frame.chat_store.load_turns(owner)[0]["request_status"] == expected_status
    status, history = frame._remote_api_history_read_ui({"chat_id": owner, "limit": 10})
    assert status == 200
    assert history["chat"]["turns"][0]["request_status"] == expected_status
    fake.push_event(KimiEvent(type="turn_completed", thread_id=sid, turn_id="t1", status="completed",
                              text="late duplicate", data={"prompt_id": pid}))
    _submit(frame, "/stop")
    assert frame.active_session_turns[0]["answer_md"] == expected_answer
    assert len(requests_seen) == 1


@pytest.mark.parametrize("result_unknown", [False, True], ids=["confirmed", "unknown"])
@pytest.mark.parametrize("clear_at", ["reply", "owner-registration", "early-event-claim", "ui"])
def test_slash_clear_retires_in_flight_submission(frame, monkeypatch, result_unknown, clear_at):
    fake = _setup_kimi_frame(frame, monkeypatch)
    fake.protocol_version = 2
    posts = []
    queued_ui = []
    queued_worker = []
    new_owner = {}

    def clear_and_send():
        _submit(frame, "/clear")
        assert frame.active_kimi_session_id == ""
        assert "Kimi Code 清理" in frame.active_session_turns[-1]["answer_md"]
        _submit(frame, "question after clear")
        new_owner.update(frame._kimi_active_turns[_active_chat_id(frame)])

    def submit(session_id, _blocks, *, prompt_id):
        posts.append((session_id, prompt_id))
        if len(posts) == 1:
            if clear_at == "reply":
                clear_and_send()
            if clear_at == "early-event-claim":
                fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id="old-turn",
                                          data={"prompt_id": prompt_id}))
            if result_unknown:
                raise KimiServerError("response lost", result_unknown=True)
        return prompt_id

    register = frame._register_kimi_prompt_owner

    def register_owner(owner, **kwargs):
        if clear_at in {"owner-registration", "early-event-claim"} and owner["session_id"] == posts[0][0]:
            clear_and_send()
        return register(owner, **kwargs)

    call_after = frame._call_after_if_alive

    def queue_startup(callback, *args, **kwargs):
        if clear_at == "ui" and callback == frame._apply_kimi_startup_state and len(posts) == 1:
            queued_ui.append((callback, args))
            return True
        return call_after(callback, *args, **kwargs)

    monkeypatch.setattr(fake, "submit_prompt", submit)
    monkeypatch.setattr(frame, "_register_kimi_prompt_owner", register_owner)
    monkeypatch.setattr(frame, "_call_after_if_alive", queue_startup)
    monkeypatch.setattr(frame, "_recover_kimi_pending_owners", lambda **_kwargs: None)
    with monkeypatch.context() as startup:
        startup.setattr(frame, "_start_kimi_worker_for_turn", lambda *args: queued_worker.append(args))
        _submit(frame, "question before clear")
    assert len(queued_worker) == 1
    frame._run_kimi_turn_worker(*queued_worker[0])
    if clear_at == "ui":
        assert len(queued_ui) == 1
        clear_and_send()
        callback, args = queued_ui.pop()
        callback(*args)

    (old_session, old_prompt), (new_session, new_prompt) = posts
    chat_id = _active_chat_id(frame)
    assert old_session != new_session
    assert frame._current_chat_state["kimi_session_id"] == new_session
    assert frame.active_kimi_session_id == new_session
    assert frame._kimi_submission_sessions[chat_id] == new_session
    assert frame._kimi_active_turns[chat_id] == new_owner
    assert set(frame._kimi_prompt_owners) == {(chat_id, new_session, new_prompt)}
    assert old_session not in frame._kimi_pending_submissions
    assert not any(key[0] == old_session for key in frame._kimi_early_events)
    assert frame._find_kimi_prompt_owner(old_prompt, session_id=old_session, include_unlanded=True) is None
    assert [turn["question"] for turn in frame.active_session_turns] == [
        "question before clear", "/clear", "question after clear",
    ]
    assert frame.active_session_turns[0]["request_status"] == "failed"
    assert "Kimi Code 清理" in frame.active_session_turns[1]["answer_md"]
    frame._save_state()
    stored_turns = frame.chat_store.load_turns(chat_id)
    assert stored_turns[0]["request_status"] == "failed"
    assert not stored_turns[0].get("kimi_session_id")
    assert "Kimi Code 清理" in stored_turns[1]["answer_md"]
    frame._rebuild_kimi_runtime_state()
    assert set(frame._kimi_prompt_owners) == {(chat_id, new_session, new_prompt)}
