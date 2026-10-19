---
title: 'frp/Caddy 第二批：台式机独立实例与手机连接验证'
type: 'feature'
ticket: ''
created: '2026-10-19'
status: 'built'
route: 'full'
route_source: 'auto'
review: 'quick'
review_source: 'auto'
lenses_ran: [president-scope-review, president-owner-lifecycle-review]
review_loop_iteration: 0
followup_review_recommended: false
context:
  - 'D:/code/sj/mc/docs/plans/2026-10-19-frp-caddy-migration.md'
  - 'D:/code/sj/mc/docs/plans/2026-10-19-frp-caddy-02-mc-rc.md'
warnings: ['oversized']
deferred: []
baseline_revision: 'ff710e9cc42dd31893ab88ab6331c69e54a74f12'
---

> 最新结果：2026-10-19，仅台式机独立包＋新增公网入口＋模拟器的当前授权范围已完成并独立验证，status=built。日常MC不退出/不替换，第二台与实体手机不在本轮范围。最新MC包381f236e…、RC APK4226b781…；详细当前状态见文末最终结果。此前blocked、旧hash及中间失败均为历史阶段。
<intent-contract>

## Intent

**Problem:** MC 依赖 cloudflared 管理，不能仅改域名实现独立 relay；手机两来源也需逐机同步正式配置。

**Approach:** 按用户两份计划实现最小 external 开关，保留默认 cloudflared；首台 default 全链通过后再迁移 laptop，完成第二批停止。

## Boundaries & Constraints

**Always:** director 实施、engineer 独立验证；保留每机现有 pair/token/history 与旧 CF 入口。日常 MC 由用户退出/重启。用户停止的完整桌面验收不恢复。正式包替换前先隔离构建并核对数据路径；仅 BuildOnly，不备份整个旧包。敏感值只入既有私有配置。

**Never:** 不执行第三批，不改 WSL、论坛 Android、forum 代理、旧 app/80/443、OneDrive；不改认证或复制另一机身份。无第二机操作条件、私有参数或用户退出条件时完成独立工作并记录具体阻塞，不能冒称部署完成。

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| 默认 | 未设置 mode | 原 cloudflared 行为 | 保留既有诊断 |
| external | 新发布 URL 含 :8443/nats | origin、本地/公网认证及恢复仍运行；全路径不管理 CF | 失败降级，不能假 ready |
| 非法配置 | 未知 mode 或无效 URL 端口 | 明确配置错误/探测失败 | 不启动 CF 补救 |
| 文件 | 运行态残留旧 cloudflared_url | external 使用当前配置并保留 :8443 | 不下发不可用本地地址 |
| 双源 | default/laptop 与各自旧 token | 归属隔离，单 frpc 中断不影响另一源 | 不重发未知结果命令 |

</intent-contract>

## Code Map

- `D:/code/sj/mc/main.py`：`_remote_runtime_config`/`_read_remote_control_setting` 复用双前缀配置读取；`_ensure_cloudflared_tunnel` 当前直接查询服务；`_ensure_remote_nats_startup_connectivity`、文件发送约3520、`_remote_health_worker`、`_stop_remote_servers` 均需按 mode 跳过 CF 管理，保留自有 origin/NATS 生命周期。`_remote_external_route_ready` 当前固定443；`_cloudflare_file_public_base_url` 当前优先旧状态 URL。
- `D:/code/sj/mc/remote_machine_config.py`：现已支持完整 WSS URL，字段 domain 是 URL；只按必要性修改，保留双机身份。
- `D:/code/sj/mc/tests/test_main_remote_nats_unit.py`、`tests/test_cloudflared_bridge_reliability.py`、`tests/test_remote_machine_config.py`、`tests/test_remote_readiness.py`：复用现有 frame/健康与认证夹具，覆盖矩阵边界，不跑全系统回归。
- `D:/code/sj/mc/package_mc.ps1`：已有 BuildOnly/DistPath/WorkPath 和重解析点保护；沿用现有打包，不另造部署框架。
- `D:/code/sj/rc/AGENTS.md`、`docs/handoff.md`、`docs/experience.md`、`docs/reflection.md` 已核对。`lib/remote_control_settings.dart:StartupOverrideRemoteControlSettingsStore` 优先编译配置；`scripts/install_physical_arm64.ps1` 已支持 DartDefineFile。RC 原则只改私有编译参数，无需改产品逻辑。
- `D:/code/sj/mc/_bmad-output/implementation-artifacts/plan-frp-caddy-01-server.md`：第一批最终 built。frps/frpc0.71.1、Caddy2.11.7；首台 EMPERORCOMPUTER/default；desktop域→回环19080→本机18080。临时 frpc PID33336 仅历史，接管前当前核对。laptop19081尚未启。实体手机93206cc7仅历史需重新核对；蜂窝曾经 VPN，不冒称裸运营商链路。
- `%LOCALAPPDATA%/MCRelay`：既有受限 frpc/独立FRP token/公共CA，不复制服务器私钥。服务器 `ubuntu@43.133.21.196` 仅新增 relay 目录/units；复用已通过证书演练，第二台只增加其 Caddy block。

## Tasks & Acceptance

**Execution（原始双机正式迁移清单；最新范围以下方 Scope Override 与最终结果为准）:**
- [x] `main.py` 和上述定向测试：实现 mode/端口/文件URL/健康与清理最小变化；估计超过100行，使用 full。
- [ ] `package_mc.ps1`：先隔离 BuildOnly；核对源码、产物和正式数据路径，再等待用户退出后替换，由用户启动。无需修改脚本除非确有必要。
- [ ] `%LOCALAPPDATA%/MCRelay/frpc.toml` 与新增登录任务：首台写新URL及持久环境并核对GUI进程继承；接管临时进程防双开；实测非交互隐藏任务、TLS与单任务重启恢复。
- [ ] `D:/code/sj/rc` 私有迁移JSON及 `scripts/install_physical_arm64.ps1`：从正式JSON复制，先只改default endpoint，完整保留两来源token/另一endpoint/backup；保数据安装并核对设备APK hash。
- [ ] 本记录：首台完成三处PONG/server_id、hello/readiness/list、手机蜂窝真实模型一次及授权文件、app重开/换网/frpc恢复后，才部署laptop；第二台同流程，最后双源隔离，记录版本/hash/任务/恢复耗时及24–48小时观察实际覆盖时长。

