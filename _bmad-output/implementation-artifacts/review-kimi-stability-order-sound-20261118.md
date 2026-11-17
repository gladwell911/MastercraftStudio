---
status: complete
baseline_revision: 7ac8fca4c6673b0d5645d4b4a0be7b6a7e5fdd1c
review_mode: full
lenses_ran: [blind-hunter, edge-case-hunter, verification-gap, intent-alignment]
failed_layers: []
---

# Kimi 稳定性、顺序与完成音效独立审查

2026-11-18。全新 dog 协调；产品代码未修改。技能入口按要求仅渲染一次，返回工作流 `D:/code/sj/mc/_bmad/render/bmad-code-review/mc-fb7c9ac34cf9/4c202333bf6112210b18/workflow.md`。项目规则要求各透镜逐个执行，优先于技能并行要求。已有明确审查授权，未重复请求 checkpoint 确认。

范围：基线至工作树完整 diff，共 10 文件、+855/-33；无遗漏的未跟踪文件。审查冻结 diff：`C:/Users/gladwell/AppData/Local/Temp/mc-kimi-review-new-dog-20261118.diff`（76,602 字节）。计划为同目录 `plan-kimi-stability-order-sound-20261118.md`。完整 diff 包含计划和实施 narrative，盲审未单独读取 claims，但该 diff 内 narrative 构成盲法局限。未把已与基线逐项同断言复现的三项旧执行步骤失败算成本轮回归。

## 原始发现（保留原文；尚非裁决）

### blind-hunter / gpt-6-astra medium

- Finding floor: 76,602 bytes / 1,024 = 74.8066 kB; N = min(floor(√74.8066 + 1), 10) = 9.
- B1 — `main.py:15036–15050`: V2 recovery treats a legitimately running or queued prompt exactly like a missing final answer. `reconcile_v2()` returns `None` for both, and the worker eventually calls `_apply_kimi_error`; the existing V1 branch instead returns `"monitor"` for ongoing work. Return explicit running/queued/terminal states and preserve monitoring for unfinished tasks.
- B2 — `main.py:15036`: The new V2 early branch bypasses `_migrate_legacy_kimi_owner`, including recovery of unresolved submissions whose POST was accepted but whose response was lost. Such owners cannot match `triggerPromptId` using their unresolved local identity. Restore an unambiguous migration path before V2 final lookup and test an accepted POST followed by response loss.
- B3 — `kimi_transcript.py:32–46`: Both upsert handlers discard nested data supplied by the incoming object: `turn.upsert` always substitutes the old `steps`, and `step.upsert` always substitutes the old `frames`. A new turn containing complete steps, or a new step containing frames, becomes empty. Preserve incoming collections when provided and retain old collections only when omitted.
- B4 — `kimi_transcript.py:21–24, 75–76` and `kimi_server_client.py:1129`: A missing parent, missing frame, or append mismatch sets `incomplete=True`, but `apply()` still advances the sequence and returns `True`. `_consume_v2()` only invokes journal recovery when `apply()` returns false. Thus an ordinary contiguous malformed/incomplete update can permanently suppress results without requesting repair; moreover its sequence has already skipped the unrepaired operation. Propagate failure and retain the last fully applied cursor.
- B5 — `kimi_transcript.py:20–27`: A fresh transcript accepts an arbitrary first operation sequence because `self.seq is None` bypasses continuity validation. A first batch that happens to contain enough objects can therefore publish from an incomplete history. Require an authoritative baseline or fetch one before applying operations to a new transcript.
- B6 — `kimi_server_client.py:1136–1140`: Journal recovery clears `incomplete` before validating the returned batches and never checks that their final sequence reaches `latest_seq` or the triggering frame. An empty, truncated, or stale catch-up can consequently restore publishability while the observed gap remains. Verify contiguous coverage through the required sequence before clearing the flag.
- B7 — `kimi_server_client.py:1125–1128, 1180–1185`: An authoritative reset replaces the transcript but leaves `_v2_frame_texts` intact. If a reset corrects or truncates a running frame, its new text fails `text.startswith(old)`, immediately making the authoritative snapshot incomplete again. Reconcile/reset the per-frame emission state alongside the snapshot and test a reset that replaces previously emitted text.
- B8 — `kimi_server_client.py:709–714, 1155, 1169, 1191`: The new transcript, completed-turn, started-turn, and frame-text caches have no eviction or lifecycle cleanup. They retain full conversation snapshots and streamed text indefinitely across sessions, unlike the bounded completion identity cache in `main.py`. Bound retention and clear session data when it is no longer needed.
- B9 — `tests/test_kimi_server_client_unit.py:1397` and `tests/test_kimi_integration.py:1924`: The new “real client” coverage bypasses the actual connection handshake and successful subscription acknowledgement: it directly injects `server_hello`/transcript frames into an unstarted client. Add an isolated transport test exercising socket greeting, `client_hello`, matching successful `subscribe_v2` ACK, submission, reconnect/resubscription, and completion. Current tests cannot detect an incompatible live handshake or successful ACK shape.

