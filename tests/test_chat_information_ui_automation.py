import ctypes
import threading
import time
from types import SimpleNamespace

import main
import pytest


_REAL_CODEX_INFORMATION_REQUEST = main.ChatFrame._request_codex_chat_information
_REAL_KIMI_INFORMATION_REQUEST = main.ChatFrame._request_kimi_chat_information


@pytest.fixture(autouse=True)
def _disable_external_codex_information_reads(monkeypatch):
    monkeypatch.setattr(main.ChatFrame, "_request_codex_chat_information", lambda *_args: None)
    monkeypatch.setattr(main.ChatFrame, "_request_kimi_chat_information", lambda *_args, **_kwargs: None)


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
    assert dialog.information_list.GetCount() == 4

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


def test_codex_total_and_weekly_quota_rows_ignore_stale_reply(frame, wx_app, monkeypatch):
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = "codex/main"
    turn = {"question": "q", "model": "codex/main", "request_status": "done",
            "codex_turn_id": "turn-1", "codex_thread_id": "thread-1"}
    chat = {"id": "chat-1", "model": "codex/main", "codex_thread_id": "thread-1",
            "codex_turn_id": "turn-1", "codex_account_id": "account-1", "turns": [turn]}
    frame.active_chat_id = frame.current_chat_id = "chat-1"
    frame.active_codex_thread_id = "thread-1"
    frame.active_codex_turn_id = "turn-1"
    frame.active_session_turns = chat["turns"]
    frame._current_chat_state = chat
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)
    frame.input_edit.SetFocus()
    wx_app.Yield()
    assert frame._show_chat_information()
    dialog = frame._chat_information_dialog
    dialog.information_list.SetSelection(1)
    dialog.information_list.SetFocus()
    identity = dialog.identity
    frame._chat_information_request = ("req-new", 2, identity)

    stale = {"chat_id": "chat-1", "identity": list(identity), "generation": 1,
             "account": {"account": {"type": "chatgpt", "id": "account-1"}},
             "rate_limits": {"rateLimits": {"limitId": "codex", "secondary": {
                 "windowDurationMins": 10080, "usedPercent": 25, "resetsAt": 1770000000}}}}
    frame._on_codex_worker_message("chat-1", {"type": "chat_information", "id": "req-old", "payload": stale})
    assert dialog.information_list.GetString(3) == "Codex 周额度：暂不可用"

    fresh = dict(stale, generation=2)
    frame._on_codex_worker_message("chat-1", {"type": "chat_information", "id": "req-new", "payload": fresh})
    assert "剩余 75.0%" in dialog.information_list.GetString(3)
    assert "本地重置" in dialog.information_list.GetString(3)
    assert dialog.information_list.GetSelection() == 1
    assert main.wx.Window.FindFocus() is dialog.information_list

    frame._on_codex_event_for_chat("chat-1", main.CodexEvent(
        type="token_count", thread_id="thread-1", turn_id="turn-1",
        data={"turn_idx": 0, "context_generation": 0, "session_total_tokens": 789},
    ))
    assert dialog.information_list.GetString(1) == "会话累计 token：789"
    frame._handle_codex_clear_command(chat)
    assert dialog.information_list.GetString(1) == "会话累计 token：暂不可用"
    frame._on_codex_event_for_chat("chat-1", main.CodexEvent(
        type="token_count", thread_id="thread-1", turn_id="turn-1",
        data={"turn_idx": 0, "context_generation": 0, "session_total_tokens": 999},
    ))
    assert dialog.information_list.GetString(1) == "会话累计 token：暂不可用"
    dialog.Close()
    wx_app.Yield()


