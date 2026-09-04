---
title: 'Story 4: Desktop and Mobile Model Display Synchronization'
type: 'bugfix'
created: '2026-09-04'
status: 'done'
baseline_revision: '129d0ed021d7d1878596feef96ecc8b5eeb1ecd4'
review_loop_iteration: 0
followup_review_recommended: true
context:
  - 'D:\\code\\sj\\rc\\AGENTS.md'
  - 'D:\\code\\sj\\mc\\AGENTS.md'
warnings: []
deferred: []
---

<intent-contract>

## Intent

**Problem:** Selecting a model in the desktop model combobox does not commit or publish that selection until a later send or new-chat action. Mobile state processing also discards the authoritative model in a desktop state snapshot, so the mobile menu can remain fixed on `codex` or a stale model.

**Approach:** Treat a supported desktop selection as an authoritative active-chat state change, publish the existing state snapshot, and let mobile retain and apply its exact model identifier to the matching remote session. This must work for at least two non-`codex` desktop models and when switching back to `codex`.

## Boundaries & Constraints

**Always:** Preserve exact supported desktop model IDs over the existing NATS state protocol; unknown optional state fields must remain safely ignorable by older clients. Keep the active chat and persisted desktop selection coherent, publish only after an actual selection change, and do not alter message sending, model-list, history, or backend-routing semantics. On mobile, update only the matching remote session after a state snapshot and preserve local-selection acknowledgement rules. Keep the existing menu's accessible label and display mapping, including `codex` for `codex/main` and verbatim labels for opaque desktop IDs.

**Block If:** The fix requires a breaking NATS wire-format change, cannot retain the exact model ID for an existing supported desktop model, or needs unrelated protocol/UI redesign.

**Never:** Do not modify Story 1 or Story 3, replace the NATS transport, coerce non-Codex IDs into `codex`, dispatch UI work from desktop background polling, or require a message send/new-chat action for the mobile display to synchronize.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|---------------|----------------------------|----------------|
| Desktop non-Codex selection | Active desktop chat changes from `codex/main` to each of two supported non-Codex IDs | The committed desktop state and its next NATS state snapshot carry the exact selected ID; mobile applies it to that chat and its menu displays that model | A legacy/mobile snapshot without `model` retains its current session model |
| Return to Codex | Same desktop chat changes from a supported non-Codex ID to `codex/main` | State synchronization updates mobile's matching session and menu to `codex` | No stale non-Codex label remains |
| Repeated selection | Desktop combobox reports the already selected supported ID | No state persistence or remote state push is performed | Existing display and focus remain unchanged |

</intent-contract>

## Code Map

- `D:\\code\\sj\\mc\\main.py:5049-5054` -- `_on_model_changed` currently only toggles the Codex-speed control; commit supported combo changes to the active chat, persist them, and publish one state update.
- `D:\\code\\sj\\mc\\main.py:9324-9388` -- `_remote_api_state_ui` already serializes the active chat's `model` in its state body; reuse this compatible payload rather than adding a parallel protocol.
- `D:\\code\\sj\\mc\\main.py:8197-8209` -- `_push_remote_state` broadcasts the state body after desktop selection is committed.
- `D:\\code\\sj\\mc\\tests\\test_main_unit.py:17368-17406` -- existing combobox commit/deferred-selection assertions; replace obsolete deferred-selection behavior and cover two non-Codex models, return to Codex, persistence, and state push.
- `D:\\code\\sj\\rc\\lib\\remote_control_models.dart:639-747` -- `RemoteStateSnapshot` parses state payloads but currently drops `model`; add an optional exact model value with an empty legacy fallback.
- `D:\\code\\sj\\rc\\lib\\remote_session_store.dart:503-522` -- `_mergeHistorySnapshot` propagates state status into remote-history entries; apply a non-empty authoritative snapshot model so HomePage session synchronization observes it.
- `D:\\code\\sj\\rc\\lib\\remote_nats_chat_service.dart:1028-1045` -- state events/responses are decoded through `_applyStateBody`; retain the existing route and compatibility fallback.
- `D:\\code\\sj\\rc\\lib\\main.dart:2502-2601,6147-6153` -- HomePage derives matching session model/backend from remote history and the chat menu displays that session model; reuse both without label-map coercion.
- `D:\\code\\sj\\rc\\test\\widget_test.dart:4365-4466` -- remote-store/menu regression scaffold; drive state snapshots for two non-Codex IDs and `codex/main`, then assert exact menu labels.
- `D:\\code\\sj\\rc\\test\\remote_nats_chat_service_test.dart` and/or `test\\remote_session_store_test.dart` -- focused protocol/store coverage for model-bearing and legacy model-less state snapshots.

## Tasks & Acceptance

**Execution:**
- [x] `D:\\code\\sj\\mc\\main.py` -- make an actual supported desktop combobox selection commit the active chat's exact model, preserve relevant speed-control behavior, defer existing persistence safely, and emit the existing state update once.
- [x] `D:\\code\\sj\\mc\\tests\\test_main_unit.py` -- cover two supported non-Codex transitions, return to `codex/main`, exact state-body model, one persistence/state push per change, and no-op repeated selection.
- [x] `D:\\code\\sj\\rc\\lib\\remote_control_models.dart` and `lib\\remote_session_store.dart` -- deserialize optional state-snapshot model IDs and merge a non-empty model into the matching remote history entry while model-less legacy snapshots retain the prior model.
- [x] `D:\\code\\sj\\rc\\test\\remote_nats_chat_service_test.dart` and `test\\widget_test.dart` -- cover the wire/state fallback and the mobile menu after two non-Codex desktop state updates followed by `codex/main`.

