import wx


def test_chat_attachments_acceptance_alt_menu_routes_load_action(frame, monkeypatch):
    seen = {"popup": 0, "load": 0}
    captured = {"items": []}

    def _popup(menu, *_args):
        seen["popup"] += 1
        captured["items"] = [
            (item.GetItemLabelText(), item.GetId())
            for item in menu.GetMenuItems()
            if not item.IsSeparator()
        ]

    monkeypatch.setattr(frame, "PopupMenu", _popup)
    monkeypatch.setattr(frame, "_load_chat_attachments_via_dialog", lambda: seen.__setitem__("load", seen["load"] + 1) or True)

    frame._show_tools_menu()

    assert seen["popup"] == 1
    load_item_id = next(item_id for label, item_id in captured["items"] if label == "载入图片或文件")
    event = wx.CommandEvent(wx.wxEVT_MENU, load_item_id)
    event.SetEventObject(frame)
    frame.ProcessEvent(event)

    assert seen["load"] == 1
