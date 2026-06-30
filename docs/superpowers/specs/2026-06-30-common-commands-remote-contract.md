# Common Commands Remote Contract

## Scope

This note records the implemented desktop-owned remote contract for common-commands sync.

Desktop is the only command authority.

Mobile uses the existing remote transport for:

- snapshot reads
- create
- update
- delete
- pin
- unpin
- move up
- move down

## Snapshot

Command type:

- `common_commands_list`

Success body shape:

```json
{
  "accepted": true,
  "revision": 7,
  "commands": [
    {
      "id": "cmd-1",
      "title": "List Files",
      "content": "dir",
      "pinned": false,
      "sort_order": 0,
      "version": 2,
      "revision": 6,
      "device_id": "desktop-a",
      "request_id": "req-1"
    }
  ]
}
```

Read error body:

```json
{
  "accepted": false,
  "error": "common_commands_unavailable"
}
```

## Mutations

Command types:

- `common_commands_create`
- `common_commands_update`
- `common_commands_delete`
- `common_commands_pin`
- `common_commands_unpin`
- `common_commands_move_up`
- `common_commands_move_down`

Shared request fields:

- `request_id`
- `device_id`
- `observed_revision`

Additional non-create request fields:

- `id`
- `observed_version`

Additional create request fields:

- `title`
- `content`

Additional update request fields:

- `title`
- `content`

Phase 1 and Phase 2 validation rules:

- `content` is required for create
- `title` may be empty

## Mutation Success

All mutation success responses return a fresh full snapshot body.

Create includes:

- `result: "created"` or `result: "replayed"`

Update includes:

- `result: "updated"`

Delete includes:

- `result: "deleted"`

Pin and unpin include:

- `result: "updated"`

Move up and move down include:

- `result: "updated"`

## Mutation Errors

Stale revision or stale version:

```json
{
  "accepted": false,
  "error": "stale_state"
}
```

Missing command:

```json
{
  "accepted": false,
  "error": "not_found"
}
```

Desktop read or storage unavailable:

```json
{
  "accepted": false,
  "error": "common_commands_unavailable"
}
```

## Idempotency

Desktop create replay detection uses:

- `device_id`
- `request_id`

If the same pair is seen again for create, desktop returns:

- success status
- `result: "replayed"`
- the latest full snapshot

## Current Real E2E Coverage

The real mobile integration test currently proves:

- live snapshot fetch works
- seeded desktop command appears on mobile
- snapshot revision is positive
- command id, title, and content are stable across an immediate second read

If the optional seeded ordering titles are supplied, the same real test also checks:

- a pinned command remains before unpinned commands
- an explicitly moved command remains before the following command in the unpinned section

The real mobile integration test does not yet prove:

- live mobile create, update, or delete round-trip against desktop authority
- live mobile send round-trip against the bound chat id
- live stale-state handling
- live not-found handling
