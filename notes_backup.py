from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


BACKUP_FORMAT = "mc-notes-backup"
BACKUP_VERSION = 1


def _public_notebook_doc(doc) -> dict[str, Any]:
    return {
        "id": str(getattr(doc, "id", "") or ""),
        "title": str(getattr(doc, "title", "") or ""),
        "created_at": str(getattr(doc, "created_at", "") or ""),
        "updated_at": str(getattr(doc, "updated_at", "") or ""),
    }


def _public_entry_doc(doc) -> dict[str, Any]:
    return {
        "id": str(getattr(doc, "id", "") or ""),
        "notebook_id": str(getattr(doc, "notebook_id", "") or ""),
        "content": str(getattr(doc, "content", "") or ""),
        "created_at": str(getattr(doc, "created_at", "") or ""),
        "updated_at": str(getattr(doc, "updated_at", "") or ""),
        "sort_order": int(getattr(doc, "sort_order", 0) or 0),
        "source": str(getattr(doc, "source", "") or "manual"),
    }


def export_notes_backup(store, file_path: str | Path) -> dict[str, int]:
    snapshot = store.load_documents()
    notebooks = [_public_notebook_doc(doc) for doc in snapshot.notebooks if not bool(getattr(doc, "deleted", False))]
    notebook_ids = {item["id"] for item in notebooks}
    entries = [
        _public_entry_doc(doc)
        for doc in snapshot.entries
        if not bool(getattr(doc, "deleted", False)) and str(getattr(doc, "notebook_id", "") or "") in notebook_ids
    ]
    payload = {
        "format": BACKUP_FORMAT,
        "version": BACKUP_VERSION,
        "exported_at": datetime.now(UTC).isoformat(),
        "notebooks": notebooks,
        "entries": entries,
    }
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"notebooks": len(notebooks), "entries": len(entries)}


def _load_backup_payload(file_path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(file_path).read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("invalid notes backup")
    if payload.get("format") != BACKUP_FORMAT or int(payload.get("version") or 0) != BACKUP_VERSION:
        raise ValueError("unsupported notes backup")
    notebooks = payload.get("notebooks")
    entries = payload.get("entries")
    if not isinstance(notebooks, list) or not isinstance(entries, list):
        raise ValueError("invalid notes backup")
    return payload


def restore_notes_backup(store, file_path: str | Path) -> dict[str, int]:
    payload = _load_backup_payload(file_path)
    backup_notebooks = [item for item in payload["notebooks"] if isinstance(item, dict)]
    backup_entries = [item for item in payload["entries"] if isinstance(item, dict)]
    entries_by_notebook_id: dict[str, list[dict[str, Any]]] = {}
    for entry in backup_entries:
        notebook_id = str(entry.get("notebook_id") or "").strip()
        if notebook_id:
            entries_by_notebook_id.setdefault(notebook_id, []).append(entry)

    existing_by_title = {str(item.title or ""): item for item in store.list_notebooks()}
    created_notebooks = 0
    created_entries = 0

    for backup_notebook in backup_notebooks:
        title = str(backup_notebook.get("title") or "").strip() or "untitled notebook"
        notebook = existing_by_title.get(title)
        if notebook is None:
            notebook = store.create_notebook(title)
            existing_by_title[title] = notebook
            created_notebooks += 1

        existing_contents = {str(entry.content or "") for entry in store.list_entries(notebook.id)}
        for backup_entry in entries_by_notebook_id.get(str(backup_notebook.get("id") or ""), []):
            content = str(backup_entry.get("content") or "")
            if not content.strip() or content in existing_contents:
                continue
            store.create_entry(
                notebook.id,
                content,
                source=str(backup_entry.get("source") or "backup_restore"),
            )
            existing_contents.add(content)
            created_entries += 1

    return {"created_notebooks": created_notebooks, "created_entries": created_entries}
