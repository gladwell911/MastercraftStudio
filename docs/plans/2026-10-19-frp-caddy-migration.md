# frp / Caddy 迁移实施计划

日期：2026-10-19。状态：仅计划，未修改产品实现、DNS、安全组、服务器或手机，未部署或重启。
源码基线：MC `main@bae577a66d98248272e7e257589cc7601b047335`，已由父代理确认与 GitHub `origin/main` 一致。
目标：手机 RC 经新入口连接两台 MC；论坛 Android 经独立新入口访问本机 WSL Discourse；现有云服务器论坛保持运行。

## 1. 结论与边界

采用最小链路：**独立 Caddy TCP 8443 → frps 回环端口 → Windows frpc → MC 18080 / WSL 4200**。frps 控制连接使用 TCP 7000。
服务器现有 Docker 已占用 80/443，不将 Caddy 插到旧论坛前面，不改旧容器、反代、证书、共享数据库或 `/var/discourse`。
先用新增测试子域名并保留旧 Cloudflare 通道；先验证一台 MC，再验证 WSL 论坛，最后加第二台 MC。不擅自改 `site.tingyou.cc`。
RC 是 Flutter 远程控制客户端；真正论坛客户端为 `D:/code/tingyouAndroid` 的 React Native 应用。两者分别改地址，不能混为一项。
论坛已有真实 HTTP 服务，不增加 OpenAI、HTTP-RPC 或模型协议适配层。
日常 MC 退出和重启由用户本人操作。开发、隔离构建和静态检查可先完成，不借迁移恢复用户已停止的整轮桌面验收。

本方案可以移除数据路径对 Cloudflare 代理的依赖，不能保证运营商链路、电脑睡眠或 WSL 开发服务长期稳定；是否改善以同设备、同网络对照结果为准。

## 2. 已确认事实与执行前待核项

| 类别 | 已确认 | 执行前仍需核对 |
|---|---|---|
| MC 源码与运行包 | 源码 `D:/code/sj/mc`；日常运行 `D:/code/cx/mc/mc.exe`、`mc_worker.exe` | 安装包是否含本次 external 改动；源码更新不等于运行包更新 |
| 本机远程链路 | MC 18080 origin bridge；`/nats` 到 NATS，其余路径到文件服务；本次现场公网为 `wss://rc.tingyou.cc/nats` | 实际 pair、当前 NATS 监听端口及 server_id；不能按旧文档固定 18081 |
| 身份和认证 | `default` 台式机、`laptop` 笔记本；各自 token；私有数据在 `D:/code/cx/history` | 逐机核对真实身份，不由当前域名反推电脑；新 broker 使用 `mobile/password` |
| Cloudflare | 本机唯一 Windows cloudflared 服务的 origin 为 `http://127.0.0.1:18080` | 云端 ingress 未取得；不能断言 WSL 论坛与 MC 共用同一 origin |
| 手机 RC | 支持 WSS；完整编译期启动参数覆盖持久偏好；旧 `auth_token` 不兼容新认证 | 正式私有 JSON、真实安装包与两个 endpoint 的一致性 |
| WSL 论坛 | Ubuntu 运行；`discourse_dev`；4200 Ember，`start-dev.sh` 代理到 3000；3000 为 Docker 发布端口 | Windows `127.0.0.1:4200` 能否稳定直达；实际外部 host/port 配置 |
| 论坛公网 | 2026-10-19 唯一 HEAD marker 同时出现在 WSL development.log / rails.log，证明 `site.tingyou.cc` 到本机 WSL | 新入口须重新证明，旧 Cloudflare 200 不能代替新路由验收 |
| Windows 转发 | LAN `192.168.31.136:4200 → 172.24.40.23:4200`；LAN `:10081 → 127.0.0.1:4200` | LAN 与 WSL 地址可能改变，不将现场地址永久写死 |
| CVM | `ubuntu@43.133.21.196`，Ubuntu 24.04.4，2 CPU / 7.4 GiB，约 4.4 GiB 可用内存、51 GiB 空闲盘 | 安装时再次确认架构、空闲端口和磁盘 |
| 旧云论坛 | 容器 `app`，镜像 `local_discourse/app`，已运行约 4 个月；数据在 `/var/discourse/shared/standalone`；80/443 由 Docker 占用 | 变更前后记录同一旧站 URL、容器 ID/启动时间、监听与 HTTP 状态 |
| 新服务 | 未安装 frps/Caddy/nginx 可执行；7000、8443、拟用内部端口未监听 | 无监听时公网 TCP 超时不能证明安全组拒绝；需监听后复测 |
| 云网络/证书 | 无公网 IPv6；UFW inactive，INPUT ACCEPT，存在云商 SSH 黑名单链；旧证书只覆盖 `tingyou.cc` | 腾讯云安全组未取得控制台权限；新子域证书必须单独签发 |

