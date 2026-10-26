神匠工坊（mc）是面向读屏用户的 wxPython Windows 桌面客户端，整合 Codex、Claude Code、Kimi Code、OpenClaw 等模型工作流，以及聊天历史、远程控制和笔记。

2026-10-26：通知流修复已由用户重包到日常 MC，实体 RC 已配套保数据升级，双方 feed 与日常聊天库一致；日常新回复已有系统通知发布记录。隔离实体测试通过后台/锁屏与去重，日常完整锁屏验收仍待完成。当前状态见 docs/handoff.md；维护规则见 docs/runtime-maintenance.md。

## 快速开始

需要 Windows 10/11 与 Python 3.11。

    python -m pip install -r requirements.txt
    python main.py

开发依赖：python -m pip install -r requirements-dev.txt。详细运行与配置说明见[归档的原入口](docs/archive/entry-context-2026-09-29/README.txt)。

## 既有能力与历史验证

2026-10-18：已集成可携带的“打开 HTTP(S) 网页并显示前台”入口、随包 mc-browser-open 技能和默认自动审查迁移。源码入口为 `.venv/Scripts/python.exe browser_open.py --url <URL>`，onedir 包入口为 `mc_worker.exe --browser-open --url <URL>`；模型通过运行态 MC_BROWSER_OPEN_ARGV 获取准确路径。最终隔离包同一自有页面续验已验证真实前台；正式 MC GUI、全新 profile 首启和另一台电脑仍待验，见 docs/handoff.md。本轮未替换或重启日常 MC。

2026-10-17：用户已重包 CX，正式笔记本认证、实体手机列表读取及重开自动连接通过。手机已从 CZ 临时入口恢复正式私有构建；电脑重包不会修改手机地址。原目录保留 history，新目录需迁移配置；具体步骤见 docs/remote-control.md。当前版本模型问答及长时稳定性尚待验，见 docs/handoff.md。

2026-10-09：手机已通过公网同时控制台式机与笔记本 MC，固定 default/laptop 身份及各自地址/token；`emperorComputer`、`emperorLaptop` 自动识别对应身份。笔记本独立 cloudflared 和实体 ARM64 手机双来源各一次真实问答通过，三标签过滤及单侧已读清零实测通过；文件下载、单机断连恢复、锁屏通知点击和 TalkBack 尚待验。配置见[双电脑远程身份](docs/remote-control.md)。

2026-10-07：已修复 Kimi 系统注入被误判为下一条问题、导致已有终答报“no final answer”的源码问题；覆盖回答、思考、分页和旧 owner 恢复边界，272 项定向测试通过。BMAD 旧模板渲染兼容修复另有 5 项测试通过。2026-10-07 当时安装版未包含该修复；此后用户已重包笔记本 MC，但现场旧失败记录未改动，也未据此宣称公网 Kimi 恢复通过。范围、证据和接续见[当前交接](docs/handoff.md)。

跨设备已读同步、未读标签和 Ctrl+Shift+X 未读聊天跳转保留。自动定位不确认已读，主动阅读确认固定回答范围；手机按范围清理通知。完整跨端验收尚未通过，用户已停止完整桌面验收。

F1 即时可视尾页与索引、消息活动即时排序、Kimi 本机安全 GET 恢复保留。2026-10-04 对应自动化与私有 Local 场景通过，物理 F1、公网 Kimi 与实体手机不在当次验证范围。

既有独立答案完成时间、累计五分钟标记、通知路由与 Codex 执行语义修复保留。菜单“应用 → Codex 执行审批”默认“自动审查（工作区沙箱）”，原生参数为 on-request / workspace-write / auto_review。旧无版本配置仅迁移一次，此后保留用户手选的“不询问”“需要时询问”或自动审查；只影响后续线程启动/恢复。官方审核不保证批准，拒绝后停止。

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
