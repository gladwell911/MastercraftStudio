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

聊天信息 Story 1.1–1.5 已提交；跨端 v3 执行投影、Kimi/Codex 聊天信息与 OneDrive 数据路径已有实现。2026-09-29 的云端文件只是快照；切包前核对实际安装包的数据路径，并按 docs/handoff.md 补齐之后的改动及验证。宽回归有已知基线失败，不能把定向通过称作全量通过。

- 当前交接：docs/handoff.md
- 项目文档：docs/README.md
- 工程指令：AGENTS.md
- 原入口：docs/archive/entry-context-2026-09-29/README.txt
