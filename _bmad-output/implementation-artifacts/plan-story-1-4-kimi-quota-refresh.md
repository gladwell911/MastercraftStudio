---
title: '修复 Kimi 聊天信息额度刷新'
type: 'bugfix'
ticket: ''
created: '2026-09-28'
status: 'built'
route: 'oneshot'
route_source: 'auto'
review: 'quick'
review_source: 'auto'
lenses_ran: ['quick']
review_loop_iteration: 0
followup_review_recommended: false
context: []
warnings: []
deferred: []
baseline_revision: 'f502ae19bfbcfa18942ca530b9538e5e3c7e4479'
---

<intent-contract>

## Intent

**Problem:** Kimi 任务完成时可见的聊天信息窗口没有立即重查 OAuth 额度；相同成功结果也不会更新真实查询时间；七日倒计时缺少定时器 GUI 回归。

**Approach:** 在两个任务完成路径复用现有可见额度请求及其身份/代次守卫；收到每次有效成功结果时更新查询时间，仍仅在显示行变化时刷新列表；补充真实 wx 列表和计时器测试。

</intent-contract>

## Implementation Notes

- Oneshot：预计仅修改 `main.py` 和聊天信息 GUI 自动化测试，约 50 行；沿用现有额度请求和列表 `set_rows` 的文本比较。
- 任务完成仅触发可见窗口的额度请求，不触碰其他聊天或后台窗口。
- 两条 Kimi turn 完成路径都调用已有 `_request_kimi_quota`，由它验证面板身份。有效相同额度回复仍更新查询时间；仅显示行变化时调用局部刷新，列表内部继续比较文本避免无变化 `Set`。
- `tests/test_chat_information_ui_automation.py` 新增活动/历史完成事件、相同额度时间戳和七日倒计时定时器回归。

## Plan Change Log

## Review Triage Log

### 2026-09-28 — Review pass
- verdicts: 1 finding — high 0, medium 1, low 0, false 0, maybe-false 0
- findings:
  - `[medium]` `[patch]` `kind=error` 且 `failed=False` 的额度响应也更新成功查询时间 — 后台明确可能返回该状态；已将时间戳更新限定为 `kind=ok`，并补相同错误回包回归。

## Verification

**Commands:**
- `D:/code/sj/.build-envs/mc-py311/Scripts/python.exe -m pytest tests/test_chat_information_ui_automation.py tests/test_kimi_server_client_unit.py -q` — GUI 与 Kimi 客户端回归通过。
- `git diff --check` — 无空白错误。

## Auto Run Result

- 活动与历史 Kimi turn 完成时，信息窗口可见且身份匹配才立即发起 OAuth 额度重查。
- 有效相同成功额度回复更新实际查询时间；显示文本不变时不刷新列表或改变焦点。错误回复不记为成功查询时间。
- `main.py`：完成事件触发和额度更新时间逻辑。`tests/test_chat_information_ui_automation.py`：补活动/历史完成、七日倒计时与相同回复回归。
- Quick 审查：1 项 medium 已修复；0 项延期，0 项拒绝。补丁级修复 1 项 medium；无需后续审查。
- 验证：聊天信息 GUI 与 Kimi 客户端 116 passed；`git diff --check` 通过。
- 限制：使用受控 Kimi 响应验证，未运行真实服务或账号的端到端验收。
