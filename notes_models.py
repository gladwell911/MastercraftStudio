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
            title=str(row["title"] or ""),
            created_at=str(row["created_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
            deleted_at=str(row["deleted_at"]) if row.get("deleted_at") else None,
            pinned=_as_bool(row.get("pinned")),
            sort_order=_as_int(row.get("sort_order")),
            version=_as_int(row.get("version"), 1),
            device_id=str(row.get("device_id") or ""),
            last_modified_by=str(row.get("last_modified_by") or ""),
            is_conflict_copy=_as_bool(row.get("is_conflict_copy")),
            origin_notebook_id=str(row["origin_notebook_id"]) if row.get("origin_notebook_id") else None,
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
            notebook_id=str(row["notebook_id"] or ""),
            content=str(row["content"] or ""),
            created_at=str(row["created_at"] or ""),
            updated_at=str(row["updated_at"] or ""),
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
            op_id=str(row["op_id"]),
            entity_type=str(row["entity_type"] or ""),
            entity_id=str(row["entity_id"] or ""),
            action=str(row["action"] or ""),
            payload_json=str(row["payload_json"] or "{}"),
            base_version=_as_int(row.get("base_version")),
            created_at=str(row["created_at"] or ""),
            retry_count=_as_int(row.get("retry_count")),
            status=str(row["status"] or "pending"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
