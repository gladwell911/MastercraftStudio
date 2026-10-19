---
title: '第一批 frp/Caddy 独立 relay'
type: 'chore'
ticket: ''
created: '2026-10-19'
status: 'built'
route: 'full'
route_source: 'auto'
review: 'quick'
review_source: 'pinned'
lenses_ran: [president-scope-review]
review_loop_iteration: 0
followup_review_recommended: false
context:
  - 'D:/code/sj/mc/AGENTS.md'
  - 'D:/code/sj/mc/docs/plans/2026-10-19-frp-caddy-migration.md'
  - 'D:/code/sj/mc/docs/plans/2026-10-19-frp-caddy-01-server.md'
warnings: [oversized]
deferred: []
baseline_revision: '327871467aa4f58e04048f3ec2d6b5664f6eb03b'
---

<intent-contract>

## Intent

**Problem:** 第一批需要独立 relay 服务及首台现有 MC 的认证链路。最终域名及 DNS/安全组访问尚未齐备，不能直接部署候选域名。
**Approach:** 按总计划第 4.7 节先完成可独立验证的配置模板与证书部署脚本，记录实际只读基线；资料未齐备时暂停外部写入，整批保持 blocked。

## Boundaries & Constraints

**Always:** 第一批按现有总计划执行；真实凭据在仓库外；仅新增 mc-relay 服务；模板必须明确填值后再执行，默认不执行远程操作。
**Never:** 不改 MC 产品代码、运行包、发布 URL、RC、WSL 或旧 CF；不动旧论坛、80/443、容器和数据库；不建立 Windows 持久任务；不创建 DNS、安全组或服务器文件，直到明确部署前提齐备。

</intent-contract>
<intent-contract id="first-batch-live-continuation-2026-10-19">

## Intent — 第一批真实实施接续

**Problem:** 准备产物已验证，现需按第一批步骤 1→9 真实落地独立 relay 并验证首台最小链路。
**Confirmed inputs:** 用户确认四域为 `frp-gw.tingyou.cc`、`mc-desktop-test.tingyou.cc`、`mc-laptop-test.tingyou.cc`、`forum-dev-test.tingyou.cc`，ACME 邮箱 `gaopeiyou@foxmail.com`；用户说明已完成 Cloudflare/腾讯云登录，明确授权代理配置 DNS、安全组、独立证书与本批 relay 服务。输入不再限于模板准备。
**Scope:** 直接复用准备文件，先补旧站/本机认证基线，再按第一批顺序配置四个 DNS-only A 到 43.133.21.196、实际绑定安全组 TCP7000/8443、独立 frps/Caddy/Certbot/renew 和首台临时 frpc。后由 engineer 独立验证。真实蜂窝另取实际证据；完成第一批即停止。
**Boundaries:** 不改旧站、80/443、Docker/数据库、MC运行包/发布URL、CF、RC或WSL；不重启日常MC、不建Windows持久任务、不推进第二批、不提交Git。凭据仅在受限私有路径，不输出token；仅使用用户指定实际 web-access 技能与专用浏览器，不改全局技能。

</intent-contract>

## Code Map

- docs/plans/2026-10-19-frp-caddy-migration.md 第 5–6 节：配置、权限、服务和续签契约。
- docs/plans/2026-10-19-frp-caddy-01-server.md：范围及验收依据。
- 新增 deploy/mc-relay/：可检查的模板、证书部署脚本和简短使用说明；沿用仓库现有组织，如存在同类目录则优先复用。

## Tasks & Acceptance

**Execution:**
- [x] deploy/mc-relay/：frps.toml.example、frpc.toml.example、仅首台的 Caddyfile.example；明确 pair/域名/CA/token 的替换，不预启第二台或论坛。
- [x] deploy/mc-relay/：新增 frps、Caddy、renew 的 systemd 模板与 timer；所有路径独立，普通服务用户 mc-relay。
- [x] deploy/mc-relay/deploy-cert.sh：按总计划实现仅限 mc-relay lineage 的证书链/SAN/有效期/公私钥核验、受限备份、临时文件、替换后 Caddy 用户 validate、仅新服务 reload/restart、失败恢复。
- [x] deploy/mc-relay/README.md：最小安装/verify/validate/签证/dry-run/正式deploy及恢复检查命令，明确外部前提与人工填值；不写泛用部署框架。
- [x] 本计划 Implementation Notes：记录已执行与未执行，禁止把模板检查当实际部署通过。

