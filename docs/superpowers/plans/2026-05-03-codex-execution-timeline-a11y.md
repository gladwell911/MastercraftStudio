# Codex Execution Timeline A11y Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `F1` execution timeline show near-CLI process messages with answer-list-style accessibility, while updating the visible list incrementally without disruptive full refreshes.

**Architecture:** Normalize Codex protocol events in `codex_client.py` into richer `CodexEvent` objects, then convert those events in `main.py` into persisted execution entries with separate `list_text` and `detail_text`. Update the execution list through a split model: rebuild only on view/chat switches, append only when a new execution entry is formed, and buffer `agent_message_delta` commentary before flushing a single timeline item.

**Tech Stack:** Python 3, wxPython, pytest

---

### Task 1: Extend `CodexEvent` And Normalize Item Payloads

**Files:**
- Modify: `codex_client.py`
- Test: `tests/test_codex_client_unit.py`

- [ ] **Step 1: Add richer display fields to `CodexEvent`**

```python
@dataclass
class CodexEvent:
    type: str
    thread_id: str = ""
    turn_id: str = ""
    item_id: str = ""

    text: str = ""
    raw_text: str = ""
    title: str = ""
    command: str = ""
    exit_code: int | None = None

    phase: str = ""
    status: str = ""
    subtype: str = ""
    display_kind: str = ""

    flags: list[str] = field(default_factory=list)
    request_id: str | int | None = None
    method: str = ""
    params: dict = field(default_factory=dict)
    data: dict = field(default_factory=dict)
    usage: dict = field(default_factory=dict)
```

- [ ] **Step 2: Add item parsing helpers in `codex_client.py`**

```python
def _first_non_empty(*values) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _item_title(item: dict) -> str:
    return _first_non_empty(item.get("title"), item.get("name"), item.get("label"))


def _item_command(item: dict) -> str:
    return _first_non_empty(item.get("command"), item.get("commandLine"), item.get("cmd"))


def _item_exit_code(item: dict) -> int | None:
    value = item.get("exitCode")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
```

- [ ] **Step 3: Add a helper that converts `item/started` and `item/completed` into a normalized `CodexEvent`**

```python
def _build_item_event(method: str, params: dict) -> CodexEvent:
    item = params.get("item") if isinstance(params.get("item"), dict) else {}
    item_type = str(item.get("type") or "").strip()
    title = _item_title(item)
    command = _item_command(item)
    raw_text = str(item.get("text") or "").strip()
    event_type = "item_completed" if method.endswith("completed") else "item_started"

    display_kind = "artifact"
    text = raw_text
    subtype = item_type
    if item_type == "commandExecution":
        display_kind = "command"
        text = command or raw_text or title
    elif item_type == "agentMessage":
        display_kind = "commentary"
        text = raw_text
    elif item_type == "fileChange":
        display_kind = "artifact"
        text = raw_text or "代码变更已更新"
    else:
        display_kind = "status"
        text = raw_text or title

    return CodexEvent(
        type=event_type,
        thread_id=str(params.get("threadId") or ""),
        turn_id=str(params.get("turnId") or ""),
        item_id=str(item.get("id") or ""),
        text=text,
        raw_text=raw_text,
        title=title,
        command=command,
        exit_code=_item_exit_code(item),
        phase=str(item.get("phase") or ""),
        status=item_type,
        subtype=subtype,
        display_kind=display_kind,
        data=item,
    )
```

- [ ] **Step 4: Route item events and other protocol events through the richer event fields**

```python
if method == "turn/plan/updated":
    self._emit_event(
        CodexEvent(
            type="plan_updated",
            thread_id=str(params.get("threadId") or ""),
            turn_id=str(params.get("turnId") or ""),
            text=str(params.get("explanation") or ""),
            raw_text=str(params.get("explanation") or ""),
            subtype="turnPlanUpdated",
            display_kind="plan",
            data=params,
        )
    )
    return

if method == "item/agentMessage/delta":
    delta = str(params.get("delta") or "")
    self._emit_event(
        CodexEvent(
            type="agent_message_delta",
            thread_id=str(params.get("threadId") or ""),
            turn_id=str(params.get("turnId") or ""),
            item_id=str(params.get("itemId") or ""),
            text=delta,
            raw_text=delta,
            subtype="agentMessageDelta",
            display_kind="commentary",
            data=params,
        )
    )
    return

if method in {"item/started", "item/completed"}:
    self._emit_event(_build_item_event(method, params))
    return
```

- [ ] **Step 5: Write focused normalization tests**

