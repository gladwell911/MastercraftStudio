# Historical handoff snapshot before dual-computer control

Archived on 2026-10-08. Claims below apply only to their dated stages; use ../handoff.md for current status.

# 当前交接

截至2026-10-07。MC 新增 Kimi 系统注入边界修复（`497f24d`）和 BMAD 旧模板渲染兼容修复（`21413cf`）；既有 Kimi 公开思考/进度（`171c3bc`）、Codex 全局技能复用与打包后启动（`7f78c2b`）保留。跨设备已读同步与 Ctrl+Shift+X 未读聊天跳转保留，对应 MC `92b284b`、RC `9d57fa8`。MC 使用 `main`，RC 使用 `master`；后续文档提交以 `git log -1` 为准。完整跨端验收尚未通过，用户已停止完整桌面验收。源码和 GitHub 提交不代表已部署；本轮未实际打包、更新或启动日常安装版，也未改动现场失败记录或实体手机。

## 当前实现

- Kimi 的 `_kimi_is_user_prompt` 统一回答、思考、分页与旧 owner/alias 恢复的七处边界判断。REST 的 `metadata.origin` 存在时优先使用，仅缺失时兼容顶层 `origin`；只有字典 origin 的 kind 精确为 injection 才跳过 user 消息。未知、缺失或畸形值保守保留边界，隐藏正文、tool_use、权威完成与 owner/generation 隔离规则保留。
- BMAD renderer 仅规范化旧模板源中的配置、workflow 与 snapshot 标记，不递归渲染已解析内容；缺配置和遗漏链接仍严格失败。项目忽略文件 `_bmad/custom/config.user.toml` 补齐既有安装答案 `modules.bmm.user_skill_level=intermediate`，不修改全局技能；新机器须在本地提供该配置。
- Codex 全局技能复用：隔离 `.codex-home` 的 `skills`、`plugins` 整目录链接当前用户 `.codex` 来源，旧普通目录迁移到同 home 唯一 `*.legacy-*` 路径；来源缺失时保留旧内容，链接失败报错且不退回复制，全局文件字节（含 BOM）不改。每个新任务在 start/resume/turn 前注册当前 `.agents/skills` 并 forceReload 原生技能清单；全局 plugins/marketplaces 配置变化才更新对应配置并保留 home 重启 app-server，随后走原 thread 恢复链。执行中 steer 不刷新或重启，明确无活动 turn 后才准备新任务。同名技能选择与插件身份由原生 Codex 处理。
- 打包成功后启动：`package_mc.ps1` 先临时构建校验再更新，reparse 清理只操作节点，保留 `.codex-home`、包内外 history 和 OneDrive 数据。最终产物校验后从最终目录启动一次，不等待 GUI 退出；失败返回非零且不启动旧包或临时包。默认最终目录为 `D:/code/cx/mc`，调用方法见 [README](../../README.txt)。更新阶段失败可能留下部分程序文件，保留会话/history 且不启动，重新执行脚本完成更新；默认不创建旧包备份。
- Kimi 2.1.1 真实无 messageId 的 thinking/assistant 流按 session、epoch、turn、agent、原生 step 与内容种类补充稳定身份；公开思考在生成中直接进入 canonical 执行投影，列表展示原文摘录、详情保留全文。相同 seq 的不同 offset、重放、缺口和过期 step/epoch 保持隔离。REST 只在完整 prompt 边界内按 assistant 步骤顺序补全既有行；晚到流不能覆盖完成快照。工具调用前的 assistant 原文转为 commentary，REST 的 text + tool_use 同样保留，权威终答排除这些中间说明。隐藏标记整段单调生效，已缓存与持久化组装内容也同步清除；主 REST 不补写子代理流。RC 继续消费 v3 的 list_text/detail_text，无 RC 产品改动。
- 只有首次成功且非空的权威回答产生未读。稳定 canonical message_id 与首次完成 answer_seq 关联；首次启用的旧历史设为已读基线，清空才更换独立 generation。pair/chat/generation 下的 read_seq 只增不减，游标、durable fact 和 outbox 同事务提交。
- 自动定位不确认已读；主动回答导航、全文显示及成功打开网页确认冻结范围。Ctrl+Shift+X 使用原生 MOD_NOREPEAT 注册，恢复实际前台和最后项焦点，按已显示且不超过冻结上限的最大完成序列确认；无候选调用 ZDSR 播报“无未读聊天”。Home 首行非回答时不误读。未读标签不改变消息活动排序。
- RC 原生持久 pending 和范围通知取消已接入；后台目前固定 default pair，其他 pair 明确拒绝本地确认。旧通知缺少回答元数据时保留，不 cancelAll。协议和手机验收入口见 RC 的 `docs/current/remote-control.md` 与 `testing.md`。
- F1首帧使用当前chat/view/turn/revision的已知可视尾页；新步骤使扫描失效时仍保留有效尾页，深历史补齐留在worker。冷聊天正常打开通过可视索引获取真实尾页，避免隐藏占位遮住真实项。清空、切换owner和过期generation回调保持隔离。
- ChatStore的`execution_steps.visible`与最终payload同事务维护；旧库初始化时一次原子回填，`idx_execution_visible_tail`部分索引用于可视尾页。读取不再逐条调用Python predicate，原始payload、step_index和分页cursor保留。SQL LIMIT只约束结果数，不能证明实际工作量有界。
- 发送受理和首次成功权威终答直接更新对应历史行，绕过idle/导航静默等待，保留选中聊天身份与输入焦点。读取、标题、失败和终答重放不产生新的消息活动；置顶组规则保留。RC接纳权威时间，点击详情和状态读取不再置顶。
- Kimi自有本机REST Session禁用环境代理，WS明确绕过loopback代理。GET连接异常最多一次预算内恢复；Session配置后才发布共享引用；POST结果不明不重发。transport_error走owner恢复，权威provider error仍失败，不能仅凭文字含10054就重试。
- created_at/answer_at、累计300秒时间标记、Codex命令审批、通知路由、聊天信息及跨端v3执行投影保留。纯notLoaded占位过滤与UI/存储共享可视规则，有用描述和对应失败内容保留。