上述运行状态为本次父代理只读勘察；代码契约已核对 `AGENTS.md`、`docs/handoff.md`、`experience.md`、`reflection.md`、`remote-control.md` 及相关源文件。现场值不是未来部署保证。

## 3. 入口、端口与目录

以下域名仅为候选，执行时确认后统一替换。都新建 DNS-only A 记录到 `43.133.21.196`，不添加 AAAA，不改旧记录。

| 用途 | 候选入口 | CVM 监听/映射 | 电脑目标 |
|---|---|---|---|
| frpc 控制 | `frp-gw.tingyou.cc:7000` | 公网 TCP 7000 | 两台 frpc 主动连接 |
| 台式 MC | `wss://mc-desktop-test.tingyou.cc:8443/nats` | Caddy → `127.0.0.1:19080` | 台式 `127.0.0.1:18080` |
| 笔记本 MC | `wss://mc-laptop-test.tingyou.cc:8443/nats` | Caddy → `127.0.0.1:19081` | 笔记本 `127.0.0.1:18080` |
| WSL 论坛 | `https://forum-dev-test.tingyou.cc:8443` | Caddy → `127.0.0.1:19420` | 论坛所在电脑可达的 WSL 4200 |
| Caddy 管理 | 不对外 | `127.0.0.1:2019` | 本机 reload |

MC 文件下载使用对应 `https://mc-…:8443/<现有文件路径>`，与 `/nats` 共用整个 origin，不单独暴露文件端口。19080/19081/19420、NATS、3000/8025/9292/9405 均不向公网开放。
两个 MC 不共用一个 upstream，不配置负载均衡或身份故障回退。论坛代理只放在实际运行 WSL 的电脑。

新文件限定：CVM 二进制 `/opt/mc-relay`，配置/密钥 `/etc/mc-relay`，运行状态/日志 `/var/lib/mc-relay`；仅新增 `mc-relay-*.service/timer`。
Windows 建议 `%LOCALAPPDATA%/MCRelay` 保存 frpc、配置、CA 公钥及日志，置于 Git 和 OneDrive 同步目录之外。
认证分三层：公网 HTTPS/WSS 证书；frpc→frps TLS + 独立 FRP token；MC 自身各 pair 的 mobile 密码。FRP token 不复用 MC token。

## 4. 阶段 A：前置检查和旧站基线（约 20–40 分钟）

1. 只读复查真实运行目录、pair、18080 和 NATS 端口；记录字段名称及验证结果，不打印 token、完整带 token URL 或服务命令行。
2. 用同一入口、同一私有应用 token 完成当前本地与旧公网认证 PONG；若当前链路本就失败，分开记录，不归因新迁移。
3. 云端记录 `uname -m`、`df -h`、`free -h`、`sudo ss -lntp`、`docker ps` 和旧容器启动时间。禁止用完整 docker inspect 输出环境秘密。
4. 对旧云论坛实际生产 URL 做一次只读 HTTP 请求并记录状态/跳转；不要把指向 WSL 的 `site.tingyou.cc` 当旧云论坛。
5. Windows 分别请求 `http://127.0.0.1:4200/`、现有 LAN 转发地址；只有内容确认为同一 WSL 论坛才选为 frpc localIP/localPort。
6. 腾讯云控制台找到本 CVM 实际绑定的安全组，只增入站 TCP 7000、8443 允许规则。8443 面向手机动态地址；7000 如电脑出口固定可限出口，否则允许动态来源并由 TLS/token 认证。检查规则顺序和绑定对象，保留 22/80/443 及出站规则，禁止“放通全部”或重建安全组。
7. 新域名、DNS 编辑凭据、安全组访问是执行所需资料；暂未取得时继续准备代码与模板，停在对应外部写入前。

