---
status: done
followup_review_recommended: true
review_loop_iteration: 1
baseline_revision: caccf414353777913ac62f4060217a667d0355bc
context:
  - D:/code/sj/mc/AGENTS.md
  - D:/code/sj/mc/docs/non-live-regression-baseline-2026-09-06.md
  - D:/code/sj/mc/docs/handoff.md
  - D:/code/sj/mc/_bmad-output/implementation-artifacts/spec-fix-kimi-authoritative-completion-and-concurrent-chat-recovery.md
deferred:
  - summary: >-
      quiet 期间重复的轻量执行通知仍按事件数累积。
    evidence: |-
      第1轮 B7 已判定 low/defer；基线亦累积通知，本次去除深拷贝但未另建计数器。不是本次新增问题。
    location: >-
      main.py:_request_execution_list_sync
    severity: low
  - summary: >-
      共享列表模型的全笔记领域调用者未进行完整验收。
    evidence: |-
      第1轮 I5 已记录；已验证共享模型、执行/回答、文件提取、常用指令，但不宣称全笔记领域通过。
    location: >-
      listbox_model.py callers
    severity: low
  - summary: >-
      初始前台两次有限页查询仍可能被单次 SQLite 慢读阻塞。
    evidence: |-
      第2轮 B5。基线在完整 active 内存判断前即同步 COUNT/读取最近页，本次未新增此入口；后台长扫描已移出 GUI，但查询数量有界不等于单次时延有界。进一步消除此风险需要把初始读取也异步化并另验收持久化一致性。
    location: >-
      main.py:_current_execution_steps_for_render
    severity: medium
  - summary: >-
      历史跨轮展开仍是平面执行列表，只有一个问题/回答上下文，不是逐轮分组。
    evidence: |-
      第2轮 B6。基线即将单个上下文包围所有历史步骤，本次纠正为最新有效轮但未新增逐轮分组；可能使跨轮阅读归属不够直观。保持既定分页契约，另行设计逐轮展示。
    location: >-
      main.py:_execution_turn_context_steps
    severity: medium
---

# 执行列表阻塞：第一性原理分析、修复方案、验收标准与测试方案

<intent-contract>
用户要求：分析电脑端 fix 分支代码中的阻塞，使用第一性原理制定详细修复方案、验收标准和测试方案，再派 engineer 使用 bmad-build-auto 实施。

本次可复现入口是前序讨论的 15 项执行列表回归失败，以及其对应的真实 wx 界面性能、焦点、分页和延迟刷新行为。将真实程序问题与过时测试契约分开修复，不能以删除功能或弱化断言换取绿色测试。保留当前 F1 问题/回答上下文行、最新 100 内容行分页、过时尾项修复、历史仅查看、Kimi 中文摘要与权威完成及跨聊天隔离。

最终交付包含诊断依据、实际代码和测试、逐条验收结果及未验证范围。在当前 fix/mobile-kimicode-routing 实施并按自动流程本地提交；不合并 main、不推送、不打包部署、不启动真实付费外部任务、不改真实笔记或用户聊天数据。其他旧失败若与本次有关应查清，不能据旧基线直接免检；无关领域只记录。
</intent-contract>

## 第一性原理与原因证据

1. GUI 主线程串行处理键盘、绘制、无障碍通知。每个执行事件都清空并重建 N 行，会把处理量从少量变化放大成事件数乘 N，也产生不必要的读屏通知。`main.py:_append_visible_execution_entry` 对每个权威事件调用 `_rebuild_execution_list_from_state`；`listbox_model.py:replace_visible_page` 在 id 列表改变时 Clear/Append 全页。这是实际性能缺陷，不是需要取消权威校验的理由。
2. 权威数据决定显示内容，控件只是投影。修复旧尾项需要比较真实控件与期望页，但无需所有变化均全量清空。状态、物理控件和 metadata 必须在更新后保持一致。
3. 选择属于内容身份，不属于屏幕下标。分页滑动或插入问题行会改变下标；当前 rebuild 用旧下标选新行，可能移动读屏位置。保留还存在的行身份，所选行被移出页才使用明确的邻近行回退。
4. 一个按键只应有一个导航执行者。`_handle_primary_tab_navigation` 的 execution→input 分支在手动 SetFocus 后仍 Skip；单元确认重复交给默认处理，但是否双跳须真实 wx 事件验证。
5. 延迟队列只是刷新通知，不是第二份业务真相。当前 quiet flush 从 chat 权威状态重建是合理方向；旧测试直接塞入不存在于 chat 的队列数据并要求 replay，属于测试契约失效。清空通知必须在成功同步后，隐藏/失败状态应可重试。
6. 问题和最终回答合成行是当前已交付语义，旧空状态、行数和分页断言没有算它们。内容行分页包含合成行，“更多”不计入 100 内容行；更新测试必须同时验证上下文保留、顺序和实际执行步骤没有重复/串轮。
7. 模式切换 flush delta 可能先触发一次 rebuild，再显式 render 一次。合并事务应在结束时只同步一次。无可见变化应没有控件写入和额外持久化。
8. 有界页面不等于有界数据读取：`_current_execution_steps` 与 `_execution_turn_context_steps` 在历史分支使用默认 hydrate（include_execution_steps=True），需要禁止渲染历史页先加载全部执行历史。

