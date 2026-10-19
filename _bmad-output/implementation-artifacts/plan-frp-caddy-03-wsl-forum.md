---
title: '第三批：WSL 论坛与 Android 接入 frp/Caddy'
type: 'feature'
ticket: ''
created: '2026-10-19'
status: 'built'
route: 'full'
route_source: 'auto'
review: ''
review_source: ''
lenses_ran: []
review_loop_iteration: 0
followup_review_recommended: false
context:
  - 'D:/code/sj/mc/docs/plans/2026-10-19-frp-caddy-03-wsl-forum.md'
  - 'D:/code/sj/mc/docs/plans/2026-10-19-frp-caddy-migration.md'
warnings: [oversized]
deferred: []
baseline_revision: '50adade130d7f8637efb3315d4dc05aeb0de7588'
---

> 最新结论（2026-10-19）：用户最终只验收首页，已通过，status=built；登录凭据不再阻塞。下文认证验收和blocked记录是范围收窄前的历史，最新范围见文末Scope Override。后续台式机正式切换与连接问题以docs/handoff.md为准。

<intent-contract>

## Intent

**Problem:** 论坛 Android 仍访问旧入口，需经独立 8443 入口访问本机 WSL Discourse，并以实际登录、发帖、上传和长轮询证明可用。

**Approach:** 沿用已部署独立 relay、证书和 Windows frpc，仅增加 forum-wsl→4200→19420 及 Caddy forum block；局部调整开发实例 canonical 和 Android SINGLE_SITE_URL。完成第三批即停止。

## Boundaries & Constraints

**Always:** director 实施、engineer 独立验证。最终域名使用第一批已创建且证书覆盖的 forum-dev-test.tingyou.cc。记录原配置再改；保持现有 MC、RC、CF 和旧站运行态。历史证据仅在版本/配置/环境未变化时复用。第一批正式四SAN/续签已通过；第二批仅独立台式机和模拟器范围 built，不声称第二台或实体已完成，只核必要现存实例存活。

**Never:** 改云 /var/discourse、app、80/443、旧站 DNS/数据/证书；改 MC endpoint/token、RC Flutter；关闭日常 MC/共享浏览器；迁移/复制数据库、批量 rebake、wsl --shutdown、放宽 CSRF/CORS/Cookie。私钥/token/账号/私有日志/APK 不入 Git。未知结果的写入不自动重发。

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| 公网路由 | 新入口唯一无秘密 marker | 本机 WSL 日志有 marker，旧云 app 无对应请求 | 不凭 200 判断归属 |
| origin 不通 | localhost4200 不可达 | 复用已证实 LAN4200；必要时仅更新原 portproxy connectaddress | 不改 WSL 网络模式或 reset |
| canonical | https 新域:8443 | 跳转、资源、上传绝对 URL 保留 https/域名/8443 | 失败恢复原 hostname/port/https 与 Android URL，撤论坛代理 |
| Android 会话 | 新 host、现有安装数据 | 实际正常登录/退出、Cookie 持续、重开仍可用 | 不迁移旧 host 授权，不绕过认证；无可用账号/设备明确报告阻塞 |
| 长请求 | 实际 MessageBus 长轮询 | 完成并继续下一轮 | 仅调整有证据的限制；实际使用 SSE/WS 才验对应协议 |

</intent-contract>

## Code Map

- `D:/code/sj/mc/docs/plans/2026-10-19-frp-caddy-migration.md` §5.4/9：唯一技术配置依据；第三批文件限定操作顺序及回滚。
- `D:/code/sj/mc/_bmad-output/implementation-artifacts/plan-frp-caddy-01-server.md`：frp0.71.1、Caddy2.11.7、四域SAN、独立目录与续签证据；服务器 ubuntu@43.133.21.196，配置 `/etc/mc-relay`，服务 mc-relay-frps/caddy。
- `D:/code/sj/mc/_bmad-output/implementation-artifacts/plan-frp-caddy-02-mc-rc.md`：当前新 desktop-test 指独立 origin28080，不是 daily18080；现存PID只是历史快照，实施前重读。
- `%LOCALAPPDATA%/MCRelay/frpc.toml`：现有受限客户端配置；只增 forum-wsl，保留 MC block/CA/TLS/token；不输出全文。
- `D:/code/sj/mc/deploy/mc-relay/Caddyfile.example`、`frpc.toml.example`：沿用结构；运行 Caddyfile 只增论坛 block。
- WSL Ubuntu `/home/gladwell/discourse/AGENTS.md`、`docs/handoff.md`、`start-dev.sh`、`config/database.yml`、`lib/middleware/enforce_hostname.rb`：先读取现有实例/启动参数；hostname 不带端口，站点 port8443；保留 Rails3000/Ember4200。日志 `log/development.log` 或 `tmp/start-dev/rails.log`。
- `D:/code/tingyouAndroid/AGENTS.md`、`docs/handoff.md`、`docs/experience.md`、`docs/reflection.md`、`README.md`：React Native 既有构建/签名/保数据安装；现有 OTA 模拟器状态须先核对，不能把旧 bundle 当当前配置。
- `D:/code/tingyouAndroid/js/single_site_config.js`：当前 SINGLE_SITE_URL=https://site.tingyou.cc，仅改为 https://forum-dev-test.tingyou.cc:8443。该仓反思确认 site.tingyou.cc 现有服务端就是本机 WSL；保留旧入口不保证旧 canonical 同时登录。

