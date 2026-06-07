import pytest

from listbox_model import IncrementalListBoxModel


class FakeListBox:
    def __init__(self):
        self.items = []
        self.selection = -1
        self.calls = []

    def GetCount(self):
        return len(self.items)

    def GetString(self, idx):
        return self.items[idx]

    def SetString(self, idx, label):
        self.calls.append(("SetString", idx, label))
        self.items[idx] = label

    def Append(self, label):
        self.calls.append(("Append", label))
        self.items.append(label)

    def Insert(self, label, idx):
        self.calls.append(("Insert", idx, label))
        self.items.insert(idx, label)

    def Delete(self, idx):
        self.calls.append(("Delete", idx))
        del self.items[idx]

    def Clear(self):
        self.calls.append(("Clear",))
        self.items.clear()
        self.selection = -1

    def GetSelection(self):
        return self.selection

    def SetSelection(self, idx):
        self.calls.append(("SetSelection", idx))
        self.selection = idx


def test_update_label_changes_only_existing_row():
    control = FakeListBox()
    model = IncrementalListBoxModel(control)
    model.replace_visible_page([("a", "Alpha"), ("b", "Beta")], selected_id="b")
    control.calls.clear()

    changed = model.update_label("a", "Alpha 2")

    assert changed is True
    assert control.items == ["Alpha 2", "Beta"]
    assert control.calls == [("SetString", 0, "Alpha 2")]
    assert model.visible_ids == ["a", "b"]


def test_update_label_noops_when_label_is_unchanged():
    control = FakeListBox()
    model = IncrementalListBoxModel(control)
    model.replace_visible_page([("a", "Alpha")], selected_id="a")
    control.calls.clear()

    changed = model.update_label("a", "Alpha")

    assert changed is False
    assert control.calls == []


def test_insert_and_remove_touch_only_target_rows():
    control = FakeListBox()
    model = IncrementalListBoxModel(control)
    model.replace_visible_page([("a", "Alpha"), ("c", "Charlie")])
    control.calls.clear()

    model.insert("b", "Beta", 1)
    model.remove("a")

    assert control.items == ["Beta", "Charlie"]
    assert control.calls == [("Insert", 1, "Beta"), ("Delete", 0)]
    assert model.visible_ids == ["b", "c"]


def test_append_uses_append_without_reselecting_existing_row():
    control = FakeListBox()
    model = IncrementalListBoxModel(control)
    model.replace_visible_page([("a", "Alpha")], selected_id="a")
    control.calls.clear()

    changed = model.append("b", "Beta")

    assert changed is True
    assert control.items == ["Alpha", "Beta"]
    assert control.selection == 0
    assert control.calls == [("Append", "Beta")]
    assert model.visible_ids == ["a", "b"]


def test_move_preserves_selected_id():
    control = FakeListBox()
    model = IncrementalListBoxModel(control)
    model.replace_visible_page([("a", "Alpha"), ("b", "Beta"), ("c", "Charlie")], selected_id="b")
    control.calls.clear()

    moved = model.move("b", 0, preserve_selection=True)

    assert moved is True
    assert control.items == ["Beta", "Alpha", "Charlie"]
    assert model.visible_ids == ["b", "a", "c"]
    assert model.selected_id() == "b"
    assert control.selection == 0
    assert control.calls == [("Delete", 1), ("Insert", 0, "Beta"), ("SetSelection", 0)]


def test_replace_visible_page_noops_when_ids_and_labels_match():
    control = FakeListBox()
    model = IncrementalListBoxModel(control)
    model.replace_visible_page([("a", "Alpha"), ("b", "Beta")], selected_id="a")
    control.calls.clear()

    changed = model.replace_visible_page([("a", "Alpha"), ("b", "Beta")], selected_id="a")

    assert changed is False
    assert control.calls == []
