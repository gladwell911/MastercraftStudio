# 文档索引

这个目录只保留当前仍有维护价值、并且适合在日常开发时直接阅读的文档。

## 当前有效

2026-10-09：双电脑远程身份及手机三标签已部署，笔记本独立隧道和实体手机双公网各一次真实问答通过；来源过滤和单侧已读清零实测通过。此前 MC 83 项、RC 定向和原生测试及双 broker Local 的结果仍对应各自版本。文件下载、单机断连、锁屏通知点击及 TalkBack 尚待验。详见[双电脑配置](remote-control.md)和[当前交接](handoff.md)。旧完整桌面验收停止状态保持。

2026-10-07：Kimi 系统注入不再截断当前 prompt 的回答、思考及恢复范围；统一按结构化 origin 判断边界，272 项定向测试通过。BMAD 旧模板兼容修复另有 5 项通过。源码修复与现场安装版分开核对：本轮未重新打包，也未恢复旧失败记录。详情见[当前交接](handoff.md)与[注入边界修复规格](../_bmad-output/implementation-artifacts/spec-fix-kimi-injected-message-boundaries.md)。

2026-10-06 当前状态见[交接](handoff.md)：Kimi 无消息 ID 的公开思考流已适配真实 step 生命周期，执行页及时显示原文摘录与全文；REST 补齐同一条目，工具前普通说明作为 commentary 保留。当前实施记录为 `D:/code/sj/_bmad-output/implementation-artifacts/plan-kimi-thinking-progress.md`。跨设备已读、通知范围清理及 Ctrl+Shift+X 已实现；完整矩阵与中文听验未完成，用户已停止完整桌面验收。2026-10-04 F1、Kimi 恢复与活动排序成果仍保留，对应历史报告只证明当时版本。

Codex 通过整目录链接复用全局技能，在新任务前刷新原生清单；打包成功并校验最终产物后自动启动一次。三个定向文件独立验证 122 项通过，原生非模型探针确认技能/插件发现与刷新；此次未实际打包或更新安装版。方案及阶段验证见[全局技能与打包后启动实施记录](../_bmad-output/implementation-artifacts/plan-global-skills-package-start.md)，使用方法见[项目入口](../README.txt)。

既有通知、回答时间与执行语义的[独立报告](../_bmad-output/implementation-artifacts/verify-notification-routing-answer-time-accessibility.md)、Codex 审批[实施记录](../_bmad-output/implementation-artifacts/plan-codex-command-approval.md)保留，仅证明相应版本。

