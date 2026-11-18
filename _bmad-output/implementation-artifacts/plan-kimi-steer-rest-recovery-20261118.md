---
status: built
route: full
review: thorough
review_source: auto
lenses_ran: [blind-hunter, edge-case-hunter, verification-gap, intent-alignment]
failed_layers: [edge-case-hunter]
deferred: []
followup_review_recommended: false
workflow:
  route: full
baseline_revision: 50c9ebeb76a01133136f259e0dbc0456b000a7ee
context:
  - AGENTS.md
  - docs/handoff.md
  - docs/experience.md
  - docs/reflection.md
  - docs/README.md
  - docs/kimi-runtime.md
  - kimi_transcript.py
  - kimi_server_client.py
  - tests/test_kimi_server_client_unit.py
  - tests/test_kimi_integration.py
---

# Kimi steer 后 REST 最终回答恢复

2026-11-18，dog 规划，director 实施，engineer 独立复验，新 dog 四透镜审查；当前状态 built。源码提交 `8858753cd5a64903ef8c6b66af6e15299e4f9ab9`，真实隔离云端验收已通过，物理声音与日常部署未验收。

## 问题与协议依据

真实隔离长任务接受追问后，实时最终回答、owner/alias 共用答案、一次完成音调用均正常；原 owner 的 reconcile_v2 持续返回 None。REST 主代理 turn completed，triggerPromptId 匹配原 owner，原始及追问 prompts completed，末步 interrupted 且包含最终 assistant text。Transcript.results 要求末步 completed，丢弃该恢复候选。

脱敏证据及脚本：C:/Users/gladwell/AppData/Local/Temp/mc-kimi-live-e2e-fu_cj887 下 evidence.json、result.json、verify_live.py。原证据只保留 endReason 的键，没有值；不能宣称实际原因已确认是 steered。

只读核对本机 C:/Users/gladwell/.kimi-code/bin/kimi.exe，SHA256 为 3fe337ab7aa742ba0b72cc87fcc9a267756e609a54358c0d85bbe200da9bec8d，与固定 2.5.3 一致。其打包 JS 表明：

- agent machine 的 steer 路径调用 emitStepInterrupted(..., "steered")，取消路径为 "aborted"。
- packages/transcript/src/history/groupTurns.ts 的 draftToTurnItem 将持久内容步骤投影为 completed。
- packages/transcript/src/history/foldFacts.ts 的 foldWireRecordFacts 按 turn/step ordinal 收集 turn.step.interrupted，再覆盖对应内容步骤的 state/endReason，保留 frames；没有依据后来完成撤销覆盖。turn.state 则独立从 turn ended reason 映射。这解释了 completed turn 与 interrupted 内容步骤共存。
- mapTurnEndReason 保留 cancelled/failed 的失败语义。整个 turn completed 才是成功权威；不能凭 interrupted、prompts completed 或正文标记判断成功。

## 最小实施顺序

1. director 先复用下述隔离脚本采集真实 REST endReason，只记录该枚举值及必要结构；确认 steered 后实施以下窄分支。若为其他值，交父代理核对局部方案，禁止直接放开所有 interrupted。
2. 在现有 client 测试加入合成 REST 夹具：目标 completed turn、末步 interrupted/endReason=steered，包含追问 user、thinking、最终 assistant，另有更晚其他 prompt。先证明基线 reconcile_v2 返回 None。
3. 仅在 kimi_transcript.py 的 Transcript.results 放开明确分支：completed turn 下，最高 ordinal 末步可为 completed，或 interrupted 且 endReason 精确等于 steered。其他/缺失原因、running/failed 末步仍不接纳。保留 transcript.incomplete、无 tool、非空公开 assistant text 过滤和失败 turn 原行为；不回退扫描早先步骤，不改写 provider 状态。
4. interrupted/steered 分支只取末步最后一个结构化 user frame 之后的 assistant text；无 user frame 则取该末步 assistant text。最后 user 后无正文则无结果，防止拼入追问前 partial。用 kind/role 判断，不按正文猜 injection；thinking、private/hidden/non_disclosable 均不能进入答案。正常 completed 路径保持原状。
5. 保持 reconcile_v2 的 main 请求、精确 triggerPromptId、歧义拒绝、分页和事件生成；共享 results 同时影响 V2 reset/ops，需真实外层 reset 回归。不增加重发、重试框架、状态缓存、音频队列或泛化重构。