def test_codex_information_account_states_and_identity_switch(frame, wx_app, monkeypatch):
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = "codex/main"
    chat = {"id": "chat-1", "model": "codex/main", "codex_thread_id": "thread-1",
            "codex_account_id": "account-1"}
    frame.active_chat_id = frame.current_chat_id = "chat-1"
    frame.active_codex_thread_id = "thread-1"
    frame._current_chat_state = chat
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)
    assert frame._show_chat_information()
    dialog = frame._chat_information_dialog
    identity = dialog.identity

    frame._chat_information_request = ("req-1", 1, identity)
    payload = {"chat_id": "chat-1", "identity": list(identity), "generation": 1,
               "account": {"account": {"type": "apiKey", "id": "account-1"}},
               "rate_limits_error": "not available"}
    frame._on_codex_worker_message("chat-1", {"type": "chat_information", "id": "req-1", "payload": payload})
    assert dialog.information_list.GetString(3) == "Codex 周额度：API key 账号不适用"

    frame._chat_information_request = ("req-2", 2, identity)
    payload = dict(payload, generation=2, account={"account": None}, rate_limits_error="")
    frame._on_codex_worker_message("chat-1", {"type": "chat_information", "id": "req-2", "payload": payload})
    assert dialog.information_list.GetString(2) == "Codex 账号：未登录"
    assert dialog.information_list.GetString(3) == "Codex 周额度：未登录"

    current_rows = list(dialog.information_list.GetStrings())
    frame._chat_information_request = ("req-3", 3, dialog.identity)
    chat["codex_thread_id"] = "thread-2"
    payload = dict(payload, identity=list(dialog.identity), generation=3,
                   account={"account": {"type": "chatgpt", "id": "account-2"}})
    frame._on_codex_worker_message("chat-1", {"type": "chat_information", "id": "req-3", "payload": payload})
    assert list(dialog.information_list.GetStrings()) == current_rows
    closed_identity = dialog.identity
    saved_account_label = chat.get("codex_account_label")
    dialog.Close()
    wx_app.Yield()
    frame._chat_information_request = ("req-late", 4, closed_identity)
    frame._on_codex_worker_message("chat-1", {"type": "chat_information", "id": "req-late",
                                              "payload": dict(payload, identity=list(closed_identity), generation=4)})
    assert chat.get("codex_account_label") == saved_account_label


def test_first_codex_information_read_starts_worker_off_ui_thread(frame, wx_app, monkeypatch):
    monkeypatch.setattr(main.ChatFrame, "_request_codex_chat_information", _REAL_CODEX_INFORMATION_REQUEST)
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = "codex/main"
    frame.active_chat_id = frame.current_chat_id = "chat-1"
    frame.active_codex_thread_id = "thread-1"
    frame._current_chat_state = {"id": "chat-1", "model": "codex/main", "codex_thread_id": "thread-1"}
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)
    started = threading.Event()
    release = threading.Event()
    calls = []

    class Client:
        def start(self):
            calls.append(threading.current_thread())
            started.set()
            assert release.wait(2)

        def read_chat_information(self, **payload):
            frame._on_codex_worker_message("chat-1", {
                "type": "chat_information", "id": "req-1", "payload": {
                    "chat_id": "chat-1", "identity": payload["identity"],
                    "generation": payload["generation"],
                    "account": {"account": {"type": "chatgpt", "id": "account-1"}},
                    "rate_limits": {"rateLimits": {"limitId": "codex", "primary": {
                        "windowDurationMins": 10080, "usedPercent": 20, "resetsAt": 1770000000}}},
                },
            })
            return "req-1"

    monkeypatch.setattr(frame, "_get_or_create_codex_client", lambda *_args: Client())
    frame.input_edit.SetFocus()
    wx_app.Yield()
    assert frame._show_chat_information()
    dialog = frame._chat_information_dialog
    assert started.wait(1)
    assert calls[0] is not threading.main_thread()
    assert main.wx.Window.FindFocus() is dialog.information_list
    assert dialog.information_list.GetString(3) == "Codex 周额度：暂不可用"
    release.set()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and "剩余 80.0%" not in dialog.information_list.GetString(3):
        wx_app.Yield()
        time.sleep(0.01)
    assert "剩余 80.0%" in dialog.information_list.GetString(3)
    assert main.wx.Window.FindFocus() is dialog.information_list
    dialog.Close()
    wx_app.Yield()


