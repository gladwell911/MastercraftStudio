---
title: 'Story 11: Answer and Execution List Stale Tail Content Fix'
type: 'bugfix'
created: '2026-09-04'
status: 'done'
baseline_revision: 'f4c286dd80083cf0473c35fcfc4bbe68d16e8537'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - 'D:\\code\\sj\\mc\\AGENTS.md'
warnings: []
deferred:
  - summary: >-
      The broad answer/execution/switch unit-test selector remains blocked by an unrelated legacy keyboard-navigation assertion and a wx COM teardown failure.
    evidence: |-
      The first failure is test_answer_key_down_ctrl_right_navigates_history_view, which asserts event.Skip() after a successful history switch; Story 11 does not modify that handler. The selector then terminates during existing wx fixture teardown with Windows code 0x8001010d.
    location: >-
      tests/test_main_unit.py; tests/conftest.py:55
    severity: medium
---

<intent-contract>

## Intent

**Problem:** Incremental answer and execution-list updates can leave an old tail row visible or append it again after a chat change, refresh, or re-entry. That lets the visible desktop list disagree with the current chat or turn.

**Approach:** Make each list reconcile its visible rows and row metadata to the current authoritative chat/turn snapshot at update boundaries, while retaining safe incremental updates only when their target is still the visible source.

## Boundaries & Constraints

**Always:** The answer list must contain only rows derived from the currently visible chat's turns; the execution list must contain only visible entries for that chat and, in active mode, its current turn. Incremental completion, execution-event, chat-switch, refresh, and detail-mode paths must not leave stale or duplicate tail rows. Preserve current Chinese labels, list ordering, selection/focus preservation, row limits, detail opening, persisted history, and background-refresh rules: no UI-thread work, repaint, selection/focus change, or state write when the visible state did not change.

**Block If:** Correctness requires changing the NATS/shared protocol, persisted chat/execution schema, or the Flutter `rc` client.

**Never:** Do not alter reply or execution content, change unrelated history/navigation behavior, rebuild an unchanged ListBox, expose data from another chat/turn, or modify completed Story 1 through Story 10 specifications.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|---------------|----------------------------|----------------|
| Incremental current-chat update | A current turn receives several answers or execution events | Each current row appears once; the final visible tail belongs to the most recent current answer/event | Repeated or stale incremental delivery reconciles to the canonical current rows rather than appending a duplicate |
| Chat/turn change | The user switches chats or starts another turn while prior tail rows exist | The answer and execution lists immediately represent only the selected chat/current turn | Late data for a non-visible chat is stored only with its owner and cannot mutate the visible list |
| Refresh or re-entry | The detail panel is refreshed, toggled, or reopened | Re-rendered rows and metadata exactly match the current authoritative state, with no old tail or cross-list pollution | No-op render leaves ListBox, focus, and selection untouched |

</intent-contract>

## Code Map

- `D:\code\sj\mc\main.py:4510-4707` -- answer row IDs, bounded-page projection, and `_render_answer_list`; this is the canonical answer-list reconciliation point and must keep `answer_meta` aligned with the visible model.
- `D:\code\sj\mc\main.py:4825-5051` -- fast-path submitted/completed-answer append and live answer updates; validate their rows against the current active chat/turn before retaining an incremental tail.
- `D:\code\sj\mc\main.py:5208-5267,5727-5958,6439-6515` -- execution source selection, visible-tail append/deferred flush, and canonical rebuild; preserve turn scoping and prevent stale deferred data from becoming visible after navigation.
- `D:\code\sj\mc\main.py:14599-14692` -- chat switch rehydrates the next chat then resets limits/renders; retain this as the switch boundary rather than leaking previous model metadata.
- `D:\code\sj\mc\listbox_model.py:1-124` -- incremental ListBox identity/selection helper; keep its no-op replacement and selection behavior intact.
- `D:\code\sj\mc\tests\test_main_unit.py:17180-17420,18264-18330,18524-18645` -- existing answer/execution render, current-turn, no-op, and hidden-update test fixtures; extend them with stale-tail/duplicate/cross-chat regressions.
- `D:\code\sj\mc\tests\test_history_ui_automation.py:803-1138` -- wx automation for late answers, history transitions, and execution detail views; extend the visible-surface regression without changing its focus guarantees.