**Acceptance Criteria:**
- Given 未确认域名与凭据, when 准备模板, then 不执行任何外部写入，所有输入显式标为待配置。
- Given 本批仅首台, when 检查 Caddy/frpc, then 仅一个可选 pair upstream，无第二台或论坛启用块，内部端口仅回环。
- Given 无效证书、错误 lineage 或密钥不匹配, when 执行证书脚本, then 在修改现有证书/服务之前拒绝。
- Given 部署校验失败, when 脚本恢复, then 恢复上一对 relay 证书且只操作新增服务。
- Given 准备产物通过, when 汇报, then 真实 DNS/证书签发/服务/蜂窝/PONG/续签仍标未验，整批不标完成。

## Implementation Notes

只读预检：Git main 工作树干净且 refresh 成功；完整 HEAD 见 frontmatter。SSH ubuntu@43.133.21.196 BatchMode 与 sudo -n 成功；服务器 x86_64、约51GiB空盘、约4.4GiB可用内存。80/443 docker-proxy；7000/8443/19080/19081/19420未监听。本机 EMPERORCOMPUTER、18080/18081监听。上述仅瞬时检查，不等于端到端认证。
准备阶段当时尚缺四个最终域名确认、DNS与实际绑定安全组的可用访问、ACME联系邮箱。未发现本机tccli或相关环境配置不能证明没有其他访问方式；后续确认见接续记录。

已准备 `deploy/mc-relay/` 十份文件：首台专用 frps/frpc/Caddy 模板、四域清单、四份独立 systemd unit/timer、证书部署脚本及最小 README。pair、端口、最终域名、CA 路径和 token 均需人工填值；没有生产凭据。脚本只接受本套正式 lineage，核验系统信任链、四域 SAN、当前有效期/剩余至少一天和密钥匹配后才替换；受限备份、服务用户 validate 与失败恢复只涉及新增 relay 服务。

自验：WSL Ubuntu `bash -n deploy/mc-relay/deploy-cert.sh` 通过；Python tomllib 在替换占位符后解析两份 TOML 通过。WSL root 临时树 `/tmp/mc-relay-cert-test-rzd3rkvx` 内，以测试 CA/证书和 install/runuser/systemctl 命令桩验证错误 lineage、密钥不匹配在写入前拒绝、validate 失败恢复上一对文件、成功部署/0640 权限/仅新增服务调用，均通过。首次非 root 试跑未覆盖脚本主体，已明确改用隔离 WSL root 后完成上述验证；命令桩不证明真实 mc-relay 账号权限或真实服务可用。`git diff --check` 通过。engineer 独立验证结果见 Auto Run Result。

未执行任何 DNS、安全组或服务器写入、签证、服务启动、临时 frpc、手机蜂窝/PONG、错误 CA 网络拒绝、真实续签或恢复。旧云论坛实际 URL/前后 HTTP 仍未核验。该次准备结束时四个最终域名确认、DNS/实际绑定安全组访问和 ACME 邮箱仍缺；后续域名与邮箱已确认，当前仅待取得登录操作条件，整批仍保持 blocked，不把模板检查当作部署完成。

## Plan Change Log

## Review Triage Log

## Verification

- 用 bash -n 检查 deploy-cert.sh；如可用 WSL/Linux，使用临时文件和受控命令桩验证错误 lineage、证书不匹配及失败恢复；不调用真实生产 systemctl。
- 用 Python tomllib 检查替换示例输入后的 TOML；核对服务路径、单 upstream、端口及无真实凭据。
- git diff --check；engineer 独立复核产物与实际变更范围。
## Auto Run Result — 第一批最终验收完成（2026-10-19）

Status: built。第一批已实施并验收完成；第二、三批未开始。以下历史准备/分阶段记录保留，均不覆盖本段最终状态。

