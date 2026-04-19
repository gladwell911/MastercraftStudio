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
        if "entries" in tables and self._table_columns(conn, "entries") != ENTRY_COLUMNS:
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
            CREATE INDEX IF NOT EXISTS idx_entries_notebook_sort
            ON entries (notebook_id, sort_order, created_at);
            """
        )

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
                sort_order, version, device_id, last_modified_by,
                is_conflict_copy, origin_entry_id, source,
                rev, deleted, dirty
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                doc.id,
                doc.notebook_id,
                doc.content,
                doc.created_at,
                doc.updated_at,
                doc.sort_order,
                doc.version,
                doc.device_id,
                doc.last_modified_by,
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
                ORDER BY notebook_id ASC, sort_order ASC, created_at ASC, id ASC
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
            "SELECT COALESCE(MAX(sort_order), 0) AS value FROM entries WHERE notebook_id = ?",
            (notebook_id,),
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
            doc = EntryDoc(
                id=str(entry_id or uuid.uuid4().hex),
                notebook_id=notebook_id,
                content=str(content or ""),
                created_at=str(created_at or now),
                updated_at=str(updated_at or created_at or now),
                sort_order=int(sort_order) if sort_order is not None else self._next_entry_sort_order(conn, notebook_id),
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
        sql += " ORDER BY sort_order ASC, created_at ASC, id ASC"
        with self._connect() as conn:
            rows = conn.execute(sql, (notebook_id,)).fetchall()
        return [self._project_entry(EntryDoc.from_row(dict(row))) for row in rows]

    def list_all_entries(self, include_deleted: bool = False) -> list[NoteEntry]:
        sql = "SELECT * FROM entries"
        if not include_deleted:
            sql += " WHERE deleted = 0"
        sql += " ORDER BY notebook_id ASC, sort_order ASC, created_at ASC, id ASC"
        with self._connect() as conn:
            rows = conn.execute(sql).fetchall()
        return [self._project_entry(EntryDoc.from_row(dict(row))) for row in rows]

    def search_entries(self, notebook_id: str, query: str, include_deleted: bool = False) -> list[NoteEntry]:
        sql = "SELECT * FROM entries WHERE notebook_id = ? AND content LIKE ?"
        params: list[object] = [notebook_id, f"%{str(query or '').strip()}%"]
        if not include_deleted:
            sql += " AND deleted = 0"
        sql += " ORDER BY sort_order ASC, created_at ASC, id ASC"
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
        entry = self.get_entry(entry_id, include_deleted=True)
        if entry is None:
            raise KeyError(entry_id)
        return entry

    def move_entry_to_bottom(self, entry_id: str, *, record_outbox: bool = True) -> NoteEntry:
        current = self.get_entry(entry_id, include_deleted=True)
        if current is None:
            raise KeyError(entry_id)
        with self._connect() as conn:
            row = conn.execute("SELECT notebook_id FROM entries WHERE id = ?", (entry_id,)).fetchone()
            if row is None:
                raise KeyError(entry_id)
            next_sort_order = self._next_entry_sort_order(conn, str(row["notebook_id"]))
            conn.execute(
                """
                UPDATE entries
                SET sort_order = ?, updated_at = ?, version = ?, device_id = ?,
                    last_modified_by = ?, dirty = 1
                WHERE id = ?
                """,
                (next_sort_order, _utc_now(), current.version + 1, self.device_id, "desktop", entry_id),
            )
            updated_row = conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()
            assert updated_row is not None
            updated_doc = EntryDoc.from_row(dict(updated_row))
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

    def _set_entry_sort_order(self, entry_id: str, sort_order: int, *, pinned: bool | None = None, record_outbox: bool = True) -> NoteEntry:
        current = self.get_entry(entry_id, include_deleted=True)
        if current is None:
            raise KeyError(entry_id)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE entries
                SET sort_order = ?, updated_at = ?, version = ?, device_id = ?,
                    last_modified_by = ?, dirty = 1
                WHERE id = ?
                """,
                (int(sort_order), _utc_now(), current.version + 1, self.device_id, "desktop", entry_id),
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