## Tasks & Acceptance

**Execution:**
- `D:\code\sj\mc\main.py` -- reconcile answer and execution list models/metadata with the authoritative visible chat and active turn before or during incremental updates; discard or defer non-visible tails and preserve no-op/focus-safe behavior.
- `D:\code\sj\mc\tests\test_main_unit.py` -- add deterministic regressions for repeated incremental replies/events, chat and turn switching, refresh/re-entry, and exact ListBox/model/meta contents.
- `D:\code\sj\mc\tests\test_history_ui_automation.py` -- exercise the wx visible surface through late/background updates and detail/list transitions, asserting that no old, duplicate, or cross-list tail appears.
- This Story 11 spec -- record implementation, review triage, test evidence, revisions, and residual manual accessibility risk.

**Acceptance Criteria:**
- Given a current chat receives multiple replies and execution entries, when incremental updates complete, then each list tail contains only the current authoritative rows and no previous reply text or duplicate tail row.
- Given the user switches chats or begins a new active turn, when a late update or a normal refresh occurs, then neither list shows a row from the previous chat/turn and each owner retains only its own stored data.
- Given a user refreshes, reopens, or toggles the detail view, when the lists render again, then their visible rows and metadata exactly match the current state without a stale tail, duplicate, or answer/execution cross-list contamination.
- Given a render has no visible-state change, when the reconciliation path runs, then it does not clear/repaint the ListBox or alter the user's selection/focus.

## Design Notes

The rebuild functions already project authoritative state through `IncrementalListBoxModel.replace_visible_page`, which avoids a physical ListBox rebuild when row identities and labels are unchanged. Use that projection as the correctness backstop; fast paths may remain only when they cannot outlive their source chat/turn or drift from their matching metadata.

## Verification

**Commands:**
- `uv run --python 3.12 --with pytest --with wxPython --with markdown --with aiohttp --with requests --with numpy --with websocket-client --with nats-py --with copyparty --with sounddevice python -m pytest tests\test_main_unit.py -k "answer or execution or switch_current_chat" -q` -- expected: current-state, incremental, switch, and no-op list regressions pass.
- `uv run --python 3.12 --with pytest --with wxPython --with markdown --with aiohttp --with requests --with numpy --with websocket-client --with nats-py --with copyparty --with sounddevice python -m pytest tests\test_history_ui_automation.py -k "late or execution or history" -q` -- expected: wx visible-surface transitions preserve focus and exclude stale tails.
- `C:\\Users\\gaope\\AppData\\Local\\Programs\\Python\\Python312\\python.exe -m py_compile main.py` -- expected: changed desktop code compiles.
- `git diff --check` -- expected: no whitespace errors.

## Implementation Record

- Reconciled the answer fast paths against the authoritative active chat turn collection before accepting a submitted, completed, or live-answer update.  The canonical answer projection now replaces any stale or duplicate tail while `IncrementalListBoxModel` keeps genuine no-op renders focus- and repaint-safe.
- Reconciled repeated execution row identities through the canonical execution projection instead of appending a second metadata tail.  Deferred execution tails now rebuild once from their owning visible chat rather than replaying stale append operations.
- Strengthened `IncrementalListBoxModel.replace_visible_page` so its no-op decision also verifies the actual ListBox strings and row count, repairing a physical stale tail even if model metadata was left behind by an interrupted transition.
- Added deterministic answer/execution stale-tail, repeated-completion, cross-turn, and wx detail re-entry regressions.

## Review Triage

- No protocol, persistence-schema, or Flutter-client changes were required.
- The broader Story 11 unit selector currently stops on an unrelated existing keyboard-navigation assertion (`test_answer_key_down_ctrl_right_navigates_history_view` expects `event.Skip()` after a successful history switch).  This change does not touch that handler; focused Story 11 list regressions pass.

## Test Evidence

