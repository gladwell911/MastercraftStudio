import asyncio
import threading
import time
from typing import Callable

import sounddevice as sd

from realtime_asr import BLOCK_FRAMES, CHANNELS, SAMPLE_RATE, RealtimeAsrClient

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


class VoiceInputController:
    def __init__(
        self,
        on_state_change: Callable[[str], None],
        on_result: Callable[[str, str], None],
        on_error: Callable[[str], None],
        on_stop_recording: Callable[[], None] | None = None,
    ) -> None:
        self.on_state_change = on_state_change
        self.on_result = on_result
        self.on_error = on_error
        self.on_stop_recording = on_stop_recording or (lambda: None)
        self.detector = CtrlTapDetector()
        self.state = "idle"
        self._stream: sd.RawInputStream | None = None
        self._lock = threading.Lock()
        self._record_started_at = 0.0
        self._cancelled = False
        self._active_mode = MODE_DIRECT
        self._asr_loop: asyncio.AbstractEventLoop | None = None
        self._asr_loop_thread: threading.Thread | None = None
        self._asr_client: RealtimeAsrClient | None = None
        self._last_text = ""

    def on_ctrl_keyup(self, combo_used: bool = False, side: str = "left") -> None:
        if self.state == "idle":
            if self.detector.on_tap_idle(side):
                self.start_recording(mode=MODE_DIRECT)
        elif self.state == "recording":
            if self.detector.on_tap_recording():
                self.stop_and_transcribe()

    def _ensure_loop(self) -> None:
        if self._asr_loop and self._asr_loop.is_running():
            return
        self._asr_loop = asyncio.new_event_loop()
        self._asr_loop_thread = threading.Thread(target=self._run_loop, daemon=True)
        self._asr_loop_thread.start()

    def _run_loop(self) -> None:
        if not self._asr_loop:
            return
        asyncio.set_event_loop(self._asr_loop)
        self._asr_loop.run_forever()

    def _on_stream_text(self, text: str) -> None:
        with self._lock:
            incoming = str(text or "").strip()
            if incoming:
                self._last_text = incoming

    def _on_stream_error(self, message: str) -> None:
        self.on_error(message)

    def start_recording(self, mode: str = MODE_DIRECT) -> None:
        with self._lock:
            if self.state != "idle":
                return
            try:
                self._cancelled = False
                self._active_mode = mode if mode in (MODE_DIRECT, MODE_OPTIMIZE) else MODE_DIRECT
                self._record_started_at = time.monotonic()
                self._last_text = ""
                self._ensure_loop()
                self._asr_client = RealtimeAsrClient(
                    on_text=self._on_stream_text,
                    on_error=self._on_stream_error,
                )
                if not self._asr_loop:
                    raise RuntimeError("语音识别事件循环未初始化")

                start_fut = asyncio.run_coroutine_threadsafe(self._asr_client.start(), self._asr_loop)
                start_fut.result(timeout=10)

                def _callback(indata, _frames, _time_info, status):
                    if status:
                        return
                    if self._cancelled or not self._asr_client or not self._asr_loop:
                        return
                    self._asr_loop.call_soon_threadsafe(self._asr_client.push_audio, bytes(indata))

                self._stream = sd.RawInputStream(
                    samplerate=SAMPLE_RATE,
                    blocksize=BLOCK_FRAMES,
                    channels=CHANNELS,
                    dtype="int16",
                    callback=_callback,
                )
                self._stream.start()
                self.state = "recording"
                self.on_state_change("开始录音")
            except Exception as exc:
                try:
                    if self._stream is not None:
                        self._stream.stop()
                        self._stream.close()
                except Exception:
                    pass
                self._stream = None
                if self._asr_client and self._asr_loop:
                    try:
                        stop_fut = asyncio.run_coroutine_threadsafe(self._asr_client.stop(), self._asr_loop)
                        stop_fut.result(timeout=5)
                    except Exception:
                        pass
                self._asr_client = None
                self.state = "idle"
                self.on_error(f"麦克风不可用：{exc}")

    def stop_and_transcribe(self) -> None:
        with self._lock:
            if self.state != "recording":
                return
            self.state = "transcribing"
            mode = self._active_mode
            self.on_stop_recording()
            try:
                if self._stream is not None:
                    self._stream.stop()
                    self._stream.close()
            except Exception:
                pass
            self._stream = None
            self.on_state_change("正在识别")

        def _worker():
            try:
                if self._asr_client and self._asr_loop:
                    stop_fut = asyncio.run_coroutine_threadsafe(self._asr_client.stop(), self._asr_loop)
                    stop_fut.result(timeout=12)
                if self._cancelled:
                    return
                with self._lock:
                    text = self._last_text
                    self._last_text = ""
                    self._asr_client = None
                if not text:
                    self.on_error("未识别到语音内容")
                else:
                    self.on_result(text, mode)
                    self.on_state_change("识别完成，已追加")
            except Exception as exc:
                if self._cancelled:
                    return
                self.on_error(str(exc))
            finally:
                with self._lock:
                    self.state = "idle"
                    self._record_started_at = 0.0

        threading.Thread(target=_worker, daemon=True).start()

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            try:
                if self._stream is not None:
                    self._stream.stop()
                    self._stream.close()
            except Exception:
                pass
            self._stream = None
            self.state = "idle"
            self._record_started_at = 0.0
            self._active_mode = MODE_DIRECT
            self._last_text = ""
            client = self._asr_client
            loop = self._asr_loop
            self._asr_client = None
        if client and loop:
            try:
                stop_fut = asyncio.run_coroutine_threadsafe(client.stop(), loop)
                stop_fut.result(timeout=5)
            except Exception:
                pass
