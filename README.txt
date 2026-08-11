神匠工坊（`mc`）是一个基于 `wxPython` 的 Windows 桌面客户端，当前主要用于统一接入 Codex、Claude Code、OpenClaw 等模型工作流，并管理本地聊天历史、上下文使用量和远程运行时。

## 快速开始

环境要求：
- Windows 10 / 11
- Python 3.11

安装依赖：

```powershell
python -m pip install -r requirements.txt
```

开发测试依赖：

```powershell
python -m pip install -r requirements-dev.txt
```

启动程序：

```powershell
python main.py
```

## 常用配置

如需使用 OpenRouter，配置 `OPENROUTER_API_KEY`：

```powershell
setx OPENROUTER_API_KEY "你的Key"
```

如果项目目录下存在 `.venv` 或 `.venv311`，打包和测试流程优先使用对应虚拟环境。

如需使用 Kimi Code 聊天（模型下拉的 "Kimi Code"），安装并登录 Kimi Code CLI（`kimi`）。程序会自动拉起本地 `kimi web` server；可用 `KIMI_BIN` 环境变量指定 kimi 可执行文件路径。真实链路冒烟测试默认跳过，设置 `KIMI_LIVE_TEST=1` 后运行 `pytest tests/test_kimi_live_smoke.py`。

## 数据位置

- 聊天历史和通用应用状态仍按应用数据目录解析；源码运行时通常在项目内的 `dist\history`。
- 笔记数据库独立存放在 `D:\code\note\notes.db`，由 `resolve_notes_data_dir()` 创建目录并定位文件。测试中应 monkeypatch 这个函数，避免读写真实笔记库。

## 打包

标准打包入口：

```powershell
.\package_mc.ps1
```

默认使用 `zgwd.spec`，产物输出到 `C:\code\cx\mc\`。注意：`package_mc.ps1` 设计为在非管理员 PowerShell 会话中运行。

## 代码入口

- `main.py`：主界面与大部分应用逻辑
- `codex_client.py`：Codex 客户端封装
- `claudecode_client.py`：Claude Code 客户端封装
- `openclaw_client.py`：OpenClaw 客户端封装
- `kimi_server_client.py`：Kimi Code 本地 server（`kimi web`）客户端封装，支撑 `kimi/` 模型族聊天
- `nats_runtime.py`、`remote_nats.py`：NATS 相关运行时与远程协作逻辑
- `tests/`：当前 pytest 测试
- `docs/README.md`：当前有效文档索引

## 当前维护约定

- 把 `README.txt` 视为项目主入口文档。
- `docs/` 第一层只放当前仍有效的说明文档。
- 历史设计和计划文档统一放在 `docs/archive/`，默认不作为日常上下文输入。
