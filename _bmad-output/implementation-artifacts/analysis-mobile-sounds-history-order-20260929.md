# RC 消息缺少桌面音效与历史排序更新：分析及修复建议

日期：2026-09-29。只读调查；本文为唯一新增文件。未修改产品代码、停止正在运行的 MC、发送测试请求或写入生产数据。已阅读 AGENTS.md、handoff、experience、reflection；其旧验证记录不视为当前版本验收。

## 结论

两项现象都有明确的代码分支差异。手机向后台聊天发消息走专用提交路径，该路径遗漏发送音效与桌面历史列表更新；Codex 后台完成分支还遗漏回复音效。桌面列表本身另有“当前聊天永久第一”的规则，以及缺少消费逻辑的延迟重排标记。因此仅补一次刷新，仍不能达到用户期望的最近聊天上移行为。

音频资源已做只读存在性检查：打包目录 `_internal/sound/send.wav` 为 352060 字节，`reply.wav` 为 331108 字节。不能据此证明扬声器实际输出，但目前优先根因是调用缺失，无需先重装音频依赖。

## 根因链一：发送与回复音效

### 发送声音缺失

1. `main.py:11595 _remote_api_message_ui()` 根据 requested_chat_id 判断目标是否为当前聊天。
2. 后台目标进入 `main.py:17658 _submit_archived_remote_question()`。该函数加载聊天、追加 turn、启动 provider、标记 dirty、延迟保存后直接返回成功，没有 `_play_send_sound()`。
3. 当前聊天进入 `_submit_question()`，其 provider 分支在 17845、17899、17981、18028 等位置包含 `_play_send_sound()`。因此“收到手机问题”与“播放发送声音”在后台路径脱节。

### Codex 回复声音缺失

1. `main.py:12744 _on_codex_event_for_chat()` 区分当前与非当前聊天。
2. 非当前聊天的 `turn_completed` 分支（约 12832）正确标记 done、更新答案、时间与 dirty，但保存后直接 return，没有播放回复声音。
3. 当前聊天完成分支在 `main.py:12939` 调用 `_play_finish_sound()`，且受 `is_current_chat` 限制。

不要把此结论泛化为所有 provider 都漏回复音效：Kimi 非当前聊天完成路径在约 `14999` 已在 `successful` 非空时播放；通用 `_on_done()` 在 `18704` 也会播放。这一差异要求修复时避免 Kimi、通用路径重复播放。

### 最小修复建议

- 后台提交接受成功后补发送声音，放在拒绝/异常回滚之后；空问题、模型拒绝、clear/resend fence 拒绝不得响。
- Codex 后台轮次首次有效完成时补回复声音；以 owner、turn 与完成状态跃迁判重。不能在每个 item_completed、delta 或轮询读取中响，也不能对重复 turn_completed 重复响。
- 保留 `_accept_clear_operation_result()`、generation 与 turn 归属校验。清空后过期事件、错 owner 事件不能触发声音。
- 保留既有 clear/resend 的 durable sound grant；不要把新通用通知接到该流程后再播放第二次。
- 若抽取小的公共“接受/完成副作用”函数，应明确哪些分支已调用，避免把 Kimi 与 `_on_done()` 的现有声音叠加。此次不需要设计新的音频服务。
- 音效调用无需改变活动聊天、焦点或答案页。现有 `_play_send_sound()`/`_play_finish_sound()` 在 21922/21913 使用 winsound，带系统提示音 fallback，可复用。

## 根因链二：桌面聊天列表不随新消息更新

### 刷新遗漏

- `_submit_archived_remote_question()` 已把 `chat['updated_at']` 设置为新问题时间，但没有 `_upsert_history_row()` 或 `_mark_history_list_dirty()`。
- `_remote_api_message_ui()` 成功后推送 `_push_remote_state()` 和 `_push_remote_history_changed()` 给手机；推送不等于桌面列表刷新。
- Codex 后台事件调用 `main.py:8078 _refresh_visible_history_chat()`，该函数要求 `view_mode == 'history'` 且 `view_history_id == chat_id`，否则直接返回。用户正在看 A 时，B 收到消息不会触发 B 的桌面列表重排。

### 排序规则不一致

- `main.py:4798 _refresh_history()` 先把 current_id 放入 ids 第 0 行，再遍历按 pinned/updated_at 排序的 archived_chats。即使补了刷新，最新后台聊天也无法越过旧当前聊天。
- `main.py:4850 _history_chat_sort_key()` 给 current_id 固定最高优先级，同样阻止增量重排置顶。
- `main.py:19921 _get_all_chat_ids_in_order()` 又实现了 current-first，影响键盘相邻聊天导航。只改列表显示而不改此处，会造成视觉顺序与快捷键顺序不同。
- `_remote_api_history_list_ui():12182` 返回含 updated_at 的摘要；本次未检查 RC 实现，手机端自行按时间排序是待核实项，不能声称 MC 返回数组已经按相同规则排序。

### 延迟重排可能永久搁置

- `main.py:4885 _upsert_history_row()` 在导航控件有焦点时不移动行，只设置 `_pending_history_reorder=True`。
- 对 main.py 全文检索，该标记只在初始化与上述赋值出现，没有消费/清除逻辑，也未因此安排 idle flush。
- 通用 `_on_done()` 还传 `allow_reorder=not _primary_navigation_control_has_focus()`；有焦点时连待重排意图都不会进入。
- `_flush_idle_ui_refreshes():5939` 只看 `_history_list_dirty` 等标记，不消费 `_pending_history_reorder`。另外 `_primary_navigation_control_is_recently_active()` 在焦点存在但 last interaction 为 0 时始终 True，若沿用此门槛，启动后未按键也可能一直延迟。

