---
title: '修复 Kimi 权威完成判定、回答延迟展示与多聊天并发恢复'
type: 'bugfix'
created: '2026-09-07'
status: 'done'
baseline_revision: '3a811eede429dbd8f01ffe8ae012f1791f41b9e0'
baseline_commit: '3a811eede429dbd8f01ffe8ae012f1791f41b9e0'
review_loop_iteration: 6
followup_review_recommended: false
context:
  - '{project-root}/AGENTS.md'
warnings: [multiple-goals, oversized]
deferred: []
---

<intent-contract>

## Intent

**Problem:** MC 把同一 Kimi session 内子代理的 `turn.ended`、`prompt.completed` 和 `subagent.completed` 当作用户主任务完成，导致最终正文尚未生成就播放完成音效，并把执行过程提前写入回答列表；切换多个 Kimi 聊天时，又因运行态恢复、缺失 `turn.started` 的事件认领和 WebSocket 断线恢复不完整而丢失答案。

**Approach:** 建立“主代理权威完成”边界：子代理只贡献执行过程，回答正文只在主代理最终完成且正文非空后一次性公开并响铃；同时恢复每聊天 Kimi 运行态，允许无歧义的缺起始事件认领，增加 WebSocket 游标重连及 REST 终态对账，并为 Kimi 用户级全局指令写入简体中文要求。

## Boundaries & Constraints

**Always:** 以 `chat_id + session_id + turn_id + agent_id` 隔离生命周期和正文；后台聊天完成可持久化但不得抢焦点或无变化重绘；一个共享 Kimi 服务必须支持多个 session 并行；只有主代理权威最终正文落库后才新增回答项并播放一次完成音效；QA 完成后才进入 CR。

**Never:** 不用串行化或限制单聊天规避并发；不把子代理正文、思考、审批请求或空正文 completion 当最终回答；不因共享 WebSocket 一次瞬断就把所有活动聊天标失败；不修改 Codex/Claude/OpenClaw 路径，不回显日志中的凭据。

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| 子代理先结束 | 主 turn 运行中，子代理产生 delta、turn.ended、prompt.completed、subagent.completed | 仅更新 F1 执行过程；主 turn 仍 pending，回答列表无 AI 项且不响铃 | 丢弃其最终回答资格，保留结构化进度 |
| 主回答完成 | 主代理最终正文分片后产生权威 completion | 原子写入完整 `answer_md`，新增一个回答项并响铃一次 | 无正文则保持可恢复状态并启动对账，不伪造 done |
| 重复终态 | 主 turn.ended 后再到 prompt.completed 或重放事件 | 不重复写回答、不重复响铃 | 以完成身份幂等去重 |
| 两聊天交错 | 两个 session 使用相同 turnId，频繁 A→B→A 切换 | 各自状态、答案和音效严格隔离，均可继续执行 | 多候选时不猜归属，等待可验证事件或对账 |
| 缺 turn.started | 某 session 唯一 pending 本地 turn 收到带新 turnId 的正文/终态 | 首个 turn-scoped 事件认领该 turn 并正常完成 | 非唯一候选不认领 |
| WebSocket 瞬断 | 多个 session 活动时连接被 10054 关闭 | 自动重连并携带订阅和游标；恢复后终态 exactly-once | 重试失败后仅处理能确定所属且不可恢复的 turn |
| 全局语言规则 | Kimi Code 启动并读取用户级 AGENTS | Thinking、计划、过程说明和最终回答均使用简体中文 | 保留其他已有规则，不改 Codex 全局文件 |

</intent-contract>

## Code Map

- `kimi_server_client.py:193-360` -- `map_session_event()` 已保留 `agent_id`，但把所有 agent 的 `turn.ended`/`prompt.completed` 统一降级为 `turn_completed`；需区分主/子代理与 fallback 身份。
- `kimi_server_client.py:622-635` -- `list_messages()` 可用于终态对账；`session_exists()` 当前吞掉所有传输错误，须只把明确不存在视为 `False`。
- `kimi_server_client.py:681-687,802-989` -- 订阅、`client_hello`、接收循环和失效处理；当前 cursors 为空且 reader 断线后不自动恢复。
- `tests/fixtures/kimi_server_events.jsonl:2-3`、`docs/superpowers/specs/2026-08-10-kimicode-server-chat-design.md:108-126` -- cursor 是 `epoch + seq`，ack 可返回 `resync_required`；volatile delta 可共享 seq 并用 offset 区分，恢复必须遵守服务端游标协议而非自行猜测。
- `tests/fixtures/kimi_server_probe_notes.json:233-249`、`docs/superpowers/specs/2026-08-10-kimicode-server-chat-design.md:238-253` -- REST assistant 消息没有 turnId/promptId/agentId；prompt_id 等于提交产生的 user message id，需以该 user message 为 transcript 边界关联其后的主回答；steer 成功表示合并进活动 turn，失败才作为独立排队 prompt。
- `docs/cross-client-codex-kimicode-audit-2026-09-06.md:142-160` -- 已定义 `prompt_id → chat/session/turn` 的稳定归属、早到事件暂存及无歧义回退规则，实施时直接复用。
- `main.py:10293-10775` -- Kimi 消息入口、路由、回答缓冲和完成状态；当前正文键缺 `agent_id`，无 turnId completion 可覆盖整个 session。
- `main.py:10958-11140` -- UI 总入口；`server_request` 会误播完成音，`subagent_result` 会提前写回答，任意 completion 都可能结束主任务。
- `main.py:14305-14420,15069-15143` -- 聊天归档与切换；归档保存 Kimi 字段，但切回时未对称恢复。
- `tests/test_kimi_event_mapping_unit.py`、`tests/test_kimi_server_client_unit.py` -- 协议 agent 身份、cursor 重连、REST 错误分类和幂等队列测试入口。
- `tests/test_kimi_integration.py` -- 主/子代理交错、缺 start 认领、回答延迟公开、两聊天同 turnId 与恢复对账的外层回归入口。
- `tests/test_kimi_ui_responsiveness_automation.py`、`tests/test_main_unit.py` -- 音效 exactly-once、pending 无回答项、聊天切换 Kimi 五项状态恢复、后台无抢焦点/无效重绘。
- `tests/test_kimi_live_smoke.py` -- live smoke 终止条件必须只接受主代理权威 completion。
- `D:/code/cx/history/chat_history.db`、`C:/Users/gladwell/.kimi-code/server/events/*.jsonl` -- 只读现场证据；GB 服务端已完成但 MC 仍 pending，Android 子代理早于主任务结束。
- `C:/Users/gladwell/.kimi-code/AGENTS.md` -- Kimi 用户级全局指令真实位置；当前不存在，需新建并写入用户指定的第 2 条中文规则。

## Tasks & Acceptance

