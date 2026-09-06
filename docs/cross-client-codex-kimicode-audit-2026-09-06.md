# Codex 与 KimiCode 跨端聊天审查与修复指南（2026-09-06）

## 用途与状态

本文是后续修复电脑端 `mc` 与手机端 `rc` 聊天问题的工作底稿。它把一次
只读审查的发现、证据、修复边界和验收要求集中到一个位置；即使没有此前的
会话上下文，也应能据此拆分任务、实现和回归。

审查基线：`mc` 的 `fix/mobile-kimicode-routing` 分支，完整提交为
`a71083d08b7b3a673073ac8d3fe5316cd09067d8`；手机端为 `rc` 的 `master`，完整
提交为 `990288b587dff47c7b50e7ff81c1aa565b8f7ed0`。审查时间为
2026-09-06（Asia/Shanghai），环境为 Windows；报告所述桌面定向测试使用
Python 3.11（项目 `.venv` 指向失效的 `C:\Users\Lenovo...` 路径），手机端使用
Flutter stable 3.41.2。审查不是真实手机、弱网 NATS 或真实 Codex/Kimi 凭据的端到端
试验。代码随后的变更
可能已经修复部分发现，因此每个工作包开始前必须先按“证据”复核当前代码；
不要机械地套用本文中的旧行号。

范围包括：

- 桌面端和手机端的已有聊天模型切换、发送、流式回答、中断和命令；
- `rc → NATS/JetStream → mc` 的命令、响应、事件与重试；
- Codex、KimiCode、Claude Code 三类本地 CLI 的模型分派与会话隔离；
- Kimi `kimi web` 的 WebSocket 事件、session/turn/prompt 归属、进程生命周期；
- 手机端乐观 UI、去重、状态刷新与无障碍稳定性。

本文不授权修改模型提供方、NATS 拓扑、持久化 schema 或权限策略。若修复需要
这些不可逆或跨系统变更，应先单独设计并确认协议兼容方案。

## 先读：系统链路与不变量

```text
手机 rc
  └─ RemoteNatsChatService：命令、乐观 UI、事件去重
       └─ JetStream/NATS：至少一次投递、可能重试和重投递
            └─ 桌面 mc remote_nats.py：执行命令、回发 response/event
                 └─ ChatFrame main.py：解析 chat_id + 模型、创建 turn、分派 worker
                      ├─ Codex worker
                      ├─ KimiServerClient → kimi web（REST + WebSocket）
                      └─ Claude Code 交互客户端
```

所有修复必须保持以下不变量：

1. **模型选择是真实请求意图。** 用户显式切换模型后，桌面端必须以该模型创建或
   续接目标聊天的 turn，不能只更新手机显示。
2. **聊天和会话身份不可串用。** 输入、回答、错误和完成事件都必须属于正确的
   `chat_id`；Kimi 事件有 `session_id` 时，还必须以 session 为第一定位条件。
3. **命令至少一次投递不等于至少执行一次。** 同一个客户端命令 ID 只能执行一次；
   重投递只能重放已保存的结果。
4. **“已发送”必须代表桌面端已接收。** 手机端发布到 broker 成功不是模型任务
   已受理；UI 必须等待受理响应或明确显示失败。
5. **后台事件不能破坏前台可访问性。** 不需要的刷新、选中项改变、焦点抢占和
   延时回调堆积都属于回归；Kimi 的 I/O/WS 仍须后台合并、UI 批量交付。

### 变更协议的最低兼容契约

以下约定是 F-01、F-05、F-06 的实现前置条件。字段应以现有远程命令 JSON 的稳定
`command_id`/`chat_id` 外层为准，新增字段不得改变旧字段含义。

| 字段 | 类型与允许值 | 缺失/未知值 | 兼容行为 |
| --- | --- | --- | --- |
| `model` | string，规范化模型 ID | 缺失时沿用现有校验 | 不能单独表示已有聊天改模。 |
| `model_change_mode` | 可选 string：`inherit` 或 `explicit` | 缺失视为 `inherit`；未知值返回 `invalid_request`，不得创建 turn | 新手机主动选模才发送 `explicit`；旧手机缺失字段仍保持旧的继承行为。 |
| `command_id` | 每个安装实例生成的稳定 `client_id` 与 UUID 组成的唯一对 | 缺失视为不兼容请求，拒绝而非猜测 | 重试必须复用同一对；新桌面以此做幂等。 |

