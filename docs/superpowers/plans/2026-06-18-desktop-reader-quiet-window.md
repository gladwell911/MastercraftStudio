# Desktop Reader Quiet Window Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent background Codex UI updates from interfering with screen reader keyboard navigation by adding a 3-second navigation quiet window and deferring background list mutations.

**Architecture:** Add a small quiet-window state machine to `ChatFrame`, touch it from foreground keyboard handlers, and route background Codex UI event draining through a guard that updates stores but does not mutate wx controls while quiet is active. Execution steps generated during quiet are collected as pending tail appends and flushed in order after the quiet window expires.

**Tech Stack:** Python, wxPython, pytest, existing `IncrementalListBoxModel`, existing Codex event queue in `main.py`.

---

## File Structure

- Modify `main.py`: add quiet-window helpers, key detection, background UI guard, execution append deferral, quiet-expiration drain, and small diagnostics.
- Modify `tests/test_main_unit.py`: add unit tests for trigger detection, quiet extension, background mutation deferral, execution append flushing, and direct user command behavior.
- Modify `tests/test_history_ui_automation.py`: add/extend UI automation for key-trigger quiet behavior and focus stability.
- No mobile `rc` changes are required for this phase.

## Task 1: Add Quiet Window State And Trigger Detection

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_unit.py`

- [ ] **Step 1: Write failing tests for quiet trigger detection**

Add these tests near the existing shortcut/key mapping tests in `tests/test_main_unit.py`:

```python
class _QuietKeyEvent:
    def __init__(self, key, *, alt=False, shift=False, ctrl=False):
        self._key = key
        self._alt = alt
        self._shift = shift
        self._ctrl = ctrl

    def GetKeyCode(self):
        return self._key

    def AltDown(self):
        return self._alt

    def ShiftDown(self):
        return self._shift

    def ControlDown(self):
        return self._ctrl


def test_navigation_quiet_trigger_keys(frame):
    keys = [
        main.wx.WXK_TAB,
        main.wx.WXK_UP,
        main.wx.WXK_DOWN,
        main.wx.WXK_LEFT,
        main.wx.WXK_RIGHT,
        main.wx.WXK_HOME,
        main.wx.WXK_END,
        main.wx.WXK_PAGEUP,
        main.wx.WXK_PAGEDOWN,
        main.wx.WXK_RETURN,
        main.wx.WXK_NUMPAD_ENTER,
        main.wx.WXK_SPACE,
    ]
    for key in keys:
        assert frame._is_navigation_quiet_trigger(_QuietKeyEvent(key))
    assert frame._is_navigation_quiet_trigger(_QuietKeyEvent(main.wx.WXK_TAB, shift=True))
    for key in "ASDFGBCasdfgbc":
        assert frame._is_navigation_quiet_trigger(_QuietKeyEvent(ord(key), alt=True))
    assert not frame._is_navigation_quiet_trigger(_QuietKeyEvent(ord("X"), alt=True))
    assert not frame._is_navigation_quiet_trigger(_QuietKeyEvent(ord("A"), alt=False))