**Execution:**
- [ ] `kimi_server_client.py` -- 保留 agent 作用域完成语义：协议缺省 agentId 按已捕获约定视为 main，任何显式非 main 身份均不得权威完成；按协议跟踪每 session 的 `epoch + seq`，同 epoch 的旧稳定 seq 在映射前丢弃、同 seq volatile offset 全部保留；reader、pong 和普通 WS send 失败均进入单一恢复状态机，执行有界指数退避、重连和全量重订阅；恢复线程退出时若替代 reader 已再次断开，必须交接启动下一轮恢复；解析 ack 的 `resync_required` 并发出明确恢复事件；细分 REST 404 与传输错误。
- [ ] `main.py` -- 建立持久的 `prompt_id → chat/session/turn_idx/turn_id` 归属并用于 start/completion/REST 对账；子代理开始、正文、错误和终态只进 F1；主完成必须以稳定 owner、非空最终正文及已实际验证的空闲/权威终态为条件，绝不伪造 `idle_verified`；`prompt.completed` 仅在同 owner 未见 `turn.ended` 时 fallback；启动和 resync 对账同样必须等待空闲或权威终态。
- [ ] `main.py` -- REST 对账固定原 owner 并带退避/截止时间重试；真实 REST 消息缺 turnId/promptId 时，以 id 等于 prompt_id 的 user message 为 transcript 边界，仅提取其后、下一 user message 前的最终 assistant text，复用 session 中不得靠列表顺序或“最后一条”跨 prompt 猜测；截止仍 busy/空/异常时，无论是否带 original_error 都要把可证明 owner 置为失败，并优先显示原始传输/服务端错误。
- [ ] `main.py` -- steer 成功的 prompt 是活动服务端 turn 的 alias，不进入独立队列，主 turn 最终答案原子结算其所有本地 alias；steer 失败才进入 `kimi_request_queue`。活动 turn 完成但仍有服务端排队 prompt 时保持运行态，等待带 promptId 的 start/终态激活或精确结算下一 owner；精确 queued prompt 终态必须先移除其队列项再完成，禁止用活动 owner 的无 prompt 终态结算 queued owner。
- [ ] `main.py` -- 对称恢复每聊天 Kimi 五项运行态；启动、transport_error、resync 和进程退出都枚举活动及排队的每个 pending owner；启动共享 client/订阅失败进入同一有界重试并在截止时以原始错误结束可证明 owner；早到事件只要 prompt 所有权尚未落库就暂存（即使 session 已能解析到 chat），落库后按原序重放；缺 start 仅在 session 与 prompt/唯一 pending owner 可证明时认领。
- [ ] `kimi_server_client.py`、`main.py` -- CR3 并发加固：owner 主键固定为 `(chat_id, session_id, prompt_id)`，切换/启动时合并所有当前及归档聊天 owner 而非整表替换；owner 集合、cursor、socket 与恢复状态统一加锁。WebSocket 连接/握手有界，旧 reader 的迟到帧丢弃；同 epoch ack 不回退，epoch 只由当前连接的握手确认切换；服务进程重启必须清空旧 banner/token 并重新发现凭据，单次故障只通知一次。
- [ ] `main.py` -- CR3 完成门槛：idle 证明绑定 owner 与派发代次，并在提交/start/owner 切换时清零；不得由 delta/terminal 隐式合成 `turn.started`；迟到 `prompt.completed` 受同 owner 终态 tombstone 阻断；失败/中断原子清理 owner、alias、queue、buffer 与恢复意图。无 promptId 的 start 只在 session、问题文本和候选唯一一致时认领，重复问题不得猜队首，改由各自 prompt_id 的 REST transcript 边界对账。
- [ ] `main.py` -- CR3 恢复加固：REST 恢复按 session 共用单一协调器，覆盖当前/归档、活动/排队/尚未落地 owner；尚未落地时持久记录恢复意图，落地后继续对账。旧存档缺 prompt_id 时用问题文本、时间与 REST user message 无歧义迁移，否则截止后明确失败。steer alias 以最后一个 alias 的 transcript 边界取得最终正文；早到事件限制总键数但不得丢弃正文分片，正文按 offset 合并或转 REST 对账，晚到外来/已完成事件不缓存。
- [ ] `kimi_server_client.py` -- CR4 传输闭环：volatile delta 以 `(session, epoch, seq, agent, turn, stream, offset)` 跨 drain 幂等并按 offset 组装，传输中断后把受影响流标为不完整，未经 REST 完整正文对账不得用非空 partial 完成。subscribe/pong/send 均在锁外做有界 I/O；ack 必须校验 code、accepted/not_found/resync，订阅拒绝进入恢复。恢复线程退出使用 generation 原子交接；`submit_prompt` 缺 ID 明确报错；`steer_prompts` 只有明确业务拒绝才返回 False，超时/5xx 返回“结果未知”供对账。
- [ ] `main.py` -- CR4 提交事务：每 session 使用独立提交锁，必须先取得具体 active owner 才允许 steer；`submit_prompt` 或 steer 结果未知时保留 unresolved intent，以 REST user message 边界确认已接受/alias/queued 后再落地或失败。两个同 session 未落地提交的早到 prompt-less/prompt-bearing 事件均按 session、turn、offset 暂存并在 ID 落地后无歧义回放，子代理事件永不写主 owner 的 turn_id。
- [ ] `main.py` -- CR4 恢复事务：每 session 维护 requested generation，worker 退出前原子判断并交接新一轮；按 `owner_prompt_id` 分组且每组只对账一次，异步回调落地前复核 owner、tombstone 与 pending 状态。transport 已恢复而 session 仍 busy 时返回事件驱动监控，不得以短截止把健康长任务判失败；已闭合的较早 transcript 边界即使后续 queue 令 session busy 也可对账。所有调用共享 monotonic 总截止时间。
- [ ] `main.py` -- CR4 终态事务：prompt-less 事件若 owner 已有 turn_id 必须严格匹配；scope 只在 UI 线程且先校验 main 身份。完成音效按本次 finalized owner exactly-once 播放，不依赖整个聊天是否仍有 queue；pending 状态按 chat+session 过滤。错误从 alias 触发时清理 alias group 的 active owner；tombstone/volatile 去重表设有界回放窗口。
- [ ] `kimi_server_client.py` -- CR5 协议边界：已建立 epoch 后收到省略 epoch 的 sequenced 帧不得把 cursor epoch 清空；无 offset 的 `volatile:true` 状态帧不能用同一粗粒度 replay key 折叠，必须以足以区分 phase/state/usage 的稳定内容身份处理，无法证明幂等时宁可交给上层去重。订阅 ack 拒绝某 session 时必须从重订阅集合隔离该坏 ID，不能让它在每次重连时拖垮其他有效 session。HTTP 200 中非零应用 code 对 POST 仍按 5xx/超时语义标记 `result_unknown`。成功 reader 重连必须只发一次 `transport_recovered` 并携带实际 session 集合；同一 client 的服务进程重启必须验证重新发现 token。
- [ ] `main.py` -- CR5 提交与恢复：ambiguous steer owner 必须保留与当时 active owner 的候选 alias 关系和提交时间，REST 依据真实 user-message 边界决定它是 alias 还是 queued；重复问题也要能用时间/ID 无歧义迁移。恢复共享一个绝对 monotonic deadline，并把剩余预算传进 start/subscribe/status/messages 的每次阻塞调用；所有恢复成功/失败回调在 UI 线程落地前必须复核 owner generation、pending 状态和 tombstone，旧回调不得覆盖完成结果。恢复线程不得直接修改 chat/turn/UI dirty 状态。
- [ ] `main.py` -- CR5 正文与终态：显式非 main 的 assistant delta 只进入 F1，不能消失；`work_changed busy:false` 只有主代理/根 session 事件可生成 owner-generation idle 证明。offset 组装必须先检测缺口再决定是否完成，多 stream 按协议/首次出现顺序合并而非按字符串排序。alias group 的结算与 active 清理都用同一 `(chat_id, session_id, owner_prompt_id)`，失败/中断只更新错误状态，绝不播放“回答完毕”；聊天 Kimi 运行态只统计 Kimi pending owners。`/clear` 与运行态 rebuild 必须清除或裁剪已不存在的 owner、submission、buffer、early-event、recovery 状态，迟到事件不能复活已清理聊天。
- [ ] `tests/test_kimi_server_client_unit.py`、`tests/test_kimi_integration.py` -- CR5 回归必须覆盖：省略 epoch、同 seq 无 offset volatile 多 phase、拒绝一个订阅但其他 session 可恢复、HTTP 200 应用 5xx 的 POST 结果未知、reader 失败后成功重连并发出/消费一次 `transport_recovered`、同 client 进程重启换 token、ambiguous steer accepted/queued 两分支、重复问题 unresolved 重启迁移、deadline 贯穿阻塞调用、迟到失败回调、subagent assistant delta 进 F1、非 main idle 不授权 completion、offset 缺口、多 stream 顺序、alias turn.ended 清 active、失败/中断不响铃、clear/rebuild 不保留幽灵 owner。
- [ ] `tests/test_kimi_event_mapping_unit.py`、`tests/test_kimi_server_client_unit.py` -- 覆盖主/子代理开始、错误与完成、缺省 main/显式 unknown 身份、epoch/seq 多 session 单调游标、旧稳定事件丢弃、同 seq volatile、pong/reader/普通 send 失败、恢复线程交接、指数退避、重订阅、resync_required、错误分类和 exactly-once 队列。
- [ ] `tests/test_kimi_integration.py`、`tests/test_main_unit.py`、`tests/test_kimi_ui_responsiveness_automation.py` -- 复刻现场主/子代理交错、子代理 error、partial+prompt.completed+turn.ended、prompt 早到/steer 成功 alias/steer 失败排队/精确 queued completion/无 turnId transcript 边界、多聊天切换、transport_error/resync/进程退出对活动与排队 owner 的 REST 重试、真实 `_load_state()` 启动恢复和首次启动失败后重试；直接断言 pending 时无 AI 回答行、音效 exactly-once、另一聊天状态及焦点不变，并覆盖畸形非 dict turn 不导致分派异常。
- [ ] `tests/test_kimi_live_smoke.py` -- 将 live smoke 的完成条件收紧到主代理最终完成，避免子代理误判。
- [x] `C:/Users/gladwell/.kimi-code/AGENTS.md` -- 新建或合并用户级规则：`2. Thinking、计划、过程说明和最终回答都使用简体中文。`
- [ ] `_bmad-output/implementation-artifacts/tests/test-summary.md` -- 实现后先运行一轮 Kimi 映射、客户端、集成、UI 可访问性及主逻辑 QA，记录命令与结果，并将新增失败与已知 15 项基线分开判断。
- [ ] `_bmad-output/implementation-artifacts/spec-fix-kimi-authoritative-completion-and-concurrent-chat-recovery.md` -- QA 完成后运行一轮独立 CR，在 Review Triage Log 逐项记录 triage，修复确认问题并复验。

**Acceptance Criteria:**
- Given 主任务仍在执行且一个或多个子代理已经结束，when 用户查看回答列表或听取提示音，then 不出现 AI 回答项、不播放“回答完毕”，任务仍保持运行。
- Given 主代理生成完整最终正文并权威结束，when UI 消费终态，then 回答列表一次性显示完整结果、状态变为完成且只播放一次完成音效。
- Given 两个 Kimi 聊天交错执行并来回切换，when 任一聊天继续发送或后台完成，then 两者均收到自己的最终回答，另一聊天的 session、turn、队列、列表和焦点不受污染。
- Given WebSocket 瞬断或恢复事件缺少 `turn.started`，when连接恢复或首个可归属事件到达，then 唯一 pending turn 能继续并通过事件或 REST 对账完成，不永久停留“正在请求...”。
- Given Kimi Code 读取用户级全局规则，when生成 Thinking、计划、过程说明或最终回答，then内容使用简体中文。

## Spec Change Log