桌面 response 至少回显 `command_id`、`chat_id`、`accepted`、规范化后的
`resolved_model`、`model_change_mode` 和失败时稳定的 `error_code`/可读错误。这样
旧客户端可以忽略新增字段，新客户端则能确认桌面实际接受的模型；任何未知模式都应
显式失败，而不是静默降级。

## 建议实施顺序

| 顺序 | 工作包 | 依赖 | 完成定义 |
| --- | --- | --- | --- |
| P0-1 | 显式模型切换与正确模型分派 | 无 | 手机真实传达显式选择；桌面只让匹配模型接收输入。 |
| P0-2 | Kimi 完成/错误事件精确归属 | P0-1 无硬依赖，可并行 | 多 session、同 turn ID 时不会串答、串错或永久 pending。 |
| P0-3 | 命令受理确认和幂等执行 | P0-1 | 手机只在桌面 accepted 后成功；重试不重复执行。 |
| P1-1 | 远程命令、去重和刷新稳定性 | P0-3 | 失败可见可重试，重投递不丢失，页面无陈旧刷新。 |
| P1-2 | Kimi 并发安全与资源清理 | P0-2 | WS 不因竞态停止，失败/启动失败没有 buffer 或子进程遗留。 |
| P1-3 | Claude CLI 实际模型参数 | P0-1 | 显示、持久化和 CLI 实际执行模型一致。 |

每个 P0 工作包应独立提交和测试；不要把协议修复、Kimi 重构和 UI 调整混入同一
变更。P0-1/P0-2/P0-3 均是当前合入阻断项。

## P0-1：显式模型切换与错误输入截获

### F-01：手机端切换已有聊天的模型没有真正生效

- **触发：** 在手机已有聊天中将 Codex 切为 KimiCode（或反向），随后发送消息。
- **现象与影响：** 手机显示已切换，桌面仍按旧模型处理；后续状态同步会固化
  错误显示。用户会以为正在使用另一模型，实际得到旧会话的回答和计费。
- **证据：** `rc/lib/main.dart:5269-5286` 仅更新本地模型；
  `rc/lib/remote_nats_chat_service.dart:554-563` 只发送 `model`；
  `mc/main.py:9062-9078` 对已有聊天默认采用 `inherit`，仅在
  `model_change_mode == "explicit"` 时接纳新模型。`mc/tests/test_main_unit.py`
  的 `983-1065` 区间分别覆盖了默认继承和 explicit 切换的契约。
- **修复方向：** 在手机远程命令协议中表达“用户主动选模”这一意图。仅在主动
  选择模型后发送 `model_change_mode: "explicit"`；普通发送保持继承语义，避免
  陈旧客户端意外覆盖桌面模型。桌面端继续以该字段作为兼容边界，并按上表返回
  `resolved_model` 供手机纠正乐观显示。
- **验收：**
  - Given 已有 Codex 聊天，When 手机主动改选 Kimi 并发送，Then 桌面聊天模型、
    新建 turn 和 worker 都是 `kimi/*`。
  - Given 手机未主动改选模型，When 发送旧客户端或重试命令，Then 桌面仍继承
    已有模型。
  - Given 手机到桌面的完整 payload，When 检查协议，Then 只有主动改选场景含
    `model_change_mode: explicit`。

### F-02：活动 Claude 客户端可截获应发给 Codex/KimiCode 的输入

- **触发：** 同一聊天还保留 Claude Code 交互客户端时，用户显式选 Codex 或
  KimiCode 后发送；历史聊天切换期间还可能使聊天 A 的客户端接收聊天 B 的输入。
- **现象与影响：** 输入写到旧 Claude stdin，目标模型 turn 不创建；附件因纯文本
  stdin 快捷路径被忽略。模型切换在界面上成功而行为上失效。