**Acceptance Criteria:**
- Given 默认或 external 模式，when 启动、文件发送、健康恢复及关闭，then 默认回归通过且 external 无 CF 管理动作，真实失败保持降级。
- Given 首台完成代码与隔离构建，when 用户启动正式新包并经手机新入口操作，then 实际进程继承external、三处同身份认证、列表/真实模型/授权文件及恢复均通过，旧数据保留。
- Given 首台全部必要项通过，when 同版迁移第二台并同时连接RC，then 两机各自对应URL/token/数据，单机断线另一机可用，设备APK hash与迁移JSON对应。

## Implementation Notes

## Plan Change Log
### 2026-10-19 — 用户范围覆盖：仅台式机与手机，隔离运行

触发：用户明确不迁移/测试第二台，日常 MC 暂不能退出；要求另一个目录构建并完成相应测试。本条覆盖上文历史 intent、任务与 Auto Run Result 中“等待日常退出/必须第二台”的当前适用性，历史文本保留。KEEP：已完成 external 实现、有效独立测试及已核准包 hash 证据；源码/输入未变时不重复验证或构建。实体设备与正式 JSON 缺失仅是上次检查结果，接续须当前复查。

## Scope Override — 当前执行范围

只对 default 台式机及实体手机实施隔离测试，不覆盖/退出/重启 `D:/code/cx/mc`，不部署或测试 laptop，不执行第三批。不声称隔离测试已切换日常安装。可暂时将本批 `mc-desktop-test` 对应新 frpc 的 localPort 切至隔离 origin，保留旧 CF 与正式18080；清理仅限隔离实例及本批隧道。

### 最小隔离方案与实际代码依据

- 已核准包 `D:/code/sj/_verification/frp-caddy-02-dist/mc` 的 frozen history 自动为父目录 `frp-caddy-02-dist/history`（main.py:resolve_app_data_dir），single-instance key 按该路径哈希；无需新增 history 配置。
- 仅对子进程设置 `OneDriveConsumer` 与 `OneDrive` 到 `D:/code/sj/_verification/frp-caddy-02-data`。准备其 `OneDrive/code/data/sj/notes.db` 和 `common_commands.json`，满足 frozen 两个 resolver。notes 源库用 SQLite 只读连接+backup API 生成一致本地快照，目标 quick_check；不得裸拷活跃WAL数据库。common_commands 复制为本地普通文件。禁止 junction/symlink 指回个人 OneDrive。这样初始化/同步改动只进入测试副本；清除测试进程继承的 NOTES_COUCHDB_URL 以免访问已配同步源。
- 全新 history，不复制日常 `state`、chat_history、nats、desktop-auth.secret、Codex线程或上传数据；仅通过进程环境私下传入现有台式机 token、pair=default、发布URL=`wss://mc-desktop-test.tingyou.cc:8443/nats`、`REMOTE_CONTROL_TUNNEL_MODE=external`、`REMOTE_CONTROL_HOST=127.0.0.1`、`REMOTE_CONTROL_PORT=28080`（启动前验证空闲）。配置不写用户级环境，不改日常配置。notes/common_commands 是本机测试副本，不是另一机凭据。
- 现包 NATS 固定先试4222；占用时尝试复用。独立 history 生成不同 desktop-auth.secret 可令复用认证失败后落到4622等备用端口，但会探测日常broker。`main.py:_start_remote_nats_server_if_configured` 无显式TCP端口入口。为直接保证隔离，增加可选 `REMOTE_CONTROL_NATS_PORT`（沿双前缀习惯），显式设置时只用该端口且占用即失败、不复用；未设置时完全保留现有行为。本次隔离设24622并预检。WS已有空闲fallback，读取实际端口/PID核对即可，无需再加WS配置。
- 文件HTTP已有3923到49300..49320回退，但 `file_transfer.py:DEFAULT_DESKTOP_FILE_STORAGE_DIR` 固定 `D:/code/file`。为隔离文件数据，仅在 `main.py:ChatFrame.__init__` 创建 library 时读取可选 `DESKTOP_FILE_STORAGE_DIR`，默认沿用原根；测试指向 `frp-caddy-02-data/files`。不改默认常量与公开构造接口。
- 以上两项是完整隔离必需的局部配套，director实现+engineer独验后才需一次重建；若不作代码改动，已有核准包不应重建，但不能把共享默认文件目录当作完整数据隔离。
- 可以执行真实 GUI、模型和授权文件链路。fresh history 用新的测试聊天，不恢复日常线程；模型登录复用本用户现有授权，工作目录在隔离包。文件只用本地测试根唯一marker。实例、NATS、origin、文件HTTP均须PID归属隔离包；新server_id必须与日常不同，隔离直连/bridge/公网三处相同后才发模型命令。

### 当前任务与验收（覆盖旧 Execution / AC）

- [ ] `main.py`：可选NATS TCP端口及文件根两个最小入口；定向测试验证默认不变、显式NATS端口占用不复用、合法端口与测试文件根生效；engineer独验实际变化。
- [ ] `package_mc.ps1`：只在上述源码发生必要变化后，同独立dist/work执行BuildOnly并重新核对hash/代码对象。日常包完全不动。
- [ ] `frp-caddy-02-data` 与 `frp-caddy-02-dist/history`：准备上述普通文件与全新history、私有启动环境；启动隔离GUI，核对实际进程环境、打开文件/监听端口、独立desktop secret与server_id。发现复用日常broker则立即停止隔离实例，不切隧道。
- [ ] `%LOCALAPPDATA%/MCRelay`：先记录本批原localPort，仅接管新增desktop-test临时frpc，避免19080双注册；切到实际隔离origin，verify后隐藏启动。保持旧CF与日常MC；复验公网身份。暂不建立指向短期隔离环境的永久登录任务。
- [ ] `D:/code/sj/rc`：当前重新核对实体设备和正式私有JSON；有条件时只更新default endpoint，保留laptop/backup/token字段，保数据安装并比较APK hash。缺失则完成全部不依赖手机的授权测试，记录当前证据，不伪造正式参数。
- [ ] 本记录：隔离三处mobile/password PONG同server_id、hello/readiness/list，手机蜂窝列表、一次真模型回答、独立marker授权文件、重开/换网与单frpc恢复。无手机时可先验证隔离GUI与真实协议，但不能替代实体验收。结束恢复本批frpc原localPort或按用户要求保留并明确记录，不操作日常MC。

