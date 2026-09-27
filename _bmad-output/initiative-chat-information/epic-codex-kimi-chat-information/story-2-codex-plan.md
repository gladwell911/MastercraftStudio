---
title: '接入 Codex 累计消耗和主周限额'
type: 'feature'
ticket: '2'
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
baseline_revision: '703a4b70b019c14374a65e5623fb1e330f3c3f4f'
---

<intent-contract>

## Intent

**Problem:** Story 1.1 的信息列表只有 Codex 当前上下文，缺少当前原生 thread 的累计 token、账号和 Codex 主周限额。
**Approach:** 从 token 事件的独立累计字段读取 thread 总量；经 Codex worker 异步读取账号和主周限额，按聊天、thread、账号及请求代次校验回包，在现有信息列表显示独立状态。

</intent-contract>

## Implementation Notes

- 仅做 Story 1.2；Kimi 数据源与额度留给后续故事。
- Codex v2 `tokenUsage.total.totalTokens` 与旧 `info.total_token_usage.total_tokens` 独立解析，缺失不从 `last` 或输入输出组件猜测。
- 总量沿用 Story 1.1 的 native thread、turn index、generation guard；clear 或换 thread 时清除。
- 额度读取经 worker，避免 UI 线程同步请求；只选择 codex bucket 中 10080 分钟的窗口，将 `usedPercent` 转成剩余百分比，并将 `resetsAt` 转本地时间。
- 账号未登录、API key、周窗缺失、字段缺失与查询失败分别显示；相同文本不重建列表。

## Plan Change Log

## Review Triage Log

### 2026-09-27 — Review pass

- President identified that the first account/quota request could run before its worker started. Fixed by starting the worker on a daemon thread and checking generation and identity on completion; the GUI test verifies focus remains responsive.
- President identified that account A→B left the old native thread attached. Fixed by retiring the old thread/turn owner and rejecting late token events; the GUI regression covers both cumulative and context usage.
- Final president review found no blocking issue. No item was deferred; follow-up review is not recommended.

## Verification

- `py -3.11 -m pytest tests/test_codex_client_unit.py tests/test_codex_worker_process.py tests/test_codex_worker_client.py tests/test_codex_worker_protocol.py tests/test_context_usage_unit.py tests/test_context_usage_ui_automation.py tests/test_context_usage_e2e.py tests/test_chat_information_ui_automation.py tests/test_main_unit.py -q -k "not test_main_unit or clear_context or codex_token_count or context_usage or codex_clear_command or codex_worker_thread_state or codex_weekly_quota"` — 197 passed, 913 deselected.
- `py -3.11 -m py_compile main.py codex_client.py codex_worker_client.py codex_worker_process.py codex_worker_protocol.py` — passed.
- `git diff --check` — passed.

## Auto Run Result

Built Codex session cumulative total and asynchronous account/weekly quota rows. The first worker start happens in a daemon thread, so opening the panel retains keyboard focus. Tests cover legacy/v2 totals, weekly-window selection, account and missing-field states, late replies, thread reset, account-switch retirement of old native thread/turn, and unchanged-list behavior. Independent engineer verification: 197 passed, 0 failed; compilation and diff checks passed. President final review found no blocking issue. Real Codex app-server E2E was not run. The broad main suite retains unrelated historical baseline failures; this run verified the Story 1.2 focused selection. The external `tickets.py` naming fix was made under `C:\Users\gladwell\.agents\skills` and is outside this repository commit.
