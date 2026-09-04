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

## Provider 分派不能依赖请求来源

- 桌面本地输入和手机 `remote-ws` 只是不同传输入口，不能据此选择模型提供商。应先解析模型，再由 `resolved_model` 决定 Codex、Kimi、Claude Code 或 OpenRouter worker。
- Kimi 的 OpenRouter 401 文案可能是错误路由的结果，不等于缺少 Kimi 凭据。排查时先核对最终 worker 分派，再检查环境变量。
- 为每个远程专用模型增加“不会调用通用 OpenRouter worker”的回归断言，可直接防止同类问题复发。
