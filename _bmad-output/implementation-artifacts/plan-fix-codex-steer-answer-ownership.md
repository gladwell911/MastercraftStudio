---
title: '修复 Codex 追加输入后的回答归属与覆盖'
type: 'bugfix'
ticket: ''
created: '2026-10-02'
status: 'built'
baseline_revision: 'cd6ab9beb341ba69840a49f8d459cd93c281c255'
route: 'full'
route_source: 'auto'
review: 'quick'
review_source: 'user-scope'
lenses_ran: [quick]
review_loop_iteration: 0
followup_review_recommended: false
context: []
warnings: [oversized]
deferred: []
---

<intent-contract>

## Intent

**Problem:** 指挥塔聊天中，19:07 的“群号187387007”新增本地问题，但旧 native turn 的后续回答仍更新旧记录，出现在新问题上方并覆盖旧答；新问题保持 pending，真正终答也未显示。

**Approach:** 为已确认接收的 steer 输入建立明确的本地回答归属边界，保留原 item 的 owner；仅完成的主代理回答更新列表，并保留不同完成回答及终答。

## Boundaries & Constraints

**Always:** 保持问题发送顺序、旧回答正文、chat/model/thread/native turn/context generation/client 身份守卫；当前与归档聊天采用相同结果逻辑；原生 GUI 验证串行，测试隔离 notes 和 app/history 数据。

**Never:** 不把同一 native turn 的全部事件重新绑定最新 local turn，不以最新列表记录兜底未知事件；不改真实历史、打包目录、安装包或手机端，不改数据库 schema，不重构通用消息架构。

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| 同一任务追加输入 | 旧问题已有回答；steer 成功且返回同一 native turn | 新输入拥有新 local record；确认的输入边界后新 assistant item 归新记录；旧 item 迟到事件仍归旧记录 | 未确认归属的事件不猜测 owner |
| ACK 格式 | 嵌套 turn.id、turn_id、turnId；成功 steer 无 ID | 三种合法键均解析；无 ID 的成功 steer 仅可回退本次 expected native turn | fallback start 必须取新 start 的 ID，不能沿用旧 ID |
| ACK 前事件 | steer 调用中收到旧 item、新输入 item、新回答 item | ACK 先于新 owner 事件；原 item 归属不变；新输入边界按确认接受后事件顺序生效 | 失败/overflow 保留原 owner、不发布失败请求的新结果 |
| 异步提问与终答 | 新记录先收到一个 completed final_answer，之后另一个 completed final_answer | 新记录答文按完成 item 顺序保留两段；同 item 重放不重复，delta 不覆盖 canonical 答文 | 无 item ID 的旧协议保持安全兼容，重复完整文本不追加 |
| native 完成 | 一个 native turn 对应多个已接受 local 输入 | 属于该任务的待完成 local 输入结束等待；真终答不被已有提问文本挡住 | 已 done 的旧回答不覆写，失败/清除/旧 generation 不复活 |

</intent-contract>

## Code Map

- `codex_worker_process.py` — `_on_event` / `_dispatch_event`（约 98–150）、`_handle_start_turn`（约 152–274）、`_extract_id`（446）：已有有界 native scope/startup buffer。当前优先 native turn scope；重复 turn 不同 local owner 映射被标 None，而 ACK 缺 turnId 时保留旧 owner。复用现有锁/有界缓存；局部增加已确认 steer 输入边界、item owner 与相关 completion owner 信息。`CodexEvent.data` 保留原 item，`subtype` 表示 userMessage，`item_id` 可用于已有 item 归属。
- `main.py` — `_event_scoped_turn_index`（7087）优先显式 turn_idx，允许多 local 绑定同 native ID；`_apply_codex_worker_thread_state`（15987）将 ACK 绑定记录。`_on_codex_event_for_chat`（13304）分当前/归档路径；首答 helper（9877）拒绝非空更新，final phase 的普通事件（13618）却直接覆盖。统一 Codex 完成 item 累积/去重及 native completion 对关联输入的收尾，保留 Kimi helper 当前契约，勿顺带改变 Kimi 行为。
- `codex_client.py` — `_event_from_item` 保存 `item_id/subtype/data`，无需另造协议解析层；如确需解析 userMessage 的 content，仅局部复用原 item。
- `chat_store.py` — turn payload JSON 持久化，新增最小 Codex item metadata 可用现有 payload 存储，无 schema 迁移；确认 main 序列化路径也保留所需字段。
- `tests/test_codex_worker_process.py` — 已有 steer、新 start fallback、startup ACK 顺序、失败/overflow 和 prior owner 用例。
- `tests/test_codex_integration.py` — 已有 send steer 和 final append/focus 场景；覆盖当前/归档结果及持久化。
- `tests/test_codex_ui_responsiveness_automation.py` — 已有真实 timer、切聊天完成、回答列表验证夹具；新增同 native 多 local 的可见列表测试。

