---
title: '实施 Kimi 聊天用量、Ctrl 语音和 RC 文件分享修复'
type: 'feature'
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
warnings: ['oversized', 'multiple-goals']
deferred: []
---

<intent-contract>

## Intent

Kimi 的 Alt+Y 需要呈现会话累计 token 和累计缓存命中率，聊天顶部以整数千位展示会话累计。Windows 左右 Ctrl 需要一致的双击起录与单击停止，分别写入 MC 输入框和当前焦点，并消除空框首次追加的「输入」前缀。

Android RC 接收微信分享文件后，提供最近聊天及来源和「存储到文件管理」。完整复验从 RC 回答原文提取中文文件并传输至手机。

## Boundaries & Constraints

以 `D:/code/sj` 组织两个独立 Git 仓库。分别读取两仓 `AGENTS.md`、`docs/handoff.md`、`docs/experience.md`、`docs/reflection.md`。分别记下实际 HEAD 和既有工作区改动，保留 MC `AGENTS.md` 中与本轮无关的个人改动且不提交或推送。RC 三处既有测试及实测记录可独立提交作为基线。

聊天、账户、扫描代次和双电脑来源必须保持归属。Kimi 数据只有在字段完整且同口径时补齐确切累计。分享使用已授权 URI 的私有副本并可恢复重试，同一请求只交付一轮。测试使用合成文件和隔离日常数据的环境。没有发布授权时只做隔离构建与专用 AVD 安装，日常 MC 退出和重启由用户安排。

</intent-contract>

## Code Map

- `mc/kimi_server_v2.py`、`mc/mc_frame_chat_information.py`、`mc/context_usage.py` 管理历史用量、聊天信息请求归属及顶部累计显示。
- `mc/speech_input.py`、`mc/mc_frame_keyboard.py`、`mc/mc_frame_voice.py`、`mc/main.py` 管理按键判定、写入路由和输入框。
- `mc/remote_nats.py` 公告能力。`mc/mc_frame_remote_routes.py` 接受远程问答。`mc/mc_frame_files.py` 管理文件记录。`mc/mc_frame_requests.py` 提交归属明确的一轮。
- `rc/android/app/src/main/AndroidManifest.xml`、同应用源码下 `MainActivity.kt`、`SharedFileInbox.kt` 管理外部 intent、私有 URI 副本及入队。`rc/lib/main.dart`、`files_page.dart`、`remote_nats_chat_service.dart` 管理手机分享交互、文件库与聊天提交。
- `mc/scripts/nats_file_transfer_e2e_harness.py`、`rc/integration_test/real_answer_extracted_file_transfer_e2e_test.dart`、`rc/integration_test/extracted_file_action_progress_e2e_test.dart` 验证中文文件的传输链路。

## Tasks & Acceptance

### Execution

### 1. MC 聊天用量

