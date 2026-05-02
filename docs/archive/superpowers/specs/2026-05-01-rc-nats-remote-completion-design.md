# RC NATS Remote Completion Design

## Goal

Complete the mobile-side NATS remote control migration in `c:\code\rc` before migrating the desktop app. After this work, `codex`, `claudecode`, and `openclaw` remote sessions on mobile must use one NATS-based transport model, and Android background remote listening and notifications must use the same transport. The mobile app will no longer support legacy `ws://.../ws` remote endpoints.

## Scope

This design covers:

- Flutter foreground remote control for `codex`, `claudecode`, and `openclaw`
- Shared remote endpoint parsing and validation
- Remote session/history/state synchronization
- Android background listener and notification flow
- Flutter, integration, and Android instrumentation test coverage

This design does not yet migrate the desktop app to NATS on `main`. Desktop migration starts only after the mobile app is functionally complete and verified.

## Current Context

The current `rc` workspace is in a mixed state:

- `codex` foreground remote flow is already centered around `RemoteNatsChatService`
- `claudecode` still depends on `RemoteSocketClient`
- Some tests and archived docs still assume legacy `/ws` endpoints
- Android background code has partial NATS normalization logic, but the full runtime behavior must be treated as unfinished until verified end to end

The repo also has a `feature/nats-jetstream-sync` branch that contains earlier NATS migration work. That branch is useful as a source of code and tests, but the migration must be completed against the current `master` state instead of merged blindly.

## Requirements

### Functional

1. `codex`, `claudecode`, and `openclaw` remote model flows must all send commands and consume events through NATS.
2. Foreground and background remote behavior must share the same event semantics:
   - `response`
   - `status`
   - `state_changed`
   - `final_answer`
   - `history_changed`
   - request/reply style prompts where applicable
3. Remote settings must accept only:
   - `nats://host:port`
   - `ws://host[:port]/nats`
   - `wss://host[:port]/nats`
4. Legacy `/ws` endpoints must no longer be treated as valid runtime inputs.
5. History refresh, state hydration, and remote session resume must still work after reconnect.
6. Android background service must connect through NATS and still surface remote notifications correctly.

### Non-Functional

1. Migration must preserve current app-level UX behavior as closely as possible.
2. Tests must prove correctness before desktop migration begins.
3. The code should converge on one mobile remote transport abstraction rather than parallel long-lived WebSocket and NATS stacks.

## Chosen Approach

Use the current `RemoteNatsChatService` and related protocol/client files as the authoritative remote transport path, then migrate remaining remote model services and Android background code onto the same protocol family.

The key design decision is to remove transport ambiguity. The mobile app should no longer decide between old WebSocket transport and NATS transport at runtime. Instead:

- endpoint normalization produces only NATS-shaped endpoints
- foreground remote services publish commands and consume events through NATS
- background services consume the same event stream semantics through NATS
- model-specific code stays responsible for request semantics, not transport semantics

## Architecture

### 1. Shared Remote Transport Layer

Files centered here:

- `lib/remote_nats_protocol.dart`
- `lib/remote_nats_client.dart`
- `lib/remote_nats_chat_service.dart`
- `lib/remote_control_settings.dart`

Responsibilities:

- normalize and validate endpoint/token settings
- build command payloads with request ids, device ids, chat ids, and model metadata
- consume desktop events with replay-safe dedupe behavior
- expose a model-agnostic command/event interface to higher-level chat services

The transport layer remains NATS-specific. Higher layers should not know how subjects, subscriptions, or acknowledgements work internally.

### 2. Foreground Chat Service Layer

Files centered here:

- `lib/codex_chat_service.dart`
- `lib/claudecode_chat_service.dart`
- `lib/main.dart`
- model/session wiring code that chooses remote transport

Responsibilities:

- route model-specific actions onto the shared NATS command set
- interpret response payloads into session store updates
- keep UI session behavior consistent across all three remote models

`claudecode` must stop using `RemoteSocketClient`. If `openclaw` still relies on any legacy transport-only branch, it must be converted to the same NATS service path or to a thin adapter over the NATS service layer.

### 3. Android Background Remote Layer

Files centered here:

- `android/app/src/main/kotlin/com/example/zhuge_qa/RemoteBackgroundBridge.kt`
- `android/app/src/main/kotlin/com/example/zhuge_qa/RemoteBackgroundService.kt`
- `lib/remote_background_service.dart`
- `lib/remote_notification_bridge.dart`

Responsibilities:

- accept normalized NATS endpoint settings from Flutter
- connect/reconnect in background mode
- listen for remote completion/status events
- trigger background notification behavior without requiring the foreground Flutter tree

