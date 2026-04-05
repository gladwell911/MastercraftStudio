import asyncio
import gzip
import json
import uuid

import websockets

import config
import protocol
from realtime_dialog_client import RealtimeDialogClient


def _parse_client_packet(packet: bytes):
    cursor = 4  # protocol header
    event = int.from_bytes(packet[cursor:cursor + 4], "big")
    cursor += 4

    # Start/FinishConnection packets don't carry session id.
    if event in (1, 2):
        payload_len = int.from_bytes(packet[cursor:cursor + 4], "big")
        cursor += 4
        payload_raw = packet[cursor:cursor + payload_len]
        payload = json.loads(gzip.decompress(payload_raw).decode("utf-8"))
        return {"event": event, "session_id": "", "payload": payload}

    sid_len = int.from_bytes(packet[cursor:cursor + 4], "big")
    cursor += 4
    sid = packet[cursor:cursor + sid_len].decode("utf-8")
    cursor += sid_len
    payload_len = int.from_bytes(packet[cursor:cursor + 4], "big")
    cursor += 4
    payload_raw = packet[cursor:cursor + payload_len]
    payload = json.loads(gzip.decompress(payload_raw).decode("utf-8"))
    return {"event": event, "session_id": sid, "payload": payload}


def _build_server_full_response(event: int, session_id: str, payload_obj: dict) -> bytes:
    payload_bytes = gzip.compress(json.dumps(payload_obj).encode("utf-8"))
    packet = bytearray(
        protocol.generate_header(
            message_type=protocol.SERVER_FULL_RESPONSE,
            message_type_specific_flags=protocol.MSG_WITH_EVENT,
            serial_method=protocol.JSON,
            compression_type=protocol.GZIP,
        )
    )
    packet.extend(int(event).to_bytes(4, "big"))
    packet.extend(int(len(session_id)).to_bytes(4, "big"))
    packet.extend(session_id.encode("utf-8"))
    packet.extend(int(len(payload_bytes)).to_bytes(4, "big"))
    packet.extend(payload_bytes)
    return bytes(packet)


def test_integration_client_live_speed_update_local_ws():
    observed_events = []
    observed_speech_rates = []
    done = asyncio.Event()

    async def ws_handler(websocket):
        session_id = ""
        try:
            while True:
                data = await websocket.recv()
                parsed = _parse_client_packet(data)
                event = parsed["event"]
                observed_events.append(event)

                if event == 1:
                    await websocket.send(_build_server_full_response(50, "", {}))
                elif event == 100:
                    session_id = parsed["session_id"]
                    await websocket.send(_build_server_full_response(150, session_id, {}))
                elif event == 101:
                    rate = parsed["payload"]["tts"]["audio_params"]["speech_rate"]
                    observed_speech_rates.append(rate)
                    await websocket.send(_build_server_full_response(151, session_id, {}))
                elif event == 102:
                    await websocket.send(_build_server_full_response(152, session_id, {}))
                elif event == 2:
                    await websocket.send(_build_server_full_response(52, "", {}))
                    break
        finally:
            done.set()

    async def run_case():
        server = await websockets.serve(ws_handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        ws_cfg = {
            "base_url": f"ws://127.0.0.1:{port}",
            "headers": {},
        }
        client = RealtimeDialogClient(
            ws_cfg,
            str(uuid.uuid4()),
            output_audio_format="pcm_s16le",
            mod="audio",
            recv_timeout=30,
            tts_speaker=config.DEFAULT_SPEAKER,
            tts_speech_rate=0,
        )
        try:
            await client.connect()
            await client.update_session_tts(
                speaker=config.DEFAULT_SPEAKER,
                speed_ratio=config.DEFAULT_SPEED_RATIO,
                speech_rate=42,
            )
            await client.finish_session()
            await client.finish_connection()
            await asyncio.wait_for(done.wait(), timeout=2)
        finally:
            await client.close()
            server.close()
            await server.wait_closed()

    asyncio.run(run_case())

    assert observed_events.count(100) == 1
    assert observed_events.count(101) == 1
    assert observed_speech_rates == [42]
