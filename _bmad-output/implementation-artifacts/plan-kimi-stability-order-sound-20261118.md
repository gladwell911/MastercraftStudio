---
status: built
followup_review_recommended: true
review: thorough
review_source: auto
lenses_ran: [blind-hunter, edge-case-hunter, verification-gap, intent-alignment]
workflow:
  route: full
baseline_commit: 7ac8fca4c6673b0d5645d4b4a0be7b6a7e5fdd1c
baseline_revision: 7ac8fca4c6673b0d5645d4b4a0be7b6a7e5fdd1c
route: full
review_loop_iteration: 0
warnings: [multiple-goals]
deferred:
  - summary: >-
      D1: complete=true journal coverage has no demonstrated invalid producer path.
    evidence: |-
      Confirm only with a reachable fixed-2.5.3 response whose batches fail its completeness contract; no speculative defense added.
    location: >-
      kimi_server_client.py:_consume_v2
    severity: medium (unverified)
  - summary: >-
      D2: same-frame authoritative reset shortening/replacement is not demonstrated.
    evidence: |-
      Needs a reachable 2.5.3 producer path or redacted trace before adding reset/replay mechanisms.
    location: >-
      kimi_server_client.py:_v2_execution_events
    severity: medium (unverified)
---

# Kimi 兼容性、消息顺序与完成音效实施计划

日期：2026-11-18。规划者：dog。源码基线：`7ac8fca`，工作区 `D:/code/sj/mc`。状态：built，源码实施、审查patch及独立定向验证已完成；真实云端新请求、物理声音、日常部署及长稳未验收。

## 目标及边界

恢复当前 Kimi Code 2.5.3 的发送、订阅、权威完成与断线恢复；使长任务中的追问和后到回答按实际出现顺序展示；恢复首次成功回答的完成提示，并防止同一回答重复提示。隔离 MC 使用的 Kimi 版本，避免日常 CLI 自动升级再次直接改变 MC 依赖。

只修改完成上述行为所需的适配、投影、测试和运行说明。保留现有 chat/session/prompt/turn/account/generation、清空 revision、隐私过滤、读屏焦点保护及跨端最终回答契约。不改其他 provider 的语义，不重构整个 main.py，不引入通用插件平台或音频服务。

本轮授权是源码实施、隔离验证、审查、收尾提交推送；**不执行 `package_mc.ps1`，不替换或重启 `D:/code/cx/mc`，不写日常聊天/笔记库，不替用户发送真实聊天**。运行时准备逻辑可实施并在临时目录验证，日常运行时安装和应用切换另列待部署。日常 MC 退出/重启由用户操作。

## 已确认依据与未确认事项

1. 本机 Kimi 已从 2.1.1 更新为 2.5.3：`C:/Users/gladwell/.kimi-code/updates/rollout.log:212` 记录 2026-11-15 eligible 更新，随后二进制时间及版本吻合。MC 默认从 PATH/共享用户安装目录寻找 Kimi，见 `kimi_server_client.py:105`。
2. 当前 Kimi 的 WebSocket `server_hello` 声明协议 2。其实际二进制内打包 JS 只处理 `client_hello/subscribe_v2/unsubscribe_v2`，未知帧直接忽略；MC 仍发送 `subscribe`。新订阅参数为 `session_id/transcript/transcript_since`。二进制内消息分派约字节偏移 111556611，schema 约 110021271；以实际内容为准，偏移只用于本版本定位。
3. 当前 session 的 `/status`、`/snapshot` 返回 404，`/messages` 返回 200 且有 assistant。MC `get_status/get_snapshot` 及 `main.py:_reconcile_kimi_owner_worker` 仍依赖旧接口。11/18 06:16 的真实请求在约 7 秒后已完成，MC 同一 prompt 仍 pending。不要重新读取或复制其正文作为测试夹具。
4. 纯内存执行真实 `_update_active_answer_row/_render_answer_list/_answer_rows_with_limit` 已复现：Q0、Q1 提交后 A0 才完成，列表变成 Q0/A0/Q1；即便 answer_at 晚于 Q1.created_at 也一样。缺失答案行触发全量重建，重建按 turn 逐组输出问题和答案。
5. steer 成功把追问登记为原 owner 的 alias；`_finalize_kimi_turn_state` 将同一答案写入同 owner 的多个 turn，投影可产生重复答案。有限现场查询未定位用户某次具体 alias 记录；此项按确定的代码行为与合成输入回归验证。
6. 首次成功完成的前台和后台分支均调用 `_play_finish_sound`，无失焦静音门禁。本次协议故障不进入成功分支，足以解释本次无声。其他历史“已显示却无声”没有对应日志，不得宣称已定位。
7. 音效共用异步 PlaySound；潜在相互覆盖及终结后 UI 异常造成漏提示尚未映射到现场。**不据此直接建设持久音效队列或重播系统**。
8. 已核对实际 mc.exe 的相关方法与最新源码一致；单纯重打包无效。当前 2.5.3 二进制内 `isAutoUpdateDisabledByEnv` 已确认 `KIMI_CODE_NO_AUTO_UPDATE=1`（旧别名 `KIMI_CLI_NO_AUTO_UPDATE`）禁用检查、自动安装、提示和启动 staged-swap；`[upgrade].auto_install=false` 仅控制自动安装。

