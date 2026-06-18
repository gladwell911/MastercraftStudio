# Desktop Reader Quiet Window Design

## Problem

The wxPython desktop app can feel sluggish to screen reader users while Codex is running in the background. The visible symptoms are delayed Tab navigation, delayed arrow-key navigation, and occasional focus or list instability while background Codex events are being processed.

The root cause is that background execution events, list rendering, state persistence, and user navigation all compete for the same wx main thread and the same accessibility-visible control tree. Even when Codex work runs on background threads, frequent `wx.CallAfter` callbacks can still mutate list boxes, repaint controls, or restore selection while the user and screen reader are navigating.

The fix is to make foreground keyboard navigation authoritative. Background work may continue, but background-triggered UI mutations must pause during a short quiet window after navigation keys.

## Goals

- Keep Tab, Shift+Tab, arrow-key, Enter, Space, and configured Alt shortcut navigation responsive while Codex runs.
- Prevent background events from mutating wx controls for 3 seconds after a navigation-trigger key.
- Preserve command behavior for the user's own key press. For example, Enter still opens the selected chat and Alt+A still clears the visible chat immediately.
- Keep execution steps ordered and visible at the tail after the quiet window ends.
- Avoid changing the existing execution-process experience except for deferring background-driven list appends during active keyboard browsing.
- Add automated tests that prove background UI operations do not touch list controls during the quiet window.

## Non-Goals

- Do not replace wxPython.
- Do not redesign the execution-process page.
- Do not remove the existing execution list tail behavior.
- Do not make every UI update delayed; user-initiated UI changes must remain immediate.
- Do not move Codex to a separate process in the first implementation phase. Process isolation is a later phase after scheduler boundaries are in place.

## Quiet Window

Add a `NavigationQuietWindow` owned by the UI process.

It records:

- `quiet_until`: monotonic timestamp until which background UI updates are blocked.
- `last_trigger`: optional debug label for the key that started or extended the quiet window.
- counters for deferred events by category, used only for diagnostics and tests.

Any matching key event sets:

```text
quiet_until = now + 3 seconds
```

Repeated key events extend the window.

Trigger keys:

- Tab
- Shift+Tab
- Up
- Down
- Left
- Right
- Home
- End
- PageUp
- PageDown
- Enter
- Space
- Alt+A
- Alt+S
- Alt+D
- Alt+F
- Alt+G
- Alt+B
- Alt+C

The quiet window is entered before the key's command handler runs:

```text
on_key_down(event):
    if is_quiet_trigger(event):
        quiet_window.touch(event)
    handle_user_key_command(event)
```

This ordering prevents a pending background drain from sneaking in between key classification and command handling.

## Foreground vs Background Rule

The quiet window blocks only background-triggered UI mutations.

Allowed during quiet window:

- The current user key's direct UI effect.
- Focus movement caused by Tab or Shift+Tab.
- Opening the selected chat with Enter.
- Activating the selected item with Space.
- Clearing the visible chat with Alt+A.
- Any synchronous UI update that is necessary to complete the user command.

Blocked during quiet window:

- Background Codex token delta rendering.
- Background answer row refresh.
- Background history list refresh or reordering.
- Background context usage refresh.
- Background execution list append or rebuild.
- Background-triggered `Refresh`, `SetSelection`, `SetFocus`, `Clear`, `Append`, or `replace_visible_page`.

Background events still update stores and dirty flags. They just cannot mutate wx controls until the quiet window expires.

## UI Scheduler

Introduce a single `UiScheduler` as the only path from background work to wx controls.

Priorities:

```text
P0 foreground_input_and_focus
P1 deferred_execution_tail_append
P2 visible_final_answer
P3 visible_answer_delta
P4 history_context_background
```

Rules:

- P0 is not queued as background work; it is handled by the wx event currently being processed.
- If `quiet_window.is_active()` is true, P1-P4 do not mutate controls.
- P1-P4 events may update `ChatRuntimeStore`, compact pending state, and set dirty flags.
- When quiet ends, the scheduler drains with an 8-16ms time budget per wx turn.
- If the user presses another trigger key while draining, the drain stops and the quiet window restarts.

Compaction:

- Answer delta keeps the latest merged content per `(chat_id, turn_id)`.
- History refresh keeps a dirty flag and selected chat id.
- Context usage keeps only the latest value per chat.
- Execution steps keep ordered lightweight row records; large details live in the existing store/detail path.

## Execution Process List

The execution process list keeps its tail semantics.

New execution step flow:

```text
execution_step event
  -> append to ChatRuntimeStore immediately
  -> if quiet window active:
       mark pending_execution_append for chat/turn
     else if execution list is visible for the same chat/turn:
       append row to execution_list_model tail immediately
     else:
       mark execution list dirty
```

When quiet ends:

```text
if pending_execution_append exists:
    append rows with seq > last_rendered_execution_seq in sequence order
    repaint lightly
```

