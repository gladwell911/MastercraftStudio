# Kimi steer REST 恢复独立审查

2026-11-18，新 dog 协调。基线 `50c9ebeb76a01133136f259e0dbc0456b000a7ee`；冻结 diff `C:/Users/gladwell/AppData/Local/Temp/mc-steer-reviewed-4a15c39824ee4a049d3eb978131e98b2.diff`，4 files、247 insertions、1 deletion。full/thorough；与 build-auto step04 共用本轮，不重复审查。报告完成前不代表裁决。

技能精确 render 一次，cwd D:/code/sj，快照 `D:/code/sj/mc/_bmad/render/bmad-code-review/mc-fb7c9ac34cf9/4c202333bf6112210b18/workflow.md`。依项目 AGENTS 逐个派遣，优先于 skill 的并行要求；按全局角色模型分工。四透镜均使用空白上下文；但 diff 自带计划和实施证据，blind/verification 并非严格无叙述盲审。未改产品或测试，未运行云端/GUI，未操作日常进程、凭据及数据库。

## 原始结果：blind-hunter

- Finding floor: 21,175 bytes ÷ 1,024 = 20.68 kB; N = min(floor(√20.68 + 1), 10) = 5.
- `tests/test_kimi_server_client_unit.py::_steered_rest_snapshot`: Add a successful case with two follow-up user frames and assistant text between them. Existing successful fixtures have only one user boundary; the second-user cases have no final answer. They would not catch a regression that selects the first user boundary and publishes an earlier partial answer.
- `tests/test_kimi_integration.py::test_v2_steered_rest_and_reset_complete_alias_once`: Add a `transcript.ops` progression from an active turn through interrupted/steered to completed. The new behavior affects incremental WebSocket extraction as well as REST/reset, but this test only supplies already-terminal snapshots. Assert no completion before the completed turn update, then one final answer and one completion notification.
- `tests/test_kimi_server_client_unit.py::test_v2_steered_rest_rejects_unsafe_terminal_candidates`: Add mixed-visibility successful cases containing both hidden/private assistant frames and a public final frame after the user boundary. Current visibility cases hide the sole final frame and assert rejection; they do not prove that filtering preserves the legitimate public answer while excluding private text.
- `_bmad-output/implementation-artifacts/plan-kimi-steer-rest-recovery-20261118.md`: Preserve the sanitized live reproduction script and minimal protocol evidence in a durable project artifact or documented artifact store. All real-provider reproduction evidence currently points into one developer’s temporary directory, so another reviewer cannot reliably reproduce or audit the protocol exception after cleanup.
- `_bmad-output/implementation-artifacts/plan-kimi-steer-rest-recovery-20261118.md`: Reconcile its lifecycle statements. Frontmatter says `status: in-review`, the introduction says this is planning only, and the final paragraph says frontmatter remains `in-progress`, despite recording completed implementation and live verification. Update these statements to identify the actual completed work and remaining review steps consistently.

## 原始结果：edge-case-hunter

```json
[]
```

依 workflow 空结果记 `failed_layers=edge-case-hunter`。这是审查层限制，不是产品失败，也不表示已发现边界缺陷。

## 原始结果：verification-gap

No verification gaps found.

## 原始结果：intent-alignment

1. Defensible readings of the intent:

   - **User-visible behavior:** After a follow-up during a real, active Kimi long task, REST recovery must recover the completed task’s final answer while preserving its original owner, follow-up alias, order, and one completion sound.
   - **Protocol-specific repair:** Fix the particular REST transcript shape responsible for that failure: a completed turn whose final step remains `interrupted` with `endReason="steered"`.
   - **Operational acceptance:** Demonstrate the repair against real Kimi in isolation, with daily MC unaffected. “一次完成音效” can mean one completion-sound invocation or one physically audible sound; the intent does not distinguish them explicitly.

2. This diff implements the protocol-specific reading and tests part of the user-visible reading.

   `Transcript.results()` now accepts the exact interrupted/steered final-step shape. In that branch, it extracts public assistant text after the last structured user frame. The new client test exercises REST reconciliation and exact owner selection. The integration test exercises owner/alias completion, persisted answer and order, replay stability, and one completion-sound method invocation.

3. The expectations and evidence occupy different surfaces:

   - **Real activity versus constructed transcripts:** The intent describes a real Kimi long task receiving a follow-up. The executable tests added here use synthetic snapshots, fake submissions/events, and mocked transport responses. The added document reports successful real isolated runs, but their scripts and result artifacts live at external temporary paths and are not part of this diff.
   - **REST recovery versus shared extraction:** The requested failure is REST recovery. The production change is in shared `Transcript.results()`, so it also changes transcript-reset processing. The integration test explicitly exercises both entry points.
   - **Final-body preservation:** Tests establish exact extraction for the constructed frame order. Equality between a real streamed final answer and its REST recovery is reported in the document rather than established by the added executable tests.
   - **Identity and order:** The integration test checks persisted owner/alias order and identity within one owner/follow-up pair. It does not directly inspect rendered ordering during real activity.
   - **Completion sound:** The integration test counts a mocked `_play_finish_sound` call. The reported live run also suppresses acoustic output and counts a substitute invocation. Neither establishes physical audible playback.
   - **Isolation and daily MC:** The document reports separate runtime/data locations, cleanup, and unchanged daily process identities. Those operational properties are documentary evidence here; the added tests do not exercise them.

