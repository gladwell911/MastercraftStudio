---
title: 'Story 6: Mobile Files Tab Minimal Current-State Display'
type: 'bugfix'
created: '2026-09-04'
status: 'done'
baseline_revision: '9831648ae097fbdd41d2c7d3b07fdbe0313cf1aa'
review_loop_iteration: 0
followup_review_recommended: true
context:
  - 'D:\\code\\sj\\rc\\AGENTS.md'
warnings: [oversized]
deferred:
  - summary: >-
      A lost file_accept response can leave the phone-local connecting state pending because the product has no timeout or retry policy.
    evidence: |-
      Story 1 already deliberately deferred preparation timeout/retry policy, and CAP-7 defines current-state presentation but no timeout duration, retry ownership, or user recovery behavior.
    location: >-
      D:\code\sj\rc\lib\main.dart:2019
    severity: medium
---

<intent-contract>

## Intent

**Problem:** Files-tab rows expose implementation detail (path, progress, speed, and error text) and cannot distinguish all required live transfer phases. This makes the mobile file list noisy and can misrepresent work that is still preparing or connecting.

**Approach:** Make each Files-tab record a two-field presentation—filename and one canonical current-state label—derived from the existing local transfer controller. Add only phone-local lifecycle states needed to truthfully represent preparation and connection, preserving the desktop/NATS wire contract.

## Boundaries & Constraints

**Always:** Every Files-tab row must render exactly its filename and one current status, with no path, size, internal ID, percentage, speed, error detail, or other metadata. It must distinguish Chinese labels for preparing, connecting, waiting to receive, downloading, paused, stopped, failed, and completed, and rerender from genuine controller updates. Preserve Story 1's progress-first navigation and existing controller as transfer-state authority; retain existing Chinese UI/accessibility semantics outside the Files-tab row; retain existing remote protocol payloads and event types.

**Block If:** Meeting the acceptance requires a breaking desktop/NATS protocol change or a product decision about a phase not observable from the phone's transfer workflow.

**Never:** Do not modify completed Story 1, Story 3, Story 4, or Story 5 specifications; do not redesign the transfer-detail page or transfer controls; do not persist new file state; do not alter desktop behavior or change event payload compatibility.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|---------------|---------------------------|----------------|
| Incoming offer | Controller contains a valid incoming offer | Row shows only the offered filename and `等待接收` | Invalid offer continues to show only filename and `下载失败` |
| Local accept lifecycle | User starts acceptance, command is pending, then download reports bytes | Row changes `连接中` then `正在下载` from real controller updates | Command/HTTP failure changes only the state to `下载失败` |
| Terminal/control state | Controller pauses, stops, or completes an item | Row shows exactly one of `已暂停`, `已停止`, or `已完成` and does not revive a stopped item | Out-of-order remote updates retain the controller's terminal guard |

</intent-contract>

## Code Map

- `D:\code\sj\rc\lib\file_transfer_models.dart:1-117` -- transfer enum, protocol conversion, and expanded `displayLabel`; add phone-local preparing/connecting states without changing existing protocol values and expose a canonical Files-row state label.
- `D:\code\sj\rc\lib\file_transfer_controller.dart:9-204` -- remote event mapping and protected state transitions; retain its protocol event compatibility and stopped-transfer terminal protection while accepting the added local phases.
- `D:\code\sj\rc\lib\main.dart:1970-2085,2276-2335,2421-2438` -- acceptance/download lifecycle and controller-fed FilesPage; record preparing before delayed probe and connecting before `file_accept`, then retain actual download/terminal updates.
- `D:\code\sj\rc\lib\files_page.dart:410-424,616-633` -- Files-tab ListTile currently renders expanded display label and path; replace it with filename plus one canonical current-state label, retaining detail-page behavior.
- `D:\code\sj\rc\test\files_page_test.dart:357-432` -- Files-row and notifier rerender coverage; assert exactly two visible text values and all required state labels/live transitions.
- `D:\code\sj\rc\test\file_transfer_controller_test.dart:7-203` -- controller event/terminal-state coverage; extend for local phase transition and stopped-transfer regression if model transitions change.
- `D:\code\sj\rc\test\answer_file_extraction_test.dart:418-515` -- Story 1 progress-first fixture; extend only to prove the controller records visible preparing/connecting in the real extracted-file flow.

## Tasks & Acceptance

**Execution:**
- `D:\code\sj\rc\lib\file_transfer_models.dart` and `D:\code\sj\rc\lib\file_transfer_controller.dart` -- define canonical state labels and phone-local preparing/connecting lifecycle transitions while preserving existing NATS serialization/event compatibility and terminal guards.
- `D:\code\sj\rc\lib\main.dart` -- update the real extracted-file and accept workflows so the controller emits preparing, connecting, downloading, failure, completion, and existing pause/stop states at their truthful boundaries.
- `D:\code\sj\rc\lib\files_page.dart` -- render every Files-tab row as filename title and exactly one canonical status subtitle; remove path and all expanded transfer metadata from that surface.
- `D:\code\sj\rc\test\files_page_test.dart`, `D:\code\sj\rc\test\file_transfer_controller_test.dart`, and `D:\code\sj\rc\test\answer_file_extraction_test.dart` -- cover all eight labels, notifier-driven real-state rerendering, local lifecycle boundaries, and protocol-compatible stopped behavior.

