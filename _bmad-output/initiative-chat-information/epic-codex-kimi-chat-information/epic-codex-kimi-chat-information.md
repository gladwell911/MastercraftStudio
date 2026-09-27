---
type: epic
title: "桌面端查看 Codex 与 Kimi 聊天信息"
parent: initiative-chat-information
covers: [CAP-1, CAP-2, CAP-3, CAP-4, CAP-5, CAP-6, CAP-7]
after: []
risk: medium
---

# 桌面端查看 Codex 与 Kimi 聊天信息

## Description

在桌面端交付独立、可键盘浏览的聊天信息列表，并接入两种引擎的上下文、累计消耗及账号限额数据。

## Outcome

当前 Codex 或 Kimi 聊天的信息可在不中断任务、不扰动键盘焦点的情况下查看。

## Requirements

- CAP-1：从顶部“应用(&A)”菜单打开当前 Codex/Kimi 聊天信息列表，方向键浏览，Esc 关闭并恢复焦点。
- CAP-2：显示当前上下文百分比和已用／总窗口 token，采用最新权威数据。
- CAP-3：显示当前原生 thread/session 的累计 token，且与上下文占用分开。
- CAP-4：Codex 仅显示主 `codex` 类别的周限额剩余百分比和具体重置时间。
- CAP-5：Kimi 分别显示五小时及七日已用百分比；前者给具体重置时间，后者给距重置时长。
- CAP-6：事件与低频账号查询刷新可见内容，只有文本变化时更新，不扰动选择和焦点。
- CAP-7：未登录、不适用、字段缺失、查询失败或过期数据各有明确状态。

## Done when

1. 当前 Codex/Kimi 聊天的菜单、列表和 Esc 键盘路径满足 CAP-1，其他模型不提供该入口。
2. 两家上下文和累计 token 的口径、动态更新及聊天归属满足 CAP-2、CAP-3。
3. Codex 主类别周限额及 Kimi 五小时／七日限额满足 CAP-4、CAP-5；缺值不显示为零。
4. 运行中刷新、会话和账号切换、网络失败及关闭窗口后的异步结果满足 CAP-6、CAP-7。
5. 按 AGENTS.md 串行运行相关 GUI 自动化和模型流程回归，屏幕阅读器键盘焦点保持稳定。

## Boundaries

桌面端菜单、独立列表、Codex/Kimi 客户端用量解析与状态读取。遵守规格 Non-goals；不改发送、停止或移动端逻辑。

## References

- spec — _bmad-output/specs/spec-codex-kimi-chat-information/SPEC.md, Capabilities and Constraints
- contract — _bmad-output/specs/spec-codex-kimi-chat-information/data-contract.md, 数据与界面契约
- project — AGENTS.md, Project Instructions

## Notes

- Decision: 首个步骤贯通菜单、列表和 Codex 上下文一行；后续步骤按共享 UI 依赖串行推进。
- Decision: Codex 仅显示主 `codex` 类别的周窗口，用户已确认。
- Assumption: 本地仓库文件作为 ticket store；未连接外部看板。
