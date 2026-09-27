---
title: 'Refactor sweep'
type: 'refactor'
ticket: '5'
created: '2026-09-27'
status: 'built'
route: 'oneshot'
route_source: 'auto'
review: 'quick'
review_source: 'auto'
---

<intent-contract>

## Intent

审查 Story 1.1–1.4 的用量映射、刷新和 UI 路径，仅清理可证实的重复实现，不增加新功能。

</intent-contract>

## Audit and Changes

- 阅读前四项计划和审查记录。Kimi 实时事件与 REST status 都映射同一上下文值；抽出纯值解析器，REST 不再构造虚假的 `CodexEvent`。
- Codex 定时器只发起后台额度请求；去掉请求前的无意义列表刷新。Kimi 定时器仍在本地更新七日倒计时，并查询额度。
- 信息窗口的两个 session rebind 入口服务于不同调用时机和数据清理契约，未合并。其它 Codex/Kimi 用量来源有明确区别，未强行统一。
- 保留现有对话、session、账号、generation 和焦点守卫；列表文本未变时不调用 `Set`。

## Verification

- Director 自检：聊天信息 GUI 与 Kimi client 单元测试 106 passed；相关 main 选择集 26 passed；`git diff --check` 通过。
- 独立 engineer 验证：141 passed，1 项已知基线 deselected；`py_compile` 与 `git diff --check` 通过。

## Review and Result

- 当前整理仅涉及 Kimi 用量解析复用和 Codex 定时器请求前的 UI 工作；未引入新数据源或功能。
- President 审查完成，未发现阻塞项。
- Story 1.4 已提交为 `68082ce`；Story 1.5 实现、验证和审查已完成，进入本地提交及后续发布验证。
