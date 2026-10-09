# 双电脑远程身份配置

截至 2026-10-09，实体手机已分别通过公网连接台式机与笔记本 MC。机器身份固定：台式机 `default`，笔记本 `laptop`；每台配置自己的公开地址与固定 token，不能用地址或 token 作为聊天身份。

## MC 配置

首次启动笔记本前，在其运行环境中设置：

```powershell
$env:REMOTE_CONTROL_PAIR_ID = 'laptop'
$env:REMOTE_CONTROL_DOMAIN = 'wss://your-laptop.example/nats'
$env:REMOTE_CONTROL_TOKEN = '<private-laptop-token>'
```

这些值只是配置示例。`CLAUDECODE_REMOTE_CONTROL_*` 同名变量仍兼容，`REMOTE_CONTROL_*` 优先。机器身份的优先级为显式 `PAIR_ID` 环境变量、计算机名、持久值、`default`：`emperorComputer` 自动识别为 `default`，`emperorLaptop` 自动识别为 `laptop`，名称忽略大小写和首尾空格；其他名称沿用持久身份或旧默认。`DOMAIN` 和 `TOKEN` 的显式环境变量仍优先，否则沿用同身份持久值。身份切换会丢弃另一身份的持久地址和 token，笔记本不会继承台式凭据；改名后若识别为新身份，需要单独配置该机器的地址和 token。配置保存到 `app_data_dir/remote_machine.json`，不要把该文件或真实 token 提交到 Git。

`DOMAIN` 可输入域名或 ws/wss/http/https/nats URL；HTTP 转为对应 WebSocket，WebSocket 路径规范为 `/nats`。传输主题、执行游标、已读事件、复制连接 URL 与公开文件 URL 均采用该机器配置。公开文件 base URL 使用其对应 HTTP(S) 来源。

## 手机与隧道

手机设置分别保存两台电脑；笔记本使用 `wss://laptop.tingyou.cc/nats` 和独立应用 token，台式机沿用自己的地址和 token。两台电脑使用独立 cloudflared named tunnel 与 DNS，笔记本转发整条路径到本机 `127.0.0.1:18080` origin bridge。2026-10-09 笔记本 NATS 直连、MC 桥接与公网三处 `INFO`/`PING/PONG` 的 server ID 一致；文件 URL 的实际授权下载另待验。token 与隧道凭据均只存私有本地配置，不入 Git。

手机的标签、未读定义、完整部署步骤与 Local 测试入口维护在 [RC 双电脑文档](https://github.com/gladwell911/remoteControl/blob/master/docs/current/dual-computer-control.md)。共享笔记/常用命令同步不修改；两台 MC 可保持连接，用户保证不同时写入 OneDrive 数据。

## 验证边界

MC 配置与真实 ChatFrame 启动、pair 主题及 URL 有定向回归；私有双 broker 的 Android 通知与读清零链路已通过。用户自行重包并手动重启笔记本 MC 后，实体 ARM64 手机对两台电脑各完成一次真实问答，来源过滤与已读隔离通过。实体文件下载、单机断连、锁屏通知点击和 TalkBack 听验仍待验。当前结论与接续见 [交接](handoff.md)。
