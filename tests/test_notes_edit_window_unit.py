"""Independent note entry edit window and export-migration delete behavior."""

import json

import wx
import pytest

import main
from notes_edit_window import NoteEntryEditWindow
from notes_store import NotesStore


def _make_store(tmp_path) -> NotesStore:
    store = NotesStore(tmp_path / "notes" / "notes.db", device_id="edit-window-test")
    store.initialize()
    return store


def _seed_notebook(store) -> tuple:
    notebook = store.create_notebook("工作", record_outbox=False)
    first = store.create_entry(notebook.id, "第一条内容", source="manual", record_outbox=False)
    second = store.create_entry(notebook.id, "第二条内容", source="manual", record_outbox=False)
    return notebook, first, second


# ----- store-level export migration -------------------------------------------------

def test_delete_entry_for_export_clears_body_and_keeps_tombstone(tmp_path):
    store = _make_store(tmp_path)
    notebook, first, _second = _seed_notebook(store)
    normal = store.create_entry(notebook.id, "普通删除保留正文", source="manual", record_outbox=False)
    store.delete_entry(normal.id)

    store.delete_entry_for_export(first.id)

    docs = {doc.id: doc for doc in store.load_documents().entries}
    deleted_doc = docs[first.id]
    assert bool(getattr(deleted_doc, "deleted", False)) is True
    assert str(getattr(deleted_doc, "content", "x") or "") == ""
    ops = [op for op in store.list_pending_ops()
           if str(getattr(op, "entity_id", "") or "") == first.id]
    assert len(ops) == 1
    op = ops[0]
    assert op.action == "delete"
    payload = json.loads(op.payload_json)
    assert payload.get("id") == first.id
    assert payload.get("notebook_id") == notebook.id
    assert payload.get("content", "missing") == ""
    assert payload.get("device_id")
    assert payload.get("version")
    # 普通删除路径保留正文，迁出路径清空正文。
    assert str(getattr(docs[normal.id], "content", "") or "") == "普通删除保留正文"


def test_delete_entry_for_export_base_version_covers_old_versions(tmp_path):
    store = _make_store(tmp_path)
    _notebook, first, _second = _seed_notebook(store)
    store.update_entry(first.id, "更新后的内容", source="manual")
    updated = store.get_entry(first.id)
    store.delete_entry_for_export(first.id)
    ops = [op for op in store.list_pending_ops()
           if str(getattr(op, "entity_id", "") or "") == first.id and op.action == "delete"]
    assert len(ops) == 1
    assert ops[0].base_version == updated.version


# ----- edit window key behavior -----------------------------------------------------

def test_edit_window_save_key_classification():
    from notes_edit_window import should_save_on_enter
    assert should_save_on_enter(wx.WXK_RETURN, ctrl=False, shift=False, alt=False) is True
    assert should_save_on_enter(wx.WXK_NUMPAD_ENTER, ctrl=False, shift=False, alt=False) is True
    assert should_save_on_enter(wx.WXK_RETURN, ctrl=True, shift=False, alt=False) is False
    assert should_save_on_enter(wx.WXK_RETURN, ctrl=False, shift=True, alt=False) is False
    assert should_save_on_enter(wx.WXK_RETURN, ctrl=False, shift=False, alt=True) is False
    assert should_save_on_enter(wx.WXK_SPACE, ctrl=False, shift=False, alt=False) is False


def test_edit_window_enter_saves_and_closes(frame):
    saved = []
    window = NoteEntryEditWindow(
        frame, title="编辑笔记条目", content="初始草稿",
        on_save=lambda text: saved.append(text) or (True, ""),
    )
    try:
        window.editor.SetValue("回车保存的内容")
        event = wx.KeyEvent(wx.wxEVT_KEY_DOWN)
        event.SetKeyCode(wx.WXK_RETURN)
        window._on_key_down(event)
        assert saved == ["回车保存的内容"]
        assert window._saved is True
    finally:
        if not window.IsBeingDeleted():
            window.Destroy()