- 已部署：实际Lighthouse `lhins-n5cgxryh`（东京二区，43.133.21.196），四域DNS-only A，独立限zone续签token，新增TCP7000/8443规则，独立frps/Caddy/Certbot/renew.timer。旧论坛HTTP200、容器ID/StartedAt与80/443原进程保持不变。
- frps/frpc均0.71.1，Caddy2.11.7，Certbot/插件均5.8.0；实际文件、校验值、权限、四域证书到期2027-01-16 22:26:42 UTC、timer现场下次时间见下方实施证据。
- 首台default仅注册127.0.0.1:19080，第二台与论坛未启用。三处NATS/bridge/新公网mobile认证PONG、同server_id、错误CA拒绝、renew dry-run及正式deploy后的frpc自动恢复通过；真实短断已记录，不称零扰动。
- 实体手机默认CELLULAR，存在ClashMeta VPN，未改手机网络或VPN。Android现有curl以系统信任根验证8443证书/hostname成功，真实HTTPS请求到origin返回404（不是502）；本项证明TLS及实际请求可达，不冒充手机端NATS认证。手机未执行NATS认证、模型命令或RC地址切换。
- engineer已独立完成server-only、Windows新链路及新增手机网络/TLS/HTTPS证据核对，判定第一批核心要求满足。president仅做一次限范围审查，无实质代码缺陷；唯一收尾项是本次当前状态归一，不增加评审轮次。
- 用户明确“防火墙不用管了”，Windows当前防护状态仅作为本次验证环境事实记录，不再blocking；代理未改防火墙/Defender。历史隔离事件及后续官方包单次正常下载/校验/运行结果保留，不把旧拦截冒充当前阻塞。
- 临时frpc PID33336保留运行供第二批接续，无Windows持久任务。MC/旧CF/RC/WSL和旧论坛未改；无手机新增APP、临时标签/文件或adb forward待清理。本次不推进后续批次；实施代理不提交，最终由主代理按bmad-build-auto提交到本地Git，不push。

最新移动数据确认：engineer已按用户最新要求重新实测，默认MOBILE[NR]、VPN仍在，Android系统验证TLS1.3与域名通过，GET请求到origin返回404、exit0。

## 实施过程摘要（历史阶段，后续结果以上方最终验收为准）

当时：server-only已部署且engineer独立验证通过；第一批端到端仍因Windows frpc被Defender隔离而blocked，等待用户处理决定（此为历史状态，已由上方最终验收覆盖）。下方旧Auto Run Result为准备阶段历史，缺域名/技能/登录不再是当前阻塞。

