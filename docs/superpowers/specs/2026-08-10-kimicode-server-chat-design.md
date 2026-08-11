# Kimi Code Server Chat Design

## Problem

The app already chats with the Codex CLI through a dedicated worker-process pipeline
(`codex_client.py` + `codex_worker_process.py` + `codex_worker_client.py`). We want the
same kind of chat experience backed by the Kimi Code CLI, exposing it as a new model
family (`kimi/...`) in the existing model combo box, reusing the question list, answer
list, execution-process list, and all surrounding behaviors (multi-chat, resume after
restart, interrupt, steer, slash commands).

## Chosen transport: `kimi web` local server

Kimi Code CLI (>= 0.28) ships `kimi web`: a single local process exposing a REST API
plus a WebSocket event stream on `127.0.0.1`. Verified against kimi 0.33.0 on this
machine (`GET /openapi.json`, `GET /asyncapi.json`):

- `POST /api/v1/sessions` — create a session. Body supports `metadata.cwd`,
  `agent_config.model`, `agent_config.permission_mode` (`manual|yolo|auto`),
  `plan_mode`, `goal_objective`, `goal_control` (`pause|resume|cancel`).
- `GET /api/v1/sessions` / `GET /api/v1/sessions/{id}` — list / inspect sessions.
- `POST /api/v1/sessions/{id}/prompts` — submit a prompt; `content` is an array of
  blocks (`text`, `image`, `file`, ...).
- `POST /api/v1/sessions/{id}/prompts:steer` — steer a running turn (parity with
  Codex `turn/steer`).
- `GET /api/v1/sessions/{id}/messages`, `GET .../transcript`, `GET .../transcript/ops`
  — history replay after restart.
- `GET /api/v1/sessions/{id}/status`, `GET .../snapshot` — run state / usage.
- `GET /api/v1/sessions/{id}/approvals`, `POST .../approvals/{approval_id}` — tool
  approval decisions (`approved|rejected|cancelled`).
- `GET /api/v1/sessions/{id}/questions`, `POST .../questions/{tail}` — agent question
  elicitation answers.
- `GET /api/v1/sessions/{id}/goal` — goal state.
- `POST /api/v1/shutdown` — stop the server.
- WebSocket channel `kimiCodeWebSocket`: client sends `client_hello` / `subscribe`
  (with `session_ids` array) / `abort`; server pushes `session_event` messages with
  monotonically increasing `seq` per session (resync supported).

`session_event` payload types observed in the AsyncAPI document (superset of what the
Codex pipeline emits):

- Answer streaming: `assistant.delta`, `thinking.delta`
- Turn lifecycle: `turn.started`, `turn.ended`, `turn.step.started`,
  `turn.step.completed`, `turn.step.retrying`, `turn.step.interrupted`
- Tool execution (execution-process list): `tool.call.started`, `tool.call.delta`,
  `tool.progress`, `tool.result`, `shell.started`, `shell.output`, `shell.completed`
  with tool kinds `command | file_io | diff | search | url_fetch | agent_call |
  skill_call | todo_list | ...`
- Sub-agents: `subagent.spawned | started | suspended | completed | failed`
- Prompt lifecycle: `prompt.submitted | completed | aborted | steered`
- Compaction: `compaction.started | blocked | cancelled | completed`
- Misc: `goal.updated`, `session.meta.updated`, `agent.status.updated`,
  `error`, `warning`

Why the server mode instead of ACP (`kimi acp`) or print mode (`kimi -p`):

- No worker-process layer needed: the server itself is the isolated process and the
  protocol endpoint. The UI process talks HTTP+WS directly, guarded by the same
  background-batch mechanisms the Codex path uses.
- Steer is supported (`prompts:steer`); ACP has no steer equivalent.
- Sessions are persisted server-side under `~/.kimi-code`, so app restart recovery is
  just "keep the session_id and prompt again"; even a server restart keeps sessions.
- One WebSocket subscription covers all session ids, so multi-chat is cheaper than the
  Codex per-chat process pool.

## Architecture