- [`../README.txt`](../README.txt)：项目主入口，包含运行、打包、代码入口和维护约定
- [`F5_QUICK_RUN.md`](./F5_QUICK_RUN.md)：F5 快速运行功能的当前简版说明
- [`handoff.md`](./handoff.md)：当前阶段状态、验证结果和后续事项
- [`remote-control.md`](./remote-control.md)：台式机 default、笔记本 laptop 固定配置和跨端部署边界
- Epic1 的工作区实施记录与 QA 报告当前未找到，移除失效链接；聊天信息规格和五项任务见下列仓库记录，旧验证结论只证明当时版本。
- [`../_bmad-output/specs/spec-codex-kimi-chat-information/SPEC.md`](../_bmad-output/specs/spec-codex-kimi-chat-information/SPEC.md)：Codex/Kimi 聊天信息规格；同目录 `data-contract.md` 是必读数据契约
- [`../_bmad-output/initiative-chat-information/epic-codex-kimi-chat-information/tickets.toml`](../_bmad-output/initiative-chat-information/epic-codex-kimi-chat-information/tickets.toml)：聊天信息五项任务；Story 1.1–1.5 已完成，后续进入发布验证
- [`reflection.md`](./reflection.md)：本轮真实纠错记录
- [`experience.md`](./experience.md)：已经验证、可复用的排障与运行经验
- [`../_bmad-output/implementation-artifacts/spec-fix-concurrent-kimi-session-event-routing.md`](../_bmad-output/implementation-artifacts/spec-fix-concurrent-kimi-session-event-routing.md)：Kimi 并发 session 事件路由修复的验收规格
- [`../_bmad-output/implementation-artifacts/spec-fix-kimi-authoritative-completion-and-concurrent-chat-recovery.md`](../_bmad-output/implementation-artifacts/spec-fix-kimi-authoritative-completion-and-concurrent-chat-recovery.md)：Kimi 权威完成、回答延迟展示与多聊天恢复的验收规格
- [`../_bmad-output/implementation-artifacts/spec-localize-and-coalesce-kimi-f1-execution-steps.md`](../_bmad-output/implementation-artifacts/spec-localize-and-coalesce-kimi-f1-execution-steps.md)：Kimi F1 执行过程中文化与流式步骤归并的验收规格
- [`../_bmad-output/implementation-artifacts/spec-fix-execution-ui-blockers.md`](../_bmad-output/implementation-artifacts/spec-fix-execution-ui-blockers.md)：2026-09-08 执行列表增量同步、异步历史分页、等待期归属与真实 GUI 验收；末尾为最终结果，frontmatter 为剩余限制
- Story 2.4 跨端执行时间线：旧工作区规格当前未找到，不保留失效链接；以现有协议实现、测试及归档审查记录核对。
- 2026-09-21 Epics 1–4 最终规格位于工作区 `_bmad-output/implementation-artifacts/`：`spec-1-1-restart-the-current-text-context-once-with-truthful-audio.md`、`spec-2-1-preserve-each-chats-selected-model.md`、`spec-2-2-create-and-name-a-new-chat-immediately.md`、`spec-3-1-show-meaningful-kimi-execution-stages.md`、`spec-3-2-coalesce-one-logical-provider-event-into-one-item.md`、`spec-3-3-group-every-execution-timeline-by-accessible-time.md`、`spec-4-1-edit-answer-detail-temporarily-without-saving.md`。
- [`non-live-regression-baseline-2026-09-06.md`](./non-live-regression-baseline-2026-09-06.md)：2026-09-06 全量非实时测试的历史失败基线与复现范围

## 关键当前事实

