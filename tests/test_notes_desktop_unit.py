import json
from contextlib import contextmanager
from pathlib import Path

import wx
import pytest

import main


def test_notes_store_tracks_notebooks_entries_and_pending_ops(tmp_path):
    store = main.NotesStore(tmp_path / "notes.db", device_id="desktop-test")
    store.initialize()

    notebook = store.create_notebook("收件箱")
    entry = store.create_entry(notebook.id, "第一条内容", source="manual")

    fetched_notebook = store.get_notebook(notebook.id)
    fetched_entry = store.get_entry(entry.id)
    pending_ops = store.list_pending_ops(limit=10)

    assert fetched_notebook is not None
    assert fetched_notebook.title == "收件箱"
    assert fetched_notebook.version == 1
    assert fetched_entry is not None
    assert fetched_entry.content == "第一条内容"
    assert fetched_entry.source == "manual"
    assert pending_ops[-1].entity_type == "entry"
    assert pending_ops[-1].action == "create"


def test_import_note_entries_from_file_skips_blank_rows(tmp_path):
    store = main.NotesStore(tmp_path / "notes.db", device_id="desktop-test")
    store.initialize()
    notebook = store.create_notebook("导入测试")
    file_path = tmp_path / "notes.txt"
    file_path.write_text("第一行\n\n  第二行  \n", encoding="utf-8")

    created = main.import_note_entries_from_file(store, notebook.id, file_path)

    assert [entry.content for entry in created] == ["第一行", "第二行"]
    assert all(entry.source == "import_file" for entry in created)


def test_notes_sync_preserves_conflict_copy_on_stale_update(tmp_path):
    store = main.NotesStore(tmp_path / "notes.db", device_id="desktop-test")
    store.initialize()
    sync = main.NotesSyncService(store)
    notebook = store.create_notebook("同步测试")
    entry = store.create_entry(notebook.id, "桌面版内容", source="manual")
    store.update_entry(entry.id, "桌面版更新内容")

    result = sync.apply_remote_ops(
        [
            {
                "entity_type": "entry",
                "action": "update",
                "entity_id": entry.id,
                "base_version": 1,
                "payload": {
                    "content": "手机端内容",
                    "updated_at": "2026-04-12T00:00:00+00:00",
                },
            }
        ]
    )

    entries = store.list_entries(notebook.id, include_deleted=True)
    assert result["conflicts"]
    assert len(entries) == 2
    assert any(item.is_conflict_copy for item in entries)
    assert any(item.content == "桌面版更新内容" for item in entries)
    assert any("手机端内容" in item.content for item in entries)


def test_remote_create_preserves_incoming_entry_id_and_modifier_identity(tmp_path):
    store = main.NotesStore(tmp_path / "notes.db", device_id="desktop-test")
    store.initialize()
    sync = main.NotesSyncService(store)
    notebook = store.create_notebook("远端创建")

    result = sync.apply_remote_ops(
        [
            {
                "entity_type": "entry",
                "entity_id": "entry-remote-1",
                "action": "create",
                "source_device": "mobile-1",
                "payload": {
                    "notebook_id": notebook.id,
                    "content": "远端新建内容",
                    "source": "manual",
                },
            }
        ]
    )

    entry = store.get_entry("entry-remote-1")
    assert result["applied"]
    assert entry is not None
    assert entry.id == "entry-remote-1"
    assert entry.device_id == "mobile-1"
    assert entry.last_modified_by == "mobile"
    assert entry.content == "远端新建内容"


def test_remote_create_preserves_full_incoming_metadata(tmp_path):
    store = main.NotesStore(tmp_path / "notes.db", device_id="desktop-test")
    store.initialize()
    sync = main.NotesSyncService(store)
    notebook = store.create_notebook("远端元数据")

    sync.apply_remote_ops(
        [
            {
                "entity_type": "entry",
                "entity_id": "entry-remote-meta",
                "action": "create",
                "source_device": "mobile-2",
                "payload": {
                    "notebook_id": notebook.id,
                    "content": "远端完整元数据",
                    "created_at": "2026-04-12T01:02:03+00:00",
                    "updated_at": "2026-04-12T04:05:06+00:00",
                    "pinned": True,
                    "sort_order": 88,
                    "version": 9,
                    "is_conflict_copy": True,
                    "origin_entry_id": "origin-42",
                    "source": "voice",
                },
            }
        ]
    )

    entry = store.get_entry("entry-remote-meta")
    assert entry is not None
    assert entry.created_at == "2026-04-12T01:02:03+00:00"
    assert entry.updated_at == "2026-04-12T04:05:06+00:00"
    assert entry.pinned is True
    assert entry.sort_order == 88
    assert entry.version == 9
    assert entry.is_conflict_copy is True
    assert entry.origin_entry_id == "origin-42"
    assert entry.device_id == "mobile-2"
    assert entry.last_modified_by == "mobile"


def test_entries_are_listed_in_chronological_order_by_default(tmp_path):
    store = main.NotesStore(tmp_path / "notes.db", device_id="desktop-test")
    store.initialize()
    notebook = store.create_notebook("chronological notebook")
    first = store.create_entry(
        notebook.id,
        "first entry",
        source="manual",
        created_at="2026-04-12T10:00:00+00:00",
        updated_at="2026-04-12T10:00:00+00:00",
        sort_order=100,
    )
    second = store.create_entry(
        notebook.id,
        "second entry",
        source="manual",
        created_at="2026-04-12T10:00:01+00:00",
        updated_at="2026-04-12T10:00:01+00:00",
        sort_order=101,
    )

    entries = store.list_entries(notebook.id)

    assert [item.id for item in entries] == [first.id, second.id]


def test_remote_notebook_create_uses_payload_updated_at_fallback(tmp_path):
    store = main.NotesStore(tmp_path / "notes.db", device_id="desktop-test")
    store.initialize()
    sync = main.NotesSyncService(store)

    sync.apply_remote_ops(
        [
            {
                "entity_type": "notebook",
                "entity_id": "notebook-remote-1",
                "action": "create",
                "source_device": "mobile-7",
                "payload": {
                    "title": "remote notebook",
                    "created_at": "2026-04-12T02:03:04+00:00",
                },
            }
        ]
    )

    notebook = store.get_notebook("notebook-remote-1")
    assert notebook is not None
    assert notebook.created_at == "2026-04-12T02:03:04+00:00"
    assert notebook.updated_at == "2026-04-12T02:03:04+00:00"


def test_pull_since_returns_snapshot_when_client_cursor_is_ahead_of_desktop_store(tmp_path):
    store = main.NotesStore(tmp_path / "notes.db", device_id="desktop-test")
    store.initialize()
    sync = main.NotesSyncService(store)
    notebook = store.create_notebook("周三起床")

    result = sync.pull_since("999")

    assert result["cursor"] == store.current_cursor()
    assert "notebooks" in result
    assert "entries" in result
    assert any(item["id"] == notebook.id for item in result["notebooks"])