```
ChatFrame (main.py, wx main thread)
   │  in-process Python calls (background threads for blocking I/O)
   ▼
KimiServerClient (kimi_server_client.py)            NEW
   │  REST via requests; WebSocket via websocket-client (both already dependencies)
   ▼
kimi web --no-open --host 127.0.0.1 --port <picked>  (spawned child process)
   │
   ▼
Kimi Code sessions persisted under ~/.kimi-code
```

Compared with the Codex stack, `kimi_server_client.py` replaces
`codex_client.py` + `codex_worker_process.py` + `codex_worker_protocol.py` +
`codex_worker_client.py`. The UI-side integration pattern in `main.py` stays the same.

### KimiServerClient responsibilities

- **Lifecycle**: `start()` spawns `kimi web` (binary resolution: `KIMI_BIN` env var,
  then `kimi` on PATH, then `%USERPROFILE%\.kimi-code\bin\kimi.exe`), picks a free
  port, waits for `GET /api/v1/healthz`, obtains the bearer token (parse the startup
  banner from stdout; fall back to `~/.kimi-code/server.token`), opens the WebSocket
  and sends `client_hello`. `close()` unsubscribes, closes the socket, then
  `POST /api/v1/shutdown` with terminate/kill escalation as fallback.
- **REST wrappers**: `create_session(cwd, model, permission_mode="auto", goal=None)`,
  `submit_prompt(session_id, content_blocks)`, `steer_prompts(session_id, prompt_ids)`,
  `list_messages(session_id)`, `get_transcript_ops(session_id)`,
  `get_status(session_id)`, `answer_approval(session_id, approval_id, decision, ...)`,
  `answer_question(session_id, ...)`, `goal_control(session_id, action)`.
- **WebSocket read thread**: parses `session_event` messages, maps them to
  `KimiEvent` dataclasses (same field shape as `CodexEvent`: `type, thread_id,
  turn_id, item_id, text, raw_text, title, command, exit_code, subtype, display_kind,
  phase, status, flags, request_id, method, params, data, usage`; `thread_id` carries
  the kimi `session_id`). `abort` is sent over the socket for interrupt.
- **Delta coalescing and batched delivery**: identical accessibility rule as the Codex
  path — consecutive `assistant.delta` / `thinking.delta` events for the same
  `(session_id, turn_id, item_id)` are merged in the background thread; the UI thread
  is only handed batches via a `messages_pending` notification + `drain_pending()`
  call, never one callback per delta.
- **Reconnect**: on socket drop, reconnect with exponential backoff and re-`subscribe`
  with the last seen `seq` cursors; if the server answers `resync_required`, fall back
  to `GET .../transcript/ops` to rebuild state. (Phase 2 hardening; phase 1 surfaces a
  `transport_error` event like the Codex path does.)

### Event mapping (kimi -> KimiEvent)

| server `session_event` | KimiEvent.type | notes |
| --- | --- | --- |
| `assistant.delta` | `agent_message_delta` | merged per (session, turn, item) |
| `thinking.delta` | `agent_message_delta` | `display_kind="thinking"` |
| `turn.started` | `turn_started` | carries `turn_id` |
| `turn.ended` | `turn_completed` | final answer committed from accumulated delta |
| `turn.step.started/completed/retrying/interrupted` | `item_started` / `item_completed` | execution-process list entries |
| `tool.call.started` | `item_started` | `display_kind` from tool kind (`command`, `file_io`, `diff`, `search`, `url_fetch`, ...) |
| `tool.call.delta` / `tool.progress` / `shell.output` | `agent_message_delta` | `display_kind="commentary"`, merged |
| `tool.result` / `shell.completed` | `item_completed` | includes `exit_code` when present |
| `subagent.*` | `subagent_result` / `item_*` | title from subagent name |
| `prompt.completed` | `turn_completed` (fallback) | only if no `turn.ended` seen |
| `prompt.aborted` / `turn.step.interrupted` | `turn_completed` with `status="interrupted"` | |
| `compaction.*` | `notification` | status text |
| `goal.updated` | `notification` | `display_kind="goal"` |
| `error` / `warning` | `error` / `notification` | |
| approvals pending (via `GET .../approvals` after `agent.status.updated: awaiting_approval`) | `server_request` | drives the existing user-input dialog |
| questions pending (via `GET .../questions`) | `server_request` | same dialog path |

