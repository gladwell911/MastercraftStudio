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

2026-11-18 已用准备入口补齐源码和日常包中的固定文件，回读 SHA256 均与上述固定记录相同，独立版本探针均返回 2.5.3。日常 MC 补齐前后均为 PID 36616，启动时间 11:10:04。非管理员隔离构建用默认 spec 完成，暂存包和最终包均通过门禁。日常包仅补齐运行时文件，本轮未触发日常云端问答或应用重启。

客户端根据真实握手选择 V1 或 V2。V2 用 `subscribe_v2` 的目标 session ACK 作为发送前门禁，使用主代理 transcript 的 `triggerPromptId` 和 `turn.state` 接纳最终回答。断线补齐走 `/transcript/ops`，日志缺失或服务重启时刷新 `/transcript`；按 prompt 分页恢复，不猜最后一条 assistant。不支持的旧 status/snapshot 能力明确报错，不伪造上下文数据。Kimi 2.5.3 的 schema/路由事实已从其打包 JS 核对，测试夹具均为合成正文。

最终回答要求整个 turn completed，并取最高 ordinal 末步。末步通常 completed；真实追问可能留下 interrupted 且 `endReason` 精确为 `steered`，此时只取该步最后一个结构化 text/user frame 之后的公开 assistant 正文，无 user 则取该步正文。其他/缺失原因、tool、thinking-only、隐藏/空正文和不完整 transcript 均不发布，也不回退早先步骤；失败/取消仍保留失败语义。REST 与 V2 reset/ops 共用该提取规则，不改写服务端状态。

ACK只属于当前socket generation，失效立即撤销就绪。running/queued/blocked继续既有monitor；POST结果不明时通过只读transcript定位唯一prompt，歧义明确失败，不自动重发。append按UTF-16 code unit应用，批次失败不跳游标；终态缺正文仍走有界补齐。完成后释放正文和流缓存，仅保留最多512项完成身份、32个空闲transcript，pending文本保留；这些是局部对象保留界限，不是存储总量或长期稳定性证明。

首次完整成功回答在前后台共用接纳边界保存并请求完成音，alias 只保留问题和回答身份引用。`MC_KIMI_COMPLETION_DIAGNOSTICS=1` 开启当前进程最多 128 条匿名完成/音效结果记录（`_kimi_completion_diagnostics`）；默认关闭，不保存正文或凭据，无历史补播。

固定版本不能保证云端 API 永远兼容。回退前必须验证升级后的会话格式可读，不能直接让旧程序写日常库。物理声音、新版 MC 日常切换与长期运行仍待验收。

2026-11-18：`7852d06` 的协议/顺序/音效修复后，`8858753` 补齐 steered REST 恢复；独立 client 146项及串行 integration 13项通过，源码未变，后续审查复用该结果。真实隔离云端新请求、同会话连续性、活动长任务追问期间断连和新代目标 ACK、owner REST 正文相等与重放去重已通过，临时运行时和数据库已清理。65.39秒活动断连场景允许 WS 补终结，不代表“WS无终结仅靠自动REST调度”已验收；未决 POST 整体真实恢复、完成身份淘汰后旧重放也尚未验收。旧手机可能忽略新增顺序字段，未知历史真实到达次序无法保证重建。

最新 [全新四透镜审查](../_bmad-output/implementation-artifacts/review-kimi-steer-rest-followup-20261118.md) 为0 patch、1 defer、9 reject。defer 是 medium (unverified) 的协议边界疑问：无 user 但残留追问前正文，或同一最终 step 保留追问前 tool，是否在固定2.5.3真实可达，仍须脱敏 shape 与 producer 证据；当前按既定合同处理，不新增推测分支。edge空结果仍是 failed layer，不能称完整 clean review；本轮 blind 的代码 diff 不含计划，旧轮叙述污染限制不套用到该 blind，协调员读取原计划的影响见报告。其他证据与现场限制见 [交接](handoff.md)。

保留 [审查报告](../_bmad-output/implementation-artifacts/review-kimi-stability-order-sound-20261118.md) 的D1/D2：固定2.5.3的`complete:true`却journal覆盖不足，以及同一frame权威reset合法缩短/替换，都尚无真实可达证据，不为此新增防御。PlaySound相互覆盖未复现，不添加历史补播机制。