- 2026-09-07（CR 循环 1）：第一轮实现验证了“主代理权威完成、延迟发布回答、多聊天状态恢复”的总体方向，但独立审查发现恢复过程仍是一次性的，且 prompt/turn 所有权、无 turnId REST 消息、心跳失败、重同步与启动恢复等边界没有形成完整状态机。已回退第一轮代码，保留现场证据与设计结论，并把持续重试、稳定 owner、错误优先级、游标协议、启动重订阅和对应测试写入本规格后重新实现。
- 2026-09-07（CR 循环 2）：第二轮实现保留了主/子代理隔离、延迟公开、prompt owner、游标恢复和多聊天切换，但审查发现启动/resync 可发布未完成 REST 片段、恢复截止后仍可能永久 pending、真实 REST schema 无 turn/prompt 字段、steer alias 与 queued owner 状态机错误、早到事件丢失、活动/排队恢复覆盖不全、重连交接竞态及旧稳定事件重放。已回退第二轮代码；规格补充 transcript user-message 边界、实际 idle 证明、alias/queue 明确语义、全 pending owner 恢复、截止失败、恢复交接和旧序号过滤。KEEP：继续保留主/子代理权威边界、回答完成前隐藏、一次响铃、每 session cursor、REST 404 分类、五项聊天状态恢复、后台不抢焦点以及已验证的全局中文规则。
- 2026-09-07（CR 循环 3）：第三轮实现继续保留主/子代理隔离、延迟公开、真实 transcript 边界、steer alias、早到事件与 WS 自动恢复，但独立审查发现 owner 仍仅用 prompt_id 索引、聊天切换会替换全局 owner、idle 证明跨 prompt 复用、缺 start 会被任意终态隐式认领、失败未清理 owner、旧存档与重复问题可能永久 pending；客户端还存在无界连接占锁、重启复用旧 token、旧 reader 帧、cursor/epoch 回退和恢复通知重复等竞态。已回退第三轮代码；规格补充复合 owner、全聊天合并恢复、旧存档迁移、owner 代次 idle、严格 start、终态 tombstone、失败原子清理、按 session 协调恢复、alias 最后边界、早到正文无损合并，以及锁内 cursor/有界连接/重启换 token。KNOWN-BAD：禁止仅按 prompt_id 建索引、切换时整表替换、跨轮缓存 idle、从 delta/terminal 合成 start、按 prompt 启恢复线程或截断早到正文。KEEP：保留权威主代理边界、pending 无回答行、最终正文一次公开与一次响铃、子代理只进 F1、prompt_id→user message transcript 边界、steer 成功为 alias、后台不抢焦点、REST 404 分类及已验证的全局中文规则。
- 2026-09-07（CR 循环 4）：第四轮实现保留了复合 owner、generation idle、tombstone、归档恢复、WS generation 与单 session 恢复协调器，但审查证明提交、steer 与恢复仍不是完整事务：健康长任务会被短截止误判失败，alias 会重复对账并由迟到错误覆盖成功，子代理可污染主 turn_id，并发提交可同时成为 active，ambiguous POST/steer 会丢 owner；传输层仍缺 volatile offset 跨 drain 去重、锁外有界 send、ack 拒绝处理和恢复 generation 交接。已回退第四轮代码；规格补充 session 级提交锁与 tri-state 提交/steer、prompt-less 早到缓存、main-first scope、按 owner group 单次对账、transport-recovered busy 监控、closed-boundary 对账、全局 monotonic deadline、流完整性证明、按 owner 响铃及有界 tombstone。KNOWN-BAD：禁止把 busy 当恢复失败、逐 alias 对账、异常一律当 steer 拒绝、无 concrete owner steer、在生命周期锁内网络发送、只按非空 buffer 判最终正文。KEEP：继续保留前三轮所有 KEEP，尤其 pending 无回答行、主终态一次公开、复合 owner、旧存档迁移、后台不抢焦点、真实 transcript 边界、服务重启换 token 与全局中文规则。
- 2026-09-07（CR 循环 5）：第五轮实现保留了 session 提交锁、tri-state POST/steer、复合 owner、早到事件、offset 组装、transport-recovered 与单 session 对账，但独立审查确认仍有权威边界和事务缺口：失败/中断会播放“回答完毕”，非 main idle 可授权 fallback，子代理正文反而从 F1 消失；ambiguous steer 丢失 active alias 关系，alias 终态无法清 active，迟到恢复错误可覆盖已完成结果；无 offset volatile 状态被折叠、省略 epoch 会重置 cursor、坏订阅会导致全体 session 重连死循环，应用层 POST 5xx 也未标记结果未知。已回退第五轮代码；规格补充上述 CR5 协议、提交、恢复、正文、终态与生命周期清理约束及精确回归。KNOWN-BAD：禁止对失败/中断响完成音、让非 main work_changed 生成 idle proof、丢弃子代理 assistant delta、把 ambiguous steer 当独立 unresolved owner、按 alias turn_idx 清 active、先完成检查后才发现 offset 缺口、用无 session 的 alias group、保留拒绝订阅 ID 重连。KEEP：继续保留前四轮所有 KEEP，尤其回答完成前隐藏、主代理完整正文一次发布、双聊天复合隔离、prompt transcript 边界、健康 busy 事件驱动、锁外有界 WS I/O、全局中文规则以及第五轮已通过的 194 项 Kimi 定向测试结构。

## Review Triage Log

| # | Reviewer | Finding | Verdict | Route / Rationale |
|---|----------|---------|---------|-------------------|
| B1 | Blind Hunter | REST 终态对账遇到 busy/空结果后只尝试一次，turn 可能永久 pending | high | bad_spec：恢复必须在截止时间内持续重试，并保留恢复意图。 |
| B2 | Blind Hunter | `transport_error` 对所有活动 turn 只做一次 `require_idle` 对账并丢弃原始错误 | high | bad_spec：固定 owner、重试对账，超时后以原始传输错误失败。 |
| B3 | Blind Hunter | reader 断线只立即重连一次，没有退避和后续重试 | high | bad_spec：统一到单一、有界指数退避的恢复循环。 |
| B4 | Blind Hunter | pong 发送失败只使连接失效并返回，不触发重连 | high | bad_spec：reader 与 pong 失败必须进入同一恢复循环。 |
| B5 | Blind Hunter | 仅以 seq 保存游标，可能遗漏同 seq 的 volatile offset 后缀 | maybe-false | defer：服务端 ack 的 cursor 协议以 epoch+seq 为准；用协议夹具和真实 ack 行为约束，不自创 offset 游标。 |
| B6 | Blind Hunter | 指定 turn 对账时仍接受无 turnId 消息，可能发布其他轮答案 | high | bad_spec：复用 session 时不得用无 turnId 消息猜测指定轮归属。 |
| B7 | Blind Hunter | 空 turn 的 answer buffer 可匹配任意 completion，且 finalize 前先弹出 | high | bad_spec：buffer 和 completion 必须绑定稳定 owner，只有原子完成后清理。 |
| B8 | Blind Hunter | `turn.started` 盲目弹出本地队首，延迟事件会错配排队请求 | medium | bad_spec：显式区分服务端活动 owner 与本地排队请求，不按到达顺序猜测。 |
| B9 | Blind Hunter | `prompt.completed` 带 prompt_id，但主逻辑未用它选择 owner | high | bad_spec：建立并持久维护 prompt_id 所有权映射。 |
| B10 | Blind Hunter | 仅在恰好一个 pending 时恢复 active turn，活动加排队场景恢复失败 | medium | bad_spec：启动恢复应根据 prompt/服务端状态重建唯一 active，其余保持 queued。 |
| B11 | Blind Hunter | 启动只恢复字段，没有启动共享 client、重订阅或安排对账 | medium | bad_spec：启动恢复必须完整重建传输和 pending 对账。 |
| B12 | Blind Hunter | failed/interrupted 时用部分正文覆盖服务端错误 | high | bad_spec：错误终态优先展示原始服务端错误，部分正文不可伪装成失败原因。 |
| B13 | Blind Hunter | QA 摘要在主逻辑仍有 15 个失败且未跑 live 时宣称完成 | false | reject：15 项均与本改动无关且与已记录基线逐项一致；live smoke 为可选、会触发真实服务。 |
| B14 | Blind Hunter | 用户级 AGENTS 在仓库 diff 外，fresh checkout 不可复现 | false | reject：用户明确要求修改本机 Kimi 全局文件；该文件不属于 MC 仓库，已用精确内容检查验收。 |
| E1 | Edge Hunter | REST 消息若按新到旧返回，当前反转可能选到最旧答案 | maybe-false | defer：顺序契约未确认；实现不得依赖列表顺序，应优先用稳定 owner/turn 标识筛选。 |
| E2 | Edge Hunter | REST 多轮回复且缺 turnId 时可能发布上一轮答案 | high | bad_spec：与 B6 合并，禁止在复用 session 中猜测无归属答案。 |
| E3 | Edge Hunter | 对账进行中若新 prompt 成为 active，旧轮可能错误完成到新 owner | high | bad_spec：每次对账从创建到结束必须固定原 owner。 |
| E4 | Edge Hunter | pong 失败没有恢复 | high | bad_spec：与 B4 合并到统一恢复循环。 |
| E5 | Edge Hunter | 同 seq volatile offset 可能在重连时丢失 | maybe-false | defer：与 B5 合并，按服务端 cursor/resync 协议测试。 |
| E6 | Edge Hunter | `turn.ended` 与无 turnId `prompt.completed` 使用不同完成键，可能二次完成并响铃 | high | bad_spec：幂等键优先使用 session+prompt+agent，turn 只作已证明归属的后备。 |
| E7 | Edge Hunter | 对账仅一次 | high | bad_spec：与 B1 合并，加入退避、截止时间与确定终态。 |
| E8 | Edge Hunter | ack 中的 `resync_required` 被丢弃 | high | bad_spec：显式解析并触发固定 owner 的 REST/ops 重建。 |
| E9 | Edge Hunter | 无 exact/active 元数据时，唯一 pending follow-up 仍无法认领 | false | reject：正常运行中唯一 active 元数据路径已有覆盖；启动缺失所有权由 B10/B11 单独修复。 |
| V1 | Verification Gap | 未验证多个 session 的 epoch+seq 游标分别单调前进 | medium | bad_spec：新增多 session 游标与重订阅测试。 |
| V2 | Verification Gap | 未从 UI 消息入口覆盖 `transport_error` 到 REST 对账 | medium | bad_spec：新增外层恢复与错误保留测试。 |
| V3 | Verification Gap | 启动恢复测试未清空并重建 active turn 状态 | medium | bad_spec：新增干净启动、活动加排队恢复测试。 |
| V4 | Verification Gap | 对账一次性失败缺少测试 | high | bad_spec：新增 busy、空结果、瞬时失败后成功及截止失败测试。 |
| I1 | Intent Alignment | 只断言列表不变，没有明确断言 pending 时无 AI 行 | false | reject：既有 `test_render_answer_list_hides_requesting_placeholder_until_done` 与新增列表快照共同覆盖该可见行为。 |
| I2 | Intent Alignment | 主代理 partial 后先到 `prompt.completed`、再到 delta/`turn.ended` 可能提前完成 | high | bad_spec：fallback 必须确认同 owner 尚无 turn.ended、session 空闲且正文完整；新增乱序测试。 |
| I3 | Intent Alignment | “同时运行一个程序”也可能指多个 MC 进程 | false | reject：用户给出的“同一程序内 gb/安卓两个聊天室”现场明确了本次范围是多聊天并发。 |
| I4 | Intent Alignment | 未把修复部署回 `D:/code/cx/mc` 打包目录或保存现场日志 | false | reject：该目录被用户指定为只读诊断现场，没有授权覆盖已打包程序；源码修复和测试是交付范围。 |
| I5 | Intent Alignment | 多聊天仅合成测试，未做真实 Kimi 端到端 | low | reject：真实服务测试会产生外部任务；确定性集成测试足以作为必需 QA，live smoke 保留为可选验证。 |
| I6 | Intent Alignment | 音效和列表通过 mock/外层对象验证，未听真实音效或操作真实 UI | false | reject：音效调用次数与 wx 列表内容是精确、可重复的外部可观察边界。 |
| I7 | Intent Alignment | live smoke 未运行 | low | reject：live smoke 非用户明确要求，且会改变真实服务状态；不作为自动 QA 阻断项。 |
| I8 | Intent Alignment | 全局 AGENTS 不在仓库 diff | false | reject：与 B14 相同，本机用户级配置通过独立文件检查验收。 |
| I9 | Intent Alignment | 审查时 triage 为空且状态仍为 in-review | false | reject：这是审查器读取进行中工件的时序现象；本日志在审查汇总阶段补齐。 |