## 执行顺序

### 第一步：建立最小失败回归与 V2 契约夹具

director 在现有 Kimi client/integration 测试中先加入失败回归，复用测试结构，不新建平行测试框架。

- 从当前安装二进制的可读实现核对 V2 transcript 帧、权威终态、prompt 关联、主/子代理标识、订阅 ACK、REST transcript/状态及序列补齐接口。只补齐实施必需的字段；不得将内部 wire.jsonl 的事件形状当作线上 WebSocket 形状。
- 用无私人正文的最小夹具保存准确结构，注明来源版本。禁止把 token、系统提示、真实会话或机器路径整段写入仓库。
- 失败用例：V2 握手后旧 subscribe 无 ACK；旧 status/snapshot 不可用；Q0/Q1/A0 的投影顺序；同 owner 多 alias 的重复答案及通知。
- 对仍需核实的 V2 字段直接检查本地实现或隔离服务；不猜测接口，不重启日常服务，不用真实日常 session 进行写操作。

交付：可失败的定向测试和最小契约记录。此步依赖协议事实，后续实现直接使用，不重复开展广泛调研。

### 第二步：完成 Kimi 2.5.3 协议适配和恢复

主要入口：`kimi_server_client.py`、`main.py` 的 Kimi owner/reconcile/terminal 处理，以及现有相关测试。

- 在现有 client 中按握手协议选取局部实现；保留现有 V1 路径及其测试，不让 V2 数据进入旧解析分支。不要为了未来未知版本建立通用框架。
- V2 使用实际 `subscribe_v2` 契约；只有成功且包含目标 session 的 ACK 才标记订阅就绪。未知版本、拒绝或超时形成可见、可重试的兼容错误，不静默挂起。
- 发送前要求传输和订阅准备就绪；请求已被接纳但结果不明时保留 owner，禁止自动重发造成重复任务。能力不满足时明确说明失败阶段。
- 将 V2 transcript 的消息/更新/完成映射到现有内部事件，保持主代理、session、prompt、turn、generation 归属；有完整正文及可信完成证据才发布最终答案。子代理、过程片段、失败与隐私内容不当最终回答。
- 替换 V2 的旧 status/snapshot 依赖，使用已核实的 transcript/状态来源；上下文信息等可选能力缺失不能阻塞核心回答，必须展示“当前版本不支持/暂不可用”，不能伪造数值。
- 以 V2 序列及实际补齐接口恢复断连，处理重放、缺口和服务重启；保留现有有界 deadline/退避，不引入无界高频轮询。终态先于正文落盘仍可恢复，不靠“最后一条 assistant”猜最终答案。
- pending 的旧会话通过稳定 session/prompt 身份只读找回完成结果；无歧义才接纳，错误提示区分接口不兼容、连接问题与生成失败。

验收：合成完整 V2 流及断线恢复均从真实 client 入口到 MC 最终状态；同一答案只接纳一次，已接纳请求不重发；V1 定向回归通过。支持历史恢复不等于已修复日常数据库，禁止在本步骤写它。

### 第三步：隔离和固定 MC 的 Kimi 运行时

