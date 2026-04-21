# Codex Execution List Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-chat execution-process list for Codex CLI events, persist it with chat state, and let users press `F1` to toggle the right-side detail area between answers and execution steps.

**Architecture:** Keep the current answer list intact and add a second `wx.ListBox` for execution steps in the same detail area. Store `detail_panel_mode` and `execution_steps` on each chat, map bounded Codex events into readable step rows, and refresh only the currently visible detail list without stealing focus.

**Tech Stack:** Python, wxPython, pytest

---

## File Map

- Modify: `main.py`
  - Add chat-level defaults for `detail_panel_mode` and `execution_steps`
  - Add `execution_list`, execution list metadata, detail title switching, and `F1` toggle handling
  - Add execution-step render helpers and Codex event-to-step mapping
  - Persist and restore the new chat fields
- Modify: `tests/test_main_unit.py`
  - Add focused unit tests for chat defaults, `F1` mode switching, execution list rendering, and Codex event recording

### Task 1: Lock Chat-State Defaults With Tests

**Files:**
- Modify: `tests/test_main_unit.py`
- Modify: `main.py`

- [ ] **Step 1: Write the failing state-default tests**

```python
def test_new_chat_defaults_to_answer_detail_mode(frame):
    frame._on_new_chat_clicked(None)

    assert frame._current_chat_state["detail_panel_mode"] == "answers"
    assert frame._current_chat_state["execution_steps"] == []


def test_normalize_archived_chat_adds_execution_list_defaults(frame):
    chat = {"id": "c1", "title": "旧聊天", "turns": [], "created_at": 1.0, "updated_at": 1.0}

    changed = frame._normalize_archived_chat(chat)

    assert changed is True
    assert chat["detail_panel_mode"] == "answers"
    assert chat["execution_steps"] == []
```

- [ ] **Step 2: Run the new tests to verify they fail for the expected reason**

Run: `pytest tests/test_main_unit.py -k "detail_panel_mode or execution_list_defaults" -v`

Expected: FAIL because `detail_panel_mode` and `execution_steps` are not created yet.

- [ ] **Step 3: Add minimal chat-state defaults in production code**

```python
def _normalize_archived_chat(self, chat: dict) -> bool:
    changed = False
    if str(chat.get("detail_panel_mode") or "").strip() not in {"answers", "execution"}:
        chat["detail_panel_mode"] = "answers"
        changed = True
    if not isinstance(chat.get("execution_steps"), list):
        chat["execution_steps"] = []
        changed = True
    ...


self._current_chat_state = {
    "id": "",
    "title": self._next_default_chat_title(),
    ...
    "detail_panel_mode": "answers",
    "execution_steps": [],
}
```

- [ ] **Step 4: Run the targeted tests again**

Run: `pytest tests/test_main_unit.py -k "detail_panel_mode or execution_list_defaults" -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_main_unit.py main.py
git commit -m "test: add chat execution list defaults"
```

### Task 2: Add the Toggleable Execution List UI With TDD

**Files:**
- Modify: `tests/test_main_unit.py`
- Modify: `main.py`

- [ ] **Step 1: Write the failing UI-mode tests**

```python
def test_f1_toggles_active_chat_detail_mode(frame):
    frame._current_chat_state["detail_panel_mode"] = "answers"

    class E:
        def GetKeyCode(self):
            return wx.WXK_F1
        def AltDown(self):
            return False
        def ControlDown(self):
            return False
        def Skip(self):
            raise AssertionError("should not skip")

    frame._on_char_hook(E())

    assert frame._current_chat_state["detail_panel_mode"] == "execution"


def test_apply_detail_panel_mode_updates_label_and_visible_list(frame):
    frame._current_chat_state["detail_panel_mode"] = "execution"

    frame._apply_chat_detail_list_mode()

    assert frame.detail_list_title.GetLabel() == "执行过程："
    assert frame.execution_list.IsShown() is True
    assert frame.answer_list.IsShown() is False
```

