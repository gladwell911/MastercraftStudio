---
title: 'Story 3: Push Notification Toggle and Persistence'
type: 'bugfix'
created: '2026-09-03'
status: 'done'
baseline_revision: '8adfb595bb5275b2ce01851e4b4d2754626642f4'
review_loop_iteration: 0
followup_review_recommended: true
context:
  - 'D:\\code\\sj\\rc\\AGENTS.md'
warnings: []
deferred: []
---

<intent-contract>

## Intent

**Problem:** Mobile users cannot globally control whether desktop replies are presented as system notifications. The existing per-chat English-only filter does not cover all reply notifications, and a Flutter-only preference would not reliably govern the Android background service after an app restart.

**Approach:** Add an accessible, default-on notification-presentation switch to mobile Settings. Persist its value where the Android background notification producer can read it, and gate only message-notification presentation while leaving remote-message receipt and synchronization intact.

## Boundaries & Constraints

**Always:** Keep the switch's accessible name, value, and checked state truthful. Default missing persisted state to enabled. Changing the preference must be safe while the background service is running. When disabled, continue parsing, receiving, and synchronizing remote replies; suppress only their system-notification presentation. Preserve the existing per-chat English-only filter and the foreground-service status notification.

**Block If:** Implementation requires changing the desktop `mc` project, the NATS reply protocol, Android runtime notification-permission policy, or the semantics of existing per-chat filters.

**Never:** Do not stop the background service, unsubscribe from remote replies, discard reply data, alter chat content, or replace the existing notification channels.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|---------------|----------------------------|----------------|
| Initial settings load | No stored global preference | Settings shows the notification switch on | Missing preference is treated as enabled |
| Disable then background reply | User turns the switch off; a desktop reply arrives | Reply processing continues but no message system notification is posted | A failed platform update leaves the UI at the last confirmed value and announces failure |
| Restart after change | Preference was set on or off before process restart | Settings and the Android background producer use the stored value | Missing/invalid preference safely falls back to enabled |
| Existing suppression rules | A reply is already suppressed for the active chat or English-only filter | Existing behavior remains unchanged; the global switch is an additional presentation gate | No duplicate notification or sync side effect |

</intent-contract>

## Code Map

- `D:\code\sj\rc\lib\settings_page.dart` -- owns the Settings UI and its optimistic, rollback-on-failure switch pattern; add the clearly labelled global notification switch without changing remote credential save behavior.
- `D:\code\sj\rc\lib\main.dart` -- creates `SettingsPage` in `_HomePageState`; bridge the settings callback to the Android-backed global preference and keep its displayed state synchronized.
- `D:\code\sj\rc\lib\remote_notification_bridge.dart` -- Flutter platform-channel contract for remote notification settings; extend it with load/save operations for the global presentation preference and maintain MissingPlugin-safe behavior.
- `D:\code\sj\rc\android\app\src\main\kotlin\com\example\zhuge_qa\RemoteNotificationBridge.kt` -- owns Android notification preferences and method dispatch; persist the default-on global preference here so the background service can use it independently of Flutter lifecycle.
- `D:\code\sj\rc\android\app\src\main\kotlin\com\example\zhuge_qa\RemoteBackgroundService.kt` -- `postMessageNotification` is the final message-notification presentation boundary; return before `NotificationManager.notify` when the global preference is disabled, after normal upstream message processing has occurred.
- `D:\code\sj\rc\test\codex_remote_test.dart` -- focused SettingsPage widget tests; extend coverage for default value, semantic label/state, callback, and persistence-error rollback.
- `D:\code\sj\rc\test\widget_test.dart` -- shared fake `RemoteNotificationBridgeClient` and HomePage notification flow coverage; update the fake contract and verify the Settings-to-bridge integration does not prevent receipt/synchronization.

## Tasks & Acceptance

**Execution:**
- [x] `D:\code\sj\rc\lib\remote_notification_bridge.dart` and `lib\main.dart` -- expose default-on global notification-presentation load/save methods and connect their confirmed value to HomePage Settings state.
- [x] `D:\code\sj\rc\lib\settings_page.dart` -- render an accessible global “push notifications” switch, serialize updates, announce success/failure, and restore the prior confirmed value on error.
- [x] `D:\code\sj\rc\android\app\src\main\kotlin\com\example\zhuge_qa\RemoteNotificationBridge.kt` and `RemoteBackgroundService.kt` -- persist the setting in Android shared preferences and gate only `postMessageNotification`, retaining all reply handling and the status notification.
- [x] `D:\code\sj\rc\test\codex_remote_test.dart` and `test\widget_test.dart` -- cover the settings surface, bridge propagation/persistence default, disabled presentation, and unchanged reply-handling path.