def test_navigation_quiet_window_extends_on_repeated_trigger(frame, monkeypatch):
    times = iter([100.0, 101.5])
    monkeypatch.setattr(main.time, "monotonic", lambda: next(times))

    frame._touch_navigation_quiet_window(_QuietKeyEvent(main.wx.WXK_DOWN))
    assert frame._navigation_quiet_until == 103.0

    frame._touch_navigation_quiet_window(_QuietKeyEvent(main.wx.WXK_UP))
    assert frame._navigation_quiet_until == 104.5
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
pytest tests/test_main_unit.py::test_navigation_quiet_trigger_keys tests/test_main_unit.py::test_navigation_quiet_window_extends_on_repeated_trigger -q
```

Expected: both fail because `_is_navigation_quiet_trigger`, `_touch_navigation_quiet_window`, or `_navigation_quiet_until` does not exist.

- [ ] **Step 3: Add minimal quiet-window implementation**

In `main.py`, near existing shortcut helper constants, add:

```python
NAVIGATION_QUIET_SECONDS = 3.0
NAVIGATION_QUIET_ALT_KEYS = {ord(ch) for ch in "ASDFGBCasdfgbc"}
NAVIGATION_QUIET_KEYS = {
    wx.WXK_TAB,
    wx.WXK_UP,
    wx.WXK_DOWN,
    wx.WXK_LEFT,
    wx.WXK_RIGHT,
    wx.WXK_HOME,
    wx.WXK_END,
    wx.WXK_PAGEUP,
    wx.WXK_PAGEDOWN,
    wx.WXK_RETURN,
    wx.WXK_NUMPAD_ENTER,
    wx.WXK_SPACE,
}
```

In `ChatFrame.__init__`, initialize:

```python
self._navigation_quiet_until = 0.0
self._navigation_quiet_last_trigger = ""
self._deferred_background_ui_counts = {}
```

Add methods to `ChatFrame`:

```python
def _is_navigation_quiet_trigger(self, event) -> bool:
    try:
        key = int(event.GetKeyCode())
    except Exception:
        return False
    alt_down = self._event_alt_down(event)
    ctrl_down = self._event_control_down(event)
    if ctrl_down:
        return False
    if alt_down:
        return key in NAVIGATION_QUIET_ALT_KEYS
    return key in NAVIGATION_QUIET_KEYS


def _touch_navigation_quiet_window(self, event) -> None:
    if not self._is_navigation_quiet_trigger(event):
        return
    self._navigation_quiet_until = time.monotonic() + NAVIGATION_QUIET_SECONDS
    try:
        key = int(event.GetKeyCode())
    except Exception:
        key = 0
    self._navigation_quiet_last_trigger = f"key:{key}"


def _navigation_quiet_active(self) -> bool:
    return time.monotonic() < float(getattr(self, "_navigation_quiet_until", 0.0) or 0.0)
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
pytest tests/test_main_unit.py::test_navigation_quiet_trigger_keys tests/test_main_unit.py::test_navigation_quiet_window_extends_on_repeated_trigger -q
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```powershell
git add main.py tests/test_main_unit.py
git commit -m "feat: add reader navigation quiet window"
```

## Task 2: Touch Quiet Window From Foreground Key Handlers

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_unit.py`

- [ ] **Step 1: Write failing tests for handler ordering**

Add tests to `tests/test_main_unit.py`:

```python
def test_char_hook_touches_quiet_window_before_alt_a_clear(frame, monkeypatch):
    frame.active_chat_id = "chat-a"
    frame.current_chat_id = "chat-a"
    frame._current_chat_state = {"id": "chat-a", "turns": []}
    observed = []

    def fake_clear():
        observed.append(frame._navigation_quiet_until)
        return True

    monkeypatch.setattr(main.time, "monotonic", lambda: 200.0)
    monkeypatch.setattr(frame, "_clear_context_and_start_new_chat", fake_clear)

    event = _QuietKeyEvent(ord("A"), alt=True)
    frame._on_char_hook(event)

    assert observed == [203.0]


def test_input_key_down_touches_quiet_window_before_send_shortcut(frame, monkeypatch):
    observed = []
    monkeypatch.setattr(main.time, "monotonic", lambda: 300.0)
    monkeypatch.setattr(frame, "_trigger_send", lambda: observed.append(frame._navigation_quiet_until))

    event = _QuietKeyEvent(main.wx.WXK_RETURN)
    frame._on_input_key_down(event)

    assert observed == [303.0]
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
pytest tests/test_main_unit.py::test_char_hook_touches_quiet_window_before_alt_a_clear tests/test_main_unit.py::test_input_key_down_touches_quiet_window_before_send_shortcut -q
```

Expected: fail because handlers do not touch the quiet window yet.

- [ ] **Step 3: Touch quiet window at the beginning of key handlers**

In these `ChatFrame` handlers, call `_touch_navigation_quiet_window(event)` immediately after escape-minimize handling and before command-specific logic:

- `_on_char_hook`
- `_on_input_key_down`
- `_on_generic_key_down`
- `_on_history_key_down`
- `_on_answer_key_down`
- `_on_execution_key_down`

Use this pattern:

```python
if self._on_any_key_down_escape_minimize(event):
    return
