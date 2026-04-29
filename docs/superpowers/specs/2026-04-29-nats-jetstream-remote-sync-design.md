# NATS JetStream Remote Sync Design

## Goal

Replace the current desktop-to-mobile remote message sync transport with a NATS JetStream based transport while keeping the existing chat/control business protocol recognizable. The desktop app owns the NATS service by bundling and starting `nats-server.exe`, and the mobile app connects either directly or through the existing `cloudflared` public endpoint.

The first implementation targets reliable text message sync, remote state sync, history sync, request/reply prompts, and event replay. It also reserves a file attachment event shape so image, video, and file transfer can be added later without redesigning the messaging layer.

## Current Context

The desktop app in `c:\code\mc` is a Python/wx application. It currently exposes remote control through `remote_ws.py` and `remote_http.py`, with `main.py` wiring callbacks such as `_remote_api_message_ui`, `_remote_api_state_ui`, `_remote_api_history_list_ui`, `_remote_api_history_read_ui`, `_push_remote_state`, `_push_remote_final_answer`, and `_push_remote_history_changed`.

The mobile app in `c:\code\rc` is a Flutter/Dart app. Its foreground remote control path uses `lib/remote_socket_client.dart` through `RemoteCodexChatService`. Android background notifications currently use `RemoteBackgroundService.kt`, also over WebSocket.

The current public access path uses `cloudflared` to expose a WebSocket endpoint such as `wss://rc.tingyou.cc/ws` to the desktop WebSocket listener.

## Chosen Approach

Use NATS JetStream as the main remote sync transport.

The desktop app will:

- Start and stop a bundled `nats-server.exe`.
- Enable JetStream with a persistent store directory under the app data directory.
- Enable a normal NATS TCP listener for direct/public-IP access.
- Enable a NATS WebSocket listener for `cloudflared` access.
- Connect to the local NATS server as the authority process.
- Consume mobile commands durably and publish desktop events durably.

The mobile app will:

- Connect to `wss://rc.tingyou.cc/nats` when using `cloudflared`.
- Optionally connect to `ws://<desktop-lan-ip>:<ws-port>` on LAN.
- Optionally connect to `nats://<desktop-public-ip>:4222` when direct TCP is available.
- Consume desktop events with a durable consumer and ack after applying them.

The old WebSocket code remains as a fallback in the first version. Endpoints beginning with `nats://`, `ws://.../nats`, or `wss://.../nats` use the NATS path. Existing `ws://.../ws` and `wss://.../ws` endpoints continue to use the existing WebSocket path.

## NATS Runtime

The desktop app generates a local NATS config at startup:

```text
port: 4222
jetstream {
  store_dir: "<app_data>/nats/jetstream"
}
authorization {
  token: "<remote_control_token>"
}
websocket {
  port: 8081
  no_tls: true
}
```

`cloudflared` should route the public NATS WebSocket path to the local NATS WebSocket listener:

```text
wss://rc.tingyou.cc/nats -> http://127.0.0.1:8081
```

The NATS WebSocket listener itself stays local and non-TLS because TLS is terminated by Cloudflare. Direct public TCP access should use token authentication in the first version. TLS for direct NATS TCP is a later hardening task.

The desktop process manager must:

- Prefer the bundled `nats-server.exe` path in packaged builds.
- Allow an environment override for development.
- Write config files under app data, not the repo root.
- Start the process hidden on Windows.
- Verify readiness by connecting before reporting the remote endpoint as ready.
- Stop the child process when the desktop app exits.

## Streams And Subjects

Use a pair namespace to isolate one desktop/mobile pairing:

```text
zgwd.<pair_id>.commands
zgwd.<pair_id>.events
zgwd.<pair_id>.acks
zgwd.<pair_id>.files
```

Streams:

```text
ZGWD_COMMANDS_<pair_id>
  subjects: zgwd.<pair_id>.commands
  storage: file
  retention: work queue or limits

ZGWD_EVENTS_<pair_id>
  subjects: zgwd.<pair_id>.events, zgwd.<pair_id>.files
  storage: file
  retention: limits
```

The desktop has the authoritative command consumer. Mobile devices publish commands. Mobile devices have durable event consumers keyed by device id.

## Payloads

Command payload:

```json
{
  "id": "message-1",
  "type": "message",
  "chat_id": "chat-1",
  "text": "hello",
  "model": "codex/main",
  "device_id": "mobile-1",
  "created_at": 1710000000.0
}
```

Response event:

```json
{
  "event_id": "response-message-1",
  "request_id": "message-1",
  "type": "response",
  "ok": true,
  "status": 200,
  "body": {
    "accepted": true
  },
  "chat_id": "chat-1",
  "created_at": 1710000001.0
}
```

Desktop push event:

```json
{
  "event_id": "evt-1",
  "type": "state_changed",
  "chat_id": "chat-1",
  "body": {},
  "created_at": 1710000002.0
}
```

Future attachment event:

```json
{
  "event_id": "evt-file-1",
  "type": "attachment_ready",
  "chat_id": "chat-1",
  "message_id": "msg-1",
  "attachment": {
    "kind": "image",
    "name": "image.png",
    "size": 123456,
    "sha256": "abc",
    "download_url": "https://example.test/files/image.png"
  }
}
```

## Data Flow

Startup:

1. Desktop starts `nats-server.exe`.
2. Desktop initializes JetStream streams and command consumer.
3. Desktop publishes runtime status including direct TCP and WebSocket/cloudflared URLs.
4. Mobile loads settings and selects the NATS transport when the endpoint is NATS-shaped.
5. Mobile connects and requests `state` and `history_list` to hydrate UI from the desktop authority.
6. Mobile starts durable event consumption and acks events after local application.

Sending a message:

1. Mobile publishes a `message` command with a unique request id.
2. Desktop command consumer acks the command after routing it to `_remote_api_message_ui`.
3. Desktop publishes a `response` event for the request id.
4. Desktop later publishes `status`, `state_changed`, `final_answer`, and `history_changed` events as the chat progresses.
5. Mobile applies each event once, using existing `RemoteEventDeduper`, then acknowledges the JetStream message.

Reconnect:

1. Mobile reconnects to NATS.
2. Durable event consumption resumes after the last acked event.
3. Mobile still requests fresh `state` and `history_list` after reconnect to correct any stale local UI state.

## Error Handling

- Invalid JSON commands produce a durable `response` event with status `400`.
- Unknown command types produce status `404`.
- Desktop command handling exceptions produce status `500`.
- Duplicate command ids are idempotent where possible; desktop stores a bounded in-memory recent request cache for first implementation.
- Mobile ignores duplicate `event_id` values before applying to UI.
- If NATS startup fails, desktop reports NATS remote runtime as unavailable and leaves the old WebSocket fallback untouched.
- If mobile NATS support cannot connect to the configured endpoint, it surfaces the same disconnected status path currently used by WebSocket.

## Security

First version:

- Use the existing `remote_control_token` as NATS token authentication.
- Do not expose unauthenticated NATS listeners.
- For `cloudflared`, terminate TLS at Cloudflare and route to local NATS WebSocket.

Deferred hardening:

- TLS certificates for direct NATS TCP.
- Per-device credentials instead of one shared token.
- Pairing flow that generates `pair_id`, token, and endpoint QR code.

## Tests

Desktop tests:

- NATS runtime config generation includes JetStream, authorization, TCP listener, and WebSocket listener.
- NATS transport maps existing command types to existing `_remote_api_*` callbacks.
- Desktop publishes `response`, `state_changed`, `final_answer`, and `history_changed` events.
- Duplicate events contain stable ids and are safe for mobile dedupe.

Mobile tests:

- Endpoint parsing selects NATS transport for `nats://` and `/nats` WebSocket endpoints.
- NATS chat service sends commands with request ids and device ids.
- NATS event handler applies the same payload shapes as the current WebSocket service.
- Durable replay is simulated by delivering stored events after reconnect and verifying dedupe.

Integration smoke test:

- Start bundled or development NATS server.
- Start desktop transport.
- Connect a mobile-side test client.
- Send a `state` command and receive a `response`.
- Publish a desktop event and verify mobile handler applies it.

## Non-Goals For First Version

- Do not transfer large file bytes through NATS.
- Do not remove the existing WebSocket/HTTP code.
- Do not require a cloud server.
- Do not build a new UI for file management.
- Do not implement full TLS and per-device credential rotation in the first pass.