- 为 MC 规定独立、版本化运行时目录和一个小型受支持版本记录（版本、协议、二进制 SHA256）。源码及 onedir 都通过现有路径工具解析，不硬编码当前用户名。
- 缺少固定运行时时显示可执行的准备说明，不悄悄退回共享 PATH。显式 `KIMI_BIN` 仍可作为诊断/高级覆盖，但也必须通过版本和能力检查，不能绕过错误提示。
- 提供最小准备/校验入口：从明确的本地候选二进制复制到指定独立目录，校验版本与哈希后供 MC 使用。仅在临时目录验收，不自动安装到日常目录，不修改用户全局 Kimi 配置。
- MC 子进程环境显式设置 `KIMI_CODE_NO_AUTO_UPDATE=1`；验证它不会被传入环境覆盖。禁止把 credential/token 写入版本记录。
- 独立路径避免用户在其他终端自动升级共享 Kimi 时改变 MC 依赖；单独设置环境变量不足以保护共享二进制。
- 启动前核对版本/哈希，握手后核对实际能力；错误信息包含必要的本地版本、预期范围和失败阶段，不泄露 token。升级操作只能选择已验证候选，不做后台自动替换。
- 文档说明固定版本不保证云端 API 永远兼容；回退必须先确认升级后的会话数据格式可读，不能直接用旧程序写日常库。无需本轮实现自动回滚系统或更新下载器。

验收：共享 PATH 候选改变不影响固定运行时；文件缺失/哈希变化/未知版本/订阅不兼容均明确失败；复制与环境构造在隔离目录验证。准备入口完成与日常安装完成分别记录。

### 第四步：统一消息顺序，处理 steer alias 唯一答案

主要入口：`main.py:_render_answer_list/_append_submitted_question_to_answer_list/_append_completed_answer_to_answer_list/_update_active_answer_row/_finalize_kimi_turn_state`，必要的 `chat_store.py` 序列化/投影及相关测试。

- 采用一个最小共享消息投影：问题和答案分别是消息，按稳定出现顺序排列，不再按 turn 强制相邻。原 prompt/turn 关联保留，不为了排列迁移 owner。
- 优先复用已有 canonical message 身份和可用顺序字段；若现有字段不能同时表示问题与回答的出现次序，仅给 turn payload 的问题/答案增加所需稳定顺序与回答身份字段，避免无必要的大范围 schema 重建。
- 新问题发送时分配顺序；最终答案首次被接纳时分配顺序，后续重放或恢复更新正文不再换序。created_at/answer_at 用于时间标签，不作为易冲突的唯一序号。
- 增量追加、缺失行重建、切换聊天、重启、历史分页和时间行均使用同一投影规则；按消息取尾页，不能先按 turn 截断导致后到答案丢失。保持稳定 row ID、选中身份和读屏焦点。
- 一个 provider owner 的最终回答只发布一次。alias 输入仍各自显示并结束 pending 状态，但引用同一个回答身份；不要给每个 alias 再生成独立可读答案、通知或未读计数。独立 queued prompt 的结果仍各自发布。
- 检查现有跨端最终发布/可读答案路径，确保唯一回答规则在源数据层生效。只修改 MC 必要配套，不展开手机端重构；若旧消费者有硬性不兼容，报告精确契约边界。
- 旧记录缺少顺序时做确定性只读兼容投影：有可信问题/答案时间时稳定合并，缺失时间保留旧相对次序，不编造当前时间、不按正文去重。未知的历史真实到达顺序不能保证恢复；本轮不批量重写日常历史。

验收：Q0/Q1/A0 始终 Q0/Q1/A0；steer 成功只一个答案，拒绝排队后两个独立答案；逆序完成、重放、重启及分页保持顺序。先失败后修复，覆盖真实 UI 方法和既有 UI automation。

### 第五步：修复并验证完成音效，不扩大到音频平台

