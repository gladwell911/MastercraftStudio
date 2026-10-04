---
title: 隔离非通知发件故障并恢复通知自动重试
type: bugfix
ticket: ''
created: '2026-10-04'
status: built
baseline_revision: 'de70ced44c6fa61a5e8899b3ce6e0985317eb823'
route: full
route_source: auto
review: thorough
review_source: auto
lenses_ran: [blind-hunter, edge-case-hunter, verification-gap, intent-alignment]
review_loop_iteration: 0
followup_review_recommended: false
context: []
warnings: [oversized]
deferred: []
---

<intent-contract>

用户要求：避免文件、执行记录等其他事件的错误阻塞手机回答通知。实施目标是持久化 outbox 中的 assistant_final 通知可以独立发布、自动重试和恢复；不通过删除事件、伪造 ACK 或修改手机通知过滤实现。

已确认现场：实际桌面进程 D:/code/cx/mc/mc.exe 使用 D:/code/cx/history/chat_history.db。seq=73011 的 file_offer 已失败 5 次，blocked_reason=nats: timeout，取证时阻塞后续 9060 条。最新 assistant_final seq=81904 等 hash 正常、published_at=NULL、attempts=0，手机无对应 ledger。手机通知权限 granted、消息 channel importance=4。最后成功发布时间为北京时间 2026-10-03 13:56:39，不等于已证实的第五次失败时间。

### 关键决策

1. 在现有 outbox 上建立两个发布通道：notification（durable_facts.kind=assistant_final）和 other（其余所有事件，包括 user_message、files、execution、clear）。分类使用关联 durable_facts 的持久 kind，不依赖 event_id 前缀或对 payload 的脆弱字符串匹配。只保护当前实际会产生 Android 通知的 kind，不建立通用多优先级框架。
2. 两通道有独立 worker/锁/重试任务，notification 先启动。other 的 publish 超时、blocked 行、验证错误或 store 异常不得阻止 notification 获得执行机会。不能仅在一个串行 for 中将 break 改为 continue，也不能先等待 other 的网络请求完成再开始 notification。
3. 保留每条原有 event_id、domain、sync_sequence、canonical_hash 和 payload 字节。有效事件在各自通道内保持升序；允许 notification 超越 other，因此跨通道不再保证提交序就是服务端到达序。其余事件互相之间仍保留原顺序，文件故障不会被顺手改成所有其他事实随意乱序。
4. 非通知通道永久故障可以继续挡住该通道，不能挡住通知。通知通道的临时故障仍保留头部，后续有效通知不超越它；确定性非法行保留并明确标记，允许同通道后续有效行前进。共享网络不可用、整个数据库不可访问仍会影响双方，不声称能够绕开共同依赖故障。
5. 不改变 RC 启动 watermark、origin、revision、可见 owner、权限和 event_id 去重规则。启动前旧事实继续按原合同不提示；本次修复保证有效新回答不被其他事件卡住，不承诺积压中的所有旧回答均补响。

## 边界

修改仅限 MC 的 outbox 查询/状态、transport 发布和必要测试；不新增 NATS subject/stream、不修改 wire protocol、不重新生成事实、不改变 canonical 序号分配、不添加第三方依赖。原 publish_event/command response 保持现有行为。RC 原生 notification actor 不依赖连续 sync_sequence，仅比较启动 watermark，并按 event_id ledger 去重；Dart 已按 owner/revision 缓冲且 execution gap 单独恢复。跨通道乱序仍须以针对性测试验证 clear/revision 场景，不能只依靠上述代码观察。

生产数据不在编码阶段直接修改。现有安装包必须单独构建、部署并验证，源码通过不代表 D:/code/cx/mc 已更新。

## I/O matrix

