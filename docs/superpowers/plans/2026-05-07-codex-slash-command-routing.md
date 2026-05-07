# Codex Slash Command Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route Codex slash-style input locally so `/...` commands are recognized by the desktop app instead of being sent to Codex as normal chat text.

**Architecture:** Add a parser that treats a leading slash token as a Codex local command only when the selected model is Codex and no attachments are being sent. Replace the single `/status` check with a command registry whose handlers either perform an app-server action, return a local answer, or explicitly report that the command is not supported by the desktop app yet.

**Tech Stack:** Python, wxPython, existing `CodexAppServerClient`, pytest.

---

### Task 1: Parser and Regression Tests

**Files:**
- Modify: `tests/test_main_unit.py`
- Modify: `main.py`

- [ ] **Step 1: Write failing tests**
  - Add tests that `/help`, `/compact now`, and `/unknown` are identified as local Codex commands.
  - Add tests that ordinary text containing `/status` is not identified.
  - Add tests that unsupported slash commands produce an answer and do not call `start_turn`.

- [ ] **Step 2: Implement parser**
  - Replace `_codex_local_command_name()` with a parser returning `{name, args, raw}`.
  - Parse only inputs whose first non-space character is `/`.

### Task 2: Registry and Handlers

**Files:**
- Modify: `main.py`
- Modify: `codex_client.py`
- Modify: `tests/test_codex_client_unit.py`

- [ ] **Step 1: Add app-server wrappers**
  - Add `compact_thread(thread_id)`.
  - Add `interrupt_turn(thread_id, turn_id)`.

- [ ] **Step 2: Add handlers**
  - `/status`: existing status report.
  - `/help`: list supported commands.
  - `/compact`: call `thread/compact/start` when a thread exists.
  - `/model`: show current model or switch to a valid Codex model.
  - `/new`: create a new local chat.
  - `/clear`: clear Codex thread state for the current chat.
  - `/stop`: interrupt the active Codex turn.
  - Other slash commands: return a clear unsupported-command answer.

### Task 3: Verification

**Files:**
- Test: `tests/test_main_unit.py`
- Test: `tests/test_codex_client_unit.py`
- Test: `tests/test_codex_ui_responsiveness_automation.py`

- [ ] **Step 1: Run targeted parser and worker tests**
  - `.\.venv\Scripts\python.exe -m pytest tests\test_main_unit.py -k "codex_slash or codex_status_command" -q`

- [ ] **Step 2: Run Codex client tests**
  - `.\.venv\Scripts\python.exe -m pytest tests\test_codex_client_unit.py -q`

- [ ] **Step 3: Run Codex UI automation**
  - `.\.venv\Scripts\python.exe -m pytest tests\test_codex_ui_responsiveness_automation.py -q`