Given 日常MC仍运行，when 隔离包启动并执行公网测试，then 日常PID/数据/旧CF保持，隔离实例拥有独立history、notes/common_commands/files及NATS/origin/file监听，新公网明确到隔离server_id。

Given 实体手机及完整正式配置可用，when default经desktop-test操作，then 模型与文件marker落在隔离实例，重开/换网/隧道恢复通过；laptop不部署不测试，正式日常迁移与永久任务不作为本次完成条件。

## Review Triage Log

## Verification

**Commands:**
- MC `.venv/Scripts/python.exe -m pytest tests/test_main_remote_nats_unit.py tests/test_cloudflared_bridge_reliability.py tests/test_remote_machine_config.py tests/test_remote_readiness.py`；先选实际受影响用例，wx串行；必要UI变更补相关ui_automation用例。
- `git diff --check`；engineer独立核对实际修改及测试证据，仅补疑点。
- `package_mc.ps1 -BuildOnly -DistPath` 配合实际独立父目录、`-WorkPath` 配合独立构建目录；预检目标无运行MC。
- 实际 frpc `verify`，当前身份/监听/持久任务核对；各机现有token用mobile/password测试直连、bridge、公网server_id/PONG，再验证真实协议与手机表面。

**Manual checks:**
- 必需人工动作仅限用户退出/启动MC、缺失的设备/登录条件；记录阻塞，不绕过现有桌面与安装边界。手机不卸载/清数据，未知执行结果模型请求不重发。

## Implementation Notes — Deployment preparation

2026-10-19：仅完成不依赖用户退出/设备的准备，未部署、未切换运行态。

- 首台现场为 EMPERORCOMPUTER，`D:/code/cx/mc/mc.exe` PID3820 仍在运行。正式持久身份 `D:/code/cx/history/remote_machine.json` 为 default，旧发布地址 `wss://rc.tingyou.cc/nats`，原 token 存在且未输出/修改。正式替换前仍须用户本人退出，之后用户本人启动新版；未假称退出，也未操作日常 MC。
- `%LOCALAPPDATA%/MCRelay/frpc.exe` PID33336 为原临时进程；配置 verify 成功，TLS serverName/公共 CA 保留，仅 mc-default、本地18080→remote19080。未终止或重复启动，未注册任务。
- 仓库外准备目录 `D:/code/sj/_bmad-output/implementation-artifacts/frp-caddy-02-preparation/`：`prepare-frpc-task.ps1` 与导出的 `MCRelay-frpc-default.xml`，S4U/当前用户登录触发/隐藏/失败每分钟重启999次/禁止重复实例/无限执行时长/电池允许。只导出XML，实际任务注册数0；S4U网络与故障恢复仍待正式条件满足后实测。
- `prepare-rc-default-json.ps1` 强制要求提供现有正式JSON及新私有输出目录，仅变更 default endpoint 到 `wss://mc-desktop-test.tingyou.cc:8443/nats`，逐字段断言另一来源token/endpoint、backup及其他字段保留。PowerShell语法与 synthetic fixture 字段保留检查通过；未创建冒充正式配置的JSON。
- 在 D:/code/sj 隐藏/忽略文件、D:/code 下JSON，以及用户TEMP内RC/remote/define/physical/dual/formal/restore相关JSON检索，未找到完整正式双源编译JSON。不得用QA参数、历史日志或手机偏好拼凑。正式参数缺失是安装前阻塞。
- 当前 adb 仅 `emulator-5570`，实体 `93206cc7` 不在设备列表；不能安装实体ARM64或执行手机蜂窝/真实模型/授权文件/重开换网验收。未操作模拟器冒充真机，未修改手机配置。
- 未取得已确认的 emperorLaptop 管理入口/会话；本机SSH目录无config。不能据旧公网可连推定第二台可部署，且首台尚未全验通过，未准备或启动第二台运行配置、未改服务器block。
- 正式包按现有 frozen 路径契约使用 `D:/code/cx/history`，个人OneDrive笔记与常用命令位于 `C:/Users/gladwell/OneDrive/OneDrive/code/data/sj/`，两文件存在。未写入OneDrive或history，未备份整个旧包。
- 当前仅准备工作完成；MC源码实现、隔离构建与独立验证由其他代理负责。首台部署和首台全部业务门槛仍未完成，不推进第二台或第三批，不提交/push。

## Auto Run Result — 首轮历史

Status: blocked。第二批代码和隔离构建已完成；正式部署及两机业务验收未完成，不能标为 built，不推进第三批。

Blocking condition: 首台日常 MC 仍在运行，退出/启动须由用户本人操作；当前 ADB 无实体手机；完整正式双源 DartDefine JSON 未找到；第二台电脑可操作入口未确认。这些是操作条件缺失，不是缺少本批授权。

### 已完成与证据

- director 修改 `main.py`：双前缀 external|cloudflared 模式，默认 cloudflared、未知值配置错误；external 的启动/文件/健康/关闭不管理 CF，但保留 origin 与真实认证、失败降级和恢复探测。当前配置优先于旧 cloudflared_url，URL 保留 :8443；路由探测使用实际端口。
- 两个现有测试文件补齐配置优先级、非法端口、双来源URL、启动/文件失败、健康降级和恢复用例。未修改认证、身份配置实现或 RC 产品代码。
- director 定向首轮86 passed及一个并发 Path.replace WinError5；该未改模块用例精确重试通过。新增边界18 passed，默认启动/文件/真实bridge及并发精确重试16 passed。engineer 独立远程链路76 passed，身份配置16 passed（含并发写），真实 RemoteNatsTransport 双实例 token/session/subject/事件隔离通过。
- engineer 独立链路还有一个已知旧基线失败：test_archived_mobile_result_v2_outbox_keeps_interleaved_owners_and_unique_finals:141 缺 chat-a，与 docs/handoff.md 已记录基线一致；未修复无关模块，不宣称整套全绿。
- 隔离构建：普通权限现有 Explorer 宿主 isAdmin=false，`package_mc.ps1 -BuildOnly -DistPath D:/code/sj/_verification/frp-caddy-02-dist -WorkPath D:/code/sj/_verification/frp-caddy-02-build`，exitCode=0。未替换或启动日常 MC。engineer 从 CArchive 提取 main 代码对象，规范化 co_filename 后与当前源码 compile 相等；必要资源存在，未混入history、身份/私有home或notes.db。
- 仓库外构建记录 `D:/code/sj/_bmad-output/implementation-artifacts/frp-caddy-02-build-status.json`、同前缀.log/.ps1；包在 `D:/code/sj/_verification/frp-caddy-02-dist/mc`。mc.exe SHA256=dd5fa0eaab9307c8fe5ab83a9a203b8463faeca262afaad6ed71ff3087f45d0c；mc_worker.exe SHA256=192ed0b86fb3c1449e34ea3a6ba67e8d5172ae407f15b2947c90c8448f7f7078。
- 准备产物的 XML 与 synthetic JSON 已由 engineer 独立检查；S4U/hidden/IgnoreNew配置存在，JSON仅default endpoint改变，其余token/laptop/backup及未知字段保留。未注册任务，S4U联网和任务恢复未实测。
- 核对实际 diff 与 `git diff --check`，未进入正式审查/提交阶段，代码与本记录保留在工作区；无 push。用户原始两份计划未改。

