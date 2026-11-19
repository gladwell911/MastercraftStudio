import json
import main
import pytest
import sqlite3
import wx
from pathlib import Path

from main import resolve_notes_data_dir as original_resolve_notes_data_dir


def test_packaged_notes_resolve_personal_onedrive_and_source_stays_local(tmp_path, monkeypatch):
    monkeypatch.setattr(main.sys, "frozen", True, raising=False)
    monkeypatch.setenv("OneDriveConsumer", str(tmp_path / "personal"))
    monkeypatch.setenv("OneDrive", str(tmp_path / "other"))
    target = tmp_path / "personal" / "OneDrive" / "code" / "data" / "sj" / "notes.db"
    target.parent.mkdir(parents=True)
    main.NotesStore(target, device_id="test").initialize()
    assert original_resolve_notes_data_dir() == target.parent
    monkeypatch.delattr(main.sys, "frozen")
    assert original_resolve_notes_data_dir() == Path(r"D:\code\note")


def test_packaged_notes_falls_back_to_onedrive_env(tmp_path, monkeypatch):
    monkeypatch.setattr(main.sys, "frozen", True, raising=False)
    monkeypatch.delenv("OneDriveConsumer", raising=False)
    monkeypatch.setenv("OneDrive", str(tmp_path))
    target = tmp_path / "OneDrive" / "code" / "data" / "sj" / "notes.db"
    target.parent.mkdir(parents=True)
    main.NotesStore(target, device_id="test").initialize()
    assert original_resolve_notes_data_dir() == target.parent


def test_packaged_notes_accepts_legacy_notes_schema(tmp_path, monkeypatch):
    monkeypatch.setattr(main.sys, "frozen", True, raising=False)
    monkeypatch.setenv("OneDriveConsumer", str(tmp_path))
    target = tmp_path / "OneDrive" / "code" / "data" / "sj" / "notes.db"
    target.parent.mkdir(parents=True)
    with sqlite3.connect(target) as conn:
        conn.executescript("""
            CREATE TABLE notebooks (
                id TEXT, title TEXT, created_at TEXT, updated_at TEXT, version INTEGER,
                device_id TEXT, last_modified_by TEXT, is_conflict_copy INTEGER,
                origin_notebook_id TEXT, deleted_at TEXT
            );
            CREATE TABLE note_entries (
                id TEXT, notebook_id TEXT, content TEXT, created_at TEXT, updated_at TEXT,
                sort_order INTEGER, pinned INTEGER, version INTEGER, device_id TEXT,
                last_modified_by TEXT, is_conflict_copy INTEGER, origin_entry_id TEXT,
                source TEXT, deleted_at TEXT
            );
        """)
    assert original_resolve_notes_data_dir() == target.parent
    main.NotesStore(target, device_id="test").initialize()
    assert original_resolve_notes_data_dir() == target.parent


def test_packaged_notes_rejects_unrelated_sqlite_without_modifying_file(tmp_path, monkeypatch):
    monkeypatch.setattr(main.sys, "frozen", True, raising=False)
    monkeypatch.setenv("OneDriveConsumer", str(tmp_path))
    target = tmp_path / "OneDrive" / "code" / "data" / "sj" / "notes.db"
    target.parent.mkdir(parents=True)
    with sqlite3.connect(target) as conn:
        conn.execute("CREATE TABLE unrelated (id TEXT)")
    before = target.read_bytes()
    with pytest.raises(RuntimeError, match="完整性检查"):
        original_resolve_notes_data_dir()
    assert target.read_bytes() == before
    assert not (target.parent / "notes.db-wal").exists()


@pytest.mark.parametrize("condition", ["missing_env", "missing_file", "corrupt", "empty_sqlite"])
def test_packaged_notes_fail_closed_before_store_initialize(tmp_path, monkeypatch, condition):
    monkeypatch.setattr(main.sys, "frozen", True, raising=False)
    monkeypatch.delenv("OneDriveConsumer", raising=False)
    monkeypatch.delenv("OneDrive", raising=False)
    if condition != "missing_env":
        monkeypatch.setenv("OneDriveConsumer", str(tmp_path))
        target = tmp_path / "OneDrive" / "code" / "data" / "sj" / "notes.db"
        target.parent.mkdir(parents=True)
        if condition == "corrupt":
            target.write_bytes(b"not a database")
        elif condition == "empty_sqlite":
            with sqlite3.connect(target):
                pass
    with pytest.raises(RuntimeError):
        original_resolve_notes_data_dir()
    if condition == "missing_file":
        assert not target.exists()