| 输入/情况 | 持久状态与输出 | 顺序/恢复 |
|---|---|---|
| other 头部失败或旧 blocked 行，存在 assistant_final | 通知独立 publish，ACK 后写 published_at | 通知通道升序；other 原事实保留 |
| 通知 publish ACK 丢失 | 不写 published_at；重试相同 payload 字节/event_id | 保持至少一次投递，RC ledger 去重 |
| 超过五次网络异常 | attempts 累加，仍可自动重试，不因次数永久封锁 | 有界退避，恢复后不需要人工 repair |
| 旧 blocked_reason=nats: timeout 或其他旧异常字符串 | 不把历史 blocked 字符串作为永久失败证明；尝试原行 | 首次启动即可恢复，成功后清理阻塞状态 |
| 本地确定性非法 payload/hash/路由 | 原行保留，记录明确 permanent 前缀及原因，不写 ACK | 通知非法行可跳过；other 仍保留顺序边界 |
| 100 条以上积压、无新消息触发 | 后续有界批次持续调度直到无可发布项 | 不只发首批，不忙循环永久失败行 |
| 程序重启 | 从 published_at=NULL 的持久行重建工作 | 不重发已 ACK 行；临时行继续重试 |
| worker 被取消或关闭 | 不记录伪成功，取消所有 retry/worker 并等待结束 | 不遗留任务、不把 CancelledError 当发布失败 |

</intent-contract>

## Code Map

- remote_nats.py:96：现有一个 lock/retry task，替换为两个固定通道的最小状态；保留对外 drain_outbox() 入口与整数成功数量约定。
- remote_nats.py:268：重写 drain 的调度和每通道有界发布；两通道并发，单通道内串行；按 full pair/domain/sequence 标识状态写入。
- remote_nats.py:300：现有 retry 在自身任务中再次 schedule 会因 task 尚未 done 丢掉下一次重试；必须修复任务生命周期。每通道最多一个 retry，触发前解除旧任务引用，或用单个明确 retry loop，不同时叠加两种机制。
- remote_nats.py:308、329：repair 与 close 更新新的状态/任务，repair 不使用默认生产路径，也不删除事实。
- chat_store.py:915：pending_outbox 增加可选固定 lane 过滤（默认维持原公共查询），JOIN durable_facts 分类；筛选在 SQL LIMIT 之前，避免通知被前 100 条其他事件遮蔽。若发现孤立 outbox 行，应归 other 并保留，不因 inner join 静默消失。
- chat_store.py:923、937：ACK/失败状态及 publisher checkpoint；不得在并发乱序后用 MAX 冒充连续发布完成边界。
- tests/test_remote_nats_unit.py:585：现有测试要求任意五次异常永久堵住全部队列，需替换为新合同；保留相同字节重发与 ACK 前 checkpoint 不进位的断言。
- tests/test_chat_store_unit.py、test_notification_contract_unit.py、test_main_remote_nats_unit.py：补查询分类、重启恢复、事实不变及 canonical notification 入口定向检查。

## Tasks & Acceptance

### 1. 持久查询与状态

实施 SQL 过滤的两通道查询，不扫描/解析所有正文再取前 100 条，不新增 lane 数据迁移。保留原未分类查询供已有调用方及诊断使用。局部增加失败记录的明确永久参数或专用方法：只有本地可确定的合同/数据错误使用 permanent 标记；publish 抛出的未知异常默认可重试，不根据异常字符串猜永久故障。record_outbox_failure 不再以 attempts>=5 判永久。旧 blocked 行只有显式新 permanent 前缀才不自动尝试；重试失败清除旧永久误判，保留 attempts 证据；成功 ACK 清除 blocked_reason。

publisher checkpoint 改为该 pair/domain 实际连续 ACK 前缀的最高已发布序号：遇到该 domain 最早未发布行就不跨越；对稀疏 domain 取它前方已 ACK 的最大序号，不要求整个 pair 每一个整数都属于该 domain。checkpoint 不用于选择待发项，待发项始终 published_at=NULL。即使永久非法行被通知 worker 跳过，checkpoint 也不能假称已发布该行。

Given 前 150 个 other 未发布，When 查询 notification limit=100，Then 能找到之后的 assistant_final，且 other 查询不含这些通知。Given 跨 lane 通知先 ACK，Then publisher checkpoint 不跨过同 domain 更早未 ACK 行；该行后来 ACK 后能准确推进。

### 2. 独立发布、顺序与去重

