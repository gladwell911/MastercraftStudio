import wx


def test_notes_ui_automation_menu_key_exports_selected_entry_ranges(frame, monkeypatch):
    frame.Show()
    notebook = frame.notes_store.create_notebook("ui automation export notebook")
    first = frame.notes_store.create_entry(notebook.id, "ui first entry", source="manual")
    second = frame.notes_store.create_entry(notebook.id, "ui second entry", source="manual")
    third = frame.notes_store.create_entry(notebook.id, "ui third entry", source="manual")
    frame._notes_select_notebook(notebook.id, view="note_detail")
    frame.notes_entry_list.SetSelection(frame._notes_entry_ids.index(second.id))
    frame.notes_entry_list.SetFocus()

    captured = {"items": [], "copied": []}

    class _MenuEvent:
        def GetKeyCode(self):
            return wx.WXK_MENU

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            raise AssertionError("menu key should not skip")

    monkeypatch.setattr(frame.notes_notebook_list, "HasFocus", lambda: False)
    monkeypatch.setattr(frame.notes_entry_list, "HasFocus", lambda: True)
    monkeypatch.setattr(frame, "_on_any_key_down_escape_minimize", lambda _event: False)
    monkeypatch.setattr(frame, "PopupMenu", lambda menu: captured.__setitem__(
        "items",
        [(item.GetItemLabelText(), item.GetId()) for item in menu.GetMenuItems() if not item.IsSeparator()],
    ))
    monkeypatch.setattr(frame, "_set_clipboard_text", lambda text: captured["copied"].append(text) or True)

    frame._on_notes_key_down(_MenuEvent())

    down_item_id = next(item_id for label, item_id in captured["items"] if label == "向下导出全部到剪贴板")
    up_item_id = next(item_id for label, item_id in captured["items"] if label == "向上导出全部到剪贴板")

    down_event = wx.CommandEvent(wx.wxEVT_MENU, down_item_id)
    down_event.SetEventObject(frame)
    frame.ProcessEvent(down_event)

    up_event = wx.CommandEvent(wx.wxEVT_MENU, up_item_id)
    up_event.SetEventObject(frame)
    frame.ProcessEvent(up_event)

    assert captured["copied"] == [
        "\n\n".join([second.content, third.content]),
        "\n\n".join([first.content, second.content]),
    ]
