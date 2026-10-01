# Deferred Work

## 五 Epic 深审候选（2026-10-01，未确认，不扩展本轮修复）

- Codex complete→partial token 记录回退：需要真实或契约允许的事件序列，不能推测部分字段会覆盖有效上下文。
- RC 通知 post→gate 变化→cancel 的瞬时提醒：需要设备音、震动或 heads-up 实证，属既有机制。
- Codex 请求发送后 ACK 前 worker 退出：需要受控真实请求与完整 timeout/recovery 观察。ACK 前注入 metadata 的复现不可达，不能据此放宽 generation 守卫。

详见同目录 `review-five-epics-deep-20261001.md`；确认缺陷 R1–R8 已修复并独立验证。

## Deferred from: code review of spec-fix-active-claudecode-client-cross-chat-interception (2026-09-06)

- 单个全局活跃 Claude 客户端槽无法同时保留两个聊天中并行运行的 Claude worker；后注册客户端会替换先前聊天的引用。
- `_start_claudecode_worker_for_turn` 的当前聊天检查与客户端注册不是原子操作，聊天切换可能在两者之间发生。
- Claude worker 仅在启动时属于当前聊天才登记客户端，后台聊天的运行中 worker 之后无法续写。
- 新聊天、归档及上下文重置使用无条件清理，可能与另一个 Claude worker 的注册交错并清除替换客户端。
- 同一聊天显式选择 Kimi、Codex 或 OpenRouter 模型时，活跃 Claude 客户端仍可能在模型解析前截获输入。