### edge-case-hunter / gpt-6-astra medium

```json
[
  {"id":"E1","location":"kimi_server_client.py:1052-1071","trigger_condition":"A previously acknowledged session submits after socket invalidation, before reconnection finishes","guard_snippet":"Invalidate session readiness with the socket; require ACK for the current socket generation.","potential_consequence":"POST proceeds without a live subscription, leaving the accepted request without streamed completion."},
  {"id":"E2","location":"kimi_transcript.py:38-75","trigger_condition":"An operation references missing parents or contains conflicting append text","guard_snippet":"Return False when incomplete becomes true; recover from the last fully applied sequence.","potential_consequence":"apply returns True, bypassing immediate recovery while subsequent results remain suppressed."},
  {"id":"E3","location":"kimi_transcript.py:21-27","trigger_condition":"The first transcript operation arrives without an authoritative reset","guard_snippet":"if self.seq is None: self.incomplete = True; return False","potential_consequence":"An arbitrary initial sequence is accepted without proving preceding transcript operations were received."},
  {"id":"E4","location":"kimi_transcript.py:28-75","trigger_condition":"The server sends an unrecognized transcript operation","guard_snippet":"else: self.incomplete = True; return False","potential_consequence":"The cursor advances past ignored changes, allowing stale transcript content to appear complete."},
  {"id":"E5","location":"kimi_server_client.py:1152-1155,1174-1191","trigger_condition":"A long-lived client processes increasing numbers of turns and streamed frames","guard_snippet":"Retire frame text and started identities on completion; bound completed identities and inactive transcripts.","potential_consequence":"Completed identities, transcript state, and accumulated frame text grow without a retention bound."},
  {"id":"E6","location":"kimi_runtime.py:44-52","trigger_condition":"Two preparations target the same destination concurrently","guard_snippet":"Create the target exclusively; remove it on failure only when this invocation created it.","potential_consequence":"One invocation can overwrite or delete the other invocation's validated runtime."}
]
```

### verification-gap / gpt-6.1-sol high

#### V1 — V2 append reconstruction has no normal regression coverage

- **Changed surface:** `Transcript.apply` reconstructs text from append offsets, including overlap and conflict handling — `kimi_transcript.py:61`.
- **Impacted consumer or site:** `_consume_v2` publishes the reconstructed text as an authoritative completed answer — `kimi_server_client.py:1150`.
- **Existing test evidence:** `Regression gap`. Repository-wide Python searches for `Transcript`, its imports, `transcript.ops`, and `transcript.reset` locate the new coverage in `tests/test_kimi_server_client_unit.py` and `tests/test_kimi_integration.py`. The journal test at `tests/test_kimi_server_client_unit.py:1469` uses only upserts with complete text. The integration test at `tests/test_kimi_integration.py:1921` supplies an already-completed snapshot. Neither executes an append. The real streamed-answer smoke at `tests/test_kimi_live_smoke.py:78` is skipped unless `KIMI_LIVE_TEST=1`.
- **Missing verification:** Assert that append operations, overlapping replay, and final state updates produce exactly the complete answer, while conflicting fragments prevent publication.
- **Demonstration:** Dropping the append branch would leave initial frame text unchanged. All the normal V2 tests above would still pass because they supply complete text through snapshots or upserts.
- **Consequence:** A streamed answer could be published as an authoritative truncated answer without those tests failing.
- **Disposition:** `patch` — add an append-driven case to `tests/test_kimi_server_client_unit.py`, asserting the emitted completed-event text and conflict suppression.

