# 第一批：独立 relay 服务器与最小链路

日期：2026-10-19。状态：仅计划，未执行；本文不代表服务器、DNS、手机或本机配置已经变更。
本批须由用户后续明确启动；director 实施，engineer 独立验证，完成本批后停止。

## 必读与依赖

- [总计划第 1 节：共同边界](2026-10-19-frp-caddy-migration.md#1-结论与边界)。
- [第 2–3 节：现场事实、候选入口与端口](2026-10-19-frp-caddy-migration.md#2-已确认事实与执行前待核项)。
- [第 4 节：前置检查](2026-10-19-frp-caddy-migration.md#4-阶段-a前置检查和旧站基线约-2040-分钟)。
- [第 5 节：服务器、证书和服务模板](2026-10-19-frp-caddy-migration.md#5-阶段-b安装独立-relay-与证书约-4590-分钟)。
- [第 6 节：frpc 配置与链路探针](2026-10-19-frp-caddy-migration.md#6-阶段-c第一台电脑-frpc-与外部链路约-3060-分钟)，本批仅取临时连接部分。
- 执行前取得最终新域名、DNS 编辑权限、安全组权限及一台现有 MC 的本机访问条件；候选域名不能视为已确认。
- 读取项目 AGENTS.md 与当前交接。历史 IP、端口、版本和机器身份须现场核对。

## 目标与范围

证明新服务器独立运行，且真实外网可经 Caddy → frps → 临时 frpc → 当前 MC 完成认证 PONG。
包含 DNS、安全组、独立证书及续签、frps/Caddy、新监听，以及第一台电脑的最小临时 frpc 探针。
本批不改 MC 产品代码、运行包、发布地址、RC 手机地址或 WSL 论坛 canonical；不建立 Windows 持久任务。
可以启动独立 frpc 访问当前 18080，不退出或重启日常 MC，不操作旧 cloudflared。
服务器固定为 ubuntu@43.133.21.196；旧 /var/discourse、app 容器及其 80/443 保持原状。

## 按序执行

1. 记录旧云论坛实际 URL、HTTP 状态、80/443 所有者、app 容器 ID/启动时间及服务器架构、资源。
   核对首台真实 pair、NATS 当前端口及 18080；在现有本地/旧公网完成同机认证 PONG，分别记录既有异常。
2. 确认四个新域名后，按总计划第 3 节建立 DNS-only A → 43.133.21.196，不加 AAAA，不改旧记录。
   仅为实际绑定安全组新增 TCP 7000、8443；保留 SSH、旧站和出站规则，不放通全部端口。
3. 按第 5.1 节下载并核验架构、版本、校验值；frps/frpc 固定同版，Caddy 使用独立二进制。
   仅使用 /opt/mc-relay、/etc/mc-relay、/var/lib/mc-relay 和新增 mc-relay 服务名，落实专用用户及权限。
4. 按第 5.2 节建立独立 Certbot 环境，以 DNS-01 签四域 SAN 正式证书，不占 80/443。
   凭据与私钥留在受限私有路径；Windows 只取得对应公共 CA 根证书，FRP token 不复用 MC token。
5. 按第 5.3–5.4 节落地 frps、Caddy 与 systemd 模板，先 verify/validate，再启动新增服务。
   frps 控制端口为 7000，Caddy 为 8443；只启首台对应 MC block，不提前启第二台或 forum block。
   内部映射 default=127.0.0.1:19080、laptop=127.0.0.1:19081、forum=127.0.0.1:19420；管理端口也只回环。
6. 按第 6 节在首台 %LOCALAPPDATA%/MCRelay 放置独立 frpc，配置真实 pair 对应 remotePort 与现有 18080。
   核对 serverName、trustedCaFile 和独立 FRP token；verify 后隐藏启动临时进程，验证错误 CA 确实拒绝连接；先确认无现有 frpc 争用同一 proxy/映射端口。
   不注册持久任务；旧 MC 和 CF 同时保留。记录该临时进程是否在本批结束时停止，交给第二批接续。
7. 在新监听启动后，从电脑验证 7000，从手机蜂窝验证 8443 的证书及真实请求。
   使用 user=mobile、pass=<该机原 token> 认证，比较 NATS 直连、18080 bridge、新 WSS 三处 server_id，并完成 PONG。
   手机蜂窝可使用临时探针，不修改 RC 的持久或编译地址；不发送模型命令。
   按 DNS→安全组→监听→TLS→frp→origin 定位失败；ss 监听、HTTP 101、INFO 均不能代替端到端认证。
8. 按第 5.2 节完成 deploy-cert.sh、独立 renew.service/timer、renew --dry-run。
   再用当前正式证书手动执行 deploy 路线，检查权限、Caddy reload、frps 重新加载及临时 frpc 自动恢复。
   记录 timer 下次执行时间及 8443 实际证书；续签造成的新隧道短断必须如实记录。
9. 对照第 1 步复核旧论坛、80/443、app ID/启动时间；engineer 独立核对本批证据和实际状态。
   只检查本批链路，不运行 MC 全套回归，不提前开始第二批部署。

## 验收与停手

- DNS 指向、证书 SAN/有效期、服务版本明确；公网只新增 TCP 7000/8443，内部映射仅回环。
- 首台三处 server_id 一致且 mobile 认证 PONG 成功；真实蜂窝 8443 可达，错误 CA 被拒绝。
- 续签 dry-run、正式 deploy 路线与临时 frpc 恢复通过；自动续签仅手工 DNS 时不能标为完成。
- 旧云论坛及旧 MC/CF 路径保持可用；未修改运行包、手机地址或 WSL 配置。
- 若用户不允许临时本地 frpc，仅可交付“server-only 验收”，端到端项标未验，不能称 frp 整体通过；第二批先补此项。
- 身份不清、端口占用、证书不可信、PONG 失败或旧站异常即停在对应步骤，不推进手机切换。
- 回滚只停本次临时 frpc、新 relay 服务并撤销本次新增错误配置/DNS/规则；不重启 Docker、app 或 MC，不替换旧证书。

## 交付下一批

| 非敏感交接项 | 记录要求 |
|---|---|
| 域名与路由 | 四个最终域名、A 记录、端口、首台 pair 与映射；完整 URL 不含 token |
| 版本与状态 | frps/frpc/Caddy/Certbot 版本和校验值、服务名、配置位置、临时 frpc 去留 |
| 证据 | 旧站前后结果、三处 server_id/PONG、蜂窝结果、错误 CA 结果、续签及恢复结果 |
| 待办 | 未验项和原因、证书下次检查时间；凭据只指私有保存位置，不抄内容 |

[下一批：两台 MC 与 RC](2026-10-19-frp-caddy-02-mc-rc.md)。此处停止，下一批需用户另行启动。

> 启动口令：执行 docs/plans/2026-10-19-frp-caddy-01-server.md，仅第一批；由 director 实施、engineer 独立验证，完成后停止，不推进第二批。