### 未完成与接续

1. 用户本人退出日常 MC 后，按既有 -BuildOnly 更新正式目录，保留原身份/历史/数据；写入首台确认的新URL与external环境后由用户本人启动，核验新GUI继承。包预检通过不替代运行态验收。
2. 使用正式双源JSON（需提供实际路径），连接实体手机；先完成首台持久任务、三处mobile认证/server_id、hello/readiness/list，再保数据安装RC及真实蜂窝模型/文件/重开/换网/单frpc恢复。不得用合成配置或QA参数冒充正式参数。
3. 首台全部必要项通过，且取得第二台实际操作条件后，才复用同版实现迁移第二台并验双来源隔离；尚未修改服务器第二台block。
4. 长时24–48小时观察尚未开始；本次没有制造新模型请求、手机安装、网络切换或frpc中断。第一批证书与续签配置未变，未重复签发/重启。

Review: 尚未达到整批部署验收条件，本轮止于独立验证；未启动额外审查轮次。followup_review_recommended=false 不代表整批验收完成。
### Engineer 可复现命令

工作目录为 MC 源码根目录，wx 串行。测试输出在本轮工具记录中，无独立 pytest 日志文件。

```powershell
.venv/Scripts/python.exe -m pytest tests/test_main_remote_nats_unit.py tests/test_remote_runtime_recovery_unit.py tests/test_cloudflared_bridge_reliability.py tests/test_remote_readiness.py tests/test_dual_local_harness.py -q
# 76 passed, 1 failed in 62.57s；唯一失败为上文精确已知基线。
.venv/Scripts/python.exe -m pytest tests/test_remote_machine_config.py -q
# 16 passed in 0.07s
```

## Implementation Notes — Isolated desktop runtime

2026-10-19 新 Scope Override 后，仅独立台式机测试，第二台已从当前范围移除。

- `frp-caddy-02-data/OneDrive/code/data/sj/notes.db` 由生产笔记只读连接及SQLite backup创建，连接均关闭，目标quick_check=ok；common_commands是普通文件副本，files独立目录。上述各级目录及dist/history均无reparse属性；history启动前全新空目录，未复制原state/chat/nats/secret/线程。
- 新构建mc.exe SHA256 `fc074e62d257f960f0cd859d44b4e02acc5f471cfa7bd4b61e6a0f6bbc835980`，经带hash门禁的 `preparation/launch-isolated-mc.ps1` 启动PID42228；仅子进程覆盖OneDrive、external/default、原token、origin28080、NATS24622、隔离files，移除NOTES_COUCHDB_URL。未改用户环境。
- 独立NATS PID37284，TCP24622与WS18082；独立origin28080归属42228。daily MC PID3820及daily NATS32504（4222/18081）与origin18080仍在，未退出/重启。新desktop-auth.secret与daily hash不同。
- mobile认证PONG：daily server_id为 `NCMBXH66I6OMPDLOBRD3HYTIE73IUG7FZROJFE3I5GSM3D6CKLHQXVFW`；独立直连18082及bridge28080均为 `NCQMYTGFKM4CKDA7IV4G5QSSB5E7YJLZGXPMVYJS2RYVVO2CGNA5T25M`，明确不同daily。独立RPC hello(protocol2)、readiness(ready)、history_list(空fresh列表)、model_list均200。可复跑脚本与结果位于仓库外preparation目录。
- 将本批desktop-test临时frpc从18080接管至28080的命令被自动审批拒绝，理由为可能中断日常远程链路、认为用户未明确授权这项运行态切换。命令未执行，原PID33336/配置未改；已报告主代理，不以同19080并行进程绕过。公网仍指daily，不能宣称三处独立身份验证通过。
- 依“公网确认到独立server_id后才发模型”的门槛，目前未发送模型/文件业务。真实手机、正式私有JSON仍待当前条件核对；无实体通过证据。未注册永久任务、未改第二台/服务器/旧CF。

### Local business evidence after runtime checks

主代理确认本地独立身份已足以完成本地业务，公网切换仍受审批阻塞，不将本地结果冒充公网/实体结果。

- 仅一次真实codex/main请求，fresh chat `relay-isolated-a69eaf45b5664799baea68e813d506d2`；message=200/accepted，history_read canonical `answer=RELAY_ISOLATED_OK`。`run-isolated-model-once.py`带持久once guard，未重发未知结果、未恢复日常thread。证据`isolated-model-history.json`。
- 独立files唯一marker的file_download_request=200，发布URL保留:8443。复用同一offer对本地3923与bridge28080作无授权/正确授权GET均404，文件下载验收失败，交president只读定位。`isolated-file-offer-private.json`含授权URL，私有材料不得公开原文。
- 文件请求前3923曾属于daily3820，之后Get-NetTCPConnection显示3923属于isolated42228，未见49300；日常MC仍运行。未据此直接修改代码、停止daily或宣布端口隔离成功。
- 当前唯一模拟器emulator-5570实际AVD名`Detox_API_35`，并非交接历史专用AVD；仅只读核对，无reverse映射，未安装/清数据。未把未知归属模拟器当实体或默认专用QA设备。

### Final isolated runtime verification — file bind fix