self._touch_navigation_quiet_window(event)
```

For `_on_char_hook`, it does not call `_on_any_key_down_escape_minimize`; add the quiet touch after key/alt/ctrl calculation and before `_handle_window_focus_shortcut`.

- [ ] **Step 4: Run tests**

Run:

```powershell
pytest tests/test_main_unit.py::test_char_hook_touches_quiet_window_before_alt_a_clear tests/test_main_unit.py::test_input_key_down_touches_quiet_window_before_send_shortcut -q
```

Expected: `2 passed`.

- [ ] **Step 5: Run existing shortcut regressions**

Run:

```powershell
pytest tests/test_main_unit.py -q -k "shortcut or clear_context"
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

```powershell
git add main.py tests/test_main_unit.py
git commit -m "feat: mark reader quiet window from keyboard handlers"
```

## Task 3: Add Background UI Quiet Guard

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_unit.py`

- [ ] **Step 1: Write failing tests that background events do not mutate visible lists during quiet**

Add this test to `tests/test_main_unit.py`:

```python
def test_background_codex_event_defers_answer_list_mutation_during_quiet(frame, monkeypatch):
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.view_mode = "active"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {
            "question": "q",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": 1.0,
            "request_status": "pending",
            "codex_turn_id": "turn-1",
        }
    ]
    frame._current_chat_state = {
        "id": "chat-active",
        "turns": frame.active_session_turns,
        "execution_steps": [],
        "codex_turn_id": "turn-1",
    }
    frame._navigation_quiet_until = time.monotonic() + 3.0
    monkeypatch.setattr(frame, "_update_active_answer_row", lambda *_args, **_kwargs: pytest.fail("background answer row update must be deferred during quiet"))
    monkeypatch.setattr(frame, "_refresh_answer_list_preserving_selection", lambda *_args, **_kwargs: pytest.fail("background answer list refresh must be deferred during quiet"))

    event = main.CodexEvent(type="subagent_result", turn_id="turn-1", text="background answer")
    frame._on_codex_event_for_chat("chat-active", event)

    assert frame.active_session_turns[0]["answer_md"] == "background answer"
    assert frame._background_answer_list_dirty is True
```

- [ ] **Step 2: Run test to verify failure**

Run:

```powershell
pytest tests/test_main_unit.py::test_background_codex_event_defers_answer_list_mutation_during_quiet -q
```

Expected: fail because `_update_active_answer_row` is called or `_background_answer_list_dirty` does not exist.

- [ ] **Step 3: Add background UI guard helpers**

In `ChatFrame.__init__`, add:

```python
self._background_ui_update_depth = 0
self._background_answer_list_dirty = False
self._background_history_dirty_ids = set()
self._background_context_usage_dirty_ids = set()
```

Add methods:

```python
def _in_background_ui_update(self) -> bool:
    return int(getattr(self, "_background_ui_update_depth", 0) or 0) > 0


def _background_ui_mutations_blocked(self) -> bool:
    return self._in_background_ui_update() and self._navigation_quiet_active()


def _mark_background_answer_list_dirty(self) -> None:
    self._background_answer_list_dirty = True
    counts = getattr(self, "_deferred_background_ui_counts", {})
    counts["answer"] = int(counts.get("answer", 0)) + 1
    self._deferred_background_ui_counts = counts
```

In `_drain_codex_ui_events`, wrap the batch processing:

```python
self._background_ui_update_depth += 1
try:
    for queued_chat_id, queued_event in batch:
        self._on_codex_event_for_chat(queued_chat_id, queued_event)
finally:
    self._background_ui_update_depth = max(0, self._background_ui_update_depth - 1)
    self._flush_deferred_execution_list_updates()
    self._start_execution_step_persist_worker()
```

In `_on_codex_event_for_chat`, before any background-triggered answer list mutation, guard:

```python
if self._background_ui_mutations_blocked():
    self._mark_background_answer_list_dirty()
else:
    self._update_active_answer_row(target_idx)
