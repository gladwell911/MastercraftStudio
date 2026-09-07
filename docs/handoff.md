# 当前交接

## 快照（2026-09-07）

当前分支为 `fix/mobile-kimicode-routing`。本轮完成 Kimi Code 的权威完成判定、回答延迟展示和同一应用内多聊天并发恢复：F1 继续显示本地化的执行摘要，而回答列表只在主代理的最终回答被权威确认后更新。

## 已完成

- `kimi_server_client.py` 为 WebSocket/REST 恢复保留 session、epoch、stream、offset 与订阅边界；进程重启会重新发现 token，订阅拒绝隔离到单个 session，恢复在握手和订阅确认后才报告成功。
- `main.py` 以 `(chat_id, session_id, prompt_id)` 隔离 Kimi owner、排队请求、alias、早到事件、增量正文和恢复状态。切换聊天、`/clear`、恢复和进程退出不会让旧状态复活或串到其他聊天。
- 最终回答须有主代理身份、匹配 owner、完整正文和权威终态；失败/中断不会播放完成音。子代理的过程信息仅进入 F1。
- README 与文档索引已同步这项用户可见行为；用户级 `C:\Users\gladwell\.kimi-code\AGENTS.md` 已确认包含简体中文规则（仓库外文件，未纳入本仓库）。

## 已验证

```powershell
py -3.11 -m pytest tests/test_kimi_event_mapping_unit.py tests/test_kimi_server_client_unit.py -q
# 132 passed

py -3.11 -m pytest tests/test_kimi_integration.py tests/test_kimi_ui_responsiveness_automation.py -q
# 79 passed

py -3.11 -m pytest tests/test_main_unit.py -q -k "kimi or execution or switch_current_chat"
# 99 passed, 15 failed, 631 deselected

py -3.11 -m compileall -q main.py kimi_server_client.py tests/test_kimi_event_mapping_unit.py tests/test_kimi_integration.py tests/test_kimi_live_smoke.py tests/test_kimi_server_client_unit.py tests/test_kimi_ui_responsiveness_automation.py
git diff --check
```

主逻辑筛选中的 15 项失败逐项等同于 [2026-09-06 冻结基线](./non-live-regression-baseline-2026-09-06.md)，均为通用执行列表分页、导航和增量追加断言，不是新增 Kimi 回归。`tests/test_kimi_live_smoke.py` 在默认环境为 `5 skipped`；需已登录的 Kimi CLI 并显式设置 `KIMI_LIVE_TEST=1` 才会创建真实外部任务。

## 当前风险与下一步

- 本地提交完成后，分支仍未配置 Git upstream；收尾流程禁止自行修改 remote/upstream，因此本轮不能推送。需要维护者先建立明确的 GitHub 上游后再推送该提交。
- Kimi 协议新增未知工具类别时会回退为“正在处理任务”；新增常见类别应补充中文摘要映射与 fixture。
- 若处理 15 项通用 execution-list 基线，应另建任务，不要为了旧断言回退本轮的 owner 隔离、流完整性或焦点保护。

## 不应重复尝试

- 不要按英文字符过滤 F1，也不要把原始 thinking/tool 文本直接当作 F1 标题；应使用结构化事件生成中文阶段摘要。
- 不要只用 `turn_id` 跨 session 路由；Kimi 的身份、缓存、完成和恢复均需要聊天和 session 边界。
- 不要把 `status`、`usage` 或静默通知当作文本 flush 边界，也不要让不完整的增量正文覆盖 REST 对账得到的完整最终答案。
