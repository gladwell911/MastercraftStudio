from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


@dataclass(slots=True, frozen=True)
class NotebookDoc:
    id: str
    title: str
    created_at: str
    updated_at: str
    version: int = 1
    device_id: str = ""
    last_modified_by: str = "desktop"
    is_conflict_copy: bool = False
    origin_notebook_id: str | None = None
    rev: str = ""
    deleted: bool = False
    dirty: bool = True

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "NotebookDoc":
        return cls(
            id=str(row["id"]),
            title=str(row.get("title") or ""),
            created_at=str(row.get("created_at") or ""),
            updated_at=str(row.get("updated_at") or ""),
            version=_as_int(row.get("version"), 1),
            device_id=str(row.get("device_id") or ""),
            last_modified_by=str(row.get("last_modified_by") or "desktop"),
            is_conflict_copy=_as_bool(row.get("is_conflict_copy")),
            origin_notebook_id=str(row["origin_notebook_id"]) if row.get("origin_notebook_id") else None,
            rev=str(row.get("rev") or ""),
            deleted=_as_bool(row.get("deleted")),
            dirty=_as_bool(row.get("dirty", 1)),
        )

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "version": self.version,
            "device_id": self.device_id,
            "last_modified_by": self.last_modified_by,
            "is_conflict_copy": int(self.is_conflict_copy),
            "origin_notebook_id": self.origin_notebook_id,
            "rev": self.rev,
            "deleted": int(self.deleted),
            "dirty": int(self.dirty),
        }

    def to_document(self) -> dict[str, Any]:
        return {
            "_id": f"notebook:{self.id}",
            "type": "notebook",
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "version": self.version,
            "device_id": self.device_id,
            "last_modified_by": self.last_modified_by,
            "is_conflict_copy": self.is_conflict_copy,
            "origin_notebook_id": self.origin_notebook_id,
            "rev": self.rev,
            "deleted": self.deleted,
            "dirty": self.dirty,
        }


@dataclass(slots=True, frozen=True)
class EntryDoc:
    id: str
    notebook_id: str
    content: str
    created_at: str
    updated_at: str
    sort_order: int
    version: int = 1
    device_id: str = ""
    last_modified_by: str = "desktop"
    is_conflict_copy: bool = False
    origin_entry_id: str | None = None
    source: str = "manual"
    rev: str = ""
    deleted: bool = False
    dirty: bool = True

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "EntryDoc":
        return cls(
            id=str(row["id"]),
            notebook_id=str(row.get("notebook_id") or ""),
            content=str(row.get("content") or ""),
            created_at=str(row.get("created_at") or ""),
            updated_at=str(row.get("updated_at") or ""),
            sort_order=_as_int(row.get("sort_order")),
            version=_as_int(row.get("version"), 1),
            device_id=str(row.get("device_id") or ""),
            last_modified_by=str(row.get("last_modified_by") or "desktop"),
            is_conflict_copy=_as_bool(row.get("is_conflict_copy")),
            origin_entry_id=str(row["origin_entry_id"]) if row.get("origin_entry_id") else None,
            source=str(row.get("source") or "manual"),
            rev=str(row.get("rev") or ""),
            deleted=_as_bool(row.get("deleted")),
            dirty=_as_bool(row.get("dirty", 1)),
        )

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "notebook_id": self.notebook_id,
            "content": self.content,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "sort_order": self.sort_order,
            "version": self.version,
            "device_id": self.device_id,
            "last_modified_by": self.last_modified_by,
            "is_conflict_copy": int(self.is_conflict_copy),
            "origin_entry_id": self.origin_entry_id,
            "source": self.source,
            "rev": self.rev,
            "deleted": int(self.deleted),
            "dirty": int(self.dirty),
        }

    def to_document(self) -> dict[str, Any]:
        return {
            "_id": f"entry:{self.id}",
            "type": "entry",
            "notebook_id": self.notebook_id,
            "content": self.content,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "sort_order": self.sort_order,
            "version": self.version,
            "device_id": self.device_id,
            "last_modified_by": self.last_modified_by,
            "is_conflict_copy": self.is_conflict_copy,
            "origin_entry_id": self.origin_entry_id,
            "source": self.source,
            "rev": self.rev,
            "deleted": self.deleted,
            "dirty": self.dirty,
        }


@dataclass(slots=True, frozen=True)
class NotesSnapshot:
    notebooks: list[NotebookDoc]
    entries: list[EntryDoc]

    def to_documents(self) -> list[dict[str, Any]]:
        documents = [item.to_document() for item in self.notebooks]
        documents.extend(item.to_document() for item in self.entries)
        return documents


