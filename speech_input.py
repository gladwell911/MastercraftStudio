import asyncio
import threading
import time
import queue
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

import sounddevice as sd

from realtime_asr import BLOCK_FRAMES, CHANNELS, SAMPLE_RATE, RealtimeAsrClient, PcmBuffer

MODE_DIRECT = "direct"
MODE_OPTIMIZE = "optimize"


class CtrlTapDetector:
    def __init__(self, double_tap_ms: int = 200) -> None:
        self.double_tap_s = double_tap_ms / 1000.0
        self._last_tap_by_side: dict[str, float] = {"left": 0.0, "right": 0.0}
        self._last_any_tap = 0.0

    def on_tap_idle(self, side: str) -> bool:
        now = time.monotonic()
        side_key = "right" if side == "right" else "left"
        last = self._last_tap_by_side.get(side_key, 0.0)
        hit = (now - last) <= self.double_tap_s
        self._last_tap_by_side[side_key] = now
        self._last_any_tap = now
        return hit

    def on_tap_recording(self) -> bool:
        now = time.monotonic()
        hit = (now - self._last_any_tap) >= 0.02
        self._last_any_tap = now
        return hit

    def reset(self) -> None:
        self._last_tap_by_side = {"left": 0.0, "right": 0.0}
        self._last_any_tap = 0.0


@dataclass
class _Session:
    generation: int
    mode: str
    started_at: float = field(default_factory=time.monotonic)
    buffer: PcmBuffer = field(default_factory=PcmBuffer)
    intent: threading.Event = field(default_factory=threading.Event)
    audio_done: threading.Event = field(default_factory=threading.Event)
    network_done: bool = False
    cancelled: bool = False
    failed: bool = False
    ready: bool = False
    result: str = ""
    client: object = None
    future: object = None
    delivered: set = field(default_factory=set)
    converged: bool = False
    milestones: dict = field(default_factory=dict)


