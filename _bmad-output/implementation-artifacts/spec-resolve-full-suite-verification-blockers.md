---
title: '修复全量回归验证阻塞'
type: 'bugfix'
created: '2026-09-05'
status: 'in-progress'
review_loop_iteration: 0
baseline_commit: '9c28e5acf93b26c3b7b6e851a5071a514fb50659'
context: ['AGENTS.md', 'README.txt']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Kimi WebSocket 修复无法发布，因全量非实时回归被多组过时测试契约阻塞，另有 Codex 最终答案重绘与执行占位符两个真实可访问性回归。

**Approach:** 用已提交的产品语义更新测试，并修正 Codex 的两个回归。保留 Kimi 修复，不为了绿色测试撤销已交付的 UI 行为。

## Boundaries & Constraints

**Always:** 保留 time row、历史仅查看不切换 active chat、置顶优先排序、Cloudflared 公网隧道验证、Kimi 心跳修复；测试不得依赖安装真实 Claude CLI。

**Ask First:** 如需改变历史导航语义、对外 CLI/Cloudflared 契约，停止并请求确认。

**Never:** 不删时间行，不恢复 `_switch_current_chat`，不绕过隧道验证，不读写真实 Kimi 用户数据。

## Code Map

- `main.py:4659-4665` 插入 time meta；`main.py:14710-14749` 定义 Ctrl 历史查看；`main.py:2174-2183` 验证隧道。
- `main.py:5085-5088` 与 Codex final-answer 路径不应全量重绘；执行步骤首次显示必须替换“暂无执行过程”。
- `tests/test_chat_attachments_ui_automation.py`、`tests/test_integration_context.py` 含过时行号、排序、hotkey/API 断言。
- `tests/test_file_manager_ui_unit.py` 仍 mock 旧 Cloudflared helper。
- `tests/test_claudecode_client_unit.py` 有四项未 mock CLI resolver；Codex 测试需同步 refresh 参数、time meta 与 main-loop 调度契约。

## Tasks & Acceptance

**Execution:**
- [ ] 修改 attachment、context、file-manager 测试，按当前 meta、历史视图和隧道契约断言。
- [ ] 修改 Claude 测试为显式 fake CLI。
- [ ] 修改 `main.py` 和 Codex 测试：final answer 追加而非全量渲染；真实执行步骤替换占位行。
- [ ] 运行聚焦组、Codex 组和全量非实时回归。

**Acceptance Criteria:**
- Given 已交付的 UI 语义, when 运行这些测试, then 不要求已移除的 API 或旧行号。
- Given 未安装 Claude CLI, when 运行 Claude 测试, then 通过 fake 验证行为。
- Given Kimi 修复与更新契约, when 运行全量非实时回归, then 完整退出且无阻塞失败。

## Design Notes

基线修订：`9c28e5acf93b26c3b7b6e851a5071a514fb50659`。测试更新应追随已提交的用户行为；不把其作为回滚产品设计的借口。

## Verification

- `.venv\\Scripts\\python.exe -m pytest tests/test_chat_attachments_ui_automation.py tests/test_integration_context.py tests/test_file_manager_ui_unit.py -q` -- 通过且不遗留进程。
- `.venv\\Scripts\\python.exe -m pytest tests/test_claudecode_client_unit.py tests/test_claudecode_manager_integration.py tests/test_cli_agent_manager_unit.py tests/test_codex_client_unit.py tests/test_codex_integration.py tests/test_codex_worker_client.py tests/test_codex_worker_process.py tests/test_codex_worker_protocol.py -q` -- 不需真实 Claude CLI。
- `.venv\\Scripts\\python.exe -m pytest -m 'not live' -q` -- 完整退出。