基线复现：`py -3.11 -m pytest tests/test_main_unit.py -q -k "kimi or execution or switch_current_chat" --tb=short` 为 15 failed / 99 passed / 631 deselected。244 项 Kimi/路由相关回归在前序检查通过，不代表其他故障已解决。

## 修复方案（依赖顺序）

- [x] T1 在 `tests/test_main_unit.py` 逐项归类 15 失败，记录实际问题、旧契约及不能确认项；补上真实 canonical state 的 fixture，保留必要的调用次数预算断言。
- [x] T2 在 `main.py` 的执行页投影/行身份/选择恢复，以及必要时 `listbox_model.py` 增量同步处做最小修改：从权威状态计算期望页，正常新增/删除/改标签不 Clear 全页；物理陈旧尾项仍修复；更新 meta 与模型对齐；使用稳定身份保留所选内容。若修改共享 ListBox 模型，必须验证现有模型测试和其他调用者，不引入通用复杂 diff 框架。
- [x] T3 在 `main.py` execution 模式转换、delta flush、批量/quiet 更新路径合并刷新：一批事件至多一次期望页同步，静默和隐藏页不写控件；待处理通知在成功刷新后消耗，失败保留重试。保持后台按 chat 隔离，不因刷新显示旧轮数据。
- [x] T4 在 `main.py` 历史执行页读取路径显式避免全量执行 hydrate，继续使用 recent page + 未持久化尾部合并，保留上下文行和分页上限；更多只在确实存在可显示的前页时提供，详情直接使用已渲染内容。
- [x] T5 在 `main.py` 导航 handler 修复可证实的重复导航责任；在 `tests/test_codex_ui_responsiveness_automation.py` 使用真实 wx 焦点路径验证 F1、Tab/Shift+Tab 和事件突发，而不是仅 mock HasFocus。
- [x] T6 更新失效单元契约、增加有意义边界测试，跑下面验证；在本文记录实际结果、基线变化和局限，交付本地提交。不要改其他旧文档或 AGENTS.md 以掩盖风险。

## 验收标准（Given / When / Then）

- AC1 给定显示当前轮且有问题上下文的执行页，当新执行事件或最终回答到达，则内容顺序符合权威聊天状态且无重复/跨轮行；正常尾部新增不调用 ListBox.Clear，控件、meta、visible_ids 数量一致。
- AC2 给定物理控件有同长错误尾项或多余旧行，当重放/新权威事件到达，则移除错误内容并显示当前页；重复同状态刷新不调用 Clear/Append/Delete/SetString/SetSelection 或 repaint。
- AC3 给定执行页聚焦且选择非末尾内容，当插入、分页滚动或完成事件到达，则焦点仍在执行页、同一内容还在页内时仍被选中；移出页时采用可预测的有效邻近行，不跳到其他聊天内容。
- AC4 给定输入框聚焦或正在导航的 quiet 窗口，当一批后台事件到达，则不抢焦点，quiet 期间不改列表；结束后至多一次同步该可见 chat 的权威页，没有积压重放、旧尾项复活或跨聊天更新；同步失败保留可重试标记。
- AC5 给定 answers 模式存在待 flush 的执行 delta，当 F1 或显式切到 execution，则先吸收 delta 再一次同步；重复无变化切换不重建。F1 聚焦最新项，F1 返回 answers，完成事件不自动切换视图。
- AC6 给定真实 wx execution 控件聚焦，当按一次 Tab/Shift+Tab，则只到既定主导航序列的下一/上一控件；Enter/Shift+Enter 仍开启正确详情，后台事件不导致二次跳转。
- AC7 给定超过 100 个可见内容行（包含合成上下文行），当首次展示/追加/更多，则初始内容不超过 100 加一个更多行；向上展开无重复，最终无前页时更多消失；隐藏状态不写控件，当前轮隔离不改变。
- AC8 给定 SQLite 中有大量历史执行记录和部分未持久化尾部，当打开历史执行页、读取详情或刷新，则使用有限页查询，禁止调用全量 execution loader；持久化尾部与内存尾部按身份合并无重复、顺序正确。
- AC9 给定既有 Kimi owner/session/offset 恢复及远程模型分派，当运行已验证回归，则全部通过，无新增跨聊天或权威完成故障。
- AC10 给定当前机器真实 wx 自动化，当执行事件突发时按键导航，则既有相关响应时间门槛通过；记录耗时和操作次数，不通过放宽阈值、sleep 或跳过测试获得成功。人工读屏未实际执行应明确记录。

## 测试方案

测试在项目 Python 3.11 + wx 环境，使用临时数据目录和 fake provider，不同时运行两个 GUI 套件。先 red 复现，再 green 验证。边界至少覆盖：空轮有/无问题；只有合成回答；99/100/101 内容行；已有更多的滚动与展开；所选行保留/淘汰；物理与模型同长不同内容；无变化重放；后台聊天事件；quiet→可见切换和重试；存储页+未保存尾部；原始 id 缺失时身份稳定性。

测试更新必须从用户可见行为断言，禁止删除失败测试/xfail/skip 以过关。与历史契约不同的测试重命名并说明原因。正常增量 Clear=0、一次事务至多一次期望页同步、静默控件写=0、历史全量执行读取=0 是结构性能指标，配合真实焦点/耗时自动化。

## Verification

依次运行并保留输出到规格结果（新的测试必须纳入这些文件或显式追加命令）：

