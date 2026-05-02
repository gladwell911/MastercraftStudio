# OpenClaw Multi-Chat Design

## Goal

OpenClaw should match Codex and ClaudeCode chat behavior: multiple app chats can exist independently, each chat keeps its own OpenClaw session, and app restart restores the last active OpenClaw chat so the user can continue without mixing records.

## Current State

The app already persists OpenClaw runtime fields on active and archived chats:

- `openclaw_session_id`
- `openclaw_session_file`
- `openclaw_sync_offset`
- `openclaw_last_event_id`
- `openclaw_last_synced_at`

The weak point is that OpenClaw sync still depends on the global session key `agent:main:main`. That key points to OpenClaw's current main session and can move between chats, so different app chats can accidentally follow the same OpenClaw session file.

## Architecture

Use one OpenClaw agent (`main`) and many app chats. Each app chat owns one OpenClaw `session_id`, generated from the app `chat_id` using the existing `zgwd-<chat_id>` convention. This mirrors Codex `thread_id` and ClaudeCode `session_id` rather than creating one OpenClaw agent per chat.

OpenClaw session file discovery is session-id based:

1. If the app chat already has `openclaw_session_file`, read that file from its stored offset.
2. Otherwise, scan OpenClaw `sessions.json` for an entry whose `sessionId` matches the app chat's `openclaw_session_id`.
3. Fall back to `agent:main:main` only for legacy chats that do not have a session id yet.

## New Chat Behavior

When OpenClaw is selected, pressing New Chat should behave like other models:

- archive the current chat if it has turns;
- create a new app chat id;
- clear OpenClaw runtime fields for the new app chat;
- do not send `/new` into the global OpenClaw main session.

The first OpenClaw message in the new app chat creates or resumes that chat's own `session_id`.

## Data Flow

Send:

1. Ensure active app `chat_id`.
2. Ensure active OpenClaw `session_id` from that `chat_id`.
3. Run `openclaw agent --session-id <session_id> --message <text> --json`.
4. The UI waits for session-file sync to fill the reply.

Sync:

1. Resolve the active chat's OpenClaw session file by stored file or matching `session_id`.
2. Read JSONL events from the stored offset.
3. Merge user and assistant events only into the active chat's turns.
4. Persist the updated session file, offset, last event id, and timestamp.

## Migration

Existing saved chats keep working:

- Chats with stored `openclaw_session_id` use that id.
- Chats with only OpenClaw turns but no id get a deterministic `zgwd-<chat_id>` id.
- Legacy sessions with only `agent:main:main` can still fall back to the default pointer.

## Testing

Add tests that prove:

- session pointers can be found by `sessionId`, not only by key;
- a new OpenClaw chat creates a new app chat instead of sending `/new`;
- two OpenClaw chats preserve different session ids/files/offsets when archived and switched;
- sync uses the current chat's stored session file and does not import events from the global main pointer.