#### V2 — Explicit message-order precedence is unverified

- **Changed surface:** Answer projection now prefers `question_order` and `answer_order` over timestamps — `main.py:6293`.
- **Impacted consumer or site:** `_render_answer_list` uses this projection to rebuild the visible conversation — `main.py:6475`.
- **Existing test evidence:** `Regression gap`. Repository-wide Python searches for `question_order`, `answer_order`, and `_next_answer_message_order` found no tests directly configuring or asserting these fields. The late-answer tests at `tests/test_answer_list_time_rows_unit.py:21` and `tests/test_answer_presentation_ui_automation.py:161` configure distinct timestamps and omit sequence fields. The completion test at `tests/test_answer_list_time_rows_unit.py:55` checks `answer_at`, but does not check the allocated order or resulting projection.
- **Missing verification:** Assert message order when explicit sequences disagree with timestamps, including after replay or reconstruction.
- **Demonstration:** Replacing sequence precedence with timestamp-only sorting would still satisfy both late-answer tests. A fixture where sequences specify Q0/Q1/A0 but timestamps specify Q0/A0/Q1 would expose the regression.
- **Consequence:** The stable-order contract could regress while the new ordering tests remain green.
- **Disposition:** `patch` — extend `tests/test_answer_list_time_rows_unit.py` with conflicting timestamps and explicit sequence values, asserting Q0/Q1/A0 after rebuild.

#### Other findings

- V3 — Removing the turn pre-slice makes `_render_answer_list` format every historical answer before applying the visible-row limit (`main.py:6473–6480`). The existing test `tests/test_main_unit.py:19149` supplies 500 turns and asserts at line 19166 that formatting calls do not exceed the visible limit. The changed loop necessarily formats all 500, violating that assertion and restoring work proportional to total history on each rebuild.
- V4 — The real prompt smoke still calls `live_client.get_status(session_id)` at `tests/test_kimi_live_smoke.py:100`. With the newly supported protocol 2, `get_status` unconditionally raises at `kimi_server_client.py:964`, so this smoke cannot pass against the pinned 2.5.3 runtime even after a successful answer.

### intent-alignment / gpt-6.1-sol high

1. **可辩护的意图读法**
   - **分析与方案交付**：“继续分析根因、应该如何解决”可以理解为要求 dog 给出可核查的根因和长期方案，未必单凭这句话就要求部署。
   - **日常运行稳定**：“彻底解决、保持稳定”也可以理解为用户实际使用的 MC 不再被 Kimi 升级破坏，而非只修复当前版本协议。
   - **长任务追问的可见顺序**：任务未完成时发送“做到哪一步了”，随后出现的回复应位于回答列表尾部。这既可指原任务最终回答晚到，也可指对追问产生的即时状态回复。
   - **实际听到提示音**：“回复了，但没有播放回复的音效”描述的是用户可感知的音频结果，而不只是程序调用了播放函数。

2. **diff 实现的读法**

   实现的是“当前 2.5.3 的源码适配，加上避免共享 CLI 升级影响 MC 的隔离机制”：新增 V2 订阅、transcript 解析和恢复；固定独立二进制的版本与哈希，禁用子进程自动更新；按问题和答案的独立顺序排序；steer alias 共用一个最终答案；把完成音调用移到 UI 后处理之前的共同接纳边界。

   排序具体采用“问题提交时、最终回答接纳时”的消息顺序。音效具体保证“成功答案接纳后调用一次 `_play_finish_sound`”，并记录调用或异常。

