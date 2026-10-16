"""Read-only, explicitly addressed DNS/NATS path sampling; never manages tunnels."""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import socket
import time
from urllib.parse import urlsplit

import aiohttp


async def _sample(label: str, url: str, token: str) -> dict:
    started = time.monotonic()
    host = urlsplit(url).hostname
    result = {"path": label, "utc": time.time(), "phase": "dns"}
    try:
        async with asyncio.timeout(10):
            return await _exchange(label, url, token, started, host, result)
    except Exception as error:
        result.update(error=type(error).__name__, elapsed_ms=round((time.monotonic() - started) * 1000))
        return result


async def _exchange(label, url, token, started, host, result):
    try:
        addresses = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM), 5)
        result.update(dns_ms=round((time.monotonic() - started) * 1000), addresses=sorted({row[4][0] for row in addresses}))
        result["phase"] = "websocket"
        # This diagnostic process alone bypasses environment HTTP proxies.
        async with aiohttp.ClientSession(trust_env=False, timeout=aiohttp.ClientTimeout(total=8)) as session:
            async with session.ws_connect(url) as websocket:
                info = await websocket.receive(timeout=3)
                if info.type in {aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR}:
                    raise ConnectionError("closed_before_info")
                raw = info.data.decode() if isinstance(info.data, bytes) else str(info.data)
                if not raw.startswith("INFO "):
                    raise ValueError("invalid_nats_info")
                result["server_id"] = json.loads(raw[5:])["server_id"]
                result["phase"] = "authentication_ping"
                await websocket.send_str("CONNECT " + json.dumps({"user": "mobile", "pass": token, "verbose": False}) + "\r\nPING\r\n")
                while True:
                    message = await websocket.receive(timeout=3)
                    if message.type in {aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR}:
                        raise ConnectionError("closed_before_pong")
                    text = message.data.decode() if isinstance(message.data, bytes) else str(message.data)
                    if "-ERR" in text:
                        raise ValueError("nats_authentication_failed")
                    if "PONG" in text:
                        break
                    if "PING" in text:
                        await websocket.send_str("PONG\r\n")
        result.update(phase="ready", elapsed_ms=round((time.monotonic() - started) * 1000))
    except Exception as error:
        # Error type and phase are sufficient; raw URLs/credentials never log.
        result.update(error=type(error).__name__, elapsed_ms=round((time.monotonic() - started) * 1000))
    return result


sample = _sample


async def run(args):
    token = os.environ.get(args.token_env, "")
    if not token:
        raise SystemExit("Set the specified token environment variable; never pass a token on argv")
    paths = [(label, url) for label, url in (("direct", args.direct), ("bridge", args.bridge), ("public", args.public)) if url]
    if not paths:
        raise SystemExit("Supply at least one explicitly addressed WebSocket path")
    for index in range(args.samples):
        for result in await asyncio.gather(*(sample(label, url, token) for label, url in paths)):
            print(json.dumps({"sample": index + 1, "protocol_label": args.protocol_label,
                "measurement": "application_phase_timings_not_transport_wire_bytes", **result}), flush=True)
        if index + 1 < args.samples:
            await asyncio.sleep(args.interval)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--direct", default="")
    parser.add_argument("--bridge", default="")
    parser.add_argument("--public", default="")
    parser.add_argument("--protocol-label", choices=["auto", "quic", "http2"], required=True)
    parser.add_argument("--token-env", default="TUNNEL_DIAGNOSTIC_TOKEN")
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--interval", type=float, default=30)
    args = parser.parse_args()
    if args.samples < 1 or args.interval < 0:
        parser.error("samples must be positive and interval nonnegative")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
