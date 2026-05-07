# Chat History SQLite Storage Design

## Problem

Packaged builds resolve app data beside the package parent directory. For example, `C:\code\cx\mc\mc.exe` uses `C:\code\cx\history`, while `C:\code\cv\mc\mc.exe` uses `C:\code\cv\history`. The same executable can therefore feel fast or slow depending on the size of that sibling history directory.

The current `app_state.json` mixes small preferences with large chat data:

- runtime settings such as selected model, remote control, voice settings, and notes UI state
- current active chat turns
- all archived chats
- full answers, attachments, context usage, and Codex execution steps

On `C:\code\cx`, `app_state.json` reached about 64 MB. That forces startup JSON parsing, full in-memory history loading, and repeated full-file writes for changes that only touch one chat or one execution step. With screen readers, these large state changes amplify list refresh and accessibility tree work, making Tab and arrow-key navigation feel stalled.

## Recommended Architecture

Use a hybrid store:

- Keep `app_state.json` as a small configuration and UI-state file.
- Move chat history, turns, and execution steps into `chat_history.db` using SQLite.
- Load history list rows from chat summaries only.
- Load turns and execution steps on demand when opening a chat or switching to execution view.

SQLite is a better fit than a single JSON file for this data because the app needs partial reads and incremental writes. The existing notes feature already ships SQLite successfully, so this does not add a new platform dependency.

## Storage Model

Create a focused `chat_store.py` module with a `ChatStore` class. It should be independent from wx and expose plain dict-based methods so `main.py` can migrate gradually.

Tables:

- `chats`: one row per chat summary.
  - `id TEXT PRIMARY KEY`
  - `title TEXT NOT NULL`
  - `model TEXT NOT NULL DEFAULT ''`
  - `created_at REAL NOT NULL DEFAULT 0`
  - `updated_at REAL NOT NULL DEFAULT 0`
  - `pinned INTEGER NOT NULL DEFAULT 0`
  - `title_manual INTEGER NOT NULL DEFAULT 0`
  - `title_source TEXT NOT NULL DEFAULT 'default'`
  - `title_updated_at REAL NOT NULL DEFAULT 0`
  - `title_revision INTEGER NOT NULL DEFAULT 1`
  - `detail_panel_mode TEXT NOT NULL DEFAULT 'answers'`
  - CLI/session metadata needed to resume Codex, Claude Code, and OpenClaw.
- `turns`: one row per chat turn.
  - `chat_id TEXT NOT NULL`
  - `turn_index INTEGER NOT NULL`
  - `payload_json TEXT NOT NULL`
  - primary key `(chat_id, turn_index)`
- `execution_steps`: one row per execution step.
  - `chat_id TEXT NOT NULL`
  - `step_index INTEGER NOT NULL`
  - `turn_idx INTEGER`
  - `display_kind TEXT NOT NULL DEFAULT ''`
  - `event_type TEXT NOT NULL DEFAULT ''`
  - `list_text TEXT NOT NULL DEFAULT ''`
  - `detail_text TEXT NOT NULL DEFAULT ''`
  - `payload_json TEXT NOT NULL`
  - primary key `(chat_id, step_index)`
- `meta`: schema and migration markers.

Indexes:

- `chats(pinned, updated_at DESC, created_at DESC)`
- `turns(chat_id, turn_index)`
- `execution_steps(chat_id, turn_idx, step_index)`

`payload_json` keeps compatibility with existing dict-shaped turn and execution fields without designing a large relational schema up front. Summary columns exist only for query speed and list rendering.

## App Behavior

Startup:

- Initialize `chat_history.db`.
- Read only small config from `app_state.json`.
- Load chat summaries from `ChatStore.list_chat_summaries()`.
- Load the active chat turns by `active_chat_id` only.
- Do not load every archived chat's full turns or execution steps.

Saving:

- `_save_state()` writes only small preferences and active identifiers to `app_state.json`.
- Chat edits call targeted store methods such as `upsert_chat()`, `replace_turns()`, `append_execution_step()`, and `update_chat_summary()`.
- Background Codex progress must never trigger a full config rewrite.

History list:

- `_refresh_history()` renders from lightweight summaries.
- `history_ids` remains the UI mapping.
- Opening a history chat loads that chat's turns from SQLite on demand.

Execution view:

- Current-turn execution rows load only for the active or viewed chat.
- Append new execution steps incrementally.
- Keep the existing dedupe behavior.
- Add a retention guard for visible execution steps, defaulting to the most recent 500 per turn. Older rows remain queryable only if explicitly preserved; v1 can prune older execution rows to prevent unbounded growth.

Remote API:

- History list responses continue to be summary-only.
- History read/state endpoints load full turns only for the requested chat.
- Paged reads should use SQLite `LIMIT` instead of slicing a full in-memory list.

## Migration

On first startup after the feature lands:

1. If `chat_history.db` is empty and legacy `app_state.json` contains `archived_chats`, `chats`, `active_chat`, or `active_session_turns`, import them into SQLite.
2. Back up the old file as `app_state.json.bak.<timestamp>`.
3. Write a slim `app_state.json` without `archived_chats`, `chats`, or full turn arrays.
4. Keep the backup if migration fails; do not delete user data.
5. Skip repeated migration once `meta.legacy_json_migration_complete = 1`.

Legacy behavior must remain readable during the migration window so users do not lose history.

## Guardrails

- Add tests proving `app_state.json` does not contain `archived_chats`, `chats`, `active_session_turns`, or full `active_chat.turns` after saving.
- Add a size guard test that a generated large history still writes a config file under 200 KB.
- Add performance/accessibility automation with many chats and long answers, covering history list, answer list, execution list, input box, and model combo.
- Add packaged-path tests for `resolve_app_data_dir()` plus `chat_history.db` location.
- Keep AGENTS.md requirements: no unnecessary UI-thread work, repaint, selection/focus changes, or state writes when there is no visible change.

## Rollout

Implement in stages:

1. Add `ChatStore` and tests.
2. Add legacy JSON migration and slim config save/load.
3. Wire active chat and history summaries to the store.
4. Wire on-demand history reads and execution steps.
5. Add retention and performance automation.
6. Repackage and run real packaged desktop automation.

