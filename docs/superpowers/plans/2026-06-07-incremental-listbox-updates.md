# Incremental ListBox Updates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace full-list refresh patterns in the desktop wxPython app with incremental row updates so screen-reader Tab and arrow-key navigation stays responsive while Codex, ClaudeCode, OpenClaw, remote sync, or notes sync work continues in the background.

**Architecture:** Introduce a focused `IncrementalListBoxModel` helper that owns `visible_ids`, `labels_by_id`, and id-to-row mapping for wx `ListBox` controls. Migrate history, execution, answer, and notes lists to update only changed rows, insert/delete/move only affected ids, and defer disruptive reordering while the user is navigating primary controls. Keep existing bounded visible-row behavior and build tests before each migration step.

**Tech Stack:** Python 3, wxPython, pytest, existing wx UI automation tests in `tests/test_*ui_automation.py`.

---

## Files And Responsibilities

- Create: `listbox_model.py`
  - Owns reusable incremental list synchronization for wx `ListBox`.
  - Provides row update, insert, delete, move, replace-page, and selection-by-id helpers.
  - Does not know about chats, answers, execution steps, or notes.

- Modify: `main.py`
  - Uses `IncrementalListBoxModel` for `history_list`, `answer_list`, `execution_list`, `notes_notebook_list`, and `notes_entry_list`.
  - Stops calling full refresh methods from hot background paths when a single-row update is enough.
  - Defers reorder operations while primary navigation controls have focus.

- Test: `tests/test_listbox_model_unit.py`
  - Unit coverage for the helper with fake ListBox controls.

- Modify: `tests/test_main_unit.py`
  - Regression tests for no full refresh, no selection stealing, and single-row updates.

- Modify: `tests/test_codex_ui_responsiveness_automation.py`
  - Real wx automation for active Codex-event bursts while the user navigates execution, answer, history, model combo, and input controls.

- Modify: `tests/test_history_ui_automation.py`
  - History list keyboard and row-move regressions.

- Modify: `tests/test_notes_ui_automation.py`
  - Notes list incremental update regressions.

---

### Task 1: Add A Reusable Incremental ListBox Model

**Files:**
- Create: `listbox_model.py`
- Test: `tests/test_listbox_model_unit.py`

- [ ] **Step 1: Write the failing helper tests**

Create `tests/test_listbox_model_unit.py`:

```python
import pytest

from listbox_model import IncrementalListBoxModel


class FakeListBox:
    def __init__(self):
        self.items = []
        self.selection = -1
        self.calls = []

    def GetCount(self):
        return len(self.items)

    def GetString(self, idx):
        return self.items[idx]

    def SetString(self, idx, label):
        self.calls.append(("SetString", idx, label))
        self.items[idx] = label

    def Append(self, label):
        self.calls.append(("Append", label))
        self.items.append(label)

    def Insert(self, label, idx):
        self.calls.append(("Insert", idx, label))
        self.items.insert(idx, label)

    def Delete(self, idx):
        self.calls.append(("Delete", idx))
        del self.items[idx]

    def Clear(self):
        self.calls.append(("Clear",))
        self.items.clear()
        self.selection = -1

    def GetSelection(self):
        return self.selection

    def SetSelection(self, idx):
        self.calls.append(("SetSelection", idx))
        self.selection = idx


def test_update_label_changes_only_existing_row():
    control = FakeListBox()
    model = IncrementalListBoxModel(control)
    model.replace_visible_page([("a", "Alpha"), ("b", "Beta")], selected_id="b")
    control.calls.clear()

    changed = model.update_label("a", "Alpha 2")

    assert changed is True
    assert control.items == ["Alpha 2", "Beta"]
    assert control.calls == [("SetString", 0, "Alpha 2")]
    assert model.visible_ids == ["a", "b"]


def test_update_label_noops_when_label_is_unchanged():
    control = FakeListBox()
    model = IncrementalListBoxModel(control)
    model.replace_visible_page([("a", "Alpha")], selected_id="a")
    control.calls.clear()

    changed = model.update_label("a", "Alpha")

    assert changed is False
    assert control.calls == []


def test_insert_and_remove_touch_only_target_rows():
    control = FakeListBox()
    model = IncrementalListBoxModel(control)
    model.replace_visible_page([("a", "Alpha"), ("c", "Charlie")])
    control.calls.clear()

    model.insert("b", "Beta", 1)
    model.remove("a")

    assert control.items == ["Beta", "Charlie"]
    assert control.calls == [("Insert", 1, "Beta"), ("Delete", 0)]
    assert model.visible_ids == ["b", "c"]


def test_move_preserves_selected_id():
    control = FakeListBox()
    model = IncrementalListBoxModel(control)
    model.replace_visible_page([("a", "Alpha"), ("b", "Beta"), ("c", "Charlie")], selected_id="b")
    control.calls.clear()

    moved = model.move("b", 0, preserve_selection=True)

    assert moved is True
    assert control.items == ["Beta", "Alpha", "Charlie"]
    assert model.visible_ids == ["b", "a", "c"]
    assert model.selected_id() == "b"
    assert control.selection == 0
    assert control.calls == [("Delete", 1), ("Insert", 0, "Beta"), ("SetSelection", 0)]


def test_replace_visible_page_noops_when_ids_and_labels_match():
    control = FakeListBox()
    model = IncrementalListBoxModel(control)
    model.replace_visible_page([("a", "Alpha"), ("b", "Beta")], selected_id="a")
    control.calls.clear()

    changed = model.replace_visible_page([("a", "Alpha"), ("b", "Beta")], selected_id="a")

    assert changed is False
    assert control.calls == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
pytest tests/test_listbox_model_unit.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'listbox_model'`.

- [ ] **Step 3: Implement the helper**

Create `listbox_model.py`:

```python
from __future__ import annotations


class IncrementalListBoxModel:
    def __init__(self, control):
        self.control = control
        self.visible_ids: list[str] = []
        self.labels_by_id: dict[str, str] = {}

    def _normalize_id(self, item_id: str) -> str:
        return str(item_id or "").strip()

    def _normalize_label(self, label: str) -> str:
        return str(label or "")

    def row_for_id(self, item_id: str) -> int:
        normalized = self._normalize_id(item_id)
        if not normalized:
            return -1
        try:
            return self.visible_ids.index(normalized)
        except ValueError:
            return -1

    def selected_id(self) -> str:
        idx = self.control.GetSelection()
        if idx < 0 or idx >= len(self.visible_ids):
            return ""
        return self.visible_ids[idx]

    def set_selection_by_id(self, item_id: str) -> bool:
        idx = self.row_for_id(item_id)
        if idx < 0:
            return False
        if self.control.GetSelection() != idx:
            self.control.SetSelection(idx)
        return True

    def replace_visible_page(self, rows: list[tuple[str, str]], selected_id: str | None = None) -> bool:
        normalized_rows = [
            (self._normalize_id(item_id), self._normalize_label(label))
            for item_id, label in rows
            if self._normalize_id(item_id)
        ]
        ids = [item_id for item_id, _label in normalized_rows]
        labels = {item_id: label for item_id, label in normalized_rows}
        current_labels = [self.labels_by_id.get(item_id, "") for item_id in self.visible_ids]
        new_labels = [label for _item_id, label in normalized_rows]
        if self.visible_ids == ids and current_labels == new_labels:
            if selected_id:
                self.set_selection_by_id(selected_id)
            return False
        self.control.Clear()
        self.visible_ids = []
        self.labels_by_id = {}
        for item_id, label in normalized_rows:
            self.control.Append(label)
            self.visible_ids.append(item_id)
            self.labels_by_id[item_id] = label
        if selected_id:
            self.set_selection_by_id(selected_id)
        return True

    def update_label(self, item_id: str, label: str) -> bool:
        normalized = self._normalize_id(item_id)
        if normalized not in self.labels_by_id:
            return False
        new_label = self._normalize_label(label)
        if self.labels_by_id.get(normalized) == new_label:
            return False
        idx = self.row_for_id(normalized)
        if idx < 0:
            return False
        self.control.SetString(idx, new_label)
        self.labels_by_id[normalized] = new_label
        return True

    def insert(self, item_id: str, label: str, index: int) -> bool:
        normalized = self._normalize_id(item_id)
        if not normalized:
            return False
        if normalized in self.labels_by_id:
            self.update_label(normalized, label)
            return self.move(normalized, index)
        idx = max(0, min(int(index), len(self.visible_ids)))
        new_label = self._normalize_label(label)
        self.control.Insert(new_label, idx)
        self.visible_ids.insert(idx, normalized)
        self.labels_by_id[normalized] = new_label
        return True

    def append(self, item_id: str, label: str) -> bool:
        return self.insert(item_id, label, len(self.visible_ids))

    def remove(self, item_id: str) -> bool:
        normalized = self._normalize_id(item_id)
        idx = self.row_for_id(normalized)
        if idx < 0:
            return False
        self.control.Delete(idx)
        del self.visible_ids[idx]
        self.labels_by_id.pop(normalized, None)
        return True

    def move(self, item_id: str, index: int, *, preserve_selection: bool = True) -> bool:
        normalized = self._normalize_id(item_id)
        old_idx = self.row_for_id(normalized)
        if old_idx < 0:
            return False
        new_idx = max(0, min(int(index), len(self.visible_ids) - 1))
        if old_idx == new_idx:
            return False
        selected_id = self.selected_id() if preserve_selection else ""
        label = self.labels_by_id[normalized]
        self.control.Delete(old_idx)
        del self.visible_ids[old_idx]
        self.control.Insert(label, new_idx)
        self.visible_ids.insert(new_idx, normalized)
        if preserve_selection and selected_id:
            self.set_selection_by_id(selected_id)
        return True
```

- [ ] **Step 4: Run helper tests**

Run:

```powershell
pytest tests/test_listbox_model_unit.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add listbox_model.py tests/test_listbox_model_unit.py
git commit -m "feat: add incremental listbox model"
```

---

### Task 2: Initialize List Models In `ChatFrame`

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_unit.py`

- [ ] **Step 1: Write the failing initialization test**

Add to `tests/test_main_unit.py`:

```python
def test_chat_frame_initializes_incremental_list_models(frame):
    assert frame.history_list_model.control is frame.history_list
    assert frame.answer_list_model.control is frame.answer_list
    assert frame.execution_list_model.control is frame.execution_list
    assert frame.notes_notebook_list_model.control is frame.notes_notebook_list
    assert frame.notes_entry_list_model.control is frame.notes_entry_list
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
pytest tests/test_main_unit.py::test_chat_frame_initializes_incremental_list_models -q
```

Expected: FAIL with `AttributeError` for missing model attributes.

- [ ] **Step 3: Wire models after controls are created**

In `main.py`, import the helper near the other local imports:

```python
from listbox_model import IncrementalListBoxModel
```

After `history_list`, `answer_list`, `execution_list`, `notes_notebook_list`, and `notes_entry_list` are created in `ChatFrame.__init__`, add:

```python
self.history_list_model = IncrementalListBoxModel(self.history_list)
self.answer_list_model = IncrementalListBoxModel(self.answer_list)
self.execution_list_model = IncrementalListBoxModel(self.execution_list)
self.notes_notebook_list_model = IncrementalListBoxModel(self.notes_notebook_list)
self.notes_entry_list_model = IncrementalListBoxModel(self.notes_entry_list)
```

- [ ] **Step 4: Run initialization test**

Run:

```powershell
pytest tests/test_main_unit.py::test_chat_frame_initializes_incremental_list_models -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add main.py tests/test_main_unit.py
git commit -m "feat: initialize incremental list models"
```

---

### Task 3: Convert History List To Single-Row Updates

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_unit.py`
- Test: `tests/test_history_ui_automation.py`

- [ ] **Step 1: Write failing unit tests for history row updates**

Add to `tests/test_main_unit.py`:

```python
def test_history_title_change_updates_single_row_without_full_refresh(frame, monkeypatch):
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame._current_chat_state = {"id": "chat-active", "title": "old title", "turns": []}
    frame.archived_chats = [
        {"id": "chat-old", "title": "old archived", "turns": [], "created_at": 1.0, "updated_at": 1.0}
    ]
    frame._refresh_history("chat-active")
    frame.history_list.SetSelection(0)

    monkeypatch.setattr(frame.history_list, "Clear", lambda: pytest.fail("history title update must not clear the list"))
    frame._current_chat_state["title"] = "new title"

    changed = frame._upsert_history_row("chat-active", allow_reorder=False)

    assert changed is True
    assert frame.history_list.GetString(0) == "new title"
    assert frame.history_ids == ["chat-active", "chat-old"]
    assert frame.history_list.GetSelection() == 0


def test_background_history_update_defers_reorder_while_primary_control_has_focus(frame, monkeypatch):
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame._current_chat_state = {"id": "chat-active", "title": "active", "turns": [], "updated_at": 10.0}
    frame.archived_chats = [
        {"id": "chat-old", "title": "old", "turns": [], "created_at": 1.0, "updated_at": 1.0}
    ]
    frame._refresh_history("chat-active")
    monkeypatch.setattr(frame, "_primary_navigation_control_has_focus", lambda: True)
    frame.history_list.SetSelection(0)

    frame.archived_chats[0]["updated_at"] = 99.0
    changed = frame._upsert_history_row("chat-old", allow_reorder=True)

    assert changed is True
    assert frame.history_ids == ["chat-active", "chat-old"]
    assert frame._pending_history_reorder is True
    assert frame.history_list.GetSelection() == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
pytest tests/test_main_unit.py::test_history_title_change_updates_single_row_without_full_refresh `
       tests/test_main_unit.py::test_background_history_update_defers_reorder_while_primary_control_has_focus -q
```

Expected: FAIL with missing `_upsert_history_row`.

- [ ] **Step 3: Add history label and row helpers**

In `main.py`, add:

```python
def _history_chat_label(self, chat: dict, *, is_current: bool = False) -> str:
    if is_current:
        return self._current_history_title()
    title = str((chat or {}).get("title") or "新聊天")
    if self._is_default_chat_title(title):
        title = EMPTY_CURRENT_CHAT_TITLE
    return f"[置顶] {title}" if (chat or {}).get("pinned") else title

def _history_chat_sort_key(self, chat_id: str) -> tuple:
    chat_id = str(chat_id or "").strip()
    current_id = self._current_history_id()
    if current_id and chat_id == current_id:
        return (0, 0, 0, chat_id)
    chat = self._find_archived_chat(chat_id)
    if not isinstance(chat, dict):
        return (9, 0, 0, chat_id)
    pinned_rank = 0 if chat.get("pinned") else 1
    updated = float(chat.get("updated_at") or chat.get("created_at") or 0.0)
    created = float(chat.get("created_at") or 0.0)
    return (1, pinned_rank, -updated, -created, chat_id)

def _history_row_for_chat_id(self, chat_id: str) -> tuple[str, str] | None:
    chat_id = str(chat_id or "").strip()
    if not chat_id:
        return None
    current_id = self._current_history_id()
    if current_id and chat_id == current_id:
        return chat_id, self._history_chat_label(self._current_chat_state, is_current=True)
    chat = self._find_archived_chat(chat_id)
    if not isinstance(chat, dict):
        return None
    return chat_id, self._history_chat_label(chat)

def _desired_history_index(self, chat_id: str) -> int:
    ids = list(getattr(self, "history_ids", []) or [])
    if chat_id not in ids:
        ids.append(chat_id)
    ordered = sorted(ids, key=self._history_chat_sort_key)
    try:
        return ordered.index(chat_id)
    except ValueError:
        return len(ids)

def _upsert_history_row(self, chat_id: str, *, allow_reorder: bool = True) -> bool:
    row = self._history_row_for_chat_id(chat_id)
    if row is None:
        return False
    item_id, label = row
    selected_id = ""
    if hasattr(self, "history_list_model"):
        selected_id = self.history_list_model.selected_id()
    if item_id not in getattr(self.history_list_model, "labels_by_id", {}):
        index = self._desired_history_index(item_id) if allow_reorder else len(self.history_ids)
        changed = self.history_list_model.insert(item_id, label, index)
    else:
        changed = self.history_list_model.update_label(item_id, label)
        if allow_reorder and not self._primary_navigation_control_has_focus():
            changed = self.history_list_model.move(item_id, self._desired_history_index(item_id)) or changed
        elif allow_reorder:
            self._pending_history_reorder = True
    self.history_ids = list(self.history_list_model.visible_ids)
    if selected_id:
        self.history_list_model.set_selection_by_id(selected_id)
    if changed:
        self._request_listbox_repaint(self.history_list)
    return changed
```

- [ ] **Step 4: Route `_refresh_history()` through the model without changing behavior**

Replace the body after labels/ids are computed with:

```python
rows = list(zip(ids, labels))
selected_id = ids[selected_idx] if selected_idx is not None and 0 <= selected_idx < len(ids) else ""
changed = self.history_list_model.replace_visible_page(rows, selected_id=selected_id)
self.history_ids = list(self.history_list_model.visible_ids)
if changed:
    self._request_listbox_repaint(self.history_list)
```

- [ ] **Step 5: Replace hot-path full history refresh calls after background completion**

In `_on_done()`, replace:

```python
self._refresh_history(resolved_chat_id or None)
```

with:

```python
if resolved_chat_id:
    self._upsert_history_row(resolved_chat_id, allow_reorder=not self._primary_navigation_control_has_focus())
else:
    self._refresh_history(None)
