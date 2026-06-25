# UI Responsiveness Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the remaining UI-thread work that can grow with chat history, execution logs, streaming deltas, or note volume and cause screen-reader keyboard navigation to lag.

**Architecture:** Keep keyboard handlers and visible-list navigation O(1) or bounded by the visible row limit. Move heavy persistence and remote/status work behind debounced or background paths, and make every list rebuild conditional on visible state changes.

**Tech Stack:** wxPython, SQLite-backed `ChatStore` and `NotesStore`, pytest, Windows wx UI automation tests.

---

### Task 1: Stop History Answer Entry From Loading Execution Logs

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_unit.py`
- Test: `tests/test_codex_ui_responsiveness_automation.py`

- [ ] **Step 1: Write the failing unit test**

Add this test near the existing chat-store hydration tests in `tests/test_main_unit.py`:

```python
def test_show_history_chat_does_not_load_execution_steps_for_answer_view(frame, tmp_path, monkeypatch):
    frame.chat_db_path = tmp_path / "chat_history.db"
    frame.chat_store = main.ChatStore(frame.chat_db_path)
    frame.chat_store.initialize()
    frame._chat_store_enabled = True
    frame.chat_store.upsert_chat({"id": "chat-heavy", "title": "heavy", "updated_at": 10.0})
    frame.chat_store.replace_turns("chat-heavy", [{"question": "q", "answer_md": "a"}])
    for idx in range(500):
        frame.chat_store.append_execution_step("chat-heavy", {"turn_idx": 0, "list_text": f"step {idx}"})
    frame.archived_chats = frame.chat_store.list_chat_summaries()
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        frame.chat_store,
        "load_execution_steps",
        lambda *args, **kwargs: pytest.fail("history answer view should not load execution steps"),
    )

    assert frame._show_history_chat("chat-heavy", focus_answer_list=False) is True

    assert frame.view_mode == "history"
    assert frame.view_history_id == "chat-heavy"
    assert "execution_steps" not in frame.archived_chats[0]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_main_unit.py::test_show_history_chat_does_not_load_execution_steps_for_answer_view -q`

Expected: FAIL because `_show_history_chat()` calls `_hydrate_chat_from_store()` with the default `include_execution_steps=True`.

- [ ] **Step 3: Implement the minimal fix**

Change `main.py` inside `_show_history_chat()`:

```python
chat = self._hydrate_chat_from_store(
    self._find_archived_chat(selected_id),
    include_execution_steps=False,
)
```

Keep `_current_execution_steps()` unchanged for now so execution view can still load execution logs when the user explicitly opens that view.

- [ ] **Step 4: Add wx UI automation coverage**

Extend `tests/test_codex_ui_responsiveness_automation.py::test_real_ui_history_answer_navigation_does_not_load_execution_history` so it calls `_show_history_chat("chat-heavy", focus_answer_list=False)` before rendering and keeps the `load_execution_steps` fail-fast monkeypatch.

- [ ] **Step 5: Verify**

Run:

```powershell
pytest tests/test_main_unit.py::test_show_history_chat_does_not_load_execution_steps_for_answer_view `
       tests/test_codex_ui_responsiveness_automation.py::test_real_ui_history_answer_navigation_does_not_load_execution_history -q
```

Expected: PASS.

---

### Task 2: Page Execution Log Rendering Instead of Loading All Rows

**Files:**
- Modify: `chat_store.py`
- Modify: `main.py`
- Test: `tests/test_chat_store_unit.py`
- Test: `tests/test_main_unit.py`
- Test: `tests/test_codex_ui_responsiveness_automation.py`

- [ ] **Step 1: Add store tests for bounded execution reads**

Add to `tests/test_chat_store_unit.py`:

```python
def test_chat_store_loads_recent_execution_steps_with_total_count(tmp_path):
    store = ChatStore(tmp_path / "chat_history.db", max_execution_steps_per_turn=1000)
    store.initialize()
    store.upsert_chat({"id": "chat-1", "title": "First"})
    for idx in range(150):
        store.append_execution_step("chat-1", {"turn_idx": 2, "list_text": f"step {idx}"})

    total, rows = store.load_recent_execution_steps("chat-1", turn_idx=2, limit=10)

    assert total == 150
    assert [row["list_text"] for row in rows] == [f"step {idx}" for idx in range(140, 150)]
```

- [ ] **Step 2: Run the store test to verify it fails**

Run: `pytest tests/test_chat_store_unit.py::test_chat_store_loads_recent_execution_steps_with_total_count -q`

Expected: FAIL because `load_recent_execution_steps()` does not exist.

- [ ] **Step 3: Implement the store method**

Add this method to `ChatStore`:

```python
def load_recent_execution_steps(
    self,
    chat_id: str,
    *,
    turn_idx: int | None = None,
    limit: int = 100,
) -> tuple[int, list[dict[str, Any]]]:
    normalized = str(chat_id or "").strip()
    if not normalized:
        return 0, []
    params: list[Any] = [normalized]
    where = "chat_id = ?"
    if turn_idx is not None:
        where += " AND turn_idx = ?"
        params.append(int(turn_idx))
    row_limit = max(1, int(limit or 1))
    with self._connect() as conn:
        total_row = conn.execute(f"SELECT COUNT(*) AS total FROM execution_steps WHERE {where}", tuple(params)).fetchone()
        rows = conn.execute(
            f"""
            SELECT payload_json, turn_idx, event_type, display_kind, list_text, detail_text
            FROM execution_steps
            WHERE {where}
            ORDER BY step_index DESC
            LIMIT ?
            """,
            tuple(params + [row_limit]),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for row in reversed(rows):
        payload = self._json_dict(row["payload_json"])
        payload.setdefault("turn_idx", row["turn_idx"])
        payload.setdefault("event_type", str(row["event_type"] or ""))
        payload.setdefault("display_kind", str(row["display_kind"] or ""))
        payload.setdefault("list_text", str(row["list_text"] or ""))
        payload.setdefault("detail_text", str(row["detail_text"] or ""))
        out.append(payload)
    return int(total_row["total"] if total_row is not None else 0), out
```

- [ ] **Step 4: Route execution rendering through the bounded store method**

In `main.py`, add a helper that chooses `turn_idx` for active chats and calls `load_recent_execution_steps()` when the chat has been persisted:

```python
def _current_execution_steps_for_render(self) -> tuple[int, list]:
    limit = max(
        EXECUTION_LIST_DEFAULT_VISIBLE_ROWS,
        int(getattr(self, "execution_visible_row_limit", EXECUTION_LIST_DEFAULT_VISIBLE_ROWS) or 0),
    )
    chat_id = self._visible_execution_chat_id()
    store = getattr(self, "chat_store", None)
    if getattr(self, "_chat_store_enabled", False) and store is not None and chat_id:
        turn_idx = None
        if self.view_mode == "active":
            active_idx = int(getattr(self, "active_turn_idx", -1) or -1)
            turn_idx = active_idx if active_idx >= 0 else None
        total, rows = store.load_recent_execution_steps(chat_id, turn_idx=turn_idx, limit=limit)
        return total, rows
    rows = list(self._current_execution_steps())
    return len(rows), rows[-limit:]
```

Then update `_rebuild_execution_list_from_state()` to use `(total, steps)` and set `has_more = total > len(visible_items)` after filtering visible rows.

- [ ] **Step 5: Verify execution view remains bounded**

Add a unit test in `tests/test_main_unit.py` that monkeypatches `frame.chat_store.load_execution_steps` to fail, creates 2000 persisted execution steps, switches to execution mode, and asserts only the recent page is rendered.

Run:

```powershell
pytest tests/test_chat_store_unit.py::test_chat_store_loads_recent_execution_steps_with_total_count `
       tests/test_main_unit.py -k "execution_list and recent" -q