drain_outbox 触发两个固定 worker，通知优先启动且互不等待网络调用。每通道使用自己的锁，重复调用 drain/retry 不产生该通道重入。一次取有界 batch，批量后显式让出 event loop；有剩余即安排下一批，不靠新模型回调再次触发。保持对外入口可 await；返回本次成功数，持续积压由受控任务继续。避免长时间 backlog drain 挡住 start_threaded 的启动 deadline：启动仅启动受控 drain，或有界完成首批后继续后台，选择最小形式。

publish ACK 后才能 mark_outbox_acked；两者任何失败都保留待发状态。重发必须直接读取已持久 payload，不能重新序列化、换 event_id 或修改正文。确定性数据校验复用 decode_payload/validate_v2_durable，只有失败时持久 permanent 原因；确认路由仅是既有 events/files。数据库标记失败时不得继续同通道下一行，也不得让 gather 抛异常取消另一个通道。

Given other 的 publish Future 挂起，When 通知 worker 工作，Then 通知 Future 可独立成功（测试用 asyncio.Event，不靠耗时猜测）。Given other 永久错误、store 标记异常，Then notification 仍有成功机会。Given 两个有效通知，When 首个暂时失败，Then 第二个不能先发布。Given 一个非法通知后一个有效通知，Then 非法行保留、有效行发布、无假 ACK。Given ACK 丢失，Then 重试 payload 完全相同，重复 event_id 仍由既有客户端 dedup 处理。

### 3. 自动恢复与积压

每通道最多一个有界退避 retry：采用简单 0.25 秒开始、指数增长上限 30 秒的现有 asyncio 定时方式；成功进展后重置退避。外部 drain 请求不能绕过已安排的冷却而对故障头部形成热循环。停止时取消并 await 所有任务。重启恢复不需要持久 retry 时间字段；attempts 和未 ACK 行保留，重启从最小退避继续即可。旧 blocked 超时行自动重试；不在 initialize 无条件抹掉历史记录。

Given 连续至少六次超时后服务恢复，When 没有新事件提交，Then 自动重试最终成功且后续通知继续。Given retry 回调再次失败，Then 后续 retry 仍被安排（覆盖当前 self-task guard 缺陷）。Given 重启且旧文件 blocked 行仍存在，Then 新/已有通知可以发布，文件按原字节恢复重试。Given 两通道各超过 100 条，Then 无新 trigger 也能最终 drain，双方不饿死。Given stop，Then task 全结束且无 further publish。

### 4. 契约与最小集成

把旧全队列严格阻塞测试改成通道内顺序合同，保留 ACK 不确定性测试。测试使用 tmp_path 数据库和 FakeJetStream，不接触生产库。增加 clear/revision 非通知阻塞时通知先到的 RC 定向测试：原生 actor 在既有合同下可提示有效新通知，Dart 保留 owner/revision 边界并可通过原历史恢复取得正文；不为满足测试放宽旧 revision。

Given other clear/execution 延迟、notification 到达在前，Then 不产生跨 owner 数据污染、旧 revision 不覆盖当前、通知点击仍能通过原历史机制取详情。Given 已发布通知重启，Then 桌面不重复主动发布；ACK 丢失重投只产生一个 event_id 的通知 ledger。既有 watermark 过滤不改，也不把历史积压当全新提醒。

## Implementation Notes

2026-10-04 localized review fixes (director):
- Successful lane progress now forces one fresh pending query before exit, retaining notifications enqueued while a prior publish is suspended without bypassing retry cooldown or changing lane order.
- Reset successful-progress retry delay to 0.25 before any exit decision.
- Checkpoint queries earliest pending explicitly; NULL means no upper boundary, so a legally generated MAX_INT64 ACK is included.
- Added six focused regressions: Event-suspended first notification with second commit/drain; prior 30s delay resets and next failure sleeps 0.25; legal MAX_INT64 sequence generation/ACK checkpoint; other-lane pending query exception isolation; close cancels a still-blocked publish without ACK/failure attempt; start returns while outbox publish remains blocked.
- Scope-limited command: .venv/Scripts/python.exe -m pytest tests/test_remote_nats_unit.py tests/test_chat_store_unit.py -q => 90 passed in 19.47s. git diff --check passed (existing CRLF normalization warning only). Independent broader verification remains with engineer/root.


