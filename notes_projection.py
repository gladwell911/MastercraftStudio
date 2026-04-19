from __future__ import annotations

from dataclasses import dataclass

from notes_models import NoteEntry, Notebook, NotesSnapshot


@dataclass(slots=True)
class _ProjectionState:
    notebooks: list[Notebook]
    entries: list[NoteEntry]
    notebook_by_id: dict[str, Notebook] | None = None
    entry_by_id: dict[str, NoteEntry] | None = None
    entries_by_notebook: dict[str, list[NoteEntry]] | None = None

    def __post_init__(self) -> None:
        self.notebook_by_id = {item.id: item for item in self.notebooks}
        self.entry_by_id = {item.id: item for item in self.entries}
        self.entries_by_notebook = {}
        for entry in self.entries:
            self.entries_by_notebook.setdefault(entry.notebook_id, []).append(entry)


class DesktopNotesProjection:
    def __init__(self, store) -> None:
        self.store = store

    def _load_state(self) -> _ProjectionState:
        snapshot = self.store.load_documents()
        return _ProjectionState(
            notebooks=self._project_notebooks(snapshot),
            entries=self._project_entries(snapshot),
        )

    def _project_notebooks(self, snapshot: NotesSnapshot) -> list[Notebook]:
        return [
            Notebook.from_doc(doc, device_id=getattr(self.store, "device_id", ""))
            for doc in snapshot.notebooks
        ]

    def _project_entries(self, snapshot: NotesSnapshot) -> list[NoteEntry]:
        return [
            NoteEntry.from_doc(doc, device_id=getattr(self.store, "device_id", ""), source=doc.source)
            for doc in snapshot.entries
        ]

    @staticmethod
    def _filter_deleted(items, *, include_deleted: bool):
        if include_deleted:
            return list(items)
        return [item for item in items if getattr(item, "deleted_at", None) is None]

    def list_notebooks(self, include_deleted: bool = False) -> list[Notebook]:
        state = self._load_state()
        return self._filter_deleted(state.notebooks, include_deleted=include_deleted)

    def search_notebooks(self, query: str, include_deleted: bool = False) -> list[Notebook]:
        needle = str(query or "").strip().casefold()
        notebooks = self.list_notebooks(include_deleted=include_deleted)
        if not needle:
            return notebooks
        return [item for item in notebooks if needle in str(item.title or "").casefold()]

    def get_notebook(self, notebook_id: str, include_deleted: bool = False) -> Notebook | None:
        state = self._load_state()
        notebook = state.notebook_by_id.get(str(notebook_id or ""))
        if notebook is None:
            return None
        if not include_deleted and notebook.deleted_at is not None:
            return None
        return notebook

    def list_entries(self, notebook_id: str, include_deleted: bool = False) -> list[NoteEntry]:
        state = self._load_state()
        entries = state.entries_by_notebook.get(str(notebook_id or ""), [])
        return self._filter_deleted(entries, include_deleted=include_deleted)

    def get_entry(self, entry_id: str, include_deleted: bool = False) -> NoteEntry | None:
        state = self._load_state()
        entry = state.entry_by_id.get(str(entry_id or ""))
        if entry is None:
            return None
        if not include_deleted and entry.deleted_at is not None:
            return None
        return entry