**Acceptance Criteria:**
- Given a user opens mobile Settings, when a screen reader reaches the push-notification control, then it exposes a meaningful label, switch role, and truthful enabled/disabled value and state.
- Given no earlier choice exists, when Settings first loads, then the push-notification switch is enabled by default.
- Given the user disables push notifications and a desktop reply arrives, when the background service handles that reply, then it does not present a message system notification while normal receipt and synchronization still occur.
- Given the user enables or disables the setting, when the app restarts, then both Settings and the Android notification producer retain that same choice.

## Spec Change Log

## Review Triage Log

### 2026-09-04 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 6 (high 1, medium 5)
- defer: 0
- reject: 14 (low 14)
- addressed_findings:
  - `[high]` `[patch]` Updated all integration-test bridge fakes for the expanded platform contract, preventing integration test compilation failures.
  - `[medium]` `[patch]` Protected the initial preference load from stale completion and unexpected errors.
  - `[medium]` `[patch]` Kept a successful switch value locally and restored the actual previous value for either-direction persistence failure.
  - `[medium]` `[patch]` Rejected malformed native update arguments and mapped preference-write exceptions to a controlled platform failure.
  - `[medium]` `[patch]` Added production-bridge false-value, platform-failure, delayed-load, and success/rollback regression coverage.
  - `[medium]` `[patch]` Extended Android source-behavior assertions that the global gate remains limited to message presentation and does not suppress event handling or status notifications.

## Design Notes

The global preference belongs in Android shared preferences rather than only in Flutter because `RemoteBackgroundService` may receive replies while no Flutter widget tree exists. The presentation gate is deliberately placed immediately before notification posting: parsing and state/sync work have already completed, so disabling the control cannot accidentally block background synchronization.

## Verification

**Commands:**
- `flutter test test/codex_remote_test.dart` -- expected: Settings switch accessibility, callback, and error behavior pass.
- `flutter test test/widget_test.dart` -- expected: HomePage notification and fake-bridge integration regressions pass.
- `flutter analyze lib/main.dart lib/settings_page.dart lib/remote_notification_bridge.dart` -- expected: no diagnostics in changed Flutter production files.
- `./gradlew :app:compileDebugKotlin` -- expected: changed Android bridge and background-service code compiles.

## Auto Run Result

**Summary:** Added a default-on, persistent Android push-notification presentation preference and an accessible mobile Settings switch. Disabling it suppresses only background message notification posting; remote event processing, synchronization, existing per-chat filtering, and the foreground-service status notification remain intact.

**Files changed:**
- `lib/remote_notification_bridge.dart`, `lib/main.dart`, and `lib/settings_page.dart` -- platform API, load/update coordination, and accessible switch with rollback.
- `android/app/src/main/kotlin/com/example/zhuge_qa/RemoteNotificationBridge.kt` and `RemoteBackgroundService.kt` -- native preference persistence, validation, controlled save failure, and message-presentation gate.
- `test/codex_remote_test.dart`, `test/widget_test.dart`, and `test/remote_background_notification_config_test.dart` -- Settings, bridge, race, rollback, and Android boundary coverage.
- `integration_test/chat_detail_behavior_test.dart`, `integration_test/desktop_file_offer_download_flow_test.dart`, and `integration_test/last_screen_restore_test.dart` -- updated bridge fakes for the new contract.

**Review findings:** 6 patches applied (high 1, medium 5); 0 deferred; 14 rejected as non-actionable or duplicative. Follow-up review recommendation: `true` because this pass applied one high-severity compatibility fix (score: high finding present).

**Verification:**
- `flutter test test/widget_test.dart` — passed, 117 tests.
- `flutter test test/remote_background_notification_config_test.dart` — passed, 20 tests.
- `flutter analyze integration_test/chat_detail_behavior_test.dart integration_test/desktop_file_offer_download_flow_test.dart integration_test/last_screen_restore_test.dart` — passed.
- `flutter analyze lib/main.dart lib/settings_page.dart lib/remote_notification_bridge.dart` — only two pre-existing unused-element warnings in `main.dart`.
- `./gradlew :app:compileDebugKotlin` — succeeded (Gradle daemon log: `BUILD SUCCESSFUL`).
- `flutter test test/codex_remote_test.dart` — new Story 3 cases pass, but the full file retains five pre-existing pending-timer failures in `_ChatPageState._refreshRemoteStateAfterSend`.

**Residual risks:** Android service behavior is covered by focused Flutter/source-boundary tests and Kotlin compilation, but a real-device notification delivery/persistence run was not completed in this automated environment.