### 2026-09-07 — Review pass 2
- verdicts: 30 findings — high 17, medium 3, low 3, false 7, maybe-false 0
- findings:
  - `[high]` `[bad_spec]` 启动与 resync 使用 `require_idle=False`，可把活动中的 REST 片段伪装成已验证最终答案 — 已补充所有恢复入口必须取得真实 idle/权威终态证明，且禁止伪造 `idle_verified`。
  - `[high]` `[bad_spec]` 六次对账后无原始错误时静默返回，turn 仍永久 pending — 已补充截止后无论有无 original_error 都必须确定失败并给出恢复错误。
  - `[high]` `[bad_spec]` 真实 REST assistant 无 turnId/promptId，复用 session 的后续轮无法恢复，而测试使用了现场不存在的字段 — 已补充以 prompt_id 对应 user message 为 transcript 边界关联回答。
  - `[high]` `[bad_spec]` 忽略 `steer_prompts()` 返回值并总是排队，成功 steer 的本地行会永久 pending — 已明确成功 steer 是活动 owner 的 alias，失败才独立排队。
  - `[high]` `[bad_spec]` 活动轮完成时仍有 queue 却清空运行态，允许新请求越过旧队列 — 已明确 pending queue 存在时保持运行并按 prompt owner 激活下一轮。
  - `[high]` `[bad_spec]` queued turn 因队列项仍在而拒绝精确 completion/abort/REST 终态 — 已明确精确 owner 终态先移除自身队列项再结算。
  - `[high]` `[bad_spec]` chat 可由 session 解析但 prompt 尚未落库时，早到 completion 被丢弃而未暂存 — 已扩展早到缓冲条件为“prompt owner 尚未持久化”。
  - `[high]` `[bad_spec]` transport/resync/process-exit 只遍历 active owner，排队 prompt 在事件缺口中永久 pending — 已要求所有恢复入口枚举活动与排队的每个 pending owner。
  - `[high]` `[bad_spec]` 启动 client/subscribe 首次异常被吞掉，恢复聊天不再重试 — 已纳入有界启动恢复并在截止后保留原始错误。
  - `[high]` `[bad_spec]` 替代 reader 在既有恢复线程存活时再次失败会丢失恢复唤醒 — 已要求恢复线程退出时交接并在仍断开时启动下一轮。
  - `[high]` `[bad_spec]` 旧稳定 seq 虽不更新 cursor 仍被映射入队，重连重放会重复正文 — 已要求在事件映射前丢弃同 epoch 的旧稳定 seq，同时保留同 seq volatile offset。
  - `[false]` `[reject]` 缺 agentId 的子代理终态会被当主代理 — 捕获协议中 main 明确使用 main 或缺省根身份，子代理均带显式 agentId；显式未知身份已非权威，审查未证明无身份子代理可达。
  - `[medium]` `[bad_spec]` 普通 WS send 失败没有进入恢复循环，短时故障后共享连接可持续断开 — 已把 subscribe/abort 等普通发送失败纳入统一恢复状态机。
  - `[false]` `[reject]` Edge：缺 agentId 的子代理终态会权威完成 — 与本轮第 12 项相同，协议现场反证其触发前提。
  - `[high]` `[bad_spec]` Edge：旧稳定序号事件仍会进入回答缓冲 — 与本轮第 11 项同根，已补充映射前过滤和回归测试。
  - `[high]` `[bad_spec]` Edge：替代 reader 二次失败时恢复线程竞态导致无人重连 — 与本轮第 10 项同根，已补充恢复交接约束。
  - `[high]` `[bad_spec]` Edge：启动订阅瞬时失败会永久搁置恢复 turn — 与本轮第 9 项同根，已补充启动退避与截止失败。
  - `[low]` `[patch]` 后台 completion 指向非 dict turn 时 `finalized` 未初始化，可中断事件分派 — 最小修复是在分支前初始化空列表并加畸形状态测试；因 bad_spec 回环，本项随重新实现一并保留。
  - `[high]` `[bad_spec]` Edge：启动/resync 未检查 idle 即发布 REST 内容 — 与本轮第 1 项同根，已补充真实终态证明。
  - `[high]` `[bad_spec]` Edge：对账耗尽且无原始错误时永久 pending — 与本轮第 2 项同根，已补充确定截止行为。
  - `[medium]` `[patch]` `_load_state()` 到恢复 helper 的真实调用路径缺少测试 — 预验证缺口成立；已要求通过真实 `_load_state()` 断言启动、订阅与对账，随回环重新实现。
  - `[high]` `[bad_spec]` transport/resync 恢复未覆盖 queued prompt owner — 与本轮第 8 项同根，已扩展恢复所有权集合和测试。
  - `[medium]` `[patch]` 子代理 error 隔离没有保护性测试 — 实现方向正确但删除映射分支不会触发现有失败；已加入子代理错误回归要求。
  - `[false]` `[reject]` 意图审查：测试未播放真实音效 — 音效调用点与次数是精确可观察边界，自动 QA 无需真的播放声音。
  - `[low]` `[reject]` 意图审查：未在 `D:/code/cx/mc` 打包成品做双聊天 E2E — 该目录仅获授权作只读取证，覆盖打包部署的改动成本与本次源码修复不相称。
  - `[false]` `[reject]` 意图审查：“同时运行一个程序”也要求两个 MC 进程 — 用户的 gb/安卓示例明确指向同一 MC 内多聊天并发。
  - `[low]` `[reject]` 意图审查：协议恢复只用 fake、未运行 live smoke — live 会创建真实外部会话，用户未要求该副作用；确定性传输与集成测试是本轮 QA 边界。
  - `[false]` `[reject]` 意图审查：仓库 diff 不能证明 Kimi 全局 AGENTS — 用户级文件不属于仓库，但已在本机做精确存在和内容检查。
  - `[false]` `[reject]` 意图审查：diff 不能证明 skill 且 CR 状态未完成 — skill 调用记录在运行流程中，审查读取时 CR 正在执行，属于时序现象。
  - `[false]` `[reject]` 意图审查：QA 不是零失败且未跑 live — 15 项与冻结基线逐项相同且不在本改动路径，live 为有外部副作用的可选测试。

