from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from notes_models import NoteEntry, Notebook, SyncOp


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sort_key() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _semantic_modified_by(device_identity: str) -> str:
    normalized = str(device_identity or "").strip().lower()
    if "mobile" in normalized or "phone" in normalized or "android" in normalized:
        return "mobile"
    return "desktop"


def _semantic_source_label(device_identity: str) -> str:
    normalized = str(device_identity or "").strip().lower()
    if "mobile" in normalized or "phone" in normalized or "android" in normalized:
        return "手机端"
    return "电脑端"


def _semantic_source_label(device_identity: str) -> str:
    normalized = str(device_identity or "").strip().lower()
    if "mobile" in normalized or "phone" in normalized or "android" in normalized:
        return "手机端"
    return "电脑端"


def _semantic_source_label(device_identity: str) -> str:
    normalized = str(device_identity or "").strip().lower()
    if "mobile" in normalized or "phone" in normalized or "android" in normalized:
        return "手机端"
    return "电脑端"


def _semantic_conflict_suffix_label(device_identity: str) -> str:
    normalized = str(device_identity or "").strip().lower()
    if "mobile" in normalized or "phone" in normalized or "android" in normalized:
        return "手机"
    return "电脑"


class NotesStore:
    def __init__(self, db_path: Path, device_id: str) -> None:
        self.db_path = Path(db_path)
        self.device_id = str(device_id or "").strip() or "desktop-local"

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE IF NOT EXISTS notebooks (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted_at TEXT,
                    pinned INTEGER NOT NULL DEFAULT 0,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    version INTEGER NOT NULL DEFAULT 1,
                    device_id TEXT NOT NULL,
                    last_modified_by TEXT NOT NULL,
                    is_conflict_copy INTEGER NOT NULL DEFAULT 0,
                    origin_notebook_id TEXT
                );
                CREATE TABLE IF NOT EXISTS note_entries (
                    id TEXT PRIMARY KEY,
                    notebook_id TEXT NOT NULL REFERENCES notebooks(id),
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted_at TEXT,
                    pinned INTEGER NOT NULL DEFAULT 0,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    version INTEGER NOT NULL DEFAULT 1,
                    device_id TEXT NOT NULL,
                    last_modified_by TEXT NOT NULL,
                    is_conflict_copy INTEGER NOT NULL DEFAULT 0,
                    origin_entry_id TEXT,
                    source TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sync_outbox (
                    op_id TEXT PRIMARY KEY,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    base_version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notes_change_log (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    op_id TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    base_version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    source_device TEXT NOT NULL
                );
                """
            )

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

    def _log_change(self, conn: sqlite3.Connection, entity_type: str, entity_id: str, action: str, payload: dict, base_version: int) -> str:
        op_id = str(payload.get("op_id") or uuid.uuid4().hex)
        conn.execute(
            """
            INSERT INTO notes_change_log (
                op_id, entity_type, entity_id, action, payload_json,
                base_version, created_at, source_device
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                op_id,
                entity_type,
                entity_id,
                action,
                json.dumps(payload, ensure_ascii=False),
                int(base_version),
                str(payload.get("created_at") or _utc_now()),
                self.device_id,
            ),
        )
        return op_id

    def _enqueue_outbox(self, conn: sqlite3.Connection, entity_type: str, entity_id: str, action: str, payload: dict, base_version: int) -> str:
        op_id = str(payload.get("op_id") or uuid.uuid4().hex)
        conn.execute(
            """
            INSERT INTO sync_outbox (
                op_id, entity_type, entity_id, action, payload_json,
                base_version, created_at, retry_count, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                op_id,
                entity_type,
                entity_id,
                action,
                json.dumps(payload, ensure_ascii=False),
                int(base_version),
                str(payload.get("created_at") or _utc_now()),
                0,
                "pending",
            ),
        )
        return op_id

    def _record_local_change(self, conn: sqlite3.Connection, entity_type: str, entity_id: str, action: str, payload: dict, base_version: int) -> None:
        self._log_change(conn, entity_type, entity_id, action, payload, base_version)
        self._enqueue_outbox(conn, entity_type, entity_id, action, payload, base_version)

    def _row_to_dict(self, row: sqlite3.Row | None) -> dict | None:
        return dict(row) if row is not None else None

    def _write_notebook_record(self, conn: sqlite3.Connection, notebook: Notebook, *, record_outbox: bool = True) -> None:
        conn.execute(
            """
            INSERT INTO notebooks (
                id, title, created_at, updated_at, deleted_at, pinned,
                sort_order, version, device_id, last_modified_by,
                is_conflict_copy, origin_notebook_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                notebook.id,
                notebook.title,
                notebook.created_at,
                notebook.updated_at,
                notebook.deleted_at,
                int(notebook.pinned),
                notebook.sort_order,
                notebook.version,
                notebook.device_id,
                notebook.last_modified_by,
                int(notebook.is_conflict_copy),
                notebook.origin_notebook_id,
            ),
        )
        if record_outbox:
            self._record_local_change(conn, "notebook", notebook.id, "create", notebook.to_dict(), notebook.version)
        else:
            self._log_change(conn, "notebook", notebook.id, "create", notebook.to_dict(), notebook.version)

    def _write_entry_record(self, conn: sqlite3.Connection, entry: NoteEntry, *, record_outbox: bool = True) -> None:
        conn.execute(
            """
            INSERT INTO note_entries (
                id, notebook_id, content, created_at, updated_at, deleted_at,
                pinned, sort_order, version, device_id, last_modified_by,
                is_conflict_copy, origin_entry_id, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.id,
                entry.notebook_id,
                entry.content,
                entry.created_at,
                entry.updated_at,
                entry.deleted_at,
                int(entry.pinned),
                entry.sort_order,
                entry.version,
                entry.device_id,
                entry.last_modified_by,
                int(entry.is_conflict_copy),
                entry.origin_entry_id,
                entry.source,
            ),
        )
        if record_outbox:
            self._record_local_change(conn, "entry", entry.id, "create", entry.to_dict(), entry.version)
        else:
            self._log_change(conn, "entry", entry.id, "create", entry.to_dict(), entry.version)

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
        notebook = Notebook(
            id=str(notebook_id or uuid.uuid4().hex),
            title=str(title or "").strip() or "未命名笔记",
            created_at=now,
            updated_at=now,
            deleted_at=None,
            pinned=False,
            sort_order=_sort_key(),
            version=1,
            device_id=str(device_id or self.device_id),
            last_modified_by=str(last_modified_by or "desktop"),
            is_conflict_copy=False,
            origin_notebook_id=None,
        )
        with self._connect() as conn:
            self._write_notebook_record(conn, notebook, record_outbox=record_outbox)
        return notebook

    def get_notebook(self, notebook_id: str, include_deleted: bool = False) -> Notebook | None:
        query = "SELECT * FROM notebooks WHERE id = ?"
        if not include_deleted:
            query += " AND deleted_at IS NULL"
        with self._connect() as conn:
            row = conn.execute(query, (notebook_id,)).fetchone()
        row_dict = self._row_to_dict(row)
        return Notebook.from_row(row_dict) if row_dict else None

    def list_notebooks(self, include_deleted: bool = False) -> list[Notebook]:
        query = "SELECT * FROM notebooks"
        if not include_deleted:
            query += " WHERE deleted_at IS NULL"
        query += " ORDER BY pinned DESC, sort_order DESC, updated_at DESC, created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query).fetchall()
        return [Notebook.from_row(dict(row)) for row in rows]

    def get_entry(self, entry_id: str, include_deleted: bool = False) -> NoteEntry | None:
        query = "SELECT * FROM note_entries WHERE id = ?"
        if not include_deleted:
            query += " AND deleted_at IS NULL"
        with self._connect() as conn:
            row = conn.execute(query, (entry_id,)).fetchone()
        row_dict = self._row_to_dict(row)
        return NoteEntry.from_row(row_dict) if row_dict else None

    def list_entries(self, notebook_id: str, include_deleted: bool = False) -> list[NoteEntry]:
        query = "SELECT * FROM note_entries WHERE notebook_id = ?"
        if not include_deleted:
            query += " AND deleted_at IS NULL"
        query += " ORDER BY pinned DESC, sort_order DESC, updated_at DESC, created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query, (notebook_id,)).fetchall()
        return [NoteEntry.from_row(dict(row)) for row in rows]

    def list_all_entries(self, include_deleted: bool = False) -> list[NoteEntry]:
        query = "SELECT * FROM note_entries"
        if not include_deleted:
            query += " WHERE deleted_at IS NULL"
        query += " ORDER BY updated_at DESC, created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query).fetchall()
        return [NoteEntry.from_row(dict(row)) for row in rows]

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
        created_value = str(created_at or now)
        updated_value = str(updated_at or created_value)
        entry = NoteEntry(
            id=str(entry_id or uuid.uuid4().hex),
            notebook_id=notebook_id,
            content=str(content or ""),
            created_at=created_value,
            updated_at=updated_value,
            deleted_at=None,
            pinned=bool(pinned) if pinned is not None else False,
            sort_order=int(sort_order) if sort_order is not None else _sort_key(),
            version=int(version) if version is not None else 1,
            device_id=str(device_id or self.device_id),
            last_modified_by=str(last_modified_by or "desktop"),
            is_conflict_copy=bool(is_conflict_copy) if is_conflict_copy is not None else False,
            source=str(source or "manual"),
            origin_entry_id=str(origin_entry_id) if origin_entry_id else None,
        )
        with self._connect() as conn:
            self._write_entry_record(conn, entry, record_outbox=record_outbox)
        return entry

    def update_notebook(
        self,
        notebook_id: str,
        title: str | None = None,
        *,
        pinned: bool | None = None,
        sort_order: int | None = None,
        record_outbox: bool = True,
    ) -> Notebook:
        notebook = self.get_notebook(notebook_id, include_deleted=True)
        if notebook is None:
            raise KeyError(notebook_id)
        now = _utc_now()
        updated = Notebook(
            id=notebook.id,
            title=str(title if title is not None else notebook.title),
            created_at=notebook.created_at,
            updated_at=now,
            deleted_at=notebook.deleted_at,
            pinned=bool(notebook.pinned if pinned is None else pinned),
            sort_order=int(sort_order) if sort_order is not None else notebook.sort_order,
            version=notebook.version + 1,
            device_id=self.device_id,
            last_modified_by="desktop",
            is_conflict_copy=notebook.is_conflict_copy,
            origin_notebook_id=notebook.origin_notebook_id,
        )
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE notebooks
                SET title = ?, updated_at = ?, pinned = ?, sort_order = ?, version = ?,
                    device_id = ?, last_modified_by = ?, is_conflict_copy = ?, origin_notebook_id = ?
                WHERE id = ?
                """,
                (
                    updated.title,
                    updated.updated_at,
                    int(updated.pinned),
                    updated.sort_order,
                    updated.version,
                    updated.device_id,
                    updated.last_modified_by,
                    int(updated.is_conflict_copy),
                    updated.origin_notebook_id,
                    notebook.id,
                ),
            )
            payload = updated.to_dict()
            if record_outbox:
                self._record_local_change(conn, "notebook", notebook.id, "update", payload, notebook.version)
            else:
                self._log_change(conn, "notebook", notebook.id, "update", payload, notebook.version)
        return updated

    def rename_notebook(self, notebook_id: str, title: str, *, record_outbox: bool = True) -> Notebook:
        return self.update_notebook(notebook_id, title, record_outbox=record_outbox)

    def pin_notebook(self, notebook_id: str, pinned: bool | None = None, *, record_outbox: bool = True) -> Notebook:
        notebook = self.get_notebook(notebook_id, include_deleted=True)
        if notebook is None:
            raise KeyError(notebook_id)
        return self.update_notebook(
            notebook_id,
            notebook.title,
            pinned=(not notebook.pinned) if pinned is None else bool(pinned),
            record_outbox=record_outbox,
        )

    def move_notebook_to_bottom(self, notebook_id: str, *, record_outbox: bool = True) -> Notebook:
        notebook = self.get_notebook(notebook_id, include_deleted=True)
        if notebook is None:
            raise KeyError(notebook_id)
        with self._connect() as conn:
            row = conn.execute("SELECT COALESCE(MIN(sort_order), 0) AS min_sort_order FROM notebooks").fetchone()
            min_sort_order = int(row["min_sort_order"] if row is not None else 0)
        return self.update_notebook(
            notebook_id,
            notebook.title,
            pinned=False,
            sort_order=min_sort_order - 1,
            record_outbox=record_outbox,
        )

    def search_notebooks(self, query: str, include_deleted: bool = False) -> list[Notebook]:
        pattern = f"%{str(query or '').strip()}%"
        sql = "SELECT * FROM notebooks WHERE title LIKE ?"
        params: list[object] = [pattern]
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        sql += " ORDER BY pinned DESC, sort_order DESC, updated_at DESC, created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [Notebook.from_row(dict(row)) for row in rows]

    def import_entries(self, notebook_id: str, lines: Iterable[str], source: str) -> list[NoteEntry]:
        created: list[NoteEntry] = []
        with self._connect() as conn:
            if conn.execute("SELECT 1 FROM notebooks WHERE id = ? AND deleted_at IS NULL", (notebook_id,)).fetchone() is None:
                raise KeyError(notebook_id)
            for line in lines:
                text = str(line or "").strip()
                if not text:
                    continue
                now = _utc_now()
                entry = NoteEntry(
                    id=uuid.uuid4().hex,
                    notebook_id=notebook_id,
                    content=text,
                    created_at=now,
                    updated_at=now,
                    deleted_at=None,
                    pinned=False,
                    sort_order=_sort_key(),
                    version=1,
                    device_id=self.device_id,
                    last_modified_by="desktop",
                    is_conflict_copy=False,
                    source=str(source or "manual"),
                    origin_entry_id=None,
                )
                self._write_entry_record(conn, entry, record_outbox=True)
                created.append(entry)
        return created

    def pin_entry(self, entry_id: str, pinned: bool | None = None, *, record_outbox: bool = True) -> NoteEntry:
        entry = self.get_entry(entry_id, include_deleted=True)
        if entry is None:
            raise KeyError(entry_id)
        now = _utc_now()
        updated = NoteEntry(
            id=entry.id,
            notebook_id=entry.notebook_id,
            content=entry.content,
            created_at=entry.created_at,
            updated_at=now,
            deleted_at=entry.deleted_at,
            pinned=(not entry.pinned) if pinned is None else bool(pinned),
            sort_order=entry.sort_order,
            version=entry.version + 1,
            device_id=self.device_id,
            last_modified_by="desktop",
            is_conflict_copy=entry.is_conflict_copy,
            source=entry.source,
            origin_entry_id=entry.origin_entry_id,
        )
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE note_entries
                SET content = ?, updated_at = ?, deleted_at = ?, pinned = ?, sort_order = ?,
                    version = ?, device_id = ?, last_modified_by = ?, is_conflict_copy = ?,
                    origin_entry_id = ?, source = ?
                WHERE id = ?
                """,
                (
                    updated.content,
                    updated.updated_at,
                    updated.deleted_at,
                    int(updated.pinned),
                    updated.sort_order,
                    updated.version,
                    updated.device_id,
                    updated.last_modified_by,
                    int(updated.is_conflict_copy),
                    updated.origin_entry_id,
                    updated.source,
                    entry.id,
                ),
            )
            payload = updated.to_dict()
            if record_outbox:
                self._record_local_change(conn, "entry", entry.id, "update", payload, entry.version)
            else:
                self._log_change(conn, "entry", entry.id, "update", payload, entry.version)
        return updated

    def move_entry_to_bottom(self, entry_id: str, *, record_outbox: bool = True) -> NoteEntry:
        entry = self.get_entry(entry_id, include_deleted=True)
        if entry is None:
            raise KeyError(entry_id)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MIN(sort_order), 0) AS min_sort_order FROM note_entries WHERE notebook_id = ?",
                (entry.notebook_id,),
            ).fetchone()
            min_sort_order = int(row["min_sort_order"] if row is not None else 0)
        return self._set_entry_sort_order(entry_id, min_sort_order - 1, pinned=False, record_outbox=record_outbox)

    def _set_entry_sort_order(self, entry_id: str, sort_order: int, *, pinned: bool | None = None, record_outbox: bool = True) -> NoteEntry:
        entry = self.get_entry(entry_id, include_deleted=True)
        if entry is None:
            raise KeyError(entry_id)
        now = _utc_now()
        updated = NoteEntry(
            id=entry.id,
            notebook_id=entry.notebook_id,
            content=entry.content,
            created_at=entry.created_at,
            updated_at=now,
            deleted_at=entry.deleted_at,
            pinned=entry.pinned if pinned is None else bool(pinned),
            sort_order=int(sort_order),
            version=entry.version + 1,
            device_id=self.device_id,
            last_modified_by="desktop",
            is_conflict_copy=entry.is_conflict_copy,
            source=entry.source,
            origin_entry_id=entry.origin_entry_id,
        )
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE note_entries
                SET content = ?, updated_at = ?, deleted_at = ?, pinned = ?, sort_order = ?,
                    version = ?, device_id = ?, last_modified_by = ?, is_conflict_copy = ?,
                    origin_entry_id = ?, source = ?
                WHERE id = ?
                """,
                (
                    updated.content,
                    updated.updated_at,
                    updated.deleted_at,
                    int(updated.pinned),
                    updated.sort_order,
                    updated.version,
                    updated.device_id,
                    updated.last_modified_by,
                    int(updated.is_conflict_copy),
                    updated.origin_entry_id,
                    updated.source,
                    entry.id,
                ),
            )
            payload = updated.to_dict()
            if record_outbox:
                self._record_local_change(conn, "entry", entry.id, "update", payload, entry.version)
            else:
                self._log_change(conn, "entry", entry.id, "update", payload, entry.version)
        return updated

    def search_entries(self, notebook_id: str, query: str, include_deleted: bool = False) -> list[NoteEntry]:
        pattern = f"%{str(query or '').strip()}%"
        sql = "SELECT * FROM note_entries WHERE notebook_id = ? AND content LIKE ?"
        params: list[object] = [notebook_id, pattern]
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        sql += " ORDER BY pinned DESC, sort_order DESC, updated_at DESC, created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [NoteEntry.from_row(dict(row)) for row in rows]

    def update_entry(self, entry_id: str, content: str, *, source: str | None = None, record_outbox: bool = True) -> NoteEntry:
        entry = self.get_entry(entry_id, include_deleted=True)
        if entry is None:
            raise KeyError(entry_id)
        now = _utc_now()
        updated = NoteEntry(
            id=entry.id,
            notebook_id=entry.notebook_id,
            content=str(content or ""),
            created_at=entry.created_at,
            updated_at=now,
            deleted_at=entry.deleted_at,
            pinned=entry.pinned,
            sort_order=entry.sort_order,
            version=entry.version + 1,
            device_id=self.device_id,
            last_modified_by="desktop",
            is_conflict_copy=entry.is_conflict_copy,
            source=str(source or entry.source or "manual"),
            origin_entry_id=entry.origin_entry_id,
        )
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE note_entries
                SET content = ?, updated_at = ?, version = ?, device_id = ?, last_modified_by = ?, source = ?
                WHERE id = ?
                """,
                (
                    updated.content,
                    updated.updated_at,
                    updated.version,
                    updated.device_id,
                    updated.last_modified_by,
                    updated.source,
                    entry_id,
                ),
            )
            if record_outbox:
                self._record_local_change(conn, "entry", entry_id, "update", updated.to_dict(), entry.version)
            else:
                self._log_change(conn, "entry", entry_id, "update", updated.to_dict(), entry.version)
        return updated

    def list_pending_ops(self, limit: int = 100) -> list[SyncOp]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT op_id, entity_type, entity_id, action, payload_json, base_version,
                       created_at, retry_count, status
                FROM sync_outbox
                WHERE status = 'pending'
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (max(int(limit), 0),),
            ).fetchall()
        return [SyncOp.from_row(dict(row)) for row in rows]

    def list_outbox_ops(self, statuses: Iterable[str] | None = None, limit: int = 100) -> list[SyncOp]:
        status_values = [str(item or "").strip() for item in (statuses or ("pending", "sending", "acked", "failed")) if str(item or "").strip()]
        params: list[object] = []
        query = """
            SELECT op_id, entity_type, entity_id, action, payload_json, base_version,
                   created_at, retry_count, status
            FROM sync_outbox
        """
        if status_values:
            placeholders = ", ".join("?" for _ in status_values)
            query += f" WHERE status IN ({placeholders})"
            params.extend(status_values)
        query += " ORDER BY created_at ASC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(max(int(limit), 0))
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [SyncOp.from_row(dict(row)) for row in rows]

    def _fetch_outbox_ops_by_ids(self, conn: sqlite3.Connection, op_ids: list[str]) -> list[SyncOp]:
        if not op_ids:
            return []
        placeholders = ", ".join("?" for _ in op_ids)
        rows = conn.execute(
            f"""
            SELECT op_id, entity_type, entity_id, action, payload_json, base_version,
                   created_at, retry_count, status
            FROM sync_outbox
            WHERE op_id IN ({placeholders})
            """,
            op_ids,
        ).fetchall()
        rows_by_id = {str(row["op_id"]): SyncOp.from_row(dict(row)) for row in rows}
        return [rows_by_id[op_id] for op_id in op_ids if op_id in rows_by_id]

    def claim_outbox_ops(self, limit: int = 100) -> list[SyncOp]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT op_id
                FROM sync_outbox
                WHERE status IN ('pending', 'failed')
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (max(int(limit), 0),),
            ).fetchall()
            op_ids = [str(row["op_id"]) for row in rows]
            if op_ids:
                placeholders = ", ".join("?" for _ in op_ids)
                conn.execute(
                    f"""
                    UPDATE sync_outbox
                    SET status = 'sending', retry_count = retry_count + 1
                    WHERE op_id IN ({placeholders})
                    """,
                    op_ids,
                )
            return self._fetch_outbox_ops_by_ids(conn, op_ids)

    def mark_outbox_acked(self, op_ids: Iterable[str]) -> list[SyncOp]:
        op_list = [str(op_id or "").strip() for op_id in op_ids if str(op_id or "").strip()]
        with self._connect() as conn:
            if op_list:
                placeholders = ", ".join("?" for _ in op_list)
                conn.execute(
                    f"""
                    UPDATE sync_outbox
                    SET status = 'acked'
                    WHERE op_id IN ({placeholders})
                    """,
                    op_list,
                )
            return self._fetch_outbox_ops_by_ids(conn, op_list)

    def mark_outbox_failed(self, op_ids: Iterable[str]) -> list[SyncOp]:
        op_list = [str(op_id or "").strip() for op_id in op_ids if str(op_id or "").strip()]
        with self._connect() as conn:
            if op_list:
                placeholders = ", ".join("?" for _ in op_list)
                conn.execute(
                    f"""
                    UPDATE sync_outbox
                    SET status = 'failed'
                    WHERE op_id IN ({placeholders})
                    """,
                    op_list,
                )
            return self._fetch_outbox_ops_by_ids(conn, op_list)

    def current_cursor(self) -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT COALESCE(MAX(seq), 0) AS seq FROM notes_change_log").fetchone()
        return str(int(dict(row).get("seq") or 0)) if row is not None else "0"

    def list_ops_since(self, cursor: str | int) -> tuple[list[dict], str]:
        try:
            seq = int(cursor or 0)
        except Exception:
            seq = 0
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT seq, op_id, entity_type, entity_id, action, payload_json,
                       base_version, created_at, source_device
                FROM notes_change_log
                WHERE seq > ?
                ORDER BY seq ASC
                """,
                (seq,),
            ).fetchall()
        ops: list[dict] = []
        max_seq = seq
        for row in rows:
            data = dict(row)
            max_seq = max(max_seq, int(data["seq"]))
            ops.append(
                {
                    "cursor": str(int(data["seq"])),
                    "op_id": data["op_id"],
                    "entity_type": data["entity_type"],
                    "entity_id": data["entity_id"],
                    "action": data["action"],
                    "payload": json.loads(data["payload_json"] or "{}"),
                    "base_version": int(data["base_version"] or 0),
                    "created_at": data["created_at"],
                    "source_device": data["source_device"],
                }
            )
        return ops, str(max_seq)

    def snapshot(self) -> dict:
        return {
            "cursor": self.current_cursor(),
            "notebooks": [item.to_dict() for item in self.list_notebooks(include_deleted=True)],
            "entries": [item.to_dict() for item in self.list_all_entries(include_deleted=True)],
        }

    def push_ops(self, ops: Iterable[dict]) -> dict:
        applied: list[dict] = []
        conflicts: list[dict] = []
        acked: list[str] = []
        for op in list(ops or []):
            op_id = str((op or {}).get("op_id") or "").strip()
            result = self.apply_remote_op(op)
            applied.append(result)
            conflicts.extend(list(result.get("conflicts") or []))
            if op_id and result.get("applied"):
                acked.append(op_id)
        return {"cursor": self.current_cursor(), "applied": applied, "conflicts": conflicts, "acked": acked}

    def apply_remote_op(self, op: dict) -> dict:
        op = dict(op or {})
        entity_type = str(op.get("entity_type") or "").strip()
        action = str(op.get("action") or "").strip()
        entity_id = str(op.get("entity_id") or "").strip()
        payload = dict(op.get("payload") or {})
        base_version = int(op.get("base_version") or 0)
        source_device = str(op.get("source_device") or op.get("device_id") or "remote").strip() or "remote"
        if entity_type == "entry":
            return self._apply_remote_entry_op(entity_id, action, payload, base_version, source_device)
        if entity_type == "notebook":
            return self._apply_remote_notebook_op(entity_id, action, payload, base_version, source_device)
        return {"applied": False, "conflicts": [], "cursor": self.current_cursor()}

    def _apply_remote_entry_op(self, entry_id: str, action: str, payload: dict, base_version: int, source_device: str) -> dict:
        current = self.get_entry(entry_id, include_deleted=True)
        if action == "create" and current is None:
            created = self.create_entry(
                str(payload.get("notebook_id") or ""),
                str(payload.get("content") or ""),
                source=str(payload.get("source") or "manual"),
                record_outbox=False,
                entry_id=entry_id,
                device_id=source_device,
                last_modified_by=_semantic_modified_by(source_device),
                created_at=str(payload.get("created_at") or _utc_now()),
                updated_at=str(payload.get("updated_at") or payload.get("created_at") or _utc_now()),
                pinned=payload.get("pinned"),
                sort_order=payload.get("sort_order"),
                version=payload.get("version"),
                is_conflict_copy=payload.get("is_conflict_copy"),
                origin_entry_id=payload.get("origin_entry_id"),
            )
            return {"applied": True, "conflicts": [], "cursor": self.current_cursor(), "entry": created.to_dict()}
        if current is None:
            return {"applied": False, "conflicts": [], "cursor": self.current_cursor()}
        if action in {"update", "rename", "delete"} and base_version and current.version != base_version:
            conflict = self._create_entry_conflict_copy(current, payload, source_device)
            return {"applied": True, "conflicts": [conflict.to_dict()], "cursor": self.current_cursor()}
        if action == "delete":
            self.delete_entry(entry_id, record_outbox=False)
            return {"applied": True, "conflicts": [], "cursor": self.current_cursor()}
        if action in {"update", "rename"}:
            updated = self._write_entry_remote_update(current, payload, source_device)
            return {"applied": True, "conflicts": [], "cursor": self.current_cursor(), "entry": updated.to_dict()}
        return {"applied": False, "conflicts": [], "cursor": self.current_cursor()}

    def _write_entry_remote_update(self, entry: NoteEntry, payload: dict, source_device: str) -> NoteEntry:
        now = str(payload.get("updated_at") or _utc_now())
        updated = NoteEntry(
            id=entry.id,
            notebook_id=entry.notebook_id,
            content=str(payload.get("content") or entry.content),
            created_at=entry.created_at,
            updated_at=now,
            deleted_at=str(payload.get("deleted_at")) if payload.get("deleted_at") else entry.deleted_at,
            pinned=bool(payload.get("pinned", entry.pinned)),
            sort_order=int(payload.get("sort_order") or entry.sort_order),
            version=entry.version + 1,
            device_id=source_device,
            last_modified_by=_semantic_modified_by(source_device),
            is_conflict_copy=False,
            source=str(payload.get("source") or entry.source or "manual"),
            origin_entry_id=entry.origin_entry_id,
        )
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE note_entries
                SET content = ?, updated_at = ?, deleted_at = ?, pinned = ?, sort_order = ?,
                    version = ?, device_id = ?, last_modified_by = ?, is_conflict_copy = ?, origin_entry_id = ?, source = ?
                WHERE id = ?
                """,
                (
                    updated.content,
                    updated.updated_at,
                    updated.deleted_at,
                    int(updated.pinned),
                    updated.sort_order,
                    updated.version,
                    updated.device_id,
                    updated.last_modified_by,
                    int(updated.is_conflict_copy),
                    updated.origin_entry_id,
                    updated.source,
                    entry.id,
                ),
            )
            self._log_change(conn, "entry", entry.id, "update", updated.to_dict(), entry.version)
        return updated

    def _create_entry_conflict_copy(self, entry: NoteEntry, payload: dict, source_device: str) -> NoteEntry:
        now = str(payload.get("updated_at") or _utc_now())
        content = str(payload.get("content") or entry.content)
        source_label = _semantic_source_label(source_device)
        conflict = NoteEntry(
            id=uuid.uuid4().hex,
            notebook_id=entry.notebook_id,
            content=f"【冲突副本：来自{source_label}】\n{content}",
            created_at=now,
            updated_at=now,
            deleted_at=None,
            pinned=False,
            sort_order=_sort_key(),
            version=1,
            device_id=source_device,
            last_modified_by=_semantic_modified_by(source_device),
            is_conflict_copy=True,
            source=str(payload.get("source") or entry.source or "manual"),
            origin_entry_id=entry.id,
        )
        conflict = NoteEntry(
            id=conflict.id,
            notebook_id=conflict.notebook_id,
            content=f"【冲突副本：来自{source_label}】\n{content}",
            created_at=conflict.created_at,
            updated_at=conflict.updated_at,
            deleted_at=conflict.deleted_at,
            pinned=conflict.pinned,
            sort_order=conflict.sort_order,
            version=conflict.version,
            device_id=conflict.device_id,
            last_modified_by=conflict.last_modified_by,
            is_conflict_copy=conflict.is_conflict_copy,
            source=conflict.source,
            origin_entry_id=conflict.origin_entry_id,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO note_entries (
                    id, notebook_id, content, created_at, updated_at, deleted_at,
                    pinned, sort_order, version, device_id, last_modified_by,
                    is_conflict_copy, origin_entry_id, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conflict.id,
                    conflict.notebook_id,
                    conflict.content,
                    conflict.created_at,
                    conflict.updated_at,
                    conflict.deleted_at,
                    int(conflict.pinned),
                    conflict.sort_order,
                    conflict.version,
                    conflict.device_id,
                    conflict.last_modified_by,
                    int(conflict.is_conflict_copy),
                    conflict.origin_entry_id,
                    conflict.source,
                ),
            )
            self._log_change(conn, "entry", conflict.id, "create", conflict.to_dict(), conflict.version)
        return conflict

    def _apply_remote_notebook_op(self, notebook_id: str, action: str, payload: dict, base_version: int, source_device: str) -> dict:
        current = self.get_notebook(notebook_id, include_deleted=True)
        if action == "create" and current is None:
            created = Notebook(
                id=notebook_id,
                title=str(payload.get("title") or "未命名笔记"),
                created_at=str(payload.get("created_at") or _utc_now()),
                updated_at=str(payload.get("updated_at") or payload.get("created_at") or _utc_now()),
                deleted_at=str(payload.get("deleted_at")) if payload.get("deleted_at") else None,
                pinned=bool(payload.get("pinned", False)),
                sort_order=int(payload.get("sort_order") or _sort_key()),
                version=int(payload.get("version") or 1),
                device_id=source_device,
                last_modified_by=_semantic_modified_by(source_device),
                is_conflict_copy=bool(payload.get("is_conflict_copy", False)),
                origin_notebook_id=str(payload.get("origin_notebook_id")) if payload.get("origin_notebook_id") else None,
            )
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO notebooks (
                        id, title, created_at, updated_at, deleted_at, pinned,
                        sort_order, version, device_id, last_modified_by,
                        is_conflict_copy, origin_notebook_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        created.id,
                        created.title,
                        created.created_at,
                        created.updated_at,
                        created.deleted_at,
                        int(created.pinned),
                        created.sort_order,
                        created.version,
                        created.device_id,
                        created.last_modified_by,
                        int(created.is_conflict_copy),
                        created.origin_notebook_id,
                    ),
                )
                self._log_change(conn, "notebook", created.id, "create", created.to_dict(), created.version)
            return {"applied": True, "conflicts": [], "cursor": self.current_cursor(), "notebook": created.to_dict()}
        if current is None:
            return {"applied": False, "conflicts": [], "cursor": self.current_cursor()}
        if action in {"update", "rename", "delete"} and base_version and current.version != base_version:
            conflict = self._create_notebook_conflict_copy(current, payload, source_device)
            return {"applied": True, "conflicts": [conflict.to_dict()], "cursor": self.current_cursor()}
        if action == "delete":
            self.delete_notebook(notebook_id, record_outbox=False)
            return {"applied": True, "conflicts": [], "cursor": self.current_cursor()}
        if action in {"update", "rename"}:
            updated = self._write_notebook_remote_update(current, payload, source_device)
            return {"applied": True, "conflicts": [], "cursor": self.current_cursor(), "notebook": updated.to_dict()}
        return {"applied": False, "conflicts": [], "cursor": self.current_cursor()}

    def _write_notebook_remote_update(self, notebook: Notebook, payload: dict, source_device: str) -> Notebook:
        now = str(payload.get("updated_at") or _utc_now())
        updated = Notebook(
            id=notebook.id,
            title=str(payload.get("title") or notebook.title),
            created_at=notebook.created_at,
            updated_at=now,
            deleted_at=str(payload.get("deleted_at")) if payload.get("deleted_at") else notebook.deleted_at,
            pinned=bool(payload.get("pinned", notebook.pinned)),
            sort_order=int(payload.get("sort_order") or notebook.sort_order),
            version=notebook.version + 1,
            device_id=source_device,
            last_modified_by=_semantic_modified_by(source_device),
            is_conflict_copy=False,
            origin_notebook_id=notebook.origin_notebook_id,
        )
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE notebooks
                SET title = ?, updated_at = ?, deleted_at = ?, pinned = ?, sort_order = ?,
                    version = ?, device_id = ?, last_modified_by = ?, is_conflict_copy = ?, origin_notebook_id = ?
                WHERE id = ?
                """,
                (
                    updated.title,
                    updated.updated_at,
                    updated.deleted_at,
                    int(updated.pinned),
                    updated.sort_order,
                    updated.version,
                    updated.device_id,
                    updated.last_modified_by,
                    int(updated.is_conflict_copy),
                    updated.origin_notebook_id,
                    notebook.id,
                ),
            )
            self._log_change(conn, "notebook", notebook.id, "update", updated.to_dict(), notebook.version)
        return updated

    def _create_notebook_conflict_copy(self, notebook: Notebook, payload: dict, source_device: str) -> Notebook:
        now = str(payload.get("updated_at") or _utc_now())
        source_label = _semantic_source_label(source_device)
        conflict = Notebook(
            id=uuid.uuid4().hex,
            title=f"{str(payload.get('title') or notebook.title)}（冲突副本：来自{source_label}）",
            created_at=now,
            updated_at=now,
            deleted_at=None,
            pinned=False,
            sort_order=_sort_key(),
            version=1,
            device_id=source_device,
            last_modified_by=_semantic_modified_by(source_device),
            is_conflict_copy=True,
            origin_notebook_id=notebook.id,
        )
        conflict = Notebook(
            id=conflict.id,
            title=f"{str(payload.get('title') or notebook.title)}（冲突副本-{_semantic_conflict_suffix_label(source_device)}）",
            created_at=conflict.created_at,
            updated_at=conflict.updated_at,
            deleted_at=conflict.deleted_at,
            pinned=conflict.pinned,
            sort_order=conflict.sort_order,
            version=conflict.version,
            device_id=conflict.device_id,
            last_modified_by=conflict.last_modified_by,
            is_conflict_copy=conflict.is_conflict_copy,
            origin_notebook_id=conflict.origin_notebook_id,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO notebooks (
                    id, title, created_at, updated_at, deleted_at, pinned,
                    sort_order, version, device_id, last_modified_by,
                    is_conflict_copy, origin_notebook_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conflict.id,
                    conflict.title,
                    conflict.created_at,
                    conflict.updated_at,
                    conflict.deleted_at,
                    int(conflict.pinned),
                    conflict.sort_order,
                    conflict.version,
                    conflict.device_id,
                    conflict.last_modified_by,
                    int(conflict.is_conflict_copy),
                    conflict.origin_notebook_id,
                ),
            )
            self._log_change(conn, "notebook", conflict.id, "create", conflict.to_dict(), conflict.version)
        return conflict

    def _soft_delete_entry_in_conn(self, conn: sqlite3.Connection, entry_id: str, now: str, *, record_outbox: bool) -> bool:
        row = conn.execute("SELECT * FROM note_entries WHERE id = ?", (entry_id,)).fetchone()
        if row is None:
            return False
        entry = NoteEntry.from_row(dict(row))
        conn.execute(
            """
            UPDATE note_entries
            SET deleted_at = ?, updated_at = ?, version = version + 1, device_id = ?, last_modified_by = ?
            WHERE id = ?
            """,
            (now, now, self.device_id, "desktop", entry_id),
        )
        payload = entry.to_dict()
        payload["deleted_at"] = now
        payload["updated_at"] = now
        payload["version"] = entry.version + 1
        if record_outbox:
            self._record_local_change(conn, "entry", entry_id, "delete", payload, entry.version)
        else:
            self._log_change(conn, "entry", entry_id, "delete", payload, entry.version)
        return True

    def delete_entry(self, entry_id: str, *, record_outbox: bool = True) -> None:
        now = _utc_now()
        with self._connect() as conn:
            if not self._soft_delete_entry_in_conn(conn, entry_id, now, record_outbox=record_outbox):
                raise KeyError(entry_id)

    def purge_entry(self, entry_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM note_entries WHERE id = ?", (entry_id,))
            conn.execute("DELETE FROM sync_outbox WHERE entity_type = 'entry' AND entity_id = ?", (entry_id,))
            conn.execute("DELETE FROM notes_change_log WHERE entity_type = 'entry' AND entity_id = ?", (entry_id,))

    def delete_notebook(self, notebook_id: str, *, record_outbox: bool = True) -> None:
        notebook = self.get_notebook(notebook_id, include_deleted=True)
        if notebook is None:
            raise KeyError(notebook_id)
        now = _utc_now()
        with self._connect() as conn:
            child_rows = conn.execute(
                "SELECT id FROM note_entries WHERE notebook_id = ? AND deleted_at IS NULL",
                (notebook_id,),
            ).fetchall()
            conn.execute(
                """
                UPDATE notebooks
                SET deleted_at = ?, updated_at = ?, version = version + 1, device_id = ?, last_modified_by = ?
                WHERE id = ?
                """,
                (now, now, self.device_id, "desktop", notebook_id),
            )
            payload = notebook.to_dict()
            payload["deleted_at"] = now
            payload["updated_at"] = now
            payload["version"] = notebook.version + 1
            if record_outbox:
                self._record_local_change(conn, "notebook", notebook_id, "delete", payload, notebook.version)
            else:
                self._log_change(conn, "notebook", notebook_id, "delete", payload, notebook.version)
            for row in child_rows:
                self._soft_delete_entry_in_conn(conn, str(row["id"]), now, record_outbox=record_outbox)