3. **意图期待的表面与 diff／测试表面的差异**
   - I1 — **稳定性**：用户期待落在日常安装后的持续运行。diff 落在源码、固定运行时准备入口和明确拒绝不兼容版本；测试用临时文件、伪造版本输出和模拟 HTTP/WS。文档明确日常运行时尚未安装，未打包、切换或重启 MC；真实云端新请求和长期运行仍待验收。固定版本隔离覆盖“共享 CLI 升级改变依赖”，并不等同于持续兼容云端服务。
   - I2 — **追问顺序**：用户期待落在真实长任务与追问交互后的可见列表。新增单元和原生 UI 测试直接构造 Q0、Q1，再把 A0 的正文与时间写入内存，验证重建为 Q0/Q1/A0及焦点保留。它们覆盖“原回答晚到”的投影；没有在真实运行任务中提交状态追问、观察其即时回复。alias 测试则把追问解释为并入同一 owner，最终只保留一个答案，未表达“追问本身收到独立状态回答”的读法。
   - I3 — **声音**：用户期待落在扬声器实际发声。新增测试以列表或计数器替换 `_play_finish_sound`，证明 UI 后处理抛异常时仍请求播放，重放不重复请求。它没有验证 Windows 播放、设备输出或异步音效覆盖。diff 中的分析也明确：协议终态未接纳能解释一次无声，UI 异常由故障注入证实，但历史“答案已显示却无声”的具体现场未全部定位。
   - I4 — **影响范围**：用户问题针对 MC 中的 Kimi；排序实现位于通用回答列表及通用 pending/done 方法，因此变更表面涉及其他 provider 的消息顺序。远程 payload 增加顺序和 alias 字段，但没有修改其他客户端的消费逻辑；桌面列表验证不等同于跨端显示验证。

这些差异在新增文档中大多被明确标为交付边界；该 diff 没有宣称已完成日常部署、物理声音或长期稳定性验收。

## 审查结论

**Code review complete：0 decision-needed、11 patch、2 defer、9 rejected。** 四透镜全部完成，无 failed layer。11 个 patch 中包含 4 项验证/测试适配工作，不表示 11 个独立现场故障。当前不能作为 clean review 或实施完成证明；优先修复长任务恢复、未决提交归属、UTF-16 append 和断连 ACK 门禁。

结果按本次明确要求只保存在本报告；技能的计划/deferred 重复落盘合并到这里，不修改计划 status、不另建 deferred-work，不启动修复流程。父代理已获授权使用下一轮 bmad-build-auto 派 director 修复，无需再次询问是否修复。

## 逐项裁决（先裁决，后分组）

每条原始发现均保留。编号为统一顺序；B/E/V/I 保持来源映射。

