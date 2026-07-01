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
        main.wx.WXK_DOWN: 0x50,
        main.wx.WXK_RETURN: 0x1C,
    }
    virtual_keys = {
        main.wx.WXK_DOWN: 0x28,
        main.wx.WXK_RETURN: 0x0D,
    }
    scan = scan_codes.get(key_code, 0)
    virtual_key = virtual_keys.get(key_code, int(key_code))
    down_lparam = 1 | (scan << 16)
    up_lparam = 1 | (scan << 16) | (1 << 30) | (1 << 31)
    hwnd = int(window.GetHandle())
    user32.SendMessageW(hwnd, wm_keydown, virtual_key, down_lparam)
    user32.SendMessageW(hwnd, wm_keyup, virtual_key, up_lparam)


def test_e2e_alt_z_open_arrow_select_enter_sends_common_command(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    frame.common_commands_store.create_command(
        main.CommonCommandCreate(title="First", content="echo first")
    )
    frame.common_commands_store.create_command(
        main.CommonCommandCreate(title="Second", content="echo second")
    )
    frame.input_edit.SetValue("keep me")
    submitted = []

    def _submit(question, source="local", model=None, chat_id=""):
        submitted.append((question, source, model, chat_id, frame.input_edit.GetValue()))
        return True, ""

    monkeypatch.setattr(frame, "_submit_question", _submit)

    event = wx.CommandEvent(wx.wxEVT_MENU, int(frame._common_commands_menu_id))
    event.SetEventObject(frame)
    assert frame.ProcessEvent(event)
    wx_app.Yield()

    dialog = frame.common_commands_dialog
    assert dialog is not None
    assert dialog.IsShown()
    assert dialog.common_commands_list.HasFocus()
    assert dialog.common_commands_list.GetSelection() == 0

    _send_listbox_key(dialog.common_commands_list, main.wx.WXK_DOWN)
    wx_app.Yield()
    _send_listbox_key(dialog.common_commands_list, main.wx.WXK_RETURN)
    wx_app.Yield()

    assert dialog.common_commands_list.GetSelection() == 1
    assert dialog.selected_command().content == "echo second"
    assert submitted == [("echo second", "local", None, "", "keep me")]
    assert frame.input_edit.GetValue() == "keep me"
