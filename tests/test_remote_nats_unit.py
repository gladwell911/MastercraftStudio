import asyncio

from remote_nats import RemoteNatsTransport


class FakeJetStream:
    def __init__(self):
        self.streams = []
        self.published = []

    async def add_stream(self, **kwargs):
        self.streams.append(kwargs)

    async def publish(self, subject, payload):
        self.published.append((subject, payload))


class FakeJetStreamExisting:
    def __init__(self):
        self.info_calls = []
        self.streams = []

    async def stream_info(self, name):
        self.info_calls.append(name)
        return {"name": name}

    async def add_stream(self, **kwargs):
        raise AssertionError(f"add_stream should not be called for {kwargs['name']}")


def test_transport_initializes_streams():
    async def run():
        jetstream = FakeJetStream()
        transport = RemoteNatsTransport(
            pair_id="default",
            token="secret",
            jetstream=jetstream,
        )

        await transport.initialize_streams()

        assert jetstream.streams[0]["name"] == "ZGWD_COMMANDS_default"
        assert jetstream.streams[1]["name"] == "ZGWD_EVENTS_default"

    asyncio.run(run())


def test_transport_initialize_streams_skips_existing_streams():
    async def run():
        jetstream = FakeJetStreamExisting()
        transport = RemoteNatsTransport(
            pair_id="default",
            token="secret",
            jetstream=jetstream,
        )

        await transport.initialize_streams()

        assert jetstream.info_calls == [
            "ZGWD_COMMANDS_default",
            "ZGWD_EVENTS_default",
        ]
        assert jetstream.streams == []

    asyncio.run(run())


def test_transport_routes_state_command_and_publishes_response():
    async def run():
        jetstream = FakeJetStream()
        transport = RemoteNatsTransport(
            pair_id="default",
            token="secret",
            jetstream=jetstream,
            on_state=lambda payload: (
                200,
                {
                    "accepted": True,
                    "status": "idle",
                    "chat_id": payload.get("chat_id"),
                },
            ),
        )

        await transport.handle_command({"id": "state-1", "type": "state", "chat_id": "c1"})

        assert len(jetstream.published) == 1
        subject, raw = jetstream.published[0]
        assert subject == "zgwd.default.events"
        assert b'"request_id":"state-1"' in raw
        assert b'"status":"idle"' in raw

    asyncio.run(run())


def test_routes_model_list_command():
    transport = RemoteNatsTransport(
        pair_id="default",
        token="token",
        on_model_list=lambda: (
            200,
            {"accepted": True, "models": [{"id": "codex/main", "label": "codex"}]},
        ),
    )

    status, body = transport._route_command({"type": "model_list"})

    assert status == 200
    assert body == {
        "accepted": True,
        "models": [{"id": "codex/main", "label": "codex"}],
    }


def test_transport_routes_notes_changes_command_and_publishes_response():
    async def run():
        jetstream = FakeJetStream()
        transport = RemoteNatsTransport(
            pair_id="default",
            token="secret",
            jetstream=jetstream,
            on_notes_changes=lambda payload: (
                200,
                {
                    "results": [],
                    "last_seq": payload.get("since", "0"),
                },
            ),
        )

        await transport.handle_command(
            {"id": "notes-1", "type": "notes_changes", "since": "7"}
        )

        assert len(jetstream.published) == 1
        _, raw = jetstream.published[0]
        assert b'"request_id":"notes-1"' in raw
        assert b'"last_seq":"7"' in raw

    asyncio.run(run())


def test_transport_routes_notes_bulk_docs_command_and_publishes_response():
    async def run():
        jetstream = FakeJetStream()
        transport = RemoteNatsTransport(
            pair_id="default",
            token="secret",
            jetstream=jetstream,
            on_notes_bulk_docs=lambda payload: (
                201,
                {
                    "results": [
                        {"id": doc["_id"], "ok": True, "rev": "1-local"}
                        for doc in payload.get("docs", [])
                    ],
                },
            ),
        )

        await transport.handle_command(
            {
                "id": "notes-2",
                "type": "notes_bulk_docs",
                "docs": [{"_id": "notebook:abc"}],
            }
        )

        assert len(jetstream.published) == 1
        _, raw = jetstream.published[0]
        assert b'"request_id":"notes-2"' in raw
        assert b'"status":201' in raw
        assert b'"results":[{"id":"notebook:abc"' in raw

    asyncio.run(run())


def test_transport_invokes_callbacks_through_configured_invoker():
    async def run():
        calls = []
        jetstream = FakeJetStream()

        def invoke(callback):
            calls.append("invoked")
            return callback()

        transport = RemoteNatsTransport(
            pair_id="default",
            token="secret",
            jetstream=jetstream,
            invoke_callback=invoke,
            on_state=lambda payload: (200, {"accepted": True}),
        )

        await transport.handle_command({"id": "state-1", "type": "state"})

        assert calls == ["invoked"]
        assert len(jetstream.published) == 1

    asyncio.run(run())


def test_transport_publishes_push_event():
    async def run():
        jetstream = FakeJetStream()
        transport = RemoteNatsTransport(
            pair_id="default",
            token="secret",
            jetstream=jetstream,
        )

        await transport.publish_event({"type": "history_changed", "chat_id": "c1"})

        assert jetstream.published[0][0] == "zgwd.default.events"
        assert b'"event_id":"history_changed-' in jetstream.published[0][1]

    asyncio.run(run())


def test_transport_publish_event_threadsafe_returns_false_without_running_loop():
    jetstream = FakeJetStream()
    transport = RemoteNatsTransport(
        pair_id="default",
        token="secret",
        jetstream=jetstream,
    )

    scheduled = transport.publish_event_threadsafe({"type": "state", "chat_id": "c1"})

    assert scheduled is False
    assert jetstream.published == []


def test_transport_publish_event_threadsafe_schedules_on_running_loop():
    async def run():
        jetstream = FakeJetStream()
        transport = RemoteNatsTransport(
            pair_id="default",
            token="secret",
            jetstream=jetstream,
        )

        scheduled = transport.publish_event_threadsafe({"type": "state", "chat_id": "c1"})
        await asyncio.sleep(0)

        assert scheduled is True
        assert jetstream.published[0][0] == "zgwd.default.events"
        assert b'"type":"state"' in jetstream.published[0][1]
        assert b'"chat_id":"c1"' in jetstream.published[0][1]

    asyncio.run(run())
