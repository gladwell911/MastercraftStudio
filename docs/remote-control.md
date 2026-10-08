# 双电脑远程身份配置

截至 2026-10-08，手机可以同时连接台式机与笔记本 MC。机器身份固定：台式机 `default`，笔记本 `laptop`；每台配置自己的公开地址与固定 token，不能用地址或 token 作为聊天身份。

## MC 配置

首次启动笔记本前，在其运行环境中设置：

```powershell
$env:REMOTE_CONTROL_PAIR_ID = 'laptop'
$env:REMOTE_CONTROL_DOMAIN = 'wss://your-laptop.example/nats'
$env:REMOTE_CONTROL_TOKEN = '<private-laptop-token>'
```

这些值只是配置示例，未创建实际隧道。`CLAUDECODE_REMOTE_CONTROL_*` 同名变量仍兼容，`REMOTE_CONTROL_*` 优先。环境变量值会保存到该 MC 的 `app_data_dir/remote_machine.json`，此后可使用持久配置；不要把该文件或真实 token 提交到 Git。优先级为环境变量、持久值、台式机旧默认；笔记本没有台式机地址/token 回退。切换 pair 会丢弃复制来的另一身份持久值。

`DOMAIN` 可输入域名或 ws/wss/http/https/nats URL；HTTP 转为对应 WebSocket，WebSocket 路径规范为 `/nats`。传输主题、执行游标、已读事件、复制连接 URL 与公开文件 URL 均采用该机器配置。公开文件 base URL 使用其对应 HTTP(S) 来源。

## 手机与隧道

手机设置分别保存两台电脑；笔记本只需填写自己的地址和固定 token。每台电脑需独立的 cloudflared named tunnel 与 DNS，转发 `/nats` 和该 MC 的文件 origin bridge。台式机已有配置应保留；实际笔记本隧道尚未配置，不能假称已验证。

手机的标签、未读定义、完整部署步骤与 Local 测试入口维护在 [RC 双电脑文档](https://github.com/gladwell911/remoteControl/blob/master/docs/current/dual-computer-control.md)。共享笔记/常用命令同步不修改；两台 MC 可保持连接，用户保证不同时写入 OneDrive 数据。

## 验证边界

MC 配置与真实 ChatFrame 启动、pair 主题及 URL 有定向回归；私有双 broker 的 Android 通知与读清零链路已通过。实际笔记本账户、双公网隧道、实体通知点击及 TalkBack 未验收；安装包未更新。当前结论与接续见 [交接](handoff.md)。
