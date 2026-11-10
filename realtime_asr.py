from __future__ import annotations

import asyncio
import gzip
import json
import os
import struct
import uuid
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

try:
    import aiohttp
except Exception:  # pragma: no cover - handled at runtime
    aiohttp = None

DEFAULT_APP_ID = "5685852259"
DEFAULT_ACCESS_TOKEN = "tCNf26V6T1NYLoauifmAB46QPKv1rs_0"
DEFAULT_WS_URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"
# Model generation is selected by resource ID; model_name remains "bigmodel".
DEFAULT_RESOURCE_ID = "volc.seedasr.sauc.duration"

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


class PcmBuffer:
    """The sole PCM queue. Callback-side work never takes the coordinator lock."""
    MAX_BYTES = 640000
    MAX_FRAME_BYTES = 6400

    def __init__(self):
        self.lock = threading.Lock()
        self.frames = deque()
        self.size = 0
        self.peak = 0
        self.sealed = False
        self.failure = ""
        self.cancelled = False
        self.committed = False
        self.first_frame = threading.Event()
        self.changed = threading.Event()
        self._loop = None
        self._event = None
        self._wake_pending = False
        self._failure_callback = None
        self.first_frame_at = None

    def bind(self, loop, event, failure_callback):
        with self.lock:
            self._loop, self._event = loop, event
            self._failure_callback = failure_callback
            self._wake_locked()

    def _wake_locked(self):
        self.changed.set()
        if self._loop is not None and not self._wake_pending:
            self._wake_pending = True
            self._loop.call_soon_threadsafe(self._deliver_wake)

    def _deliver_wake(self):
        with self.lock:
            self._wake_pending = False
            event, failure, callback = self._event, self.failure, self._failure_callback
        if event:
            event.set()
        if failure and callback:
            callback(failure)

    def append(self, data):
        with self.lock:
            if self.cancelled or self.failure or self.sealed or self.committed:
                return False
            if not data:
                return False
            if len(data) > self.MAX_FRAME_BYTES or self.size + len(data) > self.MAX_BYTES:
                self.failure = "语音缓存已满，本次未写入，请重试"
                self._wake_locked()
                return False
            self.frames.append(data)
            self.size += len(data)
            self.peak = max(self.peak, self.size)
            if self.first_frame_at is None:
                self.first_frame_at = time.monotonic()
            self.first_frame.set()
            self._wake_locked()
            return True

    def fail(self, message):
        with self.lock:
            if not self.committed and not self.cancelled and not self.failure:
                self.failure = message
                self._wake_locked()

    def seal(self):
        with self.lock:
            self.sealed = True
            self._wake_locked()

    def discard(self):
        with self.lock:
            self.cancelled = True
            self.frames.clear()
            self.size = 0
            self._wake_locked()


