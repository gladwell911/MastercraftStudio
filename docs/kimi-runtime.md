# MC 的 Kimi 运行时

MC 启动 Kimi 功能时查找本机 CLI。显式设置 `KIMI_BIN` 时，值须为可执行文件的绝对路径。未设置时使用当前用户目录下的 `.kimi-code/bin/kimi.exe`。显式路径无效会报告该路径。MC 和 Codex 可在 Kimi CLI 缺失时启动。

```powershell
$env:KIMI_BIN = 'C:/path/to/verified/kimi.exe'
```

`kimi_runtime.py` 按 SHA-256 识别已验证构建，然后执行 `--version` 确认版本。受支持的 Windows 2.5.3 构建 SHA-256 为 `3fe337ab7aa742ba0b72cc87fcc9a267756e609a54358c0d85bbe200da9bec8d`。未知哈希会在执行前被拒绝。版本探针和服务进程均设置 `KIMI_CODE_NO_AUTO_UPDATE=1`，CLI 安装和升级由用户自行管理。

受支持的 Windows x64 2.6.3 构建 SHA-256 为 `b50c7ce3bed69e9be58f9a29aa090c0e7c61f7a1887503c4943267384a71ef30`。新版 API 使用带 Bearer 子协议的 WebSocket、当前连接的逐会话订阅确认，以及 `ping/pong`。MC 使用主代理 REST turn 的明确 opener prompt 身份、终态和最后完整步骤发布回答。断线后重新订阅并只读核对旧 prompt，不重新提交。2.6.3 活动中的后续输入进入独立队列。2.5.3 的同轮追问沿用该版本经确认的协议。

MC 桌面新聊天关闭计划模式，默认选择自动审批。工具菜单分别设置计划和审批，模型及推理强度取自原生服务目录。两个版本都会在接收后续提问时更新实时设置，MC 先保留改过设置的提问及提交快照，待原生回合结束后按顺序在同一会话发送。

2.6.3 已在隔离环境实测真实模型连续两问、窗口审批允许和拒绝、精确取消与活动断线后恢复。旧会话跨构建迁移和降级可读性仍待验证。

`zgwd.spec` 无需准备 Kimi 文件。默认及自定义 spec 的暂存树均不得携带 Kimi CLI，检查包括 `_internal/history` 和 `_internal/.codex-home`。最终树验证跳过这两个保留用户数据目录。更新包时旧 `_internal/runtimes/kimi` 随旧程序文件清理。构建验收须在非管理员 PowerShell 中运行，并传入临时目录。

```powershell
$isolated = Join-Path ([IO.Path]::GetTempPath()) ('mc-build-' + [Guid]::NewGuid().ToString('N'))
.\package_mc.ps1 -DistPath (Join-Path $isolated 'dist') -WorkPath (Join-Path $isolated 'work') -BuildOnly
```

旧方言根据真实握手选择 V1 或 V2。2.5.3 的 V2 用 `subscribe_v2` 的目标 session ACK 作为发送前门禁，使用主代理 transcript 的 `triggerPromptId` 和 `turn.state` 接纳最终回答。断线补齐走 `/transcript/ops`，日志缺失或服务重启时刷新 `/transcript`；按 prompt 分页恢复，不猜最后一条 assistant。不支持的旧 status/snapshot 能力明确报错，不伪造上下文数据。Kimi 2.5.3 的 schema/路由事实已从其打包 JS 核对，测试夹具均为合成正文。

Alt+Y 的 V2 当前上下文取自主代理的顶层 `usage` 帧。2.5.3 暂存先到的用量帧，目标会话获 ACK 后才投递。2.6.3 目前只消费当前代次 ACK 后的用量帧，隔离实测初始用量在 ACK 后到达。累计从主代理 transcript 分页读取，按 `turnId` 对终态回合去重并合并当前增量。最近命中率用最新完成 step 的输入，累计命中率用各回合 `inputTokens + cachedTokens` 的总和作分母。完整回合汇总优先。部分汇总只有在各消耗步骤终止、用量字段完整且与已知汇总逐项相符时才补齐，取消空回合缺少零用量证据时保留未知。上下文和累计分别判定可用性，零分母显示暂不可用，顶部累计以千为单位截去小数。查询诊断只记失败阶段、异常类别与聚合字段计数。重置、删除和序列缺口会使旧扫描失效。套接字、账号和会话归属均参与回写校验。V1 继续读取原有 status/snapshot，额度刷新独立进行。

V1 完成回答后会补读同会话的 status/snapshot。已接受的用量刷新在本地或远端聊天信息观察者关闭后仍可结算。MC 保存会话累计并通知对应的 RC 会话。额度由聊天信息入口单独刷新。

最终回答要求整个 turn completed，并取最高 ordinal 末步。末步通常 completed；真实追问可能留下 interrupted 且 `endReason` 精确为 `steered`，此时只取该步最后一个结构化 text/user frame 之后的公开 assistant 正文，无 user 则取该步正文。其他/缺失原因、tool、thinking-only、隐藏/空正文和不完整 transcript 均不发布，也不回退早先步骤；失败/取消仍保留失败语义。REST 与 V2 reset/ops 共用该提取规则，不改写服务端状态。

