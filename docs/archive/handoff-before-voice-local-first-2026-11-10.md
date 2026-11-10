# 当前交接

更新日期：2026-11-03。源码 `D:/code/sj/mc`，`main` 跟踪 `origin/main`。当前目标是保存台式机文件下载修复和可接续状态。2026-11-02 启动迁移与空白DOMAIN修复已提交；日常新入口与实体手机一次完整公网下载已有证据。下载慢的瓶颈仍未知，APK安装未确认。本次收尾不操作日常进程、配置或设备。

## 台式机文件下载：已完成与边界

- 源码 `ea241a5` 增加default/external旧默认入口启动迁移，`18221e2`修正空白DOMAIN遮盖持久值。非空显式DOMAIN/HOST/PORT、laptop、自定义域及cloudflared模式不迁移。DOMAIN空白跳过，TOKEN显式空值仍清除凭据。消息入口 `wss://mc-desktop-test.tingyou.cc:8443/nats`，文件origin `https://mc-desktop-test.tingyou.cc:8443`。
- 2026-11-02实体报告核日常 `D:/code/cx/mc/mc.exe`（当时PID25464，19:12:42启动），EXE SHA256 `3c1e0fa6f87684d5dd1274ad106ddda5f73f5e8ee905f8579bd62be968bbd15e`。PYZ含迁移常量、模式external且无DOMAIN覆盖，两份持久domain和手机前后台default endpoint均为新入口。报告早于当天19:58的空白DOMAIN补丁，不能证明安装包包含18221e2；其部署仍待核实。后续重新核运行态，勿复用历史PID。
- 实体日常主包com.example.zhuge_qa从真实回答仅请求一次下载 `D:/code/tingyouAndroid/android/app/build/outputs/apk/release/tingyou-283-0.0.27-release.apk`，原503未再出现。手机最终文件、电脑原APK和MC服务副本均为97,677,556字节，完整SHA256均为 `1e54b12f0af3145f7ff01ee2857e74f40b65e7b2dc6aa46e655ddda5692b6964`，手机库verifiedDownload=true。旧计划的270包不是本次验收目标。
- 19:18:13请求、19:44:04完成，约25分51秒，平均约61.6 KB/s，同一part持续增长。完成后进入MIUI ICP安装确认，未点击确认。Caddy/FRP配置未见显式限速，但尚未定位瓶颈；不把FRP拓扑或桥接整文件读取当作已证实根因。
- 隔离模拟器真实UI/生产worker/Copyparty HTTP链路通过1MiB完整大小和SHA256，并复现NATS健康检查失败可独立阻断文件准备。消息与文件健康检查未拆分，不自动扩大修复。

## 下一步、问题与证据

1. 若继续速度调查，固定同一文件和来源做分段测量。原报告未取得当前file_offer令牌，没有本地/公网64KiB对照结论，不扩查其他聊天或复制凭据。
2. 空白DOMAIN补丁只有源码验证；部署按既有打包约定另行执行，日常MC退出/启动由用户操作，打包末尾自动启动例外保持。
3. 既有token-only app_state保存及Windows并发replace WinError5未修；排除overlapping用例的结果不代表全套通过。
4. 实施入口：[启动迁移](../../_bmad-output/implementation-artifacts/plan-desktop-external-route-migration.md)、[空白DOMAIN修复](../../_bmad-output/implementation-artifacts/plan-fix-blank-domain-migration.md)。[10月31日计划](../../_bmad-output/implementation-artifacts/plan-desktop-file-download-route-20261031.md)仅保留历史，不再按未实施任务执行。
5. 私有证据：`D:/code/sj/.diagnostics/emulator-download-20261102/report.md`、`D:/code/sj/.diagnostics/phone-download-after-repack-20261102/report.md`及`D:/code/sj/.diagnostics/dog-review-migration-20261102/report.md`。配置、凭据、原始日志和APK不入Git。

## 本次验证