```powershell
py -3.11 -m pytest tests/test_main_unit.py -q -k "kimi or execution or switch_current_chat" --tb=short
py -3.11 -m pytest tests/test_main_unit.py -q -k "idle_history or idle_ui" --tb=short
py -3.11 -m pytest tests/test_codex_ui_responsiveness_automation.py -q -s -k "execution or f1 or navigation_quiet" --tb=short
py -3.11 -m pytest tests/test_codex_ui_responsiveness_automation.py -q --tb=short
py -3.11 -m pytest tests/test_kimi_event_mapping_unit.py tests/test_kimi_server_client_unit.py tests/test_kimi_integration.py tests/test_kimi_ui_responsiveness_automation.py tests/test_mobile_kimi_cross_chat_e2e.py tests/test_remote_model_dispatch.py tests/test_main_remote_nats_unit.py -q --tb=short
py -3.11 -m compileall -q main.py listbox_model.py chat_store.py tests/test_main_unit.py tests/test_codex_ui_responsiveness_automation.py tests/test_chat_store_unit.py tests/test_listbox_model_unit.py
git diff --check
py -3.11 -m pytest tests/test_listbox_model_unit.py tests/test_chat_store_unit.py tests/test_file_extraction_ui_unit.py tests/test_common_commands_ui_automation.py -q --tb=short
```

若改共享模型：找到并运行全部 listbox_model 单元测试；增加到本节。相关真实 GUI 失败必须诊断，不能仅以旧基线存在作为通过依据。全仓其他 120 项旧基线不在本次已验证声明内。

## Code Map

- `main.py:5337` 执行记录读取；`:5385` recent store 合并；`:5963` 行身份；`:6008` 可见追加；`:6181` 延迟合并；`:6585` 上下文合成；`:6743` 页同步；`:6860` 模式切换；`:14264` quiet 防护；`:16117` 执行按键；主导航 helper 需结合当前行号追踪。
- `listbox_model.py:41` 共享可见页更新，`:73` ids 改变时 Clear。
- `tests/test_main_unit.py` 旧失败和已有 stale-tail/quiet/分页回归。
- `tests/test_codex_ui_responsiveness_automation.py` 真实 wx F1 与事件突发回归。

## 工程执行约束

engineer 独占本次实现涉及的 main.py、listbox_model.py（必要时）、上述测试和本规格结果更新。你不是代码库唯一工作者，保留其他人编辑，禁止 revert 不属于本次的工作；遇新变更先协调。禁止自行合并、推送、部署或改变用户数据。主代理已完整读取并激活 bmad-build-auto，执行 step03 委派；engineer 完成后由主代理按 workflow 校验和多视角审查再提交。

## Review Triage Log

### 2026-09-08 — Review pass 1

- verdicts: 20 findings — high 4, medium 10, low 2, false 4, maybe-false 0
- findings（B=blind，V=verification-gap，E=edge，I=intent）：
  - B1 `[medium]` `[bad_spec]` 合并内存覆盖发生在停止扫描判断之后，可见转隐藏时页不足且错误隐藏更多；应先按权威覆盖过滤再决定候选足够。
  - B2 `[high]` `[bad_spec]` while 在 GUI 主线程内无限前探隐藏记录，每页有界未限制总阻塞；必须转后台扫描或每次回调有界续扫，并守护结果所属 chat/turn/页及版本。
  - B3 `[medium]` `[bad_spec]` 每个 cursor 页 COUNT(*) 重复计算剩余历史，放大 B2；前页 continuation 不应反复 count 全余量。
  - B4 `[high]` `[bad_spec]` store 异常上抛，idle timer 已清空且没有注册重试；真实调度失败必须自动恢复，不能依赖测试手动调用第二遍。
  - B5 `[medium]` `[bad_spec]` 模式切换 mismatch 比较和 render 各算一次 projection，可重复查询/转换；一次事务只生成一份投影再比较/应用。
  - B6 `[medium]` `[bad_spec]` 新 occurrence 身份遍历所有原始记录并生成完整文本key，包括隐藏/页外数据；身份工作应只覆盖最终可见行与必要的稳定位置，不放大主动轮渲染成本。
  - B7 `[low]` `[defer]` quiet 每事件累计相同通知；原实现本已每事件累积深拷贝，当前没有加重该旧风险，且已有诊断测试消费通知计数。后续可专门将通知与统计分离，本轮不改公共诊断契约。
  - B8 `[medium]` `[bad_spec]` 旧存储行 paged 带 step_index，complete-memory 不带，身份会变化；同一记录两种来源需稳定身份，必须覆盖来源切换选择保持。
  - B9 `[medium]` `[patch]` 新共享模型缺排列及中途原生写失败后重试测试，补真实同步结果、身份和选择断言；随重新实现保留该验证。
  - V1 `[medium]` `[patch]` Kimi batch 深度新增但244项只验内容/焦点，缺 changed batch 一次实际同步预算；补多事件Kimi drain计数回归，保留真实实现。
  - E1 `[medium]` `[bad_spec]` 多轮历史前探把旧轮加入steps，合成上下文取首轮会把旧回答接在最新过程后；需以当前显示页的最新有效轮上下文为锚，补两轮边界测试。
  - E2 `[medium]` `[bad_spec]` 相同item_id多个合法存储事件按出现次数区分，展开页次数变化会换选择；存储行位置应优先于可重复item_id，并与内存稳定标识协调。
  - E3 `[high]` `[bad_spec]` UI投影异常发生于drain finally，后续persist启动及has_more调度被跳过，导致事件停滞；与B4同组，隔离展示失败，确保持久化和队列续调度执行。
  - E4 `[false]` `[reject]` 共享模型重复row_id反例不满足模型唯一身份契约；生产调用者使用数据库/命令/文件稳定唯一身份，执行页已加occurrence后缀，没有展示出该非法输入由改动产生的路径。不增加推测性分支。
  - I1 `[medium]` `[bad_spec]` 新GUI测试先drain再按键，没有事件与按键交错的消息队列延迟证据；增加定时/排队交错的真实wx断言和时长，而非只串行函数耗时。
  - I2 `[high]` `[bad_spec]` 单页有限不是历史总成本有限；与B2同组，规格加强总主线程预算。
  - I3 `[false]` `[reject]` 未聚焦时保留选择是明确记录的阅读位置策略，用户未要求自动追尾；F1仍显式追尾且真实焦点验证通过，不是需求偏离。
  - I4 `[false]` `[reject]` 既有Kimi/路由测试是有效回归证据，父代理独立复跑244项通过；不要求重新编写全部端到端测试，人工读屏/付费provider明确未宣称验证。
  - I5 `[low]` `[defer]` 完整笔记领域未全测属于共享组件残余验证范围，现有通用模型/多个调用者已过；本次不声称覆盖全部笔记。
  - I6 `[false]` `[reject]` in-review时尚未提交是流程正常阶段，最终提交将由父代理完成，不能把中途快照当最终漏交付。

