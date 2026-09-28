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

## 2026-09-23 — MC Epics 1–4 定向回归

本轮复用已有自动化用例，没有新增或修改应用代码、测试代码。

### 通过

- wx UI：`tests/test_history_ui_automation.py` 与 `tests/test_codex_ui_responsiveness_automation.py` 的 Alt+A 清空上下文、清空通知归属、新聊天创建、所选模型恢复和临时回答详情编辑筛选：12 passed，39 deselected。
- Kimi UI 响应：`tests/test_kimi_ui_responsiveness_automation.py`：8 passed。
- 执行事件持久存储：`tests/test_chat_store_unit.py`：53 passed。
- Codex/Kimi 执行事件集成筛选：10 passed，93 deselected。
- 执行时间分组、时间戳与分页身份窄选：22 passed。

### 失败与范围说明

- 较宽的 time/execution/page/focus/replay 筛选：232 passed、7 failed、736 deselected。3 项旧 commentary 文本/相邻位置去重断言与当前 Epic 3.2 的稳定事件身份契约冲突；其余为 2 项 Ctrl+方向键、1 项归档标题和 1 项 NATS 固定端口断言，均不属于本轮时间分组窄选路径。
- 两项旧相邻 commentary 去重断言单独复跑仍失败；未修改测试或产品代码。
- 本轮未连接真实 Codex/Kimi provider，也未运行公网 Live。

## 2026-09-25 — 稳定事件身份回归测试

### 新增与更新的测试

- tests/test_main_unit.py：新增同一 provider 身份精确重放只保留一条并只广播一次的测试。
- tests/test_main_unit.py：新增文案相同但 provider item 身份不同仍保留两条并各自广播的测试。
- 将 3 项旧的相邻文本去重断言改为验证无稳定身份时不按文案合并。

### 验证

- 5 个精确 pytest 用例通过。
- 本轮未连接真实 Codex/Kimi provider；此处验证的是桌面事件归并、持久化入口和广播行为。

## 2026-09-27 — 聊天信息 Story 1.4/1.5 QA 自动化

### 新增与复用的测试

- `tests/test_chat_information_ui_automation.py`：使用真实 wx 聊天信息窗口和定时器入口，受控 Kimi 客户端验证无 session 的 OAuth 账号 A→B 切换、迟到 A 回包按代次丢弃、焦点与选择保持，以及定时器不重查 session status/snapshot。
- 同一文件新增真实后台线程与 `wx.CallAfter` 投递的窗口集成测试；有界事件泵等待 A、B 两次额度更新，不使用固定 sleep。迟到回包用例采用确定性调度，以精确控制回包顺序。
- 同一文件覆盖非 OAuth、unauthenticated、expired、revoked 与 HTTP 401 的列表状态。
- 已有 `tests/test_kimi_server_client_unit.py` 覆盖 `/api/v1/auth`、`/api/v1/oauth/userinfo`、`/api/v1/oauth/usage` 三个只读客户端端点，本轮无需重复添加端点测试。

### 验证边界

- GUI 使用真实 wx 控件与真实后台线程；Kimi 服务响应由替身提供，不验证真实 Kimi 账号或网络服务。
- `tests/test_chat_information_ui_automation.py` 与 `tests/test_kimi_server_client_unit.py`：112 passed。真实 provider 验收仍需发布环境完成。

## 2026-09-28 — 聊天信息只读 Live 验证

- 新增 `tests/test_chat_information_live_readonly.py`，仅在 `CHAT_INFORMATION_LIVE_TEST=1` 时运行。真实 Kimi 客户端只读 `/api/v1/auth`、OAuth userinfo/usage；authenticated 时要求有效 userId、`kind=ok` 额度及至少一个有效窗口比例，并与真实 wx 行比对。非 OAuth/未登录状态逐项比对行文案。只有显式设置 `KIMI_LIVE_SESSION_ID` 才读取该既有 session 的 status/snapshot，并核对 context/total 行；不枚举或创建 session。Codex 测试走真实 `ChatFrame → worker → app-server → UI` 回包路径，核对账号行和周额度类别/百分比。两项测试均检查焦点，不输出凭据或原始账号 payload。
- 本机 opt-in 实测：**2 passed**。Kimi managed OAuth 状态为 **authenticated**，真实额度行与有效比例匹配；Codex 周额度行显示 **剩余百分比**。未设置 `KIMI_LIVE_SESSION_ID`，因此 session status/snapshot 分支未运行。
- 未设置 opt-in 标志的相关串行 GUI/client 回归：**116 passed、2 skipped**。本轮未运行会发送真实 prompt 的 `tests/test_kimi_live_smoke.py`，也未发送 prompt、创建或删除 session。
- 覆盖边界：验证本机真实只读账号/额度接口及 GUI 投影；未验证付费推理、任务执行、session 可用性或其他设备的登录状态。