class VoiceInputController:
    FIRST_FRAME_TIMEOUT = 2.0

    def __init__(self, on_state_change, on_result, on_error, on_stop_recording=None,
                 dispatch=None):
        self.on_state_change, self.on_result, self.on_error = on_state_change, on_result, on_error
        self.on_stop_recording = on_stop_recording or (lambda: None)
        self._dispatch = dispatch or (lambda fn: fn())
        self.detector = CtrlTapDetector()
        self.state = "idle"
        self._lock = threading.Lock()
        self._generation = 0
        self._session = None
        self._closed = False
        self._asr_loop = self._asr_loop_thread = self._audio_thread = None
        self._audio_queue = queue.Queue()
        self._loop_ready = threading.Event()
        self._shutdown_done = threading.Event()
        # Bounded, memory-only metadata. No PCM, transcript or credentials.
        self.diagnostics = deque(maxlen=128)

    def _mark(self, session, name):
        session.milestones[name] = time.monotonic()

    def is_generation_current(self, generation):
        with self._lock:
            return (not self._closed and self._session is not None
                    and self._session.generation == generation and not self._session.cancelled
                    and not self._session.failed)

    @property
    def generation(self):
        with self._lock:
            return self._generation

    def _emit(self, session, kind, *payload):
        def deliver():
            with self._lock:
                if (self._closed or self._session is not session or session.cancelled
                        or kind in session.delivered):
                    return
                if kind != "error" and session.failed:
                    return
                if kind == "ready" and (self.state != "recording" or session.intent.is_set()):
                    return
                session.delivered.add(kind)
                self._mark(session, kind + "_gui")
            if kind == "ready":
                self.on_state_change("开始录音")
            elif kind == "stop":
                self.on_stop_recording()
                self.on_state_change("正在识别")
            elif kind == "error":
                self.on_error(payload[0])
            elif kind == "result":
                self.on_result(*payload)
                self.on_state_change("识别完成，已追加")
        self._mark(session, kind + "_posted")
        self._dispatch(deliver)

    def on_ctrl_keyup(self, combo_used=False, side="left"):
        if self.state == "idle":
            if self.detector.on_tap_idle(side):
                self.start_recording(MODE_DIRECT)
        elif self.state == "starting":
            self.cancel()
        elif self.state == "recording" and self.detector.on_tap_recording():
            self.stop_and_transcribe()

    def _ensure_workers_locked(self):
        if self._asr_loop is None:
            loop = self._asr_loop = asyncio.new_event_loop()
            def run_loop():
                asyncio.set_event_loop(loop)
                loop.call_soon(self._loop_ready.set)
                try:
                    loop.run_forever()
                finally:
                    loop.close()
                    self._shutdown_done.set()
            self._asr_loop_thread = threading.Thread(target=run_loop, name="voice-asr-loop", daemon=True)
            self._asr_loop_thread.start()
            self._audio_thread = threading.Thread(target=self._audio_worker, name="voice-audio", daemon=True)
            self._audio_thread.start()

    def start_recording(self, mode=MODE_DIRECT):
        with self._lock:
            if self.state != "idle" or self._closed:
                return
            self._ensure_workers_locked()
            self._generation += 1
            session = _Session(self._generation, mode if mode in (MODE_DIRECT, MODE_OPTIMIZE) else MODE_DIRECT)
            self._session = session
            self.state = "starting"
            self.detector.reset()
            self._mark(session, "accepted")
            session.client = RealtimeAsrClient(
                on_text=lambda text: self._stream_text(session, text),
                on_error=lambda message: self._fail(session, message),
                buffer=session.buffer, started_at=session.started_at)
            session.future = asyncio.run_coroutine_threadsafe(self._network(session), self._asr_loop)
            self._audio_queue.put(session)

    def _stream_text(self, session, text):
        with self._lock:
            if self._session is session and not session.cancelled and not session.failed:
                session.result = str(text or "").strip()

    async def _network(self, session):
        self._mark(session, "connect_start")
        try:
            await session.client.run()
        except asyncio.CancelledError:
            with session.buffer.lock:
                failure = session.buffer.failure
            if failure:
                self._fail(session, failure)
        except Exception as exc:
            self._fail(session, str(exc))
        finally:
            try:
                await session.client._cleanup()
            except Exception as exc:
                self._fail(session, str(exc))
            with self._lock:
                session.network_done = True
            self._converge(session)

    def _fail(self, session, message):
        with self._lock:
            if self._session is not session or session.cancelled or session.failed or self._closed:
                return
            with session.buffer.lock:
                if session.buffer.committed:
                    return
                session.buffer.failure = session.buffer.failure or message
                message = session.buffer.failure
            session.failed = True
            session.intent.set()
            self.state = "cancelling"
        self._emit(session, "error", message)
        self._abort(session)

    def _abort(self, session):
        if self._asr_loop and not self._asr_loop.is_closed():
            async def abort():
                await session.client.abort()
                task = session.client._run_task
                if task and not task.done():
                    task.cancel()
            asyncio.run_coroutine_threadsafe(abort(), self._asr_loop)

    def _audio_worker(self):
        while True:
            session = self._audio_queue.get()
            if session is None:
                return
            stream = None
            try:
                if session.intent.is_set():
                    continue
                def callback(indata, _frames, _time_info, status):
                    if status:
                        session.buffer.fail("录音数据不完整，本次未写入，请重试")
                    else:
                        session.buffer.append(bytes(indata))
                self._mark(session, "device_open")
                stream = sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=BLOCK_FRAMES,
                                           channels=CHANNELS, dtype="int16", callback=callback)
                if session.intent.is_set():
                    continue
                stream.start()
                self._mark(session, "device_start")
                deadline = time.monotonic() + self.FIRST_FRAME_TIMEOUT
                while not session.intent.is_set():
                    with session.buffer.lock:
                        failure = session.buffer.failure
                    if failure:
                        self._fail(session, failure)
                        break
                    if session.buffer.first_frame.is_set():
                        with self._lock:
                            if (self._session is session and not session.intent.is_set()
                                    and not session.failed and not self._closed):
                                self.state = "recording"
                                session.ready = True
                                session.milestones["first_frame"] = session.buffer.first_frame_at
                        if session.ready:
                            self._emit(session, "ready")
                        break
                    if time.monotonic() >= deadline:
                        self._fail(session, "麦克风不可用：未收到录音数据")
                        break
                    session.intent.wait(.01)
                while not session.intent.wait(.02):
                    with session.buffer.lock:
                        failure = session.buffer.failure
                    if failure:
                        self._fail(session, failure)
            except Exception as exc:
                self._fail(session, f"麦克风不可用：{exc}")
            finally:
                if stream is not None:
                    try:
                        stream.stop()
                    except Exception as exc:
                        self._fail(session, f"麦克风不可用：{exc}")
                    finally:
                        try:
                            stream.close()
                        except Exception as exc:
                            self._fail(session, f"麦克风不可用：{exc}")
                # PortAudio stop/close has quiesced its final callback before sealing.
                self._mark(session, "device_stopped")
                session.audio_done.set()
                if not session.cancelled and not session.failed:
                    session.buffer.seal()
                    self._mark(session, "sealed")
                self._converge(session)

    def stop_and_transcribe(self):
        with self._lock:
            if self.state == "starting":
                starting = True
            elif self.state == "recording" and self._session:
                starting = False
                session = self._session
                self.state = "finishing"
                session.intent.set()
                self._mark(session, "stop")
            else:
                return
        if starting:
            self.cancel()
        else:
            self._emit(session, "stop")

    def _converge(self, session):
        result = None
        with self._lock:
            if session.converged or not session.audio_done.is_set() or not session.network_done:
                return
            if self._session is not session:
                return
            session.converged = True
            with session.buffer.lock:
                valid = (not session.failed and not session.cancelled and not self._closed
                         and not session.buffer.failure and not session.buffer.cancelled
                         and session.buffer.sealed and not session.buffer.size
                         and session.client.terminal_sent and session.client.final_received)
                if valid:
                    session.buffer.committed = True
                    result = session.result
                session.buffer.frames.clear()
                session.buffer.size = 0
            self._mark(session, "cleanup")
            session.milestones.update(session.client.milestones)
            # Keep the bounded metadata record live for queued GUI delivery.
            self.diagnostics.append((session.generation, session.milestones, session.buffer.peak))
            self.state = "closed" if self._closed else "idle"
            self.detector.reset()
        if result:
            self._emit(session, "result", result, session.mode)
        elif valid:
            self._emit(session, "error", "未识别到语音内容")

    def cancel(self):
        with self._lock:
            session = self._session
            if not session or self.state == "closed":
                return
            session.cancelled = True
            session.intent.set()
            session.buffer.discard()
            self.state = "idle" if session.converged else "cancelling"
            self.detector.reset()
        self._abort(session)

    def shutdown(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self.state = "closed"
            session = self._session
            if session:
                session.cancelled = True
                session.intent.set()
                session.buffer.discard()
            loop = self._asr_loop
            self._audio_queue.put(None)
        if loop is None:
            self._shutdown_done.set()
            return
        if session:
            self._abort(session)
        async def finish_shutdown():
            # Device-native calls cannot safely be killed. Keep their owner isolated.
            if session:
                await session.client.abort()
            current = asyncio.current_task()
            tasks = [t for t in asyncio.all_tasks() if t is not current]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            loop.call_soon(loop.stop)
        asyncio.run_coroutine_threadsafe(finish_shutdown(), loop)