## Tasks & Acceptance

**Execution:**
- [x] 本记录及第一/二批记录 -- 只读复核当前配置/证书SAN、MC必要存活、旧云app/80/443基线 -- 不重新跑模型或双机部署。
- [x] WSL `start-dev.sh` 和原 Windows portproxy -- 证明 Windows origin4200 到目标实例，必要时仅更新旧转发地址并并入原启动入口 -- 保持网络结构。
- [x] `%LOCALAPPDATA%/MCRelay/frpc.toml` -- verify 后加入 forum-wsl remotePort19420，必要时仅重启该 frpc 并记录同机MC恢复 -- 保留原代理。
- [x] 服务器 `/etc/mc-relay/Caddyfile` -- 新域反代127.0.0.1:19420，保持 Host/协议、X-Forwarded-Port8443，不改路径；validate 后 reload -- 内部19420仅回环。
- [x] WSL 开发配置及 Android `js/single_site_config.js` -- 记录并局部设置 canonical、更新 URL，按现有签名构建并保数据安装测试包 -- 先核开发数据库和实际运行bundle。
- [ ] 本记录 -- director 执行矩阵中的真实请求与 Android 业务检查，engineer 独立核实 marker、设备/包证据、业务及局部差异 -- 不以内部代理代替 app 验收。

**Acceptance Criteria:**
- Given 新公网入口，when 发送唯一 marker 并打开 CSS/JS/CSRF，then marker 落本机 WSL 且路径正确透传，旧云实例保持原身份/启动时间/80/443。
- Given 当前实际论坛 Android 测试包，when 登录退出、再次登录、读写一条测试帖/回复、上传打开小文件并重开 app，then 会话有效且 URL/canonical 正确，无 mixed content，实际长轮询进入下一轮。
- Given 当前 MC 与旧入口基线，when 论坛配置应用，then 必要 MC PONG/readiness 保持或短断后恢复，原凭据/endpoint/CF 不变。
- Given 尚未自然重启 WSL 且未经过24–48小时，when 交付，then 明确标注恢复和长期观察未验，不人为重启凑证据。

## Implementation Notes
### Director implementation — 2026-10-19

当前为局部完成，尚未达到整批 Android 认证业务验收；无可用测试凭据，未尝试密码猜测、修改现有账号或创建管理员。保留已通过的新入口配置，缺凭据不等于已证实 canonical/CSRF 不支持 8443；尚无触发失败回滚的业务结果。