class RealtimeAsrClient:
    CONNECT_TIMEOUT = 10.0
    DRAIN_TIMEOUT = 12.0
    FINAL_TIMEOUT = 12.0
    CLEANUP_TIMEOUT = 3.0

    def __init__(self, on_text, on_error, ws_url=None, buffer=None, started_at=None):
        self.on_text, self.on_error = on_text, on_error
        self.buffer = buffer if buffer is not None else PcmBuffer()
        self.started_at = started_at if started_at is not None else time.monotonic()
        self._ws_url = ws_url or os.getenv("DOUBAO_ASR_WS_URL", DEFAULT_WS_URL)
        self._session = self._ws = None
        self._seq = 1
        self._running = False
        self._sender_task = self._receiver_task = self._connect_task = None
        self._run_task = None
        self._sent_wav_header = False
        self.terminal_sent = False
        self._terminal_send_task = None
        self.final_received = False
        self._terminal_time = None
        self._event = None
        self._aborted = False
        self._cleanup_task = None
        self._failure_cancel_requested = False
        self.cleanup_error = ""
        self.milestones = {}

    @property
    def running(self):
        return self._running

    async def _connect(self):
        if aiohttp is None:
            raise RuntimeError("缺少依赖 aiohttp")
        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(self._ws_url, headers=Protocol.build_auth_headers())
        await self._ws.send_bytes(Protocol.build_full_request(self._seq))
        self._seq += 1

    async def start(self):
        if self._aborted:
            raise asyncio.CancelledError()
        if self._connect_task is None:
            self._event = asyncio.Event()
            self.buffer.bind(asyncio.get_running_loop(), self._event, self._buffer_failed)
            self._connect_task = asyncio.create_task(self._connect())
        try:
            remaining = self.CONNECT_TIMEOUT - (time.monotonic() - self.started_at)
            await asyncio.wait_for(asyncio.shield(self._connect_task), max(0, remaining))
            self.milestones["connected"] = time.monotonic()
            if self._aborted:
                raise asyncio.CancelledError()
            if self._sender_task is None:
                self._running = True
                self._sender_task = asyncio.create_task(self._sender(), name="voice-asr-sender")
                self._receiver_task = asyncio.create_task(self._receiver(), name="voice-asr-receiver")
        except BaseException:
            await self.abort()
            raise

    def _buffer_failed(self, message):
        # Failure is already irreversible under the FIFO lock.
        if (not self._failure_cancel_requested and self._cleanup_task is None
                and self._run_task and not self._run_task.done()):
            self._failure_cancel_requested = True
            self._run_task.cancel()

    async def run(self):
        self._run_task = asyncio.current_task()
        try:
            try:
                await self.start()
            except asyncio.TimeoutError as exc:
                raise RuntimeError("识别服务连接超时，本次未写入") from exc
            except Exception as exc:
                raise RuntimeError(f"识别服务连接失败：{exc}") from exc
            # Receiver rejects early final/EOF. A sender error must interrupt it immediately.
            pending = {self._sender_task, self._receiver_task}
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            if not self.final_received:
                raise RuntimeError("识别服务提前结束，本次未写入")
        except OSError as exc:
            raise RuntimeError("识别服务网络失败，本次未写入") from exc
        finally:
            await self._cleanup()

    def push_audio(self, pcm_bytes):
        return self.buffer.append(bytes(pcm_bytes))

    async def stop(self):
        self.buffer.seal()
        if self._run_task:
            await asyncio.shield(self._run_task)
        else:
            try:
                await self.start()
                await asyncio.gather(self._sender_task, self._receiver_task)
            finally:
                await self._cleanup()

    async def abort(self):
        self._aborted = True
        self.buffer.discard()
        try:
            await self._cleanup()
        except RuntimeError:
            # Cancellation has no user error. The bounded cleanup failure remains
            # observable through cleanup_error, and run() cannot commit success.
            return

    async def _cleanup(self):
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._close_resources())
        await asyncio.shield(self._cleanup_task)

    async def _close_resources(self):
        current = asyncio.current_task()
        tasks = [t for t in (self._connect_task, self._sender_task, self._receiver_task)
                 if t and t is not current]
        for task in tasks:
            if not task.done():
                task.cancel()
        async def close():
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            try:
                if self._ws is not None and not self._ws.closed:
                    await self._ws.close()
            finally:
                if self._session is not None and not self._session.closed:
                    await self._session.close()
        try:
            await asyncio.wait_for(close(), self.CLEANUP_TIMEOUT)
        except Exception as exc:
            self.cleanup_error = f"识别服务资源清理失败：{type(exc).__name__}"
            raise RuntimeError(self.cleanup_error) from exc
        finally:
            self._running = False

    async def _sender(self):
        drain_deadline = None
        while True:
            self._event.clear()
            with self.buffer.lock:
                if self.buffer.failure:
                    raise RuntimeError(self.buffer.failure)
                if self.buffer.cancelled:
                    raise asyncio.CancelledError()
                if self.buffer.sealed and drain_deadline is None:
                    drain_deadline = time.monotonic() + self.DRAIN_TIMEOUT
                chunk = self.buffer.frames.popleft() if self.buffer.frames else None
                if chunk is not None:
                    self.buffer.size -= len(chunk)
                sealed = self.buffer.sealed
            if chunk is None and not sealed:
                await self._event.wait()
                continue
            if chunk is None:
                packet = Protocol.build_audio_request(self._seq, b"", True)
            else:
                if not self._sent_wav_header:
                    chunk = build_stream_wav_header(SAMPLE_RATE, CHANNELS, 16) + chunk
                    self._sent_wav_header = True
                packet = Protocol.build_audio_request(self._seq, chunk, False)
            # A seal must also bound a send that was already blocked before seal.
            async def send_packet():
                await self._ws.send_bytes(packet)
                if chunk is None:
                    self.terminal_sent = True
                    self._terminal_time = time.monotonic()
                    self.milestones["terminal"] = self._terminal_time
            send = asyncio.create_task(send_packet())
            if chunk is None:
                self._terminal_send_task = send
            try:
                while not send.done():
                    with self.buffer.lock:
                        if self.buffer.sealed and drain_deadline is None:
                            drain_deadline = time.monotonic() + self.DRAIN_TIMEOUT
                    budget = None if drain_deadline is None else drain_deadline - time.monotonic()
                    if budget is not None and budget <= 0:
                        raise RuntimeError("识别服务发送超时，本次未写入")
                    await asyncio.wait({send}, timeout=min(.05, budget) if budget is not None else .05)
                send.result()
            finally:
                if not send.done():
                    send.cancel()
                    await asyncio.gather(send, return_exceptions=True)
            self._seq += 1
            if chunk is None:
                return

    async def _receiver(self):
        iterator = self._ws.__aiter__()
        while True:
            receive = asyncio.create_task(iterator.__anext__())
            try:
                while not receive.done():
                    if self._terminal_time is not None and time.monotonic() - self._terminal_time >= self.FINAL_TIMEOUT:
                        raise RuntimeError("识别结果超时，本次未写入")
                    await asyncio.wait({receive}, timeout=.05)
                try:
                    msg = receive.result()
                except StopAsyncIteration as exc:
                    raise RuntimeError("识别服务提前关闭，本次未写入") from exc
            finally:
                if not receive.done():
                    receive.cancel()
                    await asyncio.gather(receive, return_exceptions=True)
            if msg.type == aiohttp.WSMsgType.BINARY:
                response = Protocol.parse_response(msg.data)
                if response.code:
                    raise RuntimeError(f"识别服务错误：{response.code}")
                if response.is_last_package:
                    if not self.terminal_sent and self._terminal_send_task is not None:
                        # The server may receive EOF before the transport's send
                        # await returns. Hold final until that exact send succeeds.
                        await asyncio.wait({self._terminal_send_task})
                        if self._terminal_send_task.cancelled():
                            raise RuntimeError("识别服务终包发送未完成，本次未写入")
                        self._terminal_send_task.result()
                    with self.buffer.lock:
                        if (not self.terminal_sent or not self.buffer.sealed or self.buffer.size
                                or self.buffer.failure or self.buffer.cancelled):
                            raise RuntimeError("识别服务提前结束，本次未写入")
                    self.final_received = True
                    self.milestones["final"] = time.monotonic()
                text = extract_text(response.payload_msg)
                if text:
                    self.on_text(text)
                if self.final_received:
                    return
            elif msg.type == aiohttp.WSMsgType.ERROR:
                raise RuntimeError("识别服务网络错误，本次未写入")