```python
def test_item_completed_command_execution_promotes_command_fields():
    events = []
    client = CodexAppServerClient(on_event=events.append)
    client._emit_protocol_event(
        "item/completed",
        {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "item": {
                "id": "item-1",
                "type": "commandExecution",
                "title": "运行测试",
                "command": "pytest tests/test_main_unit.py -k codex",
                "exitCode": 1,
            },
        },
        {},
    )

    event = events[0]
    assert event.display_kind == "command"
    assert event.command == "pytest tests/test_main_unit.py -k codex"
    assert event.title == "运行测试"
    assert event.exit_code == 1


def test_agent_message_delta_keeps_full_text():
    events = []
    client = CodexAppServerClient(on_event=events.append)
    client._emit_protocol_event(
        "item/agentMessage/delta",
        {"threadId": "thread-1", "turnId": "turn-1", "itemId": "msg-1", "delta": "发现当前逻辑仍在全量刷新"},
        {},
    )

    event = events[0]
    assert event.display_kind == "commentary"
    assert event.text == "发现当前逻辑仍在全量刷新"
    assert event.raw_text == "发现当前逻辑仍在全量刷新"
```

- [ ] **Step 6: Run the codex client unit tests**

Run: `pytest tests/test_codex_client_unit.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add codex_client.py tests/test_codex_client_unit.py
git commit -m "refactor: normalize codex execution events"
```

### Task 2: Add Execution Entry Plain/Detail Model In `main.py`

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_unit.py`

- [ ] **Step 1: Add `execution_meta` to frame state**

```python
self.answer_meta = []
self.execution_meta = []
```

- [ ] **Step 2: Add execution text builders that produce full detail and single-line list text**

```python
def _execution_detail_text_from_event(self, event: CodexEvent) -> str:
    if event.type == "turn_started":
        return "开始处理本轮请求"
    if event.type == "turn_completed":
        return "本轮处理结束"
    if event.type == "server_request":
        return str(event.text or event.raw_text or "等待用户输入").strip()
    if event.type == "diff_updated":
        return "代码变更已更新"
    return str(event.text or event.raw_text or event.command or event.title or "").strip()


def _execution_list_text_from_detail(self, detail: str, kind: str) -> str:
    text = " ".join(str(detail or "").replace("\r", "\n").split())
    if not text:
        text = "执行过程更新"
    prefix = ""
    if kind == "command":
        prefix = "[命令] "
    elif kind == "error":
        prefix = "[stderr] "
    elif kind == "plan":
        prefix = "[计划] "
    value = f"{prefix}{text}".strip()
    return value[:180] + "..." if len(value) > 180 else value
```

- [ ] **Step 3: Add a single function that converts normalized events into persisted execution entries**

```python
def _build_execution_entry(self, event: CodexEvent) -> dict | None:
    detail_text = self._execution_detail_text_from_event(event)
    if not detail_text:
        return None
    return {
        "event_type": event.type,
        "display_kind": str(event.display_kind or "").strip() or "status",
        "subtype": str(event.subtype or "").strip(),
        "list_text": self._execution_list_text_from_detail(detail_text, str(event.display_kind or "").strip()),
        "detail_text": detail_text,
        "raw_text": str(event.raw_text or ""),
        "title": str(event.title or ""),
        "command": str(event.command or ""),
        "exit_code": event.exit_code,
        "phase": str(event.phase or ""),
        "status": str(event.status or ""),
        "thread_id": str(event.thread_id or ""),
        "turn_id": str(event.turn_id or ""),
        "item_id": str(event.item_id or ""),
        "created_at": time.time(),
    }
```

- [ ] **Step 4: Add execution meta tuple builder and full rebuild renderer**

```python
def _execution_meta_tuple(self, step_idx: int, step: dict) -> tuple:
    kind = str(step.get("display_kind") or "status").strip() or "status"
    plain = str(step.get("list_text") or step.get("step") or "").strip()
    detail = str(step.get("detail_text") or step.get("list_text") or step.get("step") or "").strip()
    return (kind, step_idx, plain, detail)


def _rebuild_execution_list_from_state(self) -> None:
    if not hasattr(self, "execution_list"):
        return
    self.execution_list.Clear()
    self.execution_meta = []
    steps = list(self._current_execution_steps())
    if not steps:
        self.execution_list.Append("暂无执行过程")
        self.execution_meta.append(("info", -1, "暂无执行过程", ""))
        return
    for idx, step in enumerate(steps):
        kind, step_idx, plain, detail = self._execution_meta_tuple(idx, step if isinstance(step, dict) else {"step": str(step or "")})
        self.execution_list.Append(plain)
        self.execution_meta.append((kind, step_idx, plain, detail))