### 2026-09-08 — Review pass 2

本轮主代理先独立复验 136+8+244+49=437 项通过，再读取完整 unified diff。运行时 thread limit 拒绝新建第3名审查者；因此两名 context-free 审查者先完成 blind/edge，再分别完成 verification-gap/intent，四个视角均已返回后才统一判定。后两个视角没有独立上下文，是本轮审查隔离性的明确限制。未运行并行 GUI 测试。

- verdicts: 20 findings — high 3, medium 17, low 0, false 0, maybe-false 0
- findings（按报告顺序；修补项已交回同一 engineer，完成证据在最终结果补录）：
  - `[high]` `[patch]` E1 跨聊天 pending 保留旧页 — 沿 _show_history_chat→render→ExecutionPagePending→详情入口确认；最小修补是在已证明的 owner 切换时发布不可打开的 loading 行。
  - `[medium]` `[patch]` E2 异步 F1 不选新页最新项 — 同步 focus helper 先于扫描完成；延续当前请求的显式选择意图，离开后不抢焦点。
  - `[medium]` `[patch]` E3 同对象多次出现的行身份碰撞 — id(step) 单值映射覆盖前一次位置；改为每次出现的位置，不改模型公开接口。
  - `[medium]` `[patch]` E4 无 id 旧内存尾与存储重复 — store/content 身份不匹配，兼容分支只处理 provider id；补按出现次数的旧内容匹配。
  - `[medium]` `[patch]` B1 旧尾重复且覆盖失效 — 同 E4 根因；内存覆盖必须保留已匹配存储位置，合法重复事件不能合并。
  - `[medium]` `[patch]` B2 重复字符串选第二行后跳第一行 — 同 E3 根因，labels_by_id 无法表达重复 row id；增加真实投影选择重放断言。
  - `[high]` `[patch]` B3 等待期间 A 内容可在 B 打开 — 同 E1 根因，迟到结果 key 守卫不保护当前物理内容；补 owner/meta/loading 回归。
  - `[medium]` `[patch]` B4 F1 旧页定位不会延续 — 同 E2；补异步完成选择与用户已 Tab 离开的测试。
  - `[medium]` `[defer]` B5 初始同步 COUNT/查询仍可能阻塞 — git show 基线确认查询先于完整内存判断，属既有单查询时延风险；记录到唯一 deferred 列表，不宣称所有数据库卡顿已解决。
  - `[medium]` `[defer]` B6 跨轮平面历史只有一个上下文 — 基线也是单 prefix/suffix 包围多轮步骤；本次最新有效轮修正不等于逐轮分组，记录既有展示局限。
  - `[medium]` `[patch]` B7 旧 provider id 行来源切换丢选择 — 新行 UID 测试不能覆盖既有位置→store 身份；补有限页 legacy 身份关联和来源切换回归。
  - `[medium]` `[patch]` B8 更多改变页大小缺少迟到/最终成功验收 — 这是新增扫描 key 的用户可达分支；新增旧结果被拒、新请求最终成功测试。
  - `[medium]` `[patch]` B9 后台查询错误分支未受测试保护 — 既有失败只发生第一条前台查询；补第3页抛错一次后的真实事件循环自动恢复。
  - `[medium]` `[patch]` B10 GUI 没有先显示 A 再切 B 的等待期断言 — 同 E1 根因及测试缺口；补 label/meta/详情入口隔离断言。
  - `[high]` `[patch]` I1 跨聊天隔离意图在等待期偏离 — 同 E1，修复当前物理页而不只检查异步结果。
  - `[medium]` `[patch]` I2 F1 最新项意图偏离 — 同 E2，保留显式导航意图到成功应用。
  - `[medium]` `[patch]` I3 旧数据身份导致分页重复/缺失 — 同 E3/E4 的报告合项，修复出现位置与旧数据匹配；保留两种独立坏结果。
  - `[medium]` `[patch]` V1 后台失败自动恢复验证缺口 — 按 verification-gap 已检索证据直接判定；第3次读取一次错误，禁止手动第二次 render，断言最终 dirty/pending 清除。
  - `[medium]` `[patch]` V2 聊天切换等待期归属验证缺口 — 按该层证据直接判定；补 A→B 的受阻扫描及详情测试。
  - `[medium]` `[patch]` V3 异步 F1 行为缺陷 — 该层 Other findings 复核与 B4 相同调用路径，合入 F1 修补。