最终包mc.exe SHA256 `63c9d185316d7cac9bafdb9c6ff4db20f1d9ef0bb4a0919c5ba9b9f6d912d2a1`（新构建普通权限、exit0）。仅原隔离实例经CloseMainWindow优雅退出后重启；daily不动。启动预检发现svchost已有IPv6 ::1:28080，仅将仓库外launch脚本预检限定为实际IPv4绑定及wildcard冲突，未停止系统监听。

- 新隔离GUI PID40276、NATS35804；NATS TCP24622、WS18082，origin IPv4 28080归40276，文件HTTP正确fallback至49300归40276。daily MC3820、NATS32504及文件3923保持；原临时frpc33336未改。
- 新隔离server_id `NDPOWSKVW7KZ2WLL6UBK2NKMCJPAMLQF2OA2ET6RPF7CLZQBVJ4CTXVB`，直连18082与bridge28080均mobile/password认证PONG，且不同daily。hello/readiness/history_list/model_list重新只读验证均200。
- 使用已有隔离marker再次获取手机式文件授权，RPC200；发布URL保留:8443。仅替换authority到本地bridge28080测试同path/query：无授权403、正确授权200、下载内容SHA256与marker匹配。证据`preparation/isolated-file-result.json`；未把本地下载冒充公网下载。
- 原唯一模型chat及canonical回答持久化只读检查通过，notes/chat数据库quick_check正常；没有再次发送模型请求。自有GUI40276与NATS35804保留，供后续实体手机条件满足时接入。
- 新公网仍指原daily，自动审批拒绝的frpc切换未重试。实体设备/正式JSON及具体切换批准尚无新输入；未知归属Detox模拟器未操作。当前已完成本地独立MC/真实模型/文件验证，公网与实体项目保持未验。

## Auto Run Result — 历史：本地验证完成、待公网授权（2026-10-19）

Status: blocked。用户最新范围为仅台式机与手机，不部署或测试第二台；日常 MC 暂不能退出，改用独立目录构建运行。本地隔离运行及业务验证已完成，手机/公网验证仍待具体条件。首轮历史中的“退出日常MC”“第二台操作条件”不再是当前测试的阻塞项。

### 已完成

- 独立包：`D:/code/sj/_verification/frp-caddy-02-dist/mc/mc.exe`。普通权限 BuildOnly 第三次构建 pid39824/isAdmin=false/exitCode0；SHA256 `63c9d185316d7cac9bafdb9c6ff4db20f1d9ef0bb4a0919c5ba9b9f6d912d2a1`。engineer确认包内main和file_transfer代码对象与当前源码一致。
- 为此次并行测试新增最小隔离入口：双前缀REMOTE_CONTROL_NATS_PORT显式端口独占、失败不复用/回退其他broker；DESKTOP_FILE_STORAGE_DIR指定测试文件根，未设置保持原逻辑。director与engineer分别定向18 passed/43 deselected。
- 独立history、新desktop broker secret、本地OneDrive笔记快照及文件库；原笔记以只读源连接和SQLite backup复制，生产数据不写入，未克隆日常线程/聊天/认证secret。测试启动配置只作用于子进程，不持久修改用户环境。
- 本地文件404在此次并行运行暴露既有Windows端口复用问题；president以实际重复bind及单服务编码路径对照定位。file_transfer.py仅为embedded服务器增加独占绑定（禁用地址复用、Windows SO_EXCLUSIVEADDRUSE），沿用候选端口回退。director与engineer文件范围分别37 passed，覆盖真实双实例占用回退、同名marker分别200、缺/串token403。
- 当前自有隔离GUI40276、broker35804；TCP24622、WS18082、origin28080，文件服务49300。日常MC3820/文件3923仍保留。独立server_id `NDPOWSKVW7KZ2WLL6UBK2NKMCJPAMLQF2OA2ET6RPF7CLZQBVJ4CTXVB`；新直连/bridge以原台式机token完成mobile认证PONG且server_id一致，与日常不同。hello/readiness/history_list/model_list均200。PID与端口是现场快照，后续重新读取。
- 隔离新聊天 `relay-isolated-a69eaf45b5664799baea68e813d506d2` 仅发送一次codex/main模型请求，canonical回答 `RELAY_ISOLATED_OK`。重构建前优雅关闭旧测试实例，模型已持久保存；最终包仅只读核查原答案，不重新请求模型。
- 本地实际文件业务：file_download_request=200，发布URL完整保留:8443；用相同授权path/query请求独立bridge28080，未授权403、正确授权200且SHA256内容匹配。只使用隔离marker，未写生产文件根。
- 自有测试实例保留运行，日常MC、旧Cloudflare和原frpc均未退出、重启或改配；未注册永久任务，未操作第二台/论坛/OneDrive同步。未提交或push。

### 当前未完成与自动审批结果

- 公网尚未转至隔离实例。自动审批拒绝了“停止本批临时frpc33336、把新增desktop-test的localPort18080改28080再隐藏启动”操作，理由为本次用户授权未明确允许运行态切换、可能影响正在使用的远程链路。该命令未执行，没有绕过拒绝；原frpc及配置保持。
- 父代理只读核对：日常发布域为rc.tingyou.cc，新Caddy域mc-desktop-test.tingyou.cc:8443仅转到回环19080，frpc映射mc-default/19080→本机18080。已向用户提出具体授权：只将新增测试入口暂切独立28080，日常MC/旧rc入口保持。尚无答复，不把超时当批准。
- 当前ADB仅emulator-5570，其实际AVD为Detox_API_35、归属未确认；未安装或操作它，未将模拟器冒充实体手机。实体手机尚未连接，正式双源DartDefine JSON路径尚未提供；已询问用户。若连接实体并取得正式参数，保留其笔记本配置，仅更改台式机测试endpoint。
- 因此公网/实体手机连接、蜂窝网络、保数据安装、手机重开/换网以及frpc恢复仍未验；本地独立模型/文件通过不替代这些证据。第二台不再列入当前待办。

### 本次新增检查的精确命令

```powershell
# MC 源码根目录，director和engineer各自独立执行，未全套回归。
.venv/Scripts/python.exe -m pytest tests/test_main_remote_nats_unit.py -q -k 'explicit_nats_port or file_storage_root_override or remote_nats_server'
# 18 passed, 43 deselected
.venv/Scripts/python.exe -m pytest tests/test_copyparty_file_service.py tests/test_file_http_service.py tests/test_file_transfer_unit.py tests/test_file_transfer_protocol.py -q
# 37 passed
```