## 验收与最小测试

- AC1：Given 真实形状的 completed + interrupted/steered turn；When 按原 owner 调用 reconcile_v2；Then 恢复为 authoritative、terminal_verified、stream_complete 的 completed event；prompt_id 是原 owner，文本严格等于最终公开正文。更晚其他 prompt 不影响结果，missing prompt 无结果。
- AC2：Given 非成功 turn，或末步 running/failed、interrupted 的 aborted/未知/缺失原因；When 运行终态提取及恢复；Then 拒绝成功发布，失败/取消维持既有失败事件。末步 tool、thinking-only、空白、隐藏正文和缺口仍不发布；前一步有文本也不回退。追问后无回答或仅 thinking 时不能拼入先前 partial。
- AC3：Given 同形状 transcript.reset 已成功一次；When 重复 reset/REST 回放；Then 不增加答案和完成音；正常 completed 步骤仍通过。session/prompt/主代理边界不变。
- AC4：Given 真实新云端长任务正在活动；When 提交追问并等待完成、执行 REST 恢复及回放；Then owner/alias 均 done，仅一个答案身份及一次完成音调用；REST owner 文本与实时最终正文完全相等；回放恢复事件后仍一个答案和一次音效。保留 endReason、身份及文本相等布尔值，不留原始正文/凭据。

## Verification

在 tests/test_kimi_server_client_unit.py 添加核心正反例，复用现有 helpers；在 tests/test_kimi_integration.py 的 V2 恢复/alias 用例旁增加 steered REST 首次接纳及重放，检查保存状态及音效调用。不能只测 results helper。

命令 cwd 固定 D:/code/sj：mc/.venv/Scripts/python.exe -m pytest mc/tests/test_kimi_server_client_unit.py -q。integration 只跑新增及相邻 V2 恢复、alias、完成音用例，记录精确 node IDs 与结果；wx 串行。先失败复现再通过，不扩大为全系统回归。此次不改 UI；若实现确需 UI 改动，再按项目规则加对应 UI automation。

## 真实隔离复验及清理

复用上述 verify_live.py 和同目录私有桌面启动脚本，在新的任务临时目录执行，保留原失败证据。先核脚本当前入口和清理，不假定旧临时数据存在。保持 CreateDesktop 私有桌面，不调用 SwitchDesktop；隐藏窗口、禁全局钩子/远控/文件服务，真实声学输出抑制，计数真实完成方法到 winsound 替身调用。

独立 Kimi home、运行时副本、workspace、app-data、chat/notes 数据库和端口。只为该隔离运行读取复制必要现有凭据，不回写共享目录、不输出凭据；prepare_runtime 核版本/哈希，no-auto-update 保持。补录活动期追问、REST endReason、正文相等布尔值、owner/alias/音效次数与重放结果。

日常 MC PID18416、Kimi PID24596 绝不重启、替换或终止，不按进程名清理；不打包，不启动日常包，不改共享凭据/笔记/历史。抑制音效不能宣称物理出声验收。

try/finally 关闭自有 client/frame/timers、等待自有进程退出、释放私有 desktop。核绝对路径和任务归属后清理本轮临时凭据、运行时副本、数据库及 workspace；保留必要脱敏结果、失败证据及脚本，报告残留及日常进程未动。原失败证据不擅自删除。

## 执行协调

父代理派 director 实施；完成后由父代理派 engineer 独立复验。用户指定的新 dog code-review 和后续 director neat-freak 均由父代理协调。下游不重复派遣、不重启方案研究；发现新证据只反馈必要局部调整。源码、真实隔离验收、物理声音和日常部署状态分别报告。