2026-11-03：运行配置及真实ChatFrame定向回归，明确排除既有overlapping并发替换用例，结果47 passed、1 deselected（4.12s）。验证命令：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_remote_machine_config.py tests/test_main_remote_nats_unit.py::test_external_desktop_startup_migrates_machine_and_file_origin tests/test_main_remote_nats_unit.py::test_laptop_machine_settings_reach_actual_transport_startup tests/test_main_unit.py::test_remote_nats_defaults_to_fixed_domain_when_host_is_unset tests/test_main_unit.py::test_remote_nats_url_strips_invalid_port_zero_from_domain tests/test_main_unit.py::test_remote_control_settings_normalize_dirty_domain tests/test_main_unit.py::test_fixed_domain_mode_forces_public_host_and_stable_port -k "not overlapping" -q
```

核文档链接与git diff --check；不重新执行历史通知、实体下载或宽回归。

## 历史：Codex 默认访问权限（2026-10-27）

- 默认沙箱为 `danger-full-access`，审批仍为 `on-request / auto_review`。访问权限与审批模式独立保存；缺失或无效权限回退新默认，已有 `read-only`、`workspace-write`、`danger-full-access` 均保留。
- 验证命令：`.venv/Scripts/python.exe -m pytest tests/test_codex_client_unit.py tests/test_codex_command_approval_unit.py tests/test_codex_worker_process.py tests/test_codex_command_approval_ui_automation.py -q`，221 passed。覆盖客户端请求、worker、审批流程与串行 wx 配置迁移测试；不代表真实模型或安装包验收。
- 下一步如需部署，按既有打包流程执行；本次只验证、提交和推送源码。

## 历史：通知部署（2026-10-26）

- 通知流修复提交为 `cd07002`。此前源码已有修复，但旧日常 MC 与手机 APK 均未包含该实现；旧手机基线 186475 高于实际新事件序列，事件在系统通知 gate 前即被标 terminal 并 ACK。
- 用户于 2026-10-26 重新打包并启动 `D:/code/cx/mc/mc.exe`。已检查实际 EXE 的 PYZ 包含 `notification-feed-v1`、`notification_feed_id` 和 feed devices 实现；EXE SHA256 为 `beb2f09617d82106af355eaedb8d2bb46bdf980ba0b80528b827c1e60b1fdee0`。
- 日常聊天库仍为 `D:/code/cx/history/chat_history.db`。手机 default 的 feed 与该库 meta 中的 UUID 一致，首次新流基线为 24725；未因数字下降清库、轮换 UUID 或清理通知账本。
- RC 已用正式双来源配置保数据覆盖安装。日常日志在 18:38:11、18:50:57（北京时间）分别出现 `gate=eligible → notification_posted`，对应 answer_seq 14386、14397；前一条随后因 `read_cursor` 取消。这证明日常实际发布路径生效，不代表本轮已完成日常通知栏、锁屏和点击的完整矩阵。
- 临时测试应用 `com.example.zhuge_qa.notificationprobe` 已卸载，独立测试 MC/broker 已停止，自有 ADB reverse 已移除。最终打开的是日常 `com.example.zhuge_qa`，不是同名测试入口。台式机部署已核实，笔记本未在本轮重新部署或验收。

## 验证范围

- RC 本轮四文件 Dart 定向测试 148 项、Android 原生单元测试 128 项通过。独立完整新 MC 的真实握手成功，包内 ChatStore/RemoteNatsTransport 生成并投递测试终答；这不是实际模型生成验收。
- 实体手机独立 QA 应用验证：186475 旧基线迁移到新流基线 3，桌面后台通知中心实际出现标题和正文；同一次锁屏操作发送前后均 `deviceLocked=true`，系统通知 active；同事件收到两次，仅发布一次。该证据不可替代日常锁屏验收。
- 最初执行 `pytest tests/test_notification_sync.py tests/test_notification_contract_unit.py --noconftest -o pythonpath=. -p no:cacheprovider -q` 得到 22 passed、6 failed。失败均在旧 contract fixture 未设置完成状态时触发 `INCOMPLETE_CANONICAL_NOTIFICATION`；本轮未修改它们，不宣称该组合全绿。
- 2026-10-26 通知文档更新复用上述对应源码的测试与构建结果；2026-10-27 Codex 验证范围见上节。

## 下一步与限制

1. 日常新消息验收应固定同一 pair/event/answer_seq，分别核收包、gate、posted、系统通知中心及读后取消。保持手机在桌面或锁屏，不能用 ACK 或补拉历史代替可见通知。
2. 手机 2026-10-26 多次被 MIUI `OneKeyClean` 强停，18:28:33 的强停发生在新 broker 启动前；重新打开后才完成新 feed 协商。强停是独立运行条件，不能仅靠本次 feed 修复保证继续接收。
3. 旧事件已被 terminal 去重，升级不自动补弹；不得批量清 posted/terminal/read 账本。数据库备份恢复且沿用同 UUID 的自动识别仍不支持。
4. 2026-10-19 的 TLS、恢复期发送及详情延迟未在本轮统一修复或长期验收；成功的新连接不能据此关闭历史问题。停止的完整 Windows/TalkBack 验收不自动恢复。
5. 既有长期运行风险保持：日志 reader 的受控读写/关闭交错尚未裁决；两个旧基线失败 `test_archived_mobile_result_v2_outbox_keeps_interleaved_owners_and_unique_finals`、`test_registered_history_handler_bounds_escaped_long_content_and_preserves_older_cursor` 不计通过。24–72 小时性能和月级稳定性没有结论。

## 保留的实现与运维约定

- 长期维护的部分索引、独立 publication_progress、安全前沿内有界清理、自有日志轮转/短写恢复、构建产物过滤及笔记启动恢复均保留。历史 242 passed/2 deselected、7 项补丁 51 passed、笔记 UI 7 passed 只对应各自版本；规则及复现见 [维护说明](../runtime-maintenance.md)、[协议](../remote-slim-protocol.md)和[历史验证](../remote-slim-validation.md)。
- 用户本人退出、重包和启动日常 MC。需要隔离运行时使用不同 EXE 父目录/聊天库、随机 token、独立端口和测试 OneDrive；不能接管日常隧道或按进程名批量停止服务。
- default/laptop 的配置、文件与读状态按来源隔离。打包版笔记/常用命令使用既有个人 OneDrive 数据，源码笔记为 `D:/code/note/notes.db`；本轮未更换或合并这些数据。

## 接续证据

私有诊断和验收 JSON 归档在仓库外 `D:/code/sj/_bmad-output/implementation-artifacts/notification-deployment-20261026/`，不提交日志、凭据或 APK。长期入口为 [远程配置](../remote-control.md)、[文档索引](../README.md)、[经验](../experience.md)、[反思](../reflection.md)。

## 2026-10-30 desktop FRP independent supervision

Source tooling: `deploy/mc-relay/frpc-supervisor.ps1` and `install-frpc-supervisor.ps1`; runtime copies and minimal wrappers are installed for `MCRelay-frpc` and `ControlTower-frpc`. Both use exact normalized config/Windows argv ownership with a per-user/config Global lifetime mutex, orphan adoption, bounded child recovery, maintenance stop/resume and deployed hash drift detection. Future desktop relay setup must use this installer; historical ControlTower instructions can replace the wrapper incorrectly. ControlTower source remains untouched; laptop rollout is excluded. This 2026-10-30 task did not rebuild the package; its Git status is historical.

Both unchanged configs passed `frpc verify`. Actual tasks have current-user interactive logon plus one-minute supplemental trigger, IgnoreNew, StartWhenAvailable, unlimited duration, battery operation and existing one-minute/999 failure retry. Runtime script/task backups are outside Git under `%LOCALAPPDATA%/MCRelay-supervisor-backups/20261030-230838-820` (original) and `20261030-230932-097` (successful task-trigger update). The first attempted hand-built trigger XML was rejected; the system task-trigger API replaced that implementation, and both tasks were successfully registered and inspected afterward.

Windows PowerShell 5.1 isolated fake-child matrix passed in full, including spaces/case/slashes, quoted config, same-EXE independent configs, concurrent wrapper, child-only proxy cleanup, orphan adoption/abandoned lock, duplicate refusal, zero/nonzero exits, precise stop/maintenance suppression, missing config/access denial and safe Status. Actual controlled recovery: MC child 30100→14776 with ControlTower 17404 unchanged; ControlTower 17404→15888 with MC 14776 unchanged; MC wrapper 29284 was terminated and the scheduled supplement adopted surviving child 14776. Each config finished with one child and a running task.

Authenticated direct18081/bridge18080/new-public `wss://mc-desktop-test.tingyou.cc:8443/nats` all returned PONG with identical server ID; `https://forum-dev-test.tingyou.cc:8443/` returned HTTP200 with normal certificate verification after recovery. Earlier probes against historical rc/tingyou endpoints included transient timeouts and are not evidence for the new FRP path. Daily MC PID7480 and creation time remained unchanged, and both config SHA256 values were identical before/after. Tokens were read only into probe memory; no model request, business write, phone setting change, CA/token/config rewrite or shared service restart occurred.

