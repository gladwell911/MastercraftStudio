# Codex Thread Isolation And Image Generation Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent new Codex chats from reusing stale `gpt-5.3` thread state and verify that `codex/gpt-5.3-codex-spark-high` never sends the runtime `image_generation` tool.

**Architecture:** Fix the thread crossover at the active-chat state layer in `main.py` by explicitly resetting active Codex session state on every new-chat path. Keep model-specific tool capability gating centralized in `codex_client.py` and verify it with focused unit tests plus a log-based smoke check.

**Tech Stack:** Python, wxPython desktop app state management, pytest, Codex worker process, sqlite-backed runtime logs

---

### Task 1: Add failing coverage for stale Codex state on new-chat paths

**Files:**
- Modify: `tests/test_main_unit.py`

- [ ] **Step 1: Add a failing test for local new-chat reset when the previous chat has no turns**

Insert a new test near the existing `_on_new_chat_clicked()` coverage:

```python
def test_new_chat_clears_stale_active_codex_state_without_turns(frame, monkeypatch):
    frame.active_chat_id = "chat-old"
    frame.current_chat_id = "chat-old"
    frame.active_session_turns = []
    frame.active_codex_thread_id = "thread-stale"
    frame.active_codex_turn_id = "turn-stale"
    frame.active_codex_turn_active = True
    frame.active_codex_pending_prompt = "pending"
    frame.active_codex_pending_request = {"kind": "user_input"}
    frame.active_codex_request_queue = [{"prompt": "queued"}]
    frame.active_codex_thread_flags = ["waitingOnUserInput"]
    frame.active_codex_latest_assistant_text = "partial"
    frame.active_codex_latest_assistant_phase = "answer"
    frame._current_chat_state = {"id": "chat-old", "turns": []}

    monkeypatch.setattr(frame, "_archive_active_session", lambda **_kwargs: None)
    monkeypatch.setattr(frame, "_sync_codex_speed_combo_from_chat", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_mark_history_list_dirty", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_render_answer_list", lambda: None)
    monkeypatch.setattr(frame.input_edit, "SetFocus", lambda: None)
    monkeypatch.setattr(frame, "SetStatusText", lambda _text: None)
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: None)
    monkeypatch.setattr(frame, "_mark_openclaw_lifecycle_dirty", lambda: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *_args, **_kwargs: None)

    frame._on_new_chat_clicked(None)

    assert frame.active_codex_thread_id == ""
    assert frame.active_codex_turn_id == ""
    assert frame.active_codex_turn_active is False
    assert frame.active_codex_pending_prompt == ""
    assert frame.active_codex_pending_request is None
    assert frame.active_codex_request_queue == []
    assert frame.active_codex_thread_flags == []
    assert frame.active_codex_latest_assistant_text == ""
    assert frame.active_codex_latest_assistant_phase == ""
    assert frame._current_chat_state["codex_thread_id"] == ""
    assert frame._current_chat_state["codex_turn_id"] == ""
    assert frame._current_chat_state["codex_turn_active"] is False
```

- [ ] **Step 2: Add a failing test for remote new-chat reset**

Add a second test near the `_start_remote_new_chat()` coverage:

```python
def test_remote_new_chat_clears_stale_active_codex_state_without_turns(frame, monkeypatch):
    frame.active_chat_id = "chat-old"
    frame.current_chat_id = "chat-old"
    frame.active_session_turns = []
    frame.active_codex_thread_id = "thread-stale"
    frame.active_codex_turn_id = "turn-stale"
    frame.active_codex_turn_active = True
    frame.active_codex_pending_prompt = "pending"
    frame.active_codex_pending_request = {"kind": "user_input"}
    frame.active_codex_request_queue = [{"prompt": "queued"}]
    frame.active_codex_thread_flags = ["waitingOnUserInput"]
    frame.active_codex_latest_assistant_text = "partial"
    frame.active_codex_latest_assistant_phase = "answer"
    frame._current_chat_state = {"id": "chat-old", "turns": []}

    monkeypatch.setattr(frame, "_archive_active_session", lambda **_kwargs: None)
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_refresh_history", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_render_answer_list", lambda: None)
    monkeypatch.setattr(frame, "SetStatusText", lambda _text: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *_args, **_kwargs: None)

    created = frame._start_remote_new_chat({"model": "codex/gpt-5.4-medium"})

    assert created["model"] == "codex/gpt-5.4-medium"
    assert frame.active_codex_thread_id == ""
    assert frame.active_codex_turn_id == ""
    assert frame.active_codex_turn_active is False
    assert frame._current_chat_state["codex_thread_id"] == ""
    assert frame._current_chat_state["codex_turn_id"] == ""
    assert frame._current_chat_state["codex_turn_active"] is False
```

