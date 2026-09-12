# 当前交接

## 快照（2026-09-12）

Story 4.1/4.2 的代码实现和两轮 BMAD 审查修复已经完成，但联合规格最终状态是 `blocked`，不能标记为 `done`。剩余发布门槛是：用户延期的 Android 真机 TalkBack 精确听觉记录，以及在轮换后的隔离 NATS 凭据下重跑 connected E2E。

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

## 阻塞与下一步

1. 准备轮换后的隔离凭据，设置 `NATS_E2E_ENDPOINT`、`NATS_E2E_TOKEN`、`NATS_E2E_PAIR_ID`；pair id 不得使用共享/default 值。重跑 MC canonical fact → outbox → NATS → Android notification/tap 的最终树 connected E2E。
2. 用户准备好后，在真机执行 TalkBack 听觉遍历并记录时间/正文分离、4:59/5:00 边界、`时间未知`、切换控件、执行行和生命周期焦点。
3. 两项证据补齐后重新运行 `bmad-build-auto` review；只有所有 release exit criteria 通过后，才能把 Story 4.1/4.2 及关联 Story 3 状态更新为 `done`。

## 不要重复踩坑

- 不要把凭据写入脚本默认值、日志或测试夹具；暴露过的 token 不能继续作为最终验收凭据。
- 不要用单进程拼接全部 wx GUI 套件；按 AGENTS.md 串行、隔离运行，并通过真实 Close 路径清理 frame/timer/worker。
- 不要用时间戳、显示文本或到达顺序替代 canonical owner、message id、sequence domain 和 execution sequence。
- 模拟器和 Semantics 测试只能补充，不能替代真机通知中心与 TalkBack 听觉证据。
