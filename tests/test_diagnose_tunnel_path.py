import asyncio
from aiohttp import web
import pytest
from scripts.diagnose_tunnel_path import sample

@pytest.mark.parametrize("mode", ["silent", "closed"])
def test_sample_bounds_silent_or_closed_websocket(mode):
    async def exercise():
        async def handler(request):
            socket = web.WebSocketResponse()
            await socket.prepare(request)
            if mode == "closed":
                await socket.close()
            else:
                await asyncio.sleep(4)
            return socket
        app = web.Application()
        app.router.add_get("/nats", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        try:
            port = site._server.sockets[0].getsockname()[1]
            result = await asyncio.wait_for(sample("fixture", f"ws://127.0.0.1:{port}/nats", "fixture"), 5)
            assert result["error"] in {"TimeoutError", "ConnectionError"}
            assert result["elapsed_ms"] < 4500
        finally:
            await runner.cleanup()
    asyncio.run(exercise())