以上修补只修正已证明的内部状态/兼容分支并补测试，不新增公开接口或产品语义；保持第1轮 KEEP 约束。两个新 defer 和第1轮两个既有 defer 均保存在 frontmatter 唯一列表。

补丁后的独立验收补充：主筛选147通过；GUI首轮3失败（普通Tab去了history、普通Enter走错分支）。随后只读GetAsyncKeyState/GetKeyState确认系统左Shift及总Shift均处于按下状态，Ctrl/Alt未按下。测试的SendMessage继承系统修饰键是可证实的夹具隔离缺陷，另作为1项medium/patch交回同一engineer：仅在线程局部明确发送所需修饰键并finally恢复，不发送全局松键，不修改产品导航或放宽阈值。此验收发现不冒充四层审查原始20项之一。

追加完整GUI文件以覆盖发键helper全部调用者，首次19通过1失败。定向轨迹证实：发键前Yield已消费至第20批，问题行被100内容行分页淘汰，选中行从0合法回退到1；Down后到2且正文从background plan 1到2，并非双跳。另原夹具在发键前已耗尽队列。作为第2项medium/patch修正该节点：基于发键前的稳定内容身份断言单步移动，使用真实批次timer/事件循环保证发键时确有pending，保持0.5秒门槛与其他控件断言；完整文件命令加入Verification。

## Spec Change Log

### 2026-09-08 — 第1轮审查后的重新实现约束

触发：B1–B6、B8、E1–E3、I1–I2 证明方案在有限页查询、身份来源和异常调度方面不足。原始intent-contract不变。先保存第1版代码补丁到 `C:/Users/gladwell/AppData/Local/Temp/execution-ui-pass1-preserved.patch`，再仅撤回本轮7个代码/测试文件，保留本规格及证据。以下内容覆盖原T2–T5未限定的实现细节，所有原AC仍须通过。

KEEP：保留增量原生行同步与陈旧物理尾项修复、chat隔离、问答上下文、100内容行分页、无焦点保留选择/F1显式定位、Tab一次导航、Codex/Kimi批量深度、临时数据测试、125+7+244+45已过的合理契约与新增边界测试。可读取保存补丁复用已经验证的代码，但必须按以下约束重新审视读取/投影/失败传播；不是机械重放整份旧实现。

1. 历史大规模隐藏记录扫描不能在单个GUI回调内循环读完整历史。优先把纯存储读取/过滤/合并放到后台，GUI只应用有限结果；或采用有界续扫。请求必须带chat、轮/页和代次，切换/清空/新事件后丢弃过时结果，关闭后不访问控件。原有页面同步接口可保留，小页快速路径不得变成无限同步扫描。每个GUI回调最多2个有限存储页查询，后续读取必须释放主线程；单个查询limit不超过当前页大小。长隐藏尾测试应人为阻塞后续读取，验证UI原生按键在读取未完成时仍被处理，再释放读取验证最终正确页。
2. 为大扫描提供不重复COUNT的cursor页接口或兼容可选参数；现有load_recent_execution_steps调用兼容，无schema迁移。可见判断采用与UI一致的纯数据规则，在决定读够之前应用内存覆盖。结果页至少能够区分真实前页与已耗尽，不能因隐藏覆盖而提前宣布没有更多。
3. 同一历史记录的稳定位置适用于所有加载路径；重复item_id是合法记录，不得用它独占行身份。稳定存储行身份优先，完整内存来源保留相同标识；新记录的持久化前后身份亦需稳定。只为显示页构造最终id，旧重复项需要的位置数据可在有界读取时计算，不在UI为全部隐藏/页外文本生成key。
4. 历史多轮页面上下文锚定该显示页最新有效轮，不能因为了检测更多而预读旧轮就换成旧问题/答案。当前active仅当前轮保持不变。
5. 一次模式切换只计算一次投影并复用；无变化同步零控件写。所有执行UI读取/投影失败保留dirty且安排受控重试（沿用现有delay机制，避免零延迟忙重试），展示失败不能中断事件队列续调度和执行持久化启动。原生drain和idle回调中注入一次错误后，不手动第二次flush，自动回调必须恢复；仍排队事件必须继续处理。
6. 补充Kimi changed batch的实际同步/重绘至多一次，补共享模型排列及中途失败重试，补真实wx事件/按键交错和长期隐藏读取未完成时仍响应；所有测试加入Verification的已选择文件/selector，必要时补命令。记录新AC映射与实际测量，不以跳过或调高阈值过关。

## Auto Run Result

以下“15 项 red”至原验证摘要保留第1版证据；第1轮审查后的重新实现结果见本文末尾。T6 的独立审查及本地提交由主代理继续完成，工程代理未提交、推送或部署。

### 15 项 red 逐项归类

原始命令复现 `15 failed, 99 passed, 631 deselected`（31.26 秒），没有使用 skip/xfail，也没有删除失败测试。

