import json
import main
import pytest
import time
from codex_client import CodexEvent
from execution_projection import should_show_execution_step
from kimi_server_client import KimiEvent, KimiServerClient, event_to_payload, map_session_event
from kimi_test_support import (
    TEST_TURN_ID,
    _active_chat_id,
    _canonical_kimi_rows,
    _injection,
    _sdk_push,
    _setup_kimi_frame,
    _submit,
)
from pathlib import Path


def test_sdk_thinking_is_visible_before_step_completion_with_empty_rest(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "真实流思考")
    session_id = fake.created_sessions[0]["session_id"]
    transport = KimiServerClient()
    _sdk_push(frame, transport, session_id, "turn.started", 1)
    _sdk_push(frame, transport, session_id, "turn.step.started", 2, step=1, stepId="sdk-step-1")
    text = "先核对现有实现。\n\n" + "这是公开的完整细节。" * 350
    replay = _sdk_push(frame, transport, session_id, "thinking.delta", 2, offset=0, delta=text)
    _sdk_push(frame, transport, session_id, "thinking.delta", 2, offset=len(text), delta="然后验证受影响行为。")
    assert fake.messages_by_session.get(session_id, []) == []
    row = next(row for row in _canonical_kimi_rows(frame) if row.get("kind") != "time")
    assert row["list_text"] == "先核对现有实现。"
    assert row["detail_text"] == text + "然后验证受影响行为。"
    before = frame._current_chat_state["execution_steps"][:]
    transport._handle_ws_message(replay)
    assert transport.drain_pending_messages(10) == []
    assert frame._current_chat_state["execution_steps"] == before
    assert frame.active_session_turns[0]["answer_md"] == main.REQUESTING_TEXT


@pytest.mark.parametrize("private_fragment", [0, 1])
def test_sdk_hidden_stream_redacts_live_row_and_durable_assembler(frame, monkeypatch, private_fragment):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "隐私边界")
    session_id = fake.created_sessions[0]["session_id"]
    transport = KimiServerClient()
    _sdk_push(frame, transport, session_id, "turn.started", 1)
    _sdk_push(frame, transport, session_id, "turn.step.started", 2, step=1, stepId="private-step")
    offset = 0
    for index, text in enumerate(("EARLY SECRET ", "LATE SECRET")):
        _sdk_push(frame, transport, session_id, "thinking.delta", 2, offset=offset, delta=text, hidden=index == private_fragment)
        offset += len(text)
    assert not [step for step in frame._current_chat_state["execution_steps"] if should_show_execution_step(step)]
    assert "SECRET" not in str(frame._current_chat_state["execution_steps"])
    _canonical_kimi_rows(frame)
    with frame.chat_store._connect() as connection:
        states = connection.execute("SELECT state_json FROM execution_assemblers WHERE chat_id=?", (_active_chat_id(frame),)).fetchall()
    assert states and "SECRET" not in str([row["state_json"] for row in states])


def test_rest_progress_rejects_stale_owner_and_later_prompt_content(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "本轮")
    session_id = fake.created_sessions[0]["session_id"]
    owner = frame._find_kimi_prompt_owner(fake.submitted[0]["prompt_id"], session_id=session_id)
    messages = [{"id": owner["prompt_id"], "role": "user", "content": []},
                {"id": "next-prompt", "role": "user", "content": []},
                {"id": "other-turn", "role": "assistant", "content": [{"type": "thinking", "thinking": "别轮内容"}]}]
    assert not frame._kimi_sync_thinking_rows(_active_chat_id(frame), owner, messages)
    assert not frame._kimi_sync_thinking_rows(_active_chat_id(frame), dict(owner, revision=-1), messages)
    assert "别轮内容" not in str(frame._current_chat_state["execution_steps"])


def test_sdk_gap_waits_for_prefix_and_rest_completion_survives_late_delta(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "补齐片段")
    session_id = fake.created_sessions[0]["session_id"]
    transport = KimiServerClient()
    _sdk_push(frame, transport, session_id, "turn.started", 1)
    _sdk_push(frame, transport, session_id, "turn.step.started", 2, step=1, stepId="gap-step")
    _sdk_push(frame, transport, session_id, "thinking.delta", 2, offset=3, delta="后半段")
    assert not _canonical_kimi_rows(frame)
    _sdk_push(frame, transport, session_id, "thinking.delta", 2, offset=0, delta="前三字")
    assert _canonical_kimi_rows(frame)[0]["detail_text"] == "前三字后半段"
    owner = frame._find_kimi_prompt_owner(fake.submitted[0]["prompt_id"], session_id=session_id)
    messages = [{"id": owner["prompt_id"], "role": "user", "content": []},
                {"id": "rest-complete", "role": "assistant", "content": [{"type": "thinking", "thinking": "前三字后半段完整完成。"}]}]
    assert frame._kimi_sync_thinking_rows(_active_chat_id(frame), owner, messages)
    _sdk_push(frame, transport, session_id, "thinking.delta", 2, offset=7, delta="晚到片段")
    rows = _canonical_kimi_rows(frame)
    assert len(rows) == 1 and rows[0]["detail_text"] == "前三字后半段完整完成。"
    messages[1]["content"][0]["hidden"] = True
    frame._kimi_sync_thinking_rows(_active_chat_id(frame), owner, messages)
    assert not _canonical_kimi_rows(frame)
    assert "前三字" not in str(frame.chat_store.load_execution_steps(_active_chat_id(frame)))
    with frame.chat_store._connect() as connection:
        states = connection.execute("SELECT state_json FROM execution_assemblers WHERE chat_id=?", (_active_chat_id(frame),)).fetchall()
    assert "前三字" not in str([row["state_json"] for row in states])


