---
title: '修复通知详情、聊天路由、消息时间与 Codex 执行朗读'
type: 'bugfix'
ticket: ''
created: '2026-10-02'
status: 'built'
route: 'full'
route_source: 'auto'
review: 'thorough'
review_source: 'auto'
lenses_ran: [blind-hunter, edge-case-hunter, verification-gap, intent-alignment]
review_loop_iteration: 0
followup_review_recommended: false
context: []
warnings: [multiple-goals, oversized]
deferred: []
baseline_revision:
  mc: '712b3985a476fbf59d571db045654884d6f49c28'
  rc: 'ef27252dbb40420ec30762b55a2712ca4194f010'
---

<intent-contract>

## Intent

**Problem:** 锁屏收到的通知解锁后仍缺正文，点击通知偶尔进入其他聊天，两端长时间执行的回答缺时间，手机 Codex 执行行同一正文重复朗读。

**Approach:** 修正 Android 私有通知内容与点击身份，解耦打开聊天与关闭页面；按实际消息时间生成回答时间行；在单个执行行内识别已知阶段包装后消除相同正文重复。预计跨层超过 100 行，采用 full。

## Boundaries & Constraints

**Always:** 沿用 owner/revision/generation 守卫、稳定语义标识、现有通知持久化与去重。问题时间与回答时间独立；五分钟从上一条已显示时间累计。旧记录无可信答案时间时沿用历史 fallback，不生成当前时间。

**Never:** 不改聊天排序/执行事件身份，不跨不同事件按文本去重；不联网、不提交推送、不安装实体手机、不更新桌面安装包。仅修改四项核心行为及必要测试；模拟器安装保留数据。

## I/O & Edge-Case Matrix

| 场景 | 输入/状态 | 预期 | 失败处理 |
|---|---|---|---|
| 通知隐私 | 锁屏时接收正文 | private version 保存正文，public version 仍显示新消息；解锁自动显示正文 | 保留系统隐私设置 |
| 点击身份 | A/B 冷热启动或连续点击；hash 碰撞 | 每次按该通知完整身份打开目标，后续点击无需等待旧页面关闭 | 目标不存在沿用既有失效处理 |
| 时间边界 | 问题后 299/300/1800 秒收到答案 | 299 秒不重复标时间，300/1800 秒标实际答案时间 | 无可信历史时间沿用既有未知/fallback |
| 执行正文 | 标题正文 X，详情“开始执行：阶段：commentaryX” | 单行语义仅包含 X 一次，详情完整保留 | 不同正文与不同 event 同文均保留 |

</intent-contract>

## Code Map

- `rc/android/app/src/main/kotlin/com/example/zhuge_qa/RemoteBackgroundService.kt`: `buildRemoteMessageNotification` 当前按 deviceLocked 固化 visibleMessage；publicVersion 已遮罩。`buildContentIntent` 约 745 行只有 hash requestCode 与 UPDATE_CURRENT，需给 intent 加 pair/event 唯一 data URI，保留 extras。
- `rc/lib/main.dart`: `_drainPendingNotificationTaps` 2705 → route 2888 → `openSession` 1573 await Navigator.push，guard 一直占用到页面退出。拆打开完成与关闭生命周期，保留旧调用者关闭后工作和最新点击目标。远程消息构建 1788/1804、4855 同用 turn.createdAt；消息 equality 4945 需包含答案时间变化。执行 `_fullTextForDisplayItem` 9109 的 regex 要求 commentary 后空白，漏掉用户无空白实例。
- `mc/main.py`: `answer_time_projection` 689 已累计 300 秒；5332 append 按 turn 去重，6021 投影按 turn 唯一 created_at，6170 兼容渲染也按 turn。`_remote_turn_payload` 11703 payload/cache signature 均需答案时间。终结入口 `_on_done` 19554、Codex 活动/后台终结和 Kimi 完成需沿共同 final 持久化链写入答案时间，不能改问题 created_at。
- `mc/execution_projection.py`: 184 按同 turn question/final canonical execution row 取实际 ts，可复用恢复旧记录答案时间。`mc/chat_store.py` 保存/读取 turn payload，核对新字段保留；只在实际链路需要时改持久化格式。
- `rc/lib/remote_control_models.dart`: RemoteTurn 564 与解码新增可选答案时间；`rc/lib/remote_nats_chat_service.dart` 核对投影传递；本地 ChatMessage 构建保持实际时间。
- 复用 `rc/test/widget_test.dart` 时间、通知路由、execution detail 测试；`rc/android/app/src/test/kotlin/com/example/zhuge_qa/RemoteBackgroundNotificationPolicyTest.kt` / `RemoteBackgroundNotificationIdentityTest.kt`；`mc/tests` 既有回答展示、存储与远程协议定向测试。设备入口 `rc/integration_test/execution_process_test.dart`。
- 已读取两仓库 AGENTS 与 handoff/experience/reflection：wx GUI 串行、测试隔离 notes 数据；后台事件不能抢 owner；模拟器与真实 TalkBack/实体机证据分别报告。