- **证据：** `mc/main.py:13186-13195` 在计算 `resolved_model` 与目标聊天之前调用
  `_send_active_claudecode_input` 并提前返回；目标解析在 `13196-13200` 之后。
- **修复方向：** 先解析真实目标 `chat_id` 和规范化模型，再决定是否可续写 Claude。
  只有“目标模型仍为 Claude、客户端归属同一 chat、客户端确实等待输入、且没有附件
  或另有附件处理路径”时，才能走 stdin 快捷路径。其余情况必须创建正常 turn 并按
  `codex/*`、`kimi/*`、`claudecode/*` 分派专用 worker。
- **验收：**
  - Given 聊天 A 存在活动 Claude 客户端，When 向 A 显式切换 Kimi/Codex 后发送，Then
    Claude stdin 不收到输入，目标专用 worker 启动。
  - Given A 存在活动 Claude 客户端，When 从远程入口向聊天 B 发送，Then A 永不收到
    B 的输入，B 的模型分派正确。
  - Given 同聊天、同 Claude、等待输入，When 发送纯文本，Then 仍复用既有客户端，
    不额外创建 worker。

## P0-2：Kimi 完成和错误事件的精确归属

### F-03：`prompt.completed` 可同时结束同一 Kimi session 的多个 turn

- **触发：** 一个 Kimi session 中有多个本地 turn，收到真实的 `prompt.completed`。
  该事件含 `session_id`、`promptId`，但没有 `turnId`。
- **影响：** 同 session 的 answer buffer 被拼接，多个本地 turn 被标为完成或错误；
  这会制造错答、重复答或把仍运行的提问伪装为完成。
- **证据：** 真实 fixture 在 `mc/tests/fixtures/kimi_server_events.jsonl:28`；
  `mc/kimi_server_client.py:330-337` 映射后仍不含 turn ID；
  `mc/main.py:10501-10522` 按 session 弹出 buffer，`10597-10684` 遍历并结束该
  session 的多个 turn。
- **修复方向：** 本地提交 prompt 时保存 `prompt_id → (chat_id, session_id, turn)`
  关系；完成事件优先使用 `prompt_id` 精确结算。该映射必须在桌面将“已受理”响应
  发出前，和现有聊天/turn 持久状态一起落盘；重启或 WS 重连后从该状态恢复。终态
  （完成、失败、中断）确认后才可清除映射和 buffer，并保留有限的终态记录以吸收
  重复事件。若事件先于映射可见，放入按 `session_id + prompt_id` 键控的有界短期
  暂存队列并触发一次状态/转录核对；超过上限或时限只能保留 pending 并记录诊断，
  不得猜测归属。只有 session 范围内唯一候选时才允许 session-only 回退，绝不能
  批量清空或批量完成。

### F-04：Kimi error 未携带 session 身份，会失败到旧 turn

- **触发：** 同一聊天前后创建两个 Kimi session，两个 session 都使用 `turnId=0`，
  新 session 出错。
- **影响：** 旧 turn 被标记失败，真实失败的 turn 永久 pending，错误原因也显示在
  错误聊天中。
- **证据：** `mc/main.py:11003-11004` 调错误处理时未传 session；
  `10689-10705` 仅按 `kimi_turn_id` 取首个匹配项。
- **修复方向：** 错误、完成、delta、清理全部使用同一完整 identity：至少
  `(chat_id, session_id, turn_id 或 prompt_id)`。候选不唯一时保留事件并记录诊断，
  但不得修改任何 turn。
- **共同验收：**
  - Given 两个 Kimi session 都出现 turn `0`，When delta/completed/error 交错到达，Then
    每个事件只影响自己的聊天和本地 turn。
  - Given `prompt.completed` 缺 `turnId` 但含 `promptId`，When 处理，Then 仅对应 prompt
    的 turn 完成且其 buffer 被清理。
  - Given 身份不完整且候选不唯一，When 处理，Then 没有 turn 被错误完成或失败。

## P0-3：远程消息受理确认与幂等执行

### F-05：NATS 重试/重投递会重复执行同一命令

- **触发：** 命令已到桌面但手机收到模糊失败后重试，或桌面任务已执行但 response
  发布/ack 失败，引发 JetStream 重投递。
