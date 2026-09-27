# 当前交接

## 快照（2026-09-27）

Codex/Kimi 聊天信息 Story 1.1–1.3 已分别完成并提交为 `703a4b7`、`2c1c106`、`454037f`。桌面端可从顶部“应用(&A)”菜单打开当前 Codex/Kimi 聊天的信息列表；Story 1.4 的 Kimi 账号额度与 Story 1.5 的整理仍待实施。此前执行可视投影和 `execution_page_v3` 冻结分页的代码基线为 `130581f`；手机端配套提交和修订为 `ce8e73f`、`edab290`、`07d9dce`、`9366527`。

## 已完成

- `execution_projection.py` 抽出桌面执行内容的 Kimi 生命周期折叠、问答上下文、可见性过滤、文案和稳定行身份；桌面列表与远程 v3 行复用这些规则。
- `ChatStore` 的 canonical 步骤替换改为单事务，轮次、修订版和步骤从同一读事务获取。v3 将可见行写入不可变 SQLite 快照，按索引分页并校验 chat/revision/有效期；旧 V2 durable fact 接口保留。
- v3 的数据库读取、投影和快照创建在 NATS worker 执行；wx 主线程只捕获与复核有界 owner 状态。活动轮尚未持久化时安全返回待同步状态，手机端有限重试并有手动重试入口。
- 先前 Kimi F1 思考叙述与答案恢复加固（`fbe2325`、`c1ef0c0`）仍在当前树；其测试和历史限制见相应规格。
- 聊天信息列表支持方向键浏览和 Esc 关闭后的焦点恢复，仅对 Codex/Kimi 聊天启用；文本不变时不重绘或移动选择。Codex 上下文来自最近一次 `last` 用量，会话累计来自 `total`，主 `codex` 周额度通过后台 worker 查询并显示剩余比例和本地重置时间。
- Kimi 上下文来自实时 status，缺窗口时显示未知；会话累计仅取 snapshot 的 `session.usage.input_tokens + output_tokens`。后台 status 用于恢复，不覆盖更新的实时事件；旧 session 事件与异步旧回包不能写入当前聊天。Codex 清空、切 thread 或切账号后同样拒绝迟到 token/ack。

## 验证状态

- v3 投影、ChatStore 事务和游标、NATS 路由的定向测试通过；桌面 Codex/Kimi 相关 wx UI 自动化与慢数据库 UI 响应测试通过。
- 手机端完整 `widget_test.dart` 144/144 通过；`integration_test/remote_send_state_refresh_e2e_test.dart` 在 Android 15 模拟器 2/2 通过，但远程服务使用确定性替身，不证明公网 NATS。
- 2026-09-27 Local 跨端回归全阶段通过：隔离 strict-V2 桌面 harness、手机聊天列表、Codex/Kimi 往返与精确 provider 路由。
- 本阶段未运行真实 provider、公网 Live、正式打包和实体设备 TalkBack。2026-09-14 的 APK/Live 记录不包含本次两端提交，不能作为当前发布证据。
- 2026-09-26 Kimi F1 相关宽套件有既有失败基线；本次局部验证不能表述为全部 MC 测试通过。
- 聊天信息 Story 1.1 定向验证 189 项通过；Story 1.2 定向验证 197 项通过；Story 1.3 定向验证 226 项通过，另将 5 项已确认的旧 Kimi 执行摘要基线失败排除后，Kimi integration 87 项通过。各结果属于对应提交树，不能相加描述为同一树的全量通过。
- 聊天信息未运行真实 Codex app-server 账号/周窗或真实 Kimi 服务的端到端验收。Kimi integration 的 5 项旧执行摘要失败经停用新增信息查询后仍复现；完整 MC 宽套件不能视为通过。

## 下一步

1. 从 `_bmad-output/initiative-chat-information/epic-codex-kimi-chat-information/tickets.toml` 的 Story 1.4 继续：接入 Kimi OAuth 五小时/七日额度与可见窗口刷新；之后实施 Story 1.5。数据契约在 `_bmad-output/specs/spec-codex-kimi-chat-information/data-contract.md`。
2. 原有跨端发布前仍需在具备真实配置的环境运行 `D:\code\sj\rc\scripts\run_cross_client_regression.ps1 -Mode Live`，重新打包 MC，并验收 Kimi F1、长回合恢复和手机 v3 执行页。
3. 在实体 Android 设备上验收 TalkBack、后台通知和 ARM64 安装；历史 Kimi 宽套件失败需按旧基线逐项复核。

## 不要重复踩坑

- 不要将 V2 `durable_facts` 直接当作桌面可视执行列表；v3 的 canonical 步骤和快照是独立投影，旧协议仍需兼容。
- 不要在 wx 主线程全量读取/哈希/投影历史；NATS worker 做重工作，UI 只捕获和复核少量 owner 状态。
- Kimi 模型文本来自 REST `/messages`，事件流只用作活动触发；中途思考同步不能挂在只于回合完成时运行的恢复 worker 上。
- 不要把定向通过或旧 APK/Live 记录写成当前全量验收；发布证据须来自同一最终树。
- Codex 的普通 `turn/start` 在活跃 turn 上可被 Core 当作 steer；“发出新请求”不能推断为“开启独立任务”。聊天信息不得将累计 `total` 当作当前上下文 `last`。
- Kimi status 恢复查询可能晚于实时事件返回；只在请求后没有更新的实时 status 时应用 REST 状态。历史 session 的 status 可用于历史执行路由，但不能更新当前聊天上下文。
- 本机 BMad 票据 CLI 位于 `C:\Users\gladwell\.agents\skills\bmad-preview-ticketing\scripts\tickets.py`；其外部修复使用 ticket ID 防止中文标题压缩后计划路径碰撞，不在 MC Git 提交内。换机时先核对 `find` 返回的票据 ID 和 plan 路径。
