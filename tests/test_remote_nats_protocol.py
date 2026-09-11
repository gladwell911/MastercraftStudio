from dataclasses import FrozenInstanceError
import json
from pathlib import Path

import pytest

from remote_nats_protocol import (
    DEFAULT_PAIR_ID,
    NatsSubjects,
    build_error_response,
    build_response_event,
    decode_payload,
    encode_payload,
    make_event_id,
    normalize_pair_id,
    validate_v2_durable,
    validate_v2_ephemeral,
)


def test_nats_subjects_from_pair_id_normalizes_pair_and_builds_names():
    subjects = NatsSubjects.from_pair_id("Phone 1")

    assert subjects.pair_id == "phone-1"
    assert subjects.commands == "zgwd.phone-1.commands"
    assert subjects.events == "zgwd.phone-1.events"
    assert subjects.files == "zgwd.phone-1.files"
    assert subjects.command_stream == "ZGWD_COMMANDS_phone_1"
    assert subjects.event_stream == "ZGWD_EVENTS_phone_1"


def test_nats_subjects_are_frozen():
    subjects = NatsSubjects.from_pair_id("phone-1")

    with pytest.raises(FrozenInstanceError):
        subjects.pair_id = "other"


def test_normalize_pair_id_falls_back_drops_non_ascii_and_cleans_separators():
    assert normalize_pair_id("") == DEFAULT_PAIR_ID
    assert normalize_pair_id("\u4e2d\u6587 pair") == "pair"
    assert normalize_pair_id("Phone 1!") == "phone-1"
    assert normalize_pair_id("phone---__one") == "phone-one"
    assert normalize_pair_id("_-phone-1-_") == "phone-1"


def test_payload_encoding_round_trips_dicts_and_preserves_utf8_text():
    payload = {"accepted": True, "text": "\u4e2d\u6587"}
    encoded = encode_payload(payload)

    assert b"\xe4\xb8\xad\xe6\x96\x87" in encoded
    assert b"\\u4e2d" not in encoded
    assert decode_payload(encoded) == payload


def test_decode_payload_reports_invalid_json_and_invalid_payload():
    with pytest.raises(ValueError, match="invalid_json"):
        decode_payload(b"{bad")
    with pytest.raises(ValueError, match="invalid_payload"):
        decode_payload(b"[]")


def test_build_response_event_includes_request_status_body_and_chat():
    event = build_response_event(
        request_id="state-1",
        status=200,
        body={"accepted": True, "chat_id": "c1"},
        chat_id="c1",
    )

    assert event["type"] == "response"
    assert event["request_id"] == "state-1"
    assert event["event_id"] == "response-state-1"
    assert event["ok"] is True
    assert event["chat_id"] == "c1"


def test_build_error_response_sets_error_body_and_failed_status():
    event = build_error_response("bad-1", 400, "invalid_payload")

    assert event["type"] == "response"
    assert event["request_id"] == "bad-1"
    assert event["ok"] is False
    assert event["status"] == 400
    assert event["body"]["error"] == "invalid_payload"


def test_make_event_id_returns_unique_prefixed_ids():
    first = make_event_id("state")
    second = make_event_id("state")

    assert first.startswith("state-")
    assert second.startswith("state-")
    assert first != second


def test_shared_v2_contract_matrix_matches_python_validator():
    matrix = json.loads((Path(__file__).parent / "fixtures" / "remote_protocol_v2_contract_matrix.json").read_text(encoding="utf-8"))
    for row in matrix["durable"]:
        if row["valid"]:
            assert validate_v2_durable(row["payload"])["chat_id"]
        else:
            with pytest.raises(ValueError, match=row["error"]):
                validate_v2_durable(row["payload"])
    for row in matrix["ephemeral"]:
        if row["valid"]:
            assert validate_v2_ephemeral(row["payload"], expected_epoch="epoch-1")["request_id"]
        else:
            with pytest.raises(ValueError, match=row["error"]):
                validate_v2_ephemeral(row["payload"], expected_epoch="epoch-1")
def test_execution_routes_delegate_to_authority_and_structure_errors():
    from remote_nats import RemoteNatsTransport

    class Store:
        def __init__(self): self.calls = []
        def load_execution_page(self, **kwargs):
            self.calls.append(("page", kwargs))
            return {"entries": [], "revision": 1, "sequence_domain": "events", "snapshot_high": 0}
        def load_execution_range(self, **kwargs):
            self.calls.append(("range", kwargs))
            return {"entries": [], "revision": 1}

    store = Store()
    transport = RemoteNatsTransport(pair_id="pair", token="secret", durable_store=store)
    for command in ("execution_tail", "execution_history", "execution_snapshot"):
        status, body = transport._route_command({"type": command, "chat_id": "chat", "limit": 10,
                                                  "cursor": "opaque"})
        assert status == 200 and body["revision"] == 1
    status, body = transport._route_command({"type": "execution_backfill", "chat_id": "chat",
                                              "revision": 1, "from_sequence": 2, "to_sequence": 3})
    assert status == 200 and body["revision"] == 1
    assert [call[0] for call in store.calls] == ["page", "page", "page", "range"]

    status, body = transport._route_command({"type": "execution_backfill", "chat_id": "chat",
                                              "revision": {}, "from_sequence": 2, "to_sequence": 3})
    assert status == 409 and body["recovery"] == "SNAPSHOT_REQUIRED"
