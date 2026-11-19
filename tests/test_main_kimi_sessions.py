import main
import pytest


def test_restored_kimi_lifecycle_projects_completion_once(frame):
    started = {
        "event_type": "item_started", "source_kind": "tool.call.started",
        "thread_id": "session-a", "turn_id": "1", "item_id": "tool-a",
        "list_text": "正在执行读取文件：README.md",
    }
    completed = {
        "event_type": "item_completed", "source_kind": "tool.result",
        "thread_id": "session-a", "turn_id": "1", "item_id": "tool-a",
        "list_text": "已完成：README.md",
    }

    progress = dict(started, event_type="agent_message_delta", source_kind="tool.progress", list_text="halfway")
    unrelated_turn = dict(started, turn_id="2", list_text="other turn")

    assert frame._collapse_kimi_execution_lifecycle([started, progress, unrelated_turn, completed]) == [
        completed,
        unrelated_turn,
    ]


def test_kimi_authoritative_completion_closes_matching_clear_operation(frame, tmp_path):
    store = main.ChatStore(tmp_path / "kimi-clear-success.db")
    store.initialize()
    store.upsert_chat({"id": "kimi-owner"})
    store.replace_turns("kimi-owner", [{"question": "first"}])
    operation = store.begin_clear_operation("kimi-owner", idempotency_key="request")
    store.claim_clear_resend_dispatch(operation["operation_id"], chat_id="kimi-owner", revision=operation["revision"])
    turn = {
        "question": "first",
        "answer_md": main.REQUESTING_TEXT,
        "model": main.DEFAULT_KIMI_MODEL,
        "request_status": "pending",
        "kimi_session_id": "session-1",
        "kimi_turn_id": "turn-1",
        "kimi_prompt_id": "prompt-1",
        "clear_operation_id": operation["operation_id"],
        "clear_revision": operation["revision"],
    }
    frame.chat_store = store
    frame._chat_store_enabled = True
    frame.active_chat_id = frame.current_chat_id = "kimi-owner"
    frame.active_session_turns = [turn]
    frame._current_chat_state = {"id": "kimi-owner", "turns": frame.active_session_turns, "kimi_request_queue": []}
    event = main.CodexEvent(
        type="turn_completed", thread_id="session-1", turn_id="turn-1", text="done",
        status="completed", data={"source_kind": "turn.ended", "agent_id": "main"},
    )

    assert frame._finalize_kimi_turn_state(
        "kimi-owner", frame._current_chat_state, frame.active_session_turns, 0, event
    ) == [0]
    assert turn["request_status"] == "done"
    assert store.get_clear_operation(operation["operation_id"])["state"] == "completed_with_resend"


def test_kimi_authoritative_offscreen_failure_blocks_only_owner_operation(frame, tmp_path, monkeypatch):
    store = main.ChatStore(tmp_path / "kimi-clear-failure.db")
    store.initialize()
    store.upsert_chat({"id": "kimi-owner"})
    store.replace_turns("kimi-owner", [{"question": "first"}])
    operation = store.begin_clear_operation("kimi-owner", idempotency_key="request")
    store.claim_clear_resend_dispatch(operation["operation_id"], chat_id="kimi-owner", revision=operation["revision"])
    failed_turn = {
        "question": "first", "model": main.DEFAULT_KIMI_MODEL, "request_status": "pending",
        "kimi_turn_id": "turn-1", "clear_operation_id": operation["operation_id"],
        "clear_revision": operation["revision"],
    }
    archived = {"id": "kimi-owner", "turns": [failed_turn], "kimi_request_queue": []}
    frame.chat_store = store
    frame._chat_store_enabled = True
    frame.archived_chats = [archived]
    frame.active_chat_id = frame.current_chat_id = "active-owner"
    frame.active_session_turns = [{"question": "active stays"}]
    frame._current_chat_state = {"id": "active-owner", "turns": frame.active_session_turns}
    monkeypatch.setattr(frame, "_refresh_visible_history_chat", lambda *_args: None)

    frame._apply_kimi_error("kimi-owner", "provider failed", turn_idx=0, turn_id="turn-1")

    assert failed_turn["request_status"] == "failed"
    assert store.get_clear_operation(operation["operation_id"])["state"] == "resend_blocked"
    assert frame.active_session_turns == [{"question": "active stays"}]


