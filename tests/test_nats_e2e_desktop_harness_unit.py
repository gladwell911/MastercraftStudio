import json
from pathlib import Path

from scripts import nats_e2e_desktop_harness as harness


def test_can_bind_loopback_tcp_port_returns_false_when_port_accepts_connections(monkeypatch):
    class _ConnectedSocket:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(harness.socket, "create_connection", lambda *args, **kwargs: _ConnectedSocket())

    assert harness._can_bind_loopback_tcp_port(4222) is False


def test_resolve_runtime_ports_falls_back_when_default_tcp_port_is_busy(monkeypatch):
    monkeypatch.setattr(
        harness,
        "_can_bind_loopback_tcp_port",
        lambda port: port != 4222,
    )

    tcp_port, websocket_port = harness.resolve_runtime_ports(
        preferred_port=4222,
        preferred_ws_port=8081,
    )

    assert tcp_port == 4223
    assert websocket_port == 8081


def test_write_ready_file_persists_runtime_ports_and_endpoint(tmp_path):
    ready_file = tmp_path / "ready.json"

    harness.write_ready_file(
        ready_file,
        tcp_port=4223,
        websocket_port=8082,
        token="test-token",
        pair_id="default",
    )

    payload = json.loads(ready_file.read_text(encoding="utf-8"))
    assert payload == {
        "tcp_port": 4223,
        "websocket_port": 8082,
        "endpoint": "ws://127.0.0.1:8082/nats",
        "token": "test-token",
        "pair_id": "default",
    }


def test_notes_bulk_docs_round_trip_through_in_memory_harness_store():
    store = harness.InMemoryNotesHarnessStore()

    status, body = store.bulk_docs(
        {
            "docs": [
                {
                    "_id": "notebook:one",
                    "type": "notebook",
                    "title": "desktop note",
                },
                {
                    "_id": "entry:one",
                    "type": "entry",
                    "notebook_id": "notebook:one",
                    "content": "hello desktop",
                },
            ]
        }
    )

    assert status == 201
    assert [item["id"] for item in body["results"]] == ["notebook:one", "entry:one"]

    status, body = store.changes({"since": "0", "include_docs": True})

    assert status == 200
    assert body["last_seq"] == "2"
    docs = [row["doc"] for row in body["results"]]
    assert any(item["_id"] == "notebook:one" for item in docs)
    assert any(item["_id"] == "entry:one" for item in docs)
