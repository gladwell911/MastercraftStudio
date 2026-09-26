"""End-to-end tests for the Kimi F1 thinking-narrative + answer-recovery feature.

These tests drive the whole stack black-box: real submit path, real WebSocket
event dispatch, real reconcile worker, real F1 list rendering. The fake Kimi
server only replaces the network (event push + REST transcripts).
"""

import time

from test_kimi_integration import (
    TEST_TURN_ID,
    KimiEvent,
    _active_chat_id,
    _setup_kimi_frame,
    _submit,
    event_to_payload,
)

import main
from codex_client import CodexEvent


def _visible_execution_labels(frame):
    return [frame.execution_list.GetString(i) for i in range(frame.execution_list.GetCount())]


def _render_execution_view(frame):
    frame._current_chat_state["detail_panel_mode"] = "execution"
    frame._render_execution_list(force=True)


def _emulate_newest_first_api(fake):
    """The real /messages endpoint returns newest-first pages; the shared fake
    store is oldest-first, so wrap list_messages to match server semantics."""
    original = fake.list_messages

    def newest_first(session_id, before_id=None, timeout=None):
        return list(reversed(original(session_id, before_id=before_id, timeout=timeout)))

    fake.list_messages = newest_first
    return fake


def _transcript_answer_turn(prompt_id, thinking_texts, answer_text, session_id):
    """Oldest-first transcript: prompt, thinking blocks, final answer."""
    rows = [
        {"id": prompt_id, "role": "user", "created_at": "2026-01-01T00:00:00Z",
         "content": [{"type": "text", "text": "question"}]},
    ]
    for index, text in enumerate(thinking_texts):
        rows.append({
            "id": f"think-{index}", "role": "assistant",
            "created_at": f"2026-01-01T00:00:{10 + index:02d}Z",
            "content": [{"type": "thinking", "text": text}],
        })
    rows.append({"id": "answer-1", "role": "assistant", "created_at": "2026-01-01T00:01:00Z",
                 "content": [{"type": "text", "text": answer_text}]})
    return rows


def _push(frame, chat_id, event):
    """Drive one protocol event through the real ws->UI dispatch path."""
    frame._dispatch_kimi_event_to_ui(chat_id, CodexEvent(**event_to_payload(event)))
    frame._drain_kimi_ui_events()


def test_e2e_turn_shows_thinking_rows_and_answer_without_tool_rows(frame, monkeypatch):
    fake = _emulate_newest_first_api(_setup_kimi_frame(frame, monkeypatch))
    _submit(frame, "分析这个问题")
    session_id = fake.created_sessions[0]["session_id"]
    chat_id = _active_chat_id(frame)
    prompt_id = fake.submitted[0]["prompt_id"]
    fake.messages_by_session[session_id] = _transcript_answer_turn(
        prompt_id, ["先分析问题结构", "再制定修复计划"], "最终回答", session_id
    )
    fake.status_by_session[session_id] = {"busy": False, "status": "completed"}

    _push(frame, chat_id, KimiEvent(type="turn_started", thread_id=session_id, turn_id=TEST_TURN_ID))
    _push(frame, chat_id, KimiEvent(
        type="item_started", thread_id=session_id, turn_id=TEST_TURN_ID, item_id="tool-1",
        title="Run", display_kind="command", status="running",
        data={"source_kind": "tool.call.started", "tool": {"name": "Shell"}}))

    # Mid-turn: the tool event triggers the throttled REST sync, so thinking
    # rows exist BEFORE the turn completes.
    steps_mid_turn = [
        step for step in frame._current_chat_state["execution_steps"]
        if step.get("source_kind") == "rest.thinking"
    ]
    assert [step["detail_text"] for step in steps_mid_turn] == ["先分析问题结构", "再制定修复计划"]
    assert not any(
        str(step.get("display_kind") or "") in {"command", "tool", "file", "diff", "search"}
        for step in frame._current_chat_state["execution_steps"]
    )

    _push(frame, chat_id, KimiEvent(
        type="item_completed", thread_id=session_id, turn_id=TEST_TURN_ID, item_id="tool-1",
        title="Run", display_kind="command", status="completed", exit_code=0,
        data={"source_kind": "tool.result", "tool": {"name": "Shell"}}))
    _push(frame, chat_id, KimiEvent(
        type="turn_completed", thread_id=session_id, turn_id=TEST_TURN_ID, status="completed",
        data={"source_kind": "prompt.completed", "adapter": "kimi_server", "authoritative": True}))

    # End of turn: the final answer arrives through the recovery path and the
    # F1 list renders thinking rows only (plus turn context), no tool rows.
    turn = frame.active_session_turns[-1]
    assert turn["answer_md"] == "最终回答"
    assert turn["request_status"] == "done"

    _render_execution_view(frame)
    labels = _visible_execution_labels(frame)
    assert "我：分析这个问题" in labels
    assert "先分析问题结构" in labels
    assert "再制定修复计划" in labels
    assert "小诸葛：最终回答" in labels
    assert not any("命令" in label or "读取文件" in label or "搜索" in label for label in labels)