2026-10-04 director implementation:
- `chat_store.py`: SQL LEFT JOIN classification by durable kind before LIMIT; orphan rows remain in other. Explicit `permanent:` failures only; transient attempts never permanently block. ACK clears legacy blocked reason. Publisher checkpoint is the ACK prefix of the pair/domain, including sparse domains.
- `remote_nats.py`: notification-first independent lane workers/locks. Each worker reports its first bounded batch, then continues backlog with event-loop yields or 0.25s exponential retry capped at 30s. External drains do not bypass an active retry cooldown. Startup launches controlled workers without waiting on backlog/network; close cancels and awaits all workers. Local validation reuses decode_payload/validate_v2_durable and checks persisted identity/routing; publish uses unchanged stored bytes.
- RC product code unchanged. Added Dart delayed-clear notification owner/revision test and native actor gap/watermark/ledger test.

Director matrix evidence (tmp_path databases / FakeJetStream only):
- Event-controlled hanging other publish allows notification ACK independently; checkpoint stays before earlier unacked same-domain row and advances after that row ACK.
- Six ACK-loss failures retry automatically without a new trigger, identical bytes, notification head ordering, attempts retained and checkpoint unchanged before ACK.
- Store ACK marking failure in other leaves that row pending while notification succeeds; repeated external drains respect cooldown; close awaits tasks and forbids further publish.
- Isolated seq=73011 legacy blocked file, 151 other +102 notifications; reopen SQLite before publish; malformed notification retained with permanent reason; 252 valid rows drain without new trigger, original identity/payload preserved, ACK rows do not republish after another reopen.
- LEFT JOIN orphan preservation and sparse-domain checkpoint are directly tested in test_chat_store_unit.py.
- RC notification overtakes clear, buffers until authority, old revision cannot overwrite, second owner remains intact; existing history_read recovery positive control passes. Actor alerts sequence 152 before delayed clear 11, deduplicates event ledger, keeps startup watermark filtering.

Director verification evidence:
- MC specified suites initial combined result: 123 passed, 1 existing failure. Final affected MC suites (remote_nats, notification_contract, chat_store, main_remote_nats): 120 passed in 29.39s; engineer independently rechecks final tree.
- `tests/test_story4_review_regressions_unit.py::test_close_vetoes_and_schedules_retry_while_persistence_worker_is_live`: baseline de70ced44c6fa61a5e8899b3ce6e0985317eb823 main.py AND test loaded via git show into an isolated temporary directory reproduce AttributeError: SimpleNamespace missing _stop_answer_refresh_deadline (main.py:22935). No unrelated fixture/product fix.
- Dart command: flutter test --no-pub test/remote_nats_chat_service_test.dart --name 'notification overtakes|same-revision facts|history_changed backfills|negotiated v2 retains' => 4 passed.
- Native command: android/gradlew.bat :app:testDebugUnitTest --tests com.example.zhuge_qa.RemoteBackgroundNotificationActorTest --offline => BUILD SUCCESSFUL, 15 tests/0 failures/0 errors. Machine-readable log: D:/code/sj/rc/build/app/test-results/testDebugUnitTest/TEST-com.example.zhuge_qa.RemoteBackgroundNotificationActorTest.xml.
- MC/Dart console evidence resides in this implementation tool transcript; no fabricated persistent log paths. git diff --check passed before final verification.

Source changes only. D:/code/cx/mc runtime package and physical phone were not updated; production database untouched. Engineer independent verification remains pending.

## Plan Change Log

## Review Triage Log

按用户当前全局派遣规则“复杂问题由 dog 审查、能一次完成不增加代理调用”，四个选定审查视角交同一 context-free dog 一次完成；不额外派遣四名审查代理。

### 2026-10-04 — Review pass