- [ ] **Step 2: Run the UI-mode tests to verify RED**

Run: `pytest tests/test_main_unit.py -k "f1_toggles_active_chat_detail_mode or apply_detail_panel_mode_updates_label" -v`

Expected: FAIL because the new label, list, and toggle helpers do not exist yet.

- [ ] **Step 3: Implement the smallest UI slice**

```python
self.detail_list_title = wx.StaticText(panel, label="回答：")
self.answer_list = wx.ListBox(panel, style=wx.LB_SINGLE)
self.execution_list = wx.ListBox(panel, style=wx.LB_SINGLE)
right.Add(self.detail_list_title, 0, wx.LEFT, 10)
right.Add(self.answer_list, 1, wx.EXPAND | wx.ALL, 10)
right.Add(self.execution_list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)


def _detail_panel_mode_for_chat(self, chat: dict | None = None) -> str:
    target = chat if isinstance(chat, dict) else self._current_chat_state
    mode = str((target or {}).get("detail_panel_mode") or "").strip()
    return mode if mode in {"answers", "execution"} else "answers"


def _apply_chat_detail_list_mode(self) -> None:
    mode = self._detail_panel_mode_for_chat()
    self.detail_list_title.SetLabel("执行过程：" if mode == "execution" else "回答：")
    self.answer_list.Show(mode != "execution")
    self.execution_list.Show(mode == "execution")
    self.chat_root_panel.Layout()


def _toggle_chat_detail_list_mode(self) -> None:
    current = self._detail_panel_mode_for_chat()
    self._current_chat_state["detail_panel_mode"] = "execution" if current == "answers" else "answers"
    self._apply_chat_detail_list_mode()
    self._save_state()
```

And in `_on_char_hook`:

```python
if key == wx.WXK_F1:
    self._toggle_chat_detail_list_mode()
    return
```

- [ ] **Step 4: Run the targeted UI tests**

Run: `pytest tests/test_main_unit.py -k "f1_toggles_active_chat_detail_mode or apply_detail_panel_mode_updates_label" -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_main_unit.py main.py
git commit -m "feat: toggle chat detail view with F1"
```

### Task 3: Render and Persist Execution Steps With TDD

**Files:**
- Modify: `tests/test_main_unit.py`
- Modify: `main.py`

- [ ] **Step 1: Write the failing rendering and persistence tests**

```python
def test_render_execution_list_shows_saved_steps(frame):
    frame._current_chat_state["execution_steps"] = [
        {"text": "开始处理本轮请求", "type": "turn_started", "created_at": 1.0},
        {"text": "已生成代码变更", "type": "diff_updated", "created_at": 2.0},
    ]

    frame._render_execution_list()

    assert list(frame.execution_list.GetItems()) == ["开始处理本轮请求", "已生成代码变更"]


def test_save_and_load_state_preserves_execution_steps(frame, tmp_path):
    frame.state_path = tmp_path / "state.json"
    frame._current_chat_state["detail_panel_mode"] = "execution"
    frame._current_chat_state["execution_steps"] = [{"text": "等待用户输入", "type": "server_request", "created_at": 1.0}]
    frame.archived_chats = [dict(frame._current_chat_state)]

    frame._save_state()

    restored = type(frame)()
    restored.state_path = frame.state_path
    restored._load_state()

    assert restored.archived_chats[0]["detail_panel_mode"] == "execution"
    assert restored.archived_chats[0]["execution_steps"][0]["text"] == "等待用户输入"
```

- [ ] **Step 2: Run the new tests and verify RED**

Run: `pytest tests/test_main_unit.py -k "render_execution_list_shows_saved_steps or preserves_execution_steps" -v`

Expected: FAIL because execution-list rendering and state serialization are incomplete.

- [ ] **Step 3: Implement rendering and persistence**