@pytest.mark.parametrize("after_finalization", [False, True])
def test_rest_steering_alias_keeps_original_step_ownership(frame, monkeypatch, after_finalization):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "原始问题")
    session_id = fake.created_sessions[0]["session_id"]
    transport = KimiServerClient()
    _sdk_push(frame, transport, session_id, "turn.started", 1)
    _sdk_push(frame, transport, session_id, "turn.step.started", 2, step=1, stepId="original-step")
    _sdk_push(frame, transport, session_id, "thinking.delta", 2, offset=0, delta="原始步骤。")
    _sdk_push(frame, transport, session_id, "turn.step.completed", 3, step=1, stepId="original-step")
    _submit(frame, "追加要求")
    _sdk_push(frame, transport, session_id, "turn.step.started", 4, step=2, stepId="steered-step")
    _sdk_push(frame, transport, session_id, "thinking.delta", 4, offset=0, delta="追加步骤。")
    original = fake.submitted[0]["prompt_id"]
    alias = fake.submitted[1]["prompt_id"]
    owner = frame._find_kimi_prompt_owner(original, session_id=session_id)
    messages = [{"id": original, "role": "user", "content": []},
                {"id": "first", "role": "assistant", "content": [{"type": "thinking", "thinking": "原始步骤。全文。"}]},
                _injection("before-alias"),
                {"id": alias, "role": "user", "content": []},
                _injection("after-alias"),
                {"id": "second", "role": "assistant", "content": [{"type": "thinking", "thinking": "追加步骤。全文。"}]}]
    messages.extend([
        {"id": "real-next", "role": "user", "content": "later request"},
        {"id": "foreign-step", "role": "assistant", "content": [
            {"type": "thinking", "text": "foreign step"}]},
    ])
    if after_finalization:
        fake.messages_by_session[session_id] = messages
        callbacks = []
        original_call_after = frame._call_after_if_alive
        def defer_sync(fn, *args, **kwargs):
            if fn == frame._start_kimi_thinking_sync:
                callbacks.append((fn, args, kwargs))
                return
            return original_call_after(fn, *args, **kwargs)
        monkeypatch.setattr(frame, "_call_after_if_alive", defer_sync)
        frame._kimi_thinking_fetch_worker(owner)
        assert len(callbacks) == 1
        fake.push_event(KimiEvent(type="turn_completed", thread_id=session_id, turn_id="1", text="Final answer", status="completed",
                                 data={"source_kind": "rest.reconciled", "stream_complete": True, "prompt_id": original,
                                       "owner_generation": owner["generation"], "idle_verified": True}))
        assert not frame._kimi_prompt_owners
        fn, args, kwargs = callbacks[0]
        fn(*args, **kwargs)
    else:
        assert frame._kimi_sync_thinking_rows(_active_chat_id(frame), owner, messages)
    steps = [step for step in frame._current_chat_state["execution_steps"] if step.get("display_kind") == "thinking"]
    assert [(step["kimi_step_id"], step["detail_text"]) for step in steps] == [
        ("original-step", "原始步骤。全文。"), ("steered-step", "追加步骤。全文。")]
    assert [step["kimi_step_number"] for step in steps] == [1, 2]
    assert all(step["turn_idx"] == owner["turn_idx"] and step["turn_id"] == owner["turn_id"] for step in steps)
    assert "foreign step" not in str(_canonical_kimi_rows(frame))


def test_sdk_reverse_fragments_in_one_batch_reach_store_in_absolute_order(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "Order")
    session_id = fake.created_sessions[0]["session_id"]
    transport = KimiServerClient()
    _sdk_push(frame, transport, session_id, "turn.started", 1)
    _sdk_push(frame, transport, session_id, "turn.step.started", 2, step=1)
    for offset, text in ((3, "BBB"), (0, "AAA")):
        transport._handle_ws_message({"type": "thinking.delta", "session_id": session_id, "epoch": "sdk-epoch", "seq": 2,
                                      "offset": offset, "volatile": True,
                                      "payload": {"type": "thinking.delta", "turnId": 1, "agentId": "main", "delta": text}})
    queued = transport.drain_pending_messages(10)
    assert len(queued) == 2
    for message in queued:
        frame._on_kimi_event_for_chat(_active_chat_id(frame), CodexEvent(**message["payload"]["event"]))
    assert [row["detail_text"] for row in _canonical_kimi_rows(frame)] == ["AAABBB"]


