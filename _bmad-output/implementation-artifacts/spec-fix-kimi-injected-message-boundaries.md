---
title: '修复 Kimi 系统注入截断最终回答'
type: bugfix
created: '2026-10-07'
status: done
baseline_revision: '21413cf14bef37b2ac712909211f83b62e2ff365'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/AGENTS.md'
  - '{project-root}/docs/handoff.md'
  - '{project-root}/docs/experience.md'
  - '{project-root}/docs/reflection.md'
warnings: [oversized]
deferred:
  - summary: >-
      安装版部署和“继续教育学习”已失败记录的现场恢复尚未执行。
    evidence: |-
      本轮交付仓库修复。D:/code/cx/mc/mc.exe 及其三个 mc_worker.exe 仍在运行，package_mc.ps1 会拒绝更新运行包；未关闭当前会话或直接改写现场数据库。
      源码和隔离测试通过不证明现场已恢复，最终答复须保留这一边界。部署后应通过同原 prompt 的应用恢复流程获取权威终答，避免重发。
    location: >-
      D:/code/cx/mc
    severity: medium
---

<intent-contract>

## Intent

**Problem:** 用户在安装版 MC 的“继续教育学习”中使用 kimicode，第 27 轮正常生成终答，却显示 `Kimi Code transcript has no final answer for this prompt`。Kimi 插入 role=user 的 AGENTS 提醒，MC 把它当下一真实提问截断；同样影响执行过程补齐和旧请求恢复。

**Approach:** 使用结构化来源识别系统注入，让目标请求的回答与执行过程跨过注入记录，保留真实后续提问的隔离；通过正常 MC 对账、持久化和回答展示路径验证修复，完成 BMAD 实现、独立审查与本地提交。

## Boundaries & Constraints

**Always:** REST 的真实来源字段为 `metadata.origin.kind`，兼容原始/旧适配行的顶层 `origin.kind`；仅明确 injection 不构成用户提问。缺失、未知或畸形来源维持保守边界。用准确 prompt_id 定位，并保持 chat/session/turn/revision/generation 隔离、alias 规则、主代理权威终态、隐私过滤和幂等完成。测试隔离笔记及聊天库；wx 串行且清理计时器。

**Never:** 不靠 `<system-reminder>` 等正文关键词判别，不忽略所有 user，不把思考/工具中间说明当终答，不重发现场 prompt 或直接改现场数据库。该迭代交付代码、测试与提交；安装包部署和已经失败的现场记录恢复须另列状态，不冒充已完成。不改其他 provider 或全局技能，不推送，不启动真实模型，不做完整桌面/实体设备验收。

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| M1 真实注入 | target user → 工具步骤 → metadata.origin=injection 的 AGENTS 提醒 → final assistant | 正常对账取得目标终答，MC turn done，answer_md 和答案列表完整，持久化一致 | 不再生成该空终答错误 |
| M2 多次及顶层注入 | 多个 injection，不同 variant；顶层 origin 旧结构 | 每个注入都被跨过，不依赖正文，取得目标回答 | 不触发错误边界 |
| M3 真正下一提问 | target → injection → final → 真 user → 后续 assistant | 只取 target final，closed 只由真 user 置为 true | 不串到下一请求 |
| M4 保守兼容 | user 无来源、未知来源、非字典来源；包含伪造 system-reminder 正文 | 仍构成边界，不凭正文越过 | 无 final 则维持已有空答处理 |
| M5 无终答 | target → injection → 仅 thinking 或带 tool_use 的 assistant | 不产生成功回答，保留已有恢复/错误语义 | 不使用中间文本兜底 |
| M6 执行补齐 | 同 owner 的多步骤，步骤间 user injection；真 next/alias | 注入后步骤正确补齐，alias 不重置步骤序号，真 next 的步骤不归当前请求 | 保持 owner/revision 守卫 |
| M7 旧请求迁移 | injection 正文与旧 question 相同，另有/没有真实匹配 prompt | injection 不参与问题匹配；唯一真实匹配才迁移，无真实匹配返回 None | 不错误认领提醒 |
| M8 分页 | 原始 prompt 在前一页，注入与终答在最新页 | before_id 找回原 prompt，正常对账并完成 | 不重新 submit |
| M9 幂等与隔离 | 同一次 completion 重放、其他 session/旧 generation 回调 | 终答持久化一次，完成音一次；不改其他聊天或清空后的新轮 | 沿用已有拒绝规则 |
| M10 隐私与代理 | 注入后隐藏文本/思考和子代理终态 | 隐藏/思考/子代理仍不作为公开终答 | 保持现有过滤 |

