import ctypes

import main
import wx


def _activate_frame(frame, wx_app):
    frame.Show()
    frame.Raise()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetForegroundWindow(int(frame.GetHandle()))
    wx_app.Yield()


def _send_listbox_key(window, key_code):
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    wm_keydown = 0x0100
    wm_keyup = 0x0101
    scan_codes = {
        main.wx.WXK_UP: 0x48,
        main.wx.WXK_DOWN: 0x50,
        main.wx.WXK_RETURN: 0x1C,
        main.wx.WXK_NUMPAD_ENTER: 0x1C,
    }
    virtual_keys = {
        main.wx.WXK_UP: 0x26,
        main.wx.WXK_DOWN: 0x28,
        main.wx.WXK_RETURN: 0x0D,
        main.wx.WXK_NUMPAD_ENTER: 0x0D,
    }
    scan = scan_codes.get(key_code, 0)
    virtual_key = virtual_keys.get(key_code, int(key_code))
    down_lparam = 1 | (scan << 16)
    up_lparam = 1 | (scan << 16) | (1 << 30) | (1 << 31)
    hwnd = int(window.GetHandle())
    user32.SendMessageW(hwnd, wm_keydown, virtual_key, down_lparam)
    user32.SendMessageW(hwnd, wm_keyup, virtual_key, up_lparam)


class _EnterEvent:
    def __init__(self):
        self.skipped = 0

    def GetKeyCode(self):
        return main.wx.WXK_RETURN

    def Skip(self):
        self.skipped += 1


class _MenuEvent:
    def GetKeyCode(self):
        return main.wx.WXK_MENU

    def Skip(self):
        raise AssertionError("menu key should be handled")


class _DeleteEvent:
    def GetKeyCode(self):
        return main.wx.WXK_DELETE

    def Skip(self):
        raise AssertionError("Delete should be handled")


class _EscapeEvent:
    def GetKeyCode(self):
        return main.wx.WXK_ESCAPE

    def Skip(self):
        raise AssertionError("Escape should be handled")


class _DialogEnterEvent:
    def __init__(self):
        self.skipped = 0

    def GetKeyCode(self):
        return main.wx.WXK_RETURN

    def Skip(self):
        self.skipped += 1


def _seed_command(frame, *, title, content):
    return frame.common_commands_store.create_command(
        main.CommonCommandCreate(title=title, content=content)
    )


def test_ui_automation_alt_m_opens_common_commands(frame, wx_app):
    _activate_frame(frame, wx_app)
    _seed_command(frame, title="First", content="echo first")

    event = wx.CommandEvent(wx.wxEVT_MENU, int(frame._common_commands_menu_id))
    event.SetEventObject(frame)
    frame.ProcessEvent(event)
    wx_app.Yield()

    dialog = frame.common_commands_dialog
    assert dialog is not None
    assert dialog.IsShown()
    assert dialog.common_commands_list.GetCount() == 1
    assert dialog.common_commands_list.GetString(0) == "First"


