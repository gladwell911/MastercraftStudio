# MC 的 Kimi 运行时

MC 使用独立运行时 `runtimes/kimi/2.5.3/kimi.exe`。目录根与现有 `browser_open.resource_root()` 一致，源码运行使用项目目录，onedir 使用 `mc/_internal`。固定文件缺失时明确报错，禁止自动查找共享 PATH 或用户 CLI 安装目录。

受支持记录位于 `kimi_runtime.py`：版本 2.5.3，WebSocket 协议 2，SHA256 `3fe337ab7aa742ba0b72cc87fcc9a267756e609a54358c0d85bbe200da9bec8d`。这是本次实际核对的 Windows 二进制；其他构建需要先验证协议并更新支持记录，不会仅因版本字符串相同而接纳。

首次在本机使用默认 spec 构建前，从已经核验的本地文件显式准备源码运行时。已有文件通过校验时可继续使用。`--destination` 指向版本目录。

```powershell
.venv/Scripts/python.exe kimi_runtime.py --source '<已验证的本地kimi.exe>' --destination 'runtimes/kimi/2.5.3'
```

准备入口先核对 SHA256，再执行版本探针，然后复制并复验。目标已经存在时保留原文件。显式 `KIMI_BIN` 只影响 MC 客户端运行时解析，仍执行相同版本和哈希检查。探针和 MC 子进程都强制 `KIMI_CODE_NO_AUTO_UPDATE=1`。

默认 `zgwd.spec` 在收集前校验源码固定文件，将它作为单条 DATA 收入 `mc/_internal/runtimes/kimi/2.5.3/kimi.exe`，保留原字节。`package_mc.ps1` 在更新前校验暂存包，在启动前再校验最终包。自定义 spec 同样必须交付固定文件。`KIMI_BIN`、共享 PATH 和用户安装目录均无法替代缺失的包内文件。

在非管理员 PowerShell 中可用独立目录验收构建。`-BuildOnly` 仍会更新传入的 `DistPath`，因此验收时显式传入临时目录。

```powershell
$isolated = Join-Path ([IO.Path]::GetTempPath()) ('mc-build-' + [Guid]::NewGuid().ToString('N'))
.\package_mc.ps1 -DistPath (Join-Path $isolated 'dist') -WorkPath (Join-Path $isolated 'work') -BuildOnly
.venv/Scripts/python.exe kimi_runtime.py --package-root (Join-Path $isolated 'dist/mc')
```

2026-11-18 用准备入口补齐了源码和日常包的固定文件，两处版本探针均为 2.5.3。2026-11-19 再次回读，两处 SHA256 均匹配固定记录。非管理员隔离构建的默认 spec 暂存包和最终包均通过门禁。日常包当时只更新运行时文件，后续源码修复尚待打包切换。复制阶段失败可能留下部分程序文件，最终校验会阻止启动，回滚仍是维护留项。见 [打包修复计划](../_bmad-output/implementation-artifacts/plan-fix-kimi-runtime-packaging-20261118.md) 与 [功能审查](../_bmad-output/implementation-artifacts/review-kimicode-functional-20261119.md)。

客户端根据真实握手选择 V1 或 V2。V2 用 `subscribe_v2` 的目标 session ACK 作为发送前门禁，使用主代理 transcript 的 `triggerPromptId` 和 `turn.state` 接纳最终回答。断线补齐走 `/transcript/ops`，日志缺失或服务重启时刷新 `/transcript`；按 prompt 分页恢复，不猜最后一条 assistant。不支持的旧 status/snapshot 能力明确报错，不伪造上下文数据。Kimi 2.5.3 的 schema/路由事实已从其打包 JS 核对，测试夹具均为合成正文。

Alt+Y 的 V2 当前上下文取自主代理的顶层 `usage` 帧。首次订阅中先到的帧暂存到同一 socket 代次，目标会话获 ACK 后才投递。累计从主代理 transcript 分页读取，按 `turnId` 对终态回合去重并合并当前增量。最近命中率用最新完成 step 的输入，累计命中率用各回合 `inputTokens + cachedTokens` 的总和作分母。完整回合汇总优先。部分汇总只有在各消耗步骤终止、用量字段完整且与已知汇总逐项相符时才补齐，取消空回合缺少零用量证据时保留未知。上下文和累计分别判定可用性，零分母显示暂不可用，顶部累计以千为单位截去小数。查询诊断只记失败阶段、异常类别与聚合字段计数。重置、删除和序列缺口会使旧扫描失效。套接字、账号和会话归属均参与回写校验。V1 继续读取原有 status/snapshot，额度刷新独立进行。