### main.py integration (mirrors the Codex branch)

- `kimi/main` (plus optional extra aliases later) added to `MODEL_IDS`;
  `is_kimi_model()` gates a branch in `_submit_question` next to the codex/claudecode
  branches.
- Chat dict fields: `kimi_session_id`, `kimi_turn_id`, `kimi_turn_active`,
  `kimi_pending_prompt`, `kimi_request_queue`, plus per-turn `kimi_session_id` /
  `kimi_turn_id` / `request_resume_token`. Persisted in `_save_state` / `_load_state`
  and in archived chat snapshots exactly like the `codex_*` fields.
- Functions cloned from the codex equivalents (same names with `codex`->`kimi`):
  `_start_kimi_turn_worker`, `_run_kimi_turn_worker`, `_on_kimi_client_message`,
  `_dispatch_kimi_event_to_ui` + `_drain_kimi_ui_events` (with the navigation quiet
  window batch-size reduction), `_on_kimi_event_for_chat`,
  `_apply_kimi_thread_state`, `_apply_kimi_error`, `_on_kimi_client_exit`,
  `_ensure_kimi_client`. One shared `KimiServerClient` for the whole frame (server is
  multi-session); per-chat isolation comes from `session_id`.
- Slash commands (kimi scope): `/stop` (WS `abort`), `/new` and `/clear` (drop local
  `kimi_session_id`; next prompt creates a fresh session), `/status` (local summary +
  `GET .../status`). `/compact` is gated behind a live probe of the server API during
  implementation; if no supported endpoint exists it is excluded from the kimi command
  help (documented limitation).
- Approvals/questions reuse `CodexUserInputDialog` (renamed usage only, no new dialog
  in phase 1). Sessions are created with `permission_mode="auto"` so approvals are
  rare; when one arrives the dialog path handles it.
- Interrupt-then-resend: if a prompt is submitted while a turn is active and
  `prompts:steer` rejects it, queue locally (`kimi_request_queue`) and resubmit when
  `turn_completed` arrives — same fallback semantics as the codex worker.
- `ChatFrame._on_close` shuts the shared client down (which stops the server process).

### Resume semantics

- Same-app-session resume: chat dict keeps `kimi_session_id`; prompts go to that
  session; the server holds the context.
- App restart: `_load_state` restores `kimi_session_id`; history lists are rebuilt
  from the archived chat snapshot (app-side, like codex); the next prompt continues
  the server-side session. If the server reports the session unknown
  (`session.not_found`), the client creates a fresh session and primes it with a
  rebuilt-history prompt (same recovery pattern as `_build_rollout_recovery_prompt`).
- Server restart: sessions live under `~/.kimi-code`, so a respawned server still
  knows the session; the client re-`subscribe`s and continues.

### Goal mode

Phase 2 (documented, not in the first cut): `/goal <objective>` creates/updates via
`goal_objective`; `/goal pause|resume|cancel` maps to `goal_control`; `goal.updated`
events render as execution entries. The design above already carries the hooks
(`goal.updated` mapping, client methods).

## Accessibility constraints (from AGENTS.md, non-negotiable)

- The UI thread never blocks on REST/WS I/O: all blocking calls run on background
  threads; results reach wx via the existing batched drain pattern.
- No list repaint, selection change, focus change, or state write when there is no
  visible state change; delta batches are suppressed while the user is navigating
  (reuse `_kimi_ui_event_batch_size` logic mirroring `_codex_ui_event_batch_size`).
- Execution-process entries append at the tail immediately outside the reader quiet
  window; the 3-second quiet-window rule for Tab/arrows/Enter/Space/Alt+* is
  preserved.
- Sounds (`_play_finish_sound`) and status-bar texts mirror the codex behavior.

## Non-Goals (phase 1)

- No goal-mode UI beyond the event mapping (phase 2).
- No MCP/config management through the server API.
- No reuse of an externally started `kimi web` instance (we always spawn our own on a
  private port and shut it down with the app).
- No `--dangerous-bypass-auth`: bearer-token auth is always used.
- No remote (non-loopback) binding.

## Testing summary

