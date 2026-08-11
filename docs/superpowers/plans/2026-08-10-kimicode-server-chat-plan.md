# Kimi Code Server Chat — Development Plan

Design: `docs/superpowers/specs/2026-08-10-kimicode-server-chat-design.md`
Test plan: `docs/superpowers/plans/2026-08-10-kimicode-server-chat-test-plan.md`

Execution order is strict: each step lands with its tests green before the next
starts. Steps 1–2 touch only new files; steps 3+ touch `main.py`.

## Step 0 — API probes (no code changes)

Confirm the under-documented endpoints against a live `kimi web` instance
(spawn on a scratch port, `POST /api/v1/shutdown` afterwards):

1. Token acquisition: verify the startup-banner token format and the
   `~/.kimi-code/server.token` fallback path.
2. `POST /api/v1/sessions` minimal body: required fields, response shape
   (session id field name).
3. `POST .../prompts` + WS `subscribe`: capture a real `session_event` stream for a
   trivial prompt (`assistant.delta`, `turn.started/ended` field names) and save it
   as a test fixture under `tests/fixtures/`.
4. Interrupt: WS `abort` behavior and the resulting events.
5. Steer: `POST .../prompts:steer` accepted/rejected shapes.
6. Compact: probe for a compaction trigger endpoint (`POST /api/v1/sessions/{id}/{tail}`
   catch-all, e.g. `.../prompts` with a `/compact` text vs a dedicated route). Record
   the verdict in the design doc; if unsupported, `/compact` is excluded from kimi
   slash-command help.
7. Resume: create session, shut server down, restart server, prompt the same session
   id — confirm continuity.

Deliverable: `tests/fixtures/kimi_server_events.jsonl` (captured stream) + verdict
notes appended to the design doc.

## Step 1 — `kimi_server_client.py` (new module)

Pure-Python module, no wx imports (enforced by test, mirroring the codex worker
source check). Contents:

- Constants: `KIMI_MODEL_PREFIX = "kimi/"`, `DEFAULT_KIMI_MODEL = "kimi/main"`,
  `is_kimi_model()`, timeout constants (connect 10s, REST 60s, health-wait 45s).
- `resolve_kimi_launch_command()` — `KIMI_BIN` env -> `shutil.which("kimi")` ->
  `%USERPROFILE%\.kimi-code\bin\kimi.exe`.
- `pick_free_port()` helper.
- `@dataclass KimiEvent` — same fields as `CodexEvent` (see design); plus
  `event_to_payload()` / `event_from_payload()`.
- `map_session_event(session_id, raw: dict) -> KimiEvent | None` — the mapping table
  from the design doc. Unknown types return a `notification` event with
  `data["unmapped"]=True` so nothing is silently dropped.
- `class KimiServerClient`:
  - `start()` / `close()` lifecycle as designed (spawn, health wait, token, WS hello;
    shutdown with escalate-to-kill fallback).
  - REST methods listed in the design (thin wrappers around `requests`, raising
    `KimiServerError` with status/body context on failure).
  - WS read thread + `subscribe(session_ids)` / `abort(session_id)` send methods
    (send lock; recv loop dispatches).
  - Delta coalescing: consecutive `agent_message_delta` for the same
    `(session_id, turn_id, item_id)` merge; cap queue at 2000 messages (drop-oldest
    with a warning event), mirroring `CodexWorkerClient`.
  - Notification model identical to the codex client: `on_message(callback)` where
    callback receives either `{"type": "messages_pending"}` or control messages
    (`{"type": "transport_error", ...}`, `{"type": "exit", ...}`); UI calls
    `drain_pending_messages()` on the wx main thread.
  - Thread safety: public methods are callable from any thread; internal state guarded
    by a lock.

Tests: `tests/test_kimi_server_client_unit.py` (see test plan, section A).

## Step 2 — event mapping + fixtures

- Finalize `map_session_event` against the Step-0 fixture; table-driven tests for
  every mapped type.
- `tests/test_kimi_event_mapping_unit.py` (test plan section B).

Gate: steps 1–2 green (`pytest tests/test_kimi_server_client_unit.py
tests/test_kimi_event_mapping_unit.py`).