V1 完成回答后会补读同会话的 status/snapshot。已接受的用量刷新在本地或远端聊天信息观察者关闭后仍可结算。MC 保存会话累计并通知对应的 RC 会话。额度由聊天信息入口单独刷新。

最终回答要求整个 turn completed，并取最高 ordinal 末步。末步通常 completed；真实追问可能留下 interrupted 且 `endReason` 精确为 `steered`，此时只取该步最后一个结构化 text/user frame 之后的公开 assistant 正文，无 user 则取该步正文。其他/缺失原因、tool、thinking-only、隐藏/空正文和不完整 transcript 均不发布，也不回退早先步骤；失败/取消仍保留失败语义。REST 与 V2 reset/ops 共用该提取规则，不改写服务端状态。

ACK 只属于当前 socket generation，失效立即撤销就绪。running、queued、blocked 继续既有 monitor。V2 发 POST 前分配精确 prompt ID，回包结果不明时通过只读 transcript 确认唯一归属，遇到歧义明确失败，禁止自动重发。append 按 UTF-16 code unit 应用，批次失败时保留游标。终态缺正文仍走有界补齐，完成后释放正文和流缓存，最多保留 512 项完成身份和 32 个空闲 transcript，pending 文本保留。

普通 `/clear` 在发送入口退休当前聊天的会话关联，保留历史并结算此前未完成的 Kimi 提问。新问题分配新会话，迟到的回包、早到 WS 认领和延迟界面回调都校验提交时的锁身份。V2 `/stop` 按当前准确的 session 与 prompt 发送。服务端受理后等待权威终态，被拒后原提问仍可完成。

V2 审批和提问按 session 与 interaction ID 归属。已知失败可经“应用 → 重试 Kimi 请求”再次答复，结果未知先只读查询。菜单轮流处理当前聊天的未决请求，历史归档仅在对应请求仍有效时显示重试提示。HTTP 响应、WS ended 和本地退休共用终态处理，最多保留 512 个近期终态用于抑制重放，活动请求保持可用。

首次完整成功回答在前后台共用接纳边界保存并请求完成音，alias 只保留问题和回答身份引用。`MC_KIMI_COMPLETION_DIAGNOSTICS=1` 开启当前进程最多 128 条匿名完成/音效结果记录（`_kimi_completion_diagnostics`）；默认关闭，不保存正文或凭据，无历史补播。

## 键盘导航与执行详情

Kimi 流式片段、执行投影和隐私撤回按序在后台写入。REST 整理消息并分批应用到 wx，按 chat、revision、session、turn 和 prompt 归属处理。续批提交公开块前重新检查撤回状态，重复快照无需重绘列表。撤回处理覆盖超出可见缓存的持久化记录，保护重开的执行历史及远端交付。可重试存储故障持续时，正常关闭继续等待已经接收的批次和写入。

源码测试已经在 SQLite 写锁持有期间和长 REST 快照的实际 wx 定时器续批之间记录 Tab 焦点与方向键选中事件。争渡声学出声与日常构建仍待现场验收。测试和审查证据见 [实施计划](../_bmad-output/implementation-artifacts/plan-kimi-keyboard-responsiveness.md)。

## 兼容与验收

固定版本不能保证云端 API 永远兼容。回退前必须验证升级后的会话格式可读，不能直接让旧程序写日常库。物理声音、新版 MC 日常切换与长期运行仍待验收。

源码、隔离云端、frozen GUI 和日常包的验证状态及延期项集中记录在 [当前交接](handoff.md)。原有 steered 末步、journal 覆盖和权威 reset 的待证场景分别保存在 [steered 后续审查](../_bmad-output/implementation-artifacts/review-kimi-steer-rest-followup-20261118.md) 与 [稳定性审查](../_bmad-output/implementation-artifacts/review-kimi-stability-order-sound-20261118.md)。
