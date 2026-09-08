# Project Instructions

- This is a wxPython desktop app used with screen readers. Any UI-facing change must preserve keyboard focus stability and avoid unnecessary foreground refreshes while background work is running.
- For UI changes, run targeted accessibility/performance regression tests before completion. At minimum, include the relevant `tests/test_*ui_automation.py` test and any model-specific workflow tests touched by the change.
- Do not schedule UI-thread work, repaint list controls, change list selection/focus, or write app state from background polling when there is no visible state change. This is required to keep Tab and arrow-key navigation responsive with screen readers.
- Chat history and general app state still resolve under the app data/history directory, but notes storage is intentionally separate: `resolve_notes_data_dir()` returns `D:\code\note`, and `ChatFrame` uses `D:\code\note\notes.db`. Tests should monkeypatch `resolve_notes_data_dir()` instead of writing to the real notes directory.
- The `kimi/` model family chats through a spawned local `kimi web` server; `kimi_server_client.py` owns the process, REST calls, and the WebSocket event stream. Like the codex path, inbound events must be coalesced in the background and handed to the UI in batches (`drain_pending_messages`), never one callback per delta.
- Provider dispatch must follow the normalized model id, not the transport source: remote `kimi/*`, `codex/*`, and `claudecode/*` messages use their dedicated workers and must not fall through to OpenRouter.
- Any interactive CLI client reference that can consume later user input must carry its owning `chat_id`. Only matching-chat input may be forwarded to that client; add a cross-chat regression whenever this routing changes.
- Execution-page changes must preserve the applied chat/turn owner across labels, metadata, selection and detail actions, including pending/error states. Keep bounded foreground reads, generation-checked background results, and automatic retry without interrupting provider drains. See the current execution specification linked from `docs/README.md`.
- Run wx GUI suites serially. Tests enabling real timers must own cleanup from frame construction through teardown, stopping their timers/scans before destroying the frame; do not disable production drains or relax navigation thresholds to hide cross-test contamination.

## Durable project context

Start with `README.txt` and `docs/README.md`; `docs/handoff.md` is the current snapshot. Dated plans and frozen regression baselines are historical evidence, not proof of current failures or full-suite success. Keep project facts here and in project docs, not in global agent configuration.