- verdicts: 23 findings/observations — high 6, medium 10, low 5, false 2, maybe-false 0.
- 用户要求明确局部错误由 director 直接修复、engineer 复验；本轮按该高优先级要求保留已通过实现，三个可直接修正问题均走 patch，不执行低优先级的整体重推流程。
- findings（按视角报告顺序逐项记录，重复根因不删行）:
  - `[high]` `[patch]` Blind 1 活跃 worker 丢失新终答唤醒 — main.py:11618-11638 实际提交后触发；旧 rows 的长度不足100导致退出。成功批次后继续一次查询，新 Event 回归验证无额外 trigger 自动发第二条。
  - `[medium]` `[patch]` Blind 2 清空成功批次未重置退避 — transport 字典跨 worker 保留30秒。将成功重置移至退出判断之前，测试下一故障从0.25秒开始。
  - `[low]` `[patch]` Blind 3 MAX_INT64 ACK 被哨兵排除 — 合法生成路径允许从MAX_INT64-1生成MAX_INT64；改成显式NULL分支，边界回归保护。
  - `[high]` `[patch]` Blind 4 缺发送期间提交的确定性回归 — 原测试都预先入队；补 first publish挂起→提交second→drain→release→无需新trigger送达。
  - `[medium]` `[patch]` Blind 5 other读取异常隔离缺直接测试 — 原测试仅故障注入ACK写入；新增pending_outbox(other)抛错、notification实际ACK的回归。
  - `[medium]` `[patch]` Blind 6 挂起publish关停缺直接测试 — 原挂起用例先release；新增未release时close完成、worker结束、未ACK行保留。
  - `[medium]` `[patch]` Blind 7 启动不等积压缺直接测试 — 新start路径实际变更；新增Fake NATS+挂起publish，start仍返回。
  - `[high]` `[patch]` Edge 1 活跃期drain触发丢失 — 同Blind 1，保留新批次再查询修正与确定性回归。
  - `[medium]` `[patch]` Edge 2 成功短批保留旧退避 — 同Blind 2，成功先重置，测试通过。
  - `[low]` `[patch]` Edge 3 无pending的MAX_INT64合法边界 — 同Blind 3，以NULL明确表示无上界。
  - `[high]` `[patch]` Edge 4 声称有剩余会继续与旧退出条件矛盾 — 同遗漏唤醒根因，成功后续批次核对新pending。
  - `[medium]` `[patch]` Edge 5 声称成功重置与提前return矛盾 — 同退避根因，调整语句顺序。
  - `[low]` `[patch]` Edge 6 连续ACK前缀声称在最大合法值不成立 — 同checkpoint边界，以最小局部SQL修正。
  - `[medium]` `[patch]` Verification 1 start不等publish缺验证 — 同Blind 7，定向启动回归已增加。
  - `[medium]` `[patch]` Verification 2 挂起publish取消缺验证 — 同Blind 6，Event取消回归已增加。
  - `[medium]` `[patch]` Verification 3 队列读取异常缺验证 — 同Blind 5，读取故障隔离回归已增加。
  - `[high]` `[patch]` Verification other 1 丢失活跃期唤醒 — 同Blind 1，已核对实际终答入口并修复。
  - `[medium]` `[patch]` Verification other 2 成功后保留退避 — 同Blind 2，成功重置修复。
  - `[low]` `[patch]` Verification other 3 最大合法ACK被排除 — 同Blind 3，显式NULL修复。
  - `[high]` `[patch]` Intent observation 1 持续新消息表面未被预入队测试覆盖 — 同遗漏唤醒，新增确定性运行中入队回归补齐。
  - `[low]` `[reject]` Intent observation 2 没有clear永不发布再点击通知的完整UI场景 — 属实，现有RC测试只证明乱序缓冲/历史恢复合同；不宣称组合点击验收。用户本轮目标为通知阻塞隔离，该观察无已复现错误，补完整点击UI流程超出本次小范围必要验证。
  - `[false]` `[reject]` Intent observation 3 原生actor测试不是实体通知投递 — 描述事实而非源码缺陷；本轮从未宣称真实手机验收，产品端未改，结果继续明确该限制。
  - `[false]` `[reject]` Intent observation 4 源码不能证明当前运行包改变 — 描述事实而非交付偏离；当前指令要求build修复，未明确部署，计划与结果均说明运行包未更新。

Grouped corrections: one high wakeup issue (including its regression), one medium cooldown issue, one low direct checkpoint correction, and three medium verification gaps. No deferred review findings.

## Verification

