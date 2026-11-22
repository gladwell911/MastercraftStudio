---
title: 'Kimi 运行时的键盘导航与读屏响应'
type: 'bugfix'
created: '2026-11-22'
status: 'built'
route: 'full'
route_reason: '涉及后台持久化、界面调度及其交错验证。'
review: 'thorough'
review_source: 'auto'
lenses_ran: ['blind-hunter', 'edge-case-hunter', 'verification-gap', 'intent-alignment']
followup_review_recommended: true
baseline_revision: '969a14519b186799832b140828a8e5632cd6c173'
context: ['AGENTS.md', 'docs/README.md', 'tests/test_kimi_ui_responsiveness_automation.py']
---

## 目标与依据

<intent-contract>

修复 `D:\code\cx\md\mc.exe` 对应源码中的 Kimi 任务导航迟缓。任务输出期间，Tab 和方向键应及时更新焦点与选中项，历史结果可恢复，原有 Codex 导航、Kimi 会话归属及隐私撤回语义保持有效。真实争渡播报时延作为现场验收。

</intent-contract>

用户在 Kimi Code 任务期间按 Tab 切换控件、用方向键换行时感觉播报迟缓，Codex 同类操作正常。目标是在流式写库和思考记录补齐期间仍能及时处理原生焦点和选中事件，随后能从历史库恢复完整结果。诊断对象是 `D:\code\cx\md\mc.exe`。实施以本地提交 `969a145` 为基线，其中已单独保存此前工作区的历史改动。

- Kimi 事件通过 wx 主线程进入 `mc_frame_execution.py::_buffer_execution_delta`，直接调用 `chat_store_execution.py::apply_execution_fragment`；其 `BEGIN IMMEDIATE` 会等待 SQLite 写锁。REST 撤回公开内容的 `mc_frame_kimi_events.py::_redact_kimi_narrative` 也直接写同一存储。减小事件批量无法打断已经开始的事务。
- Kimi 的 REST 取消息已在后台，`_kimi_thinking_fetch_worker` 通过 CallAfter 把 `_kimi_sync_thinking_rows` 整批排回主线程。它为每条 assistant 消息、两种内容类型重复扫描执行记录，在去重前就发生。直接用目标包方法和合成数据运行六次，80 条消息、400 条记录中位约 8 ms，160 条、800 条约 34 ms。此数据衡量单次核对开销，实际按键到出声的延迟仍待现场测量。

## 实施顺序与验收

- [x] **1. 固定回归入口。** 在现有 Kimi wx 测试中启用临时真实 ChatStore，走 SDK 消息映射、UI 入队和实际执行片段路径。用独立 SQLite 连接占写锁，以事件门闩和有界看门狗保证失败可收尾。排入 UI 心跳及窗口 Tab、上下键。断言锁释放前心跳已执行、焦点已切换、目标列表选中已变化，记录写入端线程身份。基线应先复现旧实现的失败；以顺序断言判定通过，机器速度仅作参考。
- [x] **2. 移出片段写入并保持因果顺序。** 复用现有执行记录持久化 worker 的生命周期或接入等效的有序后台通道。让普通 Kimi 流和 REST 撤回均通过该通道，工作项拥有独立输入，以 chat、revision、session、turn、agent、logical key 及适用的 prompt owner/代次定位。保留 store 返回的 accepted、replay、conflict、pending_gap 和 contiguous assembled 语义。使工具边界、flush、权威完成和关闭等待各自的前序工作，等待时 wx 可继续处理导航。高频片段合并唤醒、按序排空，避免逐片创建线程或在 UI 等锁。验证写入失败可观察，未成功工作能按幂等语义重试；已有 Codex 流程作对照。
- [x] **3. 分开 REST 计算与 UI 提交。** 后台从只读快照提取 prompt 边界、顺序、公开块与撤回标记，或使用可让出的有界增量计算。一次建立候选索引，消除逐消息全表扫描；快照建立也要避免主线程无界复制。UI 仅应用有变化的小批次，批次间返回 wx。相同 owner 的重复通知可合并，但保留撤回和终态的顺序。每批核对 owner、revision、session、turn 及适用的代次；切换可见聊天后仅更新正确归属。已完成 owner 的合法历史补齐、steer alias、结构化 injection 边界和子代理隔离继续生效。
- [x] **4. 校验隐私、存储与退出。** 收到隐藏标记即撤回内存、回答缓冲和可见详情，标记保护贯穿已排队及迟到的公开结果。用临时库核对 assembler、执行投影、远端可读详情及重启后加载均无已撤回正文。交错覆盖重复/乱序、缺口、冲突、清理后旧回调、不同会话同 turn、终态早于写完、连续两次关闭和写入失败。正常退出前排空已接受的工作，使用现有延迟关闭机制，在窗口销毁后拒绝 UI 回调。测试隔离日常聊天库和笔记库。
- [x] **5. 证明 REST 核对也会让出 UI。** 在真实 wx frame 交付大份合成 transcript，保留真实 REST 解析/投影路径；通过受控 worker 门闩或批次调度器排入心跳与导航，断言在完成整份核对前已执行，并检查最终行、详情、焦点和选择。用全量重复快照验证无新增行时不会触发列表重绘。若分片应用，在两批间插入 clear、撤回或关闭，用数据库重读验证过期回调无法恢复旧正文。

