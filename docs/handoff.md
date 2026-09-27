# 当前交接

## 快照（2026-09-27）

桌面端 `main` 当前代码提交为 `130581f`，新增与手机端同源的执行可视投影和 `execution_page_v3` 冻结分页。手机端配套代码已在 `ce8e73f`，后续修订刷新竞态、发送后状态刷新与模拟器 E2E 分别在 `edab290`、`07d9dce`、`9366527`。工作区 `_bmad-output/` 不是 Git 仓库，实施计划和测试摘要留在本地。

## 已完成

- `execution_projection.py` 抽出桌面执行内容的 Kimi 生命周期折叠、问答上下文、可见性过滤、文案和稳定行身份；桌面列表与远程 v3 行复用这些规则。
- `ChatStore` 的 canonical 步骤替换改为单事务，轮次、修订版和步骤从同一读事务获取。v3 将可见行写入不可变 SQLite 快照，按索引分页并校验 chat/revision/有效期；旧 V2 durable fact 接口保留。
- v3 的数据库读取、投影和快照创建在 NATS worker 执行；wx 主线程只捕获与复核有界 owner 状态。活动轮尚未持久化时安全返回待同步状态，手机端有限重试并有手动重试入口。
- 先前 Kimi F1 思考叙述与答案恢复加固（`fbe2325`、`c1ef0c0`）仍在当前树；其测试和历史限制见相应规格。

## 验证状态

- v3 投影、ChatStore 事务和游标、NATS 路由的定向测试通过；桌面 Codex/Kimi 相关 wx UI 自动化与慢数据库 UI 响应测试通过。
- 手机端完整 `widget_test.dart` 144/144 通过；`integration_test/remote_send_state_refresh_e2e_test.dart` 在 Android 15 模拟器 2/2 通过，但远程服务使用确定性替身，不证明公网 NATS。
- 2026-09-27 Local 跨端回归全阶段通过：隔离 strict-V2 桌面 harness、手机聊天列表、Codex/Kimi 往返与精确 provider 路由。
- 本阶段未运行真实 provider、公网 Live、正式打包和实体设备 TalkBack。2026-09-14 的 APK/Live 记录不包含本次两端提交，不能作为当前发布证据。
- 2026-09-26 Kimi F1 相关宽套件有既有失败基线；本次局部验证不能表述为全部 MC 测试通过。

## 下一步

1. 发布前在具备真实配置的环境中运行 `D:\code\sj\rc\scripts\run_cross_client_regression.ps1 -Mode Live`。
2. 重新打包 MC 并验收 Kimi F1 思考叙述、长回合答案恢复及手机 v3 执行页与桌面同聊天同轮次的行内容和顺序。
3. 在实体 Android 设备上验收完整 TalkBack、后台通知和 ARM64 安装。历史 Kimi 宽套件失败应按旧基线逐项复核，不能简单归入本次回归。

## 不要重复踩坑

- 不要将 V2 `durable_facts` 直接当作桌面可视执行列表；v3 的 canonical 步骤和快照是独立投影，旧协议仍需兼容。
- 不要在 wx 主线程全量读取/哈希/投影历史；NATS worker 做重工作，UI 只捕获和复核少量 owner 状态。
- Kimi 模型文本来自 REST `/messages`，事件流只用作活动触发；中途思考同步不能挂在只于回合完成时运行的恢复 worker 上。
- 不要把定向通过或旧 APK/Live 记录写成当前全量验收；发布证据须来自同一最终树。
