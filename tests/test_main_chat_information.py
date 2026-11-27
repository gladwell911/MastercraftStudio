import time

import main
import pytest


@pytest.mark.parametrize("model", ["openai/gpt-5.2", "claudecode/default", "codex/main", "openclaw/main"])
def test_remote_new_chat_from_history_clears_context_usage_for_model_variants(frame, model):
    frame.view_mode = "history"
    frame.view_history_id = "chat-history"
    frame.archived_chats = [
        {
            "id": "chat-history",
            "title": "历史会话",
            "context_usage": {
                "used_tokens": 31000,
                "context_window": 200000,
                "source": "openclaw",
                "exact": True,
                "fresh": True,
                "model": "claudecode/default",
                "updated_at": 1.0,
            },
            "turns": [],
            "created_at": 1.0,
            "updated_at": 1.0,
        }
    ]
    frame.active_session_turns = []
    frame.current_chat_id = "chat-current"
    frame.active_chat_id = "chat-current"
    frame._current_chat_state = {
        "id": "chat-current",
        "title": "当前聊天",
        "turns": [],
        "context_usage": {
            "used_tokens": 31000,
            "context_window": 200000,
            "source": "openclaw",
            "exact": True,
            "fresh": True,
            "model": model,
            "updated_at": 1.0,
        },
    }
    frame._pending_context_usage_by_turn = {
        ("chat-current", 0): {
            "used_tokens": 123,
            "context_window": 200000,
            "source": "openclaw",
            "exact": True,
            "fresh": True,
            "model": model,
            "updated_at": 1.0,
        }
    }

    frame._start_remote_new_chat({"model": model})

    assert frame.view_mode == "active"
    assert frame.view_history_id is None
    assert frame._pending_context_usage_by_turn == {}
    assert frame._current_chat_state.get("context_usage") is None
    assert frame.answer_list.GetString(0) == ("累计暂无" if model == "codex/main" else "暂无")


def test_on_done_uses_worker_context_usage_for_regular_model(frame):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_session_turns = [{"question": "q", "answer_md": "", "model": "openai/gpt-5.2", "created_at": 1.0}]
    frame._current_chat_state["id"] = "chat-current"
    frame._current_chat_state["turns"] = frame.active_session_turns
    frame._pending_context_usage_by_turn = {
        ("chat-current", 0): {
            "used_tokens": 1500,
            "context_window": 128000,
            "source": "api",
            "exact": True,
            "fresh": True,
            "model": "openai/gpt-5.2",
            "updated_at": 1.0,
        }
    }

    frame._on_done(0, "answer", "", "openai/gpt-5.2", "", frame.active_chat_id)

    assert frame._current_chat_state["context_usage"]["used_tokens"] == 1500
    assert frame.answer_list.GetString(0) == "2k / 128k"