- Windows 127.0.0.1:4200 与既有 LAN4200 均 HTTP200，WSL172.24.40.23/Ember1503。现有 portproxy connectaddress 正确，无需修改；现有 start-dev 已具备动态地址转发入口，本批未改启动脚本或网络模式，正常 WSL 重启后恢复仍未验。
- 原 frpc 配置受限备份于 MCRelay/frpc-pre-forum.toml，仅追加 forum-wsl → 127.0.0.1:4200 / remote19420；原配置完整前缀逐字相同。frpc verify 成功后仅替换本次临时 frpc33288，隐藏新PID28916；无永久计划任务。
- 服务器实际 allowPorts 原仅19080，与总计划含19420的模板不同。必要配套仅补19420，不启19081；原配置 /etc/mc-relay/frps-pre-forum.toml，frps verify 后仅重启 mc-relay-frps（69792→160411）。19420和19080均仅127.0.0.1；原token/TLS及MC映射保留。
- Caddy 仅追加新论坛域 → 127.0.0.1:19420，X-Forwarded-Port8443，Host/路径沿用默认透传；原内容完整前缀不变，受限备份 /etc/mc-relay/Caddyfile-pre-forum。服务用户 validate 成功后 reload；沿用已实核覆盖四域的正式SAN，不重跑签证/续签。
- WSL 开发库实核 Rails.env=development、数据库 discourse_development，原host=localhost、force_hostname空、port空、force_https=false。原.env受限保存 /tmp/forum-relay-original.env；原目标进程完整环境仅在容器 /tmp/forum-relay-process-original.json（0600）。.env仅加 DISCOURSE_HOSTNAME=forum-dev-test.tingyou.cc 和 RAILS_DEVELOPMENT_HOSTS=forum-dev-test.tingyou.cc；SiteSetting.port=8443、force_https=true。首次公网403为精确HostAuthorization拒绝，添加精确新域后解决；未放宽CSRF/CORS/Cookie。
- 仅原开发Pitchfork supervisor4457优雅退出并以原argv/env叠加上述两键启动为35438；其管理的Rails/Sidekiq同步加载新host。容器/PostgreSQL/Redis/Ember及3000/4200未重启或改端口。没有迁移、rebake或数据库复制。
- Android 唯一源码变更 js/single_site_config.js：SINGLE_SITE_URL=https://forum-dev-test.tingyou.cc:8443。既有普通 release、默认OTA baseURL=null；Gradle --offline assembleRelease 成功（1m39s）。同签名覆盖安装到已授权 Detox_API_35/emulator-5570，未卸载或清数据。

### Director verification and remaining acceptance

- 唯一marker `forum-frp-20261019-0523` 同时出现在 WSL log/development.log 与 tmp/start-dev/rails.log；旧云app最近stdout、/var/log/nginx/access.log、/shared/log/rails/production.log均无对应marker。
- 公网首页200、精确canonical=https://forum-dev-test.tingyou.cc:8443/、CSRF200；实际CSS200/8503B、JS200/46906B。当前生成的绝对canonical/base URL保留https/新域/8443。登录、头像/上传生成URL与认证跳转未验，不由首页替代。
- MessageBus真实公网协议：首次同步200/0.309s，随后长轮询200/25.292s并正常结束，Allow-Origin精确新origin。证据明确标 direct-protocol-not-app；RN原生论坛实际请求中未出现MessageBus，不能冒称Android实际长轮询验收完成，也未凭空安排WS/SSE验证。
- Android旧OTA基包SHA256=9d5f22c0e315f13014c88f80053984f3a81fe5a2e83beb6bf0d9de8ec746cc55。新APK与设备回拉SHA256均17c99ee78560d62b1515989a0e1066e18a732805b6505071f6e7747c90bd0f92，证书均5c7d4343187c3f91e1a7fb46b4c8029502c219c54de64fcda5e022d2e08ab9bd；嵌入新URL=true、旧URL=false。旧OTA目录没有可执行bundle子目录；实际应用PID12388建立到43.133.21.196:8443的TCP连接，未误把旧bundle/旧CF入口当本轮生效。
- 实际App游客首页列表、打开现有帖子773读取楼层1272/1397、Mine游客登录页、force-stop后重开首页均通过。当前无有效账号会话；既有上轮remediation私有账号文件已清空，库中仅一个更早的active QA账号但未取得凭据。登录/退出/再次登录、会话保持、写测试帖/回复、上传打开小文件及认证后重开均阻塞，未冒称业务通过。engineer独立验证待主代理接续。
- 变更后必要MC只读认证PONG：daily直连ready；独立直连18082、bridge28080、公网8443均ready且同server_id NCPTWNGHDUXE5VYM7YST6O72KE7KTIYYWIXTEAASZPP4LPXEJX6YVMQC，与daily不同。重启relay后首次复验已恢复，没有精确测量短断时长；未新增模型请求。dailyMC3820/隔离MC36980/Cloudflared11096保持原进程及配置。
- 旧云 https://tingyou.cc/ 前后200，app ID db3ae44725f66faff858dde1b836b85f85531f892b53573bfcd9657e3bc6d5ec、StartedAt2026-06-19T22:58:29.673494446Z和80/443原docker-proxy PID保持不变。site.tingyou.cc DNS/旧CF未改；旧开发canonical不保证同时登录。
- 两受影响Git仓库 git diff --check通过；Android仅一行URL，无新增复述实现的单测。未提交/push。自然WSL重启后origin→公网marker、24–48小时观察仍未验。