仓库外证据位于 `D:/code/sj/_bmad-output/implementation-artifacts/frp-caddy-02-preparation/`（launch/probe/model-once/file脚本及结果；private offer含签名不公开）。构建日志与状态仍为frp-caddy-02-build.*。旧哈希对应此前构建，不冒充当前最终包。
最终实际文件链已由engineer独立复验：只复用现有offer对127.0.0.1:28080做GET，无签名403、有效签名200；30字节与隔离files下marker逐字节相同，SHA256=9706bf0446a3210b7557ea244789b06c33e03aa8f1143458ee9baff50ab5a694。engineer核对49300归属测试GUI40276，日常3923归属3820。无新增模型命令或运行态切换。最终git diff --check通过。
## Scope Override — 模拟器及公网切换明确授权（2026-10-19）

用户明确说明当前无实体手机，改用模拟器验证；明确允许此前提出的“只将新增desktop-test临时frpc从本机18080转到独立测试实例28080，日常MC与旧rc.tingyou.cc保持不动”。此前自动审批拒绝因授权不明确的条件已由此新指令补齐，不再重复向用户索要同一批准。允许在C/D硬盘查找未知位置的正式RC配置JSON，查找不打印token。

director执行新增公网路由及已授权模拟器emulator-5570/Detox_API_35上的RC实际验证；engineer先独立只读查找JSON，后核对实际网络/模拟器证据。既有本地模型回答不重复发送；模拟器验证只证明模拟器网络与应用行为，实体/蜂窝/保数据真机安装不在本轮验收要求中。第二台不部署/测试，日常MC不退出/重启。
## Implementation Notes — Authorized public relay and emulator validation

2026-10-19 用户明确批准新增desktop-test临时frpc从18080切到独立28080，并授权使用当前Detox_API_35模拟器；不再沿用此前审批阻塞。当前未动daily/旧rc入口。

- 现场核对独立GUI40276/NATS35804和daily3820后，仅接管临时frpc33336。frpc.toml除localPort18080→28080外保留FRP token、CA、SNI及remote19080；verify通过，隐藏新PID39760。原配置仅私下保存在受限MCRelay目录frpc-pre-isolated.toml。
- 公网WSS8443 mobile认证PONG与独立直连18082/bridge28080三处server_id同为NDPOWSKVW7KZ2WLL6UBK2NKMCJPAMLQF2OA2ET6RPF7CLZQBVJ4CTXVB，且不同daily。新公网文件GET未授权403、有效授权200/30字节，SHA256=9706bf0446a3210b7557ea244789b06c33e03aa8f1143458ee9baff50ab5a694。
- 模拟器独立QA构建配置为受限MCRelay/rc-isolated-emulator-defines.json，仅default测试endpoint/token，laptop空；不称正式双源迁移JSON。工程师全盘可读目录搜索无正式完整双源JSON。
- 首次APK构建因旧.dart_tool/package_config指向Pub Cache dart_nats0.6.5而缺少本地NatsAuthenticationException；flutter pub get --offline修正元数据后构建成功。其带来的pubspec.lock五项SDK解算差异已精确检查并恢复，RC产品仓库无本次源码变更。
- APK为x86_64（核jar条目），adb install -r -t成功，无卸载/清数据。SHA256与设备均2197e1f2fe35aec996b5fa7845364d3349a89737c59507aea5c582424f47c026；设备此前无RC包，故不称已有RC数据保留升级证明。通知/后台运行弹窗通过实际UI允许。
- 模拟器原WiFi=1、airplane=0、captive_portal_mode=null。真实Android Chrome访问本次8443/nats显示安全连接及origin“缺少WebSocket Upgrade”响应，证明真实TLS可达；Google网络验证未通过令RC等待VALIDATED。临时设置仅此模拟器captive_portal_mode=0，并实际断开/恢复WiFi，随后RC连接。该条件不同生产蜂窝，不修改RC网络守卫。原设置待结束恢复并记录最终状态。
- 实际RC UI显示同测试聊天列表，打开已有唯一模型回答显示RELAY ISOLATED OK（UI按已有格式将下划线投影为空格，canonical仍RELAY_ISOLATED_OK），证据emulator-answer.xml。没有新增模型请求。
- RC force-stop/start真实重开后仍使用新公网，列表显示台式机已连接。仅停止新frpc39760时UI正在自动重连，隐藏恢复新PID37104后约6.21秒内恢复已连接与同chat；证据emulator-reopen-chat.xml、emulator-frpc-down.xml、emulator-frpc-recovered.xml。未终止daily或旧CF。
- 文件客户端尚失败：PC RPC广播既有marker可复现RC“未命名文件/0B/未提供下载地址”，而PC公网站下载通过；这仅是故障复现，不冒称手机UI发起/下载成功。原因不明已交president只读定位，等待明确最小方案，不修改产品或扩建框架。

### Emulator file flow and normal network validation

- 公网切换后原MC file_offer外层body重复嵌套导致手机未命名/0B失败；修复MC后新seq22/23已正确平铺，但RC将投影后的V2事件缓冲造成二次canonical验证失败。RC保存原始envelope并补clear_context单独drain投影后，冷启动文件tab可接收正确marker；旧坏seq21未改写。
- 实际点接收再暴露INVALID_CHAT_ID：冷启动文件页无activechat，文件控制丢失原offer owner。RC最小owner修复后中间QA APK `e0f3c5c0345e9deae97815747c4b3dbc2a6b81bc247aca19ed0eea14c6e9bb01`以-r安装且设备hash一致；新MC UI file_offer seq26/850d5fdb90044ecbafde5886e9be5247、原chatowner/revision1，从冷启动文件页实际接收并下载成功，不先打开聊天详情。
- 真实设备私有落地 `/data/user/0/com.example.zhuge_qa/app_flutter/files/relay-isolated-marker.txt`，30字节，SHA256 `9706bf0446a3210b7557ea244789b06c33e03aa8f1143458ee9baff50ab5a694` 与隔离源完全一致。截图及XML `emulator-owner-fixed-complete.*`，冷启动/等待/接收菜单证据同前缀；发送由Windows原生菜单“文件管理→已有marker→发送到手机”触发，不是PC RPC替代UI。
- 此时owner同record跨chat后续更新仍有局部收尾修复，故上述APK是成功业务中间证据，不冒充最终源码交付；待最终冻结后重构QA作最小coldfile复验。MC最终包381f236edd4e45c39afdbd4dfeee5b7504a0a5eed45cc007eda07c6893b21fd3不需重建。
- 正常网络验证收尾：删除captive_portal_mode恢复原null；仅模拟器保留 `captive_portal_http_url=http://connect.rom.miui.com/generate_204` 与对应https URL（原值均null），use_https仍null；WiFi恢复开启、飞行模式仍0。先宿主实测HTTP/HTTPS204，再在模拟器正常验证模式实际重连。
- Android NetworkMonitor 2026-10-19 03:03:34 UTC实际HTTP21ms/HTTPS127ms均ret204，网络104 WIFI CONNECTED且VALIDATED，RC UI恢复台式机已连接。证据emulator-network-stack.txt、emulator-normal-connectivity.txt、emulator-normal-network.xml；原值emulator-network-original.json。不是长期关闭验证，也不称真实蜂窝测试。
- 当前独立MC36980/NATS28620/frpc37104保留；daily3820/NATS32504/旧CF/文件3923不动。没有新增模型请求，没有卸载清数据，没有永久任务或第二台部署。