@dataclass(slots=True)
class Notebook:
    id: str
    title: str
    created_at: str
    updated_at: str
    deleted_at: str | None
    pinned: bool
    sort_order: int
    version: int
    device_id: str
    last_modified_by: str
    is_conflict_copy: bool
    origin_notebook_id: str | None = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "Notebook":
        return cls(
            id=str(row["id"]),
            title=str(row.get("title") or ""),
            created_at=str(row.get("created_at") or ""),
            updated_at=str(row.get("updated_at") or ""),
            deleted_at=str(row["deleted_at"]) if row.get("deleted_at") else None,
            pinned=_as_bool(row.get("pinned")),
            sort_order=_as_int(row.get("sort_order")),
            version=_as_int(row.get("version"), 1),
            device_id=str(row.get("device_id") or ""),
            last_modified_by=str(row.get("last_modified_by") or ""),
            is_conflict_copy=_as_bool(row.get("is_conflict_copy")),
            origin_notebook_id=str(row["origin_notebook_id"]) if row.get("origin_notebook_id") else None,
        )

    @classmethod
    def from_doc(cls, doc: NotebookDoc, *, device_id: str = "", last_modified_by: str = "desktop") -> "Notebook":
        return cls(
            id=doc.id,
            title=doc.title,
            created_at=doc.created_at,
            updated_at=doc.updated_at,
            deleted_at=doc.updated_at if doc.deleted else None,
            pinned=False,
            sort_order=0,
            version=doc.version,
            device_id=doc.device_id or device_id,
            last_modified_by=doc.last_modified_by or last_modified_by,
            is_conflict_copy=doc.is_conflict_copy,
            origin_notebook_id=doc.origin_notebook_id,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class NoteEntry:
    id: str
    notebook_id: str
    content: str
    created_at: str
    updated_at: str
    deleted_at: str | None
    pinned: bool
    sort_order: int
    version: int
    device_id: str
    last_modified_by: str
    is_conflict_copy: bool
    source: str
    origin_entry_id: str | None = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "NoteEntry":
        return cls(
            id=str(row["id"]),
            notebook_id=str(row.get("notebook_id") or ""),
            content=str(row.get("content") or ""),
            created_at=str(row.get("created_at") or ""),
            updated_at=str(row.get("updated_at") or ""),
            deleted_at=str(row["deleted_at"]) if row.get("deleted_at") else None,
            pinned=_as_bool(row.get("pinned")),
            sort_order=_as_int(row.get("sort_order")),
            version=_as_int(row.get("version"), 1),
            device_id=str(row.get("device_id") or ""),
            last_modified_by=str(row.get("last_modified_by") or ""),
            is_conflict_copy=_as_bool(row.get("is_conflict_copy")),
            source=str(row.get("source") or "manual"),
            origin_entry_id=str(row["origin_entry_id"]) if row.get("origin_entry_id") else None,
        )

    @classmethod
    def from_doc(
        cls,
        doc: EntryDoc,
        *,
        device_id: str = "",
        last_modified_by: str = "desktop",
        source: str = "manual",
    ) -> "NoteEntry":
        return cls(
            id=doc.id,
            notebook_id=doc.notebook_id,
            content=doc.content,
            created_at=doc.created_at,
            updated_at=doc.updated_at,
            deleted_at=doc.updated_at if doc.deleted else None,
            pinned=False,
            sort_order=doc.sort_order,
            version=doc.version,
            device_id=doc.device_id or device_id,
            last_modified_by=doc.last_modified_by or last_modified_by,
            is_conflict_copy=doc.is_conflict_copy,
            source=doc.source or source,
            origin_entry_id=doc.origin_entry_id,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SyncOp:
    op_id: str
    entity_type: str
    entity_id: str
    action: str
    payload_json: str
    base_version: int
    created_at: str
    retry_count: int
    status: str

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "SyncOp":
        return cls(
            op_id=str(row.get("op_id") or ""),
            entity_type=str(row.get("entity_type") or ""),
            entity_id=str(row.get("entity_id") or ""),
            action=str(row.get("action") or ""),
            payload_json=str(row.get("payload_json") or "{}"),
            base_version=_as_int(row.get("base_version")),
            created_at=str(row.get("created_at") or ""),
            retry_count=_as_int(row.get("retry_count")),
            status=str(row.get("status") or "pending"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