- 第二步修复“终态不被接纳所以无声”的确定原因；第四步以唯一回答身份消除 alias 的重复完成提示。
- 首次成功最终回答的通知路径应对当前/后台聊天一致，导航保护只延迟 UI，不吞掉提示。失败、片段、子代理和重复终态不播放成功音。
- 对“答案已被接纳、UI 后处理抛异常、终态重放被去重”先做确定性故障注入。如果确实漏掉通知，将完成音调用从易失败的 UI 刷新链局部解耦，沿用现有完成身份去重；不要只重排一行而留下前后台语义不一致。修复不得因声音异常阻止答案保存。
- 为诊断增加最小、可关闭且有界的完成/音效调用结果记录，只记录匿名 owner/回答身份、接纳/去重/调用/失败阶段，不记录正文或凭据。
- 不新增持久音效 outbox、自动历史补播、通用音效调度器。PlaySound 相互覆盖未复现则保留为未验证限制；若隔离实测证明本任务确定触发的覆盖，再提交最小局部处理方案，不据猜测扩展设计。

验收：首次当前/后台完成各调用一次；重复 WS/REST 完成不再调用；排队任务仍活动时已完成答案照常提示；导航/输入焦点不影响调用；错误和片段不冒充成功。调用次数测试不等于物理听觉验证，真实输出设备验收单独记录。

## Given/When/Then 验收合同

- AC1：Given 当前 2.5.3 的真实 V2 契约，When client 握手、订阅并消费合成主代理最终回答，Then 正确接纳一次答案和成功通知；不调用旧 status/snapshot。
- AC2：Given 未知协议或订阅无 ACK，When 建立聊天连接，Then 在有界等待后显示明确兼容错误，不让用户请求永久停在无解释的 pending，不盲目重发已接纳请求。
- AC3：Given 已接纳的 session/prompt 及断线或序列缺口，When 按实际 V2 接口恢复，Then 只恢复该 owner 的完整权威回答，重放不重复发布，正文未完整时继续有限恢复或报告明确状态。
- AC4：Given 已验证独立运行时，When 共享 PATH Kimi 被替换或升级，Then MC 仍选择固定路径并向子进程注入禁更新变量；固定文件缺失/哈希变化时明确报错，不退回共享版本。
- AC5：Given Q0 长任务后提交 Q1，When A0 在 Q1 后完成，Then 增量、重建、重启、分页均显示 Q0/Q1/A0，时间行和焦点保持正确。
- AC6：Given Q1 steer 成功成为 Q0 alias，When owner 最终完成及重复完成事件到达，Then 两个输入均可见且不再 pending，但同一回答、未读事实和完成音只产生一次；steer 拒绝后的独立 queued 回答仍独立发布。
- AC7：Given 当前或后台聊天有首次成功完整答案，When UI 因导航延迟刷新或经故障注入失败，Then 完成提示不因该 UI 路径遗漏且答案状态仍可保存；重复事件、错误、片段、子代理不产生额外成功音。若注入不能复现遗漏，不添加无依据补偿机制并记录验证结果。
- AC8：Given 旧历史缺少消息顺序字段，When 打开并分页，Then 使用确定性兼容投影且不写日常数据库、不按正文合并、不伪造新时间；无法重建的历史真实次序标为兼容边界。

## 验证与执行分工

1. director 按本计划通过 `bmad-build-auto` 实施。既有方案直接执行；局部明确错误直接修复，原因不明或需要重大改方案再按全局规则升级，不重复撰写整套计划。
2. engineer 独立核对实际受影响测试和实现；优先复用有效结果，仅补关键缺口。相关 `tests/test_kimi_server_client_unit.py`、`tests/test_kimi_integration.py`、答案投影/存储及受影响 `tests/test_*ui_automation.py` 必须覆盖。wx 测试串行，fixture 显式隔离 app/history/notes 路径并清理定时器。
3. 单元/模拟服务通过后，可启动本任务拥有的临时 Kimi 2.5.3 服务，使用独立 home/端口/合成 session 验证真实握手、订阅和可读 transcript；不占用日常端口、不复用日常写入目录。无需复制全部缓存和会话。
4. 当前未授权发送真实云端测试消息；无凭据的隔离协议测试、脱敏夹具及已完成会话的只读核对可以执行。真实新请求端到端、物理声音、安装后长期运行必须标明待验收，不能以模拟结果冒充。
5. 用户指定的新 dog 执行 `bmad-code-review`；发现问题由 `bmad-build-auto` 修复，再做受影响范围复验。最后 director 执行 `neat-freak`，同步文档、提交并推送。