**Acceptance Criteria:**
- Given an active desktop chat using `codex/main`, when the user selects each of two supported non-Codex models in turn, then each committed state snapshot carries that exact ID and, after mobile receives it, the corresponding mobile menu displays the same actual model.
- Given the same chat has synchronized a non-Codex model, when desktop selects `codex/main`, then mobile state synchronization makes that chat's menu display `codex` rather than the previous model.
- Given a compatible older desktop state payload has no model field, when mobile applies it, then the existing mobile model display remains unchanged and the snapshot continues to apply its other state fields.
- Given desktop reports the currently selected model again, when the model-change handler runs, then it does not persist or broadcast a redundant state update and focus/display behavior remains stable.

## Verification

**Commands:**
- `uv run --python 3.12 --with pytest --with wxPython --with markdown --with aiohttp --with requests --with numpy --with websocket-client --with nats-py --with copyparty --with lark-oapi --with sounddevice python -m pytest tests\\test_main_unit.py -k "model_changed or remote_state" -q` -- expected: desktop selection commit/state-push and compatible state serialization tests pass.
- `C:\\src\\flutter\\bin\\flutter.bat test test/remote_nats_chat_service_test.dart test/remote_session_store_test.dart` -- expected: exact state-model parsing/merge and legacy fallback pass.
- `C:\\src\\flutter\\bin\\flutter.bat test test/widget_test.dart --plain-name "desktop model state snapshots synchronize exact mobile menu labels"` -- expected: two non-Codex state transitions and Codex return display correctly in the menu.
- `C:\\src\\flutter\\bin\\flutter.bat analyze lib/remote_control_models.dart lib/remote_session_store.dart lib/remote_nats_chat_service.dart lib/main.dart` -- expected: no new analyzer diagnostics in changed Flutter production files.

## Review Triage Log

### 2026-09-04 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 5 (medium 2, low 3)
- defer: 0
- reject: 16 (low 16)
- addressed_findings:
  - `[medium]` `[patch]` Invalidated the desktop history-list cache and publish only for the changed chat ID, avoiding stale history responses and unscoped/mismatched model state events.
  - `[medium]` `[patch]` Asserted the actual desktop NATS publication body, model identity, duplicate-selection suppression, and two supported non-Codex transitions before the Codex return.
  - `[low]` `[patch]` Ignored non-string state `model` values so malformed compatible payloads retain the existing mobile history model.
  - `[low]` `[patch]` Added an unsupported-combobox regression proving it cannot persist or publish an invalid shared model.
  - `[low]` `[patch]` Made the mobile menu regression use the same Google model family exercised by the desktop selection flow.

## Auto Run Result

**Summary:** Desktop combobox selection now commits the active chat's exact supported model, persists it through the existing deferred path, and publishes the existing model-bearing NATS state snapshot. Mobile now retains that optional state model and updates only the matching remote session; a missing or malformed legacy field leaves its prior model intact.

**Files changed:**
- `mc/main.py` and `mc/tests/test_main_unit.py` — scoped desktop model commit/state publication plus exact transport, unsupported-model, duplicate, and Codex-return coverage.
- `rc/lib/remote_control_models.dart` and `rc/lib/remote_session_store.dart` — optional compatible state-model parsing and matching-history merge.
- `rc/test/remote_nats_chat_service_test.dart` and `rc/test/widget_test.dart` — state protocol fallback plus two non-Codex mobile menu displays followed by `codex`.
- This Story 4 spec — plan, code map, completed tasks, review triage, and verification record.

**Review findings:** 5 patches applied (medium 2, low 3); 0 deferred; 16 rejected as non-actionable, duplicated by the applied transport/store coverage, or outside the stated model-display behavior. Follow-up review recommendation: `true` (score `2 × 3 + 3 × 1 = 9`).

**Verification:**
- `C:\\Users\\gaope\\AppData\\Local\\Microsoft\\WinGet\\Packages\\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\\uv.exe run --python 3.12 --with pytest --with wxPython --with markdown --with aiohttp --with requests --with numpy --with websocket-client --with nats-py --with copyparty --with lark-oapi --with sounddevice python -m pytest tests\\test_main_unit.py -k "model_changed or remote_state" -q` — passed, 10 tests.
- `C:\\src\\flutter\\bin\\flutter.bat test test/remote_nats_chat_service_test.dart test/remote_session_store_test.dart` — passed, 53 tests.
- `C:\\src\\flutter\\bin\\flutter.bat test test/widget_test.dart --plain-name "desktop model state snapshots synchronize exact mobile menu labels"` — passed.
- `C:\\src\\flutter\\bin\\flutter.bat analyze lib/remote_control_models.dart lib/remote_session_store.dart lib/remote_nats_chat_service.dart lib/main.dart` — only two pre-existing unused-element warnings in `lib/main.dart:7334` and `lib/main.dart:7362`.
- `C:\\Users\\gaope\\AppData\\Local\\Programs\\Python\\Python312\\python.exe -m py_compile main.py` — passed.

**Residual risks:** The focused tests compose the real desktop publish body, mobile NATS parser/store, and menu surface, but an actual concurrently running desktop plus physical mobile NATS session was not run in this environment. The repository's original `mc/.venv` is unusable because its Python 3.11 base interpreter is missing; verification used an isolated Python 3.12 environment instead.