def test_conflict_copy_labels_use_semantic_source_words(tmp_path):
    store = main.NotesStore(tmp_path / "notes.db", device_id="desktop-test")
    store.initialize()
    sync = main.NotesSyncService(store)

    mobile_notebook = store.create_notebook("conflict notebook")
    mobile_entry = store.create_entry(mobile_notebook.id, "stale entry", source="manual")
    store.update_entry(mobile_entry.id, "local entry update")

    desktop_entry = store.create_entry(mobile_notebook.id, "stale entry 2", source="manual")
    store.update_entry(desktop_entry.id, "local entry update 2")

    sync.apply_remote_ops(
        [
            {
                "entity_type": "entry",
                "action": "update",
                "entity_id": mobile_entry.id,
                "source_device": "mobile-9",
                "base_version": 1,
                "payload": {
                    "content": "remote entry update",
                    "updated_at": "2026-04-12T10:11:12+00:00",
                },
            },
            {
                "entity_type": "entry",
                "action": "update",
                "entity_id": desktop_entry.id,
                "source_device": "desktop-8",
                "base_version": 1,
                "payload": {
                    "content": "remote entry update 2",
                    "updated_at": "2026-04-12T10:11:13+00:00",
                },
            },
        ]
    )

    entry_conflicts = [item for item in store.list_entries(mobile_notebook.id, include_deleted=True) if item.is_conflict_copy]
    assert len(entry_conflicts) >= 2
    assert "\u624b\u673a\u7aef" in entry_conflicts[0].content or "\u624b\u673a\u7aef" in entry_conflicts[1].content
    assert "\u7535\u8111\u7aef" in entry_conflicts[0].content or "\u7535\u8111\u7aef" in entry_conflicts[1].content
    assert "mobile-9" not in entry_conflicts[0].content + entry_conflicts[1].content
    assert "desktop-8" not in entry_conflicts[0].content + entry_conflicts[1].content


def test_import_entries_uses_single_transaction_for_batch(tmp_path, monkeypatch):
    store = main.NotesStore(tmp_path / "notes.db", device_id="desktop-test")
    store.initialize()
    notebook = store.create_notebook("批量导入")
    real_connect = store._connect
    connect_calls = {"count": 0}

    @contextmanager
    def counting_connect():
        connect_calls["count"] += 1
        with real_connect() as conn:
            yield conn

    monkeypatch.setattr(store, "_connect", counting_connect)

    created = store.import_entries(notebook.id, ["第一条", "", "第二条"], source="import_file")

    assert connect_calls["count"] == 1
    assert [entry.content for entry in created] == ["第一条", "第二条"]
    assert sorted(entry.content for entry in store.list_entries(notebook.id)) == ["第一条", "第二条"]


def test_notes_controller_restore_prefers_edit_detail_or_list(tmp_path):
    class FakeEditor:
        def __init__(self):
            self.value = ""
            self.cursor = None
            self.scroll = None

        def SetValue(self, value):
            self.value = value

        def SetInsertionPoint(self, cursor):
            self.cursor = cursor

        def ShowPosition(self, scroll):
            self.scroll = scroll

    class FakeFrame:
        def __init__(self, store):
            self.notes_store = store
            self.notes_editor = FakeEditor()
            self._current_notes_state = {}

    store = main.NotesStore(tmp_path / "notes.db", device_id="desktop-test")
    store.initialize()
    notebook = store.create_notebook("恢复测试")
    entry = store.create_entry(notebook.id, "保存过的草稿", source="manual")
    frame = FakeFrame(store)
    controller = main.DesktopNotesController(frame, store)

    controller.restore_state(
        {
            "active_root_tab": "notes",
            "notes_view": "notes_list",
            "active_notebook_id": notebook.id,
            "active_entry_id": entry.id,
        }
    )
    assert controller.notes_view == "notes_list"

    controller.restore_state(
        {
            "active_root_tab": "notes",
            "notes_view": "note_detail",
            "active_notebook_id": notebook.id,
            "active_entry_id": entry.id,
        }
    )
    assert controller.notes_view == "note_detail"

    controller.restore_state(
        {
            "active_root_tab": "notes",
            "notes_view": "note_edit",
            "active_notebook_id": notebook.id,
            "active_entry_id": entry.id,
            "entry_editor_draft": "待恢复草稿",
            "entry_editor_dirty": True,
            "entry_editor_cursor": 3,
            "entry_editor_scroll": 9,
        }
    )

    assert controller.notes_view == "note_edit"
    assert frame.notes_editor.value == "待恢复草稿"
    assert frame.notes_editor.cursor == 3
    assert frame.notes_editor.scroll == 9
    assert frame._current_notes_state["entry_editor_draft"] == "待恢复草稿"

    frame.notes_editor = FakeEditor()
    controller.restore_state(
        {
            "active_root_tab": "notes",
            "notes_view": "note_edit",
            "active_notebook_id": notebook.id,
            "active_entry_id": "missing-entry",
            "entry_editor_draft": "不会恢复",
            "entry_editor_dirty": True,
        }
    )

    assert controller.notes_view == "note_detail"
    assert frame.notes_editor.value == ""

    controller.restore_state(
        {
            "active_root_tab": "notes",
            "notes_view": "note_edit",
            "active_notebook_id": "missing-notebook",
            "active_entry_id": "missing-entry",
            "entry_editor_draft": "也不会恢复",
            "entry_editor_dirty": True,
        }
    )

    assert controller.notes_view == "notes_list"


def test_notes_controller_restore_keeps_unsaved_new_entry_draft(tmp_path):
    class FakeEditor:
        def __init__(self):
            self.value = ""
            self.cursor = None
            self.scroll = None

        def SetValue(self, value):
            self.value = value

        def SetInsertionPoint(self, cursor):
            self.cursor = cursor

        def ShowPosition(self, scroll):
            self.scroll = scroll

    class FakeFrame:
        def __init__(self, store):
            self.notes_store = store
            self.notes_editor = FakeEditor()
            self._current_notes_state = {}

    store = main.NotesStore(tmp_path / "notes.db", device_id="desktop-test")
    store.initialize()
    notebook = store.create_notebook("unsaved draft notebook")
    frame = FakeFrame(store)
    controller = main.DesktopNotesController(frame, store)

    controller.restore_state(
        {
            "active_root_tab": "notes",
            "notes_view": "note_edit",
            "active_notebook_id": notebook.id,
            "active_entry_id": "",
            "entry_editor_draft": "unsaved draft content",
            "entry_editor_dirty": True,
            "entry_editor_cursor": 5,
            "entry_editor_scroll": 2,
        }
    )

    assert controller.notes_view == "note_edit"
    assert controller.active_notebook_id == notebook.id
    assert controller.active_entry_id == ""
    assert controller.entry_editor_draft == "unsaved draft content"
    assert controller.entry_editor_dirty is True
    assert frame.notes_editor.value == "unsaved draft content"
    assert frame._current_notes_state["entry_editor_draft"] == "unsaved draft content"


def test_notes_store_persists_entries_across_reopen(tmp_path):
    db_path = tmp_path / "notes.db"
    store = main.NotesStore(db_path, device_id="desktop-test")
    store.initialize()
    notebook = store.create_notebook("persist notebook")
    entry = store.create_entry(notebook.id, "persisted entry content", source="manual")

    reopened = main.NotesStore(db_path, device_id="desktop-test")
    reopened.initialize()

    reopened_notebook = reopened.get_notebook(notebook.id)
    reopened_entry = reopened.get_entry(entry.id)
    assert reopened_notebook is not None
    assert reopened_entry is not None
    assert reopened_entry.content == "persisted entry content"
    assert [item.id for item in reopened.list_entries(notebook.id)] == [entry.id]