## director 实施证据（2026-11-18）

- 前置真实探针：`C:/Users/gladwell/AppData/Local/Temp/mc-kimi-live-e2e-10445551e49e4677b0b8cb3ff866459f/result.json`。确认 completed turn 的最高 ordinal 末步为 interrupted，`endReason=steered`，frames 为 user/thinking/assistant；owner/alias 均 done、完成音替身调用一次，原实现 `reconcile_v2=None`。该次 `passed=false` 是计划要求保留的修复前失败证据。
- 合成正例 `test_v2_steered_rest_recovers_exact_owner_public_final` 先在原实现失败（`event is None`）；实现后全 client 文件 `146 passed in 1.89s`。命令 cwd `D:/code/sj`：`mc/.venv/Scripts/python.exe -m pytest mc/tests/test_kimi_server_client_unit.py -q`。
- 产品仅修改 `kimi_transcript.py::Transcript.results`：completed turn 下允许精确 interrupted/steered 末步，仅该分支截取最后结构化 text/user frame 之后的公开 assistant 正文；保留已有其他过滤和失败事件，不改 client/UI/音频流程。
- wx 串行定向 `13 passed in 4.75s`，命令 cwd `D:/code/sj`，使用同一 Python、`-m pytest` 和以下 node IDs（文件前缀均为 `mc/tests/test_kimi_integration.py::`）：
  - `test_v2_steered_rest_and_reset_complete_alias_once[reset]`
  - `test_v2_steered_rest_and_reset_complete_alias_once[rest]`
  - `test_v2_real_client_transcript_to_mc_final_and_rest_replay`
  - `test_v2_recovery_live_prompt_remains_pending_beyond_short_budget[running]`
  - `test_v2_recovery_live_prompt_remains_pending_beyond_short_budget[queued]`
  - `test_v2_recovery_live_prompt_remains_pending_beyond_short_budget[blocked]`
  - `test_v2_lost_post_response_recovers_only_unique_prompt_without_reposting[False]`
  - `test_v2_lost_post_response_recovers_only_unique_prompt_without_reposting[True]`
  - `test_v2_terminal_without_body_exhausts_only_finite_recovery_budget`
  - `test_prompt_during_active_turn_steers`
  - `test_turn_completed_reenables_new_chat_and_plays_sound[False]`
  - `test_turn_completed_reenables_new_chat_and_plays_sound[True]`
  - `test_completed_active_owner_plays_sound_once_while_queue_remains`
- 修复后全新真实隔离验证：`C:/Users/gladwell/AppData/Local/Temp/mc-kimi-live-e2e-0d77cf0e71e74dc095dd06548fbae473/result.json`，`passed=true`，39.53 秒；脚本及 `run.log` 同目录保留。包含真实普通云端回答、重连新代 ACK、历史连续性、活动长任务追问、REST 恢复及重放。
- `rest_steer_checks`：turn completed、末步 interrupted/steered；owner REST 与实时最终正文完全相等且 prompt 匹配；owner/alias done、alias 身份匹配；长任务仅一个答案/一次完成音调用。REST 事件两次回放后正文、答案身份及 alias 不变，仍一个答案/一次调用。只保留布尔值和枚举，没有保存原始正文/凭据。
- 两个新目录均只留脚本、脱敏 result 和 log；本轮 home、runtime、app-data、notes、files、workspace 已清理，自有服务退出（修复后 PID36488）。私有 desktop 由 launcher finally 释放；原失败证据目录未修改。日常 PID18416/PID24596 仍为原路径和 06:12 启动时间，未重启、终止、替换或打包。
- `git diff --check` 通过。源码及真实隔离验收已完成；engineer 独立复验和新 dog 审查结果见下文，frontmatter 为 built。物理声音因抑制输出未验收，日常部署未做。

## Review Triage Log

### 2026-11-18 Review pass