| 原测试名称的区分部分 | 分类及处理 |
|---|---|
| apply_detail_panel_mode_only_rebuilds | 实际进入模式漏同步；修复进入条件，spy 接受现有 force 参数。 |
| append_execution_step_refreshes | 实际 Clear 全页；增量同步保留 Clear=0、Append=1，正文与新增稳定 id 一并断言。 |
| append_execution_entry_preserves_selection | 原断言要求未聚焦列表跳到末尾，与保持阅读位置矛盾；按最终导航契约改名并断言原选择不动。 |
| active_turn_completed | 旧占位行契约与实际完成后未投影答案同时存在；改名并断言问题、最终回答顺序，保留不调用全量 render 的预算。 |
| switch_to_execution_mode_flushes_pending_delta | 实际同步两次；delta 吸收后一次页面同步。 |
| final_answer_live_event | 同完成项：保留问题并立即增量展示最终回答，完成不切视图。 |
| execution_event_appends_single_row | 实际 Clear；原禁止 Clear 断言保留并通过。 |
| execution_list_tab | 实际手动导航后仍 Skip；去除重复交接，新增原生 wx 焦点验证。 |
| pending_execution_steps_append_after_quiet | 队列夹具未写 canonical state；改名并以已入库/内存 owner 状态为输入，验证只同步一次且无重放。 |
| pending_execution_flush_keeps_unappendable_rows | 旧轮通知不是待重放业务数据；改名并验证通知被消费、旧轮不复活；新增失败重试保留通知测试。 |
| active_execution_list_shows_only_current_turn | 旧断言漏当前问题；改名并同时断言当前上下文和步骤，无旧轮内容。 |
| execution_list_defaults_to_latest_100_rows | 旧断言漏合成回答；改名并断言完整 100 内容行序列。 |
| execution_list_uses_recent_store_page | 同分页口径，保留禁止全量 loader；新增真正历史 summary hydration 和分页测试。 |
| execution_list_more_item_expands | 旧断言漏问题/回答；改名并断言展开后全部 222 行恰好一次。 |
| appending_execution_entry_keeps_latest_100_rows | 旧断言漏回答占位；改名并断言步骤追加后仍为 100 内容行，最终回答位于末尾。 |

进一步核验发现原 deferred 单元测试把“主控件持有焦点”视作永久冻结条件，而 Kimi 已验证契约要求 quiet 外显示新内容并保持选择。该测试改为真实 quiet 前提，保留不重绘和延迟调度断言；quiet 外每批一次同步，所有列表均保留现有内容身份，只有显式 F1 跟随最新项。Kimi 原测试未改动，244 项全部通过。

### 实现与验收结果

- AC1–AC3：共享模型按行身份 Delete/Insert/Append 并修正物理标签，无全页 Clear；同状态不写控件、不选择、不 repaint。新增缺 id 重复行、相同时间戳存储行、问题插入、分页滚动及选择淘汰测试。新入站无 id 记录补稳定 id；旧存储记录使用内部 step_index，显示身份包含 chat，合成行包含 turn。
- AC4–AC5：Codex 与 Kimi drain 合并一次执行页投影；quiet 通知在成功同步后消费，失败保留，隐藏页只标 dirty。切换先吸收 delta；无变化切换不重建。完成回答投影不改变视图或焦点。后台新增及 quiet flush 不自动选择末尾。
- AC6、AC10：真实 wx 控件与原生窗口 Tab 消息、线程 Shift 状态共 10 轮、40 个事件、10 次页面同步；每轮 Tab 到 input，Shift+Tab 回 execution，再 Shift+Tab 到 history；每轮保持所选内容且 Clear=0。最后一次定向运行中位数 18.81 ms、最大 79.34 ms，原有 0.5 秒门槛未放宽。F1 与 Enter/Shift+Enter 原回归通过。
- AC7–AC8：99/100/101 内容行、有/无问题、仅回答、完整展开、隐藏前页和未保存尾部均有测试。`chat_store.load_recent_execution_steps` 增加可选 before_step_index 和内部稳定行索引，签名向后兼容，无 schema 迁移。每次查询不超过当前内容页 limit；350 原始记录、230 个尾部隐藏记录的初次历史渲染为 4 次、每次最多 100 条读取，禁止全量 execution loader。为了准确判断可见前页，隐藏记录多时需要更多有界查询，最坏会逐页检查全部隐藏记录；不是恒定总查询次数。每次查询仍仅取有限页，内存仅保留候选可见行与当前页。
- AC9：244 项 Kimi owner/session/offset、集成、wx、跨聊天、远程分派回归全部通过。

### 实际验证输出摘要

- 规定主筛选：`125 passed, 631 deselected, 1 warning in 37.38s`。warning 是原 wx shiftDown 弃用属性，无新增失败。
- 扩展 Codex GUI 筛选：`7 passed, 12 deselected in 6.32s`；测量见上。
- 规定 Kimi/路由七文件：`244 passed in 27.24s`。
- 全部 listbox_model 与 chat_store 单元测试，加文件提取和常用指令真实 UI 调用者：`45 passed in 11.53s`。
- 补充 `py -3.11 -m pytest tests/test_main_unit.py -q -k "execution_history or execution_quiet_sync or execution_store_merge or initializes_incremental or answer_list_render_populates_incremental" --tb=short`：`6 passed, 750 deselected in 6.04s`，覆盖最后的读取失败保留 dirty 调整相关路径及回答模型调用者。
- `compileall`（main/listbox_model/chat_store 与四个修改测试文件）和 `git diff --check` 通过。

