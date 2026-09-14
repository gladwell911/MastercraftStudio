# 当前交接

## 快照（2026-09-14）

桌面端与手机端的 strict-V2 远程链路已经完成本阶段修复。正式桌面程序位于 `D:\code\cx\mc\mc.exe`，SHA-256 为 `0A0DA59EBBB1BA60B67901CEC3F74B6391BCCE25A241133907CF3C40FD76BB12`。公网 Live 回归已验证 `codex/main` 与 `kimi/main` 从手机发送、桌面关联接受、真实 provider 执行、数据库 `done`、durable `assistant_final` 到手机可见回答的完整链路。

## 已完成

- strict-V2 全局命令与会话命令范围、响应关联和 owner/epoch 校验已与 RC 对齐。
- `assistant_final` 只在 turn 已持久化为 `done` 后，从真实 `answer_md` 幂等发布；`正在请求...` 等占位文本不会占用 canonical message identity。
- 手机显式选择 `kimi/main` 时不会继承已有聊天的 `codex/main`；桌面按规范化模型 ID 分派真实 provider。
- Kimi F1 执行过程使用真实协议事件生成中文主要步骤，不把结构性占位文案冒充执行过程。
- 正式包已经重建并完成公网双模型回归。

## 验证状态

- `tests/test_story4_review_regressions_unit.py`：5 passed。
- `tests/test_main_unit.py` 的 remote-final 定向用例：1 passed。
- 公网 WSS/token 探针、Codex IME、Kimi 按钮发送、精确 assistant UI：全部通过。
- 数据库核验：两模型均为正确模型、`request_status=done`，每轮只有一条真实且非占位的 durable `assistant_final`。

## 当前问题与后续建议

- 无阻断交付的 P0/P1。
- 可选增强：增加 repeated `done` 保存、非占位部分 pending 回答及进程重启交错场景，进一步验证 exactly-once final。
- `D:\code\sj` 根目录不是 Git 仓库，根目录 BMAD 规格不会随 MC/RC 提交；验证事实应同时保留在两仓库交接文档。

## 不要重复踩坑

- 不要从 provider 的早期 callback 直接发布最终通知；必须以已持久化的 `done` turn 为事实来源。
- 不要把 `正在请求...`、partial delta 或执行过程行当成 assistant final。
- 不要用固定历史 marker 验证公网链路；每轮生成唯一 marker，并同时核对 DB、durable fact 与手机 assistant UI。
- 不要在日志、文档或提交中记录真实 token。
