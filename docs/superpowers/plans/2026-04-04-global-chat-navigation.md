# Global Chat Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `Ctrl+Left` and `Ctrl+Right` switch across the full ordered chat sequence instead of only jumping among archived chats.

**Architecture:** Reuse the existing history ordering that already represents the UI-visible chat order. Add a focused helper that returns the navigable chat id list, then update keyboard navigation to compute the adjacent id from that unified sequence. Cover the behavior with unit tests before touching implementation.

**Tech Stack:** Python, wxPython, pytest

---

### Task 1: Define unified chat navigation order

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_unit.py`

- [ ] **Step 1: Write the failing test**

```python
def test_adjacent_chat_id_uses_full_history_order(frame):
    frame.active_chat_id = "chat-b"
    frame.archived_chats = [
        {"id": "chat-a", "title": "聊天A", "turns": [], "created_at": 1.0, "updated_at": 1.0},
        {"id": "chat-c", "title": "聊天C", "turns": [], "created_at": 3.0, "updated_at": 3.0},
    ]
    frame._refresh_history()

    assert frame._adjacent_history_chat_id(-1) == "chat-c"
    assert frame._adjacent_history_chat_id(1) == "chat-c"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_main_unit.py::test_adjacent_chat_id_uses_full_history_order -v`
Expected: `FAIL` because the current implementation ignores the current chat and returns the wrong adjacent id.

- [ ] **Step 3: Write minimal implementation**

```python
def _ordered_navigable_chat_ids(self) -> list[str]:
    current_id = str(self.current_chat_id or "").strip()
    ordered = [str(chat_id or "").strip() for chat_id in self.history_ids if str(chat_id or "").strip()]
    if current_id and current_id not in ordered:
        ordered.insert(0, current_id)
    return ordered


def _adjacent_history_chat_id(self, direction: int) -> str:
    try:
        step = int(direction)
    except Exception:
        step = 0
    if step == 0:
        return ""
    current_id = str(self.current_chat_id or "").strip()
    ordered_chat_ids = self._ordered_navigable_chat_ids()
    if not current_id or len(ordered_chat_ids) < 2 or current_id not in ordered_chat_ids:
        return ""
    current_index = ordered_chat_ids.index(current_id)
    return ordered_chat_ids[(current_index + (-1 if step < 0 else 1)) % len(ordered_chat_ids)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_main_unit.py::test_adjacent_chat_id_uses_full_history_order -v`
Expected: `PASS`

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_main_unit.py docs/superpowers/plans/2026-04-04-global-chat-navigation.md
git commit -m "feat: navigate across all chats with ctrl arrows"
```

### Task 2: Protect keyboard shortcuts with navigation regression tests

**Files:**
- Modify: `tests/test_main_unit.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_char_hook_ctrl_left_wraps_from_current_chat_to_previous_visible_chat(frame):
    frame.active_chat_id = "chat-b"
    frame.archived_chats = [
        {"id": "chat-a", "title": "聊天A", "turns": [], "created_at": 1.0, "updated_at": 1.0},
        {"id": "chat-c", "title": "聊天C", "turns": [], "created_at": 3.0, "updated_at": 3.0},
    ]
    frame._refresh_history()
    seen = {}
    frame._switch_current_chat = lambda chat_id: seen.setdefault("chat_id", chat_id) or True

    class E:
        def GetKeyCode(self):
            return wx.WXK_LEFT
        def ControlDown(self):
            return True
        def AltDown(self):
            return False
        def Skip(self):
            raise AssertionError("should not skip")

    frame._on_char_hook(E())

    assert seen["chat_id"] == "chat-c"


def test_char_hook_ctrl_right_wraps_from_current_chat_to_next_visible_chat(frame):
    frame.active_chat_id = "chat-b"
    frame.archived_chats = [
        {"id": "chat-a", "title": "聊天A", "turns": [], "created_at": 1.0, "updated_at": 1.0},
        {"id": "chat-c", "title": "聊天C", "turns": [], "created_at": 3.0, "updated_at": 3.0},
    ]
    frame._refresh_history()
    seen = {}
    frame._switch_current_chat = lambda chat_id: seen.setdefault("chat_id", chat_id) or True

    class E:
        def GetKeyCode(self):
            return wx.WXK_RIGHT
        def ControlDown(self):
            return True
        def AltDown(self):
            return False
        def Skip(self):
            raise AssertionError("should not skip")

    frame._on_char_hook(E())

    assert seen["chat_id"] == "chat-c"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_main_unit.py::test_char_hook_ctrl_left_wraps_from_current_chat_to_previous_visible_chat tests/test_main_unit.py::test_char_hook_ctrl_right_wraps_from_current_chat_to_next_visible_chat -v`
Expected: `FAIL` because the existing navigation does not traverse the full unified sequence.

- [ ] **Step 3: Keep keyboard handlers using the fixed adjacent-id helper**

```python
if event.ControlDown() and not event.AltDown() and key in (wx.WXK_LEFT, wx.WXK_RIGHT):
    target_chat_id = self._adjacent_history_chat_id(-1 if key == wx.WXK_LEFT else 1)
    if target_chat_id and self._switch_current_chat(target_chat_id):
        return
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_main_unit.py::test_char_hook_ctrl_left_wraps_from_current_chat_to_previous_visible_chat tests/test_main_unit.py::test_char_hook_ctrl_right_wraps_from_current_chat_to_next_visible_chat -v`
Expected: `PASS`

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_main_unit.py docs/superpowers/plans/2026-04-04-global-chat-navigation.md
git commit -m "test: cover ctrl arrow chat navigation order"
```

### Task 3: Verify the targeted regression set

**Files:**
- Modify: `tests/test_main_unit.py`

- [ ] **Step 1: Run the focused navigation tests**

Run: `pytest tests/test_main_unit.py -k "ctrl_left or ctrl_right or history_navigation" -v`
Expected: `PASS`

- [ ] **Step 2: Run the broader chat-state regression tests most likely affected**

Run: `pytest tests/test_main_unit.py -k "switch_current_chat or archived_chat or history_ids" -v`
Expected: `PASS`

- [ ] **Step 3: Review for accidental behavior changes**

```python
assert frame.history_ids[0] == frame.current_chat_id
assert frame._adjacent_history_chat_id(0) == ""
```

- [ ] **Step 4: Save final state after green tests**

Run: `git diff -- main.py tests/test_main_unit.py docs/superpowers/plans/2026-04-04-global-chat-navigation.md`
Expected: Only the planned navigation and test changes appear.

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_main_unit.py docs/superpowers/plans/2026-04-04-global-chat-navigation.md
git commit -m "chore: verify global chat navigation change"
```
