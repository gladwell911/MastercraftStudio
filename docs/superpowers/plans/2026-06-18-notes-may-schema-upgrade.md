# Notes May Schema Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make May 2026 document-cache `notes.db` files upgrade in place without startup failure or data loss when opened by the current version.

**Architecture:** Keep the existing `NotesStore.initialize()` flow, but teach `_needs_legacy_migration()` that a May document-cache `entries` table missing only `pinned` is not legacy. Move the `pinned` column upgrade ahead of index creation so SQLite never builds an index against a missing column.

**Tech Stack:** Python, SQLite, pytest, existing `NotesStore` in `notes_store.py`.

---

### Task 1: Add Regression Tests For May Schema Upgrade

**Files:**
- Modify: `tests/test_notes_desktop_unit.py`

- [ ] **Step 1: Add a May-schema test helper**

Add this helper near the existing notes store schema tests in `tests/test_notes_desktop_unit.py`, after `test_notes_store_creates_document_cache_schema` or before the migration tests:

```python
def _create_may_document_cache_notes_db(db_path, *, migration_marker: bool) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE notebooks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                device_id TEXT NOT NULL DEFAULT '',
                last_modified_by TEXT NOT NULL DEFAULT 'desktop',
                is_conflict_copy INTEGER NOT NULL DEFAULT 0,
                origin_notebook_id TEXT,
                rev TEXT NOT NULL DEFAULT '',
                deleted INTEGER NOT NULL DEFAULT 0,
                dirty INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE entries (
                id TEXT PRIMARY KEY,
                notebook_id TEXT NOT NULL REFERENCES notebooks(id),
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                sort_order INTEGER NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                device_id TEXT NOT NULL DEFAULT '',
                last_modified_by TEXT NOT NULL DEFAULT 'desktop',
                is_conflict_copy INTEGER NOT NULL DEFAULT 0,
                origin_entry_id TEXT,
                source TEXT NOT NULL DEFAULT 'manual',
                rev TEXT NOT NULL DEFAULT '',
                deleted INTEGER NOT NULL DEFAULT 0,
                dirty INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE sync_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT INTO notebooks (
                id, title, created_at, updated_at, version, device_id,
                last_modified_by, is_conflict_copy, origin_notebook_id,
                rev, deleted, dirty
            ) VALUES (
                'may-nb', 'May notebook', '2026-05-26T00:00:00+00:00',
                '2026-05-26T00:01:00+00:00', 3, 'desktop-may',
                'desktop', 0, NULL, '1-may-nb', 0, 0
            );
            INSERT INTO entries (
                id, notebook_id, content, created_at, updated_at,
                sort_order, version, device_id, last_modified_by,
                is_conflict_copy, origin_entry_id, source,
                rev, deleted, dirty
            ) VALUES (
                'may-entry', 'may-nb', 'May entry body',
                '2026-05-26T00:02:00+00:00',
                '2026-05-26T00:03:00+00:00',
                7, 4, 'desktop-may', 'desktop', 0, NULL,
                'manual', '1-may-entry', 0, 0
            );
            INSERT INTO sync_state (key, value) VALUES ('last_cursor', '42');
            """
        )
        if migration_marker:
            conn.execute(
                "INSERT INTO sync_state (key, value) VALUES (?, ?)",
                ("legacy_notes_migration_complete", "complete"),
            )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 2: Add the failing test for May schema without marker**

Add this test after the helper:

```python
def test_notes_store_upgrades_may_document_cache_schema_without_marker_without_data_loss(tmp_path):
    db_path = tmp_path / "notes.db"
    _create_may_document_cache_notes_db(db_path, migration_marker=False)

    store = NotesStore(db_path, device_id="desktop-test")
    store.initialize()

    notebook = store.get_notebook("may-nb", include_deleted=True)
    entry = store.get_entry("may-entry", include_deleted=True)
    assert notebook is not None
    assert notebook.title == "May notebook"
    assert entry is not None
    assert entry.notebook_id == "may-nb"
    assert entry.content == "May entry body"
    assert entry.sort_order == 7
    assert entry.pinned is False
    assert store.current_cursor() == "42"
```

- [ ] **Step 3: Add the failing test for May schema with marker**

Add this test after the previous one:

```python
def test_notes_store_upgrades_may_document_cache_schema_with_marker_without_data_loss(tmp_path):
    db_path = tmp_path / "notes.db"
    _create_may_document_cache_notes_db(db_path, migration_marker=True)

    store = NotesStore(db_path, device_id="desktop-test")
    store.initialize()

    notebook = store.get_notebook("may-nb", include_deleted=True)
    entry = store.get_entry("may-entry", include_deleted=True)
    assert notebook is not None
    assert notebook.title == "May notebook"
    assert entry is not None
    assert entry.content == "May entry body"
    assert entry.pinned is False
    assert store.current_cursor() == "42"