- 实际目标：腾讯云Lighthouse `lhins-n5cgxryh`（东京二区，43.133.21.196）。仅该实例防火墙新增一条IPv4 TCP7000,8443允许规则，现共5条；原22/80/443/ICMP四条未变。
- Cloudflare四条DNS-only A均已读回正确，原11条DNS保留。独立zone token保存在服务器 `/etc/mc-relay/cloudflare.ini` root0600，凭据未入Git/argv/报告。
- 已安装：`/opt/mc-relay/frps` 0.71.1（实际二进制SHA256 `2322762939e66e66850bbc9103b6d73fec39e83bd8ad6c71ebeeb120d56d6ab3`）；`/opt/mc-relay/caddy` 2.11.7（实际二进制SHA256 `678ade3bfc088749c81a681adc603333ee0bb023b6a6cfe3c0f58bef8ff854e9`）；独立venv Certbot/插件均5.8.0，pip check通过。下载包官方校验见后文。
- 配置：`/etc/mc-relay/frps.toml`（独立随机FRP token、allowPorts仅19080、proxyBindAddr回环）；`/etc/mc-relay/Caddyfile`（仅desktop域→127.0.0.1:19080）；`/etc/mc-relay/cert-domains`四域；`/opt/mc-relay/deploy-cert.sh`；四份 `/etc/systemd/system/mc-relay-*` service/timer。配置/部署证书root:mc-relay0640，专用普通用户mc-relay。
- 正式DNS01签证成功：lineage `/etc/mc-relay/acme/live/mc-relay`，四域SAN吻合，Let's Encrypt YE2，2027-01-16 22:26:42 UTC到期。TLS证书SHA256指纹 `E2:47:7D:D8:F8:A1:47:5E:74:C7:1E:A3:4B:69:D9:AA:F5:DA:01:B4:84:9D:5E:7C:1A:64:FA:1E:17:06:57:25`。系统公共ISRG Root X1单根校验证书链通过，可供后续frpc信任根使用，未导出私钥。
- 真机检查通过：frps verify、mc-relay用户Caddy validate、systemd-analyze verify；mc-relay-frps/caddy和renew.timer均active且enable。仅新7000/8443公网监听、2019回环，首台19080已由临时frpc注册且仅监听127.0.0.1，19081/19420无监听。
- 当前电脑公网核验：四域均只解析43.133.21.196、7000 TCP可连、8443 TLS1.3系统信任与四SAN正确；早期未启动frpc时曾返回502（历史），现新公网8443已通过mobile认证PONG，与本机NATS/bridge的server_id一致。
- 当前正式证书手动deploy成功，Caddy reload和frps重启均实际执行（frps PID53383→54616），证书对受限备份保留。renew.timer下次为2026-10-19 15:22:04 CST（现场读取）。独立renew dry-run真实完成exit0（默认随机等待155.70秒后DNS传播60秒）。随后再次用当前正式证书手动deploy完成exit0，三个新增unit仍active；公网8443指纹再次与正式证书一致，未部署staging证书。
- 旧站对照：同一 https://tingyou.cc/ 前后HTTP200，app完整ID和StartedAt保持本记录原值，80/443仍由原docker-proxy PID持有。日常MC/CF未动，三处旧链路mobile认证PONG基线已通过。

Windows先前阻塞（本次单次重试已解除；以下为历史）：Windows Defender曾隔离官方ZIP，路径 `C:/Users/gladwell/AppData/Local/Temp/mc-relay-download-320d18cd1a8d4094a3feede3682f648f/frp_0.71.1_windows_amd64.zip` 现在Test-Path=false；威胁名Trojan:Win32/Kepavll!rfn、隔离成功。官方来源 `https://github.com/fatedier/frp/releases/download/v0.71.1/frp_0.71.1_windows_amd64.zip`，官方预期SHA256 `b90074b2b6a1d792a7a40f085ac6d276bcab2d1bc60201f8a86ca53ce65ce821`，未当作本机实测校验通过。PATH、已知MC项目/常见工具目录未发现现成frpc，亦无frpc进程；未恢复隔离、添加排除、换构建规避或运行被拦截文件。待用户决定Windows安全中心处理。

当前待独立核对：实体手机移动网络下8443系统信任TLS与真实HTTPS请求（本次已自验通过，VPN条件见后文）。用户已免除Windows防火墙恢复门槛，代理不更改防护。Windows临时frpc verify/错误CA拒绝/仅回环19080注册、三处新链路server_id与认证PONG、正式deploy后的frpc自动恢复现均通过（见后续现场结果）。未改手机地址、未发送模型命令、无Windows持久任务。当前只交付server-only证据，不称第一批整体完成，不推进第二批，不提交Git。
## Auto Run Result — 历史准备阶段

Status: blocked
Blocking condition: 四个最终域名及 ACME 邮箱已由用户确认；用户表示 Cloudflare 与腾讯云均可登录，并计划登录后由代理配置。当前仍待取得登录操作条件：用户规定使用 web-access/SKILL.md 和 scripts/start-browser.ps1，但技能入口与启动脚本未找到，尚未进入平台配置。此前已按总计划第 4.7 节完成独立准备，未执行外部写入。

交付：deploy/mc-relay/ 十文件（frps/frpc/Caddy 示例、四域清单、证书部署脚本、frps/Caddy/renew service 与 renew timer、README）。没有安装、签证、启动、DNS/安全组写入、MC/CF/手机/WSL 修改；未提交或推送 Git。第一批未完成，不推进第二批。

