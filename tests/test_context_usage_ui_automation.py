import ctypes

import main


def _send_listbox_key(window, key_code):
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    wm_keydown = 0x0100
    wm_keyup = 0x0101
    scan_codes = {
        main.wx.WXK_UP: 0x48,
        main.wx.WXK_DOWN: 0x50,
        main.wx.WXK_HOME: 0x47,
        main.wx.WXK_END: 0x4F,
    }
    virtual_keys = {
        main.wx.WXK_UP: 0x26,
        main.wx.WXK_DOWN: 0x28,
        main.wx.WXK_HOME: 0x24,
        main.wx.WXK_END: 0x23,
    }
    scan = scan_codes.get(key_code, 0)
    virtual_key = virtual_keys.get(key_code, int(key_code))
    down_lparam = 1 | (scan << 16)
    up_lparam = 1 | (scan << 16) | (1 << 30) | (1 << 31)
    hwnd = int(window.GetHandle())
    user32.SendMessageW(hwnd, wm_keydown, virtual_key, down_lparam)
    user32.SendMessageW(hwnd, wm_keyup, virtual_key, up_lparam)


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


def test_ui_automation_context_usage_row_is_fixed_above_answers(frame):
    frame.Show()
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_session_turns = [
        {"question": "first question", "answer_md": "first answer", "model": "openai/gpt-5.2", "created_at": 1.0},
        {"question": "second question", "answer_md": "second answer", "model": "openai/gpt-5.2", "created_at": 2.0},
    ]
    frame._current_chat_state = {
        "id": "chat-current",
        "turns": frame.active_session_turns,
        "context_usage": _usage(used=1536),
    }

    frame._render_answer_list()

    rows = list(frame.answer_list.GetStrings())
    assert rows[:6] == ["2k / 128k", "当前模型：openai/gpt-5.2", "我", "first question", "小诸葛", "first answer"]
    assert frame.answer_meta[0][0] == "context_usage"
    assert frame.answer_meta[1][0] == "current_model"
    assert frame.answer_meta[2][0] == "user"
    assert frame.answer_meta[3][0] == "question"
    assert frame.answer_meta[4][0] == "ai"

    frame._focus_latest_answer()

    selected = frame.answer_list.GetSelection()
    assert selected != 0
    assert frame.answer_meta[selected][0] == "answer"


def test_ui_automation_answer_list_arrow_keys_can_select_context_usage_row(frame):
    frame.Show()
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_session_turns = [
        {"question": "first question", "answer_md": "first answer", "model": "openai/gpt-5.2", "created_at": 1.0}
    ]
    frame._current_chat_state = {
        "id": "chat-current",
        "turns": frame.active_session_turns,
        "context_usage": _usage(used=1536),
    }

    frame._render_answer_list()
    frame.answer_list.SetFocus()
    frame.answer_list.SetSelection(2)

    up = main.wx.KeyEvent(main.wx.wxEVT_KEY_DOWN)
    up.SetKeyCode(main.wx.WXK_UP)

    assert frame.answer_list.ProcessEvent(up)
    assert frame.answer_list.GetSelection() == 1
    assert frame.answer_meta[1][0] == "current_model"
    assert frame.answer_list.GetString(1) == "当前模型：openai/gpt-5.2"

    assert frame.answer_list.ProcessEvent(up)
    assert frame.answer_list.GetSelection() == 0
    assert frame.answer_meta[0][0] == "context_usage"
    assert frame.answer_list.GetString(0) == "2k / 128k"

    down = main.wx.KeyEvent(main.wx.wxEVT_KEY_DOWN)
    down.SetKeyCode(main.wx.WXK_DOWN)

    assert frame.answer_list.ProcessEvent(down)
    assert frame.answer_list.GetSelection() == 1
    assert frame.answer_meta[1][0] == "current_model"


