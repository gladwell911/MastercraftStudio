# 四项修复独立验证（2026-10-02）

结论：本轮四项核心行为的定向、桌面 GUI、模拟器真实通知表面及跨端 Local 验证通过。没有修改产品或测试代码，没有提交或推送，没有操作实体 63b35d91、桌面安装包或个人数据库。验证范围不代表完整产品套件、实体 TalkBack 语音、生产 Live 或真实模型。

最终交付 diff：`C:/Users/gladwell/AppData/Local/Temp/sj-four-fixes-c9433ad0e6054ba982524b5c9c5bbb4d.diff`，SHA256 `08951AECB51DF523445626EA8EDF6481BDD490DF39546EC1B072E651118B2321`。已独立核对 hash，root 确认重建后产品未再改。读取完整计划、monkey 修前报告、MC/RC AGENTS、handoff/experience/reflection 及当前 testing；GUI 与设备运行串行。源码基线见实施计划。

## 定向命令与结果

MC cwd `D:/code/sj/mc`，全部 exit 0：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_answer_list_time_rows_unit.py tests/test_chat_store_unit.py -k 'answer_time or completion_time or time_row or wechat or should_show or cumulative or shared_contract' -q
.venv/Scripts/python.exe -m pytest tests/test_answer_presentation_ui_automation.py -k show_more_recomputes_visible_answer_time_anchor -q
.venv/Scripts/python.exe -m pytest tests/test_codex_integration.py -k steer_completed_items_accumulate_and_reload -q
.venv/Scripts/python.exe -m pytest tests/test_kimi_integration.py -k turn_completed_reenables_new_chat_and_plays_sound -q
```

分别 24、1、2、1 项通过。覆盖 question/assistant 独立 299/300/1800 秒、累计锚点、增量与重建、存储重载、可信同 owner final 恢复、无时间旧历史 fallback、远程 DTO/cache 时间更新与重复终结不刷新时间；Codex 活动/后台与 Kimi 权威完成路径。GUI show-more 实际回答时间行重算通过。测试隔离 notes 数据，没有真实 provider 调用。

RC cwd `D:/code/sj/rc`，全部 exit 0：

```powershell
flutter test --no-pub test/widget_test.dart --name 'execution process row|remote notification pop from existing|answer timestamp|remote assistant uses independent|notification.*hydration|back-to-back duplicate'
flutter test --no-pub test/remote_control_models_test.dart --name RemoteTurn
flutter analyze --no-pub lib/main.dart lib/remote_control_models.dart test/widget_test.dart test/remote_control_models_test.dart integration_test/execution_process_test.dart
```

22 widget、1 DTO 测试通过，analyze 无问题。覆盖无空白包装、不同详情与独立 event 保留、真实 ChatMessage 独立时间及仅时间变化刷新、299/300/1800 秒、more 历史重投影、通知水合/去重及已开聊天连续路由。新增 NATS fixture 文件的既有 unused `_HydratedHistoryService`、line239 curly 两项诊断由 director 记录；未将其声称为无诊断文件。

Android cwd `D:/code/sj/rc/android`：

```powershell
.\gradlew.bat app:testDebugUnitTest --tests '*RemoteBackgroundNotificationPolicyTest' --tests '*RemoteBackgroundNotificationIdentityTest' app:assembleDebugAndroidTest --offline
```

BUILD SUCCESSFUL，policy 6、identity 4 无 failures/errors。发现受影响旧 instrumentation `durableEventGroupsAreStableDistinctAndNeverSummaries` 仍期待主版“新消息”，立即报告，director 仅更新该预期；随后重新 build AndroidTest，保数据安装 app/test APK，并独立运行以下 selected class 方法，共 3 项通过：

```powershell
adb -s emulator-5554 shell am instrument -w -e class 'com.example.zhuge_qa.RemoteBackgroundServiceNotificationChannelTest#sameRequestCodeStillCreatesIndependentNotificationPendingIntents,com.example.zhuge_qa.RemoteBackgroundServiceNotificationChannelTest#lockedBuilderKeepsPrivateContentOutOfPublicVersionAndOnlyAlertsOnceOnRetry,com.example.zhuge_qa.RemoteBackgroundServiceNotificationChannelTest#durableEventGroupsAreStableDistinctAndNeverSummaries' com.example.zhuge_qa.test/androidx.test.runner.AndroidJUnitRunner
```

实际 Android PendingIntent 同 requestCode、不同 pair/event 不相等，同身份稳定；locked private 正文、公版遮罩及 only-alert-once 通过。日志 `engineer-android-instrumentation.log`。未扩大运行全部 instrumentation。

## 模拟器真实语义树

设备 emulator-5554，TalkBack 服务开启。正式 execution test 全部 5 项设备断言曾打印 All tests passed；标准 drive 外壳 exit 1，保留 `engineer-execution-device-clean.log`，不以外壳 exit 1 当作产品断言失败或隐去。之前两次 drive 空白、driver extension 不可用保留原日志，确认 rc cwd 后清可逆 Flutter 构建缓存、offline 恢复依赖。最终仅精确复跑新增用例：

```powershell
flutter test -d emulator-5554 integration_test/execution_process_test.dart --no-pub --plain-name 'attached commentary wrapper has one accessible body' --dart-define=QA_WRAPPER_SURFACE_HOLD_SECONDS=15
```

1 项通过，显式 `$LASTEXITCODE=0`。hold 窗口通过 `adb shell uiautomator dump` 保存 `engineer-wrapper-surface.xml`，不是只检查 Dart 字段：Android accessibility 树的 `chat:chat-exec-device:execution:attached-wrapper` 与 `same-body-other-event` 各含用户完整正文一次，`independent-detail` 保留正文和独立详情。与 monkey 修前同一 label 正文两次的设备复现对应。设备测试与 XML 不能证明已录制 TalkBack 发声或实体机验收。

## 真实通知中心、热路由与冷启动

私有 NATS 只监听本机 TCP14527/WS18527，MC `scripts/story4_notification_e2e_desktop.py` 使用本轮临时隔离 ChatStore。两组 `REMOTE_CONTROL_ENDPOINT/TOKEN` 与 `NATS_E2E_ENDPOINT/TOKEN` 显式指向 `ws://10.0.2.2:18527/nats`/测试 token，pair default，expected high1；发 fixture 前实际 background 与测试日志确认 default/events/high1。最终有效命令：