- 源码运行的笔记数据库使用 `D:\code\note\notes.db`；打包版将使用个人版 OneDrive 根目录下的 `OneDrive\code\data\sj\notes.db`。切换前核对实际运行包的数据路径；数据衔接步骤见 [`handoff.md`](./handoff.md)。
- MC 当前主分支为 `main`，跟踪 `origin/main`；RC 为 `master`，跟踪 `origin/master`。旧开发分支已合并并删除；日常安装版版本另按交接核对。
- 修改笔记存储、同步或测试夹具时，优先通过 `resolve_notes_data_dir()` 注入测试路径，不要让测试写入真实笔记库。
- Kimi 的 `turn_id` 只在 session 内唯一；携带 `session_id` 的事件必须按会话隔离，不能仅凭 turn 在聊天之间路由。
- Kimi REST 的 user 消息仅在结构化 `origin.kind == "injection"` 时跳过 prompt 边界；优先采用存在的 `metadata.origin`，仅其缺失时兼容顶层 `origin`。未知或畸形 origin 保守视为真实问题，不能按正文猜测系统注入。
- Kimi 的 F1 执行页保留 provider 实际公开的思考与已由工具调用确认的中间说明；列表取首个非空行、最多 80 字，全文详情保留完整公开正文。无消息 ID 的流按 session/epoch/turn/agent/step/kind 隔离，保留 offset 去重与缺口处理。REST 在明确的 prompt 边界内按 assistant 步骤顺序补齐原行；隐藏/private/non-disclosable 内容不进入可视投影，工具流水仍不生成执行行。
- Kimi 的回答列表仅在主代理最终正文获得权威完成确认后更新；子代理过程、不完整流片段和失败终态只保留在执行过程或错误状态，不能提前显示为回答或播放完成音。
- 执行列表的旧 15 项失败已在上述执行规格逐项归类并修复/更新契约；最终定向验收去重 467 项通过，不代表冻结基线的其他领域或全笔记领域已验证。
- 旧 V2 远程执行事实流仍以 `durable_facts` 为来源；手机可视执行页优先使用 `execution_page_v3`，与桌面共用 canonical `execution_steps` 投影。v3 的 SQLite 快照行与分页游标按 chat/revision 隔离，V2 仍供旧客户端使用。
- 桌面执行项按 owner/revision/turn/provider/native event 身份归并；时间节点以“上一次实际显示时间的过程”为累计 300 秒基准，而不是比较相邻行。
- ChatStore 的 `execution_steps.visible` 在写入时按共享规则维护，旧库初始化一次原子回填；`idx_execution_visible_tail` 支持真实可视尾页有界读取。F1 复用同 owner/revision 的已知尾页，完整补齐在 worker，不能用原始行 LIMIT 或运行时 UDF 扫描假称前台工作量有界。
- 聊天活动事实直接更新历史单行排序并保留身份/焦点；读取、标题、失败和重放不刷新活动时间。手机遵循相同规则。
- 已读游标按 pair/chat/generation 单调合并；首次成功非空回答的 answer_seq 独立于活动时间、列表位置和执行序列。清空才更换 read generation；自动定位不确认已读。`mark_chat_read`、`chat_read_state` 和 `chat_read_changed` 合同见 RC 的 `docs/current/remote-control.md`，复跑见其 `testing.md`。
- 每台 MC 的 `remote_machine.json` 保存本机 pair/domain/token；显式 pair 环境变量优先，其后已知计算机名 `emperorComputer`→`default`、`emperorLaptop`→`laptop`，未知名回退持久值/default。地址与 token 的显式环境值优先，笔记本没有台式机凭据回退。手机未读标签按成功收到的通知消息计数，而不是未读聊天数。OneDrive 同步本次不改，用户保证共享数据不并发写入。
- 回答使用独立的 created_at/answer_at，时间行同样累计 300 秒。终结重放保持答案时间，完成及 accepted 延迟刷新核对现有回答行；旧记录只恢复可信 final 时间，不填当前时间。
- 聊天信息菜单与 Codex/Kimi 上下文、累计用量现已接入；Codex 主 `codex` 周额度和 Kimi OAuth 五小时/七日额度也已接入。信息行的数据来源、缺失状态和异步身份约束以聊天信息规格及 Story 1.1–1.5 计划为准。
- 回答详情编辑仅作用于一次性 scratch buffer；canonical 回答与冻结 owner payload 不可变，关闭窗口即丢弃编辑。
- 手机端日常跨端回归使用 RC 的 `scripts/run_cross_client_regression.ps1 -Mode Local`；本仓库的 `scripts/nats_e2e_desktop_harness.py` 只提供隔离 strict-V2 fixture。真实 Cloudflare/NATS 与 Codex/Kimi provider 只能由 `-Mode Live` 验证。

## 历史归档

- `superpowers/specs/`、`superpowers/plans/`：仍保留原路径的带日期设计与计划；其中全量重建、直接重放 quiet 队列等旧执行方案已由当前执行规格取代，不据未勾选任务判定现状。
- `archive/superpowers/specs/`：历史设计文档
- `archive/superpowers/plans/`：历史实施计划
- `archive/multi_agent/`：多代理脚手架与实验材料

这些文档保留用于追溯历史决策，但默认不建议在新会话中优先加载。

[`cross-client-codex-kimicode-audit-2026-09-06.md`](./cross-client-codex-kimicode-audit-2026-09-06.md) 是指定旧提交的跨端审查底稿，不是当前发布结论；开始后续跨端工作前须复核证据。旧 Story 2.4 规格当前未找到，跨端 durable 合同以当前协议代码、定向测试和 RC 当前协议文档核对，不能引用缺失规格证明现状。2026-09-21 的桌面事件归并、时间投影和临时详情编辑以对应新规格和当前代码为准。

## 不再保留的内容

以下类型文档已经移除或不应再进入主上下文：

- 一次性的测试报告
- 构建生成的警告输出
- 已过时且与当前代码不一致的操作说明
- 编码损坏、无法可靠维护的旧文档
