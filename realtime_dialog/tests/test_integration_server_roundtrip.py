import asyncio
import os
import uuid

import config
from realtime_dialog_client import RealtimeDialogClient


def test_server_roundtrip_text_and_audio():
    # Set RUN_INTEGRATION=0 to skip in offline CI.
    if os.getenv("RUN_INTEGRATION", "1") != "1":
        return

    async def _run():
        client = RealtimeDialogClient(
            config.ws_connect_config,
            str(uuid.uuid4()),
            output_audio_format="pcm_s16le",
            mod="text",
            recv_timeout=120,
        )
        await client.connect()
        await client.say_hello()
        await client.chat_text_query("你好")

        got_useful_response = False
        try:
            for _ in range(20):
                response = await asyncio.wait_for(client.receive_server_response(), timeout=5)
                if response.get("message_type") == "SERVER_ACK" and isinstance(response.get("payload_msg"), bytes):
                    if len(response["payload_msg"]) > 0:
                        got_useful_response = True
                        break
                if response.get("message_type") == "SERVER_FULL_RESPONSE":
                    payload = response.get("payload_msg")
                    if payload:
                        got_useful_response = True
        finally:
            await client.finish_session()
            await client.finish_connection()
            await client.close()

        assert got_useful_response

    asyncio.run(_run())