def test_chatframe_exposes_notes_root_widgets(frame):
    assert hasattr(frame, "notes_list_panel")
    assert hasattr(frame, "notes_detail_panel")
    assert hasattr(frame, "notes_edit_panel")
    assert hasattr(frame, "notes_notebook_list")
    assert hasattr(frame, "notes_entry_list")
    assert hasattr(frame, "notes_editor")
    assert not hasattr(frame, "notes_button")
    assert not hasattr(frame, "notes_search_ctrl")
    assert not hasattr(frame, "notes_back_button")


def test_notes_notebook_list_is_visible_in_main_view(frame):
    assert frame.notes_notebook_list.IsShown()
    assert frame.notes_controller.root_tab == "notes"
    assert frame.notes_controller.notes_view == "notes_list"
    assert frame.notes_notebook_list.GetName() == "笔记"
    assert frame.notes_notebook_list.GetToolTipText() == "笔记"


def test_notes_root_tab_path_keeps_chat_and_notes_controls_linked(frame):
    assert frame.chat_tab_order[-1] is frame.answer_list
    assert frame.root_tab_order[0] is frame.input_edit
    assert frame.root_tab_order[3] is frame.model_combo
    assert frame.root_tab_order[4] is frame.history_list
    assert frame.root_tab_order[5] is frame.notes_notebook_list
    assert frame.root_tab_order[-2] is frame.notes_entry_list
    assert frame.root_tab_order[-1] is frame.notes_editor


def test_notes_notebook_selection_only_changes_selection_until_enter(frame, monkeypatch):
    first = frame.notes_store.create_notebook("first notebook")
    second = frame.notes_store.create_notebook("second notebook")

    frame._notes_select_notebook(first.id, view="notes_list")
    frame.notes_notebook_list.SetSelection(frame._notes_notebook_ids.index(second.id))
    frame._on_notes_notebook_selected(None)

    assert frame.notes_controller.notes_view == "notes_list"
    assert frame.notes_controller.active_notebook_id == second.id

    class _EnterEvent:
        def GetKeyCode(self):
            return wx.WXK_RETURN

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            raise AssertionError("should not skip")

    monkeypatch.setattr(frame.notes_notebook_list, "HasFocus", lambda: True)
    monkeypatch.setattr(frame, "_on_any_key_down_escape_minimize", lambda _event: False)
    frame._on_notes_key_down(_EnterEvent())

    assert frame.notes_controller.notes_view == "note_detail"
    assert frame.notes_controller.active_notebook_id == second.id


def test_notes_char_hook_enter_prefers_open_notebook_over_send(frame, monkeypatch):
    notebook = frame.notes_store.create_notebook("char hook notebook")
    frame._notes_select_notebook(notebook.id, view="notes_list")
    frame.notes_notebook_list.SetSelection(frame._notes_notebook_ids.index(notebook.id))
    frame._on_notes_notebook_selected(None)

    seen = {"send": 0, "skip": 0}
    monkeypatch.setattr(frame.notes_notebook_list, "HasFocus", lambda: True)
    monkeypatch.setattr(frame.notes_entry_list, "HasFocus", lambda: False)
    monkeypatch.setattr(frame.notes_editor, "HasFocus", lambda: False)
    monkeypatch.setattr(frame, "_trigger_send", lambda: seen.__setitem__("send", seen["send"] + 1))

    class _Event:
        def GetKeyCode(self):
            return wx.WXK_RETURN

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            seen["skip"] += 1

    frame._on_char_hook(_Event())

    assert frame.notes_controller.notes_view == "note_detail"
    assert frame.notes_controller.active_notebook_id == notebook.id
    assert seen["send"] == 0


def test_notes_notebook_menu_includes_open_and_routes_to_detail(frame, monkeypatch):
    notebook = frame.notes_store.create_notebook("open from menu notebook")
    frame._notes_select_notebook(notebook.id, view="notes_list")
    frame.notes_notebook_list.SetSelection(frame._notes_notebook_ids.index(notebook.id))
    frame._on_notes_notebook_selected(None)

    captured = {"items": []}
    monkeypatch.setattr(frame.notes_notebook_list, "HasFocus", lambda: True)
    monkeypatch.setattr(frame.notes_entry_list, "HasFocus", lambda: False)
    monkeypatch.setattr(frame, "PopupMenu", lambda menu: captured.__setitem__(
        "items",
        [(item.GetItemLabelText(), item.GetId()) for item in menu.GetMenuItems() if not item.IsSeparator()],
    ))

    frame._show_notes_menu()

    labels = [label for label, _item_id in captured["items"]]
    assert "打开笔记" in labels

    open_item_id = next(item_id for label, item_id in captured["items"] if label == "打开笔记")
    event = wx.CommandEvent(wx.wxEVT_MENU, open_item_id)
    event.SetEventObject(frame)
    frame.ProcessEvent(event)

    assert frame.notes_controller.notes_view == "note_detail"
    assert frame.notes_controller.active_notebook_id == notebook.id


def test_notes_notebook_menu_includes_copy_and_copies_all_entries(frame, monkeypatch):
    notebook = frame.notes_store.create_notebook("copy notebook")
    frame.notes_store.create_entry(notebook.id, "first line\n\nsecond paragraph", source="manual")
    frame.notes_store.create_entry(notebook.id, "second entry", source="manual")
    frame._notes_select_notebook(notebook.id, view="notes_list")
    frame.notes_notebook_list.SetSelection(frame._notes_notebook_ids.index(notebook.id))

    captured = {"items": [], "copied": None}
    monkeypatch.setattr(frame.notes_notebook_list, "HasFocus", lambda: True)
    monkeypatch.setattr(frame.notes_entry_list, "HasFocus", lambda: False)
    monkeypatch.setattr(frame, "PopupMenu", lambda menu: captured.__setitem__(
        "items",
        [(item.GetItemLabelText(), item.GetId()) for item in menu.GetMenuItems() if not item.IsSeparator()],
    ))
    monkeypatch.setattr(frame, "_set_clipboard_text", lambda text: captured.__setitem__("copied", text) or True)

    frame._show_notes_menu()

    labels = [label for label, _item_id in captured["items"]]
    assert "复制笔记" in labels

    copy_item_id = next(item_id for label, item_id in captured["items"] if label == "复制笔记")
    event = wx.CommandEvent(wx.wxEVT_MENU, copy_item_id)
    event.SetEventObject(frame)
    frame.ProcessEvent(event)

    expected = "\n\n".join(item.content for item in frame.notes_store.list_entries(notebook.id))
    assert captured["copied"] == expected