def test_app_shows_notes_error_before_constructing_frame(monkeypatch):
    class Checker:
        def IsAnotherRunning(self):
            return False

    messages = []
    monkeypatch.setattr(main.wx, "SingleInstanceChecker", lambda _name: Checker())
    monkeypatch.setattr(main, "resolve_common_commands_path", lambda: Path("existing.json"))
    monkeypatch.setattr(main, "resolve_notes_data_dir", lambda: (_ for _ in ()).throw(RuntimeError("笔记数据库未同步")))
    monkeypatch.setattr(main.wx, "MessageBox", lambda *args: messages.append(args))
    monkeypatch.setattr(main, "ChatFrame", lambda: pytest.fail("NotesStore.initialize must not run"))
    assert main.ChatApp.OnInit(type("FakeApp", (), {})()) is False
    assert messages[0][0] == "笔记数据库未同步"


def test_screen_reader_primary_tab_order_matches_requested_chat_flow(frame):
    frame.Show()
    notebook = frame.notes_store.create_notebook("tab order notebook")
    frame._notes_select_notebook(notebook.id, view="notes_list")
    frame._current_chat_state["detail_panel_mode"] = "answers"
    frame._apply_detail_panel_mode("answers", refresh_execution=False)

    assert frame.root_tab_order[:8] == [
        frame.input_edit,
        frame.new_chat_button,
        frame.model_combo,
        frame.codex_speed_combo,
        frame.send_button,
        frame.notes_notebook_list,
        frame.history_list,
        frame.answer_list,
    ]
    assert frame.chat_tab_order == frame.root_tab_order[:8]


def test_screen_reader_primary_tab_order_stays_notes_history_answers_in_note_detail(frame):
    frame.Show()
    notebook = frame.notes_store.create_notebook("tab order detail notebook")
    frame.notes_store.create_entry(notebook.id, "tab order detail entry", source="manual")
    frame._notes_select_notebook(notebook.id, view="note_detail")
    frame._current_chat_state["detail_panel_mode"] = "answers"
    frame._apply_detail_panel_mode("answers", refresh_execution=False)

    assert frame.root_tab_order[:8] == [
        frame.input_edit,
        frame.new_chat_button,
        frame.model_combo,
        frame.codex_speed_combo,
        frame.send_button,
        frame.notes_entry_list,
        frame.history_list,
        frame.answer_list,
    ]
    assert frame.chat_tab_order == frame.root_tab_order[:8]


def test_tab_moves_from_notes_to_history_then_answer_then_input(frame):
    frame.Show()
    notebook = frame.notes_store.create_notebook("tab notebook")
    frame._notes_select_notebook(notebook.id, view="notes_list")
    frame.notes_notebook_list.SetSelection(frame._notes_notebook_ids.index(notebook.id))
    frame.notes_notebook_list.SetFocus()

    class E:
        def __init__(self):
            self.skipped = 0

        def GetKeyCode(self):
            return wx.WXK_TAB

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def ShiftDown(self):
            return False

        def Skip(self):
            self.skipped += 1

    notes_event = E()
    frame._on_notes_key_down(notes_event)
    assert frame.history_list.HasFocus()
    assert notes_event.skipped == 0

    history_event = E()
    frame._on_history_key_down(history_event)
    assert frame.answer_list.HasFocus()
    assert history_event.skipped >= 1

    answer_event = E()
    frame._on_answer_key_down(answer_event)
    assert frame.input_edit.HasFocus()
    assert answer_event.skipped >= 1


def test_notes_refresh_ui_skips_list_rebuild_when_projection_unchanged(frame, monkeypatch):
    notebook = frame.notes_store.create_notebook("nb")
    frame.notes_store.create_entry(notebook.id, "entry", source="manual")
    frame.notes_controller.active_notebook_id = notebook.id
    frame._notes_refresh_ui()

    notebook_clears = []
    entry_clears = []
    monkeypatch.setattr(frame.notes_notebook_list, "Clear", lambda: notebook_clears.append(True))
    monkeypatch.setattr(frame.notes_entry_list, "Clear", lambda: entry_clears.append(True))

    frame._notes_refresh_ui()

    assert notebook_clears == []
    assert entry_clears == []


