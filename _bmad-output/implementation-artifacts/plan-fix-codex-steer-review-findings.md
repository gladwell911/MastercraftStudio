---
title: '修复 Codex steer 独立审查发现'
type: 'bugfix'
ticket: ''
created: '2026-10-02'
status: 'built'
baseline_revision: 'c5f8d726413f3b4d899a530a2312605d0783656f'
route: 'full'
route_source: 'auto'
review: 'quick'
review_source: 'user-scope'
lenses_ran: [quick]
review_loop_iteration: 0
followup_review_recommended: false
context: []
warnings: []
deferred:
  - summary: '最新 completion owner 失效可能阻断 fan-out'
    location: 'main.py:13351'
    severity: 'unverified medium'
    evidence: 'Original deferred record: no verified write chain at planning time; failed steer does not register a new owner.'
    resolution: 'Resolved in this run: related-item capacity error proved the chain reachable. Completion-owner fan-out now precedes individual status/generation checks; independent worker-entry tests pass for valid and stale earlier owners.'
---

<intent-contract>

## Intent

**Problem:** 独立审查证实旧 item 缓存淘汰后错归新问题、失败 steer 丢失旧任务新完成回答、失败终态错误污染正式答文；两个跨边界回归缺少行为断言。

**Approach:** 局部修复稳定归属、失败输入隔离及正式正文筛选，补齐消息入口和重载回归，保留已实现的追加回答顺序。

## Boundaries & Constraints

**Always:** 身份守卫及缓存有界；prior合法回答可见，未接受新输入结果不发布；GUI串行，隔离app/history/notes；director实现→engineer独立验证。

**Never:** 改schema、Kimi契约、真实数据、消息架构；打包或推送。未证明的触发链不以手工注入状态当缺陷。

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| 归属容量 | 旧回答后超过256工具item，再steer及旧item迟到 | 旧owner不变，新回复仍归新问题 | 有界容量耗尽显式失败，不能猜新owner |
| steer失败/overflow | RPC期间旧任务首次final，随后新user边界及新结果 | 保留prior合法final，不发布未接受后缀 | 错误只作用本次请求 |
| native失败 | 已有正文+failed completion.error.message | 正文及完成item元数据不含错误 | 保留现有终态处理契约 |
| UI入口 | 顶层owners含两个pending local turn | 两者完成，无等待残留 | 不放宽身份守卫 |
| reload | pending部分回答保存重载 | 同item不重复，不同item继续追加 | 原文本完整保留 |

</intent-contract>

预计产品与回归合计超过100行，采用full；现有证据足够实施，无需重新全仓分析。

## Code Map

- `codex_worker_process.py:97–328` — `_on_event/_dispatch_event/_handle_start_turn`；现有锁、ACK顺序、native owners、startup有界buffer可复用。工具item与稳定user/assistant归属分开；活跃任务相关owner不采用淘汰后current兜底。相关item也须有界，耗尽时显式错误/拒绝未知归属，保留已知owner。
- `main.py:9892,13351–13411,13498,13580,15945` — completed helper、terminal两路径、owners复制入口。仅权威completed回答写正文；真实`codex_client.py:1024`将error.message赋予terminal.text。成功terminal通常无正文。
- `tests/test_codex_worker_process.py` — 复用FakeClient与真实`_event_from_item`；失败前旧final和边界后未接受新final必须成对覆盖。
- `tests/test_codex_integration.py`、`tests/test_codex_ui_responsiveness_automation.py` — 复用隔离frame/store，UI入口须经过顶层payload转换；保留真实列表验收。

## Tasks & Acceptance

**Execution:**
- [x] `codex_worker_process.py` — 稳定记录真正需要归属的user/assistant item，工具量不挤掉旧答；相关容量极限安全显式处理。失败/overflow按待确认用户边界区分prior前缀与未接受后缀，保留旧final；不能在异常时回放所有新事件。
- [x] `main.py` — 成功完成正文与失败终态错误分离，当前/归档一致；核查最新owner失效真实写入链，只有证明可达才局部调整fan-out，否则保持deferred。
- [x] `tests/test_codex_worker_process.py`、`tests/test_codex_integration.py`、`tests/test_codex_ui_responsiveness_automation.py` — 覆盖矩阵；至少两个pending经真实worker消息入口收尾，pending重载后执行去重；GUI核对列表正文与位置。

**Acceptance Criteria:**
- Given 旧问旧答和已接受新输入，when 工具量超过原缓存上限且旧item迟到，then 可见列表旧问→旧答→新问→新答，旧答不进入新答。
- Given steer期间旧final及未接受新结果，when RPC失败或overflow，then 原问题保留合法回答，新问题不展示未接受结果；切聊天及重载一致。
- Given 已有正式回答，when native失败，then 列表和重载正文不追加error.message。

## Implementation Notes

## Plan Change Log

- 2026-10-02: The previously deferred fan-out condition is now verified through the real accepted-owner chain: bounded related-item capacity emits a latest-owner scoped error, the worker error entry marks that owner failed, and native completion still carries that latest owner as its top-level scope. Move completion-owner expansion ahead of individual-owner status/generation checks. Preserve shared chat/native identity checks, exact per-owner thread/native/model checks, client checks at the dispatch boundary, and all original checks during recursive owner delivery. A failed latest owner remains failed; only a valid earlier owner completes, and stale generations remain pending. This is a local main.py correction with worker-message integration coverage; worker, Kimi, DB, and real data are unchanged.

