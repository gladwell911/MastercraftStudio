# Pending Answer Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 当用户在回答完成前关闭程序时，下次启动后自动恢复未完成请求，并将最终回答写回原聊天原 turn，覆盖 `codex`、`claudecode`、普通模型。

**Architecture:** 为每个 turn 增加持久化的请求恢复元数据，把“正在运行”的临时状态改成“可恢复任务”状态。关闭程序时将未完成 turn 标记为 `interrupted_pending` 并落盘；启动后由统一恢复调度器扫描这些 turn，再按模型类型分别走 `resume` 或 `retry`。UI 只在“开始恢复”和“恢复完成”两个节点刷新，不恢复高频流式刷新。

**Tech Stack:** Python 3.11, wxPython, 现有 `CodexAppServerClient`, 现有状态文件 `app_state.json`, pytest

---

## File Map

- Modify: `main.py`
  - 扩展 turn 持久化字段
  - 关闭前标记未完成请求
  - 启动后恢复调度器
  - 普通模型 / ClaudeCode 的恢复入口
  - 渲染“恢复中”状态
- Modify: `codex_client.py`
  - 如有必要补线程读取辅助方法；优先复用现有 `resume_thread`
- Modify: `tests/test_main_unit.py`
  - 关闭、启动、普通模型恢复、ClaudeCode 恢复、UI 状态测试
- Modify: `tests/test_codex_integration.py`
  - Codex thread 恢复与回答回填测试
- Modify: `tests/test_remote_api_integration.py`
  - 如远程状态需要暴露恢复状态，则补状态接口测试

## Shared Data Contract

每个 turn 新增并持久化以下字段，统一由 `main.py` 中的辅助函数维护：

```python
{
    "request_status": "",  # "", "pending", "interrupted_pending", "restoring", "done", "failed"
    "request_model": "",
    "request_question": "",
    "request_started_at": 0.0,
    "request_last_attempt_at": 0.0,
    "request_attempt_count": 0,
    "request_recoverable": False,
    "request_recovery_mode": "",  # "", "resume", "retry"
    "request_resume_token": {},   # codex / claudecode 用
    "request_error": "",
    "request_recovered_after_restart": False,
}
```

新增辅助函数的目标签名：

```python
def _mark_turn_request_pending(self, turn: dict, model: str, question: str) -> None: ...
def _mark_turn_request_interrupted(self, turn: dict) -> None: ...
def _mark_turn_request_restoring(self, turn: dict) -> None: ...
def _mark_turn_request_done(self, turn: dict) -> None: ...
def _mark_turn_request_failed(self, turn: dict, error: str) -> None: ...
def _turn_request_status_text(self, turn: dict) -> str: ...
```