</intent-contract>

## Code Map

- `main.py:14020` `_kimi_message_text` 保留文本与隐私过滤；新增共享注入/真实提问判定放在相关 helper 附近。
- `main.py:14093` `_kimi_messages_back_to_boundary` 按 id 分页定位；同类起点定位也应拒绝显式 injection，防假 prompt。
- `main.py:14163` `_kimi_sync_thinking_rows` 以 assistant ordinal 匹配已有流；14218 任意非 alias user break，需共用边界判定。
- `main.py:14334` `_kimi_rest_answer_boundary` 当前任意 user break；应跨过注入，保留 tool_use 排除和准确起点。
- `main.py:14450` `_reconcile_kimi_owner_worker` 通过上述 helper 取答案后构造 rest.reconciled 权威完成；保留预算和生命周期。
- `main.py:14664` `_migrate_legacy_kimi_owner` 问题匹配需排除 injection，并沿用时间消歧和 owner 提交流程。
- `kimi_server_client.py:941` list_messages 原样保留 items，无需改 transport。
- `tests/test_kimi_integration.py` 已有 FakeKimiServerClient、_setup_kimi_frame、_submit、_sdk_push 与隔离 frame；按实际 metadata.origin 新增用例。`tests/test_kimi_ui_responsiveness_automation.py` 用于受影响 F1 补齐及后台焦点回归。
- 只读证据：`C:/Users/gaope/.kimi-code/server/events/session_ecf88af0-90eb-4443-b138-06c8579ce165.jsonl` seq1121 AGENTS injection，seq1123/1124/1126 completed；对应 `sessions/wd__internal_fcfe2895237e/.../agents/main/wire.jsonl` 第2429行含1142字符真实终答。不要复制私人原文进仓库。
- 本机 `C:/Users/gaope/.kimi-code/bin/kimi.EXE` 内嵌 `packages/kap-server/src/services/messages/messageProjection.ts` 的 toProtocolMessage 明确将 msg.origin 映射为 metadata.origin；用脱敏等结构夹具，不能只测顶层 origin。

## Tasks & Acceptance

**Execution:**
- [x] `main.py`：共享判定并用于答案、执行同步、旧请求匹配及准确起点，不扩展终态资格。
- [x] `tests/test_kimi_integration.py`：覆盖矩阵全部行；M1/M8/M9 至少经过真实 frame 正常对账和持久化，不止直接调用字符串 helper。其他行可用已有通过用例覆盖，记录准确用例名称。
- [x] 本规格：记录实跑矩阵映射、审查、结果与部署边界；独立审查后提交，工作区清洁（最终 Git 状态校验为准）。

**Acceptance Criteria:**
- Given 已受理 Kimi 请求和含系统注入的权威 REST transcript，when 正常 MC 对账完成并刷新回答表面，then 用户可见完整目标答案，数据库为 done，完成重放不重复响铃或新增未读。
- Given 注入后的同 owner 步骤及真实后续请求，when F1 过程补齐，then 当前过程包含该轮注入后的公开步骤，后续请求步骤不串入，后台补齐不抢焦点。
- Given 本次修复工作区，when BMAD 独立实现、定向验证和审查完成，then 审查改动已本地提交，未部署/现场恢复的状态明确。

## Spec Change Log

## Review Triage Log

### 2026-10-07 — Review pass 1

