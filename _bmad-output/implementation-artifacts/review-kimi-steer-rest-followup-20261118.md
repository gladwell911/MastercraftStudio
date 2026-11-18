---
status: complete
baseline_revision: 50c9ebeb76a01133136f259e0dbc0456b000a7ee
reviewed_revision: 30ca37a0fd7fb7c7ddd357bf8359d931c84b87cd
review_mode: full
lenses_ran: [blind-hunter, edge-case-hunter, verification-gap, intent-alignment]
failed_layers: [edge-case-hunter]
followup_review_recommended: false
patch: []
deferred:
  - id: 7
    severity: medium
    verified: false
    reason: 固定2.5.3真实可达性未证实，需脱敏shape及producer证明
---

# Kimi steered REST 新一轮独立审查

2026-11-18。按用户授权执行 bmad-code-review，在 cwd `D:/code/sj` 精确 render 一次，完整读取生成 workflow；快照为 `_bmad/render/bmad-code-review/mc-fb7c9ac34cf9/4c202333bf6112210b18/`。按项目 AGENTS 的用户阶段规则逐个启动全新 context-free reviewer，覆盖技能默认并行要求。复杂 blind/edge 使用 dog（gpt-6-astra medium），普通 verification/intent 使用 president（gpt-6.1-sol high），来自全局 `.codex/agents/*.toml`。

范围是3个生产/测试文件，151行新增、1行删除；冻结 diff：`C:/Users/gladwell/AppData/Local/Temp/mc-kimi-followup-review-dc060b1040a44bf4b47ef64548bc1189.diff`。blind 输入没有计划和叙述；claims/plan 为 `plan-kimi-steer-rest-recovery-20261118.md`。不修改产品、原冻结计划或原报告；不运行云端/GUI，不打包部署，不动日常进程、共享凭据、笔记或历史。

## 透镜原文

### blind-hunter

- **审查规模**：diff 为 9,890 字节，按十进制计为 9.89 kB；`N = min(floor(sqrt(9.89) + 1), 10) = 4`。以下四项属于需要补齐的回归验证，不将测试缺口表述为已证实的运行时故障。

- **缺少 steered 完成经 `transcript.ops` 到达的验证。** 新增集成测试只覆盖 REST 与 `transcript.reset`，但 `kimi_server_client.py::_consume_v2()` 还有通过 `Transcript.apply()` 增量构造末步、随后调用同一个 `results()` 的生产路径。当前测试无法证明真实增量更新中的 `state`、`endReason` 和 user/assistant frame 保留顺序能触发此次修复。应增加通过连续 ops 构造追问及最终完成的用例，并验证最终正文、owner/alias 保存与一次完成提示。

- **没有验证“最后一个 user”这一新增规则。** `kimi_transcript.py` 新增 `max(...)` 明确要求选择最后一个结构化 user 边界，但正例均只有一个 user；负例追加 user 后没有公开答案，因此也不能区分选第一个还是最后一个边界。把实现错误地改成选择第一个 user，当前新增测试仍可能全部通过。应增加 `旧正文 → user1 → 回答1 → user2 → 回答2` 的正例，断言结果严格为回答2。

- **无 user 的回退分支只验证了最简单的单帧输入。** `test_v2_steered_rest_without_user_frame_keeps_public_assistant_text()` 将 frames 整体替换为一个公开 assistant frame，未验证 `boundary == -1` 时保留整个帧列表后仍执行正文拼接和隐私过滤。这个回退分支正是此次新增行为之一。应保留多个公开 assistant frame，并混入 thinking、private、hidden 或 `disclosable=False` 的帧，断言仅公开正文按顺序拼接。

- **`gap` 用例调用了 REST 恢复，却没有检查恢复结果。** `test_v2_steered_rest_rejects_unsafe_terminal_candidates()` 的 `gap` 分支先正确断言有缺口的 `Transcript.results()` 为空，随后用完整 snapshot 调用 `reconcile_v2()`，但通过 `if case != "gap"` 跳过了对 `event` 的断言。注释所声明的“REST 使用新的完整快照、不继承流缺口”因此没有被验证；即使 REST 对该有效 steered snapshot 返回 `None`，此分支也会通过。应明确断言恢复事件为 completed，正文等于 `final public answer`，并保留权威完成标记。