```

Apply the same pattern where `_refresh_answer_list_preserving_selection` is called from Codex event handling.

- [ ] **Step 4: Run test**

Run:

```powershell
pytest tests/test_main_unit.py::test_background_codex_event_defers_answer_list_mutation_during_quiet -q
```

Expected: `1 passed`.

- [ ] **Step 5: Commit**

```powershell
git add main.py tests/test_main_unit.py
git commit -m "feat: defer background answer UI during reader quiet"
```

## Task 4: Defer Execution List Tail Appends During Quiet

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_unit.py`

- [ ] **Step 1: Write failing tests for execution append deferral and flush order**

Add tests:

```python
def test_background_execution_step_defers_list_append_during_quiet(frame, monkeypatch):
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.view_mode = "active"
    frame._current_chat_state = {"id": "chat-active", "turns": [], "execution_steps": []}
    frame._navigation_quiet_until = time.monotonic() + 3.0
    frame._background_ui_update_depth = 1
    frame._apply_detail_panel_mode("execution", refresh_execution=True)
    monkeypatch.setattr(frame.execution_list_model, "append", lambda *_args, **_kwargs: pytest.fail("execution append must be deferred during quiet"))

    assert frame._append_execution_entry_to_chat(
        "chat-active",
        {"turn_idx": 0, "display_kind": "commentary", "list_text": "step 1", "detail_text": "step 1"},
        save_state=False,
    )

    assert frame._current_chat_state["execution_steps"][0]["list_text"] == "step 1"
    assert frame._pending_execution_tail_appends["chat-active"]


def test_pending_execution_steps_append_after_quiet_in_order(frame, monkeypatch):
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.view_mode = "active"
    frame._current_chat_state = {"id": "chat-active", "turns": [], "detail_panel_mode": "execution", "execution_steps": []}
    frame._apply_detail_panel_mode("execution", refresh_execution=True)
    frame._navigation_quiet_until = 0.0
    frame._pending_execution_tail_appends = {
        "chat-active": [
            (0, {"turn_idx": 0, "display_kind": "commentary", "list_text": "step 1", "detail_text": "step 1"}),
            (1, {"turn_idx": 0, "display_kind": "commentary", "list_text": "step 2", "detail_text": "step 2"}),
        ]
    }
    appended = []
    original_append = frame.execution_list_model.append

    def record_append(row_id, row_text):
        appended.append(row_text)
        return original_append(row_id, row_text)

    monkeypatch.setattr(frame.execution_list_model, "append", record_append)

    frame._flush_pending_background_ui_updates()

    assert appended == ["step 1", "step 2"]
    assert frame.execution_list.GetString(frame.execution_list.GetCount() - 1) == "step 2"
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
pytest tests/test_main_unit.py::test_background_execution_step_defers_list_append_during_quiet tests/test_main_unit.py::test_pending_execution_steps_append_after_quiet_in_order -q
```

Expected: fail because pending append storage and flush method do not exist.

- [ ] **Step 3: Implement pending execution append storage**

In `ChatFrame.__init__`, add:

```python
self._pending_execution_tail_appends = {}
```

In `_append_visible_execution_entry`, at the top after visibility checks, add:

```python
chat_id = str((target_chat or {}).get("id") or self.active_chat_id or self.current_chat_id or "").strip()
if self._background_ui_mutations_blocked():
    pending = self._pending_execution_tail_appends.setdefault(chat_id, [])
    pending.append((step_idx, step))
    counts = getattr(self, "_deferred_background_ui_counts", {})
    counts["execution"] = int(counts.get("execution", 0)) + 1
    self._deferred_background_ui_counts = counts
    return True
```

Add a flush helper:

```python
def _flush_pending_background_ui_updates(self) -> None:
    if self._navigation_quiet_active():
        return
    self._flush_pending_execution_tail_appends()
    if self._background_answer_list_dirty:
        self._background_answer_list_dirty = False
        if self.view_mode == "active":
            self._refresh_answer_list_preserving_selection(refresh_execution=self._detail_panel_mode() != "execution")


def _flush_pending_execution_tail_appends(self) -> None:
    chat_id = self._visible_execution_chat_id()
    if not chat_id:
        return
    pending = self._pending_execution_tail_appends.get(chat_id) or []
    if not pending:
        return
    if not self._execution_list_visible_for_updates():
        self._mark_execution_list_dirty()
        return
    self._pending_execution_tail_appends[chat_id] = []
    for step_idx, step in sorted(pending, key=lambda item: item[0]):
        chat = self._chat_state_for_execution_steps(chat_id)
        if isinstance(chat, dict):
            self._append_visible_execution_entry(chat, step_idx, step)
```

