# 测试自动化总结

## 生成的测试

### API 测试

- [x] `tests/test_mobile_kimi_cross_chat_e2e.py` — NATS `message` 命令拒绝空文本和无效显式模型，返回 400，且不会触碰活跃 Claude 客户端或启动 worker。

### E2E 测试

- [x] `tests/test_mobile_kimi_cross_chat_e2e.py` — 模拟手机端 NATS 命令经过 `RemoteNatsTransport` 路由、桌面消息入口和模型分派，验证跨聊天 Kimi 消息不会进入另一聊天的 Claude 客户端。
- [x] `tests/test_mobile_kimi_cross_chat_e2e.py` — 验证同聊天 Claude 远端消息仍续写现有客户端，不创建重复 turn 或 worker。

## 覆盖范围

- NATS `message` 命令：1/1 个相关命令路由已覆盖。
- 核心场景：4/4（跨聊天 Kimi、同聊天 Claude、空文本、无效模型）。
- 模型路由：确认 Kimi 专用 worker 被调用，且 Codex、Claude、OpenRouter 路径不会误启动。
- 状态隔离：确认跨聊天提交后原 Claude 客户端及所属 `chat_id` 保持不变。
- 外部真实链路：0/1；测试不连接真实手机、NATS 服务器、Kimi 或 Claude 服务，以保持默认测试稳定且无凭据依赖。

## 后续建议

- 在具备 Android 模拟器和本地 NATS Server 的专用环境中增加 opt-in `live` 测试，覆盖 Flutter 客户端实际发布消息。
- 将本测试文件加入 CI 的默认 pytest 测试集。

## 回归验证记录

- 首次组合回归：51 passed、1 failed；失败来自既有 NATS 端口测试仍期待旧回退端口 `4223`。
- 已将该测试改为跟随 `REMOTE_NATS_PORT_FALLBACKS[0]`，使断言与生产端口优先级保持一致。
- 最终组合回归：`52 passed in 7.06s`。

## 验证清单

- [x] 已生成适用的 API 与 E2E 测试。
- [x] 使用项目现有 pytest、wx fixture 和 NATS 路由接口。
- [x] 覆盖正常路径和两个关键错误路径。
- [x] 全部生成测试及相关组合回归执行成功。
- [x] 测试描述清晰，无硬编码等待或顺序依赖。
- [x] 本场景没有浏览器/GUI 元素定位器，语义定位项不适用。
- [x] 测试与总结已保存到项目约定目录，并记录覆盖指标。

## 2026-09-07 — Kimi 权威完成与并发恢复

- `py -3.11 -m pytest tests/test_kimi_event_mapping_unit.py tests/test_kimi_server_client_unit.py tests/test_kimi_integration.py tests/test_kimi_ui_responsiveness_automation.py -q`：CR6 收尾定向回归最终 `211 passed`。
- `py -3.11 -m pytest tests/test_main_unit.py -q -k "kimi or execution or switch_current_chat" --tb=no`：`99 passed, 15 failed, 631 deselected`；15 项失败与规格冻结的 execution-list 基线逐项一致，没有新增 Kimi 失败。
- `py -3.11 -m compileall -q main.py kimi_server_client.py tests/test_kimi_event_mapping_unit.py tests/test_kimi_server_client_unit.py tests/test_kimi_integration.py tests/test_kimi_ui_responsiveness_automation.py tests/test_main_unit.py tests/test_kimi_live_smoke.py`：通过。
- Kimi 用户级 `C:\Users\gladwell\.kimi-code\AGENTS.md` 第 2 条中文规则：精确内容检查通过。
- `tests/test_kimi_live_smoke.py` 已收紧权威终态条件；真实服务 live smoke 未运行，因为它会创建/中断真实外部任务，保持 opt-in。

### CR6 新增覆盖

- 已覆盖省略 epoch 继承、同 seq 无 offset volatile phase、坏订阅隔离、HTTP 200 应用层 5xx 的 POST 结果未知，以及恢复成功仅报告一次实际 session 集合。
- 已覆盖子代理 assistant 正文进入 F1、非 main idle 不授权 fallback、失败终态不响铃、offset 缺口阻止发布、stream 首次出现顺序，以及 `/clear` 清理 owner/buffer/recovery 状态。
- 已覆盖同一 client 的服务进程重启换 token、ambiguous steer 的 alias/queued 双分支、迟到失败 generation 不覆盖完成结果，以及共享 deadline 剩余预算传递。
