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

    assert seen == ["chat-e", "chat-g", "chat-f", "chat-b", "chat-c", "chat-a", "chat-d"]
    assert all(event.skipped == 1 for event in events)


def test_e2e_enter_on_notes_notebook_opens_detail_without_send(frame, monkeypatch):
    notebook = frame.notes_store.create_notebook("e2e notebook")
    frame._notes_select_notebook(notebook.id, view="notes_list")
    frame.notes_notebook_list.SetSelection(frame._notes_notebook_ids.index(notebook.id))
    frame._on_notes_notebook_selected(None)

    seen = {"send": 0, "skip": 0}
    monkeypatch.setattr(frame.notes_notebook_list, "HasFocus", lambda: True)
    monkeypatch.setattr(frame.notes_entry_list, "HasFocus", lambda: False)
    monkeypatch.setattr(frame.notes_editor, "HasFocus", lambda: False)
    monkeypatch.setattr(frame, "_trigger_send", lambda: seen.__setitem__("send", seen["send"] + 1))

    class E:
        def GetKeyCode(self):
            return main.wx.WXK_RETURN

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            seen["skip"] += 1

    frame._on_char_hook(E())

    assert frame.notes_controller.notes_view == "note_detail"
    assert frame.notes_controller.active_notebook_id == notebook.id
    assert seen["send"] == 0


def test_e2e_notes_same_slot_navigation_and_backspace(frame, monkeypatch):
    notebook = frame.notes_store.create_notebook("e2e same slot notebook")
    entry = frame.notes_store.create_entry(notebook.id, "e2e same slot entry", source="manual")
    frame._notes_select_notebook(notebook.id, view="notes_list")
    frame.notes_notebook_list.SetSelection(frame._notes_notebook_ids.index(notebook.id))
    frame._on_notes_notebook_selected(None)

    monkeypatch.setattr(frame.notes_notebook_list, "HasFocus", lambda: True)
    monkeypatch.setattr(frame.notes_entry_list, "HasFocus", lambda: False)
    frame._on_notes_key_down(type("Evt", (), {
        "GetKeyCode": lambda self: main.wx.WXK_RETURN,
        "ControlDown": lambda self: False,
        "AltDown": lambda self: False,
        "Skip": lambda self: None,
    })())

    assert frame.notes_detail_panel.IsShown()
    assert frame.notes_list_panel.IsShown()
    assert frame.notes_entry_list.GetCount() >= 1

    frame.notes_entry_list.SetSelection(frame._notes_entry_ids.index(entry.id))
    monkeypatch.setattr(frame.notes_notebook_list, "HasFocus", lambda: False)
    monkeypatch.setattr(frame.notes_entry_list, "HasFocus", lambda: True)
    frame._on_notes_key_down(type("Evt", (), {
        "GetKeyCode": lambda self: main.wx.WXK_RETURN,
        "ControlDown": lambda self: False,
        "AltDown": lambda self: False,
        "Skip": lambda self: None,
    })())

    assert frame.notes_edit_panel.IsShown()
    assert not frame.notes_detail_panel.IsShown()
    assert not frame.notes_entry_list.IsEnabled()
    assert frame.notes_list_panel.IsShown()
    assert frame.notes_notebook_list.IsEnabled()

    frame._notes_select_notebook(notebook.id, view="note_detail")
    monkeypatch.setattr(frame.notes_entry_list, "HasFocus", lambda: True)
    frame._on_notes_key_down(type("Evt", (), {
        "GetKeyCode": lambda self: main.wx.WXK_BACK,
        "ControlDown": lambda self: False,
        "AltDown": lambda self: False,
        "Skip": lambda self: None,
    })())

    assert frame.notes_list_panel.IsShown()
    assert not frame.notes_detail_panel.IsShown()


