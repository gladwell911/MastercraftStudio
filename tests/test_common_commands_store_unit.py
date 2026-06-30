from common_commands_models import CommonCommandCreate, CommonCommandUpdate
from common_commands_store import CommonCommandsVersionConflictError, DesktopCommonCommandsStore


def test_create_appends_to_unpinned_tail_and_increments_revision(tmp_path):
    store = DesktopCommonCommandsStore(tmp_path / "common_commands.json")
    store.initialize()

    first = store.create_command(
        CommonCommandCreate(
            device_id="desktop-a",
            request_id="req-a",
            command_text="first",
        )
    )
    second = store.create_command(
        CommonCommandCreate(
            device_id="desktop-a",
            request_id="req-b",
            command_text="second",
        )
    )

    snapshot = store.read_snapshot()

    assert first.version == 1
    assert first.revision == 1
    assert first.sort_order == 0
    assert second.version == 1
    assert second.revision == 2
    assert second.sort_order == 1
    assert [item.command_text for item in snapshot.commands] == ["first", "second"]
    assert [item.sort_order for item in snapshot.commands] == [0, 1]
    assert snapshot.revision == 2


def test_update_rejects_stale_command_version(tmp_path):
    store = DesktopCommonCommandsStore(tmp_path / "common_commands.json")
    store.initialize()
    created = store.create_command(
        CommonCommandCreate(
            device_id="desktop-a",
            request_id="req-a",
            command_text="first",
        )
    )

    updated = store.update_command(
        created.id,
        CommonCommandUpdate(
            expected_version=created.version,
            command_text="updated",
        ),
    )

    assert updated.version == 2

    try:
        store.update_command(
            created.id,
            CommonCommandUpdate(
                expected_version=created.version,
                command_text="stale",
            ),
        )
    except CommonCommandsVersionConflictError as exc:
        assert exc.current_version == 2
        assert exc.expected_version == 1
    else:
        raise AssertionError("expected CommonCommandsVersionConflictError")


def test_create_is_idempotent_per_device_request_id(tmp_path):
    store = DesktopCommonCommandsStore(tmp_path / "common_commands.json")
    store.initialize()

    first = store.create_command(
        CommonCommandCreate(
            device_id="desktop-a",
            request_id="req-a",
            command_text="first",
        )
    )
    second = store.create_command(
        CommonCommandCreate(
            device_id="desktop-a",
            request_id="req-a",
            command_text="changed but ignored",
        )
    )

    snapshot = store.read_snapshot()

    assert second == first
    assert len(snapshot.commands) == 1
    assert snapshot.commands[0].command_text == "first"
    assert snapshot.revision == 1
