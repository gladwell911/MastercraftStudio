# Codex Answer English Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Codex-only UI toggle that filters file paths, file names, and test method names from answers across the main list, detail pages, history reloads, and copy/export flows.

**Architecture:** Keep original answer content unchanged and add one shared filter helper in `main.py`. Persist a single toggle flag in app state, expose it through the tools menu, and route every rendered/exported Codex answer through the same helper when the flag is enabled.

**Tech Stack:** Python, wxPython, pytest

---

### Task 1: Add failing tests for the toggle and filter behavior

**Files:**
- Modify: `tests/test_main_unit.py`

- [ ] Add tests that verify:
  - the menu label changes with the toggle state
  - Codex answers are filtered in rendered list output
  - non-Codex answers remain unchanged
  - detail/export helper paths reuse the same filtered output

### Task 2: Implement persisted toggle state and menu wiring

**Files:**
- Modify: `main.py`

- [ ] Add a persisted boolean state field for the Codex answer filter.
- [ ] Add a tools menu item that toggles between enabled/disabled labels.
- [ ] Re-render the active answer view after the toggle changes.

### Task 3: Implement shared Codex-only filter helpers

**Files:**
- Modify: `main.py`

- [ ] Add a shared helper that filters file paths, file names, and test method names from Codex answer text.
- [ ] Add wrapper helpers for rendered/exported answer markdown/plain text so all output surfaces use the same decision logic.

### Task 4: Wire the filter into answer list, detail pages, and copy/export flows

**Files:**
- Modify: `main.py`

- [ ] Update answer list rendering to display filtered Codex answers when enabled.
- [ ] Update detail page generation to emit filtered Codex answers when enabled.
- [ ] Update copy/export paths to emit filtered Codex answers when enabled.

### Task 5: Verify behavior

**Files:**
- Modify: `tests/test_main_unit.py`
- Modify: `main.py`

- [ ] Run focused validation for the new helpers and UI state transitions.
- [ ] Confirm Codex-only filtering and non-Codex pass-through behavior.
