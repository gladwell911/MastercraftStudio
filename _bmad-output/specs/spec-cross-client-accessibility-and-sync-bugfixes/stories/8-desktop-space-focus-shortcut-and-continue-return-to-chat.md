---
title: 'Story 8: Desktop Space Focus Shortcut and Continue Returns to Chat'
type: 'bugfix'
created: '2026-09-04'
status: 'done'
baseline_revision: '66b00763532e51a9caebeb86eb862e00927777ba'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - 'D:\\code\\sj\\mc\\AGENTS.md'
warnings: []
deferred: []
---

<intent-contract>

## Intent

**Problem:** In the desktop client, Space on the Notes, history, answer, and model-selection navigation controls does not provide the requested input-focus shortcut and can fall through to each control's default action. The Answer Detail dialog's Continue button sends a new prompt instead of returning users, including screen-reader focus, to the detail surface of its owning chat.

**Approach:** Make an unmodified single Space in precisely those navigation controls perform the same safe focus transfer as Alt+D and consume the event. Make Answer Detail Continue restore the owning chat-detail state and focus the input without creating a request.

## Boundaries & Constraints

**Always:** Preserve the exact existing Alt+D focus result. Handle only an unmodified Space while focus is in the notes notebook list, notes entry list, history list, answer list, or model combo box; consume it before default list/combo behavior. Retain normal Space input/action behavior everywhere else, including the notes editor, input editor, and other combo boxes. Continue must restore the selected answer's owning active or archived chat detail, place focus in the input box, and not submit, mutate, or persist a new question. Preserve keyboard focus stability and do not schedule background UI work or refresh unrelated lists.

**Block If:** Correct ownership restoration requires changing the chat persistence model, the desktop/mobile protocol, or a product decision about what Continue should send.

**Never:** Do not change completed Story 1 through Story 7 specifications, alter model selection, list selection, or normal Space behavior outside the named controls, or redesign Answer Detail.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|---------------|---------------------------|----------------|
| Navigation-space shortcut | Focus is in each named list or model combo; user presses Space once with no modifiers | Input editor receives focus, identically to Alt+D; the control's default Space action does not run | Disabled/missing input safely leaves focus unchanged while the event remains consumed |
| Ordinary Space | Focus is in input editor, notes editor, a different control, or modifiers are held | Existing Space behavior remains available | No shortcut is applied |
| Continue from active/history Answer Detail | User selects an answer from an active or archived chat and clicks Continue | Dialog closes; the owning chat detail becomes visible and input editor receives focus | Missing owner leaves the existing visible detail unchanged and creates no request |

</intent-contract>

## Code Map

- `D:\code\sj\mc\main.py:8678-8726` -- `_focus_input_box` and `_handle_window_focus_shortcut` define the existing safe Alt+D result; reuse rather than duplicate focus logic.
- `D:\code\sj\mc\main.py:12090-12194` -- frame-wide character hook is the central pre-default interception point for the Space shortcut.
- `D:\code\sj\mc\main.py:13992-14025,14345-14373,14896-14906,16150-16220` -- direct list/combo/notes key handlers must also consume Space if they receive it before the character hook.
- `D:\code\sj\mc\main.py:890-960,14298-14361` -- AnswerTextViewerDialog returns `wx.ID_OK` for Continue; capture its owner before display and restore it only after the modal dialog has closed.
- `D:\code\sj\mc\main.py:14404-14440` -- `_show_history_chat` is the established active/history chat-detail restoration path and can avoid its default answer-list focus.
- `D:\code\sj\mc\tests\test_main_unit.py:9640-9677,17220-17269` -- existing Continue and Alt+D behavior tests; extend with all named controls, consumed-event/default-action assertions, and no-submit owner restoration.

## Tasks & Acceptance

**Execution:**
- `D:\code\sj\mc\main.py` -- centralize the named-control/unmodified-Space predicate and consume that shortcut in every relevant keyboard entry point, delegating its focus action to the established input-focus helper.
- `D:\code\sj\mc\main.py` -- capture the Answer Detail owner when opening its viewer and restore that owner through the existing chat-detail path on Continue, then focus input without submitting a prompt.
- `D:\code\sj\mc\tests\test_main_unit.py` -- add focused keyboard and Continue regressions covering every matrix case and protection from default list/combo/editor behavior.

**Acceptance Criteria:**
- Given focus is on each Notes list, history list, answer list, or model combo, when the user presses one unmodified Space, then the input box receives focus with the same observable result as Alt+D and no list default action, model change, or inserted space occurs.
- Given focus is outside those named controls or Space has modifiers, when Space is pressed, then the control retains its existing Space behavior.
- Given a user clicks Continue in Answer Detail for an answer from an active or archived chat, when the dialog closes, then the corresponding chat detail is visible and input focus is restored without submitting a new message.

## Spec Change Log

## Review Triage Log