```python
self.execution_meta = []


def _get_view_execution_steps(self) -> list[dict]:
    if self.view_mode == "history":
        chat = self._find_archived_chat(self.view_history_id)
        return list(chat.get("execution_steps") or []) if isinstance(chat, dict) else []
    return list(self._current_chat_state.get("execution_steps") or [])


def _render_execution_list(self) -> None:
    self.execution_list.Clear()
    self.execution_meta = []
    steps = self._get_view_execution_steps()
    if not steps:
        self.execution_list.Append("暂无执行过程")
        self.execution_meta.append(("info", -1, "暂无执行过程"))
        self._request_listbox_repaint(self.execution_list)
        return
    for idx, step in enumerate(steps):
        text = str((step or {}).get("text") or "").strip() or "执行步骤更新"
        self.execution_list.Append(text)
        self.execution_meta.append(("step", idx, text))
    self._request_listbox_repaint(self.execution_list)
```

Add to `_save_state()`:

```python
"active_chat": self._current_chat_state,
```

And make sure `_normalize_archived_chat()` plus active-chat setup keep `detail_panel_mode` and `execution_steps` intact.

- [ ] **Step 4: Run the targeted tests**

Run: `pytest tests/test_main_unit.py -k "render_execution_list_shows_saved_steps or preserves_execution_steps" -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_main_unit.py main.py
git commit -m "feat: render and persist codex execution steps"
```

### Task 4: Record Codex Events Into Execution Steps With TDD

**Files:**
- Modify: `tests/test_main_unit.py`
- Modify: `main.py`

- [ ] **Step 1: Write the failing event-recording tests**

```python
def test_codex_plan_update_appends_execution_step(frame):
    frame._current_chat_state["execution_steps"] = []

    frame._on_codex_event_for_chat("chat-1", CodexEvent(type="plan_updated", text="准备修改 main.py"))

    assert frame._current_chat_state["execution_steps"][-1]["text"] == "计划更新：准备修改 main.py"


def test_codex_agent_message_delta_does_not_append_execution_step(frame):
    frame._current_chat_state["execution_steps"] = []

    frame._on_codex_event_for_chat("chat-1", CodexEvent(type="agent_message_delta", text="增量文本"))

    assert frame._current_chat_state["execution_steps"] == []


def test_execution_step_append_does_not_focus_execution_list(frame, monkeypatch):
    frame._current_chat_state["execution_steps"] = []
    focused = {"count": 0}
    monkeypatch.setattr(frame.execution_list, "SetFocus", lambda: focused.__setitem__("count", focused["count"] + 1))

    frame._append_execution_step_for_chat("chat-1", {"text": "开始处理本轮请求"})

    assert focused["count"] == 0
```

- [ ] **Step 2: Run the new tests to verify RED**

Run: `pytest tests/test_main_unit.py -k "plan_update_appends_execution_step or agent_message_delta_does_not_append_execution_step or does_not_focus_execution_list" -v`

Expected: FAIL because the execution-step append helpers and event mapping do not exist yet.

- [ ] **Step 3: Implement the minimal event-mapping layer**

