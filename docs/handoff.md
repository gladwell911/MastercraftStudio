# 当前交接

## 快照（2026-09-13）

桌面端已经具备用于手机端回归的隔离 strict-V2 NATS 测试夹具。它使用独立 `ChatStore`，提供桌面聊天列表、精确模型列表、新建聊天、状态查询和消息路由，并通过生产 durable outbox 发布 canonical `assistant_final` 事实。Local 模式不会调用真实 Codex/Kimi，也不会读写正常桌面数据。

跨端规格位于 `D:\code\sj\_bmad-output\implementation-artifacts\spec-cross-client-local-live-regression.md`。该父目录不是 Git 仓库，规格和验证记录不会随 MC/RC 提交。

## 本轮已完成

- `scripts/nats_e2e_desktop_harness.py` 支持 `codex/main`、`kimi/main`、种子聊天、独立新聊天、确定性回复标记和结果证据。
- V2 最终回答先提交到隔离 `ChatStore`，再通过 `RemoteNatsTransport.drain_outbox()` 发布，不再用 legacy/fake final event 冒充 V2。
- 端口优先使用固定回退表；候选均占用时分配 loopback 临时端口，TCP 与 WebSocket 端口保持不同。
- stop-file、信号处理和 RC runner 的进程树兜底共同负责结束本轮夹具；正常桌面程序、Cloudflare 和生产数据不在 Local 模式控制范围内。

## 验证状态

- `D:\code\sj\.build-envs\mc-py311\Scripts\python.exe -m pytest tests\test_nats_e2e_desktop_harness_unit.py -q`：`7 passed`。
- `python -m py_compile scripts\nats_e2e_desktop_harness.py`：通过。
- `git diff --check`：通过。
- 与 RC runner 联跑时，Android 模拟器识别和 Local strict-V2 桌面夹具启动均通过。
- 完整手机 UI 往返尚未通过：模拟器运行后 Windows 可用虚拟内存不足，Java 在 Android 构建开始前无法保留 256–512 MB heap。Live 模式也尚未使用真实 endpoint/token 和已登录的 Codex/Kimi 执行。
- 本轮 MC 改动已完整审查并暂存；由于上述跨端验收门禁尚未通过，尚未创建或推送收尾提交。

## 下一步

1. 先关闭或暂停不需要的 Docker/WSL 工作负载，或扩大 Windows pagefile，确保模拟器在线时 Java 仍可启动。
2. 在 `D:\code\sj\rc` 启动 `Codex_Cross_Client_API_35`，执行 `powershell -ExecutionPolicy Bypass -File .\scripts\run_cross_client_regression.ps1 -Mode Local`。
3. Local 全绿后，准备真实公网 endpoint/token、现有桌面聊天标题、运行中的 MC/cloudflared，以及已登录的 Codex/Kimi，再执行 Live 模式。

## 不要重复踩坑

- 不要用已连接的物理手机代替这套模拟器回归；runner 会主动拒绝物理设备。
- 不要把 fake provider 回答或 V1 直发事件当成 Live/V2 通过证据。
- 当前仓库 `.venv` 指向旧机器路径；本机已验证可用的解释器是 `D:\code\sj\.build-envs\mc-py311\Scripts\python.exe`。
- 不要为解决端口占用而停止用户正在运行的桌面/NATS 服务；夹具会自动选择可用 loopback 端口。
- 不要在日志、提交或文档中保存真实 token。