def test_codex_account_switch_retires_old_thread_and_rejects_its_tokens(frame, wx_app, monkeypatch):
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = "codex/main"
    turns = [{"question": "q", "model": "codex/main", "request_status": "done",
              "codex_thread_id": "thread-1", "codex_turn_id": "turn-1"}]
    chat = {"id": "chat-1", "model": "codex/main", "codex_thread_id": "thread-1",
            "codex_turn_id": "turn-1", "codex_account_id": "account-A", "turns": turns,
            "codex_session_total_tokens": 500,
            "context_usage": {"used_tokens": 100, "context_window": 1000,
                              "source": "codex", "exact": True, "fresh": True}}
    frame.active_chat_id = frame.current_chat_id = "chat-1"
    frame.active_codex_thread_id = "thread-1"
    frame.active_codex_turn_id = "turn-1"
    frame.active_session_turns = turns
    frame._current_chat_state = chat
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)
    assert frame._show_chat_information()
    dialog = frame._chat_information_dialog
    identity = dialog.identity
    frame._chat_information_request = ("req-switch", 1, identity)
    frame._on_codex_worker_message("chat-1", {"type": "chat_information", "id": "req-switch",
                                              "payload": {"chat_id": "chat-1", "identity": list(identity),
                                                          "generation": 1,
                                                          "account": {"account": {"type": "chatgpt", "id": "account-B"}},
                                                          "rate_limits": {"rateLimits": {}}}})
    assert chat["codex_thread_id"] == ""
    assert frame.active_codex_thread_id == ""
    assert dialog.information_list.GetString(1) == "会话累计 token：暂不可用"
    frame._on_codex_event_for_chat("chat-1", main.CodexEvent(
        type="token_count", thread_id="thread-1", turn_id="turn-1",
        data={"turn_idx": 0, "context_generation": 0, "session_total_tokens": 999},
        usage={"used_tokens": 900, "context_window": 1000,
               "source": "codex", "exact": True, "fresh": True},
    ))
    assert chat["codex_session_total_tokens"] is None
    assert chat["context_usage"] is None
    assert dialog.information_list.GetString(1) == "会话累计 token：暂不可用"
    dialog.Close()
    wx_app.Yield()


