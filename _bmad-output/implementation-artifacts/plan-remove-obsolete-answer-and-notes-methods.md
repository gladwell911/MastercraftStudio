---
title: '清理被覆盖的回答详情和旧笔记方法'
type: 'refactor'
ticket: ''
created: '2026-11-19'
status: 'done'
baseline_revision: 'd5864ef1948965bd457394219f3e456ee2d823c6'
route: 'full'
route_source: 'auto'
review: 'thorough'
review_source: 'auto'
lenses_ran: [blind-hunter, edge-case-hunter, verification-gap, intent-alignment]
review_loop_iteration: 0
followup_review_recommended: false
context: ['{project-root}/AGENTS.md']
warnings: [multiple-goals]
deferred: []
---

<intent-contract>

## Intent

**Problem**　回答详情有一个被覆盖的定义，笔记存储保留了旧的私有远程操作路径，增加了查找和维护成本。

**Approach**　从 `d5864ef1948965bd457394219f3e456ee2d823c6` 开始，按文件移除这两组代码，用现有测试分别记录改动前后的行为。

## Boundaries & Constraints

**Always**　保留回答详情过滤、键盘入口和笔记远程写入、重放、冲突及旧库迁移行为。wx 测试串行，笔记夹具使用临时数据。

**Never**　本批仅删除指定方法，整份 `notes_store_impl.py`、结构迁移及安装发布属于后续工作。

</intent-contract>

## Code Map

以下代码位置对应基线提交。

- `main.py:4212` 是旧的 `_build_answer_detail_html(self, answer_md)`。`main.py:10301` 的同名定义接受 `model` 并负责现行过滤。
- `notes_store.py:1107` 的 `apply_remote_op` 通过带 `_conn` 的私有方法在一个 SQLite 事务内执行。文件末尾 `notes_store.py:1390` 起的旧方法组没有现行入口。同名方法在 `notes_store_impl.py` 属于另一个类。
- 两处删除合计 267 行。自动选择 full，交给 director 实施，使用原有测试验证。

## Tasks & Acceptance

**Execution**

- [x] `main.py` 删除首个 `_build_answer_detail_html` 和相邻分隔空行，保留现行实现与调用路径。
- [x] `notes_store.py` 删除末尾 `_remote_result`、`_apply_remote_entry_op`、`_write_entry_remote_update`、`_create_entry_conflict_copy`、`_apply_remote_notebook_op`、`_write_notebook_remote_update`、`_create_notebook_conflict_copy`。

**Acceptance Criteria**

- Given 用户打开模型回答，when 使用 Enter 或 Shift+Enter，then 原生详情、网页详情和模型文本过滤表现与基线一致。
- Given 临时笔记库包含旧库数据或已处理的远程操作，when 运行迁移、回放和冲突场景，then 数据结果与基线一致，现行事务和兼容响应保留。
- Given 两组定向测试的基线已记录，when 分别完成两处删除，then 逐组复测无新增失败，生产差异仅有指定删除。

## Implementation Notes

- `main.py` 删除 14 行，`notes_store.py` 删除 253 行。两份生产文件共净减 267 行。
- director 串行跑了改动前后两组用例。回答组均通过 6 项，笔记组均通过 31 项。笔记测试使用临时库。

## Plan Change Log

## Review Triage Log

### 2026-11-19 — Review pass

- verdicts: 5 findings — high 0, medium 0, low 5, false 0, maybe-false 0
- findings:
  - `[low]` `[reject]` 键盘测试和 HTML 过滤测试分别运行。Git 差异与 AST 核对确认实际网页生成仍传入原聊天模型，未发现调用链回归。
  - `[low]` `[reject]` 首轮选择器未包含字符钩子的普通 Enter 用例。追加运行该既有用例并通过。
  - `[low]` `[reject]` 定向笔记测试未注入回放登记失败。当前 `BEGIN IMMEDIATE`、登记及提交范围经 AST 核对整体一致，回放与版本冲突用例通过。本批没有新的事务路径。
  - `[low]` `[reject]` 首轮选择器未包含旧端文档位置兼容用例。追加运行该既有用例并通过。
  - `[low]` `[reject]` 暂存后直接 `git diff` 的检查范围偏窄。实际采用 `git diff --cached --check` 和 `git diff --cached --stat` 覆盖生产差异，均已通过。

## Verification

- `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_main_unit.py -k "answer_detail_html_filters_cli_models or answer_shift_enter_keeps_web_detail_shortcut or answer_char_hook_shift_enter_opens_web_detail or answer_enter_opens_plain_text_viewer_instead_of_web_detail"`
- `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_notes_desktop_unit.py tests/test_notes_remote_hardening_unit.py -k "legacy or migrat or remote or conflict or document_cache"`
- `git diff --check`、`git diff --stat`，并用 Python AST 核对只删除指定定义。两组测试均先于源码改动运行，再按文件复测。

## Code Review

### 2026-11-19 · 独立复审

**Code review complete.** 0 decision-needed, 0 patch, 0 defer, 0 rejected.

- 新 dog 使用 `bmad-code-review` quick，唯一审查镜头通过，无需修复。
- Python AST 对照基线确认只删除指定的 8 个方法定义，`git diff --check` 通过。
- 串行复跑回答组 6 项和笔记组 31 项，另跑 `test_answer_char_hook_enter_opens_plain_text_viewer` 和 `test_notes_sync_old_document_preserves_existing_placement_v2`，全部通过。快捷键路由和 HTML 模型过滤由独立用例验证，笔记用例使用临时 SQLite 数据库。
- 验证覆盖源码，实体读屏和打包未测试。

Rejected: 无。

## Auto Run Result

- `main.py` 和 `notes_store.py` 合计净删 267 行。AST 对照基线确认仅移除指定的八个定义。
- 回答组 6 项和笔记组 31 项在基线、实施后和主会话复测均通过。额外两项既有用例通过。暂存差异检查通过。
- 内置审查的五项低级建议裁决见 Review Triage Log。独立审查也已完成，结果见 Code Review。
- 验证覆盖源码和临时数据。独立冻结包、实体读屏和日常安装切换属于后续结构迁移的验收范围。
