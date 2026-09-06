---
title: '修复并发 Kimi 会话事件误路由导致无回复'
type: 'bugfix'
created: '2026-09-06'
status: 'done'
review_loop_iteration: 0
baseline_commit: '6f62dc841f55bcfe77b91721c8e84d9cb2af7fe0'
context:
  - '{project-root}/AGENTS.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** 一个 Kimi 聊天执行时，新建另一 Kimi 聊天并提问，服务端虽正常生成答案，桌面端却可能按仅在 session 内唯一的 `turn_id` 误选旧聊天，再因 `session_id` 不匹配丢弃正文事件，界面永久停留在“正在请求...”。

**Approach:** 以 `(session_id, turn_id)` 作为事件身份：先限定 session，再匹配 turn；仅在事件缺少 session 且无歧义时允许 turn 回退。没有正文的完成事件不得把占位回复标成成功。

## Boundaries & Constraints

**Always:** 保持多聊天并发；事件只更新所属聊天；后台事件不得抢焦点或无必要重绘；保留现有事件批处理。

**Ask First:** 若需改变 Kimi 进程/协议、数据库结构或限制为单会话，先征求用户决定。

**Never:** 不以串行化规避问题；不改无关 provider 路径；不保存 `done + 正在请求...` 的假完成。

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| 同号 turn | 两个 session 都运行 turn `0` | 事件各自落入正确聊天 | session 不匹配时不跨聊天回退 |
| 无正文完成 | `prompt.completed` 无 turn id/正文 | 占位 turn 不标成功 | 保持可恢复或明确失败 |
| 后台完成 | 当前查看新聊天，旧聊天完成 | 旧聊天保存答案，前台焦点稳定 | 走既有延迟刷新 |

</frozen-after-approval>

## Code Map

- `main.py:10308` `_known_kimi_event_chat_id` — 当前先全局匹配 turn 再匹配 session，是根因入口。
- `main.py:10374` `_resolve_kimi_event_chat_id` — 总路由，应优先 session 与活跃元数据。
- `main.py:10403` `_kimi_event_is_compatible_with_chat` — 误选旧聊天后在此丢弃新事件。
- `main.py:10544` `_apply_kimi_turn_started` — 写回 session/turn 身份。
- `main.py:10598` `_finalize_kimi_turn_state` — 无正文仍标 done，需完成态保护。
- `main.py:10868` `_on_kimi_event_for_chat` — 保持当前/后台 UI 隔离。
- `tests/test_kimi_integration.py:270` — 复用后台聊天无重绘断言并增加同号 turn 回归。
- `D:/code/cx/history/chat_history.db` — 现场只读证据：新聊天两回合均为 `done`、占位正文、空 turn id。
- `C:/Users/gladwell/.kimi-code/server/events/session_cc98f7eb-ae9e-45a1-b43a-c021c89c50fd.jsonl` — 服务端两回合正常完成，REST `/messages` 可读完整答案。

## Tasks & Acceptance

**Execution:**
- [x] `main.py` — 实现 session-first 路由与无正文完成保护，不改变后台 UI 约束。
- [x] `tests/test_kimi_integration.py` — 覆盖两 session 同 turn id、交错事件和无正文完成。
- [x] `tests/test_kimi_ui_responsiveness_automation.py` — 补充三元 answer key 与焦点稳定断言，复验后台事件不重绘、不抢焦点。

**Acceptance Criteria:**
- Given 两个 Kimi session 都使用 turn `0`，when 事件交错到达，then 每个聊天只得到自己的 turn id、正文和完成状态。
- Given 旧聊天后台执行，when 任一聊天产生事件，then 当前聊天选择、焦点和列表重绘保持可访问性约束。
- Given 完成事件无正文且本地仍为占位符，when 完成逻辑执行，then 不持久化为空答案成功。

## Spec Change Log

## Design Notes

`turn_id` 仅在单个 Kimi session 内唯一。有 `session_id` 时禁止仅凭 turn 跨 session 匹配；缺少 session 时也只能在唯一候选下回退。

## Verification

**Commands:**
- `python -m pytest tests/test_kimi_integration.py -q` — 并发路由与完成态通过。
- `python -m pytest tests/test_kimi_ui_responsiveness_automation.py -q` — 无焦点/重绘回归。
- `python -m pytest tests/test_kimi_server_client_unit.py -q` — 客户端队列与线程安全无回归。

## Suggested Review Order

**路由**

- session-first
  [main.py:10308](../../main.py#L10308)

- 复合定位
  [main.py:10421](../../main.py#L10421)

**状态**

- 答案隔离
  [main.py:10486](../../main.py#L10486)

- 完成所有权
  [main.py:10598](../../main.py#L10598)

**回归**

- 跨聊天竞争
  [test_kimi_integration.py:270](../../tests/test_kimi_integration.py#L270)

- 无会话唯一
  [test_kimi_integration.py:340](../../tests/test_kimi_integration.py#L340)

- 同聊天复用
  [test_kimi_integration.py:360](../../tests/test_kimi_integration.py#L360)

- 空正文保护
  [test_kimi_integration.py:416](../../tests/test_kimi_integration.py#L416)

- 后台焦点
  [test_kimi_ui_responsiveness_automation.py:239](../../tests/test_kimi_ui_responsiveness_automation.py#L239)