engineer 独立验证通过：bash -n；替换占位符后两份 TOML 解析；单首台 upstream、回环映射和服务路径；隔离测试中错误 lineage、密钥不匹配、缺 SAN 均拒绝且目标 SHA256 不变；validate 失败恢复后旧证书对 SHA256 相同；成功部署文件均0640且只调用新增 relay 服务。systemd-analyze verify 未报语法错误，但因隔离环境没有目标二进制退出1，未计为真实服务通过。git diff --check 通过；未发现需修复问题，不增加多轮审查。

只读旧站容器基线：app ID db3ae44725f66faff858dde1b836b85f85531f892b53573bfcd9657e3bc6d5ec，StartedAt 2026-06-19T22:58:29.673494446Z。尚未取得旧站实际 URL/HTTP 基线；未完成真实 pair/token、server_id 或认证 PONG。真实二进制版本/校验、账号权限、DNS、证书、服务、蜂窝、错误 CA 网络拒绝、续签和恢复均待执行，不能用隔离测试代替。

接续确认：用户已确认采用总计划四个候选域名：`frp-gw.tingyou.cc`、`mc-desktop-test.tingyou.cc`、`mc-laptop-test.tingyou.cc`、`forum-dev-test.tingyou.cc`；ACME 联系邮箱为 `gaopeiyou@foxmail.com`。用户说明 Cloudflare 与腾讯云均可登录，计划由用户登录后代理配置；域名和邮箱不再是阻塞项。

当前浏览器阻塞：用户规定交互访问遵循 `web-access/SKILL.md` 并在本机运行其 `scripts/start-browser.ps1`。`C:/Users/gladwell/.codex/skills/web-access` 目录存在，但 `SKILL.md` 缺失；已使用 rg 搜索 `D:/code/sj`、`.agents` 及 `.codex/skills`，未找到该技能或启动脚本。当前仅记录缺失，不修复全局技能、不替换浏览器启动流程；仍待取得符合该流程的登录操作条件。

取得登录操作条件后，从第一批第1步补齐旧站实际 URL/HTTP、首台身份及认证基线，再按既定顺序配置；不推进第二批。凭据仅以仓库外私有保存位置交接，不写入报告。

## 第一批真实接续进度（2026-10-19）

状态：已按新授权接续实施；历史准备结果保留，不再以旧缺域名/邮箱记录拒绝执行。实际浏览器技能已找到并读取：`D:/code/cx/mc/_internal/.codex-home/skills.legacy-20261008172317085944/web-access/SKILL.md`，使用其 start-browser.ps1 复用专用 headless，proxy3456/CDP9222。

本次只读基线：旧云论坛配置 DISCOURSE_HOSTNAME 为 tingyou.cc；`https://tingyou.cc/` 返回 HTTP200、x-discourse-route=list/latest。当前 app ID 与 StartedAt 对照准备阶段完全一致；服务器 x86_64，根盘约51GiB空闲、约4.3GiB可用内存，80/443仍由原 docker-proxy 占用，7000/8443/2019/19080/19081/19420未监听。

本机 EMPERORCOMPUTER，实际持久身份 pair_id=default（读取配置确认，不由机器名推断）；监听进程为 D:/code/cx/mc/mc.exe 与同包 nats-server。读取实际运行包 history/remote_machine.json 原 token（未输出）；18081 NATS直连、18080 bridge、旧公网 rc.tingyou.cc/nats 均通过 mobile/password 认证 PONG，三处 server_id 一致：NCMBXH66I6OMPDLOBRD3HYTIE73IUG7FZROJFE3I5GSM3D6CKLHQXVFW。

