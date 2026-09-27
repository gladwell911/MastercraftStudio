---
title: '接入 Kimi 上下文和会话累计消耗'
type: 'feature'
ticket: '3'
created: '2026-09-27'
status: 'built'
route: 'oneshot'
route_source: 'auto'
review: 'quick'
review_source: 'auto'
lenses_ran: []
review_loop_iteration: 0
followup_review_recommended: false
context: []
warnings: []
deferred: []
baseline_revision: '2c1c106f18c5d2469b20896564315edda315fb9f'
---

<intent-contract>

## Intent

**Problem:** Kimi 聊天信息列表尚无实时上下文和原生 session 累计 token。
**Approach:** 从 status 事件或后台 status 恢复当前上下文；从 snapshot 的 session.usage 读取 input_tokens 与 output_tokens 之和；按聊天、session、账号和请求代次约束异步结果。

</intent-contract>

## Implementation Notes

- 仅实施 Story 1.3；Kimi OAuth 额度留给 Story 1.4。
- status 缺窗口时保留已用 token，面板标记窗口未知；压缩后的下降值为有效更新。
- snapshot 仅计 input_tokens + output_tokens，两个字段必须是非负整数；不加入 cache 子项。
- 面板打开和 turn 完成时后台查询 status 与 snapshot；两种结果及失败状态独立应用。
- clear、新 session、聊天或账号身份变化后拒旧结果，清除旧 session 数值；无文本变化不重建列表或移动焦点。

## Plan Change Log

- Restrict turn-completion refreshes to the visible information panel so background chats do not start a Kimi query.
- Apply REST status only when no newer live status event has arrived; reject live status events from an old native session. Keep snapshot application independent.

## Review Triage Log

- President reviewed the refresh timing and old-session ownership fixes. Final review found no blocking issue.
- Five Kimi execution-summary integration failures were reproduced with the new information refresh disabled and are an existing baseline limitation.

## Verification

- Independent engineer verification: 226 focused tests passed; Kimi integration with the five established baseline failures deselected: 87 passed, 5 deselected. Total: 313 passed, 0 failed.
- `git diff --check` and `py_compile` passed.
- Live Kimi service end-to-end verification was not run.

## Auto Run Result

Story 1.3 is built and independently verified. Kimi context and native session totals appear in chat information with guarded background refresh and stable focus.
