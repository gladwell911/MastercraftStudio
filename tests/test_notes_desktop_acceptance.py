import wx
import pytest

import main


def _button_event(button):
    event = wx.CommandEvent(wx.wxEVT_BUTTON, button.GetId())
    event.SetEventObject(button)
    return event


def _key_event(key_code, *, ctrl=False, alt=False):
    class _Event:
        def GetKeyCode(self):
            return key_code

        def ControlDown(self):
            return ctrl

        def AltDown(self):
            return alt

        def Skip(self):
            return None

    return _Event()


def _select_row_by_id(control, ids, row_id):
    control.SetSelection(ids.index(row_id))


@pytest.mark.parametrize(
    ("exit_choice", "trigger", "expected_view", "expected_dirty"),
    [
        ("save", "ctrl_enter", "note_detail", False),
        ("discard", "escape", "note_detail", False),
        ("cancel", "escape", "note_edit", True),
    ],
)
def test_notes_acceptance_list_detail_edit_and_exit_paths(frame, monkeypatch, exit_choice, trigger, expected_view, expected_dirty):
    notebook = frame.notes_store.create_notebook("acceptance notebook")
    entry = frame.notes_store.create_entry(notebook.id, "original note", source="manual")

    frame._notes_select_notebook(notebook.id, view="notes_list")
    _select_row_by_id(frame.notes_notebook_list, frame._notes_notebook_ids, notebook.id)
    monkeypatch.setattr(frame.notes_notebook_list, "HasFocus", lambda: True)
    frame._on_notes_key_down(_key_event(wx.WXK_RETURN))

    assert frame.notes_controller.notes_view == "note_detail"
    assert frame.notes_controller.active_notebook_id == notebook.id
    assert frame.notes_entry_list.GetCount() >= 1

    _select_row_by_id(frame.notes_entry_list, frame._notes_entry_ids, entry.id)
    monkeypatch.setattr(frame.notes_notebook_list, "HasFocus", lambda: False)
    monkeypatch.setattr(frame.notes_entry_list, "HasFocus", lambda: True)
    frame._on_notes_key_down(_key_event(wx.WXK_RETURN))

    assert frame.notes_controller.notes_view == "note_edit"
    assert frame.notes_controller.active_entry_id == entry.id

    frame.notes_editor.SetValue("edited note")
    frame._on_notes_editor_changed(None)
    assert frame.notes_controller.entry_editor_dirty is True

    if trigger == "ctrl_enter":
        frame._on_notes_key_down(_key_event(wx.WXK_RETURN, ctrl=True))
    else:
        monkeypatch.setattr(frame, "_prompt_notes_dirty_exit", lambda: exit_choice)
        frame._on_notes_key_down(_key_event(wx.WXK_ESCAPE))

    saved = frame.notes_store.get_entry(entry.id)
    assert frame.notes_controller.notes_view == expected_view
    assert frame.notes_controller.entry_editor_dirty is expected_dirty

    if exit_choice == "save":
        assert saved is not None
        assert saved.content == "edited note"
    else:
        assert saved is not None
        assert saved.content == "original note"


def test_notes_acceptance_menu_key_opens_notes_menu_and_routes_actions(frame, monkeypatch):
    notebook = frame.notes_store.create_notebook("menu notebook")
    frame._notes_select_notebook(notebook.id, view="note_detail")
    frame.notes_entry_list.SetFocus()

    seen = {"popup": 0, "file": 0}
    captured = {"items": []}

    def _popup(menu):
        seen["popup"] += 1
        captured["items"] = [
            (item.GetItemLabelText(), item.GetId())
            for item in menu.GetMenuItems()
            if not item.IsSeparator()
        ]

    monkeypatch.setattr(frame, "PopupMenu", _popup)
    monkeypatch.setattr(frame, "_notes_import_from_file", lambda: seen.__setitem__("file", seen["file"] + 1) or True)

    frame._on_notes_key_down(_key_event(wx.WXK_MENU))

    assert seen["popup"] == 1
    labels = [label for label, _item_id in captured["items"]]
    assert "新建笔记条目" in labels
    assert "从文件导入" in labels
    assert "从剪贴板导入" in labels

    import_item_id = next(item_id for label, item_id in captured["items"] if label == "从文件导入")
    event = wx.CommandEvent(wx.wxEVT_MENU, import_item_id)
    event.SetEventObject(frame)
    frame.ProcessEvent(event)

    assert seen["file"] == 1