停手：身份对应不清、端口被占、旧站已有异常时先定位，不抢占端口。回滚：此阶段只读；新增错误安全组/DNS 规则只撤销本次新增项。
依据：[腾讯云安全组配置](https://cloud.tencent.com/document/product/213/15377)。

## 5. 阶段 B：安装独立 relay 与证书（约 45–90 分钟）

### 5.1 二进制与权限

从 [frp 官方 Releases](https://github.com/fatedier/frp/releases) 和 [Caddy 官方下载](https://caddyserver.com/download) 选执行时可用稳定版，记录版本及校验值；frps/frpc 固定同版。按 `uname -m` 与 Windows 系统架构选择构建，不假定 ARM/x86。
只取二进制，不使用会自动占用 80/443 的系统默认 Caddy 服务。普通 Caddy 不假设已带 Cloudflare DNS 插件。
确认用户和目录未被其他任务使用后创建专用 `mc-relay` 系统用户；`/opt/mc-relay` root 所有；配置 root:mc-relay 0640，密钥目录 0750；状态目录 mc-relay 可写。
命令示例均为**后续实施模板**，含占位符时不可直接执行：

```bash
sudo useradd --system --home /var/lib/mc-relay --shell /usr/sbin/nologin mc-relay
sudo install -d -m 0755 /opt/mc-relay
sudo install -d -m 0750 -o root -g mc-relay /etc/mc-relay /etc/mc-relay/tls
sudo install -d -m 0750 -o mc-relay -g mc-relay /var/lib/mc-relay
# 将核验过的 frps、caddy 分别 install -m 0755 到 /opt/mc-relay/
```

### 5.2 DNS-01 签证与自动续签

首选独立 Certbot + 官方 `certbot-dns-cloudflare` 插件签一张包含四个新增域名的 SAN 证书，Caddy 手动加载文件。无需占 80/443，无需改旧证书。
在 `/opt/mc-relay/acme-venv` 建独立 Python venv，安装经确认的兼容版本 `certbot`、`certbot-dns-cloudflare`；先检查 python3/venv 是否存在，仅补所需包，不升级整机。
DNS API token 限定目标 zone 的 DNS 编辑权限，私下写 `/etc/mc-relay/cloudflare.ini`：`dns_cloudflare_api_token = <PRIVATE_DNS_TOKEN>`，root 0600；不放命令参数、Git 或部署报告。
配置/工作/日志全部独立，避免碰旧论坛证书。以下域名须与最终入口一致：

```bash
sudo /opt/mc-relay/acme-venv/bin/certbot certonly --non-interactive --agree-tos \
  --email '<ACME_CONTACT_EMAIL>' --cert-name mc-relay \
  --config-dir /etc/mc-relay/acme --work-dir /var/lib/mc-relay/acme-work \
  --logs-dir /var/lib/mc-relay/acme-logs \
  --dns-cloudflare --dns-cloudflare-credentials /etc/mc-relay/cloudflare.ini \
  --dns-cloudflare-propagation-seconds 60 \
  -d frp-gw.tingyou.cc -d mc-desktop-test.tingyou.cc \
  -d mc-laptop-test.tingyou.cc -d forum-dev-test.tingyou.cc
```

60 秒只是初始 DNS 等待值，按权威 DNS 实测调整。可先用独立 staging 配置目录验证授权，再签正式证书，不能把 staging 证书交手机。
证书成功后把 fullchain.pem / privkey.pem 安装到 `/etc/mc-relay/tls`，root:mc-relay 0640。只复制公有 CA 根证书到 Windows 作为 frpc trustedCaFile，**不复制服务器私钥**。
选择与实际签发链对应的公共 CA 根证书；来源用系统可信 CA 包或 CA 官方发布，校验链后固定。不能把会频繁轮换的叶证书当长期信任根。

部署脚本 `/opt/mc-relay/deploy-cert.sh`（root 拥有，仅管理新服务）至少执行以下顺序：
- 只接受本套 `mc-relay` lineage，检查证书链、SAN、有效期及证书公钥与私钥匹配；不输出私钥。
- 保存上一对 relay 证书到同一受限目录；新文件先写临时文件，均校验成功后替换两文件，再以服务用户运行 Caddy validate。
- 若 Caddy 已运行则 reload；若 frps 已运行则只重启 `mc-relay-frps.service` 以加载新证书。若尚未启动，仅安装文件。失败恢复上一对文件并恢复新服务，不动旧论坛。
- frps 重启会让新隧道短暂断线，手机应自动重连；把这一恢复纳入验收。不要声称续签无连接扰动。

独立续签 unit `mc-relay-renew.service` 核心：
```ini
[Unit]
Description=Renew certificates for MC relay only
[Service]
Type=oneshot
ExecStart=/opt/mc-relay/acme-venv/bin/certbot renew --quiet --config-dir /etc/mc-relay/acme --work-dir /var/lib/mc-relay/acme-work --logs-dir /var/lib/mc-relay/acme-logs --deploy-hook /opt/mc-relay/deploy-cert.sh
```
对应 `mc-relay-renew.timer`：
```ini
[Unit]
Description=Check MC relay certificate renewal
[Timer]
OnCalendar=*-*-* 03,15:00:00
RandomizedDelaySec=30m
Persistent=true
[Install]
WantedBy=timers.target
```

验收 `renew --dry-run` 使用同一三个独立目录；随后用当前正式证书手动调用 deploy 脚本检查权限/reload路径，再查 timer 下次运行时间和 8443 实际证书。不能仅记录“首次签发成功”。
若只能手工 DNS 验证，可用于一次连通测试；自动续签未跑通前不作为长期切换完成。
依据：[DNS 插件及凭据格式](https://certbot-dns-cloudflare.readthedocs.io/en/stable/)、[Certbot 官方安装](https://certbot.eff.org/instructions?ws=other&os=pip)。

### 5.3 frps 配置与服务

`/etc/mc-relay/frps.toml`：
```toml
bindAddr = "0.0.0.0"
bindPort = 7000
proxyBindAddr = "127.0.0.1"
allowPorts = [{ single = 19080 }, { single = 19081 }, { single = 19420 }]
auth.method = "token"
auth.token = "<PRIVATE_FRP_TOKEN>"
transport.tls.force = true
transport.tls.certFile = "/etc/mc-relay/tls/fullchain.pem"
transport.tls.keyFile = "/etc/mc-relay/tls/privkey.pem"
log.to = "console"
log.level = "info"
```
不启 dashboard、vhost HTTP/HTTPS、KCP、QUIC。回环绑定是隔离内部端口的关键，不只靠安全组。
`/etc/systemd/system/mc-relay-frps.service`：
```ini
[Unit]
Description=MC relay frps
After=network-online.target
Wants=network-online.target
[Service]
User=mc-relay
Group=mc-relay
WorkingDirectory=/var/lib/mc-relay
ExecStart=/opt/mc-relay/frps -c /etc/mc-relay/frps.toml
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
```
执行所选版本的 `frps verify -c …`，确认通过才启动。
依据：[frps 配置](https://gofrp.org/en/docs/reference/server-configures/)、[frp systemd](https://gofrp.org/en/docs/setup/systemd/)。

### 5.4 Caddy 配置与服务

`/etc/mc-relay/Caddyfile`，第一台只启对应 MC block，论坛和第二台按阶段加入：
```caddyfile
{
    admin 127.0.0.1:2019
    auto_https off
    https_port 8443
    servers {
        protocols h1 h2
    }
}
(relay_tls) {
    tls /etc/mc-relay/tls/fullchain.pem /etc/mc-relay/tls/privkey.pem
}
https://mc-desktop-test.tingyou.cc:8443 {
    import relay_tls
    reverse_proxy 127.0.0.1:19080
}
https://mc-laptop-test.tingyou.cc:8443 {
    import relay_tls
    reverse_proxy 127.0.0.1:19081
}
https://forum-dev-test.tingyou.cc:8443 {
    import relay_tls
    reverse_proxy 127.0.0.1:19420 {
        header_up X-Forwarded-Port 8443
    }
}
```
`auto_https off` 关闭自动签证与 HTTP 重定向，但显式 https + tls 仍提供 HTTPS；只允许 h1/h2，因此无需额外开放 UDP 8443。
Caddy 保留请求 Host 并设置 X-Forwarded-*，支持 WebSocket；不手造 Connection/Upgrade，不重写路径，不把 Host 改成 localhost。第一版不加无依据的缓冲/超时参数。
不开访问日志以免记录带 token 的查询字符串；若后续确需日志，先脱敏 query/认证/Cookie，不把完整私有日志提交。
`/etc/systemd/system/mc-relay-caddy.service`：
```ini
[Unit]
Description=MC relay Caddy on 8443
After=network-online.target mc-relay-frps.service
Wants=network-online.target
[Service]
User=mc-relay
Group=mc-relay
Environment=HOME=/var/lib/mc-relay
WorkingDirectory=/var/lib/mc-relay
ExecStart=/opt/mc-relay/caddy run --config /etc/mc-relay/Caddyfile --adapter caddyfile
ExecReload=/opt/mc-relay/caddy reload --config /etc/mc-relay/Caddyfile --adapter caddyfile
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
```
先以 `sudo -u mc-relay … caddy validate --config … --adapter caddyfile` 验证配置与密钥可读，再 `systemctl daemon-reload`，仅 enable/start 新的两个 service 和 renew.timer。
核对新监听只有 7000、8443、回环管理端口和已注册映射；旧 80/443 监听、旧容器启动时间和旧站请求结果应与阶段 A 一致。
依据：[Caddy 全局选项](https://caddyserver.com/docs/caddyfile/options)、[反向代理行为](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)。

停手/回滚：证书不受手机信任、抢占旧端口或旧站异常时，只停新增 relay 服务并撤回本次配置；不能 restart Docker、重建 app 或覆盖旧证书。

## 6. 阶段 C：第一台电脑 frpc 与外部链路（约 30–60 分钟）

首台选当前可以核验的真实 pair。下例是 laptop；若实际为 default，名称及 remotePort 改为 default/19080，不能复制另一机器凭据：
```toml
serverAddr = "frp-gw.tingyou.cc"
serverPort = 7000
loginFailExit = false
auth.method = "token"
auth.token = "<PRIVATE_FRP_TOKEN>"
transport.tls.enable = true
transport.tls.serverName = "frp-gw.tingyou.cc"
transport.tls.trustedCaFile = "C:/Users/<USER>/AppData/Local/MCRelay/relay-ca.pem"
log.to = "C:/Users/<USER>/AppData/Local/MCRelay/frpc.log"
log.level = "info"
log.maxDays = 3
[[proxies]]
name = "mc-laptop"
type = "tcp"
localIP = "127.0.0.1"
localPort = 18080
remotePort = 19081
```
frp 默认 TLS 不代表已校验服务器身份；必须配置可信 CA 与正确 serverName，并验证错误 CA 时连接失败。[frp TLS 文档](https://gofrp.org/en/docs/features/common/network/network-tls/)
先运行该版 `frpc.exe verify -c <绝对配置路径>`，再单独启动 frpc。服务器回环请求/WS 探针穿过 frp 后，应到当前 MC；此步可以在旧 cloudflared 仍运行时完成，不要求先重启 MC。
启动新 listener 后从电脑检查 7000、手机蜂窝检查 8443，定位 DNS→安全组→监听→TLS→frp→origin；不要通过放开全部端口排错。

持久运行采用 Windows 任务计划程序，使用当前用户、登录触发、隐藏运行；设置失败每 1 分钟重启、禁止重复实例、取消执行时长限制，电池供电也允许运行。
PowerShell 注册核心模板：
```powershell
$relayDir = Join-Path $env:LOCALAPPDATA 'MCRelay'
$relayAction = New-ScheduledTaskAction -Execute (Join-Path $relayDir 'frpc.exe') -Argument ('-c "' + (Join-Path $relayDir 'frpc.toml') + '"') -WorkingDirectory $relayDir
$relayTrigger = New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
$relaySettings = New-ScheduledTaskSettingsSet -Hidden -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
# 任务主体用 Password/S4U 非交互登录类型，以免 frpc 控制台窗口打断桌面；
# 本例不把密码写命令。按机器权限选择 S4U 或任务计划 UI 安全录入凭据后注册。
```
S4U 是否满足本机网络/文件权限必须实际验证；只用本机配置和出站 TCP，不依赖网络共享。若不支持，使用任务计划 UI 的“无论用户是否登录都运行”并安全存储所需凭据。
本阶段保证用户登录后可用，不将“frpc 自启”等同“MC GUI 或 WSL 开机即运行”。先核对机器已有启动安排；无人登录自启若非当前要求，不额外设计 Windows 服务包装器。
只对新任务做结束/再启动测试，确认自动恢复；不终止 MC。路由恢复依赖 MC/WSL origin 自身在线，电脑睡眠期间不可远控。
依据：[frpc 配置与 loginFailExit](https://gofrp.org/en/docs/reference/client-configures/)。

停手/回滚：新 WSS 到错误 server_id、认证失败或恢复失败时不改手机。只停新 frpc/计划任务，旧路径继续使用。

## 7. 阶段 D：MC 最小 external mode 改动（约 1.5–3 小时）

现有实现硬依赖 cloudflared，单改公网域名不足以完成迁移。新增一个小开关，建议 `REMOTE_CONTROL_TUNNEL_MODE=external|cloudflared`，默认 cloudflared；兼容现有 `CLAUDECODE_REMOTE_CONTROL_*` 优先级习惯，不做可插拔隧道框架。
开关先用用户级环境或实际启动环境持久保存；若使用 GUI 启动，要确认新 MC 进程实际继承，不能只在一次 PowerShell 会话里设置后就声称持久生效。未知值报配置错误。

| 位置（以函数名定位，基线行号仅辅助） | 最小改动 |
|---|---|
| `main.py:_ensure_cloudflared_tunnel`，约 17899 | 调用前按 mode 分流，external 不查询/启动/修改/停止 cloudflared 服务或托管进程，不把跳过管理等同公网已就绪 |
| 启动 `_ensure_remote_nats_startup_connectivity`，约 18110 | 两模式均启动现有 origin bridge、探测真实本地 NATS 和公网认证；external 失败保留可诊断降级状态和恢复探测 |
| 文件发送路径，约 3515 | 移除 external 对 cloudflared 存在的硬门槛；保留真实服务/授权和目标 URL 可用检查 |
| 健康循环，约 17110 | external 保留本地/public PONG/readiness，跳过 cloudflared 通道计数、服务重启、managed connector 恢复分支 |
| `_remote_external_route_ready` | 当前写死 443；使用 `urlsplit(url).port`，缺省按 wss/https=443、ws/http=80；按实际支持协议处理其他值，不一律改为8443 |
| `_cloudflare_file_public_base_url`，约 16997 | 已用 netloc 保留端口；external 取当前配置的发布 URL，避免旧 `cloudflared_url` 优先级覆盖新入口；不必大面积重命名 |
| 状态文案与清理路径 | external 显示真实入口与验证结果，移除硬编码 rc.tingyou.cc/cloudflared 成功描述；关闭时只清理 MC 自有资源 |

`remote_machine_config.py` 已接受完整 WSS URL 并规范为 /nats；持久字段 `domain` 存的是 URL，不是只含 hostname。新值例 `wss://mc-laptop-test.tingyou.cc:8443/nats`；文件 base 为 `https://mc-laptop-test.tingyou.cc:8443`。
保留本机 pair/token 与 history，禁止用默认 token 覆盖旧凭据。对外报告剥离 token；应用连接密码不应进入 Caddy 日志。
验证仅覆盖改动链：默认 cloudflared 行为不变、external 全部调用路径不碰 CF 管理、真实失败不误报 ready、8443 URL/文件URL保留、双 pair 不串号。
优先扩展现有 `tests/test_remote_machine_config.py`、`tests/test_main_remote_nats_unit.py`、相关 readiness/file/cloudflared 测试的必要用例；不新增泛化测试框架。
隔离构建示例 `package_mc.ps1 -BuildOnly -DistPath <独立父目录> -WorkPath <独立构建目录>`；先确认目标不存在正在运行的 MC。不备份旧整个程序包，不改 OneDrive 数据。
正式替换前完成代码与构建检查，按项目约定核对数据路径；用户退出旧 MC 后再替换，使用 `-BuildOnly` 避免自动启动，由用户手动启动。不得同时让两个同 pair 实例争用正式入口。
本次只写计划；以上实现/配置/重启均未执行。

停手/回滚：external readiness 不通过就保留/恢复旧 URL 和 cloudflared 模式；由用户重启使配置生效。只撤回新增模式配置和必要代码，不还原历史数据库。

## 8. 阶段 E：手机 RC 单机切换（约 30–60 分钟）

1. 先比较当前 NATS 直连、18080 bridge、新公网 /nats 三处 INFO.server_id；使用同一机器现有 token，以 `user=mobile, pass=<该机 token>` CONNECT 并 PING/PONG。HTTP 101 和 INFO 均不等于认证通过。
2. 执行真实 hello、readiness、列表读取，确认 pair 与 CX 运行实例，不能用另一机器成功代替。
3. 从现有正式私有 JSON 复制一份迁移配置到私有路径，只更新首台 endpoint；保留两来源 token、另一来源地址及 backup 配置。不要假设把旧入口塞进 backup 就支持自动故障转移，按现有字段语义使用。
4. RC 编译参数为 `REMOTE_CONTROL_ENDPOINT` / `REMOTE_CONTROL_TOKEN`（default）和 `REMOTE_CONTROL_LAPTOP_ENDPOINT` / `REMOTE_CONTROL_LAPTOP_TOKEN`（laptop）；新 endpoint 必须完整保留 :8443/nats。只改手机设置可能被下次启动的 StartupOverride 覆盖。
5. 在 `D:/code/sj/rc` 用既有 `scripts/install_physical_arm64.ps1 -DeviceId <实际实体 serial> -DartDefineFile <私有迁移 JSON>` 构建并保数据安装，核对设备 APK hash；不卸载、不清数据。
6. 手机蜂窝完成：列表→目标聊天→一次真模型回答→该机文件授权下载并核对内容；重开 app 仍连接新入口。再用 Wi-Fi 做一次连接对照，不重复收费模型请求。
7. 暂停/恢复首台新 frpc，观察 RC 恢复与归属，另一来源应不受影响。记录恢复耗时与错误，不重发执行结果未知的模型命令。

停手/回滚：认证模式不配套先配套升级，不能换 token 或放宽验证掩盖。新链路失败时保数据装回原正式私有配置对应 APK，并恢复该机 MC 的旧发布 URL（用户重启），旧 CF 服务继续保留。

## 9. 阶段 F：WSL 论坛与真正 Android 客户端（约 1–3 小时）

在论坛所在电脑 frpc 增一项，不与 MC 混用 remotePort：
```toml
[[proxies]]
name = "forum-wsl"
type = "tcp"
localIP = "127.0.0.1" # 只有阶段 A 证明确实到 WSL 4200 时才使用
localPort = 4200
remotePort = 19420
```
若 Windows localhost4200 不可达，先采用已证实的现有 LAN4200 转发；没有理由就不换 WSL 网络模式、不新增通用路由服务。
WSL 重启后先查当前 IP 和监听，再更新**既有那一条** portproxy 的 connectaddress；保留 listenaddress/port，不批量 reset。需要永久化时只把这一步并入现有 start-dev 启动入口或一个短启动脚本。
不为验证强行 `wsl --shutdown`；在用户下一次正常重启 WSL 后补一次 origin→公网 marker 复验。没有重启证据时如实标注未验证。
先让 Caddy 新域返回论坛；向新入口带唯一无秘密参数请求，确认 marker 出现在本机 `/home/gladwell/discourse/log/development.log` 或 `tmp/start-dev/rails.log`，旧云 app 无对应请求；确认 CSS/JS 和 /session/csrf 路径均透传。

**canonical host/port 是本阶段关键。** 已读本机 `config/database.yml`：development 的 `host_names` 使用 `DISCOURSE_HOSTNAME`，明确不包含端口，端口通过站点 `port` 设置；本机 `EnforceHostname` 会清除 X-Forwarded-Host，并将 Host 规范到配置值。因此只加代理头不能保证 :8443 登录和绝对链接正确。
先读取实际开发启动参数与有效配置，只在 WSL 开发实例做最少调整：hostname 为新测试域，适用的站点 port 为 8443，按实际请求决定是否需 force_https。不要把带 :8443 的串盲写 hostname，也不要修改 upstream 3000 或 Ember 4200 来模拟公网端口。
任何更改前记录该开发实例相关原值；如果需修改站点设置，先确认连接的是 WSL 的开发数据库，绝不连接云论坛或其他共享数据库。不批量 rebake 帖子，不运行迁移，不复制数据库。
变更 hostname/port 可能令旧开发入口产生跳转或 Cookie 差异；旧入口保留并不保证两种 canonical URL 同时完全可用。先验证再安排短切换；若需重启，只重启对应 WSL 开发 Rails/Ember 进程，不能重启整个容器或共享服务。
若当前实例无法通过局部配置支持非标准端口，停在测试入口并恢复原值；另行决策独立 443 入口资源，不能临时夺取云端旧论坛 443。
依据：[Discourse 开发数据库配置](https://github.com/discourse/discourse/blob/main/config/database.yml)、[canonical host 中间件](https://github.com/discourse/discourse/blob/main/lib/middleware/enforce_hostname.rb)。

论坛客户端仅修改 `D:/code/tingyouAndroid/js/single_site_config.js` 的 SINGLE_SITE_URL 为 `https://forum-dev-test.tingyou.cc:8443`。先查该仓库 AGENTS/现有构建说明，复用已签名安装方式；不套用 RC 的 Flutter 脚本。
优先测试包验证，保留正式配置和本地数据；新 hostname 可能要求重新登录或重新授权用户 API key，不能将旧站授权静默套给新 host。
必要验收为：登录和退出→Cookie 持续有效→读取及发送一条测试帖子/回复→上传并打开一个小文件→重开 app。检查跳转、canonical、图片/头像/上传 URL 保留正确 https/域名/:8443，无 mixed content。
网络层实际观察 MessageBus 长轮询是否完成并继续下一轮；若当前客户端确实使用 SSE/WS，再验对应流或升级，不把所有论坛请求想象成 WebSocket，也不凭首页 200 宣称完成。
CSRF/Origin 问题先核对 Host、协议、端口及 Ember→Rails 头传递；不禁用 CSRF、不开泛 CORS、不开全域 Cookie作为修复。上传与长轮询失败时只调整已证实的限制。

停手/回滚：身份、CSRF、绝对链接或长轮询失败时先恢复 WSL 原 hostname/port/https 配置和客户端旧 URL；只撤销 forum-wsl 代理及 Caddy forum block，其余 MC 链路不动。`site.tingyou.cc` DNS 保持原值。

## 10. 阶段 G：第二台 MC、恢复验证与完成标准（约 45–90 分钟）

首台已通过后再给第二台复用同版 frpc/external 支持，使用另一独立域名、映射端口、pair 和原 token；不重复设计、不克隆另一台 history。
第二台同样完成 server_id + authenticated PONG、hello/list、一次真模型回答与该机文件下载；RC 两来源同时在线，分别打开对应列表和文件，确认不串源。
只停止其中一台新 frpc，另一台保持可用；恢复后原台自动连接，已执行命令不重复发送。至少做一次手机 Wi-Fi→蜂窝切换和 app 重开。
新链路初验通过后安排 24–48 小时日常观察，关注断线恢复、睡眠唤醒、证书任务和长轮询；观察时长不是稳定性保证，不为凑时长阻塞代码交付。

| 必须证据 | 通过条件 |
|---|---|
| 旧云论坛前后对比 | 同一 URL 正常、80/443 所有者不变、app ID/启动时间不变，未改数据 |
| 网络/TLS | 手机蜂窝正确证书/SAN，7000 认证成功，内部映射只监听127.0.0.1 |
| MC 两机 | 各自本地/bridge/公网 server_id 一致，mobile 认证 PONG，hello/readiness/list 正常 |
| 真实业务 | 每机一次真模型回答、正确来源文件授权下载；不复用历史版本问答代替 |
| WSL 身份与业务 | 新公网唯一 marker 落本机日志；登录/帖子/上传/实际长轮询通过 |
| 恢复 | 单 frpc 断开再恢复、RC 重开/换网、证书 deploy 路线；WSL正常重启后复验另记录 |
| 私有配置 | pair/token 不串源、完整 :8443 URL、生效环境与APK一致；报告无秘密 |

不因外网短测通过就停用旧 cloudflared：论坛旧通道归属尚未查清，停唯一 Windows 服务可能影响其他路径。只有用户另行决定收掉旧入口且完成归属核验时才局部清理。
若任一阶段失败，只回滚该阶段新增配置；不自动清数据库、重启共享服务、全机回退或增加第二套备用架构。SSH 反向隧道仅是已有能力的排错备选，不与本主方案并行部署。

## 11. 工作量与交付

预估主动实施约 5–10 小时，受 DNS/安全组授权、手机构建、当前 WSL 非标准端口兼容性影响；24–48 小时观察另计。先打通首台能最快判断新链路是否值得继续。
交付仅需：MC 最小代码补丁及定向验证、三份服务配置和续签脚本/任务、两台私有 frpc 配置、两个手机应用的正确构建、上述一页结果表；不增加通用平台或多份流程文档。
本文件是唯一计划。实施时记录真实版本、最终域名/端口、通过项与未验项即可；真实凭据、APK和私有日志保存在仓库外。
