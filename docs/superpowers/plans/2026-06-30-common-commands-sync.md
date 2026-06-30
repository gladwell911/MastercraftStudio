# Common Commands Sync Implementation Plan

**Goal:** Build a global desktop-owned common-commands library that works locally on desktop first, then syncs to mobile through the existing remote transport, with stable keyboard and screen-reader behavior.

**Architecture:** Deliver this in staged milestones. First ship a desktop-only command store and command UI that can open with `Alt+M` and send into the active chat. Next add a minimal remote snapshot contract and verify desktop-to-mobile read/sync before building mobile editing. Only after the cross-device MVP is stable should pin, unpin, and section-local reorder be added.

**Tech Stack:** Python 3.11, wxPython, pytest, Flutter, Dart, existing NATS-backed remote transport

---

## Status Update

- Milestone 1 is implemented and verified.
- Milestone 2 is implemented and verified.
- Milestone 3 is implemented and verified for desktop local flow, mobile browse and send, mobile add, edit, and delete, and desktop-owned remote mutation handling.
- Milestone 4 is implemented and verified for pin, unpin, move up, and move down.
- Local desktop and mobile automated coverage is in place for CRUD, send, ordering, stale-state handling, and focus stability.
- The current real cross-device integration test proves desktop-to-mobile snapshot visibility and snapshot stability.
- The current real cross-device integration test does not yet prove a full real-environment mobile mutation round-trip.

## Milestones

### Milestone 1: Desktop Local MVP

Deliver:

- desktop command store
- desktop command window or dialog
- `Alt+M`
- add, edit, delete
- Enter-to-send into active chat
- desktop focus-stability tests

Exit criteria:

- a desktop user can create commands, reopen the app, and still see them
- a desktop user can open common commands with `Alt+M`
- pressing Enter on a selected command sends the command content to the active chat
- desktop list refresh does not steal focus during background updates

Status:

- complete

### Milestone 2: Remote Read Smoke

Deliver:

- desktop remote read endpoint for full snapshot
- mobile service can fetch and parse command snapshot
- minimal cross-device smoke test proving desktop create appears on mobile

Exit criteria:

- mobile can fetch the desktop command list snapshot
- snapshot includes revision and commands
- desktop create becomes visible on mobile in the defined sync window

Status:

- complete

### Milestone 3: Cross-Device MVP

Deliver:

- mobile command page
- mobile initial accessibility focus
- mobile send bound to the current chat detail chat id
- mobile add, edit, and delete through desktop authority
- stale and disconnected UX

Exit criteria:

- mobile chat detail button is `命令`
- opening the page focuses the first command or `添加`
- mobile can create, edit, delete, and send commands while connected
- disconnected mobile shows stale-read-only state and disables mutation

Status:

- complete in local automation
- real-environment mobile mutation round-trip still not covered

### Milestone 4: Phase 2 Ordering

Deliver:

- pin and unpin
- move up and move down within section
- stale-state reorder rejection
- cross-device ordering sync tests

Exit criteria:

- desktop and mobile both show pinned section before unpinned section
- move up and move down never cross section boundaries
- concurrent stale reorder is rejected without corrupting state

Status:

- complete in local automation
- real-environment ordering assertions are available in the integration test when seeded titles are provided

## Implemented File Map

### Desktop (`D:\code\sj\mc`)

- `common_commands_models.py`
- `common_commands_store.py`
- `main.py`
- `remote_nats.py`
- `scripts/real_desktop_remote_e2e_runtime.py`
- `tests/fixtures/common_commands_snapshot.json`
- `tests/test_common_commands_store_unit.py`
- `tests/test_common_commands_ui_automation.py`
- `tests/test_codex_ui_responsiveness_automation.py`
- `tests/test_main_unit.py`
- `tests/test_remote_nats_unit.py`

### Mobile (`D:\code\sj\rc`)

- `lib/common_commands_models.dart`
- `lib/common_commands_service.dart`
- `lib/common_commands_page.dart`
- `lib/main.dart`
- `integration_test/common_commands_remote_sync_e2e_test.dart`
- `test/common_commands_service_test.dart`
- `test/common_commands_page_test.dart`

## Environment Notes

- Desktop common commands persist at `resolve_app_data_dir() / "common_commands.json"`.
- Desktop remains the only persisted command authority.
- Real cross-device integration requires a remote endpoint, token, and desktop harness runtime.
- Optional seeded ordering assertions in the mobile real integration test require:
  - `REAL_REMOTE_E2E_COMMON_COMMAND_PINNED_TITLE`
  - `REAL_REMOTE_E2E_COMMON_COMMAND_MOVED_TITLE`
  - `REAL_REMOTE_E2E_COMMON_COMMAND_FOLLOWING_TITLE`

## Release and Rollback Notes

- Phase 1 and Phase 2 are now both implemented on `main`.
- If Phase 2 needs to be backed out later, command storage should be preserved; rollback should hide UI behavior before deleting stored data.
- The persisted data contract remains `common_commands.json` under app data and should not be migrated destructively during rollback.

## Verification Summary

Desktop verification used during implementation included:

- `pytest tests/test_common_commands_store_unit.py tests/test_common_commands_ui_automation.py -q`
- `pytest tests/test_codex_ui_responsiveness_automation.py -k common_commands -q`
- `pytest tests/test_remote_nats_unit.py -k common_commands -q`
- `pytest tests/test_main_unit.py -k "common_commands or remote_message_preserves_existing_chat_model_when_payload_uses_default_codex or remote_message_explicitly_switches_existing_chat_model or remote_message_rejects_invalid_explicit_model_switch or remote_message_rejects_missing_explicit_model_switch or remote_message_unknown_chat_id_uses_requested_model_for_new_remote_chat" -q`

Mobile verification used during implementation included:

- `flutter test test/common_commands_service_test.dart`
- `flutter test test/common_commands_page_test.dart`
- `dart analyze lib/common_commands_service.dart lib/common_commands_page.dart lib/remote_nats_chat_service.dart test/common_commands_service_test.dart test/common_commands_page_test.dart`

Real cross-device verification currently covers:

- desktop-seeded command visible on mobile snapshot
- snapshot stability across an immediate second read
- optional ordering assertions when seeded titles are provided

Real cross-device verification does not yet cover:

- live mobile create, edit, delete round-trip
- live mobile send round-trip