- verdicts: 9 findings/observations — high 0, medium 1, low 5, false 3, maybe-false 0。Edge Case Hunter 返回空列表，Verification Gap Reviewer 未发现验证缺口；Blind Hunter 的算术说明不计为 finding。Intent Auditor 四个表面观察逐条保留。
- findings:
  - `[low]` `[patch]` B1：注入后的隐私测试原来仅调用答案解析，没有验证私密 thinking 补齐。保留既有过滤实现，补私密 message/block 与公开 thinking 的实际同步和持久化投影断言；secret 不出现，公开正文保留。
  - `[low]` `[patch]` B2：无 final 的注入特例仅有 helper 断言。补 normal owner worker 的 thinking-only/tool-use 两参数，验证持久化 failed、正文不含中间文本、不响铃、不重发；没有增加新的成功或失败状态规则。
  - `[low]` `[patch]` B3：注入及 alias 原先分别验证。补真实 accepted alias 夹具中的前后 injection、真 next 与 foreign assistant，验证原 owner、连续 1/2 ordinal、next 内容不串入，并核对改动确实出现在磁盘 diff。
  - `[low]` `[patch]` B4：来源矩阵缺 metadata 非字典、origin 空字典/空 kind。新增六参数，覆盖顶层 injection 冲突、metadata 优先及有明确顶层来源时的兼容回退。
  - `[low]` `[reject]` B5：审查时任务清单未勾选、triage 为空。属于进行中工件，代码及验证证据已存在；本阶段补齐审查与结果，最终勾选和提交，不以修改规格冒充产品修复。
  - `[false]` `[reject]` I1：用户问题所在表面是具体安装版聊天。事实正确，但不是 diff 中的新缺陷；规格和最终结果明确区分源码/现场状态，不宣称安装版已恢复。I3 单独保留实际剩余交付。
  - `[false]` `[reject]` I2：测试表面是 fake provider 配合隔离 frame。真实 answer_list、SQLite 保存、音效和 read state 是此次应用路径断言；没有声称真实 provider 或实体设备通过，因此不存在伪装现场验证的结论。
  - `[medium]` `[defer]` I3：未更新实际运行包及失败聊天。是预先存在的现场状态，不由代码修复引入；运行包进程仍在，禁止将源码结果等同实际恢复。保留单一 deferred 项和最终说明。
  - `[false]` `[reject]` I4：unified diff 自身不能证明此前提交和技能调用顺序。会话工具结果与 Git 21413cf14bef37b2ac712909211f83b62e2ff365 证明渲染修复已先提交，原仓库开始时无未提交文件；skill 成功打印的绝对 workflow 已读取并执行。证据范围限制不是流程违规。

## Design Notes

结构化注入规则保守向后兼容；若 metadata 是字典且含 origin 键，以该来源为准（包括畸形值，仍为边界）；否则回退顶层 origin。两位置冲突时 metadata 优先，避免顶层 injection 覆盖 REST 的真实 user 来源。写冲突测试。不为缺来源的旧记录添加文本猜测，不创建新的历史自动迁移功能；现有恢复路径在修复后适用。

## Verification

命令在 MC 仓库执行（外层 cwd 保持 D:/code/sj，使用工具的显式 workdir）。

- `.venv/Scripts/python.exe -m pytest tests/test_kimi_integration.py -k "rest or recovery or legacy_owner or subagent_terminal or subagent_event or transport or resync or replay or alias or injection" -q -ra`：选中的矩阵用例及相关回归通过，无新失败；如精确基线既有失败需单独复现证据，不宣称全量通过。
- `.venv/Scripts/python.exe -m pytest tests/test_kimi_ui_responsiveness_automation.py -k "sdk_public_thinking or kimi_status_batch_preserves_focus_selection_and_skips_noop_repaint or background_events_do_not_repaint_lists or background_structured_kimi_batch_persists_owner_without_foreground_repaint" -q -ra`：受影响四项串行通过。
- `.venv/Scripts/python.exe -m pytest tests/test_kimi_event_mapping_unit.py tests/test_kimi_server_client_unit.py -q -ra`：映射和客户端既有测试通过。
- `.venv/Scripts/python.exe -m pytest tests/test_kimi_integration.py -k "interleaved_same_turn_id_routes_by_session or same_chat_reused_turn_id_keeps_sessions_isolated" -q -ra`：两个跨 session 隔离用例通过。
- `git diff --check`：无空白错误。解析修改过 Python 文件、核对所有矩阵行的已运行测试名称。

验证环境隔离，不调用现场模型、不写现场 history/notes。规格完成后若尚未打包/恢复现场失败记录，最终输出必须明确。

## Implementation Verification

