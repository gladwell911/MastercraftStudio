import asyncio

from aiohttp import ClientSession

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
