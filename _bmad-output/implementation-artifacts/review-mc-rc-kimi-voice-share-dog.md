# Dog 代码审查与修复输入

审查范围为 MC `3b85215571305bda84f983c182f9efb9322f9b02` 和 RC `d8636d912939d87013ca80d6900c2943a05b7282` 之后的 32 个新增或修改代码及测试文件。基于现有实施计划进行两仓联合审查。保留无关的本地变更。本报告供下一次 bmad-build-auto 实施修复。

## 按依赖顺序修复的已确认问题

| ID | 级别 | 位置与已确认结果 | 最小修法与验收 |
| --- | --- | --- | --- |
| R1 | 高 | [RC 协议](../../../rc/lib/remote_nats_protocol.dart#L238)将 `attachment_send_id` 平铺到顶层，[MC 路由](../../remote_nats.py#L315)只从嵌套 `body` 识别该标记。MC 不附加已协商来源，正常上传请求和完成确认失败。MC 集成测试手工注入嵌套请求及来源。 | 按协议解析器接受的形状识别标记，来源只来自已协商的 V2 RPC 连接。添加使用实际 RC 序列化形式、经 MC 真实 transport 处理的上传和完成用例，同时保留旧命令、无能力和异来源拒绝。 |
| R2 | 高 | [MC 附件解析](../../mc_frame_files.py#L348)只生成 name/path/kind。[Codex](../../mc_frame_codex.py#L899)和 [Kimi](../../mc_frame_kimi_sessions.py#L860)只转发 `status == "success"` 的附件。另有非 Claude 通用 API 目标仅收到桌面路径，缺乏读取该路径的能力，见[请求构造](../../mc_frame_requests.py#L821)和[云端调用](../../mc_frame_requests.py#L1435)。 | 复用附件归一化或生成完整成功字段，经各 provider 的实际 request 构造检查文件引用、图片或受支持内容进入请求。无实际读取能力的目标应在创建轮次前明确拒绝，RC 显示原因并保留草稿及存文件选项。不要用读取托管文件的假 worker 代替 provider 请求断言。 |
| R3 | 高 | [MC 提交](../../mc_frame_requests.py#L830)在尝试启动 worker 前调用 [持久化及投影](../../mc_frame_history.py#L338)，同步启动失败后只重写 turn。已入队或发出的 canonical question 会继续可见，用户重试又可发布一轮。 | 先储存待启动状态时不投影，worker 接受后再发布；或延迟提交至成功边界，确保相关的失败清理也覆盖回执。使用启用 V2 outbox 的真存储测试同步启动拒绝，再重试，断言失败不产生用户问题、最终唯一一次。注意处理成功写入与响应丢失。 |
| R4 | 高 | [MC 回执](../../mc_frame_remote_routes.py#L643)只在历史 turn 中查找，且只匹配 send ID 与来源；[清空](../../chat_store.py#L186)删除 turn。[RC 重试](../../../rc/lib/share_draft.dart#L311)可复传引用到新 revision，且[指令仍可编辑](../../../rc/lib/share_draft.dart#L482)。丢 ACK 后修改指令可误报新指令已发送，清空后可将旧指令重新发送。 | 固化首次提交的目标、指令和附件身份。MC 校验相同 ID 的内容，回执应独立于被清空轮次，或显式固定初次提交代次并在 RC 拒绝跨代重发。已接受回执不能在清空后触发第二轮；内容不一致要有明确冲突。覆盖两台同名聊天、丢 ACK 后编辑与清空、部分上传后重试，观察可见轮次而非仅返回 200。 |
| R5 | 高 | [路径分配](../../file_transfer.py#L380)只在附件申请时避开其它保留路径。普通上传随后可占用相同未落盘名称，[嵌入服务](../../file_transfer.py#L642)按名称选择最新记录，原 URL 失败。[两处 PUT](../../file_transfer.py#L735)在 ACCEPTED 时直写最终路径，并发重试有截断或删除已完成文件的可能。[上传重用](../../mc_frame_files.py#L543)还未校验相同 upload ID 的原名和预期大小。 | 共用路径保留规则，或把附件 PUT 独立按记录 ID 寻址并保护最终路径；写 staging 后在有效占位和状态下原子提交。不可将失败或取消的副本覆盖已完成记录。重复 upload ID 校验原始身份和大小。覆盖普通与附件同名交错、同记录并发重试和取消、两条 PUT 的大小或完整性失败。 |
| R6 | 中 | [Android Activity](../../../rc/android/app/src/main/kotlin/com/example/zhuge_qa/MainActivity.kt#L406)在 `super.onCreate` 前及热启动主线程同步复制分享文件和提交 manifest。大文件或慢 provider 可以拖住 Activity。[收件箱](../../../rc/android/app/src/main/kotlin/com/example/zhuge_qa/SharedFileInbox.kt#L101)每次新 batch ID、逐文件提交；进程中断可能拆批或遗留私有副本，分别发起的同 URI 分享在先前未确认时还可被误去重。 | 在同一串行后台收件入口内持有当前 Intent URI 的读取授权，先稳定一批的交付身份，保留一次批次处理结果并通知 Dart；重复投递识别交付身份，独立分享不能按 URI 当成同批重放。对已落盘但未登记文件保留最小可恢复状态，避免把授权中止写成可复原的文件。验证冷、热实际入口、批次中断或重投、同 URI 独立两批与部分失败。 |
| R7 | 中 | [RC 恢复入口](../../../rc/lib/share_draft.dart#L173)只打开第一份未完成草稿，可能使后续草稿无法恢复。[索引读取](../../../rc/lib/share_draft.dart#L64)缓存异常 Future，导致以后所有收件都失败。[完成清理](../../../rc/lib/share_draft.dart#L271)没有进程重启后的重试，临时副本和指令可长期留存。 | 有多份未完成时提供明确选择。加载失败后允许重试且保留问题数据。已完成但未清理的私有副本在恢复时重试，完成后清除非收据内容，确保未获原生 ACK 时重复投递仍能确认。用新建 HomePage 或进程恢复验证实际索引内容，而不是只关闭同实例路由。 |
| R8 | 中 | [文件管理导入](../../../rc/lib/file_library.dart#L147)先写最终文件后注册稳定 import ID，进程在两步之间中断后 [目录恢复](../../../rc/lib/file_library.dart#L89)生成随机 ID，重试复制第二份。 | 让已提交文件能够按原 import ID 认领，例如以最小操作记录关联预留路径与 import ID，再写和登记。覆盖重开后的原文件认领、重试冲突与命名准确性，保留主文件库既有行为。 |
| R9 | 中 | [RC 通知派发](../../../rc/lib/main.dart#L4313)只在进入时看分享路由，随后 await 提示音或导航。[分享页](../../../rc/lib/share_draft.dart#L417)的提交完成直接弹栈顶。通知已进入再开始分享，可把另一条路由推到分享页上方而被错误弹出。 | 让分享呈现与现有在途通知导航串行，或在真正 push 前复核并保留排队的通知；完成只处理该分享路由。用可阻塞的通知 cue 复验先通知后分享的交错，断言通知最终仍可跳转。 |
| R10 | 中 | [Kimi 查询诊断](../../mc_frame_chat_information.py#L460)只记录 status/snapshot 和异常类。ACK、超时、扫描代次和分页均可落成 `KimiServerError`，[详细日志](../../kimi_server_v2.py#L202)只在成功扫描后出现，仍不足以定位用户实际失败。 | 让具体失败边界携带净化后的阶段标记与仅必要的计数，不记录正文、会话标识和凭据。独立断言常见失败阶段能区分。已有累计计算和 Ctrl 手势未发现需要改变的产品逻辑。 |

## 验证边界

- 已跑的 Android instrumentation 5 项和 Flutter 离线草稿 1 项仍有效。这两层分别使用内部 FileProvider 和模拟原生通道。它们没有证明微信实际 Intent 分派、Activity 冷热入口与 Dart 页面连通。当前 Android manifest 只有 SEND/SEND_MULTIPLE，VIEW 或独立 data URI 仍未接收。用户实际微信调用形态需要验证，确认是 VIEW 前不应随意扩大接收范围；可先完成两个已支持的外部应用 Intent 冷热入口的真设备或专用 AVD 回归。
- 两组中文文件名已通过隔离 AVD 的 RC 原文、实际 NATS/HTTP、文件库落盘和 SHA-256。该验收不覆盖 RC 分享至目标模型的上传提交链路。后者至少需要实际 RC 命令和 MC transport 串起上传、失败重试、归属检查及 provider 构造，不能沿用当前两个独立假件声称通过。
- Kimi 现有隔离真实运行时覆盖已知可恢复的历史输出。Alt+Y 的一轮样本使最近与累计缓存率同值，宜用不同分母的至少两轮验证加权并覆盖真实缺字段。仍待受影响的日常历史现场复测。Ctrl 已验证合成按键回调和真实编辑器焦点，物理键、云语音和读屏属于现场边界。
- 未修改产品源码，使用静态调用链与现有测试证据完成审查。

## 审查镜头与裁决

本轮四个镜头均完成。Verification Gap 的临时只读回退会话首次无法访问渲染指令，改用原生审查子代理已成功按原指令完成。

| 来源 | 序号 | 裁决 | 证据与归并 |
| --- | --- | --- | --- |
| Blind Hunter | 1 | 高·修复 | RC 平铺命令字段与 MC 嵌套检查不一致，R1。 |
| Blind Hunter | 2 | 高·修复 | Codex/Kimi 均检查 status，R2。 |
| Blind Hunter | 3 | 高·修复 | 普通路径分配与附件保留不一致，R5。 |
| Blind Hunter | 4 | 高·修复 | ThreadingHTTPServer 在同一最终路径并发写入，失败分支会 unlink，R5。 |
| Blind Hunter | 5 | 高·修复 | 启动前投影和 outbox 被调用，回滚只写 turn，R3。 |
| Blind Hunter | 6 | 高·修复 | 清空删除历史收据，重试用最新 revision，R4。 |
| Blind Hunter | 7 | 高·修复 | 文本可编辑、回执未比较内容，R4。 |
| Blind Hunter | 8 | 高·修复 | 通用非 Claude API 只收到路径文本，R2。 |
| Blind Hunter | 9 | 中·修复 | 冷热 Activity 调用均同步进入 copy，R6。 |
| Blind Hunter | 10 | 中·修复 | 文件级同步 commit 与重新生成 batch ID 造成拆批和丢跟踪，R6。 |
| Blind Hunter | 11 | 中·修复 | 内容去重未引用交付身份，R6。 |
| Blind Hunter | 12 | 中·修复 | 最终文件早于稳定索引登记，R8。 |
| Blind Hunter | 13 | 中·修复 | 恢复入口 firstOrNull 无批次选择，R7。 |
| Blind Hunter | 14 | 中·修复 | 失败阶段的多个异常都落入相同类别，R10。 |
| Edge Case Hunter | 15 | 高·修复 | 普通写入不避开附件保留路径，R5。 |
| Edge Case Hunter | 16 | 高·修复 | 同一个 ACCEPTED 记录仍可并发请求，R5。 |
| Edge Case Hunter | 17 | 高·修复 | 完成记录不比较上传身份元数据，错把旧内容当新提交，R5。 |
| Edge Case Hunter | 18 | 高·修复 | 复用 send ID 后改文本并错误确认，R4。 |
| Edge Case Hunter | 19 | 高·修复 | 托管 API 路径文字无法代替文件，R2。 |
| Edge Case Hunter | 20 | 中·修复 | onCreate 在 super 前同步读取 URI，R6。 |
| Edge Case Hunter | 21 | 中·修复 | 拷贝完成及 manifest 持久化之间无恢复点，R6。 |
| Edge Case Hunter | 22 | 中·修复 | 异常 Future 被缓存后同次运行所有草稿入口持续失败，R7。 |
| Edge Case Hunter | 23 | 中·修复 | 已完成私有副本清理失误后无重试，R7。 |
| Edge Case Hunter | 24 | 中·修复 | 共享路由只检查进入通知处理时刻；await 后通知可覆盖草稿，R9。 |
| Verification Gap | 25 | 高·修复 | 移除 RC per-file 完成调用，当前各段测试仍能过。真实传输 + 最终聊天轮次验收纳入 R1/R4。 |
| Verification Gap | 26 | 中·修复 | 无新 HomePage 实例从持久索引恢复的测试，R7 验收。 |
| Verification Gap | 27 | 低·修复 | 两个左 Ctrl 长按分支没有从对应键盘后端驱动。回归缺口可用小测试覆盖，R11。 |
| Intent Alignment | — | 描述 | “聊天来源”依已批准计划解释为配对电脑。隔离 RC 中文真实传输覆盖当轮产品路径，实体微信、读屏、物理 Ctrl、用户实际 Kimi 会话仍属现场证据边界。 |

**R11：覆盖左 Ctrl 长按分类。** 在 [钩子与轮询现有测试](../../tests/test_main_entry.py#L152) 中，直接驱动两个后端的真实按下与释放处理，用受控时钟分别断言短按、超过 400ms 的左 Ctrl、右 Ctrl 和双侧和弦。当前直接向控制器注入 `combo_used=True` 无法发现任何后端退回旧分支。本项是定向验证缺口，未发现需改语音产品行为。

**审查结果：** 0 项需重新决策，11 个可执行修复分组，1 项现场待实证，0 项驳回。现场待实证是微信实际是否发 VIEW / data URI，按实际分派形态确定兼容，不从菜单字样推断。用户已提前指定由第二次 bmad-build-auto 实施以上修复。
