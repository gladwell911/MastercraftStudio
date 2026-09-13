import json
import asyncio
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


def test_choose_available_port_uses_ephemeral_fallback_when_candidates_are_busy(monkeypatch):
    monkeypatch.setattr(harness, "_can_bind_loopback_tcp_port", lambda _port: False)
    monkeypatch.setattr(harness, "_allocate_ephemeral_loopback_port", lambda: 49152)

    assert harness._choose_available_port(4222, (4223, 4224)) == 49152


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


def test_cross_client_state_serves_seed_history_and_model_specific_answers(tmp_path):
    durable_store = harness.ChatStore(tmp_path / "harness.db")
    durable_store.initialize()
    result_file = tmp_path / "result.json"
    state = harness.CrossClientHarnessState(
        durable_store=durable_store,
        pair_id="regression",
        seed_title="desktop seed run-1",
        codex_reply="CODEX_run-1",
        kimi_reply="KIMI_run-1",
        result_file=result_file,
    )

    status, history = state.history_list()
    assert status == 200
    assert history["chats"][0]["title"] == "desktop seed run-1"
    assert state.model_list()[1]["models"] == [
        {"id": "codex/main", "label": "Codex"},
        {"id": "kimi/main", "label": "Kimi Code"},
    ]

    for model, marker in (("codex/main", "CODEX_run-1"), ("kimi/main", "KIMI_run-1")):
        status, created = state.new_chat({"model": model})
        assert status == 200
        chat_id = created["chat_id"]
        status, response = state.message(
            {"chat_id": chat_id, "model": model, "text": f"prompt for {model}"}
        )
        assert status == 200
        assert response["model"] == model
        status, snapshot = state.state({"chat_id": chat_id})
        assert status == 200
        assert snapshot["turns"][0]["answer"] == marker

    evidence = json.loads(result_file.read_text(encoding="utf-8"))
    assert [item["model"] for item in evidence["messages"]] == [
        "codex/main",
        "kimi/main",
    ]
    pending = durable_store.pending_outbox(pair_id="regression")
    assert [row["event_id"].endswith(":assistant_final") for row in pending] == [
        True,
        True,
    ]


def test_cross_client_transport_negotiates_strict_v2(tmp_path):
    class _JetStream:
        def __init__(self):
            self.payloads = []

        async def publish(self, _subject, payload):
            self.payloads.append(json.loads(bytes(payload).decode("utf-8")))
            return object()

    durable_store = harness.ChatStore(tmp_path / "v2.db")
    durable_store.initialize()
    transport = harness.RemoteNatsTransport(
        pair_id="regression",
        token="test-token",
        durable_store=durable_store,
    )
    transport.jetstream = _JetStream()

    asyncio.run(
        transport.handle_command(
            {
                "id": "hello-1",
                "type": "hello",
                "device_id": "emulator",
                "session_id": "session-1",
                "protocol_versions": [2, 1],
            }
        )
    )

    response = transport.jetstream.payloads[-1]
    assert response["body"]["protocol_version"] == 2
    assert response["body"]["epoch"]
    assert response["body"]["sequence_domain"] == "events"