def test_kimi_status_snapshot_and_session_clear_keep_focus_and_reject_old_results(frame, wx_app, monkeypatch):
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = "kimi/main"
    chat = {"id": "chat-1", "model": "kimi/main", "kimi_session_id": "session-1",
            "kimi_account_id": "account-1", "turns": []}
    frame.active_chat_id = frame.current_chat_id = "chat-1"
    frame.active_kimi_session_id = "session-1"
    frame._current_chat_state = chat
    frame.active_session_turns = chat["turns"]
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    frame.input_edit.SetFocus()
    wx_app.Yield()


    assert frame._show_chat_information()
    dialog = frame._chat_information_dialog
    dialog.information_list.SetSelection(1)
    dialog.information_list.SetFocus()
    identity = dialog.identity
    frame._kimi_information_requests["chat-1"] = {
        "generation": 1, "identity": identity, "context_revision": 0, "visible_only": True,
        "remaining": {"status", "snapshot"},
    }
    frame._apply_kimi_chat_information_result("chat-1", 1, identity, "status",
                                               {"context_tokens": 1000, "max_context_tokens": 4000}, False)
    frame._apply_kimi_chat_information_result("chat-1", 1, identity, "snapshot",
                                               {"session": {"usage": {"input_tokens": 700,
                                                                      "output_tokens": 200,
                                                                      "cache_read_tokens": 999}}}, False)
    assert "25.0%" in dialog.information_list.GetString(0)
    assert dialog.information_list.GetString(1) == "会话累计 token：900"
    assert dialog.information_list.GetSelection() == 1
    assert main.wx.Window.FindFocus() is dialog.information_list

    frame._kimi_information_requests["chat-1"] = {
        "generation": 2, "identity": identity, "context_revision": 0, "visible_only": True,
        "remaining": {"status", "snapshot"},
    }
    frame._apply_kimi_chat_information_result("chat-1", 2, identity, "status",
                                               {"context_tokens": 300}, False)
    assert "300 token，窗口未知" in dialog.information_list.GetString(0)
    frame._kimi_information_requests["chat-1"] = {
        "generation": 3, "identity": identity, "context_revision": 0, "visible_only": True,
        "remaining": {"status", "snapshot"},
    }
    frame._apply_kimi_chat_information_result("chat-1", 3, identity, "status", None, True)
    frame._apply_kimi_chat_information_result("chat-1", 3, identity, "snapshot",
                                               {"session": {"usage": {"input_tokens": 80,
                                                                      "output_tokens": 20}}}, False)
    assert dialog.information_list.GetString(0) == "当前上下文：查询失败"
    assert dialog.information_list.GetString(1) == "会话累计 token：100"
    frame._kimi_information_requests["chat-1"] = {
        "generation": 4, "identity": identity, "context_revision": 0, "visible_only": True,
        "remaining": {"status", "snapshot"},
    }
    frame._apply_kimi_chat_information_result("chat-1", 4, identity, "status",
                                               {"context_tokens": 200, "max_context_tokens": 1000}, False)
    frame._apply_kimi_chat_information_result("chat-1", 4, identity, "snapshot", None, True)
    assert "20.0%" in dialog.information_list.GetString(0)
    assert dialog.information_list.GetString(1) == "会话累计 token：查询失败"
    frame._kimi_information_requests["chat-1"] = {
        "generation": 5, "identity": identity, "context_revision": 0, "visible_only": True,
        "remaining": {"status", "snapshot"},
    }
    chat["kimi_account_id"] = "account-2"
    frame._apply_kimi_chat_information_result("chat-1", 5, identity, "snapshot",
                                               {"session": {"usage": {"input_tokens": 999,
                                                                      "output_tokens": 999}}}, False)
    assert chat["kimi_session_total_tokens"] is None
    chat["kimi_account_id"] = "account-1"
    frame._handle_kimi_clear_command(chat)
    assert dialog.information_list.GetString(1) == "会话累计 token：暂不可用"
    frame._apply_kimi_chat_information_result("chat-1", 2, identity, "snapshot",
                                               {"session": {"usage": {"input_tokens": 900,
                                                                      "output_tokens": 900}}}, False)
    assert chat["kimi_session_total_tokens"] is None
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

