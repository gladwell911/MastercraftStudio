# Codex Worker Process Isolation Design

## Problem

The wxPython desktop process still owns too much background execution work. Even after
background UI updates are protected by the reader quiet window, the UI process can
still create `CodexAppServerClient`, launch and manage the Codex app-server, read its
stdio streams, parse Codex events, and answer Codex-side pending input requests.

That means heavy Codex activity can still compete with the foreground process that
screen readers depend on for Tab, arrow-key, Enter, Space, Alt+A, and other navigation
shortcuts.

The next phase moves Codex transport and execution orchestration out of the wx process
and into a dedicated worker process. The UI process remains the owner of visible state,
keyboard handling, accessibility behavior, and persistence.

## Goals

- Keep the wx UI process responsive while Codex is executing.
- Ensure Codex app-server launch, stdout/stderr reads, event parsing, and turn
  orchestration happen outside the UI process.
- Preserve the existing execution-process list behavior: new execution processes must
  be appended to the tail immediately when the UI is not inside the navigation quiet
  window.
- Preserve the 3-second reader quiet-window rule for Tab, arrows, Enter, Space, Alt+A,
  Alt+S, Alt+D, Alt+F, Alt+G, Alt+B, and Alt+C.
- Prevent cross-chat state updates by keeping the UI process as the source of truth for
  chat identity, visible chat selection, archived chat state, and persistence.
- Make worker failure recoverable without crashing or freezing the UI process.

## Non-Goals

- Do not replace wxPython.
- Do not redesign the execution-process list.
- Do not change the user-facing behavior of clear context, visible chat selection, or
  execution-process tail append semantics.
- Do not move wx control mutations into the worker.
- Do not let the worker write chat archives or mutate UI-owned persistent state.
- Do not introduce a network service for local IPC in this phase.

## Reviewed Risks and Adjustments

The initial process-isolation sketch is directionally correct, but four areas need
explicit guardrails.

### Pending User Input

Current code can reply to Codex pending input by directly calling
`CodexAppServerClient.respond_tool_request_user_input(...)`. After isolation, both local
dialogs and remote pending-request replies must go through UI-to-worker IPC.

This avoids recreating an in-process Codex client only for pending input.

### IPC Backpressure

Moving Codex to a worker process does not by itself solve UI stutter if the worker can
flood the UI process with unbounded JSON messages. The protocol must support event
coalescing and bounded delivery on the UI side.

The UI process may compact high-frequency answer deltas, but it must not compact
execution-process rows that represent distinct steps. Execution rows are delivered in
sequence order.

### Recovery Ownership

Thread-missing and rollout-missing recovery need one owner. Splitting recovery policy
between UI and worker would make cross-chat bugs more likely.

The worker owns Codex transport-level recovery and reports the resulting thread/turn
state back to the UI. The UI remains responsible for persisting that state under the
correct `chat_id`.

### State Ownership

The worker must not become a second source of truth for archived chats or visible chat
state. Every worker event must carry an explicit `chat_id`, and the UI process must
apply the event only to that chat's runtime store.

This is the same protection needed to avoid the earlier clear-context cross-chat bug.

## Architecture

### UI Process

The wx process owns:

- wx controls and all focus movement.
- keyboard shortcut handling.
- reader quiet-window tracking.
- `ChatRuntimeStore` and visible-chat selection.
- archived chat persistence.
- answer, history, context, and execution-process renderers.
- remote sync and remote pending-request reply routing.
- worker process lifecycle.

The UI process must not create `CodexAppServerClient` directly after this phase.

### Worker Process

The worker process owns:

- `CodexAppServerClient` instances.
- `codex app-server` lifecycle.
- Codex stdout/stderr reading.
- Codex event parsing.
- Codex turn start, steer, and recovery orchestration.
- Codex pending input response delivery.

The worker process must not import `wx`.

### Shared Protocol Module

