# 第三批：WSL 论坛与论坛 Android 接入

日期：2026-10-19。状态：仅计划，未执行；本文不代表论坛地址或客户端已经切换。
本批须由用户后续明确启动；director 实施，engineer 独立验证，完成本批后停止。

## 必读与依赖

- [总计划第 1–3 节：共同边界与论坛路由](2026-10-19-frp-caddy-migration.md#1-结论与边界)。
- [第 4 节：WSL origin 和旧站检查](2026-10-19-frp-caddy-migration.md#4-阶段-a前置检查和旧站基线约-2040-分钟)。
- [第 5.4 节：Caddy forum block](2026-10-19-frp-caddy-migration.md#54-caddy-配置与服务)。
- [第 9 节：论坛代理、canonical 与 Android 完整技术步骤](2026-10-19-frp-caddy-migration.md#9-阶段-fwsl-论坛与真正-android-客户端约-13-小时)。
- [第 10 节：最终证据与观察边界](2026-10-19-frp-caddy-migration.md#10-阶段-g第二台-mc恢复验证与完成标准约-4590-分钟)，本批只补论坛项及必要存活检查。
- [第一批](2026-10-19-frp-caddy-01-server.md)的 relay/证书须通过，并具备[第二批](2026-10-19-frp-caddy-02-mc-rc.md)已验证的 MC 稳定记录；推荐严格按 1→2→3 执行。
- 使用第一批最终论坛域名；总计划里的 forum-dev-test.tingyou.cc 仍是候选，不能据此认定已确认。
- 读取 WSL 开发实例与 D:/code/tingyouAndroid 的项目指令、现有启动/签名/构建说明。

## 目标与范围

让真正的论坛 Android 客户端经独立 :8443 入口访问本机 WSL Discourse，并验证登录、帖子、上传与长轮询。
仅增加 forum-wsl → WSL 4200 代理，调整 WSL 开发实例 canonical 与论坛 Android 地址。
本批不改 RC Flutter 工程，不重跑 RC 真模型请求，不改变两台 MC endpoint、凭据或 external 实现。
云服务器 ubuntu@43.133.21.196 的 /var/discourse、旧 app 容器和 80/443 均不动，不碰旧云论坛数据库或证书。
仍使用独立 /opt/mc-relay、/etc/mc-relay、/var/lib/mc-relay；公网仅 7000/8443，19420 只监听回环。
保留旧 CF 和 site.tingyou.cc DNS。旧入口保留不表示两个 canonical 地址可同时保持登录。

## 按序执行

1. 核对前两批版本、配置与通过项，记录已有两台 MC 的只读存活结果及旧云论坛状态。
   仅检查必要 PONG/readiness，不发模型请求；证书续签演练证据只在相关配置、版本和环境未变时复用。
2. 在论坛所在 Windows 请求 127.0.0.1:4200 和现有 LAN 转发，核对返回内容确实来自目标 WSL。
   localhost 不通时复用已证实的现有 LAN4200 转发，不擅自变更 WSL 网络模式。
   查当前 WSL IP 与监听；如需要，只更新既有那条 portproxy 的 connectaddress，保留 listenaddress/port。
   必要的动态 IP 更新并入现有 start-dev 入口或短脚本，不批量 reset，不新增通用路由服务。
3. 在这台电脑的既有 frpc 中仅增加总计划第 9 节 forum-wsl block，remotePort=19420。
   localIP/localPort 使用第 2 步已证实的目标；保持原 MC block、token、TLS 和映射不变。
   verify 后应用配置；如所选版本必须重启该 frpc，先记录同机 MC 的短暂影响，再验证自动恢复，不退出 MC。
4. 按第 5.4 节只加入最终论坛域名的 Caddy block，upstream=127.0.0.1:19420。
   保留 Host、正确代理协议和 X-Forwarded-Port 8443；不重写路径，不手造 Upgrade，不开放内部端口。
   validate 后 reload；沿用第一批证书，先核对 SAN 覆盖最终域名，未覆盖则只补该证书的必要处理。
5. 向新公网入口发送唯一无秘密 marker，确认落在本机 WSL development.log 或 tmp/start-dev/rails.log。
   核对旧云 app 无对应请求，检查 CSS/JS、/session/csrf 透传；首页 200 不能证明路由归属或登录可用。
6. 读取 WSL 实际开发启动参数与有效 hostname/port/https，记录原值并确认连接的是开发数据库。
   仅在该实例设置新 hostname、适用站点 port=8443，按实际请求决定 force_https；hostname 不带端口。
   不改 upstream 3000/Ember 4200 来模拟公网端口，不跑迁移、不批量 rebake、不复制数据库。
   EnforceHostname 会规范 Host；须以实际跳转与绝对 URL 结果验证，不能只加代理头后宣称通过。
   如需重启，仅操作对应开发 Rails/Ember 进程；不重启整个 WSL、容器或共享服务。
7. 在 D:/code/tingyouAndroid/js/single_site_config.js 修改 SINGLE_SITE_URL 为最终 https://<域名>:8443。
   使用该 React Native 项目的既有签名和安装方式，优先测试包，保留正式配置与本地数据；不套用 RC Flutter 脚本。
   新 host 可能要求重新登录或授权用户 API key；不把旧站授权静默移交新 host。
8. 在实际论坛 Android 验证登录/退出、Cookie 持续有效、读取并发送一条测试帖子或回复、上传并打开小文件、app 重开。
   核对跳转、canonical、头像/图片/上传绝对 URL 保留正确 https、域名与 :8443，无 mixed content。
   观察实际 MessageBus 长轮询完成并继续下一轮；只有客户端实际使用 SSE/WS 时才验对应协议。
9. 若 CSRF/Origin 异常，先核对 Host、协议、端口和 Ember→Rails 头传递；不禁 CSRF、不开泛 CORS/全域 Cookie。
   上传或长轮询失败仅调整已证实的限制；不能临时夺取云端旧论坛 443 解决非标准端口问题。
10. engineer 独立核对 marker、论坛业务和局部变更，复核旧站与既有 MC 的无破坏性存活结果。
    不重复前两批真模型业务或全系统回归；只有本批确实影响的配置变化才补相应验证。

## 验收与停手

- 新公网 marker 确实落 WSL，旧云论坛保持原路由与运行状态；内部 19420 仅回环。
- 论坛 Android 登录、Cookie、帖子/回复、上传、重开及实际长轮询通过，绝对 URL 正确。
- 保留旧入口的事实与旧 canonical 是否仍可登录分开记录；不得承诺同时有效。
- 动态 WSL IP 路线有记录；不为验证强行 wsl --shutdown，用户下次正常重启后再补 origin→公网 marker。
- 尚未经历正常 WSL 重启时，如实标“重启后恢复未验”；不以当前在线替代恢复结论。
- canonical、CSRF、登录、上传或长轮询失败即停；局部配置不能支持 :8443 时恢复并另行决策入口资源。
- 回滚仅恢复 WSL 原 hostname/port/https 与论坛 Android 旧 URL，撤销 forum-wsl 和 Caddy forum block。
- 不清论坛数据，不撤销两台 MC 配置，不重启云 app 或整个 WSL，不修改 site.tingyou.cc DNS。

## 交付与后续观察

| 非敏感交接项 | 记录要求 |
|---|---|
| 生效路由 | 最终论坛 URL、实际 Windows→WSL origin、19420 映射、canonical 设置及客户端版本 |
| 验收结果 | marker 所在实例与日志位置、登录/帖子/上传/长轮询结果、旧站与 MC 存活结果 |
| 恢复与观察 | WSL 正常重启后复验是否完成；实际 24–48 小时日常观察覆盖及未验项 |
| 配置边界 | 与前两批相比的变更、可复用证据版本；私钥/token/API key/私有日志不入 Git |

按总计划第 10 节汇总三批的一页结果表，区分当前通过、历史证据与待验项；不额外创建流程模板。
完成后停止。收掉旧 CF/旧入口须用户另行决定并先核验通道归属，不能作为本批收尾自动执行。

> 启动口令：执行 docs/plans/2026-10-19-frp-caddy-03-wsl-forum.md，仅第三批；由 director 实施、engineer 独立验证，只接入 WSL 论坛与论坛 Android，完成后停止。