def test_notes_notebook_menu_includes_export_to_clipboard_and_exports_all_entries(frame, monkeypatch):
    notebook = frame.notes_store.create_notebook("export notebook")
    first = frame.notes_store.create_entry(notebook.id, "first export", source="manual")
    second = frame.notes_store.create_entry(notebook.id, "second export", source="manual")
    frame._notes_select_notebook(notebook.id, view="notes_list")
    frame.notes_notebook_list.SetSelection(frame._notes_notebook_ids.index(notebook.id))

    captured = {"items": [], "copied": None}
    monkeypatch.setattr(frame.notes_notebook_list, "HasFocus", lambda: True)
    monkeypatch.setattr(frame.notes_entry_list, "HasFocus", lambda: False)
    monkeypatch.setattr(frame, "PopupMenu", lambda menu: captured.__setitem__(
        "items",
        [(item.GetItemLabelText(), item.GetId()) for item in menu.GetMenuItems() if not item.IsSeparator()],
    ))
    monkeypatch.setattr(frame, "_set_clipboard_text", lambda text: captured.__setitem__("copied", text) or True)

    frame._show_notes_menu()

    labels = [label for label, _item_id in captured["items"]]
    assert "导出到剪贴板" in labels

    export_item_id = next(item_id for label, item_id in captured["items"] if label == "导出到剪贴板")
    event = wx.CommandEvent(wx.wxEVT_MENU, export_item_id)
    event.SetEventObject(frame)
    frame.ProcessEvent(event)

    assert captured["copied"] == "\n\n".join([first.content, second.content])


def test_notes_entry_menu_exports_all_entries_from_selected_row_upward_and_downward(frame, monkeypatch):
    notebook = frame.notes_store.create_notebook("range export notebook")
    first = frame.notes_store.create_entry(notebook.id, "first entry", source="manual")
    second = frame.notes_store.create_entry(notebook.id, "second entry", source="manual")
    third = frame.notes_store.create_entry(notebook.id, "third entry", source="manual")
    frame._notes_select_notebook(notebook.id, view="note_detail")
    frame.notes_entry_list.SetSelection(frame._notes_entry_ids.index(second.id))

    captured = {"items": [], "copied": []}
    monkeypatch.setattr(frame.notes_notebook_list, "HasFocus", lambda: False)
    monkeypatch.setattr(frame.notes_entry_list, "HasFocus", lambda: True)
    monkeypatch.setattr(frame, "PopupMenu", lambda menu: captured.__setitem__(
        "items",
        [(item.GetItemLabelText(), item.GetId()) for item in menu.GetMenuItems() if not item.IsSeparator()],
    ))
    monkeypatch.setattr(frame, "_set_clipboard_text", lambda text: captured["copied"].append(text) or True)

    frame._show_notes_menu()

    labels = [label for label, _item_id in captured["items"]]
    assert "向下导出全部到剪贴板" in labels
    assert "向上导出全部到剪贴板" in labels

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


def test_notes_detail_view_reuses_list_slot_and_backspace_returns_to_notebook_list(frame, monkeypatch):
    notebook = frame.notes_store.create_notebook("same slot notebook")
    entry = frame.notes_store.create_entry(notebook.id, "entry in same slot", source="manual")
    frame._notes_select_notebook(notebook.id, view="notes_list")

    assert frame.notes_list_panel.IsShown()
    assert not frame.notes_detail_panel.IsShown()
    assert not frame.notes_edit_panel.IsShown()

    frame._notes_open_selected_notebook()

    assert frame.notes_controller.notes_view == "note_detail"
    assert not frame.notes_list_panel.IsShown()
    assert frame.notes_detail_panel.IsShown()
    assert not frame.notes_edit_panel.IsShown()
    assert frame.notes_entry_list.GetCount() >= 1

    class _BackEvent:
        def GetKeyCode(self):
            return wx.WXK_BACK

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            raise AssertionError("should not skip")

    monkeypatch.setattr(frame.notes_entry_list, "HasFocus", lambda: True)
    monkeypatch.setattr(frame, "_on_any_key_down_escape_minimize", lambda _event: False)
    frame._on_notes_key_down(_BackEvent())

    assert frame.notes_controller.notes_view == "notes_list"
    assert frame.notes_list_panel.IsShown()
    assert not frame.notes_detail_panel.IsShown()
    assert not frame.notes_edit_panel.IsShown()
    assert frame.notes_controller.active_notebook_id == notebook.id


def test_notes_edit_view_replaces_detail_list_and_disables_hidden_note_controls(frame):
    notebook = frame.notes_store.create_notebook("edit slot notebook")
    entry = frame.notes_store.create_entry(notebook.id, "editable entry", source="manual")

    frame._notes_select_notebook(notebook.id, view="note_detail")
    assert frame.notes_detail_panel.IsShown()
    assert not frame.notes_list_panel.IsShown()

    frame._notes_select_entry(entry.id, view="note_edit")

    assert frame.notes_controller.notes_view == "note_edit"
    assert frame.notes_list_panel.IsShown()
    assert not frame.notes_detail_panel.IsShown()
    assert frame.notes_edit_panel.IsShown()
    assert not frame.notes_entry_list.IsEnabled()
    assert frame.notes_editor.IsEnabled()


def test_notes_tab_order_keeps_notebook_list_reachable_while_editing(frame):
    notebook = frame.notes_store.create_notebook("tab notebook")
    entry = frame.notes_store.create_entry(notebook.id, "tab entry", source="manual")
    frame._notes_select_entry(entry.id, view="note_edit")

    assert frame.notes_list_panel.IsShown()
    assert frame.notes_notebook_list.IsShown()
    assert frame.notes_notebook_list.IsEnabled()
    assert frame.notes_editor.IsEnabled()
    assert frame.notes_editor.GetName() == "笔记"
    assert frame.notes_editor.GetToolTipText() == "笔记"


def test_notes_save_keeps_focus_on_saved_entry(frame):
    notebook = frame.notes_store.create_notebook("focus notebook")
    entry = frame.notes_store.create_entry(notebook.id, "original content", source="manual")
    frame._notes_select_entry(entry.id, view="note_edit")
    frame.notes_editor.SetValue("saved content")
    frame._on_notes_editor_changed(None)
    seen = {"focused": 0}
    frame.notes_entry_list.SetFocus = lambda: seen.__setitem__("focused", seen["focused"] + 1)

    assert frame._notes_save_current_entry() is True

    assert frame.notes_controller.notes_view == "note_detail"
    assert frame.notes_controller.active_entry_id == entry.id
    assert seen["focused"] == 1


def test_notes_editor_change_marks_dirty(frame):
    notebook = frame.notes_store.create_notebook("dirty notebook")
    entry = frame.notes_store.create_entry(notebook.id, "original", source="manual")
    frame._notes_select_entry(entry.id, view="note_edit")
    frame.notes_editor.SetValue("edited")

    frame._on_notes_editor_changed(None)

    assert frame.notes_controller.entry_editor_dirty is True
    assert frame._current_notes_state["entry_editor_dirty"] is True
    assert frame._current_notes_state["entry_editor_draft"] == "edited"