- [ ] **Step 3: Add a failing worker-path regression test**

Extend the existing worker tests near `test_codex_worker_rebuilds_context_when_saved_rollout_is_missing`:

```python
def test_codex_worker_does_not_reuse_stale_thread_after_new_chat_reset(frame, monkeypatch):
    frame.active_chat_id = "chat-old"
    frame.current_chat_id = "chat-old"
    frame.active_session_turns = []
    frame.active_codex_thread_id = "thread-stale"
    frame.active_codex_turn_id = "turn-stale"
    frame.active_codex_turn_active = True
    frame._current_chat_state = {"id": "chat-old", "turns": []}

    monkeypatch.setattr(frame, "_archive_active_session", lambda **_kwargs: None)
    monkeypatch.setattr(frame, "_sync_codex_speed_combo_from_chat", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_mark_history_list_dirty", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_render_answer_list", lambda: None)
    monkeypatch.setattr(frame.input_edit, "SetFocus", lambda: None)
    monkeypatch.setattr(frame, "SetStatusText", lambda _text: None)
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: None)
    monkeypatch.setattr(frame, "_mark_openclaw_lifecycle_dirty", lambda: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *_args, **_kwargs: None)

    frame._on_new_chat_clicked(None)

    frame.active_session_turns = [
        {
            "question": "新问题",
            "answer_md": main.REQUESTING_TEXT,
            "model": "codex/gpt-5.4-medium",
            "created_at": 1.0,
        }
    ]
    frame.active_turn_idx = 0
    frame._current_chat_state["id"] = frame.active_chat_id
    frame._current_chat_state["model"] = "codex/gpt-5.4-medium"
    frame._current_chat_state["turns"] = frame.active_session_turns

    sent = []

    class _Client:
        def start(self):
            pass

        def start_turn(self, **payload):
            sent.append(payload)
            return "req-1"

    monkeypatch.setattr(frame, "_get_or_create_codex_client", lambda _chat_id, _model="": _Client())
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *args, **kwargs: None)

    frame._run_codex_turn_worker(frame.active_chat_id, 0, "新问题", "codex/gpt-5.4-medium")

    assert sent[0]["thread_id"] == ""
```

- [ ] **Step 4: Run the focused tests to verify they fail first**

Run:

```bash
pytest tests/test_main_unit.py -k "new_chat_clears_stale_active_codex_state_without_turns or remote_new_chat_clears_stale_active_codex_state_without_turns or codex_worker_does_not_reuse_stale_thread_after_new_chat_reset" -v
```

Expected: FAIL because `_on_new_chat_clicked()` and `_start_remote_new_chat()` currently leave stale active Codex state intact when `_archive_active_session()` returns early.

### Task 2: Implement explicit active Codex reset and reuse it on fresh-chat paths

**Files:**
- Modify: `main.py`
- Modify: `tests/test_main_unit.py`

- [ ] **Step 1: Add a focused active-Codex reset helper**

In `main.py`, add two small helpers near the existing chat-state helpers:

```python
def _reset_active_codex_session_state(self) -> None:
    self.active_codex_thread_id = ""
    self.active_codex_turn_id = ""
    self.active_codex_turn_active = False
    self.active_codex_pending_prompt = ""
    self.active_codex_pending_request = None
    self.active_codex_request_queue = []
    self.active_codex_thread_flags = []
    self.active_codex_latest_assistant_text = ""
    self.active_codex_latest_assistant_phase = ""


def _write_active_codex_session_state_to_chat(self, chat: dict) -> None:
    chat["codex_thread_id"] = self.active_codex_thread_id
    chat["codex_turn_id"] = self.active_codex_turn_id
    chat["codex_turn_active"] = self.active_codex_turn_active
    chat["codex_pending_prompt"] = self.active_codex_pending_prompt
    chat["codex_pending_request"] = self.active_codex_pending_request
    chat["codex_request_queue"] = self.active_codex_request_queue
    chat["codex_thread_flags"] = self.active_codex_thread_flags
    chat["codex_latest_assistant_text"] = self.active_codex_latest_assistant_text
    chat["codex_latest_assistant_phase"] = self.active_codex_latest_assistant_phase
```

- [ ] **Step 2: Use the helper from `_on_new_chat_clicked()` and `_start_remote_new_chat()`**

Update both fresh-chat entry points so they explicitly reset active Codex state after archiving and before the new chat is used:

```python
self._reset_active_codex_session_state()
...
self._current_chat_state["model"] = model
self._write_active_codex_session_state_to_chat(self._current_chat_state)
```

Apply this in:

- `_on_new_chat_clicked()`
- `_start_remote_new_chat()`

Do not clear archived chat metadata. Only the active in-memory session state should be reset here.

- [ ] **Step 3: Align `_clear_context_and_start_new_chat()` with the new helper**

Replace the duplicated active Codex reset block in `_clear_context_and_start_new_chat()` with the shared helper and shared chat-write helper:

```python
self._reset_active_codex_session_state()
...
self._write_active_codex_session_state_to_chat(self._current_chat_state)
```

Keep the existing OpenClaw and Claude Code resets untouched.

- [ ] **Step 4: Run the focused new-chat and worker tests again**

Run:

```bash
pytest tests/test_main_unit.py -k "new_chat_clears_stale_active_codex_state_without_turns or remote_new_chat_clears_stale_active_codex_state_without_turns or codex_worker_does_not_reuse_stale_thread_after_new_chat_reset or clear_context_shortcut_clears_current_chat_backend_thread_state" -v
```

Expected: PASS for all four tests.

- [ ] **Step 5: Run broader Codex main-window regressions**

Run:

```bash
pytest tests/test_main_unit.py -k "new_chat or codex_worker_rebuilds_context_when_saved_rollout_is_missing or codex_worker_passes_saved_fast_service_tier_to_thread_and_turn" -v
```

Expected: PASS, confirming the helper refactor did not break existing new-chat or worker behavior.

- [ ] **Step 6: Commit the state-isolation fix**

```bash
git add main.py tests/test_main_unit.py
git commit -m "fix: reset active codex state on new chat"
```

### Task 3: Verify the `5.3 spark` image-generation guard remains the single runtime gate

**Files:**
- Inspect: `codex_client.py`
- Test: `tests/test_codex_client_unit.py`
- Inspect: `.codex-home/logs_2.sqlite`

- [ ] **Step 1: Run the focused Codex client guard tests**

Run:

```bash
pytest tests/test_codex_client_unit.py -k "codex_disabled_features_for_53_spark or build_codex_app_server_command_disables_image_generation_for_53_spark or build_codex_app_server_command_does_not_disable_image_generation_for_54 or start_turn_items or thread_requests_send_service_tier" -v
```

Expected: PASS, confirming the source tree still centralizes `5.3 spark` runtime disablement in `codex_client.py` and does not mutate request payload shape.

- [ ] **Step 2: Launch a fresh `5.3 spark` chat and verify the newest websocket request does not include `image_generation`**

After starting the app and sending a plain text message in a new `codex gpt5.3spark high` chat, run:

```bash
@'
import sqlite3
path = r'C:\code\sj\mc\.codex-home\logs_2.sqlite'
conn = sqlite3.connect(path)
cur = conn.cursor()
cur.execute("""
select id, feedback_log_body
from logs
where feedback_log_body like '%websocket request:%'
  and feedback_log_body like '%model=gpt-5.3-codex-spark%'
order by id desc
limit 5
""")
for log_id, body in cur.fetchall():
    text = body or ""
    print("ID", log_id, "HAS_IMAGE_GENERATION", "image_generation" in text)
conn.close()
'@ | python -
```

Expected: the newest `gpt-5.3-codex-spark` request prints `HAS_IMAGE_GENERATION False`.

- [ ] **Step 3: Launch a fresh `5.4` chat and verify session initialization does not resume a stale `5.3` thread**

After starting a new `codex gpt5.4 medium` chat and sending a plain text message, run:

```bash
@'
import sqlite3
path = r'C:\code\sj\mc\.codex-home\logs_2.sqlite'
conn = sqlite3.connect(path)
cur = conn.cursor()
cur.execute("""
select id, feedback_log_body
from logs
where feedback_log_body like '%Configuring session: model=%'
order by id desc
limit 10
""")
for log_id, body in cur.fetchall():
    text = body or ""
    if 'Configuring session: model=gpt-5.4' in text:
        print('ID', log_id, 'FOUND_GPT54', True)
        break
else:
    print('FOUND_GPT54', False)
conn.close()
'@ | python -
```

Expected: output includes `FOUND_GPT54 True`, and the corresponding new chat no longer surfaces the `image_generation` 400 from a resumed `5.3` thread.

- [ ] **Step 4: Record final verification state**

Run:

```bash
git status --short
```

Expected: only the intentional `main.py` / `tests/test_main_unit.py` changes remain if no other local work was created during verification.