新现场阻塞：复用已给 target 后，Cloudflare 93CA95F1630798F75362F5AA1DAB5B85 实际 DOM 为“正在进行安全验证”；腾讯云 6E68AB054204CD61AC8A145D7BED532B 实际仍为 cloud.tencent.com/login 微信/邮箱登录页。尚未取得可操作控制台内容，已向主代理报告，未自行切换可见模式或关闭实例。此为本次实际登录状态问题，不是缺少部署授权。尚未执行任何外部写入。
独立准备进度：官方 GitHub 当前稳定版为 frp v0.71.1（2026-10-14）、Caddy v2.11.7（2026-10-03）；服务器 x86_64、Windows X64。仅本机私有临时目录 `C:/Users/gladwell/AppData/Local/Temp/mc-relay-download-320d18cd1a8d4094a3feede3682f648f` 下载准备，无服务器写入。frp Linux amd64 SHA256 为 1d9875210972a5d1a62b2dc4c48df45ab0af05d3ca1b5e494a014428259df803；Caddy Linux amd64 SHA256 为 727b91701a392de6ebc5027509f548bf39979e5216340d0faed8fa5e69c84f8b，均与官方 release asset digest 一致，官方校验清单亦核对通过（Caddy 清单采用 SHA512）。服务器 Python3.12.3、venv 模块和 OpenSSL3.0.13 可用，未发现已有 mc-relay 用户/目录/unit。

新增实际阻塞：官方 frp_0.71.1_windows_amd64.zip 下载后被 Windows 防护判为 `Trojan:Win32/Kepavll!rfn` 并隔离（ActionSuccess=true、IsActive=false），Get-FileHash 被拒绝，未完成该包实际校验。官方预期 SHA256 为 b90074b2b6a1d792a7a40f085ac6d276bcab2d1bc60201f8a86ca53ce65ce821，但不能将预期值冒充本机文件校验通过。未恢复隔离、添加排除、禁用防护或执行该包；临时 Windows frpc 尚不能进入实施，已向主代理报告。不能据此直接断言官方包实际恶意或误报。
外部写入前范围记录：用户明确允许本次配置期间专用浏览器保持可见，已取得 Cloudflare 可操作登录态（新 target324E204392FEB62AA8D111C1615BA013）；不再运行 headless 启动脚本或切换模式。将仅在已确认 tingyou.cc zone 新增四个 DNS-only A→43.133.21.196，以及创建只限该 zone DNS Edit/读取的独立 ACME token 并私下保存；原有11条 DNS 保留。腾讯云登录仍待取得，安全组未写入；独立证书准备可先推进，不宣称已对公网开放。
实际 Cloudflare 变更已完成：四域均新增 A→43.133.21.196、proxied=false、TTL自动；读回15条记录（原11+新增4），旧记录未改。新增记录ID依次：frp-gw=9ca38aa132d4488087db190bd776451b，mc-desktop-test=4efc74b82e6f83305bb745cf373ebebf，mc-laptop-test=219c8b42f8dbd1cd63778211c3a7933b，forum-dev-test=7cc0642ebe2077906b0d01a582e68900。独立 token 名称 mc-relay-acme-tingyou-20261019，ID8bdf6796a5cdcfdcdc6f6cf3899f416d，只授权 tingyou.cc zone 的 DNS Write+Zone Read；真实值经内存/SSH stdin直接写 `/etc/mc-relay/cloudflare.ini` root:root0600，不经argv、Git或输出，页面临时变量已清除。专用系统用户 mc-relay 及 /opt、/etc、/var/lib 下对应独立目录已创建，尚未启服务。

腾讯云实际资源定位：服务器官方 metadata 回 ap-tokyo / ap-tokyo-2 / ins-eerymv7q；当前登录主账号1040337910在东京控制台仍显示未选购，已请主代理核对账号归属，不修改其他实例安全组。安全组尚未变更，证书可独立准备。
实际产品核实：控制台已确认同账号目标为 **Lighthouse轻量应用服务器 lhins-n5cgxryh / Ubuntu-k9A5 / 东京二区 / 43.133.21.196**，而非总计划原先称呼的CVM。metadata内部ins-eerymv7q不是控制台实际资源ID。该实例防火墙页面明确“等同安全组，不支持配置安全组”。本批按相同最小授权目标，只在该实例防火墙新增TCP7000/8443，保留现有TCP22/80/443及ICMP规则，不改防火墙模板、其他实例或主机规则。

frps0.71.1与Caddy2.11.7已在服务器复核SHA256后安装到独立/opt/mc-relay，未启动服务。系统venv模块在但ensurepip缺失；apt模拟会牵动5个系统Python包，因此未执行apt安装。仅使用 `venv --without-pip` 及经PyPI官方SHA256核验的pip wheel引导独立环境，Certbot5.8.0、certbot-dns-cloudflare5.8.0安装成功，pip check通过，系统Python未升级。