## 验收条件

- 给定隔离数据库有另一个连接持有写锁，当真实 Kimi 流式事件入队且用户按 Tab 或方向键时，焦点及选择在释放写锁前完成。事务随后完成，并可从重新打开的历史存储读回。
- 给定长 Kimi 消息快照及已有执行记录，当 REST 补齐与导航并行发生时，原生焦点及选择在处理完整快照前完成。重复快照保持现有行与选中项，无新增重绘。
- 给定片段乱序、重复、终态、撤回、清理、退出或切换交错，当 worker 写入和回调完成时，只有对应会话代次的合法内容可见，SQLite 重开后也保持同一结果。Codex 通过对应现有验证。

## Verification

使用仓库虚拟环境串行运行 wx 套件。按下列组记录命令、通过数及已有基线失败；交付时标明使用的源代码与构建路径。

1. `.venv/Scripts/python.exe -m pytest -q tests/test_kimi_ui_responsiveness_automation.py tests/test_desktop_navigation_ui_automation.py tests/test_codex_ui_responsiveness_automation.py`
2. `.venv/Scripts/python.exe -m pytest -q tests/test_kimi_integration_streaming.py tests/test_kimi_integration_ownership.py tests/test_kimi_integration_recovery.py tests/test_kimi_f1_e2e.py`；再运行覆盖修改后存储、写入重试与关闭函数的定向用例。用重开临时 SQLite 的内容核验最终状态。
3. 分别报告写锁被持有期间可导航、长 transcript 核对期间可导航，以及宿主测试是否取得原生焦点/选择事件。现场验收在明确标识的目标构建用相同控件、同一读屏环境对照空闲、Kimi 流式和 Codex 流式，分别记录焦点变化与实际播报延迟。真实云端、物理键盘、声学出声与日常部署各自注明验证状态。打包、切包和日常程序的退出/重启遵循当次用户授权。

## 源码实施与验证记录

- `D:\code\sj\mc` 基于 `969a14519b186799832b140828a8e5632cd6c173` 实施。Kimi 片段、执行投影和撤回复用执行记录写入 worker。原生事件、工具边界和终态通过有界 wx 批次衔接。REST worker 整理 transcript，wx 分批索引和应用当前归属发生变化的记录。
- 在基线源码运行新增的锁回归时，SQLite 锁先释放，wx 心跳随后才执行。修复后锁仍被持有时，心跳、Tab 焦点与上下键列表选中事件均已到达，随后重开临时库读回完整片段。长 REST 快照回归也观测到同类原生事件，重复投递未触发列表重绘。
- 定向交错覆盖写入失败后重试、终态等待、连续关闭、REST 批次间清理、公开详情撤回及打开新库后拒绝旧快照。对照 wx 套件在源码虚拟环境串行运行，聊天和笔记数据均使用测试临时目录。
- 日常目标仍为 `D:\code\cx\md\mc.exe`。本轮验证使用源码与合成 SDK/REST 输入，未生成冻结包，未切换日常部署。真实云端、物理键盘和同一读屏环境的按键到声学出声延迟等待现场验收。

