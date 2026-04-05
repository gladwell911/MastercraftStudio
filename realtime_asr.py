from __future__ import annotations

import asyncio
import gzip
import json
import os
import struct
import uuid
from dataclasses import dataclass
from typing import Any, Callable

try:
    import aiohttp
except Exception:  # pragma: no cover - handled at runtime
    aiohttp = None

DEFAULT_APP_ID = "5685852259"
DEFAULT_ACCESS_TOKEN = "tCNf26V6T1NYLoauifmAB46QPKv1rs_0"
DEFAULT_WS_URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel"
DEFAULT_RESOURCE_ID = "volc.bigasr.sauc.duration"

SAMPLE_RATE = 16000
CHANNELS = 1
SEGMENT_MS = 200
BLOCK_FRAMES = SAMPLE_RATE * SEGMENT_MS // 1000


def _norm_text(text: str) -> str:
    kept = []
    for ch in str(text or ""):
        cp = ord(ch)
        if ch.isalnum() or (0x4E00 <= cp <= 0x9FFF):
            kept.append(ch.lower())
    return "".join(kept)


def build_stream_wav_header(sample_rate: int, channels: int, bits_per_sample: int) -> bytes:
    data_size = 0x7FFFFFFF
    block_align = channels * bits_per_sample // 8
    byte_rate = sample_rate * block_align
    riff_chunk_size = 36 + data_size
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        riff_chunk_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b"data",
        data_size,
    )


class ProtocolVersion:
    V1 = 0b0001


class MessageType:
    CLIENT_FULL_REQUEST = 0b0001
    CLIENT_AUDIO_ONLY_REQUEST = 0b0010
    SERVER_FULL_RESPONSE = 0b1001
    SERVER_ERROR_RESPONSE = 0b1111


class MessageTypeSpecificFlags:
    POS_SEQUENCE = 0b0001
    NEG_WITH_SEQUENCE = 0b0011


class SerializationType:
    JSON = 0b0001


class CompressionType:
    GZIP = 0b0001


@dataclass
class AsrResponse:
    code: int = 0
    event: int = 0
    is_last_package: bool = False
    payload_sequence: int = 0
    payload_size: int = 0
    payload_msg: dict[str, Any] | None = None


class Protocol:
    @staticmethod
    def build_header(message_type: int, flags: int) -> bytes:
        header = bytearray()
        header.append((ProtocolVersion.V1 << 4) | 1)
        header.append((message_type << 4) | flags)
        header.append((SerializationType.JSON << 4) | CompressionType.GZIP)
        header.append(0x00)
        return bytes(header)

    @staticmethod
    def build_auth_headers() -> dict[str, str]:
        return {
            "X-Api-Resource-Id": os.getenv("DOUBAO_ASR_RESOURCE_ID", DEFAULT_RESOURCE_ID),
            "X-Api-Request-Id": str(uuid.uuid4()),
            "X-Api-Access-Key": os.getenv("DOUBAO_ASR_ACCESS_TOKEN", DEFAULT_ACCESS_TOKEN),
            "X-Api-App-Key": os.getenv("DOUBAO_ASR_APP_ID", DEFAULT_APP_ID),
        }

    @staticmethod
    def build_full_request(seq: int) -> bytes:
        payload = {
            "user": {"uid": "zhuge_accessibility"},
            "audio": {
                "format": "wav",
                "codec": "raw",
                "rate": SAMPLE_RATE,
                "bits": 16,
                "channel": CHANNELS,
            },
            "request": {
                "model_name": "bigmodel",
                "enable_itn": True,
                "enable_punc": True,
                "enable_ddc": True,
                "show_utterances": True,
                "enable_nonstream": False,
            },
        }
        payload_bytes = gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

        req = bytearray()
        req.extend(Protocol.build_header(MessageType.CLIENT_FULL_REQUEST, MessageTypeSpecificFlags.POS_SEQUENCE))
        req.extend(struct.pack(">i", seq))
        req.extend(struct.pack(">I", len(payload_bytes)))
        req.extend(payload_bytes)
        return bytes(req)

    @staticmethod
    def build_audio_request(seq: int, pcm_bytes: bytes, is_last: bool) -> bytes:
        flags = MessageTypeSpecificFlags.NEG_WITH_SEQUENCE if is_last else MessageTypeSpecificFlags.POS_SEQUENCE
        payload_seq = -abs(seq) if is_last else seq
        payload = gzip.compress(pcm_bytes)

        req = bytearray()
        req.extend(Protocol.build_header(MessageType.CLIENT_AUDIO_ONLY_REQUEST, flags))
        req.extend(struct.pack(">i", payload_seq))
        req.extend(struct.pack(">I", len(payload)))
        req.extend(payload)
        return bytes(req)

    @staticmethod
    def parse_response(msg: bytes) -> AsrResponse:
        response = AsrResponse()
        header_size = msg[0] & 0x0F
        message_type = msg[1] >> 4
        flags = msg[1] & 0x0F
        serialization = msg[2] >> 4
        compression = msg[2] & 0x0F
        payload = msg[header_size * 4 :]

        if flags & 0x01:
            response.payload_sequence = struct.unpack(">i", payload[:4])[0]
            payload = payload[4:]
        if flags & 0x02:
            response.is_last_package = True
        if flags & 0x04:
            response.event = struct.unpack(">i", payload[:4])[0]
            payload = payload[4:]

        if message_type == MessageType.SERVER_FULL_RESPONSE:
            response.payload_size = struct.unpack(">I", payload[:4])[0]
            payload = payload[4:]
        elif message_type == MessageType.SERVER_ERROR_RESPONSE:
            response.code = struct.unpack(">i", payload[:4])[0]
            response.payload_size = struct.unpack(">I", payload[4:8])[0]
            payload = payload[8:]

        if not payload:
            return response

        if compression == CompressionType.GZIP:
            payload = gzip.decompress(payload)

        if serialization == SerializationType.JSON:
            response.payload_msg = json.loads(payload.decode("utf-8"))

        return response


