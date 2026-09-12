# 当前交接

## 快照（2026-09-12）

Story 4.1/4.2 的代码实现、两轮 BMAD 审查修复和最终 connected E2E 已经完成；用户已于 2026-09-12 确认真机 TalkBack 验收完成，联合规格和关联 Story 3/4 均可标记为 `done`。

当前分支为 `feature/epic-2-cross-client-sessions`。根目录 `D:\code\sj\_bmad-output` 不是 Git 仓库，联合规格与证据台账不会随 MC 提交；权威文件是：

- `D:\code\sj\_bmad-output\implementation-artifacts\spec-4-1-and-4-2-android-notifications-and-cross-client-verification.md`
- `D:\code\sj\_bmad-output\implementation-artifacts\story-4-2-verification-evidence.md`

## 已实现

- MC 以 canonical chat/message 行原子地产生 v2 `user_message` / `assistant_final` 通知事实，携带 `sequence_domain`、`origin_client`、`message_id` 和可重算的 canonical hash。
- hello 只在 durable authority 可用时返回权威 high-water；RC 据此过滤启动前 backlog。
- 通知事实的标题、正文、owner 和角色从 canonical 数据派生，避免调用方注入显示内容；clear/replacement 竞争通过同一事务复核。
- Notes 远程 create/update/delete 已补齐类型隔离、重放幂等、父级存活检查、单事务 CAS 和冲突副本。
- 执行时间线按权威 execution sequence 排序；非法时间、Markdown 围栏、关闭阶段 persistence worker drain/join 与 wx/COM teardown 已修复并有回归。
- connected Story 4 harness 不再包含默认 endpoint/token，必须从环境读取隔离凭据。

## 验证状态

- MC main/remote 目标集：316 passed。
- Notes + hardening：111 passed。
- time/common：32 passed。
- review contracts：25 passed。
- `git diff --check`：通过。
- RC、Kotlin、模拟器和 Android 真机结果见 RC handoff 与根证据台账。

## 收尾与下一步

1. connected E2E 已在真机 `93206cc7` 通过：水位、backlog 静默、通知发布、重复/重连去重、真实通知栏点击和权威 hydration 均成功。该轮测试 APK 生命周期清除了原应用数据，证据台账已如实记录。
2. 真机 TalkBack 听觉验收已由用户完成并确认；台账如实记录该确认，不虚构代理未捕获的逐字朗读稿。
3. 合并并推送 `main` 后进入维护阶段；后续跨端回归继续复用当前共享 fixture 和 connected harness。

## 不要重复踩坑

- 内置凭据只用于当前测试项目，不得用于生产环境；运行日志仍不得打印令牌。
- 不要用单进程拼接全部 wx GUI 套件；按 AGENTS.md 串行、隔离运行，并通过真实 Close 路径清理 frame/timer/worker。
- 不要用时间戳、显示文本或到达顺序替代 canonical owner、message id、sequence domain 和 execution sequence。
- 模拟器和 Semantics 测试只能补充，不能替代真机通知中心与 TalkBack 听觉证据。
