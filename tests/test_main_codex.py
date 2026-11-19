import json
import main
import pytest
import time
import wx
from main_test_support import assert_request_metadata, desktop_source
from pathlib import Path


def test_main_does_not_call_respond_tool_request_user_input_directly():
    source = desktop_source()
    assert "respond_tool_request_user_input" not in source


def test_background_codex_event_defers_answer_list_mutation_during_quiet(frame, monkeypatch):
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.view_mode = "active"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {
            "question": "q",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": 1.0,
            "request_status": "pending",
            "codex_turn_id": "turn-1",
        }
    ]
    frame._current_chat_state = {
        "id": "chat-active",
        "turns": frame.active_session_turns,
        "execution_steps": [],
        "codex_turn_id": "turn-1",
    }
    frame._background_ui_update_depth = 1
    frame._navigation_quiet_until = time.monotonic() + 3.0
    monkeypatch.setattr(
        frame,
        "_update_active_answer_row",
        lambda *_args, **_kwargs: pytest.fail("background answer row update must be deferred during quiet"),
    )
    monkeypatch.setattr(
        frame,
        "_refresh_answer_list_preserving_selection",
        lambda *_args, **_kwargs: pytest.fail("background answer list refresh must be deferred during quiet"),
    )

    event = main.CodexEvent(type="subagent_result", turn_id="turn-1", text="background answer")
    frame._on_codex_event_for_chat("chat-active", event)

    assert frame.active_session_turns[0]["answer_md"] == "background answer"
    assert frame._background_answer_list_dirty is True


def test_codex_speed_combo_uses_short_accessible_label(frame):
    def _static_text_labels(window):
        labels = []
        for child in window.GetChildren():
            if isinstance(child, wx.StaticText):
                labels.append(child.GetLabel())
            labels.extend(_static_text_labels(child))
        return labels

    labels = _static_text_labels(frame)

    assert "速度：" in labels
    assert "Codex速度：" not in labels
    assert frame.codex_speed_combo.GetName() == "速度"


def test_codex_speed_combo_change_defers_save_and_preserves_focus(frame, monkeypatch):
    frame.selected_model = main.DEFAULT_CODEX_MODEL
    frame.model_combo.SetValue(main.model_display_name(main.DEFAULT_CODEX_MODEL))
    frame._current_chat_state = {
        "id": "chat-current",
        "model": main.DEFAULT_CODEX_MODEL,
        "codex_service_tier": "",
        "turns": [],
    }
    frame.active_session_turns = frame._current_chat_state["turns"]
    frame.codex_speed_combo.SetValue("快速")
    frame.codex_speed_combo.SetFocusFromKbd()
    saves = []
    deferred = []
    statuses = []
    refreshes = []
    renders = []
    syncs = []
    pushes = []
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: saves.append((args, kwargs)))
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: deferred.append(True))
    monkeypatch.setattr(frame, "_refresh_history", lambda *args, **kwargs: refreshes.append(True))
    monkeypatch.setattr(frame, "_render_answer_list", lambda *args, **kwargs: renders.append(True))
    monkeypatch.setattr(frame, "_sync_codex_speed_combo_from_chat", lambda *args, **kwargs: syncs.append(True))
    monkeypatch.setattr(frame, "_push_remote_state", lambda *args, **kwargs: pushes.append(True))
    monkeypatch.setattr(frame, "SetStatusText", lambda text: statuses.append(text))

    frame._on_codex_speed_changed(None)

    assert frame._current_chat_state["codex_service_tier"] == "fast"
    assert saves == []
    assert deferred == [True]
    assert refreshes == []
    assert renders == []
    assert syncs == []
    assert pushes == []
    assert frame.model_combo.GetValue() == main.model_display_name(main.DEFAULT_CODEX_MODEL)
    assert frame.codex_speed_combo.HasFocus()
    assert statuses == ["Codex 速度已切换为快速"]


def test_codex_speed_combo_change_noops_when_value_is_unchanged(frame, monkeypatch):
    frame.selected_model = main.DEFAULT_CODEX_MODEL
    frame.model_combo.SetValue(main.model_display_name(main.DEFAULT_CODEX_MODEL))
    frame._current_chat_state = {
        "id": "chat-current",
        "model": main.DEFAULT_CODEX_MODEL,
        "codex_service_tier": "fast",
        "turns": [],
    }
    frame.active_session_turns = frame._current_chat_state["turns"]
    frame.codex_speed_combo.SetValue("快速")
    saves = []
    deferred = []
    statuses = []
    syncs = []
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: saves.append(True))
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: deferred.append(True))
    monkeypatch.setattr(frame, "_sync_codex_speed_combo_from_chat", lambda *args, **kwargs: syncs.append(True))
    monkeypatch.setattr(frame, "SetStatusText", lambda text: statuses.append(text))

    frame._on_codex_speed_changed(None)

    assert frame._current_chat_state["codex_service_tier"] == "fast"
    assert saves == []
    assert deferred == []
    assert syncs == []
    assert statuses == []


def test_remote_message_preserves_existing_chat_model_when_payload_uses_default_codex(frame, monkeypatch):
    captured = {}

    frame.active_chat_id = "chat-gpt54"
    frame.current_chat_id = "chat-gpt54"
    frame.selected_model = "codex/gpt-5.4-medium"
    frame._current_chat_state.update(
        {
            "id": "chat-gpt54",
            "title": "gpt5.4 chat",
            "model": "codex/gpt-5.4-medium",
            "turns": [
                {
                    "question": "old",
                    "answer_md": "answer",
                    "model": "codex/gpt-5.4-medium",
                }
            ],
        }
    )

    def fake_submit_question(question, **kwargs):
        captured["call"] = (question, kwargs)
        return True, ""

    monkeypatch.setattr(frame, "_submit_question", fake_submit_question)
    monkeypatch.setattr(frame, "_push_remote_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *_args, **_kwargs: None)

    status, body = frame._remote_api_message_ui(
        {"chat_id": "chat-gpt54", "text": "continue", "model": "codex/main"}
    )

    assert status == 200
    assert body["accepted"] is True
    assert body["model"] == "codex/gpt-5.4-medium"
    assert captured["call"][1]["model"] == "codex/gpt-5.4-medium"
    assert frame.selected_model == "codex/gpt-5.4-medium"


def test_codex_loader_warning_stderr_event_does_not_append_execution_step(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_codex_thread_id = "thread-current"
    frame.active_codex_turn_id = "turn-current"
    frame.active_session_turns = [{"question": "q", "answer_md": "", "model": main.DEFAULT_CODEX_MODEL, "created_at": 1.0}]
    frame._current_chat_state = {
        "id": "chat-current",
        "title": "当前聊天",
        "turns": frame.active_session_turns,
        "detail_panel_mode": "execution",
        "execution_steps": [],
    }
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    frame._on_codex_event_for_chat(
        "chat-current",
        main.CodexEvent(
            type="stderr",
            thread_id="thread-current",
            turn_id="turn-current",
            text="\x1b[2m2026-05-05T03:13:03.752275Z\x1b[0m \x1b[33mWARN\x1b[0m \x1b[2mcodex_core_plugins::manifest\x1b[0m\x1b[2m:\x1b[0m ignoring interface metadata",
        ),
    )

    assert frame._current_chat_state["execution_steps"] == []


def test_remote_new_chat_clears_stale_active_codex_state_without_turns(frame, monkeypatch):
    frame.active_chat_id = "chat-old"
    frame.current_chat_id = "chat-old"
    frame.active_session_turns = []
    frame.active_codex_thread_id = "thread-stale"
    frame.active_codex_turn_id = "turn-stale"
    frame.active_codex_turn_active = True
    frame.active_codex_pending_prompt = "pending"
    frame.active_codex_pending_request = {"kind": "user_input"}
    frame.active_codex_request_queue = [{"prompt": "queued"}]
    frame.active_codex_thread_flags = ["waitingOnUserInput"]
    frame.active_codex_latest_assistant_text = "partial"
    frame.active_codex_latest_assistant_phase = "answer"
    frame._current_chat_state = {"id": "chat-old", "turns": []}

    monkeypatch.setattr(frame, "_archive_active_session", lambda **_kwargs: None)
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_refresh_history", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_render_answer_list", lambda: None)
    monkeypatch.setattr(frame, "SetStatusText", lambda _text: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *_args, **_kwargs: None)

    created = frame._start_remote_new_chat({"model": "codex/gpt-5.4-medium"})

    assert created["model"] == "codex/gpt-5.4-medium"
    assert frame.active_codex_thread_id == ""
    assert frame.active_codex_turn_id == ""
    assert frame.active_codex_turn_active is False
    assert frame.active_codex_pending_prompt == ""
    assert frame.active_codex_pending_request is None
    assert frame.active_codex_request_queue == []
    assert frame.active_codex_thread_flags == []
    assert frame.active_codex_latest_assistant_text == ""
    assert frame.active_codex_latest_assistant_phase == ""
    assert frame._current_chat_state["codex_thread_id"] == ""
    assert frame._current_chat_state["codex_turn_id"] == ""
    assert frame._current_chat_state["codex_turn_active"] is False
    assert frame._current_chat_state["codex_pending_prompt"] == ""
    assert frame._current_chat_state["codex_pending_request"] is None
    assert frame._current_chat_state["codex_request_queue"] == []
    assert frame._current_chat_state["codex_thread_flags"] == []
    assert frame._current_chat_state["codex_latest_assistant_text"] == ""
    assert frame._current_chat_state["codex_latest_assistant_phase"] == ""


def test_codex_submit_does_not_show_current_model_row(frame, monkeypatch):
    monkeypatch.setattr(frame, "_start_codex_worker_for_turn", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main, "read_codex_cli_model_label", lambda _cwd=None: "gpt-5.5 medium")

    ok, message = frame._submit_question("q", source="local", model=main.DEFAULT_CODEX_MODEL)

    assert ok is True
    assert message == ""
    assert all(meta[0] != "current_model" for meta in frame.answer_meta)
    assert all("当前模型：" not in frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount()))