## 验证与复现

### Kimi 注入边界与 BMAD 兼容（2026-10-07）

“继续教育学习”现场 turn 27 的 Kimi 实际已正常完成，wire 中存在 1142 字终答；中途 AGENTS 注入被旧 MC 当作下一条 user 问题，截断答案。离线按真实 REST 投影重放，修复后取到与 wire 完全相同的 1142 字正文。REST 的 metadata.origin 包装通过已安装 kimi.EXE 内嵌 messageProjection.ts 核实。

最终定向验证：集成 87 项、wx 4 项、映射/客户端 179 项、并发 session 隔离 2 项，共 272 passed、0 failed、0 skipped；另有 renderer 兼容 5 项通过。四层审查及详细矩阵见[已完成修复规格](../../_bmad-output/implementation-artifacts/spec-fix-kimi-injected-message-boundaries.md)。本次文档收尾复用代码未变后的结果，不重复运行模型、GUI 或真实打包。可复跑命令：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_kimi_integration.py -k 'rest or recovery or legacy_owner or subagent_terminal or subagent_event or transport or resync or replay or alias or injection' -q -ra
.venv/Scripts/python.exe -m pytest tests/test_kimi_ui_responsiveness_automation.py -k 'sdk_public_thinking or kimi_status_batch_preserves_focus_selection_and_skips_noop_repaint or background_events_do_not_repaint_lists or background_structured_kimi_batch_persists_owner_without_foreground_repaint' -q -ra
.venv/Scripts/python.exe -m pytest tests/test_kimi_event_mapping_unit.py tests/test_kimi_server_client_unit.py -q -ra
.venv/Scripts/python.exe -m pytest tests/test_kimi_integration.py -k 'interleaved_same_turn_id_routes_by_session or same_chat_reused_turn_id_keeps_sessions_isolated' -q -ra
.venv/Scripts/python.exe _bmad/scripts/tests/test_render_skill_legacy.py
```

只读检查 `D:/code/cx/mc/mc.exe` 的 PyInstaller main 字节码，确认缺少 `_kimi_is_user_prompt`，而源码已有该函数，因此当前安装版未包含修复。现场聊天 ID 为 `f1594a0f-e603-443e-825d-ef9ab9cbe87d`，session 为 `session_ecf88af0-90eb-4443-b138-06c8579ce165`，prompt 为 `msg_01M4AP1HFJZSTK32C6CDND4MFW`，本地 turn_index 为 17。日志位于用户 `.kimi-code/server/events/` 及 `.kimi-code/sessions/wd__internal_fcfe2895237e/` 对应 session。部署后若处理旧失败记录，须先核对原始 owner 与恢复入口；尚未证明普通重启会自动修复该记录，不应重发问题或直接改库冒充验证。

### 全局技能与打包后启动（2026-10-06）

engineer 独立复跑三个定向文件：122 passed、0 failed、0 skipped。覆盖真实 Windows Junction 降级、源目录增改删、legacy 保全/幂等/失败恢复、全局 BOM 字节不变、原生刷新顺序、执行中 steer、插件配置变化重启及 pending 请求结束。打包测试使用临时目录、假构建器和假启动，覆盖成功、构建失败、缺少/空 worker、更新失败、最终校验失败与启动失败七种路径；没有运行真实 PyInstaller 或 MC。五个 Python AST、PowerShell AST 与 diff 空白检查通过。

```powershell
.venv/Scripts/python.exe -m pytest --noconftest tests/test_codex_client_unit.py tests/test_codex_worker_process.py tests/test_packaging_specs.py -q -ra -o pythonpath=.
```

`--noconftest` 只用于上述纯测试，避免现有 autouse wx.App 启动 GUI；不能照搬到依赖 GUI/数据库夹具的套件。Codex 0.160.0 非模型原生协议探针另确认两个技能来源、相同 extraRoots 配置下的同名记录与顺序、普通及同版本插件技能增改删在同进程刷新、插件身份保留，以及启用配置变化时重启且保留私有 home。共 46 次非模型 RPC，没有 thread/turn/model 请求。写入、配置和认证均使用临时目录；Windows KnownFolder 仍让原生发现只读真实 `.agents/skills` 的 30 项元数据，因此不能称完全隔离。未验证模型最终同名选择、跨缓存版本升级、真实 thread 恢复或安装包。计划与结果见 [已完成实施记录](../../_bmad-output/implementation-artifacts/plan-global-skills-package-start.md)。本次文档收尾复用以上未变更代码的证据，没有重复运行 GUI、原生探针或真实打包。

### Kimi 思考与进度（2026-10-06）

Kimi 思考/进度修复最终独立复验已由 engineer 完成：MC 映射/客户端 179 项、定向集成 50 项、隔离 wx 4 项、共享执行投影 4 项，共 237 passed、0 failed、0 skipped；覆盖真实 SDK 无 ID envelope、生成中 list/detail、多步骤/代理、WS→REST 同行、缺口/重放/epoch、隐藏流、steering alias、终答分离与后台焦点，以及实际 execution 正文查看器 caller 和真实 AnswerTextViewerDialog 的公开全文显示、关闭销毁。wx 串行并清理构造至销毁期间的定时器；仅检查受影响界面，没有恢复完整桌面/设备验收。RC 无代码、夹具或环境变化，复用首轮现有 v3 两项定向检查（投影行身份及完整详情、读取投影不改写 V2 durable facts），最终验证范围共 239 项；集成 92 项与 wx 5 项为 deselected。工作区报告：`D:/code/sj/_bmad-output/implementation-artifacts/verification-kimi-thinking-progress.md`。未调用真实模型、未替换运行包；该功能后续已并入 `main` 并推送 GitHub。定向通过不证明安装版、实体手机或现场 Kimi 输出已验证。

```powershell
.venv/Scripts/python.exe -m pytest tests/test_kimi_event_mapping_unit.py tests/test_kimi_server_client_unit.py -q
.venv/Scripts/python.exe -m pytest tests/test_kimi_integration.py -k 'sdk_ or rest_progress_rejects or rest_steering_alias or rest_only_commentary or rest_answer_consumer or rest_mixed_thinking or rest_progress_requires or thinking_status_interleaving or mapped_private_reasoning or thinking_privacy_is_monotonic or rest_thinking_blocks_sync or thinking_sync_orders or thinking_fetch_worker or maybe_trigger_kimi_thinking or recovery_thinking or long_mapped_assistant or out_of_order_absolute_offsets or mapped_assistant_delta_whitespace or events_for_non_visible_chat_do_not_repaint' -q
.venv/Scripts/python.exe -m pytest tests/test_kimi_ui_responsiveness_automation.py -k 'sdk_public_thinking or kimi_status_batch_preserves_focus_selection_and_skips_noop_repaint or background_events_do_not_repaint_lists or background_structured_kimi_batch_persists_owner_without_foreground_repaint' -q
.venv/Scripts/python.exe -m pytest tests/test_execution_projection.py -q
```

2026-10-05 已读功能独验：MC 10 项、RC 已读 Flutter 7 项、连接关闭/重连 6 项、Android 原生 5 项通过，touched analyze/编译/解析通过。MC 受影响回归 138 passed、6 failed，六项在精确旧基线复现，不算全回归通过。MC cwd 可复跑非桌面定向检查：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_chat_read_state.py tests/test_answer_read_open.py -q
```