```

Expected: PASS.

---

### Task 3: Debounce Streaming Delta Saves

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_unit.py`
- Test: `tests/test_codex_ui_responsiveness_automation.py`

- [ ] **Step 1: Write a failing test for non-Codex streaming deltas**

Add to `tests/test_main_unit.py`:

```python
def test_streaming_delta_defers_state_save(frame, monkeypatch):
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.active_session_turns = [{"question": "q", "answer_md": main.REQUESTING_TEXT, "model": "openai/gpt-5.2"}]
    frame._current_chat_state = {"id": "chat-active", "turns": frame.active_session_turns}
    immediate_saves = []
    deferred_saves = []
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: immediate_saves.append(kwargs))
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: deferred_saves.append(True))

    frame._on_delta_for_chat(0, "a", "chat-active")
    frame._on_delta_for_chat(0, "b", "chat-active")

    assert immediate_saves == []
    assert deferred_saves == [True, True]
    assert frame.active_session_turns[0]["answer_md"] == "ab"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_main_unit.py::test_streaming_delta_defers_state_save -q`

Expected: FAIL because `_on_delta_for_chat()` calls `_save_state()` directly.

- [ ] **Step 3: Implement a generic debounced save**

Add to `main.py`:

```python
def _defer_chat_state_save(self) -> None:
    self._chat_state_flush_dirty = True
    if getattr(self, "_chat_state_flush_scheduled", False):
        return
    self._chat_state_flush_scheduled = True
    timer = self._call_later_if_alive(300, self._flush_chat_state_save)
    if timer is None and not self._call_after_if_alive(self._flush_chat_state_save):
        self._flush_chat_state_save()

def _flush_chat_state_save(self) -> None:
    self._chat_state_flush_scheduled = False
    if not getattr(self, "_chat_state_flush_dirty", False):
        return
    self._chat_state_flush_dirty = False
    self._save_state()
```

In `_on_delta_for_chat()`, replace `_save_state()` with `_defer_chat_state_save()`.

- [ ] **Step 4: Flush before close and destructive history actions**

Call `_flush_chat_state_save()` at the beginning of `_on_close()`, `_archive_active_session()`, `_history_delete()`, and `_history_clear_non_pinned()` before they mutate or persist chat state.

- [ ] **Step 5: Verify**

Run:

```powershell
pytest tests/test_main_unit.py::test_streaming_delta_defers_state_save `
       tests/test_main_unit.py -k "on_delta or save_state or submit_question" -q
```

Expected: PASS.

---

### Task 4: Make Notes UI Refresh Diff-Aware

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_unit.py`
- Test: `tests/test_notes_ui_automation.py`

- [ ] **Step 1: Write a failing test for no-op notes refresh**

Add to `tests/test_main_unit.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_main_unit.py::test_notes_refresh_ui_skips_list_rebuild_when_projection_unchanged -q`

Expected: FAIL because `_notes_refresh_ui()` always clears and rebuilds both listboxes.

- [ ] **Step 3: Add list snapshot comparison helpers**

Add helpers in `main.py`:

```python
def _listbox_strings(self, control) -> list[str]:
    return [control.GetString(i) for i in range(control.GetCount())]

def _replace_listbox_items_if_changed(self, control, labels: list[str], selected_idx: int | None = None) -> bool:
    if self._listbox_strings(control) == labels:
        if selected_idx is not None and control.GetSelection() != selected_idx:
            control.SetSelection(selected_idx)
        return False
    control.Clear()
    for label in labels:
        control.Append(label)
    if labels and selected_idx is not None:
        control.SetSelection(max(0, min(selected_idx, len(labels) - 1)))
    return True
```

- [ ] **Step 4: Use helpers in `_notes_refresh_notebooks()` and `_notes_refresh_entries()`**

Build label lists first, compare with current listbox strings, and only `Clear()`/`Append()` when labels changed. Preserve selection if the IDs are unchanged.

- [ ] **Step 5: Verify**

Run:

```powershell
pytest tests/test_main_unit.py::test_notes_refresh_ui_skips_list_rebuild_when_projection_unchanged `
       tests/test_notes_ui_automation.py -q
```

Expected: PASS.

---

### Task 5: Guard UI Calls From Background Threads

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_unit.py`

- [ ] **Step 1: Write a failing test for remote autostart UI marshaling**

Add to `tests/test_main_unit.py`:

```python
def test_remote_nats_worker_status_updates_are_marshaled_to_ui(frame, monkeypatch):
    posted = []
    monkeypatch.setattr(frame, "_call_after_if_alive", lambda fn, *args, **kwargs: posted.append((fn, args, kwargs)) or True)
    calls = []
    monkeypatch.setattr(frame, "SetStatusText", lambda text: calls.append(text))

    frame._set_status_text_safe("ready")

    assert calls == []
    assert posted and posted[0][0] == frame.SetStatusText
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_main_unit.py::test_remote_nats_worker_status_updates_are_marshaled_to_ui -q`

Expected: FAIL because `_set_status_text_safe()` does not exist.

- [ ] **Step 3: Add a safe status helper**

Add to `main.py`:

```python
def _set_status_text_safe(self, text: str) -> None:
    if threading.current_thread() is threading.main_thread():
        self.SetStatusText(text)
        return
    self._call_after_if_alive(self.SetStatusText, str(text or ""))
```

- [ ] **Step 4: Replace worker-path `SetStatusText()` calls**

Replace `self.SetStatusText(...)` with `self._set_status_text_safe(...)` inside remote NATS/cloudflared startup and verification paths that can run under `_schedule_remote_nats_autostart()`'s worker thread.

- [ ] **Step 5: Verify**

Run:

```powershell
pytest tests/test_main_unit.py -k "remote_nats or cloudflared or status_updates_are_marshaled" -q
```

Expected: PASS.

---

### Task 6: Add A Long-Session Responsiveness Regression Scenario

**Files:**
- Modify: `tests/test_codex_ui_responsiveness_automation.py`

- [ ] **Step 1: Add the regression test**

Add a wx UI automation test that creates:

```python
for chat_idx in range(20):
    frame.archived_chats.append({"id": f"chat-{chat_idx}", "title": f"chat {chat_idx}", "turns": []})
for turn_idx in range(20):
    frame.active_session_turns.append({"question": f"q{turn_idx}", "answer_md": f"a{turn_idx}", "model": main.DEFAULT_CODEX_MODEL})
for step_idx in range(2000):
    frame._current_chat_state["execution_steps"].append({"turn_idx": step_idx % 20, "display_kind": "commentary", "list_text": f"step {step_idx}"})
```

Then measure under 0.5s for:

```python
_send_listbox_key(frame.answer_list, main.wx.WXK_DOWN)
_send_listbox_key(frame.history_list, main.wx.WXK_DOWN)
_send_window_key(frame.model_combo, main.wx.WXK_DOWN)
frame.input_edit.WriteText("x")
```

- [ ] **Step 2: Run the test**

Run: `pytest tests/test_codex_ui_responsiveness_automation.py::test_real_ui_long_session_primary_controls_remain_responsive -q`

Expected: PASS after Tasks 1-5.

- [ ] **Step 3: Run the targeted suite**

Run:

```powershell
pytest tests/test_chat_store_unit.py `
       tests/test_main_unit.py -k "history or execution or save_state or notes or remote_nats or cloudflared" `
       tests/test_history_ui_automation.py `
       tests/test_notes_ui_automation.py `
       tests/test_codex_ui_responsiveness_automation.py -q
```

Expected: PASS.

---

### Completion Checklist

- [ ] `git diff --check`
- [ ] Targeted unit tests pass.
- [ ] Targeted wx UI automation passes on the Windows desktop.
- [ ] Package with `.\package_mc.ps1` after closing any running `mc.exe`.
- [ ] Smoke-test packaged `C:\code\cx\mc\mc.exe` with a large `history/chat_history.db`.