def test_edit_window_modified_enter_falls_through_to_newline(frame, monkeypatch):
    import notes_edit_window

    saved = []
    window = NoteEntryEditWindow(
        frame, title="编辑笔记条目", content="第一行",
        on_save=lambda text: saved.append(text) or (True, ""),
    )
    try:
        monkeypatch.setattr(notes_edit_window, "should_save_on_enter", lambda *_a, **_k: False)
        event = wx.KeyEvent(wx.wxEVT_KEY_DOWN)
        event.SetKeyCode(wx.WXK_RETURN)
        window._on_key_down(event)
        assert saved == []
        assert window._saved is False
        assert window.editor.GetValue() == "第一行"
    finally:
        if not window.IsBeingDeleted():
            window.Destroy()


def test_edit_window_save_failure_keeps_window_and_draft(frame):
    window = NoteEntryEditWindow(
        frame, title="编辑笔记条目", content="草稿",
        on_save=lambda _text: (False, "保存失败：磁盘错误"),
    )
    try:
        window.editor.SetValue("仍要保留的草稿")
        window._save()
        assert window._saved is False
        assert "磁盘错误" in window.status_label.GetLabel()
        assert window.editor.GetValue() == "仍要保留的草稿"
    finally:
        if not window.IsBeingDeleted():
            window.Destroy()


def test_edit_window_exception_keeps_window_and_reports(frame):
    def _explode(_text):
        raise OSError("db locked")

    window = NoteEntryEditWindow(frame, title="编辑笔记条目", content="x", on_save=_explode)
    try:
        assert window._save() is False
        assert "db locked" in window.status_label.GetLabel()
    finally:
        if not window.IsBeingDeleted():
            window.Destroy()


# ----- frame-level save from edit window ---------------------------------------------

def test_frame_save_from_edit_window_creates_and_updates(frame, monkeypatch):
    store = _make_store(frame.app_data_dir)
    frame.notes_store = store
    notebook, first, _second = _seed_notebook(store)

    monkeypatch.setattr(frame, "_push_remote_notes_conflict", lambda *_a, **_k: None)
    monkeypatch.setattr(type(frame), "_notes_upsert_entry_row", lambda self, _id: None)
    monkeypatch.setattr(frame, "_notes_sync_view_visibility", lambda: None)
    monkeypatch.setattr(type(frame), "_notes_after_local_mutation", lambda self, **kw: None)

    ok, error = frame._notes_save_from_edit_window(
        notebook=notebook, entry=None, base_version=0, content="  新条目内容  ")
    assert (ok, error) == (True, "")
    created = store.get_entry(frame.notes_controller.active_entry_id)
    assert created is not None
    assert created.content == "新条目内容"

    ok, error = frame._notes_save_from_edit_window(
        notebook=notebook, entry=created, base_version=created.version, content="更新后的内容")
    assert (ok, error) == (True, "")
    assert store.get_entry(created.id).content == "更新后的内容"


def test_frame_save_from_edit_window_conflict_creates_copy(frame, monkeypatch):
    store = _make_store(frame.app_data_dir)
    frame.notes_store = store
    notebook, first, _second = _seed_notebook(store)
    conflicts = []
    monkeypatch.setattr(frame, "_push_remote_notes_conflict", lambda payload: conflicts.append(payload))
    monkeypatch.setattr(type(frame), "_notes_upsert_entry_row", lambda self, _id: None)
    monkeypatch.setattr(frame, "_notes_sync_view_visibility", lambda: None)
    monkeypatch.setattr(type(frame), "_notes_after_local_mutation", lambda self, **kw: None)

    ok, _error = frame._notes_save_from_edit_window(
        notebook=notebook, entry=first, base_version=first.version + 5, content="本地编辑")
    assert ok is True
    copy = store.get_entry(frame.notes_controller.active_entry_id)
    assert copy is not None
    assert copy.id != first.id
    assert "冲突副本" in copy.content
    assert conflicts and conflicts[0]["id"] == copy.id


