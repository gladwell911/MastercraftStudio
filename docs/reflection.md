# 纠错反思

实际错误形成的下次检查点：

- 旧交接和票据不能单独证明 Story 完成；核对代码、提交、状态及测试。Live 验收需证明真实生产调用链和有效结果。
- 用户说“改成 X 那样”时先核对 X 的真实样例；菜单入口沿实际按键绑定验证可达性。
- 远程执行投影曾阻塞 wx 主线程，空活动轮曾误用上一轮末页；先明确线程边界，所有守卫和查询按目标 turn 过滤。
- 恢复 worker 不一定在健康活动轮中执行；设计增量显示前检查实际触发路径与退出条件。
- 发布通知不得把请求占位当最终回答；只从持久化 done turn 生成。跨端证据须明确是否来自同一次最终树。
- 终答补发队列曾在读取持久化状态和投递前清空，瞬时失败会丢失补发。下次只在通知及历史入队成功后移除单项，并用失败后重试测试确认。
- 清除成功不能仅凭修订号判断：相同修订可能来自无关操作。下次同时核对操作 ID、修订和权威清除状态。
- 真机安装保留数据时，不用会隐式卸载的命令；新包切换前核对 OneDrive 笔记快照之后的源库变化。
- 测试失败先按精确用例区分产品回归、旧预期和环境差异；不能用宽泛筛选器得出全量结论。
- 首版云端笔记库检查只要求 SQLite 完整且有表，可能把无关数据库当成笔记库；后续改为校验当前及旧版笔记表结构。验证“不会创建空库”时，应测试一个结构完整但用途错误的数据库，并断言文件未改。
- 首次在线备份完成后未显式关闭 SQLite 临时连接，Windows 拒绝重命名；下次使用 backup API 时显式关闭源和目标连接再放置文件。首次临时文件清理由自动审批以策略限制拒绝，未绕过。

完整纠错记录见 [归档原文](archive/entry-context-2026-09-29/reflection.md)。
- Recovery QA needs the complete authoritative final-answer protocol; a delta is insufficient. Native Alt+A automatically resends the first question, so a cleared empty-turn expectation is wrong. Activate the native GUI event loop and await visible answer rows after the existing navigation quiet interval; done status alone is not visible-answer evidence. Record the configured startup attempt budget, actual child exit and final request state.


The final combined review found two asynchronous identity gaps missed by the initial recovery scenarios. Exception/overflow prior-owner regressions and paired retired/current client UI tests now cover both; the focused dog follow-up checks the repaired boundaries without restarting the entire review.


## Epic4 final verified result (2026-10-01)

Status built for user review; acceptance pending, not Done. This supersedes earlier pending/blocked results. MC final no-temporary-hook affected56/unit94/models121=271; unchanged RC widget159 gives430 scoped checks, with three exact fresh checks overlapping affected rather than counted again. Six exact reproduced baseline failures remain excluded, COM0x8001010d remains documented in native/baseline-class controls. Compile/diff checks exit0. Final current-tree Local standard integrationDriver retry exits0 and verifies mobile chat list, exact Codex/Kimi assistant role/content/session remoteModelId and desktop provider routes; no cache-specific test is claimed. Original flutter-test DDS/comparator failures and offline-device attempt remain historical evidence. Original Android15 AVD restored; temporary runner and owned harness/flutter processes cleaned.

The test now requires exactly one actual clear after real SendInput4 rather than a mandatory menu: product CHAR_HOOK legitimately consumes A. Native HWND preparation replaces unstable wrapper identity while retaining foreground/HasFocus and final focus assertions. Light/heavy/native/current controls succeed, and all final durable/resend/newthread/stale-terminal/visible assertions pass without temporary WH hooks. Historical only-Alt cause remains unknown and currently unreproduced; no external cause or product keyboard regression is inferred. Final navigation accepted-to-visible1016ms preserves selection/focus; real frame destruction125ms cancels timers without callbacks while child destruction does not.

Full evidence/18-finding triage/six repair groups: `D:/code/sj/_bmad-output/implementation-artifacts/qa-epic4-answer-presentation.md`, overall plan and review. Original source/ASR dirty change preserved; no commit/merge claimed until root records delivery. Production Live, actual provider services, physical-device accessibility/notifications/haptics and packaging remain unverified.
