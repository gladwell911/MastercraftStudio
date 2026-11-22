"""Desktop notes views, editing, and synchronization."""

import copy
import re
import uuid
import wx
from mc_frame_settings import NOTES_ENTRY_LABEL_MAX_CHARS
from notes_backup import export_notes_backup, restore_notes_backup
from notes_import import import_note_entries_from_clipboard, import_note_entries_from_file
from notes_store import NotesPlacementConflict
from pathlib import Path

import mc_frame_bindings as bindings


class NotesMixin:
    def _notes_editor_has_protected_focus(self) -> bool:
        editor = getattr(self, "notes_editor", None)
        controller = getattr(self, "notes_controller", None)
        if editor is None or controller is None:
            return False
        try:
            if getattr(controller, "notes_view", "") != "note_edit":
                return False
            return bool(editor.HasFocus())
        except Exception:
            return False

    def _on_notes_sync_push_result(self, result: dict | None = None) -> None:
        result = dict(result or {})
        cursor = str(result.get("cursor") or self.notes_store.current_cursor() or "0")
        self._push_remote_notes_changed(cursor)
        if result.get("conflicts"):
            self._push_remote_notes_conflict({"conflicts": list(result.get("conflicts") or []), "cursor": cursor})
            self._push_remote_notes_sync_status({"status": "conflict"}, cursor=cursor, message="notes_conflict")
        else:
            self._push_remote_notes_sync_status({"status": "synced"}, cursor=cursor, message="notes_sync_status")

    def _focus_visible_notes_list(self) -> bool:
        entry_list = getattr(self, "notes_entry_list", None)
        notebook_list = getattr(self, "notes_notebook_list", None)
        view = str(getattr(getattr(self, "notes_controller", None), "notes_view", "notes_list") or "notes_list")
        if view == "note_detail":
            return self._focus_control_safely(entry_list)
        return self._focus_control_safely(notebook_list)

    def _remote_api_notes_snapshot(self, _payload: dict | None = None) -> tuple[int, dict]:
        return self._remote_api_notes_retired()

    def _remote_api_notes_pull_since(self, payload: dict | None = None) -> tuple[int, dict]:
        return self._remote_api_notes_retired()

    def _remote_api_notes_push_ops(self, payload: dict | None = None) -> tuple[int, dict]:
        return self._remote_api_notes_retired()

    def _remote_api_notes_subscribe(self, payload: dict | None = None) -> tuple[int, dict]:
        return self._remote_api_notes_retired()

    def _remote_api_notes_ack(self, payload: dict | None = None) -> tuple[int, dict]:
        return self._remote_api_notes_retired()

    def _remote_api_notes_ping(self, payload: dict | None = None) -> tuple[int, dict]:
        return self._remote_api_notes_retired()

    def _remote_api_notes_retired(self) -> tuple[int, dict]:
        return 410, {
            "accepted": False,
            "error": "retired",
            "message": "旧 notes 同步协议已退役，请使用 CouchDB 同步",
        }

    def _notes_current_notebook(self):
        notebook_id = str(self.notes_controller.active_notebook_id or self._notes_selected_notebook_id() or "").strip()
        if not notebook_id:
            return None
        projection = getattr(self, "notes_projection", None)
        if projection is not None:
            return projection.get_notebook(notebook_id, include_deleted=True)
        return self.notes_store.get_notebook(notebook_id, include_deleted=True)

    def _notes_current_entry(self):
        active_entry_id = str(getattr(self.notes_controller, "active_entry_id", "") or "").strip()
        selected_entry_id = str(self._notes_selected_entry_id() or "").strip()
        entry_list_has_focus = False
        try:
            entry_list_has_focus = bool(self.notes_entry_list.HasFocus())
        except Exception:
            entry_list_has_focus = False
        if entry_list_has_focus and selected_entry_id:
            entry_id = selected_entry_id
        elif active_entry_id:
            entry_id = active_entry_id
        elif str(getattr(self.notes_controller, "notes_view", "") or "") == "note_edit":
            entry_id = ""
        else:
            entry_id = selected_entry_id
        if not entry_id:
            return None
        projection = getattr(self, "notes_projection", None)
        if projection is not None:
            return projection.get_entry(entry_id, include_deleted=True)
        return self.notes_store.get_entry(entry_id, include_deleted=True)

    def _notes_selected_notebook_id(self) -> str:
        idx = self.notes_notebook_list.GetSelection()
        if idx != wx.NOT_FOUND and 0 <= idx < len(getattr(self, "_notes_notebook_ids", [])):
            return str(self._notes_notebook_ids[idx])
        return str(self.notes_controller.active_notebook_id or "")

    def _notes_selected_entry_id(self) -> str:
        idx = self.notes_entry_list.GetSelection()
        if idx != wx.NOT_FOUND and 0 <= idx < len(getattr(self, "_notes_entry_ids", [])):
            return str(self._notes_entry_ids[idx])
        return str(self.notes_controller.active_entry_id or "")

    def _notes_sync_view_visibility(self) -> None:
        controller = getattr(self, "notes_controller", None)
        view = str(getattr(controller, "notes_view", "notes_list") or "notes_list")
        show_notes_list = view != "note_detail"
        show_note_detail = view == "note_detail"
        show_note_edit = view == "note_edit"
        if hasattr(self, "notes_content_label"):
            self.notes_content_label.SetLabel("笔记详情：" if show_note_detail else ("笔记条目编辑：" if show_note_edit else "笔记："))
        if hasattr(self, "notes_list_panel"):
            self.notes_list_panel.Show(show_notes_list)
        if hasattr(self, "notes_detail_panel"):
            self.notes_detail_panel.Show(show_note_detail)
        if hasattr(self, "notes_edit_panel"):
            self.notes_edit_panel.Show(show_note_edit)
        try:
            self.notes_notebook_list.Enable(True)
        except Exception:
            pass
        try:
            self.notes_entry_list.Enable(show_note_detail and bool(str(getattr(controller, "active_notebook_id", "") or "").strip()))
        except Exception:
            pass
        try:
            self.notes_editor.Enable(show_note_edit)
        except Exception:
            pass
        self._notes_rebuild_tab_order()
        try:
            self.Layout()
        except Exception:
            pass

    def _notes_primary_tab_target(self):
        view = str(getattr(getattr(self, "notes_controller", None), "notes_view", "notes_list") or "notes_list")
        if view == "note_detail":
            return self.notes_entry_list
        return self.notes_notebook_list

    def _notes_rebuild_tab_order(self) -> None:
        if not all(
            hasattr(self, attr)
            for attr in (
                "input_edit",
                "send_button",
                "new_chat_button",
                "model_combo",
                "codex_speed_combo",
                "history_list",
                "notes_notebook_list",
                "notes_entry_list",
                "notes_editor",
                "answer_list",
                "execution_list",
            )
        ):
            return
        primary_notes_ctrl = self._notes_primary_tab_target()
        detail_target = self._current_detail_tab_target()
        secondary_detail_target = self.execution_list if detail_target is self.answer_list else self.answer_list
        ordered_controls = [
            self.input_edit,
            self.new_chat_button,
            self.model_combo,
            self.codex_speed_combo,
            self.send_button,
            primary_notes_ctrl,
            self.history_list,
            detail_target,
            secondary_detail_target,
            self.notes_notebook_list,
            self.notes_entry_list,
            self.notes_editor,
        ]
        seen = set()
        root_tab_order = []
        for ctrl in ordered_controls:
            marker = id(ctrl)
            if marker in seen:
                continue
            seen.add(marker)
            root_tab_order.append(ctrl)
        self.root_tab_order = root_tab_order
        self.chat_tab_order = root_tab_order[:8]
        self.notes_tab_order = [primary_notes_ctrl] + [
            ctrl
            for ctrl in (self.notes_notebook_list, self.notes_entry_list, self.notes_editor)
            if ctrl is not primary_notes_ctrl
        ]
        primary_notes_panel = self.notes_detail_panel if primary_notes_ctrl is self.notes_entry_list else self.notes_list_panel
        for previous, nxt in zip(self.root_tab_order, self.root_tab_order[1:]):
            try:
                nxt.MoveAfterInTabOrder(previous)
            except Exception:
                pass
        try:
            primary_notes_panel.MoveAfterInTabOrder(self.send_button)
        except Exception:
            pass
        try:
            primary_notes_ctrl.MoveAfterInTabOrder(primary_notes_panel)
        except Exception:
            pass
        try:
            self.history_list.MoveAfterInTabOrder(primary_notes_ctrl)
        except Exception:
            pass
        try:
            detail_target.MoveAfterInTabOrder(self.history_list)
        except Exception:
            pass

    def _notes_refresh_notebooks(self, select_id: str | None = None) -> None:
        query = str(getattr(self, "_notes_search_query", "") or "").strip()
        projection = getattr(self, "notes_projection", None)
        if projection is not None:
            notebooks = projection.search_notebooks(query) if query else projection.list_notebooks()
        else:
            notebooks = self.notes_store.search_notebooks(query) if query else self.notes_store.list_notebooks()
        self._notes_notebook_ids = [nb.id for nb in notebooks]
        model = getattr(self, "notes_notebook_list_model", None)
        if not notebooks:
            self.notes_notebook_list.Enable(True)
            if model is not None:
                model.replace_visible_page([])
            self._replace_listbox_items_if_changed(self.notes_notebook_list, ["暂无笔记本"], 0)
            return
        self.notes_notebook_list.Enable(True)
        target = str(select_id or self.notes_controller.active_notebook_id or notebooks[0].id)
        selected_idx = 0
        if target in self._notes_notebook_ids:
            selected_idx = self._notes_notebook_ids.index(target)
        labels = [self._notes_notebook_label(notebook) for notebook in notebooks]
        if model is not None:
            model.replace_visible_page(list(zip(self._notes_notebook_ids, labels)), target)
        else:
            self._replace_listbox_items_if_changed(self.notes_notebook_list, labels, selected_idx)

    def _notes_refresh_entries(self, notebook_id: str | None = None, select_id: str | None = None) -> None:
        notebook_id = str(notebook_id or self.notes_controller.active_notebook_id or "").strip()
        self._notes_entry_ids = []
        model = getattr(self, "notes_entry_list_model", None)
        if not notebook_id:
            self.notes_entry_list.Enable(False)
            if model is not None:
                model.replace_visible_page([])
            self._replace_listbox_items_if_changed(self.notes_entry_list, ["请选择笔记本"], None)
            return
        projection = getattr(self, "notes_projection", None)
        if projection is not None:
            entries = projection.list_entries(notebook_id)
        else:
            entries = self.notes_store.list_entries(notebook_id)
        self.notes_entry_list.Enable(True)
        self._notes_entry_ids = [entry.id for entry in entries]
        if not entries:
            if model is not None:
                model.replace_visible_page([])
            self._replace_listbox_items_if_changed(self.notes_entry_list, ["暂无条目"], None)
            return
        target = str(select_id or self.notes_controller.active_entry_id or entries[0].id)
        selected_idx = 0
        if target in self._notes_entry_ids:
            selected_idx = self._notes_entry_ids.index(target)
        labels = []
        for entry in entries:
            labels.append(self._notes_entry_label(entry))
        if model is not None:
            model.replace_visible_page(list(zip(self._notes_entry_ids, labels)), target)
        else:
            self._replace_listbox_items_if_changed(self.notes_entry_list, labels, selected_idx)

    def _notes_notebook_label(self, notebook) -> str:
        return f"{'★ ' if getattr(notebook, 'pinned', False) else ''}{getattr(notebook, 'title', '')}"

    def _notes_upsert_notebook_row(self, notebook_id: str) -> bool:
        notebook_id = str(notebook_id or "").strip()
        if not notebook_id:
            return False
        projection = getattr(self, "notes_projection", None)
        notebook = projection.get_notebook(notebook_id) if projection is not None else self.notes_store.get_notebook(notebook_id)
        if notebook is None:
            return self._notes_remove_notebook_row(notebook_id)
        model = getattr(self, "notes_notebook_list_model", None)
        if model is None:
            self._notes_refresh_notebooks(notebook_id)
            return True
        label = self._notes_notebook_label(notebook)
        row_idx = model.row_for_id(notebook_id)
        if row_idx >= 0:
            changed = model.update_label(notebook_id, label)
            self._notes_notebook_ids = list(model.visible_ids)
            return changed
        if not self._notes_notebook_ids or self.notes_notebook_list.GetCount() != len(self._notes_notebook_ids):
            self.notes_notebook_list.Clear()
            self._notes_notebook_ids = []
            model.visible_ids = []
            model.labels_by_id = {}
        model.append(notebook_id, label)
        self._notes_notebook_ids = list(model.visible_ids)
        return True

    def _notes_remove_notebook_row(self, notebook_id: str) -> bool:
        notebook_id = str(notebook_id or "").strip()
        model = getattr(self, "notes_notebook_list_model", None)
        changed = model.remove(notebook_id) if model is not None else False
        if notebook_id in self._notes_notebook_ids:
            self._notes_notebook_ids = [item_id for item_id in self._notes_notebook_ids if item_id != notebook_id]
            changed = True
        if model is not None:
            self._notes_notebook_ids = list(model.visible_ids)
        return changed

    def _notes_upsert_entry_row(self, entry_id: str) -> bool:
        entry_id = str(entry_id or "").strip()
        if not entry_id:
            return False
        projection = getattr(self, "notes_projection", None)
        entry = projection.get_entry(entry_id) if projection is not None else self.notes_store.get_entry(entry_id)
        if entry is None:
            return self._notes_remove_entry_row(entry_id)
        active_notebook_id = str(getattr(self.notes_controller, "active_notebook_id", "") or "")
        if active_notebook_id and str(getattr(entry, "notebook_id", "") or "") != active_notebook_id:
            return False
        model = getattr(self, "notes_entry_list_model", None)
        if model is None:
            self._notes_refresh_entries(entry.notebook_id, entry_id)
            return True
        label = self._notes_entry_label(entry)
        row_idx = model.row_for_id(entry_id)
        if row_idx >= 0:
            changed = model.update_label(entry_id, label)
            self._notes_entry_ids = list(model.visible_ids)
            return changed
        if not self._notes_entry_ids or self.notes_entry_list.GetCount() != len(self._notes_entry_ids):
            self.notes_entry_list.Clear()
            self._notes_entry_ids = []
            model.visible_ids = []
            model.labels_by_id = {}
        model.append(entry_id, label)
        self._notes_entry_ids = list(model.visible_ids)
        return True

    def _notes_remove_entry_row(self, entry_id: str) -> bool:
        entry_id = str(entry_id or "").strip()
        model = getattr(self, "notes_entry_list_model", None)
        changed = model.remove(entry_id) if model is not None else False
        if entry_id in self._notes_entry_ids:
            self._notes_entry_ids = [item_id for item_id in self._notes_entry_ids if item_id != entry_id]
            changed = True
        if model is not None:
            self._notes_entry_ids = list(model.visible_ids)
        return changed

    def _notes_entry_label(self, entry) -> str:
        prefix = "置顶：" if getattr(entry, "pinned", False) else ""
        raw = str(getattr(entry, "content", "") or "")
        preview = raw[: max(NOTES_ENTRY_LABEL_MAX_CHARS * 4, NOTES_ENTRY_LABEL_MAX_CHARS)]
        label = re.sub(r"\s*[\r\n]+\s*", " / ", preview).strip()
        max_body = max(1, NOTES_ENTRY_LABEL_MAX_CHARS - len(prefix))
        if len(label) > max_body:
            label = label[: max(1, max_body - 3)].rstrip() + "..."
        return f"{prefix}{label}"

    def _notes_sync_editor(self) -> None:
        entry = self._notes_current_entry()
        draft = ""
        if self.notes_controller.entry_editor_dirty:
            draft = self.notes_controller.entry_editor_draft
        elif entry is not None:
            draft = entry.content
        elif self.notes_controller.entry_editor_draft:
            draft = self.notes_controller.entry_editor_draft
        try:
            self._notes_editor_syncing = True
            self.notes_editor.SetValue(draft)
            self.notes_editor.SetInsertionPointEnd()
        except Exception:
            pass
        finally:
            self._notes_editor_syncing = False

    def _notes_refresh_ui(self) -> None:
        if not hasattr(self, "notes_notebook_list"):
            return
        if not hasattr(self, "notes_controller"):
            return
        self._notes_refresh_notebooks()
        self._notes_refresh_entries()
        self._notes_sync_view_visibility()
        if self.notes_controller.notes_view == "note_edit":
            self._notes_sync_editor()

    def _sync_notes_ui(self) -> None:
        self._notes_refresh_ui()

    def _open_notes_root(self) -> None:
        if not hasattr(self, "notes_controller"):
            return
        self.notes_controller.root_tab = "notes"
        if not self.notes_controller.active_notebook_id:
            projection = getattr(self, "notes_projection", None)
            first = projection.list_notebooks() if projection is not None else self.notes_store.list_notebooks()
            if first:
                self.notes_controller.active_notebook_id = first[0].id
        self.notes_controller.notes_view = "notes_list"
        self.notes_controller.active_entry_id = ""
        self.notes_controller.entry_editor_dirty = False
        self._current_notes_state = self.notes_controller.to_state_dict()
        self._notes_refresh_ui()
        try:
            self.notes_notebook_list.SetFocus()
        except Exception:
            pass

    def _on_notes_editor_changed(self, event) -> None:
        if getattr(self, "_notes_editor_syncing", False):
            if event is not None:
                event.Skip()
            return
        if not hasattr(self, "notes_controller"):
            return
        draft = ""
        try:
            draft = str(self.notes_editor.GetValue() or "")
        except Exception:
            draft = str(self.notes_controller.entry_editor_draft or "")
        self.notes_controller.root_tab = "notes"
        self.notes_controller.notes_view = "note_edit"
        capture = getattr(self.notes_controller, "capture_editor_state", None)
        if callable(capture):
            capture()
        self.notes_controller.entry_editor_draft = draft
        self.notes_controller.entry_editor_dirty = True
        self._current_notes_state = self.notes_controller.to_state_dict()
        if event is not None:
            event.Skip()

    def _prompt_notes_dirty_exit(self) -> str:
        dlg = wx.MessageDialog(self, "笔记有未保存更改。", "未保存更改", wx.YES_NO | wx.CANCEL | wx.ICON_QUESTION)
        try:
            try:
                dlg.SetYesNoCancelLabels("保存", "不保存", "取消")
            except Exception:
                pass
            ret = dlg.ShowModal()
        finally:
            dlg.Destroy()
        if ret == wx.ID_YES:
            return "save"
        if ret == wx.ID_NO:
            return "discard"
        return "cancel"

    def _notes_discard_current_entry_edits(self) -> bool:
        if not hasattr(self, "notes_controller"):
            return False
        entry = self._notes_current_entry()
        should_drop = (
            entry is not None
            and not str(entry.content or "").strip()
            and int(getattr(entry, "version", 0) or 0) <= 1
            and not bool(getattr(entry, "origin_entry_id", None))
            and not bool(getattr(entry, "is_conflict_copy", False))
        )
        if should_drop:
            try:
                self.notes_store.purge_entry(entry.id)
            except Exception:
                pass
            self.notes_controller.active_entry_id = ""
            self._notes_after_local_mutation()
        self.notes_controller.entry_editor_dirty = False
        self.notes_controller.entry_editor_draft = ""
        self.notes_controller.notes_view = "note_detail"
        self.notes_controller.entry_editor_base_version = 0
        self._current_notes_state = self.notes_controller.to_state_dict()
        self._notes_refresh_ui()
        return True

    def _notes_request_exit_edit(self) -> bool:
        if not hasattr(self, "notes_controller"):
            return False
        if self.notes_controller.notes_view != "note_edit":
            self.notes_controller.notes_view = "note_detail"
            self._current_notes_state = self.notes_controller.to_state_dict()
            self._notes_refresh_ui()
            return True
        if not self.notes_controller.entry_editor_dirty:
            return self._notes_discard_current_entry_edits()
        choice = self._prompt_notes_dirty_exit()
        if choice == "save":
            return self._notes_save_current_entry()
        if choice == "discard":
            return self._notes_discard_current_entry_edits()
        return False

    def _notes_sync_status_text(self, status: str | dict, message: str | None = None) -> str:
        if message:
            return str(message)
        if isinstance(status, dict):
            status = str(status.get("status") or "")
        mapping = {
            "pending": "待同步",
            "sending": "同步中",
            "failed": "同步失败",
            "acked": "笔记已同步",
            "synced": "笔记已同步",
            "saved": "笔记已保存",
            "conflict": "冲突",
        }
        return mapping.get(str(status or ""), str(status or ""))

    def _on_notes_sync_status_changed(self, status: str | dict, *, message: str | None = None, cursor: str | None = None) -> None:
        text = self._notes_sync_status_text(status, message)
        self._show_notes_sync_hint(text)
        self._push_remote_notes_sync_status(status, cursor=cursor, message=text)

    def _notes_after_local_mutation(self, *, message: str = "待同步") -> None:
        cursor = self.notes_store.current_cursor()
        self._push_remote_notes_changed(cursor)
        self._on_notes_sync_status_changed("pending", message=message, cursor=cursor)
        self._schedule_notes_couchdb_sync()

    def _show_notes_sync_hint(self, message: str) -> None:
        next_hint = str(message or "")
        if str(getattr(self, "notes_sync_hint", "") or "") == next_hint:
            return
        self.notes_sync_hint = next_hint

    def _on_notes_sync_status_changed_safe(self, status: str | dict, *, message: str | None = None, cursor: str | None = None) -> None:
        if bindings.app.threading.get_ident() == getattr(self, "_notes_ui_thread_id", 0):
            self._on_notes_sync_status_changed(status, message=message, cursor=cursor)
            return
        self._call_after_if_alive(self._on_notes_sync_status_changed, status, message=message, cursor=cursor)

    def _on_notes_remote_ops_applied_safe(self, result: dict | None) -> None:
        if bindings.app.threading.get_ident() == getattr(self, "_notes_ui_thread_id", 0):
            self._on_notes_remote_ops_applied(result)
            return
        self._call_after_if_alive(self._on_notes_remote_ops_applied, result)

    def _invalidate_notes_projection(self) -> None:
        projection = getattr(self, "notes_projection", None)
        invalidate = getattr(projection, "invalidate", None)
        if callable(invalidate):
            invalidate()

    def _configure_notes_couchdb_sync_from_env(self) -> None:
        base_url = str(bindings.app.os.environ.get("NOTES_COUCHDB_URL") or "").strip()
        if not base_url:
            return
        database = str(bindings.app.os.environ.get("NOTES_COUCHDB_DATABASE") or "zhuge_notes").strip() or "zhuge_notes"
        self.notes_sync.configure(base_url, database)
        self._schedule_notes_couchdb_sync()

    def _next_notes_couchdb_rev(self, current_rev: str) -> str:
        try:
            generation = int(str(current_rev or "0").split("-", 1)[0]) + 1
        except Exception:
            generation = 1
        return f"{generation}-{uuid.uuid4().hex[:8]}"

    def _remote_api_notes_couchdb_changes(self, payload: dict | None = None) -> tuple[int, dict]:
        payload = dict(payload or {})
        database = str(payload.get("database") or "").strip() or "zhuge_notes"
        if database != "zhuge_notes":
            return 404, {"error": "not_found", "reason": "unknown_database"}
        current_cursor = str(self.notes_store.current_cursor() or "0")
        try:
            requested_cursor = int(str(payload.get("since") or "0").strip() or "0")
        except Exception:
            requested_cursor = 0
        try:
            current_cursor_value = int(current_cursor)
        except Exception:
            current_cursor_value = 0
        if requested_cursor >= current_cursor_value:
            return 200, {"results": [], "last_seq": current_cursor}
        cache_key = (requested_cursor, current_cursor)
        cache = getattr(self, "_remote_notes_changes_cache", None)
        if isinstance(cache, dict) and cache.get("key") == cache_key and isinstance(cache.get("body"), dict):
            return 200, copy.deepcopy(cache["body"])
        snapshot = self.notes_store.load_documents()
        results: list[dict] = []
        for notebook in snapshot.notebooks:
            doc = self.notes_sync._notebook_to_couch_document(notebook)
            row = {"seq": current_cursor, "id": doc["_id"], "doc": doc}
            if doc.get("_deleted"):
                row["deleted"] = True
            results.append(row)
        for entry in snapshot.entries:
            doc = self.notes_sync._entry_to_couch_document(entry)
            row = {"seq": current_cursor, "id": doc["_id"], "doc": doc}
            if doc.get("_deleted"):
                row["deleted"] = True
            results.append(row)
        body = {"results": results, "last_seq": current_cursor}
        self._remote_notes_changes_cache = {"key": cache_key, "body": copy.deepcopy(body)}
        return 200, body

    def _invalidate_remote_notes_changes_cache(self) -> None:
        self._remote_notes_changes_cache = None

    def _remote_api_notes_changes(self, payload: dict | None = None) -> tuple[int, dict]:
        return self._remote_api_notes_couchdb_changes(payload)

    def _remote_api_notes_couchdb_bulk_docs(self, payload: dict | None = None) -> tuple[int, list]:
        payload = dict(payload or {})
        database = str(payload.get("database") or "").strip() or "zhuge_notes"
        if database != "zhuge_notes":
            return 404, [{"error": "not_found", "reason": "unknown_database"}]
        docs = [dict(item) for item in list(payload.get("docs") or []) if isinstance(item, dict)]
        if not docs:
            return 201, []
        results: list[dict] = []
        applied: list[dict] = []
        touched = False
        with self.notes_store._connect() as conn:
            for doc in docs:
                remote_id = str(doc.get("_id") or "").strip()
                if not remote_id:
                    results.append({"id": "", "error": "bad_request", "reason": "missing_id"})
                    continue
                if remote_id.startswith("notebook:"):
                    row = conn.execute(
                        "SELECT rev FROM notebooks WHERE id = ?",
                        (remote_id.split(":", 1)[1],),
                    ).fetchone()
                    current_rev = str(row["rev"] or "") if row is not None else ""
                    supplied_rev = str(doc.get("_rev") or "")
                    if row is not None and current_rev and supplied_rev != current_rev:
                        results.append({"id": remote_id, "error": "conflict", "reason": "document update conflict"})
                        continue
                    normalized = dict(doc)
                    normalized["_rev"] = self._next_notes_couchdb_rev(current_rev)
                    applied_result = self.notes_sync._upsert_remote_notebook(conn, normalized)
                    if applied_result:
                        applied.append(applied_result)
                    results.append({"id": remote_id, "ok": True, "rev": normalized["_rev"]})
                    touched = True
                    continue
                if remote_id.startswith("entry:"):
                    row = conn.execute(
                        "SELECT rev FROM entries WHERE id = ?",
                        (remote_id.split(":", 1)[1],),
                    ).fetchone()
                    current_rev = str(row["rev"] or "") if row is not None else ""
                    supplied_rev = str(doc.get("_rev") or "")
                    if row is not None and current_rev and supplied_rev != current_rev:
                        results.append({"id": remote_id, "error": "conflict", "reason": "document update conflict"})
                        continue
                    normalized = dict(doc)
                    normalized["_rev"] = self._next_notes_couchdb_rev(current_rev)
                    applied_result = self.notes_sync._upsert_remote_entry(conn, normalized)
                    if applied_result:
                        applied.append(applied_result)
                    results.append({"id": remote_id, "ok": True, "rev": normalized["_rev"]})
                    touched = True
                    continue
                results.append({"id": remote_id, "error": "bad_request", "reason": "unsupported_document"})
            if touched:
                self.notes_store._next_cursor(conn)
        if touched:
            self._invalidate_remote_notes_changes_cache()
            self._on_notes_remote_ops_applied_safe(
                {
                    "cursor": self.notes_store.current_cursor(),
                    "applied": applied,
                    "conflicts": [],
                }
            )
        return 201, results

    def _remote_api_notes_bulk_docs(self, payload: dict | None = None) -> tuple[int, dict]:
        status, results = self._remote_api_notes_couchdb_bulk_docs(payload)
        return status, {"results": results}

    def _schedule_notes_couchdb_sync(self) -> None:
        if not getattr(self, "notes_sync", None) or not self.notes_sync.is_configured():
            return
        with self._notes_sync_worker_lock:
            if self._notes_sync_worker_scheduled:
                return
            self._notes_sync_worker_scheduled = True
        worker = bindings.app.threading.Thread(target=self._run_notes_couchdb_sync, daemon=True)
        worker.start()

    def _run_notes_couchdb_sync(self) -> None:
        try:
            self.notes_sync.sync_once()
        except Exception:
            self._on_notes_sync_status_changed_safe("failed", message="同步失败", cursor=self.notes_sync.get_checkpoint())
        finally:
            with self._notes_sync_worker_lock:
                self._notes_sync_worker_scheduled = False

    def _on_notes_remote_ops_applied(self, result: dict | None) -> None:
        result = dict(result or {})
        self._invalidate_remote_notes_changes_cache()
        self._invalidate_notes_projection()
        active_entry_id = str(getattr(self.notes_controller, "active_entry_id", "") or "").strip()
        active_changed = False
        if active_entry_id:
            for op_result in list(result.get("applied") or []):
                if not isinstance(op_result, dict):
                    continue
                entry = op_result.get("entry")
                if isinstance(entry, dict) and str(entry.get("id") or "") == active_entry_id:
                    active_changed = True
                    break
                conflicts = list(op_result.get("conflicts") or [])
                for conflict in conflicts:
                    if isinstance(conflict, dict) and str(conflict.get("origin_entry_id") or "") == active_entry_id:
                        active_changed = True
                        break
                if active_changed:
                    break
            if not active_changed:
                for conflict in list(result.get("conflicts") or []):
                    if isinstance(conflict, dict) and str(conflict.get("origin_entry_id") or "") == active_entry_id:
                        active_changed = True
                        break
        if self._notes_remote_ops_affect_visible_ui(result, active_entry_id=active_entry_id):
            self._notes_refresh_ui()
        cursor = str(result.get("cursor") or self.notes_store.current_cursor() or "0")
        if active_changed:
            message = "当前编辑的笔记已被远程更新，已刷新。"
            if any(isinstance(item, dict) for item in list(result.get("conflicts") or [])):
                message = "当前编辑的笔记已产生冲突，已刷新。"
            self._show_notes_sync_hint(message)
            self._push_remote_notes_conflict({"entry_id": active_entry_id, "cursor": cursor, "conflicts": list(result.get("conflicts") or [])})
            self._push_remote_notes_sync_status({"status": "conflict"}, cursor=cursor, message=message)
        else:
            self._show_notes_sync_hint("笔记已同步")
            self._push_remote_notes_sync_status({"status": "synced"}, cursor=cursor, message="笔记已同步")

    def _notes_remote_ops_affect_visible_ui(self, result: dict, *, active_entry_id: str = "") -> bool:
        applied = list(result.get("applied") or [])
        conflicts = list(result.get("conflicts") or [])
        if not applied and not conflicts:
            return False
        active_notebook_id = str(getattr(self.notes_controller, "active_notebook_id", "") or "").strip()
        active_entry_id = str(active_entry_id or getattr(self.notes_controller, "active_entry_id", "") or "").strip()
        for item in applied:
            if not isinstance(item, dict):
                return True
            entity_type = str(item.get("entity_type") or "").strip()
            if entity_type == "notebook":
                return True
            if entity_type != "entry":
                return True
            entry = item.get("entry") if isinstance(item.get("entry"), dict) else {}
            entry_id = str(item.get("entity_id") or entry.get("id") or "").strip()
            notebook_id = str(entry.get("notebook_id") or item.get("notebook_id") or "").strip()
            origin_entry_id = str(entry.get("origin_entry_id") or item.get("origin_entry_id") or "").strip()
            if active_entry_id and active_entry_id in {entry_id, origin_entry_id}:
                return True
            if active_notebook_id and notebook_id == active_notebook_id:
                return True
            if not notebook_id and not entry:
                return True
        for conflict in conflicts:
            if not isinstance(conflict, dict):
                return True
            origin_entry_id = str(conflict.get("origin_entry_id") or "").strip()
            notebook_id = str(conflict.get("notebook_id") or "").strip()
            if active_entry_id and origin_entry_id == active_entry_id:
                return True
            if active_notebook_id and notebook_id == active_notebook_id:
                return True
        return False

    def _show_notes_menu(self) -> None:
        if self.notes_editor.HasFocus():
            return
        show_notebook_actions = self.notes_notebook_list.HasFocus() or self.notes_controller.notes_view == "notes_list"
        show_entry_actions = self.notes_entry_list.HasFocus() or self.notes_controller.notes_view == "note_detail"
        menu = wx.Menu()
        if show_notebook_actions:
            i_open_nb = wx.NewIdRef()
            i_new_nb = wx.NewIdRef()
            i_export_all = wx.NewIdRef()
            i_restore_backup = wx.NewIdRef()
            i_export_nb = wx.NewIdRef()
            i_copy_nb = wx.NewIdRef()
            i_del_nb = wx.NewIdRef()
            i_search_nb = wx.NewIdRef()
            i_ren_nb = wx.NewIdRef()
            menu.Append(i_open_nb, "打开笔记")
            menu.AppendSeparator()
            menu.Append(i_new_nb, "新建笔记")
            menu.Append(i_export_all, "导出所有笔记")
            menu.Append(i_restore_backup, "恢复笔记")
            menu.Append(i_export_nb, "导出到剪贴板")
            menu.Append(i_copy_nb, "复制笔记")
            menu.Append(i_del_nb, "删除笔记")
            menu.Append(i_search_nb, "搜索笔记")
            menu.Append(i_ren_nb, "重命名笔记")
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_open_selected_notebook(), id=i_open_nb)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_create_notebook(), id=i_new_nb)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_export_all_to_file(), id=i_export_all)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_restore_from_backup_file(), id=i_restore_backup)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_export_notebook_to_clipboard(), id=i_export_nb)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_copy_notebook_to_clipboard(), id=i_copy_nb)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_delete_notebook(), id=i_del_nb)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_prompt_search(), id=i_search_nb)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_rename_notebook(), id=i_ren_nb)
        elif show_entry_actions:
            i_new_entry = wx.NewIdRef()
            i_copy_entry = wx.NewIdRef()
            i_export_all = wx.NewIdRef()
            i_restore_backup = wx.NewIdRef()
            i_export_down = wx.NewIdRef()
            i_export_up = wx.NewIdRef()
            i_export_delete = wx.NewIdRef()
            i_del_entry = wx.NewIdRef()
            i_edit_entry = wx.NewIdRef()
            i_pin_entry = wx.NewIdRef()
            i_up_entry = wx.NewIdRef()
            i_down_entry = wx.NewIdRef()
            i_bottom_entry = wx.NewIdRef()
            i_unbottom_entry = wx.NewIdRef()
            i_import_file = wx.NewIdRef()
            i_import_clip = wx.NewIdRef()
            current_entry = self._notes_current_entry()
            entry_placement = str(getattr(current_entry, "placement", "normal") or "normal")
            menu.Append(i_new_entry, "新建笔记条目")
            menu.Append(i_copy_entry, "复制笔记条目")
            menu.Append(i_export_all, "导出所有笔记")
            menu.Append(i_restore_backup, "恢复笔记")
            menu.Append(i_export_down, "向下导出全部到剪贴板")
            menu.Append(i_export_up, "向上导出全部到剪贴板")
            menu.Append(i_export_delete, "从选中条目向下导出并删除")
            menu.Append(i_del_entry, "删除笔记条目")
            menu.Append(i_edit_entry, "编辑笔记条目")
            menu.Append(i_pin_entry, "取消置顶笔记条目" if entry_placement == "top" else "置顶笔记条目")
            menu.Append(i_up_entry, "向上移动笔记条目")
            menu.Append(i_down_entry, "向下移动笔记条目")
            menu.Append(i_bottom_entry, "置底笔记条目")
            menu.Append(i_unbottom_entry, "取消置底笔记条目")
            menu.Enable(i_pin_entry, current_entry is not None and entry_placement != "bottom")
            menu.Enable(i_bottom_entry, current_entry is not None and entry_placement != "bottom")
            menu.Enable(i_unbottom_entry, current_entry is not None and entry_placement == "bottom")
            menu.Enable(i_edit_entry, current_entry is not None)
            menu.AppendSeparator()
            menu.Append(i_import_file, "从文件导入")
            menu.Append(i_import_clip, "从剪贴板导入")
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_create_entry(), id=i_new_entry)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_copy_entry_to_clipboard(), id=i_copy_entry)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_export_all_to_file(), id=i_export_all)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_restore_from_backup_file(), id=i_restore_backup)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_export_selected_range_to_clipboard("down"), id=i_export_down)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_export_selected_range_to_clipboard("up"), id=i_export_up)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_export_selected_range_to_clipboard_and_delete("down"), id=i_export_delete)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_delete_entry(), id=i_del_entry)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_edit_entry(), id=i_edit_entry)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_toggle_entry_pin(), id=i_pin_entry)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_move_entry_up(), id=i_up_entry)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_move_entry_down(), id=i_down_entry)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_move_entry_to_bottom(), id=i_bottom_entry)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_unbottom_entry(), id=i_unbottom_entry)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_import_from_file(), id=i_import_file)
            self.Bind(wx.EVT_MENU, lambda _evt: self._notes_import_from_clipboard(), id=i_import_clip)
        else:
            menu.Destroy()
            return
        self.PopupMenu(menu)
        menu.Destroy()

    def _notes_apply_search(self, query: str | None = None) -> None:
        if query is not None:
            self._notes_search_query = str(query or "")
        self._notes_refresh_notebooks()
        self._notes_refresh_entries()

    def _notes_prompt_search(self) -> bool:
        initial = str(getattr(self, "_notes_search_query", "") or "").strip()
        dlg = wx.TextEntryDialog(self, "请输入要搜索的笔记名称：", "搜索笔记", initial)
        try:
            if dlg.ShowModal() != wx.ID_OK:
                try:
                    self.notes_notebook_list.SetFocus()
                except Exception:
                    pass
                return False
            self._notes_apply_search(dlg.GetValue())
            try:
                self.notes_notebook_list.SetFocus()
            except Exception:
                pass
            return True
        finally:
            dlg.Destroy()

    def _notes_select_notebook(self, notebook_id: str, entry_id: str | None = None, view: str = "note_detail", *, focus: bool = True) -> None:
        self.notes_controller.root_tab = "notes"
        self.notes_controller.active_notebook_id = str(notebook_id or "")
        self.notes_controller.active_entry_id = str(entry_id or "")
        self.notes_controller.notes_view = str(view or "note_detail")
        self.notes_controller.entry_editor_dirty = False
        if self.notes_controller.notes_view != "note_edit":
            self.notes_controller.entry_editor_base_version = 0
        self._current_notes_state = self.notes_controller.to_state_dict()
        self._notes_refresh_ui()
        if focus:
            try:
                if self.notes_controller.notes_view == "notes_list":
                    self.notes_notebook_list.SetFocus()
                elif self.notes_controller.notes_view == "note_detail":
                    self.notes_entry_list.SetFocus()
            except Exception:
                pass

    def _notes_entry_edit_window_for(self, entry_id: str):
        for window in list(getattr(self, "_open_notes_entry_edit_windows", None) or []):
            if str(getattr(window, "_entry_id", "") or "") == str(entry_id or ""):
                return window
        return None

    def _notes_guard_embedded_edit_view(self, entry_id: str) -> bool:
        """An entry with a live independent edit window must not also enter
        the embedded editor: raise the window and stay on the detail view."""
        window = self._notes_entry_edit_window_for(entry_id)
        if window is None:
            return False
        try:
            window.Raise()
        except Exception:
            pass
        return True

    def _notes_select_entry(self, entry_id: str, view: str = "note_edit", *, focus: bool = True) -> None:
        view = str(view or "note_edit")
        if view == "note_edit" and self._notes_guard_embedded_edit_view(entry_id):
            view = "note_detail"
        self.notes_controller.root_tab = "notes"
        self.notes_controller.active_entry_id = str(entry_id or "")
        self.notes_controller.notes_view = str(view or "note_edit")
        self.notes_controller.entry_editor_dirty = False
        projection = getattr(self, "notes_projection", None)
        entry = projection.get_entry(entry_id, include_deleted=True) if projection is not None else self.notes_store.get_entry(entry_id, include_deleted=True)
        self.notes_controller.entry_editor_base_version = int(entry.version if entry is not None else 0)
        self._current_notes_state = self.notes_controller.to_state_dict()
        self._notes_refresh_ui()
        if focus:
            try:
                if self.notes_controller.notes_view == "note_edit":
                    self.notes_editor.SetFocus()
            except Exception:
                pass

    def _notes_set_view(self, view: str) -> None:
        self.notes_controller.root_tab = "notes"
        self.notes_controller.notes_view = str(view or "notes_list")
        if self.notes_controller.notes_view != "note_edit":
            self.notes_controller.entry_editor_dirty = False
        self._current_notes_state = self.notes_controller.to_state_dict()
        self._notes_refresh_ui()

    def _notes_save_current_entry(self) -> bool:
        notebook = self._notes_current_notebook()
        if notebook is None:
            return False
        capture = getattr(self.notes_controller, "capture_editor_state", None)
        if callable(capture):
            capture()
        content = str(self.notes_editor.GetValue() or "").strip()
        entry = self._notes_current_entry()
        if entry is None:
            if not content:
                return False
            entry = self.notes_store.create_entry(notebook.id, content, source="manual")
        else:
            base_version = int(getattr(self.notes_controller, "entry_editor_base_version", 0) or 0)
            if base_version and entry.version != base_version:
                source_label = "电脑端"
                conflict_content = f"【冲突副本：来自{source_label}】\n{content}"
                conflict = self.notes_store.create_entry(
                    notebook.id,
                    conflict_content,
                    source=str(entry.source or "manual"),
                    entry_id=None,
                    device_id=self.notes_store.device_id,
                    last_modified_by="desktop",
                    is_conflict_copy=True,
                    origin_entry_id=entry.id,
                )
                self.notes_controller.active_entry_id = conflict.id
                self.notes_controller.entry_editor_base_version = conflict.version
                self.notes_controller.entry_editor_draft = conflict_content
                self.notes_controller.entry_editor_dirty = False
                self.notes_controller.notes_view = "note_detail"
                self._current_notes_state = self.notes_controller.to_state_dict()
                self._notes_refresh_ui()
                self._push_remote_notes_conflict(conflict.to_dict())
                self._on_notes_sync_status_changed("conflict", cursor=self.notes_store.current_cursor(), message="冲突副本已创建")
                self._push_remote_notes_changed(self.notes_store.current_cursor())
                return True
            entry = self.notes_store.update_entry(entry.id, content, source="manual")
        self.notes_controller.active_entry_id = entry.id
        self.notes_controller.entry_editor_draft = content
        self.notes_controller.entry_editor_dirty = False
        self.notes_controller.entry_editor_base_version = entry.version
        self.notes_controller.notes_view = "note_detail"
        self._current_notes_state = self.notes_controller.to_state_dict()
        self._notes_upsert_entry_row(entry.id)
        model = getattr(self, "notes_entry_list_model", None)
        if model is not None:
            model.set_selection_by_id(entry.id)
            self._notes_entry_ids = list(model.visible_ids)
        self._notes_sync_view_visibility()
        try:
            self.notes_entry_list.SetFocus()
        except Exception:
            pass
        self._notes_after_local_mutation(message="笔记已保存，待同步")
        return True

    def _notes_create_notebook(self) -> bool:
        dlg = wx.TextEntryDialog(self, "请输入笔记本名称", "新建笔记本")
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return False
            title = str(dlg.GetValue() or "").strip()
        finally:
            dlg.Destroy()
        if not title:
            return False
        notebook = self.notes_store.create_notebook(title)
        self._notes_select_notebook(notebook.id, view="note_detail")
        self._notes_after_local_mutation()
        return True

    def _notes_rename_notebook(self) -> bool:
        notebook = self._notes_current_notebook()
        if notebook is None:
            return False
        dlg = wx.TextEntryDialog(self, "请输入新的笔记本名称", "重命名笔记本", value=notebook.title)
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return False
            title = str(dlg.GetValue() or "").strip()
        finally:
            dlg.Destroy()
        if not title:
            return False
        self.notes_store.rename_notebook(notebook.id, title)
        self._notes_upsert_notebook_row(notebook.id)
        model = getattr(self, "notes_notebook_list_model", None)
        if model is not None:
            model.set_selection_by_id(notebook.id)
            self._notes_notebook_ids = list(model.visible_ids)
        self._notes_sync_view_visibility()
        self._notes_after_local_mutation()
        return True

    def _notes_delete_notebook(self) -> bool:
        notebook = self._notes_current_notebook()
        if notebook is None:
            return False
        if not self._confirm("确定删除该笔记本吗？"):
            return False
        self.notes_store.delete_notebook(notebook.id)
        self.notes_controller.active_notebook_id = ""
        self.notes_controller.active_entry_id = ""
        self.notes_controller.notes_view = "notes_list"
        self.notes_controller.entry_editor_dirty = False
        self._current_notes_state = self.notes_controller.to_state_dict()
        self._notes_refresh_ui()
        self._notes_after_local_mutation()
        return True

    def _notes_create_entry(self) -> bool:
        notebook = self._notes_current_notebook()
        if notebook is None:
            return False
        return self._notes_open_entry_edit_window(notebook=notebook, entry=None, base_version=0)

    def _notes_edit_entry(self) -> bool:
        entry = self._notes_current_entry()
        notebook = self._notes_current_notebook()
        if notebook is None:
            return False
        if entry is None:
            return False
        return self._notes_open_entry_edit_window(
            notebook=notebook,
            entry=entry,
            base_version=int(getattr(entry, "version", 0) or 0),
        )

    def _notes_entry_edit_windows(self) -> list:
        windows = getattr(self, "_open_notes_entry_edit_windows", None)
        if windows is None:
            windows = []
            self._open_notes_entry_edit_windows = windows
        return windows

    def _notes_open_entry_edit_window(self, *, notebook, entry, base_version: int,
                                      initial_content: str = "") -> bool:
        from notes_edit_window import NoteEntryEditWindow

        content = str(initial_content or "")
        if entry is not None and not content:
            content = str(getattr(entry, "content", "") or "")
        entry_key = str(entry.id) if entry is not None else f"new:{notebook.id}"
        windows = self._notes_entry_edit_windows()
        # One edit window per entry: a repeated open raises the existing
        # window instead of creating a second concurrent draft on the same
        # entry/version.
        for existing in list(windows):
            if str(getattr(existing, "_entry_id", "") or "") == entry_key:
                try:
                    existing.Raise()
                except Exception:
                    pass
                return True
        window = NoteEntryEditWindow(
            self,
            title="编辑笔记条目" if entry is not None else "新建笔记条目",
            content=content,
            on_save=lambda text: self._notes_save_from_edit_window(
                notebook=notebook, entry=entry, base_version=base_version, content=text,
            ),
        )
        window._entry_id = entry_key
        windows.append(window)
        window.Bind(
            wx.EVT_WINDOW_DESTROY,
            lambda _evt, w=window: windows.remove(w) if w in windows else None,
        )
        window.Show()
        return True

    def _notes_save_from_edit_window(self, *, notebook, entry, base_version: int,
                                     content: str) -> tuple[bool, str]:
        content = str(content or "").strip()
        if not content:
            return False, "内容为空，未保存"
        try:
            if entry is None:
                saved = self.notes_store.create_entry(notebook.id, content, source="manual")
            elif base_version and int(getattr(entry, "version", 0) or 0) != base_version:
                conflict_content = f"【冲突副本：来自电脑端】\n{content}"
                saved = self.notes_store.create_entry(
                    notebook.id,
                    conflict_content,
                    source=str(getattr(entry, "source", None) or "manual"),
                    device_id=self.notes_store.device_id,
                    last_modified_by="desktop",
                    is_conflict_copy=True,
                    origin_entry_id=entry.id,
                )
                self._push_remote_notes_conflict(saved.to_dict())
            else:
                saved = self.notes_store.update_entry(entry.id, content, source="manual")
        except Exception as exc:
            return False, f"保存失败：{exc}"
        self.notes_controller.active_entry_id = saved.id
        self.notes_controller.entry_editor_draft = str(saved.content or "")
        self.notes_controller.entry_editor_dirty = False
        self.notes_controller.entry_editor_base_version = int(getattr(saved, "version", 0) or 0)
        self.notes_controller.notes_view = "note_detail"
        self._current_notes_state = self.notes_controller.to_state_dict()
        self._notes_upsert_entry_row(saved.id)
        model = getattr(self, "notes_entry_list_model", None)
        if model is not None:
            model.set_selection_by_id(saved.id)
            self._notes_entry_ids = list(model.visible_ids)
        self._notes_sync_view_visibility()
        try:
            self.notes_entry_list.SetFocus()
        except Exception:
            pass
        self._notes_after_local_mutation(message="笔记已保存，待同步")
        return True, ""

    def _notes_delete_entry(self) -> bool:
        entry = self._notes_current_entry()
        if entry is None:
            return False
        if not self._confirm("确定删除该条目吗？"):
            return False
        notebook_id = entry.notebook_id
        visible_ids = list(getattr(self, "_notes_entry_ids", []))
        try:
            old_index = visible_ids.index(entry.id)
        except ValueError:
            old_index = 0
        self.notes_store.delete_entry(entry.id)
        survivors = [item for item in visible_ids if item != entry.id]
        self.notes_controller.active_entry_id = survivors[min(old_index, len(survivors) - 1)] if survivors else ""
        self.notes_controller.notes_view = "note_detail"
        self.notes_controller.entry_editor_dirty = False
        self._current_notes_state = self.notes_controller.to_state_dict()
        self._notes_refresh_entries(notebook_id, self.notes_controller.active_entry_id)
        self._notes_after_local_mutation()
        return True

    def _notes_pin_entry(self) -> bool:
        entry = self._notes_current_entry()
        if entry is None:
            return False
        self.notes_store.pin_entry(entry.id)
        self._notes_refresh_entries(entry.notebook_id, entry.id)
        self._notes_after_local_mutation()
        return True

    def _notes_move_entry_up(self) -> bool:
        return self._notes_move_selected_entry("up")

    def _notes_move_entry_down(self) -> bool:
        return self._notes_move_selected_entry("down")

    def _notes_move_entry_to_top(self) -> bool:
        return self._notes_move_selected_entry("top")

    def _notes_move_entry_to_bottom(self) -> bool:
        return self._notes_move_selected_entry("bottom")

    def _notes_toggle_entry_pin(self) -> bool:
        entry = self._notes_current_entry()
        if entry is not None and str(getattr(entry, "placement", "normal")) == "top":
            return self._notes_unpin_entry()
        return self._notes_move_selected_entry("top")

    def _notes_unpin_entry(self) -> bool:
        return self._notes_move_selected_entry("unpin")

    def _notes_unbottom_entry(self) -> bool:
        return self._notes_move_selected_entry("unbottom")

    def _notes_move_selected_entry(self, direction: str) -> bool:
        entry = self._notes_current_entry()
        if entry is None:
            return False
        direction_key = str(direction or "").strip().lower()
        if direction_key == "up":
            moved = self.notes_store.move_entry_up(entry.id)
        elif direction_key == "down":
            moved = self.notes_store.move_entry_down(entry.id)
        elif direction_key in {"top", "bottom", "unpin", "unbottom"}:
            target = direction_key if direction_key in {"top", "bottom"} else "normal"
            try:
                moved = self.notes_store.place_entry(
                    entry.id,
                    target,
                    expected_version=entry.version,
                    expected_rev=getattr(entry, "rev", ""),
                )
            except (NotesPlacementConflict, KeyError):
                self._invalidate_notes_projection()
                self._notes_refresh_entries(entry.notebook_id, entry.id)
                return False
        else:
            return False
        self._invalidate_notes_projection()
        self.notes_controller.active_entry_id = moved.id
        self._current_notes_state = self.notes_controller.to_state_dict()
        self._notes_refresh_entries(moved.notebook_id, moved.id)
        self._notes_after_local_mutation()
        return True

    def _notes_import_from_file(self) -> bool:
        notebook = self._notes_current_notebook()
        if notebook is None:
            return False
        dlg = wx.FileDialog(self, "选择导入文件", wildcard="文本文件 (*.txt)|*.txt|所有文件 (*.*)|*.*", style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return False
            path = Path(dlg.GetPath())
        finally:
            dlg.Destroy()
        created = import_note_entries_from_file(self.notes_store, notebook.id, path)
        if created:
            self._notes_select_notebook(notebook.id, created[0].id, view="note_detail")
            self._notes_after_local_mutation()
        return bool(created)

    def _notes_export_all_to_file(self) -> bool:
        default_name = f"notes-backup-{bindings.app.time.strftime('%Y%m%d-%H%M%S')}.json"
        dlg = wx.FileDialog(
            self,
            "导出所有笔记",
            defaultFile=default_name,
            wildcard="笔记备份 (*.json)|*.json|所有文件 (*.*)|*.*",
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        )
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return False
            path = Path(dlg.GetPath())
        finally:
            dlg.Destroy()
        if not path.suffix:
            path = path.with_suffix(".json")
        try:
            result = export_notes_backup(self.notes_store, path)
        except Exception as exc:
            self.SetStatusText(f"导出笔记失败：{exc}")
            return False
        self.SetStatusText(f"已导出所有笔记：{result['notebooks']} 个笔记，{result['entries']} 条笔记条目")
        return True

    def _notes_restore_from_backup_file(self) -> bool:
        dlg = wx.FileDialog(
            self,
            "恢复笔记",
            wildcard="笔记备份 (*.json)|*.json|所有文件 (*.*)|*.*",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return False
            path = Path(dlg.GetPath())
        finally:
            dlg.Destroy()
        try:
            result = restore_notes_backup(self.notes_store, path)
        except Exception as exc:
            self.SetStatusText(f"恢复笔记失败：{exc}")
            return False
        created_total = int(result.get("created_notebooks", 0) or 0) + int(result.get("created_entries", 0) or 0)
        if created_total:
            self._notes_refresh_ui()
            self._notes_after_local_mutation()
        self.SetStatusText(
            f"已恢复笔记：新增 {int(result.get('created_notebooks', 0) or 0)} 个笔记，"
            f"{int(result.get('created_entries', 0) or 0)} 条笔记条目"
        )
        return bool(created_total)

    def _notes_get_clipboard_text(self) -> str:
        if wx.TheClipboard.Open():
            try:
                data = wx.TextDataObject()
                if wx.TheClipboard.GetData(data):
                    return data.GetText()
            finally:
                wx.TheClipboard.Close()
        return ""

    @staticmethod
    def _notes_export_text(entries) -> str:
        parts = [str(getattr(entry, "content", "") or "") for entry in list(entries or [])]
        if not any(part.strip() for part in parts):
            return ""
        return "\n\n".join(parts)

    def _notes_export_entries_to_clipboard(self, entries, *, status_text: str) -> bool:
        text = self._notes_export_text(entries)
        if not text:
            return False
        if not self._set_clipboard_text(text):
            return False
        self.SetStatusText(status_text)
        return True

    def _notes_export_entries_to_clipboard_and_delete(self, entries, *, status_text: str) -> bool:
        frozen = []
        for entry in list(entries or []):
            entry_id = str(getattr(entry, "id", "") or "")
            if not entry_id:
                continue
            # Re-freeze from the authoritative store so repeated triggers skip
            # entries already exported and deleted, and stale views cannot
            # overwrite a newer version.
            current = self.notes_store.get_entry(entry_id)
            if current is None:
                continue
            frozen.append({
                "id": entry_id,
                "notebook_id": str(current.notebook_id),
                "version": int(getattr(current, "version", 0) or 0),
                "content": str(getattr(current, "content", "") or ""),
            })
        if not any(item["content"].strip() for item in frozen):
            return False
        text = "\n\n".join(item["content"] for item in frozen)
        # Clipboard and SQLite share no transaction: copy first, delete only
        # after the clipboard accepted the text. The flush pins the exported
        # bytes because these entries are deleted right after; a flush failure
        # aborts before any deletion and keeps every entry.
        if not self._set_clipboard_text(text, flush=True):
            self.SetStatusText("剪贴板不可用，笔记条目已保留")
            return False
        failures: list[str] = []
        deleted: list[dict] = []
        for item in frozen:
            try:
                current = self.notes_store.get_entry(item["id"], include_deleted=True)
                if current is None:
                    continue
                if int(getattr(current, "version", 0) or 0) != item["version"]:
                    failures.append(str(item["id"]))
                    continue
                self.notes_store.delete_entry_for_export(item["id"])
                deleted.append(item)
            except Exception:
                failures.append(str(item["id"]))
        notebook_id = frozen[0]["notebook_id"] if frozen else ""
        if notebook_id:
            self._notes_refresh_entries(notebook_id, self.notes_controller.active_entry_id)
        self._notes_after_local_mutation()
        if failures:
            # The clipboard still holds the text of entries that were kept;
            # rewrite it so it only contains what was actually deleted.
            remaining = "\n\n".join(
                str(item.get("content") or "")
                for item in deleted
                if str(item.get("content") or "").strip()
            )
            if remaining:
                self._set_clipboard_text(remaining, flush=True)
            else:
                # Nothing was deleted at all; the clipboard must not keep text
                # the user believes is already exported.
                try:
                    if wx.TheClipboard.Open():
                        try:
                            wx.TheClipboard.Clear()
                        finally:
                            wx.TheClipboard.Close()
                except Exception:
                    pass
            self.SetStatusText("导出完成；以下条目因并发修改已保留，可重试："
                               + "；".join(failures))
        else:
            self.SetStatusText(status_text)
        return True

    def _notes_export_selected_range_to_clipboard_and_delete(self, direction: str) -> bool:
        notebook = self._notes_current_notebook()
        if notebook is None:
            return False
        selected_entry_id = str(self._notes_selected_entry_id() or "").strip()
        if not selected_entry_id:
            return False
        projection = getattr(self, "notes_projection", None)
        entries = projection.list_entries(notebook.id) if projection is not None \
            else self.notes_store.list_entries(notebook.id)
        entry_ids = [str(entry.id) for entry in entries]
        if selected_entry_id not in entry_ids:
            return False
        selected_index = entry_ids.index(selected_entry_id)
        if str(direction or "") == "up":
            export_entries = entries[: selected_index + 1]
        else:
            export_entries = entries[selected_index:]
        return self._notes_export_entries_to_clipboard_and_delete(
            export_entries, status_text="已导出到剪贴板并删除对应笔记条目")

    def _notes_export_selected_range_to_clipboard(self, direction: str) -> bool:
        notebook = self._notes_current_notebook()
        if notebook is None:
            return False
        selected_entry_id = str(self._notes_selected_entry_id() or "").strip()
        if not selected_entry_id:
            return False
        projection = getattr(self, "notes_projection", None)
        if projection is not None:
            entries = projection.list_entries(notebook.id)
        else:
            entries = self.notes_store.list_entries(notebook.id)
        entry_ids = [str(entry.id) for entry in entries]
        if selected_entry_id not in entry_ids:
            return False
        selected_index = entry_ids.index(selected_entry_id)
        if str(direction or "") == "up":
            export_entries = entries[: selected_index + 1]
            status_text = "已向上导出笔记条目到剪贴板"
        else:
            export_entries = entries[selected_index:]
            status_text = "已向下导出笔记条目到剪贴板"
        return self._notes_export_entries_to_clipboard(export_entries, status_text=status_text)

    def _notes_export_notebook_to_clipboard(self) -> bool:
        notebook = self._notes_current_notebook()
        if notebook is None:
            return False
        projection = getattr(self, "notes_projection", None)
        if projection is not None:
            entries = projection.list_entries(notebook.id)
        else:
            entries = self.notes_store.list_entries(notebook.id)
        return self._notes_export_entries_to_clipboard(entries, status_text="已导出笔记到剪贴板")

    def _notes_copy_notebook_to_clipboard(self) -> bool:
        notebook = self._notes_current_notebook()
        if notebook is None:
            return False
        projection = getattr(self, "notes_projection", None)
        if projection is not None:
            entries = projection.list_entries(notebook.id)
        else:
            entries = self.notes_store.list_entries(notebook.id)
        text = "\n\n".join(str(entry.content or "") for entry in entries if str(entry.content or "").strip())
        if not text:
            return False
        if not self._set_clipboard_text(text):
            return False
        self.SetStatusText("已复制笔记")
        return True

    def _notes_copy_entry_to_clipboard(self) -> bool:
        entry = self._notes_current_entry()
        if entry is None:
            return False
        text = str(entry.content or "")
        if not text.strip():
            return False
        if not self._set_clipboard_text(text):
            return False
        self.SetStatusText("已复制笔记条目")
        return True

    def _notes_import_from_clipboard(self) -> bool:
        notebook = self._notes_current_notebook()
        if notebook is None:
            return False
        text = self._notes_get_clipboard_text()
        created = import_note_entries_from_clipboard(self.notes_store, notebook.id, text)
        if created:
            self._notes_select_notebook(notebook.id, created[0].id, view="note_detail")
            self._notes_after_local_mutation()
        return bool(created)

    def _on_notes_key_down(self, event):
        key = event.GetKeyCode()
        if self._handle_input_focus_space_shortcut(event):
            return
        native_focus = wx.Window.FindFocus()
        try:
            entry_list_owns_focus = bool(native_focus) and int(native_focus.GetHandle()) == int(self.notes_entry_list.GetHandle())
        except (AttributeError, TypeError, ValueError):
            entry_list_owns_focus = native_focus is self.notes_entry_list
        repeat = bool(getattr(event, "IsAutoRepeat", lambda: False)())
        if event.ControlDown() and not event.AltDown():
            if key == wx.WXK_UP and self.notes_entry_list.HasFocus():
                if self._notes_move_entry_up():
                    return
            elif key == wx.WXK_DOWN and self.notes_entry_list.HasFocus():
                if self._notes_move_entry_down():
                    return
            elif key == wx.WXK_HOME and entry_list_owns_focus and not repeat:
                if self._notes_move_entry_to_top():
                    return
            elif key == wx.WXK_END and entry_list_owns_focus and not repeat:
                if self._notes_move_entry_to_bottom():
                    return
        if self._handle_ctrl_history_navigation(event):
            return
        if self._handle_primary_tab_navigation(event):
            return
        if key == wx.WXK_ESCAPE and getattr(self, "notes_controller", None) and self.notes_controller.notes_view == "note_edit":
            if self._notes_request_exit_edit():
                return
        if self._on_any_key_down_escape_minimize(event):
            return
        if self._is_context_menu_key(key):
            self._show_notes_menu()
            return
        if event.AltDown() and key in (ord("X"), ord("x")):
            if self.notes_notebook_list.HasFocus():
                if self._notes_create_notebook():
                    return
            if self.notes_entry_list.HasFocus():
                if self._notes_create_entry():
                    return
        if event.ControlDown() and key in (ord("C"), ord("c")):
            if self.notes_notebook_list.HasFocus():
                if self._notes_copy_notebook_to_clipboard():
                    return
            if self.notes_entry_list.HasFocus():
                if self._notes_copy_entry_to_clipboard():
                    return
        if (
            getattr(self, "notes_controller", None)
            and self.notes_controller.notes_view == "note_edit"
            and ((event.ControlDown() and key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER)) or (event.AltDown() and key in (ord("S"), ord("s"))))
        ):
            self._notes_save_current_entry()
            return
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            if self.notes_notebook_list.HasFocus():
                if self._notes_open_selected_notebook():
                    return
            if self.notes_entry_list.HasFocus():
                entry_id = self._notes_selected_entry_id()
                if entry_id:
                    notebook = self._notes_current_notebook()
                    entry = self.notes_store.get_entry(entry_id) if notebook is not None else None
                    if notebook is not None and entry is not None:
                        self._notes_open_entry_edit_window(
                            notebook=notebook,
                            entry=entry,
                            base_version=int(getattr(entry, "version", 0) or 0),
                        )
                    return
        if key == wx.WXK_BACK:
            if self.notes_entry_list.HasFocus() and getattr(self, "notes_controller", None) and self.notes_controller.notes_view == "note_detail":
                notebook_id = self._notes_selected_notebook_id()
                if notebook_id:
                    self._notes_select_notebook(notebook_id, view="notes_list")
                    return
        if key == wx.WXK_DELETE:
            if self.notes_entry_list.HasFocus():
                self._notes_delete_entry()
                return
            if self.notes_notebook_list.HasFocus():
                self._notes_delete_notebook()
                return
        event.Skip()

    def _on_notes_context(self, _event):
        self._show_notes_menu()

    def _on_notes_notebook_selected(self, _event):
        notebook_id = self._notes_selected_notebook_id()
        if notebook_id:
            if notebook_id != str(self.notes_controller.active_notebook_id or ""):
                self.notes_controller.root_tab = "notes"
                self.notes_controller.active_notebook_id = notebook_id
                self.notes_controller.active_entry_id = ""
                self.notes_controller.notes_view = "notes_list"
                self.notes_controller.entry_editor_dirty = False
                self._current_notes_state = self.notes_controller.to_state_dict()
                self._notes_refresh_entries(notebook_id)
        else:
            self._notes_refresh_ui()

    def _on_notes_entry_selected(self, _event):
        entry_id = self._notes_selected_entry_id()
        if entry_id:
            current_view = self.notes_controller.notes_view or "note_detail"
            view = "note_detail" if current_view == "note_edit" else current_view
            self._notes_select_entry(entry_id, view=view, focus=False)

    def _notes_open_selected_notebook(self) -> bool:
        notebook_id = self._notes_selected_notebook_id()
        if not notebook_id:
            return False
        self._notes_select_notebook(notebook_id, view="note_detail")
        return True
