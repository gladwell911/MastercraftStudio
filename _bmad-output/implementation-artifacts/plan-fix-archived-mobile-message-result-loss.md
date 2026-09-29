# 修复计划：手机发送到后台聊天后结果丢失

日期：2026-09-29。状态：待实施。本文件记录只读调查结论及执行计划；尚未修改产品代码、恢复生产记录或重新打包。

## 目标与范围

手机向任意既有聊天发送消息后，电脑能够按该聊天的 owner、turn、thread 和 generation 接收模型回执，保存最终回答，并向手机同步。后台聊天处理期间保持桌面的活动聊天、输入草稿、焦点和选中项稳定。

最小修复聚焦历史聊天的延迟加载，保留现有身份校验和 provider 分派。先用生产同类场景证明缺陷，再实施修复。历史 pending 记录恢复与新消息修复分别验收，不把旧数据直接标为完成。

## 已核实的证据

打包程序：`D:\code\cx\mc`。该包实际聊天数据位于同级 `D:\code\cx\history`，不是 exe 目录内部。

| 聊天 | 手机消息时间（本机 UTC+8） | 数据库观察 |
| --- | --- | --- |
| 指挥塔 | 2026-09-29 09:04:41 | `question_origin=rc`，`request_status=pending`，恢复标识仍为旧 thread/turn |
| 听游安卓 | 2026-09-29 09:05:27 | `request_status=done`，成功绑定新 thread/turn |
| sj | 2026-09-29 09:05:37 | `question_origin=rc`，`request_status=pending`，恢复标识仍为旧 thread/turn |
| 任务 | 2026-09-29 09:06:02 | `question_origin=rc`，`request_status=pending`，恢复标识仍为旧 thread/turn |
| gb | 2026-09-29 09:06:19 | `question_origin=rc`，`request_status=pending`，恢复标识仍为旧 thread/turn |

证据位置：

- `D:\code\cx\history\app_state.json`：活动聊天为“听游安卓”，ID `c0c40003-0e6b-4227-927f-f65e5c48c26f`。
- `D:\code\cx\history\chat_history.db`：上述手机问题已进入 `turns`；其他聊天没有写入本次新 `codex_thread_id/codex_turn_id`。
- `D:\code\cx\mc\_internal\.codex-home\sessions\2026\09\29`：指挥塔、sj、任务的对应新 rollout 已出现 `task_complete` 与最终回答；“听游安卓”同样完成。gb 在调查读取的日志中仅确认 task_started，不能宣称它已完成。

因此已排除“所有失败聊天的消息都未到电脑”这一解释。至少三个后台聊天已完成真实模型执行，故障落在模型结果进入应用状态、保存与同步的链路。

### 已复现与尚待验证的边界

已从当前 `main.py` 的 AST 提取真实 `_hydrate_chat_from_store()`，用纯内存 fake store 复现：已有 `turns` 与新 `codex_thread_id`，但没有 `execution_steps` 的历史对象，默认补载后被数据库中的旧对象替换，新 thread/turn 丢失，`archived_chats[0] is original` 为 False。未触碰生产数据库。

生产未发现覆盖完整 worker IPC 的原始日志。下述精确事件顺序属于高置信度根因假设，必须通过包含真实 ChatStore 与事件处理函数的回归用例锁定；不能把 provider 的 task_complete 当成应用已接收完成事件。

## 缺陷时序与代码位置

以下行号基于调查时版本，实施时按函数名定位。

1. `main.py:11595 _remote_api_message_ui()` 将非活动聊天分派到 `_submit_archived_remote_question()`。
2. `main.py:17648 _submit_archived_remote_question()` 使用 `load_chat(..., include_execution_steps=False)`，历史对象包含 turns，但通常没有 execution_steps；随后追加 pending turn、启动 worker，并延迟保存。
3. `main.py:15293 _apply_codex_worker_thread_state()` 接收新线程回执，更新目标 chat/turn 的 thread、turn、generation，延迟保存。
4. 执行事件经 `main.py:6634 _chat_state_for_execution_steps()`，调用 `_hydrate_chat_from_store()`，默认要求加载执行步骤。
5. `main.py:3984 _hydrate_chat_from_store()` 因 execution_steps 缺失，调用 `store.load_chat()` 全量读取，并把 `archived_chats[idx]` 替换为新加载对象；数据库可能仍是上一轮身份或尚未更新的 pending turn。
6. `main.py:12734 _on_codex_event_for_chat()` 调用 `_codex_event_turn_is_compatible_with_chat()`；状态被退回旧身份后，本次新 turn 的回答/完成事件被拒收。即使局部变量仍持有原对象，后续按 ID 查询也会找到替换后的对象。
7. 活动聊天走 `_current_chat_state`，不经过上述历史对象补载替换，因此“听游安卓”能完成。