def test_on_done_preserves_selected_context_usage_row_when_consuming_pending_usage(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_session_turns = [
        {"question": "q", "answer_md": main.REQUESTING_TEXT, "model": "openai/gpt-5.2", "created_at": 1.0}
    ]
    frame._current_chat_state = {"id": "chat-current", "turns": frame.active_session_turns}
    frame._current_chat_state["context_usage"] = {
        "used_tokens": 1000, "context_window": 128000, "source": "api",
    }
    frame._pending_context_usage_by_turn = {
        ("chat-current", 0): {
            "used_tokens": 1500,
            "context_window": 128000,
            "source": "api",
            "exact": True,
            "fresh": True,
            "model": "openai/gpt-5.2",
            "updated_at": 1.0,
        }
    }
    monkeypatch.setattr(frame, "_call_later_if_alive", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)

    frame._render_answer_list()
    frame.answer_list.SetSelection(0)

    frame._on_done(0, "answer", "", "openai/gpt-5.2", "", "chat-current")

    assert frame._current_chat_state["context_usage"]["used_tokens"] == 1500
    assert frame.answer_list.GetString(0) == "2k / 128k"
    assert frame.answer_list.GetSelection() == 0
    assert frame.answer_meta[0][0] == "context_usage"


def test_on_done_uses_pending_claudecode_context_usage(frame):
    frame.active_session_turns = [{"question": "q", "answer_md": "", "model": "claudecode/default", "created_at": 1.0}]
    frame._current_chat_state["turns"] = frame.active_session_turns
    chat_id = frame.active_chat_id or frame.current_chat_id or ""
    frame._pending_context_usage_by_turn = {
        (chat_id, 0): {
            "used_tokens": 894,
            "context_window": 200000,
            "source": "claudecode",
            "exact": True,
            "fresh": True,
            "model": "claude-haiku-4-5-20251001",
            "updated_at": 1.0,
        }
    }

    frame._on_done(0, "完成", "", "claudecode/default", "", frame.active_chat_id)

    assert frame._current_chat_state["context_usage"]["source"] == "claudecode"
    assert frame.answer_list.GetString(0) == "小于1k / 200k"


def test_on_done_uses_pending_openclaw_context_usage(frame):
    frame.active_session_turns = [{"question": "q", "answer_md": "", "model": "openclaw/main", "created_at": 1.0}]
    frame._current_chat_state["turns"] = frame.active_session_turns
    chat_id = frame.active_chat_id or frame.current_chat_id or ""
    frame._pending_context_usage_by_turn = {
        (chat_id, 0): {
            "used_tokens": 113260,
            "context_window": 272000,
            "source": "openclaw",
            "exact": True,
            "fresh": True,
            "model": "gpt-5.4",
            "updated_at": 1.0,
        }
    }

    frame._on_done(0, "", "", "openclaw/main", "", frame.active_chat_id)

    assert frame._current_chat_state["context_usage"]["source"] == "openclaw"
    assert frame._current_chat_state["context_usage"]["model"] == "gpt-5.4"
    assert frame._pending_context_usage_by_turn == {}


def test_on_done_does_not_apply_pending_context_usage_from_other_chat(frame):
    frame.active_chat_id = "chat-b"
    frame.current_chat_id = "chat-b"
    frame.active_session_turns = [{"question": "你好", "answer_md": "", "model": "openai/gpt-5.2", "created_at": 1.0}]
    frame._current_chat_state = {"id": "chat-b", "turns": frame.active_session_turns}
    frame._pending_context_usage_by_turn = {
        ("chat-a", 0): {
            "used_tokens": 1500,
            "context_window": 128000,
            "source": "api",
            "exact": True,
            "fresh": True,
            "model": "openai/gpt-5.2",
            "updated_at": 1.0,
        }
    }

    frame._on_done(0, "你好，有什么可以帮你？", "", "openai/gpt-5.2", "", "chat-b")

    assert "context_usage" not in frame._current_chat_state


def test_on_done_codex_does_not_consume_regular_model_pending_context_usage(frame):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_session_turns = [{"question": "q", "answer_md": "", "model": "codex/main", "created_at": 1.0}]
    frame._current_chat_state = {"id": "chat-current", "turns": frame.active_session_turns}
    pending = {
        "used_tokens": 1500,
        "context_window": 128000,
        "source": "api",
        "exact": True,
        "fresh": True,
        "model": "openai/gpt-5.2",
        "updated_at": 1.0,
    }
    frame._pending_context_usage_by_turn = {("chat-current", 0): pending}

    frame._on_done(0, "codex answer", "", "codex/main", "", "chat-current")

    assert "context_usage" not in frame._current_chat_state
    assert frame._pending_context_usage_by_turn == {("chat-current", 0): pending}


def test_on_done_uses_pending_codex_context_usage(frame):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_session_turns = [{"question": "q", "answer_md": "", "model": "codex/main", "created_at": 1.0}]
    frame._current_chat_state = {"id": "chat-current", "turns": frame.active_session_turns}
    usage = {
        "used_tokens": 44176,
        "context_window": 258400,
        "source": "codex",
        "exact": False,
        "fresh": True,
        "model": "gpt-5-codex",
        "updated_at": 1.0,
    }
    frame._pending_context_usage_by_turn = {("chat-current", 0): usage}

    frame._on_done(0, "codex answer", "", "codex/main", "", "chat-current")

    assert frame._current_chat_state["context_usage"] == usage
    assert frame._pending_context_usage_by_turn == {}


def test_kimi_context_usage_keeps_used_tokens_when_window_missing(frame):
    usage = frame._kimi_context_usage_payload(main.CodexEvent(
        type="thread_status_changed", usage={"context_tokens": 300},
    ))
    assert usage["used_tokens"] == 300
    assert usage["context_window"] == 0
    assert usage["exact"] is False
    assert frame._kimi_context_usage_payload(main.CodexEvent(
        type="thread_status_changed", usage={"context_tokens": -1},
    )) is None


def test_kimi_live_usage_fences_account_and_preserves_verified_idle_snapshot(frame, monkeypatch):
    frame.active_chat_id = frame.current_chat_id = "kimi-context-owner"
    chat = frame._current_chat_state
    chat.update(id=frame.active_chat_id, model="kimi/main", kimi_session_id="session",
                kimi_verified_account_id="A")
    monkeypatch.setattr(frame, "_current_kimi_usage_socket", lambda *_: True)
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)
    owner_a = frame._information_cache_owner(chat, "kimi")
    chat["kimi_verified_account_id"] = "B"
    usage = {"context_tokens": 400, "max_context_tokens": 1000}
    frame._apply_live_kimi_usage(frame.active_chat_id, "session", "status", usage, owner_a)
    assert not chat.get("context_usage")
    owner_b = frame._information_cache_owner(chat, "kimi")
    frame._apply_live_kimi_usage(frame.active_chat_id, "session", "status", usage, owner_b)
    frame._refresh_context_usage_after_done(chat, [], 0, "kimi/main")
    assert chat["context_usage"]["used_tokens"] == 400
    chat["kimi_information_generation"] = 1
    frame._refresh_context_usage_after_done(chat, [], 0, "kimi/main")
    assert "context_usage" not in chat