ACK 只属于当前 socket generation，失效立即撤销就绪。running、queued、blocked 继续既有 monitor。V2 发 POST 前分配精确 prompt ID，回包结果不明时通过只读 transcript 确认唯一归属，遇到歧义明确失败，禁止自动重发。append 按 UTF-16 code unit 应用，批次失败时保留游标。终态缺正文仍走有界补齐，完成后释放正文和流缓存，最多保留 512 项完成身份和 32 个空闲 transcript，pending 文本保留。

普通 `/clear` 在发送入口退休当前聊天的会话关联，保留历史并结算此前未完成的 Kimi 提问。新问题分配新会话，迟到的回包、早到 WS 认领和延迟界面回调都校验提交时的锁身份。V2 `/stop` 按当前准确的 session 与 prompt 发送。服务端受理后等待权威终态，被拒后原提问仍可完成。

V2 审批和提问按 session 与 interaction ID 归属。已知失败可经“应用 → 重试 Kimi 请求”再次答复，结果未知先只读查询。菜单轮流处理当前聊天的未决请求，历史归档仅在对应请求仍有效时显示重试提示。2.6.3 用交互专用 URL 绑定 POST 回包，核对实际 `outcome`。结果未知时先查会话的待交互类别，再通过当前 socket 重订阅取得带精确 ID 的 pending 帧。连接变化、身份无法确认或查询失败时保持未知。HTTP 响应、WS ended 和本地退休共用终态处理，最多保留 512 个近期终态用于抑制重放，活动请求保持可用。

首次完整成功回答在前后台共用接纳边界保存并请求完成音，alias 只保留问题和回答身份引用。`MC_KIMI_COMPLETION_DIAGNOSTICS=1` 开启当前进程最多 128 条匿名完成/音效结果记录（`_kimi_completion_diagnostics`）；默认关闭，不保存正文或凭据，无历史补播。

## 键盘导航与执行详情

Kimi 流式片段、执行投影和隐私撤回按序在后台写入。2.6.3 用 `item_id` 和 `set` 合并更新，条目删除、隐藏或失去 prompt 归属会撤回相应内容。终态 REST 查证在有界后台执行，查不到结果时触发会话级只读补查。REST 整理消息并分批应用到 wx，按 chat、revision、session、turn 和 prompt 归属处理。续批提交公开块前重新检查撤回状态，重复快照无需重绘列表。撤回处理覆盖超出可见缓存的持久化记录，保护重开的执行历史及远端交付。可重试存储故障持续时，正常关闭继续等待已经接收的批次和写入。

源码测试已经在 SQLite 写锁持有期间和长 REST 快照的实际 wx 定时器续批之间记录 Tab 焦点与方向键选中事件。争渡声学出声与日常构建仍待现场验收。测试和审查证据见 [实施计划](../_bmad-output/implementation-artifacts/plan-kimi-keyboard-responsiveness.md)。

## PowerShell 共享执行

`mc-kimi --list` 查看本机会话。`mc-kimi --chat <ID> --send "问题"` 在本机 MC 的 Kimi Server 继续该会话。MC 和 RC 的对应聊天会显示供应方公开的工具步骤、状态和输出。在执行页打开步骤可查看详情。

`mc-kimi`、`mc-codex` 和 `mc-agy` 由 MC 注册为本地 PowerShell 脚本。如果 PowerShell 提示禁止执行脚本或要求数字签名，可用 `Get-ExecutionPolicy -List` 查看生效策略。保留现有策略，按组织流程签名启动脚本，或直接在 PowerShell 运行安装目录中的 `mc_worker.exe --shared-terminal kimi` 进入交互。Codex 和 AGY 分别传入 `codex` 和 `agy`。

MC 和 RC 可中断共享回合。待处理的审批和结构化问题在原聊天中处理。运行中发送追问时，2.5.3 根据原生回执追加当前回合或排队，2.6.3 由服务端安排下一轮。

## 原生终端历史

MC 通过当前支持的 Kimi Server 分页读取同一 Windows 账号和 Kimi Home 的会话及主代理记录。RC 在对应电脑的历史中查看原工作目录、正文和可恢复的执行步骤。旧历史首次读入不会触发回复通知。已发现会话按原生 ID 续问，工作目录变化、会话占用和暂时不可读时保留缓存。

用 `kimi --session <ID>` 在原目录继续应用创建的会话。Kimi 2.6.3 可能在 Server 退出前持有会话锁。若终端提示会话已被使用，等待原任务完成并正常关闭持有该会话的 MC 或 Kimi Web 实例，再重试。MC 的后台历史读取使用独立的短时 Server，扫描结束后关闭该 Server。

## 兼容与验收

固定版本不能保证云端 API 永远兼容。回退前必须验证升级后的会话格式可读，不能直接让旧程序写日常库。物理声音、新版 MC 日常切换与长期运行仍待验收。

源码、隔离云端、frozen GUI 和日常包的验证状态及延期项集中记录在 [当前交接](handoff.md)。原有 steered 末步、journal 覆盖和权威 reset 的待证场景分别保存在 [steered 后续审查](../_bmad-output/implementation-artifacts/review-kimi-steer-rest-followup-20261118.md) 与 [稳定性审查](../_bmad-output/implementation-artifacts/review-kimi-stability-order-sound-20261118.md)。