In `_drain_codex_ui_events`, after quiet ends and before deciding no more work:

```python
if not self._navigation_quiet_active():
    self._flush_pending_background_ui_updates()
```

Task 5 adds the timer-driven flush used when no more Codex events arrive.

- [ ] **Step 4: Run tests**

Run:

```powershell
pytest tests/test_main_unit.py::test_background_execution_step_defers_list_append_during_quiet tests/test_main_unit.py::test_pending_execution_steps_append_after_quiet_in_order -q
```

Expected: `2 passed`.

- [ ] **Step 5: Run execution list regression tests**

Run:

```powershell
pytest tests/test_main_unit.py -q -k "execution_list or execution_step"
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

```powershell
git add main.py tests/test_main_unit.py
git commit -m "feat: defer execution tail appends during reader quiet"
```

## Task 5: Add Quiet Expiration Timer

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_unit.py`

- [ ] **Step 1: Write failing test that pending UI flushes after 3 seconds without new keys**

Add:

```python
def test_quiet_window_schedules_flush_after_expiration(frame, monkeypatch):
    scheduled = []
    monkeypatch.setattr(main.time, "monotonic", lambda: 10.0)
    monkeypatch.setattr(frame, "_call_later_if_alive", lambda delay, fn, *args: scheduled.append((delay, fn.__name__)) or object())

    frame._touch_navigation_quiet_window(_QuietKeyEvent(main.wx.WXK_DOWN))
    frame._schedule_navigation_quiet_flush()

    assert scheduled == [(3000, "_flush_pending_background_ui_updates")]
```

- [ ] **Step 2: Run test to verify failure**

Run:

```powershell
pytest tests/test_main_unit.py::test_quiet_window_schedules_flush_after_expiration -q
```

Expected: fail because `_schedule_navigation_quiet_flush` does not exist.

- [ ] **Step 3: Implement quiet expiration scheduling**

In `ChatFrame.__init__`, add:

```python
self._navigation_quiet_flush_timer = None
```

Add:

```python
def _schedule_navigation_quiet_flush(self) -> None:
    remaining = max(0.0, float(getattr(self, "_navigation_quiet_until", 0.0) or 0.0) - time.monotonic())
    delay_ms = int(round(remaining * 1000))
    self._navigation_quiet_flush_timer = self._call_later_if_alive(delay_ms, self._flush_pending_background_ui_updates)
```

At the end of `_touch_navigation_quiet_window`, call:

```python
self._schedule_navigation_quiet_flush()
```

At the start of `_flush_pending_background_ui_updates`, add:

```python
if self._navigation_quiet_active():
    self._schedule_navigation_quiet_flush()
    return
```

- [ ] **Step 4: Run test**

Run:

```powershell
pytest tests/test_main_unit.py::test_quiet_window_schedules_flush_after_expiration -q
```

Expected: `1 passed`.

- [ ] **Step 5: Commit**

```powershell
git add main.py tests/test_main_unit.py
git commit -m "feat: flush deferred UI after reader quiet expires"
```

## Task 6: UI Automation For No Background Mutations During Quiet

**Files:**
- Modify: `tests/test_history_ui_automation.py`

- [ ] **Step 1: Add UI automation test**

Add:

```python
def test_ui_automation_background_events_do_not_mutate_lists_during_reader_quiet(frame, wx_app, monkeypatch):
    frame.Show()
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {
            "question": "q",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": 1.0,
            "request_status": "pending",
            "codex_turn_id": "turn-1",
        }
    ]
    frame._current_chat_state = {
        "id": "chat-active",
        "turns": frame.active_session_turns,
        "execution_steps": [],
        "codex_turn_id": "turn-1",
    }
    frame._render_answer_list()
    frame.answer_list.SetFocusFromKbd()
    wx_app.Yield()

    frame._touch_navigation_quiet_window(_AltAEvent(frame.answer_list))
    forbidden = []
    monkeypatch.setattr(frame.answer_list, "Refresh", lambda *args, **kwargs: forbidden.append("answer.Refresh"))
    monkeypatch.setattr(frame.answer_list, "SetSelection", lambda *args, **kwargs: forbidden.append("answer.SetSelection"))
    monkeypatch.setattr(frame.execution_list, "Refresh", lambda *args, **kwargs: forbidden.append("execution.Refresh"))
    monkeypatch.setattr(frame.execution_list, "Append", lambda *args, **kwargs: forbidden.append("execution.Append"))

    frame._background_ui_update_depth = 1
    frame._on_codex_event_for_chat(
        "chat-active",
        main.CodexEvent(type="subagent_result", turn_id="turn-1", text="background answer"),
    )
    frame._on_codex_event_for_chat(
        "chat-active",
        main.CodexEvent(type="item_started", turn_id="turn-1", status="commandExecution", data={"title": "run tests"}),
    )

    assert forbidden == []
    assert frame.answer_list.HasFocus()
```

- [ ] **Step 2: Run UI automation test**

Run:

```powershell
pytest tests/test_history_ui_automation.py::test_ui_automation_background_events_do_not_mutate_lists_during_reader_quiet -q
```

Expected after previous tasks: `1 passed`.

- [ ] **Step 3: Commit**

```powershell
git add tests/test_history_ui_automation.py
git commit -m "test: cover reader quiet background UI suppression"
```

## Task 7: Regression And Stress Verification

**Files:**
- Modify only if tests reveal bugs: `main.py`, `tests/test_main_unit.py`, `tests/test_history_ui_automation.py`

- [ ] **Step 1: Run focused regression suite**

Run:

```powershell
pytest tests/test_main_unit.py -q -k "clear_context or execution_list or codex_ui_event or shortcut or focus"
```

Expected: all selected tests pass.

- [ ] **Step 2: Run UI automation suite for history and responsiveness**

Run:

```powershell
pytest tests/test_history_ui_automation.py tests/test_codex_ui_responsiveness_automation.py -q
```

Expected: all tests pass. If any fail due to the new quiet window, fix only the quiet-window behavior or the test expectation directly related to the new spec.

- [ ] **Step 3: Run simulator E2E clear-context regression**

Make sure an Android emulator is available:

```powershell
adb devices
```

Then run:

```powershell
$env:NATS_CLEAR_CONTEXT_E2E_DEVICE='emulator-5556'
pytest tests/test_mobile_desktop_clear_context_e2e.py::test_mobile_emulator_clear_context_clears_desktop_chat_frame -q
```

Expected: `1 passed`. If `emulator-5556` is unavailable, use another `emulator-*` device with enough `/data` space and set `NATS_CLEAR_CONTEXT_E2E_DEVICE` accordingly.

- [ ] **Step 4: Review diff**

Run:

```powershell
git diff -- main.py tests/test_main_unit.py tests/test_history_ui_automation.py
```

Expected: changes are limited to quiet-window state, scheduler/deferral logic, and tests.

- [ ] **Step 5: Commit final fixes if needed**

If Task 7 required code/test fixes:

```powershell
git add main.py tests/test_main_unit.py tests/test_history_ui_automation.py
git commit -m "fix: stabilize reader quiet regressions"
```

If Task 7 made no changes, do not create an empty commit.

## Self-Review Checklist

- Spec requirement "3 seconds after trigger keys" is covered by Tasks 1, 2, and 5.
- Spec requirement "user command still updates immediately" is covered by Task 2.
- Spec requirement "background events do not mutate wx controls during quiet" is covered by Tasks 3 and 6.
- Spec requirement "execution steps append to tail after quiet" is covered by Task 4.
- Spec requirement "do not redesign execution-process page" is preserved by using the existing `execution_list_model.append` path.
- Spec requirement "future worker process" is documented in the spec but intentionally excluded from this first implementation plan.