### edge-case-hunter

```json
[]
```

按 workflow 空结果记为 failed layer，不当作该透镜已通过。

### verification-gap

No verification gaps found.

审查员补充的核对记录原文：

> 核查完成，未找到可证实的验证缺口。已读 Transcript.results 全部及两条直接消费路径 kimi_server_client.py:1100–1126、1179–1217；已读新增全部测试和相关旧 V2 测试。实际 rg 检索 Transcript/reconcile_v2/results 及 import kimi_transcript/from kimi_transcript，直接生产消费者只有 REST reconcile_v2 和 WS _consume_v2。新增单测 :1510 覆盖精确 owner/public final，:1530 参数化覆盖拒绝原因、最后 user 后无答案及隐藏正文，:1580 覆盖无 user；集成 :1922 参数化 REST/reset 首达并直接断言持久化正文、alias、状态及重放只响一次。pytest.ini 默认收集 tests，无新增跳过标记。未运行测试、未写文件或启动 GUI/服务。按审查提示规定最终清洁结果将只输出指定英文单行。

### intent-alignment

1. 可成立的意图解读

- **用户可见行为解读**：真实 Kimi 长任务运行期间接受追问，任务完成后即使依靠 REST 补回，也应公开完整、准确的最终回答；原请求仍为 owner，追问仍为 alias；回答显示在追问之后，完成音只响一次。
- **协议缺口解读**：上述失败由一种特定 transcript 形态引起：turn 已 `completed`，最后 step 却是 `interrupted` 且 `endReason=steered`。修复这一形态的最终正文提取，同时沿用已有 owner、alias、排序和通知逻辑。
- **运行约束解读**：“不干扰日常 MC”既可以指操作期间不影响现有实例，也可以指修复不改变正常请求的行为。两者均有文字依据，但覆盖范围不同。

2. diff 实现的解读

diff 直接实现第二种解读，并通过合成集成测试覆盖第一种解读的部分结果。

唯一生产代码改动位于 `Transcript.results()`：允许已完成 turn 的最后 step 为 `interrupted/steered`；存在结构化 user 文本帧时，只提取最后一个 user 帧之后的公开 assistant 文本。原有最后 step、工具帧、公开性和非空正文限制继续生效。

该方法由 REST reconciliation 与 transcript reset 等路径共用，因此变更范围并不限于 REST。owner、alias、显示排序及完成音代码没有改动；新增集成测试表明，在构造的数据下，现有逻辑能够接住新的完成结果。

3. 预期表面与修改、测试之间的差异

- **REST 补回**：新增单元测试直接覆盖 `reconcile_v2()` 对上述形态的恢复，验证正文、原 prompt 身份、权威完成标记及不误取后续任务答案。集成测试直接注入恢复事件，没有覆盖真实长任务通过自动恢复调度最终补回的完整过程。
- **原 owner／追问 alias**：集成测试验证持久化结果为原请求保存正文、追问正文为空并关联原回答身份，两条请求均完成。这与要求一致。
- **准确公开最终正文**：测试覆盖追问前残留文本排除，以及 thinking、私有、隐藏等内容的拒绝。但没有结构化 user 帧时，实现会保留该 step 内全部公开 assistant 文本，新增测试明确接受此行为；若真实数据同时保留追问前残文却没有该边界，现有规则不能区分两者。另一个边界是，工具帧检查发生在截取之前，追问前存在工具帧也会使整个候选结果被拒绝。
- **回答出现在追问后**：新增测试只断言持久化 owner／alias 和正文，没有断言 `answer_order`、最终消息顺序或实际界面位置。因此这项用户可见要求在本 diff 中依赖既有排序逻辑，没有新增直接证据。
- **一次完成音**：集成测试分别覆盖 REST 先到、reset 先到，并重复投递两种结果；断言完成音仅一次、存储及已读状态不变。该要求有明确的合成测试断言。
- **真实任务与日常 MC**：diff 没有真实 Kimi transcript 样本或实机运行证据，也没有日常 MC 实例不受干扰的运行证据。生产改动较集中，但作用于共享结果提取器，不能仅凭变更行数认定实际影响只限于故障中的 REST 路径。

