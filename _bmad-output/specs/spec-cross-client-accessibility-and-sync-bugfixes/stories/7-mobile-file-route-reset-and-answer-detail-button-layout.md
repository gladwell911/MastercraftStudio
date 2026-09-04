---
title: 'Story 7: Mobile File Route Reset and Answer Detail Button Layout'
type: 'bugfix'
created: '2026-09-04'
status: 'done'
baseline_revision: 'd4d87763ad4d1c716e41a7bbe90c928ae09ad60a'
review_loop_iteration: 0
followup_review_recommended: true
context:
  - 'D:\\code\\sj\\rc\\AGENTS.md'
warnings: []
deferred: []
---

<intent-contract>

## Intent

**Problem:** Opening a file from the extracted-file flow leaves completed, temporary file routes on the mobile navigation stack. Returning to the app flow can therefore show a completed transfer/action page instead of the owning chat detail. Answer detail also places its file-extraction operation differently from the chat-detail composer, giving controls a different visual and screen-reader order.

**Approach:** Treat the successful open workflow as a temporary route chain and consume it when its opened-file activity returns, restoring the owning chat detail. Extract the shared composer action arrangement so Answer Detail and Chat Detail use the same ordered controls, spacing, and semantic traversal, with `提取文件` stacked above the rightmost primary action in Answer Detail.

## Boundaries & Constraints

**Always:** Preserve Story 1's progress-first navigation and the existing transfer controller/protocol behavior. Consume only the completed temporary extracted-file route chain after a successful open; failures, stopped transfers, and unfinished downloads must remain visible for recovery. Keep Chinese labels and button semantics truthful. The Answer Detail bottom operations must have the same action order, spacing, and screen-reader traversal as Chat Detail; its `提取文件` control must be above the lower-right primary action. Verify both navigation reset and semantic/widget order with focused Flutter tests.

**Block If:** Satisfying the route reset requires changing the desktop `mc` application, shared NATS/file protocol, platform file opener contract, or the product meaning of failed/cancelled transfer recovery.

**Never:** Do not modify completed Story 1, Story 3, Story 4, Story 5, or Story 6 specs. Do not remove manual completed-file opening, alter download/probe sequencing, redesign global chat navigation, or introduce desktop changes for this mobile-only scope.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|---------------|----------------------------|----------------|
| Completed opened text file | Answer detail -> extraction -> open -> completed transfer -> text viewer, then viewer closes | Temporary extraction/action/progress routes are removed and the owning chat detail is visible | No stale completed transfer page remains in back navigation |
| Non-terminal/failed transfer | Probe, connection, or download fails, stops, or has not completed | Current progress route remains available with its actual state | Do not consume recovery UI or falsely return to chat detail |
| Shared bottom controls | Answer Detail and Chat Detail render their composer operations | Shared controls have identical ordered traversal and gaps; Answer Detail puts `提取文件` directly above its rightmost primary button | Controls retain labels and actions |

</intent-contract>

## Code Map

- `D:\code\sj\rc\lib\main.dart:2223-2387` -- owns local-file opening plus the extracted-file progress route; use the successful-open completion boundary to reset only temporary routes.
- `D:\code\sj\rc\lib\files_page.dart:18-147` -- `ExtractedFileTransferProgressPage` starts after first frame and owns automatic/manual completed-file opens; add an explicit completion callback only after a successful opened-file flow returns.
- `D:\code\sj\rc\lib\main.dart:6252-6307,6966-7063` -- Chat Detail's `_ComposerBar` is the shared source for bottom action order and spacing.
- `D:\code\sj\rc\lib\main.dart:8139-8613` -- Answer Detail currently supplies its own trailing Copy/Extract actions; adapt this surface to the shared arrangement without changing voice/text/send behavior.
- `D:\code\sj\rc\test\answer_file_extraction_test.dart` -- existing extracted-file accessibility and progress-first regression fixture; add route-reset and Answer/Chat control-order/geometry semantics tests here.
- `D:\code\sj\mc` -- read-only related desktop project; CAP-5 desktop behavior was completed in Story 8 and this Story changes no desktop files.

## Tasks & Acceptance

**Execution:**
- `D:\code\sj\rc\lib\files_page.dart` -- notify the caller only when an opened completed file returns successfully, without changing unfinished/failed transfer routes.
- `D:\code\sj\rc\lib\main.dart` -- consume the extracted-file temporary route chain after that successful callback, restoring the owning chat-detail route; refactor Answer Detail's trailing operations to the same ordered, spaced composer action structure as Chat Detail and stack `提取文件` above its primary action.
- `D:\code\sj\rc\test\answer_file_extraction_test.dart` -- add widget regressions for completed text-file return route reset, failure/unfinished non-reset, common action order/spacing/semantics, and extraction-button placement.

