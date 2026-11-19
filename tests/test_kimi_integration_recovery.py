import main
import pytest
import time
from execution_projection import should_show_execution_step
from kimi_server_client import KimiEvent, KimiServerClient, KimiServerError, event_to_payload
from kimi_test_support import (
    TEST_TURN_ID,
    _active_chat_id,
    _canonical_kimi_rows,
    _injection,
    _sdk_push,
    _setup_kimi_frame,
    _submit,
)


def test_sdk_step_agent_streams_reconcile_rest_once_and_keep_final_answer_separate(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "检查代码")
    session_id = fake.created_sessions[0]["session_id"]
    transport = KimiServerClient()
    _sdk_push(frame, transport, session_id, "turn.started", 1)
    _sdk_push(frame, transport, session_id, "turn.step.started", 2, step=1, stepId="main-step-1")
    _sdk_push(frame, transport, session_id, "thinking.delta", 2, offset=0, delta="先检查接口。")
    _sdk_push(frame, transport, session_id, "turn.step.started", 3, agent="worker", step=1, stepId="worker-step")
    _sdk_push(frame, transport, session_id, "thinking.delta", 3, agent="worker", offset=0, delta="子任务只核对日志。")
    _sdk_push(frame, transport, session_id, "assistant.delta", 3, offset=0, delta="接口已有线索，我会读取实现。")
    _sdk_push(frame, transport, session_id, "tool.call.started", 4, toolCallId="read-1", name="Read")
    _sdk_push(frame, transport, session_id, "turn.step.completed", 5, step=1, stepId="main-step-1")
    _sdk_push(frame, transport, session_id, "turn.step.started", 6, step=2, stepId="main-step-2")
    _sdk_push(frame, transport, session_id, "thinking.delta", 6, offset=0, delta="现在验证返回值。")
    _sdk_push(frame, transport, session_id, "assistant.delta", 6, offset=0, delta="这是最终回答。")
    chat_id = _active_chat_id(frame)
    owner = frame._find_kimi_prompt_owner(fake.submitted[0]["prompt_id"], session_id=session_id)
    assert frame._kimi_answer_parts(chat_id, session_id, owner["prompt_id"]) == "这是最终回答。"
    before = {step["item_id"]: step["canonical_item_id"] for step in frame._current_chat_state["execution_steps"]}
    messages = [
        {"id": owner["prompt_id"], "role": "user", "created_at": "2026-10-06T00:00:00Z", "content": []},
        {"id": "rest-step-1", "role": "assistant", "created_at": "2026-10-06T00:00:01Z", "content": [
            {"type": "thinking", "thinking": "先检查接口。\n完整补齐细节。"},
            {"type": "thinking", "thinking": "第二段公开思路。"},
            {"type": "text", "text": "接口已有线索，我会读取实现。"},
            {"type": "tool_use", "tool_call_id": "read-1", "tool_name": "Read", "input": {}},
        ]},
        {"id": "rest-tool", "role": "tool", "created_at": "2026-10-06T00:00:02Z", "content": []},
        {"id": "rest-step-2", "role": "assistant", "created_at": "2026-10-06T00:00:03Z", "content": [
            {"type": "thinking", "thinking": "现在验证返回值。\n完整验证细节。"},
            {"type": "text", "text": "这是最终回答。"},
        ]},
    ]
    assert frame._kimi_sync_thinking_rows(chat_id, owner, list(reversed(messages)))
    assert not frame._kimi_sync_thinking_rows(chat_id, owner, messages)
    steps = [step for step in frame._current_chat_state["execution_steps"] if should_show_execution_step(step)]
    assert len(steps) == 4
    assert all(before[step["item_id"]] == step["canonical_item_id"] for step in steps)
    assert [step["detail_text"] for step in steps] == [
        "先检查接口。\n完整补齐细节。\n第二段公开思路。", "子任务只核对日志。",
        "接口已有线索，我会读取实现。", "现在验证返回值。\n完整验证细节。",
    ]
    projected = [row for row in _canonical_kimi_rows(frame) if row.get("kind") != "time"]
    assert [row["detail_text"] for row in projected] == [step["detail_text"] for step in steps]
    assert frame._kimi_rest_answer_for_prompt(messages, owner["prompt_id"]) == "这是最终回答。"
    assert frame._kimi_rest_answer_for_prompt(messages[:2], owner["prompt_id"]) == ""
    fake.push_event(KimiEvent(type="turn_completed", thread_id=session_id, turn_id="1", text="这是最终回答。", status="completed",
                             data={"source_kind": "rest.reconciled", "stream_complete": True, "prompt_id": owner["prompt_id"],
                                   "owner_generation": owner["generation"], "idle_verified": True}))
    assert frame.active_session_turns[0]["answer_md"] == "这是最终回答。"


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


