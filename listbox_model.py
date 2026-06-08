from __future__ import annotations


class IncrementalListBoxModel:
    def __init__(self, control):
        self.control = control
        self.visible_ids: list[str] = []
        self.labels_by_id: dict[str, str] = {}

    def _normalize_id(self, item_id: str) -> str:
        return str(item_id or "").strip()

    def _normalize_label(self, label: str) -> str:
        return str(label or "")

    def row_for_id(self, item_id: str) -> int:
        normalized = self._normalize_id(item_id)
        if not normalized:
            return -1
        try:
            return self.visible_ids.index(normalized)
        except ValueError:
            return -1

    def selected_id(self) -> str:
        idx = self.control.GetSelection()
        if idx < 0 or idx >= len(self.visible_ids):
            return ""
        return self.visible_ids[idx]

    def set_selection_by_id(self, item_id: str) -> bool:
        idx = self.row_for_id(item_id)
        if idx < 0:
            return False
        if self.control.GetSelection() != idx:
            self.control.SetSelection(idx)
        return True

    def replace_visible_page(self, rows: list[tuple[str, str]], selected_id: str | None = None) -> bool:
        normalized_rows = [
            (self._normalize_id(item_id), self._normalize_label(label))
            for item_id, label in rows
            if self._normalize_id(item_id)
        ]
        ids = [item_id for item_id, _label in normalized_rows]
        labels = {item_id: label for item_id, label in normalized_rows}
        current_labels = [self.labels_by_id.get(item_id, "") for item_id in self.visible_ids]
        new_labels = [label for _item_id, label in normalized_rows]
        if self.visible_ids == ids and current_labels == new_labels:
            if selected_id:
                self.set_selection_by_id(selected_id)
            return False
        if self.visible_ids == ids:
            changed = False
            for idx, (item_id, label) in enumerate(normalized_rows):
                if self.labels_by_id.get(item_id, "") == label:
                    continue
                self.control.SetString(idx, label)
                self.labels_by_id[item_id] = label
                changed = True
            if selected_id:
                self.set_selection_by_id(selected_id)
            return changed
        self.control.Clear()
        self.visible_ids = []
        self.labels_by_id = {}
        for item_id, label in normalized_rows:
            self.control.Append(label)
            self.visible_ids.append(item_id)
            self.labels_by_id[item_id] = label
        if selected_id:
            self.set_selection_by_id(selected_id)
        return True

    def update_label(self, item_id: str, label: str) -> bool:
        normalized = self._normalize_id(item_id)
        if normalized not in self.labels_by_id:
            return False
        new_label = self._normalize_label(label)
        if self.labels_by_id.get(normalized) == new_label:
            return False
        idx = self.row_for_id(normalized)
        if idx < 0:
            return False
        self.control.SetString(idx, new_label)
        self.labels_by_id[normalized] = new_label
        return True

    def insert(self, item_id: str, label: str, index: int) -> bool:
        normalized = self._normalize_id(item_id)
        if not normalized:
            return False
        if normalized in self.labels_by_id:
            self.update_label(normalized, label)
            return self.move(normalized, index)
        selected_id = self.selected_id()
        idx = max(0, min(int(index), len(self.visible_ids)))
        new_label = self._normalize_label(label)
        self.control.Insert(new_label, idx)
        self.visible_ids.insert(idx, normalized)
        self.labels_by_id[normalized] = new_label
        if selected_id:
            self.set_selection_by_id(selected_id)
        return True

    def append(self, item_id: str, label: str) -> bool:
        normalized = self._normalize_id(item_id)
        if not normalized:
            return False
        if normalized in self.labels_by_id:
            self.update_label(normalized, label)
            return self.move(normalized, len(self.visible_ids))
        new_label = self._normalize_label(label)
        self.control.Append(new_label)
        self.visible_ids.append(normalized)
        self.labels_by_id[normalized] = new_label
        return True

    def remove(self, item_id: str) -> bool:
        normalized = self._normalize_id(item_id)
        idx = self.row_for_id(normalized)
        if idx < 0:
            return False
        self.control.Delete(idx)
        del self.visible_ids[idx]
        self.labels_by_id.pop(normalized, None)
        return True

    def move(self, item_id: str, index: int, *, preserve_selection: bool = True) -> bool:
        normalized = self._normalize_id(item_id)
        old_idx = self.row_for_id(normalized)
        if old_idx < 0:
            return False
        new_idx = max(0, min(int(index), len(self.visible_ids) - 1))
        if old_idx == new_idx:
            return False
        selected_id = self.selected_id() if preserve_selection else ""
        label = self.labels_by_id[normalized]
        self.control.Delete(old_idx)
        del self.visible_ids[old_idx]
        self.control.Insert(label, new_idx)
        self.visible_ids.insert(new_idx, normalized)
        if preserve_selection and selected_id:
            self.set_selection_by_id(selected_id)
        return True
