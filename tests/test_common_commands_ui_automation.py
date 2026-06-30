import ctypes

import main
import wx


def _activate_frame(frame, wx_app):
    frame.Show()
    frame.Raise()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetForegroundWindow(int(frame.GetHandle()))
    wx_app.Yield()


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
    _seed_command(frame, title="Send", content="echo send me")
    assert frame._show_common_commands_surface() is True
    dialog = frame.common_commands_dialog
    dialog.common_commands_list.SetSelection(0)
    dialog.common_commands_list.SetFocus()
    sent = []
    monkeypatch.setattr(frame, "_trigger_send", lambda: sent.append(frame.input_edit.GetValue()))

    event = _EnterEvent()
    frame._on_common_commands_key_down(event)
    wx_app.Yield()

    assert sent == ["echo send me"]
    assert event.skipped == 0


def test_ui_automation_common_command_send_failure_does_not_overwrite_input(frame, wx_app):
    _activate_frame(frame, wx_app)
    _seed_command(frame, title="Send", content="echo send me")
    assert frame._show_common_commands_surface() is True
    dialog = frame.common_commands_dialog
    dialog.common_commands_list.SetSelection(0)
    dialog.common_commands_list.SetFocus()
    frame.input_edit.SetValue("keep me")
    frame.send_button.Enable(False)

    assert frame._send_selected_common_command() is False

    assert frame.input_edit.GetValue() == "keep me"


def test_ui_automation_common_command_enter_skips_when_send_cannot_run(frame, wx_app):
    _activate_frame(frame, wx_app)
    _seed_command(frame, title="Send", content="echo send me")
    assert frame._show_common_commands_surface() is True
    dialog = frame.common_commands_dialog
    dialog.common_commands_list.SetSelection(0)
    dialog.common_commands_list.SetFocus()
    frame.input_edit.SetValue("keep me")
    frame.send_button.Enable(False)
    event = _EnterEvent()

    frame._on_common_commands_key_down(event)
    wx_app.Yield()

    assert event.skipped == 1
    assert frame.input_edit.GetValue() == "keep me"


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

    assert opened == [["添加", "编辑", "删除"]]
    assert dialog.common_commands_list.GetSelection() == dialog.common_commands_list_ids.index(first.id)


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
