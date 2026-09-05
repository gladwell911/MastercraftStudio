# 当前交接

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
