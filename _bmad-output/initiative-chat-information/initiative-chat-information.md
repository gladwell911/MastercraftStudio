---
type: initiative
title: "Codex 与 Kimi 聊天信息可查询"
parent: none
covers: [CAP-1, CAP-2, CAP-3, CAP-4, CAP-5, CAP-6, CAP-7]
risk: medium
---

# Codex 与 Kimi 聊天信息可查询

## Description

桌面端在当前 Codex 或 Kimi 聊天中提供可靠的上下文、累计 token 和账号额度信息；完整需求由现有规格定义。

## Outcome

用户打开“应用(&A)”中的“查看聊天信息”，通过键盘浏览当前聊天的准确用量与额度，关闭后回到原焦点。

## Done when

1. Codex 和 Kimi 聊天都能显示规格 CAP-1 至 CAP-7 定义的信息及缺失状态。
2. 运行中和空闲时的数据归属正确，更新不干扰列表选择或键盘焦点。
3. Esc 关闭信息列表并恢复焦点，相关模型与 UI 回归测试通过。

## Boundaries

仅桌面端聊天信息与相应的用量读取；不改变任务执行、消息路由或手机端。单一 epic 负责 UI、两家协议接入和验证。

## References

- spec — _bmad-output/specs/spec-codex-kimi-chat-information/SPEC.md, Capabilities
- contract — _bmad-output/specs/spec-codex-kimi-chat-information/data-contract.md, 数据与界面契约

## Notes

- Decision: 采用一个 epic；所有实施步骤涉及同一桌面 UI，按依赖顺序交付。
