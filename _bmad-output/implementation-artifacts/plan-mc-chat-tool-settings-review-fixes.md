---
title: '修补 MC 聊天工具设置审查发现'
type: 'bugfix'
ticket: ''
created: '2026-11-27'
status: 'built'
baseline_revision: '480c4a02172ff09cb3ce65507ca9a2cf8de2890a'
route: 'full'
route_source: 'auto'
review: 'none'
review_source: 'pinned'
lenses_ran: []
review_loop_iteration: 0
followup_review_recommended: false
context: []
warnings: []
deferred: []
---

<intent-contract>

## Intent

修复 480c4a02172ff09cb3ce65507ca9a2cf8de2890a 上已确认的设置队列、模型冷加载、AGY 能力与额度缓存问题。

## Boundaries & Constraints

保持原聊天、回合与 native session 的归属。首次提交沿用真实支持的默认设置。保留被阻止的草稿及附件。

</intent-contract>

## Code Map

- `chat_store.py` 使用现有 SQLite 聊天修订与回合存储边界。
- `mc_frame_codex.py` 与 `mc_frame_kimi_sessions.py` 是桌面设置 FIFO 派发入口。
- `mc_frame_tool_settings.py` 恢复同一聊天的设置，`mc_frame_requests.py` 在发送入口检查模型选择。
- `agy_settings.py`、`agy_acp.py` 与 `mc_frame_agy.py` 处理原生能力公开与提交配置。
- `chat_information.py` 与 `mc_frame_chat_information.py` 显示和持久化额度快照。

## Tasks & Acceptance

- [x] `chat_store.py`、`mc_frame_requests.py`、Codex/Kimi FIFO 入口：在启动 native worker 前持久认领原记录，限制旧快照重排。
- [x] `mc_frame_tool_settings.py`、`mc_frame_requests.py`：按聊天恢复异步目录设置，在普通桌面发送前确认当前选择。
- [x] `agy_acp.py`、`agy_settings.py`、`mc_frame_agy.py`：按实际能力处理部分字段，保留未选择的默认意图。
- [x] `chat_information.py`、`mc_frame_chat_information.py`：持久保存 Codex 额度变动，按重置时刻显示过期。
- [x] `agy_information.py`：仅把当前原生会话的 AGY 回合纳入聊天信息统计。
- [x] `tests/`：覆盖确认的复现路径，并运行相关桌面套件。

**Acceptance Criteria**

- Given 打开非当前 Kimi 历史聊天且原会话没有未结回合，when 已保存队列可认领，then 通过原会话派发。
- Given 两家桌面设置 FIFO 有旧式排队记录，when 原生 worker 启动后立即重启，then 首条保持认领且只发送一次，后项等待原回合结算。
- Given Codex 冷打开 Astra/high/plan 聊天并在目录返回前切换聊天，when 目录返回，then 当前聊天获得自身选择。普通发送遇到尚未验证的恢复设置时保留草稿。Kimi 空目录同样保留草稿。
- Given AGY 只公开部分能力、空模型变体或 Neon 只有 low，when 操作者发送，then 已公开的选择生效，缺失默认 high 时先提示并接受显式 low。
- Given AGY 回合之间出现桌面本地系统行，when 打开聊天信息，then 累计与最后一次缓存命中仍属于真实原生回合。
- Given 同一账号的额度发生变化或原窗口越过重置，when 读回并打开信息，then 原始更新仍在，过去窗口显示已过期。

## Implementation Notes

- Codex 与 Kimi 沿用聊天队列的延时保存。SQLite 认领在后台完成，提交前以修订、聊天、原生会话和队列行进行比对。UI 派发前重新检查这些条件和清除代次。
- 冷加载的设置保留在当前工具对应的聊天视图。发送提前遇到未验证或已失效的设置时保留草稿，显式选择后继续。
- AGY 对分别公布的模型、推理强度和工具模式按实际能力提交。额度状态比较和持久化保留 Codex 周窗口原始值，Codex 与 Kimi 在信息窗口显示越过重置点的状态。

## Plan Change Log

- 同一审查收口时补入 AGY 本地系统行统计用例和 Kimi 非当前聊天恢复用例。

## Review Triage Log

## Verification

- 设置、信息与定向存储回归使用共享 Python 串行执行，使用隔离 Codex 和 Kimi loopback 配置。
- `python -m pytest -q -p no:faulthandler -p no:cacheprovider tests/test_chat_tool_settings_ui_automation.py tests/test_kimi_chat_settings_ui_automation.py tests/test_agy_settings_ui_automation.py tests/test_agy_attachments_ui_automation.py tests/test_chat_information_ui_automation.py tests/test_main_chat_information.py tests/test_agy_information.py tests/test_chat_store_unit.py`

## Auto Run Result

- 实装五组已确认修补。Codex、Kimi 在后台持久认领设置 FIFO，冷目录保护草稿。AGY 处理部分能力与真实会话统计，Codex 与 Kimi 的已过期额度有明确显示。
- 存储与运行入口为 `chat_store.py`、`mc_frame_requests.py`、`mc_frame_codex.py`、`mc_frame_kimi_sessions.py`、`mc_frame_history.py` 和 `mc_frame_native_history.py`。选择恢复位于 `mc_frame_tool_settings.py` 和 `mc_frame_answers.py`。AGY 能力与统计位于 `agy_acp.py`、`agy_settings.py`、`agy_information.py` 和 `mc_frame_agy.py`。额度渲染位于 `chat_information.py`、`mc_frame_chat_information.py` 和 `mc_frame_dialogs.py`。
- Review 设为 none。对本轮基线已执行的单次 Dog 审查是修复输入，本轮未再启动审查视角。
- 八份测试文件的串行运行记录为 211 通过、1 跳过和 1 个工作树目录布局错误。该工作树的同级目录没有 RC，在临时同级路径放入现有 RC 夹具后，原失败项单独通过。已清理该临时路径。Codex 与 Kimi 的四条原生 loopback 用例均通过。
- SQLite 事务提交后若没有可核对的原生标识，中断恢复会保留原记录，等待原生状态确认。验证使用隔离配置和本地模型，不代表已在日常安装执行。
- Engineer 在 `cdaacc493bbf67751879fc808e82511d3c415228` 串行独立复验，11 项通过，耗时 136.45 秒。其中 10 项覆盖定向回归，1 项使用真实 AGY 1.5.18 验证同一原生会话中的八轮设置切换与排队。
