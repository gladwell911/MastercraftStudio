神匠工坊（mc）是面向读屏用户的 wxPython Windows 桌面客户端，整合 Codex、Claude Code、Kimi Code、OpenClaw 等模型工作流，以及聊天历史、远程控制和笔记。

截至 2026-11-23，源码已修复 64 位双 Ctrl 热键和可打断的争渡标题播报，并向 RC 提供会话累计、六行聊天信息和执行页最终回答。日常包以 [当前交接](docs/handoff.md) 中的部署证据为准。工程说明见 [文档索引](docs/README.md)。

## 快速开始

需要 Windows 10/11 与 Python 3.11。

    python -m pip install -r requirements.txt
    python main.py

开发依赖：python -m pip install -r requirements-dev.txt。详细运行与配置说明见[归档的原入口](docs/archive/entry-context-2026-09-29/README.txt)。

## Kimi Code

MC 使用当前用户安装的 Kimi CLI，默认查找用户目录下的 `.kimi-code/bin/kimi.exe`。可将 `KIMI_BIN` 设为绝对路径。启动 Kimi 时校验版本和构建哈希，受支持的 Windows 构建包括 2.6.3 和 2.5.3。安装和更新由用户管理。参见 [Kimi 运行时](docs/kimi-runtime.md)。

当前源码中，`/clear` 清除会话关联并保留聊天记录，下一问使用新会话。`/stop` 中断当前已开始的任务，服务器受理后界面等待实际终态。审批和提问通过对话框处理，可从“应用 → 重试 Kimi 请求”查看或重试当前聊天的未决请求。

## PowerShell 共享聊天

首次打开支持共享聊天的 MC 安装包后，新开 PowerShell，按提供方输入 `mc-codex`、`mc-kimi` 或 `mc-agy`。选会话编号，也可用 `--list` 查看会话，再以 `--chat <ID>` 指定完整的 MC 聊天 ID 或原生会话 ID。Codex 打开原生 TUI，Kimi 和 AGY 在当前提示符输入问题、查看公开步骤和终答，回合结束后可用 `/exit`。Kimi 或 AGY 的终端按 Ctrl+C 可以退出观察，仍在运行的任务可从 MC 或 RC 的同一聊天查看和停止。

每台电脑独立连接本机 MC。MC 关闭时，命令启动当前注册的安装并等待服务就绪。换安装目录后，先手动打开新目录的 MC 一次。若命令被 Windows PowerShell 的别名、函数或已有程序占用，可用 `Get-Command mc-kimi -All` 查看来源，调整冲突后重开 MC，再新开 PowerShell。MC 在当前用户目录注册命令并更新用户 Path。

在 MC 先选择模型再新建聊天，在 RC 新建时选择来源和模型。从任一端创建 AGY 聊天时选择运行电脑上的项目目录，随后可从 `mc-agy` 按同一个原生会话 ID 进入。AGY 使用当前 Windows 账号和 Windows 访问权限，默认跳过逐次工具审批。AGY 补充问题仍可在 MC 或 RC 回答。在 MC 的同一聊天输入 `/reply` 可以重新打开等待中的提问，输入 `/stop` 可以请求停止。RC 在任务状态处提供停止按钮。执行期间在两端正常发送文本可追加指令。

