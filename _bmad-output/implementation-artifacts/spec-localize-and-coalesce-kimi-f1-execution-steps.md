---
title: 'Kimi Code F1 执行过程中文化与流式步骤归并'
type: 'bugfix'
created: '2026-09-06'
status: 'done'
baseline_revision: 'c03e9b100b3e0ec61d6c225824e1ba8f576dd6fa'
review_loop_iteration: 0
followup_review_recommended: true
context:
  - '{project-root}/AGENTS.md'
warnings: [oversized]
deferred: []
---

<intent-contract>

## Intent

**Problem:** Kimi Code 执行时，F1 执行过程列表直接暴露模型生成的英文 `thinking.delta`，而协议中的状态事件又会把同一句流式文本提前截断成多个持久化条目，导致列表中英混杂、语句破碎，无法稳定表达任务的主要步骤。

**Approach:** 将 Kimi 原始思考文本降级为非公开详情信号，以结构化事件合成简短中文步骤；同时修正 delta 的字符保真、流身份和刷新边界，使一个逻辑过程段只形成一个条目。F1 列表展示“正在分析、正在搜索、正在读取/修改、正在执行测试、正在整理回答”等主要阶段，命令、路径和原始诊断保留在详情而不强行翻译。

## Boundaries & Constraints

**Always:** 保持 Kimi 多聊天事件隔离和后台批处理；无可见变化时不得重绘；F1 更新不得改变键盘焦点或用户当前选中项；列表标题使用中文结构化摘要，代码、命令、路径和错误原文允许出现在详情；同一逻辑流的片段拼接必须字符级保真。

**Block If:** 只有引入联网翻译服务、修改 Kimi 安装目录/全局配置、改变聊天存储结构或需要把模型隐藏思考全文公开时才阻塞。

**Never:** 不按“包含英文字母”粗暴过滤；不依赖追加中文提示词保证模型内部 thinking 的语言；不把 `thread_status_changed`、usage 更新或静默 session 通知当成文本刷新边界；不让 thinking、assistant 与 tool progress 共用同一缓冲流。

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| 中文主要步骤 | Kimi 返回英文 thinking，并产生搜索、文件、命令或测试工具事件 | F1 列表只显示对应的简短中文阶段摘要；原始工具参数留在详情 | 未识别的结构化事件显示通用中文步骤，不回退展示英文 thinking |
| 状态交错 | `thinking("Question") → status → thinking(": can…")` | 状态事件不产生条目、不切断逻辑流，最终最多一个“正在分析问题”步骤 | 缺少 turn/item id 时仍按 chat、来源类型及可用序列身份隔离 |
| 来源切换 | 同一 turn、空 item id 的 thinking 后紧跟 assistant | 思考与最终回答互不串接；assistant 只进入回答聚合，不作为原始英文过程行 | turn 完成时分别清理残余缓冲 |
| 空格与重放 | delta 为 `"The"`、`" user"`，或 offset 事件重复到达 | 内部归并结果保持 `"The user"`，重复片段不产生重复步骤 | 无 offset 时保持既有有序追加语义 |
| 后台聊天 | 非当前聊天收到相同交错事件 | 正确持久化所属聊天，当前列表、焦点和选择不变化 | 切回该聊天后重建一次正确列表 |

</intent-contract>

## Code Map

- `kimi_server_client.py:123-157, 188-420` -- `KimiEvent` 与 `map_session_event()`；当前 `_str().strip()` 用于 delta，且映射数据未完整保留 source kind/agentId/offset。
- `kimi_server_client.py:984-1004` -- `_enqueue_event()` 仅合并严格相邻 delta；状态事件会自然打断客户端侧合并，但不应成为 UI 语义边界。
- `main.py:5709-5860` -- 执行详情与列表标题生成；在这里为 Kimi 结构化事件生成稳定中文摘要，并把原始参数留在详情。
- `main.py:6353-6399` -- `_buffer_execution_delta()` / `_flush_execution_delta()`；缓冲键缺少 `display_kind`，刷新时又将来源统一改成 commentary。
- `main.py:6561-6605` -- `_should_show_execution_step()`；当前隐藏 command/tool 等结构化步骤、主要展示 commentary，应调整为展示 Kimi 中文步骤。
- `main.py:10868-11035` -- `_on_kimi_event_for_chat()`；当前所有非 delta（包括 status）都会触发 flush，是拆句的直接入口。
- `tests/fixtures/kimi_server_events.jsonl:14-17,37-41` -- 真实 Kimi 英文 thinking 与 status 交错证据，只读测试输入。
- `tests/test_kimi_event_mapping_unit.py` -- 增加 delta 空格、agent/source/offset 保真映射测试。
- `tests/test_kimi_integration.py` -- 增加 F1 外层可见行为、状态交错、来源隔离和后台聊天回归。
- `tests/test_kimi_ui_responsiveness_automation.py` -- 验证执行列表批量更新不抢焦点、不做无效重绘。

## Tasks & Acceptance

**Execution:**
- `kimi_server_client.py` -- 区分标识字段规范化与文本 fragment 保真，携带可靠流身份元数据。
- `main.py` -- 修正 Kimi delta 分流/刷新边界，并用结构化事件生成中文主要步骤，保留现有批处理和聊天隔离。
- `tests/test_kimi_event_mapping_unit.py`、`tests/test_kimi_integration.py` -- 覆盖矩阵中的流式协议与 F1 可见结果。
- `tests/test_kimi_ui_responsiveness_automation.py` -- 覆盖焦点、选择和重绘约束。