**Acceptance Criteria:**
- Given any item in the mobile Files tab, when its row is rendered, then it contains only the filename and one current status, and it exposes no path, size, internal ID, or other non-required information.
- Given a transfer moves through preparation, connection, waiting to receive, downloading, pause, stop, failure, or completion, when the controller receives or produces that real state, then the same Files-tab row updates to the corresponding distinct current-status label.
- Given an existing compatible desktop file event is received, when it updates a Files-tab item, then its protocol interpretation and stopped-transfer terminal behavior remain compatible while the row remains minimal.

## Spec Change Log

## Review Triage Log

### 2026-09-04 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 7: (high 1, medium 5, low 1)
- defer: 1: (medium 1)
- reject: 4: (low 4)
- addressed_findings:
  - `[high] [patch]` Guarded the post-`file_accept` boundary so a stopped or remotely terminal transfer cannot start or overwrite an HTTP download after a late command response; added production-flow regression coverage.
  - `[medium] [patch]` Restricted provisional replacement to a still-preparing row and asserted the production Files tab leaves no stale preparing duplicate after probe success.
  - `[medium] [patch]` Made notifier replacement immediately project the new notifier's current value and added widget coverage.
  - `[medium] [patch]` Removed newly added preparation/connection detail routing because that detail surface does not own fully wired controls; retained the existing menu behavior instead.
  - `[medium] [patch]` Added a concise blank-filename fallback while retaining exactly the filename and one status text on each Files row.
  - `[medium] [patch]` Added stop-during-connection regression coverage, including the late accept response case.
  - `[low] [patch]` Added rejected-state mapping coverage alongside the required eight state labels.

## Design Notes

The desktop protocol has no preparing/connecting events. These phases are nevertheless observable at the phone's own boundaries: preparation begins before the delayed extracted-file probe, and connection begins while `file_accept` is pending. They are phone-local enum values and must not add or emit new wire values.

## Verification

**Commands:**
- `flutter test test/files_page_test.dart` -- expected: Files-tab rows show only filename/current status and live notifier updates cover every required state.
- `flutter test test/file_transfer_controller_test.dart test/answer_file_extraction_test.dart` -- expected: lifecycle, protocol compatibility, and Story 1 progress-first regressions pass.
- `flutter analyze lib/main.dart lib/files_page.dart lib/file_transfer_controller.dart lib/file_transfer_models.dart` -- expected: no new analyzer diagnostics in changed production files.

## Auto Run Result

**Summary:** Implemented a minimal, live Files-tab row presentation backed by the existing mobile transfer controller. Phone-local preparing and connecting phases now bridge the real probe and accept boundaries without changing desktop/NATS payloads.

**Files changed:**
- `rc/lib/file_transfer_models.dart` -- added non-serializable phone-local phases and canonical Files-tab labels.
- `rc/lib/file_transfer_controller.dart` -- retained one provisional row through probe replacement and preserved stop/terminal guards.
- `rc/lib/main.dart` -- published preparing/connecting at real workflow boundaries and prevented late accept replies from reviving a stopped/terminal transfer.
- `rc/lib/files_page.dart` -- rendered only filename and one status, projected notifier updates live, and used a blank-name fallback.
- `rc/test/files_page_test.dart` -- covered minimal row content, all state labels, notifier updates, and fallback filename behavior.
- `rc/test/file_transfer_controller_test.dart` -- covered provisional lifecycle, connection/stop behavior, and terminal protection.
- `rc/test/file_transfer_models_test.dart` -- covered Files-tab labels and local-state protocol isolation.
- `rc/test/answer_file_extraction_test.dart` -- covered production preparation, connection, authoritative-row replacement, and late-accept stop behavior.

**Review findings:** Applied 7 patches (high 1, medium 5, low 1); deferred 1 pre-existing timeout-policy item; rejected 4 non-actionable or out-of-scope observations. Follow-up review recommendation is `true` (score `3 × 5 + 1 = 16`).

**Verification:** `flutter test test/files_page_test.dart` passed (15 tests); `flutter test test/file_transfer_controller_test.dart test/answer_file_extraction_test.dart` passed (17 tests); `flutter test test/file_transfer_models_test.dart` passed (2 tests); `git diff --check` passed. `flutter analyze lib/main.dart lib/files_page.dart lib/file_transfer_controller.dart lib/file_transfer_models.dart` reported only the 5 pre-existing collection-if info diagnostics in `file_transfer_controller.dart` and 2 pre-existing unused-element warnings in `main.dart`; no new diagnostics were introduced.

**Matrix audit:** Incoming-offer valid/invalid minimal rendering is covered by FilesPage state-row tests and controller invalid-offer coverage; local acceptance progression is covered by the production extracted-file test plus controller/notifier state rendering; pause, stop, completion, and terminal non-revival are covered by FilesPage and controller tests. All covering tests ran and passed.

**Residual risk:** A lost `file_accept` response may remain in the visible connecting state until a later event because timeout/retry behavior remains intentionally deferred pending product policy.