### 2026-09-07 — Review pass 3
- verdicts: 37 findings — high 16, medium 12, low 1, false 7, maybe-false 1
- findings:
  - `[false]` `[reject]` Blind：显式 null/空 agentId 会被提升为 main — carried：与 pass 2 的缺失 agentId 结论相同；现场协议中子代理均显式携带非 main 身份，未证明无身份子代理可达。
  - `[maybe-false]` `[defer]` Blind：volatile 同 seq 缺少 epoch/seq/offset 去重，重连可能重复 delta — carried：服务端只承诺 epoch+seq cursor，是否重放同 seq offset 需真实 ack/resync 行为才能判定；本轮 bad_spec 回环下不单独延期写入。
  - `[medium]` `[bad_spec]` Blind：任意不同 epoch 都被当作当前 epoch，迟到旧 epoch 可回滚 cursor — 当前实现确会覆盖不同 epoch；已补充 epoch 只能由当前连接握手/ack 确认，迟到旧 reader/旧 epoch 事件须丢弃。
  - `[medium]` `[bad_spec]` Blind：cursor 字典在接收线程与恢复线程间无统一锁 — `_accept_session_event` 无锁读写共享字典；已补充 cursor/socket/恢复状态统一使用生命周期锁。
  - `[medium]` `[bad_spec]` Blind：进程已退出时一次 socket 故障可重复入队 `transport_error` — `_invalidate_ws` 先入队，`_request_recovery` 的 dead-process 分支会再入队；已要求单次故障只通知一次。
  - `[high]` `[bad_spec]` Blind：WebSocket `timeout=None` 且连接时持生命周期锁，使有界恢复和 close 都可能无限阻塞 — 调用路径持锁进入无界网络连接；已要求连接/握手超时有界且不得长时间占锁。
  - `[high]` `[bad_spec]` Blind：服务进程重启复用旧 token/banner，可能无法认证新进程 — `start()` 仅在 token 为 None 时发现凭据；已要求重启清空旧 banner/token 并重新发现。
  - `[high]` `[bad_spec]` Blind：prompt owner 仅以 prompt_id 为键，跨 chat/session 碰撞会覆盖 — `_kimi_prompt_owners` 的键确为单一字符串；已将复合 `(chat_id, session_id, prompt_id)` 写成强制主键。
  - `[high]` `[bad_spec]` Blind：聊天切换重建 owner 时整表替换，丢失其他聊天尚未落地 owner — `_rebuild_kimi_runtime_state` 确会替换整个映射；已要求扫描合并当前与全部归档聊天。
  - `[medium]` `[bad_spec]` Blind：旧存档 pending turn 缺 prompt_id 时被跳过且没有迁移 — 恢复只注册有 prompt_id 的行；已补充按问题/时间/REST user message 无歧义迁移与截止失败。
  - `[high]` `[bad_spec]` Blind：`kimi_idle_verified` 跨 prompt 缓存且新派发不清零，旧空闲证明可导致提前发布 — 字段只在 work_changed 更新；已要求绑定 owner/派发代次并在 submit/start/切换 owner 时清零。
  - `[high]` `[bad_spec]` Blind：重复 queued 问题文本无法唯一认领，两个请求可能永久 pending — 当前唯一文本匹配失败后没有保证后续恢复；已要求不猜归属并用各自 prompt_id 的 REST transcript 边界持续对账。
  - `[high]` `[bad_spec]` Blind：任意权威 turn-scoped 事件都会隐式合成 start，可能把下一轮 delta 盖到上一 owner — UI 入口确在非 start 事件上调用 `_apply_kimi_turn_started`；已明确只有可证明的 `turn.started` 可认领。
  - `[high]` `[bad_spec]` Blind：`_apply_kimi_error` 未移除 owner/queue，迟到事件仍可完成失败 turn 并污染后续 steering — 错误路径仅清 active metadata；已要求失败原子清理全部 owner 状态并写终态 tombstone。
  - `[medium]` `[bad_spec]` Blind：finalize 无锁遍历 `_kimi_prompt_owners.values()`，可与恢复线程修改并发异常 — 该遍历发生在锁外；已要求所有 owner 集合遍历与修改共用一把锁。
  - `[medium]` `[bad_spec]` Blind：早到事件缓存只限制每桶、不限制键数，外来 prompt 可无界占用内存 — 缓存可为无限 prompt_id 建桶；已要求限制总键数且拒绝晚到外来/已完成事件。
  - `[false]` `[reject]` Blind：按字符串排序 `created_at` 会错排数字时间戳 — 真实 REST 夹具使用等宽 ISO-8601 字符串，词典序与时间序一致，审查未证明数字 schema 可达。
  - `[medium]` `[bad_spec]` Blind：每个 pending prompt 启一个恢复线程，故障时形成 REST/线程突发并互相竞态 — 恢复入口确逐 owner 启线程；已要求每 session 一个共享恢复协调器。
  - `[medium]` `[bad_spec]` Edge：旧 reader 在新 socket 安装后仍可能交付刚返回的帧 — recv 返回后到映射前没有再次核验 socket 身份；已要求映射前丢弃替代连接的迟到帧。
  - `[medium]` `[bad_spec]` Edge：同 epoch 的较低 ack 会回退 session cursor — `_handle_ack` 无条件覆盖 `_session_cursors`；已要求 ack 单调前进。
  - `[high]` `[bad_spec]` Edge：`volatile:true` 的非 delta 同序帧会被当稳定重放丢弃 — 现场夹具明确存在同 seq 的 volatile `agent.status.updated`，当前判断忽略顶层 volatile；已要求所有 volatile 帧保留并测试。
  - `[high]` `[bad_spec]` Edge：跨 session 相同 prompt_id 会覆盖 owner — 与 Blind 的复合键缺陷相同，代码仅用 prompt_id；已补充复合 owner 与碰撞回归。
  - `[high]` `[bad_spec]` Edge：owner 落地前超过 32 个事件会丢弃开头 delta，最终答案截断 — 每桶 deque 上限 32 会静默丢头；已要求正文 offset 合并或转 REST 对账，禁止丢正文。
  - `[high]` `[bad_spec]` Edge：前一轮 idle 证明未在下一 prompt 开始时清零，可提前接受 fallback — 与 Blind 的 owner 代次缺陷相同；已补充代次绑定与清零测试。
  - `[high]` `[bad_spec]` Edge：steer alias 间已有 assistant 文本时，按原 owner 边界可能取到中间回答 — 对账固定旧 owner_prompt_id，不能保证最后 alias 后的正文；已要求以最后 alias 的 transcript 边界读取最终回答并原子结算 aliases。
  - `[high]` `[bad_spec]` Edge：transport 恢复耗尽发生在 owner 尚未落地时，恢复意图丢失 — 当前只枚举已注册 owner；已要求未落地 owner 持久记录恢复意图并在落地后继续。
  - `[high]` `[bad_spec]` Edge：缺 promptId start 可先覆盖 active owner 的 turn_id，再检查唯一 queued 候选 — 当前认领顺序允许 active metadata 先被更新；已要求 start 在完整唯一性验证后才原子绑定且不得覆盖 active。
  - `[high]` `[bad_spec]` Edge：`turn.ended` 后迟到 `prompt.completed` 缺少同 owner tombstone，可再次用 partial 发布 — 幂等身份会因有无 turn_id 不同而变化；已要求同复合 owner 终态 tombstone 阻断所有迟到 fallback。
  - `[medium]` `[bad_spec]` Verification：缺少同 session epoch rollover 的验证 — 预验证缺口成立；已加入确认新 epoch、拒绝迟到旧 epoch及 cursor 单调测试要求。
  - `[medium]` `[bad_spec]` Verification：reader/pong 故障测试未验证成功自动恢复、`transport_recovered`、重订阅和 cursor replay — 预验证缺口成立；已加入各入口端到端恢复状态机断言。
  - `[medium]` `[bad_spec]` Verification：重启恢复只测当前 chat，未从真实加载路径恢复归档 pending chat — 预验证缺口成立；已加入当前与归档聊天的 `_load_state()` 恢复测试。
  - `[false]` `[reject]` Intent：没有操作真实 GUI/播放真实声音 — 自动化已在用户可见边界验证列表内容及音效调用次数，真实播放不是确定性 QA 的必要条件。
  - `[false]` `[reject]` Intent：没有部署到 `D:/code/cx/mc` 打包目录 — 用户只授权从该目录读取日志和记录以诊断，未要求覆盖成品。
  - `[low]` `[reject]` Intent：没有使用真实 gb/安卓会话做 live 双聊天 — live 会创建外部任务且用户未要求该副作用；确定性集成测试覆盖同样的 session/chat 交错边界，额外复杂度不值得。
  - `[false]` `[reject]` Intent：未实现两个 MC 进程同时运行 — 用户示例明确是同一 MC 程序内两个聊天室，不是多进程需求。
  - `[false]` `[reject]` Intent：全局 AGENTS 不在仓库 diff — 该用户级配置本就位于仓库外，已用本机精确内容检查验收。
  - `[false]` `[reject]` Intent：15 项基线失败、in-review 状态和 CR 未勾选表示交付未完成 — 审查读取的是 CR 进行中的工件，15 项与冻结基线一致；状态与勾选将在审查收敛后更新。