def test_kimi_completion_cross_owner_misroute_preserves_turn_buffers_and_owner(frame, tmp_path):
    store = main.ChatStore(tmp_path / "kimi-cross-owner-complete.db")
    store.initialize()
    store.upsert_chat({"id": "real-owner"})
    store.replace_turns("real-owner", [{"question": "first"}])
    operation = store.begin_clear_operation("real-owner", idempotency_key="request")
    store.claim_clear_resend_dispatch(operation["operation_id"], chat_id="real-owner", revision=operation["revision"])
    turn = {
        "question": "first", "answer_md": main.REQUESTING_TEXT,
        "model": main.DEFAULT_KIMI_MODEL, "request_status": "pending",
        "kimi_session_id": "session-x", "kimi_turn_id": "turn-x",
        "kimi_prompt_id": "prompt-x", "clear_operation_id": operation["operation_id"],
        "clear_revision": operation["revision"],
    }
    owner = {
        "chat_id": "wrong-owner", "session_id": "session-x", "turn_id": "turn-x",
        "turn_idx": 0, "prompt_id": "prompt-x", "owner_prompt_id": "prompt-x",
        "role": "active", "generation": 1,
    }
    owner_key = frame._kimi_owner_key_for(owner)
    frame.chat_store = store
    frame.active_chat_id = frame.current_chat_id = "wrong-owner"
    frame.active_session_turns = [turn]
    frame._current_chat_state = {"id": "wrong-owner", "turns": [turn], "kimi_request_queue": []}
    frame._kimi_prompt_owners[owner_key] = dict(owner)
    frame._kimi_active_turns["wrong-owner"] = dict(owner)
    buffer_key = ("wrong-owner", "session-x", "prompt-x")
    frame._kimi_turn_answer_parts[buffer_key] = ["buffered"]
    event = main.CodexEvent(
        type="turn_completed", thread_id="session-x", turn_id="turn-x", text="late answer",
        status="completed", data={"source_kind": "turn.ended", "agent_id": "main", "prompt_id": "prompt-x"},
    )

    assert frame._finalize_kimi_turn_state(
        "wrong-owner", frame._current_chat_state, frame.active_session_turns, 0, event
    ) == []
    assert turn["request_status"] == "pending"
    assert frame._kimi_turn_answer_parts[buffer_key] == ["buffered"]
    assert owner_key in frame._kimi_prompt_owners
    assert "wrong-owner" in frame._kimi_active_turns
    assert store.get_clear_operation(operation["operation_id"])["state"] == "resend_dispatched"
    with store._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) n FROM identity_quarantine WHERE reason='SUPERSEDED_CLEAR_RESULT'"
        ).fetchone()["n"] == 1


