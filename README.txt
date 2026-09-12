神匠工坊（`mc`）是一个基于 `wxPython` 的 Windows 桌面客户端，当前主要用于统一接入 Codex、Claude Code、Kimi Code、OpenClaw 等模型工作流，并管理本地聊天历史、上下文使用量和远程运行时。

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

Story 4 connected E2E 内置测试服务器、测试令牌和专用配对码，可直接运行。需要切换测试环境时，可用 `NATS_E2E_ENDPOINT`、`NATS_E2E_TOKEN` 和 `NATS_E2E_PAIR_ID` 覆盖默认值。

如需使用 OpenRouter，配置 `OPENROUTER_API_KEY`：

```powershell
setx OPENROUTER_API_KEY "你的Key"
```

如果项目目录下存在 `.venv` 或 `.venv311`，打包和测试流程优先使用对应虚拟环境。

如需使用 Kimi Code 聊天（模型下拉的 "Kimi Code"），安装并登录 Kimi Code CLI（`kimi`）。程序会自动拉起本地 `kimi web` server；可用 `KIMI_BIN` 环境变量指定 kimi 可执行文件路径。客户端会自动回应服务器心跳，空闲后无需手动重启聊天。真实链路冒烟测试默认跳过，设置 `KIMI_LIVE_TEST=1` 后运行 `pytest tests/test_kimi_live_smoke.py`。Kimi 执行期间按 F1 查看执行过程时，列表显示“正在分析问题”“正在搜索内容”“正在读取/修改文件”“正在执行测试”等中文主要步骤；英文流式思考和工具原文仅保留为内部详情，不直接作为列表标题。

多个 Kimi Code 聊天可并发运行。Kimi 的 `turn_id` 仅在各自 session 内唯一，客户端以 session 和 turn 的组合隔离事件；不要把不同聊天中同号 turn 的事件视为同一轮回答。

Kimi 的回答列表只在主代理最终正文获得权威完成确认后更新；执行过程、子代理消息、不完整流片段和失败终态不会提前显示为回答，也不会播放“回答完毕”音效。

手机端选择 `kimi/*` 后，远端消息同样走电脑端的 Kimi 专用 worker，不读取 `OPENROUTER_API_KEY`。如果 Kimi 消息返回 OpenRouter 401，应优先检查模型分派是否回退，而不是补配 OpenRouter Key。可运行以下无外部凭据回归：

```powershell
python -m pytest tests/test_mobile_kimi_cross_chat_e2e.py tests/test_remote_model_dispatch.py -q
```

## 执行过程与键盘浏览

- F1 在回答与执行过程之间切换；进入执行过程时定位最新项。若历史页尚在加载，会显示“正在加载执行过程”；加载期间切到其他控件，完成后不会抢回焦点。
- F1 进入执行过程后按稳定行身份保持焦点；刷新、重建或目标删除时只回退到同视图的确定相邻项。模态框、应用失活、离开执行过程或关闭窗口会释放该焦点租约。
- 回答和执行过程把时间与正文作为独立可访问项；执行序列相邻时间差达到 300 秒时显示新的时间分组，非法或缺失时间显示“时间未知”。紧凑摘要会隐藏有序列表序号，但详情、复制和朗读仍使用原始 Markdown。
- 初始显示最新 100 个内容行（包含问题/最终回答上下文），“更多”不计入这 100 行，可向上展开。后台更新保留仍在页内的所选内容；选中项离页后回退到有效邻近行，不自动追尾。
- 执行列表支持 Tab / Shift+Tab 单步导航、Enter / Shift+Enter 打开详情、Ctrl+C 复制完整正文。跨聊天加载时不会继续打开或复制上一聊天正文。
- 导航后的 3 秒静默窗口会延迟后台可见更新；用户主动切换视图仍即时处理。长历史继续扫描在后台进行，但初始最多两次有限页 SQLite 查询仍同步，单次慢读尚可能阻塞。
- 当前验证与未覆盖范围见 `docs/handoff.md`；真实读屏的 loading→内容播报顺序尚未人工验收。

远程 V2 会话会把桌面端的完整执行时间线作为权威事实同步到手机端。初次打开加载最新 100 个内容行，继续向上浏览使用与 owner、revision 和冻结快照绑定的不透明游标；历史页与实时事件按全局事件 ID 去重、按权威执行序号排序。检测到缺口时客户端先做有界回补，必要时应用权威快照，且不会在恢复失败时清空仍然有效的行。

## 数据位置

- 聊天历史和通用应用状态仍按应用数据目录解析；源码运行时通常在项目内的 `dist\history`。
- 笔记数据库独立存放在 `D:\code\note\notes.db`，由 `resolve_notes_data_dir()` 创建目录并定位文件。测试中应 monkeypatch 这个函数，避免读写真实笔记库。

## 打包

标准打包入口：

```powershell
.\package_mc.ps1
```

默认使用 `zgwd.spec`，产物输出到当前发布目录 `D:\code\cx\mc\`。`-DistPath` 接收产物的父目录，脚本会在其下生成 `mc\`；如需输出到其他位置，可显式指定该参数。当项目 `.venv` 不可用时，用 `-PythonExe` 显式指定 Python 3.11 解释器。注意：`package_mc.ps1` 设计为在非管理员 PowerShell 会话中运行。

打包目录同时包含 GUI 程序 `mc.exe` 和后台协议程序 `mc_worker.exe`；两者必须保持同目录。`mc_worker.exe` 专供 `mc.exe` 处理 Codex 的 UTF-8 JSONL 通信，请勿单独作为桌面程序启动。

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
- `docs/archive/` 与仍保留在 `docs/superpowers/` 的带日期设计/计划用于历史追溯，不是当前实施清单；当前行为和验收以 `docs/README.md` 指向的规格及交接为准。