Add a small protocol module that defines JSON-serializable message helpers and schema
validation for UI-to-worker and worker-to-UI messages.

This module must avoid wx imports and should be usable by both processes and unit
tests.

## IPC Transport

Use UTF-8 JSON Lines over worker stdio.

Each line is one JSON object. Every request from the UI includes:

```json
{"id":"req-123","type":"start_turn","payload":{}}
```

Worker replies or events include:

```json
{"id":"req-123","type":"thread_state","payload":{}}
```

Long-running turn events do not need to wait for a single final response. They stream
as domain events with explicit chat identity.

### UI to Worker Messages

- `start_turn`: start or continue a Codex turn for one chat.
- `reply_user_input`: answer a Codex pending input request.
- `cancel_turn`: request cancellation for one chat/turn.
- `shutdown`: ask the worker to exit cleanly.
- `ping`: health check.

### Worker to UI Messages

- `ready`: worker initialized and ready for commands.
- `event`: serialized Codex event for a specific chat.
- `thread_state`: latest Codex thread/turn state for a specific chat.
- `request_user_input`: pending user input request that must be shown or routed by UI.
- `turn_finished`: worker considers the turn complete.
- `error`: recoverable failure for one chat/turn.
- `fatal`: worker-level failure.
- `pong`: health-check response.

## Message Identity

Every turn-related message must carry:

- `chat_id`
- `turn_idx`
- `thread_id` when known
- `turn_id` when known
- `model`
- `request_id` for request/response correlation

The UI process must ignore or quarantine malformed messages that lack `chat_id` for
chat-scoped operations.

## Data Flow

### Start Turn

```text
user sends prompt
  -> UI creates/updates the chat turn in ChatRuntimeStore
  -> UI sends start_turn to worker with chat_id, turn_idx, question, model, cwd,
     current thread state, attachments, and input items
  -> worker starts or steers Codex
  -> worker streams Codex events back to UI
  -> UI dispatches events through the existing queued UI event path
```

The existing `_dispatch_codex_event_to_ui` and quiet-window drain behavior remain the
UI-side event gateway.

### Execution Process Event

```text
worker sends execution event
  -> UI applies it to ChatRuntimeStore for event.chat_id
  -> if visible and quiet window is inactive:
       append execution row to list tail immediately
     else:
       mark pending execution rows for later tail append
```

Distinct execution rows must not be coalesced away.

### Pending User Input

```text
worker sends request_user_input
  -> UI shows local dialog or accepts remote reply
  -> UI sends reply_user_input to worker
  -> worker calls Codex client respond_tool_request_user_input
```

No UI code should directly call `respond_tool_request_user_input` after this phase.

## Backpressure and Coalescing

The UI-side worker client reads worker stdout on a non-wx thread and enqueues parsed
messages into bounded per-category queues.

Rules:

- Answer deltas may be compacted to the latest content per `(chat_id, turn_id)`.
- Context usage may be compacted to the latest value per chat.
- History refresh may be represented by a dirty flag.
- Execution-process rows are not compacted across distinct row ids.
- Worker stderr logs are rate-limited before they reach UI diagnostics.
- If the UI queue exceeds a configured safety threshold, the UI records a diagnostic
  warning and compacts all compactable categories before scheduling another drain.

The reader quiet window still decides when queued background events can mutate wx
controls.

## Lifecycle

The UI process lazily starts the worker on the first Codex request.

Startup:

```text
UI starts python -m codex_worker_process
UI waits for ready with timeout
UI marks worker healthy
```

Shutdown:

```text
UI sends shutdown
waits for worker exit
terminates after timeout if needed
```

Crash handling:

- Mark in-flight turns owned by the crashed worker as interrupted.
- Keep the UI process alive.
- Allow a later turn to start a fresh worker.
- Surface a concise error in the affected chat only.

## Thread and Turn Recovery

The worker owns Codex transport-level recovery because it owns the Codex client and
app-server state.

