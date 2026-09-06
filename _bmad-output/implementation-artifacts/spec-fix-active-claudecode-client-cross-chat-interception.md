---
title: '隔离活跃 Claude Code 客户端的聊天归属'
type: 'bugfix'
created: '2026-09-06'
status: 'done'
baseline_revision: '8dfd65fd7a8ec0cdc5e17723aa6b9317c1c959f7'
review_loop_iteration: 0
followup_review_recommended: false
context: []
warnings: []
deferred:
  - summary: >-
      单个全局活跃 Claude 客户端槽无法同时保留两个聊天中并行运行的 Claude worker。
    evidence: |-
      第二个聊天注册客户端时会替换第一个聊天的引用；第二个结束后，第一个仍运行的客户端无法再接收续写。
    location: >-
      main.py:_set_active_claudecode_client
    severity: medium
  - summary: >-
      同一聊天显式切换到 Kimi 或 Codex 时，活跃 Claude 客户端仍可能先于模型分派接收输入。
    evidence: |-
      _submit_question 在 resolved_model 计算前执行同 chat_id 的 Claude 续写判断，因此当前判断只区分聊天、不区分显式模型。
    location: >-
      main.py:_submit_question
    severity: medium
  - summary: >-
      Claude worker 的当前聊天检查与客户端注册不是同一个原子操作。
    evidence: |-
      is_current_target() 在获取客户端状态锁之前执行，聊天切换可能发生在检查与注册之间并留下非当前聊天的引用。
    location: >-
      main.py:_start_claudecode_worker_for_turn
    severity: low
  - summary: >-
      无 expected_client 的聊天重置可能清除并发注册的替换客户端。
    evidence: |-
      新聊天、归档和上下文重置路径调用无条件 _clear_active_claudecode_client()；若恰与另一个 worker 注册交错，后注册引用可能被清除。
    location: >-
      main.py:_clear_active_claudecode_client
    severity: medium
  - summary: >-
      非当前聊天中继续运行的 Claude worker 不会登记可续写客户端。
    evidence: |-
      worker 仅在 is_current_target() 为真时保存客户端，切走后仍运行的聊天即使再次被选中也可能无法续写原客户端。
    location: >-
      main.py:_start_claudecode_worker_for_turn
    severity: medium
---

<intent-contract>

## Intent

**Problem:** `_submit_question` 只要发现全局 `_active_claudecode_client` 就把新输入交给它，没有校验该客户端属于哪个聊天。手机端向另一 `chat_id` 发送消息时，消息可能被正在运行的 Claude Code 聊天截获，既不会记录到目标聊天，也不会启动目标模型的 worker。

**Approach:** 为活跃 Claude Code 客户端记录明确的所属 `chat_id`，只有提交目标与所属聊天一致时才续写该客户端；其他聊天继续执行正常的模型解析、turn 创建和专用 worker 分派。保留已经完成的远端 Kimi/Codex/Claude 专用路由。

## Boundaries & Constraints

**Always:** 同一聊天对活跃 Claude Code 客户端的续写行为保持不变；跨聊天提交必须进入目标聊天并按其模型启动 worker；客户端引用及其所属聊天标识必须成对设置和清理；测试从 `_remote_api_message_ui` 外层入口观察消息是否串聊。

**Block If:** 现有协议无法可靠取得提交目标 `chat_id`，或修复要求改变 NATS/手机端消息结构。

**Never:** 不回退手机端 Kimi 专用 worker 路由；不把 CLI 模型改走 OpenRouter；不连接真实 Claude、Kimi 或 NATS 服务；不重构聊天存储或 CLI 客户端生命周期。

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| 跨聊天远端 Kimi | Claude 客户端属于聊天 A，手机端向聊天 B 提交 `kimi/main` | 不调用 A 的 `send_user_input`；在 B 创建 turn 并仅启动 Kimi worker | 不读取 OpenRouter Key，不启动 Claude worker |
| 同聊天 Claude 续写 | Claude 客户端属于聊天 A，向 A 提交文本 | 文本只交给 A 的 `send_user_input`，不创建第二个 worker | 沿用现有客户端异常行为 |
| Claude worker 结束 | 活跃客户端正常完成或抛出异常 | 同时清理客户端引用和所属 `chat_id` | 旧 worker 不得清除后来替换的新客户端归属 |
| 无活跃 Claude 客户端 | 任意目标聊天正常提交 | 沿用当前模型分派 | 沿用现有错误处理 |

</intent-contract>

## Code Map

