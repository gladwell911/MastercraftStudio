import asyncio
from chat_store import ChatStore

from remote_nats import RemoteNatsTransport


def test_transport_routes_scoped_chat_information_without_persistence():
    calls = []
    def read(payload):
        calls.append(payload)
        return 200, {"chat_id": "a", "rows": ["context", "total", "account", "quota"]}
    transport = RemoteNatsTransport(pair_id="default", token="secret", on_chat_information=read)
    payload = {"type": "chat_information", "chat_id": "a",
               "body": {"subscription_id": "page", "generation": 2}}
    status, response = transport._route_command(payload)
    assert status == 200 and response["chat_id"] == "a" and calls == [payload]


class FakeJetStream:
    def __init__(self):
        self.streams = []
        self.published = []

    async def add_stream(self, **kwargs):
        self.streams.append(kwargs)

    async def publish(self, subject, payload):
        self.published.append((subject, payload))

    async def stream_info(self, name):
        if not any(stream["name"] == name for stream in self.streams):
            raise ValueError("not_found")
        return {"name": name}

    async def update_stream(self, **kwargs):
        return kwargs


class FakeJetStreamExisting:
    def __init__(self):
        self.info_calls = []
        self.streams = []

    async def stream_info(self, name):
        self.info_calls.append(name)
        return {"name": name}

    async def add_stream(self, **kwargs):
        raise AssertionError(f"add_stream should not be called for {kwargs['name']}")

    async def update_stream(self, **kwargs):
        return kwargs


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
            "ZGWD_NOTIFY_default",
            "ZGWD_NOTIFY_default",
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
        on_common_commands_move_down=lambda payload: (
            200,
            {
                "accepted": True,
                "revision": 6,
                "commands": [{"id": payload["command_id"], "title": "Run Tests", "content": "pytest -q"}],
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
    move_down_status, move_down_body = transport._route_command(
        {"type": "common_commands_move_down", "command_id": "cmd-1"}
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
    assert move_down_status == 200
    assert move_down_body["commands"][0]["title"] == "Run Tests"


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


def test_transport_routes_common_commands_move_down_and_publishes_response():
    async def run():
        jetstream = FakeJetStream()
        transport = RemoteNatsTransport(
            pair_id="default",
            token="secret",
            jetstream=jetstream,
            on_common_commands_move_down=lambda payload: (
                200,
                {
                    "accepted": True,
                    "revision": 8,
                    "commands": [{"id": payload["command_id"], "title": "Second", "content": "echo second"}],
                    "result": "updated",
                },
            ),
        )

        await transport.handle_command(
            {
                "id": "common-move-down-1",
                "type": "common_commands_move_down",
                "command_id": "cmd-2",
            }
        )

        assert len(jetstream.published) == 1
        _, raw = jetstream.published[0]
        assert b'"request_id":"common-move-down-1"' in raw
        assert b'"revision":8' in raw
        assert b'"result":"updated"' in raw
        assert b'"commands":[{"id":"cmd-2"' in raw

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


def test_outbox_ack_loss_automatically_retries_beyond_five_identical_bytes(tmp_path):
    class AckLossJetStream(FakeJetStream):
        async def publish(self, subject, payload):
            self.published.append((subject, payload))
            if len(self.published) <= 6:
                raise RuntimeError("ack lost")
            return {"stream": "ok"}

    async def run():
        store = ChatStore(tmp_path / "outbox.db")
        store.initialize()
        first = store.commit_durable_fact(pair_id="default", domain="events", envelope={"event_id":"first","kind":"assistant_final","chat_id":"chat","body":{"n":1}})
        second = store.commit_durable_fact(pair_id="default", domain="events", envelope={"event_id":"second","kind":"assistant_final","chat_id":"chat","body":{"n":2}})
        jetstream = AckLossJetStream()
        transport = RemoteNatsTransport(pair_id="default", token="secret", jetstream=jetstream, durable_store=store)
        transport._outbox_delays["notification"] = 0.001
        assert await transport.drain_outbox() == 0
        assert store.get_checkpoint("publisher:default", "events") == 0
        for _ in range(200):
            if not store.pending_outbox(pair_id="default"):
                break
            await asyncio.sleep(0.005)
        assert store.pending_outbox(pair_id="default") == []
        assert len({raw for _, raw in jetstream.published[:7]}) == 1
        assert all(b'"event_id":"second"' not in raw for _, raw in jetstream.published[:7])
        assert store.get_checkpoint("publisher:default", "events") == second["sync_sequence"]
        with store._connect() as conn:
            row = conn.execute("SELECT * FROM publication_outbox WHERE event_id='first'").fetchone()
            assert row["attempts"] == 6 and row["blocked_reason"] is None
        await transport._close_async()

    asyncio.run(run())


def test_notification_publish_is_independent_of_hanging_other_lane(tmp_path):
    async def run():
        store = ChatStore(tmp_path / "lanes.db")
        store.initialize()
        first = store.commit_durable_fact(pair_id="default", domain="events", envelope={"event_id":"other","kind":"status","chat_id":"chat","body":{}})
        final = store.commit_durable_fact(pair_id="default", domain="events", envelope={"event_id":"final","kind":"assistant_final","chat_id":"chat","body":{}})
        entered, notified, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        class HangingJetStream(FakeJetStream):
            async def publish(self, subject, payload):
                if b'"event_id":"other"' in payload:
                    entered.set()
                    await release.wait()
                else:
                    notified.set()
                return {"stream":"ok"}
        transport = RemoteNatsTransport(pair_id="default", token="secret", jetstream=HangingJetStream(), durable_store=store)
        drain = asyncio.create_task(transport.drain_outbox())
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(notified.wait(), 1)
        assert [r["event_id"] for r in store.pending_outbox()] == ["other"]
        assert store.get_checkpoint("publisher:default", "events") == 0
        release.set()
        assert await drain == 2
        assert store.get_checkpoint("publisher:default", "events") == final["sync_sequence"]
        await transport._close_async()
    asyncio.run(run())



def test_mobile_shaped_hello_negotiates_per_session_and_v2_errors_echo_epoch(tmp_path):
    async def run():
        jetstream = FakeJetStream()
        store = ChatStore(tmp_path / "handshake.db")
        store.initialize()
        transport = RemoteNatsTransport(
            pair_id="default", token="secret", jetstream=jetstream, durable_store=store
        )
        await transport.handle_command({
            "id": "mobile-session-hello-1", "type": "hello", "device_id": "mobile",
            "protocol_versions": [2, 1], "session_id": "mobile-session",
            "body": {"protocol_versions": [2, 1], "session_id": "mobile-session"},
        })
        hello = __import__("json").loads(jetstream.published[-1][1])
        assert hello["body"]["protocol_version"] == 2
        assert "lightweight-readiness" in hello["body"]["capabilities"]
        assert "notification-feed-v1" in hello["body"]["capabilities"]
        assert hello["notification_feed_id"] == hello["body"]["notification_feed_id"] == store.get_meta("notification_feed_id")
        assert hello["body"]["sequence_domain"] == "events"
        assert hello["body"]["high_sync_sequence"] == 0
        epoch = hello["body"]["epoch"]
        assert epoch

        await transport.handle_command({
            "id": "bad-1", "request_id": "bad-1", "type": "state",
            "device_id": "mobile", "session_id": "mobile-session", "chat_id": "chat",
            "protocol_version": 2, "epoch": "wrong", "body": {},
        })
        error = __import__("json").loads(jetstream.published[-1][1])
        assert error["protocol_version"] == 2
        assert error["epoch"] == epoch
        assert error["request_id"] == "bad-1"
        assert error["chat_id"] == "chat"
        assert error["body"]["error"] == "STALE_EPOCH"

        # An unrelated session remains v1 and is not subjected to mobile-session's epoch.
        await transport.handle_command({"id":"legacy-1","type":"state","device_id":"other","session_id":"other","chat_id":"chat"})
        legacy = __import__("json").loads(jetstream.published[-1][1])
        assert "protocol_version" not in legacy

    asyncio.run(run())


def test_outbox_lanes_backlog_poison_and_restart_preserve_facts(tmp_path):
    async def run():
        path = tmp_path / "backlog.db"
        store = ChatStore(path)
        store.initialize()
        with store._connect() as conn:
            conn.execute("INSERT OR IGNORE INTO v2_feed_state(pair_id,domain,sync_sequence) VALUES('default','__pair__',73010)")
        for n in range(151):
            store.commit_durable_fact(pair_id="default", domain="files", envelope={"event_id":f"file-{n}","kind":"file_offer","chat_id":"chat","body":{"n":n}})
        for n in range(102):
            store.commit_durable_fact(pair_id="default", domain="events", envelope={"event_id":f"final-{n}","kind":"assistant_final","chat_id":"chat","body":{"n":n}})
        with store._connect() as conn:
            conn.execute("UPDATE publication_outbox SET blocked_reason='nats: timeout',attempts=5 WHERE event_id='file-0'")
            conn.execute("UPDATE publication_outbox SET payload=? WHERE event_id='final-0'", (b'invalid',))
            original = [(r["event_id"],bytes(r["payload"])) for r in conn.execute("SELECT * FROM publication_outbox ORDER BY sync_sequence")]
        assert len(store.pending_outbox(lane="notification")) == 100
        assert all(r["event_id"].startswith("file-") for r in store.pending_outbox(lane="other"))
        store = ChatStore(path)
        store.initialize()
        assert store.pending_outbox(lane="other")[0]["sync_sequence"] == 73011
        jetstream = FakeJetStream()
        transport = RemoteNatsTransport(pair_id="default", token="secret", jetstream=jetstream, durable_store=store)
        await transport.drain_outbox()
        await asyncio.gather(*transport._outbox_tasks.values())
        pending = store.pending_outbox()
        assert len(pending) == 1 and pending[0]["event_id"] == "final-0"
        assert pending[0]["blocked_reason"].startswith("permanent:")
        assert store.get_checkpoint("publisher:default", "events") == 0
        with store._connect() as conn:
            after = [(r["event_id"],bytes(r["payload"])) for r in conn.execute("SELECT * FROM publication_outbox ORDER BY sync_sequence")]
            assert original == after
            assert conn.execute("SELECT blocked_reason FROM publication_outbox WHERE event_id='file-0'").fetchone()[0] is None
        assert len(jetstream.published) == 252
        await transport._close_async()
        reopened = ChatStore(path)
        reopened.initialize()
        restarted_js = FakeJetStream()
        restarted = RemoteNatsTransport(pair_id="default", token="secret", jetstream=restarted_js, durable_store=reopened)
        assert await restarted.drain_outbox() == 0
        assert restarted_js.published == []
        await restarted._close_async()
    asyncio.run(run())


def test_other_mark_failure_and_shutdown_do_not_block_notifications(tmp_path):
    async def run():
        store = ChatStore(tmp_path / "mark.db")
        store.initialize()
        for kind in ("status", "assistant_final"):
            store.commit_durable_fact(pair_id="default", domain="events", envelope={"event_id":kind,"kind":kind,"chat_id":"chat","body":{}})
        mark = store.mark_outbox_acked
        def fail_other(sequence, **kwargs):
            if sequence == 1:
                raise RuntimeError("store unavailable for other")
            return mark(sequence, **kwargs)
        store.mark_outbox_acked = fail_other
        jetstream = FakeJetStream()
        transport = RemoteNatsTransport(pair_id="default", token="secret", jetstream=jetstream, durable_store=store)
        assert await transport.drain_outbox() == 1
        for _ in range(5):
            assert await transport.drain_outbox() == 0
        assert len(jetstream.published) == 2
        assert [r["event_id"] for r in store.pending_outbox()] == ["status"]
        tasks = list(transport._outbox_tasks.values())
        await transport._close_async()
        assert all(t.done() for t in tasks)
        assert await transport.drain_outbox() == 0
        assert len(jetstream.published) == 2
    asyncio.run(run())


def test_notification_enqueued_during_publish_drains_without_another_trigger(tmp_path):
    async def run():
        store = ChatStore(tmp_path / "wake.db")
        store.initialize()
        def enqueue(identity):
            store.commit_durable_fact(pair_id="default", domain="events", envelope={"event_id":identity,"kind":"assistant_final","chat_id":"chat","body":{}})
        enqueue("first")
        entered, release, second = asyncio.Event(), asyncio.Event(), asyncio.Event()
        class SuspendedJetStream(FakeJetStream):
            async def publish(self, subject, payload):
                self.published.append((subject, payload))
                if b'"event_id":"first"' in payload:
                    entered.set()
                    await release.wait()
                else:
                    second.set()
        js = SuspendedJetStream()
        transport = RemoteNatsTransport(pair_id="default", token="secret", jetstream=js, durable_store=store)
        initial = asyncio.create_task(transport.drain_outbox())
        await entered.wait()
        enqueue("second")
        await transport.drain_outbox()
        release.set()
        await initial
        await asyncio.wait_for(second.wait(), 1)
        assert store.pending_outbox() == []
        await transport._close_async()
    asyncio.run(run())


def test_success_resets_retry_delay_before_next_failure(tmp_path, monkeypatch):
    async def run():
        store = ChatStore(tmp_path / "delay.db")
        store.initialize()
        def enqueue(identity):
            store.commit_durable_fact(pair_id="default", domain="events", envelope={"event_id":identity,"kind":"assistant_final","chat_id":"chat","body":{}})
        enqueue("success")
        class FailingJetStream(FakeJetStream):
            async def publish(self, subject, payload):
                if b'"event_id":"failure"' in payload:
                    raise RuntimeError("temporary")
        transport = RemoteNatsTransport(pair_id="default", token="secret", jetstream=FailingJetStream(), durable_store=store)
        transport._outbox_delays["notification"] = 30
        await transport.drain_outbox()
        await asyncio.gather(*transport._outbox_tasks.values())
        assert transport._outbox_delays["notification"] == 0.25
        cooldown, release = asyncio.Event(), asyncio.Event()
        observed = []
        async def sleep(delay):
            observed.append(delay)
            cooldown.set()
            await release.wait()
        monkeypatch.setattr("remote_nats.asyncio.sleep", sleep)
        enqueue("failure")
        await transport.drain_outbox()
        await cooldown.wait()
        assert observed == [0.25]
        await transport._close_async()
    asyncio.run(run())


def test_other_lane_query_failure_does_not_block_notification(tmp_path):
    async def run():
        store = ChatStore(tmp_path / "query.db")
        store.initialize()
        store.commit_durable_fact(pair_id="default", domain="events", envelope={"event_id":"final","kind":"assistant_final","chat_id":"chat","body":{}})
        pending = store.pending_outbox
        def query(*args, **kwargs):
            if kwargs.get("lane") == "other":
                raise RuntimeError("other query unavailable")
            return pending(*args, **kwargs)
        store.pending_outbox = query
        js = FakeJetStream()
        transport = RemoteNatsTransport(pair_id="default", token="secret", jetstream=js, durable_store=store)
        assert await transport.drain_outbox() == 1
        assert len(js.published) == 1 and pending() == []
        await transport._close_async()
    asyncio.run(run())


def test_close_cancels_hanging_publish_without_ack(tmp_path):
    async def run():
        store = ChatStore(tmp_path / "cancel.db")
        store.initialize()
        store.commit_durable_fact(pair_id="default", domain="events", envelope={"event_id":"final","kind":"assistant_final","chat_id":"chat","body":{}})
        entered, blocked, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
        class HangingJetStream(FakeJetStream):
            async def publish(self, subject, payload):
                entered.set()
                try:
                    await blocked.wait()
                finally:
                    cancelled.set()
        transport = RemoteNatsTransport(pair_id="default", token="secret", jetstream=HangingJetStream(), durable_store=store)
        drain = asyncio.create_task(transport.drain_outbox())
        await entered.wait()
        tasks = list(transport._outbox_tasks.values())
        await asyncio.wait_for(transport._close_async(), 1)
        assert await drain == 0
        assert cancelled.is_set() and not blocked.is_set()
        assert all(task.done() for task in tasks)
        row = store.pending_outbox()[0]
        assert row["attempts"] == 0 and row["published_at"] is None
    asyncio.run(run())


def test_start_returns_without_waiting_for_hanging_outbox(tmp_path, monkeypatch):
    async def run():
        import nats
        store = ChatStore(tmp_path / "startup.db")
        store.initialize()
        store.upsert_chat({"id": "chat", "title": "Chat"})
        store.replace_turns("chat", [{"question": "q", "answer_md": "answer", "request_status": "done"}])
        target = store.readable_answer("chat", turn_index=0)
        store.commit_message_notification_fact(pair_id="default", domain="events", chat_id="chat",
            message_id=target["message_id"], notification_kind="assistant_final", text="answer", chat_title="Chat")
        entered, blocked = asyncio.Event(), asyncio.Event()
        class HangingJetStream(FakeJetStream):
            async def subscribe(self, *args, **kwargs):
                return None
            async def publish(self, subject, payload):
                entered.set()
                await blocked.wait()
        js = HangingJetStream()
        class Client:
            def jetstream(self):
                return js
            async def subscribe(self, *args, **kwargs):
                return None
            async def flush(self):
                return None
        async def connect(*args, **kwargs):
            return Client()
        monkeypatch.setattr(nats, "connect", connect)
        transport = RemoteNatsTransport(pair_id="default", token="secret", durable_store=store)
        await asyncio.wait_for(transport.start(), 1)
        await asyncio.wait_for(entered.wait(), 1)
        assert not blocked.is_set() and len(store.pending_outbox()) == 1
        await transport._close_async()
    asyncio.run(run())


def test_outbox_maintenance_is_bounded_scoped_disabled_and_off_event_thread(tmp_path, monkeypatch):
    import threading
    store = ChatStore(tmp_path / "maintenance.db")
    store.initialize()
    fact = store.commit_durable_fact(pair_id="pair", domain="events",
        envelope={"event_id": "old", "kind": "state_changed", "chat_id": "chat", "body": {}})
    store.mark_outbox_acked(fact["sync_sequence"], pair_id="pair", domain="events")
    with store._connect() as conn:
        conn.execute("UPDATE publication_outbox SET published_at=1")
    transport = RemoteNatsTransport(pair_id="pair", token="secret", durable_store=store)
    calls = []
    original = store.retain_publication_outbox
    def tracked(**kwargs):
        calls.append((threading.get_ident(), kwargs))
        return original(**kwargs)
    monkeypatch.setattr(store, "retain_publication_outbox", tracked)
    async def run():
        owner = threading.get_ident()
        monkeypatch.setenv("REMOTE_CONTROL_OUTBOX_RETENTION", "0")
        await transport._maintain_outbox()
        assert not calls
        monkeypatch.setenv("REMOTE_CONTROL_OUTBOX_RETENTION", "1")
        await transport._maintain_outbox()
        await transport._maintain_outbox()
        assert len(calls) == 1 and calls[0][0] != owner
        assert calls[0][1] == {"pair_id": "pair", "domain": "events", "dry_run": False}
    asyncio.run(run())
    assert store.retain_publication_outbox(pair_id="pair", domain="events")["eligible"] == 0


def test_automatic_retention_runs_through_real_outbox_workers(tmp_path, monkeypatch):
    store = ChatStore(tmp_path / "automatic-retention.db")
    store.initialize()
    old = store.commit_durable_fact(pair_id="default", domain="events",
        envelope={"event_id": "old-confirmed", "kind": "status", "chat_id": "chat", "body": {}})
    store.mark_outbox_acked(old["sync_sequence"], pair_id="default", domain="events")
    with store._connect() as conn:
        conn.execute("UPDATE publication_outbox SET published_at=1")
    monkeypatch.setenv("REMOTE_CONTROL_OUTBOX_RETENTION", "1")
    transport = RemoteNatsTransport(pair_id="default", token="secret", jetstream=FakeJetStream(), durable_store=store)
    async def run():
        await asyncio.wait_for(transport.drain_outbox(), 5)
        await asyncio.gather(*transport._outbox_tasks.values())
    asyncio.run(run())
    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM publication_outbox").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM durable_facts").fetchone()[0] == 1


def test_notification_arriving_during_maintenance_is_drained_before_workers_exit(tmp_path, monkeypatch):
    import threading
    store = ChatStore(tmp_path / "maintenance-race.db")
    store.initialize()
    old = store.commit_durable_fact(pair_id="default", domain="events",
        envelope={"event_id": "old", "kind": "status", "chat_id": "chat", "body": {}})
    store.mark_outbox_acked(old["sync_sequence"], pair_id="default", domain="events")
    with store._connect() as conn:
        conn.execute("UPDATE publication_outbox SET published_at=1")
    entered, release = threading.Event(), threading.Event()
    original = store.retain_publication_outbox
    def paused(**kwargs):
        entered.set()
        assert release.wait(5)
        return original(**kwargs)
    monkeypatch.setattr(store, "retain_publication_outbox", paused)
    monkeypatch.setenv("REMOTE_CONTROL_OUTBOX_RETENTION", "1")
    broker = FakeJetStream()
    transport = RemoteNatsTransport(pair_id="default", token="secret", jetstream=broker, durable_store=store)
    async def run():
        first = asyncio.create_task(transport.drain_outbox())
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            store.commit_durable_fact(pair_id="default", domain="events", envelope={
                "event_id": "new-final", "kind": "assistant_final", "chat_id": "chat", "body": {}})
            await asyncio.wait_for(transport.drain_outbox(), 5)
        finally:
            release.set()
        await asyncio.wait_for(first, 5)
        await asyncio.wait_for(asyncio.gather(*transport._outbox_tasks.values()), 5)
        assert store.pending_outbox(pair_id="default", lane="notification") == []
    asyncio.run(run())
    import json
    assert [json.loads(payload)["event_id"] for _, payload in broker.published] == ["new-final"]


def test_full_canonical_delivery_is_feed_hashed_without_changing_stored_fact(tmp_path):
    import json
    from remote_nats_protocol import canonical_durable_hash, validate_v2_durable

    async def run():
        for live in (False, True):
            store = ChatStore(tmp_path / f"wire-{live}.db")
            store.initialize()
            fact = store.commit_durable_fact(pair_id="default", domain="events", envelope={
                "event_id": "retained-clear", "kind": "clear_context", "chat_id": "chat", "body": {"generation": "g"}})
            original = store.pending_outbox()[0]["payload"]
            broker = FakeJetStream()
            transport = RemoteNatsTransport(pair_id="default", token="secret", jetstream=broker, durable_store=store)
            if live:
                transport._nats_client = broker
            await transport.drain_outbox()
            await asyncio.gather(*transport._outbox_tasks.values())
            subject, raw = next((subject, raw) for subject, raw in broker.published
                                if subject == (transport.subjects.hints if live else transport.subjects.events))
            wire = json.loads(raw)
            assert wire["notification_feed_id"] == store.get_meta("notification_feed_id")
            assert wire["fact_hash"] == fact["canonical_hash"]
            assert wire["canonical_hash"] == canonical_durable_hash(wire)
            assert wire["body"] == fact["body"]
            validate_v2_durable(wire)
            import pytest
            with pytest.raises(ValueError, match="CANONICAL_HASH_MISMATCH"):
                validate_v2_durable({**wire, "notification_feed_id": "retired-feed"})
            with store._connect() as conn:
                assert conn.execute("SELECT payload FROM publication_outbox WHERE event_id='retained-clear'").fetchone()[0] == original
            assert "notification_feed_id" not in json.loads(original)
    asyncio.run(run())


def test_actual_transport_feed_sync_and_read_write_authority(tmp_path):
    async def run():
        import json
        store = ChatStore(tmp_path / "feed-wire.db")
        store.initialize()
        store.upsert_chat({"id": "chat", "title": "Feed"})
        store.replace_turns("chat", [{"question": "old", "answer_md": "old answer", "request_status": "done"}])
        store.notification_sync(pair_id="default", device_id="mobile", baseline=186475,
                                incarnation="old", secret="secret")
        with store._connect() as conn:
            conn.execute("UPDATE v2_feed_state SET sync_sequence=8140 WHERE pair_id='default' AND domain='__pair__'")
        jetstream = FakeJetStream()
        transport = RemoteNatsTransport(pair_id="default", token="secret", jetstream=jetstream, durable_store=store)
        transport.notification_incarnation = "broker-created"
        await transport.handle_command({"id": "hello", "type": "hello", "device_id": "mobile",
            "session_id": "session", "protocol_versions": [2, 1],
            "body": {"session_id": "session", "protocol_versions": [2, 1]}})
        hello = json.loads(jetstream.published[-1][1])["body"]
        feed, epoch = hello["notification_feed_id"], hello["epoch"]
        assert "notification-feed-v1" in hello["capabilities"]
        assert hello["high_sync_sequence"] == 8140
        async def rpc(kind, body):
            await transport.handle_command({"id": kind, "request_id": kind, "type": kind,
                "device_id": "mobile", "session_id": "session", "protocol_version": 2,
                "epoch": epoch, "chat_id": "chat", "body": body})
            return json.loads(jetstream.published[-1][1])["body"]
        scope = {"incarnation": transport.notification_incarnation, "notification_feed_id": feed}
        first = await rpc("notification_sync", {**scope, "baseline": 8140})
        assert first.get("notification_feed_id") == feed and first["baseline"] == 8140, first
        assert not any(r.get("kind") == "assistant_final" for r in first["records"])
        store.replace_turns("chat", [{"question": "old", "answer_md": "old answer", "request_status": "done"},
                                     {"question": "new", "answer_md": "new answer", "request_status": "done"}])
        recovered = await rpc("notification_sync", {**scope, "baseline": 186475})
        finals = [r for r in recovered["records"] if r.get("kind") == "assistant_final"]
        assert len(finals) == 1 and finals[0]["notification_feed_id"] == feed
        receipt = recovered["completion_token"]
        stale = await rpc("notification_sync_complete", {**scope, "notification_feed_id": "retired", "completion_token": receipt})
        assert stale["error"] == "STALE_NOTIFICATION_FEED"
        completed = await rpc("notification_sync_complete", {**scope, "completion_token": receipt})
        assert completed["processed_sequence"] == recovered["high_water"]
        with store._connect() as conn:
            row = conn.execute("SELECT baseline,processed_sequence FROM notification_feed_devices WHERE pair_id='default' AND device_id='mobile' AND feed_id=?", (feed,)).fetchone()
            assert tuple(row) == (8140, recovered["high_water"])
        replay = await rpc("notification_sync", {**scope, "baseline": 0})
        assert not any(r.get("kind") == "assistant_final" for r in replay["records"])
        bad_receipt = await rpc("notification_sync_complete", {**scope, "completion_token": receipt + "tampered"})
        assert "error" in bad_receipt
        target = store.readable_answer("chat", turn_index=1)
        before = store.get_chat_read_state("chat", pair_id="default")
        rejected = await rpc("mark_chat_read", {**target, "notification_feed_id": "retired", "operation_id": "stale"})
        assert rejected["error"] == "STALE_NOTIFICATION_FEED"
        assert store.get_chat_read_state("chat", pair_id="default") == before
        accepted = await rpc("mark_chat_read", {**target, "notification_feed_id": feed, "operation_id": "current"})
        assert accepted["accepted"] and accepted["read_state"]["read_seq"] == target["answer_seq"]
        legacy = await rpc("mark_chat_read", {**target, "operation_id": "legacy"})
        assert legacy["accepted"]
        await transport._close_async()
    asyncio.run(run())
