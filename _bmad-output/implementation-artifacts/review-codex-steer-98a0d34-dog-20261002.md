# Codex 追加输入修复独立代码审查

日期：2026-10-02。审查角色：新 dog。只审 commit `98a0d342dd429fbd94bcda76b25e2d04cc61a1a4` 相对 `cd6ab9beb341ba69840a49f8d459cd93c281c255` 的六文件，522 additions / 37 deletions。

结论：确认 3 个产品缺陷（high 2、medium 1）、2 个测试回归缺口（medium）；另有 1 个未证实触发链的完成广播风险。只记录，不修复，不提交，不推送。

## 已证实产品缺陷

1. **[high][patch] item owner 淘汰后，旧回答归新输入。** `codex_worker_process.py:154`、`:168`、`:323`。同一任务或其他任务累计超过 256 个 item，旧回答 item owner 被全局缓存淘汰；确认 steer 用户边界后收到该旧 item 的迟到完成事件，查找失败就按 native.current 建立新 owner。旧问题的回答被添加到新问题，破坏保留原 item owner 的明确约束。edge reviewer 内存复现：原 item owner=0，经过 256 个其他 item 后 `old cached False`，steer 边界后同 ID completed final_answer 输出 `turn_idx=1`。普通工具 item 同样占用缓存，故无需 256 次用户输入。

2. **[high][patch] 失败 steer 丢失既有任务的新完成回答。** `codex_worker_process.py:105`、`:318`。已有 native turn 正常运行，发 steer RPC 期间收到旧任务首次出现的 assistant item_completed final_answer；因 item ID 尚未登记，事件进入 startup buffer。RPC 随后异常，finally 只回放 prior_completions，合法旧回答被丢弃。成功 turn/completed 实际不含正文，因此之后也不能恢复该答案。主持人使用真实 `_event_from_item` + `CodexWorkerRuntime` 和模拟 transport failure 内存复现：输出只有 scoped `error(transport fail)`，没有 `old-final` event。该回归由本次新增对 steer 期间事件的缓冲筛选引入；此前已知 native owner 的事件直接分派。

3. **[medium][patch] 失败 completion 的错误消息追加进已有正式回答。** `main.py:9892`、`:9907`、`:13498`、`:13580`；真实协议来源 `codex_client.py:1024`。已有 completed item 正文，随后 native turn status=failed 且 error.message 非空，当前/归档 completion 路径都调用新增 answer helper。该 helper 将 completion.text 作为正文追加；但真实 completion.text 是 error.message，成功 completion.text 为空。主持人直接从源码 AST 提取并执行未改动 helper：`authoritative completed answer` 变为 `authoritative completed answer\n\nquota exceeded`，并把错误登记进 codex_completed_answers。记录的 done/清空 request_error 行为是既有行为，本条只将新增污染正式答文归因于本次修改。

## 已证实验证缺口（不是当前产品失败）

4. **[medium][patch] 顶层 completion_owners 的 worker→UI 转换缺少能观测字段丢失的测试。** `main.py:15945`。worker测试仅证明producer输出，integration手工直接注入event.data；GUI虽然经过转换，但只有一个pending owner，主scope已足够完成它。若删除这两行复制，现有新增断言仍可能全部成立，其他pending owner却等待不结束。verification-gap reviewer 已检索全仓库并核对调用入口/新增断言；缺少真实顶层worker消息经UI入口收尾至少两个pending owner的断言。

5. **[medium][patch] 重载后的 item 去重行为未验证。** `tests/test_codex_integration.py:58`、`main.py:9897`。现有重放发生在保存前，重载后只核对正文/状态/metadata相等；未对重载pending turn再次重放相同item，并确认不同item仍追加。verification-gap reviewer 独立核对全仓库：忽略重载metadata而使用进程内seen集合的回归不会被现有断言发现。没有声称现实现有去重失效。

## 未证实风险

6. **[maybe-false][defer; 若触发为 medium] 最新 completion owner 失败/失效会挡住其他owner收尾。** `main.py:13351`、`:13366`、`:13383`；worker `:171` 把顶层scope设为最后owner。上游先检查最后owner的failed/generation，再进入completion_owners fan-out，因此手工构造该状态会整批return。代码路径确定，但在成功接收owner之后，真实运行中仅最新owner失效、较早owner仍有效的独立触发链尚未证明；不可把注入字段的测试称为产品复现。需要证明这组状态的真实写入链，以及native completion随后抵达。failed steer自身不会注册新owner，不能用它直接证明本候选。

