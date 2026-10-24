# 双电脑远程身份配置

2026-10-19：台式机default已用external接入wss://mc-desktop-test.tingyou.cc:8443/nats，frpc转日常18080，旧CF保留；实体RC无编译覆盖、按私有持久配置接入。TLS/恢复期发送错误及前台延迟待修，见[交接](handoff.md)。下方双CF部署/私有编译方式是历史或可选方案，不代表当前台式机。

## 当前认证与升级要求（2026-10-17）

新协议 broker 使用独立 mc/mobile 用户，手机以 mobile 为用户名、该机器原 token 为密码；不再使用旧 auth_token。身份、域名与 token 的持久化规则保持不变。电脑源码更新不代表运行中的 broker 已升级，必须在用户退出旧 MC 后重新打包启动，再核对认证、hello/readiness 与列表读取。手机私有编译参数也须与正式入口配套，不能只修改持久偏好。

2026-10-17 笔记本已从 CZ 临时测试切回正式 CX：用户重包后，直连、桥接和正式公网认证/PONG 指向同一 broker；实体手机恢复正式构建后连接及聊天列表通过，重开自动恢复。新版本真实模型问答、文件下载和长时稳定性仍未验收，详见 [交接](handoff.md)。

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

## 升级、换目录与私有配置

GitHub 只保存程序与示例，不包含实际机器 token、隧道凭据或手机私有构建 JSON。MC 身份保存在安装父目录的 `history/remote_machine.json`；当前 CX 为 `D:/code/cx/history/remote_machine.json`。原目录升级保留整个 history，用户先退出旧 MC，然后在源码目录执行：

```powershell
.\package_mc.ps1 -DistPath D:\code\cx
```

目标程序是 `D:/code/cx/mc`，DistPath 是其父目录。成功后默认自动启动一次。新目录不自动继承身份；搬迁原电脑安装时，安全迁移对应 history 和运行配置，确认环境变量没有覆盖它，且隧道 origin 指向新实例。只复制程序目录不等于迁移数据；新电脑应使用自身独立身份/地址/token，不能直接冒用另一台机器。

RC 以正式私有参数构建，再覆盖安装；打包方法和换手机边界见 [RC 配置文档](https://github.com/gladwell911/remoteControl/blob/master/docs/current/dual-computer-control.md)。带私有参数的 APK 含连接凭据，也须私下保管。换手机的系统权限和本地数据需另行处理。

## 手机与隧道

手机设置分别保存两台电脑；笔记本使用 `wss://laptop.tingyou.cc/nats` 和独立应用 token，台式机沿用自己的地址和 token。两台电脑使用独立 cloudflared named tunnel 与 DNS，笔记本转发整条路径到本机 `127.0.0.1:18080` origin bridge。2026-10-09 笔记本 NATS 直连、MC 桥接与公网三处 `INFO`/`PING/PONG` 的 server ID 一致；文件 URL 的实际授权下载另待验。token 与隧道凭据均只存私有本地配置，不入 Git。

手机的标签、未读定义、完整部署步骤与 Local 测试入口维护在 [RC 双电脑文档](https://github.com/gladwell911/remoteControl/blob/master/docs/current/dual-computer-control.md)。共享笔记/常用命令同步不修改；两台 MC 可保持连接，用户保证不同时写入 OneDrive 数据。

## 验证边界

2026-10-11：新增 V2 全局命令 `readiness`，hello 宣告 `lightweight-readiness` 能力；使用既有 device/session/epoch 校验，直接返回 `{ready: true}`，旧 epoch 正常拒绝。不进入 wx 路由、不读取历史、不重新 hello、不旋转 epoch。台式机与笔记本使用各自 pair 主题。新手机仅在能力宣告后使用 PONG 加此命令；旧 MC 兼容 PONG-only。它只证明远程协议可用，不表示 Codex 执行已经开始。纯协议/传输定向 47 项通过，实体弱网及 TalkBack 听验仍待验证；本轮没有重启日常 MC 或重新打包。

MC 配置与真实 ChatFrame 启动、pair 主题及 URL 有定向回归；私有双 broker 的 Android 通知与读清零链路已通过。用户自行重包并手动重启笔记本 MC 后，实体 ARM64 手机对两台电脑各完成一次真实问答，来源过滤与已读隔离通过。实体文件下载、单机断连、锁屏通知点击和 TalkBack 听验仍待验。当前结论与接续见 [交接](handoff.md)。

2026-10-20 笔记本 FRP 部署入口见 [笔记本部署](../deploy/mc-relay/LAPTOP.md)。私有 bundle 在仓库外；普通权限运行 setup-laptop.ps1，原目录保留 history，先由用户退出旧 MC。以后升级也使用 setup 入口；原 package 默认启动不保证 external 模式。2026-10-20记录中手机已覆盖安装内置双FRP配置的版本，但这不证明笔记本已完成本机部署；后续按现场认证/列表核验及RC当前交接继续，default来源保持不变。
