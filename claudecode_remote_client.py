import asyncio
import json
import threading
import uuid
from typing import Callable

from aiohttp import web


class ClaudeCodeRemoteWebSocketServer:
    """Claude Code 远程控制 WebSocket 服务器"""

    def __init__(
        self,
        host: str,
        port: int,
        token: str,
        on_message: Callable[[dict], tuple[int, dict]],
        on_new_chat: Callable[[dict], tuple[int, dict]],
        on_reply_request: Callable[[dict], tuple[int, dict]],
        on_state: Callable[[dict | None], tuple[int, dict]],
        on_rename_chat: Callable[[dict], tuple[int, dict]] | None = None,
        on_update_settings: Callable[[dict], tuple[int, dict]] | None = None,
        on_history_list: Callable[[], tuple[int, dict]] | None = None,
        on_history_read: Callable[[dict], tuple[int, dict]] | None = None,
    ) -> None:
        self.host = str(host or "127.0.0.1").strip() or "127.0.0.1"
        self.port = max(int(port or 0), 0)
        self.token = str(token or "").strip()
        self.on_message = on_message
        self.on_new_chat = on_new_chat
        self.on_reply_request = on_reply_request
        self.on_rename_chat = on_rename_chat
        self.on_update_settings = on_update_settings
        self.on_state = on_state
        self.on_history_list = on_history_list
        self.on_history_read = on_history_read
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._clients: dict[str, web.WebSocketResponse] = {}
        self._lock = threading.Lock()
        self._started = threading.Event()
        self._start_error: Exception | None = None
        self._bound_port = 0

    def _call_on_state(self, payload: dict | None):
        try:
            return self.on_state(payload)
        except TypeError:
            return self.on_state()

    @property
    def bound_port(self) -> int:
        return self._bound_port

    @property
    def has_clients(self) -> bool:
        with self._lock:
            return bool(self._clients)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._started.clear()
        self._start_error = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._started.wait(timeout=10)
        if self._start_error is not None:
            raise self._start_error
        if not self._started.is_set():
            raise RuntimeError("Claude Code Remote WebSocket server failed to start.")

    def stop(self) -> None:
        loop = self._loop
        if loop is None:
            return
        future = asyncio.run_coroutine_threadsafe(self._shutdown(), loop)
        future.result(timeout=10)
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2)
        self._thread = None
        self._loop = None

    def broadcast_event(self, payload: dict) -> None:
        loop = self._loop
        if loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._broadcast(payload), loop)

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._async_start())
        except Exception:
            try:
                loop.run_until_complete(self._shutdown())
            except Exception:
                pass
            loop.close()
            self._loop = None
            return
        self._started.set()
        try:
            loop.run_forever()
        finally:
            loop.run_until_complete(self._shutdown())
            loop.close()
            self._loop = None

    async def _async_start(self) -> None:
        self._app = web.Application()
        self._app.router.add_get("/ws", self._handle_ws)
        self._app.router.add_get("/healthz", self._handle_health)
        self._runner = web.AppRunner(self._app)
        try:
            await self._runner.setup()
            self._site = web.TCPSite(self._runner, self.host, self.port)
            await self._site.start()
            sockets = list(getattr(getattr(self._site, "_server", None), "sockets", []) or [])
            if sockets:
                self._bound_port = int(sockets[0].getsockname()[1])
            else:
                self._bound_port = int(self.port)
        except Exception as exc:
            self._start_error = exc
            self._started.set()
            raise

    async def _shutdown(self) -> None:
        clients = []
        with self._lock:
            clients = list(self._clients.values())
            self._clients.clear()
        for ws in clients:
            try:
                await ws.close()
            except Exception:
                pass
        if self._runner is not None:
            try:
                await self._runner.cleanup()
            except Exception:
                pass
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.stop()

    def _authorized(self, request: web.Request) -> bool:
        if not self.token:
            return False
        header_token = str(request.headers.get("X-Remote-Token") or "").strip()
        query_token = str(request.query.get("token") or "").strip()
        provided = header_token or query_token
        return provided == self.token

    async def _handle_health(self, request: web.Request) -> web.StreamResponse:
        if not self._authorized(request):
            return web.json_response({"accepted": False, "error": "unauthorized"}, status=401)
        status, payload = await asyncio.to_thread(self._call_on_state, None)
        return web.json_response(payload, status=status)

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        if not self._authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)

        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        client_id = uuid.uuid4().hex
        with self._lock:
            self._clients[client_id] = ws

        status, payload = await asyncio.to_thread(self._call_on_state, None)
        await ws.send_json(
            {
                "type": "connected",
                "client_id": client_id,
                "ok": status < 400,
                "event_id": "connected",
                "ts": asyncio.get_running_loop().time(),
                "body": payload,
            }
        )
        try:
            async for msg in ws:
                if msg.type != web.WSMsgType.TEXT:
                    continue
                await self._handle_ws_message(ws, msg.data)
        finally:
            with self._lock:
                self._clients.pop(client_id, None)
        return ws

    async def _handle_ws_message(self, ws: web.WebSocketResponse, data: str) -> None:
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            await ws.send_json({"error": "invalid_json"})
            return

        if not isinstance(payload, dict):
            await ws.send_json({"error": "invalid_payload"})
            return

        request_id = payload.get("id")
        message_type = str(payload.get("type") or "").strip().lower()
        action = str(payload.get("action") or "").strip()

        if message_type == "ping":
            await ws.send_json({"type": "pong", "id": request_id})
            return

        if action == "message":
            status, body = await asyncio.to_thread(self.on_message, payload)
            await ws.send_json({"status": status, "data": body, "id": request_id})
        elif action == "new_chat":
            status, body = await asyncio.to_thread(self.on_new_chat, payload)
            await ws.send_json({"status": status, "data": body, "id": request_id})
        elif action == "reply_request":
            status, body = await asyncio.to_thread(self.on_reply_request, payload)
            await ws.send_json({"status": status, "data": body, "id": request_id})
        elif action == "state":
            status, body = await asyncio.to_thread(self._call_on_state, payload)
            await ws.send_json({"status": status, "data": body, "id": request_id})
        elif action == "rename_chat" and self.on_rename_chat:
            status, body = await asyncio.to_thread(self.on_rename_chat, payload)
            await ws.send_json({"status": status, "data": body, "id": request_id})
        elif action == "update_settings" and self.on_update_settings:
            status, body = await asyncio.to_thread(self.on_update_settings, payload)
            await ws.send_json({"status": status, "data": body, "id": request_id})
        elif action == "history_list" and self.on_history_list:
            status, body = await asyncio.to_thread(self.on_history_list)
            await ws.send_json({"status": status, "data": body, "id": request_id})
        elif action == "history_read" and self.on_history_read:
            status, body = await asyncio.to_thread(self.on_history_read, payload)
            await ws.send_json({"status": status, "data": body, "id": request_id})
        else:
            await ws.send_json({"error": "unknown_action", "id": request_id})

    async def _broadcast(self, payload: dict) -> None:
        clients = []
        with self._lock:
            clients = list(self._clients.values())
        stale = []
        for ws in clients:
            try:
                await ws.send_json(payload)
            except Exception:
                stale.append(ws)
        if stale:
            with self._lock:
                for client_id, ws in list(self._clients.items()):
                    if ws in stale:
                        self._clients.pop(client_id, None)
