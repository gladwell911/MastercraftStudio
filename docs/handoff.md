# 当前交接

## 2026-09-04 状态

- 手机 `remote-ws` 与桌面本地请求统一按 `resolved_model` 选择 provider。`kimi/`、Codex、Claude Code 分别进入专用 worker，只有 OpenRouter 模型进入通用 OpenRouter worker。
- 已构建并部署桌面包到 `D:\code\cz\mc\mc.exe`，构建时间为 2026-09-04 22:04；旧包备份在 `D:\code\cz\mc_backup_20260904_2204`。
- 冻结环境继续使用同目录独立控制台程序 `mc_worker.exe` 处理 JSONL。打包 worker 已完成 `ready -> pong -> shutdown` 冒烟，退出码为 0。
- 桌面进程与 NATS 子进程已重新启动，本地 `127.0.0.1:18081`（WebSocket）和 `127.0.0.1:4622`（TCP）监听正常。
- 当前部署目标和 `package_mc.ps1` 默认输出均为 `D:\code\cz\mc`；不要再把默认产物构建到旧的 `C:\code\cx`。

## 已验证

- `python -m pytest -q tests/test_remote_model_dispatch.py tests/test_codex_worker_client.py tests/test_codex_worker_process.py tests/test_worker_launcher.py tests/test_packaging_specs.py`：55 项通过。
- 新增回归覆盖 `remote-ws` 下的 Kimi、Codex、Claude Code 分派，确保它们不会落入 OpenRouter worker。
- 部署版 `mc_worker.exe`：`ready -> pong -> shutdown`，退出码 0。

## 当前问题与下一步

1. 在电脑端或手机端选择 Kimi Code 发送唯一问题，确认本地 `kimi web` 收到请求，回复不再出现 OpenRouter 401。
2. 若仍出现裸 `[Errno 32] Broken pipe`，先记录模型、请求来源、worker 退出码和 stderr。当前 worker 启动、握手和自动重启测试均通过，不应在无复现证据时重构进程层。
3. 手机连接失败的公网服务端链路已验证可完成 NATS `INFO -> CONNECT(token) -> PING/PONG`；剩余问题需要真机日志区分旧 APK、持久化配置、DNS/VPN/证书时间或移动网络限制。

## 不应重试的方案

- 不要为 `kimi/main` 配置 `OPENROUTER_API_KEY` 来掩盖路由错误；`kimi/main` 应走本地 Kimi provider。
- 不要把冻结版 GUI `mc.exe` 当作 JSONL worker 启动；必须使用独立的 `mc_worker.exe`。
- 不要只凭手机“正在检查监听和认证令牌”的通用文案判断 token 错误，应查看最近一次真实连接异常。
