# RC NATS Remote Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the `c:\code\rc` mobile-side NATS migration so `codex`, `claudecode`, and `openclaw` remote flows, plus Android background remote listening, all use NATS before any desktop-side NATS migration begins.

**Architecture:** Reuse the existing `RemoteNatsChatService` stack as the only remote transport path, migrate remaining model services off `RemoteSocketClient`, tighten endpoint validation to NATS-only shapes, and move Android background remote behavior onto the same transport semantics. Keep model-specific behavior in service/session code, not in the transport layer.

**Tech Stack:** Flutter, Dart, Kotlin, Android instrumentation tests, `dart_nats`, existing `RemoteSessionStore` / `RemoteControlSettings` / `RemoteEventDeduper` infrastructure.

---

## File Structure

- `c:\code\rc\lib\remote_control_settings.dart`
  - NATS-only endpoint normalization and migration policy
- `c:\code\rc\lib\remote_control_models.dart`
  - shared model constants/helpers for `codex`, `claudecode`, `openclaw`
- `c:\code\rc\lib\remote_nats_protocol.dart`
  - canonical command/event payload helpers
- `c:\code\rc\lib\remote_nats_client.dart`
  - low-level NATS connect/publish/subscribe/ack behavior
- `c:\code\rc\lib\remote_nats_chat_service.dart`
  - authoritative foreground remote service
- `c:\code\rc\lib\claudecode_chat_service.dart`
  - remove socket transport dependency, adapt to NATS
- `c:\code\rc\lib\main.dart`
  - session/model wiring for all three remote models
- `c:\code\rc\lib\remote_background_service.dart`
  - Flutter bridge facade for Android background runtime
- `c:\code\rc\android\app\src\main\kotlin\com\example\zhuge_qa\RemoteBackgroundBridge.kt`
  - NATS-only endpoint/config bridge and service start/stop
- `c:\code\rc\android\app\src\main\kotlin\com\example\zhuge_qa\RemoteBackgroundService.kt`
  - actual background runtime and notification behavior
- Tests:
  - `c:\code\rc\test\remote_control_settings_test.dart`
  - `c:\code\rc\test\remote_nats_protocol_test.dart`
  - `c:\code\rc\test\remote_nats_chat_service_test.dart`
  - `c:\code\rc\test\claudecode_chat_service_test.dart`
  - `c:\code\rc\test\openclaw_remote_routing_test.dart`
  - `c:\code\rc\test\remote_connection_bootstrap_test.dart`
  - `c:\code\rc\test\widget_test.dart`
  - `c:\code\rc\integration_test\nats_remote_sync_e2e_test.dart`
  - `c:\code\rc\integration_test\remote_ui_regression_test.dart`
  - `c:\code\rc\android\app\src\androidTest\kotlin\com\example\zhuge_qa\RemoteBackgroundServiceNotificationChannelTest.kt`

### Task 1: Lock Endpoint Policy To NATS-Only

**Files:**
- Modify: `c:\code\rc\lib\remote_control_settings.dart`
- Modify: `c:\code\rc\lib\clipboard_detector.dart`
- Modify: `c:\code\rc\lib\settings_page.dart`
- Test: `c:\code\rc\test\remote_control_settings_test.dart`
- Test: `c:\code\rc\test\remote_connection_bootstrap_test.dart`

- [ ] **Step 1: Write failing tests for NATS-only endpoint policy**

Add or update tests to assert:
- `/nats` endpoints remain valid
- `nats://host:4222` remains valid
- legacy `/ws` endpoints are rejected or rewritten only during one-time migration, not accepted as final runtime shape
- bootstrap logic surfaces only NATS-shaped endpoints

Target commands:

```powershell
flutter test test/remote_control_settings_test.dart test/remote_connection_bootstrap_test.dart
```

- [ ] **Step 2: Run the tests and confirm the current failures**

Expected failures:
- old `/ws` values still normalize to valid runtime endpoints in some paths
- bootstrap or clipboard ingestion still accepts legacy shapes

- [ ] **Step 3: Implement NATS-only normalization**

Implementation points:
- `RemoteControlSettings.normalizeEndpoint()` must only emit:
  - `nats://host[:port]`
  - `ws://host[:port]/nats`
  - `wss://host[:port]/nats`
- `clipboard_detector.dart` must reject non-NATS-shaped remote URLs
- settings UI hint/copy must stop advertising `/ws`