def test_notes_edit_escape_dirty_can_discard_without_minimizing(frame, monkeypatch):
    notebook = frame.notes_store.create_notebook("exit notebook")
    entry = frame.notes_store.create_entry(notebook.id, "original", source="manual")
    frame._notes_select_entry(entry.id, view="note_edit")
    frame.notes_editor.SetValue("edited")
    frame._on_notes_editor_changed(None)

    seen = {"minimize": 0}
    monkeypatch.setattr(frame, "_prompt_notes_dirty_exit", lambda: "discard")
    monkeypatch.setattr(frame, "_minimize_to_tray", lambda: seen.__setitem__("minimize", seen["minimize"] + 1))

    class _Event:
        def GetKeyCode(self):
            return wx.WXK_ESCAPE

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            raise AssertionError("should not skip")

    frame._on_notes_key_down(_Event())

    assert frame.notes_controller.notes_view == "note_detail"
    assert frame.notes_controller.entry_editor_dirty is False
    assert seen["minimize"] == 0


def test_notes_edit_escape_dirty_can_save_changes(frame, monkeypatch):
    notebook = frame.notes_store.create_notebook("save notebook")
    entry = frame.notes_store.create_entry(notebook.id, "original", source="manual")
    frame._notes_select_entry(entry.id, view="note_edit")
    frame.notes_editor.SetValue("saved draft")
    frame._on_notes_editor_changed(None)

    monkeypatch.setattr(frame, "_prompt_notes_dirty_exit", lambda: "save")

    class _Event:
        def GetKeyCode(self):
            return wx.WXK_ESCAPE

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            raise AssertionError("should not skip")

    frame._on_notes_key_down(_Event())

    saved = frame.notes_store.get_entry(entry.id)
    assert saved is not None
    assert saved.content == "saved draft"
    assert frame.notes_controller.notes_view == "note_detail"
    assert frame.notes_controller.entry_editor_dirty is False


def test_notes_save_conflict_copy_when_base_version_changes(frame):
    notebook = frame.notes_store.create_notebook("stale save notebook")
    entry = frame.notes_store.create_entry(notebook.id, "original", source="manual")
    frame._notes_select_entry(entry.id, view="note_edit")
    frame.notes_editor.SetValue("local draft")
    frame._on_notes_editor_changed(None)

    frame.notes_store.update_entry(entry.id, "remote update")
    frame._notes_save_current_entry()

    entries = frame.notes_store.list_entries(notebook.id, include_deleted=True)
    original = frame.notes_store.get_entry(entry.id)
    conflicts = [item for item in entries if item.is_conflict_copy]

    assert original is not None
    assert original.content == "remote update"
    assert len(conflicts) == 1
    assert conflicts[0].content.startswith("【冲突副本：来自电脑端】")
    assert conflicts[0].origin_entry_id == entry.id
    assert frame.notes_controller.active_entry_id == conflicts[0].id
    assert frame.notes_controller.notes_view == "note_detail"


def test_delete_notebook_soft_deletes_child_entries(frame):
    notebook = frame.notes_store.create_notebook("cascade delete notebook")
    entry = frame.notes_store.create_entry(notebook.id, "child entry", source="manual")

    frame._notes_select_notebook(notebook.id, view="note_detail")
    frame.notes_store.delete_notebook(notebook.id)

    deleted_notebook = frame.notes_store.get_notebook(notebook.id, include_deleted=True)
    deleted_entry = frame.notes_store.get_entry(entry.id, include_deleted=True)

    assert deleted_notebook is not None and deleted_notebook.deleted_at is not None
    assert deleted_entry is not None and deleted_entry.deleted_at is not None


def test_notes_remote_ops_refresh_ui_and_hint_current_editing_entry(frame, monkeypatch):
    notebook = frame.notes_store.create_notebook("remote hint notebook")
    entry = frame.notes_store.create_entry(notebook.id, "local draft", source="manual")
    frame._notes_select_entry(entry.id, view="note_edit")
    frame.notes_editor.SetValue("")
    frame._on_notes_editor_changed(None)

    seen = {"refresh": 0}
    monkeypatch.setattr(frame, "_notes_refresh_ui", lambda: seen.__setitem__("refresh", seen["refresh"] + 1))

    frame.notes_sync.apply_remote_ops(
        [
            {
                "entity_type": "entry",
                "action": "update",
                "entity_id": entry.id,
                "source_device": "mobile-9",
                "base_version": 1,
                "payload": {
                    "content": "remote changed content",
                    "updated_at": "2026-04-12T10:11:12+00:00",
                },
            }
        ]
    )

    assert seen["refresh"] >= 1
    assert "远程" in frame.notes_sync_hint
    assert "刷新" in frame.notes_sync_hint


def test_notes_tab_order_links_chat_and_notes_controls(frame):
    assert frame.chat_tab_order[3] is frame.model_combo
    assert frame.chat_tab_order[4] is frame.history_list
    assert frame.chat_tab_order[5] is frame.notes_notebook_list
    assert frame.notes_tab_order[0] is frame.notes_notebook_list
    assert frame.notes_tab_order[1] is frame.notes_entry_list
    assert frame.notes_tab_order[-1] is frame.notes_editor


def test_notes_detail_view_moves_history_tab_target_to_entry_list(frame):
    notebook = frame.notes_store.create_notebook("tab target notebook")
    frame.notes_store.create_entry(notebook.id, "tab target entry", source="manual")

    frame._notes_select_notebook(notebook.id, view="note_detail")

    assert frame.root_tab_order[4] is frame.history_list
    assert frame.root_tab_order[5] is frame.notes_entry_list
    assert frame.chat_tab_order[5] is frame.notes_entry_list
    assert frame.notes_tab_order[0] is frame.notes_entry_list
    assert not frame.notes_list_panel.IsShown()
    assert frame.notes_detail_panel.IsShown()


def test_notes_detail_view_reorders_panel_tab_chain_after_history(frame, monkeypatch):
    notebook = frame.notes_store.create_notebook("panel tab notebook")
    frame.notes_store.create_entry(notebook.id, "panel tab entry", source="manual")
    calls = []
    monkeypatch.setattr(
        frame.notes_detail_panel,
        "MoveAfterInTabOrder",
        lambda previous: calls.append(("detail_panel", previous)),
    )
    monkeypatch.setattr(
        frame.answer_list,
        "MoveAfterInTabOrder",
        lambda previous: calls.append(("answer_list", previous)),
    )

    frame._notes_select_notebook(notebook.id, view="note_detail")

    assert ("detail_panel", frame.history_list) in calls
    assert ("answer_list", frame.notes_detail_panel) in calls


def test_notes_list_and_detail_views_do_not_show_action_buttons(frame):
    assert not hasattr(frame, "notes_new_notebook_button")
    assert not hasattr(frame, "notes_rename_notebook_button")
    assert not hasattr(frame, "notes_delete_notebook_button")
    assert not hasattr(frame, "notes_new_entry_button")
    assert not hasattr(frame, "notes_edit_entry_button")
    assert not hasattr(frame, "notes_delete_entry_button")
    assert not hasattr(frame, "notes_pin_entry_button")
    assert not hasattr(frame, "notes_bottom_entry_button")
    assert not hasattr(frame, "notes_import_file_button")
    assert not hasattr(frame, "notes_import_clipboard_button")
    assert not hasattr(frame, "notes_save_button")
    assert not hasattr(frame, "notes_cancel_button")