## 完成交付清单

- [x] V2 client、恢复及明确兼容错误已实现；V1 定向回归无退化。
- [x] 固定运行时准备/检查入口及禁更新环境已实现，临时目录验收通过；日常未安装状态明确。
- [x] 消息出现顺序在增量/重建/分页/重启一致；alias 最终回答和通知唯一。
- [x] 已证实无声触发链修复，必要故障注入通过；未复现声学问题如实保留。
- [x] 定向测试、独立验证和新 dog 审查证据可追溯，无未解释失败。
- [x] 任务创建的临时目录/进程已登记并清理；失败必要证据保留。无日常数据修改，无打包/重启。
- [x] handoff/README/experience/reflection 按最终事实更新；明确源码、隔离验证、真实云端、日常部署各自完成状态。

执行者在本文件末追加精简结果：修改文件、关键测试结果、未完成验收、审查及修复结论；无需为每一步再新建重复方案文档。

## director 初次实施记录（2026-11-18，后续独立验证及审查修复见下文）

- 修改 `kimi_server_client.py`、`main.py`；新增 `kimi_transcript.py`、`kimi_runtime.py` 和 `docs/kimi-runtime.md`。V1 保留；V2 从真实 server_hello 选择协议，采用目标 session 的 subscribe_v2 ACK 门禁、主代理 transcript reset/ops、triggerPromptId 完成关联、journal/full transcript 补齐及有界 prompt 分页恢复。旧 status/snapshot 对 V2 明确报告不支持。
- 运行时固定为已核对的 2.5.3 Windows SHA256；准备入口先验来源再复制复验，不覆盖已有目标。KIMI_BIN 也必须校验，子进程禁自动更新。日常独立运行时未安装。
- 问题和完成答案分别记录稳定顺序，完整消息组统一排序后取尾页；取消按 turn 预截断。旧数据有可信回答时间时合并，否则保持旧相对顺序。alias 保留问题和 done 状态，正文仅存原 owner，引用共享 answer_identity；远程 payload 同时携带顺序及引用字段。旧手机消费者若忽略新增顺序字段，其显示排序仍属于消费者契约边界，本轮没有修改手机代码。
- 故障注入确认：答案接纳后 `_request_kimi_chat_information` 抛错会漏完成音。已将状态保存调度与提示移到前后台共用接纳边界，重复终态仍去重；声音失败不撤销答案。有界匿名诊断默认关闭，无持久音效队列。
- 回归先失败：旧 subscribe、V2 未映射终答、Q0/A0/Q1 顺序及 UI 异常漏音均已复现。最终合并命令 `.venv/Scripts/python.exe -m pytest tests/test_kimi_server_client_unit.py tests/test_answer_list_time_rows_unit.py tests/test_kimi_integration.py -q`：**306 passed、3 failed**（61.95s）。剩余三项是执行步骤用例：`test_kimi_tool_completion_updates_started_item_with_result_and_failure`、`test_kimi_completion_does_not_overwrite_reused_item_id_from_another_turn`、`test_real_fixture_status_thinking_and_tool_result_produce_primary_chinese_steps`；初次运行时尚未核对基线；后续engineer已在原7ac8fca完整基线同断言复现，未计为通过。随后新增 journal 缺口回归，连同原缺口测试 **2 passed**。
- 受影响 `tests/test_answer_presentation_ui_automation.py` 首次 **11 passed、1 failed**：新加用例没有等待初始焦点任务结束；补显式原生焦点前置条件后该用例单独 **1 passed**。首次运行打印 Windows COM `0x8001010d` 诊断，但测试进程正常完成；不能把该诊断称为产品缺陷。原有11项未受后续测试前置条件修改影响。`git diff --check` 与源码编译检查通过。
- 真实无凭据隔离验证完成：准备到任务临时目录的独立二进制，独立 KIMI_CODE_HOME、空工作目录、随机端口；真实 client 得到 protocol=2、目标订阅 ACK、主代理空 transcript。没有发送云端 prompt。自有进程已退出，临时目录及154MB测试副本已清理，无日常数据库修改、无打包/重启。
- 后续engineer独立验证与新dog审查及R1–R11修复已完成。待验收：真实云端新请求、物理声音、日常安装切换及长期运行。未知历史真实到达顺序不可保证重建。尚未复现 PlaySound 相互覆盖，不添加补偿机制。最终文档已同步；Git结果见收尾汇报。

