# Codex Thread Isolation And Image Generation Guard Design

**Goal**

Eliminate the false `gpt-5.4`/`gpt-5.3` crossover during new Codex chats and ensure `codex/gpt-5.3-codex-spark-high` never sends the runtime `image_generation` tool.

**Problem**

Two failures are interacting:

1. New-chat Codex state is not always reset. When `_archive_active_session()` returns early because the current chat has no turns, the active Codex thread state can survive into the next chat.
2. The Codex runtime still emits `image_generation` for a `gpt-5.3-codex-spark` session in at least one observed path, which causes `400 invalid_request_error` with `param="tools"`.

This creates a confusing user-visible failure mode: the UI shows a new `gpt-5.4` chat, but the first request can resume an older `gpt-5.3-codex-spark` thread and fail with the same tool error as the earlier `5.3` chat.

**Chosen Approach**

Apply a narrow two-layer fix:

1. Reset active Codex thread state explicitly whenever a new chat is created, instead of relying on archive side effects.
2. Keep model-specific tool capability gating at Codex app-server launch time, with a single source of truth for disabled runtime features per model.

This keeps chat isolation concerns in `main.py` and model capability concerns in `codex_client.py`. It avoids scattering model-specific tool filtering into send paths or turn payload construction.

**Behavior Changes**

1. Starting a new chat always clears the active Codex thread, turn, pending request, and related active-session Codex metadata, even if the previous chat had zero turns.
2. A new Codex chat must not inherit `active_codex_thread_id` or `active_codex_turn_id` from the previous active chat.
3. When the first turn of a new Codex chat starts, worker payload construction must use the new chat's own thread state only; it must not fall back to stale active-session thread state left over from another chat.
4. `codex/gpt-5.3-codex-spark-high` must launch Codex app-server with `--disable image_generation`.
5. `codex/gpt-5.4-medium` and newer supported Codex models keep existing runtime tool behavior unchanged.

**Architecture**

**1. Chat-State Isolation**

Add a small helper in `main.py` that resets only the active in-memory Codex session state:

- `active_codex_thread_id`
- `active_codex_turn_id`
- `active_codex_turn_active`
- `active_codex_pending_prompt`
- `active_codex_pending_request`
- `active_codex_request_queue`
- `active_codex_thread_flags`
- `active_codex_latest_assistant_text`
- `active_codex_latest_assistant_phase`

Use this helper from:

- `_on_new_chat_clicked()`
- `_start_remote_new_chat()`
- any existing active-chat reset path that intends to start a fresh current chat

Do not clear archived chat metadata. Historical chats should retain their stored `codex_thread_id` and related fields so existing history and recovery semantics remain intact.

**2. Worker Turn Start Rules**

Keep the current worker flow centered in `_run_codex_turn_worker()`, but preserve this invariant:

- for the current active chat, a thread id may only come from that chat's current state after the new-chat reset has run
- for archived/history recovery, the archived chat's own stored thread id may still be used

This fix should not redesign worker lifecycle, chat switching, or archived-chat recovery. It only removes accidental cross-chat thread reuse.

**3. Runtime Feature Gating**

Retain the launch-time gating model in `codex_client.py`:

- define disabled runtime features from the selected Codex model
- append `--disable <feature>` when building the app-server command

For this change, only one disabled runtime feature is in scope:

- `image_generation` for `codex/gpt-5.3-codex-spark-high`

Do not add per-turn `tools` rewriting in `main.py`, `CodexWorkerRuntime`, or `turn/start` request construction. The runtime capability decision should stay at process launch time.

**4. Observability**

Keep verification log-based and minimal. The goal is to make future diagnosis cheap without adding product behavior:

- confirm the session init log model for each spawned Codex session
- confirm the websocket request tool list for `gpt-5.3-codex-spark`
- if a small internal debug log is added for launch configuration, it must be passive and must not affect UI state

No new user-facing logging UI is part of this design.

**Scope**

Modify only the code needed for:

- active new-chat Codex state reset
- active new-chat worker isolation from stale thread ids
- launch-time `image_generation` disablement for `5.3 spark`
- targeted regression tests around the two behaviors above

**Out Of Scope**

Do not include any of the following in this change:

- model chooser redesign
- attachment or `localImage` behavior changes
- generalized tool filtering across all models
- archived chat schema redesign
- Codex worker architecture rewrite
- UI copy changes for model capability descriptions

**Testing**

Add or update tests to cover:

1. New chat resets active Codex thread state even when the previous active chat has no turns.
2. New chat creation does not preserve stale `active_codex_thread_id` into the next Codex request path.
3. `build_codex_app_server_command()` adds `--disable image_generation` for `codex/gpt-5.3-codex-spark-high`.
4. `build_codex_app_server_command()` does not add that disablement for `codex/gpt-5.4-medium` or `codex/main`.
5. Existing Codex request-shape tests still pass, proving the fix does not mutate `thread/start` or `turn/start` payload structure.

Runtime acceptance should also confirm:

- a fresh `5.3 spark` plain-text chat no longer returns the `param="tools"` / `image_generation` 400
- a fresh `5.4` chat initializes a `gpt-5.4` session instead of resuming a stale `gpt-5.3-codex-spark` thread

**Risks**

The main risk is over-correcting and breaking intended same-chat resume behavior. The design avoids that by resetting only when a brand-new chat is created and by preserving archived chat metadata unchanged.

The secondary risk is adding model-specific logic in more than one place. This design avoids that by keeping runtime feature gating centralized in `codex_client.py`.
