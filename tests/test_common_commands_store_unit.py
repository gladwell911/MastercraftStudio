from pathlib import Path

import pytest

from common_commands_models import CommonCommandCreate, CommonCommandUpdate
from common_commands_store import (
    CommonCommandsReadError,
    CommonCommandsWriteError,
    CommonCommandsVersionConflictError,
    DesktopCommonCommandsStore,
)


def test_create_appends_to_unpinned_tail_and_increments_revision(tmp_path):
    store = DesktopCommonCommandsStore(tmp_path / "common_commands.json")
    store.initialize()

    first = store.create_command(
        CommonCommandCreate(
            device_id="desktop-a",
            request_id="req-a",
            title="First",
            content="first",
        )
    )
    second = store.create_command(
        CommonCommandCreate(
            device_id="desktop-a",
            request_id="req-b",
            title="Second",
            content="second",
        )
    )

    snapshot = store.read_snapshot()

    assert first.version == 1
    assert first.revision == 1
    assert first.sort_order == 0
    assert second.version == 1
    assert second.revision == 2
    assert second.sort_order == 1
    assert [item.content for item in snapshot.commands] == ["first", "second"]
    assert [item.sort_order for item in snapshot.commands] == [0, 1]
    assert snapshot.revision == 2


def test_update_rejects_stale_command_version(tmp_path):
    store = DesktopCommonCommandsStore(tmp_path / "common_commands.json")
    store.initialize()
    created = store.create_command(
        CommonCommandCreate(
            device_id="desktop-a",
            request_id="req-a",
            title="First",
            content="first",
        )
    )

    updated = store.update_command(
        created.id,
        CommonCommandUpdate(
            expected_version=created.version,
            title="Updated",
            content="updated",
        ),
    )

    assert updated.version == 2

    with pytest.raises(CommonCommandsVersionConflictError) as exc_info:
        store.update_command(
            created.id,
            CommonCommandUpdate(
                expected_version=created.version,
                content="stale",
            ),
        )

    assert exc_info.value.current_version == 2
    assert exc_info.value.expected_version == 1


def test_create_is_idempotent_per_device_request_id(tmp_path):
    store = DesktopCommonCommandsStore(tmp_path / "common_commands.json")
    store.initialize()

    first = store.create_command(
        CommonCommandCreate(
            device_id="desktop-a",
            request_id="req-a",
            title="First",
            content="first",
        )
    )
    second = store.create_command(
        CommonCommandCreate(
            device_id="desktop-a",
            request_id="req-a",
            title="Second",
            content="changed but ignored",
        )
    )

    snapshot = store.read_snapshot()

    assert second == first
    assert len(snapshot.commands) == 1
    assert snapshot.commands[0].content == "first"
    assert snapshot.revision == 1


def test_create_without_request_provenance_allows_multiple_commands(tmp_path):
    store = DesktopCommonCommandsStore(tmp_path / "common_commands.json")
    store.initialize()

    first = store.create_command(
        CommonCommandCreate(
            title="First",
            content="first",
        )
    )
    second = store.create_command(
        CommonCommandCreate(
            title="Second",
            content="second",
        )
    )

    snapshot = store.read_snapshot()

    assert first.id != second.id
    assert [item.content for item in snapshot.commands] == ["first", "second"]
    assert snapshot.revision == 2


def test_restart_read_round_trip(tmp_path):
    path = tmp_path / "common_commands.json"
    store = DesktopCommonCommandsStore(path)
    store.initialize()
    created = store.create_command(
        CommonCommandCreate(
            device_id="desktop-a",
            request_id="req-a",
            title="List files",
            content="dir",
        )
    )

    reopened = DesktopCommonCommandsStore(path)
    snapshot = reopened.read_snapshot()

    assert snapshot.revision == 1
    assert snapshot.commands == [created]


def test_malformed_file_does_not_silently_reset_to_empty_on_mutation(tmp_path):
    path = tmp_path / "common_commands.json"
    path.write_text('{"revision": 1, "commands": [', encoding="utf-8")
    before = path.read_text(encoding="utf-8")
    store = DesktopCommonCommandsStore(path)

    with pytest.raises(CommonCommandsReadError):
        store.create_command(
            CommonCommandCreate(
                device_id="desktop-a",
                request_id="req-a",
                title="Broken",
                content="echo broken",
            )
        )

    assert path.read_text(encoding="utf-8") == before