## engineer 独立验证（2026-11-18）

当前与基线三项执行步骤失败逐一同断言复现，确认为既有失败；临时基线副本已清理。新增关键验证：V2/运行时7 passed，完成/alias/音效9 passed，排序/UI焦点2 passed；wx串行，diff检查通过。无新增失败。真实云端、物理声音、日常部署及长稳仍待验收。

## Review Triage Log

### 2026-11-18 — Review pass
- verdicts: 24 findings — high 4, medium 9, low 1, false 8, maybe-false 2
- findings:
  - [high] [patch] 1 B1 — `reconcile_v2`只返回terminal event或None，丢弃已匹配turn的running/queued状态（`kimi_server_client.py:1097`）；`main.py:15036`收到None后设置terminal_wait，期限到达必经15174附近`_apply_kimi_error`。V1在15115附近明确返回monitor。恢复协调器给每轮设置短deadline（14960附近），故正常长任务断连恢复会被误判失败。patch R1。
  - [high] [patch] 2 B2 — `main.py:11565`在POST结果不明时创建`__unresolved__:`本地prompt，并启动恢复；15036的V2提前分支仅按该本地ID找triggerPromptId，绕过15080的legacy/unresolved迁移。服务端真实prompt ID不等于该本地占位，无法只读恢复已经接纳的任务。patch R2。
  - [false] [reject] 3 B3 — 实际2.5.3 schema字节110012000–110024500：`turnHeaderSchema=transcriptTurnSchema.omit({steps:true})`、`stepHeaderSchema=transcriptStepSchema.omit({frames:true})`。两种upsert不携带相应嵌套集合；保留旧集合正确，所提坏输入不属于固定协议。reject。
  - [medium] [patch] 4 B4 — engineer纯内存验证：reset(seq=1)后seq=2缺frame或冲突append，均返回True、seq推进为2、incomplete=True；真实`_consume_v2`只在False分支恢复，fake HTTP调用为[]。另外N1证明合法非BMP输入可真实触发同一不完整状态，非仅任意损坏输入。patch R3。
  - [false] [reject] 5 B5 — 固定2.5.3 broadcaster `subscribeTranscript`（字节111527911起）在没有transcript_since且新订阅从off到delta时发送reset；client仅在已有seq且完整时发送since（1059–1062）。实际首次订阅先有权威基线，未证明所指任意首ops状态可达。reject。
  - [maybe-false] [defer] 6 B6 — 源码确未核latest_seq，但候选要求服务端`complete:true`同时返回不完整/过期batches。当前固定服务的API把该标志作为完整性契约，现有证据未证明其违约；不能据假造响应新增防御。若有同版本真实可达的complete=true而覆盖不足，才确认medium。defer D1。
  - [maybe-false] [defer] 7 B7 — reset确未清frame_texts，新文本非旧文本前缀时会置incomplete；但本轮尚未证明固定服务在同一frame身份上会合法回退/替换此前已发文本。需要真实2.5.3重置/修正轨迹或producer可达路径，若成立为medium；不凭猜测增新机制。defer D2。
  - [medium] [patch] 8 B8 — 全文件检查_v2_transcripts/_v2_completed/_v2_started/_v2_frame_texts只初始化、追加；close和失效路径无会话释放。正常连续turn每帧留存文本，完成后仍保留，且每次事件重新遍历快照。无需猜测OOM阈值即可确认新增对象随历史增长；影响用户明确要求的持续稳定性。patch R4。
  - [medium] [patch] 9 B9 — 单测实际以ws_factory绕过真实greeting分支，integration直接注入完成snapshot；已有一次无凭据真实握手证明当前初次ACK有效，故不能说当前握手已坏。但正常回归没有覆盖greeting→成功ACK→重连新ACK→完成的生命周期，E1已揭示该边界实错。按验证缺口patch R5，不重复真实云端验收。
  - [high] [patch] 10 E1 — engineer用现有started_client fake，经过server_hello→subscribe→匹配ACK，再调用实际`_invalidate_ws(recover=True)`并暂停恢复worker：_ws=None但ready仍含s、Event仍set，submit_prompt发生fake POST并返回p。过期ready可绕过当前连接订阅门禁。patch R6。
  - [medium] [patch] 11 E2 — 与#4同一apply成功标志/游标缺陷，缺frame及冲突文本均经局部probe确认。patch R3。
  - [false] [reject] 12 E3 — 与#5相同，实际初订阅reset先于ops；未显示可达首ops无基线状态。reject。
  - [false] [reject] 13 E4 — 当前固定schema余下的marker/interaction/attachment/prompt upsert为非最终正文投影信息，未知未来op不在当前固定协议。未证明忽略它们会使现有final正文变旧却误发；prompt状态缺失的真实问题已由#1单独确认，不能借此证明原“未知op”主张。reject。
  - [medium] [patch] 14 E5 — 同#8，新增长期缓存缺少结束/会话清理，源代码足以确认增长路径。patch R4。
  - [low] [reject] 15 E6 — 两个显式prepare同时通过exists再copy确有竞争；本工具为人工本地准备，日常使用几乎不会同时安装同一路径。按技能低影响/罕见且需新增并发门禁分支的拒绝规则，不新增锁或临时发布机制。reject。
  - [medium] [patch] 16 V1 — 依技能信任该层已核查的测试证据：普通V2回归没有append，删掉append分支仍可绿；N1又证明该缺口已掩盖合法输入缺陷。patch R7。
  - [medium] [patch] 17 V2 — 依该层搜索证据：新顺序用例只配时间、不配显式顺序；去掉sequence优先仍能通过。稳定序列为本次功能核心，需真实重建/重放边界断言。patch R8。
  - [medium] [patch] 18 V3 — engineer单跑`test_render_answer_list_does_not_format_all_old_turns_when_bounded`：1 failed，formatted=500，visible limit=100。改动把排序前的昂贵正文格式化扩大到全部历史，影响长会话UI响应。patch R9。
  - [medium] [patch] 19 V4 — 已读live smoke第100行无条件get_status；client964对V2无条件raise，固定2.5.3无法通过该检查。未运行真实云端，用调用路径即可证明测试适配错误。patch R10。
  - [false] [reject] 20 I1 — 作为“本轮未完成已授权范围”的缺陷不成立：当前授权明确不部署、不发新云端消息；文档如实列日常安装及长期稳定为待验收。保留描述边界，不新增修复/部署。reject。
  - [false] [reject] 21 I2 — 已接受实施计划明确将成功steer并入owner、独立queued结果各自发布；本轮按该契约实现。真实追问即时回复未验收是现存验证边界，没有足够证据要求另造独立答案语义。reject。
  - [false] [reject] 22 I3 — 调用测试不等于声学验收这一描述正确，但实现与文档未宣称实际听到或全部历史无声根因已解决；授权范围也不含物理声学验收。作为代码缺陷reject，保留待验收。
  - [false] [reject] 23 I4 — 计划采用共享消息投影并明确旧远端忽略新增顺序字段的边界；仅“影响通用方法/未改手机”不能证明语义回归。唯一已证实通用回归为#18，独立修复；不追加其他provider/手机改造。reject。
  - [high] [patch] 24 N1 — 合法append的offset单位不匹配。固定二进制producer字节111605871 `offset:open.offset`，111606043 `open.offset += delta.length`，111606794 `offset:frame.text.length`；consumer109952397后appendAtOffset用JS.length/.slice，均为UTF-16单位。Python第61行却用len/code point切片。emoji前缀长度JS=2/Python=1，offset=2的合法后续x使apply返回True、incomplete=True且丢x。patch R11。