- verdicts: 11 findings；high 0, medium 0, low 9, false 2, maybe-false 0
- findings:
  - `[false]` `[reject]` #1 首个 user 边界变异漏检：已有两个 user 负例可抓住 max→min 的错误，内存变异已反证。
  - `[low]` `[reject]` #2 缺专门 steered ops 组合用例：reset/apply 共用 results，已有 ops 及真实活动追问证据，未发现本次调用者分歧，不增组合时序夹具。
  - `[low]` `[reject]` #3 混合隐私/公开成功例：沿用原公开过滤，现有正反例及只读内存 probe 未见泄露或公开正文丢失，不加组合参数。
  - `[low]` `[reject]` #4 Temp 证据持久性：当前可读不证明永久保存，风险成立；按既定范围保留冻结 diff、脚本和脱敏结果，不把证据归档建议扩展为产品修复。
  - `[low]` `[reject]` #5 计划生命周期文字冲突：属文档问题，review 不改计划；由父流程及 neat-freak 同步为 built。
  - `[low]` `[reject]` #6 真实验收与合成测试为不同证据层：真实 result 已核 passed，不要求将云端验收并入 pytest。
  - `[false]` `[reject]` #7 共享提取影响 reset：计划明确要求共享，新增 reset/rest 均走 client 入口，无越界。
  - `[low]` `[reject]` #8 真实正文相等依赖外部证据：脱敏布尔值可读，符合不保存原始正文的验收约定。
  - `[low]` `[reject]` #9 新测试未检查真实渲染顺序：现场记录 row11→13，产品不改顺序，不扩大 UI 回归。
  - `[low]` `[reject]` #10 没有物理可听证据：仅证明一次替身调用，保留物理声音未验收，不改音频。
  - `[low]` `[reject]` #11 隔离和日常进程依赖现场证据：engineer 不冒充云端复跑，保留历史观察边界，无新产品缺陷。

原始审查表见 [新 dog 报告](review-kimi-steer-rest-recovery-20261118.md)。父裁决将 #4 从原表 false 更正为 low：当前可读不能反证 Temp 持久性风险；reject 决定不变。最终统计以本计划为准，low9/false2，共11项，0 patch、0 defer。

## Auto Run Result

- status: built。Kimi completed turn 的最高 ordinal 末步允许 completed 或精确 interrupted/steered；后者仅提取最后结构化 user 之后的公开正文，aborted/未知/缺失原因及失败状态不放宽。
- 改动：`kimi_transcript.py` 约9行；client 测试增加 REST 恢复正反例；integration 增加 reset/rest 首次接纳、owner/alias 保存状态及重放去重。没有修改 UI、音频、重发或缓存框架。
- 新 dog 四透镜共11项：high0、medium0、low9、false2、maybe-false0；0 patch、0 defer、11 reject。原始候选与证据完整保留于审查报告，父流程裁决如上。
- `followup_review_recommended=false`，未修补项 high0/medium0/low0；edge空数组仍记 failed layer，diff 自带计划及叙述证据，不能称完整 clean review，也不据此宣称无所有潜在缺陷。
- engineer 独立复验：cwd `D:/code/sj`，client 完整文件 `146 passed in 1.77s`；上列13个 integration node IDs `13 passed in 4.13s`，wx串行；`git diff --check` 通过。未改代码/测试，也未重复真实云端验收。
- director 真实隔离验收39.53秒通过：新请求、重连 ACK、同会话连续性、活动长任务追问、owner REST 正文相等及重放后1答案/1音效调用；这是现场证据，与 engineer 的合成回归分开记录。
- 本轮隔离凭据、运行时、workspace、数据库及自有服务已清理，脚本/脱敏结果/冻结审查 diff 保留。日常 MC PID18416/Kimi PID24596 未动，未打包部署；物理声音、日常独立运行时安装/包切换、活动长任务断连/未决 POST 整体恢复及长期稳定性仍待验收。
- neat-freak 只同步当前知识与文档编码，不重跑已通过的159项测试，不扩修生产或测试代码；本地文档提交与显式 GitHub 推送由该收尾步骤完成。