@pytest.mark.parametrize("initially_private,after_tool", [(False, False), (False, True), (True, False)])
def test_sdk_assistant_privacy_redacts_commentary_cache_and_answer_segments(frame, monkeypatch, initially_private, after_tool):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "Privacy")
    session_id = fake.created_sessions[0]["session_id"]
    transport = KimiServerClient()
    _sdk_push(frame, transport, session_id, "turn.started", 1)
    _sdk_push(frame, transport, session_id, "turn.step.started", 2, step=1, stepId="assistant-step")
    _sdk_push(frame, transport, session_id, "assistant.delta", 2, offset=0, delta="SECRET prose", hidden=initially_private)
    if not initially_private:
        assert frame._kimi_answer_parts(_active_chat_id(frame), session_id) == "SECRET prose"
        if after_tool:
            _sdk_push(frame, transport, session_id, "tool.call.started", 3, toolCallId="read", name="Read")
            assert any(row["detail_text"] == "SECRET prose" for row in _canonical_kimi_rows(frame))
        _sdk_push(frame, transport, session_id, "assistant.delta", 2, offset=0, delta="SECRET prose", hidden=True)
    assert "SECRET" not in str(frame._execution_delta_buffer)
    assert "SECRET" not in str(frame._kimi_turn_answer_parts)
    assert "SECRET" not in str(frame._kimi_turn_answer_segments)
    if not after_tool:
        _sdk_push(frame, transport, session_id, "tool.call.started", 3, toolCallId="read", name="Read")
    assert not [row for row in _canonical_kimi_rows(frame) if "SECRET" in row["detail_text"]]
    assert "SECRET" not in str(frame.chat_store.load_execution_steps(_active_chat_id(frame)))
    assert frame._kimi_answer_parts(_active_chat_id(frame), session_id) == ""
    with frame.chat_store._connect() as connection:
        states = connection.execute("SELECT state_json FROM execution_assemblers WHERE chat_id=?", (_active_chat_id(frame),)).fetchall()
    assert "SECRET" not in str([row["state_json"] for row in states])
    frame._execution_delta_buffer.clear()
    frame._kimi_execution_private_keys.clear()
    frame._current_chat_state["execution_steps"] = []
    monkeypatch.setattr(frame, "chat_store", main.ChatStore(frame.chat_store.db_path))
    _sdk_push(frame, transport, session_id, "assistant.delta", 3, offset=len("SECRET prose"), delta="SECRET late")
    assert any(state.get("private_reasoning") for state in frame._execution_delta_buffer.values())
    assert "SECRET" not in str(frame._execution_delta_buffer)
    assert frame._kimi_answer_parts(_active_chat_id(frame), session_id) == ""
    _sdk_push(frame, transport, session_id, "turn.step.started", 4, step=2, stepId="final-step")
    _sdk_push(frame, transport, session_id, "assistant.delta", 4, offset=0, delta="Public final answer")
    assert frame._kimi_answer_parts(_active_chat_id(frame), session_id) == "Public final answer"


@pytest.mark.parametrize("target,flag,value", [
    (target, flag, value) for target in ("message", "block")
    for flag, value in (("hidden", True), ("private", True), ("disclosable", False))
])
def test_rest_only_commentary_withdraws_changed_privacy_before_dedupe(frame, monkeypatch, target, flag, value):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "REST only")
    session_id = fake.created_sessions[0]["session_id"]
    owner = frame._find_kimi_prompt_owner(fake.submitted[0]["prompt_id"], session_id=session_id)
    prose = ("Commentary excerpt.\n" + "Complete public detail. " * 120).strip()
    messages = [{"id": owner["prompt_id"], "role": "user", "content": []},
                {"id": "rest-commentary", "role": "assistant", "content": [
                    {"type": "text", "text": prose}, {"type": "tool_use", "tool_name": "Read"}]},
                {"id": "rest-final", "role": "assistant", "content": [{"type": "text", "text": "Final public answer"}]}]
    chat_id = _active_chat_id(frame)
    assert frame._kimi_sync_thinking_rows(chat_id, owner, messages)
    assert not frame._kimi_sync_thinking_rows(chat_id, owner, messages)
    rows = _canonical_kimi_rows(frame)
    assert len(rows) == 1
    assert rows[0]["list_text"] == "Commentary excerpt."
    assert rows[0]["detail_text"] == prose
    assert frame._kimi_rest_answer_for_prompt(messages, owner["prompt_id"]) == "Final public answer"
    protected = messages[1] if target == "message" else messages[1]["content"][0]
    protected[flag] = value
    assert frame._kimi_sync_thinking_rows(chat_id, owner, messages)
    assert not _canonical_kimi_rows(frame)
    assert "Commentary excerpt" not in str(frame.chat_store.load_execution_steps(chat_id))
    assert frame._kimi_rest_answer_for_prompt(messages, owner["prompt_id"]) == "Final public answer"


@pytest.mark.parametrize("target", ["message", "block"])
@pytest.mark.parametrize("restriction", [
    {"hidden": True}, {"private": True}, {"non_disclosable": True}, {"disclosable": False},
    {"visibility": "hidden"}, {"reasoning_visibility": "non-disclosable"},
])
def test_rest_answer_consumer_excludes_restrictive_message_or_text_block(frame, target, restriction):
    answer = {"id": "answer", "role": "assistant", "content": [
        {"type": "text", "text": "PUBLIC "}, {"type": "text", "text": "PROTECTED"}]}
    messages = [{"id": "prompt", "role": "user", "content": []}, answer]
    assert frame._kimi_rest_answer_for_prompt(messages, "prompt") == "PUBLIC PROTECTED"
    (answer if target == "message" else answer["content"][1]).update(restriction)
    assert frame._kimi_rest_answer_for_prompt(messages, "prompt") == ("" if target == "message" else "PUBLIC")