Remaining acceptance limits: ControlTower FRP successfully logs in/registers `controltower-api`, but public HTTPS returns502 because unchanged `127.0.0.1:18679` refuses connection; existing `ctkeepall_498eae7dc5-api-1` container is present without a host port mapping. API restoration/port publishing needs separate authorized service work; this repair did not restart it. Existing SDK ADB server5037 is alive but reports no devices, so actual phone main-package/connection acceptance remains unverified. Login startup is configured, not unattended boot availability or a reboot test.
Private actual outputs: `C:\Users\gladwell\AppData\Local\MCRelay-supervisor-evidence\20261030-230836` (`isolated-tests.txt`, `before.json`, `after.json`, `recovery.json`, `probes-frp-final.json`). Probe scripts there read existing credentials at runtime; no secrets/configs/raw response bodies enter Git.

### 2026-10-30 review fixes and redeployment

Added operation locking across Stop/Resume and installer preflight/success/rollback, strict managed-manifest identity/hash validation, fail-closed Resume marker removal, -c= ownership, exact maintenance/task-state rollback and bounded installation readiness. Both targeted Windows PowerShell5.1 suites passed (`tests/test_frpc_supervisor.ps1` and `tests/test_frpc_installer.ps1`), covering concurrency, normal/maintenance/managed rollback, startup failure, drift refusal and marker-removal failure. No production test parameters were added.

Redeployed both profiles via installer; private backup `%LOCALAPPDATA%/MCRelay-supervisor-backups/20261030-232704-694`. Final source/helper runtime bytes match exactly; both manifests clean, tasks enabled/running, exactly one child per config (MC20932, ControlTower20904). Both config hashes and daily MC PID7480/creation time stayed unchanged. Final direct/bridge/FRP-public PONG server IDs match and FRP forum HTTPS200/TLS verified. Existing ControlTower API/phone acceptance limits above remain; neither was restarted or changed. Actual output directory: `C:\Users\gladwell\AppData\Local\MCRelay-supervisor-evidence\review-fixes-20261030-232443` (`supervisor-tests.txt`, `installer-tests.txt`, `deployment.txt`, `status.json`, `before.json`, `after.json`, `probes-frp-final.json`).