def test_ui_automation_enter_on_selected_command_sends_selected_content(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    _seed_command(frame, title="你好", content="你好")
    assert frame._show_common_commands_surface() is True
    dialog = frame.common_commands_dialog
    dialog.common_commands_list.SetSelection(0)
    dialog.common_commands_list.SetFocus()
    frame.input_edit.SetValue("keep me")
    sent = []

    def _submit(question, source="local", model=None, chat_id=""):
        sent.append((question, source, model, chat_id, frame.input_edit.GetValue()))
        return True, ""

    monkeypatch.setattr(frame, "_submit_question", _submit)

    event = _EnterEvent()
    frame._on_common_commands_key_down(event)
    wx_app.Yield()

    assert sent == [("你好", "local", None, "", "keep me")]
    assert frame.input_edit.GetValue() == "keep me"
    assert event.skipped == 0


def test_ui_automation_common_command_send_failure_shows_message_and_keeps_selected_content(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    _seed_command(frame, title="Send", content="echo send me")
    assert frame._show_common_commands_surface() is True
    dialog = frame.common_commands_dialog
    dialog.common_commands_list.SetSelection(0)
    dialog.common_commands_list.SetFocus()
    frame.input_edit.SetValue("keep me")
    frame.send_button.Enable(False)
    seen = {}

    monkeypatch.setattr(frame, "_submit_question", lambda *_args, **_kwargs: (False, "submit failed"))
    monkeypatch.setattr(main.wx, "MessageBox", lambda message, title, flags: seen.update({"message": message, "title": title, "flags": flags}))

    assert frame._send_selected_common_command() is False
    assert frame.input_edit.GetValue() == "keep me"
    assert seen["message"] == "submit failed"


def test_ui_automation_common_command_enter_skips_when_submit_fails(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    _seed_command(frame, title="Send", content="echo send me")
    assert frame._show_common_commands_surface() is True
    dialog = frame.common_commands_dialog
    dialog.common_commands_list.SetSelection(0)
    dialog.common_commands_list.SetFocus()
    frame.input_edit.SetValue("keep me")
    frame.send_button.Enable(False)
    monkeypatch.setattr(frame, "_submit_question", lambda *_args, **_kwargs: (False, ""))
    event = _EnterEvent()

    frame._on_common_commands_key_down(event)
    wx_app.Yield()

    assert event.skipped == 1
    assert frame.input_edit.GetValue() == "keep me"


def test_ui_automation_enter_submits_selected_command_when_send_button_disabled(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    _seed_command(frame, title="Send", content="echo send me")
    assert frame._show_common_commands_surface() is True
    dialog = frame.common_commands_dialog
    dialog.common_commands_list.SetSelection(0)
    dialog.common_commands_list.SetFocus()
    frame.input_edit.SetValue("")
    frame.send_button.Enable(False)
    submitted = []

    def _submit(question, source="local", model=None, chat_id=""):
        submitted.append((question, source, model, chat_id))
        return True, ""

    monkeypatch.setattr(frame, "_submit_question", _submit)
    event = _EnterEvent()

    frame._on_common_commands_key_down(event)
    wx_app.Yield()

    assert submitted == [("echo send me", "local", None, "")]
    assert frame.input_edit.GetValue() == ""
    assert event.skipped == 0


def test_ui_automation_char_enter_on_selected_command_sends_selected_content(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    _seed_command(frame, title="Send", content="echo send me")
    assert frame._show_common_commands_surface() is True
    dialog = frame.common_commands_dialog
    dialog.common_commands_list.SetSelection(0)
    dialog.common_commands_list.SetFocus()
    submitted = []

    def _submit(question, source="local", model=None, chat_id=""):
        submitted.append((question, source, model, chat_id))
        return True, ""

    monkeypatch.setattr(frame, "_submit_question", _submit)

    event = main.wx.KeyEvent(main.wx.wxEVT_CHAR)
    event.SetKeyCode(main.wx.WXK_RETURN)
    dialog.common_commands_list.ProcessEvent(event)
    wx_app.Yield()

    assert submitted == [("echo send me", "local", None, "")]


def test_ui_automation_arrow_navigation_then_enter_sends_newly_selected_command(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    _seed_command(frame, title="First", content="echo first")
    _seed_command(frame, title="Second", content="echo second")
    assert frame._show_common_commands_surface() is True
    dialog = frame.common_commands_dialog
    dialog.common_commands_list.SetSelection(0)
    dialog.common_commands_list.SetFocusFromKbd()
    wx_app.Yield()
    submitted = []

    def _submit(question, source="local", model=None, chat_id=""):
        submitted.append((question, source, model, chat_id))
        return True, ""

    monkeypatch.setattr(frame, "_submit_question", _submit)

    _send_listbox_key(dialog.common_commands_list, main.wx.WXK_DOWN)
    wx_app.Yield()
    _send_listbox_key(dialog.common_commands_list, main.wx.WXK_RETURN)
    wx_app.Yield()

    assert dialog.common_commands_list.GetSelection() == 1
    assert dialog.selected_command_id() == dialog.common_commands_list_ids[1]
    assert submitted == [("echo second", "local", None, "")]


def test_ui_automation_dialog_char_hook_enter_sends_selected_command_when_list_has_focus(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    _seed_command(frame, title="Send", content="echo send me")
    assert frame._show_common_commands_surface() is True
    dialog = frame.common_commands_dialog
    dialog.common_commands_list.SetSelection(0)
    dialog.common_commands_list.SetFocus()
    wx_app.Yield()
    submitted = []

    def _submit(question, source="local", model=None, chat_id=""):
        submitted.append((question, source, model, chat_id))
        return True, ""

    monkeypatch.setattr(frame, "_submit_question", _submit)
    event = _DialogEnterEvent()

    dialog._on_char_hook(event)
    wx_app.Yield()

    assert submitted == [("echo send me", "local", None, "")]
    assert event.skipped == 0


def test_ui_automation_menu_key_opens_command_menu_without_disturbing_selection(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    first = _seed_command(frame, title="First", content="echo first")
    _seed_command(frame, title="Second", content="echo second")
    assert frame._show_common_commands_surface() is True
    dialog = frame.common_commands_dialog
    dialog.common_commands_list.SetSelection(dialog.common_commands_list_ids.index(first.id))
    dialog.common_commands_list.SetFocus()
    opened = []
    monkeypatch.setattr(dialog, "PopupMenu", lambda menu: opened.append([item.GetItemLabelText() for item in menu.GetMenuItems() if not item.IsSeparator()]) or True)

    frame._on_common_commands_key_down(_MenuEvent())
    wx_app.Yield()

    assert opened == [["添加", "编辑", "删除", "置顶", "向上移动", "向下移动"]]
    assert dialog.common_commands_list.GetSelection() == dialog.common_commands_list_ids.index(first.id)


def test_ui_automation_common_command_dialog_menu_event_runs_selected_action(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    first = _seed_command(frame, title="First", content="echo first")
    assert frame._show_common_commands_surface() is True
    dialog = frame.common_commands_dialog
    dialog.common_commands_list.SetSelection(dialog.common_commands_list_ids.index(first.id))
    dialog.common_commands_list.SetFocus()
    called = []

    monkeypatch.setattr(frame, "_edit_selected_common_command", lambda: called.append("edit") or True)

    def _popup(menu):
        edit_item = next(item for item in menu.GetMenuItems() if item.GetItemLabelText() == "编辑")
        event = wx.CommandEvent(wx.wxEVT_MENU, edit_item.GetId())
        event.SetEventObject(menu)
        menu.ProcessEvent(event)
        return True

    monkeypatch.setattr(dialog, "PopupMenu", _popup)

    assert frame._show_common_commands_menu() is True
    wx_app.Yield()

    assert called == ["edit"]


def test_ui_automation_escape_closes_common_commands_dialog_from_add_button(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    _seed_command(frame, title="First", content="echo first")
    assert frame._show_common_commands_surface() is True
    dialog = frame.common_commands_dialog
    dialog.common_commands_add_button.SetFocus()
    wx_app.Yield()
    focused = []
    monkeypatch.setattr(frame, "_focus_input_box", lambda: focused.append("input") or True)

    dialog._on_char_hook(_EscapeEvent())
    wx_app.Yield()

    assert frame.common_commands_dialog is None
    assert focused == ["input"]


def test_ui_automation_delete_key_confirms_and_removes_selected_command(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    first = _seed_command(frame, title="First", content="echo first")
    second = _seed_command(frame, title="Second", content="echo second")
    assert frame._show_common_commands_surface() is True
    dialog = frame.common_commands_dialog
    dialog.common_commands_list.SetSelection(dialog.common_commands_list_ids.index(first.id))
    dialog.common_commands_list.SetFocus()
    monkeypatch.setattr(frame, "_confirm", lambda message, title="确认": True)

    frame._on_common_commands_key_down(_DeleteEvent())
    wx_app.Yield()

    snapshot = frame.common_commands_store.read_snapshot()
    assert [item.id for item in snapshot.commands] == [second.id]
    assert dialog.common_commands_list.GetCount() == 1
    assert dialog.common_commands_list.GetString(0) == "Second"
    assert dialog.common_commands_list.HasFocus()


def test_ui_automation_save_after_add_edit_restores_focus_and_selection(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    existing = _seed_command(frame, title="Existing", content="echo existing")
    assert frame._show_common_commands_surface() is True
    dialog = frame.common_commands_dialog

    class _AddDialog:
        def __init__(self, *_args, **_kwargs):
            pass

        def ShowModal(self):
            return main.wx.ID_OK

        def values(self):
            return ("Added", "echo added")

        def Destroy(self):
            pass

    monkeypatch.setattr(main, "CommonCommandEditDialog", _AddDialog)

    assert frame._add_common_command() is True
    wx_app.Yield()

    added_id = dialog.selected_command_id()
    assert dialog.common_commands_list.HasFocus()
    assert dialog.selected_command().title == "Added"

    class _EditDialog:
        def __init__(self, *_args, **_kwargs):
            pass

        def ShowModal(self):
            return main.wx.ID_OK

        def values(self):
            return ("Edited", "echo edited")

        def Destroy(self):
            pass

    monkeypatch.setattr(main, "CommonCommandEditDialog", _EditDialog)

    assert frame._edit_selected_common_command() is True
    wx_app.Yield()

    assert dialog.common_commands_list.HasFocus()
    assert dialog.selected_command_id() == added_id
    assert dialog.selected_command().title == "Edited"
    assert existing.id in [item.id for item in frame.common_commands_store.read_snapshot().commands]


def test_ui_automation_add_save_failure_shows_message_and_keeps_dialog_open(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    assert frame._show_common_commands_surface() is True
    dialog = frame.common_commands_dialog
    seen = {}

    class _AddDialog:
        def __init__(self, *_args, **_kwargs):
            pass

        def ShowModal(self):
            return main.wx.ID_OK

        def values(self):
            return ("Added", "echo added")

        def Destroy(self):
            pass

    def fail_create(_data):
        raise main.CommonCommandsWriteError(frame.common_commands_store.path, "failed to write common commands store")

    monkeypatch.setattr(main, "CommonCommandEditDialog", _AddDialog)
    monkeypatch.setattr(frame.common_commands_store, "create_command", fail_create)
    monkeypatch.setattr(main.wx, "MessageBox", lambda message, title, flags: seen.update({"message": message, "title": title, "flags": flags}))

    assert frame._add_common_command() is False
    wx_app.Yield()

    assert seen["title"] == "提示"
    assert "failed to write common commands store" in seen["message"]
    assert dialog.IsShown()
    assert dialog.common_commands_add_button.HasFocus()
    assert frame.GetStatusBar().GetStatusText() == "就绪"


def test_ui_automation_common_commands_refresh_does_not_steal_focus_when_list_has_focus(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    first = _seed_command(frame, title="First", content="echo first")
    second = _seed_command(frame, title="Second", content="echo second")
    assert frame._show_common_commands_surface() is True
    dialog = frame.common_commands_dialog
    dialog.common_commands_list.SetSelection(dialog.common_commands_list_ids.index(first.id))
    dialog.common_commands_list.SetFocus()
    wx_app.Yield()

    original_set_selection = dialog.common_commands_list.SetSelection
    monkeypatch.setattr(dialog.common_commands_list, "SetFocus", lambda: (_ for _ in ()).throw(AssertionError("refresh should not steal focus")))

    def checked_set_selection(idx):
        if idx == dialog.common_commands_list.GetSelection():
            raise AssertionError("refresh should not repeat unchanged selection")
        return original_set_selection(idx)

    monkeypatch.setattr(dialog.common_commands_list, "SetSelection", checked_set_selection)

    version = next(item.version for item in frame.common_commands_store.read_snapshot().commands if item.id == second.id)
    frame.common_commands_store.update_command(
        second.id,
        main.CommonCommandUpdate(
            expected_version=version,
            title="Second updated",
            content="echo second",
        ),
    )
    assert dialog.refresh_commands() is True
    wx_app.Yield()

    assert dialog.common_commands_list.HasFocus()
    assert dialog.selected_command_id() == first.id
    assert "Second updated" in [dialog.common_commands_list.GetString(idx) for idx in range(dialog.common_commands_list.GetCount())]


def test_ui_automation_remote_delete_refresh_keeps_list_focus_and_moves_selection(frame, wx_app):
    _activate_frame(frame, wx_app)
    first = _seed_command(frame, title="First", content="echo first")
    second = _seed_command(frame, title="Second", content="echo second")
    third = _seed_command(frame, title="Third", content="echo third")
    assert frame._show_common_commands_surface() is True
    dialog = frame.common_commands_dialog
    dialog.common_commands_list.SetSelection(dialog.common_commands_list_ids.index(second.id))
    dialog.common_commands_list.SetFocus()
    wx_app.Yield()

    frame.common_commands_store.delete_command(second.id, expected_version=second.version)
    assert dialog.refresh_commands() is True
    wx_app.Yield()

    assert dialog.common_commands_list.HasFocus()
    assert dialog.selected_command_id() == third.id
    assert [dialog.common_commands_list.GetString(idx) for idx in range(dialog.common_commands_list.GetCount())] == [
        "First",
        "Third",
    ]


def test_ui_automation_pin_and_reorder_keep_focus_and_update_sections(frame, wx_app):
    _activate_frame(frame, wx_app)
    first = _seed_command(frame, title="First", content="echo first")
    second = _seed_command(frame, title="Second", content="echo second")
    third = _seed_command(frame, title="Third", content="echo third")
    assert frame._show_common_commands_surface() is True
    dialog = frame.common_commands_dialog
    dialog.common_commands_list.SetSelection(dialog.common_commands_list_ids.index(second.id))
    dialog.common_commands_list.SetFocus()
    wx_app.Yield()

    assert frame._toggle_selected_common_command_pin() is True
    wx_app.Yield()

    assert dialog.common_commands_list.HasFocus()
    assert dialog.selected_command_id() == second.id
    assert [dialog.common_commands_list.GetString(idx) for idx in range(dialog.common_commands_list.GetCount())] == [
        "Second",
        "First",
        "Third",
    ]

    dialog.common_commands_list.SetSelection(dialog.common_commands_list_ids.index(third.id))
    dialog.common_commands_list.SetFocus()
    wx_app.Yield()

    assert frame._move_selected_common_command_up() is True
    wx_app.Yield()

    assert dialog.common_commands_list.HasFocus()
    assert dialog.selected_command_id() == third.id
    assert [dialog.common_commands_list.GetString(idx) for idx in range(dialog.common_commands_list.GetCount())] == [
        "Second",
        "Third",
        "First",
    ]
    snapshot = frame.common_commands_store.read_snapshot()
    assert [item.id for item in snapshot.commands] == [second.id, third.id, first.id]


def test_ui_automation_move_common_command_down_updates_order_and_keeps_focus(frame, wx_app):
    _activate_frame(frame, wx_app)
    first = _seed_command(frame, title="First", content="echo first")
    second = _seed_command(frame, title="Second", content="echo second")
    third = _seed_command(frame, title="Third", content="echo third")
    assert frame._show_common_commands_surface() is True
    dialog = frame.common_commands_dialog
    dialog.common_commands_list.SetSelection(dialog.common_commands_list_ids.index(second.id))
    dialog.common_commands_list.SetFocus()
    wx_app.Yield()

    assert frame._move_selected_common_command_down() is True
    wx_app.Yield()

    assert dialog.common_commands_list.HasFocus()
    assert dialog.selected_command_id() == second.id
    assert [dialog.common_commands_list.GetString(idx) for idx in range(dialog.common_commands_list.GetCount())] == [
        "First",
        "Third",
        "Second",
    ]
    snapshot = frame.common_commands_store.read_snapshot()
    assert [item.id for item in snapshot.commands] == [first.id, third.id, second.id]