def test_frame_save_from_edit_window_store_failure_returns_error(frame, monkeypatch):
    store = _make_store(frame.app_data_dir)
    frame.notes_store = store
    notebook, first, _second = _seed_notebook(store)

    def _raise(_entry_id, _content, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(store, "update_entry", _raise)
    ok, error = frame._notes_save_from_edit_window(
        notebook=notebook, entry=first, base_version=first.version, content="内容")
    assert ok is False
    assert "disk full" in error
    assert store.get_entry(first.id).content == "第一条内容"


# ----- export to clipboard and delete ------------------------------------------------

def test_export_entries_to_clipboard_and_delete_removes_exact_entries(frame, monkeypatch):
    store = _make_store(frame.app_data_dir)
    frame.notes_store = store
    notebook, first, second = _seed_notebook(store)
    third = store.create_entry(notebook.id, "第三条内容", source="manual", record_outbox=False)
    clipboard = {}
    monkeypatch.setattr(frame, "_set_clipboard_text", lambda text, **_kw: clipboard.setdefault("text", text) or True)
    monkeypatch.setattr(type(frame), "_notes_refresh_entries", lambda self, *_a, **_k: None)
    monkeypatch.setattr(type(frame), "_notes_after_local_mutation", lambda self, **kw: None)
    frame.notes_controller.active_entry_id = first.id

    entries = [store.get_entry(first.id), store.get_entry(second.id)]
    assert frame._notes_export_entries_to_clipboard_and_delete(
        entries, status_text="已导出") is True

    assert clipboard["text"] == "第一条内容\n\n第二条内容"
    for entry_id in (first.id, second.id):
        deleted = store.get_entry(entry_id, include_deleted=True)
        assert deleted is not None
        assert str(getattr(deleted, "content", "x") or "") == ""
    survivor = store.get_entry(third.id, include_deleted=True)
    assert str(getattr(survivor, "content", "") or "") == "第三条内容"
    delete_ops = [op for op in store.list_pending_ops() if op.action == "delete"]
    assert {op.entity_id for op in delete_ops} == {first.id, second.id}
    for op in delete_ops:
        payload = json.loads(op.payload_json)
        assert payload.get("content", "missing") == ""


def test_export_entries_clipboard_failure_keeps_everything(frame, monkeypatch):
    store = _make_store(frame.app_data_dir)
    frame.notes_store = store
    notebook, first, _second = _seed_notebook(store)
    monkeypatch.setattr(frame, "_set_clipboard_text", lambda _text, **_kw: False)
    monkeypatch.setattr(type(frame), "_notes_refresh_entries", lambda self, *_a, **_k: None)
    monkeypatch.setattr(type(frame), "_notes_after_local_mutation", lambda self, **kw: None)

    ok = frame._notes_export_entries_to_clipboard_and_delete(
        [store.get_entry(first.id)], status_text="已导出")
    assert ok is False
    kept = store.get_entry(first.id, include_deleted=True)
    assert str(getattr(kept, "content", "") or "") == "第一条内容"
    assert not [op for op in store.list_pending_ops() if op.action == "delete"]


def test_export_entries_version_conflict_keeps_entry(frame, monkeypatch):
    store = _make_store(frame.app_data_dir)
    frame.notes_store = store
    notebook, first, _second = _seed_notebook(store)
    clipboard = {}
    monkeypatch.setattr(frame, "_set_clipboard_text", lambda text, **_kw: clipboard.setdefault("text", text) or True)
    monkeypatch.setattr(type(frame), "_notes_refresh_entries", lambda self, *_a, **_k: None)
    monkeypatch.setattr(type(frame), "_notes_after_local_mutation", lambda self, **kw: None)

    stale_view = store.get_entry(first.id)
    store.update_entry(first.id, "远程更新后的内容", source="manual")
    real_get = store.get_entry
    calls = {"freeze": 0}

    def flaky_get(entry_id, include_deleted=False):
        if not include_deleted:
            calls["freeze"] += 1
            if calls["freeze"] == 1:
                return stale_view
        return real_get(entry_id, include_deleted=include_deleted)

    monkeypatch.setattr(store, "get_entry", flaky_get)
    ok = frame._notes_export_entries_to_clipboard_and_delete(
        [stale_view], status_text="已导出")
    assert ok is True
    kept = store.get_entry(first.id, include_deleted=True)
    assert str(getattr(kept, "content", "") or "") == "远程更新后的内容"


def test_export_entries_repeated_trigger_is_idempotent(frame, monkeypatch):
    store = _make_store(frame.app_data_dir)
    frame.notes_store = store
    notebook, first, _second = _seed_notebook(store)
    clipboard = {}
    monkeypatch.setattr(frame, "_set_clipboard_text", lambda text, **_kw: clipboard.setdefault("text", text) or True)
    monkeypatch.setattr(type(frame), "_notes_refresh_entries", lambda self, *_a, **_k: None)
    monkeypatch.setattr(type(frame), "_notes_after_local_mutation", lambda self, **kw: None)

    entry = store.get_entry(first.id)
    assert frame._notes_export_entries_to_clipboard_and_delete([entry], status_text="已导出") is True
    second_attempt = frame._notes_export_entries_to_clipboard_and_delete([entry], status_text="已导出")
    assert second_attempt is False  # nothing left to export
    deleted = store.get_entry(first.id, include_deleted=True)
    assert str(getattr(deleted, "content", "x") or "") == ""


# ----- review 2026-11-21: window lifecycle and partial export failure ---------

def test_export_entries_partial_failure_rewrites_clipboard_with_deleted_only(frame, monkeypatch):
    import types

    store = _make_store(frame.app_data_dir)
    frame.notes_store = store
    notebook, first, second = _seed_notebook(store)
    clipboard = {"calls": []}

    def _fake_set_clipboard(text, *, flush=False):
        clipboard["calls"].append((text, flush))
        return True

    monkeypatch.setattr(frame, "_set_clipboard_text", _fake_set_clipboard)
    monkeypatch.setattr(type(frame), "_notes_refresh_entries", lambda self, *_a, **_k: None)
    monkeypatch.setattr(type(frame), "_notes_after_local_mutation", lambda self, **kw: None)
    frame.notes_controller.active_entry_id = first.id

    frozen_first = store.get_entry(first.id)
    frozen_second = store.get_entry(second.id)
    real_get = store.get_entry

    def flaky_get(entry_id, include_deleted=False):
        if include_deleted and str(entry_id) == str(second.id):
            # Simulate a concurrent version bump observed at delete time.
            return types.SimpleNamespace(
                id=second.id,
                notebook_id=notebook.id,
                version=int(frozen_second.version) + 3,
                content=frozen_second.content,
            )
        return real_get(entry_id, include_deleted=include_deleted)

    monkeypatch.setattr(store, "get_entry", flaky_get)

    ok = frame._notes_export_entries_to_clipboard_and_delete(
        [frozen_first, frozen_second], status_text="已导出")
    assert ok is True
    # First copy carries everything with the export flush; the failure
    # rewrite leaves only the actually deleted entry.
    assert clipboard["calls"][0] == ("第一条内容\n\n第二条内容", True)
    assert clipboard["calls"][-1] == ("第一条内容", True)
    deleted_first = store.get_entry(first.id, include_deleted=True)
    assert str(getattr(deleted_first, "content", "x") or "") == ""
    kept_second = store.get_entry(second.id, include_deleted=True)
    assert str(getattr(kept_second, "content", "") or "") == "第二条内容"


def test_open_entry_edit_window_twice_raises_existing(frame, monkeypatch):
    store = _make_store(frame.app_data_dir)
    frame.notes_store = store
    notebook, first, _second = _seed_notebook(store)
    raised = []
    monkeypatch.setattr(NoteEntryEditWindow, "Raise", lambda self: raised.append(self))
    try:
        assert frame._notes_open_entry_edit_window(
            notebook=notebook, entry=first, base_version=int(first.version)) is True
        windows = frame._notes_entry_edit_windows()
        assert len(windows) == 1
        assert frame._notes_open_entry_edit_window(
            notebook=notebook, entry=first, base_version=int(first.version)) is True
        assert len(frame._notes_entry_edit_windows()) == 1
        assert raised == [windows[0]]
    finally:
        for window in list(frame._notes_entry_edit_windows()):
            if not window.IsBeingDeleted():
                window.Destroy()


def test_notes_edit_entry_without_selection_returns_false(frame, monkeypatch):
    store = _make_store(frame.app_data_dir)
    frame.notes_store = store
    notebook, _first, _second = _seed_notebook(store)
    frame.notes_controller.active_notebook_id = notebook.id
    frame.notes_controller.active_entry_id = ""
    opened = []
    monkeypatch.setattr(
        frame, "_notes_open_entry_edit_window",
        lambda **kw: opened.append(kw) or True,
    )
    assert frame._notes_edit_entry() is False
    assert opened == []


def test_edit_window_dirty_close_cancel_keeps_window_and_draft(frame, monkeypatch):
    import notes_edit_window

    prompts = []

    class _Dialog:
        def __init__(self, *args, **kwargs):
            prompts.append((args, kwargs))

        def ShowModal(self):
            return wx.ID_NO

        def Destroy(self):
            pass

    monkeypatch.setattr(notes_edit_window.wx, "MessageDialog", _Dialog)
    window = NoteEntryEditWindow(
        frame, title="编辑笔记条目", content="初始草稿",
        on_save=lambda text: (True, ""),
    )
    vetoes = []
    skips = []

    class _Event:
        def CanVeto(self):
            return True

        def Veto(self, value=True):
            vetoes.append(value)

        def Skip(self):
            skips.append(True)

    try:
        window.editor.SetValue("修改后的草稿")
        window._on_close(_Event())
        assert len(prompts) == 1
        assert vetoes == [True]
        assert skips == []
        assert window.editor.GetValue() == "修改后的草稿"
        assert window._saved is False
    finally:
        if not window.IsBeingDeleted():
            window.Destroy()


def test_edit_window_dirty_close_confirm_and_clean_close_skip_prompt(frame, monkeypatch):
    import notes_edit_window

    prompts = []

    class _Dialog:
        def __init__(self, *args, **kwargs):
            prompts.append((args, kwargs))

        def ShowModal(self):
            return wx.ID_YES

        def Destroy(self):
            pass

    monkeypatch.setattr(notes_edit_window.wx, "MessageDialog", _Dialog)

    class _Event:
        def CanVeto(self):
            return True

        def Veto(self, value=True):
            raise AssertionError("clean close must not be vetoed")

        def Skip(self):
            self.skipped = True

    dirty = NoteEntryEditWindow(
        frame, title="编辑笔记条目", content="初始草稿",
        on_save=lambda text: (True, ""),
    )
    try:
        dirty.editor.SetValue("已修改")
        event = _Event()
        dirty._on_close(event)
        assert len(prompts) == 1
        assert getattr(event, "skipped", False) is True
    finally:
        if not dirty.IsBeingDeleted():
            dirty.Destroy()

    prompts.clear()
    clean = NoteEntryEditWindow(
        frame, title="编辑笔记条目", content="原样内容",
        on_save=lambda text: (True, ""),
    )
    try:
        event = _Event()
        clean._on_close(event)
        assert prompts == []
        assert getattr(event, "skipped", False) is True
    finally:
        if not clean.IsBeingDeleted():
            clean.Destroy()


def test_delete_entry_for_export_conflicts_on_concurrent_version(tmp_path, monkeypatch):
    import types

    store = _make_store(tmp_path)
    notebook, first, _second = _seed_notebook(store)
    store.update_entry(first.id, "并发更新后的内容", source="manual")
    live = NotesStore.get_entry(store, first.id, include_deleted=True)

    # Simulate a concurrent bump between the freeze read and the UPDATE.
    monkeypatch.setattr(
        store, "get_entry",
        lambda entry_id, include_deleted=False: types.SimpleNamespace(
            id=first.id,
            notebook_id=notebook.id,
            version=int(live.version) - 1,
            content="旧快照",
        ),
    )

    with pytest.raises(main.NotesPlacementConflict):
        store.delete_entry_for_export(first.id)

    current = NotesStore.get_entry(store, first.id, include_deleted=True)
    assert current is not None
    assert bool(getattr(current, "deleted", False)) is False
    assert current.content == "并发更新后的内容"
    assert not [op for op in store.list_pending_ops() if op.action == "delete"]


def test_export_entries_all_conflicts_clear_clipboard(frame, monkeypatch):
    import types

    store = _make_store(frame.app_data_dir)
    frame.notes_store = store
    notebook, first, second = _seed_notebook(store)
    clipboard_texts = []
    monkeypatch.setattr(
        frame, "_set_clipboard_text",
        lambda text, **_kw: clipboard_texts.append(text) or True,
    )
    cleared = []

    class _Clipboard:
        def Open(self):
            return True

        def Clear(self):
            cleared.append(True)
            return True

        def Close(self):
            return True

    monkeypatch.setattr(main.wx, "TheClipboard", _Clipboard())
    monkeypatch.setattr(type(frame), "_notes_refresh_entries", lambda self, *_a, **_k: None)
    monkeypatch.setattr(type(frame), "_notes_after_local_mutation", lambda self, **kw: None)

    # Both entries hit a concurrent version bump at delete time.
    frozen_first = store.get_entry(first.id)
    frozen_second = store.get_entry(second.id)
    real_get = store.get_entry

    def conflict_get(entry_id, include_deleted=False):
        if include_deleted:
            live = real_get(entry_id, include_deleted=True)
            return types.SimpleNamespace(
                id=entry_id,
                notebook_id=notebook.id,
                version=int(live.version) + 3,
                content=str(getattr(live, "content", "") or ""),
            )
        return real_get(entry_id, include_deleted=include_deleted)

    monkeypatch.setattr(store, "get_entry", conflict_get)

    ok = frame._notes_export_entries_to_clipboard_and_delete(
        [frozen_first, frozen_second], status_text="已导出")

    assert ok is True
    # The first copy carried everything; with nothing deleted the clipboard
    # is cleared instead of keeping text that was never exported.
    assert clipboard_texts == ["第一条内容\n\n第二条内容"]
    assert cleared == [True]
    for entry_id in (first.id, second.id):
        kept = store.get_entry(entry_id, include_deleted=True)
        assert str(getattr(kept, "content", "") or "") != ""


def test_select_entry_embedded_edit_defers_to_open_window(frame, monkeypatch):
    store = _make_store(frame.app_data_dir)
    frame.notes_store = store
    notebook, first, _second = _seed_notebook(store)
    raised = []
    monkeypatch.setattr(NoteEntryEditWindow, "Raise", lambda self: raised.append(self))
    frame._notes_select_notebook(notebook.id, view="note_detail")
    try:
        assert frame._notes_open_entry_edit_window(
            notebook=notebook, entry=first, base_version=int(first.version)) is True
        window = frame._notes_entry_edit_windows()[0]
        frame._notes_select_entry(first.id, view="note_edit")
        assert frame.notes_controller.notes_view == "note_detail"
        assert raised == [window]
        assert len(frame._notes_entry_edit_windows()) == 1
    finally:
        for existing in list(frame._notes_entry_edit_windows()):
            if not existing.IsBeingDeleted():
                existing.Destroy()
