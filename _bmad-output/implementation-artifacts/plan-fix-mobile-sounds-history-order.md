---
title: 'Restore desktop sounds and recency order for mobile messages'
type: 'bugfix'
ticket: ''
created: '2026-09-29'
status: 'built'
baseline_revision: 'eaae195405e6f17f6cbb75f9c51b44634d3565d5'
route: 'full'
route_source: 'auto'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
followup_review_recommended: true
context:
  - 'D:/code/sj/mc/AGENTS.md'
  - 'D:/code/sj/mc/_bmad-output/implementation-artifacts/analysis-mobile-sounds-history-order-20260929.md'
warnings: ['multiple-goals']
deferred: []
---

<intent-contract>

## Intent

**Problem:** Mobile requests to archived chats reach MC but do not play the desktop send sound, Codex background completions omit the reply sound, and recent mobile chats fail to move up the desktop history list. The current-first sort and unconsumed deferred reorder can leave list order stale even after a refresh.

**Approach:** Apply accepted-request and first valid completion sound effects at their owner-scoped paths. Use one pinned/recency ordering across full history, incremental updates, and adjacent keyboard navigation, with a bounded idle reorder that preserves selected chat identity and foreground focus.

## Boundaries & Constraints

**Always:** Keep sound counts tied to accepted request and first authorized completion; retain owner/turn/generation and clear/resend guards. Preserve active chat, draft, insertion point, selection by chat ID, and navigation focus. Use temporary stores and controlled providers for QA.

**Never:** Play sounds for rejected or stale events, duplicate existing Kimi/generic/active sounds, redraw on unchanged polling, touch production MC/history/notes, or claim audible hardware/phone verification from spies.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|---------------|----------------------------|----------------|
| Archived RC submit | B archived, A active, unique phone request | One send sound; B moves by recency without changing A or selected chat ID | Empty/rejected/fenced request stays silent and ordered as before |
| Codex completion | B valid ack/final/complete | One reply sound and stored final; later duplicate complete stays silent | Wrong owner, old generation, clear-before-result stay silent |
| Provider comparison | Active A, archived Kimi and generic owners | Existing completion sounds remain exactly once | No shared hook duplicates sounds |
| History navigation | P pinned; B/C recency changes during focus/navigation | P first, latest ordinary next; keyboard order matches displayed order after bounded quiet interval | No stable focus/selection loss or idle polling loop |

</intent-contract>

## Code Map

- `main.py:17658` `_submit_archived_remote_question` accepts and timestamps a remote archived turn but neither plays send sound nor schedules desktop history reorder. Add effects after provider acceptance/rollback boundary.
- `main.py:12744–12860` `_on_codex_event_for_chat` handles archived Codex completion and returns before the active sound path. Use status transition of the matched turn to avoid duplicate reply sound; preserve clear/identity gates.
- `main.py:4798–4920` `_refresh_history`, `_history_chat_sort_key`, `_desired_history_index`, `_upsert_history_row` differ on current-first order and deferred move. Reuse a single pinned/recency key and preserve selection by ID.
- `main.py:5908–5970` `_primary_navigation_control_is_recently_active` and `_flush_idle_ui_refreshes` govern quiet refresh; zero interaction and `_pending_history_reorder` currently can stall forever. Consume finite deferred work.
- `main.py:19921` `_get_all_chat_ids_in_order` drives adjacent chat shortcuts; align with visible order.
- `tests/test_main_unit.py:19773,11959` include archived remote and deferred reorder tests whose old assumptions need updating. `tests/test_history_ui_automation.py` and `tests/test_codex_ui_responsiveness_automation.py` provide real wx focus/navigation fixtures; `tests/test_main_remote_nats_unit.py` covers remote routes.
- `scripts/nats_e2e_desktop_harness.py` is isolated strict-V2 fixture. Any QA E2E must use isolated Local config/data and controlled providers; never attach production transport.

## Tasks & Acceptance

**Execution:**
- [x] `tests/test_main_unit.py` -- add failing `remote_sound_history` cases for accepted/rejected sends, Codex once-only completion, pinned/recency ordering, deferred focus behavior, and provider comparison; revise stale index-based assertions -- establish defect and guards.
- [x] `main.py` -- add missing owner-scoped sound effects and unify history order/reorder scheduling -- fix real desktop behavior without changing request authority.
- [x] `tests/test_history_ui_automation.py` and `tests/test_codex_ui_responsiveness_automation.py` -- exercise actual list focus, Home/End, selected ID, draft/selection, browsing B, finite idle reorder and unchanged polling -- validate accessible UI.
- [x] `tests/test_main_remote_nats_unit.py` or a focused isolated E2E test -- run real RC route for A/B/C with controlled Codex/Kimi/generic callbacks, verify sounds, ordering, final persistence and owner-scoped remote result -- meet QA end-to-end request without production access.
- [x] `package_mc.ps1` -- inspect path cleanup and build a new candidate outside the repo after verification; confirm executables and no bundled history -- provide reviewable package without touching live MC.

**Acceptance Criteria:**
- Given A active and B/C archived, when the controlled RC route accepts unique B/C requests and their valid completions, then sounds occur once per accepted send/completion, answers persist under each owner, and recency order moves B/C without activating them.
- Given pinned P and recently updated ordinary B, when history is fully refreshed or incrementally updated, then P precedes B, and keyboard adjacent navigation follows the same ID order.
- Given keyboard focus and a draft/selection in A, when B updates during navigation, then reorder completes after a finite quiet interval and preserves focused control, draft, insertion point, and selected chat ID.
- Given rejected submission, duplicate/stale completion, wrong owner/generation, or cleared context, when processed, then no success sound, answer resurrection, or history promotion occurs.
- Given an isolated candidate build, when inspected, then it contains current source and required executables while installed MC and production data remain untouched; audible device and physical phone checks remain separately reported if unavailable.