事件也可能先于 ack 到达，或补载发生于其他执行事件入口。测试须涵盖允许的顺序，并定位状态首次丢失处，不依赖单一时间延迟。

## 最小修复设计

### 实施文件与职责

| 文件 | 改动 |
| --- | --- |
| `main.py` | 修正 `_hydrate_chat_from_store()` 的分段加载与对象身份保留；检查 `_chat_state_for_execution_steps()` 所需字段 |
| `tests/test_main_unit.py` | 增加真实 ChatStore 的补载、worker 回执及最终结果回归；保留既有归属守卫测试 |
| `tests/test_codex_ui_responsiveness_automation.py` | 增加多聊天后台接收时焦点、草稿、选中项与键盘导航稳定性验证 |
| 必要时 `tests/test_main_remote_nats_unit.py` | 验证正确 owner 的最终状态/事件同步；优先复用现有 transport fake |

不预设需要修改 `chat_store.py`、worker 协议或手机端。只有完整回归给出额外证据后才扩大范围。

### 加载规则

1. 已有 turns，且无需执行步骤或已有执行步骤：直接返回原对象，维持现有快速路径。
2. 已有 turns，仅缺 execution_steps：只通过现有 store 执行步骤读取接口补充缺失数据，原位更新原对象；禁止重新载入并覆盖 turns、thread、turn、generation、请求状态、队列或用户修改。
3. 只有摘要、尚无 turns：允许从 store 加载缺失内容，但应原位补齐，保持被其他路径持有的聊天对象身份。明确摘要字段与数据库完整字段的合并规则；不得把摘要默认值覆盖数据库已有 provider 身份。
4. 已存在内存 execution_steps（包括空列表）时不覆盖。若步骤持久化/缓存带有专用状态标记，先核对其语义，不将“空列表”擅自定义为“未加载”。
5. 不用强制同步保存整个聊天来规避竞态，不放宽旧 turn、错误 owner 或过期 generation 的拒收规则。
6. 复查延迟加载调用点，保证返回值与 `archived_chats` 引用一致。执行步骤的增量保存和 canonical 投影继续沿既有通道运行。

### 实施顺序

1. 阅读当前工作区差异与 `AGENTS.md`，保护其他人的修改。
2. 添加确定性失败用例：数据库保留旧身份，内存已接收新回执且未 flush，然后触发执行步骤补载。先证明旧代码失败。
3. 实施上述最小加载修复，使对象身份、turn 列表和新线程信息保留。
4. 接入真实事件处理完成链，验证最终状态持久化、重载与远程投影。
5. 执行跨聊天、负向归属和 GUI 稳定性测试，审查 diff 后再生成候选包。

## 回归用例

所有自动化用临时 ChatStore/notes 路径，不读取或写入用户生产聊天与笔记。mock provider 外部调用可以接受，但必须保留真实 hydration、ack、事件处理、dirty 标记和持久化方法。

### 必需数据与生命周期测试

1. **已有 turns 的补载**：原对象缺 execution_steps；数据库存旧 thread/turn，内存存新身份、新 generation、pending turn。补载后 chat 与 turns 的对象身份不变，内存所有运行字段不回退，执行步骤完整。
2. **摘要初次加载**：没有 turns 的摘要能够补齐历史，原引用仍有效；缺失聊天或关闭 store 时行为保持兼容。
3. **确定性竞态**：冻结延迟保存；后台提交→新线程 ack→执行事件补载→最终回答/完成→flush。要求新消息从 pending 变 done，数据库重载后的回答与 identity 正确。
4. **保存时序变化**：参数化 ack 前后发生保存、补载前已保存/未保存、重复补载、重复 ack。不得重复追加问题或污染其他 turn。
5. **多聊天交错**：A 为活动聊天，B/C 为后台聊天；B/C 各收到唯一标记问题。交错发送 ack、delta、final、complete；B/C 各自完成并落盘，A 及旧 turns 不变。
6. **拒收仍有效**：错误 chat_id、旧 turn_id、过期 generation 和已清空上下文的旧事件仍被拒绝，不能为解决丢消息而接受所有事件。
7. **远程同步**：B/C 的 final/state/history 归属正确；占位文本不作为 final，重复完成事件不产生重复最终通知。区分 v1 状态与 v2 durable outbox，按现有能力覆盖。
8. **持久化重启读取**：关闭测试 store/frame 后重建读取，B/C 的最终回答仍在，不只验证内存字段。

### GUI 与可访问性测试

- A 输入框放置未发送草稿，记录焦点、文本选择区、插入点、历史列表当前选中 ID、回答列表选中项和活动聊天 ID。
- 运行 B/C 后台消息完整生命周期。没有可见内容变化时，不强制重绘当前列表、改变选中或转移焦点；历史排序变化必须保持按聊天 ID 对应的选中语义。
- 另测当前浏览 B 历史、活动聊天仍是 A：B 新结果可见更新，但不将 B 强制变成活动聊天，不夺取当前控件焦点，草稿不变。
- 在事件流期间用真实键盘上下/Home/End 导航，并验证导航目标、焦点与选中持续一致。
- wx GUI 套件串行运行；frame 构造到销毁期间管理全部真实定时器与后台保存线程。

