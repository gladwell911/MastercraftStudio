# 当前交接

更新日期：2026-11-18。源码 `D:/code/sj/mc`，`main` 跟踪 `origin/main`。Kimi 2.5.3 协议、顺序和完成音效修复后，追问后的 steered REST 最终回答恢复已提交为 `8858753cd5a64903ef8c6b66af6e15299e4f9ab9`，独立定向159项通过；真实隔离云端验收通过。日常 MC 仍是旧包，独立 Kimi 运行时未安装；没有打包、替换或重启日常 MC，也没有改写日常聊天/笔记库。

## Kimi：当前完成状态与接续

V2 按真实握手选择 `subscribe_v2`，只认可当前 socket generation 的目标 session ACK；权威完成绑定主代理 `triggerPromptId`。运行/排队/阻塞状态继续 monitor，POST 结果不明时只读定位唯一归属，不重发请求。UTF-16 append 原子应用，失败保留原游标并补齐；完成后释放正文缓存。消息投影先按问题/答案各自的稳定顺序选尾页，再格式化正文；steer alias 共用一个最终回答。首次成功提示位于前后台共用接纳边界，UI 后处理异常不会吞掉该调用。completed turn 的末步若为 interrupted/steered，仅提取最后结构化 user 之后的公开正文，保留 tool/隐私/空正文/缺口过滤，不回退早先步骤。

运行准备、兼容能力、诊断开关及保留界限见 [Kimi 运行时](kimi-runtime.md)。源码不再自动回退共享 PATH；需要用户安排独立运行时安装和后续切换。固定版本不保证云端 API 永远兼容，回退前先验证会话格式可读，不让旧程序直接写日常库。

本次 [steered REST 计划](../_bmad-output/implementation-artifacts/plan-kimi-steer-rest-recovery-20261118.md) 已 built，含精确 node IDs、真实探针和实施证据。合成正例先失败复现，engineer 独立复验 **146 client + 13 integration = 159项**通过（1.77s / 4.13s，wx串行）；cwd 固定 `D:/code/sj`，client 命令为 `mc/.venv/Scripts/python.exe -m pytest mc/tests/test_kimi_server_client_unit.py -q`，integration 仅运行计划列出的13个恢复/alias/音效用例。`git diff --check` 通过，未扩大旧执行步骤失败或全系统回归。

真实隔离云端新请求、真实 socket 重连新代 ACK、同会话历史连续性、活动长任务追问和 REST 重放已通过（39.53s）。实际 REST 为 completed turn + interrupted/steered 末步；原 owner REST 正文严格等于实时最终正文。owner/alias 均 done、共享一个答案身份、一次完成音调用；两次恢复事件重放后正文/身份不变，仍一个答案及一次调用。脱敏证据和脚本保留于 `C:/Users/gladwell/AppData/Local/Temp/mc-kimi-live-e2e-0d77cf0e71e74dc095dd06548fbae473/`，修复前真实失败保留于 `mc-kimi-live-e2e-10445551e49e4677b0b8cb3ff866459f/`，原始调查目录 `mc-kimi-live-e2e-fu_cj887/` 未修改。这些 Temp 路径当前可读，不保证永久保存；不得清理冻结审查 diff `C:/Users/gladwell/AppData/Local/Temp/mc-steer-reviewed-4a15c39824ee4a049d3eb978131e98b2.diff`。

本次 [新 dog 四透镜报告](../_bmad-output/implementation-artifacts/review-kimi-steer-rest-recovery-20261118.md) 没有确认新增产品缺陷；父裁决为11项 reject、0 patch、0 defer（low9/false2，#4由原表false更正为low，裁决以计划为准）。`followup_review_recommended=false` 只表明本次无待修项；edge空结果按workflow记failed layer，diff包含计划及叙述证据，不能称完整 clean review。此前 `7852d06` 的 R1–R11、历史验证和 D1/D2 仍见 [原稳定性计划](../_bmad-output/implementation-artifacts/plan-kimi-stability-order-sound-20261118.md) 与 [原审查](../_bmad-output/implementation-artifacts/review-kimi-stability-order-sound-20261118.md)；早期138/306项只证明当时版本，3项执行步骤旧基线失败不计通过。

下一步仍待真实活动长任务断连及未决 POST 整体恢复、物理声音、日常独立运行时安装/包切换和长期运行验收。当前只用 winsound 替身计数，未证明声学出声；声学覆盖未复现，不加音频队列或历史补播。D1/D2尚缺固定版本真实触发证据，不加推测性防御；完成身份淘汰后旧重放、旧手机顺序字段和未知历史到达顺序的边界保留。隔离运行的 home、runtime、workspace、app-data/chat/notes 数据库均已清理，自有服务退出、私有 desktop 释放；日常 MC PID18416/Kimi PID24596 保持原路径与06:12启动时间，未动。日常部署和退出/重启仍由用户安排。

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