## Implementation Notes

- Added accepted archived RC send and first valid archived Codex completion sounds at their owner-scoped paths. Rejected worker starts restore prior recency; duplicate completion and stale/cleared results remain silent.
- Unified full, incremental, and adjacent-navigation history order by pinned state and recency. Idle refresh consumes deferred reorder after a finite navigation quiet interval while retaining selected ID, draft, insertion point, and focus. Only recency-changing background events dirty the history list; feedback polling does not.
- Added an isolated Local RC → wx desktop → durable ChatStore/outbox end-to-end test for B/C interleaving. Provider comparison uses the existing generic and Kimi completion paths with exact sound counts.
- Inspected `package_mc.ps1` and rebuilt the reviewed candidate at `D:/code/mc-candidate-mobile-sound-history-reviewed-20260929/mc` after the review fixes. Executables and sound assets are present; the package has no `history` or `.codex-home` directory. Production MC and its data were not accessed.
- Extra broad `tests/test_main_unit.py` run: 962 passed, 25 failed, 1 warning. The related Codex background feedback scheduling regression found in the earlier 26-failure run was fixed; its precise regression test and all plan verification commands pass. The remaining broad-suite failures are outside the plan verification set and are recorded as an unresolved suite limitation, without treating them as passing.

## Plan Change Log

## Review Triage Log

### 2026-09-29 — Review pass
- verdicts: 2 findings — high 2, medium 0, low 0, false 0, maybe-false 0
- findings:
  - `[high]` `[patch]` `main.py:12825` archived final/subagent content for a cancelled clear operation can update recency and now schedule history promotion before the completion gate. Verified `_accept_clear_operation_result` ran only on `turn_completed`; fixed by checking clear authority before archived final/subagent/completion effects. Parametrized cancelled-clear regressions pass.
  - `[high]` `[patch]` `main.py:12791` an archived completion carrying an old context generation can target a pending turn by index/ID, mark it done, promote history, and play reply audio. Verified compatibility checks IDs but not the event generation; fixed by rejecting a supplied generation that differs from the targeted turn or chat generation. Old-generation regression passes.

## Verification

**Commands:**
- `.\.venv\Scripts\python.exe -m pytest tests/test_main_unit.py -k "remote_sound_history or remote_archived_owner_submission or offscreen_remote_submit" -q` -- related targeted cases collected and pass.
- `.\.venv\Scripts\python.exe -m pytest tests/test_main_remote_nats_unit.py tests/test_remote_session_authority.py tests/test_remote_model_dispatch.py -q` -- RC transport and model ownership pass.
- `.\.venv\Scripts\python.exe -m pytest tests/test_history_ui_automation.py tests/test_codex_ui_responsiveness_automation.py -q` -- serial real wx history/focus pass.
- `.\.venv\Scripts\python.exe -m pytest tests/test_remote_sound_history_e2e.py -q` -- isolated Local RC/desktop end-to-end cases pass.
- `.\.venv\Scripts\python.exe -m pytest tests/test_kimi_integration.py::test_interleaved_same_turn_id_routes_by_session -q` -- archived Kimi and active completion each sound once; replay stays silent.

## Auto Run Result

- Summary: Restored accepted mobile-message send sounds and first authorized background Codex reply sounds; desktop history now uses pinned/recency order and completes deferred reorder after navigation becomes idle.
- Files changed: `main.py` (owner-scoped audio, guarded result authority, history order/idle refresh); `tests/test_main_unit.py` (rejected/stale/clear, recency, focus regressions); `tests/test_main_remote_nats_unit.py` and `tests/test_remote_sound_history_e2e.py` (isolated RC/outbox and provider audio); `tests/test_history_ui_automation.py` and `tests/test_codex_ui_responsiveness_automation.py` (real wx focus/navigation); `tests/test_kimi_integration.py` (Kimi exact audio count); this plan (implementation and review evidence).
- Review: quick lens found two high issues. Both patched and covered by regressions; none deferred or rejected. Patched counts: high 2, medium 0, low 0. Follow-up review recommended because the new authority guards have not been verified with a physical phone and audio device.
- Verification after review fixes: targeted unit 9 passed; remote NATS/session/model 36 passed; wx history/responsiveness 53 passed; isolated Local RC end-to-end 1 passed; Kimi provider comparison 1 passed. `git diff --check` passed. A broader pre-review `tests/test_main_unit.py` run had 962 passed and 25 failed; the related feedback scheduling regression was fixed, while those 25 failures remain an unresolved suite limitation.
- Candidate: `D:/code/mc-candidate-mobile-sound-history-reviewed-20260929/mc` built with PyInstaller 6.19.0. `mc.exe` SHA-256 `10845A4B8DDBE473932BDD72AF27D5B538DCB01FADC231BE8D0DFA177853CD48`; `mc_worker.exe` SHA-256 `3A9D8E5A9992142B9059282405E59CE5E579D777F39CB4293C76D6416CA96A38`. `send.wav` and `reply.wav` present; no bundled `history` or `.codex-home`. The running production instance and its data were not changed.
- Residual risks: Audible device and physical-phone checks were unavailable in the isolated QA. The official installation requires a separate switch that preserves its `history` and `.codex-home` data.