建议新增用例使用共同关键字 `archived_mobile_result`，便于定向执行和记录。

## 验证命令与证据记录

在 PowerShell 中从项目目录执行，测试按下面顺序串行运行。新增关键字用例必须检查 collected 数量，禁止把全部 deselected 当通过。

```powershell
Set-Location D:\code\sj\mc
.\.venv\Scripts\python.exe -m pytest tests/test_main_unit.py -k archived_mobile_result -q
.\.venv\Scripts\python.exe -m pytest tests/test_main_unit.py -k "offscreen_remote_submit or remote_archived_owner_submission or hydrate_chat_from_store" -q
.\.venv\Scripts\python.exe -m pytest tests/test_remote_session_authority.py tests/test_remote_model_dispatch.py tests/test_main_remote_nats_unit.py tests/test_codex_worker_protocol.py tests/test_codex_worker_client.py tests/test_codex_worker_process.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_codex_ui_responsiveness_automation.py tests/test_history_ui_automation.py -q
```

以新增生命周期用例为修复证明；已有模型/归属与 GUI 套件为回归边界。若触及更多 provider 公共路径，补对应 provider 定向用例。失败按精确用例与已知基线区分，记录命令、提交/工作区版本、测试数量、失败详情。完成必要验证后不无目的扩大测试。

候选包使用独立的、尚未存在的输出目录，先核实 `package_mc.ps1` 当前实现与路径，避免其清理正式目录。示例候选构建命令：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\package_mc.ps1 -DistPath D:\code\sj\mc\dist_archived_message_fix -WorkPath build_archived_message_fix
```

该脚本当前会拒绝 mc.exe 运行期间打包。执行前安排正常退出，不强杀用户进程。若示例目录已存在，使用新的专用目录并确认最终绝对路径。

## 数据恢复与部署风险

1. **旧 pending 记录**：修复只能保证新事件不再丢失，不能自动找回已丢弃的完成事件。保存 SQLite 一致性备份、app_state 与关联 rollout 后，逐个核对 chat、原问题、时间、旧/新 thread、turn、最终回答。不能只按标题或时间直接覆盖记录。
2. **重复执行**：旧 pending 可能触发恢复/重发。部署前确认已有恢复逻辑对这些记录的行为；不得为验收主动重发具有外部副作用的旧问题。恢复方案需单独验证幂等性与 canonical 通知，不能手工只改 `request_status`。
3. **安全备份**：运行中的 SQLite 用 backup API 生成一致性副本，显式关闭连接并校验；避免普通文件复制遗漏 WAL。恢复或生产写入前正常退出 MC。
4. **包与数据分离**：替换程序不覆盖 `D:\code\cx\history`。保留原程序包供回滚；备份关系清楚，禁止用陈旧数据库整体覆盖当前记录。
5. **OneDrive 笔记**：遵守项目现有切包门禁，确认个人 OneDrive 笔记库完整且包含切包前最新修改；一次只运行一台 MC，换机前退出并等待同步。
6. **启动环境**：候选包使用隔离验证数据；测试配置不得连接生产 transport 或自动恢复生产任务。正式启动后先用无副作用的唯一标记问题做手机验收。
7. **性能**：仅补执行步骤仍可能读取大历史。保持原有缓存与增量保存，不在此次修复引入全量反复读取。若验证暴露 UI 阻塞，作为具体失败修复并重新验证。

## 验收标准

- 新增补载竞态用例在修复前失败、修复后通过；能证明新身份保留与最终回答落盘。
- 活动聊天 A 与至少两个后台聊天 B/C 均可通过手机发送唯一标记消息，并分别取得正确最终回答；后台聊天无需先在电脑激活。
- 后台模型已完成的测试轮不会永久 pending，重开测试数据后最终结果仍存在；每条消息 owner/turn 无串聊、无重复。
- 手机收到对应聊天最终结果；如果声称真机验收，记录实际手机、候选包版本及同一轮关联标识，不用模拟器或 provider 日志替代。
- 草稿、焦点、插入点、文本选择与列表选中稳定，后台事件不抢占可访问性导航；当前浏览后台聊天时能正常看到结果。
- 错误 owner、旧 turn 和旧 generation 仍被拒收，清空上下文隔离不退化。
- 自动化结果、候选包检查及真实跨端验证分别记录。若未执行实体手机验证，明确保留该验收项，不宣称部署完全验证。
- 历史丢失结果的恢复数量与剩余 pending 单独列出；新代码验收通过不代表旧数据已经修复。
