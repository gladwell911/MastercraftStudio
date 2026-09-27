import ctypes
from types import SimpleNamespace

import main
import pytest


def _send_key(window, key):
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    hwnd = int(window.GetHandle())
    user32.SendMessageW(hwnd, 0x0100, key, 1)
    user32.SendMessageW(hwnd, 0x0101, key, 1 | (1 << 30) | (1 << 31))


def test_codex_chat_information_list_arrows_and_escape_restore_focus(frame, wx_app):
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = "codex/main"
    frame.active_chat_id = "chat-1"
    frame.active_codex_thread_id = "thread-1"
    frame._current_chat_state = {
        "id": "chat-1", "model": "codex/main", "codex_thread_id": "thread-1",
        "context_usage": {"used_tokens": 11891, "context_window": 258400,
                          "source": "codex", "exact": True, "fresh": True},
    }
    frame.input_edit.SetFocus()
    wx_app.Yield()

    assert frame._show_chat_information()
    dialog = frame._chat_information_dialog
    wx_app.Yield()
    assert main.wx.Window.FindFocus() is dialog.information_list
    assert dialog.information_list.GetStrings()[0] == "当前上下文：已用 4.6%（11,891 / 258,400 token）"
    assert dialog.information_list.GetCount() == 2

    _send_key(dialog.information_list, 0x28)
    wx_app.Yield()
    assert dialog.information_list.GetSelection() == 1
    _send_key(dialog.information_list, 0x26)
    wx_app.Yield()
    assert dialog.information_list.GetSelection() == 0

    _send_key(dialog.information_list, 0x1B)
    wx_app.Yield()
    assert frame._chat_information_dialog is None
    assert not frame.IsIconized()
    assert main.wx.Window.FindFocus() is frame.input_edit


def test_chat_information_is_unavailable_for_other_models_and_unchanged_rows_keep_focus(frame, wx_app):
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = "openai/gpt-5.2"
    frame._current_chat_state = {"id": "chat-1", "model": "openai/gpt-5.2"}
    frame._on_chat_information_menu_open(SimpleNamespace(Skip=lambda: None))
    item = frame.GetMenuBar().FindItemById(int(frame._chat_information_menu_id))
    assert not item.IsEnabled()
    assert not frame._show_chat_information()

    frame.selected_model = "kimi/main"
    frame._current_chat_state = {"id": "chat-2", "model": "kimi/main", "kimi_session_id": "session-2"}
    frame._on_chat_information_menu_open(SimpleNamespace(Skip=lambda: None))
    assert item.IsEnabled()
    frame.input_edit.SetFocus()
    wx_app.Yield()
    assert frame._show_chat_information()
    dialog = frame._chat_information_dialog
    dialog.information_list.SetSelection(1)
    dialog.information_list.SetFocus()
    wx_app.Yield()
    frame._refresh_chat_information(frame._current_chat_state)
    assert dialog.information_list.GetSelection() == 1
    assert main.wx.Window.FindFocus() is dialog.information_list
    dialog.Close()
    wx_app.Yield()


@pytest.mark.parametrize("identity_change", ["chat", "thread", "account"])
def test_stale_chat_information_refresh_cannot_cross_identity(frame, wx_app, identity_change):
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = "codex/main"
    chat = {
        "id": "chat-1", "model": "codex/main", "codex_thread_id": "thread-1",
        "codex_account_id": "account-1",
        "context_usage": {"used_tokens": 1000, "context_window": 10000,
                          "source": "codex", "exact": True, "fresh": True},
    }
    frame._current_chat_state = chat
    frame.input_edit.SetFocus()
    wx_app.Yield()
    assert frame._show_chat_information()
    dialog = frame._chat_information_dialog
    old_rows = list(dialog.information_list.GetStrings())

    if identity_change == "chat":
        chat = dict(chat, id="chat-2")
        frame._current_chat_state = chat
    elif identity_change == "thread":
        chat["codex_thread_id"] = "thread-2"
    else:
        chat["codex_account_id"] = "account-2"
    chat["context_usage"] = {"used_tokens": 9000, "context_window": 10000,
                             "source": "codex", "exact": True, "fresh": True}

    frame._refresh_chat_information(chat)
    assert list(dialog.information_list.GetStrings()) == old_rows
    assert frame._show_chat_information()
    assert frame._chat_information_dialog is not dialog
    assert "9,000" in frame._chat_information_dialog.information_list.GetString(0)
    frame._chat_information_dialog.Close()
    wx_app.Yield()