| 验证项 | 本轮源码 | 基线对照 |
| --- | --- | --- |
| Verification 第 1 组，Kimi、桌面导航、Codex wx | 76 通过、2 失败 | 71 通过、相同 2 项失败。历史导航完成时重排和 Codex 最新回答刷新断言失败。两版的原生回答查看器测试均记录 Windows COM `0x8001010d`。 |
| Verification 第 2 组，Kimi 流式、归属、恢复、F1 | 154 通过、3 失败 | 154 通过、相同 3 项失败。失败项为 `test_kimi_tool_completion_updates_started_item_with_result_and_failure`、`test_kimi_completion_does_not_overwrite_reused_item_id_from_another_turn` 和 `test_real_fixture_status_thinking_and_tool_result_produce_primary_chinese_steps`。 |
| `test_kimi_ui_responsiveness_automation.py`、`test_chat_store_unit.py` | 93 通过 | Kimi wx 14 项和 ChatStore 79 项全部通过。 |
| `test_main_execution.py`、`test_story4_review_regressions_unit.py` 按 `persist_flush_waits or persistence_worker_defers_close` 定向筛选 | 2 通过 | 扩大范围的 12 项源码失败在基线重现。独立基线检出另缺同级 RC fixture。 |
| `test_kimi_integration_streaming.py` 按 `sdk_assistant_privacy or mapped_private or monotonic` 定向筛选 | 6 通过 | 包含从重开的 SQLite 恢复隐藏标记后丢弃迟到公开片段。 |

## Code Review

### 2026-11-22 · 独立 dog 全量审查

0 项待决策，10 项待修复，0 项延期，1 项驳回。当前实现已证明常见流式写锁场景可以导航，但下列撤回、关闭和长 transcript 问题仍可在源码重现。按本次委派保留为行动项。