The UI sends the current known thread state with `start_turn`. If the worker detects a
missing thread, missing rollout, or inactive turn mismatch, it may start a new Codex
thread using the same chat-scoped request data and then emits `thread_state` for the
new ids.

The UI persists only the `thread_state` attached to the same `chat_id` and `turn_idx`.
It must not update another chat because that chat is active, focused, or first in the
history list.

## Implementation Boundaries

Expected new modules:

- `codex_worker_protocol.py`: message schemas, serialization, validation.
- `codex_worker_process.py`: worker process entry point and command loop.
- `codex_worker_client.py`: UI-side process manager and reader thread.

Expected UI changes:

- Replace direct `CodexAppServerClient` construction with `CodexWorkerClient`.
- Route local and remote pending input replies through worker IPC.
- Preserve `_dispatch_codex_event_to_ui` as the UI-side event gateway.
- Keep clear-context and visible-chat ownership in the UI process.

## Testing Plan

Unit tests:

- Protocol serialization and validation rejects malformed chat-scoped messages without
  `chat_id`.
- Worker process module does not import `wx`.
- UI code no longer directly constructs `CodexAppServerClient`.
- Pending local dialog replies and remote replies send `reply_user_input` IPC messages.
- Worker crash marks only the affected chat as interrupted.
- Thread state messages update only their matching `chat_id`.

Worker integration tests:

- Start a fake worker process and verify `ready`, `ping`, `start_turn`, streamed events,
  and `shutdown`.
- Simulate high-frequency answer delta events and verify UI-side compaction.
- Simulate ordered execution-process rows and verify they remain ordered and unlost.
- Simulate pending user input and verify reply routing reaches the worker.

UI automation tests:

- While Codex events stream from a worker, Tab and arrow-key navigation remain
  responsive.
- During the 3-second quiet window, worker events do not mutate wx list controls.
- After the quiet window expires, pending execution rows appear at the execution list
  tail in sequence order.
- Existing clear-context tests still prove chat-scoped state does not cross between
  chats.

Regression tests:

- Clear context in visible history chat still clears that chat, not the running chat at
  the top of history.
- The "new session started" notice appears in the cleared chat, not another chat.
- Existing shortcut, history navigation, reader quiet-window, and execution tail tests
  continue to pass.

End-to-end simulator test:

- Run the mobile-to-desktop clear-context E2E flow with the desktop app using the worker
  process.
- Verify no answer list or notice is written to the wrong chat.
- Verify Codex worker startup and shutdown do not block foreground navigation.

## Acceptance Criteria

- The wx UI process does not instantiate `CodexAppServerClient`.
- Codex app-server stdout/stderr reads occur only in the worker process.
- The worker process has no wx dependency.
- Codex pending user input replies go through UI-to-worker IPC.
- Worker events are always chat-scoped before they reach UI state mutation.
- Background Codex execution does not break the existing reader quiet-window behavior.
- Execution-process rows still append to the tail immediately when the quiet window is
  inactive, and append in order after quiet-window expiration when it is active.
- Worker crash or restart affects only the chats with in-flight worker-owned turns.

## Rollout

Phase 1:

- Add protocol helpers and a fake-worker test harness.
- Add `CodexWorkerClient` behind an internal adapter while preserving the existing UI
  event gateway.

Phase 2:

- Add the real worker entry point and move Codex client creation, app-server lifecycle,
  and stdio/event parsing into the worker process.
- Route pending user input replies through IPC.

Phase 3:

- Move turn orchestration and transport-level recovery fully into the worker.
- Remove direct `CodexAppServerClient` construction from the UI process.

Phase 4:

- Add crash recovery, queue pressure diagnostics, and full simulator regression tests.

The implementation is not accepted until Phase 3 criteria are met. Earlier phases are
allowed only as internal checkpoints, not as the final answer to the user's goal.