非敏感及私有运行证据均保存在仓库外 `D:/code/sj/_bmad-output/implementation-artifacts/frp-caddy-03/`：origin-check.json、poll-check.json、android-current.json、runtime-check.json、android-guest-start.xml、android-topic-read.xml、android-mine.xml、android-reopened.xml、mc-final.json、server-final.txt、android-build.log。其中原始页面/CSRF响应、APK和设备XML仅作私有证据，不入Git。

回滚限定本批：恢复上述原.env及SiteSetting.port空/force_https=false，按原目标进程环境重启开发Rails；Android URL恢复site.tingyou.cc并用同签名原包保数据覆盖；恢复受限原frpc配置、原Caddyfile和仅19080的frps allowPorts，分别verify/validate后仅应用新relay服务。不得退出日常MC或修改旧云app、80/443、DNS和数据库内容。

## Plan Change Log

## Review Triage Log

## Verification

**Commands:**
- `frpc.exe verify -c <private-config>`、服务器 `caddy validate --config /etc/mc-relay/Caddyfile` -- 均成功再应用。
- `git diff --check`（受影响仓库）-- 无空白错误；URL单行不新增复述实现的单测。
- 沿用 Android 既有 Gradle release/测试构建及 `adb -s <实际设备> install -r <apk>`；安装后回拉hash并验证内嵌 URL/当前 bundle，禁止卸载清数据。

**Manual checks (if no CLI):**
- 实际 Android 完整业务及长轮询、marker归属、旧云与现存MC只读存活；秘密留仓库外证据目录。
- 若 canonical/CSRF/登录/上传/轮询不能局部支持8443，恢复本批原值并停止，不夺取旧443。正常WSL重启后的复验和长期观察单列待办。


## Auto Run Result

Status: blocked
Blocking condition: 缺少现有 WSL 开发论坛可用测试账号凭据，无法完成真实 Android 认证业务验收。

第三批接入配置已应用，Android 同签名 release 已保数据安装至现有模拟器；director 实施、engineer 独立验证均已完成可执行范围。公网 marker 归属 WSL、canonical/CSRF/静态资源、当前 APK hash/8443 连接、游客读帖与重开通过。旧云同路径 HTTP200、容器身份/启动时间及现有 MC/CF 保持，未新增模型请求。

engineer 独立实时复核：新入口与 CSRF200、正确 canonical、两份本地日志 marker、frpc/Caddy 旧前缀逐字保留、设备 APK hash 与当前 PID、旧云服务器同路径200及容器身份不变；游客 XML 和既有 PONG/readiness 证据有效。Windows urllib 访问旧云403与服务器 curl200存在客户端访问路径差异，未据此判断旧站故障。

未验：登录/退出/再次登录、认证会话持续、发帖回复、上传打开文件、认证后重开与实际 App MessageBus。25秒协议轮询只证明协议可达。正常 WSL 重启恢复、实体手机与24–48小时观察仍待验。未因无凭据创建账号或修改密码。

保留已通过的局部配置；没有实际业务失败触发回滚。Android 仅修改 js/single_site_config.js 一行，两个仓库 git diff --check 通过。未提交或推送；工作流在实施验证阻塞处停止，未进入额外审查/完成提交。后续提供现有私有凭据文件路径即可接续认证验收。仓库外证据目录：D:/code/sj/_bmad-output/implementation-artifacts/frp-caddy-03/。
## Scope Override — 用户最终验收范围

用户明确：不用登录论坛，只需能够打开首页。此要求覆盖前述认证业务验收条件及缺凭据阻塞；不再要求登录、会话、发帖、上传或 App MessageBus，亦不为这些项目继续索取账号。

最终状态：built。新公网入口首页已再次实时检查 HTTP200、HTML 内容及正确新域 canonical。沿用本次相同 APK/配置下 director 与 engineer 已核实的模拟器首页列表、游客读帖、冷重开和设备包 hash 证据，满足本次首页验收。旧云与 MC 的有效存活结果仍保留。WSL 正常重启恢复、实体手机和长期观察未验，不影响用户收窄后的首页验收。

本次仅更新结果记录，未重新构建、安装或修改运行配置。此前 blocked 为用户收窄验收前的历史状态。
