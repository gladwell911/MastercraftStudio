import json
import threading

import main


class ImmediateThread:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        if self._target:
            self._target(*self._args, **self._kwargs)


def _usage(*, used=2048, window=128000, source="api", exact=True, model="openai/gpt-5.2"):
    return {
        "used_tokens": used,
        "context_window": window,
        "source": source,
        "exact": exact,
        "fresh": True,
        "model": model,
        "updated_at": 1.0,
    }


def test_e2e_regular_model_send_updates_context_usage_row_from_provider_usage(frame, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")
    monkeypatch.setattr(threading, "Thread", ImmediateThread)
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *args, **kwargs: fn(*args, **kwargs))
    monkeypatch.setattr(frame, "_call_later_if_alive", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)

    class FakeChatClient:
        def __init__(self, api_key, model):
            self.last_context_usage = None

        def stream_chat(self, user_text, on_delta, history_turns=None):
            on_delta("partial")
            self.last_context_usage = _usage(used=1536)
            return "final answer"

    monkeypatch.setattr(main, "ChatClient", FakeChatClient)

    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state = {"id": "chat-current", "title": "current", "turns": []}
    frame.active_session_turns = frame._current_chat_state["turns"]
    frame.model_combo.SetValue("openai/gpt-5.2")
    frame.input_edit.SetValue("question")

    frame._on_send_clicked(None)

    assert frame._current_chat_state["context_usage"]["used_tokens"] == 1536
    assert frame._pending_context_usage_by_turn == {}
    assert frame.answer_list.GetString(0) == "2k / 128k"
    assert "final answer" in list(frame.answer_list.GetStrings())


def test_e2e_codex_token_count_lifecycle_updates_top_row_and_preserves_selection(frame, monkeypatch):
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)
    monkeypatch.setattr(frame, "_push_remote_state", lambda *_args, **_kwargs: None)
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_turn_idx = 0
    frame.active_codex_turn_active = True
    frame.view_mode = "active"
    frame.active_session_turns = [
        {
            "question": "codex q",
            "answer_md": main.REQUESTING_TEXT,
            "model": "codex/main",
            "created_at": 1.0,
            "codex_turn_id": "turn-1",
            "request_status": "pending",
        }
    ]
    frame._current_chat_state = {"id": "chat-current", "turns": frame.active_session_turns}

    frame._render_answer_list()
    frame.answer_list.SetSelection(0)
    assert frame.answer_list.GetString(0) == "暂无"

    frame._on_codex_event_for_chat(
        "chat-current",
        main.CodexEvent(type="token_count", thread_id="thread-1", turn_id="turn-1", usage=_usage(used=44176, window=258400, source="codex", exact=True, model="gpt-5-codex")),
    )

    assert frame.answer_list.GetString(0) == "暂无"
    assert frame.answer_list.GetSelection() == 0
    assert ("chat-current", 0) in frame._pending_context_usage_by_turn

    frame._on_codex_event_for_chat(
        "chat-current",
        main.CodexEvent(type="turn_completed", thread_id="thread-1", turn_id="turn-1", text="done", status="completed"),
    )

    assert frame._current_chat_state["context_usage"]["used_tokens"] == 44176
    assert frame._pending_context_usage_by_turn == {}
    assert frame.answer_list.GetString(0) == "44k / 258k"
    assert frame.answer_list.GetSelection() == 0


def test_e2e_context_usage_persists_after_restart_and_history_switch(tmp_path, monkeypatch):
    state = {
        "active_chat": {
            "id": "chat-active",
            "title": "active",
            "turns": [{"question": "active q", "answer_md": "active a", "model": "openai/gpt-5.2", "created_at": 1.0}],
            "context_usage": _usage(used=4096),
        },
        "active_session_turns": [{"question": "active q", "answer_md": "active a", "model": "openai/gpt-5.2", "created_at": 1.0}],
        "active_chat_id": "chat-active",
        "current_chat_id": "chat-active",
        "archived_chats": [
            {
                "id": "chat-archived",
                "title": "archived",
                "turns": [{"question": "archived q", "answer_md": "archived a", "model": "codex/main", "created_at": 2.0}],
                "context_usage": _usage(used=44176, window=0, source="codex", exact=True, model="gpt-5-codex"),
            }
        ],
    }
    (tmp_path / "app_state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(main, "resolve_app_data_dir", lambda: tmp_path)

    restarted = main.ChatFrame()
    try:
        restarted.Hide()
        restarted.view_mode = "active"
        restarted._render_answer_list()
        assert restarted.answer_list.GetString(0) == "4k / 128k"

        restarted.view_mode = "history"
        restarted.view_history_id = "chat-archived"
        restarted._render_answer_list()
        assert restarted.answer_list.GetString(0) == "暂无"
    finally:
        restarted.Destroy()