def test_kimi_completion_refresh_starts_only_for_visible_information(frame, wx_app, monkeypatch):
    monkeypatch.setattr(main.ChatFrame, "_request_kimi_chat_information", _REAL_KIMI_INFORMATION_REQUEST)
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = "kimi/main"
    frame.active_chat_id = frame.current_chat_id = "chat-1"
    chat = {"id": "chat-1", "model": "kimi/main", "kimi_session_id": "session-1"}
    frame._current_chat_state = chat
    calls = []

    class ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self.target = target

        def start(self):
            self.target()

    class Client:
        def start(self):
            calls.append("start")

        def get_status(self, session_id):
            calls.append(("status", session_id))
            return {"context_tokens": 2, "max_context_tokens": 10}

        def get_snapshot(self, session_id):
            calls.append(("snapshot", session_id))
            return {"session": {"usage": {"input_tokens": 3, "output_tokens": 4}}}

    monkeypatch.setattr(main.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(frame, "_ensure_kimi_client", lambda: Client())
    frame._request_kimi_chat_information(chat, "kimi/main", visible_only=True)
    assert calls == []
    assert frame._show_chat_information()
    wx_app.Yield()
    calls.clear()
    frame._request_kimi_chat_information(chat, "kimi/main", visible_only=True)
    wx_app.Yield()
    assert calls == ["start", ("status", "session-1"), ("snapshot", "session-1")]
    assert frame._chat_information_dialog.information_list.GetString(1) == "会话累计 token：7"
    frame._chat_information_dialog.Close()
    wx_app.Yield()


def test_kimi_live_status_wins_over_older_panel_status_reply(frame, wx_app):
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = "kimi/main"
    frame.active_chat_id = frame.current_chat_id = "chat-1"
    frame.active_kimi_session_id = "session-1"
    turn = {"question": "q", "model": "kimi/main", "request_status": "done", "kimi_turn_id": "turn-1"}
    chat = {"id": "chat-1", "model": "kimi/main", "kimi_session_id": "session-1", "turns": [turn]}
    frame._current_chat_state = chat
    frame.active_session_turns = chat["turns"]
    frame.active_turn_idx = 0
    assert frame._show_chat_information()
    dialog = frame._chat_information_dialog
    identity = dialog.identity
    frame._kimi_information_requests["chat-1"] = {
        "generation": 1, "identity": identity, "context_revision": 0,
        "visible_only": True, "remaining": {"status", "snapshot"},
    }

    frame._on_kimi_event_for_chat("chat-1", main.CodexEvent(
        type="thread_status_changed", thread_id="session-1", turn_id="turn-1",
        usage={"context_tokens": 100, "max_context_tokens": 1000},
    ))
    assert "10.0%" in dialog.information_list.GetString(0)
    frame._apply_kimi_chat_information_result("chat-1", 1, identity, "status",
                                               {"context_tokens": 900, "max_context_tokens": 1000}, False)
    assert "10.0%" in dialog.information_list.GetString(0)
    frame._apply_kimi_chat_information_result("chat-1", 1, identity, "snapshot",
                                               {"session": {"usage": {"input_tokens": 3, "output_tokens": 4}}}, False)
    assert dialog.information_list.GetString(1) == "会话累计 token：7"
    dialog.Close()
    wx_app.Yield()


def test_kimi_old_session_status_cannot_replace_current_session_usage(frame, wx_app):
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = "kimi/main"
    frame.active_chat_id = frame.current_chat_id = "chat-1"
    frame.active_kimi_session_id = "session-2"
    turns = [
        {"question": "old", "model": "kimi/main", "request_status": "done",
         "kimi_session_id": "session-1", "kimi_turn_id": "turn-1"},
        {"question": "new", "model": "kimi/main", "request_status": "done",
         "kimi_session_id": "session-2", "kimi_turn_id": "turn-2"},
    ]
    chat = {"id": "chat-1", "model": "kimi/main", "kimi_session_id": "session-2", "turns": turns}
    frame._current_chat_state = chat
    frame.active_session_turns = turns
    frame.active_turn_idx = 1
    assert frame._show_chat_information()
    dialog = frame._chat_information_dialog

    frame._on_kimi_event_for_chat("chat-1", main.CodexEvent(
        type="thread_status_changed", thread_id="session-2", turn_id="turn-2",
        usage={"context_tokens": 100, "max_context_tokens": 1000},
    ))
    revision = chat.get("kimi_context_revision")
    assert "10.0%" in dialog.information_list.GetString(0)
    frame._on_kimi_event_for_chat("chat-1", main.CodexEvent(
        type="thread_status_changed", thread_id="session-1", turn_id="turn-1",
        usage={"context_tokens": 900, "max_context_tokens": 1000},
    ))
    assert chat.get("kimi_context_revision") == revision
    assert "10.0%" in dialog.information_list.GetString(0)
    dialog.Close()
    wx_app.Yield()