Full test plan: `docs/superpowers/plans/2026-08-10-kimicode-server-chat-test-plan.md`.
Highlights: unit tests for the client (mocked HTTP/WS), event-mapping tests covering
every `session_event` type we consume, ChatFrame integration tests mirroring
`test_codex_integration.py`, UI-responsiveness automation mirroring
`test_codex_ui_responsiveness_automation.py`, packaging-spec test update, and a
live-server smoke test gated behind `KIMI_LIVE_TEST=1`.

## Step-0 probe verdicts (kimi 0.33.0, captured 2026-08-10)

Fixtures: `tests/fixtures/kimi_server_events.jsonl`,
`tests/fixtures/kimi_server_probe_notes.json`. Probe script:
`scripts/kimi_server_probe.py`.

- Auth: REST without a token returns 401. Token comes from the startup banner
  line `Token: <token>` (or `#token=` in the Local URL) and from
  `~/.kimi-code/server.token`. WebSocket auth uses the
  `Authorization: Bearer <token>` handshake header (query-param token is
  rejected with 401).
- WebSocket endpoint: `ws://127.0.0.1:<port>/api/v1/ws`. Handshake:
  server pushes `server_hello`, client sends `client_hello`, then `subscribe`
  with `session_ids`; acks echo the request id. Event messages are flat:
  `{"type": <event-type>, "seq": n, "session_id": ..., "epoch": ...,
  "payload": {"type": <event-type>, ...camelCase fields...}}` — the outer
  `type` equals `payload.type`.
- `POST /api/v1/sessions`: response `data.id` is `session_<uuid>`. The
  create-time `agent_config` is NOT applied (response keeps
  `agent_config.model == ""`); `POST /api/v1/sessions/{id}/profile` with the
  same `agent_config` DOES apply it (verified: `/status` then shows the model,
  `permission: "auto"`, correct `max_context_tokens`). The client therefore
  always pushes agent_config through the profile endpoint after create.
- Model must be set explicitly — a session without a model fails every turn
  with `model.not_configured`. Aliases live under
  `GET /api/v1/models` / `kimi provider list --json`
  (e.g. `kimi-code/kimi-for-coding`).
- `POST .../prompts`: response `data.prompt_id` (== `user_message_id`) and a
  `status` (`"running"` when the turn starts immediately; prompts submitted
  during an active turn become pending).
- Steer: `POST .../prompts:steer` accepts only PENDING prompt ids
  (`40402 "one or more prompts are not pending"` for the running prompt).
  App flow: submit prompt; if the response status is pending, call
  `prompts:steer` to merge it into the active turn; on failure leave it queued
  (the server runs queued prompts after the active turn).
- Abort: WS `{"type": "abort", "payload": {"session_id", "prompt_id"}}`.
- Successful turn event order (captured): `session.meta.updated`,
  `turn.started`, `agent.status.updated(running)`, `event.session.work_changed`,
  `context.spliced`, `turn.step.started`, `thinking.delta`*,
  `assistant.delta`*, `turn.step.completed` (with `usage`), `turn.ended`
  (`reason: "completed"`), `event.session.work_changed(busy=false)`,
  `prompt.completed`.
- Tool turn additionally: `tool.call.started` (`name`, `args`, `description`,
  `display.kind`), `tool.call.delta` (raw `argumentsPart` JSON fragments —
  intentionally dropped in mapping), `tool.result` (`output`).
- Delta payloads: `assistant.delta`/`thinking.delta` carry `turnId` + `delta`
  (text fragment).
- Context usage: `agent.status.updated` carries `contextTokens` /
  `maxContextTokens`; `GET .../status` returns `context_tokens`,
  `max_context_tokens`, `context_usage` (fraction).
- Compact: no supported REST trigger (`POST .../compact` etc. return
  `40001 unsupported action`); sending the literal text `/compact` is treated
  as a normal user prompt. Verdict: `/compact` is EXCLUDED from the kimi slash
  commands (documented limitation).
- Restart: sessions survive a server restart (`GET /sessions/{id}` 200,
  further prompts accepted) — resume works as designed.
- `GET .../transcript/ops` requires `agent_id` + `since_seq` query params.
