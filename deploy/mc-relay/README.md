# 第一批 MC relay 准备产物

模板已用于第一批真实部署：四域DNS-only A、目标Lighthouse实例两端口规则、独立证书和frps/Caddy/renew.timer已落地；仅首台default路由配置。Windows临时frpc已通过官方校验并运行，三处认证PONG、错误CA拒绝和正式deploy后自动恢复已实测。实体手机默认CELLULAR条件下系统信任TLS与真实HTTPS请求也已通过（手机存在ClashMeta VPN，未改）；第一批已由engineer独立验收通过，并完成一次限范围审查；第二、三批未开始。用户不以恢复Windows防火墙作为本批门槛；最新状态以 `_bmad-output/implementation-artifacts/plan-frp-caddy-01-server.md` 为准。以下保留安装/检查模板，所有占位符仍须按实际值填写。

## 填值与前提

先核对真实首台 pair：`default` 对应 19080，`laptop` 对应 19081。三份配置中所有 `<...>` 必须人工替换，frps 只允许首台一个映射，Caddy 只启该台一个 block。四域证书不代表第二台或论坛已启用。`cert-domains.example` 替换为四个最终域名，每行一个，无空行，安装为 `/etc/mc-relay/cert-domains`（root:mc-relay 0640）。Caddy 域名必须与首台一致。

最终新域名只建立 DNS-only A，不加 AAAA；安全组仅增 TCP 7000/8443，内部端口和 2019 不开放。先记录旧论坛真实 URL/状态、80/443 所有者与 app ID/启动时间；本次旧站 https://tingyou.cc/ 已核验，复用前按实际现场复查。不得动旧论坛、MC、CF、手机、WSL 或建立 Windows 持久任务。

FRP token 必须新建并与 MC token 分离，私有配置放仓库外。Windows CA 只用实际签发链对应的公共根证书，不能复制服务器私钥或把叶证书当根。

## 最小安装模板（前提齐备后才执行）

所有命令在人工审核、替换占位符后运行；本目录没有自动远程安装入口。核对架构、版本、官方校验值，frps/frpc 同版；仅安装已核验的独立二进制，不装默认 Caddy service。记录实际版本/校验值。

```bash
sudo useradd --system --home /var/lib/mc-relay --shell /usr/sbin/nologin mc-relay
sudo install -d -m 0755 /opt/mc-relay
sudo install -d -m 0750 -o root -g mc-relay /etc/mc-relay /etc/mc-relay/tls
sudo install -d -m 0750 -o mc-relay -g mc-relay /var/lib/mc-relay
sudo install -m 0755 <VERIFIED_FRPS> /opt/mc-relay/frps
sudo install -m 0755 <VERIFIED_CADDY> /opt/mc-relay/caddy
sudo install -o root -g root -m 0755 deploy-cert.sh /opt/mc-relay/deploy-cert.sh
# 先在仓库外生成填完值的配置，再安装（不是直接复制 .example）。
sudo install -o root -g mc-relay -m 0640 <FILLED_FRPS> /etc/mc-relay/frps.toml
sudo install -o root -g mc-relay -m 0640 <FILLED_CADDYFILE> /etc/mc-relay/Caddyfile
sudo install -o root -g mc-relay -m 0640 <FILLED_DOMAINS> /etc/mc-relay/cert-domains
```

先确认账号/目录未被其他任务使用；已有时不重建。检查 python3/venv，只补所需软件。独立 venv 安装执行时确认的兼容版本；不要升级整机或旧站环境。

```bash
sudo python3 -m venv /opt/mc-relay/acme-venv
sudo /opt/mc-relay/acme-venv/bin/pip install 'certbot==<CONFIRMED_VERSION>' 'certbot-dns-cloudflare==<CONFIRMED_VERSION>'
```

私下创建 `/etc/mc-relay/cloudflare.ini`，root 0600，内容为 `dns_cloudflare_api_token = <PRIVATE_DNS_TOKEN>`，仅授权目标 zone DNS 编辑。不要在命令参数/报告输出 token。

