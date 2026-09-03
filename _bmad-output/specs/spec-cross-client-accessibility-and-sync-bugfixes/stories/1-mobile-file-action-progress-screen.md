---
title: 'Story 1: Mobile File Action Opens the Progress Screen Immediately'
type: 'bugfix'
created: '2026-09-02'
status: 'done'
baseline_commit: 'd7fec00dd3223c2e08b0300ba90b77e4511ded8f'
review_loop_iteration: 0
context:
  - 'D:\\code\\sj\\rc\\AGENTS.md'
---

<frozen-after-approval reason="human-owned intent - do not modify unless human renegotiates">

## Intent

**Problem:** From an extracted file's action screen, the mobile app waits for the remote `file_probe` request, and then can begin accepting/downloading the file, before it navigates to the transfer view. Pressing either "Transfer to phone" or "Open file" therefore looks unresponsive during preparation, connection, or download.

**Approach:** Navigate synchronously to a progress route that renders a readable initial preparation state. Start the file probe only after that route has produced its first frame; update the same route with the real transfer item and its observable transfer, failure, and completion state. The open action must continue to open the completed local file only after the visible download workflow finishes.

## Boundaries & Constraints

**Always:** Preserve the existing remote file protocol and current file-transfer controller as the source of download progress. The first rendered frame after either action must be the target progress screen, before file probing, desktop connection/acceptance, or HTTP download starts. The screen must expose the actual current phase, including preparation, waiting/connection, download, failure, and completion, without blocking navigation. Keep existing Chinese UI strings and accessibility semantics stable except for the required new progress feedback. Run focused Flutter widget tests and affected static analysis.

**Ask First:** Stop and request direction before changing the desktop `mc` project, the shared NATS/file protocol, persisted file-library data, or the behavior of unrelated file-offer controls.

**Never:** Do not redesign the global Files tab, change transfer-control behavior from later stories, replace the HTTP/NATS transfer implementation, or report success before a transfer actually completes. Do not remove the existing ability to open a completed text, APK, or native file.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|---------------|----------------------------|----------------|
| Start transfer | User taps "Transfer to phone" for an extracted remote path | A progress screen is visible on the next frame with a preparation state; the remote probe and transfer begin from that screen, and controller updates are rendered there | Probe, accept, or HTTP errors change that screen to a readable failure state |
| Start then open | User taps "Open file" for an extracted remote path | The same progress-first flow runs; after successful completion, the existing local-file opener runs once | No opener call occurs if preparation or download fails |
| Slow preparation | `file_probe` remains pending | The progress screen stays visible and reports preparation rather than leaving the action page frozen | Later completion/failure updates the already-visible route |
| Repeated completion updates | The controller emits progress, completion, or failure after the route is displayed | The route resolves the real returned file ID and shows current status instead of a provisional stale item | Missing/invalid probe data remains a visible failure rather than throwing from the route |

</frozen-after-approval>

## Code Map

- `D:\code\sj\rc\lib\main.dart:1758-1840` -- `_acceptMobileFile` is the existing acceptance/download pipeline and updates `FileTransferController`; retain it as the transfer-state authority.
- `D:\code\sj\rc\lib\main.dart:2017-2079` -- current extracted-file probe, navigation, acceptance, and post-download open flow; split navigation from delayed preparation so costly work is owned by the rendered progress route.
- `D:\code\sj\rc\lib\main.dart:7387-7702` -- extracted-file action callbacks and buttons that must preserve their labels and invoke the progress-first entry point.
- `D:\code\sj\rc\lib\files_page.dart:164-282` -- existing controller-listening transfer detail presentation; reuse it or its state rendering once a real transfer ID exists, without regressing ordinary Files-tab behavior.
- `D:\code\sj\rc\lib\file_transfer_controller.dart:5-100` and `lib/file_transfer_models.dart` -- existing notifier/item status model; extend only as needed to make the initial pre-probe phase truthful and renderable.
- `D:\code\sj\rc\test\answer_file_extraction_test.dart:418-483` -- extracted-file action unit/widget coverage; add progress-first ordering and open-after-completion regression cases.
- `D:\code\sj\rc\test\files_page_test.dart:134-282` -- transfer-detail rendering/notifier-update tests to preserve live status behavior.
- `D:\code\sj\rc\integration_test\desktop_file_offer_download_flow_test.dart:89-192` -- existing fake HTTP/Home wiring pattern for an end-to-end widget flow; use only if the focused unit test cannot prove navigation ordering.

## Tasks & Acceptance

