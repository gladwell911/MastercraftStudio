# 可复用经验

## Kimi server 的文本只走 REST，事件流没有模型内容

- 经验：Kimi server 的 WebSocket 事件流只推送工具调用、步骤编号和会话管理事件，从不推送 `thinking.delta`/`assistant.delta`；模型的思考块和最终答案只能从 `GET /api/v1/sessions/{id}/messages` 拿（assistant 消息的 `content` 里 `type: "thinking"`/`"text"` 块）。该接口只返回最近 50 条，`limit`/`max_results` 参数被忽略，`page_size` 会报错，`before_id=<msg id>` 游标可链式向前翻页（每页 50 条，已对照运行中的服务端实测）。
- 为什么重要：F1 思考行、最终答案恢复、任何"模型说了什么"的功能都必须走这个接口；指望事件流会永远拿不到文本。
- 下次怎么用：展示思考/取答案一律走 REST `/messages`；需要窗口外旧记录时用 `before_id` 链式翻页并设页数上限；事件流只当"活动脉冲"（触发拉取）用。

## 恢复 worker 的生命周期边界

- 经验：`_reconcile_kimi_session_worker` 由提交、传输错误和 `prompt.completed` 触发；处理完一轮后若 `requested_generation <= handled_generation` 就直接退出，健康回合中途不运行。答案恢复靠回合结束时的 `prompt.completed` 触发，而不是持续轮询。
- 为什么重要：把"回合进行中要做的事"（如中途同步）挂在这个 worker 上会静默退化成回合结束才执行。
- 下次怎么用：中途增量功能挂在事件分发路径（`_on_kimi_event_for_chat`）上自带节流触发；恢复 worker 只做回合结束兜底。

## 最终通知以持久化 done turn 为唯一事实来源

- 经验：provider callback、UI 占位文本和数据库持久化的到达顺序不稳定；在 callback 中直接发布 final 会产生占位终态或漏发真实终态。
- 为什么重要：canonical message ID 一旦被错误 final 占用，后续真实答案会因幂等冲突消失；简单 return 又会导致没有 final。
- 下次怎么用：先持久化 canonical turn，再从 `request_status=done` 的 `answer_md` 幂等提交 `assistant_final`；测试必须覆盖 pending 不发布、done 发布一次和重复保存。

## 隔离 strict-V2 跨端夹具

- 经验：本地跨端回归也必须从隔离 `ChatStore` 的 canonical message 提交 durable fact，再调用生产 outbox drain；不能直接发布 fake/legacy `final_answer` 证明 V2。
- 为什么重要：这样才能同时覆盖 hello 协商、历史/模型命令、canonical 引用和手机端真实投影，避免“能收到假消息”被误报为协议可用。
- 下次怎么用：每轮生成唯一聊天、prompt 和回答 marker，使用 `10.0.2.2` 连接模拟器；固定端口均占用时选择临时 loopback 端口，不要停止正常桌面服务。

## Canonical 通知事实与 connected E2E

- 经验：通知正文必须由同一事务内的 canonical chat/message 行派生并提交，native 消费端再重算 canonical hash；调用方不能直接提供展示标题或正文。
- 为什么重要：clear、replacement、重放和恶意/损坏 payload 都可能让“非空字段检查”产生伪通知或过期通知。
- 下次怎么用：共享 fixture 同时覆盖合法 hash、篡改 owner/title/text/origin、缺 message id 和并发 clear；connected E2E 从生产 `ChatStore` 事实进入 outbox，不从测试端直接伪造正向 durable event。

- 经验：当前内部测试项目允许 connected E2E 内置测试专用 endpoint、token 和原有 `default` 配对码，同时保留环境变量覆盖能力。
- 为什么重要：开箱即用降低人工配置成本，并与 Android 原生后台服务当前固定的 `default` 配对域保持一致；日志仍不得打印令牌。
- 下次怎么用：Story 4 connected E2E 默认使用内置测试配置；切换环境时再设置 `NATS_E2E_ENDPOINT`、`NATS_E2E_TOKEN`、`NATS_E2E_PAIR_ID`，日志只记录非敏感身份与结果。

