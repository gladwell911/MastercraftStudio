import wx


def test_notes_ui_automation_application_menu_exports_and_restores_all_notes(frame, monkeypatch, tmp_path):
    frame.Show()
    notebook = frame.notes_store.create_notebook("ui backup notebook")
    frame.notes_store.create_entry(notebook.id, "ui backup entry", source="manual")
    frame._notes_select_notebook(notebook.id, view="notes_list")
    frame.notes_notebook_list.SetFocus()

    captured = {"items": [], "dialog_paths": [], "status": []}
    backup_path = tmp_path / "ui-notes-backup.json"

    class _FileDialog:
        def __init__(self, _parent, _message, *_args, **kwargs):
            self._style = kwargs.get("style", 0)
            if self._style & wx.FD_SAVE:
                self._path = str(backup_path)
            else:
                self._path = str(backup_path)
            captured["dialog_paths"].append(self._path)

        def ShowModal(self):
            return wx.ID_OK

        def GetPath(self):
            return self._path

        def Destroy(self):
            pass

    monkeypatch.setattr(wx, "FileDialog", _FileDialog)
    monkeypatch.setattr(frame, "SetStatusText", lambda text: captured["status"].append(text))
    monkeypatch.setattr(frame, "PopupMenu", lambda menu: captured.__setitem__(
        "items",
        [(item.GetItemLabelText(), item.GetId()) for item in menu.GetMenuItems() if not item.IsSeparator()],
    ))

    frame._show_notes_menu()

    export_id = next(item_id for label, item_id in captured["items"] if label == "导出所有笔记")
    restore_id = next(item_id for label, item_id in captured["items"] if label == "恢复笔记")

    export_event = wx.CommandEvent(wx.wxEVT_MENU, export_id)
    export_event.SetEventObject(frame)
    frame.ProcessEvent(export_event)
    assert backup_path.exists()

    restored_notebook = frame.notes_store.create_notebook("restore target")
    frame.notes_store.create_entry(restored_notebook.id, "before restore", source="manual")

    restore_event = wx.CommandEvent(wx.wxEVT_MENU, restore_id)
    restore_event.SetEventObject(frame)
    frame.ProcessEvent(restore_event)

    restored = frame.notes_store.search_notebooks("ui backup notebook")
    assert restored
    assert any(entry.content == "ui backup entry" for entry in frame.notes_store.list_entries(restored[0].id))
    assert any("导出" in text for text in captured["status"])
    assert any("恢复" in text for text in captured["status"])


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


def test_notes_ui_automation_detail_view_hides_notebook_list_and_keeps_tab_slot(frame):
    frame.Show()
    notebook = frame.notes_store.create_notebook("ui automation detail notebook")
    frame.notes_store.create_entry(notebook.id, "ui detail entry", source="manual")

    frame._notes_select_notebook(notebook.id, view="note_detail")

    assert not frame.notes_list_panel.IsShown()
    assert frame.notes_detail_panel.IsShown()
    assert frame.root_tab_order[4] is frame.history_list
    assert frame.root_tab_order[5] is frame.notes_entry_list
