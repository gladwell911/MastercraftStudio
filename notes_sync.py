from __future__ import annotations

import threading
from typing import Any

from couchdb_client import CouchDbClient
from notes_models import EntryDoc, NotebookDoc

CHECKPOINT_STATE_KEY = "notes_couchdb_checkpoint"


def _couch_doc_id(doc_type: str, local_id: str) -> str:
    prefix = f"{doc_type}:"
    value = str(local_id or "").strip()
    if value.startswith(prefix):
        return value
    return f"{prefix}{value}"


def _local_doc_id(doc_type: str, remote_id: str) -> str:
    prefix = f"{doc_type}:"
    value = str(remote_id or "").strip()
    if value.startswith(prefix):
        return value[len(prefix) :]
    return value


def _modifier_from_device(device_id: str, fallback: str = "desktop") -> str:
    normalized = str(device_id or "").strip().lower()
    if normalized.startswith("mobile"):
        return "mobile"
    if normalized.startswith("desktop"):
        return "desktop"
    return fallback


class NotesSyncService:
    def __init__(self, store, broadcaster=None, on_remote_ops_applied=None, on_status_changed=None) -> None:
        self.store = store
        self._broadcaster = broadcaster
        self._on_remote_ops_applied = on_remote_ops_applied
        self._on_status_changed = on_status_changed
        self._client: CouchDbClient | None = None
        self._sync_lock = threading.Lock()

    def configure(
        self,
        base_url: str,
        database: str,
        *,
        session=None,
        client: CouchDbClient | None = None,
    ) -> None:
        if client is not None:
            self._client = client
            return
        self._client = CouchDbClient(base_url=base_url, database=database, session=session)

    def close(self) -> None:
        client = None
        with self._sync_lock:
            client = self._client
            self._client = None
        if client is None:
            return
        try:
            client.close()
        except Exception:
            pass

    def is_configured(self) -> bool:
        return self._client is not None

    def get_checkpoint(self) -> str:
        return str(self.store.sync_state_value(CHECKPOINT_STATE_KEY) or "0")

    def sync_once(self) -> dict[str, Any]:
        with self._sync_lock:
            client = self._client
            if client is None:
                return {
                    "configured": False,
                    "pushed": [],
                    "pulled": [],
                    "checkpoint": self.get_checkpoint(),
                    "conflicts": [],
                }
            self._emit_status("sending", cursor=self.get_checkpoint())
            pushed, conflicts = self._push_dirty_documents(client)
            pulled, checkpoint = self._pull_remote_changes(client)
            self._emit_status("synced" if not conflicts else "failed", cursor=checkpoint)
            return {
                "configured": True,
                "pushed": pushed,
                "pulled": pulled,
                "checkpoint": checkpoint,
                "conflicts": conflicts,
            }

    def _emit_status(self, status: str, *, message: str | None = None, cursor: str | None = None) -> None:
        if callable(self._on_status_changed):
            try:
                self._on_status_changed(status, message=message, cursor=cursor)
            except Exception:
                pass

    def _set_sync_state(self, key: str, value: str) -> None:
        with self.store._connect() as conn:
            self.store._set_sync_state(conn, key, str(value))

    def _push_dirty_documents(self, client: CouchDbClient) -> tuple[list[str], list[dict[str, Any]]]:
        snapshot = self.store.load_documents()
        dirty_docs: list[dict[str, Any]] = []
        for notebook in snapshot.notebooks:
            if notebook.dirty:
                dirty_docs.append(self._notebook_to_couch_document(notebook))
        for entry in snapshot.entries:
            if entry.dirty:
                dirty_docs.append(self._entry_to_couch_document(entry))
        if not dirty_docs:
            return [], []

        results = client.write_documents(dirty_docs)
        pushed: list[str] = []
        conflicts: list[dict[str, Any]] = []
        with self.store._connect() as conn:
            for item in results:
                doc_id = str(item.get("id") or "").strip()
                rev = str(item.get("rev") or "").strip()
                error = str(item.get("error") or "").strip()
                if error:
                    conflicts.append({"id": doc_id, "error": error, "reason": str(item.get("reason") or "")})
                    continue
                if not doc_id or not rev:
                    continue
                self._mark_document_synced(conn, doc_id, rev)
                pushed.append(doc_id)
        return pushed, conflicts

    def _pull_remote_changes(self, client: CouchDbClient) -> tuple[list[dict[str, Any]], str]:
        checkpoint = self.get_checkpoint()
        payload = client.fetch_changes(checkpoint)
        rows = list(payload.get("results") or [])
        applied = self._apply_remote_change_rows(rows)
        next_checkpoint = str(payload.get("last_seq") or checkpoint or "0")
        self._set_sync_state(CHECKPOINT_STATE_KEY, next_checkpoint)
        if applied and callable(self._on_remote_ops_applied):
            try:
                self._on_remote_ops_applied({"cursor": next_checkpoint, "applied": applied, "conflicts": []})
            except Exception:
                pass
        return applied, next_checkpoint

    @staticmethod
    def _notebook_to_couch_document(doc: NotebookDoc) -> dict[str, Any]:
        payload = {
            "_id": _couch_doc_id("notebook", doc.id),
            "type": "notebook",
            "title": doc.title,
            "created_at": doc.created_at,
            "updated_at": doc.updated_at,
            "version": doc.version,
            "device_id": doc.device_id,
            "last_modified_by": doc.last_modified_by,
            "is_conflict_copy": doc.is_conflict_copy,
            "origin_notebook_id": _couch_doc_id("notebook", doc.origin_notebook_id) if doc.origin_notebook_id else None,
        }
        if doc.rev:
            payload["_rev"] = doc.rev
        if doc.deleted:
            payload["_deleted"] = True
        return payload

    @staticmethod
    def _entry_to_couch_document(doc: EntryDoc) -> dict[str, Any]:
        payload = {
            "_id": _couch_doc_id("entry", doc.id),
            "type": "entry",
            "notebook_id": _couch_doc_id("notebook", doc.notebook_id),
            "content": doc.content,
            "sort_order": doc.sort_order,
            "source": doc.source,
            "created_at": doc.created_at,
            "updated_at": doc.updated_at,
            "version": doc.version,
            "device_id": doc.device_id,
            "last_modified_by": doc.last_modified_by,
            "is_conflict_copy": doc.is_conflict_copy,
            "origin_entry_id": _couch_doc_id("entry", doc.origin_entry_id) if doc.origin_entry_id else None,
        }
        if doc.rev:
            payload["_rev"] = doc.rev
        if doc.deleted:
            payload["_deleted"] = True
        return payload

    def _mark_document_synced(self, conn, remote_id: str, rev: str) -> None:
        if str(remote_id).startswith("notebook:"):
            conn.execute(
                "UPDATE notebooks SET rev = ?, dirty = 0 WHERE id = ?",
                (rev, _local_doc_id("notebook", remote_id)),
            )
            return
        if str(remote_id).startswith("entry:"):
            conn.execute(
                "UPDATE entries SET rev = ?, dirty = 0 WHERE id = ?",
                (rev, _local_doc_id("entry", remote_id)),
            )

    def _apply_remote_change_rows(self, rows: list[Any]) -> list[dict[str, Any]]:
        notebook_docs: list[dict[str, Any]] = []
        entry_docs: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            doc = row.get("doc")
            if isinstance(doc, dict):
                normalized = dict(doc)
            else:
                normalized = {"_id": row.get("id")}
            if row.get("deleted") is True:
                normalized["_deleted"] = True
            remote_id = str(normalized.get("_id") or row.get("id") or "").strip()
            if remote_id.startswith("notebook:"):
                notebook_docs.append(normalized)
            elif remote_id.startswith("entry:"):
                entry_docs.append(normalized)

        applied: list[dict[str, Any]] = []
        with self.store._connect() as conn:
            for doc in notebook_docs:
                result = self._upsert_remote_notebook(conn, doc)
                if result:
                    applied.append(result)
            for doc in entry_docs:
                result = self._upsert_remote_entry(conn, doc)
                if result:
                    applied.append(result)
        return applied

    def _upsert_remote_notebook(self, conn, doc: dict[str, Any]) -> dict[str, Any] | None:
        remote_id = str(doc.get("_id") or "").strip()
        if not remote_id:
            return None
        notebook_id = _local_doc_id("notebook", remote_id)
        created_at = str(doc.get("created_at") or doc.get("updated_at") or "")
        updated_at = str(doc.get("updated_at") or created_at or "")
        deleted = bool(doc.get("_deleted") or doc.get("deleted"))
        row = conn.execute("SELECT id FROM notebooks WHERE id = ?", (notebook_id,)).fetchone()
        payload = (
            notebook_id,
            str(doc.get("title") or ""),
            created_at,
            updated_at,
            int(doc.get("version") or 1),
            str(doc.get("device_id") or ""),
            str(doc.get("last_modified_by") or _modifier_from_device(str(doc.get("device_id") or ""), "mobile")),
            int(bool(doc.get("is_conflict_copy"))),
            _local_doc_id("notebook", doc.get("origin_notebook_id")) if doc.get("origin_notebook_id") else None,
            str(doc.get("_rev") or doc.get("rev") or ""),
            int(deleted),
        )
        if row is None:
            conn.execute(
                """
                INSERT INTO notebooks (
                    id, title, created_at, updated_at, version, device_id,
                    last_modified_by, is_conflict_copy, origin_notebook_id,
                    rev, deleted, dirty
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                payload,
            )
        else:
            conn.execute(
                """
                UPDATE notebooks
                SET title = ?, created_at = ?, updated_at = ?, version = ?,
                    device_id = ?, last_modified_by = ?, is_conflict_copy = ?,
                    origin_notebook_id = ?, rev = ?, deleted = ?, dirty = 0
                WHERE id = ?
                """,
                (
                    payload[1],
                    payload[2],
                    payload[3],
                    payload[4],
                    payload[5],
                    payload[6],
                    payload[7],
                    payload[8],
                    payload[9],
                    payload[10],
                    notebook_id,
                ),
            )
        notebook = self.store.get_notebook(notebook_id, include_deleted=True)
        return {"entity_type": "notebook", "entity_id": notebook_id, "notebook": notebook.to_dict() if notebook else None}

    def _upsert_remote_entry(self, conn, doc: dict[str, Any]) -> dict[str, Any] | None:
        remote_id = str(doc.get("_id") or "").strip()
        if not remote_id:
            return None
        entry_id = _local_doc_id("entry", remote_id)
        notebook_id = _local_doc_id("notebook", doc.get("notebook_id") or "")
        if not notebook_id:
            return None
        self._ensure_notebook_exists(conn, notebook_id, str(doc.get("created_at") or ""), str(doc.get("updated_at") or ""))
        created_at = str(doc.get("created_at") or doc.get("updated_at") or "")
        updated_at = str(doc.get("updated_at") or created_at or "")
        deleted = bool(doc.get("_deleted") or doc.get("deleted"))
        row = conn.execute("SELECT id FROM entries WHERE id = ?", (entry_id,)).fetchone()
        payload = (
            entry_id,
            notebook_id,
            str(doc.get("content") or ""),
            created_at,
            updated_at,
            int(doc.get("sort_order") or 0),
            int(doc.get("version") or 1),
            str(doc.get("device_id") or ""),
            str(doc.get("last_modified_by") or _modifier_from_device(str(doc.get("device_id") or ""), "mobile")),
            int(bool(doc.get("is_conflict_copy"))),
            _local_doc_id("entry", doc.get("origin_entry_id")) if doc.get("origin_entry_id") else None,
            str(doc.get("source") or "manual"),
            str(doc.get("_rev") or doc.get("rev") or ""),
            int(deleted),
        )
        if row is None:
            conn.execute(
                """
                INSERT INTO entries (
                    id, notebook_id, content, created_at, updated_at,
                    sort_order, version, device_id, last_modified_by,
                    is_conflict_copy, origin_entry_id, source,
                    rev, deleted, dirty
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                payload,
            )
        else:
            conn.execute(
                """
                UPDATE entries
                SET notebook_id = ?, content = ?, created_at = ?, updated_at = ?,
                    sort_order = ?, version = ?, device_id = ?, last_modified_by = ?,
                    is_conflict_copy = ?, origin_entry_id = ?, source = ?,
                    rev = ?, deleted = ?, dirty = 0
                WHERE id = ?
                """,
                (
                    payload[1],
                    payload[2],
                    payload[3],
                    payload[4],
                    payload[5],
                    payload[6],
                    payload[7],
                    payload[8],
                    payload[9],
                    payload[10],
                    payload[11],
                    payload[12],
                    payload[13],
                    entry_id,
                ),
            )
        entry = self.store.get_entry(entry_id, include_deleted=True)
        return {"entity_type": "entry", "entity_id": entry_id, "entry": entry.to_dict() if entry else None}

    def _ensure_notebook_exists(self, conn, notebook_id: str, created_at: str, updated_at: str) -> None:
        row = conn.execute("SELECT id FROM notebooks WHERE id = ?", (notebook_id,)).fetchone()
        if row is not None:
            return
        conn.execute(
            """
            INSERT INTO notebooks (
                id, title, created_at, updated_at, version, device_id,
                last_modified_by, is_conflict_copy, origin_notebook_id,
                rev, deleted, dirty
            ) VALUES (?, ?, ?, ?, 1, '', 'mobile', 0, NULL, '', 0, 0)
            """,
            (notebook_id, "untitled notebook", created_at or updated_at, updated_at or created_at),
        )

    def snapshot(self) -> dict:
        return self.store.snapshot()

    def pull_since(self, cursor: str) -> dict:
        normalized_cursor = str(cursor or "0").strip() or "0"
        current_cursor = str(self.store.current_cursor() or "0")
        try:
            requested_value = int(normalized_cursor)
        except Exception:
            requested_value = 0
        try:
            current_value = int(current_cursor)
        except Exception:
            current_value = 0
        if requested_value <= 0 or requested_value > current_value:
            snapshot = self.store.snapshot()
            snapshot["cursor"] = str(snapshot.get("cursor") or current_cursor or "0")
            return snapshot
        ops, next_cursor = self.store.list_ops_since(cursor)
        return {"cursor": next_cursor, "ops": ops}

    def push_ops(self, ops: list[dict]) -> dict:
        result = self.store.push_ops(list(ops or []))
        if not isinstance(result, dict):
            result = {"cursor": self.store.current_cursor(), "applied": [], "conflicts": [], "acked": []}
        if callable(self._broadcaster):
            try:
                self._broadcaster(result)
            except Exception:
                pass
        return result

    def subscribe(self, payload: dict | None = None) -> dict:
        payload = dict(payload or {})
        return {"cursor": self.store.current_cursor(), "snapshot": self.snapshot(), "subscribed": True, "request": payload}

    def ack(self, payload: dict | None = None) -> dict:
        payload = dict(payload or {})
        op_ids = payload.get("op_ids")
        if not isinstance(op_ids, list):
            op_ids = [payload.get("op_id")] if payload.get("op_id") else []
        acked = self.store.mark_outbox_acked(op_ids)
        return {"cursor": self.store.current_cursor(), "acked": [op.op_id for op in acked], "request": payload}

    def ping(self, payload: dict | None = None) -> dict:
        payload = dict(payload or {})
        return {"cursor": self.store.current_cursor(), "pong": True, "request": payload}

    def claim_outbox_ops(self, limit: int = 100) -> list:
        ops = self.store.claim_outbox_ops(limit)
        if ops:
            self._emit_status("sending")
        return ops

    def ack_outbox_ops(self, op_ids) -> list:
        ops = self.store.mark_outbox_acked(op_ids)
        if ops:
            self._emit_status("acked")
        return ops

    def fail_outbox_ops(self, op_ids) -> list:
        ops = self.store.mark_outbox_failed(op_ids)
        if ops:
            self._emit_status("failed")
        return ops

    def apply_remote_ops(self, ops: list[dict]) -> dict:
        applied: list[dict] = []
        conflicts: list[dict] = []
        for op in list(ops or []):
            result = self.store.apply_remote_op(op)
            applied.append(result)
            conflicts.extend(list(result.get("conflicts") or []))
        result = {"cursor": self.store.current_cursor(), "applied": applied, "conflicts": conflicts}
        if callable(self._on_remote_ops_applied):
            try:
                self._on_remote_ops_applied(result)
            except Exception:
                pass
        return result