Constraints:

- Do not full rebuild while a tail append is sufficient.
- Do not call `SetFocus`.
- Do not change user selection while the execution list has focus.
- If the list is not visible, do not touch the wx list box; only mark dirty.
- Use stable row ids, such as `execution:{chat_id}:{turn_id}:{step_seq}`.

This means execution steps may be delayed up to 3 seconds while the user is actively navigating, but they still appear at the tail and in order when the UI becomes safe to update.

## State Boundaries

Add or formalize three boundaries:

### ChatRuntimeStore

Owns per-chat state:

- turns
- execution steps
- context usage
- tail notices
- pending UI flags

The store is keyed by `chat_id`. UI-only global fields must not determine which chat receives a background update.

### VisibleChatController

Owns foreground state:

- active vs history mode
- visible chat id
- detail panel mode
- focused primary control

It answers questions such as "is this event for the visible chat?" without list renderers needing to inspect `ChatFrame` internals.

### List Renderers

Own wx list mutations:

- `AnswerListRenderer`
- `HistoryListRenderer`
- `ExecutionListRenderer`

They receive view models with stable row ids and perform minimal diffs. They do not know how Codex works.

## Future Process Isolation

After scheduler boundaries are stable, move Codex execution to a worker process.

Worker responsibilities:

- launch and manage Codex
- run shell commands
- read stdout/stderr
- parse Codex events
- write execution logs
- send structured IPC events

UI process responsibilities:

- consume IPC events
- update `ChatRuntimeStore`
- schedule foreground-safe UI changes
- serve keyboard and screen reader interaction

The worker must never call wx APIs and must never send "refresh this control" commands. It sends domain events only.

Example event:

```json
{"type":"execution_step","chat_id":"c","turn_id":"t","step_seq":42,"list_text":"run pytest","detail_ref":"..."}
```

## Test Plan

Unit tests:

- Quiet trigger detection covers Tab, Shift+Tab, arrows, Home/End/PageUp/PageDown, Enter, Space, and Alt+A/S/D/F/G/B/C.
- Quiet window extends on repeated trigger keys.
- Background answer delta updates store but does not mutate answer list during quiet.
- Background history update sets dirty flag but does not mutate history list during quiet.
- Background context usage update sets latest value but does not mutate visible rows during quiet.
- Background execution step updates store and pending append but does not mutate execution list during quiet.
- Quiet expiration appends pending execution rows to the tail in sequence order.

UI automation tests:

- While answer list has focus, repeated arrow keys keep extending quiet; background events do not call `Refresh`, `SetSelection`, `SetFocus`, `Clear`, `Append`, or `replace_visible_page`.
- While execution list has focus, a background execution step during quiet does not mutate the list immediately.
- After 3 seconds without trigger keys, pending execution rows appear at the tail.
- Focus remains on the user's current control after quiet drain.

Performance tests:

- Simulate high-frequency Codex events while sending Tab/arrow keys.
- Verify key handling P95 under 50ms and P99 under 100ms.
- Verify each scheduler drain stays within the configured time budget or yields.

Manual accessibility checks:

- NVDA and Windows Narrator can browse answer/history/execution lists while Codex runs.
- No unexpected focus jumps during background execution.
- Execution steps still appear at the tail after navigation pauses.

## Rollout

Phase 1:

- Add `NavigationQuietWindow`.
- Route background UI work through `UiScheduler`.
- Block background control mutations during quiet.
- Add tests around quiet behavior.

Phase 2:

- Add `ChatRuntimeStore` boundaries for pending UI state.
- Convert answer/history/execution updates to stable row ids and minimal diffs where missing.
- Keep execution list tail append behavior.

Phase 3:

- Add scheduler metrics and debug diagnostics.
- Add automated high-frequency event plus keyboard-navigation stress tests.

Phase 4:

- Move Codex execution into a worker process.
- Replace direct background `wx.CallAfter` paths with IPC domain events.

## Risks

- A too-broad quiet window can make the UI feel stale while the user holds arrow keys. This is intentional for screen reader stability, but pending state must be compacted to avoid memory growth.
- Quiet expiration can create a burst of delayed updates. The scheduler must use a time budget and stop immediately if a new trigger key arrives.
- Execution steps can pile up during long navigation sessions. Store only lightweight rows in memory and keep large details in the existing detail storage path.
- Existing code paths may still call wx controls directly from background callbacks. Tests must monkeypatch wx list operations during quiet to catch bypasses.

## Acceptance Criteria

- During the 3-second quiet window, background events do not mutate wx controls.
- User commands such as Enter and Alt+A still update the foreground UI immediately.
- Execution steps generated during quiet are appended to the execution list tail after quiet expires.
- Focus and selection do not jump due to background events.
- Keyboard navigation remains responsive under simulated Codex event load.