The background layer must not preserve a hidden legacy WebSocket fallback. If NATS connection fails, it reports failure directly through the existing disconnected/error status path.

### 4. Test Harness Layer

Files centered here:

- `test/remote_nats_protocol_test.dart`
- `test/remote_nats_chat_service_test.dart`
- `test/remote_control_settings_test.dart`
- relevant widget/integration tests in `integration_test/`
- Android instrumentation coverage for background behavior

Responsibilities:

- validate protocol shapes and endpoint normalization
- validate service behavior for all target models
- validate reconnect/history/state workflows
- validate background notification/channel/service behavior

## Data And Protocol Rules

The mobile app should assume one remote protocol family for all remote models:

- Commands are request/response oriented and identified by request id.
- Desktop-published events are replayable and deduped by event id.
- `history_changed` remains a refresh trigger, not a full snapshot payload.
- `state_changed` remains the authoritative path for refreshing active chat state.
- `final_answer` remains the trigger for completed assistant output and notification flow.

Model-specific differences should stay in command metadata or downstream UI interpretation, not in transport format.

## Endpoint Policy

Accepted endpoints:

- `nats://host:4222`
- `ws://host:8081/nats`
- `wss://host/nats`

Rejected endpoints:

- `ws://host/ws`
- `wss://host/ws`
- bare host values that normalize to `/ws`

If there is legacy data stored in preferences, the migration layer may rewrite it once into NATS form if the result is unambiguous. Runtime behavior after migration should treat `/ws` endpoints as invalid configuration rather than supported configuration.

## Migration Strategy

### Phase 1: Stabilize Shared NATS Path

- Audit the current `RemoteNatsChatService` behavior against the latest mobile app expectations.
- Remove test and code assumptions that still require `RemoteSocketClient` for the primary remote path.
- Tighten endpoint normalization so settings and clipboard ingestion always converge on NATS endpoints.

### Phase 2: Migrate `claudecode`

- Replace `RemoteClaudeCodeChatService` transport dependency from `RemoteSocketClient` to the shared NATS transport path.
- Preserve existing request/approval/session semantics while changing only transport concerns.
- Add focused tests for message send, reply request, state refresh, history refresh, and reconnect behavior.

### Phase 3: Migrate `openclaw`

- Audit how remote `openclaw` sessions are currently represented in mobile state and route them onto the same NATS command/event flow.
- Remove any transport-specific branching that assumes only codex uses the unified remote path.
- Add tests covering remote openclaw send/resume/history behavior.

### Phase 4: Migrate Android Background Runtime

- Update the bridge/service pipeline so background remote listening uses NATS endpoint normalization and NATS event consumption.
- Verify background notification triggers for relevant event types, especially `final_answer` and waiting-for-user-input style states.
- Keep notification UX stable while changing transport internals.

### Phase 5: Remove Legacy Remote WebSocket Runtime Paths

- Remove or isolate dead mobile runtime code that exists only for the old `/ws` transport.
- Update settings UI copy, validation, documentation, and tests to reflect pure NATS support.
- Keep archived docs untouched except where they interfere with current documentation or automated checks.

## Error Handling

- Invalid endpoint formats should fail early in settings normalization or connection startup.
- Failed NATS connection should set disconnected state with a readable error.
- Event processing failures during history/state refresh should preserve replay safety where required.
- Background listener failures should not silently downgrade to another transport.

## Testing Strategy

The mobile-side migration is not considered complete until all of the following are green:

1. Dart unit tests for:
   - endpoint normalization
   - NATS protocol encoding/decoding
   - remote chat service behavior
   - model-specific remote service behavior

2. Flutter integration tests for:
   - remote startup
   - remote chat flow
   - history refresh
   - reconnect and resume

3. Android instrumentation tests for:
   - background service startup/shutdown
   - notification channel behavior
   - background event handling for remote completion/status cases

4. One strict NATS E2E smoke path that proves:
   - mobile connects
   - sends command
   - receives response
   - receives state/history/final-answer style events

## Risks

1. `claudecode` and `openclaw` may have hidden assumptions tied to the older mobile socket message flow.
2. Background service logic may have partial NATS normalization without full runtime parity.
3. Old tests may assert WebSocket-specific details instead of user-visible behavior, requiring careful rewriting instead of blind deletion.
4. Branch drift between `master` and `feature/nats-jetstream-sync` may hide useful fixes in one place and regressions in another.

## Acceptance Criteria

The `rc` migration is complete when:

- all three remote model families use NATS transport
- no production mobile runtime path depends on legacy `/ws` remote transport
- Android background remote listening also uses NATS
- the agreed automated verification layers all pass
- only then do we begin desktop-side NATS migration on `mc`