## Tasks & Acceptance

**Execution:**
- [x] `codex_worker_process.py` — 解析 ACK 合法格式、区分成功 steer 与 fallback start；确认新输入边界后分派新 item，保留旧 item owner；为完成事件携带已接受且同身份的相关 local owners — 消除误归属和 pending 残留。
- [x] `main.py` — 复用显式 scope，统一当前/归档的 Codex completed item 更新；不同完成回答按顺序累积，同 item/完整终答重放去重；delta 只进入执行流；收尾匹配任务的已接受 local inputs — 保留旧答并展示真正终答。不要更改现有 Kimi 首答契约。
- [x] `tests/test_codex_worker_process.py`、`tests/test_codex_integration.py` — 增加矩阵的局部回归，含连续两次 steer、旧 item 迟到、错误 generation、失败/fallback 与 ACK 前新事件；使用临时存储重载核对正文/状态。
- [x] `tests/test_codex_ui_responsiveness_automation.py` — 使用真实 wx loop 验证问题及回答的实际可见顺序、旧答保留和跨聊天完成；不以字段断言代替列表断言。

**Acceptance Criteria:**
- Given 回答列表已有旧问题与“旧回答”，when 新增“群号187387007”并成功 steer 后收到“真实群已连接并开始采集…” completed item，then 可见列表顺序为旧问题、旧回答、新问题、新回答，旧回答仍完整且新回答在列表尾部。
- Given 新问题已有上述异步提问，when 不同主代理 item 给出真正终答并 native turn 完成，then 新问题后的回答包含提问与终答且无重复，相关已接收问题不再显示等待。
- Given 已完成记录保存到隔离聊天数据库，when 重新加载聊天，then 列表保留相同顺序、所有已完成答文和完成状态。
- Given 用户已切换到其他聊天，when 原聊天完成并收到旧 item 迟到事件，then 返回原聊天可见正确回答顺序，新聊天列表不受影响，旧答不被新输入之后的回答替换。

## Implementation Notes

- Worker matches accepted steer input against the original userMessage content to establish the ownership boundary; known item IDs retain their original owner. New boundary and answer events arriving before ACK are buffered with limits and delivered in order after ACK. ACK parsing supports turn.id, turn_id and turnId; only a successful steer without an ID falls back to the expected native turn. A fallback start never reuses the old ID.
- Accepted owners for one native turn retain their individual context generations. Completion events carry completion_owners; main applies the existing native/thread/model/generation and request-status guards independently to each local record.
- Completed Codex answer text and item IDs persist through existing turn payload JSON. Distinct completed texts accumulate in order; item and full-text replays are deduplicated. Deltas only update the execution stream. The Kimi first-answer helper is unchanged.

## Plan Change Log

## Review Triage Log

### 2026-10-02 — Review pass
- Scope: per user instructions, one local president review plus engineer independent verification; additional blind-hunter, edge-case-hunter, verification-gap and intent-alignment lenses skipped to avoid disproportionate review rounds.
- verdicts: 1 finding — high 1, medium 0, low 0, false 0, maybe-false 0
- findings:
  - `[high]` `[patch]` A queued second steer boundary hides the first accepted input and answer after its own boundary arrives — president reproduced production item events after ACKs: u1/a1 had no scope, u2/a2 owned local 2. Director added steer_boundary_seen and a delayed-boundary regression; engineer independently reran all worker tests, 40 passed.

## Design Notes

预计产品逻辑与回归合计超过 100 行，因此 full。每个 local turn 仍只使用 `answer_md`，不同完成 assistant item 的文本按顺序合并到所属记录；最小 item 元数据只用于归属与去重，不建立独立消息表。新 owner 从已接受的 steer 用户输入边界开始，不从“最新 local 记录”推断；已知旧 item 身份优先。无法唯一确认的新 item 不修改任何回答。完成 native 任务时，仅收尾其明确接受的 local 输入。

