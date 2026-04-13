import asyncio

from aiohttp import ClientSession

import main


async def _connect_notes_client(port: int, token: str = "secret"):
    session = ClientSession()
    ws = await session.ws_connect(f"http://127.0.0.1:{port}/ws?token={token}")
    connected = (await ws.receive()).json()
    assert connected["type"] == "connected"
    return session, ws


async def _request(ws, payload: dict):
    await ws.send_json(payload)
    while True:
        message = (await ws.receive()).json()
        if message.get("type") == "response" and message.get("id") == payload.get("id"):
            return message


async def _wait_for_event(ws, event_type: str, *, timeout: float = 5.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        message = (await ws.receive(timeout=timeout)).json()
        if message.get("type") == event_type:
            return message
    raise AssertionError(f"did not receive event {event_type}")


def test_notes_acceptance_online_sync_pushes_changes_between_remote_clients(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_TOKEN", "secret")
    monkeypatch.setenv("REMOTE_CONTROL_PORT", "0")
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))
    frame._start_remote_ws_server_if_configured()
    try:
        async def _run():
            session_a, ws_a = await _connect_notes_client(frame._remote_ws_server.bound_port)
            session_b, ws_b = await _connect_notes_client(frame._remote_ws_server.bound_port)
            try:
                subscribe = await _request(ws_b, {"id": "sub-1", "type": "notes_subscribe", "cursor": "0"})
                assert subscribe["ok"] is True

                push = await _request(
                    ws_a,
                    {
                        "id": "push-1",
                        "type": "notes_push_ops",
                        "ops": [
                            {
                                "op_id": "op-notebook-1",
                                "entity_type": "notebook",
                                "entity_id": "nb-remote-1",
                                "action": "create",
                                "source_device": "mobile-1",
                                "payload": {
                                    "title": "共享笔记本",
                                    "created_at": "2026-04-12T10:00:00+00:00",
                                    "updated_at": "2026-04-12T10:00:00+00:00",
                                },
                            },
                            {
                                "op_id": "op-entry-1",
                                "entity_type": "entry",
                                "entity_id": "entry-remote-1",
                                "action": "create",
                                "source_device": "mobile-1",
                                "payload": {
                                    "notebook_id": "nb-remote-1",
                                    "content": "第一条远端内容",
                                    "source": "manual",
                                    "created_at": "2026-04-12T10:00:01+00:00",
                                    "updated_at": "2026-04-12T10:00:01+00:00",
                                },
                            },
                        ],
                    },
                )
                assert push["ok"] is True

                changed = await _wait_for_event(ws_b, "notes_changed")
                assert str(changed.get("cursor") or "0") != "0"

                pull = await _request(
                    ws_b,
                    {
                        "id": "pull-1",
                        "type": "notes_pull_since",
                        "cursor": "0",
                    },
                )
                body = pull["body"]
                assert body["notebooks"]
                assert body["entries"]
                assert any(item["id"] == "nb-remote-1" for item in body["notebooks"])
                assert any(item["id"] == "entry-remote-1" for item in body["entries"])
            finally:
                await ws_a.close()
                await ws_b.close()
                await session_a.close()
                await session_b.close()

        asyncio.run(_run())

        notebook = frame.notes_store.get_notebook("nb-remote-1")
        entry = frame.notes_store.get_entry("entry-remote-1")
        assert notebook is not None
        assert notebook.title == "共享笔记本"
        assert entry is not None
        assert entry.content == "第一条远端内容"
    finally:
        frame._remote_ws_server.stop()
        frame._remote_ws_server = None


def test_notes_acceptance_offline_conflict_generates_conflict_copy_after_reconnect(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_TOKEN", "secret")
    monkeypatch.setenv("REMOTE_CONTROL_PORT", "0")
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))
    notebook = frame.notes_store.create_notebook("冲突笔记本")
    entry = frame.notes_store.create_entry(notebook.id, "初始内容", source="manual")
    frame.notes_store.update_entry(entry.id, "电脑端离线修改")

    frame._start_remote_ws_server_if_configured()
    try:
        async def _run():
            session_sender, ws_sender = await _connect_notes_client(frame._remote_ws_server.bound_port)
            session_listener, ws_listener = await _connect_notes_client(frame._remote_ws_server.bound_port)
            try:
                await _request(ws_listener, {"id": "sub-2", "type": "notes_subscribe", "cursor": "0"})
                stale_update = await _request(
                    ws_sender,
                    {
                        "id": "push-2",
                        "type": "notes_push_ops",
                        "ops": [
                            {
                                "op_id": "op-entry-stale-1",
                                "entity_type": "entry",
                                "entity_id": entry.id,
                                "action": "update",
                                "source_device": "mobile-9",
                                "base_version": 1,
                                "payload": {
                                    "content": "手机端离线修改",
                                    "updated_at": "2026-04-12T10:30:00+00:00",
                                },
                            }
                        ],
                    },
                )
                assert stale_update["ok"] is True
                assert stale_update["body"]["conflicts"]

                conflict = await _wait_for_event(ws_listener, "notes_conflict")
                assert conflict["conflicts"]
            finally:
                await ws_sender.close()
                await ws_listener.close()
                await session_sender.close()
                await session_listener.close()

        asyncio.run(_run())

        entries = frame.notes_store.list_entries(notebook.id, include_deleted=True)
        conflict_copies = [item for item in entries if item.is_conflict_copy]
        assert frame.notes_store.get_entry(entry.id).content == "电脑端离线修改"
        assert len(conflict_copies) == 1
        assert "手机端" in conflict_copies[0].content
        assert "手机端离线修改" in conflict_copies[0].content
    finally:
        frame._remote_ws_server.stop()
        frame._remote_ws_server = None


def test_notes_acceptance_initial_pull_since_zero_returns_existing_snapshot(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_TOKEN", "secret")
    monkeypatch.setenv("REMOTE_CONTROL_PORT", "0")
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))
    notebook = frame.notes_store.create_notebook("234")
    entry = frame.notes_store.create_entry(notebook.id, "desktop existing body", source="manual")

    frame._start_remote_ws_server_if_configured()
    try:
        async def _run():
            session, ws = await _connect_notes_client(frame._remote_ws_server.bound_port)
            try:
                subscribe = await _request(ws, {"id": "sub-init", "type": "notes_subscribe", "cursor": "0"})
                assert subscribe["ok"] is True

                pull = await _request(
                    ws,
                    {
                        "id": "pull-init",
                        "type": "notes_pull_since",
                        "cursor": "0",
                    },
                )
                body = pull["body"]
                assert body["notebooks"]
                assert body["entries"]
                assert any(item["id"] == notebook.id for item in body["notebooks"])
                assert any(item["id"] == entry.id for item in body["entries"])
            finally:
                await ws.close()
                await session.close()

        asyncio.run(_run())
    finally:
        frame._remote_ws_server.stop()
        frame._remote_ws_server = None
