"""Live end-to-end smoke test against a real spawned ``kimi web`` server.

Skipped unless KIMI_LIVE_TEST=1 and the kimi binary resolves. Exercises the
real KimiServerClient: spawn, auth, session create, streamed answer, abort,
and clean shutdown. Marked ``live`` so ``-m "not live"`` excludes it.
"""

from __future__ import annotations

import os
import threading
import time

import pytest

from kimi_server_client import (
    KimiServerClient,
    KimiServerError,
    resolve_kimi_launch_command,
)

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("KIMI_LIVE_TEST") != "1", reason="set KIMI_LIVE_TEST=1 to run live smoke test"),
]


@pytest.fixture(scope="module")
def live_client():
    try:
        resolve_kimi_launch_command()
    except KimiServerError as exc:
        pytest.skip(f"kimi binary unavailable: {exc}")
    client = KimiServerClient()
    client.start()
    yield client
    client.close()


def _drain_until(client: KimiServerClient, predicate, timeout: float = 180.0):
    """Drain queued messages until predicate(events) is truthy or timeout."""
    collected = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        drained = client.drain_pending_messages(limit=200)
        collected.extend(drained)
        if predicate(collected):
            return collected
        time.sleep(0.1)
    return collected


def _events_of(messages, event_type: str):
    out = []
    for message in messages:
        if message.get("type") != "event":
            continue
        event = (message.get("payload") or {}).get("event") or {}
        if event.get("type") == event_type:
            out.append(event)
    return out


def test_live_prompt_roundtrip(live_client):
    session_id = live_client.create_session(
        cwd=os.getcwd(),
        model="kimi/main",
        title="live smoke",
    )
    assert session_id.startswith("session_")

    prompt_id = live_client.submit_prompt(
        session_id,
        [{"type": "text", "text": "Reply with exactly the single word: pong. Do not use any tools."}],
    )
    assert prompt_id

    messages = _drain_until(
        live_client,
        lambda ms: any(e.get("status") == "completed" for e in _events_of(ms, "turn_completed")),
    )
    deltas = _events_of(messages, "agent_message_delta")
    answer_text = "".join(e.get("text") or "" for e in deltas if e.get("display_kind") == "assistant")
    assert "pong" in answer_text.lower()

    status = live_client.get_status(session_id)
    assert status.get("model")
    messages_list = live_client.list_messages(session_id)
    assert any(item.get("role") == "assistant" for item in messages_list)


def test_live_abort(live_client):
    session_id = live_client.create_session(cwd=os.getcwd(), model="kimi/main", title="live abort")
    prompt_id = live_client.submit_prompt(
        session_id,
        [{"type": "text", "text": "Count from 1 to 500, one number per line, no tools."}],
    )
    started = threading.Event()

    def _wait_start():
        _drain_until(live_client, lambda ms: bool(_events_of(ms, "turn_started")), timeout=60)
        started.set()

    waiter = threading.Thread(target=_wait_start, daemon=True)
    waiter.start()
    assert started.wait(timeout=70)
    live_client.abort(session_id, prompt_id)
    messages = _drain_until(
        live_client,
        lambda ms: bool(_events_of(ms, "turn_completed")),
        timeout=90,
    )
    assert _events_of(messages, "turn_completed"), "turn did not finish after abort"


def test_live_server_shutdown_restart_keeps_session():
    try:
        resolve_kimi_launch_command()
    except KimiServerError as exc:
        pytest.skip(f"kimi binary unavailable: {exc}")
    client = KimiServerClient()
    client.start()
    try:
        session_id = client.create_session(cwd=os.getcwd(), model="kimi/main", title="live restart")
    finally:
        client.close()
    client2 = KimiServerClient()
    client2.start()
    try:
        assert client2.session_exists(session_id)
        prompt_id = client2.submit_prompt(
            session_id,
            [{"type": "text", "text": "Reply with exactly: ok"}],
        )
        assert prompt_id
    finally:
        client2.close()


