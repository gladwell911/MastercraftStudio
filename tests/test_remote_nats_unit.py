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


def test_routes_common_commands_list_command():
    transport = RemoteNatsTransport(
        pair_id="default",
        token="token",
        on_common_commands_list=lambda: (
            200,
            {
                "accepted": True,
                "revision": 3,
                "commands": [{"id": "cmd-1", "title": "List Files", "content": "dir"}],
            },
        ),
    )

    status, body = transport._route_command({"type": "common_commands_list"})

    assert status == 200
    assert body == {
        "accepted": True,
        "revision": 3,
        "commands": [{"id": "cmd-1", "title": "List Files", "content": "dir"}],
    }


def test_routes_common_commands_mutation_commands():
    transport = RemoteNatsTransport(
        pair_id="default",
        token="token",
        on_common_commands_create=lambda payload: (
            200,
            {
                "accepted": True,
                "revision": 1,
                "commands": [{"id": "cmd-1", "title": payload["title"], "content": payload["content"]}],
            },
        ),
        on_common_commands_update=lambda payload: (
            200,
            {
                "accepted": True,
                "revision": 2,
                "commands": [{"id": payload["id"], "title": payload["title"], "content": payload["content"]}],
            },
        ),
        on_common_commands_delete=lambda payload: (
            200,
            {
                "accepted": True,
                "revision": 3,
                "commands": [],
            },
        ),
        on_common_commands_pin=lambda payload: (
            200,
            {
                "accepted": True,
                "revision": 4,
                "commands": [{"id": payload["id"], "title": "Run Tests", "content": "pytest -q", "pinned": True}],
            },
        ),
        on_common_commands_move_up=lambda payload: (
            200,
            {
                "accepted": True,
                "revision": 5,
                "commands": [{"id": payload["id"], "title": "Run Tests", "content": "pytest -q"}],
            },
        ),
    )

    create_status, create_body = transport._route_command(
        {"type": "common_commands_create", "title": "List Files", "content": "dir"}
    )
    update_status, update_body = transport._route_command(
        {"type": "common_commands_update", "id": "cmd-1", "title": "Run Tests", "content": "pytest -q"}
    )
    delete_status, delete_body = transport._route_command(
        {"type": "common_commands_delete", "id": "cmd-1"}
    )
    pin_status, pin_body = transport._route_command(
        {"type": "common_commands_pin", "id": "cmd-1"}
    )
    move_status, move_body = transport._route_command(
        {"type": "common_commands_move_up", "id": "cmd-1"}
    )

    assert create_status == 200
    assert create_body["commands"][0]["title"] == "List Files"
    assert update_status == 200
    assert update_body["commands"][0]["title"] == "Run Tests"
    assert delete_status == 200
    assert delete_body == {"accepted": True, "revision": 3, "commands": []}
    assert pin_status == 200
    assert pin_body["commands"][0]["pinned"] is True
    assert move_status == 200
    assert move_body["commands"][0]["title"] == "Run Tests"


def test_routes_speed_options_and_set_speed_commands():
    routed = []
    transport = RemoteNatsTransport(
        pair_id="default",
        token="token",
        on_speed_options=lambda payload: (
            200,
            {
                "accepted": True,
                "chat_id": payload.get("chat_id"),
                "codex_service_tier": "standard",
                "codex_service_tier_options": [
                    {"value": "standard", "label": "标准"},
                    {"value": "fast", "label": "快速"},
                ],
            },
        ),
        on_set_speed=lambda payload: (
            routed.append(payload)
            or (
                200,
                {
                    "accepted": True,
                    "chat_id": payload.get("chat_id"),
                    "codex_service_tier": payload.get("codex_service_tier"),
                },
            )
        ),
    )

    options_status, options_body = transport._route_command(
        {"type": "speed_options", "chat_id": "chat-1"}
    )
    set_status, set_body = transport._route_command(
        {"type": "set_speed", "chat_id": "chat-1", "codex_service_tier": "fast"}
    )

    assert options_status == 200
    assert options_body["codex_service_tier_options"][1] == {
        "value": "fast",
        "label": "快速",
    }
    assert set_status == 200
    assert set_body["codex_service_tier"] == "fast"
    assert routed == [
        {"type": "set_speed", "chat_id": "chat-1", "codex_service_tier": "fast"}
    ]


def test_routes_clear_context_command():
    routed = []
    transport = RemoteNatsTransport(
        pair_id="default",
        token="token",
        on_clear_context=lambda payload: (
            routed.append(payload)
            or (
                200,
                {
                    "accepted": True,
                    "chat_id": payload.get("chat_id"),
                },
            )
        ),
    )

    status, body = transport._route_command(
        {"type": "clear_context", "chat_id": "chat-1"}
    )

    assert status == 200
    assert body == {"accepted": True, "chat_id": "chat-1"}
    assert routed == [{"type": "clear_context", "chat_id": "chat-1"}]


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


def test_transport_routes_common_commands_list_and_publishes_response():
    async def run():
        jetstream = FakeJetStream()
        transport = RemoteNatsTransport(
            pair_id="default",
            token="secret",
            jetstream=jetstream,
            on_common_commands_list=lambda: (
                200,
                {
                    "accepted": True,
                    "revision": 7,
                    "commands": [{"id": "cmd-1", "title": "List Files", "content": "dir"}],
                },
            ),
        )

        await transport.handle_command({"id": "common-1", "type": "common_commands_list"})

        assert len(jetstream.published) == 1
        _, raw = jetstream.published[0]
        assert b'"request_id":"common-1"' in raw
        assert b'"revision":7' in raw
        assert b'"commands":[{"id":"cmd-1"' in raw

    asyncio.run(run())


def test_transport_routes_common_commands_update_and_publishes_stale_response():
    async def run():
        jetstream = FakeJetStream()
        transport = RemoteNatsTransport(
            pair_id="default",
            token="secret",
            jetstream=jetstream,
            on_common_commands_update=lambda payload: (
                409,
                {
                    "accepted": False,
                    "error": "stale_state",
                    "current_revision": 2,
                    "observed_revision": payload.get("observed_revision"),
                },
            ),
        )

        await transport.handle_command(
            {
                "id": "common-update-1",
                "type": "common_commands_update",
                "observed_revision": 1,
            }
        )

        assert len(jetstream.published) == 1
        _, raw = jetstream.published[0]
        assert b'"request_id":"common-update-1"' in raw
        assert b'"status":409' in raw
        assert b'"error":"stale_state"' in raw

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


def test_transport_publishes_file_events_to_files_subject():
    async def run():
        jetstream = FakeJetStream()
        transport = RemoteNatsTransport(
            pair_id="default",
            token="secret",
            jetstream=jetstream,
        )

        await transport.publish_event({"type": "file_offer", "chat_id": "c1"})
        await transport.publish_event({"type": "state", "chat_id": "c1"})

        assert jetstream.published[0][0] == "zgwd.default.files"
        assert jetstream.published[1][0] == "zgwd.default.events"

    asyncio.run(run())


def test_transport_routes_file_commands_to_file_callback():
    seen = []
    transport = RemoteNatsTransport(
        pair_id="default",
        token="secret",
        on_file_command=lambda payload: seen.append(payload) or (200, {"accepted": True}),
    )

    status, body = transport._route_command({"type": "file_accept", "body": {"file_id": "file-1"}})

    assert status == 200
    assert body == {"accepted": True}
    assert seen == [{"type": "file_accept", "body": {"file_id": "file-1"}}]
