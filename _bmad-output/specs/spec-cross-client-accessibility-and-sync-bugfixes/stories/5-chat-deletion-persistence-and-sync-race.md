---
title: 'Story 5: Chat Deletion Persistence and Sync Race'
type: 'bugfix'
created: '2026-09-04'
status: 'done'
baseline_revision: '9ec5b54a0293a48b486cbba5df0a0f3bebf805d7'
review_loop_iteration: 0
followup_review_recommended: true
context:
  - 'D:\\code\\sj\\rc\\AGENTS.md'
warnings: []
deferred: []
---

<intent-contract>

## Intent

**Problem:** Mobile chat deletion only removes the current in-memory row. A later remote-history refresh, synchronization event, notification, or app restart can project the same remote chat again, and concurrent updates can create duplicate visible rows.

**Approach:** Persist a mobile-local tombstone for each deleted remote chat before updating the visible list. Treat that tombstone as authoritative for the mobile projection of remote history, while retaining the existing compatible desktop/NATS history protocol and ordinary local-chat behavior.

## Boundaries & Constraints

**Always:** A deleted mobile remote chat must disappear immediately and remain hidden after list navigation, background/history refresh, remote synchronization, notification handling, and process restart. Record the normalized remote chat ID before asynchronous persistence or refresh work can race it. Filter only the mobile presentation/projection, keep the remote store and existing NATS `history_list`/`history_changed` payloads compatible, avoid duplicate visible sessions, and clear any saved last-chat restore target for a deleted remote chat. Keep Chinese UI and accessibility feedback unchanged except for the existing successful deletion feedback.

**Block If:** Meeting the acceptance criteria requires a breaking desktop/NATS protocol change, a remote server deletion command with no compatible endpoint, or a product decision about restoring intentionally deleted mobile chats.

**Never:** Do not change Story 1, Story 3, or Story 4 specs; do not change desktop chat-deletion semantics, mutate/trim received remote history protocol data, or allow a stale remote/cache result to re-create a tombstoned chat.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|---------------|----------------------------|----------------|
| Single remote delete | A listed remote chat is deleted from the mobile chat list | The row disappears immediately; its normalized remote ID is tombstoned before asynchronous work | Preference failures retain the in-memory tombstone for the running app |
| Refresh/sync race | Delete occurs while an older `history_list`, history-change event, or cache refresh returns that chat | The returned remote chat remains hidden and no duplicate row is shown | The underlying compatible remote history remains processable |
| Restart/notification | The app restarts or receives a notification for a tombstoned chat | Persisted tombstone prevents list/notification recreation and saved detail restoration | Missing/malformed legacy preference behaves as no tombstones |

</intent-contract>

## Code Map

- `D:\code\sj\rc\lib\main.dart:511-610` -- SharedPreferences stores with memory fallbacks/generation handling; reuse the pattern for deletion tombstones and restore-target cleanup.
- `D:\code\sj\rc\lib\main.dart:914-965,1222-1275` -- HomePage startup/last-screen restore; load deletion state before projecting remote history and prevent a deleted ID from reopening.
- `D:\code\sj\rc\lib\main.dart:1582-1590,1722-1728,2420-2475` -- list refresh and notification-driven session creation paths that must honor the same tombstone guard.
- `D:\code\sj\rc\lib\main.dart:2502-2633` -- remote-history-to-session projection and dedupe point; filter tombstoned IDs while retaining received store history.
- `D:\code\sj\rc\lib\main.dart:2657-2727,2923-2929` -- clear, selected, and single delete entry points; atomically tombstone every removed remote session.
- `D:\code\sj\rc\lib\remote_session_store.dart:173-252` -- must continue to accept full remote history/snapshots for protocol compatibility; do not filter it here.
- `D:\code\sj\rc\lib\remote_nats_chat_service.dart:944-945,1112-1123` -- existing compatible history events/refresh flow; no wire-format change required.
- `D:\code\sj\rc\test\widget_test.dart:312-336,447-487,1351+` -- delayed refresh and startup-history fixtures for delete/race/restart/duplicate visible-list regressions.

## Tasks & Acceptance

