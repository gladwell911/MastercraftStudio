# 五项聊天运行时修复独立验证（2026-10-02）

engineer 只读验证产品与测试；仅写本报告。MC HEAD `b2fea0c21021738a38a3115af9a7773d90b33826`，RC HEAD `5d126e2956806e995f480204a3dd896f34a40265`，与计划 baseline 一致，验证工作树 diff。已完整读取计划、两仓 AGENTS.md、handoff/experience/reflection。

## 核心行为

- MC 隐藏执行页慢存储准备、重复隐藏 render 不重复读、完成后缓存复用：通过。读取在后台线程；隐藏列表内容和 input HasFocus 保持。
- owner/revision/generation 隔离：后台扫描 key 包含 chat/view/turn/limit/generation/state/list identity/length/revision；完成时复核 key 与 stop。失败重试及旧 owner 结果拒绝 GUI 通过。revision 同 owner 隔离由代码守卫核对，未新增同 owner revision 专用 GUI 场景。
- F1 实际键切页、完成后仍在 execution、有焦点、再次 F1 回 answer，以及 Enter/ShiftEnter：通过。fixture 只设置同步 timer 的 navigation quiet 前置条件，保留原可见性与焦点断言。
- Codex 纯 notLoaded/Not Loaded/string及纯字典占位过滤通过；mixed completed/failed 保留，非零 exit/有用 title 保留。
- Kimi deadline：入口已耗尽不启动 client、真实 timeout、退避耗尽保留原始 ConnectionError、预算传递通过。独立检查发现共享 deadline 后续 owner 无真实错误时误报“未返回任何内容”；director 局部修正后独立复验通过。优先级 original_error > last_error > deadline fallback。
- RC entry：预取/未预取均绑定当前详情 owner，entry 一次震动，重复 snapshot 静默通过。Kimi usesCodexRemoteTransport getter 包含 isKimi，与 Codex 同初始化分支；设备受控 Kimi roundtrip 通过。
- RC 历史分页：entry+实时回答总计两次震动，prepend与重复快照不增加，通过。
- RC history_changed：revision 2 当前详情 owner 拉取及 durable 首答 store 补齐通过；旧 revision 投影拒绝及在途新 revision 尾页补拉通过。

## 精确命令与结果

MC cwd `D:/code/sj/mc`，GUI 串行：

```powershell
.venv/Scripts/python.exe -m pytest tests/test_codex_client_unit.py tests/test_codex_integration.py -k not_loaded -q
# 2 passed
.venv/Scripts/python.exe -m pytest tests/test_codex_ui_responsiveness_automation.py -k 'hidden_execution or execution_mode_survives_completion or versioned_execution_snapshot_read' -q
# 4 passed
.venv/Scripts/python.exe -m pytest tests/test_codex_ui_responsiveness_automation.py -k 'execution_updates_do_not_steal or execution_scan' -q
# 1 passed
.venv/Scripts/python.exe -m pytest tests/test_codex_ui_responsiveness_automation.py -k execution_hidden_history_scan -q
# 1 passed
.venv/Scripts/python.exe -m pytest tests/test_codex_ui_responsiveness_automation.py -k f1_focuses_execution_latest -q
# 1 passed
.venv/Scripts/python.exe -m pytest tests/test_kimi_integration.py -k 'startup_retry_limits_sleep_to_remaining_deadline_budget or startup_expired_deadline_reports_timeout or reconcile_backoff_expiry_preserves_last_real_error or reconcile_expired_owner_deadline_reports_timeout' -q
# 3 passed; startup_retry...名称不存在，以下真实预算用例另跑
.venv/Scripts/python.exe -m pytest tests/test_kimi_integration.py -k recovery_deadline_helper_passes_remaining_budget -q
# 1 passed
 git diff --check
# passed
```

RC cwd `D:/code/sj/rc`：

```powershell
flutter test --no-pub test/widget_test.dart --plain-name 'chat entry binds detail owner and vibrates once with or without prefetch'
# 1 passed（含两entry路径）
flutter test --no-pub test/widget_test.dart --plain-name 'live answer during older page'
# 1 passed
flutter test --no-pub test/remote_nats_chat_service_test.dart --plain-name 'history_changed'
# 3 passed
flutter test --no-pub test/remote_nats_chat_service_test.dart --plain-name 'requestState'
# 3 passed
flutter test --no-pub test/widget_test.dart --plain-name 'old projected history cannot return after revision change'
# 1 passed
flutter test --no-pub test/widget_test.dart --plain-name 'projected tail refreshes once after revision changes in flight'
# 1 passed
flutter analyze --no-pub lib/main.dart test/widget_test.dart test/remote_nats_chat_service_test.dart
# No issues found
 git diff --check
# passed
./scripts/run_cross_client_regression.ps1 -Mode Local -DeviceId emulator-5554 -DesktopRepo D:/code/sj/mc
# exit 0; Mobile chat list/Codex roundtrip/Kimi roundtrip/exact codex/main,kimi/main routing PASS
```

跨端脚本参数已先核对，只允许 Android emulator；私有 strict-V2 desktop harness，模拟器 debug APK，正常 finally 清理 owned harness/temp。未操作实体 93206cc7、未调用真实模型、未操作用户进程或个人数据库。该跨端检查发生在最后 Kimi fallback 修正前；该修正仅错误文本兜底，后独立 Kimi 定向测试证明。

## 初失败与限制

旧 clear route widget 当前 owner 仍期待 chat-a，在新行为 chat-b 处失败（9140），已向 root 报告，由 director 更新，保留 clear目标及 execution隔离断言；最终独立复验通过，保留原 owner/execution 隔离断言，并增加同一路由 revision 2 cleared→首答直接可见断言。

本轮为受影响核心范围，未跑全模块、全系统、生产 Live、实体机 TalkBack/实际震动。widget haptic 与焦点通道为受控替身，模拟器 roundtrip不证明实体反馈。Kimi 原始生产启动失败原因未被真实日志证明，验证只证明预算误报分支修正。


## 最终 RC fixture 复验

```powershell
# cwd D:/code/sj/rc
flutter test --no-pub test/widget_test.dart --plain-name 'remote clear context targets route chat'
# 1 passed，详情 owner chat-b、clear目标及chat-a执行隔离全部保留，revision2首答无需退出ChatPage直接可见
flutter analyze --no-pub lib/main.dart test/widget_test.dart test/remote_nats_chat_service_test.dart
# No issues found (2.5s)
 git diff --check
# passed
```

结论：本轮五项受影响核心行为及 Local 模拟器双 provider roundtrip 独立验证通过；无待修复确认失败。两次发现均已由 director 局部修复并独立复验：Kimi无真实错误的共享deadline兜底、旧clear route测试预期。未验证范围如前述保留。
