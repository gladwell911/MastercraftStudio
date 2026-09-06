---
title: '修复手机端 Kimi Code 消息误走 OpenRouter 导致 401'
type: 'bugfix'
created: '2026-09-06'
status: 'done'
baseline_revision: '1d43704bc7a91d8427d9e83374407e815cdbe7f8'
review_loop_iteration: 0
followup_review_recommended: true
context: []
warnings: [oversized]
deferred:
  - summary: >-
      远端 Codex/Kimi 斜杠命令会创建 pending turn，但仅本地来源启动命令 worker。
    evidence: |-
      main.py 的 codex_local_command 和 kimi_local_command 分支均在 source != "local" 时直接返回 True，未启动命令 worker，也未将 turn 标记完成。
    location: >-
      main.py:13095
    severity: medium
  - summary: >-
      活跃 Claude Code 交互可能截获发往另一聊天的远端输入。
    evidence: |-
      _submit_question 在解析目标聊天和模型前，只要 _active_claudecode_client 非空就直接 send_user_input 并返回，未校验该客户端所属 chat_id。
    location: >-
      main.py:13002
    severity: high
---

<intent-contract>

## Intent

**Problem:** 手机端新建远程聊天并选择 `kimi/main` 后，电脑端收到的消息来源为 `remote-ws`；当前提交逻辑只允许本地来源启动 Kimi worker，导致请求误落入依赖 `OPENROUTER_API_KEY` 的通用 worker 并返回 401。电脑端本地发送正常，是因为其来源恰好为 `local`。

**Approach:** 让已解析的模型类型决定提供方分派，不再让消息传输来源决定是否启动 CLI 专用 worker；用回归测试覆盖远程 Kimi Code，并同时锁定同构的 Codex、Claude Code 路由不回退到 OpenRouter。

## Boundaries & Constraints

**Always:** 保留现有远端模型解析、新聊天创建、聊天状态持久化、附件和 UI 更新语义；CLI 模型仍走各自现有后台 worker，不能在 UI 线程执行网络或进程 I/O。

**Block If:** 当前手机端实际发送的模型不是电脑端可识别的 `kimi/*`，或专用 Kimi worker 本身在正确分派后仍返回认证失败，因为这将是不同故障链路。

**Never:** 不修改或伪造 `OPENROUTER_API_KEY`；不把 Kimi 请求改为 OpenRouter 模型；不重构 Kimi 服务生命周期、NATS 协议或手机端 UI；不把测试连接到真实 Kimi 服务。

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| 手机端 Kimi 消息 | `_submit_question(..., source="remote-ws", model="kimi/main")` | 仅启动 `_start_kimi_worker_for_turn`，保留消息、聊天 ID 和模型 | 不读取 OpenRouter Key，不启动通用 `_worker` |
| 其他远程 CLI 模型 | 远程来源加 `codex/*` 或 `claudecode/*` | 启动对应专用 worker | 不回退到通用 `_worker` |
| 非 CLI 模型 | 远程来源加受支持的普通模型 ID | 保持现有通用 `_worker` 路径 | 沿用现有 OpenRouter 错误处理 |

</intent-contract>

## Code Map

- `main.py:8956` -- `_remote_api_message_ui` 将手机端/NATS 消息规范化后以 `source="remote-ws"` 调用 `_submit_question`，并已正确传递解析后的 `kimi/main`。
- `main.py:12990` -- `_submit_question` 统一创建 turn、更新当前聊天和 UI 状态，是本次分派修复的唯一生产代码落点。
- `main.py:13253` -- 当前 Codex、Kimi、Claude Code 分支附带 `source == "local"` 条件；条件不满足时进入 OpenRouter `_worker`，是 401 的直接原因。
- `tests/conftest.py:39` -- `frame` fixture 提供隔离的 wx `ChatFrame`，可验证 worker 选择而不启动真实服务。
- `tests/test_kimi_integration.py:158` -- 现有测试只覆盖本地 Kimi 提交，因此未发现 `remote-ws` 回退。
- `backup/pre-sync-20260906-c443c69` -- 只读历史证据；其中曾有同类三模型分派修复和回归测试，但未进入当前远端分支。

## Tasks & Acceptance