主代理独立验证：集成 79 passed / 82 deselected（16.61s），受影响 wx 4 passed / 5 deselected（1.67s），事件映射与客户端 179 passed（1.77s），另补跑跨 session 同 turn 隔离 2 passed（0.99s）。定向共 264 项通过，没有跳过项；不代表全套或安装版已验收。实施代理的同一集成筛选另通过 79 项。

矩阵审计（下列用例均在以上命令中执行并通过）：

| Matrix | Passed tests |
|---|---|
| M1/M8/M9 | test_rest_injection_reconciliation_persists_visible_answer_once[False/True]：resync→权威完成→实际 answer_list→状态保存→SQLite done/answer；重放后 read state 及完整持久化 turn 不变，完成音一次、submit 一次；分页分支 before_id 非空 |
| M2/M3/M5 | test_rest_multiple_injections_preserve_real_next_boundary_and_no_final_semantics |
| M4 | test_rest_injection_boundary_is_structured_and_metadata_has_priority（最终16 参数） |
| M6 | test_rest_injection_thinking_sync_keeps_ordinals_and_stops_at_real_next；test_rest_steering_alias_keeps_original_step_ownership[False/True]；test_rest_progress_rejects_stale_owner_and_later_prompt_content |
| M7 | test_legacy_owner_injection_never_matches_question[False/True]；test_legacy_owner_injection_cannot_be_alias_start；test_legacy_owner_migration_uses_time_to_disambiguate_repeated_question |
| M9 隔离 | test_rest_progress_rejects_stale_owner_and_later_prompt_content；test_late_recovery_failure_generation_cannot_overwrite_done_turn；test_interleaved_same_turn_id_routes_by_session；test_same_chat_reused_turn_id_keeps_sessions_isolated |
| M10 | test_rest_injection_does_not_disclose_private_answer_or_thinking；test_subagent_terminal_never_publishes_answer_or_finish_sound；test_subagent_event_cannot_claim_or_stamp_main_owner_turn |
| 准确起点 | test_rest_injection_id_cannot_stop_prompt_pagination；structured priority 及 thinking sync 用例拒绝 injection 伪 prompt ID |

现场只读离线回放：把真实 seq1121 注入按本机 Kimi toProtocolMessage 转换为 metadata.origin，配合真实 wire 最终 text，修复后解析得到 1142 字符、closed=False，且与 wire 终答一致。未调用 HTTP、未重发 prompt、未保存私人原文。

补充 M5 外层错误路径：test_rest_injection_no_final_reconciliation_persists_failure_without_completion 的 thinking-only/tool-use 两参数；M6 的 accepted-alias 两参数已组合 injection、alias 和真 next；M10 现检查私密思考在持久化执行投影中被过滤。审查补测试不改变主代码。

## Auto Run Result

修复使用 metadata.origin 优先、顶层 origin 兼容回退的共享判定，明确系统 injection 不再关闭当前请求边界；答案、公开过程、分页、旧请求匹配与 alias 起点共用这一语义。真正下一提问、未知来源、隐私、主代理终态及 owner/generation 守卫保留。

修改文件：main.py（共享判定与七处应用）、tests/test_kimi_integration.py（现场结构与组合回归）、本规格（验收矩阵、审查与边界）。前置渲染兼容修复独立提交21413cf，5项兼容测试通过；读取原始技能，未改全局技能文件。个人BMAD覆盖补齐既有安装答案 user_skill_level=intermediate，文件已被忽略，不进入提交。

审查四层完成：四项低级覆盖建议已补测试；一个阶段记录建议在最终工件中处理；三个意图观察没有证明违规；安装版部署/失败聊天恢复保留一个中级 deferred。patched high=0、medium=0、low=4；followup_review_recommended=false，无未验证的新产品风险需要另开复审。

安装包未更新，现场失败记录未覆盖；未推送、未调用真实模型、未重发现场请求。源码定向验证不等同全套或已部署。

最终补测试后的主代理独立复验：集成87 passed / 82 deselected（19.95s）、受影响wx 4 passed / 5 deselected（1.69s）、映射/客户端179 passed（1.78s）、跨session隔离2 passed / 167 deselected（0.75s），共272项定向通过。原264项是第一轮结果，不与最终结果重复累加。实施代理补测试21项及最终alias2项另通过，不计入独立最终总数。Python AST、YAML frontmatter和diff空白检查通过。