```python
def _append_execution_step_for_chat(self, chat_id: str, step: dict) -> None:
    target_chat = self._current_chat_state if chat_id in {self.active_chat_id, self.current_chat_id, "", None} else self._find_archived_chat(chat_id)
    if not isinstance(target_chat, dict):
        return
    steps = target_chat.get("execution_steps")
    if not isinstance(steps, list):
        steps = []
        target_chat["execution_steps"] = steps
    entry = {
        "type": str(step.get("type") or "").strip(),
        "text": str(step.get("text") or "").strip() or "执行步骤更新",
        "phase": str(step.get("phase") or "").strip(),
        "status": str(step.get("status") or "").strip(),
        "thread_id": str(step.get("thread_id") or "").strip(),
        "turn_id": str(step.get("turn_id") or "").strip(),
        "item_id": str(step.get("item_id") or "").strip(),
        "created_at": float(step.get("created_at") or time.time()),
    }
    steps.append(entry)
    if target_chat is self._current_chat_state and self._detail_panel_mode_for_chat() == "execution":
        self._render_execution_list()
    self._save_state()


def _codex_event_execution_step(self, event: CodexEvent) -> dict | None:
    if event.type == "turn_started":
        return {"type": event.type, "text": "开始处理本轮请求"}
    if event.type == "plan_updated":
        return {"type": event.type, "text": f"计划更新：{str(event.text or '').strip() or '执行步骤更新'}"}
    if event.type == "diff_updated":
        return {"type": event.type, "text": "已生成代码变更"}
    if event.type == "server_request":
        return {"type": event.type, "text": "等待用户输入"}
    if event.type == "turn_completed":
        return {"type": event.type, "text": "本轮处理结束"}
    if event.type == "stderr":
        return {"type": event.type, "text": f"错误输出：{str(event.text or '').strip() or '执行步骤更新'}"}
    if event.type == "item_completed" and event.phase == "final_answer":
        return {"type": event.type, "text": "已生成最终回答", "phase": event.phase, "status": event.status}
    if event.type == "item_started":
        detail = str(event.status or event.phase or "执行步骤").strip()
        return {"type": event.type, "text": f"开始执行：{detail}", "phase": event.phase, "status": event.status}
    if event.type == "item_completed":
        detail = str(event.status or event.phase or "执行步骤").strip()
        return {"type": event.type, "text": f"完成执行：{detail}", "phase": event.phase, "status": event.status}
    return None
```

Call it from `_on_codex_event_for_chat()` before the final-answer/answer-list updates:

```python
step = self._codex_event_execution_step(event)
if step is not None:
    self._append_execution_step_for_chat(chat_id, step)
```

- [ ] **Step 4: Run the targeted tests**

Run: `pytest tests/test_main_unit.py -k "plan_update_appends_execution_step or agent_message_delta_does_not_append_execution_step or does_not_focus_execution_list" -v`

Expected: PASS

- [ ] **Step 5: Run a broader regression slice**

Run: `pytest tests/test_main_unit.py -k "codex or answer_list or on_char_hook" -v`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add tests/test_main_unit.py main.py
git commit -m "feat: record codex execution steps in chat state"
```

### Task 5: Final Verification

**Files:**
- Modify: `main.py`
- Modify: `tests/test_main_unit.py`

- [ ] **Step 1: Run the focused suite for this feature**

Run: `pytest tests/test_main_unit.py -k "execution or detail_panel_mode or codex" -v`

Expected: PASS

- [ ] **Step 2: Run the full `test_main_unit` module**

Run: `pytest tests/test_main_unit.py -v`

Expected: PASS

- [ ] **Step 3: Inspect the git diff for unintended churn**

Run: `git diff -- main.py tests/test_main_unit.py docs/superpowers/plans/2026-04-20-codex-execution-list.md`

Expected: Only the execution-list feature, tests, and plan file changes appear.

- [ ] **Step 4: Final commit**

```bash
git add main.py tests/test_main_unit.py docs/superpowers/plans/2026-04-20-codex-execution-list.md
git commit -m "feat: add codex execution list view"
```

## Self-Review

- Spec coverage:
  - `F1` toggle in the same detail area is covered by Task 2.
  - Per-chat persistence is covered by Tasks 1 and 3.
  - Real-time step appends and no-focus behavior are covered by Task 4.
  - Existing answer behavior remains under regression coverage in Tasks 4 and 5.
- Placeholder scan:
  - No `TODO`, `TBD`, or “handle appropriately” placeholders remain.
- Type consistency:
  - The plan consistently uses `detail_panel_mode`, `execution_steps`, `execution_list`, `_apply_chat_detail_list_mode`, `_render_execution_list`, `_append_execution_step_for_chat`, and `_codex_event_execution_step`.