def test_kimi_error_cross_owner_misroute_preserves_turn_and_owner(frame, tmp_path):
    store = main.ChatStore(tmp_path / "kimi-cross-owner-error.db")
    store.initialize()
    store.upsert_chat({"id": "real-owner"})
    store.replace_turns("real-owner", [{"question": "first"}])
    operation = store.begin_clear_operation("real-owner", idempotency_key="request")
    store.claim_clear_resend_dispatch(operation["operation_id"], chat_id="real-owner", revision=operation["revision"])
    turn = {
        "question": "first", "answer_md": main.REQUESTING_TEXT,
        "model": main.DEFAULT_KIMI_MODEL, "request_status": "pending",
        "kimi_turn_id": "turn-x", "clear_operation_id": operation["operation_id"],
        "clear_revision": operation["revision"],
    }
    owner = {
        "chat_id": "wrong-owner", "session_id": "session-x", "turn_id": "turn-x",
        "turn_idx": 0, "prompt_id": "prompt-x", "owner_prompt_id": "prompt-x",
        "role": "active", "generation": 1,
    }
    owner_key = frame._kimi_owner_key_for(owner)
    frame.chat_store = store
    frame.active_chat_id = frame.current_chat_id = "wrong-owner"
    frame.active_session_turns = [turn]
    frame._current_chat_state = {"id": "wrong-owner", "turns": [turn], "kimi_request_queue": []}
    frame._kimi_prompt_owners[owner_key] = dict(owner)
    frame._kimi_active_turns["wrong-owner"] = dict(owner)

    frame._apply_kimi_error(
        "wrong-owner", "misrouted failure", turn_idx=0, turn_id="turn-x", prompt_id="prompt-x"
    )

    assert turn["request_status"] == "pending"
    assert turn["answer_md"] == main.REQUESTING_TEXT
    assert owner_key in frame._kimi_prompt_owners
    assert "wrong-owner" in frame._kimi_active_turns
    assert store.get_clear_operation(operation["operation_id"])["state"] == "resend_dispatched"
    with store._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) n FROM identity_quarantine WHERE reason='SUPERSEDED_CLEAR_RESULT'"
        ).fetchone()["n"] == 1


@pytest.mark.parametrize("result_kind", ["success", "failure"])
def test_kimi_late_result_after_cancel_does_not_mutate_turn_or_operation(
    frame, tmp_path, result_kind
):
    store = main.ChatStore(tmp_path / f"kimi-cancelled-{result_kind}.db")
    store.initialize()
    store.upsert_chat({"id": "owner"})
    store.replace_turns("owner", [{"question": "first"}])
    operation = store.begin_clear_operation("owner", idempotency_key="request")
    store.transition_clear_operation(operation["operation_id"], "completed_clear_only")
    turn = {
        "question": "first", "answer_md": main.REQUESTING_TEXT,
        "model": main.DEFAULT_KIMI_MODEL, "request_status": "pending",
        "kimi_session_id": "session-x", "kimi_turn_id": "turn-x",
        "kimi_prompt_id": "prompt-x", "clear_operation_id": operation["operation_id"],
        "clear_revision": operation["revision"],
    }
    owner = {
        "chat_id": "owner", "session_id": "session-x", "turn_id": "turn-x",
        "turn_idx": 0, "prompt_id": "prompt-x", "owner_prompt_id": "prompt-x",
        "role": "active", "generation": 1,
    }
    owner_key = frame._kimi_owner_key_for(owner)
    frame.chat_store = store
    frame.active_chat_id = frame.current_chat_id = "owner"
    frame.active_session_turns = [turn]
    frame._current_chat_state = {"id": "owner", "turns": [turn], "kimi_request_queue": []}
    frame._kimi_prompt_owners[owner_key] = dict(owner)
    frame._kimi_active_turns["owner"] = dict(owner)

    if result_kind == "success":
        event = main.CodexEvent(
            type="turn_completed", thread_id="session-x", turn_id="turn-x", text="late answer",
            status="completed", data={"source_kind": "turn.ended", "agent_id": "main", "prompt_id": "prompt-x"},
        )
        assert frame._finalize_kimi_turn_state(
            "owner", frame._current_chat_state, frame.active_session_turns, 0, event
        ) == []
    else:
        frame._apply_kimi_error(
            "owner", "late failure", turn_idx=0, turn_id="turn-x", prompt_id="prompt-x"
        )

    assert turn["request_status"] == "pending"
    assert turn["answer_md"] == main.REQUESTING_TEXT
    assert owner_key in frame._kimi_prompt_owners
    assert "owner" in frame._kimi_active_turns
    assert store.get_clear_operation(operation["operation_id"])["state"] == "completed_clear_only"
