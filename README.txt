神匠工坊（mc）是面向读屏用户的 wxPython Windows 桌面客户端，整合 Codex、Claude Code、Kimi Code、OpenClaw 等模型工作流，以及聊天历史、远程控制和笔记。

## 快速开始

需要 Windows 10/11 与 Python 3.11。

    python -m pip install -r requirements.txt
    python main.py

开发依赖：python -m pip install -r requirements-dev.txt。详细运行与配置说明见[归档的原入口](docs/archive/entry-context-2026-09-29/README.txt)。

## 当前状态与入口

2026-10-04：F1 使用当前聊天的可视尾页即时显示执行项，历史读取使用可视索引，深扫描在后台；发送受理和成功回复后立即更新聊天排序并保留焦点。Kimi 本机连接排除环境代理，安全 GET 预算内恢复且不重复发送问题。桌面与手机针对性自动化及私有 Local 跨端测试通过；物理 F1、真实公网 Kimi 与实体手机未由本轮验证，运行包尚未更新。范围、命令和接续见 docs/handoff.md。

既有独立答案完成时间、累计五分钟标记、通知路由与 Codex 执行语义修复保留。菜单“应用 → Codex 执行审批”默认“不询问”，主动选择“需要时询问”影响后续线程启动/恢复，支持单次批准或拒绝。

聊天信息 Story 1.1–1.5 已提交；跨端 v3 执行投影、Kimi/Codex 聊天信息与 OneDrive 数据路径已有实现。2026-09-29 的云端文件只是快照；切包前核对实际安装包的数据路径，并按 docs/handoff.md 补齐之后的改动及验证。宽回归有已知基线失败，不能把定向通过称作全量通过。

- 当前交接：docs/handoff.md
- 项目文档：docs/README.md
- 工程指令：AGENTS.md
- 原入口：docs/archive/entry-context-2026-09-29/README.txt