```

In `_on_codex_event_for_chat()` background-chat branch, when `target_chat["updated_at"]` changes, call:

```python
self._upsert_history_row(chat_id, allow_reorder=not self._primary_navigation_control_has_focus())
```

Do not replace explicit user actions such as manual refresh, deletion, pinning, or switching yet.

- [ ] **Step 6: Add UI automation for history navigation during background completion**

Add to `tests/test_history_ui_automation.py`:

```python
def test_history_navigation_stays_stable_when_background_chat_title_updates(frame, wx_app, monkeypatch):
    frame.Show()
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame._current_chat_state = {"id": "chat-active", "title": "active", "turns": []}
    frame.archived_chats = [
        {
            "id": "chat-old",
            "title": "old",
            "turns": [{"question": "q", "answer_md": main.REQUESTING_TEXT, "model": main.DEFAULT_CODEX_MODEL}],
            "created_at": 1.0,
            "updated_at": 1.0,
        }
    ]
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    frame._refresh_history("chat-active")
    frame.history_list.SetSelection(0)
    frame.history_list.SetFocusFromKbd()
    wx_app.Yield()

    frame._on_done(0, "background answer", "", main.DEFAULT_CODEX_MODEL, "", "chat-old")
    wx_app.Yield()

    assert frame.history_list.GetSelection() == 0
    assert frame.history_ids == ["chat-active", "chat-old"]
    assert frame.history_list.GetString(1)
```

- [ ] **Step 7: Run history tests**

Run:

```powershell
pytest tests/test_main_unit.py::test_history_title_change_updates_single_row_without_full_refresh `
       tests/test_main_unit.py::test_background_history_update_defers_reorder_while_primary_control_has_focus `
       tests/test_history_ui_automation.py::test_history_navigation_stays_stable_when_background_chat_title_updates -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```powershell
git add main.py tests/test_main_unit.py tests/test_history_ui_automation.py
git commit -m "feat: update history rows incrementally"
```

---

### Task 4: Convert Execution List To Append/Delete/Move Only

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_unit.py`
- Test: `tests/test_codex_ui_responsiveness_automation.py`

- [ ] **Step 1: Write failing tests for execution-list no-clear behavior under events**

Add to `tests/test_main_unit.py`:

```python
def test_execution_event_appends_single_row_without_clearing_visible_list(frame, monkeypatch):
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.active_turn_idx = 0
    frame._current_chat_state = {
        "id": "chat-active",
        "title": "active",
        "turns": [{"question": "q", "answer_md": main.REQUESTING_TEXT, "model": main.DEFAULT_CODEX_MODEL}],
        "detail_panel_mode": "execution",
        "execution_steps": [],
    }
    frame._apply_detail_panel_mode("execution", refresh_execution=True)
    monkeypatch.setattr(frame.execution_list, "Clear", lambda: pytest.fail("execution append must not clear visible list"))

    frame._on_codex_event_for_chat(
        "chat-active",
        main.CodexEvent(type="plan_updated", thread_id="", turn_id="", text="step one"),
    )

    assert any(frame.execution_list.GetString(i) for i in range(frame.execution_list.GetCount()))


def test_execution_append_does_not_select_latest_while_execution_list_has_focus(frame, monkeypatch):
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.active_turn_idx = 0
    frame._current_chat_state = {
        "id": "chat-active",
        "title": "active",
        "turns": [{"question": "q", "answer_md": main.REQUESTING_TEXT, "model": main.DEFAULT_CODEX_MODEL}],
        "detail_panel_mode": "execution",
        "execution_steps": [{"turn_idx": 0, "display_kind": "commentary", "list_text": "old step"}],
    }
    frame._apply_detail_panel_mode("execution", refresh_execution=True)
    frame.execution_list.SetSelection(0)
    monkeypatch.setattr(frame.execution_list, "HasFocus", lambda: True)

    frame._on_codex_event_for_chat(
        "chat-active",
        main.CodexEvent(type="plan_updated", thread_id="", turn_id="", text="new step"),
    )

    assert frame.execution_list.GetSelection() == 0
```

- [ ] **Step 2: Run tests**

Run:

```powershell
pytest tests/test_main_unit.py::test_execution_event_appends_single_row_without_clearing_visible_list `
       tests/test_main_unit.py::test_execution_append_does_not_select_latest_while_execution_list_has_focus -q
```

Expected: one or both tests fail until the model is wired into execution rows.

- [ ] **Step 3: Add execution row ids**

In `main.py`, add:

```python
def _execution_row_id(self, step_idx: int, step) -> str:
    if isinstance(step, dict):
        item_id = str(step.get("id") or step.get("event_id") or step.get("item_id") or "").strip()
        if item_id:
            return f"execution:{item_id}"
        created_at = str(step.get("created_at") or "").strip()
        list_text = self._execution_step_list_text(step)
        return f"execution:{step_idx}:{created_at}:{self._normalize_execution_text_for_compare(list_text)}"
    return f"execution:{step_idx}:{self._normalize_execution_text_for_compare(str(step or ''))}"
```

Update `_rebuild_execution_list_from_state()` to build `rows_for_model`:

```python
rows_for_model = [(self._execution_row_id(idx, step), row_text) for idx, (row_text, _meta) in enumerate(visible_items)]
changed = self.execution_list_model.replace_visible_page(rows_for_model, selected_id=self.execution_list_model.selected_id())
```

Keep `self.execution_meta = metas` exactly aligned with visible rows.

- [ ] **Step 4: Update append path to use model operations**

In `_append_visible_execution_entry()`, after `meta` and `row_text` are computed, create:

```python
row_id = self._execution_row_id(step_idx, step)
```

Then replace direct append with:

```python
self.execution_list_model.append(row_id, row_text)
self.execution_meta.append(meta)
```

When trimming the oldest visible execution row after "更多", delete both model and meta at the same visible index:

```python
old_row_id = self.execution_list_model.visible_ids[1]
self.execution_list_model.remove(old_row_id)
del self.execution_meta[1]
```

When inserting "更多", use a stable row id:

```python
self.execution_list_model.insert("__execution_more__", "更多", 0)
self.execution_meta.insert(0, ("more", -1, "更多", ""))
```

- [ ] **Step 5: Preserve selection when user focuses execution list**

In `_flush_deferred_execution_list_updates()`, keep the existing `HasFocus()` guard. Add:

```python
if hasattr(self, "execution_list_model"):
    selected_id = self.execution_list_model.selected_id()
else:
    selected_id = ""
```

Before any batch repaint, and restore it afterward:

```python
if selected_id:
    self.execution_list_model.set_selection_by_id(selected_id)
```

- [ ] **Step 6: Run execution unit tests**

Run:

```powershell
pytest tests/test_main_unit.py::test_execution_event_appends_single_row_without_clearing_visible_list `
       tests/test_main_unit.py::test_execution_append_does_not_select_latest_while_execution_list_has_focus `
       tests/test_main_unit.py::test_codex_ui_event_drain_coalesces_execution_list_repaints `
       tests/test_main_unit.py::test_codex_ui_event_drain_preserves_execution_selection_when_list_has_focus -q
```

Expected: PASS.

- [ ] **Step 7: Run Codex pending-event UI automation**

Run:

```powershell
pytest tests/test_codex_ui_responsiveness_automation.py::test_real_ui_primary_controls_stay_responsive_while_codex_events_are_pending -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```powershell
git add main.py tests/test_main_unit.py tests/test_codex_ui_responsiveness_automation.py
git commit -m "feat: append execution rows incrementally"
```

---

### Task 5: Convert Answer List To Row-Keyed Updates

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_unit.py`
- Test: `tests/test_codex_ui_responsiveness_automation.py`

- [ ] **Step 1: Write failing tests for answer row updates**

Add to `tests/test_main_unit.py`:

```python
def test_final_answer_updates_existing_pending_answer_row_without_clearing(frame, monkeypatch):
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {"question": "q", "answer_md": main.REQUESTING_TEXT, "model": main.DEFAULT_CODEX_MODEL}
    ]
    frame._current_chat_state = {"id": "chat-active", "turns": frame.active_session_turns, "detail_panel_mode": "answers"}
    frame._render_answer_list(refresh_execution=False)
    monkeypatch.setattr(frame.answer_list, "Clear", lambda: pytest.fail("final answer must not clear answer list"))

    frame._on_done(0, "final answer", "", main.DEFAULT_CODEX_MODEL, "", "chat-active")

    labels = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert "final answer" in labels


def test_answer_update_preserves_history_focus(frame, monkeypatch):
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {"question": "q", "answer_md": main.REQUESTING_TEXT, "model": main.DEFAULT_CODEX_MODEL}
    ]
    frame._current_chat_state = {"id": "chat-active", "turns": frame.active_session_turns, "detail_panel_mode": "answers"}
    frame._render_answer_list(refresh_execution=False)
    monkeypatch.setattr(frame, "_can_focus_completion_result", lambda: False)
    monkeypatch.setattr(frame.answer_list, "SetFocus", lambda: pytest.fail("answer completion must not steal focus"))

    frame._on_done(0, "final answer", "", main.DEFAULT_CODEX_MODEL, "", "chat-active")

    assert frame.active_session_turns[0]["answer_md"] == "final answer"
```

- [ ] **Step 2: Run tests**

Run:

```powershell
pytest tests/test_main_unit.py::test_final_answer_updates_existing_pending_answer_row_without_clearing `
       tests/test_main_unit.py::test_answer_update_preserves_history_focus -q
```

Expected: first test fails if the final answer path still falls back to full render.

- [ ] **Step 3: Add answer row ids and rebuild alignment**

In `main.py`, add:

```python
def _answer_row_id(self, meta: tuple) -> str:
    kind = str(meta[0] if meta else "")
    turn_idx = int(meta[1] if len(meta) > 1 else -1)
    if kind in {"user", "question", "ai", "answer", "attachment"}:
        detail = str(meta[3] if len(meta) > 3 else "")
        if kind == "attachment":
            return f"answer:{turn_idx}:attachment:{detail}"
        return f"answer:{turn_idx}:{kind}"
    if kind in {"context_usage", "current_model", "more", "info"}:
        return f"answer:special:{kind}"
    return f"answer:{turn_idx}:{kind}:{len(str(meta))}"
```

In `_render_answer_list()`, replace direct `_replace_listbox_items_if_changed(self.answer_list, rows, selected_idx)` with:

```python
row_ids = [self._answer_row_id(meta) for meta in metas]
selected_id = ""
if selected_idx is not None and 0 <= selected_idx < len(row_ids):
    selected_id = row_ids[selected_idx]
changed = self.answer_list_model.replace_visible_page(list(zip(row_ids, rows)), selected_id=selected_id)
```

Keep `self.answer_meta = metas` aligned.

- [ ] **Step 4: Implement single-row answer update**

Add:

```python
def _update_answer_row_for_turn(self, turn_idx: int) -> bool:
    row = self._find_answer_row_index(turn_idx)
    if row < 0 or row >= len(self.answer_meta):
        return False
    turns = self._get_view_turns()
    if turn_idx < 0 or turn_idx >= len(turns):
        return False
    answer_md, answer_text = self._turn_answer_markdown(turns[turn_idx])
    answer_text = str(answer_text or "").strip()
    if not answer_text:
        return False
    row_id = self._answer_row_id(self.answer_meta[row])
    changed = self.answer_list_model.update_label(row_id, answer_text)
    self.answer_meta[row] = ("answer", turn_idx, answer_text, answer_md)
    if changed:
        self._request_listbox_repaint(self.answer_list)
    return changed
```

Update `_update_active_answer_row()` to call `_update_answer_row_for_turn()` before it falls back to rendering.

- [ ] **Step 5: Run answer tests**

Run:

```powershell
pytest tests/test_main_unit.py::test_final_answer_updates_existing_pending_answer_row_without_clearing `
       tests/test_main_unit.py::test_answer_update_preserves_history_focus `
       tests/test_main_unit.py::test_render_answer_list_does_not_clear_when_rows_are_unchanged `
       tests/test_codex_ui_responsiveness_automation.py::test_real_ui_answer_list_navigation_stays_responsive_during_codex_event_burst -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add main.py tests/test_main_unit.py tests/test_codex_ui_responsiveness_automation.py
git commit -m "feat: update answer rows incrementally"
```

---

### Task 6: Convert Notes Lists To Incremental Updates

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_unit.py`
- Test: `tests/test_notes_ui_automation.py`

- [ ] **Step 1: Write failing notes tests**

Add to `tests/test_main_unit.py`:

