# Deferred Work

## Deferred from: code review of spec-fix-active-claudecode-client-cross-chat-interception (2026-09-06)

- 单个全局活跃 Claude 客户端槽无法同时保留两个聊天中并行运行的 Claude worker；后注册客户端会替换先前聊天的引用。
- `_start_claudecode_worker_for_turn` 的当前聊天检查与客户端注册不是原子操作，聊天切换可能在两者之间发生。
- Claude worker 仅在启动时属于当前聊天才登记客户端，后台聊天的运行中 worker 之后无法续写。
- 新聊天、归档及上下文重置使用无条件清理，可能与另一个 Claude worker 的注册交错并清除替换客户端。
- 同一聊天显式选择 Kimi、Codex 或 OpenRouter 模型时，活跃 Claude 客户端仍可能在模型解析前截获输入。