直接打开普通供应商终端所产生的已落盘历史也可同步到 MC 和 RC，并按原会话 ID 继续。要从两端实时观察及控制新回合，使用共享命令或应用入口。标题与删除行为见 [共享会话](docs/remote-control.md#三家-cli-共享会话)。本地候选和实体验收进度见 [当前交接](docs/handoff.md)。

测试副本可在启动前设置 `MC_SHARED_TERMINAL_REGISTER=0`，保留本机服务并跳过用户命令注册。

## 语音输入

同侧双击左 Ctrl 或右 Ctrl 开始录音。左侧结果追加到 MC 问题框末尾，后台追加保持原前台窗口。右侧结果写入识别完成时的活动编辑位置，可替换选区。听到就绪提示后单击 Ctrl 停止。错误提示与代次约定见 [语音输入](docs/voice-input.md)。

## 聊天用量、附件与阅读

- Ctrl+上、Ctrl+下按历史顺序切换聊天，目标标题通过语音输入上屏使用的争渡通道播报。继续切换会接替旧标题，普通按键和主动移焦会打断播报。最新标题读完后直接定位到回答列表的最后一条回答或未答提问。空聊天或读屏不可用时保留原焦点。回到历史列表或在列表中继续用方向键、Enter 操作时，会先同步已打开的聊天。
- Alt+A 立即播放确认音。MC 在后台清理按键时所见的聊天并自动重发首个合格提问。清除失败时，状态栏和读屏提供反馈。
- Codex 和 KimiCode 的回答列表首行以整数千位显示会话累计 token。按 Alt+Y 查看当前上下文、精确累计、最近和累计缓存命中率及原有额度。
- 提问框保留输入中的换行，长行可用 Home、End、方向键及横向滚动查看。
- 普通 OpenRouter Claude 聊天可发送 PDF、PNG、JPEG、WebP、GIF 与支持的纯文本附件。发送前后台检查文件，附件不可读时可修改原草稿后重试。
- 支持聊天附件的 MC 可接收 RC 发送到已选聊天的文件和指令。发送前核对电脑来源及文件列表。旧 MC 可继续使用 RC 的「存储到文件管理」。见 [远程控制](docs/remote-control.md#rc-聊天附件)。
- 回答列表及 Enter 详情用「（网址）」「（文件）」缩短引用。按 Shift+Enter 在浏览器打开完整回答。文件提取与继续提问沿用完整正文。

按 Alt+F4 时窗口先隐藏，MC 在后台写完已接受的数据后退出。存储故障若重试后持续出现，窗口恢复并提供重试入口。见 [关停说明](docs/runtime-maintenance.md#关闭与持久化)。

## 既有能力与历史验证

2026-10-18：已集成可携带的“打开 HTTP(S) 网页并显示前台”入口、随包 mc-browser-open 技能和默认自动审查迁移。源码入口为 `.venv/Scripts/python.exe browser_open.py --url <URL>`，onedir 包入口为 `mc_worker.exe --browser-open --url <URL>`；模型通过运行态 MC_BROWSER_OPEN_ARGV 获取准确路径。最终隔离包同一自有页面续验已验证真实前台；正式 MC GUI、全新 profile 首启和另一台电脑仍待验，见 docs/handoff.md。本轮未替换或重启日常 MC。

2026-10-17：用户已重包 CX，正式笔记本认证、实体手机列表读取及重开自动连接通过。手机已从 CZ 临时入口恢复正式私有构建；电脑重包不会修改手机地址。原目录保留 history，新目录需迁移配置；具体步骤见 docs/remote-control.md。当前版本模型问答及长时稳定性尚待验，见 docs/handoff.md。

2026-10-09：手机已通过公网同时控制台式机与笔记本 MC，固定 default/laptop 身份及各自地址/token；`emperorComputer`、`emperorLaptop` 自动识别对应身份。笔记本独立 cloudflared 和实体 ARM64 手机双来源各一次真实问答通过，三标签过滤及单侧已读清零实测通过；文件下载、单机断连恢复、锁屏通知点击和 TalkBack 尚待验。配置见[双电脑远程身份](docs/remote-control.md)。

2026-10-07：已修复 Kimi 系统注入被误判为下一条问题、导致已有终答报“no final answer”的源码问题；覆盖回答、思考、分页和旧 owner 恢复边界，272 项定向测试通过。BMAD 旧模板渲染兼容修复另有 5 项测试通过。2026-10-07 当时安装版未包含该修复；此后用户已重包笔记本 MC，但现场旧失败记录未改动，也未据此宣称公网 Kimi 恢复通过。范围、证据和接续见[当前交接](docs/handoff.md)。

跨设备已读同步、未读标签和 Ctrl+Shift+X 未读聊天跳转保留。自动定位不确认已读，主动阅读确认固定回答范围；手机按范围清理通知。完整跨端验收尚未通过，用户已停止完整桌面验收。

F1 即时可视尾页与索引、消息活动即时排序、Kimi 本机安全 GET 恢复保留。2026-10-04 对应自动化与私有 Local 场景通过，物理 F1、公网 Kimi 与实体手机不在当次验证范围。

既有独立答案完成时间、累计五分钟标记、通知路由与 Codex 执行语义修复保留。菜单“应用 → Codex 执行审批”默认自动审查，访问权限独立保存，默认完全访问，原生默认参数为 on-request / danger-full-access / auto_review。缺失或无效的访问权限回退完全访问，已有有效手选保持不变。旧无版本审批配置仅迁移一次，此后保留用户手选的“不询问”“需要时询问”或自动审查；只影响后续线程启动/恢复。官方审核不保证批准，拒绝后停止。

Codex app-server 的共享代理和计算机名覆盖配置见 [长期运行维护](docs/runtime-maintenance.md#codex-代理)。

Codex 隔离 home 通过整目录 symlink/Junction 复用当前用户的 `.codex/skills` 和 `.codex/plugins`；用户 `.agents/skills` 与源码/随包 `assets/codex-skills` 在每个新任务前合并去重，向原生发现注册并刷新。同名技能优先级及插件身份由 Codex 处理。旧普通目录保存在同 home 的 `skills.legacy-*` / `plugins.legacy-*`，源缺失时保留旧内容；链接失败明确报错，不退回复制，不改全局文件（包括 BOM）。插件或 marketplaces 配置变化在新任务前更新对应配置并重启 app-server，通过原 thread 恢复；执行中追加输入保持当前任务。

`package_mc.ps1` 默认在源码 `.dev/build/pyinstaller` 下完成临时构建和校验，再更新最终目录；保留包内 Codex 会话与 history、包外 history 和 OneDrive 数据，清理外链只移除链接节点。默认在最终产物校验后自动启动 MC 一次；传 `-BuildOnly` 则不启动，但仍会更新 `-DistPath` 下的 mc 目录。失败返回非零。不同聊天数据目录的安装副本可以并行运行，同目录仍共用单实例锁。

以后需要实际打包时，先关闭安装版 MC 及其 worker，再在源码目录的普通非管理员 PowerShell 执行以下命令；默认更新 `D:\code\cx\mc` 并在成功后自动运行：

    powershell -NoProfile -ExecutionPolicy Bypass -File .\package_mc.ps1

更新阶段失败可能留下部分程序文件，重新执行脚本完成更新；会话/history 保留，失败时不启动程序。默认不备份旧程序包。2026-10-16 实际构建仅用于独立 cz 副本，没有替换日常 cx。

聊天信息 Story 1.1–1.5 已提交；跨端 v3 执行投影、Kimi/Codex 聊天信息与 OneDrive 数据路径已有实现。2026-09-29 的云端文件只是快照；切包前核对实际安装包的数据路径，并按 docs/handoff.md 补齐之后的改动及验证。宽回归有已知基线失败，不能把定向通过称作全量通过。

- 长期运行维护：docs/runtime-maintenance.md
- 当前交接：docs/handoff.md
- 项目文档：docs/README.md
- 工程指令：AGENTS.md
- 原入口：docs/archive/entry-context-2026-09-29/README.txt