```

- [ ] **Step 5: Add a focused test for plain/detail execution entries**

```python
def test_build_execution_entry_keeps_full_detail_and_single_line_list_text(frame):
    event = main.CodexEvent(
        type="agent_message_delta",
        text="先检查 main.py 里 F1 面板。\n下一步扩展 codex_client.py。",
        raw_text="先检查 main.py 里 F1 面板。\n下一步扩展 codex_client.py。",
        display_kind="commentary",
    )

    entry = frame._build_execution_entry(event)

    assert entry["detail_text"] == "先检查 main.py 里 F1 面板。\n下一步扩展 codex_client.py。"
    assert "\n" not in entry["list_text"]
    assert "下一步扩展 codex_client.py" in entry["list_text"]
```

- [ ] **Step 6: Run the focused main unit tests**

Run: `pytest tests/test_main_unit.py -k "execution_entry or execution_meta" -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add main.py tests/test_main_unit.py
git commit -m "feat: model execution entries with plain and detail text"
```

### Task 3: Split Execution List Rebuild And Append Paths

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_unit.py`

- [ ] **Step 1: Add a dedicated append helper for visible execution items**

```python
def _append_execution_list_item(self, step: dict) -> None:
    if not hasattr(self, "execution_list"):
        return
    steps = list(self._current_execution_steps())
    step_idx = max(len(steps) - 1, 0)
    kind, _, plain, detail = self._execution_meta_tuple(step_idx, step)
    self.execution_list.Append(plain)
    self.execution_meta.append((kind, step_idx, plain, detail))
    self._request_listbox_repaint(self.execution_list)
```

- [ ] **Step 2: Replace the old append-to-chat path with entry-based persistence plus visible append**

```python
def _append_execution_entry_to_chat(self, chat_id: str, entry: dict, *, save_state: bool = True) -> bool:
    target_chat = self._chat_state_for_execution_steps(chat_id)
    if not isinstance(target_chat, dict):
        return False
    steps = target_chat.get("execution_steps")
    if not isinstance(steps, list):
        steps = []
        target_chat["execution_steps"] = steps
    steps.append(entry)
    if target_chat is self._current_chat_state and self._detail_panel_mode() == "execution":
        self._append_execution_list_item(entry)
    if save_state:
        self._save_state()
    return True
```

- [ ] **Step 3: Update detail mode switching to rebuild only when entering execution mode**

```python
if normalized == "execution":
    self._flush_all_execution_deltas_for_chat(str(self.active_chat_id or self.current_chat_id or ""))
    self._rebuild_execution_list_from_state()
```

- [ ] **Step 4: Add tests that enforce append-only behavior for live updates**

```python
def test_append_execution_entry_to_current_chat_appends_without_clearing(frame, monkeypatch):
    frame.active_chat_id = "chat-1"
    frame.current_chat_id = "chat-1"
    frame._current_chat_state = {"id": "chat-1", "execution_steps": [], "detail_panel_mode": "execution"}
    frame.execution_meta = []
    frame.execution_list.Append("旧项")
    frame.execution_meta.append(("status", 0, "旧项", "旧项"))
    clears = {"count": 0}
    monkeypatch.setattr(frame.execution_list, "Clear", lambda: clears.__setitem__("count", clears["count"] + 1))
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    frame._append_execution_entry_to_chat("chat-1", {"display_kind": "commentary", "list_text": "新项", "detail_text": "新项"})

    assert clears["count"] == 0
    assert frame.execution_list.GetString(frame.execution_list.GetCount() - 1) == "新项"
```

- [ ] **Step 5: Run the append-path tests**

Run: `pytest tests/test_main_unit.py -k "append_execution_entry or rebuild_execution_list" -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_main_unit.py
git commit -m "feat: append execution timeline items incrementally"
```

### Task 4: Make Execution List Interaction Match Answer List Plain/Detail Behavior

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_unit.py`

- [ ] **Step 1: Update execution list copy behavior to prefer `detail_text`**

```python
if ctrl and key in (ord("C"), ord("c")):
    idx = self.execution_list.GetSelection()
    if idx != wx.NOT_FOUND and 0 <= idx < len(self.execution_meta) and wx.TheClipboard.Open():
        try:
            _item_type, _step_idx, plain, detail = self.execution_meta[idx]
            text = str(detail or plain or "").strip()
            if text:
                wx.TheClipboard.SetData(wx.TextDataObject(text))
                self.SetStatusText("已复制")
        finally:
            wx.TheClipboard.Close()