def extract_text(payload: Any) -> str:
    texts: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                lk = str(k).lower()
                if isinstance(v, str) and lk in {"text", "utterance", "transcript", "result", "sentence"}:
                    item = v.strip()
                    if item:
                        texts.append(item)
                else:
                    walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    if not texts:
        return ""

    seen: set[str] = set()
    merged: list[str] = []
    normed: list[str] = []
    for raw in texts:
        text = str(raw).strip()
        if not text:
            continue
        n = _norm_text(text)
        if not n or n in seen:
            continue
        seen.add(n)
        merged.append(text)
        normed.append(n)

    keep = [True] * len(merged)
    for i in range(len(merged)):
        ni = normed[i]
        if not ni:
            keep[i] = False
            continue
        for j in range(len(merged)):
            if i == j:
                continue
            nj = normed[j]
            if not nj:
                continue
            if len(nj) > len(ni) and ni in nj:
                keep[i] = False
                break

    out = [merged[i] for i, k in enumerate(keep) if k]
    return "\n".join(out).strip()


class RealtimeAsrClient:
    def __init__(
        self,
        on_text: Callable[[str], None],
        on_error: Callable[[str], None],
        ws_url: str | None = None,
    ):
        self.on_text = on_text
        self.on_error = on_error
        self._ws_url = ws_url or os.getenv("DOUBAO_ASR_WS_URL", DEFAULT_WS_URL)

        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)
        self._seq = 1
        self._running = False
        self._sender_task: asyncio.Task | None = None
        self._receiver_task: asyncio.Task | None = None
        self._sent_wav_header = False

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        if aiohttp is None:
            raise RuntimeError("缺少依赖 aiohttp，请先安装 requirements.txt")
        self._running = True
        self._seq = 1
        self._sent_wav_header = False
        self._queue = asyncio.Queue(maxsize=200)

        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(self._ws_url, headers=Protocol.build_auth_headers())

        await self._ws.send_bytes(Protocol.build_full_request(self._seq))
        self._seq += 1

        self._sender_task = asyncio.create_task(self._sender(), name="doubao_asr_sender")
        self._receiver_task = asyncio.create_task(self._receiver(), name="doubao_asr_receiver")

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False

        if self._sender_task:
            await self._sender_task
        if self._receiver_task:
            await self._receiver_task

        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session and not self._session.closed:
            await self._session.close()

        self._ws = None
        self._session = None
        self._sender_task = None
        self._receiver_task = None

    def push_audio(self, pcm_bytes: bytes) -> None:
        if (not self._running) or (not pcm_bytes):
            return
        try:
            self._queue.put_nowait(pcm_bytes)
        except asyncio.QueueFull:
            return

    async def _sender(self) -> None:
        if not self._ws:
            return

        while True:
            if (not self._running) and self._queue.empty():
                await self._ws.send_bytes(Protocol.build_audio_request(self._seq, b"", is_last=True))
                break

            try:
                chunk = await asyncio.wait_for(self._queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue

            is_last = (not self._running) and self._queue.empty()
            if (not self._sent_wav_header) and chunk:
                chunk = build_stream_wav_header(SAMPLE_RATE, CHANNELS, 16) + chunk
                self._sent_wav_header = True

            await self._ws.send_bytes(Protocol.build_audio_request(self._seq, chunk, is_last=is_last))
            if is_last:
                break
            self._seq += 1

    async def _receiver(self) -> None:
        if not self._ws:
            return
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    response = Protocol.parse_response(msg.data)
                    if response.code != 0:
                        detail = f", detail: {response.payload_msg}" if response.payload_msg is not None else ""
                        self.on_error(f"ASR error code: {response.code}{detail}")
                        break

                    text = extract_text(response.payload_msg)
                    if text:
                        self.on_text(text)

                    if response.is_last_package:
                        break
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    self.on_error("WebSocket error")
                    break
        except Exception as exc:
            self.on_error(f"Receive failed: {exc}")