def test_e2e_long_transcript_recovers_answer_without_no_final_answer_error(frame, monkeypatch):
    fake = _emulate_newest_first_api(_setup_kimi_frame(frame, monkeypatch))
    _submit(frame, "长历史任务")
    session_id = fake.created_sessions[0]["session_id"]
    chat_id = _active_chat_id(frame)
    prompt_id = fake.submitted[0]["prompt_id"]
    rows = [
        {"id": prompt_id, "role": "user", "created_at": "2026-01-01T00:00:00Z",
         "content": [{"type": "text", "text": "长历史任务"}]},
    ]
    for i in range(60):
        rows.append({
            "id": f"filler-{i:03d}", "role": "assistant",
            "created_at": f"2026-01-01T00:{1 + i // 60:02d}:{i % 60:02d}Z",
            "content": [{"type": "thinking", "text": f"思考步骤 {i}"}],
        })
    rows.append({"id": "final-1", "role": "assistant", "created_at": "2026-01-01T00:02:00Z",
                 "content": [{"type": "text", "text": "翻页找回的答案"}]})
    fake.messages_by_session[session_id] = rows
    fake.status_by_session[session_id] = {"busy": False, "status": "completed"}

    _push(frame, chat_id, KimiEvent(type="turn_started", thread_id=session_id, turn_id=TEST_TURN_ID))
    _push(frame, chat_id, KimiEvent(
        type="turn_completed", thread_id=session_id, turn_id=TEST_TURN_ID, status="completed",
        data={"source_kind": "prompt.completed", "adapter": "kimi_server", "authoritative": True}))

    turn = frame.active_session_turns[-1]
    assert turn["answer_md"] == "翻页找回的答案"
    assert "no final answer" not in str(turn["answer_md"])
    assert any(before_id for _sid, before_id in fake.list_messages_calls)


def test_e2e_idle_before_answer_persists_keeps_waiting_until_it_lands(frame, monkeypatch):
    fake = _emulate_newest_first_api(_setup_kimi_frame(frame, monkeypatch))
    _submit(frame, "迟到答案")
    session_id = fake.created_sessions[0]["session_id"]
    chat_id = _active_chat_id(frame)
    prompt_id = fake.submitted[0]["prompt_id"]
    fake.status_by_session[session_id] = {"busy": False, "status": "idle"}
    answered_rows = _transcript_answer_turn(prompt_id, ["等待期间的思考"], "迟到但最终到达的答案", session_id)
    fake.messages_by_session[session_id] = [
        {"id": prompt_id, "role": "user", "created_at": "2026-01-01T00:00:00Z",
         "content": [{"type": "text", "text": "迟到答案"}]},
    ]
    calls = {"n": 0}
    original = fake.list_messages

    def late_persist(session_id_arg, before_id=None, timeout=None):
        calls["n"] += 1
        if calls["n"] >= 3:
            fake.messages_by_session[session_id_arg] = answered_rows
        return original(session_id_arg, before_id=before_id, timeout=timeout)

    fake.list_messages = late_persist

    _push(frame, chat_id, KimiEvent(type="turn_started", thread_id=session_id, turn_id=TEST_TURN_ID))
    _push(frame, chat_id, KimiEvent(
        type="turn_completed", thread_id=session_id, turn_id=TEST_TURN_ID, status="completed",
        data={"source_kind": "prompt.completed", "adapter": "kimi_server", "authoritative": True}))

    for _ in range(100):
        turn = frame.active_session_turns[-1]
        if turn["request_status"] == "done":
            break
        time.sleep(0.05)
    turn = frame.active_session_turns[-1]
    assert turn["answer_md"] == "迟到但最终到达的答案"
    assert "no final answer" not in str(turn["answer_md"])