def test_menu_state_is_rechecked_when_model_changes_after_menu_opens(frame):
    frame.view_mode = "active"
    frame.selected_model = "codex/main"
    frame._current_chat_state = {"id": "chat-1", "model": "codex/main"}
    frame._on_chat_information_menu_open(SimpleNamespace(Skip=lambda: None))
    item = frame.GetMenuBar().FindItemById(int(frame._chat_information_menu_id))
    assert item.IsEnabled()

    frame.selected_model = "openai/gpt-5.2"
    frame._current_chat_state = {"id": "chat-1", "model": "openai/gpt-5.2"}
    assert not frame._show_chat_information()
    assert frame._chat_information_dialog is None


@pytest.mark.parametrize("reset_via_clear", [True, False])
def test_codex_thread_reset_discards_old_usage_until_new_thread_reports(frame, wx_app, monkeypatch, reset_via_clear):
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = "codex/main"
    frame.active_chat_id = "chat-1"
    frame.current_chat_id = "chat-1"
    frame.active_codex_thread_id = "thread-1"
    chat = {
        "id": "chat-1", "model": "codex/main", "codex_thread_id": "thread-1",
        "codex_turn_id": "turn-1",
        "turns": [{"question": "q", "model": "codex/main", "request_status": "done",
                   "codex_turn_id": "turn-1", "codex_thread_id": "thread-1"}],
        "context_usage": {"used_tokens": 5000, "context_window": 10000,
                          "source": "codex", "exact": True, "fresh": True},
    }
    frame._current_chat_state = chat
    frame.active_session_turns = chat["turns"]
    frame._pending_context_usage_by_turn[("chat-1", 0)] = dict(chat["context_usage"])
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)
    frame.input_edit.SetFocus()
    wx_app.Yield()
    assert frame._show_chat_information()
    dialog = frame._chat_information_dialog
    assert "5,000" in dialog.information_list.GetString(0)

    if reset_via_clear:
        frame._handle_codex_clear_command(chat)
        wx_app.Yield()
        assert dialog.identity[2] == ""
        assert dialog.information_list.GetString(0) == "当前上下文：暂不可用"
        frame._on_codex_event_for_chat(
            "chat-1", main.CodexEvent(type="token_count", thread_id="thread-1",
                                       usage={"used_tokens": 8000, "context_window": 10000,
                                              "source": "codex", "exact": True, "fresh": True}),
        )
        assert dialog.information_list.GetString(0) == "当前上下文：暂不可用"

    chat["turns"].append({"question": "q2", "model": "codex/main", "request_status": "pending",
                          "codex_turn_id": "turn-2",
                          "codex_context_generation": int(chat.get("codex_context_generation") or 0)})
    start_generation = int(chat.get("codex_context_generation") or 0)
    frame.active_turn_idx = 1
    frame._apply_codex_worker_thread_state(
        "chat-1", {"thread_id": "thread-2", "turn_id": "turn-2", "turn_idx": 1,
                   "context_generation": start_generation},
    )
    wx_app.Yield()
    assert chat["context_usage"] is None
    assert ("chat-1", 0) not in frame._pending_context_usage_by_turn
    assert dialog.identity[2] == "thread-2"
    assert dialog.information_list.GetString(0) == "当前上下文：暂不可用"

    frame._on_codex_event_for_chat(
        "chat-1", main.CodexEvent(type="token_count", thread_id="thread-1", turn_id="turn-1",
                                   usage={"used_tokens": 8000, "context_window": 10000,
                                          "source": "codex", "exact": True, "fresh": True}),
    )
    assert dialog.information_list.GetString(0) == "当前上下文：暂不可用"
    frame._on_codex_event_for_chat(
        "chat-1", main.CodexEvent(type="token_count", thread_id="thread-2", turn_id="turn-2",
                                   data={"turn_idx": 1, "context_generation": start_generation},
                                   usage={"used_tokens": 1000, "context_window": 10000,
                                          "source": "codex", "exact": True, "fresh": True}),
    )
    assert "1,000" in dialog.information_list.GetString(0)
    dialog.Close()
    wx_app.Yield()