def test_write_snapshot_replaces_target_atomically_with_temp_file_in_same_dir(tmp_path, monkeypatch):
    path = tmp_path / "common_commands.json"
    store = DesktopCommonCommandsStore(path)
    replaced = []
    original_replace = DesktopCommonCommandsStore._replace_file

    def capture_replace(self, source: Path, target: Path) -> None:
        replaced.append((source, target, source.exists(), source.parent == target.parent))
        original_replace(self, source, target)

    monkeypatch.setattr(DesktopCommonCommandsStore, "_replace_file", capture_replace)

    created = store.create_command(
        CommonCommandCreate(
            device_id="desktop-a",
            request_id="req-a",
            title="Atomic",
            content="echo atomic",
        )
    )

    assert created.revision == 1
    assert len(replaced) >= 1
    source, target, source_exists, same_parent = replaced[-1]
    assert target == path
    assert source_exists is True
    assert same_parent is True
    assert source.name.endswith(".tmp")
    assert not source.exists()
    assert store.read_snapshot().commands[0].content == "echo atomic"


def test_create_wraps_write_failures_in_stable_store_error(tmp_path, monkeypatch):
    path = tmp_path / "common_commands.json"
    store = DesktopCommonCommandsStore(path)

    def fail_replace(self, source: Path, target: Path) -> None:
        raise PermissionError("locked")

    monkeypatch.setattr(DesktopCommonCommandsStore, "_replace_file", fail_replace)

    with pytest.raises(CommonCommandsWriteError) as exc_info:
        store.create_command(
            CommonCommandCreate(
                device_id="desktop-a",
                request_id="req-a",
                title="Blocked",
                content="echo blocked",
            )
        )

    assert exc_info.value.path == path
    assert isinstance(exc_info.value.cause, PermissionError)
    assert not any(path.parent.glob("common_commands.json.*.tmp"))


def test_delete_command_removes_item_and_increments_revision(tmp_path):
    store = DesktopCommonCommandsStore(tmp_path / "common_commands.json")
    store.initialize()
    first = store.create_command(CommonCommandCreate(title="First", content="first"))
    second = store.create_command(CommonCommandCreate(title="Second", content="second"))

    deleted = store.delete_command(second.id, expected_version=second.version)
    snapshot = store.read_snapshot()

    assert deleted == second
    assert snapshot.revision == 3
    assert [item.id for item in snapshot.commands] == [first.id]


def test_delete_command_rejects_stale_version(tmp_path):
    store = DesktopCommonCommandsStore(tmp_path / "common_commands.json")
    store.initialize()
    created = store.create_command(CommonCommandCreate(title="First", content="first"))
    updated = store.update_command(
        created.id,
        CommonCommandUpdate(expected_version=created.version, content="updated"),
    )

    with pytest.raises(CommonCommandsVersionConflictError) as exc_info:
        store.delete_command(created.id, expected_version=created.version)

    assert updated.version == 2
    assert exc_info.value.current_version == 2
    assert exc_info.value.expected_version == 1


def test_pin_command_moves_item_to_pinned_tail(tmp_path):
    store = DesktopCommonCommandsStore(tmp_path / "common_commands.json")
    store.initialize()
    first = store.create_command(CommonCommandCreate(title="First", content="first"))
    second = store.create_command(CommonCommandCreate(title="Second", content="second"))

    pinned = store.pin_command(second.id, expected_version=second.version)
    snapshot = store.read_snapshot()

    assert pinned.pinned is True
    assert pinned.sort_order == 0
    assert pinned.version == 2
    assert snapshot.revision == 3
    assert [item.title for item in snapshot.commands] == ["Second", "First"]

    unpinned = store.unpin_command(second.id, expected_version=pinned.version)
    snapshot = store.read_snapshot()

    assert unpinned.pinned is False
    assert unpinned.sort_order == 1
    assert [item.title for item in snapshot.commands] == ["First", "Second"]


def test_move_up_and_down_only_reorders_inside_same_section(tmp_path):
    store = DesktopCommonCommandsStore(tmp_path / "common_commands.json")
    store.initialize()
    alpha = store.create_command(CommonCommandCreate(title="Alpha", content="alpha"))
    beta = store.create_command(CommonCommandCreate(title="Beta", content="beta"))
    gamma = store.create_command(CommonCommandCreate(title="Gamma", content="gamma"))
    pinned_gamma = store.pin_command(gamma.id, expected_version=gamma.version)

    moved_beta = store.move_up(beta.id, expected_version=beta.version)
    snapshot = store.read_snapshot()

    assert moved_beta.version == 2
    assert [item.title for item in snapshot.commands] == ["Gamma", "Beta", "Alpha"]

    moved_gamma = store.move_down(pinned_gamma.id, expected_version=pinned_gamma.version)
    snapshot = store.read_snapshot()

    assert moved_gamma.id == pinned_gamma.id
    assert moved_gamma.version == pinned_gamma.version
    assert snapshot.revision == 5
    assert [item.title for item in snapshot.commands] == ["Gamma", "Beta", "Alpha"]
