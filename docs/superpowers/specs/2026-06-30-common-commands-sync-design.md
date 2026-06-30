# Common Commands Sync Design

## Summary

This feature adds one global "common commands" library shared by the desktop wxPython app and the mobile chat detail view.

Users can:

- create commands
- edit commands
- delete commands
- send commands directly to the current chat
- pin and unpin commands
- reorder commands inside the pinned section
- reorder commands inside the unpinned section

The desktop app is the only persisted source of truth. The mobile app displays and edits the same library through the existing remote desktop transport while connected.

## Implementation Status

- Phase 1 is implemented.
- Phase 2 is implemented.
- Desktop local store, desktop command window, desktop remote snapshot API, desktop mutation APIs, mobile command page, mobile add/edit/delete flows, pin/unpin, and section-local move up/down are in place.
- Local desktop and mobile automated coverage is in place for CRUD, send, ordering, stale-state handling, and focus-stability behavior.
- The current real cross-device integration test proves desktop-to-mobile snapshot visibility and snapshot stability.
- The current real cross-device integration test does not yet drive a full live mobile create, edit, delete, or send round-trip against desktop authority.

## Canonical UI Strings

These strings are normative and should be used by implementation and tests.

- Desktop Alt menu item: `常用命令`
- Desktop shortcut: `Alt+M`
- Desktop window title: `常用命令`
- Desktop add button: `添加命令`
- Desktop close button: `关闭`
- Desktop add form title: `添加命令`
- Desktop edit form title: `编辑命令`
- Desktop context menu items:
  - `添加`
  - `编辑`
  - `删除`
  - `置顶`
  - `取消置顶`
  - `向上移动`
  - `向下移动`
- Mobile chat detail button label: `命令`
- Mobile page title: `常用命令`
- Mobile add button: `添加`
- Mobile disconnected mutation message: `当前未连接电脑，无法修改常用命令`
- Validation fallback title for empty title records: `未命名命令`
- Validation error for empty content: `命令内容不能为空`

## Goals

- Add a desktop entry in the Alt menu and open it with `Alt+M`.
- Add a mobile entry by renaming the current chat-detail `自定义` button to `命令`.
- Keep one global command library shared by desktop and mobile.
- Allow add, edit, delete, pin, unpin, move up, and move down on both platforms.
- Send a command immediately when the user activates its title in the command list.
- Keep keyboard focus and screen-reader behavior stable during background sync updates.
- Keep desktop and mobile ordering synchronized, including pinned and unpinned sections.

## Non-Goals

- No per-chat command libraries in v1.
- No offline mobile editing queue in v1.
- No bulk command operations in v1.
- No drag-and-drop ordering in v1.
- No independent mobile authority copy.
- No conflict merge UI in v1.

## Product Rules

- Common commands are global across the whole product.
- Desktop is the only persisted command authority.
- Mobile reads from the desktop-owned command library and sends remote mutation requests to desktop.
- Commands are displayed in two sections:
  - pinned commands first
  - unpinned commands second
- Both sections support manual reorder.
- Reorder operations only work inside the current section.
- Crossing between sections only happens through pin or unpin.
- The command title is display-only. Sending uses the command content.
- Command content may be multi-line.
- Empty titles are allowed in persisted data. When a title is empty, UI surfaces should present `未命名命令`.

## Phase Split

### Phase 1

- global command library
- desktop and mobile list display
- add, edit, delete
- direct send
- desktop-hosted sync
- mobile online-only editing
- initial accessibility and focus behavior

### Phase 2

- pin and unpin
- move up and move down inside sections
- section-aware reorder conflict tests
- additional stress and performance coverage

## Behavior Contract

### Execution Target Resolution

The command library is global, but every send action targets exactly one chat session.

Desktop target resolution:

1. Resolve the active chat at the moment the user presses Enter on a command.
2. If the active chat changes while the command window is open, sending follows the new active chat.
3. If there is no valid active chat, the command is not sent and the desktop app shows a clear error.

Mobile target resolution:

1. Bind the command page to the chat detail chat id at the moment the page is opened.
2. Sending from that page uses the bound chat id even if other remote state changes arrive later.
3. If the bound chat id is no longer valid when send is attempted, the mobile app surfaces an error and does not silently retarget.

### Accessibility and Focus

- Desktop command list refresh must not steal focus, clear selection, or repaint the entire list when there is no visible state change.
- Mobile command page should move accessibility focus to the first command on initial page open, or to `添加` when the list is empty.
- Background sync updates must not jump mobile accessibility focus back to the first row after the initial open.
- If the currently focused command is removed remotely, the next logical row should receive focus; if no row remains, focus falls back to `添加`.

### Ordering

- Pinned commands render before unpinned commands.
- `向上移动` and `向下移动` only reorder within the current section.
- `置顶` moves a command into the pinned section tail.
- `取消置顶` moves a command into the unpinned section tail.
- Stale reorder attempts are rejected without partial writes.

## Persistence

- Desktop persists commands at `resolve_app_data_dir() / "common_commands.json"`.
- Commands are stored with desktop-owned `revision` and per-command `version`.
- Create idempotency uses the pair `device_id + request_id`.

## Current Validation Rules

- `content` is required.
- `title` may be empty.
- Multi-line content is allowed.
- Send behavior always uses `content`, never `title`.

## Real-Environment Coverage Boundary

Current real cross-device automation proves:

- desktop snapshot fetch is reachable from mobile
- seeded desktop commands appear on mobile
- snapshot revision and command payload stay stable across an immediate second read

Current real cross-device automation does not yet prove:

- live mobile create against desktop authority
- live mobile edit against desktop authority
- live mobile delete against desktop authority
- live mobile send against the bound remote chat id
