import asyncio
import os
import uuid
import wave

import config
from realtime_dialog_client import RealtimeDialogClient


def test_audio_file_upload_roundtrip():
    if os.getenv("RUN_INTEGRATION", "1") != "1":
        return

    async def _run():
        client = RealtimeDialogClient(
            config.ws_connect_config,
            str(uuid.uuid4()),
            output_audio_format="pcm_s16le",
            mod="audio_file",
            recv_timeout=120,
        )
        await client.connect()
        got_audio_ack = False

        try:
            with wave.open("whoareyou.wav", "rb") as wf:
                chunk = config.input_audio_config["chunk"]
                sleep_seconds = chunk / float(wf.getframerate())
                while True:
                    data = wf.readframes(chunk)
                    if not data:
                        break
                    await client.task_request(data)
                    await asyncio.sleep(sleep_seconds)

            silence = b"\x00" * (config.input_audio_config["chunk"] * 2)
            for _ in range(10):
                await client.task_request(silence)
                await asyncio.sleep(0.02)

            for _ in range(50):
                response = await asyncio.wait_for(client.receive_server_response(), timeout=3)
                if response.get("event") == 599:
                    raise RuntimeError(response.get("payload_msg"))
                if response.get("message_type") == "SERVER_ACK" and isinstance(response.get("payload_msg"), bytes):
                    if len(response["payload_msg"]) > 0:
                        got_audio_ack = True
                        break
        finally:
            await client.finish_session()
            await client.finish_connection()
            await client.close()

        assert got_audio_ack

    asyncio.run(_run())
