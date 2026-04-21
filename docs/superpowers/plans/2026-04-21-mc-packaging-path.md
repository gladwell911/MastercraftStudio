# MC Packaging Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make packaged builds default to `C:\code\cx\mc\mc.exe` and align tests and packaging docs with that path.

**Architecture:** Keep the existing PyInstaller layout and output directory model, but rename the packaged app from `ms` to `mc` in `zgwd.spec`. Update the focused path assertion in the unit tests and revise the packaging plan document so the documented default matches the build output.

**Tech Stack:** Python 3.11, pytest, PyInstaller

---

### Task 1: Lock the new default package name with a test

**Files:**
- Modify: `tests/test_main_unit.py`
- Test: `tests/test_main_unit.py`

- [ ] Add a focused test that reads `zgwd.spec` and asserts both PyInstaller `name='mc'` entries are present.
- [ ] Run `pytest tests/test_main_unit.py -k packaging_name -v` and confirm it fails before the spec change.

### Task 2: Rename the packaged app from `ms` to `mc`

**Files:**
- Modify: `zgwd.spec`

- [ ] Change both PyInstaller `name='ms'` values to `name='mc'`.

### Task 3: Align tests and docs with the new output path

**Files:**
- Modify: `tests/test_main_unit.py`
- Modify: `docs/superpowers/plans/2026-04-14-sibling-history-packaging.md`

- [ ] Update the frozen executable path assertion from `C:\code\cx\ms\ms.exe` to `C:\code\cx\mc\mc.exe`.
- [ ] Update the packaging plan document to describe `C:\code\cx\mc\mc.exe` and `mc.exe` as the default packaged output.

### Task 4: Rebuild and verify output

**Files:**
- Verify: `C:\code\cx\mc\mc.exe`

- [ ] Run `.\.venv\Scripts\python.exe -m PyInstaller -y --clean --distpath C:\code\cx --workpath build_pyinstaller zgwd.spec`.
- [ ] Confirm the command exits with code `0`.
- [ ] Confirm `C:\code\cx\mc\mc.exe` exists and record its timestamp and size.