### 2026-09-07 — Review pass 4
- verdicts: 40 findings — high 22, medium 10, low 2, false 6, maybe-false 0
- findings:
  - `[high]` `[bad_spec]` Blind：无 prompt 事件只按 session 唯一 active owner 归属，不核对双方已有 turn_id，迟到旧终态可完成下一轮 — 已补充两边 turn_id 存在时必须严格相等。
  - `[high]` `[bad_spec]` Blind：事件在 WS 线程先做 scope 且未先验 authority，子代理 turn_id 可写入主 owner — 已要求 scope 仅在 UI 线程执行并先拒绝非 main 的身份写入。
  - `[high]` `[bad_spec]` Blind：HTTP 返回 prompt_id 前到达的 prompt-less start/delta 既不能认领也不缓存 — 已补充以 session+submission intent 暂存并在 ID 落地后回放。
  - `[high]` `[bad_spec]` Blind：瞬断后 session 持续 busy 约十秒即把健康长任务标失败 — 已区分 transport 已恢复的 busy 监控与不可恢复失败，busy 返回事件驱动状态而非倒计时失败。
  - `[high]` `[bad_spec]` Blind：session 恢复 worker 结束窗口收到的新恢复请求会丢失交接 — 已补充 per-session requested generation 和退出原子 handoff。
  - `[high]` `[bad_spec]` Blind：active owner 与 steer aliases 被分别对账，异步迟到 error 可覆盖已完成答案 — 已要求按 owner_prompt_id 分组单次对账，回调落地前复核 pending/tombstone。
  - `[high]` `[bad_spec]` Blind：POST 超时但服务端已接受时 finally 删除 pending submission，后续事件无 owner — 已补充 ambiguous POST 的 unresolved intent 与 REST user-message 确认。
  - `[high]` `[bad_spec]` Blind：`steer_prompts` 把超时/503 一律当明确拒绝，已接受 alias 会被误建为 queue — 已把 steer 结果改为接受/拒绝/未知三态，未知走对账。
  - `[high]` `[bad_spec]` Blind：没有具体 active owner 时仍可能依据布尔状态 steer，生成空 owner_prompt_id alias — 已要求 concrete owner 是 steer 前置条件。
  - `[high]` `[bad_spec]` Blind：同 session 的提交/steer/owner 注册缺少事务锁，并发远端请求可同时成为 active — 已补充每 session 独立提交锁，保留跨 session 并行。
  - `[high]` `[bad_spec]` Blind：volatile exact replay 缺跨 drain offset 去重，答案会重复拼接 — 已要求持久的复合 offset 幂等键和绝对 offset 组装。
  - `[low]` `[reject]` Blind：注入测试用 `ws_factory` 可无限阻塞 — 生产连接已使用有界 timeout；为测试注入器包装硬截止会增加线程泄漏复杂度，日常用户不可达。
  - `[high]` `[bad_spec]` Blind：subscribe 与 pong 持生命周期锁执行 socket send，阻塞发送会卡住 close/recovery — 已要求锁内只快照 socket/generation，网络 I/O 在锁外且有界。
  - `[high]` `[bad_spec]` Blind：ack 的 code、accepted/not_found 未校验，订阅拒绝会被伪装成健康 — 真实夹具证明这些字段存在；已补充拒绝重试与 resync/reconcile。
  - `[medium]` `[bad_spec]` Blind：只有整个 chat 无 pending 时才响铃，前一个已完成回答在还有 queue 时无音效 — 已把音效幂等绑定 finalized owner，而非 chat idle。
  - `[medium]` `[bad_spec]` Blind：每次 rebuild 为全部历史完成 turn 加 tombstone 且不裁剪 — 已要求 tombstone 保留有界 replay 窗口。
  - `[medium]` `[patch]` Blind：live smoke 仍把 main `prompt.completed` 当权威终止，即使无完整正文/idle 证明 — 最小修复为只接受 main `turn.ended` 或带终态证明的 REST 完成并要求非空正文；因 bad_spec 回环随重实现保留。
  - `[medium]` `[patch]` Edge：submit 响应缺 prompt_id/user_message_id 时返回空串而非明确失败 — 当前确会留下不可恢复 active placeholder；最小修复是抛出带上下文的 `KimiServerError`，随回环保留。
  - `[high]` `[bad_spec]` Edge：steer 服务端已成功但响应丢失时被建为独立 queue — 与 Blind 三态 steer 同根，已补充未知结果对账。
  - `[high]` `[bad_spec]` Edge：replacement reader 在最终 handoff 检查后、worker 真正退出前失败仍可无人接管 — 当前存在窄窗口；已补充 recovery generation 原子清权与交接。
  - `[high]` `[bad_spec]` Edge：active owner 的无锁查找与 completion 并发，可能 steer 到刚结束 owner — 已合并到 session 提交事务锁及 owner 锁要求。
  - `[high]` `[bad_spec]` Edge：两个同 session submission 都未落地时，早到带 prompt 的终态因候选不唯一直接丢弃 — 已补充未落地事件桶和 ID 返回后的确定绑定。
  - `[medium]` `[bad_spec]` Edge：unlanded owner 的早到流以事件 list 无界增长，没有 offset 合并或强制 REST — 已要求按 offset 合并并在容量门槛转 mandatory REST。
  - `[medium]` `[bad_spec]` Edge：聊天切换与恢复线程读取 landed/turn 状态没有共同快照，owner 可被跳过并清掉恢复意图 — 已要求 owner 与目标 turn 在同一锁/快照中决定。
  - `[false]` `[reject]` Edge：REST 时间戳缺失、混合宽度或等价时区会错选边界 — 真实 schema 夹具使用统一 ISO-8601，且归属主键是 user message id；未证明混合格式可达。
  - `[high]` `[bad_spec]` Edge：每 owner 内嵌 start 重试和多次 REST timeout，名义截止可延长到数分钟 — 已要求一个 monotonic 总 deadline 并限制每次调用和 sleep。
  - `[high]` `[bad_spec]` Edge：后续 queued work 令 session busy 时，已闭合的前一 prompt 边界仍被等到截止并判失败 — 已允许对明确闭合 transcript 边界单独完成，不能用整 session busy 否定前一 owner。
  - `[high]` `[bad_spec]` Edge：assistant delta 乱序或非相邻重放按到达顺序追加 — 已要求用协议 offset 排序、重叠去重后组装。
  - `[high]` `[bad_spec]` Edge：断线丢了一部分 volatile delta 后，迟到 terminal 可把非空残片当完整答案发布 — 已要求传输故障标记流不完整，须经 REST 完整对账才能完成。
  - `[medium]` `[bad_spec]` Edge：finalize 按 session 查询所有 chat pending，另一 chat 同 session 会让已完成 chat 保持运行且不响铃 — 已要求 pending 与 active 计算同时过滤 chat_id+session_id，响铃按 owner。
  - `[high]` `[bad_spec]` Edge：alias 错误无 turn_id 时用 alias turn_idx 清 active metadata 失败，后续请求 steer 到死 turn — 已要求按 alias group 的 active owner 索引清理。
  - `[medium]` `[patch]` Verification：未测试 reader recv 失败→自动恢复成功→重订阅/cursor replay→单个 `transport_recovered` — 预验证缺口成立；已加入精确客户端回归要求。
  - `[medium]` `[patch]` Verification：未测试 `session_exists` 瞬时 503 后成功并复用 session、只提交一次 — 预验证缺口成立；已加入集成回归要求。
  - `[medium]` `[patch]` Verification：未测试 create_session 成功而首次 subscribe 失败仍保留 session 与订阅恢复意图 — 预验证缺口成立；已加入客户端回归要求。
  - `[false]` `[reject]` Intent：未操作真实 GUI 或播放真实音效 — wx 列表内容和音效调用次数是确定、可重复的用户可见边界，实际发声不增加逻辑置信度。
  - `[false]` `[reject]` Intent：未部署到 `D:/code/cx/mc` 打包成品 — 该目录只获授权作现场日志/记录读取，用户未要求覆盖成品。
  - `[low]` `[reject]` Intent：未使用真实 gb/安卓会话做 live 双聊天 — live 会创建外部任务且用户未授权该副作用；确定性多 session 集成测试是合适边界。
  - `[false]` `[reject]` Intent：未实现两个独立 MC 进程 — 用户示例明确是同一 MC 内两个聊天室。
  - `[false]` `[reject]` Intent：仓库 diff 不含 Kimi 用户级 AGENTS — 该文件本来就在仓库外，已通过本机精确内容检查验收。
  - `[false]` `[reject]` Intent：规格仍 in-review、CR 未勾选且有 15 项失败，不能证明流程完成 — 审查读取时流程尚在 CR；15 项与冻结的无关基线一致，最终状态在收敛后更新。