## Implementation Notes — Final emulator deployment evidence

2026-10-19 当前用户范围已完成台式机隔离实例＋授权模拟器验证；不代表日常安装已迁移、实体蜂窝已验或第二台已部署。此前失败/中间包记录保留，由下列最终结果覆盖其当前状态。

- 最终MC包mc.exe SHA256 `381f236edd4e45c39afdbd4dfeee5b7504a0a5eed45cc007eda07c6893b21fd3`；当前隔离GUI36980、NATS28620，TCP24622/WS18082/origin28080/文件49300，独立history与OneDrive本地快照保持。日常MC3820、NATS32504、origin18080/文件3923保留，Cloudflared服务仍Running；未退出/重启日常。
- 最终RC模拟器x86_64 QA APK SHA256 `4226b781e368e30a4c5f55c58b1e74801629b77fdfb09151696703ffadaade81`，adb install -r -t且设备hash一致。仅default测试公网配置，正式双源JSON仍未找到；不冒称正式参数或实体升级。
- 最后owner生命周期修复后，在正常网络VALIDATED环境冷启动文件页，不先打开聊天详情。原已落地marker的重offer被现有缓存保护忽略，故选择隔离库中已有同内容且设备未落地的 `relay-isolated-marker (1).txt` 复验，未清QA数据或创建生产文件。
- 原生MC文件管理UI选择已有副本→“发送到手机”，新成功offer seq29，event_id `file_offer-e6f4289a3d604987b98dd98dcb729b10`，record `file-e0cc75913e55477ab6f5677556f5c494`，owner `relay-isolated-a69eaf45b5664799baea68e813d506d2`，revision1；outbox已published、无blocked。seq28是在冷启动ready前发出的尝试，未用它冒充成功。
- RC真实UI显示完整文件名/等待接收，点行→接收后已完成。设备实际私有路径 `/data/user/0/com.example.zhuge_qa/app_flutter/files/relay-isolated-marker (1).txt`，30字节，SHA256 `9706bf0446a3210b7557ea244789b06c33e03aa8f1143458ee9baff50ab5a694`，与源相同。证据`emulator-last-marker1-waiting.xml`、`-menu.xml`、`-complete.xml`；没有通过PC直接写设备文件冒充下载。
- 同最终APK随后仅打开原唯一模型聊天，UI仍显示已有RELAY ISOLATED OK，canonical仍RELAY_ISOLATED_OK。未再次发送模型请求；`emulator-last-answer.xml`。
- 最后只中断新增frpc37104并隐藏恢复为33288，仍localPort28080/remote19080。UI在重启后5.079秒观察为正在自动重连，17.637秒上界内恢复已连接。该数字是轮询观察上界，不是精确恢复时刻；`final-frpc-recovery.json`、`emulator-last-frpc-recovered.xml`、`emulator-last-online.png`。
- 最后认证PONG新直连/bridge/公网三处同 `NCPTWNGHDUXE5VYM7YST6O72KE7KTIYYWIXTEAASZPP4LPXEJX6YVMQC`，与daily直连server_id不同。额外主动探测旧rc公网的命令被auto-review拒绝（旧公网未明确授权、认证载荷未核查），命令未执行/未绕过；仅只读核Cloudflared运行及daily PID，不把旧公网主动认证称本次通过。
- 网络最终mode恢复原null，未长期关闭验证；只在获授权模拟器保留HTTP/HTTPS `connect.rom.miui.com/generate_204` 探测URL（原值null），设备端实际HTTP/HTTPS均204，最终WiFi仍CONNECTED＋VALIDATED，RC UI已连接。原值与最终证据见此前网络section及`emulator-final-connectivity.txt`；无真实蜂窝结论。
- 最终保持隔离MC36980/NATS28620/frpc33288及模拟器QA在线；仅新增desktop-test入口指独立28080，旧CF与daily运行态不动。原frpc配置私有保存可恢复18080；本轮不自行回切破坏当前授权测试入口。未注册永久任务、未操作第二台/论坛、未提交/push。

## Auto Run Result — 最终完成：台式机独立包与模拟器（2026-10-19）

Status: built。按用户后续明确缩小的范围交付：仅台式机独立目录、已授权新增desktop-test公网与模拟器；不等待/退出/替换日常MC，不部署第二台，不要求实体手机。原始双机正式部署清单不作为当前验收门槛。

### 最终版本与运行状态