The diff therefore contains a narrow repair plus synthetic regression coverage, accompanied by a report of broader real-world acceptance. The report and the committed executable coverage are distinct evidence surfaces.

## 逐项验证与裁决（先裁决，再归并）

下表按原始项分别裁决；没有把附近结论替代本项验证。intent 的六项表面差异保留为独立项；其描述属实，不意味着应增加工作。没有确认当前改动引入的产品缺陷。

| ID / 来源 | 候选与位置 | Verdict | 证据与建议 |
|---|---|---|---|
| 1 / blind | 首个 user 边界变异不会被抓住；unit:1530 | false | 已有 `no_answer_after_user`/`thinking_after_user` 包含两次 user，二者之间有 final。只在内存把 `max` 改成 `min`，原负例断言由通过变失败。该具体声称被直接反证；reject。 |
| 2 / blind | 缺新增 steered ops 进展用例；integration:1922 | low | 专门新增用例确只有 reset/rest；但 `_consume_v2` 在 reset/apply 后共用 results，turn 未 completed 时 results:104 拒绝；既有 `test_v2_client_gap_uses_journal_before_publishing_final` 保护真实 ops→results，真实活动追问完成证据也已存在。未发现命名调用者分歧或失效，仅增加组合覆盖；额外时序夹具与本次9行局部改动不相称，reject。 |
| 3 / blind | 缺混合私有/公开成功例；unit:1530 | low | 新分支裁切后仍沿用原公开过滤，新增正例与各隐藏负例分别保护两端。内存构造混合正文实测仅输出 public；并无泄露或拒绝公开正文。缺单个组合用例属轻微补强，新增组合参数非必要直接修正，reject。 |
| 4 / blind | Temp证据不可长期审核；plan:71 | false | 实际读取修复后 result.json 成功，脚本仍存在；计划明确清理运行时/数据而保留脚本、脱敏result/log。当前“清理后无法审核”并未发生，未来Temp丢失只是可能性；建议本身要求改审查计划，workflow也要求reject。 |
| 5 / blind | 计划生命周期文字不一致；plan:96 | low | frontmatter in-review 与末段声称 in-progress 确实矛盾；仅文档表述，不改变功能或审查状态。workflow明确reject修计划候选；由父build-auto负责状态，不由review修。 |
| 6 / intent | 真实活动与合成测试分处两层 | low | 描述属实。只读核对 `mc-kimi-live-e2e-0d77cf0e71e74dc095dd06548fbae473/result.json`：活动期follow-up、real_long_followup_order、passed=true；真实证据无需写入pytest成为合成断言。没有需修产品项，reject，保留证据边界。 |
| 7 / intent | 共享提取也影响reset | false | 这不是越界缺陷：计划明确共享results影响reset/ops，新增测试参数reset/rest各走真实client入口，终态过滤在共用函数；reject。 |
| 8 / intent | 真实正文相等依赖外部证据 | low | result 的 `owner_rest_equals_realtime=true`、`replay_text_unchanged=true` 可读；因不保存私人正文，保留布尔值是既定验收方式。仅证据存储表面差异，无必要代码修复，reject。 |
| 9 / intent | 新集成测试未检查真实渲染顺序 | low | 真实result记录 followup_row=11、final_row=13、answers=1/shared_answer=true；本次生产不改顺序。报告明确这是外部真实运行证据，非新增pytest覆盖；无需扩大UI测试，reject。 |
| 10 / intent | 没有物理可听播放证据 | low | 两层都计数替身调用，计划与真实result均明确real_audio_suppressed；物理声音未验收。此次不改音频，已满足一次调用契约，不能宣称物理声学通过；reject代码修改，保留验收限制。 |
| 11 / intent | 隔离和日常进程依赖现场证据 | low | 现有result隔离路径、frame_hidden/global_hooks_disabled、owned_server_exited均明确，计划记录日常进程核对；本审查未重跑现场，不能独立复证历史进程。属于验收观察边界，无新增产品缺陷，reject。 |

## 验证记录与结论

- 四位 reviewer 依次结束再派下一位；blind/edge 为 dog（gpt-6-astra medium），verification/intent 为 president（gpt-6.1-sol high），采用全局角色配置，优先于skill同模型建议。
- blind floor 5已满足；verification 明确报告无gap；edge空结果按规则记failed layer，因此本轮不能称完整clean review。diff内嵌计划使blind/verification存在叙述污染；未隐去该局限。
- 逐项11条先裁决再归并：没有存活项，无须合并。**0 decision-needed、0 patch、0 defer、11 rejected**。没有需要director修复的新项。low项主要是测试组合/证据表面，不新增无明确失败依据的防御或验证框架。
- 只读检查真实结果、脚本相关断言及生产调用者；纯内存probe验证边界变异与混合可见性，退出码0，未落盘修改代码/测试，无GUI/云端调用。父engineer的独立测试结论另行记录，本报告不冒充完整套件复跑。
- 报告是唯一新增交付文件；没有自有运行时、凭据、测试目录或进程需要清理。冻结diff与原live证据保留。按父任务明确约束，不回写计划、不改status、不另写deferred；由父build-auto承接记录。