## Tasks & Acceptance

**Execution:**
- [x] `mc/main.py`, `mc/chat_store.py`, `mc/execution_projection.py`：追踪共同完成与持久化入口，优先复用现有可信完成时间字段，必要时最小新增并保留真实 assistant 时间；复用可信 canonical final 时间兼容旧记录；远程 payload/cache signature 同步更新。
- [x] `rc/lib/remote_control_models.dart`, `rc/lib/main.dart`, 必要时 `rc/lib/remote_nats_chat_service.dart`：传递答案时间，两端回答列表改按实际 question/assistant 消息顺序累计投影，支持同 turn 的独立时间与分页/刷新。
- [x] `rc/android/app/src/main/kotlin/com/example/zhuge_qa/RemoteBackgroundService.kt`：private 主版始终保留 event.message，publicVersion 遮罩；点击 intent 用独特 URI 隔离完整身份。
- [x] `rc/lib/main.dart`：通知 drain 只等待目标页面完成打开，不等待关闭；确保已有聊天页上连续 A/B 点击实际切换，保留普通 openSession 的关闭回调行为。
- [x] `rc/lib/main.dart`：先接收 monkey 模拟器复现证据；处理已知阶段包装的无空白边界，对单行相同正文去重且返回完整 detail；不删除独立内容或其他事件。
- [x] 上述定向测试与 `rc/integration_test/execution_process_test.dart`：覆盖矩阵；将 `rc/integration_test/monkey_repro_temp.dart` 转正式回归，monkey 已在模拟器 drive exit 0 证实单 label 正文出现两次（证据 `_bmad-output/implementation-artifacts/monkey-notification-time-accessibility-analysis.md`）；模拟器保留修复前后真实语义树/通知中心/页面目标证据；engineer 独立复核实际影响范围。

**Acceptance Criteria:**
- Given 手机锁屏收到 A 的详细消息，when 解锁打开通知中心，then 原通知显示完整正文，无需再次接收消息；锁屏可见内容遵循遮罩。
- Given 冷/热启动及已有 B 页面，when 点击 A 通知再连续点击 B/A，then 每次实际聊天页标题及 owner 对应被点击通知，无需先关闭上一页；碰撞身份也互不覆盖。
- Given 两端回答列表包含长执行和多条短间隔消息，when 展示、刷新或加载历史，then 每条距离上一显示时间累计至少 300 秒的消息前都有其实际时间，五分钟内省略。
- Given 模拟器 Codex 执行列表包含用户所述无空白包装，when 读屏聚焦该行，then 语义正文出现一次，完整详情可访问，不同事件同文及不同详情仍保留；记录模拟器可验证范围。

## Implementation Notes

- 2026-10-02 director 已实施四项产品行为。frontmatter `context: []` 无附加文件；读取两仓库 AGENTS、handoff/experience/reflection、MC docs/README 与 monkey 分析证据后执行。
- 新增可选 `answer_at`，不更改问题 `created_at` 或聊天排序。Codex 活动/后台、Kimi 权威完成及 API `_on_done` 使用共同 `_mark_turn_request_done` 写入首次终结时刻，done 重放不刷新。ChatStore 保留原 JSON 格式；读取历史和分页时仅按同 owner/turn canonical final 恢复缺失时间，无可信事实继续旧 fallback。远程 DTO/cache 与 RC 两处消息构建、刷新 equality 同步携带字段。
- MC 时间行按 question/assistant 顺序投影并采用独立稳定时间行 ID；增量回答同样用 assistant 时间。无答案时间的未知旧回合保留原先每 turn 一条未知时间行为。RC 保留现有累计 300 秒投影。
- Android private 通知始终保存正文；publicVersion 继续遮罩。点击 data URI 编码完整 pair/event，隔离相同 requestCode。通知导航在 push 后完成，页面关闭清理保留；队列处理运行期间新增的 tap。
- 单行仅在已知阶段包装去除后正文相等时折叠，允许无空白；详情完整保留，不跨 event 去重。monkey 临时测试已并入正式 `execution_process_test.dart`，同时覆盖不同 event 同文及独立详情。
- Kimi 定向测试初次在提交阶段进入 wx 模态框，Windows 报 0x8001010d；将该用例模态框替换为显式测试失败后，确认既有 `_ImmediateThread` 夹具不接受线程 `name`。局部补参数后权威完成与重放时间断言通过，未改产品提交流程。