正式四域SAN证书已由独立DNS01签发成功：issuer Let's Encrypt YE2，有效期2026-10-18 22:26:43 UTC至2027-01-16 22:26:42 UTC，四个SAN均吻合，lineage=/etc/mc-relay/acme/live/mc-relay；私钥未输出。当前尚未启动relay服务，后续仍需脚本安装/validate、续签dry-run和真实公网检查。
server-only最终自验（2026-10-19）：独立renew dry-run exit0；完成后再次正式deploy exit0、仅新Caddy reload与frps restart，公网8443实证仍为上述正式证书指纹；renew.timer下次仍为2026-10-19 15:22:04 CST。受限日志仅服务器 `/var/lib/mc-relay/renew-dry-run-output.log`、`deploy-cert-output.log` 和独立acme-logs。server-only现可交engineer独立核对；第一批仍未完成，Windows临时frpc/错误CA/PONG/真实蜂窝/隧道恢复继续未验，不拿server-only代替端到端。

## Engineer 独立验收 — 真实 server-only

server-only通过，第一批整体blocked。engineer独立核对版本、服务器二进制SHA256与现存官方校验清单；frps/Caddy/renew.timer active+enabled；普通服务用户；frps verify、Caddy validate、systemd verify通过（仅无关旧tat_agent警告）；四域仅解析目标IPv4，公网7000可达、8443 TLS1.3系统信任/四SAN/正式指纹一致；2019仅回环，映射未注册，desktop502符合无frpc状态。真实dry-run成功日志、正式deploy输出及journal reload/restart证据一致；未重新签证、续签或重启。

权限独立核对：配置/部署证书0640，DNS凭据和日志0600，证书备份目录0700。旧站Windows及服务器curl均HTTP200，旧app ID/StartedAt及80/443原docker-proxy PID与基线一致；WSL urllib的CF403不计为旧站故障。DNS-only/防火墙控制台属性沿用director实际读回证据，本次engineer未重开UI。git diff --check通过，只有部署目录和执行记录新增，未提交Git。

剩余阻塞：Defender隔离官方Windows包，等待用户处理决定；未恢复隔离、添加排除或关闭防护。Windows frpc、错误CA拒绝、19080注册、新入口认证PONG、蜂窝与隧道恢复继续未验。专用浏览器按用户明确允许保持可见。未推进第二批。

Windows分支新进展（用户明确要求重试一次）：未更改任何系统防护，原路径官方ZIP本次下载可读，实测SHA256 b90074b2b6a1d792a7a40f085ac6d276bcab2d1bc60201f8a86ca53ce65ce821吻合。只提取frpc.exe到当前用户受限 `%LOCALAPPDATA%/MCRelay`，version0.71.1，实际二进制SHA256 23d11640bee87207c7a99d006b84c554df997feeba7ac7243db20a88a9cb5e01。原Defender拦截是历史，本次正常下载/运行成功，没有恢复隔离、添加排除、改名或换构建。配置从服务器私读独立FRP token，经内存写入受限目录；公共ISRG Root X1通过scp复制，无服务器私钥。正确与错误CA配置均verify通过，错误CA真实启动exit1且TLS证书拒绝，未留下负例进程。现启动正确配置独立临时frpc，无Windows持久任务。
Windows新链路实际自验（2026-10-19）：正确临时frpc PID33336，路径 `%LOCALAPPDATA%/MCRelay/frpc.exe`，隐藏独立进程，无持久任务，当前保留运行供后续接续。配置frpc.toml、relay-ca.pem、负例配置/日志均在同一当前用户+SYSTEM受限目录；仅公共ISRG Root X1，没有复制服务器私钥。真实错误CA启动exit1（证书未知CA拒绝），正确配置注册mc-default→127.0.0.1:19080；19081/19420未启用。