def test_notes_acceptance_menu_new_entry_creates_blank_draft_and_focuses_editor(frame, monkeypatch):
    notebook = frame.notes_store.create_notebook("menu new entry notebook")
    existing = frame.notes_store.create_entry(notebook.id, "existing entry", source="manual")
    frame._notes_select_notebook(notebook.id, view="note_detail")
    _select_row_by_id(frame.notes_entry_list, frame._notes_entry_ids, existing.id)
    captured = {"items": []}

    def _popup(menu):
        captured["items"] = [
            (item.GetItemLabelText(), item.GetId())
            for item in menu.GetMenuItems()
            if not item.IsSeparator()
        ]

    monkeypatch.setattr(frame, "PopupMenu", _popup)
    monkeypatch.setattr(frame.notes_notebook_list, "HasFocus", lambda: False)
    monkeypatch.setattr(frame.notes_entry_list, "HasFocus", lambda: True)
    frame._on_notes_key_down(_key_event(wx.WXK_MENU))

    new_item_id = next(item_id for label, item_id in captured["items"] if label == "新建笔记条目")
    event = wx.CommandEvent(wx.wxEVT_MENU, new_item_id)
    event.SetEventObject(frame)
    frame.ProcessEvent(event)

    assert frame.notes_controller.notes_view == "note_edit"
    assert frame.notes_controller.active_entry_id == ""
    assert frame.notes_editor.GetValue() == ""
    assert frame.notes_editor.HasFocus()
    assert frame.notes_store.list_entries(notebook.id) == [existing]


def test_notes_acceptance_file_and_clipboard_imports_create_entries_from_ui(frame, monkeypatch, tmp_path):
    notebook = frame.notes_store.create_notebook("import notebook")
    frame._notes_select_notebook(notebook.id, view="note_detail")

    file_path = tmp_path / "notes.txt"
    file_path.write_text("file one\n\nfile two\n", encoding="utf-8")

    class _FileDialog:
        def __init__(self, _parent, *_args, **_kwargs):
            self._path = str(file_path)

        def ShowModal(self):
            return wx.ID_OK

        def GetPath(self):
            return self._path

        def Destroy(self):
            pass

    class _Clipboard:
        def __init__(self, text):
            self._text = text

        def Open(self):
            return True

        def Close(self):
            return True

        def GetData(self, data):
            data.SetText(self._text)
            return True

    monkeypatch.setattr(main.wx, "FileDialog", _FileDialog)
    monkeypatch.setattr(main.wx, "TheClipboard", _Clipboard("clip one\nclip two\n"))

    assert not hasattr(frame, "notes_import_file_button")
    assert not hasattr(frame, "notes_import_clipboard_button")

    assert frame._notes_import_from_file()
    after_file = frame.notes_store.list_entries(notebook.id)
    assert {entry.content for entry in after_file} == {"file one", "file two"}
    assert frame.notes_controller.notes_view == "note_detail"
    assert frame.notes_controller.active_entry_id == next(entry.id for entry in after_file if entry.content == "file one")

    assert frame._notes_import_from_clipboard()
    after_clip = frame.notes_store.list_entries(notebook.id)
    assert {entry.content for entry in after_clip} == {"clip one", "clip two", "file one", "file two"}
    assert frame.notes_controller.notes_view == "note_detail"
    assert frame.notes_store.get_entry(frame.notes_controller.active_entry_id).content == "clip one"


def test_notes_acceptance_notebook_menu_includes_open_and_routes_to_detail(frame, monkeypatch):
    notebook = frame.notes_store.create_notebook("menu open notebook")
    frame._notes_select_notebook(notebook.id, view="notes_list")
    _select_row_by_id(frame.notes_notebook_list, frame._notes_notebook_ids, notebook.id)

    seen = {"popup": 0}
    captured = {"items": []}

    def _popup(menu):
        seen["popup"] += 1
        captured["items"] = [
            (item.GetItemLabelText(), item.GetId())
            for item in menu.GetMenuItems()
            if not item.IsSeparator()
        ]

    monkeypatch.setattr(frame, "PopupMenu", _popup)
    monkeypatch.setattr(frame.notes_notebook_list, "HasFocus", lambda: True)
    monkeypatch.setattr(frame.notes_entry_list, "HasFocus", lambda: False)

    frame._on_notes_key_down(_key_event(wx.WXK_MENU))

    assert seen["popup"] == 1
    assert "打开笔记" in [label for label, _item_id in captured["items"]]

    open_item_id = next(item_id for label, item_id in captured["items"] if label == "打开笔记")
    event = wx.CommandEvent(wx.wxEVT_MENU, open_item_id)
    event.SetEventObject(frame)
    frame.ProcessEvent(event)

    assert frame.notes_controller.notes_view == "note_detail"
    assert frame.notes_controller.active_notebook_id == notebook.id