- [ ] **Step 4: Re-run the focused endpoint tests**

```powershell
flutter test test/remote_control_settings_test.dart test/remote_connection_bootstrap_test.dart
```

Expected: PASS

- [ ] **Step 5: Commit**

```powershell
git -C c:\code\rc add lib/remote_control_settings.dart lib/clipboard_detector.dart lib/settings_page.dart test/remote_control_settings_test.dart test/remote_connection_bootstrap_test.dart
git -C c:\code\rc commit -m "refactor: enforce nats-only remote endpoints on mobile"
```

### Task 2: Stabilize Shared NATS Protocol And Foreground Service

**Files:**
- Modify: `c:\code\rc\lib\remote_nats_protocol.dart`
- Modify: `c:\code\rc\lib\remote_nats_client.dart`
- Modify: `c:\code\rc\lib\remote_nats_chat_service.dart`
- Modify: `c:\code\rc\lib\remote_control_models.dart`
- Test: `c:\code\rc\test\remote_nats_protocol_test.dart`
- Test: `c:\code\rc\test\remote_nats_chat_service_test.dart`

- [ ] **Step 1: Add failing protocol/service tests for multi-model use**

Cover:
- `message`, `reply_request`, `state`, `history_read`, `new_chat`, `rename_chat`, `update_settings`
- command payload carries explicit model ids for `codex/main`, `claudecode/...`, and `openclaw/main`
- event handling continues to dedupe and ack safely

Run:

```powershell
flutter test test/remote_nats_protocol_test.dart test/remote_nats_chat_service_test.dart
```

- [ ] **Step 2: Verify failures or missing assertions**

Expected gaps:
- model metadata assumptions are too codex-centric
- tests do not yet prove `claudecode` and `openclaw` can use the same NATS service contract

- [ ] **Step 3: Generalize the shared NATS service layer**

Implementation points:
- extend model helpers in `remote_control_models.dart`
- keep `RemoteNatsChatService` model-agnostic
- ensure request builders and response/state application do not special-case codex transport

- [ ] **Step 4: Re-run focused service tests**

```powershell
flutter test test/remote_nats_protocol_test.dart test/remote_nats_chat_service_test.dart
```

Expected: PASS

- [ ] **Step 5: Commit**

```powershell
git -C c:\code\rc add lib/remote_nats_protocol.dart lib/remote_nats_client.dart lib/remote_nats_chat_service.dart lib/remote_control_models.dart test/remote_nats_protocol_test.dart test/remote_nats_chat_service_test.dart
git -C c:\code\rc commit -m "refactor: stabilize shared remote nats service for all models"
```

### Task 3: Migrate ClaudeCode Foreground Remote Flow Off WebSocket

**Files:**
- Modify: `c:\code\rc\lib\claudecode_chat_service.dart`
- Modify: `c:\code\rc\lib\main.dart`
- Test: `c:\code\rc\test\claudecode_chat_service_test.dart`
- Test: `c:\code\rc\test\widget_test.dart`

- [ ] **Step 1: Write failing tests for ClaudeCode NATS transport**

Add or rewrite tests so they assert:
- `RemoteClaudeCodeChatService` no longer depends on `RemoteSocketClient`
- consecutive sends/replies work through the shared NATS transport
- history/state refresh still updates `RemoteSessionStore`

Run:

```powershell
flutter test test/claudecode_chat_service_test.dart test/widget_test.dart
```

- [ ] **Step 2: Confirm the existing socket-based service fails the new expectations**

Expected failures:
- constructor and fake transport scaffolding still require `RemoteSocketClient`
- message flow remains socket-specific

- [ ] **Step 3: Replace socket dependency with shared NATS path**

Implementation points:
- refactor `RemoteClaudeCodeChatService` to depend on `RemoteNatsClient` or a small adapter over `RemoteNatsChatService`
- preserve approval/user-input/session semantics
- update `main.dart` so ClaudeCode remote sessions are wired through NATS-backed service construction

- [ ] **Step 4: Re-run ClaudeCode-focused tests**

```powershell
flutter test test/claudecode_chat_service_test.dart test/widget_test.dart
```

Expected: PASS

- [ ] **Step 5: Commit**

```powershell
git -C c:\code\rc add lib/claudecode_chat_service.dart lib/main.dart test/claudecode_chat_service_test.dart test/widget_test.dart
git -C c:\code\rc commit -m "refactor: migrate claudecode mobile remote flow to nats"
```