## Plan Change Log

## Review Triage Log

### 2026-10-02 — Review pass
- User minimum-call delegation combined blind-hunter, edge-case-hunter, verification-gap and intent-alignment in one context-free president review; no lens skipped.
- verdicts: 5 findings — high 0, medium 5, low 0, false 0, maybe-false 0
- findings:
  - `[medium]` `[patch]` Blind: existing Codex/Kimi answer rows do not reproject completion timestamps — terminal update only calls `_update_active_answer_row`; existing label update omits time rows. Completed-time reconciliation and delayed-refresh visibility were patched; independent terminal/quiet/owner tests passed.
  - `[medium]` `[patch]` Edge: answer_at changes on completion while existing aligned rows remain unchanged — same root cause and patch.
  - `[medium]` `[patch]` Edge claim: accepted-refresh regards text/attachments as fully visible while the time row is missing — root read confirms its early return; patch must also cover delayed refresh.
  - `[medium]` `[patch]` Verification: provider integration assertions omit visible time on existing answer rows — Direct terminal and delayed-refresh surface assertions were added; independent affected tests passed.
  - `[medium]` `[patch]` Intent: full rebuild works but realtime existing-row completion diverges from the requested message timing — Resolved by the same independently verified local patch.
- All five findings share one demonstrated root cause and one medium patch entry. No rejected or deferred findings. Source was verified by root at both the terminal callers and visibility helper.

## Verification

### Director 自验（2026-10-02）

- RC `flutter test --no-pub test/widget_test.dart --name 'execution process row|remote notification pop from existing|answer timestamp'`：17 项通过，包含已有聊天上连续 A/B/A 页面及 owner 断言、包装边界、完整详情和不同事件保留。
- RC `flutter test --no-pub test/widget_test.dart --name 'remote assistant uses independent|notification.*hydration|back-to-back duplicate'`：5 项通过，含 299/300/1800 秒及同文仅更新时间刷新。
- RC `flutter test --no-pub test/remote_control_models_test.dart --name 'RemoteTurn'`：1 项通过。
- RC `flutter analyze --no-pub lib/main.dart lib/remote_control_models.dart test/widget_test.dart test/remote_control_models_test.dart integration_test/execution_process_test.dart`：无问题。
- Android `./gradlew.bat app:testDebugUnitTest --tests '*RemoteBackgroundNotificationPolicyTest' --tests '*RemoteBackgroundNotificationIdentityTest' --offline`：BUILD SUCCESSFUL，policy 6 项、identity 4 项；Kotlin daemon 使用编译 fallback 后完成。
- MC `.venv/Scripts/python.exe -m pytest tests/test_answer_list_time_rows_unit.py tests/test_chat_store_unit.py -k 'answer_time or completion_time or time_row or wechat or should_show or cumulative or shared_contract' -q`：24 项通过。初验新增测试清模型方法不匹配以及未知时间重复投影均已局部修正并复验。
- MC `.venv/Scripts/python.exe -m pytest tests/test_answer_presentation_ui_automation.py -k 'show_more_recomputes_visible_answer_time_anchor' -q`：1 项通过。
- MC Codex `steer_completed_items_accumulate_and_reload` 活动/后台两参数先通过；同批 Kimi 在终结前被上述夹具错误阻断。修复后独立运行 `.venv/Scripts/python.exe -m pytest tests/test_kimi_integration.py -k turn_completed_reenables_new_chat_and_plays_sound -q`：1 项通过。
- 尚待 engineer 独立验证：正式模拟器语义树、锁屏收到同条通知后解锁的通知中心正文、实际 PendingIntent 碰撞隔离、冷热启动/连续点击的页面标题与 owner，以及必要受影响模型复核。未安装实体手机、未联网、未更新桌面包、未提交推送。模拟器/真实 TalkBack 与公网证据不能由上述自验代替。