- `main.py:1358` -- `ChatFrame` 初始化 Claude 会话状态；在这里初始化活跃客户端所属聊天标识。
- `main.py:8269` -- `_start_claudecode_worker_for_turn` 已接收 `chat_id`；设置活跃客户端时可同时保存其归属，并在 `finally` 中进行身份安全的成对清理。
- `main.py:8965` -- `_remote_api_message_ui` 解析并传递手机端目标 `chat_id`，是跨聊天回归测试的外层入口。
- `main.py:12999` -- `_submit_question` 当前在目标聊天/模型处理前无条件截获输入；需以提交目标和客户端归属相等作为截获前提。
- `main.py:13265` -- Codex、Kimi、Claude 的专用 worker 分派是必须保留的既有修复，跨聊天 Kimi 仍应到达 `_start_kimi_worker_for_turn`。
- `tests/test_remote_model_dispatch.py` -- 已覆盖远端 CLI 模型不走 OpenRouter；在此增加活跃 Claude 与另一聊天并发提交、同聊天续写及生命周期回归。
- `tests/test_main_unit.py:4838` -- 已覆盖新建聊天会清除旧 Claude 客户端，修改状态字段时需保持兼容。

## Tasks & Acceptance

**Execution:**
- `main.py` -- 保存 `_active_claudecode_client` 的所属 `chat_id`，按目标聊天隔离续写，并在所有客户端重置点保持引用与归属一致。
- `tests/test_remote_model_dispatch.py` -- 新增从远端消息入口触发的跨聊天并发回归，以及同聊天 Claude 续写和完成清理覆盖。
- `tests/test_main_unit.py` -- 如新增归属状态影响既有新聊天重置测试，更新断言与 worker 桩签名，但不放宽原有行为。

**Acceptance Criteria:**
- Given 聊天 A 的 Claude Code 客户端仍活跃且手机端向聊天 B 发送 Kimi 消息, when `_remote_api_message_ui` 处理该请求, then A 不收到输入、B 保存该消息并启动 Kimi 专用 worker。
- Given 聊天 A 的 Claude Code 客户端仍活跃, when 用户向聊天 A 继续发送文本, then 文本仍传给原客户端且不创建新 turn 或新 worker。
- Given Claude worker 完成或失败, when 后续向任意聊天提交消息, then 已结束客户端及其归属不会截获该消息。
- Given 修复完成, when 运行新增测试、原远端模型分派测试及相关主模块测试, then 全部通过且 `kimi/main` 不读取 OpenRouter Key。

## Spec Change Log

## Review Triage Log

### 2026-09-06 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 0
- defer: 5: (high 0, medium 4, low 1)
- reject: 14: (high 0, medium 6, low 8)
- addressed_findings:
  - none

## Design Notes

客户端引用和所属聊天标识构成同一份瞬时状态。清理时必须继续使用现有的客户端对象身份判断，避免较早结束的 worker 把后来启动的客户端状态清空。提交目标优先使用显式 `chat_id`；没有显式值时使用当前活动聊天标识，以维持桌面端同聊天续写语义。

## Verification

**Commands:**
- `D:\code\sj\.build-envs\mc-py311\Scripts\python.exe -m pytest tests/test_remote_model_dispatch.py -q` -- 跨聊天消息不串入 Claude，Kimi/Codex/Claude 专用路由全部通过。
- `D:\code\sj\.build-envs\mc-py311\Scripts\python.exe -m pytest tests/test_main_unit.py -q -k "claudecode and (new_chat or submit or worker)"` -- Claude 生命周期与聊天重置相关回归通过。
- `git diff --check` -- 补丁格式无错误。

## Auto Run Result

- Summary: 活跃 Claude Code 客户端现在与所属 `chat_id` 成对保存；只有同一聊天的输入会续写该客户端，其他聊天继续创建自己的 turn 并按模型分派。手机端 `kimi/main` 专用 worker 路由保持不变。
- Files changed:
  - `main.py` — 新增 Claude 客户端归属、锁保护的成对设置/清理和按聊天隔离的输入发送。
  - `tests/test_remote_model_dispatch.py` — 增加跨聊天 Kimi、同聊天 Claude 续写、正常/异常清理和替换客户端安全性覆盖。
  - `tests/test_main_unit.py` — 更新新聊天清理断言及与当前生产签名一致的测试桩。
  - `_bmad-output/implementation-artifacts/spec-fix-active-claudecode-client-cross-chat-interception.md` — 记录意图、实现、审查和验证结果。
- Review findings: 本轮无需补丁或规格返工；延期 5 项既有 Claude 多会话/生命周期限制；拒绝 14 项重复、假设性或已被现有组合覆盖满足的建议。
- Follow-up review recommendation: `false`；本轮 patch high 0、medium 0、low 0，评分 `0`。
- Verification:
  - `tests/test_remote_model_dispatch.py`: 10 passed。
  - Claude 相关 `tests/test_main_unit.py`: 7 passed, 735 deselected。
  - `python -m py_compile main.py`: 通过。
  - `git diff --check`: 通过，仅有 Windows 行尾转换提示。
- Residual risks: 本轮是进程内远端入口自动化，没有连接真实手机端、NATS、Claude 或 Kimi 服务；更外层链路由后续 `bmad-qa-generate-e2e-tests` 评估和补充。