## 稳定身份驱动的无障碍焦点

- 焦点恢复应保存 owner、视图和稳定内容身份，而不是控件实例或行号；刷新后优先匹配原身份，删除时再选择同视图的确定相邻项。
- 焦点租约只能由显式用户导航建立，并在模态框、应用失活、离开视图或关闭时释放，防止延迟 UI 回调抢焦点。
- 时间分组必须基于权威执行顺序和明确边界；本阶段跨端 fixture 验证了 `>=300s`，避免 Python 与 Dart 的边界漂移。

## 权威执行时间线与分页恢复

- 经验：远程执行投影与 durable fact、revision 和每聊天执行序号必须在同一事务中提交；canonical 问题/最终回答只保存稳定消息引用，不复制正文。
- 为什么重要：这同时避免重复投影、半提交状态、跨 owner 混入及消息正文分叉。
- 下次怎么用：以全局 event ID 去重、以权威 execution sequence 排序；known kind 严格校验，unknown kind 保留可读摘要和详情。
- 经验：tail、older、live、backfill 和 snapshot 必须进入同一个 owner/revision reconciler；分页游标应绑定完整 scope 和首次签发的到期时间。
- 为什么重要：历史与实时重叠、游标重放或保留窗口丢失时，单独处理任何一路都会造成覆盖、遗漏或顺序漂移。
- 下次怎么用：精确回补缺口并保留触发缺口的事件；无法回补时应用权威快照，最多自动重启三次，始终保留有效行和显式手动重试。

## 权威清空与乱序对账

- clear authority 必须随真实 state/history 快照传递，缓存键也要包含 authority 签名；只修改列表摘要无法支持恢复。
- 移动端主动发起 clear 时应在发布命令前按 owner 进入 `awaitingAuthority`，只有匹配的权威终态才能解除普通发送封锁。
- 陈旧或冲突 clear 不得取消恢复、清空较新缓冲或标记 ready；`revision == current` 且尚无 clear identity 的首次 clear 仍可能是权威事件。
- 跨语言矩阵应保持字节一致，并为每行编码 given/events/actions/expected，使协议、Store、Service 和 UI 都能基于同一数据契约验证。

## Kimi 流式完成的验证边界

将 Kimi 最终答案的发布条件建模为 owner、主代理身份、流完整性和权威终态的交集；音效与同一 finalized owner 绑定为 exactly-once。这样同聊天仍有排队任务时，已完成的回答可正常结算，而失败、子代理消息和不完整 delta 不会误触发完成。

验证应分三层：协议映射/客户端单元测试、跨聊天集成与 wx 焦点回归、显式 opt-in 的真实 Kimi smoke。前两层可在无凭据环境稳定运行；第三层会创建外部任务，不能把“默认跳过”表述为已完成 live 验收。

## 多聊天恢复的状态边界

Kimi 的 `turn_id` 只在 session 内有意义。owner、alias、提交意图、早到事件、缓冲流和恢复代次必须同时保留 `chat_id`、`session_id`、`prompt_id`（及 stream/offset）；`/clear` 和运行态 rebuild 必须一起清理这些结构，才能防止迟到事件污染新聊天。

## 无障碍流式 UI

测量导航前先确认事件队列仍有 pending，再捕获当前行及下一行的稳定 ID。这样可把分页淘汰后的合法选择回退与真正的按键双跳区分开。真实 SendMessage 测试应保存并明确设置线程局部修饰键，finally 恢复，不发全局松键或改变用户物理键盘状态。

异步恢复分为“存储结果准备好”“quiet 到期”“控件发布”三阶段。结果缓存已经完成而 dirty 仍在 quiet 内是合法状态；测试先断言 quiet 内零写入，再模拟 quiet 到期并等待原自动回调，不能手动 render 或增加 timeout 掩盖故障。

