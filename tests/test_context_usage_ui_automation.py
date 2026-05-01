import main


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
    assert rows[:5] == ["上下文：2K/128K，1.2%已用", "我", "first question", "小诸葛", "first answer"]
    assert frame.answer_meta[0][0] == "context_usage"
    assert frame.answer_meta[1][0] == "user"
    assert frame.answer_meta[3][0] == "ai"

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

    class _KeyEvent:
        def __init__(self, key):
            self.key = key
            self.skipped = 0

        def GetKeyCode(self):
            return self.key

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            self.skipped += 1

        def StopPropagation(self):
            return None

    frame._render_answer_list()
    frame.answer_list.SetFocus()
    frame.answer_list.SetSelection(1)

    up = _KeyEvent(main.wx.WXK_UP)
    frame._on_answer_key_down(up)

    assert up.skipped == 0
    assert frame.answer_list.GetSelection() == 0
    assert frame.answer_meta[0][0] == "context_usage"
    assert frame.answer_list.GetString(0) == "上下文：2K/128K，1.2%已用"

    down = _KeyEvent(main.wx.WXK_DOWN)
    frame._on_answer_key_down(down)

    assert down.skipped == 0
    assert frame.answer_list.GetSelection() == 1
    assert frame.answer_meta[1][0] == "user"


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
    assert frame.answer_list.GetString(0) == "上下文：4K/128K，3.2%已用"

    frame.view_mode = "history"
    frame.view_history_id = "chat-codex"
    frame._render_answer_list()
    assert frame.answer_list.GetString(0) == "上下文：刷新中"

    frame.view_history_id = "chat-stored"
    frame._render_answer_list()
    assert frame.answer_list.GetString(0) == "上下文：44K/未知"


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
    assert frame.answer_list.GetString(0) == "上下文：刷新中"

    frame._on_codex_event_for_chat(
        "chat-visible",
        main.CodexEvent(type="token_count", thread_id="thread-1", turn_id="turn-1", usage=_usage(used=44176, window=0, source="codex", exact=True, model="gpt-5-codex")),
    )

    assert frame.answer_list.GetString(0) == "上下文：44K/未知"
    assert frame.answer_list.GetSelection() == 0
    assert frame.answer_meta[0][0] == "context_usage"