- **影响：** 同一问题执行两次，产生重复 turn、重复回答和可能的重复计费。
- **证据：** 手机在 `rc/lib/remote_nats_chat_service.dart:874-903` 以相同
  payload/id 重试；桌面 `mc/remote_nats.py:165-195` 每次投递均执行 callback，
  且在 response 发布后才 ack；`rc/lib/remote_nats_client.dart:395-396` 未提供
  服务端去重头。
- **修复方向：** 桌面按稳定 command ID 建立有界、可恢复的幂等记录。首次执行前
  原子地取得或创建以 `(client_id, command_id)` 为键的记录；相同键的并发投递只有
  一个请求能从 `received` 变为 `claimed`。状态机为
  `received → claimed → accepted → terminal_response`，提交前错误转为
  `failed_response`；后两种状态均保存完整 response 并可重放。response 在 ack 前
  持久化；重复投递在 `claimed/accepted` 时不再创建任务，在终态时只重放保存结果。
  记录应有配置化 TTL、容量上限和按终态/过期清理策略；过期后才允许同一客户端 ID
  的新 command ID 继续写入，不能复用旧 ID。

### F-06：手机将 broker 发布成功误报为“已发送”

- **触发：** 桌面模型无效、拒绝命令、UI 调度超时或提交失败。
- **影响：** 手机新增乐观消息并朗读“已发送”，但模型从未处理；用户既不知道失败，
  也无法就地重试。
- **证据：** `sendUserMessage` 在
  `rc/lib/remote_nats_chat_service.dart:554-563,857-872` fire-and-forget 调用
  `_sendCommand`；桌面会在 `mc/remote_nats.py:182-195` 返回 400/500；手机在
  `rc/lib/main.dart:5450-5500` 发布后直接标成功。
- **修复方向：** 把提交改为有超时的 request/response。仅在收到匹配 command ID 且
  `accepted: true` 的桌面响应后将乐观消息置为已发送；超时、拒绝或错误则显示失败，
  保留原文本并提供重试。手机创建请求 inbox/reply subject 并在发送后持有订阅直到
  接到关联 response 或本地超时；response 以 `(client_id, command_id)` 关联，重复或
  乱序 response 只接受首个合法终态。页面导航只解除 UI 监听、保留命令状态以便回到
  聊天后恢复显示；它不是取消模型任务，取消必须发送明确命令。重试复用原 command ID，
  以配合 F-05。
- **共同验收：**
  - Given 同 command ID 重投递，When 桌面已执行过，Then 只返回第一次保存的结果，
    模型任务数量为 1。
  - Given 桌面拒绝/超时，When 手机等待 response，Then 消息显示失败而非成功，且可
    重试。
  - Given accepted response 丢失，When 手机以同 ID 重试，Then 仍只产生一个桌面 turn。

## P1：可靠性、资源与 UI 稳定性

### F-07：远程 `/stop` 等 Codex/Kimi 命令永久 pending

`mc/main.py:13228-13324` 会创建命令 turn，但 worker 仅在 `source == "local"` 时
启动；远程请求仍返回 accepted。修复应二选一：完整支持远程命令执行和最终状态，或
在创建 turn 前明确拒绝并让手机显示原因。验收为任一远程命令都在有限时间内完成、
失败或被拒绝，绝不永久 pending。

### F-08：Kimi 活动路由表跨线程无同步

worker、WS 回调和 UI 线程同时访问 `_kimi_active_turns`（`mc/main.py:8011-8041`、
`10381-10399`、`10543-10572`）。字典迭代时写入可使 WS 事件线程异常退出，且回调
链没有恢复路径。应以专用锁保护读写与快照，或统一封送到单一线程；锁内不得执行
网络/GUI I/O。验收：并发创建、完成、错误和断线事件压力下无 `RuntimeError`、WS
线程存活，且路由正确。

### F-09：手机事件在成功处理前即去重，失败重投递会丢失

`rc/lib/remote_event_deduper.dart:10-23` 先写入 seen，
`rc/lib/remote_nats_chat_service.dart:907-924` 处理异常时不 ack；重投递却命中 seen，
被跳过后 ack。应改为“预占—成功提交”或异常释放预占；验收为首次处理抛错后重投递
仍会执行，成功事件只执行一次。

