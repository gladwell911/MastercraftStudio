神匠工坊（mc）是面向读屏用户的 wxPython Windows 桌面客户端，整合 Codex、Claude Code、Kimi Code、OpenClaw 等模型工作流，以及聊天历史、远程控制和笔记。

## 快速开始

需要 Windows 10/11 与 Python 3.11。

    python -m pip install -r requirements.txt
    python main.py

开发依赖：python -m pip install -r requirements-dev.txt。详细运行与配置说明见[归档的原入口](docs/archive/entry-context-2026-09-29/README.txt)。

## 当前状态与入口

2026-10-06：已实现跨设备已读同步、未读标签和 Ctrl+Shift+X 未读聊天跳转。自动定位不确认已读，主动阅读确认固定回答范围；手机按范围清理通知。定向检查通过，完整跨端验收尚未通过，用户已停止桌面验收；源码未更新到日常运行包。范围、证据和接续见 docs/handoff.md。

F1 即时可视尾页与索引、消息活动即时排序、Kimi 本机安全 GET 恢复保留。2026-10-04 对应自动化与私有 Local 场景通过，物理 F1、公网 Kimi 与实体手机不在当次验证范围。

既有独立答案完成时间、累计五分钟标记、通知路由与 Codex 执行语义修复保留。菜单“应用 → Codex 执行审批”默认“不询问”，主动选择“需要时询问”影响后续线程启动/恢复，支持单次批准或拒绝。

Codex 隔离 home 通过整目录 symlink/Junction 复用当前用户的 `.codex/skills` 和 `.codex/plugins`；`.agents/skills` 在每个新任务前向原生发现注册并刷新。同名技能优先级及插件身份由 Codex 处理。旧普通目录保存在同 home 的 `skills.legacy-*` / `plugins.legacy-*`，源缺失时保留旧内容；链接失败明确报错，不退回复制，不改全局文件（包括 BOM）。插件或 marketplaces 配置变化在新任务前更新对应配置并重启 app-server，通过原 thread 恢复；执行中追加输入保持当前任务。

`package_mc.ps1` 先在临时目录构建和校验，再更新最终目录；保留包内 Codex 会话与 history、包外 history 和 OneDrive 数据，清理外链只移除链接节点。最终 `mc.exe`、`mc_worker.exe` 和标题规则校验通过后，从最终目录自动启动 MC 一次；失败返回非零，不启动旧包或临时包。本轮仅更新源码并运行隔离假构建/假启动测试，未实际打包、替换或启动日常安装版。

聊天信息 Story 1.1–1.5 已提交；跨端 v3 执行投影、Kimi/Codex 聊天信息与 OneDrive 数据路径已有实现。2026-09-29 的云端文件只是快照；切包前核对实际安装包的数据路径，并按 docs/handoff.md 补齐之后的改动及验证。宽回归有已知基线失败，不能把定向通过称作全量通过。

- 当前交接：docs/handoff.md
- 项目文档：docs/README.md
- 工程指令：AGENTS.md
- 原入口：docs/archive/entry-context-2026-09-29/README.txt