def test_notes_blank_entry_cancel_deletes_persisted_row(frame):
    notebook = frame.notes_store.create_notebook("blank cancel notebook")
    frame._notes_select_notebook(notebook.id, view="note_detail")
    frame._notes_create_entry()
    created_id = frame.notes_controller.active_entry_id

    assert created_id == ""
    assert frame.notes_store.list_entries(notebook.id) == []

    frame._notes_request_exit_edit()

    assert frame.notes_store.list_entries(notebook.id, include_deleted=True) == []
    assert frame.notes_controller.notes_view == "note_detail"


def test_new_entry_stays_draft_only_until_saved(frame):
    notebook = frame.notes_store.create_notebook("draft only notebook")
    frame._notes_select_notebook(notebook.id, view="note_detail")
    frame._notes_create_entry()

    assert frame.notes_controller.notes_view == "note_edit"
    assert frame.notes_controller.active_entry_id == ""
    assert frame.notes_store.list_entries(notebook.id) == []

    frame.notes_editor.SetValue("draft only entry")
    frame._on_notes_editor_changed(None)
    assert frame._notes_save_current_entry()

    entries = frame.notes_store.list_entries(notebook.id)
    assert len(entries) == 1
    assert entries[0].content == "draft only entry"


def test_new_entry_stays_draft_only_until_saved(frame):
    notebook = frame.notes_store.create_notebook("draft only notebook")
    frame._notes_select_notebook(notebook.id, view="note_detail")
    frame._notes_create_entry()

    assert frame.notes_controller.notes_view == "note_edit"
    assert frame.notes_controller.active_entry_id == ""
    assert frame.notes_store.list_entries(notebook.id) == []

    frame.notes_editor.SetValue("draft only entry")
    frame._on_notes_editor_changed(None)
    assert frame._notes_save_current_entry()

    entries = frame.notes_store.list_entries(notebook.id)
    assert len(entries) == 1
    assert entries[0].content == "draft only entry"


def test_notes_edit_cancel_dirty_prompts_and_can_discard(frame, monkeypatch):
    notebook = frame.notes_store.create_notebook("cancel notebook")
    entry = frame.notes_store.create_entry(notebook.id, "original", source="manual")
    frame._notes_select_entry(entry.id, view="note_edit")
    frame.notes_editor.SetValue("edited via cancel")
    frame._on_notes_editor_changed(None)

    seen = {"prompt": 0, "minimize": 0}
    monkeypatch.setattr(frame, "_prompt_notes_dirty_exit", lambda: seen.__setitem__("prompt", seen["prompt"] + 1) or "discard")
    monkeypatch.setattr(frame, "_minimize_to_tray", lambda: seen.__setitem__("minimize", seen["minimize"] + 1))

    frame._on_notes_key_down(type("Evt", (), {
        "GetKeyCode": lambda self: wx.WXK_ESCAPE,
        "ControlDown": lambda self: False,
        "AltDown": lambda self: False,
        "Skip": lambda self: None,
    })())

    assert seen["prompt"] == 1
    assert seen["minimize"] == 0
    assert frame.notes_controller.notes_view == "note_detail"
    assert frame.notes_controller.entry_editor_dirty is False


def test_notes_menu_actions_create_and_edit_notes(frame, monkeypatch):
    class _TextEntryDialog:
        def __init__(self, _parent, _message, _title, value=""):
            self._value = value or "新建笔记本"

        def ShowModal(self):
            return main.wx.ID_OK

        def GetValue(self):
            return self._value

        def Destroy(self):
            pass

    monkeypatch.setattr(main.wx, "TextEntryDialog", _TextEntryDialog)
    monkeypatch.setattr(frame, "_confirm", lambda *_a, **_k: True)

    frame._notes_create_notebook()
    assert frame.notes_store.search_notebooks("新建笔记本")

    notebook = frame.notes_store.search_notebooks("新建笔记本")[0]
    frame._notes_select_notebook(notebook.id)
    frame._notes_rename_notebook()
    assert frame.notes_store.get_notebook(notebook.id).title

    frame._notes_create_entry()
    assert frame.notes_controller.active_entry_id == ""
    assert frame.notes_store.list_entries(notebook.id) == []
    frame.notes_editor.SetValue("menu created entry")
    frame._on_notes_editor_changed(None)
    assert frame._notes_save_current_entry()

    entry = frame.notes_store.list_entries(notebook.id)[0]
    frame._notes_select_entry(entry.id)
    frame._notes_pin_entry()
    assert frame.notes_store.get_entry(entry.id).pinned is True
    frame._notes_move_entry_to_bottom()
    assert frame.notes_store.list_entries(notebook.id)[-1].id == entry.id
    monkeypatch.setattr(frame, "_confirm", lambda *_a, **_k: True)
    frame._notes_delete_entry()
    assert frame.notes_store.get_entry(entry.id) is None


def test_notes_create_entry_from_detail_keeps_blank_draft_and_focuses_editor(frame):
    notebook = frame.notes_store.create_notebook("existing notebook")
    existing = frame.notes_store.create_entry(notebook.id, "existing content", source="manual")
    frame._notes_select_notebook(notebook.id, view="note_detail")
    frame.notes_entry_list.SetSelection(frame._notes_entry_ids.index(existing.id))

    assert frame._notes_create_entry() is True

    assert frame.notes_controller.notes_view == "note_edit"
    assert frame.notes_controller.active_notebook_id == notebook.id
    assert frame.notes_controller.active_entry_id == ""
    assert frame.notes_editor.GetValue() == ""
    assert frame.notes_editor.HasFocus()
    assert frame.notes_store.list_entries(notebook.id) == [existing]


def test_notes_menu_actions_search_and_import_entries(frame, monkeypatch, tmp_path):
    class _TextEntryDialog:
        def __init__(self, _parent, _message, _title, value=""):
            self._value = value or "搜索笔记本"

        def ShowModal(self):
            return main.wx.ID_OK

        def GetValue(self):
            return self._value

        def Destroy(self):
            pass

    class _FileDialog:
        def __init__(self, _parent, *_args, **_kwargs):
            self._path = str(tmp_path / "import.txt")

        def ShowModal(self):
            return main.wx.ID_OK

        def GetPath(self):
            return self._path

        def Destroy(self):
            pass

    monkeypatch.setattr(main.wx, "TextEntryDialog", _TextEntryDialog)
    monkeypatch.setattr(main.wx, "FileDialog", _FileDialog)
    monkeypatch.setattr(frame, "_confirm", lambda *_a, **_k: True)
    monkeypatch.setattr(frame, "_notes_get_clipboard_text", lambda: "剪贴板一行\n剪贴板二行")

    frame._notes_create_notebook()
    notebook = frame.notes_store.search_notebooks("搜索笔记本")[0]
    frame._notes_select_notebook(notebook.id)

    file_path = tmp_path / "import.txt"
    file_path.write_text("文件一行\n文件二行\n", encoding="utf-8")

    frame._notes_apply_search("搜索笔记本")
    assert frame.notes_notebook_list.GetCount() >= 1

    assert frame._notes_import_from_file()
    file_contents = [entry.content for entry in frame.notes_store.list_entries(notebook.id)]
    assert {"文件一行", "文件二行"}.issubset(file_contents)

    assert frame._notes_import_from_clipboard()
    all_contents = [entry.content for entry in frame.notes_store.list_entries(notebook.id)]
    assert {"剪贴板一行", "剪贴板二行"}.issubset(all_contents)