def test_ui_automation_native_listbox_arrow_key_reaches_context_usage_row(frame, wx_app):
    frame.Show()
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_session_turns = [
        {"question": "first question", "answer_md": "first answer", "model": "openai/gpt-5.2", "created_at": 1.0}
    ]
    frame._current_chat_state = {
        "id": "chat-current",
        "turns": frame.active_session_turns,
        "context_usage": _usage(used=1536),
    }

    frame._render_answer_list()
    frame.answer_list.SetSelection(2)
    frame.answer_list.SetFocusFromKbd()
    wx_app.Yield()

    _send_listbox_key(frame.answer_list, main.wx.WXK_UP)
    wx_app.Yield()

    assert frame.answer_list.GetSelection() == 1
    assert frame.answer_meta[1][0] == "current_model"
    assert frame.answer_list.GetString(1) == "当前模型：openai/gpt-5.2"

    _send_listbox_key(frame.answer_list, main.wx.WXK_UP)
    wx_app.Yield()

    assert frame.answer_list.GetSelection() == 0
    assert frame.answer_meta[0][0] == "context_usage"
    assert frame.answer_list.GetString(0) == "2k / 128k"

    _send_listbox_key(frame.answer_list, main.wx.WXK_DOWN)
    wx_app.Yield()

    assert frame.answer_list.GetSelection() == 1
    assert frame.answer_meta[1][0] == "current_model"


def test_ui_automation_history_switch_uses_stored_context_usage_then_cli_unknown(frame):
    frame.Show()
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.active_session_turns = [
        {"question": "active q", "answer_md": "active a", "model": "openai/gpt-5.2", "created_at": 3.0}
    ]
    frame._current_chat_state = {
        "id": "chat-active",
        "title": "active",
        "turns": frame.active_session_turns,
        "context_usage": _usage(used=4096),
    }
    frame.archived_chats = [
        {
            "id": "chat-codex",
            "title": "codex",
            "turns": [{"question": "codex q", "answer_md": "codex a", "model": "codex/main", "created_at": 1.0}],
        },
        {
            "id": "chat-stored",
            "title": "stored",
            "turns": [{"question": "stored q", "answer_md": "stored a", "model": "codex/main", "created_at": 2.0}],
            "context_usage": _usage(used=44176, window=0, source="codex", exact=True, model="gpt-5-codex"),
        },
    ]

    frame.view_mode = "active"
    frame._render_answer_list()
    assert frame.answer_list.GetString(0) == "4k / 128k"

    frame.view_mode = "history"
    frame.view_history_id = "chat-codex"
    frame._render_answer_list()
    assert frame.answer_list.GetString(0) == "暂无"

    frame.view_history_id = "chat-stored"
    frame._render_answer_list()
    assert frame.answer_list.GetString(0) == "暂无"


def test_ui_automation_context_row_selection_survives_visible_history_usage_refresh(frame, monkeypatch):
    frame.Show()
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.active_session_turns = [
        {"question": "active q", "answer_md": "active a", "model": "openai/gpt-5.2", "created_at": 1.0}
    ]
    frame._current_chat_state = {"id": "chat-active", "turns": frame.active_session_turns}
    frame.archived_chats = [
        {
            "id": "chat-visible",
            "title": "visible",
            "turns": [
                {
                    "question": "q",
                    "answer_md": "done",
                    "model": "codex/main",
                    "created_at": 1.0,
                    "codex_turn_id": "turn-1",
                    "request_status": "done",
                }
            ],
        }
    ]
    frame.view_mode = "history"
    frame.view_history_id = "chat-visible"
    monkeypatch.setattr(frame, "_call_later_if_alive", lambda *_args, **_kwargs: None)

    frame._render_answer_list()
    frame.answer_list.SetSelection(0)
    assert frame.answer_list.GetString(0) == "暂无"

    frame._on_codex_event_for_chat(
        "chat-visible",
        main.CodexEvent(type="token_count", thread_id="thread-1", turn_id="turn-1", usage=_usage(used=44176, window=258400, source="codex", exact=True, model="gpt-5-codex")),
    )

    assert frame.answer_list.GetString(0) == "44k / 258k"
    assert frame.answer_list.GetSelection() == 0
    assert frame.answer_meta[0][0] == "context_usage"