def test_notes_projection_reuses_document_snapshot_until_cursor_changes(frame, monkeypatch):
    notebook = frame.notes_store.create_notebook("cached projection notebook")
    frame.notes_store.create_entry(notebook.id, "cached projection entry", source="manual")
    projection = main.DesktopNotesProjection(frame.notes_store)

    assert [item.id for item in projection.list_notebooks()] == [notebook.id]
    monkeypatch.setattr(frame.notes_store, "load_documents", lambda: pytest.fail("same-cursor projection read should use cache"))

    assert [item.notebook_id for item in projection.list_entries(notebook.id)] == [notebook.id]


def test_notes_remote_ops_skip_full_refresh_for_invisible_entry_change(frame, monkeypatch):
    active_notebook = frame.notes_store.create_notebook("active")
    other_notebook = frame.notes_store.create_notebook("other")
    other_entry = frame.notes_store.create_entry(other_notebook.id, "remote", source="manual")
    frame.notes_controller.active_notebook_id = active_notebook.id
    frame.notes_controller.active_entry_id = ""
    frame.notes_controller.notes_view = "notes_list"
    refresh_calls = []
    monkeypatch.setattr(frame, "_notes_refresh_ui", lambda: refresh_calls.append("full"))

    frame._on_notes_remote_ops_applied(
        {
            "cursor": frame.notes_store.current_cursor(),
            "applied": [
                {
                    "entity_type": "entry",
                    "entity_id": other_entry.id,
                    "entry": other_entry.to_dict(),
                }
            ],
            "conflicts": [],
        }
    )

    assert refresh_calls == []


def test_notes_remote_ops_refresh_visible_notebook_entries(frame, monkeypatch):
    active_notebook = frame.notes_store.create_notebook("active")
    active_entry = frame.notes_store.create_entry(active_notebook.id, "remote", source="manual")
    frame.notes_controller.active_notebook_id = active_notebook.id
    frame.notes_controller.active_entry_id = ""
    frame.notes_controller.notes_view = "notes_list"
    refresh_calls = []
    monkeypatch.setattr(frame, "_notes_refresh_ui", lambda: refresh_calls.append("full"))

    frame._on_notes_remote_ops_applied(
        {
            "cursor": frame.notes_store.current_cursor(),
            "applied": [
                {
                    "entity_type": "entry",
                    "entity_id": active_entry.id,
                    "entry": active_entry.to_dict(),
                }
            ],
            "conflicts": [],
        }
    )

    assert refresh_calls == ["full"]