### 2026-09-07 — Review pass 5
- verdicts: 37 findings — high 13, medium 13, low 2, false 9, maybe-false 0
- findings:
  - `[medium]` `[bad_spec]` Blind：同 seq、无 offset 的 volatile `agent.status.updated` 只用粗粒度键去重，不同 phase/state 会被折叠 — 真实状态帧可共享 seq 且差异位于嵌套 payload；已补充以稳定内容身份区分或把无法证明幂等的帧交上层处理。
  - `[high]` `[bad_spec]` Blind：订阅 ack 拒绝一个 session 后仍把该 ID 留在重订阅集合，重连会反复失败并拖垮其他 session — 代码在抛错前未隔离 rejected ID；已补充坏订阅隔离与其他 session 继续恢复。
  - `[high]` `[bad_spec]` Blind：HTTP 200 中非零应用 code 的 POST 未标记 `result_unknown` — 服务端可能已接受提交但响应以应用 5xx 表达，当前会把歧义结果当确定失败；已补充应用 code 的 POST 三态分类。
  - `[high]` `[bad_spec]` Blind：ambiguous steer 被建为与 active owner 无关的 unresolved owner — 服务端实际已接受时只能结算 follow-up 行、原 active 永久 pending；已补充候选 alias 关系并要求 REST 判定 alias/queued 后再落地。
  - `[medium]` `[bad_spec]` Blind：unresolved submission intent 没有 `created_at`，重复问题无法按时间消歧 REST user message — 多个同文本 user message 时迁移必然失败；已补充提交时间与真实 ID/边界迁移。
  - `[medium]` `[bad_spec]` Blind：恢复 deadline 只在阻塞调用前检查，start/subscribe/status/messages 各自 timeout 可令总恢复远超截止 — 与 CR4 的同类约束一致但实现仍未传递剩余预算；已补充每次 I/O 共用绝对 monotonic deadline。
  - `[medium]` `[bad_spec]` Blind：恢复线程直接修改 chat/turn 与 dirty 状态 — legacy 迁移和 worker 提交路径可与导航、保存、渲染竞态；已要求所有此类状态在 UI 回调落地且落地前复核 owner。
  - `[medium]` `[bad_spec]` Blind：显式子代理的 assistant delta 既不进入回答 buffer，也没有进入 F1 — 当前分支使子代理正文完全消失，违背“子代理过程只进 F1”；已补充非 main assistant delta 的执行过程映射。
  - `[high]` `[bad_spec]` Blind：非 main 的 `work_changed busy:false` 可为主 owner 写入 idle proof — 后续 main `prompt.completed` 可借该证明提前发布；已补充 idle proof 必须来自 main/根 session 且绑定 owner generation。
  - `[high]` `[bad_spec]` Blind：alias prompt 的 `turn.ended` 用 alias turn_idx 清 active owner，索引不一致时 chat 永久保持 active — 已补充 alias group 结算与清理由同一 active owner 身份执行。
  - `[high]` `[bad_spec]` Blind：alias covered 集合只比 chat 与 owner_prompt_id，漏掉 session_id — prompt ID 碰撞时可跨 session 结算错误行，违反复合 owner 边界；已补充完整 `(chat, session, owner_prompt)` 过滤。
  - `[false]` `[reject]` Blind：`_apply_kimi_error` 在显式 target_idx 路径引用未定义 `turn` — 第 11931 行在所有有效 target_idx 分支统一赋值并验证 dict，之后第 12032 行可安全读取，不会触发 `UnboundLocalError`。
  - `[high]` `[bad_spec]` Blind：failed/interrupted 终态只要 finalize 返回行就播放“回答完毕” — 两个 UI 分支均未按成功状态过滤，直接违反用户的音效要求；已补充只有非空权威最终答案成功发布才响铃。
  - `[medium]` `[bad_spec]` Blind：`/clear` 只清可见 session 字段，新增 owner、submission、buffer、early-event 和 recovery 状态仍存活 — 迟到事件可复活或污染已清理聊天；已补充完整生命周期清理。
  - `[high]` `[bad_spec]` Blind：runtime rebuild 从旧 owner/active 映射复制后只增不删 — 已删除 chat/turn 的幽灵 owner 可继续捕获事件，直接威胁多聊天路由；已补充按当前持久化 pending 集裁剪。
  - `[medium]` `[patch]` Verification：成功 reader 重连发出 `transport_recovered` 并驱动第二次 REST 对账没有测试 — carried：与上一轮相同位置和主张，仍缺 reader failure→success→consumer 的完整断言；随重实现保留客户端与集成续跑测试。
  - `[medium]` `[patch]` Verification：同一 client 的服务进程重启重新发现 token 没有测试 — 生产路径已写 token reset，但删除该行现有测试仍会通过；随重实现新增 token A→进程退出→token B 握手回归。
  - `[false]` `[reject]` Verification：非 main approval 绕过“权威代理边界”必须被禁止 — `<intent-contract>` 只禁止把子代理审批请求当最终回答，并未禁止子代理为继续任务请求用户审批；该对话框不发布回答也不播放完成音，所述坏结果不成立。
  - `[high]` `[bad_spec]` Edge：已建立 epoch 后的 sequenced 帧省略 epoch 会把 cursor epoch 清空 — 更新分支会写入空 epoch，随后旧帧可重新被接受；已补充省略 epoch 时继承已确认 epoch、不得重置。
  - `[medium]` `[bad_spec]` Edge：同 seq、无 offset volatile 多帧会共用 replay key — 与本轮 Blind 首项同根，合法 phase/state 更新会丢失；共享同一协议身份修复。
  - `[medium]` `[bad_spec]` Edge：多个 assistant stream 按字符串 stream key 排序拼接 — 创建顺序与词典序不一致时最终正文错序；已补充协议顺序或首次出现顺序。
  - `[high]` `[bad_spec]` Edge：offset 缺口在完成门槛检查之后才被 `_kimi_answer_parts` 发现 — 同事件仍可能用其他非空片段/terminal text 发布残缺回答；已补充先组装并判完整，再允许 finalize。
  - `[medium]` `[bad_spec]` Edge：Kimi 错误后的运行态用所有模型 pending turn 数量计算 — 同聊天仍有非 Kimi pending 时会把 Kimi 标成 active；已补充 chat+session+Kimi owner 过滤。
  - `[false]` `[reject]` Edge：live abort 只发 `prompt.aborted` 时会超时 — live predicate 已显式接受 `source_kind in {"turn.ended", "prompt.aborted"}`，声称的缺口不存在。
  - `[medium]` `[bad_spec]` Edge：子代理正文没有进入 F1 — 与本轮 Blind 第八项同根，分支可达且内容会消失；共享子代理过程映射修复。
  - `[high]` `[bad_spec]` Edge：失败或中断仍播放完成音 — 与本轮 Blind 第十三项同根，两个当前/后台 completion 分支都会误播；共享成功终态音效门槛。
  - `[high]` `[bad_spec]` Edge：重启 rebuild 只把 `__legacy__` owner 标为可迁移，`__unresolved__` 被当普通 prompt — ambiguous accepted submission 重启后找不到该伪 ID 的 transcript 边界并被误判失败；已补充 unresolved 恢复迁移。
  - `[high]` `[bad_spec]` Edge：异步恢复失败回调落地前不复核 owner generation/tombstone/pending — 旧 worker 可在成功完成后覆盖 turn 为失败；已补充带身份的 UI 落地复核。
  - `[medium]` `[bad_spec]` Edge：所有对账调用未共享真正的绝对截止预算 — 与本轮 Blind 第六项同根，独立网络 timeout 可累计到数分钟；共享 deadline 传递修复。
  - `[false]` `[reject]` Intent：没有真实播放并监听音效 — carried：wx 回答列表状态与 `_play_finish_sound` 调用边界是确定、可重复的用户可见逻辑验收，真实扬声器播放不增加状态机置信度。
  - `[false]` `[reject]` Intent：没有修改或部署 `D:/code/cx/mc` 打包产物 — carried：用户仅授权读取该目录的日志和记录用于诊断，没有授权覆盖打包成品。
  - `[low]` `[reject]` Intent：没有在真实“gb/安卓”聊天执行双会话 E2E — carried：这会创建真实外部任务，用户未授权该副作用；确定性多 session 集成测试是必需 QA 边界。
  - `[low]` `[reject]` Intent：真实 WebSocket/Kimi live smoke 未运行 — carried：live 是 opt-in 且会改变外部服务状态；默认 QA 用协议与集成 fake 覆盖，额外复杂度与副作用不值得。
  - `[false]` `[reject]` Intent：仓库 diff 无法证明 Kimi 用户级 AGENTS 修改 — carried：该文件天然位于仓库外，已在本机用 UTF-8 精确内容检查验收。
  - `[false]` `[reject]` Intent：规格仍为 in-review、任务未勾选，无法证明 QA→CR 完成 — carried：审查器读取的是 CR 进行中的工件；状态和任务只可在审查收敛后的 finalization 更新。
  - `[false]` `[reject]` Intent：现场日志本身未包含在 diff — 日志是用户授权的只读取证据，规格已记录去敏后的根因结论；把运行记录复制进代码变更既非修复也会违反凭据边界。
  - `[false]` `[reject]` Intent：未实现两个独立 MC 进程并发 — carried：用户的具体复现明确是同一 MC 内“gb/安卓”两个聊天室，较弱的多进程字面读法不是本次范围。

