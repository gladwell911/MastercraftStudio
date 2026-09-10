from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from notes_models import EntryDoc, NoteEntry, Notebook, NotebookDoc, NotesSnapshot, SyncOp

LEGACY_MIGRATION_STATE_KEY = "legacy_notes_migration_complete"
LEGACY_MIGRATION_STATE_VALUE = "complete"
LAST_CURSOR_STATE_KEY = "last_cursor"
COMPAT_OUTBOX_STATE_KEY = "compat_outbox"
PLACEMENT_MIGRATION_STATE_KEY = "entry_placement_v2"
PLACEMENT_MIGRATION_STATE_VALUE = "complete"

NOTEBOOK_COLUMNS = {
    "id",
    "title",
    "created_at",
    "updated_at",
    "version",
    "device_id",
    "last_modified_by",
    "is_conflict_copy",
    "origin_notebook_id",
    "rev",
    "deleted",
    "dirty",
}
ENTRY_COLUMNS = {
    "id",
    "notebook_id",
    "content",
    "created_at",
    "updated_at",
    "sort_order",
    "pinned",
    "placement",
    "region_order",
    "normal_predecessor_id",
    "normal_successor_id",
    "version",
    "device_id",
    "last_modified_by",
    "is_conflict_copy",
    "origin_entry_id",
    "source",
    "rev",
    "deleted",
    "dirty",
}
PRE_PLACEMENT_ENTRY_COLUMNS = ENTRY_COLUMNS - {"placement", "region_order", "normal_predecessor_id", "normal_successor_id"}
MAY_DOCUMENT_CACHE_ENTRY_COLUMNS = PRE_PLACEMENT_ENTRY_COLUMNS - {"pinned"}


