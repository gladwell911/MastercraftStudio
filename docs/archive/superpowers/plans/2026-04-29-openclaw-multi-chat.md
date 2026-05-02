# OpenClaw Multi-Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make OpenClaw support multiple independent app chats using per-chat OpenClaw session ids and files.

**Architecture:** Keep one OpenClaw agent (`main`) and bind each app chat to its own OpenClaw `session_id`. Resolve sync files by stored file first, then by matching `sessionId` in `sessions.json`, with default-key fallback only for legacy state.

**Tech Stack:** Python, wxPython app state in `main.py`, OpenClaw JSONL session sync helpers in `openclaw_client.py`, pytest.

---

### Task 1: Session Pointer Lookup

**Files:**
- Modify: `openclaw_client.py`
- Test: `tests/test_openclaw_client_unit.py`

- [ ] Add a failing test for resolving a pointer by `sessionId`.
- [ ] Implement `load_session_pointer_by_session_id(sessions_json_path, session_id)`.
- [ ] Run the targeted OpenClaw client tests.

### Task 2: Per-Chat OpenClaw Session Lifecycle

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_unit.py`, `tests/test_openclaw_new_chat.py`

- [ ] Add failing tests that OpenClaw New Chat archives the current chat and creates a fresh app chat without sending `/new`.
- [ ] Change `_ensure_active_openclaw_session_id()` to generate a per-chat id instead of adopting `agent:main:main`.
- [ ] Change `_on_new_chat_clicked()` so OpenClaw follows the general new-chat path.
- [ ] Run targeted main/OpenClaw new-chat tests.

### Task 3: Session-Id-Based Sync

**Files:**
- Modify: `main.py`
- Test: `tests/test_openclaw_integration.py`, `tests/test_openclaw_e2e.py`

- [ ] Add failing tests that sync reads the current chat's stored session file even when `sessions.json` points to another session.
- [ ] Add fallback lookup by active OpenClaw `session_id` when the file is unknown.
- [ ] Preserve file, offset, and event metadata on archive/switch/restart.
- [ ] Run OpenClaw integration and e2e tests.

### Task 4: Regression Verification

**Files:**
- Verify: `openclaw_client.py`, `main.py`, OpenClaw tests

- [ ] Run all OpenClaw tests.
- [ ] Run the CLI/chat regression subset.
- [ ] Run `py_compile` for changed Python files.
- [ ] Check `git diff --stat` and `git status --short --branch`.
