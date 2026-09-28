# 文档索引

这个目录只保留当前仍有维护价值、并且适合在日常开发时直接阅读的文档。

## 当前有效

- [`../README.txt`](../README.txt)：项目主入口，包含运行、打包、代码入口和维护约定
- [`F5_QUICK_RUN.md`](./F5_QUICK_RUN.md)：F5 快速运行功能的当前简版说明
- [`handoff.md`](./handoff.md)：当前阶段状态、验证结果和后续事项
- [`../_bmad-output/specs/spec-codex-kimi-chat-information/SPEC.md`](../_bmad-output/specs/spec-codex-kimi-chat-information/SPEC.md)：Codex/Kimi 聊天信息规格；同目录 `data-contract.md` 是必读数据契约
- [`../_bmad-output/initiative-chat-information/epic-codex-kimi-chat-information/tickets.toml`](../_bmad-output/initiative-chat-information/epic-codex-kimi-chat-information/tickets.toml)：聊天信息五项任务；Story 1.1–1.5 已完成，后续进入发布验证
- [`reflection.md`](./reflection.md)：本轮真实纠错记录
- [`experience.md`](./experience.md)：已经验证、可复用的排障与运行经验
- [`../_bmad-output/implementation-artifacts/spec-fix-concurrent-kimi-session-event-routing.md`](../_bmad-output/implementation-artifacts/spec-fix-concurrent-kimi-session-event-routing.md)：Kimi 并发 session 事件路由修复的验收规格
- [`../_bmad-output/implementation-artifacts/spec-fix-kimi-authoritative-completion-and-concurrent-chat-recovery.md`](../_bmad-output/implementation-artifacts/spec-fix-kimi-authoritative-completion-and-concurrent-chat-recovery.md)：Kimi 权威完成、回答延迟展示与多聊天恢复的验收规格
- [`../_bmad-output/implementation-artifacts/spec-localize-and-coalesce-kimi-f1-execution-steps.md`](../_bmad-output/implementation-artifacts/spec-localize-and-coalesce-kimi-f1-execution-steps.md)：Kimi F1 执行过程中文化与流式步骤归并的验收规格
- [`../_bmad-output/implementation-artifacts/spec-fix-execution-ui-blockers.md`](../_bmad-output/implementation-artifacts/spec-fix-execution-ui-blockers.md)：2026-09-08 执行列表增量同步、异步历史分页、等待期归属与真实 GUI 验收；末尾为最终结果，frontmatter 为剩余限制
- [`../../_bmad-output/implementation-artifacts/spec-2-4-synchronize-the-complete-execution-timeline-to-mobile.md`](../../_bmad-output/implementation-artifacts/spec-2-4-synchronize-the-complete-execution-timeline-to-mobile.md)：Story 2.4 跨端权威执行时间线、冻结分页、缺口恢复与验收结果
- 2026-09-21 Epics 1–4 最终规格位于工作区 `_bmad-output/implementation-artifacts/`：`spec-1-1-restart-the-current-text-context-once-with-truthful-audio.md`、`spec-2-1-preserve-each-chats-selected-model.md`、`spec-2-2-create-and-name-a-new-chat-immediately.md`、`spec-3-1-show-meaningful-kimi-execution-stages.md`、`spec-3-2-coalesce-one-logical-provider-event-into-one-item.md`、`spec-3-3-group-every-execution-timeline-by-accessible-time.md`、`spec-4-1-edit-answer-detail-temporarily-without-saving.md`。
- [`non-live-regression-baseline-2026-09-06.md`](./non-live-regression-baseline-2026-09-06.md)：2026-09-06 全量非实时测试的历史失败基线与复现范围

## 关键当前事实

- 笔记数据库不再跟随通用应用数据目录；当前固定使用 `D:\code\note\notes.db`。
- 修改笔记存储、同步或测试夹具时，优先通过 `resolve_notes_data_dir()` 注入测试路径，不要让测试写入真实笔记库。
- Kimi 的 `turn_id` 只在 session 内唯一；携带 `session_id` 的事件必须按会话隔离，不能仅凭 turn 在聊天之间路由。
- Kimi 的 F1 执行过程列表以协议事件生成中文主要步骤；不要把原始英文 `thinking.delta`、状态通知或工具流片段直接作为列表行。
- Kimi 的回答列表仅在主代理最终正文获得权威完成确认后更新；子代理过程、不完整流片段和失败终态只保留在执行过程或错误状态，不能提前显示为回答或播放完成音。
- 执行列表的旧 15 项失败已在上述执行规格逐项归类并修复/更新契约；最终定向验收去重 467 项通过，不代表冻结基线的其他领域或全笔记领域已验证。
- 旧 V2 远程执行事实流仍以 `durable_facts` 为来源；手机可视执行页优先使用 `execution_page_v3`，与桌面共用 canonical `execution_steps` 投影。v3 的 SQLite 快照行与分页游标按 chat/revision 隔离，V2 仍供旧客户端使用。
- 桌面执行项按 owner/revision/turn/provider/native event 身份归并；时间节点以“上一次实际显示时间的过程”为累计 300 秒基准，而不是比较相邻行。
- 聊天信息菜单与 Codex/Kimi 上下文、累计用量现已接入；Codex 主 `codex` 周额度和 Kimi OAuth 五小时/七日额度也已接入。信息行的数据来源、缺失状态和异步身份约束以聊天信息规格及 Story 1.1–1.5 计划为准。
- 回答详情编辑仅作用于一次性 scratch buffer；canonical 回答与冻结 owner payload 不可变，关闭窗口即丢弃编辑。
- 手机端日常跨端回归使用 RC 的 `scripts/run_cross_client_regression.ps1 -Mode Local`；本仓库的 `scripts/nats_e2e_desktop_harness.py` 只提供隔离 strict-V2 fixture。真实 Cloudflare/NATS 与 Codex/Kimi provider 只能由 `-Mode Live` 验证。

## 历史归档

- `superpowers/specs/`、`superpowers/plans/`：仍保留原路径的带日期设计与计划；其中全量重建、直接重放 quiet 队列等旧执行方案已由当前执行规格取代，不据未勾选任务判定现状。
- `archive/superpowers/specs/`：历史设计文档
- `archive/superpowers/plans/`：历史实施计划
- `archive/multi_agent/`：多代理脚手架与实验材料

这些文档保留用于追溯历史决策，但默认不建议在新会话中优先加载。

[`cross-client-codex-kimicode-audit-2026-09-06.md`](./cross-client-codex-kimicode-audit-2026-09-06.md) 是指定旧提交的跨端审查底稿，不是当前发布结论；开始后续跨端工作前须复核证据。跨端 durable 协议仍以 Story 2.4 规格和代码为准，2026-09-21 的桌面事件归并、时间投影和临时详情编辑以对应新规格和当前代码为准。

## 不再保留的内容

以下类型文档已经移除或不应再进入主上下文：

- 一次性的测试报告
- 构建生成的警告输出
- 已过时且与当前代码不一致的操作说明
- 编码损坏、无法可靠维护的旧文档