### Task 4: Make OpenClaw Explicitly Use The Shared NATS Path

**Files:**
- Modify: `c:\code\rc\lib\main.dart`
- Modify: `c:\code\rc\lib\remote_control_models.dart`
- Modify: `c:\code\rc\lib\remote_session_store.dart`
- Test: `c:\code\rc\test\openclaw_remote_routing_test.dart`
- Test: `c:\code\rc\test\remote_session_send_race_test.dart`

- [ ] **Step 1: Write failing tests for openclaw NATS invariants**

Assertions:
- starting a remote openclaw session uses `openclaw/main`
- sends, resumes, and race-condition recovery continue to use the shared remote service path
- no openclaw path depends on legacy websocket endpoint assumptions

Run:

```powershell
flutter test test/openclaw_remote_routing_test.dart test/remote_session_send_race_test.dart
```

- [ ] **Step 2: Confirm current openclaw coverage is incomplete**

Expected gap:
- routing tests may still pass through a generic codex service fake without proving the full NATS-only contract

- [ ] **Step 3: Tighten openclaw model routing**

Implementation points:
- ensure `main.dart` routes openclaw remote sessions through the shared NATS-backed service with explicit model ids
- ensure session-store updates and resume paths remain consistent after migration

- [ ] **Step 4: Re-run openclaw-focused tests**

```powershell
flutter test test/openclaw_remote_routing_test.dart test/remote_session_send_race_test.dart
```

Expected: PASS

- [ ] **Step 5: Commit**

```powershell
git -C c:\code\rc add lib/main.dart lib/remote_control_models.dart lib/remote_session_store.dart test/openclaw_remote_routing_test.dart test/remote_session_send_race_test.dart
git -C c:\code\rc commit -m "refactor: route openclaw mobile remote sessions through nats"
```

### Task 5: Migrate Android Background Runtime To NATS

**Files:**
- Modify: `c:\code\rc\android\app\src\main\kotlin\com\example\zhuge_qa\RemoteBackgroundBridge.kt`
- Modify: `c:\code\rc\android\app\src\main\kotlin\com\example\zhuge_qa\RemoteBackgroundService.kt`
- Modify: `c:\code\rc\lib\remote_background_service.dart`
- Modify: `c:\code\rc\lib\remote_notification_bridge.dart`
- Test: `c:\code\rc\android\app\src\androidTest\kotlin\com\example\zhuge_qa\RemoteBackgroundServiceNotificationChannelTest.kt`
- Test: `c:\code\rc\test\remote_background_notification_config_test.dart`

- [ ] **Step 1: Add failing tests for NATS-only background configuration**

Cover:
- bridge normalization rejects `/ws`
- saved background config is NATS-only
- background service start path no longer no-ops
- notification channel behavior still passes

Run:

```powershell
flutter test test/remote_background_notification_config_test.dart
flutter test test/remote_connection_bootstrap_test.dart
```

And Android instrumentation:

```powershell
flutter test --machine > $null
./gradlew app:connectedDebugAndroidTest -Ptarget=`"androidTest`" -Pandroid.testInstrumentationRunnerArguments.class=com.example.zhuge_qa.RemoteBackgroundServiceNotificationChannelTest
```

- [ ] **Step 2: Confirm background runtime gaps**

Expected failures or gaps:
- `RemoteBackgroundBridge.startService()` currently stops service without starting NATS listening
- `RemoteBackgroundService` is effectively a stub and exits immediately

- [ ] **Step 3: Implement background NATS runtime**

Implementation points:
- bridge persists only NATS config
- background service establishes NATS connection and listens for relevant event types
- service routes completion/status events into notification policy
- service shutdown/restart remains explicit and testable

- [ ] **Step 4: Re-run background verification**

```powershell
flutter test test/remote_background_notification_config_test.dart test/remote_connection_bootstrap_test.dart
./gradlew app:connectedDebugAndroidTest -Ptarget=`"androidTest`" -Pandroid.testInstrumentationRunnerArguments.class=com.example.zhuge_qa.RemoteBackgroundServiceNotificationChannelTest
```

Expected: PASS

- [ ] **Step 5: Commit**

```powershell
git -C c:\code\rc add android/app/src/main/kotlin/com/example/zhuge_qa/RemoteBackgroundBridge.kt android/app/src/main/kotlin/com/example/zhuge_qa/RemoteBackgroundService.kt lib/remote_background_service.dart lib/remote_notification_bridge.dart test/remote_background_notification_config_test.dart android/app/src/androidTest/kotlin/com/example/zhuge_qa/RemoteBackgroundServiceNotificationChannelTest.kt
git -C c:\code\rc commit -m "feat: move mobile background remote runtime to nats"
```

### Task 6: Remove Legacy Mobile WebSocket Remote Runtime Paths

**Files:**
- Modify: `c:\code\rc\lib\remote_socket_client.dart`
- Modify: `c:\code\rc\lib\claudecode_chat_service.dart`
- Modify: `c:\code\rc\lib\main.dart`
- Modify: `c:\code\rc\docs\current\remote-control.md`
- Modify: `c:\code\rc\docs\current\testing.md`
- Test: `c:\code\rc\test\widget_test.dart`

- [ ] **Step 1: Write or update tests that assert no primary runtime path depends on legacy WebSocket transport**

Run:

```powershell
flutter test test/widget_test.dart test/remote_control_settings_test.dart test/claudecode_chat_service_test.dart
```

- [ ] **Step 2: Remove or isolate dead runtime code**

Implementation points:
- if `remote_socket_client.dart` is no longer used by production runtime, either delete it or mark it as non-runtime leftover only if tests/tools still need it temporarily
- remove UI copy and docs that imply `/ws` is valid
- ensure app startup constructs NATS-backed remote services by default for all remote models

- [ ] **Step 3: Re-run focused cleanup tests**

```powershell
flutter test test/widget_test.dart test/remote_control_settings_test.dart test/claudecode_chat_service_test.dart
```

Expected: PASS

- [ ] **Step 4: Commit**

```powershell
git -C c:\code\rc add lib/remote_socket_client.dart lib/claudecode_chat_service.dart lib/main.dart docs/current/remote-control.md docs/current/testing.md test/widget_test.dart test/remote_control_settings_test.dart test/claudecode_chat_service_test.dart
git -C c:\code\rc commit -m "refactor: remove legacy mobile websocket remote runtime"
```

### Task 7: Run Full Mobile Verification Gate

**Files:**
- No code changes expected unless failures appear
- Verification touches all modified code and tests

- [ ] **Step 1: Run Dart and Flutter test suites relevant to remote NATS**

```powershell
flutter test test/remote_control_settings_test.dart test/remote_nats_protocol_test.dart test/remote_nats_chat_service_test.dart test/claudecode_chat_service_test.dart test/openclaw_remote_routing_test.dart test/remote_connection_bootstrap_test.dart test/remote_session_send_race_test.dart test/widget_test.dart
```

Expected: PASS

- [ ] **Step 2: Run Flutter integration tests**

```powershell
flutter test integration_test/nats_remote_sync_e2e_test.dart integration_test/remote_ui_regression_test.dart
```

Expected: PASS

- [ ] **Step 3: Run Android instrumentation coverage**

```powershell
./gradlew app:connectedDebugAndroidTest -Ptarget=`"androidTest`" -Pandroid.testInstrumentationRunnerArguments.class=com.example.zhuge_qa.RemoteBackgroundServiceNotificationChannelTest
```

Expected: PASS

- [ ] **Step 4: Run one strict end-to-end NATS smoke path**

Use the mobile integration path plus the desktop-side NATS harness endpoint intended for mobile verification. Record:
- endpoint used
- model used
- command accepted
- response received
- `state_changed`
- `history_changed`
- `final_answer`

- [ ] **Step 5: Fix any failing tests before proceeding**

Do not begin desktop migration until all failures are resolved.

- [ ] **Step 6: Commit final mobile completion state**

```powershell
git -C c:\code\rc status
git -C c:\code\rc commit -am "test: verify mobile nats remote migration is complete"
```

## Self-Review

- Spec coverage: the plan covers endpoint policy, shared service stabilization, `claudecode`, `openclaw`, Android background runtime, cleanup, and full verification.
- Placeholder scan: no `TODO`/`TBD` markers remain; each task names concrete files and commands.
- Type consistency: the plan keeps `CodexChatService`, `RemoteNatsChatService`, `RemoteClaudeCodeChatService`, and Android background bridge/service names aligned with the current codebase.