def test_codex_token_count_does_not_show_current_model_row(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_turn_idx = 0
    frame.active_codex_turn_active = True
    frame.view_mode = "active"
    frame.active_session_turns = [
        {
            "question": "q",
            "answer_md": main.REQUESTING_TEXT,
            "model": "codex/main",
            "created_at": 1.0,
            "codex_turn_id": "turn-1",
            "request_status": "pending",
        }
    ]
    frame._current_chat_state = {"id": "chat-current", "turns": frame.active_session_turns}
    monkeypatch.setattr(main, "read_codex_cli_model_label", lambda _cwd=None: "gpt-5.5 medium")
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    frame._render_answer_list()
    assert all(meta[0] != "current_model" for meta in frame.answer_meta)

    frame._on_codex_event_for_chat(
        "chat-current",
        main.CodexEvent(
            type="token_count",
            turn_id="turn-1",
            usage={
                "used_tokens": 44176,
                "context_window": 258400,
                "source": "codex",
                "exact": True,
                "fresh": True,
                "model": "gpt-5-codex",
                "updated_at": 1.0,
            },
        ),
    )

    assert all(meta[0] != "current_model" for meta in frame.answer_meta)
    assert all("当前模型：" not in frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount()))


def test_codex_token_count_event_stores_pending_usage_until_turn_completed(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_turn_idx = 0
    frame.active_codex_turn_active = True
    frame.active_session_turns = [
        {
            "question": "q",
            "answer_md": main.REQUESTING_TEXT,
            "model": "codex/main",
            "created_at": 1.0,
            "codex_turn_id": "turn-1",
            "codex_thread_id": "thread-1",
        }
    ]
    frame._current_chat_state = {"id": "chat-current", "turns": frame.active_session_turns, "codex_thread_id": "thread-1"}
    usage = {
        "used_tokens": 44176,
        "context_window": 258400,
        "source": "codex",
        "exact": False,
        "fresh": True,
        "model": "gpt-5-codex",
        "updated_at": 1.0,
    }
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)

    frame._on_codex_event_for_chat(
        "chat-current",
        main.CodexEvent(type="token_count", thread_id="thread-1", turn_id="turn-1", data={"turn_idx": 0, "context_generation": 0}, usage=usage),
    )

    assert frame._pending_context_usage_by_turn[("chat-current", 0)] == usage
    assert "context_usage" not in frame._current_chat_state

    frame._on_codex_event_for_chat(
        "chat-current",
        main.CodexEvent(type="turn_completed", thread_id="thread-1", turn_id="turn-1", status="completed"),
    )

    assert frame._current_chat_state["context_usage"] == usage
    assert frame._pending_context_usage_by_turn == {}


