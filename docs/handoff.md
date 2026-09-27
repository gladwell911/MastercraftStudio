# 当前交接

## 快照（2026-09-27）

桌面端执行可视投影和 `execution_page_v3` 冻结分页的代码基线为 `130581f`；手机端配套提交和后续修订分别为 `ce8e73f`、`edab290`、`07d9dce`、`9366527`。本轮已将 Codex/Kimi 聊天信息的规格、数据契约和五项任务提交到 MC 仓库（`036b14f`）；功能代码尚未开始实施。

## 已完成

- `execution_projection.py` 抽出桌面执行内容的 Kimi 生命周期折叠、问答上下文、可见性过滤、文案和稳定行身份；桌面列表与远程 v3 行复用这些规则。
- `ChatStore` 的 canonical 步骤替换改为单事务，轮次、修订版和步骤从同一读事务获取。v3 将可见行写入不可变 SQLite 快照，按索引分页并校验 chat/revision/有效期；旧 V2 durable fact 接口保留。
- v3 的数据库读取、投影和快照创建在 NATS worker 执行；wx 主线程只捕获与复核有界 owner 状态。活动轮尚未持久化时安全返回待同步状态，手机端有限重试并有手动重试入口。
- 先前 Kimi F1 思考叙述与答案恢复加固（`fbe2325`、`c1ef0c0`）仍在当前树；其测试和历史限制见相应规格。
- 聊天信息规划已确认顶部“应用(&A)”菜单入口、上下文与累计 token 的不同口径、Codex 主 `codex` 周额度及 Kimi 五小时／七日额度，任务树中 Story 1.1 是当前首个可实施项。

## 验证状态

- v3 投影、ChatStore 事务和游标、NATS 路由的定向测试通过；桌面 Codex/Kimi 相关 wx UI 自动化与慢数据库 UI 响应测试通过。
- 手机端完整 `widget_test.dart` 144/144 通过；`integration_test/remote_send_state_refresh_e2e_test.dart` 在 Android 15 模拟器 2/2 通过，但远程服务使用确定性替身，不证明公网 NATS。
- 2026-09-27 Local 跨端回归全阶段通过：隔离 strict-V2 桌面 harness、手机聊天列表、Codex/Kimi 往返与精确 provider 路由。
- 本阶段未运行真实 provider、公网 Live、正式打包和实体设备 TalkBack。2026-09-14 的 APK/Live 记录不包含本次两端提交，不能作为当前发布证据。
- 2026-09-26 Kimi F1 相关宽套件有既有失败基线；本次局部验证不能表述为全部 MC 测试通过。
- 本轮仅对聊天信息规格和任务依赖做了静态检查：`tickets.py status` 显示 5 项 planned、无未固定依赖；未运行功能测试或真实服务验收。

## 下一步

1. 用 `$bmad-build` 实施 `_bmad-output/initiative-chat-information/epic-codex-kimi-chat-information/tickets.toml` 中的 Story 1.1；之后按 1.2→1.3→1.4→1.5 的依赖推进。规格在 `_bmad-output/specs/spec-codex-kimi-chat-information/`。
2. 原有跨端发布前仍需在具备真实配置的环境运行 `D:\code\sj\rc\scripts\run_cross_client_regression.ps1 -Mode Live`，重新打包 MC，并验收 Kimi F1、长回合恢复和手机 v3 执行页。
3. 在实体 Android 设备上验收 TalkBack、后台通知和 ARM64 安装；历史 Kimi 宽套件失败需按旧基线逐项复核。

## 不要重复踩坑

- 不要将 V2 `durable_facts` 直接当作桌面可视执行列表；v3 的 canonical 步骤和快照是独立投影，旧协议仍需兼容。
- 不要在 wx 主线程全量读取/哈希/投影历史；NATS worker 做重工作，UI 只捕获和复核少量 owner 状态。
- Kimi 模型文本来自 REST `/messages`，事件流只用作活动触发；中途思考同步不能挂在只于回合完成时运行的恢复 worker 上。
- 不要把定向通过或旧 APK/Live 记录写成当前全量验收；发布证据须来自同一最终树。
- Codex 的普通 `turn/start` 在活跃 turn 上可被 Core 当作 steer；“发出新请求”不能推断为“开启独立任务”。当前 Codex 用量解析还可能把累计 token 当作上下文占用，实施 Story 1.1 时先核对协议字段。
