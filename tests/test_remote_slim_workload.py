"""Actual broker/socket byte comparison for a deterministic ten-minute trace.

The logical 600-second trace is replayed without wall-clock sleeps. This is an
isolated transport benchmark, not a public-path latency or long-soak claim.
"""
import asyncio
import json
from pathlib import Path
import secrets
import socket
import subprocess

import nats
import pytest

from chat_store import ChatStore
from nats_runtime import NatsRuntimeConfig, NatsServerProcess
from remote_nats import RemoteNatsTransport
from remote_nats_protocol import canonical_durable_hash, compact_notification, encode_payload, rpc_reply_subject


BASELINE = "cded38d5b39820726316debed6ee41eb973542cf"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def seed(directory):
    store = ChatStore(directory / "chats.db")
    store.initialize()
    for chat in range(100):
        store.upsert_chat({"id": f"chat-{chat}", "title": f"Chat {chat}"})
        store.replace_turns(f"chat-{chat}", [{"question": f"question-{turn}",
            "answer_md": "answer " + "a" * 1024, "request_status": "done"} for turn in range(100)])
    with store._connect() as conn:
        conn.execute("INSERT INTO v2_feed_state(pair_id,domain,sync_sequence) VALUES('fixture','__pair__',50000)")
        for first in range(0, 50000, 500):
            facts, outbox = [], []
            for index in range(first, first + 500):
                event = {"protocol_version": 2, "event_id": f"step-{index}", "kind": "execution_entry",
                    "chat_id": f"chat-{index % 100}", "domain": "events", "sequence_domain": "events",
                    "origin_client": "mc", "revision": 1, "sync_sequence": index + 1,
                    "execution_sequence": index // 100 + 1,
                    "body": {"kind": "tool", "title": f"Step {index}", "detail": "step " + "x" * 8192}}
                event["canonical_hash"] = canonical_durable_hash(event)
                raw = encode_payload(event)
                facts.append((event["event_id"], event["canonical_hash"], "execution_entry", "fixture", "events",
                    event["chat_id"], 1, index + 1, event["execution_sequence"], raw.decode()))
                outbox.append(("fixture", "events", index + 1, event["event_id"], "events", raw))
            conn.executemany("INSERT INTO durable_facts(event_id,canonical_hash,kind,pair_id,domain,chat_id,revision,sync_sequence,execution_sequence,envelope_json) VALUES(?,?,?,?,?,?,?,?,?,?)", facts)
            conn.executemany("INSERT INTO publication_outbox(pair_id,domain,sync_sequence,event_id,subject_domain,payload) VALUES(?,?,?,?,?,?)", outbox)
    for chat in range(20):
        target = store.readable_answer(f"chat-{chat}", turn_index=99)
        store.commit_message_notification_fact(pair_id="fixture", domain="events", chat_id=f"chat-{chat}",
            message_id=target["message_id"], notification_kind="assistant_final", text="", chat_title="")
    return store