以上为 diff 和相关代码的静态核对，没有运行测试或进行产品操作。

## 独立裁决

四层全部返回后才逐条裁决，先裁决再合并；以下不合并、不丢弃任何候选。intent 的六项描述也逐项保留，包括已经一致的观察。verification 无候选，edge 空结果单独记失败。父层无确证需修补项：decision-needed 0、patch 0、defer 1、reject 9。

| ID | 来源 | 候选与位置 | 裁决 | 核查、处置与理由 |
|---|---|---|---|---|
| 1 | blind-hunter | steered ops 专门组合未测；kimi_server_client.py:1179 | low / reject | 专门组合确实未新增，但 `_consume_v2` 的 reset/apply 最终共用 `results()`；本次不改 ops 合并器。既有 ops 测试、新 REST/reset 外层回归及真实活动追问重连已经覆盖相关链路。未识别会分歧的调用者，不为组合矩阵新增多事件夹具。 |
| 2 | blind-hunter | 最后 user 边界 max→min 漏检；tests/test_kimi_server_client_unit.py:1530 | false / reject | 现有 no_answer_after_user 和 thinking_after_user 两个负例在首 user 与末 user 之间保留公开 final；max→min 会发布它，违反既有 `results == []`。本轮纯内存变异直接输出原版 `[]`、变异 `['final public answer']`，反证“当前测试仍可能全部通过”。 |
| 3 | blind-hunter | 无 user 多公开/隐私组合未测；tests/test_kimi_server_client_unit.py:1580 | low / reject | 无 user 用例确为单公开帧；切片 `frames[0:]` 后仍是同一个已有公开过滤，不存在另一过滤分支。纯内存混合 assistant A、thinking、hidden、assistant B 得 `AB`；现有公开/隐私正反例已保护过滤。未确认产品漏报/泄露，不加组合参数矩阵。 |
| 4 | blind-hunter | gap 分支没有 REST event 断言；tests/test_kimi_server_client_unit.py:1530 | false / reject | 局部无断言是事实，但所称缺失的 REST 成功契约已有 :1510 精确正文和全部权威标记断言。此 gap 是单独构造的 Transcript，从未注入 client；`reconcile_v2`:1108 每页创建新 Transcript，不能继承这个对象的 gap。给此分支补一条重复断言也不能验证“带缺口 client 恢复”。没有独立未受保护的本次行为。 |
| 5 | intent-alignment | 合成测试不等于真实自动恢复；tests/test_kimi_integration.py:1922 | low / reject | 描述正确；外部65.39秒隔离证据另证明追问活动期断WS、新代目标ACK、owner完成和精确owner REST相等。它仍不单独证明 WS 始终缺终结时的纯 REST 自动调度，报告保留该边界。本次产品只改提取器且真实失败/成功已有证据，不扩建调度框架或重跑云端。 |
| 6 | intent-alignment | owner/alias 与意图一致；tests/test_kimi_integration.py:1922 | false / reject | 原文没有缺陷：新增断言持久化 owner 有正文、alias 为空并引用同身份、两者done，真实证据也匹配；没有需要修补的不一致。 |
| 7 | intent-alignment | 无 user 旧残文与追问前 tool 边界；kimi_transcript.py:121 | maybe-false / defer；medium (unverified) | 代码确实无 user 时取所有公开 assistant，任意 tool 拒绝；这是明确保留的协议合同。审查未证明固定2.5.3会把“无 user 但混有追问前残文”或“同一最终 model step 内留追问前tool”作为可发布终答投影。如果这些形态真实可达，错误正文或丢失终答的用户影响为 medium，不能称低影响。待取得固定2.5.3真实脱敏shape及producer证明后再裁决；目前不是确证需修项，不增加推测分支。 |
| 8 | intent-alignment | 本diff没有界面顺序断言；tests/test_kimi_integration.py:1922 | low / reject | 新增pytest确实只验证存储；新现场脚本实际投影中 assert long_answer_row > follow_row，结果为11<13。排序生产代码本轮未改，无新排序缺陷；不把外部证据冒称新增pytest覆盖，也不加重复UI组合。 |
| 9 | intent-alignment | 一次完成音已有合成断言；tests/test_kimi_integration.py:1922 | false / reject | 原文已明确与意图一致，REST/reset两种首达和重复投递均 `played == [True]`。真实证据完成方法到音效替身也一次；不等同物理声音，未确认代码问题。 |
| 10 | intent-alignment | diff无现场隔离证据且共享提取；kimi_transcript.py:101 | low / reject | 纯diff确不含现场资料，这是有意移除叙述以保持blind独立；外部result/script记录私有desktop、独立home/runtime/data及自有服务退出。共享提取同时作用REST/reset/ops属于明确方案，不是越界。审查本身未启动/操作日常MC；现场记录不外推日常已部署或所有运行状态。 |