### F-10：每条发送建立两组不可取消延时刷新

`rc/lib/main.dart:5499,5721-5737` 为每条消息安排 350ms 和 1200ms 刷新；快速发送或
response 乱序时，旧刷新可覆盖新状态，页面销毁后 timer 仍在运行。应按聊天维护单一
可取消、带 generation 的 debounce/coalescing 刷新；验收为快速连续发送后最终状态不
回退，离开页面后无未处理 timer 或 `setState` 异常。

### F-11：Kimi 启动 WebSocket 失败会遗留 `kimi web` 进程

`mc/kimi_server_client.py:476-511` 启动进程后连接 WS；`788-834` 连接异常没有统一
走 `_abort_start` 清理，下次启动覆盖 process 引用。应把启动包装为事务：任意 health、
token、REST 或 WS 阶段失败都关闭资源并 terminate/kill 子进程。验收为模拟每个阶段
失败后没有存活子进程、端口和引用均被释放。

### F-12：Kimi 终端错误不清理 answer buffer

delta 在 `mc/main.py:10485-10499` 累积，只有正常完成的 `10501-10522` 弹出；
`10689-10744` 错误路径未清理。应在完成、失败、中断和 transport exit 按完整 identity
清理，并设容量/生命周期上限。验收为反复失败后 buffer 数量和内存可回收，复用身份
不会得到上一次回答文本。

### F-13：Kimi 执行增量 buffer 缺少 session ID

`mc/main.py:6353-6375` 的键为 `(chat_id, turn_id, item_id)`；不同 session 复用 turn ID
时可能混入执行输出。键必须纳入 `session_id`（或唯一 prompt identity），并对旧状态
迁移/缺失 identity 采用仅唯一候选的安全回退。验收为两个 session 的相同 turn/item ID
交错输出时，执行列表互不混杂。

### F-14：动态 Claude 模型没有实际传给 CLI

`mc/main.py:8300-8305` 接收 `resolved_model`，但
`mc/claudecode_client.py:263-267` 的命令未带 `--model`。结果是手机/桌面显示
`claudecode/opus`，实际执行仍为 CLI 默认模型。应将规范化后的允许模型映射为 CLI
参数，并对不支持的模型明确拒绝；验收为命令构造、状态展示与实际 CLI 选择一致。

## 测试和交付清单

每个工作包至少补充一条“真实跨端 payload/事件”的回归，不要只断言内部 helper。
建议新增或扩展下列覆盖：

- `mc/tests/test_remote_model_dispatch.py`：手机 `explicit` 选模、Claude 不截获
  Codex/Kimi、跨 chat 输入隔离；
- `mc/tests/test_main_unit.py`：已有聊天 inherit/explicit 协议契约、远程命令状态；
- `mc/tests/test_kimi_integration.py` 和 Kimi event mapping tests：prompt ID 完成、
  多 session 同 turn ID、错误身份、buffer 清理；
- `mc/tests/test_kimi_server_client_unit.py`：启动每阶段失败的进程清理、并发路由表；
- `rc` 的 `remote_nats_chat_service`、协议、event deduper 和 session store tests：
  accepted response、拒绝/超时、同 command ID 重试、失败后重投递；
- `rc/test/remote_session_send_race_test.dart`：可取消刷新和页面销毁；此前该套件有
  3 个未完成 timer 失败，修复 F-10 后应转为通过。

以下矩阵明确每项发现的测试归属。测试名称是待实现的行为名称；实现时应在指定文件中
使用同名或等价的明确测试名，并在 PR 描述中引用本表。`真实手机冒烟` 是手工步骤，
不能被 mock 单测替代。

