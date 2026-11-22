---
title: '修复 MC 与 RC 文件分享审查发现'
type: 'bugfix'
ticket: ''
created: '2026-11-22'
status: 'built'
baseline_revision: 'NO_VCS'
baseline_revisions:
  mc: '3b85215571305bda84f983c182f9efb9322f9b02'
  rc: 'd8636d912939d87013ca80d6900c2943a05b7282'
route: 'full'
route_source: 'pinned'
review: 'none'
review_source: 'pinned'
lenses_ran: []
review_loop_iteration: 0
followup_review_recommended: false
context:
  - 'D:/code/sj/mc/AGENTS.md'
  - 'D:/code/sj/rc/AGENTS.md'
  - 'D:/code/sj/mc/_bmad-output/implementation-artifacts/mc-rc-kimi-voice-share-implementation-plan.md'
  - 'D:/code/sj/mc/_bmad-output/implementation-artifacts/review-mc-rc-kimi-voice-share-dog.md'
warnings: ['oversized']
deferred: []
---

<intent-contract>

## Intent

修复新的 Dog 在本轮代码审查中确认的产品缺陷。保留已验的 Kimi 累计、两侧 Ctrl、Android 分享去向与中文文件传输。使用真实的协议编码、接收和模型附件构建路径验证修复。

## Boundaries & Constraints

MC 和 RC 是独立仓库，工作区根没有 Git。保留现有产品改动、RC 基线提交及 MC 的本地 AGENTS.md 私人改动。采用报告中可验证的最小修法。日常 MC、实体 RC 和日常连接不用于自动化发送或部署。支持旧 MC 的离线文件管理用途。报告中的已知基线失败和现场设备条件按实记录。

</intent-contract>

## Code Map

- `mc/remote_nats.py`、`mc/mc_frame_remote_routes.py`、`mc/mc_frame_requests.py` -- 已协商安装身份、远程发送及 worker 启动。
- `mc/file_transfer.py`、`mc/mc_frame_files.py`、`mc/chat_store.py` -- 传输准备、文件占位与提交、按聊天归属的持久数据。
- `rc/lib/remote_nats_chat_service.dart`、`rc/lib/share_draft.dart`、`rc/lib/file_library.dart` -- 线上编码、草稿与完成回执、文件库。
- `rc/android/app/src/main/kotlin/com/example/zhuge_qa/SharedFileInbox.kt`、`MainActivity.kt` -- 共享 URI 接收与批次持久化。

## Tasks & Acceptance

- [x] `mc/remote_nats.py`、`mc/mc_frame_files.py`、`mc/mc_frame_requests.py` -- 按 RC 实际线上形态读取归属字段并忽略客户端伪造的内部来源，真实 Codex/Kimi 与 OpenRouter Claude 附件构造器接收规范化成功附件。对未真正支持文件的模型明确反馈并保留草稿。
- [x] `mc/mc_frame_remote_routes.py`、`mc/mc_frame_requests.py`、`mc/chat_store.py`、`rc/lib/share_draft.dart` -- 以已协商来源、聊天和发送 ID 持久确认同一份提交。冻结已发起的目标、指令与逻辑文件列表，阻止确认丢失和清空组合导致复传到新代次。worker 拒绝或启动失败不得提前公开为成功用户轮次。
- [x] `mc/file_transfer.py`、`mc/mc_frame_files.py` -- 同名并发预约全路径唯一，按可信记录和令牌校验，原子发布完整字节。重试不得破坏已完成文件。处理完成确认时校验范围、长度、记录状态。保持普通上传下载兼容。
- [x] `rc/android/app/src/main/kotlin/com/example/zhuge_qa/SharedFileInbox.kt`、`MainActivity.kt`、`rc/lib/share_draft.dart`、`rc/lib/file_library.dart` -- 按报告修复批次发布和重复、主线程复制、多个草稿可恢复选择、保存时幂等与重启清理。保留本地副本直到已持久的下一状态接管。按已证实的 Android Intent 和 MIME/权限边界处理。
- [x] `mc/kimi_server_v2.py`、`mc/mc_frame_chat_information.py` 等对应报告文件 -- 修复审查确认的额外问题，验证真实受影响入口，不扩展已明确归为既有或现场待验的项目。