| # / 原ID | 来源 | verdict | 核实证据及路由 |
|---|---|---|---|
| 1 B1 | blind-hunter | high | `reconcile_v2`只返回terminal event或None，丢弃已匹配turn的running/queued状态（`kimi_server_client.py:1097`）；`main.py:15036`收到None后设置terminal_wait，期限到达必经15174附近`_apply_kimi_error`。V1在15115附近明确返回monitor。恢复协调器给每轮设置短deadline（14960附近），故正常长任务断连恢复会被误判失败。patch R1。 |
| 2 B2 | blind-hunter | high | `main.py:11565`在POST结果不明时创建`__unresolved__:`本地prompt，并启动恢复；15036的V2提前分支仅按该本地ID找triggerPromptId，绕过15080的legacy/unresolved迁移。服务端真实prompt ID不等于该本地占位，无法只读恢复已经接纳的任务。patch R2。 |
| 3 B3 | blind-hunter | false | 实际2.5.3 schema字节110012000–110024500：`turnHeaderSchema=transcriptTurnSchema.omit({steps:true})`、`stepHeaderSchema=transcriptStepSchema.omit({frames:true})`。两种upsert不携带相应嵌套集合；保留旧集合正确，所提坏输入不属于固定协议。reject。 |
| 4 B4 | blind-hunter | medium | engineer纯内存验证：reset(seq=1)后seq=2缺frame或冲突append，均返回True、seq推进为2、incomplete=True；真实`_consume_v2`只在False分支恢复，fake HTTP调用为[]。另外N1证明合法非BMP输入可真实触发同一不完整状态，非仅任意损坏输入。patch R3。 |
| 5 B5 | blind-hunter | false | 固定2.5.3 broadcaster `subscribeTranscript`（字节111527911起）在没有transcript_since且新订阅从off到delta时发送reset；client仅在已有seq且完整时发送since（1059–1062）。实际首次订阅先有权威基线，未证明所指任意首ops状态可达。reject。 |
| 6 B6 | blind-hunter | maybe-false | 源码确未核latest_seq，但候选要求服务端`complete:true`同时返回不完整/过期batches。当前固定服务的API把该标志作为完整性契约，现有证据未证明其违约；不能据假造响应新增防御。若有同版本真实可达的complete=true而覆盖不足，才确认medium。defer D1。 |
| 7 B7 | blind-hunter | maybe-false | reset确未清frame_texts，新文本非旧文本前缀时会置incomplete；但本轮尚未证明固定服务在同一frame身份上会合法回退/替换此前已发文本。需要真实2.5.3重置/修正轨迹或producer可达路径，若成立为medium；不凭猜测增新机制。defer D2。 |
| 8 B8 | blind-hunter | medium | 全文件检查_v2_transcripts/_v2_completed/_v2_started/_v2_frame_texts只初始化、追加；close和失效路径无会话释放。正常连续turn每帧留存文本，完成后仍保留，且每次事件重新遍历快照。无需猜测OOM阈值即可确认新增对象随历史增长；影响用户明确要求的持续稳定性。patch R4。 |
| 9 B9 | blind-hunter | medium | 单测实际以ws_factory绕过真实greeting分支，integration直接注入完成snapshot；已有一次无凭据真实握手证明当前初次ACK有效，故不能说当前握手已坏。但正常回归没有覆盖greeting→成功ACK→重连新ACK→完成的生命周期，E1已揭示该边界实错。按验证缺口patch R5，不重复真实云端验收。 |
| 10 E1 | edge-case-hunter | high | engineer用现有started_client fake，经过server_hello→subscribe→匹配ACK，再调用实际`_invalidate_ws(recover=True)`并暂停恢复worker：_ws=None但ready仍含s、Event仍set，submit_prompt发生fake POST并返回p。过期ready可绕过当前连接订阅门禁。patch R6。 |
| 11 E2 | edge-case-hunter | medium | 与#4同一apply成功标志/游标缺陷，缺frame及冲突文本均经局部probe确认。patch R3。 |
| 12 E3 | edge-case-hunter | false | 与#5相同，实际初订阅reset先于ops；未显示可达首ops无基线状态。reject。 |
| 13 E4 | edge-case-hunter | false | 当前固定schema余下的marker/interaction/attachment/prompt upsert为非最终正文投影信息，未知未来op不在当前固定协议。未证明忽略它们会使现有final正文变旧却误发；prompt状态缺失的真实问题已由#1单独确认，不能借此证明原“未知op”主张。reject。 |
| 14 E5 | edge-case-hunter | medium | 同#8，新增长期缓存缺少结束/会话清理，源代码足以确认增长路径。patch R4。 |
| 15 E6 | edge-case-hunter | low | 两个显式prepare同时通过exists再copy确有竞争；本工具为人工本地准备，日常使用几乎不会同时安装同一路径。按技能低影响/罕见且需新增并发门禁分支的拒绝规则，不新增锁或临时发布机制。reject。 |
| 16 V1 | verification-gap | medium | 依技能信任该层已核查的测试证据：普通V2回归没有append，删掉append分支仍可绿；N1又证明该缺口已掩盖合法输入缺陷。patch R7。 |
| 17 V2 | verification-gap | medium | 依该层搜索证据：新顺序用例只配时间、不配显式顺序；去掉sequence优先仍能通过。稳定序列为本次功能核心，需真实重建/重放边界断言。patch R8。 |
| 18 V3 | verification-gap Other | medium | engineer单跑`test_render_answer_list_does_not_format_all_old_turns_when_bounded`：1 failed，formatted=500，visible limit=100。改动把排序前的昂贵正文格式化扩大到全部历史，影响长会话UI响应。patch R9。 |
| 19 V4 | verification-gap Other | medium | 已读live smoke第100行无条件get_status；client964对V2无条件raise，固定2.5.3无法通过该检查。未运行真实云端，用调用路径即可证明测试适配错误。patch R10。 |
| 20 I1 | intent-alignment | false | 作为“本轮未完成已授权范围”的缺陷不成立：当前授权明确不部署、不发新云端消息；文档如实列日常安装及长期稳定为待验收。保留描述边界，不新增修复/部署。reject。 |
| 21 I2 | intent-alignment | false | 已接受实施计划明确将成功steer并入owner、独立queued结果各自发布；本轮按该契约实现。真实追问即时回复未验收是现存验证边界，没有足够证据要求另造独立答案语义。reject。 |
| 22 I3 | intent-alignment | false | 调用测试不等于声学验收这一描述正确，但实现与文档未宣称实际听到或全部历史无声根因已解决；授权范围也不含物理声学验收。作为代码缺陷reject，保留待验收。 |
| 23 I4 | intent-alignment | false | 计划采用共享消息投影并明确旧远端忽略新增顺序字段的边界；仅“影响通用方法/未改手机”不能证明语义回归。唯一已证实通用回归为#18，独立修复；不追加其他provider/手机改造。reject。 |
| 24 N1 | verification-followup | high | 合法append的offset单位不匹配。固定二进制producer字节111605871 `offset:open.offset`，111606043 `open.offset += delta.length`，111606794 `offset:frame.text.length`；consumer109952397后appendAtOffset用JS.length/.slice，均为UTF-16单位。Python第61行却用len/code point切片。emoji前缀长度JS=2/Python=1，offset=2的合法后续x使apply返回True、incomplete=True且丢x。patch R11。 |

