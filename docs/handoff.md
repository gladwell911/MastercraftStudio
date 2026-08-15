# 当前交接

## 目标

保持手机端通过 `wss://rc.tingyou.cc/nats` 稳定连接桌面端，并让 NATS 与 `cloudflared` 启动失败具有可追踪、脱敏的诊断信息。

## 当前状态

- 2026-08-15，用户已在真实手机上确认可以连接位于 `D:\code\cx\mc` 的桌面程序。
- 电脑端 NATS、origin bridge 与 `cloudflared` 已恢复运行，Cloudflare 1033 已消失。
- `nats_runtime.py` 将 NATS 输出写入应用数据目录的 `nats/nats-server.log`，早退和超时会报告退出码、日志路径及脱敏日志尾部。
- `main.py` 将托管 `cloudflared` 输出写入应用数据目录的 `cloudflared/cloudflared.log`，并在启动后确认进程仍然存活。
- 当前运行的打包程序仍位于 `D:\code\cx\mc`；用户明确要求本轮不再打包或继续做连接测试。

## 验证记录

- 桌面恢复相关新增测试：11/11 通过。
- 桌面远程与打包目标组合：24 通过，1 个既有断言失败；失败测试仍期望 TCP fallback 为 `4223`，当前生产常量为 `4622`。
- 桌面远程子集：105 通过；其余既有失败与现场端口占用或旧测试假设有关。
- Flutter 远程 settings/NATS/bootstrap 核心测试：103/103 通过。
- Flutter 目标静态分析仅有一个既有 `avoid_print` info。
- 最终产品验收：2026-08-15 用户在真实手机上确认连接成功。

用户已要求不再运行设备、连接或自动化测试；收尾阶段未追加测试。

## 后续事项

- 如果要让新的日志与存活检查进入日常打包版，需要在用户授权后重新打包和部署；本轮未执行。
- 可单独修正 `tests/test_main_remote_nats_unit.py` 中过时的 `4223` 断言，使其与当前 `REMOTE_NATS_PORT_FALLBACKS` 一致。
- 若再次出现 1033，先检查 connector 进程与新日志，再检查 origin；不要先归因于手机应用 token。

## 不应重复的做法

- 不要把 `127.0.0.1:18080` 显示为 LISTEN 当成 origin 健康；它可能只是指向无监听端口的 `portproxy`。
- 不要仅凭 `subprocess.Popen` 成功判定 `cloudflared` 已上线。
- 未事先通知用户，不得为冷启动测试关闭或重启 `D:\code\cx\mc\mc.exe`。