**Acceptance Criteria:**
- Given a user opens a file through Answer Detail's extracted-file flow, when the completed local file viewer returns, then the temporary route state is consumed and the related chat detail is shown rather than a completed temporary file page.
- Given the transfer is still pending, stopped, or failed, when the user returns from any available surface, then its truthful progress/recovery route is not consumed.
- Given screen-reader or visual navigation reaches either page's bottom operations, when it traverses their shared actions, then it encounters the same order and spacing; on Answer Detail, `提取文件` is positioned directly above the lower-right primary action.

## Spec Change Log

## Review Triage Log

### 2026-09-04 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 7 (high 1, medium 5, low 1)
- defer: 0
- reject: 14 (low 14)
- addressed_findings:
  - `[high]` `[patch]` Propagated production open and incomplete-transfer failures so temporary routes are retained for recovery instead of being consumed after a swallowed opener error.
  - `[medium]` `[patch]` Made route consumption idempotent and safe for a root Answer Detail, while retaining the parent Chat Detail when one exists.
  - `[medium]` `[patch]` Added mounted guards and catches around flow callbacks so late or failed actions cannot reset a defunct route or create an uncaught asynchronous error.
  - `[medium]` `[patch]` Added the real Home-to-Chat extracted-file route-chain regression, including the production forwarding path.
  - `[medium]` `[patch]` Covered manual retry after automatic open failure and verified that only the successful retry completes the flow.
  - `[medium]` `[patch]` Covered Answer Detail's expanded composer, exact shared action-gap widgets, and button semantics in addition to collapsed layout geometry.
  - `[low]` `[patch]` Added stable keys for shared composer gaps so spacing regressions are directly observable in widget tests.

## Design Notes

The route reset is deliberately tied to the completion of an actual opened-file flow, not to the transfer becoming complete. This retains the completion screen for manual open and error recovery, while preventing an already-consumed temporary workflow from becoming the next visible page after the file viewer closes.

## Verification

**Commands:**
- `C:\\src\\flutter\\bin\\flutter.bat test test/answer_file_extraction_test.dart` -- expected: extraction navigation, reset, control order, geometry, and accessibility regressions pass.
- `C:\\src\\flutter\\bin\\flutter.bat test test/files_page_test.dart` -- expected: progress-page opening and transfer rendering regressions remain green.
- `C:\\src\\flutter\\bin\\flutter.bat analyze lib/main.dart lib/files_page.dart` -- expected: no new analyzer diagnostics in changed production files.
- `git diff --check` -- expected: no whitespace errors.

## Auto Run Result

**Summary:** Completed the mobile extracted-file route reset and Answer Detail bottom-operation alignment. Successful file opening now consumes only the temporary extraction/action/progress chain and restores the owning Chat Detail; failed, stopped, or unfinished work remains available for recovery. Answer Detail uses the shared composer ordering and places `提取文件` above its lower-right primary action.

**Files changed:**
- `D:\code\sj\rc\lib\main.dart` -- propagates successful-flow completion through Home/Chat/Answer extraction routes, preserves failed opens, resets temporary routes safely, and unifies composer action placement.
- `D:\code\sj\rc\lib\files_page.dart` -- reports an opened completed-file flow only after the opener completes without error.
- `D:\code\sj\rc\test\answer_file_extraction_test.dart` -- covers route reset, root safety, production forwarding, expanded/collapsed layout, exact spacing, and action semantics.
- `D:\code\sj\rc\test\files_page_test.dart` -- covers successful/failed automatic open and manual retry completion callbacks.
- This Story 7 spec -- records the plan, review triage, verification, and completion result.

**Review findings:** 7 patches applied (high 1, medium 5, low 1); 0 deferred; 14 rejected as speculative, duplicate, outside the stated mobile scope, or already satisfied by the corrected implementation. Follow-up review recommendation: `true` (a high-severity patch was applied; score `3 × 5 + 1 = 16`).

**Verification:**
- `C:\\src\\flutter\\bin\\flutter.bat test test/answer_file_extraction_test.dart` -- passed, 15 tests.
- `C:\\src\\flutter\\bin\\flutter.bat test test/files_page_test.dart` -- passed, 17 tests.
- `C:\\src\\flutter\\bin\\flutter.bat analyze lib/main.dart lib/files_page.dart` -- only two pre-existing unused-private-method warnings at `lib/main.dart:7628` and `lib/main.dart:7656`; no new diagnostics.
- `git diff --check` -- passed (Git emitted only CRLF conversion notices).

**Residual risks:** Native external-file openers and APK installers only signal handoff completion, because their platform UI runs outside Flutter. A platform failure is now surfaced as an error and preserves the temporary recovery route; real-device TalkBack traversal should still be confirmed for the final physical focus behavior.