- Passed: `python -m pytest tests\\test_main_unit.py -k "stale_physical_tail or repeated_current_answer or execution_replay_reconciles" -q` (3 passed).
- Passed: `python -m pytest tests\\test_main_unit.py -k "update_active_answer_row_skips_repaint or appending_execution_entry_keeps_latest or render_answer_list_does_not_clear or render_execution_list_does_not_clear or hidden_execution or submit_question_clears_visible_execution" -q -x` (8 passed).
- Passed: `python -m pytest tests\\test_history_ui_automation.py -k "late or execution or history" -q` (22 passed).
- Passed: `python -m py_compile main.py`; `git diff --check`.

## Residual Manual Accessibility Risk

- wx automation verifies visible strings and model/metadata alignment, but a manual screen-reader check of a rapid chat switch while an execution batch completes remains advisable to confirm the platform's spoken selection announcement stays stable.

## Review Triage Log

### 2026-09-04 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 1: (medium 1)
- defer: 1: (medium 1)
- reject: 10: (low 10)
- addressed_findings:
  - `[medium]` `[patch]` Repaired same-length physical ListBox corruption in `IncrementalListBoxModel.replace_visible_page`; the old metadata-only fast path could retain a stale tail label when the control row count still matched. Added a regression that replaces only the final physical label and verifies canonical rendering restores it.

## Auto Run Result

**Summary:** Answer and execution list updates now reconcile against their visible authoritative state, preventing stale, duplicate, cross-chat, and cross-turn tail rows after incremental updates or re-entry. The ListBox model also verifies physical rows before treating a render as a no-op.

**Files changed:**
- `listbox_model.py` -- makes no-op reconciliation validate actual ListBox labels and repair stale same-length rows.
- `main.py` -- routes authoritative answer updates and repeated/deferred execution tails through canonical current-state reconciliation while retaining focus-safe no-op behavior.
- `tests/test_main_unit.py` -- covers stale physical answer tails, repeated completion, execution replay, and same-length physical corruption.
- `tests/test_history_ui_automation.py` -- covers wx execution-detail re-entry after a non-visible chat update.
- This Story 11 spec -- records plan, review triage, deferred baseline-suite limitation, and verification evidence.

**Review findings:** Applied 1 medium patch; deferred 1 medium pre-existing test-environment issue; rejected 10 low-confidence, duplicate, or already-covered findings. Follow-up review recommendation: `false` (`3 × 1 = 3`).

**Verification:**
- `uv run --python 3.12 --with pytest --with wxPython --with markdown --with aiohttp --with requests --with numpy --with websocket-client --with nats-py --with copyparty --with sounddevice python -m pytest tests\\test_main_unit.py -k "stale_physical_tail or repeated_current_answer or execution_replay_reconciles" -q` -- passed, 4 tests.
- `uv run --python 3.12 --with pytest --with wxPython --with markdown --with aiohttp --with requests --with numpy --with websocket-client --with nats-py --with copyparty --with sounddevice python -m pytest tests\\test_main_unit.py -k "update_active_answer_row_skips_repaint or appending_execution_entry_keeps_latest or render_answer_list_does_not_clear or render_execution_list_does_not_clear or hidden_execution or submit_question_clears_visible_execution" -q -x` -- passed, 8 tests.
- `uv run --python 3.12 --with pytest --with wxPython --with markdown --with aiohttp --with requests --with numpy --with websocket-client --with nats-py --with copyparty --with sounddevice python -m pytest tests\\test_history_ui_automation.py -k "late or execution or history" -q` -- passed, 22 tests.
- `C:\\Users\\gaope\\AppData\\Local\\Programs\\Python\\Python312\\python.exe -m py_compile main.py` -- passed.
- `git diff --check` -- passed (only Git's CRLF conversion notices).
- The specified broad `test_main_unit.py -k "answer or execution or switch_current_chat"` selector was attempted but is deferred as described above; the Story 11-focused matrix coverage ran cleanly.

**Matrix audit:** The incremental row is covered by the repeated-completion and execution-replay regressions; the chat/turn-change row by the execution replay and wx re-entry test; the refresh/re-entry row by both stale-tail tests and the wx re-entry test. All covering tests ran and passed.

**Residual risks:** Automated tests verify ListBox strings, IDs, metadata, and focus-safe no-op behavior. A manual screen-reader rapid-switch/execution-batch check remains advisable for spoken-selection stability.
