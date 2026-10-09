import asyncio

import pytest
from aiohttp import ClientSession, WSMsgType, web

import main


@pytest.mark.parametrize("heartbeat, survives", [(5, False), (30, True)])
def test_bridge_delayed_mobile_pong(heartbeat, survives, monkeypatch):
    """Exercise the actual bridge: a three-second mobile PONG exceeds the old budget."""
    monkeypatch.setattr(main, "REMOTE_CONTROL_WEBSOCKET_HEARTBEAT_SECONDS", heartbeat)

    async def scenario():
        upstream_closed = asyncio.Event()
        bridge_finished = asyncio.Event()

        async def echo(request):
            websocket = web.WebSocketResponse()
            await websocket.prepare(request)
            try:
                async for message in websocket:
                    if message.type == WSMsgType.TEXT:
                        await websocket.send_str(message.data)
            finally:
                upstream_closed.set()
            return websocket

        upstream_app = web.Application()
        upstream_app.router.add_get("/nats", echo)
        upstream_runner = web.AppRunner(upstream_app)
        await upstream_runner.setup()
        upstream_site = web.TCPSite(upstream_runner, "127.0.0.1", 0)
        await upstream_site.start()
        upstream_port = upstream_site._server.sockets[0].getsockname()[1]
        proxy = main.CloudflaredOriginProxy(
            listen_host="127.0.0.1", listen_port=0,
            nats_ws_url=f"http://127.0.0.1:{upstream_port}/nats", file_base_url="",
        )

        async def bridge(request):
            try:
                return await proxy._proxy_websocket(request)
            finally:
                bridge_finished.set()

        bridge_app = web.Application()
        bridge_app.router.add_get("/nats", bridge)
        bridge_runner = web.AppRunner(bridge_app)
        await bridge_runner.setup()
        bridge_site = web.TCPSite(bridge_runner, "127.0.0.1", 0)
        await bridge_site.start()
        bridge_port = bridge_site._server.sockets[0].getsockname()[1]
        try:
            async with ClientSession() as session:
                async with session.ws_connect(
                    f"http://127.0.0.1:{bridge_port}/nats", autoping=False,
                ) as client:
                    ping = await client.receive(timeout=heartbeat + 3)
                    assert ping.type == WSMsgType.PING
                    await asyncio.sleep(3)
                    if survives:
                        await client.pong(ping.data)
                        await client.send_str("after-delayed-pong")
                        reply = await client.receive(timeout=2)
                        assert reply.type == WSMsgType.TEXT
                        assert reply.data == "after-delayed-pong"
                    else:
                        closed = await client.receive(timeout=2)
                        assert closed.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR)
            await asyncio.wait_for(upstream_closed.wait(), timeout=2)
            await asyncio.wait_for(bridge_finished.wait(), timeout=2)
        finally:
            await bridge_runner.cleanup()
            await upstream_runner.cleanup()

    asyncio.run(scenario())


def test_bridge_production_pong_budget_is_separate_from_local_probe():
    assert main.REMOTE_CONTROL_HEALTH_TIMEOUT_SECONDS == 5
    assert main.REMOTE_CONTROL_WEBSOCKET_HEARTBEAT_SECONDS == 30
    assert web.WebSocketResponse(
        heartbeat=main.REMOTE_CONTROL_WEBSOCKET_HEARTBEAT_SECONDS,
    )._pong_heartbeat == 15
