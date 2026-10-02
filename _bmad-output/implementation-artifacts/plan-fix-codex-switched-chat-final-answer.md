---
title: '修复聊天切换后 Codex 最终回答丢失'
type: 'bugfix'
ticket: ''
created: '2026-10-02'
status: 'built'
baseline_revision: '6826233a6b7a3f5e8796c51cb90854c590842c99'
route: 'full'
route_source: 'auto'
review: 'quick'
review_source: 'user-scope-override'
lenses_ran: ['quick']
review_loop_iteration: 0
followup_review_recommended: false
context: ['D:/code/sj/mc/AGENTS.md']
warnings: []
deferred: []
---

<intent-contract>

## Intent

**Problem:** Codex 已完成回答，但活动聊天切走/归档时丢失聊天级 codex_context_generation，最终回答与完成事件被归属守卫拒绝，记录永久 pending。今天新建“指挥塔”第二轮真实事件与回合 generation 都为 2，归档聊天 metadata 缺该字段。

**Approach:** 局部修复活动状态持久化及归档字段传递，保留严格旧事件守卫；用真实归档、保存、重载和终答显示链路的回归测试锁住故障。

## Boundaries & Constraints

**Always:** 复用现有 ChatStore、wx fixture 与 worker ACK 路径；测试隔离数据；保留 chat/thread/turn/start/context generation 检查与前台焦点。已有归档需要更新当前代次而非 setdefault。

**Never:** 删除或放宽终答守卫、写真实聊天/笔记库、重发真实问题、顺带重构。此次实现不恢复历史生产记录，不部署或覆盖运行中的程序；交付修复源码和验证结果。

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| 新聊天切走 | 活动回合 generation=2，经实际归档后收到终答与 completed | 自己的回答入库 done，重开聊天回答列表显示 | 无 |
| 再次归档 | 已有归档 generation=1，当前活动 generation=2 | 归档更新为2，当前终答接收 | 无 |
| 保存重载 | 活动请求经 slim state 保存、ChatStore 重载 | 代次保持，合法终答可接收且持久化 | 无 |
| 旧事件 | 回合/聊天归属正确但事件 generation 过期，或清空后的旧终答 | 保持拒绝，不覆盖当前回答 | 保留现有检查 |

</intent-contract>

## Code Map

- `main.py:19708` `_archive_active_session` 手工组装 archived，缺 codex_context_generation；existing preserved.update 也会保留旧值。
- `main.py:4176` `_slim_active_chat_state` 未复制代次；`_persist_chat_history_to_store` 用 slim upsert，ChatStore metadata 会替换。
- `main.py:13332` `_on_codex_event_for_chat` 非当前终答专用代次守卫：不可移除。
- `tests/test_main_unit.py` 现有 `test_archived_clear_resend_real_ack_preserves_start_generation` 与 ChatStore fixture 可复用。
- `tests/test_codex_ui_responsiveness_automation.py`、`tests/test_codex_integration.py` 可复用真实 wx 与最终答测试，优先最小新增用例。

## Tasks & Acceptance

**Execution:**
- [x] 回归测试先在旧代码复现拒收，再修复 `main.py` 两处字段传递及确实必要的局部恢复路径。
- [x] 覆盖矩阵四行，至少一个 wx 测试走实际聊天切换后，查看目标聊天的回答列表。

**Acceptance Criteria:**
- Given 执行中的 Codex 聊天，when 切到其他聊天后收到权威 final/completed，then 回答归属于原聊天且 done、持久化重载可见。
- Given 新归档或已有旧代次的归档，when 再次归档当前代次，then 合法结果通过，过期结果仍拒绝。
- Given 活动聊天保存重载，when 恢复后的合法终答到达，then 保存的归属代次足以正确处理结果。

## Implementation Notes

预计产品修改少量，完整生命周期与原生 UI 回归超过100行，采用 full；已明确根因与修复方向，不重新开展深析。

- director 修复仅两行产品字段传递，新增5项单元回归和1项原生 wx 回归。integration 既有 fixture 缺回合代次，内存加载旧代码确认同样失败后补齐字段，未改变原断言。
- engineer 独立串行验证：新增unit 5、integration 31、wx 1、clear/resend 1，共38项通过。旧代码内存回放新增unit为3 failed/2 passed，证明测试捕获三条真实丢失路径。
- 用户 AGENTS.md 要求小改动不安排多轮全模块审查，优先于技能默认 full→四镜头。产品仅两行，采用单一 president quick 局部审查；不改变实施路线，不展开额外流程。

## Plan Change Log

## Review Triage Log

### 2026-10-02 — Quick review pass
- verdicts: 0 findings — high 0, medium 0, low 0, false 0, maybe-false 0
- president 核对实际 diff、调用链、归属守卫及隔离测试，未发现可确认缺陷或未满足验收项。

## Verification

- `.venv/Scripts/python.exe -m pytest tests/test_main_unit.py -k switched_chat_final -q`：新增生命周期回归全部通过，旧代码失败证据记录。
- `.venv/Scripts/python.exe -m pytest tests/test_codex_integration.py -q`：受影响模型事件流程通过。
- `.venv/Scripts/python.exe -m pytest tests/test_codex_ui_responsiveness_automation.py -k switched_chat_final -q`：原生 wx 回答列表可见性通过，串行运行。
- `git diff --check`：通过。

## Auto Run Result

- Status: built。修复活动聊天保存及归档丢失请求代次，后台终答不再因该字段缺失或陈旧被丢弃。未放宽旧事件守卫。
- 文件：`main.py` 两行字段传递；`tests/test_main_unit.py` 五项生命周期回归；`tests/test_codex_ui_responsiveness_automation.py` 一项实际聊天切换/回答列表回归；`tests/test_codex_integration.py` 补齐真实回合归属夹具；本计划记录实施和验证。
- 旧代码回归由 director 与 engineer 分别验证：新unit 3 failed、2 passed，失败分别为新归档、已有旧代次归档、保存重载。只在内存加载基线方法，未回滚源码或修改生产数据。
- engineer 最终严格串行验证：`test_main_unit.py -k switched_chat_final` 5 passed；`test_codex_integration.py` 31 passed；`test_codex_ui_responsiveness_automation.py -k switched_chat_final` 1 passed，无skip；`test_main_unit.py -k archived_clear_resend_real_ack_preserves_start_generation` 1 passed。两个 diff --check 通过。首次integration/UI启动短暂重叠的结果未作为最终证据，已完整串行重跑。
- 矩阵覆盖：新/旧归档均完成终答入库，保存重载保持归属；过期及清空旧事件仍拒绝；原生wx测试确认切回回答列表可见，并保留前台、焦点、草稿。
- Review: president quick，无 findings、patch、defer 或 reject。followup_review_recommended=false。
- 范围边界：未重新打包或更新安装目录；未自动修复此前丢失的历史回答；未执行真实模型请求。新测试通过受控事件覆盖本次实际受影响链路，不声称全部聊天问题永不发生。