```python
def test_notes_notebook_rename_updates_single_row_without_clear(frame, monkeypatch):
    notebook = frame.notes_store.create_notebook("old notebook")
    frame._notes_refresh_ui()
    row = frame._notes_notebook_ids.index(notebook.id)
    assert frame.notes_notebook_list.GetString(row)
    monkeypatch.setattr(frame.notes_notebook_list, "Clear", lambda: pytest.fail("notebook rename must not clear list"))

    frame.notes_store.update_notebook(notebook.id, title="new notebook")
    frame._notes_upsert_notebook_row(notebook.id, allow_reorder=False)

    row = frame._notes_notebook_ids.index(notebook.id)
    assert "new notebook" in frame.notes_notebook_list.GetString(row)


def test_notes_entry_edit_updates_single_row_without_clear(frame, monkeypatch):
    notebook = frame.notes_store.create_notebook("notebook")
    entry = frame.notes_store.create_entry(notebook.id, "old content", source="manual")
    frame._notes_select_notebook(notebook.id, view="note_detail")
    row = frame._notes_entry_ids.index(entry.id)
    monkeypatch.setattr(frame.notes_entry_list, "Clear", lambda: pytest.fail("entry edit must not clear list"))

    frame.notes_store.update_entry(entry.id, content="new content")
    frame._notes_upsert_entry_row(entry.id, allow_reorder=False)

    row = frame._notes_entry_ids.index(entry.id)
    assert "new content" in frame.notes_entry_list.GetString(row)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
pytest tests/test_main_unit.py::test_notes_notebook_rename_updates_single_row_without_clear `
       tests/test_main_unit.py::test_notes_entry_edit_updates_single_row_without_clear -q
```

Expected: FAIL with missing helper methods.

- [ ] **Step 3: Add notes row helpers**

Add:

```python
def _notes_notebook_label(self, notebook) -> str:
    title = str(getattr(notebook, "title", "") or "未命名笔记").strip()
    return title or "未命名笔记"