@pytest.mark.parametrize("aggregate", [False, True])
def test_rest_mixed_thinking_block_privacy_withdraws_persisted_matching_narrative(frame, monkeypatch, aggregate):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "Mixed blocks")
    session_id = fake.created_sessions[0]["session_id"]
    transport = KimiServerClient()
    _sdk_push(frame, transport, session_id, "turn.started", 1)
    _sdk_push(frame, transport, session_id, "turn.step.started", 2, step=1, stepId="mixed-step")
    if aggregate:
        _sdk_push(frame, transport, session_id, "thinking.delta", 2, offset=0, delta="SECRET and public")
    owner = frame._find_kimi_prompt_owner(fake.submitted[0]["prompt_id"], session_id=session_id)
    messages = [{"id": owner["prompt_id"], "role": "user", "content": []},
                {"id": "mixed", "role": "assistant", "content": [
                    {"type": "thinking", "thinking": "SECRET block"},
                    {"type": "thinking", "thinking": "Public sibling"}]}]
    chat_id = _active_chat_id(frame)
    assert frame._kimi_sync_thinking_rows(chat_id, owner, messages)
    assert len(_canonical_kimi_rows(frame)) == (1 if aggregate else 2)
    messages[1]["content"][0]["hidden"] = True
    assert frame._kimi_sync_thinking_rows(chat_id, owner, messages)
    assert [row["detail_text"] for row in _canonical_kimi_rows(frame)] == ([] if aggregate else ["Public sibling"])
    assert "SECRET" not in str(frame.chat_store.load_execution_steps(chat_id))


@pytest.mark.parametrize("kind", ["thinking", "assistant"])
def test_sdk_rest_first_privacy_reaches_completed_row_with_different_stream_id(frame, monkeypatch, kind):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "REST first")
    session_id = fake.created_sessions[0]["session_id"]
    transport = KimiServerClient()
    _sdk_push(frame, transport, session_id, "turn.started", 1)
    _sdk_push(frame, transport, session_id, "turn.step.started", 2, step=1, stepId="rest-first-step")
    owner = frame._find_kimi_prompt_owner(fake.submitted[0]["prompt_id"], session_id=session_id)
    content = ([{"type": "thinking", "thinking": "SECRET complete"}] if kind == "thinking" else
               [{"type": "text", "text": "SECRET complete"}, {"type": "tool_use", "tool_name": "Read"}])
    messages = [{"id": owner["prompt_id"], "role": "user", "content": []},
                {"id": "rest-first", "role": "assistant", "content": content}]
    chat_id = _active_chat_id(frame)
    assert frame._kimi_sync_thinking_rows(chat_id, owner, messages)
    row_id = _canonical_kimi_rows(frame)[0]["row_id"]
    _sdk_push(frame, transport, session_id, f"{kind}.delta", 2, offset=0, delta="SECRET complete", hidden=True)
    assert not _canonical_kimi_rows(frame)
    assert "SECRET" not in str(frame.chat_store.load_execution_steps(chat_id))
    assert any(row_id.endswith(step["canonical_item_id"].removeprefix("execution-")) and step.get("private_reasoning")
               for step in frame._current_chat_state["execution_steps"])


@pytest.mark.parametrize("missing_owner", [False, True])
def test_rest_progress_requires_explicit_complete_owning_prompt_boundary(frame, monkeypatch, missing_owner):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "New turn")
    session_id = fake.created_sessions[0]["session_id"]
    owner = frame._find_kimi_prompt_owner(fake.submitted[0]["prompt_id"], session_id=session_id)
    messages = [{"id": "previous", "role": "assistant", "content": [{"type": "thinking", "thinking": "Previous turn"}]}]
    if missing_owner:
        owner = dict(owner, prompt_id="", owner_prompt_id="")
    assert not frame._kimi_sync_thinking_rows(_active_chat_id(frame), owner, messages)
    assert not _canonical_kimi_rows(frame)
    assert "Previous turn" not in str(frame.chat_store.load_execution_steps(_active_chat_id(frame)))