@pytest.mark.parametrize("state", ["running", "queued", "blocked"])
def test_v2_recovery_live_prompt_remains_pending_beyond_short_budget(frame, monkeypatch, state):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "synthetic long task")
    sid, prompt = fake.created_sessions[0]["session_id"], fake.submitted[0]["prompt_id"]
    fake.protocol_version = 2
    transport = KimiServerClient()
    page = {"items": [], "prompts": [{"promptId": prompt, "status": state}], "has_more": False}
    monkeypatch.setattr(transport, "get_transcript", lambda *a, **k: page)
    fake.reconcile_v2 = transport.reconcile_v2
    owner = frame._find_kimi_prompt_owner(prompt, session_id=sid)
    clock = [10.0]
    monkeypatch.setattr(main.time, "monotonic", lambda: clock[0])
    for _ in range(3):
        assert frame._reconcile_kimi_owner_worker({**owner, "_recovery_deadline": clock[0] + .01}) == "monitor"
        clock[0] += 10
        assert frame.active_session_turns[0]["request_status"] == "pending"
    page["items"] = [{"kind": "turn", "turnId": "t", "triggerPromptId": prompt, "state": "completed",
        "steps": [{"stepId": "st", "state": "completed", "frames": [
            {"kind": "text", "role": "assistant", "frameId": "f", "text": "long final"}]}]}]
    played = []
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: played.append(True))
    assert frame._reconcile_kimi_owner_worker(owner) == "done"
    assert frame._reconcile_kimi_owner_worker(owner) == "done"
    assert frame.active_session_turns[0]["answer_md"] == "long final"
    assert played == [True]
    assert len(fake.submitted) == 1


@pytest.mark.parametrize("ambiguous", [False, True])
def test_v2_lost_post_response_recovers_only_unique_prompt_without_reposting(frame, monkeypatch, ambiguous):
    fake = _setup_kimi_frame(frame, monkeypatch)
    monkeypatch.setattr(frame, "_recover_kimi_pending_owners", lambda **kwargs: None)
    posts = []
    def lost_post(*args):
        posts.append(True)
        raise KimiServerError("synthetic accepted response lost", result_unknown=True)
    fake.submit_prompt = lost_post
    _submit(frame, "synthetic lost request")
    sid = fake.created_sessions[0]["session_id"]
    owner = frame._kimi_pending_owners()[0]
    assert owner["prompt_id"].startswith("__unresolved__:")
    transport = KimiServerClient()
    def turn(prompt):
        return {"kind": "turn", "turnId": prompt, "triggerPromptId": prompt, "state": "completed",
            "prompt": "synthetic lost request", "origin": {"kind": "user"}, "steps": [
                {"stepId": "st", "state": "completed", "frames": [{"kind": "text", "role": "assistant", "text": "recovered unique final", "frameId": "f"}]}]}
    page = {"items": [turn("actual-prompt")], "has_more": False}
    if ambiguous:
        page["items"].append(turn("other-prompt"))
    monkeypatch.setattr(transport, "get_transcript", lambda *a, **k: page)
    fake.protocol_version = 2
    fake.reconcile_v2 = transport.reconcile_v2
    fake.recovery_messages_v2 = transport.recovery_messages_v2
    assert frame._reconcile_kimi_owner_worker(owner) == "done"
    result = frame.active_session_turns[0]
    if ambiguous:
        assert result["request_status"] == "failed"
        assert "ambiguous" in result["request_error"]
        assert result["kimi_prompt_id"].startswith("__unresolved__:")
        assert result["answer_md"] != "recovered unique final"
    else:
        assert result["kimi_prompt_id"] == "actual-prompt"
        assert result["request_status"] == "done"
        assert result["answer_md"] == "recovered unique final"
    assert posts == [True]