最后完整尝试 v9 的八键、全文/网页、通知与重连、冷尾页和热键阶段通过，但晚段点击被路由过渡挡住，整轮失败。修正夹具后的短验已通过真实点击、加载后切走及加载期间锁屏守卫；离线 pending 等待失败时实际仍处于 keyguard，解锁前提未成立，不能判产品 pending 缺陷。真实 ZDSR 回环声音非静音采集通过，中文内容未现场听验。不得拼接不同失败运行宣称完整通过。

工作区证据（不在产品 Git）：`D:/code/sj/_bmad-output/implementation-artifacts/plan-cross-device-read-state.md`；`.build-envs/read-state-mc-directed-independent-final.log`；`read-state-e2e-independent-final-v9-close-fixed/` 与 `read-state-loading-route-stable-clean-independent/`。最后清理已实查测试 MC/NATS/peer 和 QA 包退出、QA PIN 与设备临时设置恢复，共享无头浏览器保留。

### 2026-10-04 已有证据

对应版本源码独验：Kimi client87项、恢复/provider25项、RC store/service103项及recency widget/analyze通过。索引/迁移/writer/冷聊天/F1定向11项通过；101与3000隐藏尾行的可视查询均386条SQLite VM指令。五个宽Kimi summary/ID用例在修复基线也失败，未扩展修复；这些结果不代表完整产品套件通过。

