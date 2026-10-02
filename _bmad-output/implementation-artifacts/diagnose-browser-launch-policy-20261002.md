---
status: blocked
blocked_reason: 内置 Windows 启发式与 Never 根因已定位，源码修复通过 66 项验证；运行包未更新，浏览器真实审批启动待验收。
---

# 浏览器启动策略拒绝诊断

日期：2026-10-02。以下记录保留初期诊断阶段事实；最终源码修复与运行包未更新的状态见文末。初期未修改产品源码、执行策略或用户已有修改，未重启 MC。

## 已确认事实

- 实际运行链：MC → mc_worker → npm Codex；当前 CLI 为 **0.159.2**。此前检查的 `.sandbox-bin` 版本属于另一安装。
- 错误在 `CreateProcess` 前返回 `Rejected("blocked by policy")`，没有执行浏览器启动或前台激活。因此不能将本次失败归因 Windows 焦点切换。
- MC 启动及恢复线程固定传 `approval_policy="never"`、`sandbox="danger-full-access"`：`codex_worker_process.py:245`、`:353`；客户端原样传递：`codex_client.py:546`、`:565`。运行时记录也确认 `Never + DangerFullAccess`。
- MC 使用专用 `.codex-home`，复制清单不包含用户 `rules`：`codex_client.py:116`、`:197`。活跃目录无规则文件，用户规则只有 allow，没有发现直接解释这次拒绝的 forbidden/prompt 规则。
- 运行时 `guardian_approval=true` 仅证明特性可用；不能据此判断此次执行经过自动审查。两个已知线程的 rollout 未发现 Guardian、approval 或 assessment 事件。
- 只读 `execpolicy check` 帮助可执行，但带被拒浏览器命令文本的规则诊断也在执行前被拒。未再次执行浏览器启动。

## 可核对的完成证据

原线程记录：`D:/code/cx/mc/_internal/.codex-home/sessions/2026/10/02/rollout-2026-10-02T18-31-49-01a0fc2b-8922-74e0-8f64-01bebcd365ee.jsonl`。

| 行号 | UTC 时间 | 调用 ID |
|---|---|---|
| 1753 | 2026-10-02T12:52:54.544Z | call_OjqLBXddFxBpbnCnQI12AnvD |
| 1769 | 2026-10-02T12:53:45.376Z | call_ZHqFxA9Tr62QeggcNCrMl4tj |

仅检查事件类型、时间、调用归属和错误；未复制聊天正文、凭据、二维码或完整启动命令。SQLite 日志记录会被清理，复核优先使用上述 rollout 时间及调用 ID。

## 最小后续方案

1. 向 Codex 支持或其执行组件维护者提供 CLI 版本、上述调用 ID、时间及有效权限组合，要求返回执行前策略判断来源、匹配规则、拒绝理由及对应正式复核入口。
2. 只有获得真实自动审查拒绝事件后，才能使用实际 0.159.2 协议中的 `thread/approveGuardianDeniedAction`，参数为线程 ID 和原始 `GuardianAssessmentEvent`。由用户确认具体事件，一次重试仍接受审查；不伪造事件或授权标记。
3. 若确认 MC 未展示已收到的真实拒绝理由，再局部补充事件展示及必要的人工复核传递，按 director 实现 → engineer 独立验证。当前未证实存在该次事件，故不启动实现。

MC 当前仅处理 `requestUserInput` 的交互入口（`main.py:13559`）；没有命令审批回复调用路径。直接将 `never` 改为 `on-request` 可能产生无法回复的请求，且不能保证解除本次拒绝。不能关闭审核、添加放行规则或更换执行通道作为此次修复。