def test_codex_repeated_total_refreshes_once_when_snapshot_recovers(frame, monkeypatch):
    frame.active_chat_id = frame.current_chat_id = "codex-total-owner"
    chat = frame._current_chat_state
    chat.update(id=frame.active_chat_id, model="codex/main", codex_thread_id="thread",
                codex_turn_id="turn", codex_session_total_tokens=400, codex_snapshot_error=True)
    frame.active_session_turns = chat["turns"] = [
        {"model": "codex/main", "codex_thread_id": "thread", "codex_turn_id": "turn"},
    ]
    refreshes = []
    monkeypatch.setattr(frame, "_refresh_chat_information", refreshes.append)
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)
    event = main.CodexEvent(type="token_count", thread_id="thread", turn_id="turn",
                            data={"turn_idx": 0, "context_generation": 0, "session_total_tokens": 400})
    frame._on_codex_event_for_chat(frame.active_chat_id, event)
    frame._on_codex_event_for_chat(frame.active_chat_id, event)
    assert chat["codex_snapshot_error"] is False
    assert refreshes == [chat]


def test_codex_weekly_quota_uses_only_codex_10080_minute_window():
    weekly_window = main.ChatFrame._codex_weekly_quota_window
    label = main.ChatFrame._codex_weekly_quota_label
    assert "剩余 75.0%" in label(weekly_window({"rateLimits": {"limitId": "codex", "primary": {
        "windowDurationMins": 300, "usedPercent": 90}, "secondary": {
        "windowDurationMins": 10080, "usedPercent": 25, "resetsAt": time.time() + 3600}}}))
    assert weekly_window({"rateLimits": {"limitId": "other", "primary": {
        "windowDurationMins": 10080, "usedPercent": 25}}}) is None
    assert weekly_window({"rateLimits": {"limitId": "codex", "primary": {
        "windowDurationMins": 300, "usedPercent": 25}}}) is None
    assert "剩余比例暂不可用" in label({"resetsAt": time.time() + 3600})
    assert "重置时间暂不可用" in label({"usedPercent": 25})


