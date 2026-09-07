# 文档索引

这个目录只保留当前仍有维护价值、并且适合在日常开发时直接阅读的文档。

## 当前有效

- [`../README.txt`](../README.txt)：项目主入口，包含运行、打包、代码入口和维护约定
- [`F5_QUICK_RUN.md`](./F5_QUICK_RUN.md)：F5 快速运行功能的当前简版说明
- [`handoff.md`](./handoff.md)：当前阶段状态、验证结果和后续事项
- [`reflection.md`](./reflection.md)：本轮真实纠错记录
- [`experience.md`](./experience.md)：已经验证、可复用的排障与运行经验
- [`../_bmad-output/implementation-artifacts/spec-fix-concurrent-kimi-session-event-routing.md`](../_bmad-output/implementation-artifacts/spec-fix-concurrent-kimi-session-event-routing.md)：Kimi 并发 session 事件路由修复的验收规格
- [`../_bmad-output/implementation-artifacts/spec-fix-kimi-authoritative-completion-and-concurrent-chat-recovery.md`](../_bmad-output/implementation-artifacts/spec-fix-kimi-authoritative-completion-and-concurrent-chat-recovery.md)：Kimi 权威完成、回答延迟展示与多聊天恢复的验收规格
- [`../_bmad-output/implementation-artifacts/spec-localize-and-coalesce-kimi-f1-execution-steps.md`](../_bmad-output/implementation-artifacts/spec-localize-and-coalesce-kimi-f1-execution-steps.md)：Kimi F1 执行过程中文化与流式步骤归并的验收规格
- [`non-live-regression-baseline-2026-09-06.md`](./non-live-regression-baseline-2026-09-06.md)：2026-09-06 全量非实时测试的历史失败基线与复现范围

## 关键当前事实

- 笔记数据库不再跟随通用应用数据目录；当前固定使用 `D:\code\note\notes.db`。
- 修改笔记存储、同步或测试夹具时，优先通过 `resolve_notes_data_dir()` 注入测试路径，不要让测试写入真实笔记库。
- Kimi 的 `turn_id` 只在 session 内唯一；携带 `session_id` 的事件必须按会话隔离，不能仅凭 turn 在聊天之间路由。
- Kimi 的 F1 执行过程列表以协议事件生成中文主要步骤；不要把原始英文 `thinking.delta`、状态通知或工具流片段直接作为列表行。
- Kimi 的回答列表仅在主代理最终正文获得权威完成确认后更新；子代理过程、不完整流片段和失败终态只保留在执行过程或错误状态，不能提前显示为回答或播放完成音。

## 历史归档

- `archive/superpowers/specs/`：历史设计文档
- `archive/superpowers/plans/`：历史实施计划
- `archive/multi_agent/`：多代理脚手架与实验材料

这些文档保留用于追溯历史决策，但默认不建议在新会话中优先加载。

## 不再保留的内容

以下类型文档已经移除或不应再进入主上下文：

- 一次性的测试报告
- 构建生成的警告输出
- 已过时且与当前代码不一致的操作说明
- 编码损坏、无法可靠维护的旧文档
