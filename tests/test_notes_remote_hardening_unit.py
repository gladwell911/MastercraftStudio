from __future__ import annotations

import sqlite3

import pytest

from notes_store import NotesStore


@pytest.mark.parametrize(
    ("op", "reason"),
    [
        ("not-a-map", "INVALID_REMOTE_OP"),
        (
            {
                "entity_type": "notebook",
                "entity_id": "nb-bad-payload",
                "action": "create",
                "payload": ["not", "a", "map"],
            },
            "INVALID_REMOTE_PAYLOAD",
        ),
        (
            {
                "entity_type": "notebook",
                "entity_id": "nb-bool-version",
                "action": "create",
                "payload": {"title": "bad", "version": True},
            },
            "INVALID_VERSION",
        ),
        (
            {
                "entity_type": "notebook",
                "entity_id": "nb-missing-base",
                "action": "update",
                "payload": {"title": "bad"},
            },
            "INVALID_BASE_VERSION",
        ),
        (
            {
                "entity_type": "notebook",
                "entity_id": "nb-bool-base",
                "action": "delete",
                "base_version": True,
                "payload": {},
            },
            "INVALID_BASE_VERSION",
        ),
        (
            {
                "entity_type": "notebook",
                "entity_id": "nb-oversized-version",
                "action": "create",
                "payload": {"title": "bad", "version": 1 << 63},
            },
            "INVALID_VERSION",
        ),
        (
            {
                "entity_type": "notebook",
                "entity_id": "nb-oversized-base",
                "action": "delete",
                "base_version": 1 << 63,
                "payload": {},
            },
            "INVALID_BASE_VERSION",
        ),
    ],
)
def test_remote_note_ops_quarantine_malformed_shapes_and_numeric_bools(
    tmp_path, op, reason
):
    store = NotesStore(tmp_path / "notes.db", device_id="desktop-test")
    store.initialize()

    result = store.apply_remote_op(op)

    assert result["applied"] is False
    assert result["quarantined"] is True
    assert result["reason"] == reason
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM remote_op_quarantine WHERE reason=?", (reason,)
        ).fetchone()[0] == 1


def test_remote_note_op_id_replay_is_idempotent_without_cursor_or_row_duplication(
    tmp_path,
):
    store = NotesStore(tmp_path / "notes.db", device_id="desktop-test")
    store.initialize()
    op = {
        "op_id": "remote-create-once",
        "entity_type": "notebook",
        "entity_id": "notebook-once",
        "action": "create",
        "payload": {"title": "created once"},
    }

    first = store.apply_remote_op(op)
    second = store.apply_remote_op({**op, "payload": {"title": "mutated replay"}})

    assert second == first
    assert store.current_cursor() == first["cursor"]
    assert store.get_notebook("notebook-once").title == "created once"
    with sqlite3.connect(store.db_path) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM notebooks WHERE id='notebook-once'"
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM applied_remote_ops "
                "WHERE op_id='remote-create-once'"
            ).fetchone()[0]
            == 1
        )


def test_remote_entry_create_and_move_require_a_live_parent_notebook(tmp_path):
    store = NotesStore(tmp_path / "notes.db", device_id="desktop-test")
    store.initialize()
    live = store.create_notebook("live")
    deleted = store.create_notebook("deleted")
    entry = store.create_entry(live.id, "entry", source="manual")
    store.delete_notebook(deleted.id)

    create_result = store.apply_remote_op(
        {
            "op_id": "create-under-deleted",
            "entity_type": "entry",
            "entity_id": "remote-entry-deleted-parent",
            "action": "create",
            "payload": {"notebook_id": deleted.id, "content": "must reject"},
        }
    )
    move_result = store.apply_remote_op(
        {
            "op_id": "move-under-deleted",
            "entity_type": "entry",
            "entity_id": entry.id,
            "action": "update",
            "base_version": entry.version,
            "payload": {"notebook_id": deleted.id, "content": "must reject"},
        }
    )

    assert create_result["applied"] is False
    assert move_result["applied"] is False
    assert store.get_entry(entry.id).notebook_id == live.id


def test_remote_note_compare_and_swap_does_not_overwrite_newer_version(tmp_path):
    store = NotesStore(tmp_path / "notes.db", device_id="desktop-test")
    store.initialize()
    notebook = store.create_notebook("base")
    updated = store.update_notebook(notebook.id, "local newer")

    result = store.apply_remote_op(
        {
            "op_id": "stale-update",
            "entity_type": "notebook",
            "entity_id": notebook.id,
            "action": "update",
            "base_version": notebook.version,
            "source_device": "mobile-test",
            "payload": {"title": "stale remote overwrite"},
        }
    )

    assert result["applied"] is True
    assert result["conflicts"]
    current = store.get_notebook(notebook.id)
    assert current.version == updated.version
    assert current.title == "local newer"


def test_stale_entry_update_does_not_create_conflict_under_deleted_parent(tmp_path):
    store = NotesStore(tmp_path / "notes.db", device_id="desktop-test")
    store.initialize()
    notebook = store.create_notebook("soon deleted")
    entry = store.create_entry(notebook.id, "original")
    store.update_entry(entry.id, "local newer")
    store.delete_notebook(notebook.id)

    result = store.apply_remote_op(
        {
            "op_id": "stale-under-deleted-parent",
            "entity_type": "entry",
            "entity_id": entry.id,
            "action": "update",
            "base_version": entry.version,
            "source_device": "mobile-test",
            "payload": {"content": "must not create conflict"},
        }
    )

    assert result["applied"] is False
    assert result["conflicts"] == []
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM entries WHERE origin_entry_id=? AND is_conflict_copy=1",
            (entry.id,),
        ).fetchone()[0] == 0