- 2026-10-02: Local review proved the previously unverified fan-out risk reachable through the new scoped item-capacity error. The worker error fails the latest accepted local input; native completion still uses that input as its top-level scope, preventing earlier pending inputs from completing. Include a local correction: expand accepted completion owners before individual status/generation checks, preserving chat/native/thread/client identity and applying the existing full guards recursively per owner. Keep failed owners failed and stale generations rejected; add a real top-level worker error then completion regression.

## Review Triage Log

### 2026-10-02 — Local review
- Scope: one focused dog review plus independent engineer verification under the user's proportionate-review instructions; extra blind/edge/verification/intent lens rounds skipped.
- verdicts: 1 finding — high 0, medium 1, low 0, false 0, maybe-false 0
- findings:
  - `[medium]` `[patch]` Latest failed owner blocks accepted completion owners — dog reproduced actual ACK/user boundary/capacity error then completion, yielding pending/failed instead of done/failed. Director moved fan-out before individual guards while retaining shared identity and recursive per-owner guards. Engineer independently verified valid earlier owner done/failed and stale earlier owner pending/failed.

## Verification

所有命令cwd `D:/code/sj`；不得重跑已知COM挂起的宽integration。

- `mc/.venv/Scripts/python.exe -m pytest mc/tests/test_codex_worker_process.py -q` — worker核心归属/ACK/失败通过。
- `mc/.venv/Scripts/python.exe -m pytest mc/tests/test_codex_integration.py -k 'steer_completed or legacy_completed or native_completion or worker_completion_payload or reloaded_pending or failed_completion' -q` — 入口、失败正文、重载通过；新增测试名使用这些关键词。
- `mc/.venv/Scripts/python.exe -m pytest mc/tests/test_codex_ui_responsiveness_automation.py -k 'steer or switched_chat_final' -q` — 隔离原生列表顺序通过。
- `git -C mc diff --check` — 无格式错误。



### Director implementation and self-verification (2026-10-02)

- Relevant user/assistant ownership is retained per native turn; tool items do not consume this capacity. At the bounded item limit, unknown relevant items lose scope and emit an explicit scoped error, while known items retain ownership. Native-owner retirement removes its item records.
- During a steer RPC, known old items and the prior prefix before the new user boundary remain deliverable. Prior terminal closes the prefix. Failure/overflow discards the unaccepted suffix, including its terminal, rather than replaying it.
- The shared completed-answer helper rejects unsuccessful native terminal text before writing answer content or completed-answer metadata; current and archived handling use the same helper.
- Regressions exercise more than 256 tool items, relevant-item capacity exhaustion, first prior final before failed/overflowed steer, the top-level worker completion-owner payload with two pending local turns, actual pending reload followed by item deduplication/append, failed terminals, and native list order/switching.
- Deferred latest-owner issue unchanged: failed steer never appends an accepted owner; no verified real write chain invalidating only the latest accepted owner was established. Identity guards remain intact.

Self-checks, cwd D:/code/sj:

- Worker: 41 passed.
- Selected integration: 7 passed, 31 deselected.
- Selected native GUI: 3 passed, 31 deselected, run serially after integration.
- git -C mc diff --check: passed.

Initial self-check failures were local test setup issues: a source test assumed repository cwd despite the plan's parent cwd; pending persistence used metadata-only upsert without replace_turns; the expanded GUI owner payload required adding its third local turn. These were corrected and the affected checks passed. Independent engineer verification is pending. No packaging, push, schema, Kimi, or real-data changes.

### Director local follow-up (2026-10-02)

- Moved the existing completion-owner fan-out before per-owner failure/generation early returns, with shared native-chat compatibility checked before expansion. Each accepted owner continues through the unchanged recursive identity guards.
- Added real top-level worker-message regressions delivering latest scoped error followed by two-owner completion: valid earlier owner => done/failed; stale earlier generation => pending/failed. The latest error text is retained.
- Ran only affected integration cases: steer_completed, native_completion, worker_completion_payload: 5 passed, 35 deselected. git diff --check passed. Full independent verification remains with the parent workflow.

## Auto Run Result

- Status: built. Repaired the three confirmed product defects and two regression gaps from the independent dog review. Tool traffic no longer evicts stable user/assistant owners; the bounded capacity rejects unknown ownership explicitly. Failed/overflowed steer preserves the legitimate prior prefix and discards unaccepted results. Unsuccessful terminal errors never enter completed-answer content or metadata.
- The previously unverified completion-owner risk was proven reachable through the new scoped capacity error and repaired locally. Shared chat/native/client checks remain, and each owner retains exact status/thread/model/generation checks. Failed owners remain failed; stale owners remain unchanged.
- Files: `codex_worker_process.py` fixes ownership retention and failed-steer isolation; `main.py` filters terminal error text and expands completion owners safely; three Codex test files cover tool pressure, bounded capacity, failure isolation, worker message entry, pending reload and visible list behavior. The original plan and deferred log record resolution of the review actions.
- Review: one local dog quick review found one medium patch, now independently verified; no new findings deferred or rejected. Extra lens rounds were skipped under the user's proportionate-review instructions. The original deferred entry is retained with its resolution for history; no open deferred item remains from this task.
- Final independent evidence, cwd D:/code/sj: worker **41 passed**; selected integration **9 passed, 31 deselected**; serial native GUI **3 passed, 31 deselected**. Every I/O matrix row ran and passed. Worker/GUI were unchanged by the final main-only patch, so their valid results were reused. Diff checks passed.
- Follow-up review recommended: false. This local review patched one medium entry; its real worker error/completion entry and generation guards have independent coverage.
- Limits: no full integration run, real-model end-to-end run, package deployment or repair of existing production history. App/history/notes were isolated. No push.
- Finalization: local commit of the reviewed changes and task records; working tree must be clean.