- [x] [Review][Patch] **高：缓存外的原始公开行未撤回。** [mc_frame_kimi_events.py:393] 在临时库写入 `native-stream` 的公开详情，将其挤出 500 行缓存，然后投递同 step 的整条消息撤回。库中产生两个空白 REST 标记，原始 native 行仍含合成正文。后台按现有 chat、revision、session、turn、agent、message/step 查找真实持久化候选，并撤回每个匹配的逻辑键。来源：edge-case-hunter、独立探针。
- [x] [Review][Patch] **高：有公开兄弟块时，本次才出现的隐藏块缺少持久标记。** [mc_frame_kimi_events.py:393] 先写消息 m 的公开块 0，再标记块 1 私有，库中仍只有块 0。重开临时库并投递旧公开快照后，块 1 的正文被写回。逐一检查受保护索引，即使同消息有其他槽也要保存缺失的撤回标记。来源：blind-hunter、edge-case-hunter、独立探针。
- [x] [Review][Patch] **中：原始 replay 和 JetStream 单独注入的发布分支仍可读到撤回正文。** [chat_store_notifications.py:595] [chat_store_notifications.py:709] [remote_nats.py:493] 同一个合成 execution fact 撤回后，详情页返回 0 条，原始 replay 返回旧正文。真实 drain 方法接入捕获器后，已连接客户端只发无正文的 owner hint，只有 JetStream 的分支发出含旧文的 events。未找到正式 RPC 直接调用 replay_after。对可交付数据统一应用撤回判定，重放可沿用 SNAPSHOT_REQUIRED 重新取权威快照；备用发布交付不含正文的投影，核对合法哈希、事件身份、序号及 ACK 连续性。保留不可变原 fact 和发布进度，不能靠删除队列行解决。来源：四个视角中的 blind、edge、verification 及独立探针。
- [x] [Review][Patch] **高：关闭丢弃已经开始处理的 REST 尾部。** [main.py:1402] [mc_frame_kimi_events.py:203] 已接受的 iterator 在下一批开始前关闭，继续函数直接将 sync count 从 1 清零，没有执行下一条撤回。终态可随即放行，或窗口先销毁。拒收关闭后新增任务，已有 iterator 继续分批排空，关闭门禁同时等待它和产生的写入。来源：blind-hunter、edge-case-hunter、独立探针。
- [x] [Review][Patch] **高：写入重试耗尽与成功共用结算。** [mc_frame_execution.py:1320] 三次稳定注入 OperationalError 后，实测回调收到 None，队列、write counts 均为空且 boundary 已释放。重开库会缺历史；对投影，已登记的 REST 去重键也会抑制重投。区分临时失败与明确拒绝，保留失败项及顺序以便之后后台重试，或显式报告未保存并保留可恢复工作；成功前不把相关终态/关闭当作已持久化。来源：blind-hunter、edge-case-hunter、独立探针。
- [x] [Review][Patch] **中：撤回一条回答会清空同会话另一 owner 的同名流。** [mc_frame_kimi_events.py:70] 段键第 3 项是 prompt owner，清理时没有比对。两个 owner 同用显式 item ID，方法调用一次后两个公开回答缓冲都为空；SDK 映射允许显式 item/message ID。将被撤回 turn 对应的 owner key 传入并按完整归属匹配。来源：独立探针。
- [x] [Review][Patch] **高：500 行缓存饱和后，每新增一行都重新索引全表。** [mc_frame_execution.py:1419] [mc_frame_kimi_events.py:310] 淘汰最早一行会替换 list；iterator 见新 list 就从头按行 yield。500 行满缓存、21 条 REST 消息（20 条新投影）的探针计得 10,023 次 yield，即 627 批；正常主循环为每个续批设置 10 ms 定时器。真实墙钟未在此估算。索引维护要支持从缓存头淘汰，避免每次更换 list 就整表重建。现有导航测试最高 440 行。来源：独立探针。
- [x] [Review][Patch] **高：单条大消息内部尚无可让出的位置。** [mc_frame_kimi_events.py:331] [mc_frame_kimi_events.py:411] 同一消息 600 个公开思考块，一次 next() 已触发 600 次投影。每个工具边界或撤回也可能反复遍历整个 delta buffer、按行重建可见列表。按块与候选数量分批，在恢复后重验归属，将一次撤回批的可见变化合并更新；对命中的缓冲按流定位。来源：blind-hunter、edge-case-hunter、独立探针。
- [x] [Review][Patch] **中：重复 REST 快照可以同时占用 wx 及终态门禁。** [mc_frame_kimi_events.py:199] 生产端按会话节流抓取，但上次迭代超过节流时间时下一次仍独立索引全部已有行。每个迭代都增加 sync count，终态等待最后一份。先合并同 owner 相同且尚未完成的投递，同时保留先前撤回；超过缓存上限时覆盖两份重叠投递。来源：blind-hunter、调用链核对。
- [x] [Review][Patch] **中：原生导航测试没有覆盖实际主循环定时器续批。** [tests/test_kimi_ui_responsiveness_automation.py:270] 当前 wx fixture 手动 Yield，按键观察挂在 CallAfter fallback；主循环走 CallLater。覆盖实际定时器分支，观察两批之间的导航及最后一条记录的持久化。来源：verification-gap。

**驳回**：blind-hunter 推断 `_persist_kimi_execution_step` 忽略远端发布失败的返回值。核对 `mc_frame_remote_routes.py:92`，该调用以 retry_on_failure=False 进入；提交失败会抛出异常并由 worker 重试，显式 False 对应旧 revision 拦截。三次重试耗尽的问题已另列。

## Review Triage Log

### 2026-11-22 · 独立审查修复

- 共 11 项。高 6 项，中 4 项，驳回 1 项。
- `[high] [patch]` 缓存外的公开行仍在库中。后台查询同归属的持久化候选，并撤回匹配行。
- `[high] [patch]` 有公开兄弟块时遗漏隐藏块。逐块保存撤回标记。
- `[medium] [patch]` 通用重放和 JetStream 回退可以返回旧正文。重放要求重新获取快照，回退发送无正文通知，并保留原事实与 ACK 进度。
- `[high] [patch]` 关闭丢弃已接收的 REST 尾部。关闭等待已接收批次及后续写入结算。
- `[high] [patch]` 重试耗尽后错误放行终态。暂时性失败保持 FIFO 并后台退避，持久化后才登记 REST 去重。
- `[medium] [patch]` 撤回影响另一 prompt 的同名流。匹配完整 prompt owner。
- `[high] [patch]` 缓存满后重复索引。按缓存增删维护索引。
- `[high] [patch]` 单条大消息一次提交所有块。分块让出 wx，合并撤回的可见更新。
- `[medium] [patch]` 相同快照重叠处理。合并正在处理的同归属相同快照。
- `[medium] [patch]` 原测试仅覆盖 CallAfter。补测主循环中的 CallLater，观察焦点、选中及持久化。
- `[false] [reject]` 远端发布失败被忽略。调用失败会抛出供 worker 重试，False 表示旧 revision。