### Task 1: 建立 turn 级恢复元数据

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_unit.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_submit_question_marks_turn_pending_with_recovery_metadata(frame, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(main.threading, "Thread", lambda *a, **k: type("T", (), {"start": lambda self: None})())
    frame._refresh_openclaw_sync_lifecycle = lambda force_replay=False: None
    frame._play_send_sound = lambda: None

    ok, _ = frame._submit_question("恢复这条回答", source="local", model="openai/gpt-5.2")

    turn = frame.active_session_turns[-1]
    assert ok is True
    assert turn["request_status"] == "pending"
    assert turn["request_model"] == "openai/gpt-5.2"
    assert turn["request_question"] == "恢复这条回答"
    assert turn["request_recoverable"] is True
    assert turn["request_recovery_mode"] == "retry"


def test_codex_submit_sets_resume_recovery_mode(frame, monkeypatch):
    monkeypatch.setattr(main.threading, "Thread", lambda *a, **k: type("T", (), {"start": lambda self: None})())
    frame._refresh_openclaw_sync_lifecycle = lambda force_replay=False: None
    frame._play_send_sound = lambda: None
    frame.active_codex_thread_id = "thread-1"

    ok, _ = frame._submit_question("继续修复", source="local", model="codex/main")

    turn = frame.active_session_turns[-1]
    assert ok is True
    assert turn["request_recovery_mode"] == "resume"
    assert turn["request_resume_token"]["thread_id"] == "thread-1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_main_unit.py -k "marks_turn_pending_with_recovery_metadata or codex_submit_sets_resume_recovery_mode" -v`
Expected: FAIL because turn objects do not yet include recovery metadata.

- [ ] **Step 3: Write minimal implementation**

```python
def _request_resume_token_for_model(self, model: str) -> dict:
    if is_codex_model(model):
        return {
            "thread_id": str(self.active_codex_thread_id or ""),
            "turn_id": str(self.active_codex_turn_id or ""),
        }
    if is_claudecode_model(model):
        return {
            "session_id": str(self.active_claudecode_session_id or ""),
        }
    return {}


def _mark_turn_request_pending(self, turn: dict, model: str, question: str) -> None:
    turn["request_status"] = "pending"
    turn["request_model"] = str(model or "")
    turn["request_question"] = str(question or "")
    turn["request_started_at"] = time.time()
    turn["request_last_attempt_at"] = time.time()
    turn["request_attempt_count"] = 1
    turn["request_recoverable"] = True
    turn["request_recovery_mode"] = "resume" if (is_codex_model(model) or is_claudecode_model(model)) else "retry"
    turn["request_resume_token"] = self._request_resume_token_for_model(model)
    turn["request_error"] = ""
    turn["request_recovered_after_restart"] = False
```

在 `_submit_question()` 中，append turn 后立即调用：

```python
self._mark_turn_request_pending(self.active_session_turns[self.active_turn_idx], resolved_model, q)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_main_unit.py -k "marks_turn_pending_with_recovery_metadata or codex_submit_sets_resume_recovery_mode" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_main_unit.py
git commit -m "feat: persist per-turn recovery metadata"
```

### Task 2: 关闭程序时把未完成请求标记为可恢复

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_unit.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_on_close_marks_pending_turns_interrupted_before_save(frame, monkeypatch):
    frame.active_session_turns = [
        {
            "question": "未完成问题",
            "answer_md": main.REQUESTING_TEXT,
            "model": "openai/gpt-5.2",
            "request_status": "pending",
            "request_recoverable": True,
        }
    ]
    saved = {"snapshot": None}
    monkeypatch.setattr(frame, "_save_state", lambda: saved.__setitem__("snapshot", frame.active_session_turns[0]["request_status"]))
    monkeypatch.setattr(frame._voice_input, "cancel", lambda: None)
    monkeypatch.setattr(frame._realtime_call, "shutdown", lambda: None)
    monkeypatch.setattr(frame, "_stop_openclaw_sync", lambda: None)
    monkeypatch.setattr(frame, "_store_current_chat_if_needed", lambda: None)
    monkeypatch.setattr(frame._global_ctrl_hook, "stop", lambda: None)
    monkeypatch.setattr(frame, "_unregister_global_hotkey", lambda: None)
    frame._codex_clients = {}

    class E:
        def Skip(self): pass

    frame._on_close(E())

    assert saved["snapshot"] == "interrupted_pending"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_main_unit.py -k "marks_pending_turns_interrupted_before_save" -v`
Expected: FAIL because `_on_close()` currently saves without rewriting pending statuses.

- [ ] **Step 3: Write minimal implementation**

```python
def _mark_all_recoverable_turns_interrupted(self) -> None:
    for chat in [self._current_chat_state, *self.archived_chats]:
        turns = chat.get("turns") if isinstance(chat, dict) else None
        if not isinstance(turns, list):
            continue
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            if turn.get("request_status") == "pending" and turn.get("request_recoverable"):
                turn["request_status"] = "interrupted_pending"
```

在 `_on_close()` 中、`_save_state()` 之前调用：

```python
self._mark_all_recoverable_turns_interrupted()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_main_unit.py -k "marks_pending_turns_interrupted_before_save" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_main_unit.py
git commit -m "feat: persist interrupted requests on close"
```

### Task 3: 启动后扫描并调度未完成请求恢复

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_unit.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_load_state_schedules_recovery_for_interrupted_turns(frame, monkeypatch):
    scheduled = []
    monkeypatch.setattr(frame, "_schedule_pending_request_recovery", lambda: scheduled.append("scheduled"))
    frame._current_chat_state["turns"] = [
        {"question": "恢复", "model": "openai/gpt-5.2", "request_status": "interrupted_pending", "request_recoverable": True}
    ]

    frame._after_state_loaded_for_tests()

    assert scheduled == ["scheduled"]
```

说明：如果没有合适入口，先新增小型启动后钩子 `_after_state_loaded_for_tests()`，让生产初始化和测试都能复用。

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_main_unit.py -k "schedules_recovery_for_interrupted_turns" -v`
Expected: FAIL because there is no recovery scheduler hook.

- [ ] **Step 3: Write minimal implementation**

```python
def _iter_recoverable_turns(self):
    for chat in [self._current_chat_state, *self.archived_chats]:
        turns = chat.get("turns") if isinstance(chat, dict) else []
        for idx, turn in enumerate(turns):
            if isinstance(turn, dict) and turn.get("request_status") == "interrupted_pending" and turn.get("request_recoverable"):
                yield chat, idx, turn


def _schedule_pending_request_recovery(self) -> None:
    if getattr(self, "_pending_recovery_started", False):
        return
    self._pending_recovery_started = True
    threading.Thread(target=self._recover_pending_requests_worker, daemon=True).start()
```

在主窗口初始化加载状态后、`_render_answer_list()` 前后任选一个固定点调用：

```python
self._schedule_pending_request_recovery()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_main_unit.py -k "schedules_recovery_for_interrupted_turns" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_main_unit.py
git commit -m "feat: schedule pending request recovery on startup"
```

### Task 4: 普通模型恢复为自动重试并写回原 turn

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_unit.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_recover_regular_model_retries_same_question_into_original_turn(frame, monkeypatch):
    turn = {
        "question": "关闭前的问题",
        "answer_md": "",
        "model": "openai/gpt-5.2",
        "request_status": "interrupted_pending",
        "request_model": "openai/gpt-5.2",
        "request_question": "关闭前的问题",
        "request_recoverable": True,
        "request_recovery_mode": "retry",
    }
    frame._current_chat_state["turns"] = [turn]
    seen = {}
    monkeypatch.setattr(frame, "_run_recovery_retry", lambda chat_id, turn_idx, turn_obj: seen.setdefault("call", (chat_id, turn_idx, turn_obj["request_question"])))

    frame._recover_pending_requests_worker()

    assert seen["call"][1] == 0
    assert seen["call"][2] == "关闭前的问题"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_main_unit.py -k "recover_regular_model_retries_same_question_into_original_turn" -v`
Expected: FAIL because retry recovery path does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
def _recover_pending_requests_worker(self) -> None:
    for chat, turn_idx, turn in self._iter_recoverable_turns():
        mode = str(turn.get("request_recovery_mode") or "")
        chat_id = str(chat.get("id") or self.current_chat_id or "")
        if mode == "retry":
            self._run_recovery_retry(chat_id, turn_idx, turn)
        else:
            self._run_recovery_resume(chat_id, turn_idx, turn)


def _run_recovery_retry(self, chat_id: str, turn_idx: int, turn: dict) -> None:
    turn["request_status"] = "restoring"
    turn["request_last_attempt_at"] = time.time()
    turn["request_attempt_count"] = int(turn.get("request_attempt_count") or 0) + 1
    question = str(turn.get("request_question") or turn.get("question") or "").strip()
    model = str(turn.get("request_model") or turn.get("model") or "").strip()
    wx.CallAfter(self._save_state)
    threading.Thread(
        target=self._worker,
        args=(os.getenv("OPENROUTER_API_KEY", "").strip(), turn_idx, question, model, False, chat_id),
        daemon=True,
    ).start()
```

`_on_done()` 成功后补充：

```python
turns[turn_idx]["request_status"] = "done"
turns[turn_idx]["request_error"] = ""
turns[turn_idx]["request_recovered_after_restart"] = turns[turn_idx].get("request_status") == "restoring"
```

失败时：

```python
turns[turn_idx]["request_status"] = "failed"
turns[turn_idx]["request_error"] = err
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_main_unit.py -k "recover_regular_model_retries_same_question_into_original_turn" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_main_unit.py
git commit -m "feat: retry interrupted non-cli requests on startup"
```

### Task 5: Codex 与 ClaudeCode 恢复逻辑

**Files:**
- Modify: `main.py`
- Modify: `codex_client.py`
- Test: `tests/test_codex_integration.py`
- Test: `tests/test_main_unit.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_recover_codex_turn_prefers_resume_token(frame, monkeypatch):
    turn = {
        "question": "继续改",
        "model": "codex/main",
        "request_status": "interrupted_pending",
        "request_model": "codex/main",
        "request_recoverable": True,
        "request_recovery_mode": "resume",
        "request_resume_token": {"thread_id": "thread-1"},
    }
    frame._current_chat_state["id"] = "chat-a"
    frame._current_chat_state["turns"] = [turn]
    seen = {}
    monkeypatch.setattr(frame, "_resume_codex_pending_turn", lambda chat_id, turn_idx, turn_obj: seen.setdefault("call", (chat_id, turn_idx, turn_obj["request_resume_token"]["thread_id"])))

    frame._recover_pending_requests_worker()

    assert seen["call"] == ("chat-a", 0, "thread-1")


def test_recover_claudecode_turn_falls_back_to_retry_when_no_session(frame, monkeypatch):
    turn = {
        "question": "继续整理",
        "model": "claudecode/default",
        "request_status": "interrupted_pending",
        "request_model": "claudecode/default",
        "request_recoverable": True,
        "request_recovery_mode": "resume",
        "request_resume_token": {},
    }
    frame._current_chat_state["id"] = "chat-b"
    frame._current_chat_state["turns"] = [turn]
    seen = {}
    monkeypatch.setattr(frame, "_run_recovery_retry", lambda chat_id, turn_idx, turn_obj: seen.setdefault("call", (chat_id, turn_idx, turn_obj["model"])))

    frame._recover_pending_requests_worker()

    assert seen["call"] == ("chat-b", 0, "claudecode/default")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_main_unit.py -k "recover_codex_turn_prefers_resume_token or recover_claudecode_turn_falls_back_to_retry_when_no_session" -v`
Expected: FAIL because CLI recovery dispatch does not exist.

- [ ] **Step 3: Write minimal implementation**

```python
def _run_recovery_resume(self, chat_id: str, turn_idx: int, turn: dict) -> None:
    model = str(turn.get("request_model") or turn.get("model") or "").strip()
    if is_codex_model(model):
        self._resume_codex_pending_turn(chat_id, turn_idx, turn)
        return
    self._resume_claudecode_pending_turn(chat_id, turn_idx, turn)


def _resume_codex_pending_turn(self, chat_id: str, turn_idx: int, turn: dict) -> None:
    token = turn.get("request_resume_token") if isinstance(turn.get("request_resume_token"), dict) else {}
    thread_id = str(token.get("thread_id") or "").strip()
    if not thread_id:
        self._run_recovery_retry(chat_id, turn_idx, turn)
        return
    turn["request_status"] = "restoring"
    wx.CallAfter(self._save_state)
    threading.Thread(target=self._resume_codex_thread_worker, args=(thread_id,), daemon=True).start()


def _resume_claudecode_pending_turn(self, chat_id: str, turn_idx: int, turn: dict) -> None:
    token = turn.get("request_resume_token") if isinstance(turn.get("request_resume_token"), dict) else {}
    session_id = str(token.get("session_id") or "").strip()
    if not session_id:
        self._run_recovery_retry(chat_id, turn_idx, turn)
        return
    turn["request_status"] = "restoring"
    wx.CallAfter(self._save_state)
    self._start_claudecode_worker_for_turn(chat_id=chat_id, turn_idx=turn_idx, question=str(turn.get("request_question") or turn.get("question") or ""), session_id=session_id)
```

`codex_client.py` 仅在确有缺口时补一个只读 helper；如果现有 `resume_thread()` 足够，保持不改。

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_main_unit.py -k "recover_codex_turn_prefers_resume_token or recover_claudecode_turn_falls_back_to_retry_when_no_session" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add main.py codex_client.py tests/test_main_unit.py tests/test_codex_integration.py
git commit -m "feat: resume interrupted codex and claudecode turns"
```

### Task 6: 渲染恢复中状态并限制重试次数

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_unit.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_render_answer_list_shows_restoring_status_for_interrupted_turn(frame):
    frame.active_session_turns = [
        {
            "question": "还没完成",
            "answer_md": "",
            "model": "openai/gpt-5.2",
            "request_status": "restoring",
        }
    ]

    frame._render_answer_list()

    rows = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert "正在恢复上次未完成的回答" in rows


def test_recovery_stops_after_max_attempts(frame):
    turn = {
        "question": "失败三次",
        "model": "openai/gpt-5.2",
        "request_status": "interrupted_pending",
        "request_attempt_count": 3,
        "request_recoverable": True,
        "request_recovery_mode": "retry",
    }
    frame._current_chat_state["turns"] = [turn]

    frame._recover_pending_requests_worker()

    assert turn["request_status"] == "failed"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_main_unit.py -k "shows_restoring_status_for_interrupted_turn or recovery_stops_after_max_attempts" -v`
Expected: FAIL because UI and retry limit do not exist.

- [ ] **Step 3: Write minimal implementation**

```python
MAX_RECOVERY_ATTEMPTS = 3


def _turn_answer_markdown(self, turn: dict) -> tuple[str, str]:
    answer_md = str((turn or {}).get("answer_md") or "")
    request_status = str((turn or {}).get("request_status") or "")
    if request_status == "restoring" and not answer_md.strip():
        return answer_md, "正在恢复上次未完成的回答"
    if request_status == "failed" and not answer_md.strip():
        error = str((turn or {}).get("request_error") or "").strip()
        return answer_md, error or "上次未完成回答恢复失败，可手动继续"
    ...


def _recover_pending_requests_worker(self) -> None:
    for chat, turn_idx, turn in self._iter_recoverable_turns():
        attempts = int(turn.get("request_attempt_count") or 0)
        if attempts >= MAX_RECOVERY_ATTEMPTS:
            self._mark_turn_request_failed(turn, "上次未完成回答恢复失败，可手动继续")
            wx.CallAfter(self._save_state)
            continue
        ...
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_main_unit.py -k "shows_restoring_status_for_interrupted_turn or recovery_stops_after_max_attempts" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_main_unit.py
git commit -m "feat: show recovery state and cap retry attempts"
```

### Task 7: 回归验证

**Files:**
- Modify: `tests/test_main_unit.py`
- Modify: `tests/test_codex_integration.py`
- Modify: `tests/test_remote_api_integration.py`

- [ ] **Step 1: Run focused unit and integration tests**

Run:

```bash
pytest tests/test_main_unit.py -k "recovery or interrupted or pending"
pytest tests/test_codex_integration.py -k "recover or resume"
```

Expected: PASS

- [ ] **Step 2: Run broader regression set**

Run:

```bash
pytest tests/test_main_unit.py tests/test_codex_integration.py tests/test_remote_api_integration.py -k "codex or claudecode or send or close or state or history or recovery"
```

Expected: PASS with only existing unrelated warnings, if any.

- [ ] **Step 3: Manual verification**

Run the app and verify:

```text
1. 发送普通模型问题，在收到回答前关闭程序。
2. 重新启动，确认原聊天显示“正在恢复上次未完成的回答”。
3. 等待自动重试完成，确认回答写回原 turn。
4. 发送 codex 问题，在收到回答前关闭程序。
5. 重新启动，确认 codex 优先按 thread 恢复；若恢复失败则自动重试。
6. 删除带未完成任务的聊天，重启后确认不会恢复已删除聊天。
```

- [ ] **Step 4: Commit final verification updates**

```bash
git add main.py tests/test_main_unit.py tests/test_codex_integration.py tests/test_remote_api_integration.py
git commit -m "test: cover pending answer recovery flow"
```

## Self-Review

- Spec coverage: 已覆盖关闭前标记、启动后恢复、Codex/ClaudeCode resume、普通模型 retry、原 turn 回填、失败兜底、UI 表现、删除聊天边界。
- Placeholder scan: 无 `TODO` / `TBD` / “后续再实现” 占位语句。
- Type consistency: 统一使用 `request_status`, `request_recovery_mode`, `request_resume_token`, `request_attempt_count` 等字段名，任务间保持一致。