## Step 3 — `main.py` integration

All changes mirror the codex implementation points (reference lines from the codex
branch are listed in the exploration notes; use the current file, line numbers may
drift):

1. Imports + constants: `from kimi_server_client import ...`; add `"kimi/main"` to
   `MODEL_IDS` with display name "Kimi Code"; `is_kimi_model` import.
2. `ChatFrame.__init__`: `self._kimi_client: KimiServerClient | None = None`,
   `self._kimi_client_lock`, `self._kimi_active_turns`,
   `self._pending_kimi_ui_events` queue, and `active_kimi_*` state fields — cloned
   from the codex equivalents.
3. `_load_state` / `_save_state` / archived-chat snapshot / new-chat cleanup /
   `_load_chat_as_current`: handle `kimi_*` chat fields exactly where `codex_*`
   fields are handled.
4. `_submit_question`: add the `is_kimi_model(resolved_model) and source == "local"`
   branch -> `_start_kimi_turn_for_chat(chat_id, turn_idx, q, resolved_model)`;
   kimi slash-command parsing (`/stop /new /clear /status /help`) cloned from
   `_parse_codex_local_command` (exclude `/speed`, exclude `/model`; include
   `/compact` only per Step-0 verdict).
5. Background turn runner `_run_kimi_turn_worker`: ensure client, ensure session
   (create with `permission_mode="auto"`, `cwd` = app dir; resume path on
   `session.not_found` with rebuilt-history priming), build content blocks (text +
   image attachments as base64 image blocks), submit or steer, handle queue fallback.
6. Event intake: `_on_kimi_client_message` ->
   `_dispatch_kimi_event_to_ui` -> `_drain_kimi_ui_events` (batch size reduction
   while navigating) -> `_on_kimi_event_for_chat`:
   - `agent_message_delta` -> `_buffer_execution_delta` / `_flush_execution_delta`
     (reuse as-is).
   - execution entries via `_build_execution_entry` (reuse; kimi `display_kind`
     values chosen to fit its existing branches).
   - final answer -> `_apply_kimi_final_answer_to_turn` + `_update_active_answer_row`.
   - `turn_completed` -> request status, running flags, `new_chat_button.Enable()`,
     `_play_finish_sound`, queued-prompt flush.
   - `server_request` -> existing user-input dialog -> `answer_approval` /
     `answer_question`.
   - `token/usage` fields -> context-usage update if the server exposes usage data;
     otherwise skip silently.
7. `_on_close`: `self._kimi_client.close()` (idempotent, guarded).
8. Status-bar texts parallel to codex ("已发送" etc., with "Kimi" wording where the
   codex text mentions Codex).

Tests: `tests/test_kimi_integration.py` (test plan section C) written together with
the implementation, using a `FakeKimiServerClient` (same technique as the codex
integration tests' fake worker client).

Gate: section A+B+C green.

## Step 4 — UI responsiveness automation

`tests/test_kimi_ui_responsiveness_automation.py` mirroring
`test_codex_ui_responsiveness_automation.py`: event storm during navigation, focus
stability, quiet-window batching, no repaint without visible change.

Gate: sections A–D green.

## Step 5 — live smoke test + packaging + docs

- `tests/test_kimi_live_smoke.py`: gated on `KIMI_LIVE_TEST=1`; real spawn, create
  session, one prompt, assert deltas + final answer + clean shutdown.
- `ZhugeQA_A11y.spec` / `zgwd.spec`: ensure `kimi_server_client` is bundled (pure
  python, likely automatic; the packaging test decides).
- `tests/test_packaging_specs.py`: extend expectations if the spec changed.
- Update `AGENTS.md` (feature inventory) and `README.txt` (model list / KIMI_BIN env)
  per project convention.
- Design doc: record Step-0 verdicts (compact endpoint, exact response field names).

Gate: full suite (`pytest`) green.

## Rollback

Each step is independently revertable: steps 1–2 are additive files; step 3 is a
single feature branch in `main.py` gated by `is_kimi_model`; removing the model id
from `MODEL_IDS` disables the feature without touching anything else.