**Execution:**
- [x] `D:\code\sj\rc\lib\main.dart` -- added a persisted, race-safe remote-chat deletion tombstone store and one centralized guard in remote list projection, notification creation, last-screen restoration, and every mobile deletion action.
- [x] `D:\code\sj\rc\test\widget_test.dart` -- proved immediate single/bulk deletion, stale refresh/synchronization cannot resurrect or duplicate a chat, and a rebuilt app remains hidden after persisted deletion.

**Acceptance Criteria:**
- Given a mobile user deletes a remote chat from the chat list, when the delete action completes, then that item is removed immediately and remains absent after leaving and returning to the list, background refresh, and app restart.
- Given deletion races an older remote history/cache result containing the same chat, when the result is applied, then the deletion wins and the mobile chat list contains no restored or duplicate record for that chat.
- Given a notification or saved last-chat route refers to a tombstoned remote chat, when the app handles it after deletion, then it does not recreate or reopen that chat.

## Spec Change Log

## Review Triage Log

### 2026-09-04 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 3 (high 1, medium 1, low 1)
- defer: 0
- reject: 12 (low 12)
- addressed_findings:
  - `[high]` `[patch]` Made each deletion operation await its queued tombstone persistence after immediately removing the row, so operation completion provides a durable restart boundary rather than relying on a timing delay.
  - `[medium]` `[patch]` Made single remote deletion remove every visible duplicate sharing that normalized remote chat ID before a later synchronization pass.
  - `[low]` `[patch]` Added selected-delete, clear-all, clear-unpinned, duplicate-row, and persistence-completion regressions against stale history; clear-unpinned also verifies a pinned local row remains visible.

## Design Notes

The desktop protocol has no compatible mobile chat-delete mutation endpoint. A mobile-local tombstone therefore defines the requested mobile deletion outcome without changing shared protocol data that older clients may consume.

## Verification

**Commands:**
- `C:\\src\\flutter\\bin\\flutter.bat test test/widget_test.dart --plain-name "remote chat deletion"` -- expected: immediate, race, restart, and no-duplicate regression cases pass.
- `C:\\src\\flutter\\bin\\flutter.bat test test/remote_session_store_test.dart test/remote_nats_chat_service_test.dart` -- expected: existing remote history/state protocol behavior remains compatible.
- `C:\\src\\flutter\\bin\\flutter.bat analyze lib/main.dart` -- expected: no new analyzer diagnostics in the changed production file.

## Auto Run Result

**Summary:** Added mobile-local, persisted remote-chat tombstones. Remote history continues to be received unchanged, but HomePage waits for tombstones and filters them from its visible projection; stale history, notification entry, and last-screen restoration cannot recreate a deleted chat.

**Files changed:**
- `D:\code\sj\rc\lib\main.dart` — serialized SharedPreferences tombstone storage, deletion completion semantics, projection/notification/restore guards, and duplicate cleanup.
- `D:\code\sj\rc\test\widget_test.dart` — immediate/stale refresh/restart/notification, duplicate, selected, clear-all, and clear-unpinned deletion regressions.
- This Story 5 spec — plan, code map, completed tasks, review triage, and verification record.

**Review findings:** 3 patches applied (high 1, medium 1, low 1); 0 deferred; 12 rejected as speculative, outside the mobile-local deletion intent, already pre-existing, or not required by the acceptance behavior. Follow-up review recommendation: `true` (high finding present; score `3 × 1 + 1 × 1 = 4`).

**Verification:**
- `C:\src\flutter\bin\flutter.bat test test/widget_test.dart --plain-name "remote chat deletion"` — passed.
- `C:\src\flutter\bin\flutter.bat test test/widget_test.dart` — passed, 122 tests.
- `C:\src\flutter\bin\flutter.bat test test/remote_session_store_test.dart test/remote_nats_chat_service_test.dart` — passed, 53 tests.
- `C:\src\flutter\bin\flutter.bat analyze lib/main.dart` — only two pre-existing unused-private-method warnings at `lib/main.dart:7512` and `lib/main.dart:7540`; no new diagnostics.
- `git diff --check` — passed.

**Residual risks:** Deletion is intentionally a mobile-local projection tombstone because the compatible shared protocol has no mobile delete mutation endpoint. A genuine remote/desktop deletion will still remove server history normally; a deleted remote ID is not eligible for reuse on this mobile installation without a future explicit restoration policy.