## 按根因分组的最小修复要求

下述各组均为patch，不需新产品选择，不应扩成整轮重写。director修改，engineer只复验受影响范围；与其他组共享用例时一次验证即可。

| 组 / 来源 | 精确入口 | 最小修改要求 | 必须验证的局部行为 |
|---|---|---|---|
| R1 / B1 / high | `main.py:15036`；`kimi_server_client.py:1093` | V2恢复返回可区分的运行/排队/终态结果；已证明仍running/queued/blocked时保留owner并进入现有monitor，不消耗短终态等待预算后标失败。只有明确终态缺正文才用有限补齐。 | 合成running、queued、blocked transcript进入实际owner recovery，超过原短恢复预算仍pending；随后合法final只接纳一次。终态缺正文/真实错误仍有界。 |
| R2 / B2 / high | `main.py:11565,15036,15080` | V2路径先恢复legacy/unresolved的真实prompt归属；复用已核对的只读元数据/现有迁移规则，只接纳唯一候选，歧义保留明确状态。禁止重POST，也不可用任意最后assistant。 | POST accepted但返回丢失→本地__unresolved__→只读定位真实prompt→唯一终答；两候选歧义不误归属，POST计数仍1。旧owner有稳定session/prompt时正常恢复。 |
| R3 / B4+E2 / medium | `kimi_transcript.py:38–76`；`kimi_server_client.py:1129` | apply一旦检测缺父项/冲突就返回失败，不能推进“完整应用”游标；避免半批变更被当基线（可采用最小事务式应用或直接权威reset恢复）。client必须触发已有有界补齐。 | 缺frame、冲突append经真实client入口触发repair；repair前无final，repair后完整正文仅一次；游标不跳过失败批。 |
| R4 / B8+E5 / medium | `kimi_server_client.py:709–714,1155,1169,1191` | 在实际完成/会话结束或client关闭边界释放不再需要的frame文本与started记录，对完成去重和保留transcript设局部有界保留规则；保留pending owner及恢复必需状态，不加通用缓存平台。 | 多轮完成后辅助缓存不继续线性保留全部已完成正文；pending帧仍可续传，同一完成重放仍不重复通知，close释放自有状态。 |
| R5 / B9 / medium（验证） | `tests/test_kimi_server_client_unit.py:1397`；真实连接分支`kimi_server_client.py:1404` | 增加隔离socket greeting及成功订阅ACK生命周期回归，复用现有fake/本地无凭据边界，不需要真实云端。 | server_hello→client_hello→目标ACK→允许提交；断连→旧ACK无效→新ACK后允许；最终事件走client到消费者。与R6用例合并执行。 |
| R6 / E1 / high | `kimi_server_client.py:1052–1071`；`_invalidate_ws` | ready与当前socket generation绑定；socket失效即撤销该代ready/Event；发送门禁只能认可当前代目标session ACK，保留有界超时且不重发已接纳请求。 | 使用实际invalidate路径暂停重连，已旧ACK的session不可POST；旧代迟到ACK不能解锁，新代ACK后只发送一次。 |
| R7 / V1 / medium（验证） | `tests/test_kimi_server_client_unit.py:1469`；`kimi_transcript.py:61` | 增加append驱动的正常流到completed事件断言，不仅upsert完整文本；覆盖重叠重放、冲突和UTF-16边界。 | 精确完整正文与一次final；删append/改offset单位的代表性错误能被抓住。与R3、R11共同复验。 |
| R8 / V2 / medium（验证） | `tests/test_answer_list_time_rows_unit.py:21`；`main.py:6293` | 在既有列表测试增加显式question_order/answer_order与时间矛盾的fixture，断言序列优先及重放不换序；无需新测试框架。 | sequence指定Q0/Q1/A0而timestamp指定Q0/A0/Q1，重建/分页及保存重载后仍Q0/Q1/A0；重复完成不改answer_order。 |
| R9 / V3 / medium | `main.py:6473–6480`；`tests/test_main_unit.py:19149` | 在昂贵Markdown格式化前先按轻量消息身份/顺序选可见尾页；不能简单恢复旧按turn截断，否则再次丢晚到A0。保持焦点与时间行。 | 现有500轮有界格式化测试通过，同时旧turn晚到答案仍入尾页，Q0/Q1/A0与原生选中/焦点测试不退化。 |
| R10 / V4 / medium（测试适配） | `tests/test_kimi_live_smoke.py:100`；`kimi_server_client.py:964` | smoke按协议版本用V2 transcript/prompt权威终态检查，V1才调用旧status；保留测试原有能力意图，不放宽为“有任何消息”。 | 无网络fake验证V2不调用旧status、仍断言目标prompt成功；真实live保持opt-in，本轮不发送云端请求。 |
| R11 / N1 / high | `kimi_transcript.py:61–69` | 按V2实际UTF-16 code-unit offset应用append与重叠校验；Python内部可继续存str，但不能直接把协议offset当Python索引。限定本协议转换，避免调整无关provider。 | emoji/非BMP前缀后连续append得到完整文本；重复/部分重叠、冲突、合法代理对边界处理；最终client完整答案可接纳，正常中文/ASCII不退化。 |

