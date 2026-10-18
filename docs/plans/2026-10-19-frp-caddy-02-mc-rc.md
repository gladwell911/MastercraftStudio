# 第二批：两台 MC 与手机 RC 切换

日期：2026-10-19。状态：仅计划，未执行；本文不代表代码、运行包或手机已完成切换。
本批须由用户后续明确启动；director 实施，engineer 独立验证，首台通过后再执行第二台，完成本批后停止。

## 必读与依赖

- [总计划第 1–3 节：边界、身份和路由](2026-10-19-frp-caddy-migration.md#1-结论与边界)。
- [第 6 节：frpc 与 Windows 持久任务](2026-10-19-frp-caddy-migration.md#6-阶段-c第一台电脑-frpc-与外部链路约-3060-分钟)。
- [第 7 节：MC external mode 最小改动表](2026-10-19-frp-caddy-migration.md#7-阶段-dmc-最小-external-mode-改动约-153-小时)。
- [第 8 节：RC 编译与手机验收](2026-10-19-frp-caddy-migration.md#8-阶段-e手机-rc-单机切换约-3060-分钟)。
- [第 10 节：第二台与恢复验证](2026-10-19-frp-caddy-migration.md#10-阶段-g第二台-mc恢复验证与完成标准约-4590-分钟)，本批只执行 MC/RC 项。
- [第一批结果](2026-10-19-frp-caddy-01-server.md)须已具备独立 relay、证书和端到端证据；若仅 server-only，先补临时 frpc 认证探针。
- 使用第一批确认的实际域名；总计划示例域名仍是候选，不把占位符写入真实配置。
- 读取 MC、D:/code/sj/rc 各自项目指令与交接，取得两台电脑可操作条件及正式私有 RC 参数。

## 目标与范围

让 RC 经新 :8443/nats 入口连接 default 台式机和 laptop 笔记本，并保留各自历史、token 与来源归属。
包含 MC 小范围代码改动及验证、构建部署、逐台 frpc 持久恢复、RC 保数据更新与两台业务验收。
本批不改 WSL/云论坛、论坛 Android、forum-wsl 代理或论坛 canonical；保留旧 CF 入口。
服务器仍为 ubuntu@43.133.21.196，仅使用第一批独立目录、7000/8443 和回环 19080/19081。
不动 /var/discourse、app 容器及其 80/443；证书、token、私有日志不上 Git。

## 按序执行

1. 核对第一批最终域名、版本、首台 pair、临时 frpc 状态及未验项，复用有效服务器证据。
   只做必要的当前端点与身份检查；NATS 端口按运行态读取，不能固定为旧文档 18081。
2. 按总计划第 7 节逐项实现 external|cloudflared 小开关，默认 cloudflared；未知值报配置错误。
   external 各路径不管理 CF，但保留 origin bridge、本地/公网真实认证、readiness 与恢复探测。
   修正写死 443 的端口解析；文件发布 URL 优先用当前配置，完整保留 :8443，防止旧 cloudflared_url 覆盖。
   覆盖文件发送门槛、健康循环、状态文案和清理路径，不扩展为通用隧道框架。
3. 运行改动链的现有定向测试：默认模式、external 不触碰 CF、失败不假 ready、URL/文件端口及双 pair 隔离。
   engineer 独立核对实际改动及证据，必要时补相关检查；不恢复用户已停止的整轮桌面验收。
4. 隔离构建使用第 7 节 -BuildOnly/-DistPath/-WorkPath 方式；检查包与源码版本及数据路径。
   正式替换前先完成构建验证；日常 MC 由用户退出，替换使用 -BuildOnly，由用户手动启动。
   保留原 pair/token/history，不克隆另一机器数据，不建立整个旧程序包备份，不改 OneDrive 数据。
5. 首台写入经确认的新发布 URL 与 external 启动环境，并核验 GUI 新进程实际继承。
   先核对并停止本批接管的临时独立 frpc，避免同 proxy/映射端口双启动；按第 6 节注册当前用户隐藏的登录触发任务：失败重启、禁止重复、无时长限制、电池允许运行。
   选择并实测 S4U 或安全录入凭据的非交互运行方式；只结束/启动新任务验恢复，不终止 MC。
   保留 TLS serverName/CA 校验及独立 FRP token；frpc 登录自启不代表 MC GUI 或 WSL 无人登录自启。
6. 首台用现有应用 token 以 mobile/password 做 NATS 直连、18080、新公网三处 server_id/PONG 对比。
   再验证 hello/readiness/list 与实际 pair；认证不配套先配套升级，不能换 token 或放宽认证掩盖。
7. 从正式私有 JSON 复制迁移配置，只更新首台 endpoint，保留两来源 token、另一来源 endpoint 与 backup 语义。
   default 的 REMOTE_CONTROL_ENDPOINT/REMOTE_CONTROL_TOKEN 与 laptop 的 REMOTE_CONTROL_LAPTOP_ENDPOINT/REMOTE_CONTROL_LAPTOP_TOKEN 必须齐全。
   endpoint 保留完整 :8443/nats；StartupOverride 优先于持久设置，不以“手机设置已改”代替编译参数核对。
   按第 8 节在 D:/code/sj/rc 用 install_physical_arm64.ps1 与私有 DartDefineFile 保数据安装，核对 APK hash。
8. 首台在蜂窝完成列表、目标聊天、一次真模型回答和该机授权文件下载，核对内容与来源。
   app 重开仍使用新入口，再以 Wi-Fi/蜂窝切换验证恢复；网络对照不重复模型请求。
   停/启首台新 frpc，核对断线恢复及另一来源不受影响；结果未知的模型命令不重发。
9. 首台所有必要项通过后，复用同版实现部署第二台，使用另一独立 pair/token/域名及回环映射。
   仅增加第二台 Caddy block，validate 后 reload；不修改 forum 配置或重做服务器安装。
   第二台重复第 4–8 步的必要部署与验收，RC 配置同时保留首台最终 endpoint；每台各一次真模型与授权文件。
10. 两台同时在线时分别打开列表和文件，确认不串源；仅断开其中一台 frpc，另一台保持可用，恢复不重复命令。
    engineer 独立核对两台、两份私有构建配置的生效结果；只检查受影响范围。

## 验收与停手

- 两台各自三处 server_id 相同、mobile PONG、hello/readiness/list 通过；来源与原 token 一一对应。
- 两台各一次真实模型回答与对应文件授权下载通过；重开、换网、单机断线恢复及持久任务恢复有记录。
- external 无 CF 管理动作、URL 保留 :8443、默认模式回归通过；运行包与 RC APK 对应实际验证版本。
- 第一批续签演练仅在相关配置、版本、环境未变时复用；若有变化，只补受影响证书/恢复检查。
- 身份串源、认证失败、readiness/文件失败或恢复失败时停在当前机器，不推进第二台或第三批。
- 局部回滚恢复该机旧发布 URL/cloudflared 模式，由用户重启；RC 保数据装回原正式参数对应包。
- 只停本次 frpc/任务并撤销该机新映射，保留另一台已通过链路和旧 CF；不清数据库或重启共享服务。

## 交付下一批

| 非敏感交接项 | 记录要求 |
|---|---|
| 生效配置 | 两台 pair/最终 URL/映射、MC 与 RC 版本或 hash、external 生效方式、任务名 |
| 验收结果 | 逐机 server_id/PONG、真实模型与文件结果、恢复耗时、双来源隔离、未验项 |
| 稳定记录 | 安排 24–48 小时日常断线/睡眠唤醒观察，注明实际覆盖时间；不把等待时长作为交付门槛 |
| 延续信息 | 第一批证书演练的版本/配置适用性，论坛所在电脑和现有 relay 位置；不抄 token |

[下一批：WSL 论坛与论坛 Android](2026-10-19-frp-caddy-03-wsl-forum.md)。MC 稳定记录作为下一批基线，此处停止。

> 启动口令：执行 docs/plans/2026-10-19-frp-caddy-02-mc-rc.md，仅第二批；由 director 实施、engineer 独立验证，首台通过再做第二台，完成后停止，不推进第三批。
