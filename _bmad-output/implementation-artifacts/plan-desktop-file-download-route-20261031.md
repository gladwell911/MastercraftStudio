---
title: 台式机 default 文件下载公网路由修复
created: 2026-10-31
status: superseded
scope: desktop-default-only
---

> 2026-11-03：此为10月31日历史计划。后续采用启动迁移修复（ea241a5、18221e2），11月2日实体97MB完整下载通过。不要再次执行A手改配置；B健康检查拆分未实施。当前状态和最后补丁部署边界见[交接](../../docs/handoff.md)。

## 目标与边界

修复台式机 MC 准备 APK 时返回 public_file_route_unavailable。只处理 default；不改 laptop、论坛、ControlTower、OneDrive、FRPC/Caddy 等已工作服务。不在本轮实施、打包或启动。日常 MC 退出/重启只能由用户本人执行。

已证实：运行包 D:/code/cx/mc/mc.exe PID7480 自10月30日19:08运行；机器配置仍旧 rc 域。新台式机公网 /nats INFO 与本地一致；随机缺失文件HTTP在新公网与本地18080同404及响应样本hash。尚未证明新公网认证和实际文件下载。

## 推荐：先 A，成功即结束当前故障；B 为独立小修复

### A. 统一台式机实际地址，不需要先重包

1. 核实体手机当前前台包、default选择及前后台生效endpoint。只需endpoint，不读取/输出凭据。手机启动override可能覆盖保存设置；若已内置新default入口，不改手机、不重包。当前只有模拟器，手机此项待用户提供可验收设备。
2. 用户本人退出日常 MC 后再改配置，防止运行实例回写。复核确为default与原token存在；局部保留原pair/token及全部无关字段，仅将 D:/code/cx/history/remote_machine.json 的 domain 更新为 wss://mc-desktop-test.tingyou.cc:8443/nats。同步 app_state.json 的实际 domain 设置字段，避免旧表面状态；不能只改app_state，main.py::_initialize_remote_control_settings 会通过remote_machine_config.py::load_machine_config重新赋值。
3. 不生成/替换token，不把台式机凭据复制到其他来源；保留external模式，不新增全局DOMAIN环境覆盖。写入用同目录临时文件原子替换，修改前仅保存必要旧字段到私有回退记录，不将凭据/完整配置写入报告或Git。异常立即停止，不覆盖损坏配置。
4. 用户本人启动 MC；脱敏复核default/domain、external模式和文件public_base为 https://mc-desktop-test.tingyou.cc:8443。新PID/创建时间证明重启，不用磁盘文件代表运行值。
5. 先无凭据验证新入口消息INFO与文件HTTP，再在明确授权后完成限定认证和真实APK下载。成功则A交付，不为本次故障强制加入B。

### B. 修正健康检查对象，避免误诊；独立评估后实施

main.py::_remote_api_file_command_worker当前3659行及_ensure_public_file_route_ready检查NATS WS，却称文件路由。消息能通不保证HTTP能通；旧WS失败也不能独立证明文件HTTP失败。保留原消息健康检查用途，文件准备入口改查实际文件origin。

最小方案：在CopypartyFileService HTTP分发加入固定只读健康路径，由CloudflaredOriginProxy原样转发；响应仅包含短期随机实例标识，需与当前本地服务一致。路径不遍历文件、不列目录、不接受写入、不返回路径或token；不放宽任何真实文件授权。可用GET小响应、短超时且禁止跨origin重定向。对配置捕获和响应校验继续使用原35秒准备预算、owner/transport守卫、取消与失败回滚。不引入通用健康框架。

修改文件限 main.py（文件worker、_ensure_public_file_route_ready及必要bridge分发）、file_transfer.py（只读健康处理）；真实URL仍为 /<quote(name)>?每文件token，不替换为/files/id。保留public_file_route_unavailable及现有status/exists结构，附加脱敏阶段诊断；不把内部异常、URL查询或token发到手机/日志。

中文说明可在MC返回兼容的可选message，旧手机仍能按error处理。当前手机只显示error字段时，单改MC不能让旧包自动识别新message；本次不为文案强制改RC或重装。不要把稳定error码换成长中文而破坏兼容。若将来改RC，仅将该码映射为通用中文，去掉files_page.dart硬编码旧域。

## 包版本与部署门槛

A仅改配置，不改EXE。B实施前只读检查当前安装包PYZ中相关函数/模块与源码的代码特征或hash，记录差异；10月30日EXE时间不证明包含当前源码。B源码测试通过不代表日常包已更新。需要包更新时另行明确授权，按package_mc.ps1既有隔离构建再更新流程执行，并保留AGENTS明确允许的打包末尾自动启动例外。A配置阶段仍由用户本人退出/启动日常MC；当前仅规划，不运行打包或进程操作。

## 验证与失败回退

离线测试命令（D:/code/sj/mc）：

`.venv/Scripts/python.exe -m pytest -q tests/test_remote_machine_config.py tests/test_main_remote_nats_unit.py tests/test_file_manager_ui_unit.py tests/test_copyparty_file_service.py tests/test_file_http_service.py tests/test_cloudflared_bridge_reliability.py`

B再执行 `.venv/Scripts/python.exe -m pytest -q tests/test_main_unit.py -k public_file_route`。wx套件串行；全部fixture使用临时数据，不修改真实历史。

- Given旧域不可达但新default链路正常，When A完成且用户重启，Then生成URL使用新origin及8443，文件准备不再因旧域检查失败。
- Given新消息WS正常而文件HTTP坏，When B文件准备，Then仍返回兼容503且不发布不可用offer；反向故障由消息通道单独报告，不假冒文件故障。
- Given健康响应来自另一实例/跨域重定向，When检查，Then拒绝；真实文件无token仍拒绝。
- Given仅default配置变化，When选择default下载，Then请求及offer归default，laptop配置/凭据未变，不跨来源重试。
- Given用户授权新台式机origin验证，When从实体手机重新请求并下载目标APK，Then核大小和完整SHA256与现有独立证据一致（以已知8ac6dee8…完整记录为准），不是使用旧本地副本。

A失败：由用户退出，再原子恢复本次旧domain字段，用户重启；说明旧域当前不可达，回退只恢复配置不保证恢复服务。保留文件库和历史，不清手机数据、不转接laptop、不重启其他服务。B失败仅撤回B变更/包，A配置独立保留。

## 必须明确的授权和用户操作

本轮只规划。实施配置及部署需后续授权；日常MC退出/启动由用户操作。此前公网凭据发送被自动审批拒绝，不重试或绕过；下一阶段授权应仅限default指定新origin https://mc-desktop-test.tingyou.cc:8443（对应WSS /nats）上必要认证及该APK文件下载，不包含旧域、laptop或其他目的地。无凭据健康探测与离线单测先行；实体手机下载未验收前不得宣称全链路修复。
