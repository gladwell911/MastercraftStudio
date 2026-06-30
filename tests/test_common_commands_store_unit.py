from pathlib import Path

import pytest

from common_commands_models import CommonCommandCreate, CommonCommandUpdate
from common_commands_store import (
    CommonCommandsReadError,
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