### 2026-09-04 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 8: (medium 6, low 2)
- defer: 0
- reject: 8: (low 8)
- addressed_findings:
  - `[medium]` `[patch]` Consumed the recognized Space shortcut before navigation-quiet scheduling, preventing a focus-only action from enqueueing background work.
  - `[medium]` `[patch]` Verified an owner exists before `_show_history_chat`, so a removed archived chat cannot flush execution deltas or disturb the visible detail.
  - `[medium]` `[patch]` Added a nonempty unavailable-owner regression, rather than only testing the early empty-ID return path.
  - `[medium]` `[patch]` Made `_show_history_chat` recognize the current-state active ID, matching the captured-owner fallback and preserving active-detail restoration during transient ID state.
  - `[medium]` `[patch]` Tested the actual modal `wx.ID_OK` flow after dialog close, including active and archived owner capture without submission.
  - `[medium]` `[patch]` Replaced the history synthetic event with a native Space-key UI automation regression that proves focus moves without history activation.
  - `[low]` `[patch]` Covered recognized Space consumption when the input control is disabled.
  - `[low]` `[patch]` Confirmed input focus is restored only after the modal viewer returns, avoiding modal-parent focus loss.

## Design Notes

The shortcut is intentionally scoped by the currently focused control instead of globally remapping Space. This preserves text editing and normal control behavior while preventing list activation and combo changes in the named navigation surfaces. Continue is a navigation operation, so it restores its captured owner rather than routing through the question-submission workflow.

## Verification

**Commands:**
- `uv run --python 3.12 --with pytest --with wxPython --with markdown --with aiohttp --with requests --with numpy --with websocket-client --with nats-py --with copyparty --with lark-oapi --with sounddevice python -m pytest tests\\test_main_unit.py -k "space or answer_text_viewer_continue or window_focus_shortcuts" -q` -- expected: all focused keyboard and Continue behavior tests pass.
- `uv run --python 3.12 --with pytest --with wxPython --with markdown --with aiohttp --with requests --with numpy --with websocket-client --with nats-py --with copyparty --with lark-oapi --with sounddevice python -m pytest tests\\test_history_ui_automation.py -k "space_from_history" -q` -- expected: affected desktop UI automation confirms focus transfer without history activation.
- `C:\\Users\\gaope\\AppData\\Local\\Programs\\Python\\Python312\\python.exe -m py_compile main.py` -- expected: changed desktop source compiles.
- `git diff --check` -- expected: no whitespace errors.

## Auto Run Result

**Summary:** Implemented the desktop Story 8 accessibility/navigation fixes. An unmodified Space on either Notes list, the history list, the answer list, or the model combo now has the same input-focus result as Alt+D and is consumed before native default behavior. Answer Detail Continue now closes the modal dialog, restores its captured active or archived chat detail, and focuses input without submitting a new message.

**Files changed:**
- `main.py` -- added the scoped Space shortcut and safe owner restoration after the Answer Detail modal closes.
- `tests/test_main_unit.py` -- covered all named controls, outside/modifier behavior, disabled input, owner capture/restoration, unavailable owners, and no submission.
- `tests/test_history_ui_automation.py` -- added a native history-list Space-key focus/activation regression.
- `tests/test_codex_ui_responsiveness_automation.py` -- updated the AnswerTextViewerDialog constructor call for the modal-result API.
- This Story 8 spec -- recorded planning, review triage, and verification evidence.

**Review findings:** Applied 8 patches (medium 6, low 2); deferred 0; rejected 8. Follow-up review recommendation: `true` (score `3 × 6 + 2 = 20`).

**Verification:**
- `uv run --python 3.12 --with pytest --with wxPython --with markdown --with aiohttp --with requests --with numpy --with websocket-client --with nats-py --with copyparty --with sounddevice python -m pytest tests\\test_main_unit.py -k "space or answer_text_viewer_continue or window_focus_shortcuts" -q` -- passed, 23 tests.
- `uv run --python 3.12 --with pytest --with wxPython --with markdown --with aiohttp --with requests --with numpy --with websocket-client --with nats-py --with copyparty --with sounddevice python -m pytest tests\\test_history_ui_automation.py -k "space_from_history" -q` -- passed, 1 test.
- `uv run --python 3.12 --with pytest --with wxPython --with markdown --with aiohttp --with requests --with numpy --with websocket-client --with nats-py --with copyparty --with sounddevice python -m pytest tests\\test_codex_ui_responsiveness_automation.py -k "answer_viewer_keeps_single_lines" -q` -- passed, 1 test.
- `C:\\Users\\gaope\\AppData\\Local\\Programs\\Python\\Python312\\python.exe -m py_compile main.py` -- passed.
- `git diff --check` -- passed.

**Residual risks:** Focus behavior is covered with wx tests and native history-list key delivery, but final TalkBack/NVDA-style physical screen-reader confirmation across all five named controls remains a manual-device check.