局限：未进行人工读屏或真实付费 provider 测试，未运行全仓冻结的其余领域；测试使用临时数据与 fake provider。没有修改旧基线、handoff、AGENTS 或真实用户笔记/聊天数据。共享模型还服务历史和笔记列表；本次验证涵盖通用模型、主执行/回答、文件提取和常用指令，未宣称全笔记领域已验证。

### 第1轮审查后重新实现结果（2026-09-08）

- AC1–AC3 / B6、B8、B9、E2：保留原生增量行同步和零写入回放。显示页才构造最终 ID；既有存储行在完整加载和分页加载中均携带同一 step_index，重复 provider item_id 不再独占身份。新入站记录携带可持久化 UID，覆盖持久化前后选择稳定；历史完整来源切换保留选择。新增模型排列和第1/第2次原生写失败后恢复测试，无 Clear。
- AC4–AC5 / B4、B5、E3、V1：模式转换只有一次投影；展示边界捕获读取/投影/原生更新错误，保留 dirty 和通知，用现有1000ms idle机制自动重试。Codex/Kimi drain 异常注入测试验证后续队列继续消费、持久化启动三次且最终页面恢复；idle 数据库读取错误也由真实 wx timer 自动恢复，没有手动第二次 flush。Kimi 四事件真实 drain 只同步和重绘各一次。
- AC7–AC8 / B1–B3、E1、I2：前台最多两次有界读取，仍需扫描时后台续读，UI只应用结果。请求身份包含 chat、view、turn、页大小、代次及内存来源；切换、清空、新事件和关闭丢弃过时结果。续页 include_total=False 不再 COUNT，SQL追踪测试验证。内存覆盖后才计算可见候选；上下文锚定最新可见有效轮。历史完整内存快速路径要求存储位置覆盖，而非仅用尾部长度猜完整性。
- AC6、AC10 / I1：真实 wx 排队 drain 与原生按键交错，10轮40事件、10次同步；中位23.53ms，最大63.54ms。另一个真实GUI测试人为阻塞第3次后台查询，读取未完成时原生Tab已在2.77ms内切到输入框；释放后最终页正确且输入框保留焦点，共5页查询，其中前台2页。所有查询limit=100，原0.5秒门槛未修改。

本轮实际验证：

- 主规定筛选：136 passed, 631 deselected, 1 warning in 65.46s；唯一warning仍为旧wx shiftDown弃用属性。
- Codex GUI规定筛选：8 passed, 12 deselected in 12.52s。
- Kimi/路由七文件：244 passed in 27.89s。
- 全部listbox_model测试及chat_store、文件提取、常用指令调用者：49 passed in 12.37s。首次运行暴露完整loader字典断言漏新增位置字段及新SQL追踪夹具错误，修正后复跑全组通过。
- 最后历史来源/上下文和后台隔离调整后补跑：`py -3.11 -m pytest tests/test_main_unit.py -q -k "execution_history or execution_store_merge or execution_new_row_identity or execution_drain_projection or execution_kimi_changed or initializes_incremental or answer_list_render_populates_incremental" --tb=short`，15 passed, 752 deselected in 21.53s。
- 更新后的compileall及git diff --check通过；无schema迁移，无删除/skip/xfail测试。旧F1 helper调用断言改为实际控件零写入断言，旧手动失败重试改为真实timer自动恢复断言。

工程实施完成，等待父代理独立验证与审查。局限仍为未执行人工读屏、付费provider和全仓其他领域；单次SQLite读取仍同步执行且限页，长扫描总页数取决于隐藏历史长度，但两页之后不占GUI回调。关闭时取消后续扫描，正在执行的SQLite调用在返回后退出，未引入强制终止数据库线程。没有提交、推送、合并、部署或改真实用户数据。

### 第2轮修补与主代理独立验收

原始15项red已全部修复或更新为明确的产品契约；没有删除失败测试、skip/xfail或调高响应时间阈值。原生行增量同步、一次批量投影、稳定身份选择、隐藏/quiet零写入、长历史后台续读与失败自动恢复均保留。

审查修补完成：跨owner等待期先清旧详情并展示loading；加载提示原生写失败也不会中断drain且不能复制旧正文；F1显式最新项意图关联owner/代次，离焦后不抢焦点；历史实际模式与可见性判断同步；重复对象按出现位置生成身份；旧记录按内容/时间/种类和出现次数合并，仅唯一provider候选可降级覆盖，有限页别名保留来源切换选择。补上后台第3页一次失败自动恢复及页大小变化旧结果拒收、新结果成功的验收。

文件交付（仅本轮8个文件）：

- `main.py`：执行页同步、状态归属、后台扫描、恢复调度、选择/F1/Tab。
- `listbox_model.py`：无Clear的增量原生行对齐与失败后重试。
- `chat_store.py`：完整/分页统一存储位置，游标分页可省略COUNT，无schema迁移。
- `tests/test_main_unit.py`：旧契约归类更新、身份/归属/模式/批量/失败/分页边界。
- `tests/test_codex_ui_responsiveness_automation.py`：真实原生按键与事件交错、A→B等待期隔离、线程局部修饰键隔离。
- `tests/test_listbox_model_unit.py`：排列、重复刷新、中途原生写失败恢复。
- `tests/test_chat_store_unit.py`：游标SQL和加载来源位置一致性。
- 本规格：第一性原理诊断、修复/验收/测试方案、两轮证据及剩余风险。

