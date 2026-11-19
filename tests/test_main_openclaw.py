import main
import pytest
import time
from main_test_support import assert_request_metadata


def test_visible_model_order_prioritizes_codex_then_claudecode_then_openclaw():
    visible = list(main.VISIBLE_MODEL_IDS)

    assert visible[:6] == [
        "codex/main",
        "codex/gpt-5.4-medium",
        "codex/gpt-5.3-codex-spark-high",
        "claudecode/default",
        "kimi/main",
        "openclaw/main",
    ]


def test_send_click_still_sends_while_running(frame, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    frame._stop_openclaw_sync()
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda force_replay=False: None)
    class ImmediateThread:
        def __init__(self, *, target, args=(), kwargs=None, daemon=None, name=None):
            self.target = target
            self.args = args
            self.kwargs = kwargs or {}

        def start(self):
            self.target(*self.args, **self.kwargs)

    monkeypatch.setattr(main.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(main.wx, "MessageBox", lambda message, *_: pytest.fail(message))
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)
    monkeypatch.setattr(frame, "_focus_latest_answer", lambda: None)
    monkeypatch.setattr(main.wx, "CallLater", lambda _delay, fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(main.ChatClient, "stream_chat", lambda self, question, on_delta, history_turns=None: "第二条")
    frame.active_session_turns = [
        {"question": "第一条", "answer_md": "", "model": "openai/gpt-5.2", "created_at": time.time()}
    ]
    frame.is_running = True
    frame._active_request_count = 1
    frame.model_combo.SetValue("openai/gpt-5.2")
    frame.input_edit.SetValue("第二条")

    frame._on_send_clicked(None)

    assert len(frame.active_session_turns) == 2
    assert frame.active_session_turns[-1]["question"] == "第二条"
    assert frame.active_session_turns[-1]["answer_md"] == "第二条"


def test_idle_openclaw_lifecycle_flush_defers_while_primary_control_has_focus(frame, monkeypatch):
    frame._openclaw_lifecycle_dirty = True
    delayed = []
    monkeypatch.setattr(frame, "_primary_navigation_control_has_focus", lambda: True)
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda: pytest.fail("focused controls should defer OpenClaw lifecycle refresh"))
    monkeypatch.setattr(frame, "_call_later_if_alive", lambda delay_ms, fn, *args: delayed.append((delay_ms, fn, args)) or object())

    frame._flush_idle_ui_refreshes()

    assert frame._openclaw_lifecycle_dirty is True
    assert delayed == [(main.IDLE_UI_REFRESH_DELAY_MS, frame._flush_idle_ui_refreshes, ())]


def test_idle_openclaw_lifecycle_flush_runs_when_user_is_idle(frame, monkeypatch):
    frame._openclaw_lifecycle_dirty = True
    calls = []
    monkeypatch.setattr(frame, "_primary_navigation_control_has_focus", lambda: False)
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda: calls.append("refresh"))

    frame._flush_idle_ui_refreshes()

    assert calls == ["refresh"]
    assert frame._openclaw_lifecycle_dirty is False


def test_on_done_openclaw_without_pending_usage_does_not_estimate(frame):
    frame.active_session_turns = [{"question": "你好", "answer_md": "", "model": "openclaw/main", "created_at": 1.0}]
    frame._current_chat_state["turns"] = frame.active_session_turns
    frame._pending_context_usage_by_turn = {}

    frame._on_done(0, "", "", "openclaw/main", "", frame.active_chat_id)

    assert "context_usage" not in frame._current_chat_state


def test_apply_openclaw_sync_event_user_turn_adds_recovery_metadata(frame, monkeypatch):
    monkeypatch.setattr(main.time, "time", lambda: 5678.5)
    frame.active_session_turns = []

    event = main.OpenClawSyncEvent(event_id="evt-1", role="user", text="同步问题", timestamp=5678.5)

    result = frame._apply_openclaw_sync_event(event)

    assert result == "visible"
    turn = frame.active_session_turns[-1]
    assert_request_metadata(
        turn,
        status="done",
        model="openclaw/main",
        question="同步问题",
        recoverable=False,
        mode="retry",
        resume_token={},
        attempt_count=0,
    )


def test_apply_openclaw_sync_event_assistant_turn_adds_recovery_metadata(frame, monkeypatch):
    monkeypatch.setattr(main.time, "time", lambda: 5678.5)
    frame.active_session_turns = []

    event = main.OpenClawSyncEvent(event_id="evt-2", role="assistant", text="同步回答", timestamp=5678.5)

    result = frame._apply_openclaw_sync_event(event)

    assert result == "visible"
    turn = frame.active_session_turns[-1]
    assert_request_metadata(
        turn,
        status="done",
        model="openclaw/main",
        question="",
        recoverable=False,
        mode="retry",
        resume_token={},
        attempt_count=0,
    )
    assert turn["answer_md"] == "同步回答"


def test_openclaw_assistant_only_turn_hides_empty_user_rows(frame):
    frame._stop_openclaw_sync()
    frame.active_session_turns = [
        {
            "question": "",
            "answer_md": "外部同步的回复",
            "model": "openclaw/main",
            "created_at": time.time(),
        }
    ]
    frame.view_mode = "active"
    frame._render_answer_list()

    rows = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    meta_types = [meta[0] for meta in frame.answer_meta]

    assert rows[1:] == ["小诸葛", "外部同步的回复"]
    assert meta_types[1:] == ["ai", "answer"]


def test_openclaw_offset_only_poll_does_not_save_state(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state = {"id": "chat-current", "turns": []}
    frame.active_openclaw_sync_offset = 10
    saves = []
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: saves.append(kwargs))

    frame._apply_openclaw_sync_batch(
        {
            "chat_id": "chat-current",
            "offset": 20,
            "file_changed": False,
            "session_changed": False,
        },
        [],
    )

    assert saves == []
    assert frame.active_openclaw_sync_offset == 20