新增端到端测试由engineer独立复跑，MC cwd、wx GUI串行、app/notes隔离：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_immediate_kimi_local_e2e.py -q
.venv/Scripts/python.exe -m pytest tests/test_codex_ui_responsiveness_automation.py -k f1_first_frame -q
```

分别2项通过。前者通过HWND WM_CHAR/BM_CLICK、真实loopback HTTP和TCP reset，验证受理/成功终答两次竞争排序、焦点保持及问题仅一次POST；provider启动和WS受控。后者覆盖冷历史和执行中owner、3000隐藏尾行、慢worker首帧、新步失效及revision清空；F1采用wx KeyEvent，Tab采用HWND键消息，不等于物理键验证。

RC专用`Codex_Cross_Client_API_35`在2026-10-04为`emulator-5556`，真实UI与私有strict-V2 NATS场景通过，runner exit0。2026-10-05 已读验收对应 serial 为 `emulator-5554`；复跑须重新核对 AVD，不能据旧 serial 判断设备归属。以下为2026-10-04命令：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_cross_client_regression.ps1 -Mode Local -DeviceId emulator-5556 -DesktopRepo D:/code/sj/mc -ImmediateRecency
```

手机重复点击不重排；真实发送ACK后上移；权威后台活动到达后一帧上移；同一session终答bubble可见。harness必须发布完整execution_projection/history_changed，notification ACK不能代替历史更新。专用AVD曾VM连接失败，保数据冷启动、禁快照、softwareGPU和drive/no-dds恢复；未操作5554 Detox。私有harness/temp已清理，日志保留。

