# 当前交接

## 快照（2026-09-25）

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

- 本轮 BMAD Epic 1–4 定向回归：store 53 项、Kimi UI 8 项、历史/响应 UI 12 项及若干 Codex/Kimi integration 与时间分页焦点用例通过。
- 较宽的 pytest 选择（232 passed、7 failed、736 deselected）包含 2 项聊天快捷键候选问题、1 项历史标题恢复差异、1 项 NATS 端口断言差异，以及 3 项与稳定事件身份契约冲突的旧文本去重断言；详情见下方。
- 2026-09-25 稳定事件身份定向用例 5 项通过：同身份精确重放合并、不同 provider item 即使文案相同仍保留独立行；3 项旧文本去重断言已按当前契约更新。

## 当前待办

1. 执行实体 Android TalkBack 验收，覆盖顶部唯一状态、首次接受的不可变执行项、实时回答、通知入口、历史入口、最后一条用户/助手消息、空聊天输入框、重复文案 identity 和负例不抢焦点。
2. 已于 2026-09-23 在推荐模拟器视口完成本地跨端回归；后续再进行真实 Codex/Kimi provider 与公网 Live 回归。发布证据必须基于包含 MC/RC 本阶段提交的新构建。

## 本轮测试发现与后续处理（2026-09-24）

- 聊天切换快捷键候选问题：test_char_hook_ctrl_left_switches_to_previous_chat_from_any_focus 中事件调用了 Skip()；test_char_hook_ctrl_right_switches_to_next_chat_from_any_focus 未记录目标聊天。若真实使用中复现，会影响从不同焦点位置用 Ctrl+左/右切换聊天。后续按这两个用例单独复测并检查焦点状态下的键盘路由。
- 历史标题恢复差异：test_load_state_rebuilds_timestamp_like_archive_titles 期望恢复出包含初始提问的标题，实际得到“自动化测试”。目前证据指向历史列表可发现性/命名恢复差异，没有发现消息丢失；后续核对归档数据与标题回填规则。
- NATS 端口断言差异：test_fixed_domain_nats_runtime_uses_public_runtime_and_status 期望 ws://127.0.0.1:18080/nats，实际运行时选择 ws://127.0.0.1:18082/nats。代码有端口回退行为；只有外部隧道或对端固定假设 18080 时才可能影响连接。本轮未运行公网 Live，需在目标部署配置下确认。
- 相邻 commentary 的 3 项旧测试曾要求仅凭文本合并，已在 2026-09-25 改为验证稳定身份契约；同身份重放才归并，无稳定身份的相似文本保留独立事件。5 项相关定向测试通过。

## 不要重复踩坑

- 不要按文本、相邻位置或当前选中聊天归并 provider 事件；使用完整 owner/revision/turn/provider/native identity，并让冲突保持可观察但不改变投影。
- 不要让私有 Kimi 片段、原始命令或路径先落入可见 crash-safe projection；隐私必须跨片段、精确重放和重启单调生效。
- 不要把时间基准更新到每一条过程；只有实际显示了时间节点的过程才成为下一基准。
- 不要把详情编辑缓冲写回 canonical 数据；展示用前导换行必须按字符身份跟踪，不能按 `startswith` 猜测。
- 不要把历史专项基线或 2026-09-14 发布记录当作本阶段全量通过证据。
