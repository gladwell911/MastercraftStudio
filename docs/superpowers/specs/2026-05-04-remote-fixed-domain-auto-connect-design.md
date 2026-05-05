# 2026-05-04 Remote Fixed Domain Auto Connect Design

## Summary

This design makes the mobile app connect to the desktop app over the public internet without USB, `adb reverse`, or manual endpoint setup. The system uses one fixed public endpoint, `wss://rc.tingyou.cc/nats`, plus one fixed built-in token shared by exactly one desktop app and one mobile app.

The intended product behavior is:

- The desktop app automatically starts its local NATS runtime.
- The desktop app automatically publishes that runtime through `cloudflared` to `rc.tingyou.cc`.
- The desktop app only reports "remote available" after both local and public websocket health checks succeed.
- The mobile app launched from VSCode with `Ctrl+F5` uses the fixed public endpoint and fixed token by default.
- The mobile app automatically migrates stale local or test-only settings back to the fixed public configuration.
- Message sync, chat sync, and notes sync must all work over the public endpoint with no USB dependency.

## Scope

In scope:

- Desktop fixed-domain remote startup correctness
- Desktop public connectivity health checks
- Mobile startup configuration resolution
- Migration away from stale loopback and test settings
- Public-endpoint real-device verification on the existing phone device
- Automated regression coverage for the public connection path

Out of scope:

- Multi-user pairing
- Per-user tokens
- QR or clipboard pairing
- Arbitrary self-hosted public endpoints
- Replacing `rc.tingyou.cc` with a new infrastructure model

## Constraints

- There is exactly one desktop app and one mobile app.
- Both apps may continue to use the same built-in fixed token.
- The public endpoint remains `wss://rc.tingyou.cc/nats`.
- The system must not depend on USB or `adb reverse` for normal product behavior.
- Existing local test and harness flows may continue to exist, but they must not override the product default path in normal launches.

## Current Problems

### Product path and test path are mixed

Real-device tests currently succeed partly because they inject endpoint and token values through `--dart-define` and sometimes use `adb reverse`. A normal `Ctrl+F5` launch does not provide those overrides, so the installed app falls back to its persisted or default configuration path. That means product launches and test launches can behave differently even on the same phone.

### Desktop local readiness is not the same as public readiness

The desktop app already has logic for local NATS startup, local websocket ports, and `cloudflared` recovery. However, a phone on the public internet only cares whether `wss://rc.tingyou.cc/nats` is actually reachable and authenticated. Treating local listener readiness as "remote is ready" produces false positives.

### Stale persisted mobile settings can override the intended default

If the phone previously stored loopback endpoints, temporary test URLs, or stale quick tunnel values, a plain VSCode install may keep using those values instead of the fixed public configuration.

## Chosen Approach

Use the existing fixed-domain architecture and harden it into the default production path.

Why this approach:

- It matches the single-user, single-desktop, single-phone model.
- It requires the least product-surface change.
- It keeps existing NATS transport and sync code paths.
- It removes USB dependency without introducing a new pairing system.

## Desktop Design

### Startup contract

The desktop app must treat fixed-domain startup as successful only when all of the following are true:

1. Local NATS TCP transport is running.
2. Local websocket listener is running.
3. The `cloudflared` origin bridge points to the actual active websocket port.
4. The `cloudflared` service is running.
5. A public websocket probe against `wss://rc.tingyou.cc/nats?token=<fixed-token>` succeeds.

If any of these checks fail, the desktop app must keep the remote state in a failed or degraded state and expose a precise error reason.

### Public readiness state

The desktop runtime status object should continue to expose structured fields, but the meaning must be tightened:

- `enabled`: local remote runtime exists
- `websocket_url`: current local websocket listener URL
- `cloudflared_url`: fixed public base URL
- `published_url`: final public URL with token
- `public_ws_ready`: whether the public probe actually succeeded
- `last_error`: last startup or health-check failure reason

The UI and any automation should only treat the remote system as externally reachable if `public_ws_ready` is true.

### Error taxonomy

Desktop-side failures should be distinguishable at minimum as:

- `local_nats_start_failed`
- `local_ws_unreachable`
- `cloudflared_bridge_failed`
- `cloudflared_not_running`
- `public_ws_probe_failed`
- `authorization_failed`

Exact string names may vary, but the categories must remain distinguishable in code and tests.

### Port fallback behavior

The existing fallback behavior for local NATS ports should remain. If default local ports are occupied, the desktop app may select fallback ports, but it must still rebuild the `cloudflared` origin bridge to the chosen local websocket port before marking the public path ready.

## Mobile Design

### Startup configuration resolution

Normal app startup should resolve remote settings in this order:

1. If there is a valid fixed-domain configuration already persisted, use it.
2. If persisted settings are empty, loopback-based, stale quick tunnel based, or otherwise test-only, replace them with the fixed public configuration.
3. If startup settings are still incomplete after migration, fall back to the built-in fixed public configuration.

The resulting normal-launch configuration must converge to:

- endpoint: `wss://rc.tingyou.cc/nats`
- token: built-in fixed token

### Stale setting migration rules

The mobile app should automatically migrate these settings back to the fixed public path:

- `ws://127.0.0.1:*`
- `ws://localhost:*`
- test-only local harness URLs
- legacy quick tunnel URLs already recognized by the app
- empty endpoint with empty or whitespace token

The migration should happen before the first automatic remote bootstrap attempt.

### Connection behavior

On startup, the mobile app should:

1. Resolve settings through the fixed-domain migration path.
2. Sync those settings to background services.
3. Initialize the remote client.
4. Refresh history as the first end-to-end proof that the public path is alive.

If any stage fails, the app should surface a user-visible connection state rather than silently remaining disconnected.

### User-visible connection states

At minimum, the mobile app should differentiate:

- connecting
- connected
- desktop_not_published
- authorization_failed
- request_timeout
- disconnected

Exact wording may differ, but these conditions must be distinguishable in code and observable in tests.

## Cross-Component Data Flow

### Normal success path

1. Desktop app starts local NATS and websocket listener.
2. Desktop app rewires `cloudflared` to the active websocket port.
3. Desktop app verifies the public endpoint at `wss://rc.tingyou.cc/nats?token=<fixed-token>`.
4. Mobile app starts and resolves settings to the fixed public endpoint.
5. Mobile app connects to the same public endpoint using the built-in token.
6. Mobile app refreshes chat history successfully.
7. Notes sync, chat creation, rename, and message sync use the same transport thereafter.

### Failure path

1. Desktop app fails local startup or public publish.
2. Desktop app records a structured runtime error.
3. Mobile app attempts the public endpoint.
4. Connection or request fails with a distinct status.
5. The user sees an actionable failure state instead of a false connected state.

## Testing Design

### Desktop automated coverage

Add or extend unit tests to verify:

- Fixed-domain startup does not mark the runtime publicly ready when the public websocket probe fails.
- Successful public probe marks the runtime publicly ready and preserves the published URL.
- Fallback local websocket ports still trigger bridge rebuild against the chosen port.
- Cloudflared restart and recovery transitions the runtime from failed to ready.

### Mobile automated coverage

Add or extend tests to verify:

- Startup settings migration rewrites loopback and stale test URLs to the fixed public endpoint.
- Startup bootstrap prefers the fixed public endpoint when persisted settings are invalid.
- Connection status mapping differentiates auth failure, timeout, and unpublished desktop conditions.

### Real-device strict verification

Use the existing real device. The validation path must not use `adb reverse` for the actual business connection.

Required real-device checks:

1. The phone launched from VSCode `Ctrl+F5` connects through `wss://rc.tingyou.cc/nats`.
2. Phone sends a message and desktop receives it.
3. Desktop-created chat appears on phone.
4. Phone-created chat appears on desktop.
5. Desktop-created note appears on phone.
6. Phone-created note appears on desktop.
7. If desktop public publish is intentionally unavailable, the phone shows the expected failure state.

The pass condition is not log inspection alone. Tests must assert state on both sides, and the desktop runtime status must confirm public readiness when the happy path succeeds.

## Implementation Plan Boundaries

The implementation plan should be split into these work items:

1. Desktop public readiness hardening
2. Mobile fixed-domain startup convergence and migration
3. Test updates for startup resolution and runtime health
4. Strict real-device public-endpoint verification

This is intentionally scoped to a single implementation cycle because it is one product capability: fixed-domain automatic public connectivity.

## Risks

- Existing tests that assume loopback endpoints on normal startup may need updates.
- Persisted phone settings from earlier manual testing may mask intended behavior until migration is added.
- `cloudflared` service state on the desktop machine can make failures intermittent if not normalized during startup.
- Using one fixed token remains a security tradeoff, but it is accepted for this single-device model.

## Acceptance Criteria

- A phone installed from VSCode `Ctrl+F5` can connect to the desktop app over the public internet without USB.
- No `adb reverse` is needed for the normal product path.
- Desktop remote readiness is tied to successful public websocket verification, not merely local port readiness.
- Mobile startup automatically converges to the fixed public endpoint and token.
- Chat, messages, and notes all synchronize over the public endpoint.
- Automated tests cover the runtime readiness and settings migration logic.
- Real-device automated validation passes against the public endpoint and covers all modified behaviors.
