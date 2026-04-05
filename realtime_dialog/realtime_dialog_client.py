import asyncio
import gzip
import json
import uuid
from typing import Any, Dict, Optional

import websockets

try:
    from . import config, protocol
except ImportError:
    import config
    import protocol


class RealtimeDialogClient:
    EVENT_START_SESSION = 100
    EVENT_UPDATE_SESSION = 101
    EVENT_FINISH_SESSION = 102

    def __init__(
        self,
        config: Dict[str, Any],
        session_id: str,
        output_audio_format: str = "pcm",
        mod: str = "audio",
        recv_timeout: int = 10,
        tts_speaker: str = "",
        tts_speed_ratio: float = 1.0,
        tts_speech_rate: Optional[int] = None,
        dialog_system_role: Optional[str] = None,
    ) -> None:
        self.config = config
        self.logid = ""
        self.session_id = session_id
        self.output_audio_format = output_audio_format
        self.mod = mod
        self.recv_timeout = recv_timeout
        self.ws = None
        self.tts_speaker = tts_speaker or getattr(config_module(), "DEFAULT_SPEAKER", "zh_male_yunzhou_jupiter_bigtts")
        self.tts_speed_ratio = tts_speed_ratio
        self.tts_speech_rate = tts_speech_rate
        self.dialog_system_role = (dialog_system_role or getattr(config_module(), "DEFAULT_SYSTEM_ROLE", "")).strip()

    def is_ws_open(self) -> bool:
        return bool(self.ws) and not getattr(self.ws, "closed", False)

    async def connect(self) -> None:
        print(f"url: {self.config['base_url']}, headers: {self.config['headers']}")
        connect_kwargs = {
            "ping_interval": None,
        }
        headers = self.config["headers"]
        try:
            self.ws = await websockets.connect(
                self.config["base_url"],
                additional_headers=headers,
                **connect_kwargs,
            )
        except TypeError:
            self.ws = await websockets.connect(
                self.config["base_url"],
                extra_headers=headers,
                **connect_kwargs,
            )
        response_headers = getattr(self.ws, "response_headers", None)
        if response_headers is None:
            response = getattr(self.ws, "response", None)
            response_headers = getattr(response, "headers", {}) if response is not None else {}
        self.logid = response_headers.get("X-Tt-Logid", "")
        print(f"dialog server response logid: {self.logid}")

        start_connection_request = bytearray(protocol.generate_header())
        start_connection_request.extend(int(1).to_bytes(4, "big"))
        payload_bytes = gzip.compress(b"{}")
        start_connection_request.extend((len(payload_bytes)).to_bytes(4, "big"))
        start_connection_request.extend(payload_bytes)
        await self.ws.send(start_connection_request)

        response = await self.ws.recv()
        start_connection_resp = protocol.parse_response(response)
        print(f"StartConnection response: {start_connection_resp}")
        if "code" in start_connection_resp:
            raise RuntimeError(f"StartConnection failed: {start_connection_resp}")

        await self.start_session()

    def _build_start_session_request(self) -> Dict[str, Any]:
        request_params = json.loads(json.dumps(config.start_session_req))
        request_params["dialog"]["extra"]["recv_timeout"] = self.recv_timeout
        request_params["dialog"]["extra"]["input_mod"] = self.mod

        if self.output_audio_format == "pcm_s16le":
            request_params["tts"]["audio_config"]["format"] = "pcm_s16le"

        speech_rate = self._resolve_speech_rate(speed_ratio=self.tts_speed_ratio, speech_rate=self.tts_speech_rate)
        request_params["tts"]["speaker"] = self.tts_speaker
        request_params["tts"]["audio_params"] = {"speech_rate": speech_rate}
        request_params["tts"]["speech_rate"] = speech_rate
        if self.dialog_system_role:
            request_params["dialog"]["system_role"] = self.dialog_system_role
        return request_params

    def _resolve_speech_rate(self, speed_ratio: float, speech_rate: Optional[int]) -> int:
        if speech_rate is None:
            return int(min(max(config.speed_ratio_to_speech_rate(speed_ratio), -50), 100))
        return int(min(max(int(speech_rate), -50), 100))

    async def start_session(self) -> None:
        if not self.is_ws_open():
            raise RuntimeError("WebSocket is not connected")
        request_params = self._build_start_session_request()
        payload_bytes = gzip.compress(str.encode(json.dumps(request_params)))

        await self._send_session_event(self.EVENT_START_SESSION, payload_bytes)

        response = await self.ws.recv()
        start_session_resp = protocol.parse_response(response)
        print(f"StartSession response: {start_session_resp}")
        if "code" in start_session_resp:
            raise RuntimeError(f"StartSession failed: {start_session_resp}")

    async def restart_session(self, speaker: str, speed_ratio: float, speech_rate: Optional[int] = None) -> None:
        self.tts_speaker = speaker
        self.tts_speed_ratio = speed_ratio
        self.tts_speech_rate = speech_rate

        finished = False
        try:
            await self.finish_session()
            await self._wait_session_finished(timeout_seconds=8.0)
            finished = True
        except Exception:
            pass
        self.session_id = str(uuid.uuid4())
        if finished:
            await self.start_session()
            return

        # Fallback: if old session cannot be cleanly finished in time, recreate websocket.
        try:
            await self.finish_connection()
        except Exception:
            pass
        try:
            await self.close()
        except Exception:
            pass
        await self.connect()

    async def update_session_tts(self, speaker: str, speed_ratio: float, speech_rate: Optional[int] = None) -> None:
        if not self.is_ws_open():
            raise RuntimeError("WebSocket is not connected")
        self.tts_speaker = speaker
        self.tts_speed_ratio = speed_ratio
        self.tts_speech_rate = speech_rate

        speech_rate_value = self._resolve_speech_rate(speed_ratio=speed_ratio, speech_rate=speech_rate)
        payload = {
            "tts": {
                "speaker": speaker,
                "audio_params": {
                    "speech_rate": speech_rate_value,
                },
                "speech_rate": speech_rate_value,
            }
        }
        payload_bytes = gzip.compress(str.encode(json.dumps(payload)))
        await self._send_session_event(self.EVENT_UPDATE_SESSION, payload_bytes)

    async def _wait_session_finished(self, timeout_seconds: float = 8.0) -> None:
        if not self.ws:
            return
        end_events = {152, 153}
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        while loop.time() < deadline:
            remaining = max(0.1, deadline - loop.time())
            response = await asyncio.wait_for(self.receive_server_response(), timeout=remaining)
            event = response.get("event")
            if event in end_events:
                return

    async def say_hello(self) -> None:
        payload = {
            "content": "你好，我是豆包，有什么可以帮助你的？",
        }
        hello_request = bytearray(protocol.generate_header())
        hello_request.extend(int(300).to_bytes(4, "big"))
        payload_bytes = gzip.compress(str.encode(json.dumps(payload)))
        hello_request.extend((len(self.session_id)).to_bytes(4, "big"))
        hello_request.extend(str.encode(self.session_id))
        hello_request.extend((len(payload_bytes)).to_bytes(4, "big"))
        hello_request.extend(payload_bytes)
        await self.ws.send(hello_request)

    async def chat_text_query(self, content: str) -> None:
        payload = {
            "content": content,
        }
        request = bytearray(protocol.generate_header())
        request.extend(int(501).to_bytes(4, "big"))
        payload_bytes = gzip.compress(str.encode(json.dumps(payload)))
        request.extend((len(self.session_id)).to_bytes(4, "big"))
        request.extend(str.encode(self.session_id))
        request.extend((len(payload_bytes)).to_bytes(4, "big"))
        request.extend(payload_bytes)
        await self.ws.send(request)

    async def chat_tts_text(self, is_user_querying: bool, start: bool, end: bool, content: str) -> None:
        if is_user_querying:
            return
        payload = {
            "start": start,
            "end": end,
            "content": content,
        }
        payload_bytes = gzip.compress(str.encode(json.dumps(payload)))

        request = bytearray(protocol.generate_header())
        request.extend(int(500).to_bytes(4, "big"))
        request.extend((len(self.session_id)).to_bytes(4, "big"))
        request.extend(str.encode(self.session_id))
        request.extend((len(payload_bytes)).to_bytes(4, "big"))
        request.extend(payload_bytes)
        await self.ws.send(request)

    async def chat_rag_text(self, is_user_querying: bool, external_rag: str) -> None:
        if is_user_querying:
            return
        payload = {
            "external_rag": external_rag,
        }
        payload_bytes = gzip.compress(str.encode(json.dumps(payload)))

        request = bytearray(protocol.generate_header())
        request.extend(int(502).to_bytes(4, "big"))
        request.extend((len(self.session_id)).to_bytes(4, "big"))
        request.extend(str.encode(self.session_id))
        request.extend((len(payload_bytes)).to_bytes(4, "big"))
        request.extend(payload_bytes)
        await self.ws.send(request)

    async def task_request(self, audio: bytes) -> None:
        request = bytearray(
            protocol.generate_header(
                message_type=protocol.CLIENT_AUDIO_ONLY_REQUEST,
                serial_method=protocol.NO_SERIALIZATION,
            )
        )
        request.extend(int(200).to_bytes(4, "big"))
        request.extend((len(self.session_id)).to_bytes(4, "big"))
        request.extend(str.encode(self.session_id))
        payload_bytes = gzip.compress(audio)
        request.extend((len(payload_bytes)).to_bytes(4, "big"))
        request.extend(payload_bytes)
        await self.ws.send(request)

    async def receive_server_response(self) -> Dict[str, Any]:
        try:
            response = await self.ws.recv()
            return protocol.parse_response(response)
        except Exception as exc:
            raise Exception(f"Failed to receive message: {exc}")

    async def finish_session(self) -> None:
        if not self.is_ws_open():
            return
        payload_bytes = gzip.compress(b"{}")
        await self._send_session_event(self.EVENT_FINISH_SESSION, payload_bytes)

    async def finish_connection(self) -> None:
        if not self.is_ws_open():
            return
        request = bytearray(protocol.generate_header())
        request.extend(int(2).to_bytes(4, "big"))
        payload_bytes = gzip.compress(b"{}")
        request.extend((len(payload_bytes)).to_bytes(4, "big"))
        request.extend(payload_bytes)
        await self.ws.send(request)
        response = await self.ws.recv()
        print(f"FinishConnection response: {protocol.parse_response(response)}")

    async def close(self) -> None:
        if self.ws:
            print("Closing WebSocket connection...")
            await self.ws.close()
            self.ws = None

    async def _send_session_event(self, event: int, payload_bytes: bytes) -> None:
        if not self.is_ws_open():
            raise RuntimeError("WebSocket is not connected")
        request = bytearray(protocol.generate_header())
        request.extend(int(event).to_bytes(4, "big"))
        request.extend((len(self.session_id)).to_bytes(4, "big"))
        request.extend(str.encode(self.session_id))
        request.extend((len(payload_bytes)).to_bytes(4, "big"))
        request.extend(payload_bytes)
        await self.ws.send(request)


def config_module():
    return config