**Execution:**
- [x] `D:\code\sj\rc\lib\main.dart` -- introduce a progress-first extracted-file entry flow that pushes the target route synchronously, defers probing and transfer start until after its first frame, and retains the current completed-file open behavior.
- [x] `D:\code\sj\rc\lib\files_page.dart` and, only if required, `D:\code\sj\rc\lib\file_transfer_models.dart` / `lib\file_transfer_controller.dart` -- supply a stateful or dedicated progress presentation able to show pre-probe preparation and then bind to the returned transfer ID's live controller state.
- [x] `D:\code\sj\rc\test\answer_file_extraction_test.dart` -- add completer-backed tests proving both action buttons render the progress page before a pending probe begins/completes, verify phase/error rendering, and verify open happens only after download completion.
- [x] `D:\code\sj\rc\test\files_page_test.dart` -- extend only for any newly introduced progress widget/status rendering so controller-backed transfer detail updates remain covered.

**Acceptance Criteria:**
- Given an extracted remote file, when the user taps "Transfer to phone", then the first renderable frame is the transfer progress screen and no preparation, desktop connection, or download work began before that navigation.
- Given an extracted remote file, when the user taps "Open file" or "Download file", then the first renderable frame is the download progress screen and it presents the real preparation, waiting/connection, download, failure, or completion state.
- Given a pending probe or failed transfer, when the asynchronous result arrives, then the already-visible progress route updates without a frozen action screen, uncaught exception, or false completion state.
- Given the open action completes its visible download, when the local file is ready, then the existing appropriate opener is invoked once; it is not invoked for a failed or incomplete download.

### Review Findings

- [x] [Review][Patch] 为缺失失败原因提供明确回退状态 [rc/lib/files_page.dart:67] — onStart 返回空白失败原因时，页面会继续显示“正在准备文件”；应统一显示可读失败状态。
- [x] [Review][Patch] 传输项移除后不得回退到过期等待状态 [rc/lib/files_page.dart:112] — 已绑定的传输项在页面显示期间被删除或取消时，orElse: fallback 会继续显示陈旧详情；应显示已取消/不可用状态。
- [x] [Review][Patch] 自动打开完成文件必须安全且仅一次 [rc/lib/files_page.dart:86] — 自动打开从 build 触发，既可能与用户手动打开竞争，也会让抛出的异步异常逃逸；应防止重复打开并把打开失败转为可读状态。
- [x] [Review][Defer] 为准备阶段定义超时或重试策略 [rc/lib/files_page.dart:65] — 当前“准备中”会持续到探测返回；SPEC 未定义超时阈值或重试产品策略，暂不在 Story 1 擅自引入。deferred, pre-existing
- [x] [Review][Defer] 覆盖生产路径的成功探测、接收失败与打开一次行为 [rc/test/answer_file_extraction_test.dart:511] — 当前 widget 测试主要覆盖失败探测和注入状态；此验证缺口将由紧随本审查的 Story 1 E2E/集成测试处理。deferred, pre-existing

## Spec Change Log

## Design Notes

The returned remote `file_id` is not known until after `file_probe`, so a route keyed only to a provisional `MobileFileItem` cannot receive the controller updates that are upserted under the returned ID. The route must therefore own a truthful preparation phase, defer its start with a post-frame callback, then bind its display to the real ID once probing succeeds. It should start the existing `_acceptMobileFile` pipeline without awaiting it in the action-page callback; the controller remains responsible for continuing visible updates.

## Verification

**Commands:**
- `flutter test test/answer_file_extraction_test.dart` -- expected: the new progress-first ordering, failure, and open-after-completion tests pass.
- `flutter test test/files_page_test.dart` -- expected: existing and new transfer-detail rendering tests pass.
- `flutter analyze lib/main.dart lib/files_page.dart lib/file_transfer_controller.dart lib/file_transfer_models.dart` -- expected: no analyzer diagnostics for changed production files.

## Suggested Review Order

**Progress-first navigation**

- Push before delayed preparation.
  [`main.dart:2068`](../../../../../rc/lib/main.dart#L2068)

- Preserve the existing transfer authority.
  [`main.dart:1757`](../../../../../rc/lib/main.dart#L1757)

**Visible transfer state**

- Render preparation, then bind real ID.
  [`files_page.dart:23`](../../../../../rc/lib/files_page.dart#L23)

- Keep detailed controller-driven transfer UI.
  [`files_page.dart:265`](../../../../../rc/lib/files_page.dart#L265)

**Regression coverage**

- Assert production action ordering and errors.
  [`answer_file_extraction_test.dart:515`](../../../../../rc/test/answer_file_extraction_test.dart#L515)

- Exercise phases and completion behavior.
  [`files_page_test.dart:10`](../../../../../rc/test/files_page_test.dart#L10)