def _notes_entry_label(self, entry) -> str:
    content = str(getattr(entry, "content", "") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    first = next((line.strip() for line in content.split("\n") if line.strip()), "")
    return first[:NOTES_ENTRY_LABEL_MAX_CHARS] if first else "空笔记条目"

def _notes_upsert_notebook_row(self, notebook_id: str, *, allow_reorder: bool = True) -> bool:
    notebook = self.notes_store.get_notebook(notebook_id, include_deleted=False)
    if notebook is None:
        return self._notes_remove_notebook_row(notebook_id)
    selected_id = self.notes_notebook_list_model.selected_id()
    label = self._notes_notebook_label(notebook)
    if notebook.id in self.notes_notebook_list_model.labels_by_id:
        changed = self.notes_notebook_list_model.update_label(notebook.id, label)
    else:
        changed = self.notes_notebook_list_model.append(notebook.id, label)
    self._notes_notebook_ids = list(self.notes_notebook_list_model.visible_ids)
    if selected_id:
        self.notes_notebook_list_model.set_selection_by_id(selected_id)
    if changed:
        self._request_listbox_repaint(self.notes_notebook_list)
    return changed

def _notes_remove_notebook_row(self, notebook_id: str) -> bool:
    changed = self.notes_notebook_list_model.remove(notebook_id)
    self._notes_notebook_ids = list(self.notes_notebook_list_model.visible_ids)
    if changed:
        self._request_listbox_repaint(self.notes_notebook_list)
    return changed

def _notes_upsert_entry_row(self, entry_id: str, *, allow_reorder: bool = True) -> bool:
    entry = self.notes_store.get_entry(entry_id, include_deleted=False)
    if entry is None:
        return self._notes_remove_entry_row(entry_id)
    active_notebook_id = str(self.notes_controller.active_notebook_id or "")
    if str(getattr(entry, "notebook_id", "") or "") != active_notebook_id:
        return self._notes_remove_entry_row(entry_id)
    selected_id = self.notes_entry_list_model.selected_id()
    label = self._notes_entry_label(entry)
    if entry.id in self.notes_entry_list_model.labels_by_id:
        changed = self.notes_entry_list_model.update_label(entry.id, label)
    else:
        changed = self.notes_entry_list_model.append(entry.id, label)
    self._notes_entry_ids = list(self.notes_entry_list_model.visible_ids)
    if selected_id:
        self.notes_entry_list_model.set_selection_by_id(selected_id)
    if changed:
        self._request_listbox_repaint(self.notes_entry_list)
    return changed

def _notes_remove_entry_row(self, entry_id: str) -> bool:
    changed = self.notes_entry_list_model.remove(entry_id)
    self._notes_entry_ids = list(self.notes_entry_list_model.visible_ids)
    if changed:
        self._request_listbox_repaint(self.notes_entry_list)
    return changed
```

- [ ] **Step 4: Route full notes refresh through list models**

In `_notes_refresh_notebooks()`, build `rows = [(notebook.id, self._notes_notebook_label(notebook)) for notebook in notebooks]`, then call:

```python
self.notes_notebook_list_model.replace_visible_page(rows, selected_id=selected_id)
self._notes_notebook_ids = list(self.notes_notebook_list_model.visible_ids)
```

In `_notes_refresh_entries()`, build `rows = [(entry.id, self._notes_entry_label(entry)) for entry in entries]`, then call:

```python
self.notes_entry_list_model.replace_visible_page(rows, selected_id=selected_id)
self._notes_entry_ids = list(self.notes_entry_list_model.visible_ids)
```

- [ ] **Step 5: Run notes tests**

Run:

```powershell
pytest tests/test_main_unit.py::test_notes_notebook_rename_updates_single_row_without_clear `
       tests/test_main_unit.py::test_notes_entry_edit_updates_single_row_without_clear `
       tests/test_notes_ui_automation.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add main.py tests/test_main_unit.py tests/test_notes_ui_automation.py
git commit -m "feat: update notes rows incrementally"
```

---

### Task 7: Defer Reordering While Screen-Reader Navigation Is Active

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_unit.py`
- Test: `tests/test_codex_ui_responsiveness_automation.py`

- [ ] **Step 1: Write failing tests for deferred reorder flush**

Add to `tests/test_main_unit.py`:

```python
def test_deferred_history_reorder_flushes_when_primary_focus_leaves(frame, monkeypatch):
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame._current_chat_state = {"id": "chat-active", "title": "active", "turns": [], "updated_at": 10.0}
    frame.archived_chats = [
        {"id": "chat-old", "title": "old", "turns": [], "created_at": 1.0, "updated_at": 99.0}
    ]
    frame._refresh_history("chat-active")
    frame._pending_history_reorder = True
    monkeypatch.setattr(frame, "_primary_navigation_control_has_focus", lambda: False)

    frame._flush_deferred_list_reorders()

    assert frame._pending_history_reorder is False
    assert "chat-old" in frame.history_ids


def test_deferred_reorder_does_not_flush_while_primary_control_has_focus(frame, monkeypatch):
    frame._pending_history_reorder = True
    monkeypatch.setattr(frame, "_primary_navigation_control_has_focus", lambda: True)

    frame._flush_deferred_list_reorders()

    assert frame._pending_history_reorder is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
pytest tests/test_main_unit.py::test_deferred_history_reorder_flushes_when_primary_focus_leaves `
       tests/test_main_unit.py::test_deferred_reorder_does_not_flush_while_primary_control_has_focus -q
```

Expected: FAIL with missing `_flush_deferred_list_reorders`.

- [ ] **Step 3: Add a deferred reorder flusher**

In `main.py`, initialize:

```python
self._pending_history_reorder = False
self._pending_notes_notebook_reorder = False
self._pending_notes_entry_reorder = False
```

Add:

```python
def _flush_deferred_list_reorders(self) -> None:
    if self._primary_navigation_control_has_focus():
        return
    if getattr(self, "_pending_history_reorder", False):
        selected_id = self.history_list_model.selected_id()
        self._pending_history_reorder = False
        self._refresh_history(selected_id or None)
    if getattr(self, "_pending_notes_notebook_reorder", False):
        selected_id = self.notes_notebook_list_model.selected_id()
        self._pending_notes_notebook_reorder = False
        self._notes_refresh_notebooks(selected_id)
    if getattr(self, "_pending_notes_entry_reorder", False):
        selected_id = self.notes_entry_list_model.selected_id()
        self._pending_notes_entry_reorder = False
        self._notes_refresh_entries(select_id=selected_id)
```

Call it at the end of low-risk user actions:

```python
self._flush_deferred_list_reorders()
```

Add calls after `_on_answer_key_down`, `_on_history_key_down`, `_on_notes_key_down`, and `_on_generic_key_down` only after they finish handling non-Tab/non-arrow commands. Do not call it from background event drain loops.

- [ ] **Step 4: Run deferred reorder tests**

Run:

```powershell
pytest tests/test_main_unit.py::test_deferred_history_reorder_flushes_when_primary_focus_leaves `
       tests/test_main_unit.py::test_deferred_reorder_does_not_flush_while_primary_control_has_focus -q
```

Expected: PASS.

- [ ] **Step 5: Add UI automation for deferred reorder**

Add to `tests/test_codex_ui_responsiveness_automation.py`:

```python
def test_real_ui_history_reorder_is_deferred_during_keyboard_navigation(frame, wx_app, monkeypatch):
    frame.Show()
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame._current_chat_state = {"id": "chat-active", "title": "active", "turns": [], "updated_at": 10.0}
    frame.archived_chats = [
        {"id": "chat-old", "title": "old", "turns": [], "created_at": 1.0, "updated_at": 1.0}
    ]
    frame._refresh_history("chat-active")
    frame.history_list.SetSelection(0)
    frame.history_list.SetFocusFromKbd()
    wx_app.Yield()

    frame.archived_chats[0]["updated_at"] = 99.0
    frame._upsert_history_row("chat-old", allow_reorder=True)
    _send_listbox_key(frame.history_list, main.wx.WXK_DOWN)
    wx_app.Yield()

    assert frame.history_ids == ["chat-active", "chat-old"]
    assert frame._pending_history_reorder is True
```

- [ ] **Step 6: Run UI automation**

Run:

```powershell
pytest tests/test_codex_ui_responsiveness_automation.py::test_real_ui_history_reorder_is_deferred_during_keyboard_navigation -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add main.py tests/test_main_unit.py tests/test_codex_ui_responsiveness_automation.py
git commit -m "feat: defer list reorders during navigation"
```

---

### Task 8: Add A Strict Background-Task Screen-Reader Regression Suite

**Files:**
- Modify: `tests/test_codex_ui_responsiveness_automation.py`
- Modify: `tests/test_main_unit.py`

- [ ] **Step 1: Add operation-count assertions for primary controls**

Add to `tests/test_main_unit.py`:

```python
def test_codex_background_burst_does_not_clear_primary_lists(frame, monkeypatch):
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {"question": "q", "answer_md": main.REQUESTING_TEXT, "model": main.DEFAULT_CODEX_MODEL}
    ]
    frame._current_chat_state = {
        "id": "chat-active",
        "title": "active",
        "turns": frame.active_session_turns,
        "detail_panel_mode": "execution",
        "execution_steps": [],
    }
    frame._apply_detail_panel_mode("execution", refresh_execution=True)
    monkeypatch.setattr(frame.history_list, "Clear", lambda: pytest.fail("background Codex burst must not clear history"))
    monkeypatch.setattr(frame.answer_list, "Clear", lambda: pytest.fail("background Codex burst must not clear answers"))
    monkeypatch.setattr(frame.execution_list, "Clear", lambda: pytest.fail("background Codex burst must not clear execution"))

    for idx in range(main.CODEX_UI_EVENT_BATCH_SIZE * 3):
        frame._pending_codex_ui_events.append(
            ("chat-active", main.CodexEvent(type="plan_updated", text=f"step {idx}"))
        )
    frame._codex_ui_event_flush_scheduled = True
    frame._drain_codex_ui_events()

    assert frame._pending_codex_ui_events
```

- [ ] **Step 2: Add end-to-end wx timing assertions**

Add to `tests/test_codex_ui_responsiveness_automation.py`:

```python
def test_real_ui_background_answering_keeps_all_primary_controls_under_200ms(frame, wx_app, monkeypatch):
    frame.Show()
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.active_codex_thread_id = "thread-active"
    frame.active_codex_turn_id = "turn-active"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {
            "question": "q",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL,
            "codex_thread_id": "thread-active",
            "codex_turn_id": "turn-active",
        }
    ]
    frame._current_chat_state = {
        "id": "chat-active",
        "title": "active",
        "turns": frame.active_session_turns,
        "detail_panel_mode": "execution",
        "execution_steps": [],
    }
    frame.archived_chats = [
        {"id": f"chat-{idx}", "title": f"chat {idx}", "turns": [], "created_at": float(idx), "updated_at": float(idx)}
        for idx in range(50)
    ]
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    frame._refresh_history("chat-active")
    frame._apply_detail_panel_mode("execution", refresh_execution=True)

    for idx in range(main.CODEX_UI_EVENT_BATCH_SIZE * 8):
        frame._dispatch_codex_event_to_ui(
            "chat-active",
            main.CodexEvent(type="plan_updated", thread_id="thread-active", turn_id="turn-active", text=f"step {idx}"),
        )

    checks = [
        (frame.execution_list, lambda: _send_listbox_key(frame.execution_list, main.wx.WXK_DOWN)),
        (frame.history_list, lambda: _send_listbox_key(frame.history_list, main.wx.WXK_DOWN)),
        (frame.input_edit, lambda: frame.input_edit.WriteText("x")),
        (frame.model_combo, lambda: _send_window_key(frame.model_combo, main.wx.WXK_DOWN)),
    ]
    for control, action in checks:
        control.SetFocus()
        wx_app.Yield()
        started = time.perf_counter()
        action()
        wx_app.Yield()
        assert time.perf_counter() - started < 0.2
```

- [ ] **Step 3: Run strict regression suite**

Run:

```powershell
pytest tests/test_main_unit.py::test_codex_background_burst_does_not_clear_primary_lists `
       tests/test_codex_ui_responsiveness_automation.py::test_real_ui_background_answering_keeps_all_primary_controls_under_200ms -q
```

Expected: PASS.

- [ ] **Step 4: Commit**

```powershell
git add tests/test_main_unit.py tests/test_codex_ui_responsiveness_automation.py
git commit -m "test: cover background answering keyboard responsiveness"
```

---

### Task 9: Run Full Targeted Verification

**Files:**
- No code changes expected.

- [ ] **Step 1: Run helper and focused unit tests**

Run:

```powershell
pytest tests/test_listbox_model_unit.py `
       tests/test_main_unit.py -k "incremental or listbox or history_title_change or background_history_update or execution_event_appends or final_answer_updates_existing or notes_notebook_rename or notes_entry_edit or deferred_history_reorder or codex_background_burst" -q
```

Expected: PASS.

- [ ] **Step 2: Run wx UI automation for affected surfaces**

Run:

```powershell
pytest tests/test_history_ui_automation.py `
       tests/test_notes_ui_automation.py `
       tests/test_codex_ui_responsiveness_automation.py -q
```

Expected: PASS.

- [ ] **Step 3: Run adjacent desktop regression tests**

Run:

```powershell
pytest tests/test_chat_client_unit.py `
       tests/test_codex_client_unit.py `
       tests/test_nats_runtime.py `
       tests/test_main_unit.py::test_streaming_delta_defers_state_save `
       tests/test_main_unit.py::test_background_done_does_not_rerender_different_viewed_history_chat `
       tests/test_main_unit.py::test_focus_latest_answer_does_not_steal_focus_after_user_tabs_away `
       tests/test_main_unit.py::test_focus_latest_answer_does_not_steal_focus_from_execution_list_when_find_focus_is_empty -q
```

Expected: PASS.

- [ ] **Step 4: Run whitespace check**

Run:

```powershell
git diff --check
```

Expected: exit code 0. CRLF warnings are acceptable if there are no whitespace errors.

- [ ] **Step 5: Commit verification-only adjustments if any**

Only if tests required small fixes:

```powershell
git add main.py listbox_model.py tests
git commit -m "fix: stabilize incremental listbox regressions"
```

---

### Task 10: Manual Screen-Reader Validation Checklist

**Files:**
- No code changes expected.

- [ ] **Step 1: Start the desktop app**

Run:

```powershell
python main.py
```

Expected: app window opens.

- [ ] **Step 2: Start a Codex or ClaudeCode prompt that produces multiple execution events**

Use a prompt that triggers planning, file search, and answer generation:

```text
请检查当前项目中 main.py 的历史列表刷新逻辑，列出可能影响读屏软件响应的路径，不要修改文件。
```

Expected: execution process list receives background steps.

- [ ] **Step 3: Test keyboard responsiveness with screen reader running**

While the model is answering:

```text
Tab: input -> new chat -> model combo -> Codex speed combo -> history/notes -> answer/execution -> input
Arrow Down/Up: answer list
Arrow Down/Up: execution list
Arrow Down/Up: history list
Arrow Down/Up: model combo
Typing: input box
Click/Enter: send button
Click/Enter: new chat button
```

Expected:

```text
No multi-second stalls.
No focus jumping to answer list unless completion happens while focus is in input/answer/send.
No history list reorder while user is navigating it.
No repeated reading of the whole list after each execution event.
```

- [ ] **Step 4: Record observed results**

Append a short note to the implementation PR or task summary:

```text
Screen reader manual validation:
- Reader:
- Scenario:
- Background model:
- Lists tested:
- Result:
- Remaining issue:
```

---

## Self-Review

- Spec coverage: The plan covers history, answer, execution, notes notebook, and notes entry lists. It includes background Codex/ClaudeCode-like event pressure, keyboard navigation, no-clear regressions, and deferred reorder behavior.
- Placeholder scan: No unresolved placeholder steps remain. Each task includes concrete files, code, commands, and expected results.
- Type consistency: The plan defines `IncrementalListBoxModel` once and uses the same method names throughout: `replace_visible_page`, `update_label`, `insert`, `append`, `remove`, `move`, `selected_id`, and `set_selection_by_id`.