def test_live_frame_three_consecutive_chat_rounds_across_idle_heartbeat(frame, monkeypatch):
    """真实端到端：真实 ChatFrame + 真实 kimi web server，连续三轮问答。

    不 mock KimiServerClient、不替换后台线程，走完整 UI 提交链路
    （input_edit -> _on_send_clicked -> 后台 turn 线程 -> 真实 server ->
    事件泵回 UI）。手动 Yield 驱动 wx 事件循环等待每轮完成。
    """
    try:
        resolve_kimi_launch_command()
    except KimiServerError as exc:
        pytest.skip(f"kimi binary unavailable: {exc}")

    import main
    import wx

    # 关掉与本轮验证无关的副作用（标题自动生成会走其他模型 API）
    frame._refresh_openclaw_sync_lifecycle = lambda force_replay=False: None
    frame._play_send_sound = lambda: None
    frame._play_finish_sound = lambda: None
    frame._schedule_first_question_auto_title = lambda *a, **k: None
    monkeypatch.setattr(main, "AUTO_START_QUICK_TUNNEL", "0", raising=False)

    frame.model_combo.SetValue("Kimi Code")
    frame.selected_model = "kimi/main"

    app = wx.GetApp()
    questions = [
        "Reply with exactly the single word: one. Do not use any tools.",
        "Reply with exactly the single word: two. Do not use any tools.",
        "What word did I ask you to reply with in my first question? Answer with just that word.",
    ]
    expected = ["one", "two", "one"]

    for round_idx, (question, keyword) in enumerate(zip(questions, expected), start=1):
        if round_idx == 3:
            # Kimi 0.38.0 sends JSON pings every 10 seconds and closes after two
            # missed replies. Cross that window before reusing the same session.
            idle_deadline = time.monotonic() + 25
            while time.monotonic() < idle_deadline:
                app.Yield()
                time.sleep(0.05)
        turns_before = len(frame.active_session_turns)
        frame.input_edit.SetValue(question)
        frame._on_send_clicked(None)

        deadline = time.monotonic() + 150
        answer = ""
        status = ""
        while time.monotonic() < deadline:
            app.Yield()
            turns = frame.active_session_turns
            if len(turns) > turns_before:
                turn = turns[-1]
                status = str(turn.get("request_status") or "")
                answer = str(turn.get("answer_md") or "")
                if status == "done" and answer and answer != main.REQUESTING_TEXT:
                    break
                if status in ("error", "failed", "interrupted"):
                    break
            time.sleep(0.05)

        assert status == "done", f"第 {round_idx} 轮未完成，status={status!r} answer={answer[:200]!r}"
        assert keyword in answer.lower(), f"第 {round_idx} 轮回答不含 {keyword!r}: {answer[:200]!r}"
        assert frame.is_running is False, f"第 {round_idx} 轮后仍在运行"

    # 三轮应复用同一个服务端会话
    session_ids = {t.get("kimi_session_id") for t in frame.active_session_turns[-3:]}
    assert len(session_ids) == 1, f"三轮未复用同一会话: {session_ids}"

def test_live_frame_three_consecutive_chat_rounds_without_mainloop(frame, monkeypatch):
    """End-to-end 3-round Kimi chat while app.MainLoop is reported as not running."""
    try:
        resolve_kimi_launch_command()
    except KimiServerError as exc:
        pytest.skip(f"kimi binary unavailable: {exc}")

    import main
    import wx

    # Keep this test deterministic and offline-safe against unrelated runtime features.
    frame._refresh_openclaw_sync_lifecycle = lambda force_replay=False: None
    frame._play_send_sound = lambda: None
    frame._play_finish_sound = lambda: None
    frame._schedule_first_question_auto_title = lambda *a, **k: None
    monkeypatch.setattr(main, "AUTO_START_QUICK_TUNNEL", "0", raising=False)

    app = wx.GetApp()
    try:
        monkeypatch.setattr(app, "IsMainLoopRunning", lambda: False)
    except Exception:
        monkeypatch.setattr(main.ChatFrame, "_wx_main_loop_running", lambda self: False, raising=False)

    frame.model_combo.SetValue("Kimi Code")
    frame.selected_model = "kimi/main"

    questions = [
        "Reply with exactly the single word: one. Do not use any tools.",
        "Reply with exactly the single word: two. Do not use any tools.",
        "What word did I ask you to reply with in my first question? Answer with just that word.",
    ]
    expected = ["one", "two", "one"]

    for round_idx, (question, keyword) in enumerate(zip(questions, expected), start=1):
        turns_before = len(frame.active_session_turns)
        frame.input_edit.SetValue(question)
        frame._on_send_clicked(None)

        deadline = time.monotonic() + 150
        answer = ""
        status = ""
        while time.monotonic() < deadline:
            app.Yield()
            turns = frame.active_session_turns
            if len(turns) > turns_before:
                turn = turns[-1]
                status = str(turn.get("request_status") or "")
                answer = str(turn.get("answer_md") or "")
                if status == "done" and answer and answer != main.REQUESTING_TEXT:
                    break
                if status in ("error", "failed", "interrupted"):
                    break
            time.sleep(0.05)

        assert status == "done", f"round {round_idx} did not finish: status={status!r} answer={answer[:200]!r}"
        assert keyword in answer.lower(), f"round {round_idx} mismatch expected {keyword!r}: {answer[:200]!r}"
        assert frame.is_running is False, f"round {round_idx} should not stay running"

    session_ids = {t.get("kimi_session_id") for t in frame.active_session_turns[-3:]}
    assert len(session_ids) == 1, f"Expected one reused session: {session_ids}"