**Acceptance Criteria:**
- Given 中文用户运行 Kimi Code 且服务返回英文 thinking，when 用户按 F1，then 列表不显示原始英文 thinking，而显示按时间顺序排列的中文主要步骤。
- Given Kimi 在一个文本段中插入任意数量的 status/usage 事件，when F1 列表更新，then 不出现断句条目、重复条目或粘连文本。
- Given 用户正在用键盘或读屏浏览执行列表，when 前台或后台 Kimi 批量事件到达，then 焦点与当前选择保持稳定，且无可见变化时不重绘。

## Spec Change Log

## Review Triage Log

### 2026-09-07 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 8: (high 1, medium 6, low 1)
- defer: 0
- reject: 0
- addressed_findings:
  - `[high]` `[patch]` F1 会直接暴露英文 thinking/assistant 文本；改为基于 Kimi 结构化事件生成稳定中文主要步骤，并使 assistant 原文只进入回答聚合。
  - `[medium]` `[patch]` status、usage 与静默通知会错误刷新并切断 delta；将这些事件从文本刷新边界中排除。
  - `[medium]` `[patch]` 相同 turn/空 item 的 thinking、assistant、tool 或不同 agent 可能串流；把 display kind、agent 与 source 纳入客户端和 UI 缓冲身份。
  - `[medium]` `[patch]` delta 映射和 UI flush 会裁掉片段边缘空格；分离标识规范化与文本保真，并仅用 `text.strip()` 判断全空白内容。
  - `[medium]` `[patch]` offset 重放可能重复追加短片段；按起始 offset 和文本重叠范围跳过已接收内容。
  - `[medium]` `[patch]` tool result 丢失结构化工具类别会退化成英文通用行；从 display/kind 元数据恢复 search、file、command 等类别并生成中文摘要。
  - `[medium]` `[patch]` 后台聊天与无变化状态更新可能触发前台列表重绘；保持按聊天持久化、批量更新和无变化不重绘，并增加焦点/选择回归。
  - `[low]` `[patch]` Kimi 专用标题优先规则可能影响其他提供方；将该规则限制在 Kimi 协议事件。

## Design Notes

中文化的边界是“列表摘要”，不是翻译任意模型文本。原始 thinking 不应成为产品界面文案；结构化事件才是稳定的信息源。建议同一分析阶段使用幂等摘要，例如“正在分析问题”，工具类别使用“正在搜索内容”“正在读取文件”“正在修改文件”“正在执行命令”“正在调用子任务”，完成回答使用既有“已生成最终回答”。

## Verification

**Commands:**
- `python -m pytest tests/test_kimi_event_mapping_unit.py tests/test_kimi_integration.py -q` -- 事件映射、交错归并、中文 F1 行及跨聊天隔离全部通过。
- `python -m pytest tests/test_kimi_ui_responsiveness_automation.py -q` -- 焦点、选择、批处理和重绘约束全部通过。
- `python -m pytest tests/test_kimi_server_client_unit.py tests/test_main_unit.py -q -k "kimi or execution"` -- 验证 Kimi 队列及通用执行列表相关回归；当前保留 15 项已知基线失败，须与本任务新增断言分开判断。

## Auto Run Result

### 实现摘要

Kimi 协议事件现在先保留流身份和原始文本片段，再由 UI 层合并为逻辑步骤并生成中文摘要。F1 列表不再直接显示英文 thinking/tool chatter；状态通知不再切断同一句流式内容；不同 source/agent 的内容不会串接，offset 重放也会去重。

### 变更文件

- `kimi_server_client.py`：保留 delta 字符、补齐流元数据、按来源隔离并处理 offset 重放。
- `main.py`：生成 Kimi 中文主要步骤、修正 delta 缓冲/刷新边界、避免无效 UI 刷新。
- `tests/test_kimi_event_mapping_unit.py`：覆盖空格与流身份映射。
- `tests/test_kimi_integration.py`：覆盖英文 thinking 隐藏、中文步骤、交错状态、后台聊天和真实 fixture。
- `tests/test_kimi_server_client_unit.py`：覆盖客户端合并、来源隔离与重放去重。
- `tests/test_kimi_ui_responsiveness_automation.py`：覆盖焦点、选择及无变化不重绘。
- `tests/test_main_unit.py`：覆盖 UI 缓冲隔离、边缘空格保真和非 Kimi 行为不回归。

### 审查结果

- 已修复：8 项（high 1、medium 6、low 1）。
- 延后：0 项。
- 拒绝：0 项。
- 建议后续审查：`true`。仅按本轮 patch 计分：存在 1 项 high；同时 `3 × medium 6 + low 1 = 19`。

### 验证结果

- `py -3.11 -m pytest tests/test_kimi_event_mapping_unit.py tests/test_kimi_integration.py -q`：91 passed。
- `py -3.11 -m pytest tests/test_kimi_ui_responsiveness_automation.py -q`：6 passed。
- `py -3.11 -m pytest tests/test_kimi_server_client_unit.py tests/test_main_unit.py -q -k "kimi or execution"`：141 passed、15 failed、636 deselected；15 项失败与既有基线集合一致，集中于通用执行列表分页/导航/增量追加，不是本任务新增回归。
- 新增边缘空格及流隔离专项：3 passed、742 deselected。
- `py -3.11 -m compileall -q ...`：通过。

### 遗留风险

- 未识别的新 Kimi 工具类别会显示通用中文“正在处理任务”，需要随上游协议扩展映射。
- 命令、路径和工具参数按设计仍可能在详情中保留英文；F1 列表标题保持中文主要步骤。
- 通用执行列表测试仍有 15 项既有基线失败，建议另立任务修复，避免与本次 Kimi 专项混合。
