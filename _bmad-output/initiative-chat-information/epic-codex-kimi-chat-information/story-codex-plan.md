---
title: '打通聊天信息菜单、列表和 Codex 上下文'
type: 'feature'
ticket: '1'
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
baseline_revision: 'bc7f4e6cb48911f1146c95f1f3df71b26b16239d'
---

<intent-contract>

## Intent

**Problem:** Codex/Kimi 聊天没有独立的键盘可读信息入口，且 Codex 现有用量解析把累计 token 当成上下文占用。

**Approach:** 在应用菜单提供当前聊天专用信息列表，以聊天、原生会话和账号身份绑定数据；用 Codex 最近调用用量与动态窗口显示第一行。

</intent-contract>

## Implementation Notes

- 采用 oneshot：此故事限于一个列表和 Codex 上下文首行，后续累计及额度属于独立故事。
- `codex_client.py` 解析 v2 `tokenUsage.last` 和旧 `last_token_usage`；缺窗口时保持未知。`main.py` 加入菜单和独立信息列表，绑定聊天、原生会话、账号标识并保护焦点。
- president 审查发现 Codex 清理或换 thread 后旧用量可能留在聊天上。现清除该聊天的上下文与待应用用量，并在已打开面板中绑定新 thread，显示暂不可用直到新用量到达。
- 第二轮审查确认旧 thread 的迟到 token 事件可能重新写入新面板；现以聊天 generation、已退休 thread 列表和 worker turn 索引隔离，并在真实允许的 ack 前保留归属明确的早到用量。
- `tests/test_chat_information_ui_automation.py` 覆盖原生方向键、Esc、焦点恢复、无变化刷新，以及聊天、原生会话、账号标识切换后旧结果不得写入新列表。菜单打开后若模型改变，执行入口会重新校验。按 president 审查方案更新旧 context UI/E2E 测试中对已有时间行的固定索引，未改生产时间行。

## Plan Change Log

## Review Triage Log

## Verification

- Final independent check: 189 passed, 0 failed (138 related tests and 51 targeted main tests).
- `py -3.11 -m py_compile main.py codex_client.py codex_worker_process.py` passed.
- `git diff --check` passed.
- President and dog reviewed the identity and late-event handling. Their findings were corrected; final dog review found no blocking issue.
- A previous broad `test_main_unit.py` run had 31 unrelated baseline failures. Related targeted tests pass.

## Auto Run Result

- Implemented the Codex/Kimi chat-information menu and keyboard-readable list, with chat, native-thread, and account identity checks and focus restoration.
- Parsed Codex latest-turn token usage and dynamic context window. Guarded worker token events and acknowledgements with stable turn scope and context generation; capped worker scope maps at 256 entries.
- Changed `codex_client.py` (usage parsing), `codex_worker_process.py` (event ownership), and `main.py` (menu, information list, usage state and event guards). Updated related unit, GUI and E2E tests.
- Review: president and dog findings were fixed; final dog review found no blocking issue. No items deferred. Follow-up review is not recommended.
- Verification: 189 passed, 0 failed in the final independent run; compilation and diff checks passed.
- Residual risk: the broad main test suite has unrelated baseline failures outside Story 1.1.