async def measure(config, transport_class, store, slim, frame):
    import main
    import ast
    import types
    frame.chat_store = store
    frame._chat_store_enabled = True
    frame.active_chat_id = frame.current_chat_id = ""
    frame._current_chat_state = {}
    frame.active_session_turns = []
    frame.archived_chats = store.list_chat_summaries()
    original_page = frame._remote_chat_snapshot_page_from_store
    if not slim:
        # Execute the frozen baseline paging handler, backed by the same real store.
        source = subprocess.check_output(["git", "show", f"{BASELINE}:main.py"], cwd=Path(__file__).resolve().parents[1]).decode("utf-8")
        tree = ast.parse(source)
        method = next(node for cls in tree.body if isinstance(cls, ast.ClassDef) and cls.name == "ChatFrame"
            for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "_remote_chat_snapshot_page_from_store")
        namespace = {**vars(main), "REMOTE_STATE_DEFAULT_TURN_LIMIT": 100}
        exec(compile(ast.Module(body=[method], type_ignores=[]), "baseline_main_handler", "exec"), namespace)
        frame._remote_chat_snapshot_page_from_store = types.MethodType(namespace[method.name], frame)
    kwargs = {"pair_id": "fixture", "token": config.token,
        "on_history_list": lambda: frame._remote_api_history_list_ui(),
        "on_history_read": frame._remote_api_history_read_ui}
    if slim:
        kwargs["on_history_page"] = frame._remote_api_history_collection_worker
    if slim:
        kwargs["desktop_password"] = config.desktop_password
    transport = transport_class(**kwargs)
    proxy_tasks = set()
    wire = {"sent": 0, "received": 0}
    async def proxy(reader, writer):
        task = asyncio.current_task()
        proxy_tasks.add(task)
        remote_writer = None
        try:
            remote_reader, remote_writer = await asyncio.open_connection("127.0.0.1", config.port)
            async def copy(source, target, field):
                while data := await source.read(65536):
                    wire[field] += len(data)
                    target.write(data)
                    await target.drain()
                target.close()
            await asyncio.gather(copy(reader, remote_writer, "sent"), copy(remote_reader, writer, "received"))
        finally:
            writer.close()
            if remote_writer is not None:
                remote_writer.close()
            proxy_tasks.discard(task)
    server = mobile = None
    payload_bytes, received = 0, 0
    async def receive(message):
        nonlocal payload_bytes, received
        payload_bytes += len(message.data)
        received += 1
    try:
        await transport.start(f"nats://127.0.0.1:{config.port}")
        # Seed retained transport facts before connecting the measured phone.
        # This avoids benchmarking 50,000 desktop SQLite ACK fsyncs. The frozen
        # baseline transport itself is unchanged; outbox atomicity is tested separately.
        with store._connect() as conn:
            cursor = conn.execute("SELECT payload FROM publication_outbox ORDER BY sync_sequence")
            while rows := cursor.fetchmany(200):
                publications = []
                for row in rows:
                    raw = bytes(row[0])
                    if slim:
                        fact = json.loads(raw)
                        if fact["kind"] != "assistant_final":
                            continue
                        raw = encode_payload(compact_notification(fact, pair_id="fixture"))
                    publications.append(transport.jetstream.publish(
                        "zgwd.fixture.notify" if slim else "zgwd.fixture.events", raw))
                if publications:
                    await asyncio.gather(*publications)
        server = await asyncio.start_server(proxy, "127.0.0.1", 0)
        credentials = {"user": "mobile", "password": config.token} if slim else {"token": config.token}
        mobile = await nats.connect(f"nats://127.0.0.1:{server.sockets[0].getsockname()[1]}", **credentials)
        await mobile.subscribe("zgwd.fixture.hints" if slim else "zgwd.fixture.events", cb=receive,
            pending_msgs_limit=100000, pending_bytes_limit=1024 * 1024 * 1024)
        if slim:
            await mobile.subscribe("zgwd.fixture.rpc-reply.>", cb=receive)
        await mobile.flush()
        if not slim:
            legacy = await mobile.jetstream().pull_subscribe("zgwd.fixture.events", durable="legacy-workload",
                stream="ZGWD_EVENTS_fixture")
            replayed = 0
            while replayed < 50020:
                records = await legacy.fetch(min(500, 50020 - replayed), timeout=10)
                for record in records:
                    payload_bytes += len(record.data)
                    replayed += 1
                    await record.ack()
            assert replayed == 50020
        if slim:
            subscription = await mobile.jetstream().pull_subscribe("zgwd.fixture.notify", durable="workload",
                stream="ZGWD_NOTIFY_fixture")
            notifications = await subscription.fetch(20, timeout=5)
            assert len(notifications) == 20
            for notification in notifications:
                payload_bytes += len(notification.data)
                assert json.loads(notification.data)["kind"] == "assistant_final"
                await notification.ack()
        transport._sessions[("fixture", "session")] = (2, "epoch")
        for number in range(610):
            kind = "history_list" if number < 600 else "history_read"
            command = {"id": f"query-{number}", "request_id": f"query-{number}", "type": kind,
                "protocol_version": 2, "epoch": "epoch", "device_id": "fixture", "session_id": "session",
                "chat_id": "chat-0" if kind == "history_read" else "", "body": {"collection_page": True}}
            if slim:
                reply = rpc_reply_subject("fixture", "fixture", "session", command["id"])
                await transport.handle_command(command, reply_subject=reply)
            else:
                await transport.handle_command(command)
        for _ in range(1000):
            if received == 610:
                break
            await asyncio.sleep(.01)
        assert received == 610, received
        info = await transport.jetstream.stream_info("ZGWD_NOTIFY_fixture" if slim else "ZGWD_EVENTS_fixture")
        return {"wire": dict(wire), "payload_received": payload_bytes, "broker_bytes": info.state.bytes,
                "broker_records": info.state.messages, "notifications": 20}
    finally:
        if mobile is not None:
            await mobile.close()
        if server is not None:
            server.close()
            await server.wait_closed()
        for task in list(proxy_tasks):
            task.cancel()
        await asyncio.gather(*list(proxy_tasks), return_exceptions=True)
        await transport._close_async()
        frame._remote_chat_snapshot_page_from_store = original_page


def test_ten_minute_trace_receives_at_least_95_percent_fewer_wire_bytes(tmp_path, frame, monkeypatch):
    monkeypatch.setattr(frame, "_run_remote_ui_route", lambda callback, payload=None: callback(payload))
    repo = Path(__file__).resolve().parents[1]
    result = subprocess.run(["git", "show", f"{BASELINE}:remote_nats.py"], cwd=repo, capture_output=True)
    if result.returncode:
        pytest.skip("exact baseline Git object unavailable")
    namespace = {"__name__": "baseline_remote_nats"}
    exec(compile(result.stdout.decode("utf-8"), "baseline_remote_nats.py", "exec"), namespace)
    reports = {}
    for slim in (False, True):
        directory = tmp_path / ("new" if slim else "old")
        directory.mkdir()
        store = seed(directory)
        config = NatsRuntimeConfig(directory, secrets.token_urlsafe(24), host="127.0.0.1",
            port=free_port(), websocket_port=free_port(), role_permissions=slim, pair_id="fixture")
        runtime = NatsServerProcess(config, bundled_dir=repo / "tools" / "nats-server")
        try:
            runtime.start()
            reports["new" if slim else "old"] = asyncio.run(measure(config,
                RemoteNatsTransport if slim else namespace["RemoteNatsTransport"], store, slim, frame))
        finally:
            runtime.stop()
    ratio = reports["new"]["wire"]["received"] / reports["old"]["wire"]["received"]
    print(json.dumps({"logical_duration_seconds": 600, "wall_clock_compressed": True,
        "chats": 100, "turns_per_chat": 100, "execution_steps": 50000, "step_detail_bytes": 8197,
        "notifications": 20, "received_byte_reduction": 1 - ratio, "measurements": reports}))
    assert ratio <= .05