### Acceptance Criteria

- Given 真实 RC 编码的附件命令，when MC 收到经协商的消息并准备生成，then 文件上传、来源限制和相应模型原生构造器都使用该文件，拒绝不匹配来源、聊天或不支持附件的模型。
- Given 相同名称的普通文件和聊天附件或者重复 PUT，when 一方慢写或失败并重试，then 已成功文件仍保留完整内容，各请求不会串路径或错误鉴权。
- Given 聊天发送回执丢失，when 用户重开草稿、重复发送或清空原聊天后重试，then 已受理请求只确认原轮或拒绝失效请求，永远不创建修改文字或新代次中的第二轮。给定 worker 同步拒绝，手机与电脑均无幽灵轮次。
- Given Android 共享多文件、收件重放、进程中断或待处理多份草稿，when 重开 RC、重试保存或继续选聊天，then 批次归属和字节完整，已完成动作不重复，另一份有效草稿仍可独立选择，完成的副本可清理。

## Implementation Notes

## Plan Change Log

## Review Triage Log

## Verification

- MC 项目 venv 运行新增定向重现与复验，覆盖 RC 序列化用例、实际附件构造、HTTP 完整写入、回执、清空与失败投影。复跑受影响的 Kimi、Alt+Y、Ctrl 测试。
- RC 运行相关 widget/协议和 HTTP 测试及定向 `flutter analyze --no-pub`。构建并复验 Android 收件箱，优先保留原生有效 URI、冷暖、离线恢复及两个中文 basename/hash 的隔离证据。
- 保留前一轮通过记录，按实际代码触达范围重验。明确失败命令和未验场景。两仓检查差异空白，避免暂存私人 AGENTS.md 和诊断产物。

## Auto Run Result

已完成首轮外部代码审查的 R1–R11 修复和独立复验。MC 修正 V2 附件范围与请求构造、原子上传、SQLite 回执、清空及拒绝时的投影、Kimi 脱敏失败诊断和双 Ctrl 回归。RC 修正后台批次收件、进程恢复、草稿提交冻结、通知导航与幂等文件库导入。

- Engineer 最终 MC 定向 30/30，包含真实隔离 Kimi runtime、三模型构造、HTTP、诊断、键盘和语音首段。修正了 Claude 测试夹具意外走真实联网预判的隔离问题。最终轮原生前台测试未获得记事本/Edge 焦点，前一轮已通过，实际物理按键和云语音待验。
- Engineer 最终 RC 定向 9/9，宽套件过滤既有 owner rebind 失败后 179/179，11 个相关 Dart 文件分析通过，两仓差异检查无错误。
- 全新隔离 Android AVD 原生收件箱 6/6。Android 系统文件应用实际外部分享的冷启动及 RC 进程运行中入口均显示去向并落库为原字节。真实 RC 原文通过 NATS、HTTP 和文件库保存两个中文名，大小、原始文件名和 SHA-256 一致。
- 隔离 AVD 的 RC 确认按钮走真实 NATS/HTTP，第二份文件首次 PUT 注入 503，首次成功提交回执丢弃，两次重试最终在选中聊天只提交一轮，另一聊天为零。真实 Codex 构造器收 text 与两个 localImage，托管文件 SHA-256 一致。模型服务由替身截取，原生入站由上一项单独验证。
- 实际微信 VIEW/data 形态未取得证据，保留 SEND/SEND_MULTIPLE 支持。实体微信、TalkBack、物理 Ctrl 和日常 Kimi 会话待现场验收。MC 若在 worker 接受与回执持久化间退出，原请求保持待确认并拒绝再次启动，RC 可继续离线保存。
- 两台本轮 AVD 已关闭，临时 AVD 数据已清理。Android、上传及中文提取日志留于 `%TEMP%/sj-rc-fresh-avd-408e85e06b5d4664871ed7a3643d4ddf`。日常包保持原样。
