---
title: 'Preserve archived mobile message results during execution-step hydration'
type: 'bugfix'
ticket: ''
created: '2026-09-29'
status: 'in-review'
baseline_revision: '79ddee5456af8acc1902096c91b8efe1d6717b17'
route: 'full'
route_source: 'auto'
review: 'thorough'
review_source: 'auto'
lenses_ran: ['blind-hunter', 'edge-case-hunter']
review_loop_iteration: 0
followup_review_recommended: false
context:
  - 'D:/code/sj/mc/AGENTS.md'
  - 'D:/code/sj/mc/_bmad-output/implementation-artifacts/plan-fix-archived-mobile-message-result-loss.md'
warnings: []
deferred: []
---

<intent-contract>

## Intent

**Problem:** A phone message to a background archived chat reaches the desktop and starts Codex, but its new thread identity and final answer can disappear when execution events hydrate that chat from older SQLite state. The request can remain pending while the active chat succeeds.

**Approach:** Load missing execution steps without replacing a populated in-memory chat or its turns. Keep summary hydration in place, then verify complete remote submit, worker acknowledgment, event, persistence, and desktop UI flows across multiple chats.

## Boundaries & Constraints

**Always:** Preserve chat and turn object identity, current runtime fields, owner/turn/generation guards, incremental persistence, provider dispatch, and foreground draft/focus/selection. Use temporary ChatStore data in tests. Distinguish repaired future messages from old production pending records.

**Never:** Relax stale-event validation, force synchronous whole-chat saves, mutate production history or notes, resend old questions, or replace the installed package during candidate build.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|---------------|----------------------------|----------------|
| Background Codex response | B/C have pending phone turns and new worker IDs in memory; SQLite has old IDs | Each final answer becomes done under its own chat and survives reopen | Ignore stale or wrong-owner events |
| Lazy step hydration | Populated archived chat lacks `execution_steps` | Load only steps and retain chat/turn references and live fields | Closed/missing store leaves state intact |
| Summary hydration | Archived entry has no turns | Fill missing durable data in place; preserve valid live summary changes | Missing chat remains unchanged |
| Foreground navigation | A has draft/focus/selection while B/C complete | Foreground stays stable; browsing B shows B's result | No offscreen redraw steals focus |

</intent-contract>

## Code Map

- `main.py:3984` `_hydrate_chat_from_store` currently replaces `archived_chats[idx]` with `store.load_chat`, losing worker acknowledgments before delayed save. Use `ChatStore.load_execution_steps` for populated turns; merge summary hydration in place.
- `main.py:6634` `_chat_state_for_execution_steps` calls hydration from event handling. `main.py:15290` applies worker thread state and defers save; `main.py:12734` validates event identity. Preserve these gates.
- `main.py:17648` `_submit_archived_remote_question` loads turns without steps, appends pending turn, and starts provider. Do not alter dispatch without evidence.
- `chat_store.py:1306,1932` has full chat and step-only reads. Reuse them; avoid schema changes.
- `tests/test_main_unit.py:19773` has remote owner and store-backed submission tests; extend with deterministic unflushed ack/step/final lifecycle and multiple owners.
- `tests/test_codex_ui_responsiveness_automation.py` has real wx focus/navigation tests. Extend only as needed for background B/C and foreground A.

## Tasks & Acceptance

**Execution:**
- [x] `tests/test_main_unit.py` -- add failing ChatStore-backed race test before code change, then cover summary hydration, reorderings, B/C interleave, stale guards, final persistence and reopen -- prove the observed failure and repair.
- [x] `main.py` -- hydrate steps alone for populated chats and merge summary data in place -- prevent live identity rollback.
- [x] `tests/test_codex_ui_responsiveness_automation.py` -- exercise B/C event lifecycle while foreground A has draft, focus and list selection; cover browsing B and keyboard navigation -- preserve accessibility.
- [ ] `tests/test_main_remote_nats_unit.py` -- add targeted final/state/history ownership assertion only if existing transport coverage leaves a gap -- verify remote projection.
- [ ] `package_mc.ps1` -- inspect output behavior, then create an isolated candidate package if tests and review pass -- avoid installed package/data.