def test_notes_acceptance_copy_actions_from_notebook_and_entry_menus(frame, monkeypatch):
    notebook = frame.notes_store.create_notebook("acceptance copy notebook")
    entry = frame.notes_store.create_entry(notebook.id, "copied entry", source="manual")
    frame.notes_store.create_entry(notebook.id, "another copied entry", source="manual")
    copied = {"texts": []}
    monkeypatch.setattr(frame, "_set_clipboard_text", lambda text: copied["texts"].append(text) or True)

    frame._notes_select_notebook(notebook.id, view="notes_list")
    _select_row_by_id(frame.notes_notebook_list, frame._notes_notebook_ids, notebook.id)
    captured = {"items": []}

    def _popup(menu):
        captured["items"] = [
            (item.GetItemLabelText(), item.GetId())
            for item in menu.GetMenuItems()
            if not item.IsSeparator()
        ]

    monkeypatch.setattr(frame, "PopupMenu", _popup)
    monkeypatch.setattr(frame.notes_notebook_list, "HasFocus", lambda: True)
    monkeypatch.setattr(frame.notes_entry_list, "HasFocus", lambda: False)
    frame._on_notes_key_down(_key_event(wx.WXK_MENU))
    copy_notebook_id = next(item_id for label, item_id in captured["items"] if label == "复制笔记")
    event = wx.CommandEvent(wx.wxEVT_MENU, copy_notebook_id)
    event.SetEventObject(frame)
    frame.ProcessEvent(event)

    frame._notes_select_notebook(notebook.id, view="note_detail")
    _select_row_by_id(frame.notes_entry_list, frame._notes_entry_ids, entry.id)
    monkeypatch.setattr(frame.notes_notebook_list, "HasFocus", lambda: False)
    monkeypatch.setattr(frame.notes_entry_list, "HasFocus", lambda: True)
    frame._on_notes_key_down(_key_event(wx.WXK_MENU))
    copy_entry_id = next(item_id for label, item_id in captured["items"] if label == "复制笔记条目")
    event = wx.CommandEvent(wx.wxEVT_MENU, copy_entry_id)
    event.SetEventObject(frame)
    frame.ProcessEvent(event)

    assert copied["texts"][0] == "\n\n".join(item.content for item in frame.notes_store.list_entries(notebook.id))
    assert copied["texts"][1] == "copied entry"


def test_notes_acceptance_alt_x_creates_notebook_and_entry(frame, monkeypatch):
    notebook = frame.notes_store.create_notebook("acceptance shortcut notebook")
    entry_seen = {"notebook": 0, "entry": 0}

    original_create_notebook = frame._notes_create_notebook
    original_create_entry = frame._notes_create_entry

    def _create_notebook():
        entry_seen["notebook"] += 1
        return True

    def _create_entry():
        entry_seen["entry"] += 1
        return original_create_entry()

    monkeypatch.setattr(frame, "_notes_create_notebook", _create_notebook)
    monkeypatch.setattr(frame, "_notes_create_entry", _create_entry)

    frame._notes_select_notebook(notebook.id, view="notes_list")
    monkeypatch.setattr(frame.notes_notebook_list, "HasFocus", lambda: True)
    monkeypatch.setattr(frame.notes_entry_list, "HasFocus", lambda: False)
    frame._on_notes_key_down(_key_event(ord("X"), alt=True))

    frame._notes_select_notebook(notebook.id, view="note_detail")
    monkeypatch.setattr(frame.notes_notebook_list, "HasFocus", lambda: False)
    monkeypatch.setattr(frame.notes_entry_list, "HasFocus", lambda: True)
    frame._on_notes_key_down(_key_event(ord("X"), alt=True))

    assert entry_seen["notebook"] == 1
    assert entry_seen["entry"] == 1
    assert frame.notes_controller.notes_view == "note_edit"
    assert frame.notes_controller.active_entry_id == ""
    assert frame.notes_editor.GetValue() == ""
    assert frame.notes_editor.HasFocus()


def test_notes_acceptance_char_hook_enter_opens_notebook_without_send(frame, monkeypatch):
    notebook = frame.notes_store.create_notebook("char hook acceptance notebook")
    frame._notes_select_notebook(notebook.id, view="notes_list")
    _select_row_by_id(frame.notes_notebook_list, frame._notes_notebook_ids, notebook.id)
    seen = {"send": 0}

    monkeypatch.setattr(frame.notes_notebook_list, "HasFocus", lambda: True)
    monkeypatch.setattr(frame.notes_entry_list, "HasFocus", lambda: False)
    monkeypatch.setattr(frame.notes_editor, "HasFocus", lambda: False)
    monkeypatch.setattr(frame, "_trigger_send", lambda: seen.__setitem__("send", seen["send"] + 1))

    frame._on_char_hook(_key_event(wx.WXK_RETURN))

    assert frame.notes_controller.notes_view == "note_detail"
    assert frame.notes_controller.active_notebook_id == notebook.id
    assert seen["send"] == 0


