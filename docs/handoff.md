# 当前交接

更新日期 2026-11-19。源码位于 `D:/code/sj/mc`，分支 `main` 跟踪 `origin/main`。本阶段修复 Kimi Code 的跨聊天发送、精确取消、审批与问答、断线恢复和普通 `/clear`。日常包于 2026-11-18 补齐了固定 2.5.3 运行时。2026-11-19 的源码修复尚待打包和日常切换。

## Kimi 源码与运行时

- `8d896e8` 增加固定运行时收集和暂存、最终包门禁。源码及日常文件 SHA256 已回读一致，2026-11-18 独立版本探针均为 2.5.3。当天以非管理员身份完成隔离默认 spec 构建，日常进程保持运行。准备命令、校验和包内路径见 [Kimi 运行时](kimi-runtime.md)，当时的构建证据见 [打包修复计划](../_bmad-output/implementation-artifacts/plan-fix-kimi-runtime-packaging-20261118.md)。
- `904cda1` 落实 P1–P11 和七组审查补丁，涵盖原聊天归属、精确 prompt 中断、交互答复与只读重查、不同 owner 的独立恢复预算、未决 POST 和 WS/REST 来源区分。协议和验证命令见 [功能修复计划](../_bmad-output/implementation-artifacts/plan-fix-kimicode-functional-20261119.md)。
- `dec061a` 落实全新审查的八项产品修复及两项验证补强。普通 `/clear` 在聊天发送入口退休旧会话，保留历史并结算旧提问。旧回包、早到 WS 认领、延迟界面回调和归属重建均受原会话身份约束。交互使用原生单列控件，长选项可横向滚动。多个未决请求可轮流重查，启动失败仍可再次查询。归档重载只显示有效的重试提示，终态按完成顺序保留最近 512 项。审批可视正文和 `/stop` 被拒后的原请求状态均有回归。见 [独立审查](../_bmad-output/implementation-artifacts/review-kimicode-fresh-904cda1-20261119.md) 和 [最终修复计划](../_bmad-output/implementation-artifacts/plan-fix-kimicode-fresh-904cda1-20261119.md)。

## Kimi 验证范围

