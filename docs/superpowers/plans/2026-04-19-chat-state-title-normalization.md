# Chat State And Title Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce chat-state regression risk by funneling chat/view transitions through shared helpers and normalizing legacy default titles so first-question renaming works consistently.

**Architecture:** Keep the current external behavior, but add small internal helpers for entering active view, entering history view, refreshing chat UI, and resolving default title semantics. Reuse those helpers from existing entry points instead of letting each path write shared state directly.

**Tech Stack:** Python, wxPython, pytest

---

### Task 1: Lock Legacy Default Title Semantics

**Files:**
- Modify: `tests/test_main_unit.py`
- Modify: `main.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_next_default_chat_title_treats_legacy_placeholder_as_default(frame):
    frame.archived_chats = [
        {"id": "chat-a", "title": "新聊天", "turns": [], "created_at": 1.0, "updated_at": 1.0},
        {"id": "chat-b", "title": "心聊天1", "turns": [], "created_at": 2.0, "updated_at": 2.0},
    ]

    assert frame._next_default_chat_title() == "心聊天2"


def test_refresh_history_normalizes_legacy_placeholder_title(frame):
    frame.archived_chats = [
        {"id": "chat-a", "title": "新聊天", "turns": [], "created_at": 1.0, "updated_at": 1.0},
    ]

    frame._refresh_history()

    assert list(frame.history_list.GetStrings()) == ["心聊天"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest -q tests/test_main_unit.py -k "legacy_placeholder"`  
Expected: FAIL because legacy placeholders are not normalized everywhere.

- [ ] **Step 3: Write minimal implementation**

```python
@staticmethod
def _normalize_default_chat_title(title: str) -> str:
    ...

def _resolved_chat_title(self, chat: dict | None, fallback: str | None = None) -> str:
    ...
```

Update `_next_default_chat_title()`, `_current_history_title()`, `_refresh_history()`, `_remote_chat_snapshot()`, and `_current_chat_snapshot()` to use the normalized helpers.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest -q tests/test_main_unit.py -k "legacy_placeholder"`  
Expected: PASS

### Task 2: Funnel View-State Changes Through Shared Helpers

**Files:**
- Modify: `tests/test_main_unit.py`
- Modify: `main.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_enter_history_view_updates_view_state_and_focus(frame):
    frame.archived_chats = [{"id": "hist-1", "title": "聊天1", "turns": [], "created_at": 1.0, "updated_at": 1.0}]

    assert frame._enter_history_view("hist-1") is True
    assert frame.view_mode == "history"
    assert frame.view_history_id == "hist-1"


def test_enter_active_view_clears_history_view_state(frame):
    frame.view_mode = "history"
    frame.view_history_id = "hist-1"

    frame._enter_active_view()

    assert frame.view_mode == "active"
    assert frame.view_history_id is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest -q tests/test_main_unit.py -k "enter_history_view or enter_active_view"`  
Expected: FAIL because helpers do not exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
def _refresh_chat_views(...):
    ...

def _enter_history_view(self, chat_id: str, *, focus_answer_list: bool = True) -> bool:
    ...

def _enter_active_view(self, *, focus_answer_list: bool = False, keep_history_id=None) -> None:
    ...
```

Wire `_activate_selected_history()`, `_submit_question()`, and `_switch_current_chat()` to use the new helpers.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest -q tests/test_main_unit.py -k "enter_history_view or enter_active_view"`  
Expected: PASS

### Task 3: Regression Verification

**Files:**
- Modify: `tests/test_integration_context.py`
- Modify: `tests/test_remote_api_integration.py`

- [ ] **Step 1: Add regression coverage**

```python
def test_current_chat_snapshot_normalizes_legacy_default_title(...):
    ...

def test_history_activation_uses_shared_view_helpers(...):
    ...
```

- [ ] **Step 2: Run focused regression suites**

Run: `pytest -q tests/test_main_unit.py -k "legacy_placeholder or enter_history_view or enter_active_view"`  
Expected: PASS

Run: `pytest -q tests/test_integration_context.py -k "history or ctrl_history_navigation"`  
Expected: PASS

Run: `pytest -q tests/test_remote_api_integration.py -k "first_message or snapshot"`  
Expected: PASS