| 发现 | 自动化归属 | 必备 fixture/场景 | 额外手工验证 |
| --- | --- | --- | --- |
| F-01 | `rc/test/remote_nats_chat_service_test.dart`、`mc/tests/test_remote_model_dispatch.py` | explicit/inherit、旧客户端缺字段、response 回显 resolved model | 手机已有聊天切 Codex↔Kimi 后各发一条。 |
| F-02 | `mc/tests/test_remote_model_dispatch.py` | 同 chat 显式改模、跨 chat、纯文本 Claude 续写、附件 | Claude 活动时在手机切 Kimi/Codex 并带附件。 |
| F-03/F-04 | `mc/tests/test_kimi_integration.py`、`test_kimi_event_mapping_unit.py` | 相同 turn ID、`promptId` 完成、先事件后映射、错误事件 | 两个 Kimi 聊天并发至少两轮。 |
| F-05/F-06 | `rc/test/remote_nats_chat_service_test.dart`、`remote_nats_client_test.dart`、`mc/tests/test_remote_model_dispatch.py` | 同 `(client_id, command_id)` 并发/重投递、accepted/拒绝/超时/乱序 response | 暂停 response 或网络后点重试，确认仅一个桌面 turn。 |
| F-07 | `mc/tests/test_remote_model_dispatch.py`、`rc/test/remote_nats_chat_service_test.dart` | 远程 `/stop` 的 accepted、终态、拒绝和超时 | 在真实运行中的 Codex/Kimi turn 执行 stop。 |
| F-08 | `mc/tests/test_kimi_integration.py` | 多线程创建/完成/错误/断线压力与 WS 存活 | 长时间多聊天连续提交。 |
| F-09 | `rc/test/remote_nats_chat_service_test.dart` | 第一次事件处理抛错、同 event ID 重投递、成功后重复 | 弱网或模拟处理崩溃后确认最终事件可见。 |
| F-10 | `rc/test/remote_session_send_race_test.dart` | 快速连续发送、乱序 response、dispose 后 timer | TalkBack/读屏下快速发送并离开聊天。 |
| F-11 | `mc/tests/test_kimi_server_client_unit.py` | health、token、REST、WS 每一启动阶段失败 | 检查失败后端口和 `kimi web` 进程不存在。 |
| F-12/F-13 | `mc/tests/test_kimi_integration.py`、`test_kimi_event_mapping_unit.py` | failed/interrupted/transport exit 清理、跨 session 相同 turn/item | 并发后检查执行记录和答案不串。 |
| F-14 | `mc/tests/test_main_unit.py`、Claude client unit tests | 允许模型生成 `--model`、未知模型拒绝 | 实际 CLI status/日志与 UI 模型一致。 |

最小验证集（按实际环境调整解释器路径）：

```powershell
# mc
python -m pytest tests/test_remote_model_dispatch.py tests/test_kimi_integration.py -q
python -m pytest tests/test_kimi_server_client_unit.py tests/test_kimi_event_mapping_unit.py -q
python -m pytest tests/test_kimi_ui_responsiveness_automation.py -q
python -m py_compile main.py

# rc
flutter test test/remote_session_send_race_test.dart --no-pub
flutter test test --no-pub
```

在具备已登录的独立测试环境后，再进行：真实 Android 手机发送/切换/中断、NATS 弱网或
response 丢失后的重试、真实 Codex 与 KimiCode 的多会话并发。真实凭据、生产 endpoint
和用户历史数据不得用于自动化回归。

## 审查时的验证结果与残余风险

审查记录中，`mc` 的“定向 Codex/Kimi/远程路由套件”为 **292 passed**，`rc` 的“服务、
协议、状态存储和 authority 套件”为 **69 passed**，且执行过
`git diff --check main...HEAD` 并通过。审查记录没有保留前两组聚合统计对应的完整命令
清单、各依赖版本和失败输出，因此它们只作历史线索，**不是可复现的发布门禁**；后续
PR 必须把实际命令、解释器/Flutter 版本、提交 SHA、stdout/stderr 摘要记录在交付说明中。
已知的独立失败是 `rc/test/remote_session_send_race_test.dart`：**3 个失败**，均指向
未完成延时 timer。

因此，“现有定向单测通过”不能作为允许合入的理由。只有 P0 三个工作包的验收场景、
目标回归和至少一次真实跨端冒烟均通过后，才应评估合入；其余 P1 项应继续排期，
尤其是 Kimi 事件并发、资源清理和移动端重投递。