def test_v2_terminal_without_body_exhausts_only_finite_recovery_budget(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "synthetic terminal")
    sid, prompt = fake.created_sessions[0]["session_id"], fake.submitted[0]["prompt_id"]
    fake.protocol_version = 2
    transport = KimiServerClient()
    monkeypatch.setattr(transport, "get_transcript", lambda *a, **k: {"items": [
        {"kind": "turn", "turnId": "t", "triggerPromptId": prompt, "state": "completed", "steps": []}], "has_more": False})
    fake.reconcile_v2 = transport.reconcile_v2
    owner = frame._find_kimi_prompt_owner(prompt, session_id=sid)
    clock = [10.0]
    monkeypatch.setattr(main.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(main.time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))
    assert frame._reconcile_kimi_owner_worker({**owner, "_recovery_deadline": 10.1}) == "done"
    assert clock[0] <= 10.1
    assert frame.active_session_turns[0]["request_status"] == "failed"
    assert len(fake.submitted) == 1


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


def test_recovery_pages_back_with_before_id_to_find_boundary(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    frame._kimi_reconcile_attempts = 2
    _submit(frame, "long history")
    session_id = fake.created_sessions[0]["session_id"]
    prompt_id = fake.submitted[0]["prompt_id"]
    rows = [
        {"id": prompt_id, "role": "user", "created_at": "2026-01-01T00:00:00Z",
         "content": [{"type": "text", "text": "long history"}]},
    ]
    for i in range(60):
        rows.append({
            "id": f"filler-{i:03d}", "role": "assistant",
            "created_at": f"2026-01-01T00:{1 + i // 60:02d}:{i % 60:02d}Z",
            "content": [{"type": "thinking", "text": f"思考步骤 {i}"}],
        })
    rows.append({"id": "final-1", "role": "assistant", "created_at": "2026-01-01T00:02:00Z",
                 "content": [{"type": "text", "text": "paged answer"}]})
    fake.messages_by_session[session_id] = rows
    fake.get_status = lambda _session_id: {"busy": False, "status": "completed"}
    reconciled = []
    monkeypatch.setattr(frame, "_on_kimi_event", reconciled.append)

    owner = frame._find_kimi_prompt_owner(prompt_id, session_id=session_id)
    assert owner is not None
    frame._reconcile_kimi_owner_worker(owner)

    assert len(reconciled) == 1
    assert reconciled[0].text == "paged answer"
    assert any(before_id for _sid, before_id in fake.list_messages_calls)
    # The recovery channel syncs thinking rows from the PAGED rows, so blocks
    # older than the 50-message window (visible only via before_id paging)
    # must appear, in transcript order.
    thinking_steps = [
        step for step in frame._current_chat_state["execution_steps"]
        if step.get("source_kind") == "rest.thinking"
    ]
    assert len(thinking_steps) == 60
    assert thinking_steps[0]["detail_text"] == "思考步骤 0"
    assert thinking_steps[-1]["detail_text"] == "思考步骤 59"


def test_terminal_without_answer_keeps_monitoring_until_answer_persists(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "late answer")
    session_id = fake.created_sessions[0]["session_id"]
    prompt_id = fake.submitted[0]["prompt_id"]
    fake.get_status = lambda _session_id: {"busy": False, "status": "idle"}
    base_rows = [
        {"id": prompt_id, "role": "user", "created_at": "2026-01-01T00:00:00Z",
         "content": [{"type": "text", "text": "late answer"}]},
    ]
    answered_rows = base_rows + [
        {"id": "answer-1", "role": "assistant", "created_at": "2026-01-01T00:00:01Z",
         "content": [{"type": "text", "text": "迟到但最终到达的答案"}]},
    ]
    fake.messages_by_session[session_id] = base_rows
    calls = {"n": 0}
    original = fake.list_messages

    def late_persist(session_id_arg, before_id=None, timeout=None):
        calls["n"] += 1
        if calls["n"] >= 3:
            fake.messages_by_session[session_id_arg] = answered_rows
        return original(session_id_arg, before_id=before_id, timeout=timeout)

    fake.list_messages = late_persist
    reconciled = []
    monkeypatch.setattr(frame, "_on_kimi_event", reconciled.append)
    errors = []
    monkeypatch.setattr(frame, "_apply_kimi_error", lambda *args, **kwargs: errors.append(args))

    owner = frame._find_kimi_prompt_owner(prompt_id, session_id=session_id)
    assert owner is not None
    outcome = frame._reconcile_kimi_owner_worker(
        dict(owner, _recovery_deadline=time.monotonic() + 5.0)
    )

    assert outcome == "done"
    assert len(reconciled) == 1
    assert reconciled[0].text == "迟到但最终到达的答案"
    assert errors == []


def test_recovery_pages_back_with_before_id_under_deadline(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    frame._kimi_reconcile_attempts = 2
    _submit(frame, "deadline paging")
    session_id = fake.created_sessions[0]["session_id"]
    prompt_id = fake.submitted[0]["prompt_id"]
    rows = [
        {"id": prompt_id, "role": "user", "created_at": "2026-01-01T00:00:00Z",
         "content": [{"type": "text", "text": "deadline paging"}]},
    ]
    for i in range(60):
        rows.append({
            "id": f"filler-{i:03d}", "role": "assistant",
            "created_at": f"2026-01-01T00:{1 + i // 60:02d}:{i % 60:02d}Z",
            "content": [{"type": "thinking", "text": f"思考步骤 {i}"}],
        })
    rows.append({"id": "final-1", "role": "assistant", "created_at": "2026-01-01T00:02:00Z",
                 "content": [{"type": "text", "text": "deadline paged answer"}]})
    fake.messages_by_session[session_id] = rows
    fake.get_status = lambda _session_id: {"busy": False, "status": "completed"}
    reconciled = []
    monkeypatch.setattr(frame, "_on_kimi_event", reconciled.append)

    owner = frame._find_kimi_prompt_owner(prompt_id, session_id=session_id)
    assert owner is not None
    frame._reconcile_kimi_owner_worker(
        dict(owner, _recovery_deadline=time.monotonic() + 10.0)
    )

    assert len(reconciled) == 1
    assert reconciled[0].text == "deadline paged answer"
    # The deadline + before_id combination must actually reach the client:
    # without kwargs passthrough the paged fetch degrades to re-fetching the
    # same newest window forever.
    assert any(
        session_id == sid and before_id
        for sid, before_id in fake.list_messages_calls
    )


def test_reconcile_backoff_expiry_preserves_last_real_error(frame, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(main.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(main.time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))
    frame._kimi_reconcile_attempts = 3
    frame._kimi_reconcile_backoff = 2.0
    calls = []
    def ensure():
        calls.append(True)
        raise ConnectionError("original connection failure")
    monkeypatch.setattr(frame, "_ensure_kimi_client", ensure)
    errors = []
    monkeypatch.setattr(frame, "_call_after_if_alive", lambda fn, *args: errors.append(args[1]))
    assert frame._reconcile_kimi_owner_worker({"chat_id": "owner", "session_id": "session", "_recovery_deadline": 1.0}) == "done"
    assert calls == [True]
    assert errors == ["original connection failure"]


def test_reconcile_expired_owner_deadline_reports_timeout(frame, monkeypatch):
    monkeypatch.setattr(main.time, "monotonic", lambda: 10.0)
    monkeypatch.setattr(frame, "_ensure_kimi_client", lambda: pytest.fail("expired owner must not start a client"))
    errors = []
    monkeypatch.setattr(frame, "_call_after_if_alive", lambda fn, *args: errors.append(args[1]))
    owner = {"chat_id": "owner", "session_id": "session", "_recovery_deadline": 9.0}
    assert frame._reconcile_kimi_owner_worker(owner) == "done"
    assert errors == ["Kimi recovery deadline exhausted"]
    errors.clear()
    assert frame._reconcile_kimi_owner_worker(owner, original_error="real provider error") == "done"
    assert errors == ["real provider error"]


@pytest.mark.parametrize("paged", [False, True])
def test_rest_injection_reconciliation_persists_visible_answer_once(frame, monkeypatch, paged):
    fake = _setup_kimi_frame(frame, monkeypatch)
    played = []
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: played.append(True))
    _submit(frame, "target request")
    session_id = fake.created_sessions[0]["session_id"]
    prompt_id = fake.submitted[0]["prompt_id"]
    owner = frame._find_kimi_prompt_owner(prompt_id, session_id=session_id)
    rows = [{"id": prompt_id, "role": "user", "content": "target request"},
            {"id": "tool-step", "role": "assistant", "content": [
                {"type": "text", "text": "working"}, {"type": "tool_use", "id": "tool"}]}]
    if paged:
        rows.extend({"id": f"thinking-{i}", "role": "assistant", "content": [
            {"type": "thinking", "text": f"public step {i}"}]} for i in range(55))
    rows.extend([_injection(), {"id": "final", "role": "assistant", "content": "complete target answer"}])
    for index, row in enumerate(rows):
        row["created_at"] = f"2026-01-01T00:{index // 60:02d}:{index % 60:02d}Z"
    fake.messages_by_session[session_id] = rows
    fake.status_by_session[session_id] = {"busy": False, "status": "completed"}
    frame._on_kimi_client_message({"type": "resync_required", "payload": {"session_ids": [session_id]}})
    frame._navigation_quiet_until = 0
    frame._flush_accepted_answer_refresh(frame._answer_refresh_deadline_owner)
    turn = frame.active_session_turns[0]
    assert turn["request_status"] == "done"
    assert turn["answer_md"] == "complete target answer"
    assert sum("complete target answer" in row for row in frame.answer_list.GetStrings()) == 1
    frame._flush_chat_state_save()
    stored = frame.chat_store.load_chat(_active_chat_id(frame))["turns"][0]
    assert stored["request_status"] == "done"
    assert stored["answer_md"] == "complete target answer"
    read_state = frame.chat_store.get_chat_read_state(_active_chat_id(frame))
    # Replaying an old worker completion takes the normal rejected-owner path.
    frame._reconcile_kimi_owner_worker(owner)
    assert frame.chat_store.get_chat_read_state(_active_chat_id(frame)) == read_state
    frame._flush_chat_state_save()
    assert frame.chat_store.load_chat(_active_chat_id(frame))["turns"][0] == stored
    assert played == [True]
    assert len(fake.submitted) == 1
    assert any(before for _sid, before in fake.list_messages_calls) == paged


@pytest.mark.parametrize("fields,skipped", [
    ({}, True),
    ({"metadata": {"origin": {"kind": "injection", "variant": "other"}}}, True),
    ({"metadata": {}, "origin": {"kind": "injection"}}, True),
    ({"metadata": None, "origin": {"kind": "injection"}}, True),
    ({"metadata": "malformed", "origin": {"kind": "injection"}}, True),
    ({"metadata": "malformed", "origin": {}}, False),
    ({"metadata": {}, "origin": {}}, False),
    ({"metadata": {}, "origin": {"kind": None}}, False),
    ({"metadata": {"origin": {}}, "origin": {"kind": "injection"}}, False),
    ({"metadata": {"origin": {"kind": None}}, "origin": {"kind": "injection"}}, False),
    ({"metadata": {}, "origin": None}, False),
    ({"metadata": {"origin": {"kind": "unknown"}}}, False),
    ({"metadata": {"origin": "injection"}}, False),
    ({"metadata": {"origin": None}, "origin": {"kind": "injection"}}, False),
    ({"metadata": {"origin": {"kind": "user"}}, "origin": {"kind": "injection"}}, False),
    ({"metadata": {"origin": {"kind": "injection"}}, "origin": {"kind": "user"}}, True),
])
def test_rest_injection_boundary_is_structured_and_metadata_has_priority(frame, fields, skipped):
    rows = [{"id": "target", "role": "user"},
            _injection(content="<system-reminder>fake reminder</system-reminder>", **fields),
            {"id": "answer", "role": "assistant", "content": "target final"}]
    assert frame._kimi_rest_answer_boundary(rows, "target") == (("target final", False) if skipped else ("", True))
    assert frame._kimi_rest_answer_boundary(rows, "injected") == (("", False) if skipped else ("target final", False))


def test_rest_multiple_injections_preserve_real_next_boundary_and_no_final_semantics(frame):
    rows = [{"id": "target", "role": "user"}, _injection(),
            _injection("old-injection", metadata={}, origin={"kind": "injection"}),
            {"id": "thinking", "role": "assistant", "content": [{"type": "thinking", "text": "thought"}]},
            {"id": "tool", "role": "assistant", "content": [
                {"type": "text", "text": "interim"}, {"type": "tool_use"}]}]
    assert frame._kimi_rest_answer_boundary(rows, "target") == ("", False)
    rows.extend([{"id": "final", "role": "assistant", "content": "target final"},
                 {"id": "next", "role": "user", "content": "next request"},
                 {"id": "next-final", "role": "assistant", "content": "next final"}])
    assert frame._kimi_rest_answer_boundary(rows, "target") == ("target final", True)


def test_rest_injection_thinking_sync_keeps_ordinals_and_stops_at_real_next(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "target")
    session_id = fake.created_sessions[0]["session_id"]
    prompt_id = fake.submitted[0]["prompt_id"]
    owner = frame._find_kimi_prompt_owner(prompt_id, session_id=session_id)
    rows = [{"id": prompt_id, "role": "user"},
            {"id": "first", "role": "assistant", "content": [{"type": "thinking", "text": "first step"}]},
            _injection(),
            {"id": "second", "role": "assistant", "content": [{"type": "thinking", "text": "second step"}]},
            {"id": "next", "role": "user"},
            {"id": "foreign", "role": "assistant", "content": [{"type": "thinking", "text": "foreign step"}]}]
    assert frame._kimi_sync_thinking_rows(_active_chat_id(frame), owner, rows)
    steps = [s for s in frame._current_chat_state["execution_steps"] if s.get("source_kind") == "rest.thinking"]
    assert [(s["detail_text"], s["kimi_step_number"]) for s in steps] == [("first step", 1), ("second step", 2)]
    assert not frame._kimi_sync_thinking_rows(_active_chat_id(frame), owner, [_injection(prompt_id), *rows[1:]])


@pytest.mark.parametrize("real_match", [False, True])
def test_legacy_owner_injection_never_matches_question(frame, monkeypatch, real_match):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "target")
    session_id = fake.created_sessions[0]["session_id"]
    owner = dict(frame._find_kimi_prompt_owner(fake.submitted[0]["prompt_id"], session_id=session_id))
    owner.update(question="target", legacy=True)
    rows = [_injection(owner["prompt_id"], content="target")]
    if real_match:
        rows.append({"id": "real-prompt", "role": "user", "content": "target"})
    migrated = frame._migrate_legacy_kimi_owner(owner, rows)
    assert (migrated["prompt_id"] if migrated else None) == ("real-prompt" if real_match else None)


def test_rest_injection_does_not_disclose_private_answer_or_thinking(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "target")
    session_id = fake.created_sessions[0]["session_id"]
    prompt_id = fake.submitted[0]["prompt_id"]
    owner = frame._find_kimi_prompt_owner(prompt_id, session_id=session_id)
    rows = [{"id": prompt_id, "role": "user"}, _injection(),
            {"id": "private-message", "role": "assistant", "private": True, "content": [
                {"type": "text", "text": "secret answer"},
                {"type": "thinking", "text": "secret message thinking"}]},
            {"id": "private-block", "role": "assistant", "content": [
                {"type": "text", "private": True, "text": "secret text"},
                {"type": "thinking", "private": True, "text": "secret block thinking"},
                {"type": "thinking", "text": "public thinking"}]}]
    assert frame._kimi_rest_answer_boundary(rows, prompt_id) == ("", False)
    assert frame._kimi_sync_thinking_rows(_active_chat_id(frame), owner, rows)
    projection = _canonical_kimi_rows(frame)
    assert "secret" not in str(projection)
    assert [row["detail_text"] for row in projection] == ["public thinking"]


def test_rest_injection_id_cannot_stop_prompt_pagination(frame):
    calls = []
    class Client:
        def list_messages(self, session_id, before_id=None):
            calls.append(before_id)
            return []
    rows = frame._kimi_messages_back_to_boundary(Client(), "session", "fake-prompt",
                                                 messages=[_injection("fake-prompt")])
    assert calls == ["fake-prompt"]
    assert frame._kimi_rest_answer_boundary(rows, "fake-prompt") == ("", False)


def test_legacy_owner_injection_cannot_be_alias_start(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "target")
    session_id = fake.created_sessions[0]["session_id"]
    owner = dict(frame._find_kimi_prompt_owner(fake.submitted[0]["prompt_id"], session_id=session_id))
    owner.update(question="target", legacy=True, candidate_alias=True, owner_prompt_id="injected-owner")
    migrated = frame._migrate_legacy_kimi_owner(owner, [
        _injection("injected-owner"), {"id": "real-prompt", "role": "user", "content": "target"}])
    assert migrated["role"] == "queued"
    assert migrated["owner_prompt_id"] == "real-prompt"


@pytest.mark.parametrize("content", [
    [{"type": "thinking", "text": "only thinking"}],
    [{"type": "text", "text": "intermediate commentary"}, {"type": "tool_use", "id": "tool"}],
])
def test_rest_injection_no_final_reconciliation_persists_failure_without_completion(frame, monkeypatch, content):
    fake = _setup_kimi_frame(frame, monkeypatch)
    played = []
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: played.append(True))
    frame._kimi_reconcile_attempts = 1
    frame._kimi_reconcile_backoff = 0
    _submit(frame, "target")
    session_id = fake.created_sessions[0]["session_id"]
    prompt_id = fake.submitted[0]["prompt_id"]
    owner = frame._find_kimi_prompt_owner(prompt_id, session_id=session_id)
    fake.messages_by_session[session_id] = [
        {"id": prompt_id, "role": "user"}, _injection(),
        {"id": "intermediate", "role": "assistant", "content": content}]
    fake.status_by_session[session_id] = {"busy": False, "status": "completed"}
    # The ordinary owner worker without a recovery deadline uses the configured
    # one-attempt failure path, so this regression never waits for wall time.
    assert frame._reconcile_kimi_owner_worker(owner) == "done"
    frame._flush_chat_state_save()
    for turn in (frame.active_session_turns[0], frame.chat_store.load_chat(_active_chat_id(frame))["turns"][0]):
        assert turn["request_status"] == "failed"
        assert "no final answer" in turn["request_error"]
        assert "only thinking" not in turn["answer_md"]
        assert "intermediate commentary" not in turn["answer_md"]
    assert played == []
    assert len(fake.submitted) == 1