| 验证对象 | 结果 |
|---|---|
| 当时的隔离 frozen MC | 首问 5.97 秒完成，原生列表和 SQLite 可见。第二问完成且同聊天、同 provider session、不同 prompt ID，逐字复述断言失败。私有正文已清理，不能凭该断言定位原因。[原功能审查](../_bmad-output/implementation-artifacts/review-kimicode-functional-20261119.md) |
| `904cda1` 的源码与真实 2.5.3 | 首轮独立 223 项客户端及映射、15 项集成通过，最终补丁独立 16 项客户端、22 项 wx 集成通过。独立 API 认证下的真实 CLI 问答和取消 2 项通过（9.03 秒），源码 wx 问答及精确 `/stop` 1 项通过（10.83 秒）。CLI 登录后来恢复，最终云端复验使用独立 API 环境。脱敏日志 `live-cli.txt`、`live-wx.txt` 和 `source-sha256.txt` 位于 `%TEMP%/mc-kimi-functional-final-logs-06c7offn/`，已独立核对哈希。[验证记录](../_bmad-output/implementation-artifacts/plan-fix-kimicode-functional-20261119.md#审查补丁与最终独立验证) |
| `dec061a` 的源码 | 首轮 49 项通过，包含 wx 响应性套件。审查补丁后独立定向 40 项通过、181 项未选中（24.30 秒）。原生 wx、临时 SQLite、回环 HTTP 和确定性交错覆盖普通清理、审批/问答与取消，精确命令见 [最终修复计划](../_bmad-output/implementation-artifacts/plan-fix-kimicode-fresh-904cda1-20261119.md#最终审查补丁验证)。 |
| 旧失败基线 | 3 项旧 integration 断言失败和 2 项模拟 V1 提交超时均与基线一致，继续单列。[基线对照](../_bmad-output/implementation-artifacts/plan-fix-kimicode-functional-20261119.md#实施验证记录) |

2026-11-18 的 [steered REST 验证](../_bmad-output/implementation-artifacts/plan-kimi-steer-rest-recovery-20261118.md) 仍是活动长任务追问期间断连、新代 ACK、终答与 REST 核对、重放去重的现场证据，独立定向 159 项仅对应当时版本。65.39 秒场景允许 WS 补终结。脱敏证据在 `%TEMP%/mc-kimi-live-e2e-6f43394c04d746efb29e429d78efff4a/`。同目录根的旧失败探针和冻结 diff 保留用于复核。

## Kimi 接续

1. 新包仍需隔离构建并验收真实 frozen GUI 连续问答。日常切换需先核对部署路径和聊天、笔记数据归属，应用退出和重启由用户安排。物理键盘、读屏、声学出声、真实云端审批/提问及长时运行仍待现场验收。
2. 继续保留纯 REST 自动调度、真实未决 POST 的整体恢复、完成身份淘汰后收到旧重放等验证边界。固定版本的 steered 末步、journal 覆盖和权威 reset 等待证形态见 [steered 后续审查](../_bmad-output/implementation-artifacts/review-kimi-steer-rest-followup-20261118.md) 和 [稳定性审查](../_bmad-output/implementation-artifacts/review-kimi-stability-order-sound-20261118.md)。
3. [功能修复计划](../_bmad-output/implementation-artifacts/plan-fix-kimicode-functional-20261119.md) 保留中途硬退出、有成功状态但缺失 prompt ID 或响应畸形，以及代次切换后旧队列项的三组延期项。[最终修复计划](../_bmad-output/implementation-artifacts/plan-fix-kimicode-fresh-904cda1-20261119.md) 另保留静置过期后重试菜单暂时可用和入站过期 pending 会触发远程提醒两项既有问题。安装复制中途失败时缺乏回滚独立留在 [原功能审查](../_bmad-output/implementation-artifacts/review-kimicode-functional-20261119.md)。

隔离测试环境的临时认证、服务和数据已按原验收清理。审查探针、冻结 diff、脱敏输出及已有构建交付物留在本机。

2026-11-10 的语音阶段成果及待现场验收仍有效，保留如下；与 Kimi 本轮软件验证分别核对。

## 语音输入：已完成与证据

- 2.0 升级 `8f16036`：默认资源为 `volc.seedasr.sauc.duration`，默认 WebSocket 路径为 `/api/v3/sauc/bigmodel_async`，`model_name=bigmodel` 不变；显式环境覆盖保留，资源与接口须配套。现有凭据配合 `rc/rtjava/whoareyou.wav` 经真实 `RealtimeAsrClient` 返回“你是谁？你叫什么名字？”，`terminal_sent/final_received=true`，`cleanup_error` 为空。旧 `/bigmodel` 路径配 2.0 资源实测 HTTP 400，不应只换资源而保留旧默认路径。独立 quick 审查无发现；固定样本成功不能替代麦克风、物理双 Ctrl 与焦点插入验收。RC 未修改或重新部署。
- 实现 `2a089f22f459b4ed465f4d83195c98645e57977c`：设备启动且首帧入队后提示就绪，ASR 后台并行连接；单一有界 FIFO 保留并顺序发送连接前后音频。GUI 不等待设备或网络；会话代次隔离旧音频、结果和排队提示。已修终包发送返回前 final 到达的竞争，以及重复失败取消导致清理尚未结束就收敛的竞争；诊断记录保留后续 GUI 投递时间。
- 新 dog 审查 `71e05f5d83ae27bf6166914f58421c352beafec5`：四名全新 reviewer 逐个执行，未确认新产品缺陷；1 个 medium 回归缺口 V1 已补完。审查仍有局限：edge 空数组按 workflow 记 failed layer，blind 数量未达 floor，不能称完整 clean review。原裁决保存在 [审查报告](../_bmad-output/implementation-artifacts/review-voice-local-first-new-dog-20261110.md)。
- V1 补强 `22284df21b428800384d32de74450a4980c93ad4`：真实 receiver→controller→converge 每代处理两个不同累计 interim 和不同 final，仅提交完整 final 一次；direct/optimize 及两代相同 final 均覆盖。拼接/仅保留首版两类纯内存变异各触发 2 项预期失败；原实现通过。president quick 审查 0 项问题，engineer 独立定向复验 2 passed（0.06s），并核对变异设计和默认 fixture 未改变。

可复用的软件验证（2026-11-10；2.0 升级后 ASR/controller/integration 与 main voice 已重新通过，其余本地先录阶段证据详见 [独立验证](../_bmad-output/implementation-artifacts/verify-voice-input-local-first.md) 与 [V1 局部计划](../_bmad-output/implementation-artifacts/plan-v1-voice-cumulative-transcript-regression.md)）：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_voice_controller_unit.py tests/test_realtime_asr_unit.py tests/test_voice_input_integration.py -q
# 42 passed；2.0 默认资源与接口回归新增1项。
.venv/Scripts/python.exe -m pytest tests/test_main_unit.py -k voice -q
# 17 passed, 982 deselected；2.0 升级后重新通过。
.venv/Scripts/python.exe -m pytest tests/test_voice_input_ui_automation.py tests/test_voice_input_e2e.py -k "local_ready or queued_ready or optimize_worker or software_ready or actual_capture" -q
# 串行：9 passed, 37 deselected。
```

两个完整 wx 文件另有 26 项旧失败，已逐条核对与原 HEAD 基线相同（模型列表、聊天箭头、笔记 Alt-X）；不计为通过，也未借收尾扩修。必要失败证据仍保留在系统 Temp 的 `mc-voice-baseline-6is0yu3m/`，不提交原始日志、录音、数据库或凭据。

## 语音：仍待验收与下一步

以下五项尚未证实，不能从软件回归通过推导为已解决：

1. 实际设备 stop/close 报错后的未释放可达性；现有 fake 模拟已释放后抛错，不能保证任意驱动都及时回收。
2. 真实 transport 的取消硬截止；只有取消抵抗 fake 曾超预算，实际 aiohttp 等效问题未复现，不新增推测性隔离机制。
3. 物理全局按键到声学出声 P95；先前隔离真实设备 ready 回调 P95=250ms、最小 wx 消息响应 P95=15ms 只是元数据，不能当作音效 API 或声学验收。
4. 真实云端慢连接下句首识别；2.0 固定音频已通过正常连接的真实云端识别，但未制造慢连接并验证真实麦克风句首，固定文本/编号 PCM 替身仍只证明字节顺序与 GUI 边界。
5. 4–8 小时后的同组复测及原小时级变慢机制；短硬件样本和100周期测试不代表长稳。

下一步若继续现场验收，按 [原实施计划](../_bmad-output/implementation-artifacts/plan-voice-input-local-first.md) 的隔离输入承接、对齐回环声学证据和长期复测执行。不要用提前播提示、重置启动缓存、丢帧或文本内容去重替代会话完整性。部署须另按既有流程授权，日常 MC 的退出/重启由用户操作。

## 其他仍有效的接续事项

- **文件下载与入口迁移：** 2026-11-02 实体97,677,556字节APK完整SHA256验证通过，安装未确认；约61.6 KB/s瓶颈未知。非空显式地址与 laptop 规则保留；`18221e2` 空白 DOMAIN 补丁已有源码验证，日常安装包是否包含仍待核实。继续调查时固定同一文件/来源分段测量，不扩查其他聊天或凭据。实际旧进程PID不可复用。
- **通知与维护：** 2026-10-26 日常包和 RC 的 feed/聊天库身份已核对，真实发布日志与独立后台/锁屏测试有证据；日常完整锁屏、点击及 TalkBack 验收未完成，MIUI 强停是独立条件。不得清 posted/terminal/已读账本。维护依据 [runtime-maintenance](runtime-maintenance.md)，保留权威事实与用户数据；24–72小时/月级稳定性仍无结论。
- **台式机 FRP：** 2026-10-30 两个 supervisor 安装、所有权/恢复/维护状态回归及当时 PONG/TLS 验证通过；按 [relay 入口](../deploy/mc-relay/README.md) 操作，不凭历史PID认领进程。ControlTower API 的本地18679拒绝连接/公网502、手机未连接ADB及未做重启可用性验收仍未解决；本轮不恢复服务或改网络。
- **其他既有问题：** token-only app_state 保存和 Windows 并发 replace WinError5 未修；通知/远程旧TLS/发送/详情延迟、两项历史 remote-slim 失败不计通过。源码笔记路径与打包版 OneDrive 数据衔接仍按项目指令与 [远程配置](remote-control.md)，不清日常数据。

下载/通知/FRP 的原始时间点、完整命令、私有证据位置和其他旧验证结果保存在 [交接前快照](archive/handoff-before-voice-local-first-2026-11-10.md)。该快照只证明对应历史版本；当前任务入口以上文为准。长期方法与真实纠错分别见 [experience](experience.md)、[reflection](reflection.md)。