def test_notes_acceptance_same_slot_navigation_and_edit_replacement(frame, monkeypatch):
    notebook = frame.notes_store.create_notebook("same slot acceptance notebook")
    entry = frame.notes_store.create_entry(notebook.id, "same slot entry", source="manual")
    frame._notes_select_notebook(notebook.id, view="notes_list")
    _select_row_by_id(frame.notes_notebook_list, frame._notes_notebook_ids, notebook.id)
    monkeypatch.setattr(frame.notes_notebook_list, "HasFocus", lambda: True)
    monkeypatch.setattr(frame.notes_entry_list, "HasFocus", lambda: False)

    frame._on_notes_key_down(_key_event(wx.WXK_RETURN))

    assert frame.notes_list_panel.IsShown()
    assert frame.notes_detail_panel.IsShown()
    assert not frame.notes_edit_panel.IsShown()

    _select_row_by_id(frame.notes_entry_list, frame._notes_entry_ids, entry.id)
    monkeypatch.setattr(frame.notes_notebook_list, "HasFocus", lambda: False)
    monkeypatch.setattr(frame.notes_entry_list, "HasFocus", lambda: True)
    frame._on_notes_key_down(_key_event(wx.WXK_RETURN))

    assert frame.notes_list_panel.IsShown()
    assert not frame.notes_detail_panel.IsShown()
    assert frame.notes_edit_panel.IsShown()
    assert not frame.notes_entry_list.IsEnabled()
    assert frame.notes_editor.IsEnabled()

    frame._notes_select_notebook(notebook.id, view="note_detail")
    monkeypatch.setattr(frame.notes_entry_list, "HasFocus", lambda: True)
    frame._on_notes_key_down(_key_event(wx.WXK_BACK))

    assert frame.notes_controller.notes_view == "notes_list"
    assert frame.notes_list_panel.IsShown()
    assert not frame.notes_detail_panel.IsShown()


def test_notes_acceptance_tab_can_reach_notebook_list_while_editing(frame):
    notebook = frame.notes_store.create_notebook("tab acceptance notebook")
    entry = frame.notes_store.create_entry(notebook.id, "tab acceptance entry", source="manual")

    frame._notes_select_entry(entry.id, view="note_edit")

    assert frame.notes_list_panel.IsShown()
    assert frame.notes_notebook_list.IsShown()
    assert frame.notes_notebook_list.IsEnabled()
    assert frame.notes_edit_panel.IsShown()
    assert frame.notes_notebook_list.GetName() == "笔记"
    assert frame.notes_editor.GetName() == "笔记"


def test_notes_acceptance_ctrl_enter_save_keeps_focus_on_saved_entry(frame, monkeypatch):
    notebook = frame.notes_store.create_notebook("save focus notebook")
    entry = frame.notes_store.create_entry(notebook.id, "before save", source="manual")
    frame._notes_select_notebook(notebook.id, view="note_detail")
    _select_row_by_id(frame.notes_entry_list, frame._notes_entry_ids, entry.id)
    monkeypatch.setattr(frame.notes_notebook_list, "HasFocus", lambda: False)
    monkeypatch.setattr(frame.notes_entry_list, "HasFocus", lambda: True)
    frame._on_notes_key_down(_key_event(wx.WXK_RETURN))

    frame.notes_editor.SetValue("after save")
    frame._on_notes_editor_changed(None)
    frame._on_notes_key_down(_key_event(wx.WXK_RETURN, ctrl=True))

    assert frame.notes_controller.notes_view == "note_detail"
    assert frame.notes_controller.active_entry_id == entry.id
    assert frame.notes_entry_list.HasFocus()


def test_notes_acceptance_tab_can_reach_notebook_list_while_editing(frame):
    notebook = frame.notes_store.create_notebook("tab acceptance notebook")
    entry = frame.notes_store.create_entry(notebook.id, "tab acceptance entry", source="manual")

    frame._notes_select_entry(entry.id, view="note_edit")

    assert frame.notes_list_panel.IsShown()
    assert frame.notes_notebook_list.IsShown()
    assert frame.notes_notebook_list.IsEnabled()
    assert frame.notes_edit_panel.IsShown()
    assert frame.notes_notebook_list.GetName() == "笔记"
    assert frame.notes_editor.GetName() == "笔记"
    assert frame.notes_edit_title.GetLabel() == "笔记"