## 全部原始发现逐条 triage

归一化ID在分组前判定，每条保留：

| ID | source | claim | verdict | 证据/处理 |
|---|---|---|---|---|
| 1 | blind-hunter | 第二steer边界前未知item归前owner | false | 按确认userMessage边界切换是明确契约；在新输入边界之前仍属已确认owner。没有证明该item已属于第二输入，不能因之后将出现边界就倒推。 |
| 2 | blind-hunter | 失败终态错误追加正式答文 | medium | 真实client协议+AST helper复现；产品条3。done标记是既有，仅报告新增答文污染。 |
| 3 | blind-hunter | 最新failed owner阻止其他owner收尾 | maybe-false | 静态提前return成立；缺真实可达写入链，未证实条6。 |
| 4 | blind-hunter | 全局item缓存淘汰破坏归属 | high | edge内存复现原owner0→1；产品条1。 |
| 5 | blind-hunter | 失败steer丢弃旧任务首次出现的final item | high | 主持人真实item parser+worker内存复现，只有error输出；产品条2。 |
| 6 | blind-hunter | UI投递完成先于final item导致终答丢失 | false | codex_worker_client.py:185按deque popleft，main.py:13215 append且:13233顺序消费；正常生产已序列化消息不会因UI排队重排。未证明provider真实反序。 |
| 7 | blind-hunter | 非agent completed item带final_answer进入正文 | maybe-false | client保留phase但真实provider会否给非agent该phase未证实；仅伪造组合不足证明真实缺陷。假设性低收益guard，拒绝，无专门defer。 |
| 8 | blind-hunter | 测试给成功completion手工终答掩盖协议差异 | medium | 实际成功为空、失败为error；该测试surface确实未覆盖真实失败。并入产品条3的证据，不另列产品故障。 |
| 9 | edge-case-hunter | 256项cache淘汰后迟到旧item归新问题 | high | 同条4，共同根因，归产品条1。 |
| 10 | edge-case-hunter | 最新failed/generation owner阻断fan-out | maybe-false | 同条3，归未证实条6。 |
| 11 | verification-gap | 顶层owners复制缺有辨别力的回归 | medium | 按该lens证据规则采信独立全库检索及变异演示，测试缺口条4。 |
| 12 | verification-gap | reload后去重没有行为断言 | medium | 按该lens证据规则采信独立全库检索及变异演示，测试缺口条5。 |

归一化verdict：high 3、medium 4、maybe-false 3、false 2。共同根因分组后：decision-needed 0、patch 5、defer 1、rejected 3。Rejected包含ID1/6的反证和ID7未证实低收益防护。无lens失败。

## Intent alignment 与审查边界

意图可理解为每个local question仍只拥有一个回答容器、容器内按完成item保留多段；也可理解为每个完成item独立可见消息。现有数据结构及明确不重构消息架构的约束支持前者，diff采用前者，没有据此要求新增独立消息表。

GUI测试观察了真实列表旧问→旧答→新问→新答和焦点/切聊；worker与integration分别测试协议scope和持久化。但用户提交→真实steer ACK→provider userMessage→worker→GUI未在一条真实模型测试链贯通；现场async提问在新增夹具中被抽象为final_answer AgentMessage。此为结论边界，不要求扩大本轮范围。

已读取项目AGENTS、docs/handoff.md、docs/experience.md、docs/reflection.md、docs/README.md及指定plan。render命令恰好一次成功；cwd所有命令为D:/code/sj。默认workflow四lens均执行。平台agent thread limit包含completed thread，导致edge/intent复用既有只读调查线程，verification复用blind线程；各lens明确重置任务、分两批并统一triage，但不能宣称四个全新无上下文线程。

本轮验证仅只读源码、协议调用链、内存最小复现；没有重跑GUI或宽integration，没有修改代码/测试/真实聊天数据库，没有提交/推送。之前61项通过只作为历史证据，没有代替独立审查。
