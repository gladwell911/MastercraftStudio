---
title: '记录 Codex 与 KimiCode 跨端聊天审查及修复指南'
type: 'chore'
created: '2026-09-06'
status: 'done'
route: 'oneshot'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** 本次电脑端 `mc` 与手机端 `rc` 的 Codex/KimiCode 聊天审查发现了多项跨端协议、模型分派、Kimi 事件归属和消息可靠性问题；审查结论分散在会话记录中，后续没有当前上下文时难以组织修复。

**Approach:** 在 `mc/docs` 写入一份当前维护文档，完整记录审查范围、系统链路、每项问题的复现条件、证据、影响、修复方向、依赖关系、验收标准和建议实施顺序；该文档只记录问题与修复计划，不修改运行时代码或既有设计决策。

</frozen-after-approval>

## Implementation Notes

- 新增 `docs/cross-client-codex-kimicode-audit-2026-09-06.md`，以审查基线、跨端链路、
  不变量、按依赖排序的 P0/P1 工作包、14 项可复核发现、Given/When/Then 验收标准和
  最小回归集组织本次只读审查结果。
- 未修改任何运行时代码、协议实现、测试或外部配置。
- Blind Hunter 审阅后补充了双仓库完整基线 SHA 与环境、滚动升级兼容字段表、Kimi
  `prompt_id` 映射的持久化/早到事件规则、命令幂等状态机、request/response 关联规则、
  F-01 至 F-14 测试归属矩阵，以及历史聚合测试统计不可作为发布门禁的限制。

## Review Triage Log

- medium / 已修复：基线只固定 `mc` 提交而未固定 `rc` 和环境；现已记录双仓库完整 SHA、
  日期、时区、Windows、Python 与 Flutter 版本。
- medium / 已修复：`model_change_mode` 缺少兼容协议；现已定义类型、允许值、缺失/未知值、
  回显字段和新旧客户端行为。
- medium / 已修复：`prompt_id` 映射没有持久化、重连与早到事件策略；现已规定落盘时机、
  终态清理、有界暂存和安全回退。
- medium / 已修复：幂等记录没有命名空间与原子状态机；现已规定 `(client_id, command_id)`
  键、并发 claim、终态重放和 TTL/容量边界。
- medium / 已修复：request/response 缺少关联和导航语义；现已规定 inbox 生命周期、
  response 关联、乱序处理与导航不取消任务。
- low / 已修复：F-01 至 F-14 未映射到具体测试；现已增加自动化、fixture 与手工烟测矩阵。
- low / 已修复：历史通过/失败统计无法独立复现；现已标明基线、已知版本和原始记录缺口，
  并禁止将聚合统计作为发布门禁。