## Verification

**Commands:**
- `.venv/Scripts/python.exe -m pytest tests/test_codex_worker_process.py -q` — worker 身份、ACK、steer 与失败边界通过。
- `.venv/Scripts/python.exe -m pytest tests/test_codex_integration.py -k 'steer_completed or legacy_completed or native_completion_does or archived_completion or final_question or final_answer_appends or turn_completed_clears or ambiguous_codex_turn_id or stale_chat_id or queued_codex_final or late_final or old_codex_generation' -q` — affected model flow passes; full integration COM failure is recorded below.
- `.venv/Scripts/python.exe -m pytest tests/test_codex_ui_responsiveness_automation.py -k 'steer or switched_chat_final' -q` — new visible-order and existing switched-chat final cases pass.
- `git diff --check` — 无补丁格式错误。engineer 独立核对最终树并仅补必要验证。


### Director verification (2026-10-02)

- `.venv/Scripts/python.exe -m pytest tests/test_codex_worker_process.py -q`: **39 passed**. Eight additional parameterized cases cover ACK formats, two consecutive steers, production `_event_from_item` user/answer events, late old items, pre-ACK ordering, failure/overflow, unknown boundaries and fallback without an ID. The second steer uses a different generation, confirming completion owners retain individual identities.
- `.venv/Scripts/python.exe -m pytest tests/test_codex_integration.py -k 'steer_completed or legacy_completed or native_completion_does or archived_completion or final_question or final_answer_appends or turn_completed_clears or ambiguous_codex_turn_id or stale_chat_id or queued_codex_final or late_final or old_codex_generation' -q`: **13 passed, 22 deselected**. Covers current/archived results, persistence reload, legacy events without item IDs, Kimi first-answer behavior, and exclusion of failed/unrelated/wrong-generation owners.
- `.venv/Scripts/python.exe -m pytest tests/test_codex_ui_responsiveness_automation.py -k 'steer or switched_chat_final' -q`: **3 passed, 31 deselected**. Real wx loop checks visible question/answer ordering, old-answer preservation, distinct completed-answer accumulation without duplication, chat switching and isolated database reload.
- The full integration command passed **7 cases**, then the existing `test_send_click_routes_codex_start` reported Windows COM fatal **0x8001010d** in `main.py::_request_question_submit` and stalled. That test process was terminated. The unrelated send/COM path was not modified. There is no full-suite pass result; the focused regression command above subsequently passed independently.
- `git diff --check`: passed. No real history, packaged/installed files or database schema was changed.

## Auto Run Result

- Status: built. Successfully accepted steer input establishes the new local answer owner, known old items retain their owner, and native completion closes matching accepted local inputs. Completed question prompts and true final answers accumulate without delta overwrite or replay duplication.
- Files: `codex_worker_process.py` handles ACK formats, bounded owner maps, input boundaries and completion owners; `main.py` handles completed-answer persistence/deduplication and guarded completion; the three affected Codex test files cover worker, model/persistence and real GUI behavior; this plan records execution and evidence.
- Review: one high finding patched, none deferred or rejected. Additional review lenses were skipped under the user's proportionate-review instruction. The patch preserves q1 answers when q2 boundaries are delayed.
- Follow-up review recommended: false. The one high patch was independently exercised using production item parsing and the exact delayed-boundary scenario; no specific residual unverified patch risk remains. No extra review pass was run.
- Independent engineer results: final worker **40 passed**; affected integration **13 passed**; real GUI **3 passed**; retired/current-source and generation guards **4 passed**; clear/resend real-ACK persistence **1 passed**; inputText protocol check across four ACK formats **4/4 passed**. All five I/O matrix rows have passed coverage. Main and GUI were unchanged by the final worker-only patch, so their verified results were reused.
- Diff and cached diff checks passed. Tests isolated app/history/notes data and ran wx suites serially.
- Limits: full integration did not finish after director observed Windows COM 0x8001010d; baseline causation was not independently established. The affected tests passed, but this is not a full-suite pass. No real-model end-to-end run, package rebuild/deployment or repair of existing production history was performed.
- Finalization: local Git commit only; no push.