director 按以上明确方案实现；engineer 独立核对差异及核心行为，发现局部明确失败由 director 修复。优先运行：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_remote_nats_unit.py tests/test_notification_contract_unit.py tests/test_chat_store_unit.py -q
.venv/Scripts/python.exe -m pytest tests/test_main_remote_nats_unit.py tests/test_story4_review_regressions_unit.py -q
```

RC 只跑新增/直接受影响的乱序 owner/revision 用例与原生 actor 合同用例；若不修改 RC 产品代码，不扩大为 UI 全套。另用 isolated SQLite fixture 构造与现场同型 seq=73011 的旧 blocked file + 多于 100 条 other + assistant_final，实际 transport FakeJetStream 验证通知独立发布并且持久重启后状态正确。

本轮完成标准：以上隔离、重试超过五次、重启、ACK 丢失、checkpoint、积压和关停验收均有独立证据。真实桌面切包与实体手机新回答通知必须独立报告部署版本和结果；未部署时明确源码修复完成、运行包未更新。生产队列恢复阶段不得删除/伪 ACK 旧事件，部署后以只读统计核对队列头部和新通知的 ACK/手机 ledger；真实模型或用户消息操作由根代理按当前授权处理。

## Auto Run Result

Status: built. 实施、dog 四视角审查、director 局部修复、engineer 独立复验完成。

### 已实现

- `chat_store.py`：notification/other SQL分类，兼容孤立行及旧blocked状态，明确区分永久非法数据与可重试发布故障，连续ACK前缀及MAX_INT64合法边界。
- `remote_nats.py`：两个独立发送worker，通知无需等待other，指数退避持续恢复，有界积压续发与运行中新增事实唤醒，启动不等待积压，关停取消并等待worker。
- `tests/test_remote_nats_unit.py`、`tests/test_chat_store_unit.py`：隔离、原字节重试、超五次恢复、非法通知保留、现场同型积压、重启、检查点、唤醒竞态、退避、读异常、启动与关停。
- RC仓库 `test/remote_nats_chat_service_test.dart` 与 `android/app/src/test/kotlin/com/example/zhuge_qa/RemoteBackgroundNotificationActorTest.kt`：只补乱序owner/revision与native ledger/watermark合同测试，未改手机产品代码。RC baseline `3233619ec010554f62e6591e71a531eaccbabb54`。

### 审查与复验

23条发现/观察逐项见 Review Triage Log。按根因共修复 high 1（唤醒竞态）、medium 4（退避及3项验证缺口）、low 1（直接SQL边界修正）；无deferred项。拒绝1条low组合UI验收建议：未确认错误且超出本次必要验证；拒绝2条false描述：实体测试与部署限制是已明确的事实，不是宣称完成的交付。未通过修改事实、删除队列或伪造ACK绕过问题。

engineer最终执行：
- 第一条Verification命令：101 passed，23.34s。
- 第二条Verification命令：31 passed、1既有失败，12.95s；仍是story4 close夹具缺 `_stop_answer_refresh_deadline`，main.py:22935。该main/test相对baseline未改，先前baseline隔离复现已核对，不扩大修复。
- RC定向Dart独立4 passed；原生actor XML独立核对15 tests/0 failures/0 errors；后续MC局部修复未改变RC文件，复用有效结果。
- 两仓库 `git diff --check` 通过。最终独验摘要：系统临时目录 `notification-outbox-engineer-final-verification.txt`。
- I/O矩阵8行均有实际通过断言。6个审查新增回归实际包含于101通过结果，engineer独立检查三项代码修正，无剩余核心疑点。

Follow-up review recommendation: false。虽然本轮修正high唤醒缺陷，但已由确定性Event回归和engineer最终独验覆盖，无可指出的未验证修复风险，不追加重复审查。

### 当前交付边界

源码修复并本地提交，不push。`D:/code/cx/mc`运行包、生产队列和实体手机未更新，当前现场行为尚未验证改变；共享网络/数据库整体故障仍会使实际发送等待恢复。跨通道允许通知先于其他事实到达，各通道有效事实顺序、原payload/event_id和手机启动watermark/去重规则保留。未宣称实体通知投递或完整产品回归通过。
