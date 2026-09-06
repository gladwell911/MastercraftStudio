# 可复用经验

## 打包的后台 Worker

- PyInstaller 冻结后的 GUI 可执行程序不应再用 `sys.executable -m 模块` 启动后台 worker；这会重启 GUI 入口并可能触发单实例退出。应打包独立的控制台 worker，并由主程序调用同目录的 worker EXE。
- 父子进程间使用 JSON Lines 时，worker 必须在入口处显式将 stdin、stdout 和 stderr 配置为 UTF-8；Windows 控制台代码页不能作为协议编码的依据。
- 启动时等待 worker 的 `ready` 事件，异常退出时保留 stderr 与退出码，可把模糊的 `Broken pipe` 转化为可诊断的启动错误。

## 固定域名的 NATS 连通性验收

- 仅检测 TCP 端口已监听不足以证明移动端可用。应使用 token 发送 NATS `CONNECT` 和 `PING`，并验证本地与公开域名均返回 `PONG`。
- 解析探针返回时需容忍服务端的 `INFO`、`+OK`、`PING` 与 `PONG` 在同一次读取中出现；服务端主动 `PING` 时应立即回 `PONG`。
- Windows 上已有 cloudflared 服务时，应修正并重启该服务，避免再启动一个相同 tunnel 的临时连接器造成流量随机落到旧 origin。

## Windows 端口回退

- 选择默认端口前先考虑系统排除端口范围；文件服务在默认端口不可用时应按确定顺序尝试高位回退端口。
- Windows socket 绑定使用 `SO_EXCLUSIVEADDRUSE`；真正创建 HTTP server 时仍要捕获 bind 失败并继续尝试下一个候选端口，避免检查与绑定之间的竞争窗口。

## 流式 UI 对账

- 屏幕阅读器桌面客户端的 token 流只应更新变化的回答区域。规范模型没有可见增量时，避免完整列表替换、选择变化或前台重绘。
- 当持久化历史与内存事件重叠时，按稳定执行标识去重，并按持久化时间戳排序后刷新 UI。

## Kimi Code WebSocket 生命周期

- Kimi Code 0.38 使用应用层 JSON 心跳；收到每个 `ping` 时，用相同 nonce 回 `pong`，不产生 UI 事件。
- 空 WebSocket 读取或 CLOSE 均为终止状态。下次控制发送前使 socket 失效，并有界重连、重复 `client_hello` 和订阅恢复，避免复用已关闭连接。
- 使用心跳、关闭、重连和 socket-swap 的确定性单测验证；发布前仍需 opt-in 的 `KIMI_LIVE_TEST=1` 空闲冒烟，因为服务端时序属于协议契约。

## Python 虚拟环境恢复

- 本桌面项目目标为 Python 3.11。若 `.venv/pyvenv.cfg` 指向缺失解释器，应并行安装 Python 3.11 并用 `py -3.11 -m venv .venv` 重建；不要编辑配置文件硬改旧环境。
- 恢复 `requirements.txt` 和 `requirements-dev.txt` 后，用 `.venv\Scripts\python.exe -m pip check` 与定向 pytest 验证，再依赖该环境。

## 远端模型分派与聊天隔离

- 提供方选择必须由规范化模型 ID 决定，不能由 `local`、`remote-ws` 等传输来源决定。否则手机端 `kimi/*` 容易误落入 OpenRouter，并以 401 掩盖真实路由错误。
- 可继续接收用户输入的 CLI 客户端是聊天级状态。保存客户端引用时同时保存 owner `chat_id`，发送前校验归属，清理时用客户端对象身份防止旧 worker 清掉替换客户端。
- 远端路由测试至少覆盖“聊天 A 的交互客户端活跃，同时聊天 B 发送消息”，并断言 A 未收到输入、B 创建 turn、目标专用 worker 启动且非目标凭据未读取。

## 自动化测试分层

- 方法级入口测试、进程内协议路由 E2E、真实 NATS Server、Android 模拟器和真实模型服务是不同层级；测试汇报必须逐层说明，不能把前两层表述成真机全链路。
- 默认 CI 使用无凭据、无网络的稳定 E2E；真实手机与在线服务测试应标记为 opt-in，并在发布前单独执行。
- 端口优先级测试应引用 `REMOTE_NATS_PORT_FALLBACKS` 等生产常量，而不是复制具体回退端口，避免策略调整后测试仍断言旧值。

## Kimi 并发会话身份

Kimi 的 `turn_id` 仅在单个 session 内唯一。路由、增量缓存和完成清理应统一使用
`(chat_id, session_id, turn_id)`；事件包含 `session_id` 时禁止跨 session 的 turn 回退。
这能避免不同聊天都从 `turn_id=0` 开始时互相吞掉事件。修改这类逻辑时，至少验证两个
session 的同号 turn 交错到达，以及后台聊天不抢前台焦点。

## 用现场证据定位客户端事件丢失

当现场数据库显示 `done` 但回答仍是“正在请求...”，而 Kimi 服务端 `/messages` 已有完整
答案时，可据此判定服务端生成正常、问题在客户端事件路由或完成处理。应同时保留数据库
状态和服务端消息证据，再用同号 turn 的可控测试复现。

## Windows 桌面端打包

本项目的 `.venv` 损坏时，可使用本机 Python 3.11.5 与 PyInstaller 6.19，并通过
`package_mc.ps1 -PythonExe <绝对路径>` 显式指定解释器。目标产物与用户历史目录相邻，
打包前后必须对 `D:\code\cx\history` 做指纹保护。`imageio_ffmpeg`、`pyaudio` 的未引用
optional hidden-import 警告可记录，但不应在隔离启动正常时视为阻塞。
