# 数据与界面契约

| 显示行 | 权威来源 | 口径与缺失处理 |
| --- | --- | --- |
| Codex 当前上下文 | app-server `thread/tokenUsage/updated.tokenUsage.last.totalTokens` 与 `modelContextWindow`；兼容旧 `token_count.info.last_token_usage.total_tokens/model_context_window` | `last` 是最近模型调用的上下文压力近似值；动态窗口优先，窗口缺失时标“窗口未知”，不以静态型号表冒充精确值。压缩后接受新快照，允许数值下降。|
| Codex 会话累计 | 同事件 `tokenUsage.total.totalTokens`；旧事件 `total_token_usage.total_tokens` | thread 生命周期累计消耗，可超过上下文窗口；不得用作当前占用。|
| Codex 周额度 | `account/rateLimits/read` 的主 `codex` bucket；取 `windowDurationMins` 为 10080 的窗口的 `usedPercent`、`resetsAt` | 显示 `100 - usedPercent` 的剩余百分比，`resetsAt` 为 Unix 秒，转本地具体日期时间；缺周窗口/时间分别标明。`ordinaryUsageAllowed` 与百分比分开，不从重置时间推断额度恢复。API key 账号标不适用。|
| Kimi 当前上下文 | `agent.status.updated.contextTokens/maxContextTokens`；恢复时可查 `GET /sessions/{id}/status` 的 `context_tokens/max_context_tokens` | 两值同源相除得已用百分比；模型切换、压缩后按新状态更新。无总窗口时不推算。|
| Kimi 会话累计 | `GET /sessions/{id}/snapshot` 内 `session.usage.input_tokens + output_tokens` | 普通 session 详情的 `usage` 是零值占位；`cache_read_tokens`、`cache_creation_tokens` 不再另加。面板可见时于任务结束或低频补拉；若 snapshot 暂不可得，标上次更新时间。|
| Kimi 五小时/七日额度 | `GET /api/v1/oauth/usage` 的 `data.kind=ok`、`quota.usages.limit5h/limit7d` | `usedRatio × 100` 为已用比例；`resetAt` 是可选 RFC3339。五小时显示本地具体重置时间，七日显示按当前时刻计算的剩余时长。任一窗口缺失只影响对应行；`kind=error` 是失败，不当作 0。仅托管 OAuth 账号适用。|

## 刷新与键盘行为

- 打开时立即展示缓存快照及其更新时间，并在后台获取所需状态；后台请求不阻塞列表焦点。
- Codex token 事件、Kimi agent 状态事件到达时更新对应行；不承诺每秒收到事件。账号窗口在面板可见时低频刷新，并在任务完成、账号变化后重拉。七日倒计时可用本地计时更新文本，不代表重新查询额度。
- 只在显示文本变化时局部更新；保持当前选中行和焦点。关闭窗口停止其定时器、丢弃未返回的异步结果。结果须匹配聊天、原生会话和账号身份。
- 列表为独立窗口，默认焦点在列表；方向键逐行浏览；列表焦点按 Esc 关闭并恢复打开前控件焦点。主窗口已有 Esc 最小化行为不得触发。
- 各行区分“未登录”“此登录方式不适用”“服务端未提供”“查询失败”“上次更新于…”；缓存值不得标称实时，缺值不得显示成 0%。

## 现有代码与回归入口

- 菜单和回答列表：[main.py](../../../main.py) `1946–1958`、`2004–2006`；主窗 Esc 最小化 `16005–16015`。新面板不复用回答列表。
- 现有上下文类型和百分比：[context_usage.py](../../../context_usage.py) `14–30`；现有标签只输出取整 `used / window`，见 `105–114`。
- Codex 已能请求账号额度：[codex_client.py](../../../codex_client.py) `594–595`；现有解析 `1099–1167` 误把累计作为上下文、未展开标准 v2 `tokenUsage`；现有额度格式化 [main.py](../../../main.py) `4736–4747` 误读 `percentUsed`，应为 `usedPercent`。
- Kimi 已接状态用量：[kimi_server_client.py](../../../kimi_server_client.py) `581–604`，主窗映射 [main.py](../../../main.py) `13433–13450`；客户端尚需 snapshot 与 OAuth usage 读取封装。
- 验证需覆盖解析旧/新 Codex 事件、Kimi status/snapshot/OAuth 失败和缺字段、聊天与账号切换、Esc 和屏幕阅读器焦点、定时器关闭与无变化不重绘；按 [AGENTS.md](../../../AGENTS.md) 串行运行相关 `tests/test_*ui_automation.py` 与模型工作流测试。

## 官方依据

- [Codex token 用量协议](https://github.com/openai/codex/blob/main/codex-rs/app-server-protocol/schema/json/v2/ThreadTokenUsageUpdatedNotification.json)
- [Codex 额度协议](https://github.com/openai/codex/blob/main/codex-rs/app-server-protocol/schema/json/v2/GetAccountRateLimitsResponse.json)
- [Kimi Server API：status、snapshot、OAuth usage](https://github.com/MoonshotAI/kimi-code/blob/main/docs/en/reference/server-api.md)