```

- [ ] **Step 2: Add execution detail page generation and activation entry point**

```python
def _ensure_execution_detail_page(self, step: dict, step_idx: int) -> str:
    title = "执行过程详情"
    body = str(step.get("detail_text") or "").strip()
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{title}</title></head>
<body>
<h1>{title}</h1>
<p>类型：{step.get('display_kind') or ''}</p>
<p>阶段：{step.get('phase') or ''}</p>
<p>状态：{step.get('status') or ''}</p>
<pre>{html_escape(body)}</pre>
</body></html>"""
    page_path = self.detail_pages_dir / f"execution_{step_idx}.html"
    page_path.write_text(html, encoding="utf-8")
    return str(page_path)


def _try_open_selected_execution_detail(self) -> bool:
    idx = self.execution_list.GetSelection()
    if idx == wx.NOT_FOUND or idx >= len(self.execution_meta):
        return False
    _kind, step_idx, plain, detail = self.execution_meta[idx]
    if step_idx < 0:
        return False
    steps = list(self._current_execution_steps())
    if not (0 <= step_idx < len(steps)):
        return False
    step = steps[step_idx] if isinstance(steps[step_idx], dict) else {"detail_text": detail, "list_text": plain}
    page_path = self._ensure_execution_detail_page(step, step_idx)
    webbrowser.open(Path(page_path).as_uri())
    self.SetStatusText("已打开执行过程详情网页")
    return True
```

- [ ] **Step 3: Update Enter and double-click handling to use the detail entry point**

```python
def _on_execution_activate(self, _event):
    self._try_open_selected_execution_detail()
```

- [ ] **Step 4: Add interaction tests aligned with answer list behavior**

```python
def test_execution_list_ctrl_c_copies_detail_text(frame, monkeypatch):
    copied = {}
    frame.execution_meta = [("commentary", 0, "单行文本", "完整\n正文")]
    frame.execution_list.Append("单行文本")
    frame.execution_list.SetSelection(0)

    class _Clipboard:
        def Open(self):
            return True
        def Close(self):
            return True
        def SetData(self, obj):
            copied["text"] = obj.GetText()

    monkeypatch.setattr(main.wx, "TheClipboard", _Clipboard())

    class _Event:
        def GetKeyCode(self):
            return ord("C")
        def ControlDown(self):
            return True
        def StopPropagation(self):
            return None

    frame._on_execution_key_down(_Event())

    assert copied["text"] == "完整\n正文"
```

- [ ] **Step 5: Run the interaction tests**

Run: `pytest tests/test_main_unit.py -k "execution_list_ctrl_c or execution_detail" -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_main_unit.py
git commit -m "feat: add execution detail interaction"
```

### Task 5: Buffer Commentary Deltas And Flush Them Into Single Timeline Items

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_unit.py`

- [ ] **Step 1: Add in-memory delta buffer state**

```python
self._execution_delta_buffer = {}
```

- [ ] **Step 2: Add buffer and flush helpers**

```python
def _buffer_execution_delta(self, chat_id: str, event: CodexEvent) -> None:
    key = (str(chat_id or ""), str(event.turn_id or ""), str(event.item_id or ""))
    state = self._execution_delta_buffer.setdefault(
        key,
        {"parts": [], "event": event, "last_event_at": 0.0},
    )
    state["parts"].append(str(event.text or event.raw_text or ""))
    state["event"] = event
    state["last_event_at"] = time.time()


def _flush_execution_delta(self, chat_id: str, turn_id: str | None = None, item_id: str | None = None) -> bool:
    flushed = False
    for key in list(self._execution_delta_buffer.keys()):
        buf_chat, buf_turn, buf_item = key
        if buf_chat != str(chat_id or ""):
            continue
        if turn_id is not None and buf_turn != str(turn_id or ""):
            continue
        if item_id is not None and buf_item != str(item_id or ""):
            continue
        state = self._execution_delta_buffer.pop(key, None)
        if not isinstance(state, dict):
            continue
        text = "".join(state.get("parts") or []).strip()
        if not text:
            continue
        base = state.get("event")
        if not isinstance(base, CodexEvent):
            continue
        merged = CodexEvent(
            type="agent_message_delta",
            thread_id=base.thread_id,
            turn_id=base.turn_id,
            item_id=base.item_id,
            text=text,
            raw_text=text,
            phase=base.phase,
            status=base.status,
            subtype=base.subtype or "agentMessageDelta",
            display_kind="commentary",
        )
        entry = self._build_execution_entry(merged)
        if entry:
            self._append_execution_entry_to_chat(chat_id, entry, save_state=False)
            flushed = True
    return flushed
```

- [ ] **Step 3: Integrate buffering into `_on_codex_event_for_chat()`**

```python
if event_type == "agent_message_delta":
    self._buffer_execution_delta(chat_id, event)
    return

self._flush_execution_delta(chat_id, event_turn_id or None)
entry = self._build_execution_entry(event)
if entry:
    self._append_execution_entry_to_chat(chat_id, entry, save_state=False)
```

- [ ] **Step 4: Flush buffers before view-mode and chat switches**

```python
self._flush_all_execution_deltas_for_chat(str(self.active_chat_id or self.current_chat_id or ""))
```

- [ ] **Step 5: Add tests that multiple deltas become one timeline item**

```python
def test_execution_delta_buffer_flushes_into_single_commentary_item(frame, monkeypatch):
    frame.active_chat_id = "chat-1"
    frame.current_chat_id = "chat-1"
    frame._current_chat_state = {"id": "chat-1", "execution_steps": [], "detail_panel_mode": "execution"}
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    frame._buffer_execution_delta("chat-1", main.CodexEvent(type="agent_message_delta", turn_id="turn-1", item_id="msg-1", text="先检查 main.py。"))
    frame._buffer_execution_delta("chat-1", main.CodexEvent(type="agent_message_delta", turn_id="turn-1", item_id="msg-1", text="下一步扩展 codex_client.py。"))

    flushed = frame._flush_execution_delta("chat-1", "turn-1", "msg-1")

    assert flushed is True
    assert len(frame._current_chat_state["execution_steps"]) == 1
    assert frame._current_chat_state["execution_steps"][0]["detail_text"] == "先检查 main.py。下一步扩展 codex_client.py。"
```

- [ ] **Step 6: Run the delta-buffer tests**

Run: `pytest tests/test_main_unit.py -k "execution_delta_buffer or agent_message_delta" -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add main.py tests/test_main_unit.py
git commit -m "feat: buffer codex commentary deltas"
```

### Task 6: Run Regression Verification For The Full Execution Timeline Flow

**Files:**
- Modify: `tests/test_main_unit.py`
- Modify: `tests/test_codex_client_unit.py`

- [ ] **Step 1: Add a regression test that background chat execution events do not repaint the visible list**

```python
def test_background_chat_execution_entry_does_not_append_to_current_visible_list(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state = {"id": "chat-current", "execution_steps": [], "detail_panel_mode": "execution"}
    frame.archived_chats = [{"id": "chat-other", "execution_steps": [], "detail_panel_mode": "execution", "turns": []}]
    appended = {"count": 0}
    monkeypatch.setattr(frame, "_append_execution_list_item", lambda *_args, **_kwargs: appended.__setitem__("count", appended["count"] + 1))
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    entry = {"display_kind": "commentary", "list_text": "后台更新", "detail_text": "后台更新"}
    frame._append_execution_entry_to_chat("chat-other", entry, save_state=False)

    assert appended["count"] == 0
    assert frame.archived_chats[0]["execution_steps"] == [entry]
```

- [ ] **Step 2: Add a regression test that switching to execution mode flushes pending delta first**

```python
def test_switch_to_execution_mode_flushes_pending_delta_before_rebuild(frame, monkeypatch):
    frame.active_chat_id = "chat-1"
    frame.current_chat_id = "chat-1"
    frame._current_chat_state = {"id": "chat-1", "execution_steps": [], "detail_panel_mode": "answers"}
    frame._buffer_execution_delta("chat-1", main.CodexEvent(type="agent_message_delta", turn_id="turn-1", item_id="msg-1", text="待落地过程"))
    rebuilt = {"count": 0}
    monkeypatch.setattr(frame, "_rebuild_execution_list_from_state", lambda: rebuilt.__setitem__("count", rebuilt["count"] + 1))
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    frame._apply_detail_panel_mode("execution", refresh_execution=True)

    assert frame._current_chat_state["execution_steps"][0]["detail_text"] == "待落地过程"
    assert rebuilt["count"] == 1
```

- [ ] **Step 3: Run the regression suites**

Run: `pytest tests/test_codex_client_unit.py tests/test_main_unit.py -k "execution or codex" -v`
Expected: PASS

- [ ] **Step 4: Run the broader desktop unit suite that covers list interaction paths**

Run: `pytest tests/test_main_unit.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_main_unit.py tests/test_codex_client_unit.py
git commit -m "test: cover codex execution timeline accessibility flow"
```
