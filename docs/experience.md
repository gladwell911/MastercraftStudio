# 可复用经验

## 打包的后台 Worker

- PyInstaller 冻结后的 GUI 可执行程序不应再用 `sys.executable -m 模块` 启动后台 worker；这会重启 GUI 入口并可能触发单实例退出。应打包独立的控制台 worker，并由主程序调用同目录的 worker EXE。
- 父子进程间使用 JSON Lines 时，worker 必须在入口处显式将 stdin、stdout 和 stderr 配置为 UTF-8；Windows 控制台代码页不能作为协议编码的依据。
- 启动时等待 worker 的 `ready` 事件，异常退出时保留 stderr 与退出码，可把模糊的 `Broken pipe` 转化为可诊断的启动错误。

## 固定域名的 NATS 连通性验收

- 仅检测 TCP 端口已监听不足以证明移动端可用。应使用 token 发送 NATS `CONNECT` 和 `PING`，并验证本地与公开域名均返回 `PONG`。
- 解析探针返回时需容忍服务端的 `INFO`、`+OK`、`PING` 与 `PONG` 在同一次读取中出现；服务端主动 `PING` 时应立即回 `PONG`。
- Windows 上已有 cloudflared 服务时，应修正并重启该服务，避免再启动一个相同 tunnel 的临时连接器造成流量随机落到旧 origin。

## Windows 端口回退

- 选择默认端口前先考虑系统排除端口范围；文件服务在默认端口不可用时应按确定顺序尝试高位回退端口。
- Windows socket 绑定使用 `SO_EXCLUSIVEADDRUSE`；真正创建 HTTP server 时仍要捕获 bind 失败并继续尝试下一个候选端口，避免检查与绑定之间的竞争窗口。
## Streaming UI reconciliation

- For a screen-reader desktop client, a token stream must update only the changed answer region. Avoid full list replacement, selection changes, or foreground repaint when the canonical model has no visible delta.
- When persisted history and in-memory events overlap, deduplicate by stable execution identity and sort by persisted timestamp before refreshing the UI.

## Kimi Code WebSocket lifecycle

- Kimi Code 0.38 uses an application-level JSON heartbeat rather than relying only on WebSocket transport keepalive: reply to every `ping` with a `pong` carrying the same nonce, without emitting a UI event.
- Treat an empty WebSocket read or CLOSE as terminal. Invalidate the socket before the next control send, then perform one bounded reconnect that repeats `client_hello` and subscription recovery. This avoids reusing a closed socket after an idle interval.
- Verify this path with deterministic unit coverage for heartbeat, close, reconnect, and socket-swap races. A release still requires the opt-in live idle smoke test (`KIMI_LIVE_TEST=1`) because server timing is part of the contract.
## Python virtual-environment recovery

- This desktop project targets Python 3.11. If `.venv/pyvenv.cfg` points to a missing interpreter, install Python 3.11 side by side and recreate `.venv` with `py -3.11 -m venv .venv`; do not retarget the old environment by editing its configuration.
- Restore both `requirements.txt` and `requirements-dev.txt`, then verify with `.venv\Scripts\python.exe -m pip check` and focused pytest before relying on the repaired environment.

## 远端模型分派与聊天隔离

- 提供方选择必须由规范化模型 ID 决定，不能由 `local`、`remote-ws` 等传输来源决定。否则手机端 `kimi/*` 很容易误落入 OpenRouter，并以 401 掩盖真实的路由错误。
- 可继续接收用户输入的 CLI 客户端是聊天级状态。保存客户端引用时要同时保存 owner `chat_id`，发送前校验归属，清理时用客户端对象身份防止旧 worker 清掉替换客户端。
- 远端路由测试至少覆盖“聊天 A 的交互客户端活跃，同时聊天 B 发送消息”，并断言 A 未收到输入、B 创建 turn、目标专用 worker 启动且非目标凭据未读取。

## 自动化测试分层

- 方法级入口测试、进程内协议路由 E2E、真实 NATS Server、Android 模拟器和真实模型服务是不同层级；测试汇报必须逐层说明，不能把前两层表述成真机全链路。
- 默认 CI 使用无凭据、无网络的稳定 E2E；真实手机与在线服务测试应标记为 opt-in，并在发布前单独执行。
- 端口优先级测试应引用 `REMOTE_NATS_PORT_FALLBACKS` 等生产常量，而不是复制具体回退端口，避免策略调整后测试仍断言旧值。