**Execution:**
- `tests/test_remote_model_dispatch.py` -- 新增参数化回归测试，证明远程 Codex、Kimi、Claude Code 请求仅启动对应专用 worker，且通用 OpenRouter worker 若被构造就立即失败。
- `main.py` -- 移除三个 CLI 专用 worker 分支的本地来源限制，让规范化模型 ID 成为提供方选择依据。

**Acceptance Criteria:**
- Given 手机端新建聊天选择 Kimi Code 并发送普通文本, when 电脑端以 `remote-ws` 来源提交 `kimi/main`, then 电脑端启动 Kimi worker 且不会启动 OpenRouter worker或检查其 API Key。
- Given 远程请求使用 Codex 或 Claude Code 模型, when 电脑端提交该 turn, then 同样启动各自专用 worker，避免同构回归。
- Given 请求使用非 CLI 普通模型, when 电脑端提交该 turn, then 仍保持原有通用 worker 行为。
- Given 修复完成, when 运行新增回归测试与相关 Kimi/远端模型测试, then 全部通过且 Git 差异仅包含规格、测试和必要生产代码。

## Spec Change Log

## Review Triage Log

### 2026-09-06 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 6: (high 0, medium 5, low 1)
- defer: 2: (high 1, medium 1, low 0)
- reject: 8: (high 0, medium 3, low 5)
- addressed_findings:
  - `[medium]` `[patch]` 保留远程动态 Claude 模型：专用 worker 接收解析后的模型，并在成功和失败完成事件中原样回传。
  - `[medium]` `[patch]` 将 CLI 路由回归测试提升到 `_remote_api_message_ui` 入口，覆盖远端模型解析和响应体。
  - `[low]` `[patch]` 对每个 CLI 用例禁用所有非预期专用 worker，防止多 worker 同时启动仍误通过。
  - `[medium]` `[patch]` CLI 用例在读取 `openrouter_api_key_for_app` 时立即失败，直接锁定“不依赖 OpenRouter Key”。
  - `[medium]` `[patch]` 断言专用 worker 收到准确模型 ID，并增加动态 Claude 成功/失败完成模型测试。
  - `[medium]` `[patch]` 既有远端 Codex 状态保存单测显式桩替换专用 worker，避免启动真实后台进程。

## Verification

**Commands:**
- `D:\code\sj\.build-envs\mc-py311\Scripts\python.exe -m pytest tests/test_remote_model_dispatch.py -q` -- 修复前应失败于误启通用 worker，修复后 3 个模型用例全部通过。
- `D:\code\sj\.build-envs\mc-py311\Scripts\python.exe -m pytest tests/test_kimi_integration.py tests/test_main_unit.py -q -k "kimi or remote_message or submit_question_defers_state_save_after_send"` -- 相关 Kimi、远端模型解析和提交回归通过。
- `git diff --check` -- 无空白或补丁格式错误。

## Auto Run Result

- Summary: 远端消息现在按规范化模型选择专用 CLI worker；`kimi/main` 不再因 `remote-ws` 来源落入 OpenRouter。审查补丁同时保留动态 Claude 模型元数据和测试隔离性。
- Files changed:
  - `main.py` — 移除 CLI worker 的本地来源门槛，并让 Claude 专用 worker保留动态模型 ID。
  - `tests/test_remote_model_dispatch.py` — 覆盖真实远端 UI 入口、三种 CLI 路由、两种普通 OpenRouter 路由和动态 Claude 完成事件。
  - `tests/test_main_unit.py` — 阻止既有状态保存单测启动真实 Codex worker。
  - `_bmad-output/implementation-artifacts/spec-mobile-kimicode-remote-401.md` — 记录规格、审查、验证和延期项。
- Review findings: 已应用 6 项补丁；延期 2 项既有跨聊天/远端命令问题；拒绝 8 项重复、无具体后果或已由现有覆盖满足的建议。
- Follow-up review recommendation: `true`；本轮补丁 high 0、medium 5、low 1，评分 `3×5 + 1×1 = 16`。
- Verification:
  - `tests/test_remote_model_dispatch.py`: 7 passed。
  - 相关 Kimi/远端提交回归: 27 passed, 735 deselected。
  - 既有 Claude 后台聊天兼容测试: 2 passed。
  - `git diff --check`: 通过，仅报告 Windows 行尾转换提示。
- Residual risks: 未连接真实手机端、Kimi、Claude 或 OpenRouter 服务；两项既有问题已写入 `deferred`，不影响本次普通 Kimi 文本消息路由。