官方依据：[Auto-review](https://learn.chatgpt.com/docs/sandboxing/auto-review)、[App Server](https://learn.chatgpt.com/docs/app-server)。本机实际协议已生成至 `C:/Users/gladwell/AppData/Local/controlTower/browser-policy-diagnostics/codex-0.159.2-schema`，包含正式复核请求及审查通知结构。

## Auto Run Result

诊断完成，状态 blocked；浏览器前台目标未完成。下文同版本源码核对已将原先不明的策略拒绝定位为 Windows URL 启动启发式与固定 Never 审批组合；需要正式人工审批路径，非 Guardian 复核。未开始产品实现或额外审查；保留用户所有既有修改。

## 补充：已执行的精准诊断

同版本 `codex doctor --json` 在仅该诊断子进程使用 MC 的 `CODEX_HOME` 后成功执行。脱敏报告：`C:/Users/gladwell/AppData/Local/controlTower/browser-policy-diagnostics/codex-doctor-0.159.2-redacted.json`。

- 配置解析、npm 运行来源通过；没有本地可见的近期 Codex 安全执行阻止记录。
- doctor 检查的是 invocation 配置，明确未检查 active thread overrides；其默认审批为 OnRequest、文件及网络受限，不能代替活跃线程 Never + DangerFullAccess。
- elevated Windows sandbox 配置不完整或过时、Defender 排除项未验证是通用 warning，未提供与本次拒绝关联的事件。未运行 setup、增加安全软件排除项或据此断言系统已被全面排除。

随后启动独立、临时的同版本 stdio App Server，只初始化并执行三个只读 RPC；没有创建线程、调用模型、修改配置或重启现有 MC。采集范围为相同 `CODEX_HOME`，`cwd=D:/code/cx/mc/_internal`。

| RPC | 实际结果 | 能说明的范围 |
|---|---|---|
| `config/read`，`includeLayers=true` | 用户配置层为 MC 的 `.codex-home/config.toml`；系统层为空；无项目配置层。approval/reviewer/sandbox/default permissions 均未显式配置；Windows backend 为 elevated。 | 这次只读查询没有发现额外配置层中的浏览器禁令；活跃线程的覆盖值仍以其实际运行记录为准。 |
| `configRequirements/read` | `requirements=null` | 此查询未返回 managed requirements，不能据此排除另一级执行主机策略。 |
| `permissionProfile/list` | `:read-only`、`:workspace`、`:danger-full-access` 均 `allowed=true` | 命名权限模式均可选择，不等于具体命令已获允许。 |

所选非敏感结果：`C:/Users/gladwell/AppData/Local/controlTower/browser-policy-diagnostics/effective-policy-selected-0.159.2.json`。

进程树还确认 MC 调用的 npm Codex 下存在 `codex-code-mode-host.exe`。`tools::router` 日志只能证明 Codex 报告错误，不能仅凭模块名判断拒绝由 Codex 内部产生，还是由这个下级主机返回。

MC 的 `_stdout_loop` 解析 JSON-RPC 后分派，不保存原始 stdout 行（`codex_client.py:853`）；`_send_json` 不持久记录原始请求（`:833`）。stderr 保留最近 40 行并原样投递（`:872`），没有证据证明 MC 删除了此次具体拒绝理由。实际协议 `server/diagnostics` 仅提供 process/gauges，没有找到可查询历史命令策略理由的 RPC。

### 下一次采集的最小范围

具备执行 host 日志的维护者，应首先以已有两个调用 ID 和 UTC 时间关联以下上下游记录，而非重新执行浏览器命令：

1. MC → App Server 的线程启动/恢复请求及响应：线程 ID、审批策略、sandbox/permission profile；用于确认最终有效覆盖值。
2. App Server → code-mode host/执行器的请求及响应：上级 tool call ID、下级请求 ID、trace/span ID、执行器版本、命令摘要或哈希、目标类别。不得把包含凭据的原始命令或会话全文复制到公开诊断。
3. 拒绝决定：`deny_reason`、`policy_source`、`matched_rule`、decision/justification，以及返回拒绝的组件和判定阶段。字段名为所需诊断语义，不能假定现有实现已暴露同名字段。
4. 原生进程创建是否实际发生，以及关联的 Windows 错误码/安全事件 ID（如果有）。没有创建请求时继续查执行前策略；有创建请求和系统错误证据时再定位 Windows 层。

如果既有记录缺少第 2、3 项，由执行 host 维护者在该版本的拒绝返回点提供针对性 trace，并在正式批准的诊断流程中采集；当前 CLI 帮助及生成协议未提供可直接补查这些字段的入口。本轮没有关闭审核、绕过拒绝、更换执行通道或重复浏览器启动以制造日志。

## 最终定位：同版本内置 Windows 命令启发式

进一步核对 OpenAI 官方 `rust-v0.159.2` 源码，已找到与两次现场命令匹配的判断链。该发现取代前文“尚需上游理由才能定位”的诊断阶段结论，无须继续泛查 code-mode host。

1. [`windows_dangerous_commands.rs:34–49`](https://github.com/openai/codex/blob/rust-v0.159.2/codex-rs/shell-command/src/command_safety/windows_dangerous_commands.rs#L34)：PowerShell 参数词表同时包含 HTTP/HTTPS URL 和 `Start-Process`/`Invoke-Item` 时，判为危险命令。URL 识别没有针对 localhost 的例外；不依据 `WindowStyle`。
2. [`exec_policy.rs:759–765`](https://github.com/openai/codex/blob/rust-v0.159.2/codex-rs/core/src/exec_policy.rs#L759)：该启发式匹配且审批为 Never 时返回 Forbidden；OnRequest 时返回 Prompt。
3. [`exec_policy.rs:1028–1056`](https://github.com/openai/codex/blob/rust-v0.159.2/codex-rs/core/src/exec_policy.rs#L1028)：此类 `DangerousCommandMatch::Other` 的拒绝理由生成通用 `blocked by policy`。

两次现场命令都含 `Start-Process` 和本机 HTTP 网址，活跃线程为 Never，符合这条链。带相同文字的只读规则检查也会受词表扫描影响，解释其未执行即被拒。没有重新运行被拒动作或用不同表示绕过判断。

code-mode host 的正式帮助提供 `--otel-trace-listen` 和 `--otel-trace-exporter`，但三个 MC 子 host 进程均未配置，当前没有可订阅端点。精确拒绝模板在同安装的 codex.exe 内；这些只是辅助边界证据，根因依据是上述同版本源码和现场输入。

### 针对原因的最小正式修复

在 MC 增加用户主动选择的 OnRequest 审批模式，并贯通 `item/commandExecution/requestApproval` 的展示、用户批准/拒绝及回复路径；线程启动和恢复都使用该选择。保留现有模式，不自动批准、不关闭危险命令分类、不添加允许规则。

先用协议夹具验证两种模式及审批请求归属、批准/拒绝回复，再独立验证；实际用户批准后才验证本机浏览器前台。单独修改 Never 会让 MC 停在无人处理的审批请求上，不能作为完整修复。此次不是 Guardian 事件，因此不应调用 `thread/approveGuardianDeniedAction`。

## 源码修复与独立验证结果

2026-10-02 已按上述最小方案完成 MC 源码修复，详见 [实施计划及证据](plan-codex-command-approval.md)。新增“应用 → Codex 执行审批”，默认保留“不询问”；用户明确选“需要时询问”后作用于后续线程启动/恢复。当前有效命令审批展示原始请求，支持单次批准、拒绝及关闭拒绝，并校验 owner/client，避免回复错误请求。不修改内置危险命令检测或允许规则。

director 自验及 engineer 独验均为 **66 passed**；独验为 58 unit/worker、8 真实 wx GUI，`git diff --check` 通过。没有新增额外审查轮次或宽回归。

运行包尚未更新，当前 MC 未重启、活跃审批模式未改变，没有运行已拒的浏览器命令。源码实施计划为 built；本诊断的浏览器实际打开目标仍待新包与用户选择、真实请求审批后验收，不能报告当前会话已可打开浏览器。原诊断实施阶段保留既有修改，未提交或推送；随后用户调用 neat-freak，审批源码、验证及诊断文档纳入收尾提交。实际浏览器目标仍须新运行包与真实用户审批后验收。