def test_notes_keyboard_and_menu_key_routes(frame, monkeypatch):
    seen = {"menu": 0, "save": 0}
    monkeypatch.setattr(frame, "_show_notes_menu", lambda: seen.__setitem__("menu", seen["menu"] + 1))
    monkeypatch.setattr(frame, "_notes_save_current_entry", lambda: seen.__setitem__("save", seen["save"] + 1))
    notebook = frame.notes_store.create_notebook("menu routes notebook")
    entry = frame.notes_store.create_entry(notebook.id, "entry", source="manual")

    class _Event:
        def __init__(self, key, ctrl=False, alt=False):
            self._key = key
            self._ctrl = ctrl
            self._alt = alt
            self.skipped = False

        def GetKeyCode(self):
            return self._key

        def ControlDown(self):
            return self._ctrl

        def AltDown(self):
            return self._alt

        def Skip(self):
            self.skipped = True

    frame._notes_select_notebook(notebook.id, view="note_detail")
    frame.notes_notebook_list.SetFocus()
    frame.notes_notebook_list.SetFocus()
    frame._on_notes_key_down(_Event(wx.WXK_MENU))
    frame._notes_select_entry(entry.id, view="note_edit")
    frame.notes_editor.SetFocus()
    frame._on_notes_key_down(_Event(ord("S"), alt=True))
    assert seen["menu"] == 1
    assert seen["save"] == 1


def test_notes_ctrl_c_shortcuts_copy_notebook_and_entry(frame, monkeypatch):
    notebook = frame.notes_store.create_notebook("copy shortcut notebook")
    entry = frame.notes_store.create_entry(notebook.id, "copy shortcut entry", source="manual")
    seen = {"notebook": 0, "entry": 0}

    monkeypatch.setattr(frame, "_notes_copy_notebook_to_clipboard", lambda: seen.__setitem__("notebook", seen["notebook"] + 1) or True)
    monkeypatch.setattr(frame, "_notes_copy_entry_to_clipboard", lambda: seen.__setitem__("entry", seen["entry"] + 1) or True)

    class _Event:
        def __init__(self, notebook_focus=False, entry_focus=False):
            self._notebook_focus = notebook_focus
            self._entry_focus = entry_focus

        def GetKeyCode(self):
            return ord("C")

        def ControlDown(self):
            return True

        def AltDown(self):
            return False

        def Skip(self):
            return None

    frame._notes_select_notebook(notebook.id, view="notes_list")
    monkeypatch.setattr(frame, "_on_any_key_down_escape_minimize", lambda _event: False)
    monkeypatch.setattr(frame.notes_notebook_list, "HasFocus", lambda: True)
    monkeypatch.setattr(frame.notes_entry_list, "HasFocus", lambda: False)
    frame._on_notes_key_down(_Event(notebook_focus=True))

    frame._notes_select_notebook(notebook.id, view="note_detail")
    frame.notes_entry_list.SetSelection(frame._notes_entry_ids.index(entry.id))
    monkeypatch.setattr(frame.notes_notebook_list, "HasFocus", lambda: False)
    monkeypatch.setattr(frame.notes_entry_list, "HasFocus", lambda: True)
    frame._on_notes_key_down(_Event(entry_focus=True))

    assert seen["notebook"] == 1
    assert seen["entry"] == 1


def test_notes_alt_x_shortcuts_create_notebook_and_entry(frame, monkeypatch):
    seen = {"notebook": 0, "entry": 0}
    notebook = frame.notes_store.create_notebook("shortcut notebook")

    monkeypatch.setattr(frame, "_notes_create_notebook", lambda: seen.__setitem__("notebook", seen["notebook"] + 1) or True)
    monkeypatch.setattr(frame, "_notes_create_entry", lambda: seen.__setitem__("entry", seen["entry"] + 1) or True)

    class _Event:
        def __init__(self, *, notebook_focus=False, entry_focus=False):
            self.skipped = False
            self._notebook_focus = notebook_focus
            self._entry_focus = entry_focus

        def GetKeyCode(self):
            return ord("X")

        def ControlDown(self):
            return False

        def AltDown(self):
            return True

        def Skip(self):
            self.skipped = True

    frame._notes_select_notebook(notebook.id, view="notes_list")
    monkeypatch.setattr(frame.notes_notebook_list, "HasFocus", lambda: True)
    monkeypatch.setattr(frame.notes_entry_list, "HasFocus", lambda: False)
    frame._on_notes_key_down(_Event(notebook_focus=True))

    frame._notes_select_notebook(notebook.id, view="note_detail")
    monkeypatch.setattr(frame.notes_notebook_list, "HasFocus", lambda: False)
    monkeypatch.setattr(frame.notes_entry_list, "HasFocus", lambda: True)
    frame._on_notes_key_down(_Event(entry_focus=True))

    assert seen["notebook"] == 1
    assert seen["entry"] == 1


def test_notes_prompt_search_applies_query_and_returns_focus_to_notebook_list(frame, monkeypatch):
    first = frame.notes_store.create_notebook("alpha note")
    frame.notes_store.create_notebook("beta note")
    frame._notes_select_notebook(first.id, view="notes_list")
    seen = {"focused": 0}

    class _TextEntryDialog:
        def __init__(self, _parent, _message, _title, value=""):
            self._value = "alpha"

        def ShowModal(self):
            return main.wx.ID_OK

        def GetValue(self):
            return self._value

        def Destroy(self):
            pass

    monkeypatch.setattr(main.wx, "TextEntryDialog", _TextEntryDialog)
    monkeypatch.setattr(frame.notes_notebook_list, "SetFocus", lambda: seen.__setitem__("focused", seen["focused"] + 1))

    assert frame._notes_prompt_search() is True
    assert frame._notes_search_query == "alpha"
    assert frame.notes_notebook_list.GetCount() == 1
    assert seen["focused"] == 1


def test_remote_conflict_copy_uses_semantic_last_modified_by(tmp_path):
    store = main.NotesStore(tmp_path / "notes.db", device_id="desktop-test")
    store.initialize()
    sync = main.NotesSyncService(store)
    notebook = store.create_notebook("冲突测试")
    entry = store.create_entry(notebook.id, "本地内容", source="manual")
    store.update_entry(entry.id, "本地更新")

    sync.apply_remote_ops(
        [
            {
                "entity_type": "entry",
                "action": "update",
                "entity_id": entry.id,
                "source_device": "mobile-9",
                "base_version": 1,
                "payload": {
                    "content": "远端冲突内容",
                    "updated_at": "2026-04-12T10:11:12+00:00",
                },
            }
        ]
    )

    conflict_copies = [item for item in store.list_entries(notebook.id, include_deleted=True) if item.is_conflict_copy]
    assert conflict_copies
    assert conflict_copies[0].device_id == "mobile-9"
    assert conflict_copies[0].last_modified_by == "mobile"