本机NATS18081、bridge18080、新公网 `wss://mc-desktop-test.tingyou.cc:8443/nats` 三处mobile/password认证PONG通过，server_id同为 NCMBXH66I6OMPDLOBRD3HYTIE73IUG7FZROJFE3I5GSM3D6CKLHQXVFW，实际pair_id=default。当前正式证书deploy再执行exit0，Caddy reload、frps PID55738→69792实际重启，frpc PID33336保持不变并自动重连；期间第一次WSS握手失败，后续PONG在deploy开始后3.31秒内恢复，确有短断，未称零扰动。不重复已通过的renew dry-run。

手机条件只读核对：ADB仅发现已授权模拟器，无USB实体手机，因此尚无真实蜂窝证据；未关闭手机WiFi、未改RC地址、未装APP或发送模型命令。主代理已请求用户恢复其临时关闭的Windows防火墙并补手机授权条件，待回答后做正常环境最小复验；代理未更改任何Windows防护。电脑端可验项现交engineer独立核对，整批仍不标完成。

## 最新独立验证 — Windows 临时隧道

电脑端核心已由engineer独立验证通过：官方ZIP及frpc.exe实测SHA256与记录一致、实际配置verify通过；PID33336持续运行且无持久任务；错误CA真实日志拒绝未知签发者，服务器映射仅127.0.0.1:19080；现有只读探针独立复跑18081、18080、新公网8443均mobile认证PONG且server_id一致，旧CF公网也通过。journal确认frps重启后同秒重新注册；3.31秒恢复及一次握手失败沿用director原始实测，未重复制造中断。旧论坛GET200，git diff --check通过。

当前阻塞已变化：官方包本次正常下载/校验/执行成功，隔离不再阻塞当前临时进程。用户曾表示关闭防火墙后授权重试，代理未更改防护。engineer只读核验Domain=True、Private=False、Public=False；AMServiceEnabled=True、AntivirusEnabled=True、RealTimeProtectionEnabled=False。现有电脑端通过仅适用于该状态，不冒充恢复防护后的结果。已请求用户恢复刚关闭的防护后复验，尚待回应。

真实手机蜂窝未验；当前ADB仅模拟器，无实体USB设备。已向用户提供无需USB的手动方式：关闭手机Wi-Fi使用移动数据，打开 https://mc-desktop-test.tingyou.cc:8443/nats 并反馈显示内容/证书警告。未擅自操作手机网络或RC。第一批等待用户恢复防护及蜂窝验证，状态blocked，不推进第二批；临时frpc PID33336保留，旧MC/CF保持运行，无Windows持久任务。

实体手机移动网络最终自验（2026-10-19）：用户明确“手机已经连接，防火墙不用管了”，不再把恢复Windows防火墙作为第一批条件；代理未更改防火墙/Defender。限定授权实体serial93206cc7（模拟器未用于此证据）。只读系统报告Active default network109，为MOBILE[NR]/CELLULAR、VALIDATED；同时存在ClashMeta VPN网络105，未关闭VPN，不将本结果宣称为无VPN的运营商直连。

实体无Chrome包/devtools socket，但系统已有 `/system/bin/curl` 8.12.0-DEV，无须编译、安装APP或新增依赖。用现有curl（不加-k、不改信任根、token未用于本次HTTP请求）请求 `https://mc-desktop-test.tingyou.cc:8443/`：exit0，TLS1.3，系统CApath=/system/etc/security/cacerts，ssl_verify_result=0，hostname与SAN精确匹配，issuer Let's Encrypt YE2、期限与正式证书一致；实际socket目标43.133.21.196:8443。HEAD / 返回404，响应Via:1.1 Caddy与实际MC origin BaseHTTP/0.6 Python/3.11.5，证明真实TLS与origin请求可达；根路径404不是业务验收成功或认证PONG，未据此冒称手机业务通过。电脑三处mobile认证PONG已有独立证据；本次手机未做NATS认证，不发送模型命令、不改RC地址。

本次手机未创建临时文件、APP、CDP标签或adb forward，故无这些资源需清理；原手机浏览器标签、网络设置和专用桌面可见浏览器均保留。现第一批必要链路自验已完成，新增手机证据交engineer独立核对后由主代理汇总；临时frpc PID33336继续运行且无持久任务。不推进第二批，不提交Git。