def test_codex_without_pending_usage_clears_stale_context_usage(frame):
    frame.active_session_turns = [{"question": "你好", "answer_md": "", "model": "codex/main", "created_at": 1.0}]
    frame._current_chat_state = {
        "id": "chat-current",
        "turns": frame.active_session_turns,
        "context_usage": {
            "used_tokens": 1500,
            "context_window": 128000,
            "source": "api",
            "exact": True,
            "fresh": True,
            "model": "openai/gpt-5.2",
            "updated_at": 1.0,
        },
    }
    frame._pending_context_usage_by_turn = {}

    frame._on_done(0, "codex answer", "", "codex/main", "", "chat-current")

    assert "context_usage" not in frame._current_chat_state
    assert frame.answer_list.GetString(0) == "累计暂无"


def test_focus_latest_answer_ignores_context_usage_row(frame, monkeypatch):
    frame.active_session_turns = [
        {"question": "q", "answer_md": "a", "model": "openai/gpt-5.2", "created_at": 1.0}
    ]
    frame._current_chat_state["context_usage"] = {
        "used_tokens": 12000,
        "context_window": 128000,
        "source": "estimated",
        "exact": False,
        "fresh": True,
        "model": "openai/gpt-5.2",
        "updated_at": 1.0,
    }
    monkeypatch.setattr(frame, "_can_focus_completion_result", lambda: True)

    frame._render_answer_list()
    frame._focus_latest_answer()

    selected = frame.answer_list.GetSelection()
    assert frame.answer_meta[selected][0] == "answer"


def test_large_context_usage_after_done_is_deferred_off_ui_thread(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state = {"id": "chat-current", "turns": []}
    turns = [
        {"question": f"q{idx}", "answer_md": f"a{idx}", "model": "openai/gpt-5.2"}
        for idx in range(500)
    ]
    started = []

    class FakeThread:
        def __init__(self, target, daemon=True):
            self.target = target
            self.daemon = daemon

        def start(self):
            started.append(self.target)

    monkeypatch.setattr(main.threading, "Thread", FakeThread)
    monkeypatch.setattr(main, "estimate_turns_tokens", lambda *args, **kwargs: pytest.fail("large context usage should not be estimated on UI thread"))

    frame._refresh_context_usage_after_done(frame._current_chat_state, turns, len(turns) - 1, "openai/gpt-5.2")

    assert started
    assert "context_usage" not in frame._current_chat_state


def test_regular_context_usage_after_done_is_deferred_off_ui_thread(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state = {"id": "chat-current", "turns": []}
    turns = [
        {"question": "q", "answer_md": "a", "model": "openai/gpt-5.2"},
    ]
    started = []

    class FakeThread:
        def __init__(self, target, daemon=True):
            self.target = target
            self.daemon = daemon

        def start(self):
            started.append(self.target)

    monkeypatch.setattr(main.threading, "Thread", FakeThread)
    monkeypatch.setattr(main, "estimate_turns_tokens", lambda *args, **kwargs: pytest.fail("context usage should not be estimated on UI thread"))

    frame._refresh_context_usage_after_done(frame._current_chat_state, turns, 0, "openai/gpt-5.2")

    assert started
    assert "context_usage" not in frame._current_chat_state


def test_context_usage_estimate_applies_state_without_refreshing_visible_header(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_session_turns = [{"question": "q", "answer_md": "a", "model": "openai/gpt-5.2"}]
    frame._current_chat_state = {"id": "chat-current", "turns": frame.active_session_turns}
    frame.view_mode = "active"
    refreshes = []
    saves = []
    usage = {
        "used_tokens": 2000,
        "context_window": 128000,
        "source": "estimated",
        "exact": False,
        "fresh": True,
        "model": "openai/gpt-5.2",
    }
    monkeypatch.setattr(frame, "_refresh_context_usage_header_rows", lambda: refreshes.append(True))
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: saves.append(True))

    frame._render_answer_list()
    assert frame.answer_list.GetString(0) == main.UNKNOWN_TIME_LABEL

    frame._apply_context_usage_estimate(("chat-current", 0), usage)

    assert frame._current_chat_state["context_usage"] == usage
    assert frame.answer_list.GetString(0) == main.UNKNOWN_TIME_LABEL
    assert refreshes == []
