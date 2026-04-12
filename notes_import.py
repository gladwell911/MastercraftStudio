from __future__ import annotations

from pathlib import Path


def _read_text_with_fallbacks(file_path: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "gbk", "utf-16"):
        try:
            return file_path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return file_path.read_text(encoding="utf-8", errors="ignore")


def import_note_entries_from_file(store, notebook_id: str, file_path: Path):
    raw = _read_text_with_fallbacks(Path(file_path))
    lines = [line.strip() for line in raw.replace("\r", "\n").split("\n") if line.strip()]
    return store.import_entries(notebook_id=notebook_id, lines=lines, source="import_file")


def import_note_entries_from_clipboard(store, notebook_id: str, text: str):
    lines = [line.strip() for line in str(text or "").replace("\r", "\n").split("\n") if line.strip()]
    return store.import_entries(notebook_id=notebook_id, lines=lines, source="import_clipboard")