### 最小修复建议

1. 定义统一顺序：保留明确的置顶分组，各组按最新交互时间倒序；当前聊天不再凭“当前”身份永久占第一。无置顶项时，最新收到手机问题的聊天应为第 0 行。有置顶项时，普通聊天到普通分组第一，不自动改变 pinned。
2. 全量列表、增量 `_desired_history_index()`/sort key、`_get_all_chat_ids_in_order()` 使用同一排序规则和稳定 tie-breaker；active/current 身份不随列表移动改变。
3. 接受新问题是一次真实可见变化，应标记历史行重排；有效完成更新 updated_at 时也按既定规则调度一次。持续 token/delta 和无变化轮询不要反复刷新。
4. 使用合并的 idle 刷新或现有增量行移动，记录“当前选中的聊天 ID”，不使用本次后台消息的 chat_id 作为 keep_id；否则会把用户选中项切到后台聊天。
5. 焦点存在不应等同于永远不刷新。只在实际短暂键盘导航窗口延迟，导航停止后有限时间内完成刷新。明确消费并清除重排标记；无剩余工作时不继续轮询。
6. 保持草稿、文本选择/插入点、焦点控件和按 ID 对应的选中聊天；重排后选择索引可以改变。不要恢复旧索引导致选中另一聊天。
7. 通用 provider 与 Codex/Kimi 均复用同一历史变更通知入口，防止每条路径再出现遗漏。此次不需要更换整个列表组件。

## 现有测试需要调整的断言

`tests/test_main_unit.py:19773 test_remote_archived_owner_submission_restores_visible_owner_and_focus` 将 `_refresh_history()` mock 成直接失败，同时验证选中索引不变。这体现旧测试把“不打断前台”扩大为“不允许列表变化”。保留它对活动 owner、草稿、焦点与发送归属的保护；新需求改为允许合并后的合法重排，并按选中 ID 验证稳定。不能直接删除测试绕过回归。

## QA E2E 场景

自动化用临时 SQLite/notes 数据和受控 provider，保留真实 RC 路由、owner 分派、事件处理、列表与保存逻辑。当前生产 MC 正运行，不自动连生产 transport 或发送模型请求。GUI 测试串行，全部定时器与后台线程从构造到销毁管理。

| 场景 | 操作与验收 |
| --- | --- |
| Codex 后台完整链 | A 活动，B 为旧后台聊天；从 RC/NATS 入口给 B 发唯一标记问题。MC 接受后发送声音恰好一次，B 上移；注入有效 ack/final/complete，回复声音一次、答案持久化、RC 最终消息与 MC 相同 |
| 活动聊天对照 | 向 A 发送同类消息，发送与回复仍各一次，避免新统一入口和原分支重复 |
| 双后台交错 | B/C 交错收问题与回复；排序按统一时间规则，声音计数对应接受/完成次数，无串聊，不抢焦点 |
| 重复/过期事件 | 重放 complete、错 owner、旧 generation、清空前旧事件；无重复音效、无错误置顶或旧答案复活 |
| 拒绝和失败 | 空文本、provider 拒绝、clear fence 拒绝不播放发送成功声音。provider 运行失败的提示遵循已确定的错误音效语义，不伪装为成功完成 |
| Kimi 与通用 provider | 同样从后台提交，补齐发送声音；确认原有完成声音不被新增逻辑重复播放 |
| 焦点保持但不按键 | 输入框保持焦点、last interaction 初始为 0，后台消息仍在有限 idle 时间内上移，不永久卡住 |
| 键盘导航期间 | 保持真实上下/Home/End 导航，B 收到消息时不夺取焦点；导航短暂结束后重排，选中聊天 ID 与草稿不变，不要求旧索引不变 |
| 浏览后台聊天 | 活动 A、正在浏览 B；B 更新答案并上移，活动 owner 仍 A；答案与历史列表选中语义稳定 |
| 置顶与快捷键 | 有 pinned P，B 为普通聊天；B 位于普通组首位，P 仍置顶；相邻聊天快捷键顺序和显示顺序一致 |
| 重启与无变化 | 测试保存后重开列表顺序合理；无新消息的定时读取不播放声音、不重绘、不移动选择 |

自动化音效 spy 只能证明调用时机与次数。最终包的实机验收须另做一次可听验证：手机向一个非活动聊天发送无副作用的短问题，确认电脑两种音效与列表上移；记录包版本和同轮 chat/turn 标识。没有实际听音时不能把 spy 测试写成声卡验收通过。

建议用例关键字 `remote_sound_history`，验证命令（待实现新增用例后执行）：

```powershell
Set-Location D:\code\sj\mc
.\.venv\Scripts\python.exe -m pytest tests/test_main_unit.py -k "remote_sound_history or remote_archived_owner_submission or offscreen_remote_submit" -q
.\.venv\Scripts\python.exe -m pytest tests/test_main_remote_nats_unit.py tests/test_remote_session_authority.py tests/test_remote_model_dispatch.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_history_ui_automation.py tests/test_codex_ui_responsiveness_automation.py -q
```

新增涉及 Kimi 的公共完成副作用时，补相关 Kimi 模型流程测试；明确区分既有失败与新回归，不使用全量通过表述掩盖已知基线。候选打包和生产切换应在产品修复、上述验证后安排，遵守现有笔记与数据切包门禁。

## 验收摘要

后台 RC 消息接受与有效回复各播放一次正确桌面音效；最新聊天按统一时间/置顶规则移动，焦点存在不会永久阻止重排；显示与键盘导航顺序一致；活动聊天、用户草稿、按 ID 定义的选中项稳定；重复/错误事件不触发副作用；新行为不依赖用户先点击目标聊天。
