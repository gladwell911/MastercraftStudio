from remote_nats_protocol import build_file_command_event


def test_build_file_probe_event_wraps_path_in_body():
    event = build_file_command_event(
        request_id="req-1",
        event_type="file_probe",
        device_id="desktop",
        body={"path": "d:\\123.txt"},
        chat_id="chat-1",
    )

    assert event["type"] == "file_probe"
    assert event["request_id"] == "req-1"
    assert event["device_id"] == "desktop"
    assert event["chat_id"] == "chat-1"
    assert event["body"] == {"path": "d:\\123.txt"}
    assert event["event_id"].startswith("file_probe-")


def test_build_file_progress_event_normalizes_numeric_fields():
    event = build_file_command_event(
        request_id="req-2",
        event_type="file_progress",
        device_id="desktop",
        body={
            "file_id": "file-1",
            "name": "report.txt",
            "size_bytes": "1000",
            "transferred_bytes": "250",
            "speed_bytes_per_second": "500",
        },
    )

    assert event["body"]["size_bytes"] == 1000
    assert event["body"]["transferred_bytes"] == 250
    assert event["body"]["speed_bytes_per_second"] == 500


def test_build_file_event_rejects_unknown_type():
    try:
        build_file_command_event(
            request_id="req-3",
            event_type="unknown",
            device_id="desktop",
            body={},
        )
    except ValueError as exc:
        assert str(exc) == "invalid_file_event_type"
    else:
        raise AssertionError("expected invalid_file_event_type")


def test_build_file_control_events_are_protocol_compatible():
    for event_type in ("file_pause", "file_paused", "file_resume", "file_resumed", "file_cancel", "file_canceled"):
        event = build_file_command_event(
            request_id=f"req-{event_type}",
            event_type=event_type,
            device_id="phone",
            body={"file_id": "file-1", "transferred_bytes": "3"},
        )

        assert event["type"] == event_type
        assert event["body"]["file_id"] == "file-1"
        assert event["body"]["transferred_bytes"] == 3