完整 GUI 联跑远慢于单节点时，用 cProfile 追踪 Yield 内实际回调。本阶段约 12 秒延迟来自旧窗口保存 timer 的大量 SQLite commit，而非按键本身；应定位资源归属，不以关闭正常持久化换取性能通过。

## 有界分页与兼容身份

单页 limit 只能限制一次取数，不能限制隐藏历史的总扫描成本；应额外测试前台查询数量，并用受控第 3 页读取阻塞证明主线程已释放。内容身份要覆盖新 UID、存储位置和旧无 ID 的重复项，不能以 provider item_id 或正文相同就合并不同事件。选择测试应跨完整内存/存储分页来源重放，而非只验证同一加载路径。

## Git 收尾安全门

提交前逐项审查 staged diff；推送前必须有唯一、明确的 GitHub push URL 和当前分支上游。缺失 upstream 时可保留本地提交，但不应修改 Git 配置、猜测目标分支或使用裸 `git push`。

## 打包与进程通信

PyInstaller 冻结后的 GUI 不应以 `sys.executable -m` 启动后台 worker；应使用同目录的独立控制台 worker。JSON Lines 的 stdin/stdout/stderr 固定 UTF-8，启动等待 `ready`，异常退出保留 stderr 与退出码。

## 网络与端口验证

端口监听不等于移动端可用：NATS 应使用 token 完成 `CONNECT` 与 `PING/PONG` 验证，并兼容 `INFO`、`+OK`、`PING` 混合返回。选择端口时使用生产回退常量；Windows socket 绑定使用 `SO_EXCLUSIVEADDRUSE`，并处理检查与 bind 之间的竞争。

## 环境、远端模型与现场证据

项目目标为 Python 3.11；虚拟环境解释器失效时用 `py -3.11 -m venv .venv` 重建并运行依赖检查。模型提供方按规范化模型 ID 分派，不按传输来源决定。若现场数据库已标记完成而 `/messages` 有完整正文，应保留两边的去敏证据，再复现客户端路由或完成处理问题。

## 稳定事件身份与无障碍焦点

- 经验：同一 provider 事件必须先用 chat/revision/turn/provider/native identity 建立唯一 durable owner，再在一个事务中更新 assembler 与安全投影；精确重放无操作，冲突可观察但不改变既有投影。
- 为什么重要：列表行、时间节点、分页、重启恢复和读屏焦点都依赖同一个稳定身份；文本相似、位置或接收批次无法承担这个职责。
- 下次怎么用：新增流式事件时同时测试乱序、leading gap、重叠、冲突、重启、相同标签和跨 owner ID 复用。无法可靠区分的旧记录应保守阻止投影更新，不要猜测并转移焦点。
- 回归测试也必须按稳定事件身份判断重放与冲突；旧测试若要求仅凭相邻文本合并，先核对契约并更新测试预期，不要据此修改生产归并逻辑。

## Owner-local 候选状态与提交边界

- 经验：新聊天或模型切换中的 selector 只是候选值；只有请求被接受后才写入对应聊天的 owner-local 状态，切回聊天时从该聊天恢复。
- 为什么重要：把全局下拉框当作已提交状态，会让一个聊天的新建或失败操作污染另一个聊天。
- 下次怎么用：状态测试至少覆盖聊天 A 保持原模型、聊天 B 选择新模型并新建、失败发送不提交，以及切回 A 后 UI 与持久化均恢复 A。

## 可编辑投影不等于 canonical 数据

- 经验：详情编辑器应持有一次性 scratch buffer 和不可变 canonical payload；展示用字符若不属于正文，必须按来源身份跟踪，而不是按字符值或前缀猜测。
- 为什么重要：临时编辑、复制和读屏导航可以自由发生，同时 Continue、朗读、列表、存储和跨端同步仍保持权威内容。
- 下次怎么用：使用真实 GUI 键盘与选择事件覆盖插入、删除、同值替换、Ctrl+C、剪贴板失败、全部关闭路径和重开；断言 owner、canonical、持久化和后台焦点完全不变。
