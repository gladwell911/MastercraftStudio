# Kimi Code Server Chat — Automated Test Plan

Design: `docs/superpowers/specs/2026-08-10-kimicode-server-chat-design.md`
Dev plan: `docs/superpowers/plans/2026-08-10-kimicode-server-chat-plan.md`

Goal: every new behavior is covered by an automated test that fails without the
feature and passes with it. Layers A–D run in CI without a real kimi binary;
layer E is opt-in live testing.

## A. `tests/test_kimi_server_client_unit.py` — client unit tests

Technique: monkeypatch `requests.Session` and `websocket.WebSocketApp` (or the
client's thin wrappers) with fakes; monkeypatch `subprocess.Popen` for spawn. No real
process, no real socket.

Lifecycle:
1. `test_resolve_launch_command_env_override` — `KIMI_BIN` wins over PATH.
2. `test_resolve_launch_command_path_fallback` — PATH lookup, then user-bin fallback,
   then `KimiServerError` when nothing exists.
3. `test_pick_free_port_returns_bindable_port`.
4. `test_start_spawns_server_with_expected_args` — command line contains `web`,
   `--no-open`, chosen `--port`; loopback host only.
5. `test_start_waits_for_healthz` — health polling retried until timeout; timeout
   raises `KimiServerError` and kills the spawned process.
6. `test_token_from_startup_banner` — token parsed from fake stdout banner.
7. `test_token_fallback_server_token_file` — banner absent -> reads
   `~/.kimi-code/server.token` (monkeypatched home).
8. `test_rest_calls_send_bearer_header` — every REST call carries
   `Authorization: Bearer <token>`.
9. `test_close_sends_shutdown_then_escalates` — shutdown POST; on failure,
   terminate -> kill escalation order.
10. `test_close_idempotent`.

REST wrappers:
11. `test_create_session_body` — `metadata.cwd`, `agent_config.model`,
    `permission_mode="auto"` serialized; returns session id.
12. `test_submit_prompt_content_blocks` — text block shape; image attachment becomes
    base64 image block.
13. `test_steer_prompts_body` — `prompt_ids` array.
14. `test_rest_error_raises_with_context` — non-2xx -> `KimiServerError` containing
    status + body.
15. `test_answer_approval_and_question_bodies`.

WebSocket / event intake:
16. `test_ws_hello_and_subscribe_sent` — `client_hello` then `subscribe` with session
    ids after `create_session`.
17. `test_session_event_dispatched_as_kimi_event` — fake inbound `session_event`
    message -> mapped `KimiEvent` queued.
18. `test_delta_coalescing_merges_consecutive_deltas` — N deltas same
    (session, turn, item) -> 1 merged event, text concatenated in order.
19. `test_delta_not_merged_across_items_or_sessions`.
20. `test_messages_pending_notification_coalesced` — many queued events -> single
    `messages_pending` until `drain_pending_messages()` called.
21. `test_queue_cap_drops_oldest_with_warning` — 2000+ pending -> warning event
    injected, oldest dropped.
22. `test_abort_sends_ws_message`.
23. `test_transport_error_surfaces_event` — WS drop -> `transport_error` control
    message to UI callback.
24. `test_server_process_exit_surfaces_exit_message`.
25. `test_public_methods_thread_safe` — hammer subscribe/abort/drain from multiple
    threads; no exception, no deadlock (timeout guarded).
26. `test_module_does_not_import_wx` — source scan, mirroring the codex worker check.

## B. `tests/test_kimi_event_mapping_unit.py` — mapping table

Table-driven over `tests/fixtures/kimi_server_events.jsonl` plus synthetic events.
One parametrized case per mapped type:

1. `assistant.delta` -> `agent_message_delta` (text, ids).
2. `thinking.delta` -> `agent_message_delta` with `display_kind="thinking"`.
3. `turn.started` -> `turn_started` (turn_id present).
4. `turn.ended` -> `turn_completed`.
5. `turn.step.*` -> `item_started` / `item_completed` with execution-entry fields.
6. `tool.call.started` -> `item_started`, `display_kind` per tool kind
   (`command|file_io|diff|search|url_fetch|agent_call|skill_call`).
7. `tool.call.delta`/`tool.progress`/`shell.output` -> commentary delta.
8. `tool.result`/`shell.completed` -> `item_completed`, `exit_code` preserved.
9. `subagent.*` -> subagent events with title.
10. `prompt.aborted`/`turn.step.interrupted` -> `turn_completed status="interrupted"`.
11. `compaction.*` -> `notification`.
12. `goal.updated` -> `notification display_kind="goal"`.
13. `error`/`warning` -> `error`/`notification`.
14. `agent.status.updated: awaiting_approval` -> `server_request` (method/params
    populated for the dialog).
15. Unknown type -> `notification` with `data["unmapped"]` — nothing dropped silently.
16. `event_to_payload`/`event_from_payload` roundtrip for all cases.

## C. `tests/test_kimi_integration.py` — ChatFrame integration

Technique mirrors `test_codex_integration.py`: real `ChatFrame` via the `frame`
fixture, `FakeKimiServerClient` injected (monkeypatched client factory), events pushed
through the same drain path the real client uses.

1. `test_model_combo_contains_kimi` — `kimi/main` present, display label correct.
2. `test_submit_routes_kimi_model_to_kimi_path` — codex/claudecode/api paths not
   touched (assert fakes).
3. `test_start_turn_creates_session_once_per_chat` — second prompt reuses
   `kimi_session_id`; another chat gets its own session.
4. `test_turn_events_render_execution_list` — step/tool events append execution
   entries with expected titles/kinds.
5. `test_delta_then_final_answer_updates_answer_list` — merged delta rendering +
   final answer row, `request_status="done"`.
6. `test_turn_completed_reenables_new_chat_and_plays_sound` (sound monkeypatched).
7. `test_interrupt_via_stop_command` — `/stop` sends WS abort; interrupted turn shows
   `status="interrupted"`.
8. `test_new_and_clear_commands_drop_session` — next prompt creates a fresh session.
9. `test_status_command` — `/status` produces a local status entry without server
   round-trip surprises.
10. `test_prompt_during_active_turn_steers_or_queues` — steer accepted path; steer
    rejected -> queued and flushed after `turn_completed`.
11. `test_approval_request_opens_dialog_and_replies` — dialog monkeypatched;
    decision posted back.
12. `test_state_persists_kimi_fields` — `_save_state`/`_load_state` roundtrip of
    `kimi_session_id` etc.; archived chat snapshot keeps them.
13. `test_session_not_found_recovery` — server 404 on prompt -> new session created
    with rebuilt-history priming prompt.
14. `test_error_event_marks_turn_failed` — error event -> turn error state, UI
    not stuck running.
15. `test_client_close_on_frame_close` — `_on_close` calls client close once.
16. `test_no_ui_mutation_without_visible_change` — events for a non-visible chat do
    not repaint lists or move focus (assert via spies, mirroring codex tests).
17. `test_compact_command_included_or_excluded_per_probe` — matches the Step-0
    verdict recorded in the design doc.

## D. `tests/test_kimi_ui_responsiveness_automation.py`

Mirror `test_codex_ui_responsiveness_automation.py` with the fake client:

1. `test_event_storm_during_navigation_keeps_focus` — 500 deltas while the user tabs
   through controls: focus never stolen, selection unchanged.
2. `test_quiet_window_batches_events` — inside the 3s reader quiet window, drains are
   small/batched; afterwards a single larger flush.
3. `test_background_events_do_not_repaint_lists` — no `Refresh`/`SetSelection` calls
   from the drain path when nothing visible changed (spy on wx methods).
4. `test_execution_entries_append_at_tail_outside_quiet_window`.
5. `test_frame_close_with_active_kimi_turn_does_not_hang` — close during a running
   fake turn completes within timeout.

## E. `tests/test_kimi_live_smoke.py` — opt-in live test

Skipped unless `KIMI_LIVE_TEST=1` and `kimi` binary resolves. Real end-to-end:
spawn server, create session, submit "reply with exactly: pong", assert streamed
deltas + final answer contains "pong", abort path on a second long prompt, clean
shutdown. Marked `pytest.mark.live` so `-m "not live"` excludes it.

## F. Packaging / repo hygiene

1. `tests/test_packaging_specs.py` — extend if spec files list modules explicitly;
   assert `kimi_server_client` importable from the bundled layout.
2. AGENTS.md/README.txt updated (feature inventory, `KIMI_BIN`, live-test flag).

## Coverage checklist (traceability)

| Feature | Tests |
| --- | --- |
| spawn/lifecycle/token | A3–A10, E |
| REST wrappers | A11–A15, C3, C13 |
| WS subscribe/abort | A16, A22, C7 |
| event mapping | B1–B16 |
| delta coalescing/batching | A18–A21, D1–D2 |
| submit routing | C2 |
| execution-process list | C4, D4 |
| answer list + streaming | C5 |
| interrupt | C7, E |
| steer/queue | C10 |
| multi-chat | C3 |
| resume/restart recovery | C12, C13, E |
| approvals/questions dialog | C11 |
| slash commands | C7, C8, C9, C17 |
| persistence | C12 |
| accessibility guarantees | C16, D1–D5 |
| packaging | F1 |
| live server | E |

## Execution gates

- After dev-plan step 2: `pytest tests/test_kimi_server_client_unit.py tests/test_kimi_event_mapping_unit.py`
- After step 3: + `tests/test_kimi_integration.py`
- After step 4: + `tests/test_kimi_ui_responsiveness_automation.py`
- Final: full `pytest` (excluding `live`), plus `KIMI_LIVE_TEST=1 pytest tests/test_kimi_live_smoke.py` run once manually on a machine with the kimi CLI.