主代理最新规定命令结果：

- 主筛选：147 passed, 631 deselected, 1 warning in 75.00s（旧wx shiftDown弃用warning）。
- Codex GUI筛选：8 passed, 12 deselected in 12.12s；10轮40事件10次同步，中位20.97ms、最大83.46ms；后台读取受阻时Tab 2.74ms，前台2页/总5页。
- Kimi/路由七文件：244 passed in 26.18s。
- 共享模型、存储、文件提取、常用指令：49 passed in 11.18s。
- compileall 与 git diff --check 通过。以上448项互不重复；工程代理的12项定向及2项GUI不另加总。

审查处置：第1轮20项按日志逐条判定，4项false的反证保留在原行（重复非法模型ID不可达、未聚焦保持选择符合契约、既有244项回归可作证、审查中未提交不是缺陷）；第1轮真实设计缺口已重新实现。第2轮20项中18行合为6个修补组（high 1、medium 5），2项medium/defer；另有验收发现的线程局部修饰键隔离medium修补。唯一deferred列表共4项，无静默丢弃。原edge审查者定点复核E1/E2修补及异常/焦点路径通过。

完整GUI联跑的恢复超时也已查明：扫描finish=681157.921，而Tab后的quiet_until=681160.906；idle在681158.921检查后按策略延后，681160.921才刷新。因此原3秒完成窗口与3秒导航静默相撞，不是后台数据丢失。修正测试分阶段验证quiet内loading、模拟quiet到期后只等待已有timer自动发布；不手动render、不提高3秒或0.5秒阈值。两个测试自己启用的真实timer在本地finally/finalizer清理，避免fixture直接Destroy后残留回调污染其他测试；不修改全局fixture或产品导航静默时长。

进一步完整联跑揭示既有其他窗口的idle CallLater在窗口销毁后仍会派发；创建计时器时的alive检查不足以保证调用时仍存活。局部测试cleanup不能保护这条可达路径，故在共享idle回调清除scheduled/timer引用后使用现有_is_ui_alive守卫返回，补销毁窗口后真实timer派发回归；没有全局吞异常或改计时器架构。共享idle的6项既有测试命令已加入Verification。这是联跑揭示的关闭后回调边界修补，不隐去此前19通过1失败的证据。

联跑剩余12秒延迟经cProfile确认是测试间残留保存timer：Yield派发30个旧CallLater，19次_save_state→17次_flush_chat_state_save→3次replace_execution_steps，2003条SQLite append/commit耗11.85秒。采用本GUI文件局部autouse资源清理，在各节点真实运行期间保留产品timer/drain/保存，仅退出时停止该节点创建的CallLater并取消扫描，先于frame.Destroy执行。不改全局conftest或_save_state语义，不以禁用运行中持久化来通过性能门槛。

剩余范围：未进行人工读屏、真实付费provider、全仓其余领域；初始两次同步SQLite读取的单次时延和跨轮平面上下文仍属已记录限制。真实读屏对loading→内容发布的播报顺序尚未验收，不能据本轮绿色GUI测试宣称读屏听感已验证。

后续审查建议：true。按第2轮审查修补组计high 1、medium 5（不计defer、false和重复报告）；存在high修补，具体未验证风险是实际读屏软件在跨聊天loading→内容替换时的播报归属/顺序。自动GUI已验证标签、meta、详情、选择和焦点，但没有真人听读屏。建议后续做这项人工无障碍验收，不将其伪装成本轮已完成结果。

### 最终独立验收与交付（2026-09-08）

下列是所有代码及测试修补完成后，主代理串行运行的最终结果，覆盖上文中间版本的通过/失败记录；未同时运行两组GUI。

| 验证组 | 最终结果 |
|---|---|
| 主逻辑规定筛选 | 148 passed, 631 deselected，78.89s；1项既有wx弃用warning |
| 共享idle_history / idle_ui | 6 passed, 773 deselected，0.98s |
| Codex GUI规定筛选 | 8 passed, 12 deselected，9.95s |
| 完整Codex GUI文件（含上述8项） | 20 passed，13.72s |
| Kimi/路由七文件 | 244 passed，26.56s |
| 共享列表/存储/文件提取/常用指令 | 49 passed，11.24s |
| compileall / git diff --check / frontmatter YAML | 全部通过；deferred仅一个列表、共4项 |

去重总数：148+6+20+244+49=467。Codex筛选8项和此前定向测试不重复计数。最终GUI测量：10轮40事件10次同步，中位15.01ms、最大55.68ms；后台读取未完成时原生Tab 5.07ms，前台2页/总5页，全部原门槛保留。

验收结论：AC1–AC10在本文界定的自动化范围内通过，人工读屏明确未执行。补充联跑修复了旧固定下标断言、真实pending夹具、修饰键污染、quiet/恢复阶段冲突、跨用例保存timer污染，以及销毁后idle回调守卫；没有删除测试、增加skip/xfail或放宽门槛。只交付已审查的8个文件，在fix/mobile-kimicode-routing本地提交；不合并main、不推送、不部署，不修改真实笔记或聊天数据。提交号由最终交付消息提供。
