# 当前交接

更新日期：2026-10-26。源码 `D:/code/sj/mc`，`main` 跟踪 `origin/main`。本阶段目标是恢复手机系统通知，并核实此前源码修复是否进入日常安装包；本次收尾只同步文档，不再改产品代码或打包。

## 当前部署

- 通知流修复提交为 `cd07002`。此前源码已有修复，但旧日常 MC 与手机 APK 均未包含该实现；旧手机基线 186475 高于实际新事件序列，事件在系统通知 gate 前即被标 terminal 并 ACK。
- 用户于 2026-10-26 重新打包并启动 `D:/code/cx/mc/mc.exe`。已检查实际 EXE 的 PYZ 包含 `notification-feed-v1`、`notification_feed_id` 和 feed devices 实现；EXE SHA256 为 `beb2f09617d82106af355eaedb8d2bb46bdf980ba0b80528b827c1e60b1fdee0`。
- 日常聊天库仍为 `D:/code/cx/history/chat_history.db`。手机 default 的 feed 与该库 meta 中的 UUID 一致，首次新流基线为 24725；未因数字下降清库、轮换 UUID 或清理通知账本。
- RC 已用正式双来源配置保数据覆盖安装。日常日志在 18:38:11、18:50:57（北京时间）分别出现 `gate=eligible → notification_posted`，对应 answer_seq 14386、14397；前一条随后因 `read_cursor` 取消。这证明日常实际发布路径生效，不代表本轮已完成日常通知栏、锁屏和点击的完整矩阵。
- 临时测试应用 `com.example.zhuge_qa.notificationprobe` 已卸载，独立测试 MC/broker 已停止，自有 ADB reverse 已移除。最终打开的是日常 `com.example.zhuge_qa`，不是同名测试入口。台式机部署已核实，笔记本未在本轮重新部署或验收。

## 验证范围

- RC 本轮四文件 Dart 定向测试 148 项、Android 原生单元测试 128 项通过。独立完整新 MC 的真实握手成功，包内 ChatStore/RemoteNatsTransport 生成并投递测试终答；这不是实际模型生成验收。
- 实体手机独立 QA 应用验证：186475 旧基线迁移到新流基线 3，桌面后台通知中心实际出现标题和正文；同一次锁屏操作发送前后均 `deviceLocked=true`，系统通知 active；同事件收到两次，仅发布一次。该证据不可替代日常锁屏验收。
- 最初执行 `pytest tests/test_notification_sync.py tests/test_notification_contract_unit.py --noconftest -o pythonpath=. -p no:cacheprovider -q` 得到 22 passed、6 failed。失败均在旧 contract fixture 未设置完成状态时触发 `INCOMPLETE_CANONICAL_NOTIFICATION`；本轮未修改它们，不宣称该组合全绿。
- 本次仅文档更新，复用上述对应源码的测试与构建结果；收尾另做文档路径、差异及敏感信息检查。

## 下一步与限制

1. 日常新消息验收应固定同一 pair/event/answer_seq，分别核收包、gate、posted、系统通知中心及读后取消。保持手机在桌面或锁屏，不能用 ACK 或补拉历史代替可见通知。
2. 手机 2026-10-26 多次被 MIUI `OneKeyClean` 强停，18:28:33 的强停发生在新 broker 启动前；重新打开后才完成新 feed 协商。强停是独立运行条件，不能仅靠本次 feed 修复保证继续接收。
3. 旧事件已被 terminal 去重，升级不自动补弹；不得批量清 posted/terminal/read 账本。数据库备份恢复且沿用同 UUID 的自动识别仍不支持。
4. 2026-10-19 的 TLS、恢复期发送及详情延迟未在本轮统一修复或长期验收；成功的新连接不能据此关闭历史问题。停止的完整 Windows/TalkBack 验收不自动恢复。
5. 既有长期运行风险保持：日志 reader 的受控读写/关闭交错尚未裁决；两个旧基线失败 `test_archived_mobile_result_v2_outbox_keeps_interleaved_owners_and_unique_finals`、`test_registered_history_handler_bounds_escaped_long_content_and_preserves_older_cursor` 不计通过。24–72 小时性能和月级稳定性没有结论。

## 保留的实现与运维约定

- 长期维护的部分索引、独立 publication_progress、安全前沿内有界清理、自有日志轮转/短写恢复、构建产物过滤及笔记启动恢复均保留。历史 242 passed/2 deselected、7 项补丁 51 passed、笔记 UI 7 passed 只对应各自版本；规则及复现见 [维护说明](runtime-maintenance.md)、[协议](remote-slim-protocol.md)和[历史验证](remote-slim-validation.md)。
- 用户本人退出、重包和启动日常 MC。需要隔离运行时使用不同 EXE 父目录/聊天库、随机 token、独立端口和测试 OneDrive；不能接管日常隧道或按进程名批量停止服务。
- default/laptop 的配置、文件与读状态按来源隔离。打包版笔记/常用命令使用既有个人 OneDrive 数据，源码笔记为 `D:/code/note/notes.db`；本轮未更换或合并这些数据。

## 接续证据

私有诊断和验收 JSON 归档在仓库外 `D:/code/sj/_bmad-output/implementation-artifacts/notification-deployment-20261026/`，不提交日志、凭据或 APK。长期入口为 [远程配置](remote-control.md)、[文档索引](README.md)、[经验](experience.md)、[反思](reflection.md)。