def test_local_notes_save_broadcasts_notes_changed(frame):
    seen = []

    class _FakeServer:
        def broadcast_event(self, payload):
            seen.append(dict(payload))

    frame._remote_ws_server = _FakeServer()
    notebook = frame.notes_store.create_notebook("broadcast notebook")
    entry = frame.notes_store.create_entry(notebook.id, "original", source="manual")
    frame._notes_select_entry(entry.id, view="note_edit")
    frame.notes_editor.SetValue("updated content")
    frame._on_notes_editor_changed(None)

    assert frame._notes_save_current_entry()
    assert any(item.get("type") == "notes_changed" for item in seen)
    assert any(item.get("type") == "notes_sync_status" for item in seen)
    assert "待同步" in frame.notes_sync_hint


def test_notes_outbox_lifecycle_transitions_and_retry_counts(tmp_path):
    store = main.NotesStore(tmp_path / "notes.db", device_id="desktop-test")
    store.initialize()
    sync = main.NotesSyncService(store)
    notebook = store.create_notebook("outbox lifecycle")
    store.create_entry(notebook.id, "entry one", source="manual")
    pending = store.list_pending_ops(limit=10)

    assert pending
    assert all(item.status == "pending" for item in pending)
    assert all(item.retry_count == 0 for item in pending)

    sending = sync.claim_outbox_ops(limit=10)
    assert len(sending) == len(pending)
    assert all(item.status == "sending" for item in sending)
    assert all(item.retry_count == 1 for item in sending)
    assert len(store.list_outbox_ops(statuses=("sending",))) == len(pending)

    sync.fail_outbox_ops([item.op_id for item in sending])
    failed = store.list_outbox_ops(statuses=("failed",))
    assert len(failed) == len(pending)
    assert all(item.retry_count == 1 for item in failed)

    retried = sync.claim_outbox_ops(limit=10)
    assert len(retried) == len(pending)
    assert all(item.status == "sending" for item in retried)
    assert all(item.retry_count == 2 for item in retried)

    sync.ack_outbox_ops([item.op_id for item in retried])
    acked = store.list_outbox_ops(statuses=("acked",))
    assert len(acked) == len(pending)


def test_notes_sync_status_bar_reports_pending_sending_failed(frame, monkeypatch):
    statuses = []
    monkeypatch.setattr(frame, "SetStatusText", lambda text: statuses.append(text))
    notebook = frame.notes_store.create_notebook("sync status notebook")
    entry = frame.notes_store.create_entry(notebook.id, "original", source="manual")

    frame._notes_select_entry(entry.id, view="note_edit")
    frame.notes_editor.SetValue("updated content")
    frame._on_notes_editor_changed(None)
    frame._notes_save_current_entry()

    claimed = frame.notes_sync.claim_outbox_ops(limit=1)
    frame.notes_sync.fail_outbox_ops([item.op_id for item in claimed])

    assert any("待同步" in text for text in statuses)
    assert any("同步中" in text for text in statuses)
    assert any("同步失败" in text for text in statuses)


@pytest.mark.parametrize(
    ("source_device", "expected_suffix"),
    [
        ("mobile-9", "（冲突副本-手机）"),
        ("desktop-8", "（冲突副本-电脑）"),
    ],
)
def test_remote_notebook_conflict_copy_titles_use_spec_suffix(tmp_path, source_device, expected_suffix):
    store = main.NotesStore(tmp_path / "notes.db", device_id="desktop-test")
    store.initialize()
    sync = main.NotesSyncService(store)
    notebook = store.create_notebook("conflict notebook")
    store.update_notebook(notebook.id, "local notebook update")

    sync.apply_remote_ops(
        [
            {
                "entity_type": "notebook",
                "action": "update",
                "entity_id": notebook.id,
                "source_device": source_device,
                "base_version": 1,
                "payload": {
                    "title": "remote notebook update",
                    "updated_at": "2026-04-12T10:11:12+00:00",
                },
            }
        ]
    )

    conflict_copies = [item for item in store.list_notebooks(include_deleted=True) if item.is_conflict_copy]
    assert conflict_copies
    assert any(item.title.endswith(expected_suffix) for item in conflict_copies)
    assert all(source_device not in item.title for item in conflict_copies)


def test_remote_refresh_preserves_intentionally_empty_edit_draft(frame):
    notebook = frame.notes_store.create_notebook("empty draft notebook")
    entry = frame.notes_store.create_entry(notebook.id, "persisted content", source="manual")
    frame._notes_select_entry(entry.id, view="note_edit")
    frame.notes_editor.SetValue("")
    frame._on_notes_editor_changed(None)

    frame.notes_sync.apply_remote_ops(
        [
            {
                "entity_type": "entry",
                "action": "update",
                "entity_id": entry.id,
                "source_device": "mobile-9",
                "base_version": 1,
                "payload": {
                    "content": "remote content",
                    "updated_at": "2026-04-12T10:11:12+00:00",
                },
            }
        ]
    )

    assert frame.notes_editor.GetValue() == ""


def test_remote_notes_push_ops_broadcasts_changed_once(frame):
    seen = []

    class _FakeServer:
        def broadcast_event(self, payload):
            seen.append(dict(payload))

    frame._remote_ws_server = _FakeServer()
    notebook = frame.notes_store.create_notebook("push once notebook")

    frame._remote_api_notes_push_ops(
        {
            "ops": [
                {
                    "op_id": "op-remote-1",
                    "entity_type": "entry",
                    "entity_id": "remote-entry-1",
                    "action": "create",
                    "payload": {
                        "notebook_id": notebook.id,
                        "content": "remote entry",
                        "created_at": "2026-04-12T10:11:12+00:00",
                    },
                }
            ]
        }
    )

    assert [item.get("type") for item in seen].count("notes_changed") == 1


def test_notes_push_ops_returns_acked_op_ids(tmp_path):
    store = main.NotesStore(tmp_path / "notes.db", device_id="desktop-test")
    store.initialize()
    sync = main.NotesSyncService(store)

    result = sync.push_ops(
        [
            {
                "op_id": "op-1",
                "entity_type": "notebook",
                "entity_id": "nb-1",
                "action": "create",
                "payload": {"title": "push notebook"},
            }
        ]
    )

    assert result["acked"] == ["op-1"]


def test_notes_notebook_list_is_visible_in_main_view(frame):
    assert frame.notes_notebook_list.IsShown()
    assert frame.notes_controller.root_tab == "notes"
    assert frame.notes_controller.notes_view == "notes_list"
    assert frame.notes_notebook_list.GetName() == "笔记"
    assert frame.notes_notebook_list.GetToolTipText() == "笔记"


def test_notes_tab_order_keeps_notebook_list_reachable_while_editing(frame):
    notebook = frame.notes_store.create_notebook("tab notebook")
    entry = frame.notes_store.create_entry(notebook.id, "tab entry", source="manual")
    frame._notes_select_entry(entry.id, view="note_edit")

    assert frame.notes_list_panel.IsShown()
    assert frame.notes_notebook_list.IsShown()
    assert frame.notes_notebook_list.IsEnabled()
    assert frame.notes_editor.IsEnabled()
    assert frame.notes_editor.GetName() == "笔记"
    assert frame.notes_editor.GetToolTipText() == "笔记"
    assert frame.notes_edit_title.GetLabel() == "笔记"