统计：已确认 high 0、medium 0、low 5、false 4、maybe-false 1；第7项的条件严重度为 medium (unverified)，路由 defer。总计 decision-needed 0、patch 0、defer 1、reject 9。第7项是协议边界疑问而非已证实产品失败；没有可据此要求修复的可达状态。候选完整保留，无产品或测试改动。上述处置也遵循全局适度验证要求，不以增加覆盖组合本身作为必修理由。

## 证据与局限

- 本轮独立读取 `Transcript.results` 全部、`reconcile_v2` 的身份/分页/状态守卫和 `_consume_v2` 的 reset/ops/缺口/完成退休路径，以及新增全部测试和相关调用。核对三文件范围为151增1删，工作树起始干净。
- 纯内存 probe 通过 `exec` 原源码及仅内存 max→min 变异，无落盘或GUI：原负例 `[]`，变异 `['final public answer']`，无user混合隐私 `['AB']`。该probe证明具体裁决，不冒称重新运行159项。
- 复用独立 engineer 的146 client + 13 integration passed，源码未变；本轮没有重复测试、云端或GUI验收。旧结果不扩为全系统通过。
- 新现场证据：`C:/Users/gladwell/AppData/Local/Temp/mc-kimi-live-e2e-6f43394c04d746efb29e429d78efff4a/result.json` 和同目录 `verify_live.py`。`source_commit=30ca37a0fd7fb7c7ddd357bf8359d931c84b87cd`，65.39秒 passed；脚本187/192行确认长任务仍活动，195行断WS，随后等新代目标ACK。结果 generation 3→6，最后step interrupted/steered，owner REST与实时正文相等，owner/alias done，row11<13，回放保持1答案身份/1完成调用。
- 上述真实重连允许WS补终结，REST随后精确核对/回放；不能说本轮单独证明“WS无终结仅由自动REST调度恢复”，也不覆盖未决POST完整真实场景、物理声学出声、长稳或日常安装切换。日常MC18416/Kimi24596的历史不干扰记录不代替本轮部署验收；本审查没有操作这些进程。
- edge 返回空数组，按技能为 failed layer，不能宣布完整 clean review。blind 达 floor=4，但其候选多数是测试组合建议；verification 独立核查没有发现缺口。`followup_review_recommended=false` 仅指没有确证需修项，不表示不存在未知缺陷。
- 原计划含历史裁决，按上下文加载时协调员会见到它；未把旧结论发给 blind/verification/intent，透镜均全新context-free，edge仅按自身指令处理claims。最终裁决使用本轮源码、probe与现场证据，未把旧裁决当证据。
- 按用户指定，审查元数据与完整原文只写本新报告，不追加冻结计划、不改旧报告；这覆盖workflow默认写计划步骤。用户已授权整轮审查及必要修复，不重复确认。无需 build-auto 修补；后续收尾由父流程负责。
- 本审查新增交付物仅此报告，render按技能生成工作流快照；未创建运行时、数据库、凭据副本或测试进程，没有需清理的自建运行资源。冻结diff与既有现场证据保持不变。