def test_distinct_kimi_recovery_owner_gets_own_deadline(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    fake.protocol_version = 2
    frame._kimi_reconcile_attempts = 1
    now = [20.0]
    monkeypatch.setattr(main.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(main.time, "sleep", lambda delay: now.__setitem__(0, now[0] + delay))
    finished = []
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: finished.append(True))
    chat_id, sid = "two-owners", "shared-session"
    frame.active_chat_id = frame.current_chat_id = chat_id
    frame.active_kimi_session_id = sid
    frame.active_session_turns = [
        {"question": prompt, "model": "kimi/main", "request_status": "pending",
         "kimi_session_id": sid, "kimi_prompt_id": prompt}
        for prompt in ("slow", "ready")
    ]
    frame._current_chat_state = {"id": chat_id, "kimi_session_id": sid, "turns": frame.active_session_turns}
    for idx, prompt in enumerate(("slow", "ready")):
        frame._register_kimi_prompt_owner({
            "chat_id": chat_id, "session_id": sid, "prompt_id": prompt,
            "turn_idx": idx, "role": "active" if idx == 0 else "queued", "landed": True,
        })
    queries = []

    def reconcile(session, prompt, *, timeout):
        assert session == sid
        queries.append((prompt, now[0], timeout))
        if prompt == "ready":
            return KimiEvent(type="turn_completed", thread_id=sid, turn_id="ready-turn", text="ready answer",
                             status="completed", data={"prompt_id": "ready", "source_kind": "rest.reconciled",
                                                       "stream_complete": True})
        return None

    fake.reconcile_v2 = reconcile
    frame._reconcile_kimi_session_worker(sid)
    assert queries[0] == ("slow", 20.0, 5.0)
    assert queries[-1] == ("ready", 25.0, 5.0)
    assert frame.active_session_turns[0]["request_status"] == "failed"
    assert frame.active_session_turns[1]["answer_md"] == "ready answer"
    frame._on_kimi_event(main.CodexEvent(**event_to_payload(reconcile(sid, "ready", timeout=1))))
    assert finished == [True]
    frame._save_state()
    assert frame.chat_store.load_turns(chat_id)[1]["answer_md"] == "ready answer"


def test_v2_lost_wrapped_post_recovers_preallocated_id_after_store_reload(frame, monkeypatch, tmp_path):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "history question")
    old_session = fake.created_sessions[0]["session_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=old_session, turn_id="t-old"))
    fake.push_event(KimiEvent(type="turn_completed", thread_id=old_session, turn_id="t-old",
                              status="completed", text="history answer"))
    fake.session_exists_result = False
    fake.protocol_version = 2
    posts = []
    attachment = tmp_path / "recovery-attachment.txt"
    attachment.write_text("test attachment", encoding="utf-8")
    frame._pending_input_attachments = [{"status": "success", "kind": "file", "path": str(attachment)}]
    monkeypatch.setattr(frame, "_recover_kimi_pending_owners", lambda **_kwargs: None)

    def lose_response(session_id, blocks, *, prompt_id):
        posts.append((session_id, blocks, prompt_id))
        fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id="unrelated",
                                  data={"prompt_id": "wrong-prompt"}))
        raise KimiServerError("response lost", result_unknown=True)

    fake.submit_prompt = lose_response
    _submit(frame, "new request")
    sid, blocks, pid = posts[0]
    assert frame.active_session_turns[-1]["kimi_prompt_id"] == pid
    assert frame.active_session_turns[-1]["kimi_prompt_exact"] is True
    assert "history question" in blocks[0]["text"]
    assert "recovery-attachment.txt" in blocks[0]["text"]
    assert not frame._find_kimi_prompt_owner("wrong-prompt", session_id=sid, include_unlanded=True)
    frame._save_state()
    assert frame.chat_store.load_turns(_active_chat_id(frame))[-1]["kimi_prompt_id"] == pid
    frame._kimi_prompt_owners.clear()
    frame._kimi_active_turns.clear()
    frame._load_state()
    owner = frame._find_kimi_prompt_owner(pid, session_id=sid)
    assert owner["exact_prompt_id"] is True
    assert owner["role"] == "unresolved"
    transport = KimiServerClient()
    snapshot = {"items": [
        {"kind": "turn", "turnId": "other", "triggerPromptId": "wrong-prompt", "state": "completed",
         "prompt": "new request", "steps": [{"state": "completed", "frames": [
             {"kind": "text", "role": "assistant", "text": "unrelated answer"}]}]},
        {"kind": "turn", "turnId": "t-new", "triggerPromptId": pid, "state": "completed",
         "prompt": blocks[0]["text"], "steps": [{"state": "completed", "frames": [
             {"kind": "text", "role": "assistant", "text": "exact recovered answer"}]}]},
    ]}
    monkeypatch.setattr(transport, "get_transcript", lambda *a, **k: snapshot)
    fake.reconcile_v2 = transport.reconcile_v2
    fake.recovery_messages_v2 = transport.recovery_messages_v2
    monkeypatch.setattr(frame, "_ensure_kimi_client", lambda: fake)
    frame._reconcile_kimi_owner_worker(owner)
    assert len(posts) == 1
    assert frame.active_session_turns[-1]["answer_md"] == "exact recovered answer"


