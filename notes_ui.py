from __future__ import annotations

import wx


class DesktopNotesController:
    def __init__(self, frame, store=None) -> None:
        self.frame = frame
        self.store = store
        self.root_tab = "notes"
        self.notes_view = "notes_list"
        self.active_notebook_id = ""
        self.active_entry_id = ""
        self.entry_editor_draft = ""
        self.entry_editor_dirty = False
        self.entry_editor_cursor = 0
        self.entry_editor_scroll = 0
        self.entry_editor_base_version = 0
        self.last_sync_cursor = "0"

    def _store(self):
        return self.store or getattr(self.frame, "notes_store", None)

    def _lookup_notebook(self, notebook_id: str):
        notebook_id = str(notebook_id or "").strip()
        if not notebook_id:
            return None
        store = self._store()
        if store is None:
            return None
        getter = getattr(store, "get_notebook", None)
        if not callable(getter):
            return None
        try:
            return getter(notebook_id)
        except Exception:
            return None

    def _lookup_entry(self, entry_id: str):
        entry_id = str(entry_id or "").strip()
        if not entry_id:
            return None
        store = self._store()
        if store is None:
            return None
        getter = getattr(store, "get_entry", None)
        if not callable(getter):
            return None
        try:
            return getter(entry_id)
        except Exception:
            return None

    def _restore_editor_state(self, draft: str, cursor: int, scroll: int) -> None:
        editor = getattr(self.frame, "notes_editor", None)
        if editor is None:
            return
        previous_syncing = bool(getattr(self.frame, "_notes_editor_syncing", False))
        self.frame._notes_editor_syncing = True
        try:
            if hasattr(editor, "SetValue"):
                try:
                    editor.SetValue(draft)
                except Exception:
                    pass
            if hasattr(editor, "SetInsertionPoint"):
                try:
                    editor.SetInsertionPoint(cursor)
                except Exception:
                    pass
            if hasattr(editor, "SetScrollPos"):
                try:
                    editor.SetScrollPos(wx.VERTICAL, scroll, True)
                except Exception:
                    pass
            elif hasattr(editor, "ShowPosition"):
                try:
                    editor.ShowPosition(scroll)
                except Exception:
                    pass
        finally:
            self.frame._notes_editor_syncing = previous_syncing

    def capture_editor_state(self) -> None:
        editor = getattr(self.frame, "notes_editor", None)
        if editor is None:
            return
        if hasattr(editor, "GetValue"):
            try:
                self.entry_editor_draft = str(editor.GetValue() or "")
            except Exception:
                pass
        if hasattr(editor, "GetInsertionPoint"):
            try:
                self.entry_editor_cursor = int(editor.GetInsertionPoint() or 0)
            except Exception:
                pass
        if hasattr(editor, "GetScrollPos"):
            try:
                self.entry_editor_scroll = int(editor.GetScrollPos(wx.VERTICAL) or 0)
            except Exception:
                pass
        elif hasattr(editor, "GetFirstVisibleLine"):
            try:
                self.entry_editor_scroll = int(editor.GetFirstVisibleLine() or 0)
            except Exception:
                pass

    def restore_state(self, state: dict | None) -> None:
        state = dict(state or {})
        saved_root_tab = str(state.get("active_root_tab") or "notes")
        saved_notes_view = str(state.get("notes_view") or "notes_list")
        saved_notebook_id = str(state.get("active_notebook_id") or "")
        saved_entry_id = str(state.get("active_entry_id") or "")
        saved_draft = str(state.get("entry_editor_draft") or "")
        saved_dirty = bool(state.get("entry_editor_dirty", False))
        try:
            saved_base_version = int(state.get("entry_editor_base_version") or 0)
        except Exception:
            saved_base_version = 0
        try:
            saved_cursor = int(state.get("entry_editor_cursor") or 0)
        except Exception:
            saved_cursor = 0
        try:
            saved_scroll = int(state.get("entry_editor_scroll") or 0)
        except Exception:
            saved_scroll = 0
        self.last_sync_cursor = str(state.get("last_sync_cursor") or "0")

        notebook_exists = self._lookup_notebook(saved_notebook_id) is not None
        entry_exists = self._lookup_entry(saved_entry_id) is not None

        self.root_tab = saved_root_tab

        if notebook_exists:
            self.active_notebook_id = saved_notebook_id
            if saved_notes_view == "notes_list":
                self.notes_view = "notes_list"
                self.active_entry_id = ""
                self.entry_editor_draft = ""
                self.entry_editor_dirty = False
                self.entry_editor_cursor = 0
                self.entry_editor_scroll = 0
                self.entry_editor_base_version = 0
                self._restore_editor_state("", 0, 0)
            elif saved_notes_view == "note_detail":
                self.notes_view = "note_detail"
                self.active_entry_id = saved_entry_id if entry_exists else ""
                self.entry_editor_draft = ""
                self.entry_editor_dirty = False
                self.entry_editor_cursor = 0
                self.entry_editor_scroll = 0
                self.entry_editor_base_version = 0
                self._restore_editor_state("", 0, 0)
            elif entry_exists:
                self.notes_view = "note_edit"
                self.active_entry_id = saved_entry_id
                self.entry_editor_draft = saved_draft
                self.entry_editor_dirty = saved_dirty
                self.entry_editor_cursor = saved_cursor
                self.entry_editor_scroll = saved_scroll
                entry = self._lookup_entry(saved_entry_id)
                self.entry_editor_base_version = saved_base_version or (entry.version if entry is not None else 0)
                self._restore_editor_state(saved_draft, saved_cursor, saved_scroll)
            elif saved_notes_view == "note_edit" and not saved_entry_id and (saved_dirty or saved_draft):
                self.notes_view = "note_edit"
                self.active_entry_id = ""
                self.entry_editor_draft = saved_draft
                self.entry_editor_dirty = saved_dirty
                self.entry_editor_cursor = saved_cursor
                self.entry_editor_scroll = saved_scroll
                self.entry_editor_base_version = 0
                self._restore_editor_state(saved_draft, saved_cursor, saved_scroll)
            else:
                self.notes_view = "note_detail"
                self.active_entry_id = ""
                self.entry_editor_draft = ""
                self.entry_editor_dirty = False
                self.entry_editor_cursor = 0
                self.entry_editor_scroll = 0
                self.entry_editor_base_version = 0
                self._restore_editor_state("", 0, 0)
        else:
            self.notes_view = "notes_list"
            self.active_notebook_id = ""
            self.active_entry_id = ""
            self.entry_editor_draft = ""
            self.entry_editor_dirty = False
            self.entry_editor_cursor = 0
            self.entry_editor_scroll = 0
            self.entry_editor_base_version = 0
            self._restore_editor_state("", 0, 0)

        # Keep the loaded state reflected on the frame for save/restore tests.
        if hasattr(self.frame, "_current_notes_state"):
            self.frame._current_notes_state = self.to_state_dict()

    def to_state_dict(self) -> dict:
        draft = self.entry_editor_draft
        editor = getattr(self.frame, "notes_editor", None)
        if editor is not None and hasattr(editor, "GetValue"):
            try:
                draft = str(editor.GetValue() or "")
            except Exception:
                draft = self.entry_editor_draft
        return {
            "active_root_tab": self.root_tab,
            "notes_view": self.notes_view,
            "active_notebook_id": self.active_notebook_id,
            "active_entry_id": self.active_entry_id,
            "entry_editor_draft": draft,
            "entry_editor_dirty": self.entry_editor_dirty,
            "entry_editor_cursor": self.entry_editor_cursor,
            "entry_editor_scroll": self.entry_editor_scroll,
            "entry_editor_base_version": self.entry_editor_base_version,
            "last_sync_cursor": self.last_sync_cursor,
        }