def test_sdk_rest_append_failure_keeps_live_buffer_and_successful_retry(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "Retry")
    session_id = fake.created_sessions[0]["session_id"]
    transport = KimiServerClient()
    _sdk_push(frame, transport, session_id, "turn.started", 1)
    _sdk_push(frame, transport, session_id, "turn.step.started", 2, step=1, stepId="retry-step")
    _sdk_push(frame, transport, session_id, "thinking.delta", 2, offset=0, delta="Live prefix")
    owner = frame._find_kimi_prompt_owner(fake.submitted[0]["prompt_id"], session_id=session_id)
    chat_id = _active_chat_id(frame)
    row_id = _canonical_kimi_rows(frame)[0]["row_id"]
    messages = [{"id": owner["prompt_id"], "role": "user", "content": []},
                {"id": "retry", "role": "assistant", "content": [{"type": "thinking", "thinking": "Live prefix complete"}]}]
    original_persist = frame._persist_execution_step_or_queue
    monkeypatch.setattr(frame, "_persist_execution_step_or_queue", lambda *_args, **_kwargs: False)
    assert not frame._kimi_sync_thinking_rows(chat_id, owner, messages)
    assert frame._execution_delta_buffer
    assert not any("retry:thinking" in keys for keys in frame._kimi_thinking_synced.values())
    monkeypatch.setattr(frame, "_persist_execution_step_or_queue", original_persist)
    assert frame._kimi_sync_thinking_rows(chat_id, owner, messages)
    assert not frame._execution_delta_buffer
    rows = _canonical_kimi_rows(frame)
    assert [(row["row_id"], row["detail_text"]) for row in rows] == [(row_id, "Live prefix complete")]


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
    assert "status" not in kinds  # turn_started is protocol noise
    assert "command" not in kinds  # tool events never become F1 list rows
    assert not any(step.get("kimi_summary") for step in steps)
    commands = [str(step.get("command") or "") for step in steps]
    assert "ls" not in commands


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
    fake.push_event(KimiEvent(type="agent_message_delta", thread_id=session_id, turn_id=TEST_TURN_ID, event_id="thinking-1", fragment_id="f0", offset=0, text="The", raw_text="The", display_kind="thinking", data=source_data))
    fake.push_event(KimiEvent(type="thread_status_changed", thread_id=session_id, turn_id=TEST_TURN_ID, status="streaming"))
    fake.push_event(KimiEvent(type="agent_message_delta", thread_id=session_id, turn_id=TEST_TURN_ID, event_id="thinking-1", fragment_id="f3", offset=3, text=" user", raw_text=" user", display_kind="thinking", data={**source_data, "offset": 3}))
    fake.push_event(KimiEvent(type="agent_message_delta", thread_id=session_id, turn_id=TEST_TURN_ID, event_id="thinking-1", fragment_id="f3", offset=3, text=" user", raw_text=" user", display_kind="thinking", data={**source_data, "offset": 3}))
    fake.push_event(KimiEvent(type="agent_message_delta", thread_id=session_id, turn_id=TEST_TURN_ID, event_id="thinking-1", fragment_id="f0", offset=0, text="The", raw_text="The", display_kind="thinking", data=source_data))
    fake.push_event(KimiEvent(type="agent_message_delta", thread_id=session_id, turn_id=TEST_TURN_ID, text="progress", raw_text="progress", display_kind="commentary", data={"source_kind": "tool.progress", "agent_id": "main"}))
    fake.push_event(KimiEvent(type="agent_message_delta", thread_id=session_id, turn_id=TEST_TURN_ID, text="答案", raw_text="答案", display_kind="assistant", data={"source_kind": "assistant.delta", "offset": 0}))

    steps = frame._current_chat_state.get("execution_steps") or []
    summaries = [step.get("list_text") for step in steps if step.get("kimi_summary")]
    assert summaries == ["The user"]
    assert [step["detail_text"] for step in steps if step.get("kimi_summary")] == ["The user"]
    assert all("progress" not in str(step.get("list_text") or "") for step in steps)
    assert not frame._execution_delta_buffer
    assert frame._kimi_turn_answer_parts[(_active_chat_id(frame), session_id, TEST_TURN_ID)] == ["答案"]