- MC源码基线 `ff710e9cc42dd31893ab88ab6331c69e54a74f12`；RC源码基线 `215a80aaf04f5150eac64d3981f6b0a1c9d3f563`。所有本轮源码差异将在本地Git收束，不push。
- 独立MC包 `D:/code/sj/_verification/frp-caddy-02-dist/mc`，mc.exe SHA256 `381f236edd4e45c39afdbd4dfeee5b7504a0a5eed45cc007eda07c6893b21fd3`；构建pid41696/isAdmin=false/exitCode0，engineer确认包内main/file_transfer与源码一致。应使用仓库外launch-isolated-mc.ps1的隔离环境启动，不能将无环境的exe直启等同此验收。
- 最终模拟器RC x86_64 APK SHA256 `4226b781e368e30a4c5f55c58b1e74801629b77fdfb09151696703ffadaade81`，`adb install -r -t`，本地与设备hash一致。未触实体手机、未卸载/清除QA数据；临时pubspec.lock解算副作用已恢复，无依赖配置改动。
- 隔离GUI36980、NATS28620、frpc33288保留运行（PID为现场快照）；独立TCP24622/WS18082/origin28080、文件回退端口49300。公网 `wss://mc-desktop-test.tingyou.cc:8443/nats` → 独立MC；三处mobile认证PONG同 `NCPTWNGHDUXE5VYM7YST6O72KE7KTIYYWIXTEAASZPP4LPXEJX6YVMQC`，与日常broker不同。
- 日常MC3820、NATS32504及Cloudflared服务保留，旧入口配置未改。最新额外旧rc公网主动探针被自动审批拒绝，未执行或换工具绕过；不冒称最后一次主动旧公网验证。此前engineer旧入口PONG证据属于较早阶段。

### 源码改动与实际修复

- MC main.py：external|cloudflared双前缀小开关，external各生命周期不管理CF但保留真实探测；发布/文件URL优先当前配置并保留8443；实际端口解析；显式独占NATS端口与测试文件根入口；文件V2 durable事件展开一层既有body，避免body.body。
- MC file_transfer.py：Windows embedded文件HTTP服务实际socket排他绑定，沿用端口回退，避免两个实例同端口落错文件根。
- MC三个既有测试文件：配置/失败/URL/身份/健康、实际socket及广播→outbox→hint形状回归。
- RC remote_nats_chat_service.dart：revision缓冲保留原始签名envelope；恢复与clear drain先验原始hash、局部投影，保留既有清理/执行语义。
- RC remote_nats_protocol.dart、main.dart：保留权威外层文件chat owner；中央文件控制按pair＋文件ID使用原归属；仅controller实际接纳且无活动传输的新offer重绑owner，活动/被拒/非offer不重绑，避免冷启动空owner及同文件换聊天沿用旧owner。
- RC三个既有测试文件：真实service恢复→controller、parser不变更原文、实际Home冷启动/换活动聊天/双来源同ID/新待接收offer重绑与活动传输冻结回归。未放宽认证、验签、pair/chat或文件授权守卫。

### 当前范围的真实业务验收

- 模拟器真实RC：列表、目标聊天和原唯一模型回答 `RELAY ISOLATED OK` 显示正常（canonical为 `RELAY_ISOLATED_OK`）。本轮整个接续仅最初一次真实模型请求，后续只读查看，没有重发。
- 最终APK、正常验证网络下冷启动文件页，不先打开聊天详情：MC真实文件管理UI发送已有隔离副本，seq29/event `file_offer-e6f4289a3d604987b98dd98dcb729b10`、record `file-e0cc75913e55477ab6f5677556f5c494`。RC显示等待接收→真实点接收→已完成；不是PC RPC代替UI，不把原完成文件缓存当新下载。
- 模拟器实际落地 `/data/user/0/com.example.zhuge_qa/app_flutter/files/relay-isolated-marker (1).txt`，30B，SHA256 `9706bf0446a3210b7557ea244789b06c33e03aa8f1143458ee9baff50ab5a694` 与隔离源逐字节一致。engineer独立只读核对设备hash、完成XML与答案XML。
- app保数据重开及新公网配置恢复已通过。最终正常网络/最终APK下只断新增frpc一次：5.079秒观察到自动重连，17.637秒为恢复已连接的观察上界（不是精确恢复耗时）。engineer核对当前唯一frpc33288与恢复XML；down.xml为停后短暂仍连接状态，不能冒称该XML证明重连中画面。
- 网络仅模拟器调整：原captive_portal_mode已删除恢复null，不长期禁验证；该模拟器HTTP/HTTPS探测地址改为实际设备双204成功的 `connect.rom.miui.com/generate_204`，WiFi真实VALIDATED，RC最终在线。原值及日志在preparation目录；宿主网络/防护、日常MC、论坛/第二机均未改。

### JSON全盘查找与保留配置

engineer用rg -uuu -l覆盖C:/、D:/全部可读JSON/JSONC；首轮含用户隐藏AppData/OneDrive、ENV/PS1。未找到同时含正式desktop/laptop endpoint及两来源token的原正式双源JSON；仅QA、synthetic和字段引用。系统拒绝访问目录未绕过，不输出任何token。

本次新建且明确标为单台式机模拟器测试配置：`C:/Users/gladwell/AppData/Local/MCRelay/rc-isolated-emulator-defines.json`。它不是找回的正式双源JSON，不用于冒充实体双机迁移参数。

### 定向验证及审查收束

- MC隔离入口18 passed；文件HTTP/授权37 passed；文件durable广播/outbox/hint独验42 passed（精确排除handoff已知archived_mobile_result_v2…旧基线），不宣称全系统全绿。
- RC原始buffer相关独验：service筛选11、controller13、共享协议矩阵1通过；parser4、实际Home冷启动/换chat/双来源用例通过；最后重绑生命周期与原Home用例各1通过。用例有重叠，不合计成独立总数。相关Dart分析与两个仓库diff --check通过。
- president限范围审查先无明确问题；新owner生命周期审查1条medium/patch（首owner永久保留），已最小修复，director与engineer精确复验通过。无defer或拒绝项，followup_review_recommended=false；实际已覆盖的相邻场景无需扩大全模块审查。
- 原始实体/蜂窝/第二台正式部署/永久任务/24–48小时观察不属于用户最新模拟器验证范围，未冒称执行；当前没有待用户批准的本轮操作。
- 证据统一保存在仓库外 `D:/code/sj/_bmad-output/implementation-artifacts/frp-caddy-02-preparation/`，包括最终APK/设备hash、emulator-last-marker1-*、emulator-last-answer.xml、final-frpc-recovery.json及network日志。私有offer含签名，凭据/私有日志/APK不提交。
### 本地版本收束

RC已提交 `c1f1f7e0b2186e1e9b150d13f579e74fef721dcc`（修复远程文件恢复与聊天归属），工作树干净；最终APK对应提交中的源码，提交后没有代码变更。MC及本记录由主代理按工作流提交到本地，完整标识以本仓库本轮最终HEAD为准。两个仓库均不push；凭据、测试JSON、APK、私有日志与隔离数据均不提交。