def test_same_thread_old_turn_token_cannot_replace_latest_usage(frame, monkeypatch):
    turns = [
        {"question": "old", "model": "codex/main", "request_status": "done",
         "codex_turn_id": "turn-1", "codex_thread_id": "thread-1",
         "codex_start_generation": 0},
        {"question": "new", "model": "codex/main", "request_status": "pending",
         "codex_turn_id": "turn-2", "codex_thread_id": "thread-1",
         "codex_start_generation": 0},
    ]
    chat = {"id": "chat-1", "model": "codex/main", "codex_thread_id": "thread-1",
            "codex_turn_id": "turn-2", "turns": turns}
    frame.active_chat_id = "chat-1"
    frame.current_chat_id = "chat-1"
    frame.active_codex_thread_id = "thread-1"
    frame.active_codex_turn_id = "turn-2"
    frame.active_turn_idx = 1
    frame.active_session_turns = turns
    frame._current_chat_state = chat
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    old_event = main.CodexEvent(type="token_count", thread_id="thread-1", turn_id="turn-1",
                                data={"turn_idx": 0, "context_generation": 0},
                                usage={"used_tokens": 9000, "context_window": 10000,
                                       "source": "codex", "exact": True, "fresh": True})
    frame._on_codex_event_for_chat("chat-1", old_event)
    assert chat.get("context_usage") is None
    assert ("chat-1", 1) not in frame._pending_context_usage_by_turn

    new_event = main.CodexEvent(type="token_count", thread_id="thread-1", turn_id="turn-2",
                                data={"turn_idx": 1, "context_generation": 0},
                                usage={"used_tokens": 1000, "context_window": 10000,
                                       "source": "codex", "exact": True, "fresh": True})
    frame._on_codex_event_for_chat("chat-1", new_event)
    assert frame._pending_context_usage_by_turn[("chat-1", 1)]["used_tokens"] == 1000
    turns[1]["request_status"] = "done"
    frame._on_codex_event_for_chat("chat-1", main.CodexEvent(
        type="token_count", thread_id="thread-1",
        data={"turn_idx": 1, "context_generation": 0}, usage=new_event.usage,
    ))
    assert chat["context_usage"]["used_tokens"] == 1000


def test_early_codex_usage_survives_ack_but_not_clear_generation(frame, wx_app, monkeypatch):
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = "codex/main"
    frame.active_chat_id = "chat-1"
    frame.current_chat_id = "chat-1"
    chat = {"id": "chat-1", "model": "codex/main", "turns": [
        {"question": "q1", "model": "codex/main", "request_status": "pending",
         "codex_context_generation": 0},
    ]}
    frame._current_chat_state = chat
    frame.active_session_turns = chat["turns"]
    frame.active_turn_idx = 0
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)
    assert frame._show_chat_information()
    dialog = frame._chat_information_dialog
    first_usage = {"used_tokens": 1000, "context_window": 10000,
                   "source": "codex", "exact": True, "fresh": True}
    frame._on_codex_event_for_chat(
        "chat-1", main.CodexEvent(type="token_count", thread_id="thread-1", turn_id="turn-1",
                                   data={"turn_idx": 0, "context_generation": 0}, usage=first_usage),
    )
    assert ("chat-1", 0) in frame._pending_context_usage_by_turn
    assert "1,000" in dialog.information_list.GetString(0)

    frame._apply_codex_worker_thread_state("chat-1", {"thread_id": "thread-1", "turn_id": "turn-1", "turn_idx": 0,
                                                       "context_generation": 0})
    assert ("chat-1", 0) in frame._pending_context_usage_by_turn
    assert "1,000" in dialog.information_list.GetString(0)

    frame._handle_codex_clear_command(chat)
    wx_app.Yield()
    assert dialog.information_list.GetString(0) == "当前上下文：暂不可用"
    frame._apply_codex_worker_thread_state(
        "chat-1", {"thread_id": "thread-1", "turn_id": "turn-1", "turn_idx": 0,
                   "context_generation": 0},
    )
    assert chat["codex_thread_id"] == ""
    frame._on_codex_event_for_chat(
        "chat-1", main.CodexEvent(type="token_count", thread_id="thread-1", turn_id="turn-1",
                                   data={"turn_idx": 0, "context_generation": 0}, usage=first_usage),
    )
    assert dialog.information_list.GetString(0) == "当前上下文：暂不可用"

    chat["turns"].append({"question": "q2", "model": "codex/main", "request_status": "pending",
                          "codex_context_generation": chat["codex_context_generation"]})
    frame.active_turn_idx = 1
    frame._on_codex_event_for_chat(
        "chat-1", main.CodexEvent(type="token_count", thread_id="thread-1", turn_id="turn-1",
                                   data={"turn_idx": 1, "context_generation": 0}, usage=first_usage),
    )
    assert ("chat-1", 1) not in frame._pending_context_usage_by_turn
    frame._on_codex_event_for_chat(
        "chat-1", main.CodexEvent(type="token_count", thread_id="thread-2", turn_id="turn-2",
                                   data={"turn_idx": 1, "context_generation": chat["codex_context_generation"]}, usage=first_usage),
    )
    assert ("chat-1", 1) in frame._pending_context_usage_by_turn
    frame._apply_codex_worker_thread_state("chat-1", {"thread_id": "thread-2", "turn_id": "turn-2", "turn_idx": 1,
                                                       "context_generation": chat["codex_context_generation"]})
    assert ("chat-1", 1) in frame._pending_context_usage_by_turn
    assert "1,000" in dialog.information_list.GetString(0)
    dialog.Close()
    wx_app.Yield()