工作区证据：`D:/code/sj/_bmad-output/implementation-artifacts/`下的`plan-immediate-execution-kimi-chat-recency.md`、`verify-immediate-execution-kimi-chat-recency.md`、`tests/test-summary.md`及`tests/immediate-e2e-20261004/verify.md`。这些报告/日志不在产品Git仓库；本文件保留复跑命令与结论。

## 接续与边界

- 用户停止桌面验收后不得自动恢复 GUI/原生按键测试。若未来重新授权，先按真实布局等 PIN 入口并确认系统已解锁、应用前台及 activeChat，再短验完整加载/离线尾段，最后用全新证据目录跑一次完整矩阵；当前 unlock 夹具尚未修正。实际 HWND/focus/page 检查与 SendInput 同一次 UI action，拒发的键才可重新准备，已发键不重发。
- QA status channel 重建崩溃已对照 HEAD 确认为既有问题；仅专用模拟器干净重装后恢复，未修产品迁移。不能用卸载清数据方法操作日常包或实体手机。
- 2026-10-04物理F1 SendInput未送达char-hook，独立旧AltY也未送达处理器；保留环境失败证据，不能以wx事件通过代替物理按键验收。
- 用户现场Kimi10054具体诱因仍缺日志。本轮真实本机socket恢复通过，不证明真实Kimi启动/WS、公网Live或偶发断连消失；实体TalkBack、双机及完整产品套件未验证。
- 后续交付先核对安装版本和实际数据路径，再构建、更新及实测；源码HEAD不证明运行包已更新。RC实体包最后已知安装为2026-10-03文件分享阶段，本轮只用专用模拟器。
- 本轮 Kimi 修复的下一步是安装版交付：在部署任务中正常关闭安装版 MC 及 worker，核对笔记/OneDrive 数据衔接后使用现有打包脚本；再验证注入场景及旧失败记录的安全恢复。此次 neat-freak 仅整理、提交和推送，不执行部署或历史修复。
- MC `main` 跟踪 `origin/main`，RC `master` 跟踪 `origin/master`，各仓库仅保留对应主分支。旧开发分支已合并、删除；此前“不推送”的阶段约束已被后续明确推送请求取代。提交/推送沿用现有 remote/upstream，不代表打包或安装已完成。
- 源码笔记为`D:/code/note/notes.db`；打包版要求个人OneDrive的`code/data/sj/notes.db`存在并经完整性和已知表结构校验。2026-09-29云端库只是快照，切包前补齐后续变化，保留笔记、聊天及运行数据。跨机不并发运行MC，换机先退出等同步；默认不创建旧包备份。

既有Codex审批66项及2026-10-02通知/时间验证见本仓库`_bmad-output/implementation-artifacts/`原报告，只证明相应版本。宽失败见[历史基线](../non-live-regression-baseline-2026-09-06.md)，早期背景见[归档交接](entry-context-2026-09-29/handoff.md)。
