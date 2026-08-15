# 可复用经验

## 分层诊断 Cloudflare Tunnel

- 经验：按照“客户端 → Cloudflare 边缘 → connector → origin → 应用认证”逐层验证。
- 为什么重要：1033 表示边缘找不到健康 connector，请求尚未到达 origin 和应用 token 校验；把它解释成 token 错误会走错方向。
- 下次怎么用：先确认 HTTP 状态、`cloudflared` 服务/进程，再检查本地 origin 和认证。

## 识别 Windows 死端口代理

- 经验：`Get-NetTCPConnection` 显示 LISTEN 仍不足以证明应用存活；同时检查 owning process、`netsh interface portproxy show all` 和转发目标端口。
- 为什么重要：本轮 `18080` 由 `svchost` 监听，但实际转发到没有监听者的 `19080`，请求会被重置。
- 下次怎么用：对 portproxy 的监听端口和目标端口分别做健康检查。

## 子进程启动必须验证健康

- 经验：为 NATS 与 `cloudflared` 保留持久化日志，启动后检查 `poll()`/端口/协议健康，失败信息只附带有限且脱敏的日志尾部。
- 为什么重要：`Popen` 成功只表示进程被创建，不能证明进程仍存活或已经接入 Cloudflare。
- 下次怎么用：所有关键后台进程都采用“启动、短暂存活确认、协议探测、日志诊断、句柄关闭”的生命周期。