def test_load_state_restores_notes_ui_state(frame, tmp_path):
    notebook = frame.notes_store.create_notebook("恢复测试")
    entry = frame.notes_store.create_entry(notebook.id, "保存过的草稿", source="manual")

    class _Editor:
        def __init__(self):
            self.value = ""
            self.cursor = None
            self.scroll = None
            self.scroll_get_calls = 0
            self.scroll_set_calls = []

        def SetValue(self, value):
            self.value = value

        def SetInsertionPoint(self, cursor):
            self.cursor = cursor

        def GetScrollPos(self, orientation):
            self.scroll_get_calls += 1
            return self.scroll

        def SetScrollPos(self, orientation, pos, refresh=True):
            self.scroll_set_calls.append((orientation, pos, refresh))
            self.scroll = pos

        def GetValue(self):
            return self.value

    frame.notes_editor = _Editor()
    frame.state_path = tmp_path / "app_state.json"
    frame._current_notes_state = {}
    frame.state_path.write_text(
        json.dumps(
            {
                "notes_ui_state": {
                    "active_root_tab": "notes",
                    "notes_view": "note_edit",
                    "active_notebook_id": notebook.id,
                    "active_entry_id": entry.id,
                    "entry_editor_draft": "未保存草稿",
                    "entry_editor_dirty": True,
                    "entry_editor_cursor": 2,
                    "entry_editor_scroll": 8,
                    "last_sync_cursor": "42",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    frame._load_state()

    assert frame._current_notes_state["active_root_tab"] == "notes"
    assert frame._current_notes_state["notes_view"] == "note_edit"
    assert frame._current_notes_state["entry_editor_draft"] == "未保存草稿"
    assert frame.notes_editor.value == "未保存草稿"
    assert frame.notes_editor.cursor == 2
    assert frame.notes_editor.scroll == 8
    assert frame.notes_editor.scroll_set_calls[-1][1] == 8
    assert frame._current_notes_state["last_sync_cursor"] == "42"


def test_load_state_restores_note_detail_view_for_existing_notebook(frame, tmp_path):
    notebook = frame.notes_store.create_notebook("detail restore notebook")
    entry = frame.notes_store.create_entry(notebook.id, "detail restore entry", source="manual")
    frame.state_path = tmp_path / "app_state.json"
    frame._current_notes_state = {}
    frame.state_path.write_text(
        json.dumps(
            {
                "notes_ui_state": {
                    "active_root_tab": "notes",
                    "notes_view": "note_detail",
                    "active_notebook_id": notebook.id,
                    "active_entry_id": entry.id,
                    "entry_editor_draft": "",
                    "entry_editor_dirty": False,
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    frame._load_state()

    assert frame._current_notes_state["active_root_tab"] == "notes"
    assert frame._current_notes_state["notes_view"] == "note_detail"
    assert frame.notes_controller.notes_view == "note_detail"
    assert frame.notes_controller.active_notebook_id == notebook.id
    assert frame.notes_controller.active_entry_id == entry.id


def test_save_state_captures_notes_editor_cursor_and_scroll(frame):
    class _Editor:
        def __init__(self):
            self.value = ""
            self.cursor = 0
            self.scroll = 0
            self.scroll_get_calls = 0
            self.scroll_set_calls = []

        def SetValue(self, value):
            self.value = value

        def GetValue(self):
            return self.value

        def SetInsertionPoint(self, cursor):
            self.cursor = cursor

        def GetInsertionPoint(self):
            return self.cursor

        def SetInsertionPointEnd(self):
            self.cursor = len(self.value)

        def GetScrollPos(self, orientation):
            self.scroll_get_calls += 1
            return self.scroll

        def SetScrollPos(self, orientation, pos, refresh=True):
            self.scroll_set_calls.append((orientation, pos, refresh))
            self.scroll = pos

    notebook = frame.notes_store.create_notebook("save cursor notebook")
    entry = frame.notes_store.create_entry(notebook.id, "save cursor entry", source="manual")
    frame.notes_editor = _Editor()
    frame._notes_select_entry(entry.id, view="note_edit")
    frame.notes_editor.value = "edited cursor draft"
    frame.notes_editor.cursor = 7
    frame.notes_editor.scroll = 13
    frame._on_notes_editor_changed(None)
    frame._save_state()

    saved_state = json.loads(frame.state_path.read_text(encoding="utf-8"))["notes_ui_state"]
    assert saved_state["entry_editor_draft"] == "edited cursor draft"
    assert saved_state["entry_editor_cursor"] == 7
    assert saved_state["entry_editor_scroll"] == 13


def test_notes_transport_broadcasts_conflict_and_sync_status(frame):
    seen = []

    class _Transport:
        def publish_event_threadsafe(self, payload):
            seen.append(payload)

    frame._remote_nats_transport = _Transport()

    frame._push_remote_notes_conflict({"entry_id": "e1", "message": "conflict"})
    frame._push_remote_notes_sync_status("synced", cursor="44", message="ok")

    types = [item["type"] for item in seen]
    assert "notes_conflict" in types
    assert "notes_sync_status" in types


def test_remote_notes_apis_return_retired(frame):
    status, body = frame._remote_api_notes_snapshot(None)
    assert status == 410
    assert body["error"] == "retired"

    status, body = frame._remote_api_notes_pull_since({"cursor": "7"})
    assert status == 410
    assert body["error"] == "retired"

    status, body = frame._remote_api_notes_push_ops({"ops": [{"entity_type": "entry"}]})
    assert status == 410
    assert body["error"] == "retired"

    status, body = frame._remote_api_notes_subscribe({"cursor": "1"})
    assert status == 410
    assert body["error"] == "retired"

    status, body = frame._remote_api_notes_ack({"op_ids": ["op-1"]})
    assert status == 410
    assert body["error"] == "retired"

    status, body = frame._remote_api_notes_ping({"cursor": "9"})
    assert status == 410
    assert body["error"] == "retired"


def test_resolve_notes_data_dir_uses_d_code_note():
    p = original_resolve_notes_data_dir()
    assert str(p).lower() == r"d:\code\note"


def test_chat_frame_uses_independent_notes_data_dir(monkeypatch, tmp_path):
    app_data_dir = tmp_path / "app"
    notes_data_dir = tmp_path / "notes"
    monkeypatch.setattr(main, "resolve_app_data_dir", lambda: app_data_dir)
    monkeypatch.setattr(main, "resolve_notes_data_dir", lambda: notes_data_dir)
    monkeypatch.setattr(main.ChatFrame, "_legacy_state_paths", lambda self: [self.state_path])
    monkeypatch.setattr(main.ChatFrame, "_migrate_legacy_state_if_needed", lambda self: None)

    f = main.ChatFrame()
    try:
        assert f.notes_db_path == notes_data_dir / "notes.db"
        assert f.chat_db_path == app_data_dir / "chat_history.db"
        assert f.notes_db_path.parent.exists()
    finally:
        f.Destroy()


def test_frame_fixture_uses_temp_notes_dir(frame, tmp_path):
    assert frame.notes_db_path == tmp_path / "notes" / "notes.db"


def test_alt_b_focuses_empty_notes_list_placeholder(frame, monkeypatch):
    focused = []
    monkeypatch.setattr(frame.notes_notebook_list, "SetFocus", lambda: focused.append("notes_notebook_list"))

    class AltBEvent:
        def GetKeyCode(self):
            return ord("B")

        def ControlDown(self):
            return False

        def AltDown(self):
            return True

        def Skip(self):
            raise AssertionError("Alt+B should focus the notes list even when it is empty")

    frame._notes_select_notebook("", view="notes_list")
    frame.input_edit.SetFocus()
    focused.clear()

    frame._on_char_hook(AltBEvent())

    assert frame.notes_notebook_list.GetString(0) == "暂无笔记本"
    assert frame.notes_notebook_list.IsEnabled()
    assert focused == ["notes_notebook_list"]


def test_save_state_skips_notes_editor_capture_by_default(frame, monkeypatch, tmp_path):
    frame.state_path = tmp_path / "app_state.json"
    frame.notes_controller.notes_view = "note_edit"
    frame.notes_controller.entry_editor_dirty = True
    frame.notes_controller.entry_editor_draft = "cached draft"
    monkeypatch.setattr(frame.notes_editor, "GetValue", lambda: pytest.fail("chat state save should not read large notes editor"))

    frame._save_state()

    data = json.loads(frame.state_path.read_text(encoding="utf-8"))
    assert data["notes_ui_state"]["entry_editor_draft"] == "cached draft"


def test_save_state_can_capture_notes_editor_when_explicit(frame, monkeypatch, tmp_path):
    frame.state_path = tmp_path / "app_state.json"
    frame.notes_controller.notes_view = "note_edit"
    frame.notes_controller.entry_editor_dirty = True
    monkeypatch.setattr(frame.notes_editor, "GetValue", lambda: "fresh large draft")

    frame._save_state(capture_notes_editor=True)

    data = json.loads(frame.state_path.read_text(encoding="utf-8"))
    assert data["notes_ui_state"]["entry_editor_draft"] == "fresh large draft"


def test_notes_arrow_selection_does_not_force_focus_or_full_ui_refresh(frame, monkeypatch):
    notebook = frame.notes_store.create_notebook("nb")
    frame.notes_store.create_entry(notebook.id, "entry", source="manual")
    frame.notes_controller.notes_view = "notes_list"
    frame._notes_refresh_ui()
    frame.notes_notebook_list.SetSelection(0)
    focus_calls = []
    refresh_calls = []
    monkeypatch.setattr(frame.notes_notebook_list, "SetFocus", lambda: focus_calls.append("notebook"))
    monkeypatch.setattr(frame.notes_entry_list, "SetFocus", lambda: focus_calls.append("entry"))
    monkeypatch.setattr(frame, "_notes_refresh_ui", lambda: refresh_calls.append("full"))

    frame._on_notes_notebook_selected(None)

    assert focus_calls == []
    assert refresh_calls == []
    assert frame.notes_controller.active_notebook_id == notebook.id


def test_notes_entry_labels_are_short_previews_for_large_entries(frame):
    notebook = frame.notes_store.create_notebook("nb")
    entry = frame.notes_store.create_entry(notebook.id, "A" * 5000, source="manual")

    frame._notes_refresh_entries(notebook.id, entry.id)

    label = frame.notes_entry_list.GetString(0)
    assert len(label) <= main.NOTES_ENTRY_LABEL_MAX_CHARS + 1
    assert label.endswith("...")


def test_notes_notebook_title_change_updates_single_row_without_full_refresh(frame, monkeypatch):
    notebook = frame.notes_store.create_notebook("old notebook")
    frame._notes_refresh_notebooks(notebook.id)
    monkeypatch.setattr(frame.notes_notebook_list, "Clear", lambda: pytest.fail("notebook title update should not rebuild the listbox"))

    frame.notes_store.update_notebook(notebook.id, "new notebook")
    assert frame._notes_upsert_notebook_row(notebook.id) is True

    assert frame.notes_notebook_list.GetCount() == 1
    assert frame.notes_notebook_list.GetString(0) == "new notebook"
    assert frame._notes_notebook_ids == [notebook.id]


def test_notes_entry_content_change_updates_single_row_without_full_refresh(frame, monkeypatch):
    notebook = frame.notes_store.create_notebook("nb")
    entry = frame.notes_store.create_entry(notebook.id, "old entry", source="manual")
    frame._notes_refresh_entries(notebook.id, entry.id)
    monkeypatch.setattr(frame.notes_entry_list, "Clear", lambda: pytest.fail("entry update should not rebuild the listbox"))

    frame.notes_store.update_entry(entry.id, "new entry body", source="manual")
    assert frame._notes_upsert_entry_row(entry.id) is True

    assert frame.notes_entry_list.GetCount() == 1
    assert frame.notes_entry_list.GetString(0) == "new entry body"
    assert frame._notes_entry_ids == [entry.id]


def test_notes_rename_action_updates_single_notebook_row_without_full_refresh(frame, monkeypatch):
    class _RenameDialog:
        def __init__(self, *_args, **_kwargs):
            pass

        def ShowModal(self):
            return main.wx.ID_OK

        def GetValue(self):
            return "renamed notebook"

        def Destroy(self):
            pass

    notebook = frame.notes_store.create_notebook("old notebook")
    frame._notes_select_notebook(notebook.id, view="notes_list", focus=False)
    monkeypatch.setattr(main.wx, "TextEntryDialog", _RenameDialog)
    monkeypatch.setattr(frame.notes_notebook_list, "Clear", lambda: pytest.fail("rename action should not rebuild notebooks"))

    assert frame._notes_rename_notebook() is True

    assert frame.notes_notebook_list.GetString(frame._notes_notebook_ids.index(notebook.id)) == "renamed notebook"


def test_notes_save_action_updates_single_entry_row_without_full_refresh(frame, monkeypatch):
    notebook = frame.notes_store.create_notebook("nb")
    entry = frame.notes_store.create_entry(notebook.id, "old entry", source="manual")
    frame._notes_select_notebook(notebook.id, entry.id, view="note_edit", focus=False)
    frame.notes_editor.SetValue("saved entry")
    frame._on_notes_editor_changed(None)
    monkeypatch.setattr(frame.notes_entry_list, "Clear", lambda: pytest.fail("save action should not rebuild entries"))

    assert frame._notes_save_current_entry() is True

    assert frame.notes_entry_list.GetString(frame._notes_entry_ids.index(entry.id)) == "saved entry"
    assert frame.notes_controller.notes_view == "note_detail"


def test_notes_entry_arrow_selection_does_not_sync_large_editor_text(frame, monkeypatch):
    notebook = frame.notes_store.create_notebook("nb")
    entry = frame.notes_store.create_entry(notebook.id, "large text", source="manual")
    frame._notes_select_notebook(notebook.id, view="notes_list", focus=False)
    frame.notes_entry_list.SetSelection(frame._notes_entry_ids.index(entry.id))
    frame.notes_controller.notes_view = "note_edit"
    monkeypatch.setattr(frame.notes_editor, "SetValue", lambda value: pytest.fail("arrow selection should not write editor text"))

    frame._on_notes_entry_selected(None)

    assert frame.notes_controller.active_entry_id == entry.id
    assert frame.notes_controller.notes_view == "note_detail"
