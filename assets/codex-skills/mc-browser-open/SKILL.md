---
name: mc-browser-open
description: Open an explicitly requested HTTP(S) webpage in a visible foreground Google Chrome window on Windows through MC's portable helper. Use when the user asks to open a URL or show its page in the foreground. Do not use for ordinary searches, HTTP reads, login, or form submission.
---

# 打开网页并显示到前台

告知用户将打开其指定网页。仅当用户明确要求可见网页时使用；普通搜索优先联网工具，不启动 Chrome。本技能随 MC 发布，不依赖全局 web-access 或仓库外脚本。

`MC_BROWSER_OPEN_ARGV` 是运行时提供的 JSON argv，源码指向当前 Python 与 `browser_open.py`，安装包指向 `mc_worker.exe --browser-open`。不要猜路径、Python 或用户目录。使用透明的 PowerShell 数组调用，将实际 URL 作为独立参数：

```powershell
$browserArgv = ConvertFrom-Json -InputObject $env:MC_BROWSER_OPEN_ARGV
if ($browserArgv.Count -lt 2) { throw 'MC_BROWSER_OPEN_ARGV is missing or invalid' }
$browserExecutable = $browserArgv[0]
$browserArguments = @($browserArgv | Select-Object -Skip 1)
& $browserExecutable @browserArguments --url 'https://example.com/'
```

将示例 URL 替换为用户明确请求的 HTTP(S) URL，按 PowerShell 字符串规则转义单引号；不把 URL 拼接成 shell 源码，不使用 Invoke-Expression、base64 或隐藏动作。可以先读取实际 helper，再提交正式审核。不得向 helper 传任意 Chrome flags。

helper 只使用 `%LOCALAPPDATA%/ChromeAutomationProfile` 和 `127.0.0.1:9222`。遇到端口/profile 冲突、不可核实归属、headless 或多根实例时停止并报告；不杀进程、不删锁、不换端口、不切可见模式。保留日常 Chrome 与已有标签页，只操作自己创建的 target。

创建后会立即输出 `status=created` 和 `targetId`，随后输出实际 URL/title/readyState 及 HWND/PID/visible/minimized/foreground。只有最后 `status=ok` 才可报告完成；创建或 exit 0 本身不代表前台验证。若最终结果包含 `continuationAvailable=false`，说明创建记录写入失败，必须停止并报告，保留目标，不续验或重新创建。其他失败若有 targetId，禁止重复创建；使用同一 URL 和 `--target-id '<返回的 ID>'` 续验。续验仅接受当前核实实例中 helper 留有记录的自有目标。不能承诺锁屏或系统焦点限制下强制置前。

审批由当前会话原生策略决定。MC 默认 `on-request / workspace-write / auto_review`，不代表无条件批准；helper 不代答 accept、不启动 Codex 子会话、不修改审批。WMI `0x80041003` 等普通 sandbox 执行错误不等于审核 deny。仅当前真实工具 schema 与更高指令允许时，才可为同一个明确动作提交正式 `require_escalated`，交原生 reviewer 决定；不得无条件加入 `windows.sandbox="elevated"`。真实 auto-review deny/policy blocked 后立即停止，报告原理由，不换工具、线程或编码绕过，不降级审批。没有触发审核时如实写“未触发”。新机 sandbox 尚未设置时报告实际前置状态，不宣称已通过跨机验收。