```powershell
flutter test -d emulator-5554 integration_test/nats_notification_e2e_test.dart --no-pub --dart-define=REMOTE_CONTROL_ENDPOINT=ws://10.0.2.2:18527/nats --dart-define=REMOTE_CONTROL_TOKEN=test-token --dart-define=NATS_E2E_ENDPOINT=ws://10.0.2.2:18527/nats --dart-define=NATS_E2E_TOKEN=test-token --dart-define=NATS_E2E_PAIR_ID=default --dart-define=NATS_E2E_EXPECTED_HIGH_WATER=1
```

1 项、原生 exit0，日志 `engineer-routing-private-test.log`。ADB 按每次 XML 正文节点真实 bounds 点击系统通知，不调用 Dart 路由函数替代点击：首次 A 水合 owner/title 正确；保持原聊天页不关闭，B/A/B/A 四次各自实际 ChatPage owner、canonical title 与 historyService.currentChatId 正确。`engineer-routing-first-shade/page.xml` 与 `engineer-routing-step0..3-shade/page.xml` 保留真实通知中心和目标页证据。step2 初抓发生在新通知投递前，等待实际 marker 后重新 dump 成功，未更改产品。

锁屏使用 production builder 的 `retainSecureLockscreenFixture`（deviceLocked=true），专用 QA PIN7429、原 privacy1/1 暂改 show1/private0。实际 secure/showing=true 树显示四个 QA 标题与“新消息”，没有 PRIVATE_BODY_MARKER。首次解锁尝试被残留系统 background permission 弹窗打断，未误判已解锁；清理后重新 retain，一次完整锁屏→PIN解锁期间不再 notify。该完整周期锁屏 XML 为 `engineer-pin.xml`（四条新消息），解锁 `engineer-unlocked-same-notifications.xml` 四条 PRIVATE_BODY_MARKER_0..3，policy showing=false/secure=true。截图和首次诊断 policy/XML 亦保留。由系统切换同条 notification private/public，不以字段断言冒充正文表面证据。

冷启动采用 production `lib/main.dart` 私有显式配置 debug build（exit0），`adb install -r -t`，没有数据清除。native retained fixture args 指向刚刚私有 harness 的实际 A/B owner（A `story4-notification-1790948123189147`，B 加 `-second`），带完整 pair/event URI 与 tap extras；每次 HOME+`am kill` 后 `pidof com.example.zhuge_qa` 无输出，通知仍在。实际点击 A/B 系统通知后页面分别显示 canonical Renamed/Second 标题和对应 owner-qualified message identifiers。证据 `engineer-cold-a/b-shade/page.xml`；启动前已核对持久化 endpoint 私有地址、backup空，后续日志保留在 `engineer-cold-final-device.log`。首次 cold A 3秒内 UIA null-root，稳定后正常抓到页面，未当作目标错误。

