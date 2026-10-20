# 笔记本部署（交给笔记本 Codex）

目标：手机保留台式机 default，笔记本使用 laptop、`wss://mc-laptop-test.tingyou.cc:8443/nats`。服务器已独立配置 19081，笔记本 FRPC 转本机 18080。首次必须私下传递 bundle，Git 不含凭据。

1. 在笔记本拉取最新 MC 源码，准备现有 Python 3.11 `.venv`、requirements 与 PyInstaller、`tools/nats-server/nats-server.exe`；按原打包文档准备依赖和 OneDrive 笔记数据库。不要把台式机 history 复制给笔记本。
2. 将私有 bundle 解压到仓库外目录。它包含 `machine.json`（`pair_id/domain/token`，laptop 独立原 token）、`frpc.toml`、`relay-ca.pem`、FRPC 0.71.1 的 `frpc.exe` 和仅含 64 位 SHA-256 的 `frpc.sha256`。从本机交接提供的压缩包获取，不要从 Git 或示例生成假凭据。
3. 用户先退出旧 MC。选原笔记本程序的父目录 `DistPath`，保留其中 `history`。若本脚本管理的 FRPC 已运行，先只停止 `MC-Laptop-FRP` 任务及该目录 FRPC，再部署。普通权限 PowerShell 在源码目录执行：

```powershell
.\deploy\mc-relay\setup-laptop.ps1 -BundleDirectory 'C:\private\laptop-frp-handoff' -DistPath 'D:\code\cx'
```

该命令重包 `D:\code\cx\mc`，保留 history；已有 laptop token 必须与 bundle 一致，否则停止。拒绝已有 default 身份，不会自动迁移或覆盖。先用 `-ValidateOnly` 可只检查 bundle/冲突，不构建、注册任务或启动程序。不要另开一份同身份 MC。脚本不修改全局环境、不设置 Defender 排除；若被拦截，查看保护历史由用户决定如何处理。

配置副本与日志默认保存在 `%LOCALAPPDATA%\MC-Laptop-FRP`；当前用户普通权限登录任务 `MC-Laptop-FRP` 隐藏启动 FRPC 与 external 模式 MC。FRPC 子进程不继承 HTTP_PROXY/HTTPS_PROXY/ALL_PROXY，其它应用的代理不变。以后升级仍使用上面的 setup 命令；仅启动已有包时使用登录任务或 `start-laptop.ps1`。直接运行原 `package_mc.ps1` 默认自动启动不能保证 external 模式。

4. Codex 核实 MC 本地 18080、FRPC 日志注册成功，公网 `/nats` 使用该 laptop token 完成 mobile/password 的认证 PING/PONG，与本地 server_id 相同，再核 hello/readiness 和列表。无客户端注册时公网 502 是尚未接通，不能宣布部署成功。再试授权文件下载。不要为验证发送模型请求或修改数据。
5. 笔记本验证成功后，在手机 RC 设置只修改笔记本地址为上方地址，保留 laptop 原 token，台式机地址/token 不动。现手机无 startup override，保存设置即可，无需重装。确认两个来源连接、列表正确、重开仍保留。笔记本未上线前不要改手机。