def test_active_pending_codex_token_count_event_does_not_refresh_context_usage_row(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_turn_idx = 0
    frame.active_codex_turn_active = True
    frame.view_mode = "active"
    frame.active_session_turns = [
        {
            "question": "q",
            "answer_md": main.REQUESTING_TEXT,
            "model": "codex/main",
            "created_at": 1.0,
            "codex_turn_id": "turn-1",
            "codex_thread_id": "thread-1",
            "request_status": "pending",
        }
    ]
    frame._current_chat_state = {"id": "chat-current", "turns": frame.active_session_turns, "codex_thread_id": "thread-1"}
    usage = {
        "used_tokens": 44176,
        "context_window": 258400,
        "source": "codex",
        "exact": False,
        "fresh": True,
        "model": "gpt-5-codex",
        "updated_at": 1.0,
    }
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    frame._render_answer_list()
    frame.answer_list.SetSelection(2)
    assert frame.answer_list.GetString(0) == "暂无"
    assert frame.answer_meta[1][0] == "time"

    frame._on_codex_event_for_chat(
        "chat-current",
        main.CodexEvent(type="token_count", thread_id="thread-1", turn_id="turn-1", data={"turn_idx": 0, "context_generation": 0}, usage=usage),
    )

    assert frame._pending_context_usage_by_turn[("chat-current", 0)] == usage
    assert frame.answer_list.GetString(0) == "暂无"
    assert frame.answer_list.GetSelection() == 2
    assert frame.answer_meta[2][0] == "user"


def test_repeated_codex_token_count_event_does_not_rebuild_answer_list_or_save_state(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_turn_idx = 0
    frame.active_codex_turn_active = True
    frame.view_mode = "active"
    frame.active_session_turns = [
        {
            "question": "q",
            "answer_md": main.REQUESTING_TEXT,
            "model": "codex/main",
            "created_at": 1.0,
            "codex_turn_id": "turn-1",
            "codex_thread_id": "thread-1",
            "request_status": "pending",
        }
    ]
    frame._current_chat_state = {"id": "chat-current", "turns": frame.active_session_turns, "codex_thread_id": "thread-1"}
    usage = {
        "used_tokens": 44176,
        "context_window": 258400,
        "source": "codex",
        "exact": False,
        "fresh": True,
        "model": "gpt-5-codex",
        "updated_at": 1.0,
    }
    saves = {"count": 0}
    refreshes = {"count": 0}
    repaints = {"count": 0}
    monkeypatch.setattr(frame, "_save_state", lambda: saves.__setitem__("count", saves["count"] + 1))
    monkeypatch.setattr(
        frame,
        "_refresh_answer_list_preserving_selection",
        lambda *args, **kwargs: refreshes.__setitem__("count", refreshes["count"] + 1),
    )
    monkeypatch.setattr(frame, "_request_listbox_repaint", lambda *controls: repaints.__setitem__("count", repaints["count"] + 1))

    frame._render_answer_list()
    assert frame.answer_list.GetString(0) == "暂无"
    repaints["count"] = 0

    frame._on_codex_event_for_chat(
        "chat-current",
        main.CodexEvent(type="token_count", thread_id="thread-1", turn_id="turn-1", data={"turn_idx": 0, "context_generation": 0}, usage=usage),
    )
    frame._on_codex_event_for_chat(
        "chat-current",
        main.CodexEvent(type="token_count", thread_id="thread-1", turn_id="turn-1", data={"turn_idx": 0, "context_generation": 0}, usage=dict(usage)),
    )

    assert frame._pending_context_usage_by_turn[("chat-current", 0)] == usage
    assert frame.answer_list.GetString(0) == "暂无"
    assert refreshes["count"] == 0
    assert repaints["count"] == 0
    assert saves["count"] == 0


def test_codex_token_count_same_visible_label_does_not_rebuild_answer_list(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_turn_idx = 0
    frame.active_codex_turn_active = True
    frame.view_mode = "active"
    frame.active_session_turns = [
        {
            "question": "q",
            "answer_md": main.REQUESTING_TEXT,
            "model": "codex/main",
            "created_at": 1.0,
            "codex_turn_id": "turn-1",
            "codex_thread_id": "thread-1",
            "request_status": "pending",
        }
    ]
    frame._current_chat_state = {"id": "chat-current", "turns": frame.active_session_turns, "codex_thread_id": "thread-1"}
    first_usage = {
        "used_tokens": 44176,
        "context_window": 258400,
        "source": "codex",
        "exact": False,
        "fresh": True,
        "model": "gpt-5-codex",
        "updated_at": 1.0,
    }
    second_usage = dict(first_usage, used_tokens=44177, updated_at=2.0)
    refreshes = {"count": 0}
    repaints = {"count": 0}
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(
        frame,
        "_refresh_answer_list_preserving_selection",
        lambda *args, **kwargs: refreshes.__setitem__("count", refreshes["count"] + 1),
    )
    monkeypatch.setattr(frame, "_request_listbox_repaint", lambda *controls: repaints.__setitem__("count", repaints["count"] + 1))

    frame._render_answer_list()
    frame._on_codex_event_for_chat("chat-current", main.CodexEvent(type="token_count", thread_id="thread-1", turn_id="turn-1", data={"turn_idx": 0, "context_generation": 0}, usage=first_usage))
    repaints["count"] = 0

    frame._on_codex_event_for_chat("chat-current", main.CodexEvent(type="token_count", thread_id="thread-1", turn_id="turn-1", data={"turn_idx": 0, "context_generation": 0}, usage=second_usage))

    assert frame.answer_list.GetString(0) == "暂无"
    assert refreshes["count"] == 0
    assert repaints["count"] == 0


def test_active_pending_codex_token_count_without_native_identity_is_ignored(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_turn_idx = 1
    frame.active_codex_turn_active = True
    frame.view_mode = "active"
    frame.active_session_turns = [
        {
            "question": "q1",
            "answer_md": "a1",
            "model": "codex/main",
            "created_at": 1.0,
            "request_status": "done",
        },
        {
            "question": "q2",
            "answer_md": main.REQUESTING_TEXT,
            "model": "codex/main",
            "created_at": 2.0,
            "request_status": "pending",
        },
    ]
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
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    frame._render_answer_list()
    assert frame.answer_list.GetString(0) == "暂无"

    frame._on_codex_event_for_chat(
        "chat-current",
        main.CodexEvent(type="token_count", usage=usage),
    )

    assert ("chat-current", 1) not in frame._pending_context_usage_by_turn
    assert frame.answer_list.GetString(0) == "暂无"


def test_active_pending_codex_token_count_event_preserves_selected_context_usage_row(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_turn_idx = 0
    frame.active_codex_turn_active = True
    frame.view_mode = "active"
    frame.active_session_turns = [
        {
            "question": "q",
            "answer_md": main.REQUESTING_TEXT,
            "model": "codex/main",
            "created_at": 1.0,
            "codex_turn_id": "turn-1",
            "codex_thread_id": "thread-1",
            "request_status": "pending",
        }
    ]
    frame._current_chat_state = {"id": "chat-current", "turns": frame.active_session_turns, "codex_thread_id": "thread-1"}
    usage = {
        "used_tokens": 44176,
        "context_window": 258400,
        "source": "codex",
        "exact": False,
        "fresh": True,
        "model": "gpt-5-codex",
        "updated_at": 1.0,
    }
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    frame._render_answer_list()
    frame.answer_list.SetSelection(0)
    assert frame.answer_meta[0] == ("context_usage", -1, "暂无", "")

    frame._on_codex_event_for_chat(
        "chat-current",
        main.CodexEvent(type="token_count", thread_id="thread-1", turn_id="turn-1", data={"turn_idx": 0, "context_generation": 0}, usage=usage),
    )

    assert frame.answer_list.GetString(0) == "暂无"
    assert frame.answer_list.GetSelection() == 0
    assert frame.answer_meta[0][0] == "context_usage"


def test_active_codex_completion_preserves_selected_context_usage_row_after_pending_token_count(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_turn_idx = 0
    frame.active_codex_turn_active = True
    frame.view_mode = "active"
    frame.active_session_turns = [
        {
            "question": "q",
            "answer_md": main.REQUESTING_TEXT,
            "model": "codex/main",
            "created_at": 1.0,
            "codex_turn_id": "turn-1",
            "codex_thread_id": "thread-1",
            "request_status": "pending",
        }
    ]
    frame._current_chat_state = {"id": "chat-current", "turns": frame.active_session_turns, "codex_thread_id": "thread-1"}
    usage = {
        "used_tokens": 44176,
        "context_window": 258400,
        "source": "codex",
        "exact": False,
        "fresh": True,
        "model": "gpt-5-codex",
        "updated_at": 1.0,
    }
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)
    monkeypatch.setattr(frame, "_push_remote_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    frame._render_answer_list()
    frame.answer_list.SetSelection(0)

    frame._on_codex_event_for_chat(
        "chat-current",
        main.CodexEvent(type="token_count", thread_id="thread-1", turn_id="turn-1", data={"turn_idx": 0, "context_generation": 0}, usage=usage),
    )

    assert frame.answer_list.GetString(0) == "暂无"
    assert frame.answer_list.GetSelection() == 0

    frame._on_codex_event_for_chat(
        "chat-current",
        main.CodexEvent(type="turn_completed", thread_id="thread-1", turn_id="turn-1", text="done", status="completed"),
    )

    assert frame._current_chat_state["context_usage"] == usage
    assert frame._pending_context_usage_by_turn == {}
    context_row = next(index for index, meta in enumerate(frame.answer_meta) if meta[0] == "context_usage")
    assert frame.answer_list.GetString(context_row) == "44k / 258k"
    assert frame.answer_list.GetSelection() == 0
    assert frame.answer_meta[0][0] == "context_usage"


def test_late_codex_token_count_event_updates_context_usage_row(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {
            "question": "q",
            "answer_md": "done",
            "model": "codex/main",
            "created_at": 1.0,
            "codex_turn_id": "turn-1",
            "codex_thread_id": "thread-1",
            "request_status": "done",
        }
    ]
    frame._current_chat_state = {"id": "chat-current", "turns": frame.active_session_turns, "codex_thread_id": "thread-1"}
    frame.view_mode = "active"
    usage = {
        "used_tokens": 44176,
        "context_window": 0,
        "source": "codex",
        "exact": True,
        "fresh": True,
        "model": "gpt-5-codex",
        "updated_at": 1.0,
    }

    frame._on_codex_event_for_chat(
        "chat-current",
        main.CodexEvent(type="turn_completed", thread_id="thread-1", turn_id="turn-1", status="completed"),
    )
    assert "context_usage" not in frame._current_chat_state

    frame._on_codex_event_for_chat(
        "chat-current",
        main.CodexEvent(type="token_count", thread_id="thread-1", turn_id="turn-1", data={"turn_idx": 0, "context_generation": 0}, usage=usage),
    )

    assert frame._current_chat_state["context_usage"] == usage
    assert frame._pending_context_usage_by_turn == {}
    assert frame.answer_meta[0][0] == "time"
    assert all(meta[0] != "context_usage" for meta in frame.answer_meta)


def test_late_codex_token_count_event_updates_state_without_refreshing_visible_context_usage_row(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {
            "question": "q",
            "answer_md": "done",
            "model": "codex/main",
            "created_at": 1.0,
            "codex_turn_id": "turn-1",
            "codex_thread_id": "thread-1",
            "request_status": "done",
        }
    ]
    frame._current_chat_state = {"id": "chat-current", "turns": frame.active_session_turns, "codex_thread_id": "thread-1"}
    frame.view_mode = "active"
    usage = {
        "used_tokens": 44176,
        "context_window": 258400,
        "source": "codex",
        "exact": False,
        "fresh": True,
        "model": "gpt-5-codex",
        "updated_at": 1.0,
    }
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    frame._render_answer_list()
    rows_before = list(frame.answer_list.GetStrings())
    frame.answer_list.SetSelection(0)
    assert frame.answer_meta[0][0] == "time"

    frame._on_codex_event_for_chat(
        "chat-current",
        main.CodexEvent(type="token_count", thread_id="thread-1", turn_id="turn-1", data={"turn_idx": 0, "context_generation": 0}, usage=usage),
    )

    assert frame._current_chat_state["context_usage"] == usage
    assert list(frame.answer_list.GetStrings()) == rows_before
    assert frame.answer_list.GetSelection() == 0
    assert frame.answer_meta[0][0] == "time"


def test_late_codex_token_count_event_updates_inactive_chat_for_later_selection(frame, monkeypatch):
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.active_session_turns = [
        {"question": "active", "answer_md": "active", "model": "openai/gpt-5.2", "created_at": 1.0}
    ]
    frame._current_chat_state = {"id": "chat-active", "turns": frame.active_session_turns}
    frame.archived_chats = [
        {
            "id": "chat-inactive",
            "title": "后台聊天",
            "codex_thread_id": "thread-1",
            "turns": [
                {
                    "question": "q",
                    "answer_md": "done",
                    "model": "codex/main",
                    "created_at": 1.0,
                        "codex_turn_id": "turn-1",
                        "codex_thread_id": "thread-1",
                        "request_status": "done",
                }
            ],
        }
    ]
    usage = {
        "used_tokens": 44176,
        "context_window": 258400,
        "source": "codex",
        "exact": False,
        "fresh": True,
        "model": "gpt-5-codex",
        "updated_at": 1.0,
    }
    monkeypatch.setattr(frame, "_call_later_if_alive", lambda *_args, **_kwargs: None)

    frame._on_codex_event_for_chat(
        "chat-inactive",
        main.CodexEvent(type="token_count", thread_id="thread-1", turn_id="turn-1", data={"turn_idx": 0, "context_generation": 0}, usage=usage),
    )

    archived = frame._find_archived_chat("chat-inactive")
    assert archived["context_usage"] == usage
    assert frame._pending_context_usage_by_turn == {}

    frame.view_mode = "history"
    frame.view_history_id = "chat-inactive"
    frame._render_answer_list()
    assert frame.answer_list.GetString(0) == "44k / 258k"


def test_codex_without_pending_usage_does_not_estimate(frame):
    frame.active_session_turns = [{"question": "你好", "answer_md": "", "model": "codex/main", "created_at": 1.0}]
    frame._current_chat_state = {"id": "chat-current", "turns": frame.active_session_turns}
    frame._pending_context_usage_by_turn = {}

    frame._on_done(0, "codex answer", "", "codex/main", "", "chat-current")

    assert "context_usage" not in frame._current_chat_state


def test_append_codex_local_turn_adds_recovery_metadata(frame, monkeypatch):
    monkeypatch.setattr(main.time, "time", lambda: 4567.5)
    frame.active_codex_thread_id = "thread-1"
    frame.active_codex_turn_id = "turn-1"

    idx = frame._append_codex_local_turn("Codex 继续")

    assert idx == len(frame.active_session_turns) - 1
    turn = frame.active_session_turns[-1]
    assert_request_metadata(
        turn,
        status="pending",
        model=main.DEFAULT_CODEX_MODEL,
        question="Codex 继续",
        recoverable=True,
        mode="resume",
        resume_token={"thread_id": "thread-1", "turn_id": "turn-1"},
        attempt_count=1,
    )
    assert turn["request_started_at"] == 4567.5
    assert turn["request_last_attempt_at"] == 4567.5


def test_merge_codex_final_answer_appends_turn_with_recovery_metadata(frame, monkeypatch):
    monkeypatch.setattr(main.time, "time", lambda: 7890.5)
    frame.active_codex_thread_id = "thread-2"
    frame.active_codex_turn_id = "turn-2"
    frame.active_session_turns = []

    changed = frame._merge_codex_final_answer("最终答案")

    assert changed is True
    turn = frame.active_session_turns[-1]
    assert_request_metadata(
        turn,
        status="done",
        model=main.DEFAULT_CODEX_MODEL,
        question="",
        recoverable=False,
        mode="resume",
        resume_token={"thread_id": "thread-2", "turn_id": "turn-2"},
        attempt_count=0,
    )
    assert turn["answer_md"] == "最终答案"


def test_codex_user_input_dialog_returns_option_value_when_present(frame):
    dlg = main.CodexUserInputDialog(
        frame,
        [
            {
                "id": "choice",
                "header": "选择",
                "question": "请选择一个选项",
                "options": [
                    {"label": "Alpha", "value": "alpha-code"},
                    {"label": "Beta"},
                ],
            }
        ],
    )
    try:
        radio = dlg._controls[0]["radio"]
        radio.SetSelection(0)
        assert dlg.get_answers() == {"choice": ["alpha-code"]}

        radio.SetSelection(1)
        assert dlg.get_answers() == {"choice": ["Beta"]}
    finally:
        dlg.Destroy()


def test_handle_codex_request_dialog_does_not_submit_when_user_cancels(frame, monkeypatch):
    calls = []

    class _FakeClient:
        def respond_tool_request_user_input(self, request_id, answers):
            calls.append((request_id, answers))

    class _FakeDialog:
        def __init__(self, parent, questions):
            self.parent = parent
            self.questions = questions

        def ShowModal(self):
            return wx.ID_CANCEL

        def Destroy(self):
            calls.append("destroy")

        def get_answers(self):
            raise AssertionError("should not be called on cancel")

    monkeypatch.setattr(frame, "_ensure_codex_client", lambda: _FakeClient())
    monkeypatch.setattr(main, "CodexUserInputDialog", _FakeDialog)

    frame._handle_codex_request_dialog(
        {
            "method": "item/tool/requestUserInput",
            "request_id": "req-1",
            "params": {"questions": []},
        }
    )

    assert calls == ["destroy"]


def test_codex_server_request_reply_uses_event_model_after_selected_model_changes(frame, monkeypatch):
    calls = []

    class _FakeWorkerClient:
        def __init__(self, chat_id, model):
            self.chat_id = chat_id
            self.model = model

        def start(self):
            calls.append(("start", self.chat_id, self.model))

        def reply_user_input(self, chat_id, request_id, answers):
            calls.append(("reply", self.chat_id, self.model, chat_id, request_id, answers))

    class _FakeDialog:
        def __init__(self, parent, questions):
            self.parent = parent
            self.questions = questions

        def ShowModal(self):
            return wx.ID_OK

        def Destroy(self):
            calls.append(("destroy",))

        def get_answers(self):
            return {"reply": ["ok"]}

    frame.active_chat_id = "chat-c"
    frame.current_chat_id = "chat-c"
    frame.selected_model = "codex/new-selection"
    frame.active_codex_turn_active = True
    frame.active_session_turns = [
        {"question": "current", "answer_md": main.REQUESTING_TEXT, "model": "codex/original", "request_status": "pending"}
    ]
    frame._current_chat_state = {"id": "chat-c", "turns": frame.active_session_turns, "codex_turn_active": True}

    monkeypatch.setattr(frame, "_push_remote_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: None)
    monkeypatch.setattr(frame, "_get_or_create_codex_client", lambda chat_id, model="": _FakeWorkerClient(chat_id, model))
    monkeypatch.setattr(main, "CodexUserInputDialog", _FakeDialog)

    frame._on_codex_event_for_chat(
        "chat-c",
        main.CodexEvent(
            type="server_request",
            request_id="req-1",
            method="item/tool/requestUserInput",
            params={"questions": [{"id": "reply"}]},
            data={"model": "codex/original"},
        ),
    )

    assert ("start", "chat-c", "codex/original") in calls
    assert ("reply", "chat-c", "codex/original", "chat-c", "req-1", {"reply": ["ok"]}) in calls


def test_background_codex_request_user_input_records_pending_request_on_target_chat(frame, monkeypatch):
    frame.active_chat_id = "chat-a"
    frame.current_chat_id = "chat-a"
    frame.active_codex_pending_request = None
    frame.active_session_turns = [
        {"question": "active", "answer_md": "active answer", "model": main.DEFAULT_CODEX_MODEL}
    ]
    frame._current_chat_state = {"id": "chat-a", "turns": frame.active_session_turns}
    archived = {
        "id": "chat-b",
        "title": "Archived",
        "turns": [
            {
                "question": "background",
                "answer_md": main.REQUESTING_TEXT,
                "model": "codex/original",
                "request_status": "pending",
                "codex_turn_id": "turn-b",
            }
        ],
        "codex_turn_id": "turn-b",
        "codex_turn_active": True,
        "execution_steps": [],
    }
    frame.archived_chats = [archived]

    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)
    monkeypatch.setattr(frame, "_refresh_visible_history_chat", lambda *_args, **_kwargs: None)

    frame._on_codex_event_for_chat(
        "chat-b",
        main.CodexEvent(
            type="server_request",
            request_id="ask-b",
            method="item/tool/requestUserInput",
            params={"questions": [{"id": "reply"}]},
            turn_id="turn-b",
            data={"model": "codex/original"},
        ),
    )

    assert archived["codex_pending_request"] == {
        "chat_id": "chat-b",
        "model": "codex/original",
        "request_id": "ask-b",
        "method": "item/tool/requestUserInput",
        "params": {"questions": [{"id": "reply"}]},
    }
    assert archived["request_kind"] == "user_input"
    assert frame.active_codex_pending_request is None


def test_ui_source_no_longer_constructs_codex_app_server_client_directly():
    source = desktop_source()
    assert "CodexAppServerClient(" not in source


@pytest.mark.parametrize("event_type", ["agent_message_delta", "stderr", "plan_updated", "diff_updated"])
def test_background_codex_feedback_events_route_through_call_after(frame, monkeypatch, event_type):
    frame.active_chat_id = "chat-current"
    frame.active_codex_thread_id = "thread-current"
    frame.active_codex_turn_id = "turn-current"
    frame.archived_chats = [
        {
            "id": "chat-background",
            "title": "后台聊天",
            "title_manual": False,
            "pinned": False,
            "model": main.DEFAULT_CODEX_MODEL,
            "turns": [{"question": "后台任务", "answer_md": "", "model": main.DEFAULT_CODEX_MODEL, "created_at": 1.0}],
            "created_at": 1.0,
            "updated_at": 1.0,
            "codex_thread_id": "thread-background",
            "codex_turn_id": "turn-background",
            "codex_turn_active": True,
            "codex_pending_prompt": "",
            "codex_pending_request": None,
            "codex_request_queue": [],
            "codex_thread_flags": [],
            "codex_latest_assistant_text": "",
            "codex_latest_assistant_phase": "",
        }
    ]

    call_after_calls = []
    monkeypatch.setattr(main.wx, "GetApp", lambda: object())
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *args, **kwargs: call_after_calls.append((fn, args, kwargs)))

    frame._dispatch_codex_event_to_ui(
        "chat-background",
        main.CodexEvent(
            type=event_type,
            thread_id="thread-background",
            turn_id="turn-background",
            item_id="msg-1" if event_type == "agent_message_delta" else "",
            text="stream chunk",
        ),
    )

    assert len(call_after_calls) == 1
    assert call_after_calls[0][0].__name__ == "_drain_codex_ui_events"
    assert len(frame._pending_codex_ui_events) == 1


def test_background_codex_feedback_events_batch_state_flush(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.archived_chats = [
        {
            "id": "chat-background",
            "title": "后台聊天",
            "title_manual": False,
            "pinned": False,
            "model": main.DEFAULT_CODEX_MODEL,
            "turns": [{"question": "后台任务", "answer_md": "", "model": main.DEFAULT_CODEX_MODEL, "created_at": 1.0}],
            "created_at": 1.0,
            "updated_at": 1.0,
            "codex_thread_id": "thread-background",
            "codex_turn_id": "turn-background",
            "codex_turn_active": True,
            "codex_pending_prompt": "",
            "codex_pending_request": None,
            "codex_request_queue": [],
            "codex_thread_flags": [],
            "codex_latest_assistant_text": "",
            "codex_latest_assistant_phase": "",
        }
    ]

    scheduled = {"count": 0, "delay": None, "callback": None}
    saves = {"count": 0}
    monkeypatch.setattr(frame, "_save_state", lambda: saves.__setitem__("count", saves["count"] + 1))
    monkeypatch.setattr(
        frame,
        "_call_later_if_alive",
        lambda delay, fn, *args, **kwargs: scheduled.update({"count": scheduled["count"] + 1, "delay": delay, "callback": fn}) or object(),
    )

    frame._on_codex_event_for_chat(
        "chat-background",
        main.CodexEvent(type="stderr", thread_id="thread-background", turn_id="turn-background", text="a"),
    )
    frame._on_codex_event_for_chat(
        "chat-background",
        main.CodexEvent(type="plan_updated", thread_id="thread-background", turn_id="turn-background", text="b"),
    )

    assert scheduled["count"] == 1
    assert scheduled["delay"] == main.CODEX_BACKGROUND_FLUSH_DELAY_MS
    assert saves["count"] == 0
    assert frame._find_archived_chat("chat-background")["updated_at"] == 1.0
    assert frame._history_list_dirty is False

    scheduled["callback"]()

    assert saves["count"] == 1


def test_current_codex_final_answer_with_unknown_turn_id_does_not_fallback_to_latest_turn(frame, monkeypatch):
    frame.active_chat_id = "chat-c"
    frame.current_chat_id = "chat-c"
    frame.active_codex_thread_id = "thread-c"
    frame.active_codex_turn_id = "turn-c"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {
            "question": "当前问题",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": 2.0,
            "request_status": "pending",
            "codex_thread_id": "thread-c",
            "codex_turn_id": "turn-c",
        }
    ]
    frame._current_chat_state = {
        "id": "chat-c",
        "title": "当前聊天",
        "turns": frame.active_session_turns,
        "codex_thread_id": "thread-c",
        "codex_turn_id": "turn-c",
        "execution_steps": [],
    }
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_push_remote_final_answer", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_call_later_if_alive", lambda *args, **kwargs: None)

    frame._on_codex_event_for_chat(
        "chat-c",
        main.CodexEvent(
            type="item_completed",
            phase="final_answer",
            thread_id="thread-c",
            turn_id="turn-old",
            text="旧 turn 的迟到回答",
        ),
    )

    assert frame.active_session_turns[0]["answer_md"] == main.REQUESTING_TEXT


def test_current_codex_final_answer_without_turn_id_does_not_update_known_turn(frame, monkeypatch):
    frame.active_chat_id = "chat-c"
    frame.current_chat_id = "chat-c"
    frame.active_codex_thread_id = "thread-c"
    frame.active_codex_turn_id = "turn-c"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {
            "question": "当前问题",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": 2.0,
            "request_status": "pending",
            "codex_thread_id": "thread-c",
            "codex_turn_id": "turn-c",
        }
    ]
    frame._current_chat_state = {
        "id": "chat-c",
        "title": "当前聊天",
        "turns": frame.active_session_turns,
        "codex_thread_id": "thread-c",
        "codex_turn_id": "turn-c",
        "execution_steps": [],
    }
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_push_remote_final_answer", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_call_later_if_alive", lambda *args, **kwargs: None)

    frame._on_codex_event_for_chat(
        "chat-c",
        main.CodexEvent(
            type="item_completed",
            phase="final_answer",
            thread_id="thread-c",
            text="缺 turn_id 的回答",
        ),
    )

    assert frame.active_session_turns[0]["answer_md"] == main.REQUESTING_TEXT


def test_background_codex_final_answer_with_unknown_turn_id_does_not_fallback_to_latest_turn(frame, monkeypatch):
    frame.active_chat_id = "chat-c"
    frame.current_chat_id = "chat-c"
    frame.active_session_turns = [
        {
            "question": "当前问题",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": 2.0,
            "request_status": "pending",
            "codex_thread_id": "thread-c",
            "codex_turn_id": "turn-c",
        }
    ]
    frame._current_chat_state = {"id": "chat-c", "turns": frame.active_session_turns}
    frame.archived_chats = [
        {
            "id": "chat-a",
            "title": "后台聊天",
            "turns": [
                {
                    "question": "后台问题",
                    "answer_md": main.REQUESTING_TEXT,
                    "model": main.DEFAULT_CODEX_MODEL,
                    "created_at": 1.0,
                    "request_status": "pending",
                    "codex_thread_id": "thread-a",
                    "codex_turn_id": "turn-a",
                }
            ],
            "codex_thread_id": "thread-a",
            "codex_turn_id": "turn-a",
            "created_at": 1.0,
            "updated_at": 1.0,
            "execution_steps": [],
        }
    ]
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_call_later_if_alive", lambda *args, **kwargs: None)

    frame._on_codex_event_for_chat(
        "chat-a",
        main.CodexEvent(
            type="item_completed",
            phase="final_answer",
            thread_id="thread-a",
            turn_id="turn-old",
            text="后台旧 turn 的迟到回答",
        ),
    )

    archived = frame._find_archived_chat("chat-a")
    assert archived["turns"][0]["answer_md"] == main.REQUESTING_TEXT
    assert frame.active_session_turns[0]["answer_md"] == main.REQUESTING_TEXT


def test_current_chat_codex_delta_still_queues_ui_work(frame, monkeypatch):
    call_after_calls = []
    monkeypatch.setattr(main.wx, "GetApp", lambda: object())
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *args, **kwargs: call_after_calls.append((fn, args, kwargs)))

    frame._dispatch_codex_event_to_ui(
        frame.current_chat_id,
        main.CodexEvent(type="agent_message_delta", thread_id=frame.active_codex_thread_id, turn_id=frame.active_codex_turn_id, text="delta"),
    )

    assert len(call_after_calls) == 1


def test_current_chat_codex_delta_updates_row_without_full_rerender(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {"question": "正在改代码", "answer_md": "", "model": main.DEFAULT_CODEX_MODEL, "created_at": 1.0}
    ]
    frame.view_mode = "active"
    frame._render_answer_list()

    seen = {"render": 0, "save": 0}
    original_update = frame._update_active_answer_row
    monkeypatch.setattr(frame, "_render_answer_list", lambda: seen.__setitem__("render", seen["render"] + 1))
    monkeypatch.setattr(frame, "_save_state", lambda: seen.__setitem__("save", seen["save"] + 1))

    frame._on_codex_event_for_chat(
        "chat-current",
        main.CodexEvent(
            type="agent_message_delta",
            thread_id="thread-current",
            turn_id="turn-current",
            text="stream chunk",
        ),
    )

    original_update(0)

    assert seen["render"] == 0
    assert seen["save"] == 0
    assert frame._codex_background_flush_scheduled is True
    assert frame.active_session_turns[0]["answer_md"] == main.REQUESTING_TEXT
    answer_row = next(i for i, meta in enumerate(frame.answer_meta) if meta[0] == "answer")
    assert frame.answer_list.GetString(answer_row) == main.REQUESTING_TEXT


def test_current_chat_codex_delta_with_hidden_placeholder_does_not_rerender_question_rows(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {
            "question": "Codex 正在处理的问题",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": 1.0,
            "request_status": "pending",
        }
    ]
    frame.view_mode = "active"
    frame._render_answer_list()
    before_rows = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert frame.answer_meta[0][0] == "context_usage"
    assert before_rows[-1] == "Codex 正在处理的问题"

    seen = {"render": 0, "save": 0}
    monkeypatch.setattr(frame, "_render_answer_list", lambda: seen.__setitem__("render", seen["render"] + 1))
    monkeypatch.setattr(frame, "_save_state", lambda: seen.__setitem__("save", seen["save"] + 1))

    frame._on_codex_event_for_chat(
        "chat-current",
        main.CodexEvent(
            type="agent_message_delta",
            thread_id="thread-current",
            turn_id="turn-current",
            text="后台增量",
        ),
    )

    after_rows = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert seen["render"] == 0
    assert seen["save"] == 0
    assert frame._codex_background_flush_scheduled is True
    assert frame.active_session_turns[0]["answer_md"] == main.REQUESTING_TEXT
    assert after_rows == before_rows


def test_codex_turn_completed_does_not_refresh_when_final_answer_already_appended(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_turn_idx = 1
    frame.active_codex_thread_id = "thread-current"
    frame.active_codex_turn_id = "turn-current"
    frame.active_session_turns = [
        {"question": "上一条问题", "answer_md": "上一条回复", "model": main.DEFAULT_CODEX_MODEL, "created_at": 1.0},
        {
            "question": "当前 Codex 问题",
            "answer_md": "Codex 已追加回答",
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": 2.0,
            "codex_thread_id": "thread-current",
            "codex_turn_id": "turn-current",
        },
    ]
    frame._current_chat_state.update({
        "id": "chat-current",
        "turns": frame.active_session_turns,
        "codex_thread_id": "thread-current",
        "codex_turn_id": "turn-current",
    })
    frame.view_mode = "active"
    frame._render_answer_list()
    old_answer_row = next(idx for idx, meta in enumerate(frame.answer_meta) if meta[0] == "answer" and meta[1] == 0)
    frame.answer_list.SetSelection(old_answer_row)

    monkeypatch.setattr(frame, "_refresh_answer_list_preserving_selection", lambda *args, **kwargs: pytest.fail("Codex turn_completed should not refresh when answer row already exists"))
    monkeypatch.setattr(frame, "_push_remote_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_play_finish_sound", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame.answer_list, "SetSelection", lambda *args, **kwargs: pytest.fail("Codex turn_completed should not reselect old answer"))
    monkeypatch.setattr(frame.answer_list, "Refresh", lambda *args, **kwargs: pytest.fail("Codex turn_completed should not refresh answer list"))

    frame._on_codex_event_for_chat(
        "chat-current",
        main.CodexEvent(
            type="turn_completed",
            thread_id="thread-current",
            turn_id="turn-current",
            text="",
        ),
    )

    assert frame.active_session_turns[1]["request_status"] == "done"


def test_background_agent_message_delta_buffers_and_flushes_for_target_chat(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state = {
        "id": "chat-current",
        "title": "当前聊天",
        "turns": [],
        "detail_panel_mode": "answers",
        "execution_steps": [],
    }
    frame.archived_chats = [
        {
            "id": "chat-background",
            "title": "后台聊天",
            "turns": [],
            "detail_panel_mode": "execution",
            "execution_steps": [],
        }
    ]
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    frame._on_codex_event_for_chat(
        "chat-background",
        main.CodexEvent(
            type="agent_message_delta",
            thread_id="thread-background",
            turn_id="turn-background",
            item_id="msg-1",
            text="后台增量",
        ),
    )

    assert any(key[:3] == ("chat-background", "turn-background", "msg-1") for key in frame._execution_delta_buffer)
    assert frame.archived_chats[0]["execution_steps"] == []

    flushed = frame._flush_execution_delta("chat-background", "turn-background", "msg-1")

    assert flushed is True
    assert frame.archived_chats[0]["execution_steps"][0]["event_type"] == "agent_message_delta"
    assert frame.archived_chats[0]["execution_steps"][0]["detail_text"] == "后台增量"
    assert frame._current_chat_state["execution_steps"] == []


def test_background_chat_delta_flushes_before_next_non_delta_event(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state = {
        "id": "chat-current",
        "title": "当前聊天",
        "turns": [],
        "detail_panel_mode": "answers",
        "execution_steps": [],
    }
    frame.archived_chats = [
        {
            "id": "chat-background",
            "title": "后台聊天",
            "turns": [],
            "detail_panel_mode": "execution",
            "execution_steps": [],
        }
    ]
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    frame._on_codex_event_for_chat(
        "chat-background",
        main.CodexEvent(
            type="agent_message_delta",
            thread_id="thread-background",
            turn_id="turn-background",
            item_id="msg-1",
            text="先缓冲",
        ),
    )
    frame._on_codex_event_for_chat(
        "chat-background",
        main.CodexEvent(
            type="plan_updated",
            thread_id="thread-background",
            turn_id="turn-background",
            text="后落地",
        ),
    )

    steps = frame.archived_chats[0]["execution_steps"]
    assert [step["event_type"] for step in steps] == ["agent_message_delta", "plan_updated"]
    assert steps[0]["detail_text"] == "先缓冲"
    assert steps[1]["detail_text"] == "后落地"
    assert frame._execution_delta_buffer == {}


def test_final_answer_replaces_temporary_subagent_result(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_codex_thread_id = "thread-current"
    frame.active_codex_turn_id = "turn-current"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {
            "question": "q",
            "answer_md": "Ada（worker）\nDONE\n\n子代理结果",
            "answer_origin": "codex-subagent-result",
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": 1.0,
        }
    ]
    frame._current_chat_state = {
        "id": "chat-current",
        "title": "当前聊天",
        "turns": frame.active_session_turns,
        "detail_panel_mode": "answers",
        "execution_steps": [],
    }
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_push_remote_final_answer", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_call_later_if_alive", lambda *args, **kwargs: None)

    frame._on_codex_event_for_chat(
        "chat-current",
        main.CodexEvent(type="item_completed", phase="final_answer", thread_id="thread-current", turn_id="turn-current", text="最终回答"),
    )

    assert frame.active_session_turns[0]["answer_md"] == "最终回答"
    assert frame.active_session_turns[0].get("answer_origin") != "codex-subagent-result"


def test_shared_codex_event_resolves_original_chat_after_switching_current_chat(frame, monkeypatch):
    frame.active_chat_id = "chat-b"
    frame.current_chat_id = "chat-b"
    frame.active_turn_idx = 0
    frame._current_chat_state = {
        "id": "chat-b",
        "title": "聊天B",
        "turns": [
            {
                "question": "B的问题",
                "answer_md": main.REQUESTING_TEXT,
                "model": main.DEFAULT_CODEX_MODEL,
                "created_at": 2.0,
                "codex_thread_id": "thread-b",
                "codex_turn_id": "turn-b",
            }
        ],
        "detail_panel_mode": "answers",
        "execution_steps": [],
        "codex_thread_id": "thread-b",
        "codex_turn_id": "turn-b",
    }
    frame.active_session_turns = frame._current_chat_state["turns"]
    frame.archived_chats = [
        {
            "id": "chat-a",
            "title": "聊天A",
            "turns": [
                {
                    "question": "A的问题",
                    "answer_md": main.REQUESTING_TEXT,
                    "model": main.DEFAULT_CODEX_MODEL,
                    "created_at": 1.0,
                    "request_status": "pending",
                    "codex_thread_id": "thread-a",
                    "codex_turn_id": "turn-a",
                }
            ],
            "created_at": 1.0,
            "updated_at": 1.0,
            "detail_panel_mode": "answers",
            "execution_steps": [],
            "codex_thread_id": "thread-a",
            "codex_turn_id": "turn-a",
        }
    ]
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_render_answer_list", lambda: None)
    monkeypatch.setattr(frame, "_call_later_if_alive", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_final_answer", lambda *args, **kwargs: None)

    frame._on_codex_event(
        main.CodexEvent(
            type="item_completed",
            phase="final_answer",
            thread_id="thread-a",
            turn_id="turn-a",
            text="A 的晚到回答",
        )
    )

    archived = frame._find_archived_chat("chat-a")
    assert archived["turns"][0]["answer_md"] == "A 的晚到回答"
    assert frame.active_session_turns[0]["answer_md"] == main.REQUESTING_TEXT


def test_codex_image_item_event_records_received_attachment(frame, monkeypatch, tmp_path):
    image_path = tmp_path / "from-cli.png"
    image_path.write_text("image", encoding="utf-8")
    frame.active_chat_id = "chat-1"
    frame.current_chat_id = "chat-1"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {
            "question": "看图",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": time.time(),
            "request_status": "pending",
        }
    ]
    seen = {"render": 0}
    monkeypatch.setattr(frame, "_render_answer_list", lambda: seen.__setitem__("render", seen["render"] + 1))

    frame._on_codex_event_for_chat(
        "chat-1",
        main.CodexEvent(
            type="item_completed",
            thread_id="thread-1",
            turn_id="turn-1",
            item_id="image-1",
            status="imageView",
            data={"id": "image-1", "type": "imageView", "path": str(image_path)},
        ),
    )

    attachments = frame.active_session_turns[0].get("received_attachments") or []
    assert attachments == [
        {
            "name": "from-cli.png",
            "path": str(image_path),
            "kind": "image",
            "direction": "incoming",
            "status": "success",
            "open_path": str(image_path),
            "source": "codex",
        }
    ]
    assert seen["render"] == 1


def test_codex_final_answer_keeps_attachment_only_turn_answer_at_bottom(frame, monkeypatch, tmp_path):
    image_path = tmp_path / "final-image.png"
    image_path.write_text("img", encoding="utf-8")
    frame.active_chat_id = "chat-1"
    frame.current_chat_id = "chat-1"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {
            "question": "",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": time.time(),
            "request_status": "pending",
            "attachments": [
                {
                    "name": image_path.name,
                    "path": str(image_path),
                    "kind": "image",
                    "direction": "outgoing",
                    "status": "success",
                    "open_path": str(image_path),
                }
            ],
        }
    ]
    monkeypatch.setattr(frame, "_push_remote_final_answer", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_focus_latest_answer", lambda: None)

    frame._on_codex_event_for_chat(
        "chat-1",
        main.CodexEvent(
            type="item_completed",
            thread_id="thread-1",
            turn_id="turn-1",
            item_id="msg-1",
            status="agentMessage",
            phase="final_answer",
            text="codex 最终回答",
        ),
    )

    rows = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    meta_types = [meta[0] for meta in frame.answer_meta]
    assert rows[2:] == ["我", "图片上传成功", "小诸葛", "codex 最终回答"]
    assert meta_types[2:] == ["user", "attachment", "ai", "answer"]


def test_codex_non_final_delta_does_not_rerender_when_visible_text_is_unchanged(frame, monkeypatch):
    frame.active_chat_id = "chat-1"
    frame.current_chat_id = "chat-1"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {
            "question": "",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": time.time(),
            "request_status": "pending",
        }
    ]
    frame.answer_list.Append(main.REQUESTING_TEXT)
    frame.answer_meta = [("answer", 0, main.REQUESTING_TEXT, main.REQUESTING_TEXT)]
    frame._active_answer_row_index = 0
    seen = {"render": 0, "repaint": 0}
    monkeypatch.setattr(frame, "_render_answer_list", lambda: seen.__setitem__("render", seen["render"] + 1))
    monkeypatch.setattr(frame, "_request_listbox_repaint", lambda *_controls: seen.__setitem__("repaint", seen["repaint"] + 1))

    frame._on_codex_event_for_chat(
        "chat-1",
        main.CodexEvent(
            type="agent_message_delta",
            thread_id="thread-1",
            turn_id="turn-1",
            text="still streaming",
            phase="draft",
        ),
    )

    assert frame.active_session_turns[0]["answer_md"] == main.REQUESTING_TEXT
    assert seen["render"] == 0
    assert seen["repaint"] == 0


def test_toggle_answer_filter_updates_state_and_rerenders_visible_answers(frame, monkeypatch):
    frame.codex_answer_english_filter_enabled = False
    frame.view_mode = "active"
    seen = {"save": 0, "render": 0, "remote": 0}
    monkeypatch.setattr(frame, "_save_state", lambda: seen.__setitem__("save", seen["save"] + 1))
    monkeypatch.setattr(frame, "_render_answer_list", lambda: seen.__setitem__("render", seen["render"] + 1))
    monkeypatch.setattr(frame, "_push_remote_state", lambda *_args, **_kwargs: seen.__setitem__("remote", seen["remote"] + 1))

    frame._toggle_codex_answer_filter()

    assert frame.codex_answer_english_filter_enabled is True
    assert seen == {"save": 1, "render": 1, "remote": 1}


def test_codex_ui_dispatch_coalesces_bursty_events(frame, monkeypatch):
    scheduled = []
    handled = []

    monkeypatch.setattr(frame, "_call_after_if_alive", lambda fn, *args: scheduled.append((fn, args)) or True)
    monkeypatch.setattr(frame, "_on_codex_event_for_chat", lambda chat_id, event: handled.append((chat_id, event.type)))

    for idx in range(10):
        frame._dispatch_codex_event_to_ui("chat-active", main.CodexEvent(type="plan_updated", text=f"step {idx}"))

    assert len(scheduled) == 1
    assert handled == []

    scheduled[0][0](*scheduled[0][1])

    assert handled == [("chat-active", "plan_updated")] * 10


def test_codex_ui_event_drain_yields_after_batch_budget(frame, monkeypatch):
    handled = []
    call_after_scheduled = []
    delayed = []

    monkeypatch.setattr(frame, "_on_codex_event_for_chat", lambda chat_id, event: handled.append((chat_id, event.type)))
    monkeypatch.setattr(frame, "_call_after_if_alive", lambda fn, *args: call_after_scheduled.append((fn, args)) or True)
    monkeypatch.setattr(frame, "_call_later_if_alive", lambda delay_ms, fn, *args: delayed.append((delay_ms, fn, args)) or object())

    for idx in range(main.CODEX_UI_EVENT_BATCH_SIZE + 5):
        frame._pending_codex_ui_events.append(("chat-active", main.CodexEvent(type="plan_updated", text=f"step {idx}")))

    frame._codex_ui_event_flush_scheduled = True
    frame._drain_codex_ui_events()

    assert len(handled) == main.CODEX_UI_EVENT_BATCH_SIZE
    assert len(frame._pending_codex_ui_events) == 5
    assert delayed == []
    assert call_after_scheduled == [(frame._drain_codex_ui_events, ())]


def test_codex_ui_event_drain_uses_smaller_batch_while_primary_control_has_focus(frame, monkeypatch):
    handled = []
    delayed = []

    monkeypatch.setattr(frame, "_on_codex_event_for_chat", lambda chat_id, event: handled.append((chat_id, event.type)))
    monkeypatch.setattr(frame, "_call_later_if_alive", lambda delay_ms, fn, *args: delayed.append((delay_ms, fn, args)) or object())
    monkeypatch.setattr(frame, "_wx_main_loop_running", lambda: True)
    monkeypatch.setattr(frame.input_edit, "HasFocus", lambda: True)

    for idx in range(main.CODEX_UI_EVENT_BATCH_SIZE):
        frame._pending_codex_ui_events.append(("chat-active", main.CodexEvent(type="plan_updated", text=f"step {idx}")))

    frame._codex_ui_event_flush_scheduled = True
    frame._drain_codex_ui_events()

    assert len(handled) == main.CODEX_UI_INTERACTIVE_EVENT_BATCH_SIZE
    assert len(frame._pending_codex_ui_events) == main.CODEX_UI_EVENT_BATCH_SIZE - main.CODEX_UI_INTERACTIVE_EVENT_BATCH_SIZE
    assert delayed == [(main.CODEX_UI_EVENT_BATCH_DELAY_MS, frame._drain_codex_ui_events, ())]


def test_codex_streaming_events_defer_state_save_off_ui_hot_path(frame, monkeypatch):
    saved = {"count": 0}
    delayed = []

    monkeypatch.setattr(frame, "_save_state", lambda: saved.__setitem__("count", saved["count"] + 1))
    monkeypatch.setattr(frame, "_call_later_if_alive", lambda delay_ms, fn, *args: delayed.append((delay_ms, fn, args)) or object())
    monkeypatch.setattr(frame, "_update_active_answer_row", lambda _idx: True)

    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {
            "question": "q",
            "answer_md": "",
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": 1.0,
        }
    ]
    frame._current_chat_state = {"id": "chat-active", "turns": frame.active_session_turns}

    frame._on_codex_event_for_chat("chat-active", main.CodexEvent(type="plan_updated", text="thinking"))

    assert saved["count"] == 0
    assert delayed


def test_codex_answer_filter_menu_label_changes_with_state(frame):
    frame.codex_answer_english_filter_enabled = False
    assert frame._codex_answer_filter_menu_label() == "在回答中过滤英文内容"
    frame.codex_answer_english_filter_enabled = True
    assert frame._codex_answer_filter_menu_label() == "取消过滤英文内容"


def test_codex_answer_filter_preserves_plain_english_sentences(frame):
    frame.codex_answer_english_filter_enabled = True

    text = frame._answer_markdown_for_output(
        "Use the latest model and keep the command name pytest unchanged.",
        "codex/main",
    )

    assert "latest model" in text
    assert "pytest" in text


def test_answer_ctrl_c_copies_filtered_codex_text(frame, monkeypatch):
    copied = {}

    class _Clipboard:
        def Open(self):
            return True

        def SetData(self, data):
            copied["text"] = data.GetText()
            return True

        def Close(self):
            return True

    frame.codex_answer_english_filter_enabled = True
    frame.active_session_turns = [
        {
            "question": "测试",
            "answer_md": "参考 C:/code/codex/main.py 和 test_answer_ctrl_c_copies_filtered_codex_text",
            "model": "codex/main",
            "created_at": time.time(),
        }
    ]
    frame._render_answer_list()
    monkeypatch.setattr(main.wx, "TheClipboard", _Clipboard())
    answer_row = next(i for i, meta in enumerate(frame.answer_meta) if meta[0] == "answer")
    frame.answer_list.SetSelection(answer_row)

    class E:
        def GetKeyCode(self):
            return ord("C")

        def ControlDown(self):
            return True

        def StopPropagation(self):
            return None

        def Skip(self):
            return None

    frame._on_answer_key_down(E())

    assert "C:/code/codex/main.py" in copied["text"]
    assert "test_answer_ctrl_c_copies_filtered_codex_text" in copied["text"]


def test_load_state_restores_codex_answer_filter_flag(frame, tmp_path):
    frame.state_path = tmp_path / "app_state.json"
    frame.state_path.write_text(
        json.dumps(
            {
                "selected_model_id": main.DEFAULT_MODEL_ID,
                "codex_answer_english_filter_enabled": True,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    frame.codex_answer_english_filter_enabled = False
    frame._load_state()

    assert frame.codex_answer_english_filter_enabled is True


def test_first_run_default_model_is_codex(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "resolve_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(main.ChatFrame, "_legacy_state_paths", lambda self: [self.state_path])
    monkeypatch.setattr(main.ChatFrame, "_migrate_legacy_state_if_needed", lambda self: None)
    f = main.ChatFrame()
    try:
        assert f.selected_model == "codex/main"
        assert f.model_combo.GetValue() == "codex"
    finally:
        f.Destroy()


def test_load_state_still_defaults_to_codex(monkeypatch, tmp_path):
    state = {
        "selected_model_id": "qwen/qwen3-8b",
        "archived_chats": [],
        "active_session_turns": [],
        "active_session_started_at": 0.0,
    }
    (tmp_path / "app_state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(main, "resolve_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(main.ChatFrame, "_legacy_state_paths", lambda self: [self.state_path])
    monkeypatch.setattr(main.ChatFrame, "_migrate_legacy_state_if_needed", lambda self: None)
    f = main.ChatFrame()
    try:
        assert f.selected_model == "codex/main"
        assert f.model_combo.GetValue() == "codex"
    finally:
        f.Destroy()


@pytest.mark.parametrize(
    ("text", "expected_name", "expected_args"),
    [
        ("/status", "status", ""),
        ("  /HELP  ", "help", ""),
        ("/compact now", "compact", "now"),
        ("/prompts:speckit.specify build it", "prompts:speckit.specify", "build it"),
    ],
)
def test_codex_slash_command_parser_accepts_leading_slash_commands(frame, text, expected_name, expected_args):
    command = frame._parse_codex_local_command(text)

    assert command == {"raw": text.strip(), "name": expected_name, "args": expected_args}


@pytest.mark.parametrize("text", ["please run /status", "look at path /tmp/file", "status", "/"])
def test_codex_slash_command_parser_ignores_non_command_text(frame, text):
    assert frame._parse_codex_local_command(text) is None


@pytest.mark.parametrize(
    ("prompt", "command"),
    [
        ("/status", "status"),
        ("/help", "help"),
        ("/compact now", "compact"),
        ("/not-a-real-command arg", "not-a-real-command"),
    ],
)
def test_codex_slash_command_is_handled_locally_without_starting_turn(frame, monkeypatch, prompt, command):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state["id"] = "chat-current"
    frame.selected_model = "codex/main"
    frame.model_combo.SetValue(main.model_display_name("codex/main"))
    started = []
    monkeypatch.setattr(frame, "_start_codex_local_command_worker_for_turn", lambda *args: started.append(args))
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)

    ok, message = frame._submit_question(prompt, source="local")

    assert ok
    assert message == ""
    assert started == [("chat-current", 0, command, main.ChatFrame._parse_codex_local_command(prompt)["args"], "codex/main")]
    assert frame.active_session_turns[0]["question"] == prompt
    assert frame.active_session_turns[0]["answer_md"] == main.REQUESTING_TEXT
    assert frame.active_session_turns[0]["request_status"] == "pending"


def test_model_display_name_maps_codex_and_claudecode():
    assert main.model_display_name("openclaw/main") == "openclaw"
    assert main.model_display_name("codex/main") == "codex"
    assert main.model_display_name("codex/gpt-5.4-medium") == "codex gpt5.4 medium"
    assert main.model_display_name("codex/gpt-5.3-codex-spark-high") == "codex gpt5.3spark high"
    assert main.model_display_name("claudecode/default") == "claudeCode"
    assert main.model_id_from_display_name("openclaw") == "openclaw/main"
    assert main.model_id_from_display_name("codex") == "codex/main"
    assert main.model_id_from_display_name("codex gpt5.4 medium") == "codex/gpt-5.4-medium"
    assert main.model_id_from_display_name("codex gpt5.3spark high") == "codex/gpt-5.3-codex-spark-high"
    assert main.model_id_from_display_name("claudeCode") == "claudecode/default"


def test_get_or_create_codex_client_uses_selected_codex_model(frame):
    frame.selected_model = "codex/gpt-5.4-medium"

    client = frame._get_or_create_codex_client("chat-codex")

    assert client.codex_model == "codex/gpt-5.4-medium"


def test_codex_ui_callbacks_skip_when_frame_is_being_deleted(frame, monkeypatch):
    frame.active_chat_id = "chat-1"
    frame.current_chat_id = "chat-1"
    frame.view_mode = "active"
    frame.active_session_turns = [{"question": "q", "answer_md": "", "model": "codex/main", "created_at": 1.0}]
    frame.active_turn_idx = 0
    frame.active_session_turns[0]["request_status"] = "pending"
    frame.active_session_turns[0]["request_recoverable"] = True

    scheduled = []
    monkeypatch.setattr(frame, "IsBeingDeleted", lambda: True, raising=False)
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *args, **kwargs: scheduled.append(("after", fn.__name__)))
    monkeypatch.setattr(main.wx, "CallLater", lambda delay, fn, *args, **kwargs: scheduled.append(("later", delay, fn.__name__)) or type("T", (), {"IsRunning": lambda self: False, "Stop": lambda self: None})())
    monkeypatch.setattr(frame, "_render_answer_list", lambda: scheduled.append(("render",)))
    monkeypatch.setattr(frame, "_save_state", lambda: scheduled.append(("save",)))
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: scheduled.append(("sound",)))
    monkeypatch.setattr(frame, "_push_remote_state", lambda *args, **kwargs: scheduled.append(("remote",)))
    monkeypatch.setattr(frame, "SetStatusText", lambda *args, **kwargs: scheduled.append(("status",)))

    frame._dispatch_codex_event_to_ui("chat-1", main.CodexEvent(type="agent_message_delta", text="x"))
    frame._queue_answer_char_redirect("x")
    frame._on_done(0, "完成", "", "codex/main", "", "chat-1")

    assert all(item[0] not in {"after", "later"} for item in scheduled)
    assert scheduled == [("status",), ("remote",), ("sound",)]


def test_remote_new_chat_preserves_current_codex_speed_combo_selection(frame, monkeypatch):
    frame.selected_model = main.DEFAULT_CODEX_MODEL
    frame.model_combo.SetValue(main.model_display_name(main.DEFAULT_CODEX_MODEL))
    frame._current_chat_state["model"] = main.DEFAULT_CODEX_MODEL
    frame.codex_speed_combo.SetValue("快速")
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_refresh_history", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_render_answer_list", lambda *args, **kwargs: None)

    result = frame._start_remote_new_chat({})

    assert result["model"] == main.DEFAULT_CODEX_MODEL
    assert frame.codex_speed_combo.GetValue() == "快速"
    assert frame._current_chat_state["codex_service_tier"] == "fast"


def test_desktop_codex_speed_combo_change_does_not_push_remote_state_synchronously(frame, monkeypatch):
    frame.active_chat_id = "chat-speed"
    frame.current_chat_id = "chat-speed"
    frame.selected_model = main.DEFAULT_CODEX_MODEL
    frame.model_combo.SetValue(main.model_display_name(main.DEFAULT_CODEX_MODEL))
    frame._current_chat_state = {
        "id": "chat-speed",
        "model": main.DEFAULT_CODEX_MODEL,
        "turns": [],
        "codex_service_tier": "standard",
    }
    pushed = []
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: None)
    monkeypatch.setattr(frame, "_push_remote_state", lambda chat_id: pushed.append(chat_id))

    frame.codex_speed_combo.SetValue("快速")
    frame._on_codex_speed_changed(None)

    assert frame._current_chat_state["codex_service_tier"] == "fast"
    assert pushed == []


def test_codex_speed_standard_selection_stays_fixed_after_send_with_previous_fast_turn(frame, monkeypatch):
    frame.active_chat_id = "chat-speed"
    frame.current_chat_id = "chat-speed"
    frame.selected_model = main.DEFAULT_CODEX_MODEL
    frame.model_combo.SetValue(main.model_display_name(main.DEFAULT_CODEX_MODEL))
    frame.active_session_turns = [
        {
            "question": "old",
            "answer_md": "old answer",
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": 1.0,
            "codex_service_tier": "fast",
        }
    ]
    frame._current_chat_state = {
        "id": "chat-speed",
        "title": "小猫叫声",
        "model": main.DEFAULT_CODEX_MODEL,
        "turns": frame.active_session_turns,
        "codex_service_tier": "fast",
    }
    frame.codex_speed_combo.SetValue("标准")
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda *args, **kwargs: None)

    class _NoOpThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            pass

        def start(self):
            pass

    monkeypatch.setattr(main.threading, "Thread", _NoOpThread)

    frame._on_codex_speed_changed(None)
    ok, message = frame._submit_question("小猫怎么叫", source="local", model=main.DEFAULT_CODEX_MODEL)

    assert ok is True
    assert message == ""
    assert frame._current_chat_state["codex_service_tier"] in {"", "standard"}
    assert frame.active_session_turns[-1]["codex_service_tier"] in {"", "standard"}
    assert frame.codex_speed_combo.GetValue() == "标准"


def test_codex_final_answer_first_visible_response_appends_without_rebuild(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_codex_thread_id = "thread-current"
    frame.active_codex_turn_id = "turn-current"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {
            "question": "question",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL,
            "codex_thread_id": "thread-current",
            "codex_turn_id": "turn-current",
        }
    ]
    frame._current_chat_state = {
        "id": "chat-current",
        "turns": frame.active_session_turns,
        "detail_panel_mode": "answers",
        "execution_steps": [],
    }
    monkeypatch.setattr(
        frame,
        "_render_answer_list",
        lambda *args, **kwargs: pytest.fail("a first final answer must append instead of rebuilding the list"),
    )
    monkeypatch.setattr(frame, "_focus_latest_answer", lambda: None)

    frame._on_codex_event_for_chat(
        "chat-current",
        main.CodexEvent(
            type="item_completed",
            thread_id="thread-current",
            turn_id="turn-current",
            status="agentMessage",
            phase="final_answer",
            text="final answer",
        ),
    )

    assert frame.active_session_turns[0]["answer_md"] == "final answer"
    assert "final answer" in list(frame.answer_list.GetStrings())
