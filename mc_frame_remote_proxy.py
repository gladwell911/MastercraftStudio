"""Local Cloudflared origin proxy for NATS and file traffic."""

import asyncio
from aiohttp import ClientTimeout, WSMsgType, web

import mc_frame_bindings as bindings


class CloudflaredOriginProxy:
    def __init__(self, *, listen_host: str, listen_port: int, nats_ws_url: str, file_base_url: str):
        self.listen_host = listen_host
        self.listen_port = int(listen_port)
        self.nats_ws_url = str(nats_ws_url or "").strip()
        self.file_base_url = str(file_base_url or "").strip().rstrip("/")
        self._loop: asyncio.AbstractEventLoop | None = None
        self._runner: web.AppRunner | None = None
        self._thread: bindings.app.threading.Thread | None = None
        self._started = bindings.app.threading.Event()
        self._error: Exception | None = None

    def start(self, timeout: float = 5.0) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._started.clear()
        self._error = None
        self._thread = bindings.app.threading.Thread(target=self._run, name="cloudflared-origin-proxy", daemon=True)
        self._thread.start()
        if not self._started.wait(timeout):
            raise TimeoutError(f"cloudflared origin proxy did not start on {self.listen_host}:{self.listen_port}")
        if self._error is not None:
            raise self._error

    def stop(self) -> None:
        loop = self._loop
        runner = self._runner
        if loop is not None and runner is not None and loop.is_running():
            future = asyncio.run_coroutine_threadsafe(runner.cleanup(), loop)
            try:
                future.result(timeout=3.0)
            except Exception:
                pass
            loop.call_soon_threadsafe(loop.stop)
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)
        self._thread = None
        self._loop = None
        self._runner = None

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self._handle_request)
        runner = web.AppRunner(app)
        self._runner = runner
        try:
            loop.run_until_complete(runner.setup())
            site = web.TCPSite(runner, self.listen_host, self.listen_port)
            loop.run_until_complete(site.start())
            self._started.set()
            loop.run_forever()
        except Exception as exc:
            self._error = exc
            self._started.set()
        finally:
            try:
                loop.run_until_complete(runner.cleanup())
            except Exception:
                pass
            loop.close()

    async def _handle_request(self, request: web.Request) -> web.StreamResponse:
        if request.path.rstrip("/") == "/nats":
            return await self._proxy_websocket(request)
        return await self._proxy_file_request(request)

    async def _proxy_websocket(self, request: web.Request) -> web.WebSocketResponse:
        downstream = web.WebSocketResponse(heartbeat=bindings.app.REMOTE_CONTROL_WEBSOCKET_HEARTBEAT_SECONDS)
        await downstream.prepare(request)
        tasks = []
        try:
            async with bindings.app.ClientSession() as session:
                async with session.ws_connect(self.nats_ws_url, heartbeat=bindings.app.REMOTE_CONTROL_WEBSOCKET_HEARTBEAT_SECONDS) as upstream:
                    async def client_to_upstream() -> None:
                        async for msg in downstream:
                            if msg.type == WSMsgType.TEXT:
                                await upstream.send_str(msg.data)
                            elif msg.type == WSMsgType.BINARY:
                                await upstream.send_bytes(msg.data)
                            elif msg.type == WSMsgType.CLOSE:
                                await upstream.close()

                    async def upstream_to_client() -> None:
                        async for msg in upstream:
                            if msg.type == WSMsgType.TEXT:
                                await downstream.send_str(msg.data)
                            elif msg.type == WSMsgType.BINARY:
                                await downstream.send_bytes(msg.data)
                            elif msg.type == WSMsgType.CLOSE:
                                await downstream.close()

                    tasks = [
                        asyncio.create_task(client_to_upstream()),
                        asyncio.create_task(upstream_to_client()),
                    ]
                    try:
                        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                        for task in done:
                            task.result()
                    finally:
                        for task in tasks:
                            if not task.done():
                                task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            await downstream.close()
        return downstream

    async def _proxy_file_request(self, request: web.Request) -> web.Response:
        if not self.file_base_url:
            return web.Response(status=503, text="file service is not configured")
        target_url = f"{self.file_base_url}{request.rel_url.raw_path_qs}"
        body = await request.read()
        excluded = {"host", "connection", "content-length", "transfer-encoding"}
        headers = {key: value for key, value in request.headers.items() if key.lower() not in excluded}
        timeout = ClientTimeout(total=None, sock_connect=10)
        async with bindings.app.ClientSession(timeout=timeout) as session:
            async with session.request(request.method, target_url, data=body, headers=headers) as response:
                response_body = await response.read()
                response_headers = {
                    key: value
                    for key, value in response.headers.items()
                    if key.lower() not in {"connection", "content-length", "transfer-encoding", "content-encoding"}
                }
                return web.Response(status=response.status, body=response_body, headers=response_headers)
