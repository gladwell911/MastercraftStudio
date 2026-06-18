# Notes May Schema Upgrade Design

## Goal

Fix the high-risk upgrade path where a notes database from the last working May 2026 version can fail or lose visible notes when opened by the current project version.

The fix is scoped to the May document-cache schema. It must not redesign the notes store, move data directories, or broaden migration support beyond what is needed for May `notes.db` files.

## Background

The May 26, 2026 version stored notes in a SQLite document-cache schema with these tables:

- `notebooks`
- `entries`
- `sync_state`

The current version adds `entries.pinned` and orders entries by `pinned DESC, sort_order ASC, created_at ASC, id ASC`.

Two failure modes were reproduced against a May-style database:

- If `sync_state` contains `legacy_notes_migration_complete=complete`, `initialize()` skips legacy migration and calls `_create_document_cache_schema()`, but index creation references `pinned` before the column exists. SQLite raises `OperationalError: no such column: pinned`.
- If the marker is absent, `_needs_legacy_migration()` sees the `entries` column set differs from the current `ENTRY_COLUMNS`, treats the database as legacy, drops notes tables, and `_read_legacy_snapshot()` does not read the May `entries` table. The result is an empty notes database.

## Design

Use a minimal, in-place schema upgrade for May document-cache databases.

`NotesStore.initialize()` should continue to call `_needs_legacy_migration()` before `_create_document_cache_schema()`, but `_needs_legacy_migration()` must distinguish a May document-cache schema from a true legacy schema.

A database should not be treated as legacy when:

- It has `notebooks` and `entries`.
- The `notebooks` columns match the current `NOTEBOOK_COLUMNS`.
- The `entries` columns equal the current `ENTRY_COLUMNS` minus only `pinned`.

In that case, initialization should proceed to `_create_document_cache_schema()` without dropping any tables.

`_create_document_cache_schema()` should perform schema upgrade in this order:

1. Create missing base tables with `CREATE TABLE IF NOT EXISTS`.
2. Check `PRAGMA table_info(entries)`.
3. If `pinned` is missing, run:

   ```sql
   ALTER TABLE entries ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0
   ```

4. Create the `idx_entries_notebook_sort` index after `pinned` exists.

The existing legacy migration path for older `note_entries`, `sync_outbox`, and `notes_change_log` tables remains in place. This design does not require changing `_read_legacy_snapshot()` for the May schema because the May schema should no longer enter that path.

## Data Preservation

For May document-cache databases, initialization must preserve:

- Notebook IDs, titles, timestamps, versions, device metadata, deleted flags, dirty flags, and revision values.
- Entry IDs, notebook IDs, content, timestamps, sort order, versions, device metadata, conflict metadata, source, deleted flags, dirty flags, and revision values.
- Existing `sync_state` values.

All existing entries receive `pinned = 0`.

## Tests

Add focused regression tests in `tests/test_notes_desktop_unit.py`.

Test 1: May schema without migration marker.

- Build a temporary SQLite database with May `notebooks`, `entries`, and `sync_state` tables.
- Insert one notebook and one entry.
- Do not insert `legacy_notes_migration_complete`.
- Run `NotesStore(db_path, device_id="desktop-test").initialize()`.
- Assert the notebook and entry still exist.
- Assert the entry content is unchanged.
- Assert `entry.pinned is False`.

Test 2: May schema with migration marker.

- Build the same temporary May schema.
- Insert `legacy_notes_migration_complete=complete`.
- Run `initialize()`.
- Assert initialization does not raise.
- Assert the notebook and entry still exist.
- Assert `entry.pinned is False`.

Run targeted verification:

```powershell
python -m pytest tests/test_notes_desktop_unit.py -k "may_schema or migration or pinned" -q
```

If the keyword selection is too broad or too narrow after test naming, run the two exact new tests plus the existing schema/migration/pinned note-store tests.

## Non-Goals

- Do not move notes storage. `resolve_notes_data_dir()` remains responsible for `D:\code\note`.
- Do not add backup/restore behavior.
- Do not repair arbitrary corrupted databases.
- Do not redesign `NotesStore`.
- Do not change UI behavior.

## Success Criteria

- A May document-cache `notes.db` without `pinned` upgrades in place.
- No May notebook or entry data is dropped during initialization.
- Databases with and without the migration marker both work.
- Existing note-store schema, migration, and pinned-entry tests still pass.