## Local 跨端及实际边界

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_cross_client_regression.ps1 -Mode Local -DeviceId emulator-5554 -DesktopRepo D:/code/sj/mc
```

exit0，实际手机聊天列表、Codex/Kimi deterministic roundtrip、desktop codex/main/kimi/main 精确路由及 runner cleanup 通过，日志 `engineer-local-cross-client.log`。不是生产 NATS/Cloudflare 或真实模型成功。

必须记录的隔离越界：首次通知 fixture 命令只提供 NATS_E2E_*，未覆盖 ZhugeApp startup 的 REMOTE_CONTROL_*；background 日志出现非私有 high63303 及外来事件。立即 force-stop 专用模拟器包、停止 drive；当时未发送用户 prompt、未点击外来通知，但发生了外来事件读取，因此不能声称本轮全程未联网。证据 `engineer-routing-surface.log`（PID5721）保留，不输出凭据。后续完整双 override、私有 handshake/persisted endpoint 核对后才继续。停止后残留通知权限弹窗曾挡后续启动；保存 XML、实际 Allow 后恢复私有流程，未清个人 data。

## 清理与未验项

QA default 四通知及 cold A/B 指定通知均由对应 cleanup fixture 清理；QA PIN清除，privacy恢复1/1，最终 showing=false/secure=false，TalkBack仍开启。专用 QA app force-stop，私有 NATS server 与 MC harness 均停止。模拟器保留本轮 private main debug APK和私有 persisted设置，未恢复可能连接生产的默认配置；不冒充正式手机安装。未改实体、桌面安装或个人资料。

工具自动策略拒绝删除本轮临时 QA 目录的递归清理命令，即使命令先校验 TEMP 前缀；返回理由仅为 `blocked by policy`，未绕过。`C:/Users/gladwell/AppData/Local/Temp/sj-notification-verify-00f3e4a93cc34b10a54d22072eebbb2e` server temp目录与 `C:/Users/gladwell/AppData/Local/Temp/story4-mc-e2e-5hzwi8dj` 临时测试数据库保留。最终只读核对 TCP14527/18527 无 Listen、无 story4 harness Python进程，服务已停止，不影响运行。

没有已确认的未修复产品失败。未验：实体锁屏/TalkBack语音/MIUI、真实 provider、生产 Live、桌面打包版本、全产品套件。没有新增复杂回归或将模拟器断言扩大为这些结论。

## 审查后局部 completion surface 修补复验

审查指出 MC 已有回答行时，完成写入 answer_at 后可能未重投影实际时间行。director 在完成后的 `_update_active_answer_row`、accepted deadline visible检查局部复用既有时间投影；只在done投影变化时 reconcile，streaming delta仍label-only。engineer读取相关main与Codex/Kimi测试hunks，未修改代码。

以下精确命令在 MC cwd 独立串行执行，均 exit0：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_codex_integration.py tests/test_kimi_integration.py -k 'turn_completed_clears_busy_state or turn_completed_reenables_new_chat_and_plays_sound' -q
.venv/Scripts/python.exe -m pytest tests/test_answer_presentation_ui_automation.py -k 'final_visible_with_continuous_native_navigation_preserves_identity or answer_deadline_discards_replaced_owner_and_explicit_helper_stops_timer' -q
.venv/Scripts/python.exe -m pytest tests/test_answer_list_time_rows_unit.py tests/test_chat_store_unit.py -k 'answer_time or completion_time or time_row or wechat or should_show or cumulative or shared_contract' -q
```

分别4、2、24项通过。既有可见答案+30分钟后ordinary/quiet真实终结，assistant实际时间行、selected稳定身份和focus保留、accepted flush可见性，以及Codex delta不调用重建均有断言。GUI覆盖连续原生导航与deadline owner替换/定时器清理。差异检查通过，没有扩全模块。手机/Android/Local/device代码未改，复用前述已通过证据，不重复运行。

此时共享MC树存在其他任务新增codex_approval_policy与worker相关未提交改动；未检查或修改其实现，root承诺最终任务diff只含时间/通知/语义修复，排除其他任务hunks。复验针对包含本轮completion修补的当前共享树，不能把这些测试当作无关功能验证。

局部修补后 engineer 对同路径更新diff实际读得 SHA256 `6DB9FED5D45BA272126B401B21F1E8E3C7A7A66892B25EB465B539581C4A6336`；首段 `08951...` 只对应修补前surface验证版本，最终交付以root确认过滤其他任务hunks后的hash为准。

### Final task-only artifact

Root rebuilt the reviewed diff after the completion patch and excluded unrelated concurrent approval/worker changes and documents. Final SHA256: `CC29C9869ED194924EC6DABA04C2C5081A362014925D2ED5F8ABAA494D038A4C`. Earlier hashes above identify the historical validation snapshots; no verified mobile code changed during this filtering.