@pytest.mark.parametrize("steered", [True, False])
def test_v2_lost_same_text_steer_recovers_alias_or_distinct_prompt(frame, monkeypatch, steered):
    fake = _setup_kimi_frame(frame, monkeypatch)
    fake.protocol_version = 2
    posts = []
    sounds = []
    monkeypatch.setattr(frame, "_recover_kimi_pending_owners", lambda **_kwargs: None)
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: sounds.append(True))

    def submit(sid, _blocks, *, prompt_id):
        posts.append((sid, prompt_id))
        return prompt_id

    fake.submit_prompt = submit
    fake.steer_result = None
    _submit(frame, "same text")
    sid, parent = posts[0]
    fake.push_event(KimiEvent(type="turn_started", thread_id=sid, turn_id="parent-turn", data={"prompt_id": parent}))
    _submit(frame, "same text")
    child = posts[1][1]
    fake.push_event(KimiEvent(type="turn_completed", thread_id=sid, turn_id="parent-turn", status="completed",
                              text="parent answer", data={"prompt_id": parent}))
    assert frame.active_session_turns[0]["request_status"] == "done"
    assert frame.active_session_turns[1]["request_status"] == "pending"
    transport = KimiServerClient()
    parent_turn = {"kind": "turn", "turnId": "parent-turn", "triggerPromptId": parent, "state": "completed",
                   "prompt": "same text", "steps": [{"state": "completed", "frames": [
                       {"kind": "text", "role": "assistant", "text": "parent answer"}]}]}
    snapshot = {"items": [parent_turn], "prompts": []}
    if steered:
        parent_turn["steps"][0]["frames"].insert(
            0, {"kind": "text", "role": "user", "text": "same text", "promptIds": [child]}
        )
    else:
        snapshot["prompts"].append({"promptId": child, "status": "queued", "content": "same text"})
    monkeypatch.setattr(transport, "get_transcript", lambda *a, **k: snapshot)
    fake.reconcile_v2 = transport.reconcile_v2
    fake.recovery_messages_v2 = transport.recovery_messages_v2
    owner = frame._find_kimi_prompt_owner(child, session_id=sid)
    assert owner["role"] == "unresolved"
    frame._reconcile_kimi_owner_worker(owner)
    if steered:
        assert frame.active_session_turns[1]["request_status"] == "done"
        assert frame.active_session_turns[1]["answer_alias_of"] == frame.active_session_turns[0]["answer_identity"]
        assert frame.active_session_turns[1]["answer_md"] == ""
        assert sounds == [True]
    else:
        assert frame.active_session_turns[1]["request_status"] == "pending"
        assert frame._find_kimi_prompt_owner(child, session_id=sid)["role"] == "queued"
        fake.push_event(KimiEvent(type="turn_started", thread_id=sid, turn_id="child-turn", data={"prompt_id": child}))
        fake.push_event(KimiEvent(type="turn_completed", thread_id=sid, turn_id="child-turn", status="completed",
                                  text="independent answer", data={"prompt_id": child}))
        assert frame.active_session_turns[1]["answer_md"] == "independent answer"
        assert sounds == [True, True]
    assert len(posts) == 2