def test_mapped_private_reasoning_is_withheld_through_store(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "private")
    session_id = fake.created_sessions[0]["session_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id=TEST_TURN_ID))
    mapped = map_session_event({
        "type": "thinking.delta", "session_id": session_id, "seq": 1, "offset": 0, "volatile": True,
        "payload": {"type": "thinking.delta", "turnId": TEST_TURN_ID, "delta": "PRIVATE COT", "private": True},
    }, step_scope={"step_id": "private-step", "step_number": 1, "epoch": "private-epoch"})
    frame._on_kimi_event_for_chat(_active_chat_id(frame), main.CodexEvent(**event_to_payload(mapped)))
    frame._flush_execution_delta(_active_chat_id(frame), TEST_TURN_ID)
    step = next(step for step in frame._current_chat_state["execution_steps"] if step.get("private_reasoning"))
    assert step["list_text"] == ""
    assert not should_show_execution_step(step)
    assert step["detail_text"] == ""
    assert step["source_detail"] == {}
    assert "PRIVATE COT" not in str(step)
    frame._flush_execution_step_persists_sync()
    stored = frame.chat_store.load_execution_steps(_active_chat_id(frame))
    assert "PRIVATE COT" not in str(stored)


@pytest.mark.parametrize("private_fragment", [0, 1])
def test_thinking_privacy_is_monotonic_across_buffered_fragments_and_restart(frame, monkeypatch, private_fragment):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "private stream")
    session_id = fake.created_sessions[0]["session_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id=TEST_TURN_ID))
    fragments = ["EARLY SECRET ", "LATE SECRET"]
    offset = 0
    for index, fragment in enumerate(fragments):
        payload = {"type": "thinking.delta", "turnId": TEST_TURN_ID, "agentId": "main",
                   "messageId": "private-thinking", "fragmentId": f"private-{index}",
                   "offset": offset, "delta": fragment}
        if index == private_fragment:
            payload["nonDisclosable"] = True
        mapped = map_session_event({"type": "thinking.delta", "session_id": session_id, "offset": offset, "payload": payload})
        frame._on_kimi_event_for_chat(_active_chat_id(frame), main.CodexEvent(**event_to_payload(mapped)))
        offset += len(fragment)
    frame._flush_execution_delta(_active_chat_id(frame), TEST_TURN_ID)
    step = next(step for step in frame._current_chat_state["execution_steps"] if step.get("private_reasoning"))
    assert step["list_text"] == ""
    assert not should_show_execution_step(step)
    assert step["detail_text"] == ""
    assert step["source_detail"] == {}
    assert "SECRET" not in str(step)
    frame._flush_execution_step_persists_sync()
    restarted = main.ChatStore(frame.chat_store.db_path)
    restarted.initialize()
    assert "SECRET" not in str(restarted.load_execution_steps(_active_chat_id(frame)))


@pytest.mark.parametrize("parts", [
    ["S" * 3001],
    [" leading " + ("A" * 1700), ("B" * 1700) + " trailing "],
])
def test_long_mapped_assistant_answer_is_exact_and_creates_no_execution_row(frame, monkeypatch, parts):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "long answer")
    session_id = fake.created_sessions[0]["session_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id=TEST_TURN_ID))
    offset = 0
    for part in parts:
        mapped = map_session_event({
            "type": "assistant.delta", "session_id": session_id, "offset": offset,
            "payload": {"type": "assistant.delta", "turnId": TEST_TURN_ID, "agentId": "main", "delta": part},
        })
        frame._on_kimi_event_for_chat(_active_chat_id(frame), main.CodexEvent(**event_to_payload(mapped)))
        offset += len(part)
    fake.push_event(KimiEvent(type="turn_completed", thread_id=session_id, turn_id=TEST_TURN_ID, status="completed"))
    assert frame.active_session_turns[-1]["answer_md"] == "".join(parts)
    assert not any(step.get("display_kind") == "assistant" for step in frame._current_chat_state["execution_steps"])


def test_tool_progress_creates_no_execution_row_and_nothing_persists(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "progress")
    session_id = fake.created_sessions[0]["session_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id=TEST_TURN_ID))
    fake.push_event(KimiEvent(type="item_started", thread_id=session_id, turn_id=TEST_TURN_ID,
        item_id="tool-p", title="Run", display_kind="command",
        data={"source_kind": "tool.call.started", "operation_kind": "command"}))
    fake.push_event(KimiEvent(type="agent_message_delta", thread_id=session_id, turn_id=TEST_TURN_ID,
        item_id="tool-p", text=" line 1 ", raw_text=" line 1 ", display_kind="commentary",
        data={"source_kind": "tool.progress"}))
    steps = [step for step in frame._current_chat_state["execution_steps"] if step.get("item_id") == "tool-p"]
    assert steps == []
    frame._flush_execution_step_persists_sync()
    restarted = main.ChatStore(frame.chat_store.db_path)
    restarted.initialize()
    stored = [step for step in restarted.load_execution_steps(_active_chat_id(frame)) if step.get("item_id") == "tool-p"]
    assert stored == []


def test_structured_kimi_tool_events_create_no_list_rows(frame, monkeypatch):
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
    assert steps == []
    assert not any(
        str(step.get("display_kind") or "") in {"search", "file", "command", "agent", "tool", "diff", "test", "skill"}
        for step in frame._current_chat_state["execution_steps"]
    )
    assert not any(
        raw in str(step.get("list_text") or "") + str(step.get("detail_text") or "")
        for step in frame._current_chat_state["execution_steps"]
        for raw in ("Search source tree", "README.md", "pytest", "Delegate review")
    )


def test_mapped_test_retry_error_warning_and_unknown_actions_project_safe_stages(frame):
    messages = [
        {"type": "tool.call.started", "payload": {"type": "tool.call.started", "toolCallId": "test-1", "display": {"kind": "command", "operation": "test"}}},
        {"type": "turn.step.retrying", "payload": {"type": "turn.step.retrying", "stepId": "retry-1"}},
        {"type": "turn.step.interrupted", "payload": {"type": "turn.step.interrupted", "stepId": "stop-1", "message": "raw failure"}},
        {"type": "warning", "payload": {"type": "warning", "message": "raw warning"}},
        {"type": "database.query.started", "payload": {"type": "database.query.started", "itemId": "unknown-1"}},
    ]
    labels = []
    for message in messages:
        message["session_id"] = "session"
        message["payload"]["turnId"] = "turn"
        mapped = map_session_event(message)
        entry = frame._build_execution_entry(main.CodexEvent(**event_to_payload(mapped)))
        labels.append(entry["list_text"] if entry else None)
    assert labels == [None, "正在等待", "执行失败", "执行警告", "正在处理任务"]
    assert all("raw" not in label for label in labels if label)


def test_kimi_tool_completion_updates_started_item_with_result_and_failure(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "运行命令")
    session_id = fake.created_sessions[0]["session_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id=TEST_TURN_ID))
    fake.push_event(KimiEvent(
        type="item_started", thread_id=session_id, turn_id=TEST_TURN_ID,
        item_id="tool-command", title="Run checks", command="pytest -q",
        display_kind="command", status="running",
        data={"source_kind": "tool.call.started", "tool": {"name": "Shell"}},
    ))
    fake.push_event(KimiEvent(
        type="item_completed", thread_id=session_id, turn_id=TEST_TURN_ID,
        item_id="tool-command", exit_code=2, text="two tests failed",
        display_kind="command", status="failed",
        data={"source_kind": "tool.result", "tool": {}},
    ))

    matching = [
        step for step in frame._current_chat_state["execution_steps"]
        if step.get("item_id") == "tool-command"
    ]
    assert len(matching) == 1
    assert matching[0]["list_text"] == "执行失败命令"
    assert "Run checks" not in matching[0]["list_text"]
    assert "pytest" not in matching[0]["list_text"]
    assert "退出码：2" in matching[0]["detail_text"]
    assert "two tests failed" in matching[0]["detail_text"]
    frame._flush_execution_step_persists_sync()
    stored = [step for step in frame.chat_store.load_execution_steps(_active_chat_id(frame)) if step.get("item_id") == "tool-command"]
    assert len(stored) == 1
    assert stored[0]["event_type"] == "item_completed"
    assert stored[0]["status"] == "failed"
    assert stored[0]["_execution_uid"] == matching[0]["_execution_uid"]
    assert "two tests failed" in stored[0]["detail_text"]


def test_kimi_tool_results_no_longer_create_execution_entries(frame):
    success = frame._build_execution_entry(main.CodexEvent(
        type="item_completed", item_id="tool-success", display_kind="command", status="completed",
        data={"adapter": "kimi_server", "source_kind": "tool.result", "tool": {"name": "Shell", "exitCode": "0"}},
    ))
    failure = frame._build_execution_entry(main.CodexEvent(
        type="item_completed", item_id="tool-failure", display_kind="command", status="completed",
        data={"adapter": "kimi_server", "source_kind": "tool.result", "tool": {"name": "Shell", "exitCode": "3"}},
    ))

    assert success is None
    assert failure is None


def test_kimi_completion_does_not_overwrite_reused_item_id_from_another_turn(frame):
    frame.active_chat_id = frame.current_chat_id = "chat-current"
    frame.active_turn_idx = 1
    frame._current_chat_state = {
        "id": "chat-current",
        "turns": [{"question": "one"}, {"question": "two"}],
        "execution_steps": [{
            "event_type": "item_started", "source_kind": "tool.call.started",
            "thread_id": "session-a", "turn_id": "1", "turn_idx": 0,
            "item_id": "reused", "_execution_uid": "started-uid", "list_text": "turn one",
        }],
    }
    completion = {
        "event_type": "item_completed", "source_kind": "tool.result",
        "thread_id": "session-a", "turn_id": "2", "turn_idx": 1,
        "item_id": "reused", "event_id": "reused", "list_text": "turn two complete", "detail_text": "done",
    }

    assert frame._append_execution_entry_to_chat("chat-current", completion, save_state=False)

    steps = frame._current_chat_state["execution_steps"]
    assert [step["turn_id"] for step in steps] == ["1", "2"]
    assert steps[0]["_execution_uid"] == "started-uid"
    assert steps[1]["_execution_uid"] != "started-uid"
    assert steps[1]["event_id"] != "reused"


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
    assert [step["list_text"] for step in steps] == ["正在分析问题", "已完成搜索"]
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
    assert steps == []


def test_early_answer_fragments_are_not_truncated_at_legacy_bucket_limit(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    deferred = []
    original_call_after = frame._call_after_if_alive

    def defer_landing(fn, *args, **kwargs):
        if getattr(fn, "__name__", "") in {"_apply_kimi_startup_state", "_apply_kimi_thread_state"}:
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


def test_subagent_assistant_delta_is_not_exposed_as_execution_progress(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "main task")
    session_id = fake.created_sessions[0]["session_id"]
    fake.push_event(KimiEvent(
        type="agent_message_delta", thread_id=session_id, turn_id="7",
        text="subagent prose", display_kind="assistant",
        data={"agent_id": "agent-1", "source_kind": "assistant.delta"},
    ))

    assert not any("subagent prose" in str(step) for step in frame._current_chat_state.get("execution_steps", []))
    assert frame.active_session_turns[0]["answer_md"] == main.REQUESTING_TEXT


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


def test_rest_thinking_blocks_sync_to_execution_rows_without_duplicates(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "think hard")
    session_id = fake.created_sessions[0]["session_id"]
    chat_id = _active_chat_id(frame)
    prompt_id = fake.submitted[0]["prompt_id"]
    owner = frame._find_kimi_prompt_owner(prompt_id, session_id=session_id)
    assert owner is not None
    long_line = "先分析一个很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长的问题" + "再长一点" * 10
    full_text = long_line + "\n\n第二段：得出结论"
    messages = [
        {"id": prompt_id, "role": "user", "created_at": "2026-01-01T00:00:00Z",
         "content": [{"type": "text", "text": "think hard"}]},
        {"id": "assistant-1", "role": "assistant", "created_at": "2026-01-01T00:00:01Z",
         "content": [
             {"type": "thinking", "text": full_text},
             {"type": "thinking", "text": "第二条思路"},
             {"type": "text", "text": "done"},
         ]},
        {"id": "assistant-2", "role": "assistant", "created_at": "2026-01-01T00:00:02Z",
         "content": [{"type": "thinking", "text": "私密推理", "private": True}]},
    ]

    assert frame._kimi_sync_thinking_rows(chat_id, owner, messages) is True

    steps = [step for step in frame._current_chat_state["execution_steps"] if step.get("source_kind") == "rest.thinking"]
    assert [step["detail_text"] for step in steps] == [full_text, "第二条思路"]
    assert [step["list_text"] for step in steps] == [frame._kimi_thinking_excerpt(full_text), "第二条思路"]
    assert all(step["list_text"] == step["kimi_summary"] for step in steps)
    assert len(steps[0]["list_text"]) <= 80
    assert "\n" not in steps[0]["list_text"]
    assert steps[0]["display_kind"] == "thinking"
    assert steps[0]["turn_idx"] == owner["turn_idx"]

    # 再次同步同样的内容：记忆去重 + item_id 逻辑键都不得产生重复行
    assert frame._kimi_sync_thinking_rows(chat_id, owner, messages) is False
    steps_again = [step for step in frame._current_chat_state["execution_steps"] if step.get("source_kind") == "rest.thinking"]
    assert len(steps_again) == 2


def test_kimi_tool_events_produce_no_kimi_summary_rows(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "跑工具")
    session_id = fake.created_sessions[0]["session_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id=TEST_TURN_ID))
    fake.push_event(KimiEvent(type="item_started", thread_id=session_id, turn_id=TEST_TURN_ID,
        item_id="tool-1", title="Run", display_kind="command",
        data={"source_kind": "tool.call.started", "tool": {"name": "Shell"}}))
    fake.push_event(KimiEvent(type="item_completed", thread_id=session_id, turn_id=TEST_TURN_ID,
        item_id="tool-1", title="Run", display_kind="command", exit_code=0,
        data={"source_kind": "tool.result", "tool": {"name": "Shell"}}))
    fake.push_event(KimiEvent(type="agent_message_delta", thread_id=session_id, turn_id=TEST_TURN_ID,
        text="working", raw_text="working", display_kind="commentary",
        data={"source_kind": "tool.progress"}))

    steps = frame._current_chat_state.get("execution_steps") or []
    assert not any(step.get("kimi_summary") for step in steps)
    assert not any(str(step.get("display_kind") or "") in {"command", "tool"} for step in steps)


def test_thinking_sync_orders_newest_first_pages_into_transcript_order(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "order check")
    session_id = fake.created_sessions[0]["session_id"]
    chat_id = _active_chat_id(frame)
    prompt_id = fake.submitted[0]["prompt_id"]
    owner = frame._find_kimi_prompt_owner(prompt_id, session_id=session_id)
    assert owner is not None
    # Endpoint pages are newest-first; the sync must emit transcript order.
    messages = [
        {"id": "assistant-2", "role": "assistant", "created_at": "2026-01-01T00:00:02Z",
         "content": [{"type": "thinking", "text": "第二条思路"}]},
        {"id": prompt_id, "role": "user", "created_at": "2026-01-01T00:00:00Z",
         "content": [{"type": "text", "text": "order check"}]},
        {"id": "assistant-1", "role": "assistant", "created_at": "2026-01-01T00:00:01Z",
         "content": [{"type": "thinking", "text": "第一条思路"}]},
    ]

    assert frame._kimi_sync_thinking_rows(chat_id, owner, messages) is True

    steps = [step for step in frame._current_chat_state["execution_steps"] if step.get("source_kind") == "rest.thinking"]
    assert [step["detail_text"] for step in steps] == ["第一条思路", "第二条思路"]


def test_thinking_fetch_worker_appends_rows_through_ui_marshal(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "mid-turn")
    session_id = fake.created_sessions[0]["session_id"]
    chat_id = _active_chat_id(frame)
    prompt_id = fake.submitted[0]["prompt_id"]
    owner = frame._find_kimi_prompt_owner(prompt_id, session_id=session_id)
    assert owner is not None
    fake.messages_by_session[session_id] = [
        {"id": "assistant-1", "role": "assistant", "created_at": "2026-01-01T00:00:01Z",
         "content": [{"type": "thinking", "text": "中途思考"}]},
        {"id": prompt_id, "role": "user", "created_at": "2026-01-01T00:00:00Z",
         "content": [{"type": "text", "text": "mid-turn"}]},
    ]

    frame._kimi_thinking_fetch_worker(owner)

    steps = [step for step in frame._current_chat_state["execution_steps"] if step.get("source_kind") == "rest.thinking"]
    assert [step["detail_text"] for step in steps] == ["中途思考"]


def test_maybe_trigger_kimi_thinking_sync_spawns_throttled_fetch(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "trigger check")
    session_id = fake.created_sessions[0]["session_id"]
    chat_id = _active_chat_id(frame)
    prompt_id = fake.submitted[0]["prompt_id"]
    owner = frame._find_kimi_prompt_owner(prompt_id, session_id=session_id)
    assert owner is not None
    frame._kimi_active_turns[chat_id] = {
        "turn_idx": owner["turn_idx"], "turn_id": owner.get("turn_id") or "",
        "session_id": session_id, "model": "kimi/main",
    }
    fake.messages_by_session[session_id] = [
        {"id": "assistant-1", "role": "assistant", "created_at": "2026-01-01T00:00:01Z",
         "content": [{"type": "thinking", "text": "事件触发的中途思考"}]},
        {"id": prompt_id, "role": "user", "created_at": "2026-01-01T00:00:00Z",
         "content": [{"type": "text", "text": "trigger check"}]},
    ]
    event = KimiEvent(type="item_started", thread_id=session_id, turn_id=TEST_TURN_ID,
        item_id="tool-1", display_kind="file",
        data={"source_kind": "tool.call.started", "tool": {"name": "Read"}})

    frame._maybe_trigger_kimi_thinking_sync(chat_id, main.CodexEvent(**event_to_payload(event)))

    steps = []
    for _ in range(60):
        steps = [step for step in frame._current_chat_state["execution_steps"] if step.get("source_kind") == "rest.thinking"]
        if steps:
            break
        time.sleep(0.05)
    assert [step["detail_text"] for step in steps] == ["事件触发的中途思考"]


def test_failed_tool_events_create_no_execution_row(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "failing tool")
    session_id = fake.created_sessions[0]["session_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id=TEST_TURN_ID))
    fake.push_event(KimiEvent(type="item_started", thread_id=session_id, turn_id=TEST_TURN_ID,
        item_id="tool-fail", title="Run", display_kind="command", status="running",
        data={"source_kind": "tool.call.started", "tool": {"name": "Shell"}}))
    fake.push_event(KimiEvent(type="item_completed", thread_id=session_id, turn_id=TEST_TURN_ID,
        item_id="tool-fail", title="Run", display_kind="command", status="failed", exit_code=1,
        data={"source_kind": "tool.result", "tool": {"name": "Shell", "exitCode": 1}}))

    steps = frame._current_chat_state.get("execution_steps") or []
    assert not any(step.get("kimi_summary") for step in steps)
    assert not any(str(step.get("display_kind") or "") in {"command", "tool"} for step in steps)
