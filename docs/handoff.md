# 当前交接

## 2026-09-05 KimiCode WebSocket 修复（未完成发布验证）

- 目标：修复 Kimi Code 聊天在空闲后发送新消息时报 `socket is already closed.`。
- 已完成：`kimi_server_client.py` 现在回应 Kimi 0.38 的 JSON `ping` 心跳（原样回显 nonce 的 `pong`），在 CLOSE/空读取时使旧 socket 失效，并在控制消息发送时有界地同进程重连、重新 `client_hello` 和恢复订阅。修复位于 `fix/kimi-socket-already-closed` 分支。
- 根因：Kimi server 每 10 秒发送 JSON `ping`，若 20 秒内没有 `pong` 即关闭连接。原客户端未回复心跳且继续保留关闭的 socket，所以下一次 `subscribe()` 会在提交 prompt 之前失败。
- 已验证：`pytest tests/test_kimi_server_client_unit.py` 45/45 通过；Kimi 集成与 UI 无障碍定向集 25/25 通过。
- 阻塞：全量非实时测试在未触及的 attachment、ClaudeCode、Codex、context 和 file-manager 区域失败/卡住，因而 BMAD Build Auto 以 `implementation verification failed` 停止。未运行 `KIMI_LIVE_TEST=1` 的真实空闲窗口烟测，也未重新打包。
- 下一步：先隔离或修复全量测试失败，再设置 `KIMI_LIVE_TEST=1` 运行 `pytest tests/test_kimi_live_smoke.py`，通过后重新运行 `package_mc.ps1` 并在目标 EXE 中检验空闲后的第三轮 Kimi 聊天。

## 2026-09-01 状态

- 当前部署目标为 `D:\code\cz\mc\mc.exe`；冻结环境使用独立的控制台 `mc_worker.exe` 处理 JSONL，而不是 `mc.exe -m ...`。
- worker 启动器强制 UTF-8 stdin/stdout，修复 Windows 代码页造成的中文 Codex 回复乱码；历史中已保存的乱码不会自动恢复。
- 手机链路：文件服务默认 3923 被 Windows 保留时自动回退到 49300+；origin proxy 为 18080，NATS WebSocket 为 18081；cloudflared 服务需指向 `http://127.0.0.1:18080`。
- 验证：worker `ready -> pong -> shutdown`；本地 NATS PONG 通过，清理旧 connector 后公网 PONG 连续 10/10。

## 接续动作

1. 发送一条新的中文 Codex 消息确认不再乱码。
2. 在手机端重新连接并发送一条消息完成实机往返。
3. 再次部署前，退出 `mc.exe` 及其 NATS 子进程；目录可能被残留进程或安全软件锁定。
## 2026-09-05 cross-client review closeout

- Stories 1-11 remain complete; the blocked Story 12 record was not modified.
- The desktop review fixes historical-chat targeting, idempotent completion handling, canonical execution-list rebuild/merge ordering, and incremental Codex streaming updates that preserve keyboard focus.
- `py_compile`, focused unit tests, and UI responsiveness automation passed. Physical cross-client synchronization and screen-reader operation still require a real-device/manual check before release.