```bash
sudo /opt/mc-relay/acme-venv/bin/certbot certonly --non-interactive --agree-tos \
  --email '<ACME_CONTACT_EMAIL>' --cert-name mc-relay \
  --config-dir /etc/mc-relay/acme --work-dir /var/lib/mc-relay/acme-work \
  --logs-dir /var/lib/mc-relay/acme-logs \
  --dns-cloudflare --dns-cloudflare-credentials /etc/mc-relay/cloudflare.ini \
  --dns-cloudflare-propagation-seconds 60 \
  -d '<FINAL_FRP_DOMAIN>' -d '<FINAL_DESKTOP_DOMAIN>' \
  -d '<FINAL_LAPTOP_DOMAIN>' -d '<FINAL_FORUM_DOMAIN>'
sudo env RENEWED_LINEAGE=/etc/mc-relay/acme/live/mc-relay /opt/mc-relay/deploy-cert.sh
sudo -u mc-relay /opt/mc-relay/frps verify -c /etc/mc-relay/frps.toml
sudo -u mc-relay /opt/mc-relay/caddy validate --config /etc/mc-relay/Caddyfile --adapter caddyfile
sudo install -o root -g root -m 0644 mc-relay-*.service mc-relay-renew.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mc-relay-frps.service mc-relay-caddy.service mc-relay-renew.timer
```

60 秒按权威 DNS 实测调整。若用 staging，配置/工作/日志另放独立 staging 目录，不交手机且不调用正式 deploy 脚本。脚本只接受准确的正式 lineage；使用系统 CA 校验链与当前有效期，要求剩余至少一天、四域 SAN 和密钥匹配。替换后以 mc-relay validate；仅刷新原已运行的新服务，首次部署仅安装。旧证书对保存在 TLS 目录内 root 0700 的 `previous.*`，备份文件 root 0600。

## 临时 frpc 与真实验收

Windows `%LOCALAPPDATA%/MCRelay` 放同版 frpc、填值后的 frpc.toml、公共根 CA，先确认无已有 frpc 争用。以下 PowerShell 在真实配置完成后运行，保留进程用于第一批结束时明确停留状态，不注册任务。

```powershell
$relayDir = Join-Path $env:LOCALAPPDATA 'MCRelay'
& (Join-Path $relayDir 'frpc.exe') verify -c (Join-Path $relayDir 'frpc.toml')
if ($LASTEXITCODE -ne 0) { throw 'frpc verify failed' }
$relayProcess = Start-Process -FilePath (Join-Path $relayDir 'frpc.exe') -ArgumentList ('-c "' + (Join-Path $relayDir 'frpc.toml') + '"') -WorkingDirectory $relayDir -WindowStyle Hidden -PassThru
```

先验证错误 CA 确实拒绝，再用正确 CA。核对 7000、手机蜂窝 8443 证书，并比较 NATS 直连、18080 bridge、新 WSS 的 server_id；使用该台原 token 以 mobile/password 完成认证 PONG。仅 INFO、101、端口可达不足以通过，不发模型命令或改 RC 地址。

```bash
sudo /opt/mc-relay/acme-venv/bin/certbot renew --dry-run --cert-name mc-relay \
  --config-dir /etc/mc-relay/acme --work-dir /var/lib/mc-relay/acme-work \
  --logs-dir /var/lib/mc-relay/acme-logs
# dry-run 不加 --run-deploy-hooks；随后用当前正式证书验证 deploy/reload/恢复。
sudo env RENEWED_LINEAGE=/etc/mc-relay/acme/live/mc-relay /opt/mc-relay/deploy-cert.sh
sudo systemctl status mc-relay-caddy.service mc-relay-frps.service mc-relay-renew.timer
sudo systemctl list-timers mc-relay-renew.timer
sudo ss -lntp
openssl s_client -connect '<FINAL_FIRST_MC_DOMAIN>:8443' -servername '<FINAL_FIRST_MC_DOMAIN>' -verify_return_error </dev/null
```

frps 重启会短暂断开新隧道，必须实测 frpc 自动恢复。复查旧论坛同一 URL、容器 ID/启动时间与 80/443 所有者。

## 失败恢复检查

脚本在替换/validate/reload/restart 失败时恢复前一对文件并只刷新原运行的 relay 服务；首次部署失败则删除本次安装的证书对。恢复失败会返回非零，须核对 `journalctl -u mc-relay-caddy -u mc-relay-frps` 和 TLS 文件权限，不可据恢复提示直接认定成功。受限 `previous.*` 可用于人工恢复：将同一备份目录中的两文件 install 为 root:mc-relay 0640，mc-relay validate 成功后，仅 reload/restart 这两个新服务；绝不替换旧站证书或重启 Docker/MC。部署脚本的锁只串行化本脚本，人工恢复应避开续签执行。

准备阶段本地检查：`bash -n deploy-cert.sh`、替换示例值后用 Python `tomllib` 解析两份 TOML、`git diff --check`。这些不代表服务器配置或端到端已通过。

Upload deploy-cert.sh with LF line endings; .gitattributes pins LF for this script.