### 2026-11-22 · 独立验证发现的交错

- `[high] [patch]` 暂停的旧公开迭代器持有过期撤回映射。32 块中已写入 1 块时撤回，旧迭代恢复后可使余下 31 块重现于内存和重开的 SQLite。恢复后重新核对当前归属、消息撤回及原生步骤撤回状态，再允许写入。新增真实 wx frame 和临时 SQLite 回归。

## Auto Run Result

- 完成实施任务 1–5。Kimi 执行写入后台有序重试，REST 整理移至后台并有界应用到 wx。退出等待已接收的批次和写入，撤回按准确归属生效，并保护远端可交付详情、事件重放和回退通知。
- 界面和工作调度改动见 [主界面](../../main.py)、[执行页](../../mc_frame_execution.py)、[Kimi 事件](../../mc_frame_kimi_events.py)、[Kimi 会话](../../mc_frame_kimi_sessions.py)、[回答](../../mc_frame_answers.py) 和[历史](../../mc_frame_history.py)。持久化及远端交付改动见 [ChatStore](../../chat_store.py)、[执行存储](../../chat_store_execution.py)、[事件存储](../../chat_store_notifications.py)、[远端路由](../../mc_frame_remote_routes.py) 和 [NATS](../../remote_nats.py)。回归见 [Kimi wx](../../tests/test_kimi_ui_responsiveness_automation.py)、[Kimi 流式](../../tests/test_kimi_integration_streaming.py) 和 [NATS 单元](../../tests/test_remote_nats_unit.py)。
- 修复独立审查 10 项和复验发现的交错 1 项，其中高 7、中 4，驳回 1 项见上。保留现场复核建议。定向用例验证了持久失败后恢复的顺序，实际存储长期不可写时正常关闭会继续等待，尚未做日常包现场复验。
- 修复后独立串行宽套件为 wx 82 项通过、Kimi 集成 154 项通过，共 5 项失败与基线一致。最后一次撤回交错修正后，重新运行全量 Kimi wx 文件 21 项通过、定向流式/REST 37 项通过、存储与 NATS 131 项通过。真实 wx 主循环走过 33 次 CallLater 续批，首个续批前已处理 Tab 及上下键，最后一条执行记录可从重开的临时 SQLite 读到。
- 源码验证已完成。目标安装包 `D:\code\cx\md\mc.exe`、实际云端 Kimi 任务、物理键盘和争渡实际出声时延仍待现场验收。

**范围与核验**：审查 `git diff 969a14519b186799832b140828a8e5632cd6c173` 的完整工作树，12 文件 +1185/-234，审查输入 SHA256 为 `A320290CD0F726D65937B9AB7C0F2578D3FD9DB2FD0D0EA9357E45C2077A6437`。使用 `uv run --no-cache …/render_skill.py --project-root D:/code/sj/mc --skill C:/Users/gladwell/.agents/skills/bmad-code-review --set workflow.review=thorough` 渲染一次；blind-hunter、edge-case-hunter、verification-gap、intent-alignment 均由独立会话串行完成。用 `.venv/Scripts/python.exe -` 和临时 SQLite、合成数据直接调用 REST iterator、实际缓存裁剪、执行写入 worker、关闭续批、存储重放及 transport drain；界面和网络副作用由捕获器替代，临时库随探针退出删除。此次未另起 GUI 套件；工程师已独立运行并对照前文所列五个基线失败。intent-alignment 确认当前证据止于源码 wx 焦点/选择事件与 SQLite 重读，日常目标构建、应用重启后手动打开历史、真实云端及物理按键至争渡出声均未在本次审查验证。
