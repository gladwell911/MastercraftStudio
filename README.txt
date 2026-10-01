神匠工坊（mc）是面向读屏用户的 wxPython Windows 桌面客户端，整合 Codex、Claude Code、Kimi Code、OpenClaw 等模型工作流，以及聊天历史、远程控制和笔记。

## 快速开始

需要 Windows 10/11 与 Python 3.11。

    python -m pip install -r requirements.txt
    python main.py

开发依赖：python -m pip install -r requirements-dev.txt。详细运行与配置说明见[归档的原入口](docs/archive/entry-context-2026-09-29/README.txt)。

## 当前状态与入口

2026-10-01：Epic1–5 核心桌面/模拟器 QA 已通过，详细范围见 docs/handoff.md；当前交付尚未合并或发布。

聊天信息 Story 1.1–1.5 已提交；跨端 v3 执行投影、Kimi/Codex 聊天信息与 OneDrive 数据路径已有实现。当前安装的旧 MC 仍写本地笔记库，2026-09-29 的云端文件只是快照；新包切换前须按 docs/handoff.md 补齐数据并验证。宽回归有已知基线失败，不能把定向通过称作全量通过。

- 当前交接：docs/handoff.md
- 项目文档：docs/README.md
- 工程指令：AGENTS.md
- 原入口：docs/archive/entry-context-2026-09-29/README.txt