def test_e2e_notes_ctrl_enter_save_returns_focus_to_entry_list(frame, monkeypatch):
    notebook = frame.notes_store.create_notebook("e2e save focus notebook")
    entry = frame.notes_store.create_entry(notebook.id, "e2e before save", source="manual")
    frame._notes_select_notebook(notebook.id, view="note_detail")
    frame.notes_entry_list.SetSelection(frame._notes_entry_ids.index(entry.id))
    monkeypatch.setattr(frame.notes_notebook_list, "HasFocus", lambda: False)
    monkeypatch.setattr(frame.notes_entry_list, "HasFocus", lambda: True)

    frame._on_notes_key_down(type("Evt", (), {
        "GetKeyCode": lambda self: main.wx.WXK_RETURN,
        "ControlDown": lambda self: False,
        "AltDown": lambda self: False,
        "Skip": lambda self: None,
    })())

    frame.notes_editor.SetValue("e2e after save")
    frame._on_notes_editor_changed(None)
    frame._on_notes_key_down(type("Evt", (), {
        "GetKeyCode": lambda self: main.wx.WXK_RETURN,
        "ControlDown": lambda self: True,
        "AltDown": lambda self: False,
        "Skip": lambda self: None,
    })())

    assert frame.notes_controller.notes_view == "note_detail"
    assert frame.notes_controller.active_entry_id == entry.id
    assert frame.notes_entry_list.HasFocus()


def test_e2e_notes_alt_x_creates_blank_entry_from_detail_list(frame, monkeypatch):
    notebook = frame.notes_store.create_notebook("e2e alt x notebook")
    existing = frame.notes_store.create_entry(notebook.id, "existing e2e entry", source="manual")
    frame._notes_select_notebook(notebook.id, view="note_detail")
    frame.notes_entry_list.SetSelection(frame._notes_entry_ids.index(existing.id))
    monkeypatch.setattr(frame.notes_notebook_list, "HasFocus", lambda: False)
    monkeypatch.setattr(frame.notes_entry_list, "HasFocus", lambda: True)

    frame._on_notes_key_down(type("Evt", (), {
        "GetKeyCode": lambda self: ord("X"),
        "ControlDown": lambda self: False,
        "AltDown": lambda self: True,
        "Skip": lambda self: None,
    })())

    assert frame.notes_controller.notes_view == "note_edit"
    assert frame.notes_controller.active_entry_id == ""
    assert frame.notes_editor.GetValue() == ""
    assert frame.notes_editor.HasFocus()
    entries = frame.notes_store.list_entries(notebook.id)
    assert len(entries) == 1
    assert entries[0].id == existing.id


def test_e2e_notes_ctrl_c_copies_notebook_and_selected_entry(frame, monkeypatch):
    notebook = frame.notes_store.create_notebook("e2e copy notebook")
    entry = frame.notes_store.create_entry(notebook.id, "e2e copied first", source="manual")
    frame.notes_store.create_entry(notebook.id, "e2e copied second", source="manual")
    copied = {"texts": []}
    monkeypatch.setattr(frame, "_set_clipboard_text", lambda text: copied["texts"].append(text) or True)

    frame._notes_select_notebook(notebook.id, view="notes_list")
    frame.notes_notebook_list.SetSelection(frame._notes_notebook_ids.index(notebook.id))
    monkeypatch.setattr(frame.notes_notebook_list, "HasFocus", lambda: True)
    monkeypatch.setattr(frame.notes_entry_list, "HasFocus", lambda: False)
    frame._on_notes_key_down(type("Evt", (), {
        "GetKeyCode": lambda self: ord("C"),
        "ControlDown": lambda self: True,
        "AltDown": lambda self: False,
        "Skip": lambda self: None,
    })())

    frame._notes_select_notebook(notebook.id, view="note_detail")
    frame.notes_entry_list.SetSelection(frame._notes_entry_ids.index(entry.id))
    monkeypatch.setattr(frame.notes_notebook_list, "HasFocus", lambda: False)
    monkeypatch.setattr(frame.notes_entry_list, "HasFocus", lambda: True)
    frame._on_notes_key_down(type("Evt", (), {
        "GetKeyCode": lambda self: ord("C"),
        "ControlDown": lambda self: True,
        "AltDown": lambda self: False,
        "Skip": lambda self: None,
    })())

    assert copied["texts"][0] == "\n\n".join(item.content for item in frame.notes_store.list_entries(notebook.id))
    assert copied["texts"][1] == "e2e copied first"