## Deferred（仅保留证据边界，不加入本轮修复）

- D1 / B6 / 未核实medium：需要固定2.5.3真实`complete:true`但batches未覆盖所需seq的可达证据。当前仅任意伪造坏响应，不能据此新增防御或扩大调查。
- D2 / B7 / 未核实medium：需要同一frame ID的权威reset确实合法缩短/替换此前已发文本的producer路径或脱敏轨迹。当前只确认代码条件行为，未确认服务端触发条件；不另建重播机制。

## Rejected

- B3 false：真实upsert header schema排除嵌套集合。
- B5、E3 false：固定服务初订阅发送reset，client仅有完整游标时请求增量。
- E4 false：固定协议未展示未知正文变更op；已知忽略项不改变最终正文。
- E6 low：双人工prepare并发非日常场景，新增并发门禁成本不合本轮范围。
- I1 false：日常部署/长期运行明确不在本轮已授权验收内。
- I2 false：已接受计划定义steer alias单终答语义；真实即时追问行为仍为边界。
- I3 false：文档未把播放调用测试声称为物理出声。
- I4 false：共享投影及旧远端边界已明确；没有独立证明新增跨provider/手机缺陷。

## 独立验证证据与边界

- engineer精确运行现有`tests/test_main_unit.py::test_render_answer_list_does_not_format_all_old_turns_when_bounded`，1 failed in 0.88s；断言500 <= 100失败。该项为本轮新增回归，不属于此前三项旧执行步骤失败。
- 两种seq=2坏append的纯内存probe均`apply=True, seq=2, incomplete=True`；client未调用任何恢复HTTP。
- ACK probe使用现有fake启动器及实际`_invalidate_ws(recover=True)`，仅暂停恢复调度来固定真实窗口；失效后_ws=None、ready={s}、Event=set，submit实际发生fake POST并返回p。
- UTF-16依据为固定本机二进制的producer及consumer代码，不来自猜测schema；Node与Python长度对照及emoji合法append复现均通过。无真实会话正文进入证据。
- 全部测试和probe均隔离，不启动日常MC、不写日常聊天/笔记库、不发送真实云端消息、不打包；engineer三个Temp探针已精确清理。保留本报告及唯一审查冻结diff/claims作为必要审查证据。
- 原engineer的7项V2/运行时、9项完成/alias/音效、2项顺序/UI定向通过仍只证明其覆盖路径；不能抵消本报告发现。真实新请求、扬声器物理声音、日常安装与长期运行仍未验收。