**Acceptance Criteria:**
- Given A active and B/C archived, when each receives a unique phone request and Codex completion in interleaved order, then B/C each retain their own new identity and final answer after SQLite reopen while A and old turns remain unchanged.
- Given a populated archived chat with unflushed worker ack and older SQLite identity, when the first execution event loads steps, then the same chat and turn objects keep the new identity and later final/complete events are accepted.
- Given stale turn, wrong owner, expired generation, or cleared context, when an event arrives, then the existing guard rejects it.
- Given A's draft, focus, selection and keyboard navigation, when B/C finish offscreen, then the foreground state stays stable and viewing B shows B's final result.
- Given a built candidate, when inspected, then its files are under a new isolated path and production history/notes remain untouched; physical phone validation remains separately identified if unavailable.

## Implementation Notes

- Reproduced the reported race with a real temporary ChatStore: an unflushed worker acknowledgment was overwritten by the old whole-chat hydration. The new implementation reads only execution steps whenever the in-memory `turns` key contains a list, including an empty list.
- Summary-only entries now receive missing durable fields in place. Store errors leave live state untouched.
- Added B/C interleaved completion and reopen coverage, wrong-owner/stale-turn checks, and a wx focus/selection/navigation lifecycle test. Corrected one pre-existing timestamp assertion: Unix time `1.0` is valid under the documented UI contract.
- Initial candidate build was made before the empty-turns edge fix and must be replaced after review. It is isolated at `D:\code\cx\mc-archived-message-fix-candidate-20260929`.

## Plan Change Log

## Review Triage Log

## Verification

**Commands:**
- `.\.venv\Scripts\python.exe -m pytest tests/test_main_unit.py -k archived_mobile_result -q` -- new regression cases collected and pass.
- `.\.venv\Scripts\python.exe -m pytest tests/test_main_unit.py -k "offscreen_remote_submit or remote_archived_owner_submission or hydrate_chat_from_store" -q` -- related behavior passes.
- `.\.venv\Scripts\python.exe -m pytest tests/test_remote_session_authority.py tests/test_remote_model_dispatch.py tests/test_main_remote_nats_unit.py tests/test_codex_worker_protocol.py tests/test_codex_worker_client.py tests/test_codex_worker_process.py -q` -- routing/protocol pass.
- `.\.venv\Scripts\python.exe -m pytest tests/test_codex_ui_responsiveness_automation.py tests/test_history_ui_automation.py -q` -- serial wx navigation/focus pass.

## Auto Run Result

Status: blocked
Blocking condition: no subagents. The thorough review requires four independent reviewers to launch together, but the shared thread had only two free concurrent agent slots. The third launch returned `agent thread limit reached`; the two started reviewers were interrupted without collecting findings. The required review, candidate rebuild after the empty-turns fix, and finalization remain unfinished.

- Implemented in-place archived-chat hydration and added real ChatStore lifecycle, identity, missing-store, summary, and wx focus/navigation tests. A separate stale timestamp assertion was corrected against the documented `wechat_time_label` behavior.
- The original race test failed on old code before the fix. After the fix, verification passed: `archived_mobile_result` 7 tests; related store/remote 3; routing/protocol 95; serial wx 53. The wx run emitted a Windows COM exception trace mid-run but returned exit code 0 with all 53 tests passing.
- Matrix audit: background B/C completion and wrong-owner rejection, lazy steps with unavailable store, summary load with missing chat, and foreground navigation each have passing covering tests in those commands.
- The candidate at `D:\code\cx\mc-archived-message-fix-candidate-20260929\dist\mc` predates the final empty-turns and test corrections, so it must be rebuilt before delivery. Physical phone verification and old production pending-record recovery were not performed.