- [ ] `mc/mc_frame_chat_information.py`、`mc/tests/test_kimi_runtime_usage_e2e.py` -- 从实际运行包对应的非空 Kimi 受影响会话或等价的隔离历史复现，记录 Alt+Y 的原样状态、socket/REST 阶段、分页是否走完、各类终态的用量字段是否存在。诊断只记录异常类别、阶段和聚合计数。遇到「查询失败」，检查订阅 ACK、REST 超时或分页游标、socket 与 transcript 扫描代次。遇到「暂不可用」，检查历史记录中实际缺失的字段和终态类型。
- [ ] `mc/kimi_server_v2.py`、`mc/tests/test_kimi_server_client_unit.py` -- 在 [Kimi 用量计算](../../kimi_server_v2.py#L19) 修复已复现的形态。若真实 Kimi 2.5.3 协议证明是同口径，部分回合汇总可用完整终态步骤补缺。计算 `inputTokens` 对应的步骤 `inputOther + inputCacheCreation`，`cachedTokens` 对应 `inputCacheRead`，`outputTokens` 对应 `output`，逐项比对已知汇总字段；只有每个实际消耗步骤终止且字段完备、已知汇总一致时才补缺。完整回合汇总优先。字段冲突、真正缺失以及未证明零消耗的空取消回合保留未知。只有真实复现支持时才改订阅、分页或归属逻辑。若无法取得实际会话，仍用隔离 Kimi 2.5.3 验证字段同口径，完成符合这些条件的部分汇总恢复和脱敏查询反馈，补确定性夹具与回归。真实会话的对应原因继续标为待验。
- [ ] `mc/mc_frame_chat_information.py`、`mc/tests/test_main_chat_information.py` -- 继续以当前 chat、session、account 和 scan generation 归属提交。确认累计 token 与缓存率分别使用各自所需的字段，累计缓存率由总缓存读取量除以总输入量，分母为零显示暂不可用。覆盖已知零值、空历史、混合完整和缺失、重复终态、多页以及扫描中清空或切聊天。
- [ ] `mc/context_usage.py`、`mc/tests/test_context_usage_ui_automation.py` -- 把 [顶部格式化](../../context_usage.py#L44) 改为整数千位。覆盖 0、999、1000、1999及未知值。Alt+Y 的完整整数和缓存百分比精度照旧。

### 2. MC 语音手势与首段写入

- [ ] `mc/speech_input.py`、`mc/mc_frame_keyboard.py`、`mc/tests/test_voice_controller_unit.py` -- 在 [Ctrl 状态机](../../speech_input.py#L23) 统一左右空闲判定为同侧双击，复用现有双击时间窗口和按键去重。录音就绪后一次短按停止，根据起录侧投递结果，避免 hook/poller 双重上报、Ctrl 组合键及混合左右两次误触发。保留首帧就绪、开始中取消、会话代次、停止及资源清理行为。若实现需要确定录音中异侧短按语义，保留原有单击停止行为，验收以同侧短按为准。
- [ ] `mc/main.py`、`mc/mc_frame_voice.py`、`mc/tests/test_voice_input_ui_automation.py` -- 保留 [左侧追加和右侧焦点写入](../../mc_frame_voice.py#L346)。移除或替代输入框的两个 [RichEdit 提示调用](../../main.py#L1142)，由独立可访问标签或现有状态表达「输入」「已发送」。保留用户实际键入的同名文本、既有段落内容及末尾位置。覆盖启动空框、发送或清空后的空框、MC 失焦时后台追加、多段结果和当前编辑控件有选区的右侧写入。

### 3. 先实现 MC 聊天附件接收，再实现 RC 分享界面

- [ ] `mc/remote_nats.py`、`mc/mc_frame_remote_routes.py`、`mc/mc_frame_files.py`、`mc/mc_frame_requests.py`、`mc/tests/test_main_remote_nats_unit.py` -- 核对最新 RC/MC 的远端问答、握手能力和文件上传契约，沿用既有命令与附件归一化。若需扩展，MC 先声明独立的聊天附件能力，在收到聊天提交前按设备来源、目标聊天及当前发送归属验证文件引用。仅接受来自对应授权来源、上传完成且仍可读取的托管文件；禁止从 RC 接受任意 Windows 路径。沿用稳定的 `client_send_id` 或等价幂等键；重复请求只确认同一轮。测试异来源或异聊天、上传未完成、取消或删档后的引用、重试及附件能被目标模型读到。
- [ ] `rc/android/app/src/main/AndroidManifest.xml`、`rc/android/app/src/main/kotlin/com/example/zhuge_qa/MainActivity.kt`、`rc/android/app/src/main/kotlin/com/example/zhuge_qa/SharedFileInbox.kt`、`rc/android/app/src/androidTest/kotlin/com/example/zhuge_qa/SharedFileInboxTest.kt` -- Android 冷启动和 `onNewIntent` 汇入同一个已有 [持久收件箱](../../../rc/android/app/src/main/kotlin/com/example/zhuge_qa/SharedFileInbox.kt)。在 URI 仍有读授权时复制到应用私有目录。保留 SEND/SEND_MULTIPLE 的 EXTRA_STREAM，覆盖可授权的 content URI，尽早完成建立已知系统用例的冷暖启动链路，并按真实微信 intent 加入必要的 `ClipData` 或 `ACTION_VIEW` 的 `data` 兼容。确需 VIEW 时限定文件内容 URI 和 MIME 范围，排除网页或无文件启动。热启动通知 Dart；冷启动从已有 manifest 恢复。异常时保留可读副本并给可访问失败反馈。
- [ ] `rc/lib/main.dart`、`rc/lib/files_page.dart`、`rc/test/answer_file_extraction_test.dart` -- Dart 将一次接收的一个或多个文件变为可恢复草稿，在根导航就绪后展示最近聊天，显示默认台式机或笔记本来源，底部固定「存储到文件管理」。根导航串行呈现，重建或重复通知只恢复原草稿。选文件管理即进入本机库，可在没有网络时完成并看到文件。文件管理中可再次选文件给聊天。选聊天后可核对来源、输入指令、检查文件列表，再由用户确认发送。返回或失败保留草稿，重新打开恢复选择和已上传引用。
- [ ] `rc/lib/main.dart`、`rc/lib/remote_nats_chat_service.dart`、`rc/test/remote_nats_chat_service_test.dart`、`rc/integration_test/mobile_file_share_e2e_test.dart` -- 发送以草稿绑定的 pair/chat 为准，确认整批上传后用稳定请求 ID 提交一轮，超时重试沿用原 ID，写入成功才标记完成。取消或失败只清理本草稿拥有的临时文件或任务。先持久化草稿再确认原生收件，处理提交成功但确认丢失、进程中止与重复投递。旧 MC 只提供可用的文件管理路径，并说明该电脑暂不能接收聊天附件。双源只对支持能力且已正确选择的目标启用发送。
- [ ] `rc/integration_test/mobile_file_share_e2e_test.dart`、`mc/tests/test_main_remote_nats_unit.py` -- 对照 [原分享交付约定](../../../docs/rc-mc-remediation-plan.md#分享到-rc) 实现上述范围。当前 [Dart 入库](../../../rc/lib/main.dart#L1523) 没有目的地选择，[MC 文件上传](../../mc_frame_files.py#L487) 只创建文件传输记录。新路径必须实际交付附件到选中的聊天轮次。

### 4. 复验提取中文文件

- [ ] `mc/scripts/nats_file_transfer_e2e_harness.py`、`rc/integration_test/real_answer_extracted_file_transfer_e2e_test.dart` -- 用 [MC 隔离文件 harness](../../scripts/nats_file_transfer_e2e_harness.py) 指定 `FILE_E2E_DESKTOP_NAME` 和合成字节。使用专用 AVD、隔离 NATS 和临时库，由 RC 产品 HomePage 打开回答原文，执行「提取文件 → 传输到手机」，覆盖纯中文文件名，以及含空格和 `#%` 的文件名。核对 NATS `file_probe` 路径、实际下载完成、手机文件库记录、最终 basename、大小和 SHA-256。至少一例带 Unicode 的上级路径，并将文件名和 HTTP 内容分别校验。
- [ ] `rc/integration_test/real_answer_extracted_file_transfer_e2e_test.dart`、`rc/integration_test/extracted_file_action_progress_e2e_test.dart`、`rc/test/file_transfer_http_client_test.dart` -- [现有真实下载用例](../../../rc/integration_test/real_answer_extracted_file_transfer_e2e_test.dart) 经过 NATS 和 HTTP，但以注入的下载回调代替产品 HomePage 落库。配合 [产品进度用例](../../../rc/integration_test/extracted_file_action_progress_e2e_test.dart) 或扩展成完整流程。实测失败才修改对应阶段。对已成功的 HTTP `filename*` 路径保留测试。

### Acceptance Criteria

- **Kimi Alt+Y** — Given 隔离或实际 Kimi 2.5.3 非空历史及可校验的完整终态用量，When 会话重开后打开 Alt+Y，Then 显示正确的会话累计 token、按全部输入加权的累计缓存率。给定部分汇总与同口径完整步骤，加权结果也能恢复。若真实查询失败或历史确实缺数，则分别显示失败或暂不可用。分页中切换或清空时，界面不接纳旧会话结果。
- **顶部用量** — Given 会话累计分别为 999、1000 和 1999，When 聊天顶部用量更新，Then 依次显示「累计 0k」「累计 1k」「累计 1k」，Alt+Y 仍按完整整数与缓存百分比显示。
- **Ctrl 手势** — Given MC 或其他编辑器在前台，When 分别测试左右 Ctrl 的一次短按、同侧双击、再同侧短按，Then 首次单击不起录，双击启动，录音就绪后单击停止一次。左侧结果追加到 MC 输入框，右侧结果精确填入当前焦点或替换所选文本。用按键事件和合成识别验证自动化路径，物理按键与云语音另做现场验收。
- **左侧首段** — Given MC 是空草稿且可处于后台或刚发送、刚清空，When 左 Ctrl 完成首段识别，Then 输入框从实际识别文字开始，且后续段落顺序正确。给定真实草稿正文恰为「输入」，追加后该正文仍在。
- **外部分享** — Given 外部应用通过已授权文件 URI 转入单文件或多文件，When RC 冷启动或已在后台，Then 显示一份分享草稿，可选带来源的最近聊天或「存储到文件管理」。离线选择存储后能在手机文件库看到原文件。实体机按同样流程从实际微信菜单复验。
- **聊天附件交付** — Given 两个来源有同名聊天且目标 MC 支持此能力，When 用户选定一个聊天、提交指令和附件，并在部分上传失败或提交确认丢失后重试，Then 草稿能恢复，只有选定来源的聊天增加一轮，模型可读出合成文件中的标记。给定旧 MC，界面说明聊天附件不可用，文件管理仍可存储。
- **提取文件** — Given 回答原文含 `中文.txt` 和在 Unicode 目录中的 `中文 报告#%.txt`，When 在 RC 走「提取文件 → 传输到手机」，Then 文件库显示完成，basename、大小和 SHA-256 与 MC 来源一致。隔离完整流程以 RC 产品界面为证据，实体真机与日常连接结果另记。

## Implementation Notes

## Plan Change Log

## Review Triage Log

## Design Notes

### 当前证据


| 项目 | 现有证据与结果要求 |
| --- | --- |
| Kimi Alt+Y | 日常 `mc.exe` 已含终态步骤用量恢复。隔离 Kimi 2.5.3 冷重启曾显示累计 110 token、累计缓存命中 20.0%，本轮复跑 1 项通过。合成的部分汇总和空取消回合可让两项累计缺失。用户实际会话的缺失原因待复现。 |
| 聊天顶部累计 | 使用千为单位，截去小数。例如 1999 显示「累计 1k」。 |
| 左右 Ctrl | 两侧均双击启动，同侧再单击停止。左侧追加到 MC 输入框，右侧写入当前焦点。 |
| 左 Ctrl 首段 | 用产品相同样式的未聚焦 wx 富文本控件复现。设置「输入」提示后，`GetValue()` 可读到该字符串，首次语音追加后也保留它。 |
| 微信分享至 RC | 现有代码只认 SEND/SEND_MULTIPLE 的 EXTRA_STREAM，Dart 收到文件后直接入库。接收后应展示含电脑来源的最近聊天和「存储到文件管理」。微信实际 intent 类型和 URI 来源待验证。 |
| 提取中文文件 | 本地真实 MC HTTP 与 RC Dart 客户端已下载 `中文.txt`、`中文 报告#%.txt`，按响应元数据和 HTTP `filename*` 两条路径均保留文件名且字节一致。尚需通过 RC 原文、NATS 和手机文件库完成同一条产品路径。 |


### 阶段与职责


1. Dog 完成分析和本计划。
2. Director 用 bmad-build-auto 实施，按下节的 MC、RC 依赖顺序组织变更。先完成可独立验证的产品改动及隔离测试。
3. Engineer 独立验证工作树、入口及本计划验收。记录通过、失败和受现场环境限制的项目，避免用实现者日志代替独立验证。
4. 由新 Dog 独立审查，按证据裁决发现。如需修复，交 Director 用 bmad-build-auto 落地，再由 Engineer 独立复验。
5. 修复与复验收敛后，Director 执行 neat-freak，核对两仓文档、测试证据和提交范围。发布动作按下节授权边界处理。


### 构建、安装与交付

- 开发循环负责源码、测试、隔离构建与证据。实际操作以用户当轮授权为准。无发布授权时只做隔离 `-BuildOnly` 和专用 AVD 安装，保持日常 MC 与实体 RC 继续使用，当前的 MC 退出和重启由用户安排。
- 获得发布授权后按仓库说明核对正在使用的 MC 路径、笔记、聊天数据和运行身份，先构建并发布兼容 MC，再为实体 Android 使用仓库 ARM64 构建和保数据覆盖安装流程。核对实际前台包名，避免 QA 同名入口。保留数据库、通知、文件库和草稿，继续使用原双源归属。
- 验收记录区分源码、冻结包、AVD、物理按键、实体微信、实际 Kimi 会话及实际 NATS/HTTP。缺少某层证据时记录具体阻碍，并继续完成独立任务。若出现串聊天、丢草稿、重复发送、文件名或哈希错误，停止扩大部署，保留数据后回退对应入口或兼容包。
- 收尾逐项填写实施提交、通过与失败的测试、现场证据、已知限制，分别检查两仓棘手并发路径和 `git diff --check`。

## Verification


| 层级 | 验收 |
| --- | --- |
| MC 定向 | 项目 venv 下执行 `tests/test_kimi_server_client_unit.py`、`tests/test_main_chat_information.py`、`tests/test_context_usage_ui_automation.py`、`tests/test_voice_controller_unit.py`、`tests/test_voice_input_ui_automation.py`、`tests/test_file_manager_ui_unit.py` 及已存在的远端问答和附件测试。涉及 wx 的文件串行执行。使用 `MC_KIMI_RUNTIME_TEST=1` 执行 `tests/test_kimi_runtime_usage_e2e.py`。用 `MC_NATIVE_VOICE_TEST=1` 执行 `tests/test_voice_native_foreground_e2e.py`。 |
| RC 定向 | `flutter test --no-pub test/answer_file_extraction_test.dart test/file_transfer_http_client_test.dart test/remote_nats_chat_service_test.dart`，加受影响的 HomePage 和聊天草稿用例。运行 `flutter analyze --no-pub` 对本次 Dart 改动定向检查。Android 构建并执行 `SharedFileInboxTest`，覆盖冷启动、前台重复、外部授权 URI、批次恢复、部分复制失败和确认丢失。 |
| 隔离跨端 | 先验旧 RC 对新 MC、再新 RC 对旧 MC 降级、最后两端都新。采用专用 Android AVD 和独立 MC/NATS，验证微信等外部来源真实 URI、存入文件管理、两台电脑同名聊天分别发送、批次上传部分失败后重试只出现一轮、模型能读到合成文件里的唯一标记。按第 4 节完成提取中文文件。 |
| 现场验收 | 记录实际前台 Android 包名、ABI、版本/哈希、MC 路径和哈希、连接来源与测试范围。实体 ARM64 从微信真实菜单分别验证冷和热启动、两个去向和 TalkBack 焦点，再用实际连接测一次中文提取文件。Windows 用物理左右 Ctrl 和麦克风分别跑双击起录、单击停止，验证读屏、左侧首段和右侧对真实前台应用焦点选区写入。日常 Kimi 在受影响的历史会话重新打开 Alt+Y，核查累计两项。 |

已知基线边界：MC 精确基线有 `test_register_global_hotkey_registers_backslash_variants` 的假对象 `SetStatusText` 缺失。隔离 Kimi 复跑曾输出 wx COM `0x8001010d`，pytest 最终 1 项通过。RC 全量提取用例既有 owner rebind 失败，以及可能在其后连带出现的分享 drain 超时，drain 可独立复跑。RC 另有既有通知、长回答语义及时序失败，只有在本次运行遇到时按精确基线重核。宽回归失败不得写成通过，独立通过的定向用例单独记录。证据和命令见 [MC 测试摘要](../../_bmad-output/implementation-artifacts/tests/test-summary.md) 及 [RC 测试说明](../../../rc/docs/current/testing.md)。

## Auto Run Result

最终修复、隔离设备结果和现场待验项见 [独立复验证据](plan-fix-mc-rc-kimi-voice-share-review.md#auto-run-result)。

初次实施和独立定向验证已完成，按用户指定的下一阶段使用新的 Dog 与 bmad-code-review 审查。启用两仓独立基线，项目容器目录不是 Git 仓库。未替换日常运行包。

- MC：历史用量字段一致性恢复、整数 k、左右 Ctrl 和输入框、聊天附件握手与上传归属、隔离传输脚本。
- RC：Android 持久批次、可恢复的分享去向、聊天附件发送和离线文件库、原文中文文件提取验证。
- 独立 MC 定向 270 项通过、4 项精确基线失败。独立 RC 定向 167 项通过、1 项精确基线失败。RC 9 个相关 Dart 文件静态分析通过。专用只读 Android AVD 的原生分享收件箱 5 项、恢复后离线入库 1 项、中文提取真实 NATS/HTTP/FileLibrary 2 项通过。
- 实体微信、TalkBack、物理 Ctrl 与用户实际 Kimi 历史会话待验。日常安装未授权。隔离桌面打包命令因执行进程为管理员由包脚本拒绝。
- 额外 AVD 测试：Android 系统文件应用通过真实分享面板投递合成文件。RC 冷启动和进程已运行时均显示分享去向，选择存储后原名及正文匹配；这一证据不包括微信。既有 RC 对外分享 chooser 1 项通过，独立接收应用可读取 24 字节、MIME 为 text/plain、含 ClipData 和读取授权。


## Code Review

### 2026-11-22

新 Dog 按 bmad-code-review 完成全量代码审查。0 项需决策，11 个修复分组，1 项现场待实证，0 项驳回。详见 [Dog 审查及修复输入](review-mc-rc-kimi-voice-share-dog.md)。

- [x] [Review][Patch] R1：修复 RC 平铺命令与 MC 附件来源识别不一致 [remote_nats.py:315]
- [x] [Review][Patch] R2：附件进入 Codex/Kimi 真实请求，并明确拦截无法读取文件的模型 [mc_frame_files.py:348]
- [x] [Review][Patch] R3：同步拒绝不能泄出已排队用户问题 [mc_frame_requests.py:830]
- [x] [Review][Patch] R4：固化提交指令、上传引用、回执与 Clear 代次 [mc_frame_remote_routes.py:643]
- [x] [Review][Patch] R5：管理目录同名保留、幂等和原子 PUT [file_transfer.py:380]
- [x] [Review][Patch] R6：后台共享接收与稳定的交付批次 [rc/android/app/src/main/kotlin/com/example/zhuge_qa/MainActivity.kt:406]
- [x] [Review][Patch] R7：多草稿选择、索引失败恢复与完成清理 [rc/lib/share_draft.dart:64]
- [x] [Review][Patch] R8：本地文件管理导入中断的稳定身份 [rc/lib/file_library.dart:147]
- [x] [Review][Patch] R9：串行处理 RC 分享与通知导航 [rc/lib/main.dart:4313]
- [x] [Review][Patch] R10：Kimi 查询失败阶段脱敏诊断 [mc_frame_chat_information.py:460]
- [x] [Review][Patch] R11：真实钩子和轮询后端左 Ctrl 长按验证 [tests/test_main_entry.py:152]
- [x] [Review][Defer] 微信实际 Android Intent 形状 [rc/android/app/src/main/AndroidManifest.xml:40] — deferred: 需要从实体微信菜单发一份文件并检查 RC 收到的 action、URI 来源及临时读取授权，才能决定是否扩展 VIEW。
