import main


def test_voice_input_controller_callback_inserts_verbatim_into_editor(frame, monkeypatch):
    frame.input_edit.SetValue("")
    frame._speak_text_via_screen_reader = lambda _text: None
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *args, **kwargs: fn(*args, **kwargs))
    monkeypatch.setattr(main.wx.Window, "FindFocus", lambda: frame.input_edit)

    frame._voice_input.on_result("浠婂ぉ澶╂皵涓嶉敊浠婂ぉ澶╂皵涓嶉敊", main.MODE_DIRECT)

    assert frame.input_edit.GetValue() == "浠婂ぉ澶╂皵涓嶉敊浠婂ぉ澶╂皵涓嶉敊"


def test_voice_input_controller_duplicate_callbacks_insert_twice(frame, monkeypatch):
    frame.input_edit.SetValue("")
    frame._speak_text_via_screen_reader = lambda _text: None
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *args, **kwargs: fn(*args, **kwargs))
    monkeypatch.setattr(main.wx.Window, "FindFocus", lambda: frame.input_edit)

    frame._voice_input.on_result("浠婂ぉ澶╂皵涓嶉敊", main.MODE_DIRECT)
    frame._voice_input.on_result("浠婂ぉ澶╂皵涓嶉敊", main.MODE_DIRECT)

    assert frame.input_edit.GetValue() == "浠婂ぉ澶╂皵涓嶉敊浠婂ぉ澶╂皵涓嶉敊"


def test_e2e_ctrl_right_from_input_walks_older_chats(frame):
    frame.current_chat_id = "chat-current"
    frame._current_chat_state["id"] = "chat-current"
    frame._current_chat_state["title"] = "当前聊天"
    frame._current_chat_state["turns"] = [{"question": "当前问题", "answer_md": "当前回答"}]
    frame._current_chat_state["updated_at"] = 4.0
    frame.archived_chats = [
        {"id": "chat-a", "title": "聊天A", "turns": [], "created_at": 1.0, "updated_at": 1.0},
        {"id": "chat-b", "title": "聊天B", "turns": [], "created_at": 2.0, "updated_at": 2.0},
        {"id": "chat-c", "title": "聊天C", "turns": [], "created_at": 3.0, "updated_at": 3.0},
    ]
    frame._refresh_history()
    frame.input_edit.SetFocus()

    class E:
        def __init__(self):
            self.skipped = 0

        def GetKeyCode(self):
            return main.wx.WXK_RIGHT

        def ControlDown(self):
            return True

        def AltDown(self):
            return False

        def Skip(self):
            self.skipped += 1

    first = E()
    second = E()

    frame._on_input_key_down(first)
    assert frame.current_chat_id == "chat-c"
    frame._on_input_key_down(second)
    assert frame.current_chat_id == "chat-b"
    assert first.skipped == 1
    assert second.skipped == 1


def test_e2e_ctrl_right_from_input_reaches_pinned_and_older_chats(frame):
    frame.current_chat_id = "chat-e"
    frame._current_chat_state["id"] = "chat-e"
    frame._current_chat_state["title"] = "聊天E"
    frame._current_chat_state["turns"] = [{"question": "当前问题", "answer_md": "当前回答"}]
    frame._current_chat_state["updated_at"] = 6.0
    frame.archived_chats = [
        {"id": "chat-b", "title": "聊天B", "turns": [], "created_at": 4.0, "updated_at": 4.0},
        {"id": "chat-f", "title": "置顶F", "turns": [], "created_at": 5.0, "updated_at": 5.0, "pinned": True},
        {"id": "chat-c", "title": "置顶C", "turns": [], "created_at": 3.0, "updated_at": 3.0, "pinned": True},
        {"id": "chat-a", "title": "聊天A", "turns": [], "created_at": 2.0, "updated_at": 2.0},
        {"id": "chat-d", "title": "聊天D", "turns": [], "created_at": 2.0, "updated_at": 2.0},
        {"id": "chat-g", "title": "聊天G", "turns": [], "created_at": 9.0, "updated_at": 9.0},
    ]
    frame._refresh_history()
    frame.input_edit.SetFocus()

    class E:
        def __init__(self):
            self.skipped = 0

        def GetKeyCode(self):
            return main.wx.WXK_RIGHT

        def ControlDown(self):
            return True

        def AltDown(self):
            return False

        def Skip(self):
            self.skipped += 1

    events = [E() for _ in range(6)]
    seen = [frame.current_chat_id]

    for event in events:
        frame._on_input_key_down(event)
        seen.append(frame.current_chat_id)

    assert seen == ["chat-e", "chat-f", "chat-c", "chat-g", "chat-b", "chat-a", "chat-d"]
    assert all(event.skipped == 1 for event in events)
