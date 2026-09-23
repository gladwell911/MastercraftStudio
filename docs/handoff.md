# 当前交接

## 快照（2026-09-23）

当前分支为 `feature/epic-4-answer-detail`，最新 MC 产品代码里程碑为 `392bf03adc44244ad2d55e01534ca84e782181f7`，其后只有交接文档同步提交。MC 的 BMAD Epics 1–4 已完成；RC 已在 `feature/epic-5-mobile-accessibility` 完成 Epic 5 两项代码，提交为 `572689b` 与 `c1db1e1`。2026-09-14 的正式包、公网 Live 和哈希记录仅代表上一次发布验证，不包含这些新提交。

## 已完成

- `36543a9`：Alt+A 清除上下文后只重发当前会话首个有效用户文本；自动重发被接受后播放一次普通发送成功音效。
- `e1cce1b`：聊天各自持久化并恢复所选模型；新聊天选择不污染旧聊天。
- `25d4f61`：新聊天立即进入历史列表，使用“新聊天”及最小可用数字后缀；首条消息后再自动命名。
- `9de2441`：Kimi 执行列表只显示有意义的中文阶段，原始过程仅进入允许的详情。
- `fb82429`：Codex/Kimi 按完整 owner/revision/provider 事件身份持久归并；重放、缺口、冲突、隐私锁存和重启恢复保持同一稳定行。
- `5d80f62`：执行列表第一条显示时间，后续相对“上一次显示时间的过程”累计达到或超过 300 秒时显示下一时间；稳定时间节点支持分页、重载和焦点恢复。
- `392bf03`：回答详情可在当前窗口临时编辑和复制；关闭后丢弃，不改变 canonical 回答、Continue、列表复制、朗读、HTML、持久化或移动同步。
- RC `572689b`：手机端发送状态只保留标题栏下方一个视觉与语义节点。
- RC `c1db1e1`：按 owner-qualified 稳定身份路由真实 Android accessibility focus；回答、不可变执行项、通知/历史入口和空聊天均有定向自动化覆盖。

## 验证状态

- Story 3.2：store 53 项、Codex/Kimi integration 12 项和 UI responsiveness 32 项通过；经过独立对抗审查收口。
- Story 3.3：13 项主流程、1 项 Codex 时间来源、11 项 Kimi 映射、32 项原生 UI 测试通过；`py_compile` 与 `git diff --check` 通过。
- Story 4.1：35 项 unit/list 与 8 项原生 wx 测试通过；`py_compile` 与 `git diff --check` 通过。
- 本阶段未执行真实 provider Live、正式包重建或物理 Windows 读屏验收；不能把定向自动化结果描述为新版本公网发布结论。
- RC Epic 5 的 8 项 CAP-21 widget 测试、native resolver/dispatcher JVM 测试和 Kotlin 编译通过；实体 Android TalkBack 仍未验收。

## 当前待办

1. 执行实体 Android TalkBack 验收，覆盖顶部唯一状态、首次接受的不可变执行项、实时回答、通知入口、历史入口、最后一条用户/助手消息、空聊天输入框、重复文案 identity 和负例不抢焦点。
2. 运行本地跨端回归，再进行真实 Codex/Kimi provider 与公网 Live 回归；所有发布证据必须基于包含 MC/RC 本阶段提交的新构建。

## 不要重复踩坑

- 不要按文本、相邻位置或当前选中聊天归并 provider 事件；使用完整 owner/revision/turn/provider/native identity，并让冲突保持可观察但不改变投影。
- 不要让私有 Kimi 片段、原始命令或路径先落入可见 crash-safe projection；隐私必须跨片段、精确重放和重启单调生效。
- 不要把时间基准更新到每一条过程；只有实际显示了时间节点的过程才成为下一基准。
- 不要把详情编辑缓冲写回 canonical 数据；展示用前导换行必须按字符身份跟踪，不能按 `startswith` 猜测。
- 不要把历史专项基线或 2026-09-14 发布记录当作本阶段全量通过证据。
