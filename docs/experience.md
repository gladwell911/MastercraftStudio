# 可复用经验

## Kimi 流式完成的验证边界

将 Kimi 最终答案的发布条件建模为 owner、主代理身份、流完整性和权威终态的交集；音效与同一 finalized owner 绑定为 exactly-once。这样同聊天仍有排队任务时，已完成的回答可正常结算，而失败、子代理消息和不完整 delta 不会误触发完成。

验证应分三层：协议映射/客户端单元测试、跨聊天集成与 wx 焦点回归、显式 opt-in 的真实 Kimi smoke。前两层可在无凭据环境稳定运行；第三层会创建外部任务，不能把“默认跳过”表述为已完成 live 验收。

## 多聊天恢复的状态边界

Kimi 的 `turn_id` 只在 session 内有意义。owner、alias、提交意图、早到事件、缓冲流和恢复代次必须同时保留 `chat_id`、`session_id`、`prompt_id`（及 stream/offset）；`/clear` 和运行态 rebuild 必须一起清理这些结构，才能防止迟到事件污染新聊天。

## 无障碍流式 UI

后台事件只在可见状态确有变化时批量交付 UI。F1 的列表项应来自结构化协议摘要，而非模型的原始思考或工具文本；执行列表增量更新时保留选择和焦点，避免屏幕阅读器导航被重建打断。

## Git 收尾安全门

提交前逐项审查 staged diff；推送前必须有唯一、明确的 GitHub push URL 和当前分支上游。缺失 upstream 时可保留本地提交，但不应修改 Git 配置、猜测目标分支或使用裸 `git push`。

## 打包与进程通信

PyInstaller 冻结后的 GUI 不应以 `sys.executable -m` 启动后台 worker；应使用同目录的独立控制台 worker。JSON Lines 的 stdin/stdout/stderr 固定 UTF-8，启动等待 `ready`，异常退出保留 stderr 与退出码。

## 网络与端口验证

端口监听不等于移动端可用：NATS 应使用 token 完成 `CONNECT` 与 `PING/PONG` 验证，并兼容 `INFO`、`+OK`、`PING` 混合返回。选择端口时使用生产回退常量；Windows socket 绑定使用 `SO_EXCLUSIVEADDRUSE`，并处理检查与 bind 之间的竞争。

## 环境、远端模型与现场证据

项目目标为 Python 3.11；虚拟环境解释器失效时用 `py -3.11 -m venv .venv` 重建并运行依赖检查。模型提供方按规范化模型 ID 分派，不按传输来源决定。若现场数据库已标记完成而 `/messages` 有完整正文，应保留两边的去敏证据，再复现客户端路由或完成处理问题。
