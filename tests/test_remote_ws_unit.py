import asyncio

from aiohttp import ClientSession
import requests

from remote_ws import RemoteWebSocketServer


async def _connect(port: int, token: str = "secret"):
    session = ClientSession()
    ws = await session.ws_connect(f"http://127.0.0.1:{port}/ws?token={token}")
    return session, ws


def test_remote_ws_server_routes_and_auth():
    seen = {"message": None, "new_chat": 0, "reply": None, "state": 0}
    server = RemoteWebSocketServer(
        host="127.0.0.1",
        port=0,
        token="secret",
        on_message=lambda payload: (200, {"accepted": True, "echo": payload.get("text")}),
        on_new_chat=lambda payload: (200, {"accepted": True}),
        on_reply_request=lambda payload: (200, {"accepted": True, "kind": payload.get("kind")}),
        on_state=lambda: (200, {"accepted": True, "status": "idle"}),
        on_update_settings=lambda payload: (200, {"accepted": True, "settings": {"codex_answer_english_filter_enabled": bool(payload.get("codex_answer_english_filter_enabled"))}}),
        on_history_list=lambda: (200, {"accepted": True, "chats": [{"chat_id": "c1"}]}),
        on_history_read=lambda payload: (200, {"accepted": True, "chat": {"chat_id": payload.get("chat_id")}}),
    )
    server.start()
    try:
        async def _run():
            session = ClientSession()
            try:
                bad = await session.get(f"http://127.0.0.1:{server.bound_port}/healthz")
                assert bad.status == 401
            finally:
                await session.close()

            session, ws = await _connect(server.bound_port)
            try:
                connected = (await ws.receive()).json()
                assert connected["type"] == "connected"
                assert connected["event_id"] == "connected"
                assert "ts" in connected
                await ws.send_json({"id": "s1", "type": "state"})
                response = (await ws.receive()).json()
                assert response["body"]["status"] == "idle"

                await ws.send_json({"id": "m1", "type": "message", "text": "hello"})
                response = (await ws.receive()).json()
                assert response["body"]["echo"] == "hello"

                await ws.send_json({"id": "n1", "type": "new_chat"})
                response = (await ws.receive()).json()
                assert response["ok"] is True

                await ws.send_json({"id": "r1", "type": "reply_request", "kind": "approval", "text": "1"})
                response = (await ws.receive()).json()
                assert response["body"]["kind"] == "approval"

                await ws.send_json({"id": "u1", "type": "update_settings", "codex_answer_english_filter_enabled": True})
                response = (await ws.receive()).json()
                assert response["body"]["settings"]["codex_answer_english_filter_enabled"] is True

                await ws.send_json({"id": "h1", "type": "history_list"})
                response = (await ws.receive()).json()
                assert response["body"]["chats"][0]["chat_id"] == "c1"

                await ws.send_json({"id": "h2", "type": "history_read", "chat_id": "c1"})
                response = (await ws.receive()).json()
                assert response["body"]["chat"]["chat_id"] == "c1"
            finally:
                await ws.close()
                await session.close()

        asyncio.run(_run())
    finally:
        server.stop()


def test_remote_ws_server_retires_notes_apis():
    server = RemoteWebSocketServer(
        host="127.0.0.1",
        port=0,
        token="secret",
        on_message=lambda payload: (200, {"accepted": True}),
        on_new_chat=lambda payload: (200, {"accepted": True}),
        on_reply_request=lambda payload: (200, {"accepted": True}),
        on_state=lambda: (200, {"accepted": True, "status": "idle"}),
    )
    server.start()
    try:
        async def _run():
            session, ws = await _connect(server.bound_port)
            try:
                await ws.receive()
                await ws.send_json({"id": "ns1", "type": "notes_snapshot"})
                response = (await ws.receive()).json()
                assert response["status"] == 410
                assert response["body"]["error"] == "retired"

                await ws.send_json({"id": "np1", "type": "notes_pull_since", "cursor": "7"})
                response = (await ws.receive()).json()
                assert response["status"] == 410
                assert response["body"]["error"] == "retired"

                await ws.send_json({"id": "nps", "type": "notes_push_ops", "ops": [{"entity_type": "entry"}]})
                response = (await ws.receive()).json()
                assert response["status"] == 410
                assert response["body"]["error"] == "retired"

                await ws.send_json({"id": "nss", "type": "notes_subscribe", "cursor": "8"})
                response = (await ws.receive()).json()
                assert response["status"] == 410
                assert response["body"]["error"] == "retired"

                await ws.send_json({"id": "na", "type": "notes_ack", "op_ids": ["op-1"]})
                response = (await ws.receive()).json()
                assert response["status"] == 410
                assert response["body"]["error"] == "retired"

                await ws.send_json({"id": "np", "type": "notes_ping", "cursor": "9"})
                response = (await ws.receive()).json()
                assert response["status"] == 410
                assert response["body"]["error"] == "retired"
            finally:
                await ws.close()
                await session.close()

        asyncio.run(_run())
    finally:
        server.stop()


def test_remote_ws_server_exposes_couchdb_compatible_notes_routes():
    server = RemoteWebSocketServer(
        host="127.0.0.1",
        port=0,
        token="secret",
        on_message=lambda payload: (200, {"accepted": True}),
        on_new_chat=lambda payload: (200, {"accepted": True}),
        on_reply_request=lambda payload: (200, {"accepted": True}),
        on_state=lambda: (200, {"accepted": True, "status": "idle"}),
        on_notes_couchdb_changes=lambda payload: (
            200,
            {
                "results": [
                    {
                        "seq": "3",
                        "id": "notebook:shared-note",
                        "doc": {
                            "_id": "notebook:shared-note",
                            "_rev": "2-rev",
                            "type": "notebook",
                            "title": "shared note",
                        },
                    }
                ],
                "last_seq": "3",
            },
        ),
        on_notes_couchdb_bulk_docs=lambda payload: (
            201,
            [{"id": doc.get("_id"), "ok": True, "rev": "2-rev"} for doc in list(payload.get("docs") or [])],
        ),
    )
    server.start()
    try:
        base = f"http://127.0.0.1:{server.bound_port}"

        response = requests.get(
            f"{base}/notes_couchdb/zhuge_notes/_changes",
            params={"since": "0", "include_docs": "true", "token": "secret"},
            timeout=5,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["last_seq"] == "3"
        assert body["results"][0]["doc"]["title"] == "shared note"

        response = requests.post(
            f"{base}/notes_couchdb/zhuge_notes/_bulk_docs?token=secret",
            json={
                "docs": [
                    {
                        "_id": "notebook:shared-note",
                        "type": "notebook",
                        "title": "shared note",
                    }
                ]
            },
            timeout=5,
        )
        assert response.status_code == 201
        assert response.json()[0]["id"] == "notebook:shared-note"
    finally:
        server.stop()