完整原始透镜及R1–R11最小修复要求见 [审查报告](review-kimi-stability-order-sound-20261118.md)。已沿调用链核对，全部明确问题按patch进入本次build-auto同一实施代理修复，不重复全轮审查。

### 审查修复 R1–R11（engineer 17+4项独立复验通过）

本轮局部修复：V2 明确区分 running/queued/blocked 并返回既有 monitor；legacy/unresolved 先用只读 transcript 唯一归属，再按真实 prompt 恢复，歧义明确失败且不重POST。恢复事件统一转换为 MC 消费类型。append 批次原子应用，失败不推进游标，并按实际 UTF-16 code-unit 处理追加/重叠；真实 client 缺口或冲突触发现有修复。ACK 绑定当前 socket generation，实际 invalidate 立即撤销旧 ready/Event。WS/REST 完成后释放已完成正文、started/frame cache，完成身份最多512项、空闲transcript最多32个，保留pending文本，close清理自有状态。共享轻量消息顺序先选择尾页再格式化正文，保留旧turn晚到答案。opt-in smoke 对 V2 检查目标prompt权威终态，仅V1使用旧status。

新增回归按组覆盖：R1 三种运行状态跨短预算仍pending、后续final/重放只一次提示、终态缺正文有界；R2 丢失POST响应唯一候选恢复与两候选歧义，POST均仅1次；R3/R7/R11 非BMP/中文/ASCII/代理对、重复与部分重叠、冲突/缺frame原子回退及client repair；R4 530轮完成保留界限、pending仍可续传、WS/REST重放与close；R5/R6 实际无ws_factory连接分支读取greeting，目标ACK后POST、失效/旧ACK阻止POST、新代ACK恢复；R8 时间与显式顺序相反时重建/分页/SQLite重载/重放仍稳定；R9 既有500轮有界格式化和原生焦点回归；R10 离线fake证明不调用旧status且只认可目标成功终态。D1/D2及已拒绝项不变，未新增对应防御。

