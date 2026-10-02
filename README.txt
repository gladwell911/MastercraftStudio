神匠工坊（mc）是面向读屏用户的 wxPython Windows 桌面客户端，整合 Codex、Claude Code、Kimi Code、OpenClaw 等模型工作流，以及聊天历史、远程控制和笔记。

## 快速开始

需要 Windows 10/11 与 Python 3.11。

    python -m pip install -r requirements.txt
    python main.py

开发依赖：python -m pip install -r requirements-dev.txt。详细运行与配置说明见[归档的原入口](docs/archive/entry-context-2026-09-29/README.txt)。

## 当前状态与入口

2026-10-02：两端回答使用独立完成时间，五分钟累计标记覆盖流式完成和延迟刷新；手机通知解锁详情、点击目标及 Codex 执行重复语义已修复。Codex 新增“应用 → Codex 执行审批”，默认“不询问”，选择“需要时询问”影响后续线程启动/恢复，支持单次批准或拒绝。定向、原生模拟器及 Local 验证通过；桌面安装包和实体手机尚未更新，范围与命令见 docs/handoff.md。

聊天信息 Story 1.1–1.5 已提交；跨端 v3 执行投影、Kimi/Codex 聊天信息与 OneDrive 数据路径已有实现。2026-09-29 的云端文件只是快照；切包前核对实际安装包的数据路径，并按 docs/handoff.md 补齐之后的改动及验证。宽回归有已知基线失败，不能把定向通过称作全量通过。

- 当前交接：docs/handoff.md
- 项目文档：docs/README.md
- 工程指令：AGENTS.md
- 原入口：docs/archive/entry-context-2026-09-29/README.txt
