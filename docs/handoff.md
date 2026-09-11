# 当前交接

## 快照（2026-09-11）

当前分支为 `feature/epic-2-cross-client-sessions`。Epic 2 的 Story 2.1—2.4 均已完成；下一项是 Story 3.1（保留桌面端时间摘要与执行焦点）。其后还剩 Story 3.2、4.1、4.2；Epic 2 retrospective 为可选项。

## 已完成

- Story 2.1：规范化 owner/session 身份，增加 V2 协议协商、持久事实、outbox、epoch、哈希去重和隔离。
- Story 2.2：实现 revision 化权威清空、原子状态机、首消息 exactly-once 重发和失败恢复。
- Story 2.3：在 state/history/clear 响应中提供隐私安全的 revision 与最新 clear authority，移动端按 revision-first 对账。
- Story 2.4：以 `durable_facts` 作为远程执行权威，原子分配全局事件身份与每聊天执行序号；问题/最终回答引用稳定 canonical message；提供认证的冻结 tail/older/backfill/snapshot 查询。
- MC/RC 共享字节一致且两端实际执行的契约矩阵：清空对账 12 个场景、执行时间线 8 个场景。

## 验证状态

- MC Story 2.4 定向 selector 收尾复跑：174 通过、736 deselected；同时修复 malformed backfill revision 被错误转成零值的问题。完整回归剩余失败面与已记录的 102 项历史基线一致。
- RC Story 2.4 model/store/service/widget 收尾复跑：218 通过；完整 Flutter 回归 518 通过、23 项既有失败。
- Flutter analyzer 仍为 58 项既有问题，Story 2.4 changed surface 未新增错误。
- 两端 8 行执行时间线矩阵均可执行，夹具 SHA-256 均为 `5669716777AC6AC8CF3620E4B8307A192B399141240E4D52EA6FE4DBB91D6D07`；两个仓库 `git diff --check` 通过。
- 没有可用 Android 设备或模拟器，真实跨设备 NATS 执行时间线、重连恢复和 TalkBack 行为尚未验证。

## 下一步

1. 按 Sprint 状态开发 Story 3.1，再开发 Story 3.2、4.1、4.2。
2. Story 3.1 必须延续稳定 owner、权威执行序号、有界前台读取和不抢焦点约束。
3. 有设备时补跑 Story 2.4 的真实跨端初始 100 行、向上分页、历史/实时重叠、缺口回补、快照降级及手动重试。
4. Story 2.4 在 review 中修复过高风险并发/权威问题，后续改动前建议再做一次独立 review。

## 风险与不要重复尝试

- 完整测试仍有历史基线失败，不能把定向测试通过描述为全仓全绿。
- 不要使用到达时间、时间戳、步骤下标、当前聊天 fallback 或显示文本作为 V2 身份/顺序。
- 游标或恢复失败时不要删除有效行；自动一致性重试最多三次，之后保留 owner-local 手动重试。
- 根目录 `D:\code\sj\_bmad-output` 不属于 Git 仓库；Story 状态已更新但无法随 MC/RC 提交。
- 当前分支没有配置 Git upstream；收尾流程不会猜测或创建远端目标分支。
