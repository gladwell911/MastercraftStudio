# 当前交接

## 快照（2026-09-28）

Codex/Kimi 聊天信息 Story 1.1–1.5 已分别提交为 `703a4b7`、`2c1c106`、`454037f`、`68082ce`、`5791a98`。后续补充了确定性 GUI E2E、Kimi 额度刷新修复和只读 Live 测试。桌面端可从顶部“应用(&A)”菜单打开当前 Codex/Kimi 聊天的信息列表。此前执行可视投影和 `execution_page_v3` 冻结分页的代码基线为 `130581f`；手机端配套提交和修订为 `ce8e73f`、`edab290`、`07d9dce`、`9366527`。

## 已完成

- `execution_projection.py` 抽出桌面执行内容的 Kimi 生命周期折叠、问答上下文、可见性过滤、文案和稳定行身份；桌面列表与远程 v3 行复用这些规则。
- `ChatStore` 的 canonical 步骤替换改为单事务，轮次、修订版和步骤从同一读事务获取。v3 将可见行写入不可变 SQLite 快照，按索引分页并校验 chat/revision/有效期；旧 V2 durable fact 接口保留。
- v3 的数据库读取、投影和快照创建在 NATS worker 执行；wx 主线程只捕获与复核有界 owner 状态。活动轮尚未持久化时安全返回待同步状态，手机端有限重试并有手动重试入口。
- 先前 Kimi F1 思考叙述与答案恢复加固（`fbe2325`、`c1ef0c0`）仍在当前树；其测试和历史限制见相应规格。
- 聊天信息列表支持方向键浏览和 Esc 关闭后的焦点恢复，仅对 Codex/Kimi 聊天启用；文本不变时不重绘或移动选择。Codex 上下文来自最近一次 `last` 用量，会话累计来自 `total`，主 `codex` 周额度通过后台 worker 查询并显示剩余比例和本地重置时间。
- Kimi 上下文来自实时 status，缺窗口时显示未知；会话累计仅取 snapshot 的 `session.usage.input_tokens + output_tokens`。后台 status 用于恢复，不覆盖更新的实时事件；旧 session 事件与异步旧回包不能写入当前聊天。Codex 清空、切 thread 或切账号后同样拒绝迟到 token/ack。
- Story 1.4 接入 Kimi OAuth 五小时和七日额度：后台读取 auth、userinfo 和 usage，按认证状态及 userId 绑定结果；五小时显示本地重置时刻，七日显示剩余时长，缓存注明上次更新时间。窗口可见时低频更新额度，旧聊天/session/账号或过期请求结果不写入当前面板。
- Story 1.5 共用 Kimi 实时事件与 REST status 的上下文值解析器，移除 REST 路径构造临时事件；Codex 定时器不再做请求前的列表刷新。其余映射和 session rebind 路径保留原有独立语义。
- 打包版常用命令改为读取个人版 OneDrive 根目录下的 `OneDrive\code\data\sj\common_commands.json`，源码运行仍读 `dist\history`。本机旧文件的 5 条命令已复制到 OneDrive 目标文件且哈希一致；旧文件保留。若打包版未检测到 OneDrive 环境变量或目标文件尚未同步，启动时提示并停止，不会静默使用旧文件。此改动尚未重新打包，当前已安装的 MC 不受影响。

## 验证状态

- v3 投影、ChatStore 事务和游标、NATS 路由的定向测试通过；桌面 Codex/Kimi 相关 wx UI 自动化与慢数据库 UI 响应测试通过。
- 手机端完整 `widget_test.dart` 144/144 通过；`integration_test/remote_send_state_refresh_e2e_test.dart` 在 Android 15 模拟器 2/2 通过，但远程服务使用确定性替身，不证明公网 NATS。
- 2026-09-27 Local 跨端回归全阶段通过：隔离 strict-V2 桌面 harness、手机聊天列表、Codex/Kimi 往返与精确 provider 路由。
- 本阶段未运行真实模型请求、公网跨端 Live、正式打包和实体设备 TalkBack。2026-09-14 的 APK/Live 记录不包含本次两端提交，不能作为当前发布证据。
- 2026-09-26 Kimi F1 相关宽套件有既有失败基线；本次局部验证不能表述为全部 MC 测试通过。
- 聊天信息 Story 1.1 定向验证 189 项通过；Story 1.2 定向验证 197 项通过；Story 1.3 定向验证 226 项通过，另将 5 项已确认的旧 Kimi 执行摘要基线失败排除后，Kimi integration 87 项通过。各结果属于对应提交树，不能相加描述为同一树的全量通过。
- Story 1.4 已提交为 `68082ce`，Story 1.5 已提交为 `5791a98`。Story 1.5 独立 engineer 验证 141 项通过、1 项已知基线排除，`py_compile` 和 `git diff --check` 通过；President 审查未发现阻塞项。实现与审查记录见对应票据计划；这些定向结果不能替代完整套件。
- 当前宽回归有 6 项已知基线排除，不能把排除后的通过结果称作完整套件通过。
- 2026-09-28 修复 Kimi 额度刷新后，聊天信息 GUI 与客户端定向测试 116 项通过；显式启用的只读 Live 测试 2 项通过，访问已登录的 Kimi OAuth/usage 和 Codex app-server 账号/周窗。默认运行时这 2 项跳过。只读 Live 不发送模型请求，未覆盖 Kimi 历史 session 分支；Kimi integration 的 5 项旧执行摘要失败经停用新增信息查询后仍复现，完整 MC 宽套件不能视为通过。
- 常用命令 OneDrive 路径、缺少环境变量、目标文件未同步与启动提示的 5 项定向测试通过；现有常用命令存储、GUI 与 E2E 回归 31 项通过。

## 下一步

1. 下次打包 MC 后在两台电脑验证常用命令读取同一 OneDrive 文件；另一台电脑须先确认 `OneDrive\code\data\sj\common_commands.json` 已同步。Story 1.1–1.5 开发已完成，进入发布验证；两项实现和数据契约见 `_bmad-output/initiative-chat-information/epic-codex-kimi-chat-information/` 与 `_bmad-output/specs/spec-codex-kimi-chat-information/data-contract.md`。
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