```

- [ ] **Step 4: Run tests to verify they fail for the expected reasons**

Run:

```powershell
python -m pytest tests/test_notes_desktop_unit.py -k "may_document_cache_schema" -q
```

Expected:

- The no-marker test fails because the notebook/entry are missing after initialization.
- The marker test fails with `sqlite3.OperationalError: no such column: pinned`.

Do not change production code until both failures are observed.

### Task 2: Implement The Minimal In-Place Schema Upgrade

**Files:**
- Modify: `notes_store.py`

- [ ] **Step 1: Add a helper for May document-cache entry columns**

In `notes_store.py`, near `NOTEBOOK_COLUMNS` and `ENTRY_COLUMNS`, add:

```python
MAY_DOCUMENT_CACHE_ENTRY_COLUMNS = ENTRY_COLUMNS - {"pinned"}
```

- [ ] **Step 2: Update `_needs_legacy_migration()`**

Replace the current `entries` column comparison:

```python
        if "entries" in tables and self._table_columns(conn, "entries") != ENTRY_COLUMNS:
            return True
```

with:

```python
        if "entries" in tables:
            entry_columns = self._table_columns(conn, "entries")
            if entry_columns not in {ENTRY_COLUMNS, MAY_DOCUMENT_CACHE_ENTRY_COLUMNS}:
                return True
```

This makes a May document-cache `entries` table missing only `pinned` upgrade in place instead of going through drop/rebuild legacy migration.

- [ ] **Step 3: Reorder `_create_document_cache_schema()`**

In `_create_document_cache_schema()`, remove the index creation from the `executescript()` block:

```sql
            CREATE INDEX IF NOT EXISTS idx_entries_notebook_sort
            ON entries (notebook_id, pinned, sort_order, created_at);
```

Keep the existing post-script column check:

```python
        if "pinned" not in self._table_columns(conn, "entries"):
            conn.execute("ALTER TABLE entries ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
```

Then add the index creation after that check:

```python
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_entries_notebook_sort
            ON entries (notebook_id, pinned, sort_order, created_at)
            """
        )
```

- [ ] **Step 4: Run the focused regression tests**

Run:

```powershell
python -m pytest tests/test_notes_desktop_unit.py -k "may_document_cache_schema" -q
```

Expected: both new tests pass.

### Task 3: Verify Existing Notes Store Behavior

**Files:**
- No new edits expected.

- [ ] **Step 1: Run targeted schema, migration, and pinned tests**

Run:

```powershell
python -m pytest tests/test_notes_desktop_unit.py -k "notes_store and (schema or migration or pinned or reorders)" -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run the full notes desktop unit file if targeted selection is small or ambiguous**

Run:

```powershell
python -m pytest tests/test_notes_desktop_unit.py -q
```

Expected: all tests pass. If unrelated failures appear, capture the failing test names and inspect before changing code.

- [ ] **Step 3: Check the final diff**

Run:

```powershell
git diff -- notes_store.py tests/test_notes_desktop_unit.py
```

Expected:

- `notes_store.py` changes are limited to the May schema column-set check and index creation order.
- `tests/test_notes_desktop_unit.py` adds only the helper and the two regression tests.

### Task 4: Commit The Fix Separately If The Worktree Allows

**Files:**
- Modify: `notes_store.py`
- Modify: `tests/test_notes_desktop_unit.py`

- [ ] **Step 1: Check unrelated worktree changes**

Run:

```powershell
git status --short
```

Expected: identify whether `notes_store.py` and `tests/test_notes_desktop_unit.py` have only the intended changes. Do not stage unrelated existing changes.

- [ ] **Step 2: Stage only the fix files**

Run:

```powershell
git add notes_store.py tests/test_notes_desktop_unit.py docs/superpowers/specs/2026-06-18-notes-may-schema-upgrade-design.md docs/superpowers/plans/2026-06-18-notes-may-schema-upgrade.md
```

If those files contain unrelated user changes, stop and ask for direction before committing.

- [ ] **Step 3: Commit**

Run:

```powershell
git commit -m "fix: preserve May notes database schema upgrade"
```

Expected: commit succeeds and contains only the notes schema upgrade fix, regression tests, and supporting Superpowers docs.