class NotesPlacementConflict(RuntimeError):
    """The selected entry changed before its placement mutation committed."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class NotesStore:
    def __init__(self, db_path: Path, device_id: str) -> None:
        self.db_path = Path(db_path)
        self.device_id = str(device_id or "").strip() or "desktop-local"

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            if self._needs_legacy_migration(conn):
                snapshot = self._read_legacy_snapshot(conn)
                self._drop_notes_tables(conn)
                self._create_document_cache_schema(conn)
                self._write_snapshot(conn, snapshot)
                self._set_sync_state(conn, LEGACY_MIGRATION_STATE_KEY, LEGACY_MIGRATION_STATE_VALUE)
            else:
                self._create_document_cache_schema(conn)
            self._migrate_entry_placement(conn)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _table_names(self, conn: sqlite3.Connection) -> set[str]:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        return {str(row["name"]) for row in rows}

    def _table_columns(self, conn: sqlite3.Connection, table_name: str) -> set[str]:
        return {
            str(row["name"])
            for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }

    def _needs_legacy_migration(self, conn: sqlite3.Connection) -> bool:
        tables = self._table_names(conn)
        if "sync_state" in tables:
            marker = conn.execute(
                "SELECT value FROM sync_state WHERE key = ?",
                (LEGACY_MIGRATION_STATE_KEY,),
            ).fetchone()
            if marker is not None and str(marker["value"]) == LEGACY_MIGRATION_STATE_VALUE:
                return False
        if "note_entries" in tables or "sync_outbox" in tables or "notes_change_log" in tables:
            return True
        if "notebooks" in tables and self._table_columns(conn, "notebooks") != NOTEBOOK_COLUMNS:
            return True
        if "entries" in tables:
            entry_columns = self._table_columns(conn, "entries")
            if entry_columns not in (ENTRY_COLUMNS, PRE_PLACEMENT_ENTRY_COLUMNS, MAY_DOCUMENT_CACHE_ENTRY_COLUMNS):
                return True
        return False

    def _create_document_cache_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS notebooks (
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
            CREATE TABLE IF NOT EXISTS entries (
                id TEXT PRIMARY KEY,
                notebook_id TEXT NOT NULL REFERENCES notebooks(id),
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                sort_order INTEGER NOT NULL,
                pinned INTEGER NOT NULL DEFAULT 0,
                placement TEXT NOT NULL DEFAULT 'normal' CHECK (placement IN ('top', 'normal', 'bottom')),
                region_order INTEGER NOT NULL DEFAULT 0,
                normal_predecessor_id TEXT,
                normal_successor_id TEXT,
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
            CREATE TABLE IF NOT EXISTS sync_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        if "pinned" not in self._table_columns(conn, "entries"):
            conn.execute("ALTER TABLE entries ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
        columns = self._table_columns(conn, "entries")
        if "placement" not in columns:
            conn.execute("ALTER TABLE entries ADD COLUMN placement TEXT NOT NULL DEFAULT 'normal'")
        if "region_order" not in columns:
            conn.execute("ALTER TABLE entries ADD COLUMN region_order INTEGER NOT NULL DEFAULT 0")
        if "normal_predecessor_id" not in columns:
            conn.execute("ALTER TABLE entries ADD COLUMN normal_predecessor_id TEXT")
        if "normal_successor_id" not in columns:
            conn.execute("ALTER TABLE entries ADD COLUMN normal_successor_id TEXT")
        conn.execute("DROP INDEX IF EXISTS idx_entries_notebook_sort")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_entries_notebook_sort
            ON entries (notebook_id, placement, region_order, id)
            """
        )

    def _migrate_entry_placement(self, conn: sqlite3.Connection) -> None:
        marker = conn.execute("SELECT value FROM sync_state WHERE key = ?", (PLACEMENT_MIGRATION_STATE_KEY,)).fetchone()
        invalid = conn.execute("SELECT 1 FROM entries WHERE placement IS NULL OR placement NOT IN ('top','normal','bottom') OR region_order < 0 OR pinned != CASE WHEN placement='top' THEN 1 ELSE 0 END LIMIT 1").fetchone()
        if marker is not None and str(marker["value"]) == PLACEMENT_MIGRATION_STATE_VALUE and invalid is None:
            return
        # Normalize every row independently; partially migrated databases are valid input.
        conn.execute("UPDATE entries SET placement = CASE WHEN pinned != 0 THEN 'top' ELSE 'normal' END WHERE placement IS NULL OR placement NOT IN ('top','normal','bottom') OR (pinned != 0 AND placement = 'normal' AND normal_predecessor_id IS NULL AND normal_successor_id IS NULL)")
        rows = conn.execute(
            "SELECT id, notebook_id, placement, region_order FROM entries ORDER BY notebook_id, CASE placement WHEN 'top' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, sort_order, created_at, id"
        ).fetchall()
        counters: dict[tuple[str, str], int] = {}
        for row in rows:
            key = (str(row["notebook_id"]), str(row["placement"]))
            order = counters.get(key, 0)
            conn.execute("UPDATE entries SET region_order = ?, sort_order = ?, pinned = ? WHERE id = ?", (order, order, int(key[1] == "top"), row["id"]))
            counters[key] = order + 1
        self._set_sync_state(conn, PLACEMENT_MIGRATION_STATE_KEY, PLACEMENT_MIGRATION_STATE_VALUE)

    def _drop_notes_tables(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            DROP TABLE IF EXISTS sync_outbox;
            DROP TABLE IF EXISTS notes_change_log;
            DROP TABLE IF EXISTS note_entries;
            DROP TABLE IF EXISTS entries;
            DROP TABLE IF EXISTS notebooks;
            DROP TABLE IF EXISTS sync_state;
            """
        )

    def _read_legacy_snapshot(self, conn: sqlite3.Connection) -> NotesSnapshot:
        notebooks: list[NotebookDoc] = []
        entries: list[EntryDoc] = []
        tables = self._table_names(conn)
        if "notebooks" in tables and "deleted_at" in self._table_columns(conn, "notebooks"):
            notebook_rows = conn.execute(
                """
                SELECT * FROM notebooks
                ORDER BY updated_at DESC, created_at DESC, id ASC
                """
            ).fetchall()
            notebooks = [
                NotebookDoc(
                    id=str(row["id"]),
                    title=str(row["title"] or ""),
                    created_at=str(row["created_at"] or ""),
                    updated_at=str(row["updated_at"] or row["created_at"] or ""),
                    version=int(row["version"] or 1),
                    device_id=str(row["device_id"] or ""),
                    last_modified_by=str(row["last_modified_by"] or "desktop"),
                    is_conflict_copy=bool(row["is_conflict_copy"]),
                    origin_notebook_id=str(row["origin_notebook_id"]) if row["origin_notebook_id"] else None,
                    deleted=bool(row["deleted_at"]),
                    dirty=True,
                )
                for row in notebook_rows
            ]
        if "note_entries" in tables:
            entry_rows = conn.execute(
                """
                SELECT * FROM note_entries
                ORDER BY notebook_id ASC, sort_order ASC, created_at ASC, id ASC
                """
            ).fetchall()
            entries = [
                EntryDoc(
                    id=str(row["id"]),
                    notebook_id=str(row["notebook_id"] or ""),
                    content=str(row["content"] or ""),
                    created_at=str(row["created_at"] or ""),
                    updated_at=str(row["updated_at"] or row["created_at"] or ""),
                    sort_order=int(row["sort_order"] or 0),
                    pinned=bool(row["pinned"]),
                    placement="top" if bool(row["pinned"]) else "normal",
                    region_order=int(row["sort_order"] or 0),
                    version=int(row["version"] or 1),
                    device_id=str(row["device_id"] or ""),
                    last_modified_by=str(row["last_modified_by"] or "desktop"),
                    is_conflict_copy=bool(row["is_conflict_copy"]),
                    origin_entry_id=str(row["origin_entry_id"]) if row["origin_entry_id"] else None,
                    source=str(row["source"] or "manual"),
                    deleted=bool(row["deleted_at"]),
                    dirty=True,
                )
                for row in entry_rows
            ]
        return NotesSnapshot(notebooks=notebooks, entries=entries)

    def _write_snapshot(self, conn: sqlite3.Connection, snapshot: NotesSnapshot) -> None:
        for notebook in snapshot.notebooks:
            self._insert_notebook_doc(conn, notebook)
        for entry in snapshot.entries:
            self._insert_entry_doc(conn, entry)

    def _insert_notebook_doc(self, conn: sqlite3.Connection, doc: NotebookDoc) -> None:
        conn.execute(
            """
            INSERT INTO notebooks (
                id, title, created_at, updated_at, version, device_id,
                last_modified_by, is_conflict_copy, origin_notebook_id,
                rev, deleted, dirty
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                doc.id,
                doc.title,
                doc.created_at,
                doc.updated_at,
                doc.version,
                doc.device_id,
                doc.last_modified_by,
                int(doc.is_conflict_copy),
                doc.origin_notebook_id,
                doc.rev,
                int(doc.deleted),
                int(doc.dirty),
            ),
        )

    def _insert_entry_doc(self, conn: sqlite3.Connection, doc: EntryDoc) -> None:
        conn.execute(
            """
            INSERT INTO entries (
                id, notebook_id, content, created_at, updated_at,
                sort_order, pinned, version, device_id, last_modified_by,
                placement, region_order, normal_predecessor_id, normal_successor_id,
                is_conflict_copy, origin_entry_id, source,
                rev, deleted, dirty
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                doc.id,
                doc.notebook_id,
                doc.content,
                doc.created_at,
                doc.updated_at,
                doc.sort_order,
                int(doc.pinned),
                doc.version,
                doc.device_id,
                doc.last_modified_by,
                doc.placement,
                doc.region_order,
                doc.normal_predecessor_id,
                doc.normal_successor_id,
                int(doc.is_conflict_copy),
                doc.origin_entry_id,
                doc.source,
                doc.rev,
                int(doc.deleted),
                int(doc.dirty),
            ),
        )

    def _set_sync_state(self, conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute(
            """
            INSERT INTO sync_state (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )

    def sync_state_value(self, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM sync_state WHERE key = ?",
                (str(key),),
            ).fetchone()
        return str(row["value"]) if row is not None else None

    def _next_cursor(self, conn: sqlite3.Connection) -> str:
        row = conn.execute(
            "SELECT value FROM sync_state WHERE key = ?",
            (LAST_CURSOR_STATE_KEY,),
        ).fetchone()
        try:
            current_value = int(row["value"]) if row is not None else 0
        except Exception:
            current_value = 0
        next_value = current_value + 1
        self._set_sync_state(conn, LAST_CURSOR_STATE_KEY, str(next_value))
        return str(next_value)

    def _load_compat_outbox(self, conn: sqlite3.Connection) -> list[dict]:
        row = conn.execute(
            "SELECT value FROM sync_state WHERE key = ?",
            (COMPAT_OUTBOX_STATE_KEY,),
        ).fetchone()
        if row is None:
            return []
        try:
            value = json.loads(str(row["value"] or "[]"))
        except Exception:
            return []
        return value if isinstance(value, list) else []

    def _save_compat_outbox(self, conn: sqlite3.Connection, ops: list[dict]) -> None:
        self._set_sync_state(conn, COMPAT_OUTBOX_STATE_KEY, json.dumps(ops, ensure_ascii=False))

    def _sync_op_from_dict(self, payload: dict) -> SyncOp:
        return SyncOp.from_row(payload)

    def _record_compat_op(
        self,
        conn: sqlite3.Connection,
        *,
        entity_type: str,
        entity_id: str,
        action: str,
        payload: dict,
        base_version: int,
    ) -> SyncOp:
        ops = self._load_compat_outbox(conn)
        op = {
            "op_id": uuid.uuid4().hex,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "action": action,
            "payload_json": json.dumps(payload, ensure_ascii=False),
            "base_version": int(base_version),
            "created_at": _utc_now(),
            "retry_count": 0,
            "status": "pending",
        }
        ops.append(op)
        self._save_compat_outbox(conn, ops)
        self._next_cursor(conn)
        return self._sync_op_from_dict(op)

    def _replace_compat_ops(self, conn: sqlite3.Connection, ops: list[dict]) -> None:
        self._save_compat_outbox(conn, ops)

    def load_documents(self) -> NotesSnapshot:
        with self._connect() as conn:
            notebook_rows = conn.execute(
                """
                SELECT * FROM notebooks
                ORDER BY updated_at DESC, created_at DESC, id ASC
                """
            ).fetchall()
            entry_rows = conn.execute(
                """
                SELECT * FROM entries
                ORDER BY notebook_id ASC, CASE placement WHEN 'top' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, region_order ASC, id ASC
                """
            ).fetchall()
        return NotesSnapshot(
            notebooks=[NotebookDoc.from_row(dict(row)) for row in notebook_rows],
            entries=[EntryDoc.from_row(dict(row)) for row in entry_rows],
        )

    def load_dirty_documents(self) -> NotesSnapshot:
        with self._connect() as conn:
            notebook_rows = conn.execute(
                """
                SELECT * FROM notebooks
                WHERE dirty = 1
                ORDER BY updated_at DESC, created_at DESC, id ASC
                """
            ).fetchall()
            entry_rows = conn.execute(
                """
                SELECT * FROM entries
                WHERE dirty = 1
                ORDER BY notebook_id ASC, CASE placement WHEN 'top' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, region_order ASC, id ASC
                """
            ).fetchall()
        return NotesSnapshot(
            notebooks=[NotebookDoc.from_row(dict(row)) for row in notebook_rows],
            entries=[EntryDoc.from_row(dict(row)) for row in entry_rows],
        )

    def snapshot_documents(self) -> list[dict]:
        return self.load_documents().to_documents()

    def _project_notebook(self, doc: NotebookDoc) -> Notebook:
        return Notebook.from_doc(doc, device_id=self.device_id)

    def _project_entry(self, doc: EntryDoc, *, source: str = "manual") -> NoteEntry:
        return NoteEntry.from_doc(doc, device_id=self.device_id, source=source)

    def _next_entry_sort_order(self, conn: sqlite3.Connection, notebook_id: str) -> int:
        row = conn.execute(
            "SELECT COALESCE(MAX(region_order), -1) AS value FROM entries WHERE notebook_id = ? AND placement = ?",
            (notebook_id, "normal"),
        ).fetchone()
        return int(row["value"] if row is not None else 0) + 1

    def create_notebook(
        self,
        title: str,
        *,
        record_outbox: bool = True,
        notebook_id: str | None = None,
        device_id: str | None = None,
        last_modified_by: str | None = None,
    ) -> Notebook:
        now = _utc_now()
        doc = NotebookDoc(
            id=str(notebook_id or uuid.uuid4().hex),
            title=str(title or "").strip() or "untitled notebook",
            created_at=now,
            updated_at=now,
            version=1,
            device_id=str(device_id or self.device_id),
            last_modified_by=str(last_modified_by or "desktop"),
        )
        with self._connect() as conn:
            self._insert_notebook_doc(conn, doc)
            if record_outbox:
                self._record_compat_op(
                    conn,
                    entity_type="notebook",
                    entity_id=doc.id,
                    action="create",
                    payload=Notebook.from_doc(doc).to_dict(),
                    base_version=doc.version,
                )
        return Notebook.from_doc(doc, device_id=doc.device_id, last_modified_by=doc.last_modified_by)

    def get_notebook(self, notebook_id: str, include_deleted: bool = False) -> Notebook | None:
        sql = "SELECT * FROM notebooks WHERE id = ?"
        if not include_deleted:
            sql += " AND deleted = 0"
        with self._connect() as conn:
            row = conn.execute(sql, (notebook_id,)).fetchone()
        if row is None:
            return None
        return self._project_notebook(NotebookDoc.from_row(dict(row)))

    def list_notebooks(self, include_deleted: bool = False) -> list[Notebook]:
        sql = "SELECT * FROM notebooks"
        if not include_deleted:
            sql += " WHERE deleted = 0"
        sql += " ORDER BY updated_at DESC, created_at DESC, id ASC"
        with self._connect() as conn:
            rows = conn.execute(sql).fetchall()
        return [self._project_notebook(NotebookDoc.from_row(dict(row))) for row in rows]

    def search_notebooks(self, query: str, include_deleted: bool = False) -> list[Notebook]:
        sql = "SELECT * FROM notebooks WHERE title LIKE ?"
        params: list[object] = [f"%{str(query or '').strip()}%"]
        if not include_deleted:
            sql += " AND deleted = 0"
        sql += " ORDER BY updated_at DESC, created_at DESC, id ASC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._project_notebook(NotebookDoc.from_row(dict(row))) for row in rows]

    def update_notebook(
        self,
        notebook_id: str,
        title: str | None = None,
        *,
        pinned: bool | None = None,
        sort_order: int | None = None,
        record_outbox: bool = True,
    ) -> Notebook:
        current = self.get_notebook(notebook_id, include_deleted=True)
        if current is None:
            raise KeyError(notebook_id)
        updated_at = _utc_now()
        next_title = str(title if title is not None else current.title)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE notebooks
                SET title = ?, updated_at = ?, version = ?, device_id = ?,
                    last_modified_by = ?, dirty = 1
                WHERE id = ?
                """,
                (next_title, updated_at, current.version + 1, self.device_id, "desktop", notebook_id),
            )
            row = conn.execute("SELECT * FROM notebooks WHERE id = ?", (notebook_id,)).fetchone()
            assert row is not None
            updated_doc = NotebookDoc.from_row(dict(row))
            if record_outbox:
                self._record_compat_op(
                    conn,
                    entity_type="notebook",
                    entity_id=notebook_id,
                    action="update",
                    payload=Notebook.from_doc(updated_doc).to_dict(),
                    base_version=current.version,
                )
        return self._project_notebook(updated_doc)

    def rename_notebook(self, notebook_id: str, title: str, *, record_outbox: bool = True) -> Notebook:
        return self.update_notebook(notebook_id, title, record_outbox=record_outbox)

    def pin_notebook(self, notebook_id: str, pinned: bool | None = None, *, record_outbox: bool = True) -> Notebook:
        notebook = self.get_notebook(notebook_id, include_deleted=True)
        if notebook is None:
            raise KeyError(notebook_id)
        return notebook

    def move_notebook_to_bottom(self, notebook_id: str, *, record_outbox: bool = True) -> Notebook:
        notebook = self.get_notebook(notebook_id, include_deleted=True)
        if notebook is None:
            raise KeyError(notebook_id)
        return notebook

    def create_entry(
        self,
        notebook_id: str,
        content: str,
        source: str = "manual",
        *,
        record_outbox: bool = True,
        entry_id: str | None = None,
        device_id: str | None = None,
        last_modified_by: str | None = None,
        created_at: str | None = None,
        updated_at: str | None = None,
        pinned: bool | None = None,
        sort_order: int | None = None,
        version: int | None = None,
        is_conflict_copy: bool | None = None,
        origin_entry_id: str | None = None,
    ) -> NoteEntry:
        if self.get_notebook(notebook_id) is None:
            raise KeyError(notebook_id)
        now = _utc_now()
        with self._connect() as conn:
            target_placement = "top" if pinned else "normal"
            if sort_order is None:
                order_row = conn.execute("SELECT COALESCE(MAX(region_order), -1) AS value FROM entries WHERE notebook_id=? AND placement=?", (notebook_id, target_placement)).fetchone()
                initial_order = int(order_row["value"]) + 1
            else:
                initial_order = int(sort_order)
            doc = EntryDoc(
                id=str(entry_id or uuid.uuid4().hex),
                notebook_id=notebook_id,
                content=str(content or ""),
                created_at=str(created_at or now),
                updated_at=str(updated_at or created_at or now),
                sort_order=initial_order,
                pinned=bool(pinned) if pinned is not None else False,
                placement=target_placement,
                region_order=initial_order,
                version=int(version) if version is not None else 1,
                device_id=str(device_id or self.device_id),
                last_modified_by=str(last_modified_by or "desktop"),
                is_conflict_copy=bool(is_conflict_copy) if is_conflict_copy is not None else False,
                origin_entry_id=str(origin_entry_id) if origin_entry_id else None,
                source=str(source or "manual"),
            )
            self._insert_entry_doc(conn, doc)
            if record_outbox:
                self._record_compat_op(
                    conn,
                    entity_type="entry",
                    entity_id=doc.id,
                    action="create",
                    payload=NoteEntry.from_doc(doc).to_dict(),
                    base_version=doc.version,
                )
        return NoteEntry.from_doc(doc, device_id=doc.device_id, last_modified_by=doc.last_modified_by, source=doc.source)

    def get_entry(self, entry_id: str, include_deleted: bool = False) -> NoteEntry | None:
        sql = "SELECT * FROM entries WHERE id = ?"
        if not include_deleted:
            sql += " AND deleted = 0"
        with self._connect() as conn:
            row = conn.execute(sql, (entry_id,)).fetchone()
        if row is None:
            return None
        return self._project_entry(EntryDoc.from_row(dict(row)))

    def list_entries(self, notebook_id: str, include_deleted: bool = False) -> list[NoteEntry]:
        sql = "SELECT * FROM entries WHERE notebook_id = ?"
        if not include_deleted:
            sql += " AND deleted = 0"
        sql += " ORDER BY CASE placement WHEN 'top' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, region_order ASC, id ASC"
        with self._connect() as conn:
            rows = conn.execute(sql, (notebook_id,)).fetchall()
        return [self._project_entry(EntryDoc.from_row(dict(row))) for row in rows]

    def list_all_entries(self, include_deleted: bool = False) -> list[NoteEntry]:
        sql = "SELECT * FROM entries"
        if not include_deleted:
            sql += " WHERE deleted = 0"
        sql += " ORDER BY notebook_id ASC, CASE placement WHEN 'top' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, region_order ASC, id ASC"
        with self._connect() as conn:
            rows = conn.execute(sql).fetchall()
        return [self._project_entry(EntryDoc.from_row(dict(row))) for row in rows]

    def search_entries(self, notebook_id: str, query: str, include_deleted: bool = False) -> list[NoteEntry]:
        sql = "SELECT * FROM entries WHERE notebook_id = ? AND content LIKE ?"
        params: list[object] = [notebook_id, f"%{str(query or '').strip()}%"]
        if not include_deleted:
            sql += " AND deleted = 0"
        sql += " ORDER BY CASE placement WHEN 'top' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, region_order ASC, id ASC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._project_entry(EntryDoc.from_row(dict(row))) for row in rows]

    def update_entry(self, entry_id: str, content: str, *, source: str | None = None, record_outbox: bool = True) -> NoteEntry:
        current = self.get_entry(entry_id, include_deleted=True)
        if current is None:
            raise KeyError(entry_id)
        updated_at = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE entries
                SET content = ?, updated_at = ?, version = ?, device_id = ?,
                    last_modified_by = ?, source = ?, dirty = 1
                WHERE id = ?
                """,
                (
                    str(content or ""),
                    updated_at,
                    current.version + 1,
                    self.device_id,
                    "desktop",
                    str(source or current.source or "manual"),
                    entry_id,
                ),
            )
            row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
            assert row is not None
            updated_doc = EntryDoc.from_row(dict(row))
            if record_outbox:
                self._record_compat_op(
                    conn,
                    entity_type="entry",
                    entity_id=entry_id,
                    action="update",
                    payload=NoteEntry.from_doc(updated_doc).to_dict(),
                    base_version=current.version,
                )
        return self._project_entry(updated_doc, source=updated_doc.source)

    def import_entries(self, notebook_id: str, lines: Iterable[str], source: str) -> list[NoteEntry]:
        if self.get_notebook(notebook_id) is None:
            raise KeyError(notebook_id)
        created: list[NoteEntry] = []
        with self._connect() as conn:
            next_sort_order = self._next_entry_sort_order(conn, notebook_id)
            for line in lines:
                text = str(line or "").strip()
                if not text:
                    continue
                now = _utc_now()
                doc = EntryDoc(
                    id=uuid.uuid4().hex,
                    notebook_id=notebook_id,
                    content=text,
                    created_at=now,
                    updated_at=now,
                    sort_order=next_sort_order,
                    pinned=False,
                    placement="normal",
                    region_order=next_sort_order,
                    version=1,
                    device_id=self.device_id,
                    last_modified_by="desktop",
                    source=str(source or "manual"),
                )
                next_sort_order += 1
                self._insert_entry_doc(conn, doc)
                self._record_compat_op(
                    conn,
                    entity_type="entry",
                    entity_id=doc.id,
                    action="create",
                    payload=NoteEntry.from_doc(doc).to_dict(),
                    base_version=doc.version,
                )
                created.append(self._project_entry(doc, source=doc.source))
        return created

    def pin_entry(self, entry_id: str, pinned: bool | None = None, *, record_outbox: bool = True) -> NoteEntry:
        current = self.get_entry(entry_id, include_deleted=True)
        if current is None:
            raise KeyError(entry_id)
        target = "top" if pinned is None or bool(pinned) else "normal"
        return self.place_entry(entry_id, target, expected_version=current.version, expected_rev=current.rev, record_outbox=record_outbox)

    def move_entry_to_bottom(self, entry_id: str, *, record_outbox: bool = True) -> NoteEntry:
        current = self.get_entry(entry_id, include_deleted=True)
        if current is None:
            raise KeyError(entry_id)
        return self.place_entry(entry_id, "bottom", expected_version=current.version, expected_rev=current.rev, record_outbox=record_outbox)

    def move_entry_to_top(self, entry_id: str, *, record_outbox: bool = True) -> NoteEntry:
        return self.pin_entry(entry_id, True, record_outbox=record_outbox)

    def move_entry_to_unpinned_top(self, entry_id: str, *, record_outbox: bool = True) -> NoteEntry:
        current = self.get_entry(entry_id, include_deleted=True)
        if current is None:
            raise KeyError(entry_id)
        return self.place_entry(entry_id, "normal", expected_version=current.version, expected_rev=current.rev, record_outbox=record_outbox)

    def place_entry(self, entry_id: str, placement: str, *, expected_version: int, expected_rev: str = "", record_outbox: bool = True) -> NoteEntry:
        target = str(placement or "").lower()
        if target not in {"top", "normal", "bottom"}:
            raise ValueError(placement)
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM entries WHERE id = ? AND deleted = 0", (entry_id,)).fetchone()
            if row is None:
                raise KeyError(entry_id)
            current = EntryDoc.from_row(dict(row))
            if current.version != int(expected_version) or (expected_rev and current.rev != str(expected_rev)):
                raise NotesPlacementConflict(entry_id)
            if current.placement == target:
                return self._project_entry(current)
            rows = conn.execute("SELECT * FROM entries WHERE notebook_id=? AND deleted=0 ORDER BY CASE placement WHEN 'top' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, region_order, id", (current.notebook_id,)).fetchall()
            docs = [EntryDoc.from_row(dict(item)) for item in rows]
            regions = {name: [doc for doc in docs if doc.placement == name and doc.id != entry_id] for name in ("top", "normal", "bottom")}
            pred, succ = current.normal_predecessor_id, current.normal_successor_id
            if current.placement == "normal" and target != "normal":
                normals = [doc for doc in docs if doc.placement == "normal"]
                idx = next(i for i, doc in enumerate(normals) if doc.id == entry_id)
                pred = normals[idx - 1].id if idx else None
                succ = normals[idx + 1].id if idx + 1 < len(normals) else None
            if target == "normal":
                normals = regions["normal"]
                ids = [doc.id for doc in normals]
                if pred in ids:
                    insert_at = ids.index(pred) + 1
                elif succ in ids:
                    insert_at = ids.index(succ)
                else:
                    insert_at = len(normals)
                normals.insert(insert_at, current)
            else:
                regions[target].append(current)
            now = _utc_now()
            changed: list[tuple[EntryDoc, str, int]] = []
            for region in ("top", "normal", "bottom"):
                for order, doc in enumerate(regions[region]):
                    if doc.id == entry_id or doc.region_order != order:
                        changed.append((doc, region, order))
            for doc, region, order in changed:
                cursor = conn.execute("UPDATE entries SET placement=?,region_order=?,sort_order=?,pinned=?,normal_predecessor_id=?,normal_successor_id=?,updated_at=?,version=version+1,device_id=?,last_modified_by='desktop',dirty=1 WHERE id=? AND version=?", (region, order, order, int(region == "top"), pred if doc.id == entry_id else doc.normal_predecessor_id, succ if doc.id == entry_id else doc.normal_successor_id, now, self.device_id, doc.id, doc.version))
                if cursor.rowcount != 1:
                    raise NotesPlacementConflict(doc.id)
                updated = EntryDoc.from_row(dict(conn.execute("SELECT * FROM entries WHERE id=?", (doc.id,)).fetchone()))
                if record_outbox:
                    self._record_compat_op(conn, entity_type="entry", entity_id=doc.id, action="update", payload=NoteEntry.from_doc(updated).to_dict(), base_version=doc.version)
            result = conn.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
            return self._project_entry(EntryDoc.from_row(dict(result)))

    def move_entry_up(self, entry_id: str, *, record_outbox: bool = True) -> NoteEntry:
        return self._move_entry_by_delta(entry_id, -1, record_outbox=record_outbox)

    def move_entry_down(self, entry_id: str, *, record_outbox: bool = True) -> NoteEntry:
        return self._move_entry_by_delta(entry_id, 1, record_outbox=record_outbox)

    def _move_entry_by_delta(self, entry_id: str, delta: int, *, record_outbox: bool = True) -> NoteEntry:
        with self._connect() as conn:
            selected_row = conn.execute("SELECT * FROM entries WHERE id=? AND deleted=0", (entry_id,)).fetchone()
            if selected_row is None:
                raise KeyError(entry_id)
            current_doc = EntryDoc.from_row(dict(selected_row))
            rows = conn.execute("SELECT * FROM entries WHERE notebook_id=? AND placement=? AND deleted=0 ORDER BY region_order,id", (current_doc.notebook_id, current_doc.placement)).fetchall()
            docs = [EntryDoc.from_row(dict(row)) for row in rows]
            old_idx = next((i for i, doc in enumerate(docs) if doc.id == entry_id), -1)
            if old_idx < 0:
                raise KeyError(entry_id)
            new_idx = max(0, min(old_idx + int(delta), len(docs) - 1))
            if new_idx == old_idx:
                return self._project_entry(current_doc)
            neighbor_doc = docs[new_idx]
            now = _utc_now()
            first = conn.execute(
                """
                UPDATE entries
                SET sort_order = ?, region_order = ?, pinned = ?, placement = ?, updated_at = ?, version = version + 1,
                    device_id = ?, last_modified_by = ?, dirty = 1
                WHERE id = ? AND version = ? AND rev = ?
                """,
                (neighbor_doc.region_order, neighbor_doc.region_order, int(current_doc.placement == "top"), current_doc.placement, now, self.device_id, "desktop", current_doc.id, current_doc.version, current_doc.rev),
            )
            second = conn.execute(
                """
                UPDATE entries
                SET sort_order = ?, region_order = ?, pinned = ?, placement = ?, updated_at = ?, version = version + 1,
                    device_id = ?, last_modified_by = ?, dirty = 1
                WHERE id = ? AND version = ? AND rev = ?
                """,
                (current_doc.region_order, current_doc.region_order, int(neighbor_doc.placement == "top"), neighbor_doc.placement, now, self.device_id, "desktop", neighbor_doc.id, neighbor_doc.version, neighbor_doc.rev),
            )
            if first.rowcount != 1 or second.rowcount != 1:
                raise NotesPlacementConflict(entry_id)
            row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
            assert row is not None
            updated_doc = EntryDoc.from_row(dict(row))
            if record_outbox:
                self._record_compat_op(
                    conn,
                    entity_type="entry",
                    entity_id=entry_id,
                    action="update",
                    payload=NoteEntry.from_doc(updated_doc).to_dict(),
                    base_version=current_doc.version,
                )
                neighbor_updated = EntryDoc.from_row(dict(conn.execute("SELECT * FROM entries WHERE id=?", (neighbor_doc.id,)).fetchone()))
                self._record_compat_op(conn, entity_type="entry", entity_id=neighbor_doc.id, action="update", payload=NoteEntry.from_doc(neighbor_updated).to_dict(), base_version=neighbor_doc.version)
        return self._project_entry(updated_doc, source=updated_doc.source)

    def _set_entry_sort_order(self, entry_id: str, sort_order: int, *, pinned: bool | None = None, record_outbox: bool = True) -> NoteEntry:
        return self._update_entry_position(entry_id, sort_order=sort_order, pinned=pinned, record_outbox=record_outbox)

    def _update_entry_position(self, entry_id: str, *, sort_order: int, pinned: bool | None = None, record_outbox: bool = True) -> NoteEntry:
        current = self.get_entry(entry_id, include_deleted=True)
        if current is None:
            raise KeyError(entry_id)
        next_pinned = current.pinned if pinned is None else bool(pinned)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE entries
                SET sort_order = ?, pinned = ?, updated_at = ?, version = ?, device_id = ?,
                    last_modified_by = ?, dirty = 1
                WHERE id = ?
                """,
                (int(sort_order), int(next_pinned), _utc_now(), current.version + 1, self.device_id, "desktop", entry_id),
            )
            row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
            assert row is not None
            updated_doc = EntryDoc.from_row(dict(row))
            if record_outbox:
                self._record_compat_op(
                    conn,
                    entity_type="entry",
                    entity_id=entry_id,
                    action="update",
                    payload=NoteEntry.from_doc(updated_doc).to_dict(),
                    base_version=current.version,
                )
        return self._project_entry(updated_doc, source=updated_doc.source)

    def delete_entry(self, entry_id: str, *, record_outbox: bool = True) -> None:
        current = self.get_entry(entry_id, include_deleted=True)
        if current is None:
            raise KeyError(entry_id)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE entries
                SET deleted = 1, updated_at = ?, version = ?, device_id = ?,
                    last_modified_by = ?, dirty = 1
                WHERE id = ?
                """,
                (_utc_now(), current.version + 1, self.device_id, "desktop", entry_id),
            )
            row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
            assert row is not None
            deleted_doc = EntryDoc.from_row(dict(row))
            if record_outbox:
                self._record_compat_op(
                    conn,
                    entity_type="entry",
                    entity_id=entry_id,
                    action="delete",
                    payload=NoteEntry.from_doc(deleted_doc).to_dict(),
                    base_version=current.version,
                )

    def purge_entry(self, entry_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))

    def delete_notebook(self, notebook_id: str, *, record_outbox: bool = True) -> None:
        current = self.get_notebook(notebook_id, include_deleted=True)
        if current is None:
            raise KeyError(notebook_id)
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE notebooks
                SET deleted = 1, updated_at = ?, version = ?, device_id = ?,
                    last_modified_by = ?, dirty = 1
                WHERE id = ?
                """,
                (now, current.version + 1, self.device_id, "desktop", notebook_id),
            )
            conn.execute(
                """
                UPDATE entries
                SET deleted = 1, updated_at = ?, version = version + 1,
                    device_id = ?, last_modified_by = ?, dirty = 1
                WHERE notebook_id = ?
                """,
                (now, self.device_id, "desktop", notebook_id),
            )
            row = conn.execute("SELECT * FROM notebooks WHERE id = ?", (notebook_id,)).fetchone()
            assert row is not None
            deleted_doc = NotebookDoc.from_row(dict(row))
            if record_outbox:
                self._record_compat_op(
                    conn,
                    entity_type="notebook",
                    entity_id=notebook_id,
                    action="delete",
                    payload=Notebook.from_doc(deleted_doc).to_dict(),
                    base_version=current.version,
                )

    def snapshot(self) -> dict:
        return {
            "cursor": self.current_cursor(),
            "notebooks": [item.to_dict() for item in self.list_notebooks(include_deleted=True)],
            "entries": [item.to_dict() for item in self.list_all_entries(include_deleted=True)],
        }

    def current_cursor(self) -> str:
        return self.sync_state_value(LAST_CURSOR_STATE_KEY) or "0"

    def list_ops_since(self, cursor: str) -> tuple[list[dict], str]:
        return [], self.current_cursor()

    def list_pending_ops(self, limit: int = 100) -> list[SyncOp]:
        return self.list_outbox_ops(statuses=("pending",), limit=limit)

    def list_outbox_ops(self, statuses: Iterable[str] | None = None, limit: int = 100) -> list[SyncOp]:
        allowed = {str(item) for item in statuses} if statuses is not None else None
        with self._connect() as conn:
            ops = self._load_compat_outbox(conn)
        filtered: list[SyncOp] = []
        for item in ops:
            if allowed is not None and str(item.get("status") or "") not in allowed:
                continue
            filtered.append(self._sync_op_from_dict(item))
            if len(filtered) >= limit:
                break
        return filtered

    def claim_outbox_ops(self, limit: int = 100) -> list[SyncOp]:
        claimed: list[SyncOp] = []
        with self._connect() as conn:
            ops = self._load_compat_outbox(conn)
            for item in ops:
                if str(item.get("status") or "") not in {"pending", "failed"}:
                    continue
                item["status"] = "sending"
                item["retry_count"] = int(item.get("retry_count") or 0) + 1
                claimed.append(self._sync_op_from_dict(item))
                if len(claimed) >= limit:
                    break
            self._replace_compat_ops(conn, ops)
        return claimed

    def mark_outbox_acked(self, op_ids: Iterable[str]) -> list[SyncOp]:
        target_ids = {str(item) for item in op_ids}
        acked: list[SyncOp] = []
        if not target_ids:
            return acked
        with self._connect() as conn:
            ops = self._load_compat_outbox(conn)
            for item in ops:
                if str(item.get("op_id") or "") not in target_ids:
                    continue
                item["status"] = "acked"
                acked.append(self._sync_op_from_dict(item))
            self._replace_compat_ops(conn, ops)
        return acked

    def mark_outbox_failed(self, op_ids: Iterable[str]) -> list[SyncOp]:
        target_ids = {str(item) for item in op_ids}
        failed: list[SyncOp] = []
        if not target_ids:
            return failed
        with self._connect() as conn:
            ops = self._load_compat_outbox(conn)
            for item in ops:
                if str(item.get("op_id") or "") not in target_ids:
                    continue
                item["status"] = "failed"
                failed.append(self._sync_op_from_dict(item))
            self._replace_compat_ops(conn, ops)
        return failed

    def push_ops(self, ops: Iterable[dict]) -> dict:
        return {"cursor": self.current_cursor(), "applied": [], "conflicts": [], "acked": []}

    def apply_remote_op(self, op: dict) -> dict:
        return {"applied": False, "conflicts": [], "cursor": self.current_cursor()}