定向结果：client/时间投影/既有有界用例140 passed；新增核心组合命令 `.venv/Scripts/python.exe -m pytest tests/test_kimi_server_client_unit.py -k 'v2_ or runtime' tests/test_kimi_integration.py -k 'v2_ or runtime' tests/test_answer_list_time_rows_unit.py -k 'v2_ or runtime or explicit_sequence or lightweight_tail or late_answer' tests/test_main_unit.py::test_render_answer_list_does_not_format_all_old_turns_when_bounded -q` 为33 passed、297 deselected（全局-k过滤，不能算该命令执行了最后的bounded用例）。随后显式运行 `tests/test_answer_presentation_ui_automation.py::test_late_answer_rebuild_preserves_native_question_selection` 与 `tests/test_main_unit.py::test_render_answer_list_does_not_format_all_old_turns_when_bounded`：2 passed；最后原子apply局部微调复验 `tests/test_kimi_server_client_unit.py -k 'append_utf16 or failed_append'`：7 passed。diff及编译检查通过。测试均已结束；未发云端消息、未启动日常MC、未写日常数据、未准备日常运行时或打包。
## Auto Run Result

Status: built. 本轮实施、独立验证、指定新 dog 四透镜审查及 R1–R11 修复已完成；日常部署未执行。

- 实现：V2订阅/权威终态/恢复，独立固定运行时和禁更新，统一消息出现顺序与alias唯一答案，完成音与UI故障解耦。
- 文件：kimi_server_client.py负责通信与恢复；kimi_transcript.py负责V2原子UTF-16更新；kimi_runtime.py及docs/kimi-runtime.md负责运行时准备/校验；main.py负责投影、归属、通知；相关client/integration/时间行/UI/live-smoke测试覆盖变更；本计划和审查报告保存证据。
- 审查：四透镜全部完成，24项逐项裁决见上文及报告；11个patch组已修复，其中high 4组、medium 7组（含测试补强）；2项未核实风险deferred，9项依记录理由驳回。未将被驳回/未证实场景扩成机制。
- 修复后engineer独立复验：17项协议/恢复/运行时/append/缓存/ACK/smoke测试通过，4项显式顺序/迟到回答/有界渲染/UI焦点测试通过，无新增失败。此前三项执行步骤失败已在完整原基线上同断言复现；未计为通过。
- followup_review_recommended: true。依据本轮修复4个high组；特定未验证风险是实际云端长任务在断连重连和未决发送后的整体行为，当前只有真实本地无凭据握手及隔离合成链路证据，不能宣称云端现场已验收。物理声音及长期运行亦待现场验收。
- 全程无日常MC重启、无打包、无日常数据改写或运行时安装；director已按neat-freak同步最终文档，Git提交及推送结果见收尾汇报。
