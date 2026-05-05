# Current Turn Lists and Focus Shortcuts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scope execution rows to the current question, collapse long answer lists behind a "更多" button, and add window-local focus shortcuts.

**Architecture:** Keep the feature inside the existing `ChatFrame` wxPython UI. Add small state fields for answer row limits, render answer rows through an intermediate row list, tag new execution entries with `turn_idx`, and centralize Alt-letter shortcuts in `_on_char_hook`.

**Tech Stack:** Python 3.11, wxPython, pytest.

---

### Task 1: Answer List Folding

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_unit.py`

- [ ] Add failing tests for default 100-row rendering, "更多" button visibility, and incremental expansion.
- [ ] Run the new tests and confirm they fail because no button or row limit exists.
- [ ] Add `ANSWER_LIST_DEFAULT_VISIBLE_ROWS = 100`, `ANSWER_LIST_EXPAND_ROWS = 100`, `self.answer_visible_row_limit`, and `self.answer_total_content_rows`.
- [ ] Add `answer_more_button` below the current model/header area and bind it to `_show_more_answer_rows`.
- [ ] Refactor `_render_answer_list` to build content rows first, render the newest `answer_visible_row_limit` rows, and show/hide the button based on hidden rows.
- [ ] Preserve current selection and active-answer row behavior for visible rows.
- [ ] Run the answer-list tests.

### Task 2: Current-Turn Execution Rows

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_unit.py`

- [ ] Add failing tests for active execution list filtering by `turn_idx` and for new submissions clearing the visible execution list.
- [ ] Run the tests and confirm they fail because active execution rows are not turn-scoped.
- [ ] Add `_reset_current_turn_execution_view` and call it on successful new question submission before the new turn runs.
- [ ] Tag new execution entries with the resolved `turn_idx` in `_on_codex_event_for_chat`.
- [ ] Filter active-chat `_current_execution_steps` to the active turn when step dictionaries contain `turn_idx`.
- [ ] Run the execution tests.

### Task 3: Window Focus Shortcuts

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_unit.py`

- [ ] Add failing tests that simulate Alt+F, Alt+D, Alt+G, and Alt+B through `_on_char_hook`.
- [ ] Run the tests and confirm focus helper behavior is missing.
- [ ] Add `_handle_window_focus_shortcut` and focus helpers for detail, input, history, and notes.
- [ ] Call the shortcut handler near the top of `_on_char_hook` after Alt menu arming rules.
- [ ] Run the shortcut tests.

### Task 4: Verification

**Files:**
- Test: `tests/test_main_unit.py`

- [ ] Run targeted pytest for the new and nearby tests.
- [ ] Run the broader main unit test file if targeted tests pass.
- [ ] Report exact verification commands and outcomes.
