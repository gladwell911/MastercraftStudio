import main


class _EnterEvent:
    def GetKeyCode(self):
        return main.wx.WXK_RETURN

    def Skip(self):
        return None


def test_ui_automation_history_enter_allows_switch_during_pending_reply(frame, monkeypatch):
    frame.Show()
    frame.is_running = True
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state.update(
        {
            "id": "chat-current",
            "title": "当前聊天",
            "turns": [
                {
                    "question": "当前问题",
                    "answer_md": main.REQUESTING_TEXT,
                    "model": "openai/gpt-5.2",
                    "created_at": 4.0,
                    "request_status": "pending",
                }
            ],
        }
    )
    frame.active_session_turns = list(frame._current_chat_state["turns"])
    frame.archived_chats = [
        {
            "id": "hist-1",
            "title": "历史会话",
            "pinned": False,
            "created_at": 1.0,
            "updated_at": 1.0,
            "turns": [{"question": "历史问题", "answer_md": "历史回答", "model": "openai/gpt-5.2", "created_at": 1.0}],
        }
    ]
    shown = {"dialog": 0}
    monkeypatch.setattr(frame, "_show_ok_dialog", lambda *_args, **_kwargs: shown.__setitem__("dialog", shown["dialog"] + 1))

    frame._refresh_history("hist-1")
    frame.history_list.SetFocus()
    frame.history_list.SetSelection(frame.history_ids.index("hist-1"))

    frame._on_history_key_down(_EnterEvent())

    assert shown["dialog"] == 0
    assert frame.view_mode == "history"
    assert frame.view_history_id == "hist-1"
    assert frame.answer_list.GetString(1) == "历史问题"
    assert frame.answer_list.HasFocus()


def test_ui_automation_history_enter_can_return_to_empty_new_chat(frame):
    frame.Show()
    frame.active_chat_id = "chat-new"
    frame.current_chat_id = "chat-new"
    frame._current_chat_state.update(
        {
            "id": "chat-new",
            "title": main.EMPTY_CURRENT_CHAT_TITLE,
            "title_manual": False,
            "turns": [],
        }
    )
    frame.active_session_turns = []
    frame.archived_chats = [
        {
            "id": "hist-1",
            "title": "历史会话",
            "pinned": False,
            "created_at": 1.0,
            "updated_at": 1.0,
            "turns": [{"question": "历史问题", "answer_md": "历史回答", "model": "openai/gpt-5.2", "created_at": 1.0}],
        }
    ]

    frame._refresh_history("hist-1")
    frame.history_list.SetFocus()
    frame.history_list.SetSelection(frame.history_ids.index("hist-1"))

    frame._on_history_key_down(_EnterEvent())

    assert frame.view_mode == "history"
    assert frame.view_history_id == "hist-1"
    assert frame.answer_list.HasFocus()

    frame.history_list.SetSelection(frame.history_ids.index("chat-new"))
    frame.history_list.SetFocus()
    frame._on_history_key_down(_EnterEvent())

    assert frame.view_mode == "active"
    assert frame.view_history_id is None
    assert frame.current_chat_id == "chat-new"
    assert frame.active_chat_id == "chat-new"
    assert "历史问题" not in list(frame.answer_list.GetStrings())
    assert frame.answer_list.HasFocus()
