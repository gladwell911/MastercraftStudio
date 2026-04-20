# Chat Title Auto Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep `心聊天` as the current-session placeholder title while ensuring new chats are automatically renamed and default titles do not persist in history unless no usable first-question topic exists.

**Architecture:** Consolidate title generation and title application into one shared path in `main.py`. Route first-question async naming, archived-chat async rename, and `_on_done()` fallback naming through the same guardrails so only default titles can be upgraded to `auto`, while manual titles remain authoritative.

**Tech Stack:** Python 3, wxPython, pytest

---

### Task 1: Lock In Unified Auto-Title Behavior With Failing Tests

**Files:**
- Modify: `tests/test_main_unit.py`
- Modify: `tests/test_remote_api_integration.py`

- [ ] Add red tests for local fallback title generation, archived async rename, `_on_done()` unified metadata updates, and manual title metadata.
- [ ] Run the focused main-unit slice and confirm red failures are due to missing production behavior.
- [ ] Run the remote regression slice and confirm it still passes.

### Task 2: Implement Unified Title Generation And Application

**Files:**
- Modify: `main.py`

- [ ] Add a shared resolver for auto titles using model-first generation plus local fallback.
- [ ] Add a shared apply helper that updates title metadata only when the chat still has a default title and is not manual.
- [ ] Update `_schedule_first_question_auto_title()` to use the shared helpers.
- [ ] Implement `_schedule_async_archive_rename()` with the shared flow.
- [ ] Update `_on_done()` to use unified title resolution/application instead of direct archived title assignment.
- [ ] Route manual rename through the authoritative title revision helper.
- [ ] Run the focused unit slice and confirm it passes.

### Task 3: Regress Existing History, Remote, And Background Naming Flows

**Files:**
- Modify: `tests/test_main_unit.py`
- Modify: `tests/test_remote_api_integration.py`
- Modify: `main.py` if follow-up fixes are required

- [ ] Run the existing local regression slice for background naming and history rename.
- [ ] Run the remote regression slice for default/manual title priority behavior.
- [ ] Run the broader title/rename test bundle and compare against base-branch behavior.
- [ ] Fix any regressions introduced by the new unified flow, including latch recovery and model snapshot stability.
- [ ] Review the final diff for scope control.
