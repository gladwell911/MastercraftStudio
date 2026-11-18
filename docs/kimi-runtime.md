# MC 的 Kimi 运行时

MC 使用独立运行时 `runtimes/kimi/2.5.3/kimi.exe`，不再从共享 PATH 或用户 CLI 安装目录自动寻找。目录根与现有 `browser_open.resource_root()` 一致：源码为项目目录，onedir 为资源目录。缺失时明确报错；准备入口已用于真实隔离云端验收，**没有安装日常运行时，也没有打包或重启日常 MC**。

受支持记录位于 `kimi_runtime.py`：版本 2.5.3，WebSocket 协议 2，SHA256 `3fe337ab7aa742ba0b72cc87fcc9a267756e609a54358c0d85bbe200da9bec8d`。这是本次实际核对的 Windows 二进制；其他构建需要先验证协议并更新支持记录，不会仅因版本字符串相同而接纳。

显式准备命令（`--destination` 是版本目录；验收时应传临时目录）：

```powershell
.venv/Scripts/python.exe kimi_runtime.py --source <已验证的本地kimi.exe> --destination <独立目录>/runtimes/kimi/2.5.3
```

入口先校验来源版本与哈希，再复制、复验；不覆盖已有目标，不下载、不修改全局配置。显式 `KIMI_BIN` 可覆盖路径，仍执行相同版本和哈希检查。MC 子进程强制 `KIMI_CODE_NO_AUTO_UPDATE=1`，传入环境不能将其改为 0。准备新路径之后的日常切换由用户安排。

客户端根据真实握手选择 V1 或 V2。V2 用 `subscribe_v2` 的目标 session ACK 作为发送前门禁，使用主代理 transcript 的 `triggerPromptId` 和 `turn.state` 接纳最终回答。断线补齐走 `/transcript/ops`，日志缺失或服务重启时刷新 `/transcript`；按 prompt 分页恢复，不猜最后一条 assistant。不支持的旧 status/snapshot 能力明确报错，不伪造上下文数据。Kimi 2.5.3 的 schema/路由事实已从其打包 JS 核对，测试夹具均为合成正文。

最终回答要求整个 turn completed，并取最高 ordinal 末步。末步通常 completed；真实追问可能留下 interrupted 且 `endReason` 精确为 `steered`，此时只取该步最后一个结构化 text/user frame 之后的公开 assistant 正文，无 user 则取该步正文。其他/缺失原因、tool、thinking-only、隐藏/空正文和不完整 transcript 均不发布，也不回退早先步骤；失败/取消仍保留失败语义。REST 与 V2 reset/ops 共用该提取规则，不改写服务端状态。

ACK只属于当前socket generation，失效立即撤销就绪。running/queued/blocked继续既有monitor；POST结果不明时通过只读transcript定位唯一prompt，歧义明确失败，不自动重发。append按UTF-16 code unit应用，批次失败不跳游标；终态缺正文仍走有界补齐。完成后释放正文和流缓存，仅保留最多512项完成身份、32个空闲transcript，pending文本保留；这些是局部对象保留界限，不是存储总量或长期稳定性证明。

首次完整成功回答在前后台共用接纳边界保存并请求完成音，alias 只保留问题和回答身份引用。`MC_KIMI_COMPLETION_DIAGNOSTICS=1` 开启当前进程最多 128 条匿名完成/音效结果记录（`_kimi_completion_diagnostics`）；默认关闭，不保存正文或凭据，无历史补播。

固定版本不能保证云端 API 永远兼容。回退前必须验证升级后的会话格式可读，不能直接让旧程序写日常库。物理声音、日常安装切换与长期运行仍待验收；隔离云端和音效替身调用不代替这些验收。

2026-11-18：`7852d06` 的协议/顺序/音效修复后，`8858753` 补齐 steered REST 恢复；独立 client 146项及串行 integration 13项通过。真实隔离云端新请求、重连 ACK、同会话连续性、活动长任务追问、owner REST 正文相等与重放去重已通过，临时运行时和数据库已清理。证据及审查限制见 [交接](handoff.md)，不能称 clean review。真实活动长任务断连、未决 POST 整体恢复、完成身份淘汰后旧重放尚未验收；旧手机可能忽略新增顺序字段，未知历史真实到达次序无法保证重建。

保留 [审查报告](../_bmad-output/implementation-artifacts/review-kimi-stability-order-sound-20261118.md) 的D1/D2：固定2.5.3的`complete:true`却journal覆盖不足，以及同一frame权威reset合法缩短/替换，都尚无真实可达证据，不为此新增防御。PlaySound相互覆盖未复现，不添加历史补播机制。