### 2026-09-07 — Review pass 6
- verdicts: 27 findings — high 7, medium 8, low 3, false 8, maybe-false 1
- findings:
  - `[high]` `[bad_spec]` Blind：相邻 delta 的 incoming offset 超过现有正文末尾时仍直接拼接，offset 0 的 `abc` 与 offset 5 的 `F` 会变成连续 `abcF` — 队列层抹掉缺口后上层无法再发现流不完整，可能发布截断答案；规格已明确要求保留缺口并转 REST 对账。
  - `[maybe-false]` `[defer]` Blind：REST transcript 把 injection-origin 的 user reminder 当成下一 prompt 边界 — 角色序列证据显示存在相邻 user 行，但当前夹具未保留该行的 origin/完整 message metadata；需读取真实 `messages` 响应中该 user 行的字段才能确认应忽略哪些注入消息。若成立将是 high。
  - `[medium]` `[bad_spec]` Blind：socket 安装后、hello/subscribe ack 前就发 `transport_recovered` — ack 随后拒绝或 reader 立即失败时会产生虚假恢复通知；规格要求确认订阅后再宣告恢复。
  - `[medium]` `[bad_spec]` Blind：`_send_ws(send_timeout=...)` 在需要重连时调用无 timeout 的 `_connect_ws()` — 从上层传下来的共享 deadline 可被一到两次完整连接超时突破；规格已要求每个阻塞调用共享剩余预算。
  - `[high]` `[bad_spec]` Blind：提交锁在从 active owner 恢复真实 session_id 之前选定 — 一个 worker 可锁 `chat:<id>`，另一个锁同一最终 session ID，导致同 session submit/steer 决策重叠；违反 per-session 提交事务。
  - `[high]` `[bad_spec]` Blind：ambiguous steer 的 `candidate_alias` 没有持久化到 turn — 重启 rebuild 对真实 prompt ID 会恢复为 `candidate_alias=False`，把可能已接受的 alias 错判为独立 active；违反 ambiguous steer 的可恢复归属。
  - `[medium]` `[bad_spec]` Blind：owner migration 标记 turn dirty 后没有安排 state save — 若随后返回 monitor，真实 prompt ID/role 只在内存，第二次重启会丢失；规格要求迁移状态持久化。
  - `[high]` `[bad_spec]` Blind：runtime rebuild 只保留精确 `(session,prompt)` early-event key，并按现有 owner session 裁剪 pending submissions — 聊天切换恰逢 HTTP in-flight 时会丢掉 `__intent__:*`、`__turn__:*` 与唯一 join 信息，直接复现多聊天丢答案风险。
  - `[high]` `[bad_spec]` Blind：`/clear` 只删精确 prompt early bucket 和当前 chat-level session 状态 — intent/turn bucket、旧 session 恢复账本及 turn 上的 pending ownership 仍可在 rebuild 时复活幽灵 owner；生命周期清理不完整。
  - `[medium]` `[bad_spec]` Blind：流不完整标记绑定整个 session，而非 owner/stream — owner A 的缺口会迫使完整的 queued owner B 走恢复，且长队列期间标记持续粘住；违反复合 owner/stream 隔离。
  - `[medium]` `[bad_spec]` Blind：volatile replay key 在存在 messageId/itemId 时不再包含 stream/type — thinking 与 assistant 若共享 message ID、seq、offset，会把其中一帧误判重放；规格要求 stream 身份属于幂等键。
  - `[false]` `[reject]` Blind：QA 摘要没有证明 15 项失败与基线一致，且 live 未跑却声明收敛 — 我已独立运行相同筛选并逐项比对冻结文档，15 个节点完全一致；live 会产生外部任务且按规格为 opt-in，frontmatter 的 in-review 值也是审查时序状态。
  - `[medium]` `[patch]` Verification：create_session 的 REST 创建成功、首次 subscribe send 失败容错没有保护测试 — carried：与 Review pass 4 同位置和同主张，现有测试仍只覆盖成功订阅；需断言返回已创建 ID、保留 desired subscription 并继续 submit。
  - `[medium]` `[patch]` Verification：恢复测试未验证同一 deadline 真正传入 start/subscribe/status/messages — helper 单测只观察独立 lambda，fake 方法不接受 timeout，删除任一生产调用的 deadline 参数仍会全绿；需 timeout-aware fake 覆盖真实 coordinator 路径。
  - `[high]` `[bad_spec]` Edge：REST reconciliation 已取得完整答案时，finalize 仍优先采用旧 incomplete buffer — `stream_complete=True` 只绕过门槛，非空 partial 会覆盖 event.text 的完整 transcript；直接违反断线后必须以 REST 完整正文发布。
  - `[high]` `[bad_spec]` Edge：`/clear` 不清 turn 的 pending owner 字段与 request_status — 后续 navigation rebuild 会据此重新注册已经清理的 chat owner；与本轮 Blind 第九项同根。
  - `[medium]` `[bad_spec]` Edge：Kimi 服务进程以 return code 0 退出时，即使仍有 pending owners 也直接返回 — 非应用主动关闭场景会永久 pending；规格要求所有进程退出枚举并恢复 pending owner。
  - `[low]` `[reject]` Edge：测试注入的 `ws_factory` 可无界阻塞 — carried：生产路径使用有界 websocket timeout；为任意测试注入器增加硬截止需要额外线程与泄漏管理，日常用户不可达且不值得增加复杂度。
  - `[false]` `[reject]` Intent：diff 未部署到 `D:/code/cx/mc` 打包程序 — carried：该目录只获授权读取现场日志与记录，用户没有要求覆盖成品。
  - `[false]` `[reject]` Intent：没有通过真实扬声器监听完成音 — carried：`_play_finish_sound` 调用次数是确定且可重复的逻辑边界，真实音频设备不增加状态机置信度。
  - `[false]` `[reject]` Intent：没有人工操作打包 UI — wx 控件级测试直接断言 pending 无 AI 行和最终回答行更新，已经覆盖用户可见列表契约；人工操作不是本次自动 QA 的必要条件。
  - `[low]` `[reject]` Intent：没有在真实“安卓/gb”聊天跑双任务 — carried：真实运行会创建外部任务且用户未授权该副作用；确定性多 session/chat 交错测试是合适边界。
  - `[low]` `[reject]` Intent：真实 Kimi WebSocket/REST live smoke 未运行 — carried：live 为 opt-in 且会改变外部服务状态，不作为自动流程阻断项。
  - `[false]` `[reject]` Intent：现场日志没有包含在 diff — 日志是只读诊断材料且可能含凭据；规格保留去敏后的事实和根因即可。
  - `[false]` `[reject]` Intent：仓库 diff 不能证明用户级 AGENTS 修改 — carried：文件位于仓库外，已在本机以 UTF-8 精确匹配验证指定行存在。
  - `[false]` `[reject]` Intent：diff 不能证明调用 skill 和 QA→CR 时序 — carried：运行时编排不由源码 diff 证明，规格、QA 摘要与逐轮 triage 是对应流程工件。
  - `[false]` `[reject]` Intent：主逻辑筛选仍有 15 项失败，不能称处理完成 — carried：15 项逐一属于冻结的 execution-list 既有基线，Kimi 定向 209 项全绿；本轮阻断原因是新 CR 发现，而不是这 15 项。

## Design Notes

主代理身份采用协议事件的 `agent_id`：明确的 `agent-*`/子代理事件永不取得最终回答所有权；主事件使用协议给出的 main 身份或缺省根身份。`prompt.completed` 仅作为同一 `prompt_id` 未见 `turn.ended` 时的受控 fallback，且需等待 session 空闲并取得完整正文。完成幂等键优先使用 `session_id + prompt_id + agent_id`，缺 prompt 时才使用已证明归属的 turn；无 turnId/无 prompt 的 REST 消息在复用 session 中不得猜测。回答列表与执行列表分层：执行过程可实时更新 F1，回答结果在权威完成前保持隐藏。

恢复状态机只有一个活动 owner：每个 chat 同时至多一个服务端活动 prompt；steer 成功的 prompt 作为该 owner 的本地 alias，steer 失败的 prompt 才进入本地/服务端队列。活动 owner 完成时原子结算其 alias，但不能越权结算独立 queued owner；queue 未空时 UI 仍为运行态，下一 owner 只由精确 prompt/start/终态激活。断线后客户端在后台有界退避重连；reader、pong、普通 send 失败共享一个恢复所有权，恢复线程退出前检查替代连接，必要时交接下一轮；重连成功按服务端 cursor 语义续订，映射前丢弃旧稳定 seq，ack 要求 resync 时对活动及排队 owner 做 REST/ops 重建。

REST 真实响应的 assistant 消息不含 turnId/promptId/agentId，因此归属以 prompt_id 等于 user message id 的事实建立 transcript 边界：只读取该 user message 之后、下一条 user message 之前的最终 assistant text。所有对账入口都必须验证 session idle 或已有同 owner 权威终态，且 `idle_verified` 只记录实际检查结果；busy、空结果或瞬时失败在截止时间内重试，截止后把能明确归属的 owner 标为失败，即使没有传入 original_error 也不得静默永久 pending。启动订阅也遵循相同重试/截止原则。早到 prompt 事件在 owner 字段落库前统一暂存并按序重放。

owner 注册表不是 prompt_id 的全局字典，而是 `(chat_id, session_id, prompt_id)` 复合命名空间；重建时从当前聊天和全部归档聊天合并 pending 行，不能因切换聊天清空别人的 owner。`idle_verified` 是带 owner key 与派发 generation 的一次性证据，新 submit/start 必须清零。`turn.ended`、失败或中断写入同 owner tombstone，使有无 turn_id 的迟到 `prompt.completed` 都不能再次结算。旧存档缺 prompt_id 时只做可证明迁移，无法证明则在恢复截止后明确失败。

传输层以当前 socket generation 为边界：连接和握手有超时，网络调用不长期占生命周期锁；reader 每次 recv 后再次验证 generation，旧 reader 帧直接丢弃。ack 对同 epoch 只允许 seq 前进，新 epoch 只有当前连接握手才能确认；顶层 `volatile:true` 均不得按稳定帧丢弃。进程重启必须重新发现 token。UI 恢复以 session 为单位合并 owner 并由单一协调器对账；早到正文以 offset 合并或改走 REST，不能用固定长度 deque 丢头。

提交和恢复分别是 per-session 事务。提交事务持有 session 专属锁直到 submit/steer 的归属落地；HTTP 超时是“结果未知”而不是失败，保留 submission intent 并用 transcript user id 查证。恢复事务用 requested generation 交接，只按 owner_prompt_id 组工作；transport_recovered 后 busy 表示任务继续，由事件驱动，不启动短期失败倒计时。只有进程/业务终态明确失败，或一个 monotonic 总截止内始终无法恢复传输与归属，才失败 owner。较早 prompt 的 transcript 边界若已被下一 user message 闭合，可独立结算，不受同 session 后续 busy 影响。

流正文有完整性状态：volatile 帧用绝对 offset 和复合 stream 身份跨队列 drain 去重/排序；发生传输缺口后，该流必须经 REST transcript 取得完整正文才能发布。所有 scope 写入先验证 main 身份及 turn_id 相容性。音效的 exactly-once 键与 finalized owner 相同，即使同 chat 仍有 queued owner，每个新完成回答也各响一次；聊天运行态则只由本 chat 的 pending owners 决定。

## Verification

**Commands:**
- `py -3.11 -m pytest tests/test_kimi_event_mapping_unit.py tests/test_kimi_server_client_unit.py -q` -- 协议、恢复与客户端并发测试全部通过。
- `py -3.11 -m pytest tests/test_kimi_integration.py tests/test_kimi_ui_responsiveness_automation.py -q` -- Kimi 外层行为与可访问性测试全部通过。
- `py -3.11 -m pytest tests/test_main_unit.py -q -k "kimi or execution or switch_current_chat"` -- 主逻辑相关测试通过；既有基线失败单独核对。
- `py -3.11 -m compileall -q main.py kimi_server_client.py tests/test_kimi_event_mapping_unit.py tests/test_kimi_server_client_unit.py tests/test_kimi_integration.py tests/test_kimi_ui_responsiveness_automation.py tests/test_main_unit.py tests/test_kimi_live_smoke.py` -- 编译通过。
- `powershell -NoProfile -Command "$p='C:\Users\gladwell\.kimi-code\AGENTS.md'; if (-not (Test-Path -LiteralPath $p)) { exit 1 }; if (-not (Select-String -LiteralPath $p -SimpleMatch '2. Thinking、计划、过程说明和最终回答都使用简体中文。' -Quiet)) { exit 1 }"` -- Kimi 用户级全局规则存在且内容准确。