- RC：`flutter test --no-pub test/widget_test.dart` 用新增/既有相关测试名精确筛选；RemoteTurn/service 定向测试；`flutter analyze --no-pub` 限改动 Dart 文件。
- Android：在 rc/android 运行 `./gradlew.bat app:testDebugUnitTest --tests '*RemoteBackgroundNotificationPolicyTest' --tests '*RemoteBackgroundNotificationIdentityTest'`。
- MC：以 `.venv/Scripts/python.exe -m pytest` 精确执行新改动对应的时间展示、持久化、远程 DTO 和受影响 `tests/test_*ui_automation.py`，GUI 串行运行且隔离数据。
- 模拟器：沿 monkey 已确认可用的 `flutter drive --no-pub --no-dds` 入口运行执行行复现及修复断言；ADB 记录通知解锁前后和连续点击实际页面，不用字段测试代替通知中心表面验证。不扩大至全系统回归。

## Auto Run Result

- Status: built after final local version-control recording.
- Implemented all four requested fixes: Android retains private notification body for unlock; pair/event URI and nonblocking notification routing preserve exact target; question/answer timestamps independently persist and project with cumulative 300-second anchors; known attached phase wrappers produce one accessible body.
- Files: MC main.py (completion time, remote payload, full/incremental/deadline time rows), chat_store.py (persisted historical final time recovery), execution_projection.py (canonical answer time), four targeted test files. RC main.dart (navigation, message time and execution label), remote_control_models.dart (optional answer time), Android service/identity (private body and distinct PendingIntent), targeted widget/DTO/native and execution/notification device fixtures.
- Review: four lenses combined under the user minimum-call rule; five medium findings grouped into one root cause. One medium patch applied for existing realtime answer rows and accepted delayed refresh. Zero rejected, zero deferred. Root read the patched code and independent completion regressions passed. Follow-up review recommended: false (one medium patch group).
- Matrix audit: notification privacy covered by lockedBuilder and the same-notification secure lock/unlock XML; identity covered by actual colliding PendingIntents and warm B/A/B/A plus cold A/B; time 299/300/1800, incremental/rebuild/storage/provider terminal/quiet/deadline/owner covered; execution attached-wrapper covered by both fix-before reproduction and final actual Android accessibility XML. Every covering check ran and passed.
- Independent verification: MC 24 time/store, 1 paging GUI, Codex active/background 2; RC widget22+DTO1+analyze; Android unit10+native selected3; wrapper exact device1; notification real device1 with four warm targets, same original notifications lock/unlock, and production-entry cold A/B; Local cross-client exit0. Review patch independently passed ordinary/quiet terminal4, deadline/owner GUI2, time/store24; no unchanged mobile/device checks repeated.
- Evidence: [independent report](verify-notification-routing-answer-time-accessibility.md), [monkey analysis and original device reproduction](monkey-notification-time-accessibility-analysis.md).
- Actual limitations: no physical installation or TalkBack speech recording, no desktop package update, no real-provider/production Live/full-product claim. First notification attempt accidentally read external events because startup override was incomplete; it was stopped before prompts/taps and subsequent checks verified private endpoints. Some driver wrappers failed while independent exact native device commands exited0; original failures retained.
- Cleanup: QA PIN/notifications restored or removed, privacy restored, task servers stopped. Tool policy refused deletion of two temporary test directories; left in place with no running servers.
- Scope: concurrent MC approval/worker changes and their documents/tests were preserved and excluded from review/commit. The final task-only diff SHA256 is CC29C9869ED194924EC6DABA04C2C5081A362014925D2ED5F8ABAA494D038A4C. Implementation agents made no commits; the explicitly invoked workflow Finalize requires root to record reviewed changes locally, without push.

### Final version-control recording

- MC local commit: `6138051ed3e72503973c90fb9c9fe8f17c4a6bec`. RC local commit: `df9ccd7334153c938202d247d44b95a00e98d733`. No push. All 17 reviewed files are represented by these commits; no task hunk remains uncommitted.
- RC worktree/index clean. MC index clean and all task-only files clean; its remaining main.py hunks and other files are concurrent approval work preserved without staging or edits. User scope-preservation rules take precedence over the workflow clean-tree instruction: no unrelated change was deleted or committed to manufacture cleanliness.
- Final status: built.
