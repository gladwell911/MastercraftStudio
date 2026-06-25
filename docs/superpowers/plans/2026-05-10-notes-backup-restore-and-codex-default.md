# Notes Backup Restore And Codex Default Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make first packaged startup default to Codex and add accessible file-based notes backup and restore.

**Architecture:** Keep the UI work in `main.py` next to the existing notes menu and import/export handlers. Put backup serialization and merge logic in a small helper module so duplicate handling is testable without a wx frame.

**Tech Stack:** Python, wxPython, pytest, existing `NotesStore` document cache APIs.

---

### Task 1: Codex Startup Default

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_unit.py`

- [ ] Write a failing test that asserts a fresh `ChatFrame` selects `codex/main` and displays `codex`.
- [ ] Run `pytest tests/test_main_unit.py::test_fresh_startup_defaults_to_codex_model -q` and confirm it fails with the current `openai/gpt-5.2` default.
- [ ] Change `DEFAULT_MODEL_ID` to `DEFAULT_CODEX_MODEL`.
- [ ] Re-run the targeted test and confirm it passes.

### Task 2: Notes Backup Serialization And Merge

**Files:**
- Create: `notes_backup.py`
- Test: `tests/test_notes_desktop_unit.py`

- [ ] Write failing tests for exporting all notebooks/entries to JSON and restoring a same-title notebook while skipping exact duplicate entry content.
- [ ] Run the targeted tests and confirm they fail because `notes_backup` does not exist.
- [ ] Implement `export_notes_backup(store, path)` and `restore_notes_backup(store, path)`.
- [ ] Re-run the targeted tests and confirm they pass.

### Task 3: Notes Application Menu Actions

**Files:**
- Modify: `main.py`
- Test: `tests/test_notes_ui_automation.py`

- [ ] Write failing UI automation tests that capture the notes Application menu and invoke “导出所有笔记” and “恢复笔记”.
- [ ] Run the targeted tests and confirm the menu items are missing.
- [ ] Add menu entries in `_show_notes_menu`, plus `_notes_export_all_to_file()` and `_notes_restore_from_backup_file()` using `wx.FileDialog`.
- [ ] Ensure restore refreshes UI once and calls `_notes_after_local_mutation()` only when new notes or entries are imported.
- [ ] Re-run the targeted UI automation tests and confirm they pass.

### Task 4: Regression Verification

**Files:**
- Test only.

- [ ] Run `pytest tests/test_main_unit.py tests/test_notes_desktop_unit.py tests/test_notes_ui_automation.py -q`.
- [ ] Run the relevant real UI automation command for notes and model startup on the local machine.
- [ ] Inspect failures before claiming completion.
