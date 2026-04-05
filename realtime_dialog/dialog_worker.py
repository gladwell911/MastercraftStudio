import asyncio
import copy
import os
import queue
import tempfile
import threading
import time
import uuid
import wave
import warnings
from typing import Any, Callable, Dict, Optional

import imageio_ffmpeg
import pyaudio
os.environ.setdefault("FFMPEG_BINARY", imageio_ffmpeg.get_ffmpeg_exe())
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message="Couldn't find ffmpeg or avconv*")
    from pydub import AudioSegment

try:
    from . import config
    from .realtime_dialog_client import RealtimeDialogClient
except ImportError:
    import config
    from realtime_dialog_client import RealtimeDialogClient


WorkerCallback = Callable[[str, Dict[str, Any]], None]
AudioFactory = Callable[[], Any]
ClientFactory = Callable[..., RealtimeDialogClient]


class DialogWorker:
    def __init__(
        self,
        on_event: WorkerCallback,
        speaker: Optional[str] = None,
        speech_rate: Optional[int] = None,
        system_role: Optional[str] = None,
        audio_factory: Optional[AudioFactory] = None,
        client_factory: Optional[ClientFactory] = None,
    ):
        self.on_event = on_event

        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.loop_thread: Optional[threading.Thread] = None
        self.loop_ready = threading.Event()

        self.client: Optional[RealtimeDialogClient] = None
        self.session_id = str(uuid.uuid4())

        self._audio_factory = audio_factory or pyaudio.PyAudio
        self._client_factory = client_factory or RealtimeDialogClient
        self.audio = self._audio_factory()
        self.input_stream: Optional[pyaudio.Stream] = None
        self.output_stream: Optional[pyaudio.Stream] = None
        self.output_stream_info: Dict[str, Any] = {}
        self.audio_queue: "queue.Queue[Optional[bytes]]" = queue.Queue()
        self.player_thread: Optional[threading.Thread] = None
        self.player_running = False
        self.last_audio_event_ts = 0.0
        self.audio_bytes_received = 0
        self._audio_packet_received = False
        self._audio_playback_failed = False
        self._connecting = False
        self._greeting_in_progress = False
        self._greeting_timeout_task: Optional[asyncio.Task] = None
        self._ignore_remote_audio = False

        self.connected = False
        self.recording = False
        self.shutting_down = False

        self.recv_task: Optional[asyncio.Task] = None
        self.mic_task: Optional[asyncio.Task] = None
        self.hello_sent = False
        self.last_server_text = ""
        self.current_speaker = speaker or config.DEFAULT_SPEAKER
        if speech_rate is None:
            self.current_speed_ratio = config.DEFAULT_SPEED_RATIO
            self.current_speech_rate = config.speed_ratio_to_speech_rate(config.DEFAULT_SPEED_RATIO)
        else:
            self.current_speech_rate = int(min(max(int(speech_rate), -50), 100))
            self.current_speed_ratio = config.speech_rate_to_speed_ratio(self.current_speech_rate)
        self.current_system_role = (system_role or getattr(config, "DEFAULT_SYSTEM_ROLE", "")).strip()
        self._voice_cfg_lock: Optional[asyncio.Lock] = None
        self._reconnect_lock: Optional[asyncio.Lock] = None
        self._reconnect_block_until_ts = 0.0

        # pydub conversion backend
        AudioSegment.converter = imageio_ffmpeg.get_ffmpeg_exe()

    def _emit(self, event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
        if payload is None:
            payload = {}
        self.on_event(event_type, payload)

    def start(self) -> None:
        if self.loop_thread and self.loop_thread.is_alive():
            return
        self.loop_thread = threading.Thread(target=self._run_event_loop, daemon=True)
        self.loop_thread.start()
        self.loop_ready.wait(timeout=5)

    def _run_event_loop(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.loop_ready.set()
        self.loop.run_forever()

    def _submit(self, coro: Any):
        if not self.loop or not self.loop.is_running():
            raise RuntimeError("DialogWorker event loop is not running")
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def is_connecting(self) -> bool:
        return self._connecting

    def _is_quota_error(self, message: str) -> bool:
        lowered = message.lower()
        return "45000292" in message or "quota exceeded" in lowered or "qpm" in lowered

    def connect(self) -> None:
        if self.connected or self._connecting or self.shutting_down:
            return
        self.start()
        self._connecting = True
        self._submit(self._connect())

    async def _connect(self) -> None:
        if self.connected:
            self._emit("log", {"message": "Already connected."})
            self._connecting = False
            return
        try:
            self._emit("status", {"message": "Connecting..."})
            if self.client:
                try:
                    await self.client.close()
                except Exception:
                    pass
            self.client = self._client_factory(
                config=self._new_ws_config(),
                session_id=self.session_id,
                output_audio_format="pcm_s16le",
                mod="audio",
                recv_timeout=120,
                tts_speaker=self.current_speaker,
                tts_speed_ratio=self.current_speed_ratio,
                tts_speech_rate=self.current_speech_rate,
                dialog_system_role=self.current_system_role,
            )
            await self.client.connect()

            self._reset_audio_playback_state()
            self._ignore_remote_audio = False
            self._open_output_stream()
            self._prime_output_stream()
            self._start_player_thread()

            self.recv_task = asyncio.create_task(self._receive_loop())
            self.connected = True
            self._emit("connected", {"logid": self.client.logid})
            self._emit("log", {"message": f"Connected. logid={self.client.logid}"})
            self._emit("status", {"message": "Connected"})
            self.hello_sent = False
        except Exception as exc:
            self.connected = False
            detail = str(exc)
            if self._is_quota_error(detail):
                self._reconnect_block_until_ts = time.time() + 12
            self._emit("error", {"message": f"Connect failed: {exc}"})
            self._emit("status", {"message": "Connect failed"})
        finally:
            self._connecting = False

    def _reset_audio_playback_state(self) -> None:
        self.last_audio_event_ts = 0.0
        self.audio_bytes_received = 0
        self._audio_packet_received = False
        self._audio_playback_failed = False

    def _open_output_stream(self) -> None:
        if self.output_stream is not None:
            return
        cfg = config.output_audio_config
        attempts = self._build_output_stream_attempts()
        failures: list[str] = []
        default_device = self._safe_get_default_output_device_info()
        for index, attempt in enumerate(attempts, start=1):
            params = dict(attempt["params"])
            try:
                self.output_stream = self.audio.open(**params)
                self.output_stream_info = {
                    "attempt": index,
                    "rate": params["rate"],
                    "channels": params["channels"],
                    "format": "pcm_s16le",
                    "device_index": params.get("output_device_index"),
                    "device_name": self._resolve_output_device_name(params.get("output_device_index"), default_device),
                }
                msg = (
                    "Output stream opened: "
                    f"attempt={index}, rate={params['rate']}, channels={params['channels']}, "
                    f"format=pcm_s16le, device={self.output_stream_info['device_name']}"
                )
                self._emit("log", {"message": msg})
                return
            except Exception as exc:
                failure = (
                    f"attempt={index}, rate={params['rate']}, channels={params['channels']}, "
                    f"device_index={params.get('output_device_index')}, error={exc}"
                )
                failures.append(failure)
                self._emit("log", {"message": f"Open output stream failed: {failure}"})

        default_name = self._resolve_output_device_name(
            default_device.get("index") if isinstance(default_device, dict) else None,
            default_device,
        )
        detail = "; ".join(failures) if failures else "unknown error"
        raise RuntimeError(
            "Open output stream failed after all attempts. "
            f"default_device={default_name}, attempts={detail}"
        )

    def _build_output_stream_attempts(self) -> list[Dict[str, Any]]:
        cfg = config.output_audio_config
        attempts = [
            {
                "params": {
                    "format": cfg["bit_size"],
                    "channels": cfg["channels"],
                    "rate": cfg["sample_rate"],
                    "output": True,
                    "frames_per_buffer": cfg["chunk"],
                }
            }
        ]
        default_device = self._safe_get_default_output_device_info()
        if isinstance(default_device, dict) and default_device.get("index") is not None:
            attempts.append(
                {
                    "params": {
                        "format": cfg["bit_size"],
                        "channels": cfg["channels"],
                        "rate": cfg["sample_rate"],
                        "output": True,
                        "frames_per_buffer": cfg["chunk"],
                        "output_device_index": int(default_device["index"]),
                    }
                }
            )
        attempts.append(
            {
                "params": {
                    "format": pyaudio.paInt16,
                    "channels": 2,
                    "rate": 24000,
                    "output": True,
                    "frames_per_buffer": cfg["chunk"],
                }
            }
        )
        return attempts

    def _safe_get_default_output_device_info(self) -> Dict[str, Any]:
        try:
            info = self.audio.get_default_output_device_info()
            if isinstance(info, dict):
                return info
        except Exception:
            pass
        return {}

    def _resolve_output_device_name(self, device_index: Optional[int], default_info: Optional[Dict[str, Any]] = None) -> str:
        if default_info and device_index is not None and default_info.get("index") == device_index:
            return str(default_info.get("name") or f"index={device_index}")
        if device_index is not None:
            try:
                info = self.audio.get_device_info_by_index(int(device_index))
                return str(info.get("name") or f"index={device_index}")
            except Exception:
                return f"index={device_index}"
        if default_info:
            return str(default_info.get("name") or "default")
        return "default"

    def _describe_output_stream(self) -> str:
        if not self.output_stream_info:
            return "unknown-output"
        return (
            f"device={self.output_stream_info.get('device_name', 'unknown')}, "
            f"rate={self.output_stream_info.get('rate', 'unknown')}, "
            f"channels={self.output_stream_info.get('channels', 'unknown')}, "
            f"format={self.output_stream_info.get('format', 'unknown')}"
        )

    def _prime_output_stream(self) -> None:
        if not self.output_stream:
            return
        rate = int(self.output_stream_info.get("rate") or config.output_audio_config["sample_rate"])
        channels = int(self.output_stream_info.get("channels") or config.output_audio_config["channels"])
        # Warm the speaker path once during connect so the first remote packet is less likely to stutter.
        silence = b"\x00" * max(1, int(rate * channels * 2 * 0.12))
        try:
            self.output_stream.write(silence)
            self._emit(
                "log",
                {"message": f"Output stream primed with {len(silence)} bytes of silence."},
            )
        except Exception as exc:
            self._emit("log", {"message": f"Output stream prime skipped: {exc}; {self._describe_output_stream()}"})

    def _start_player_thread(self) -> None:
        if self.player_thread and self.player_thread.is_alive():
            return
        self.player_running = True
        self.player_thread = threading.Thread(target=self._player_loop, daemon=True)
        self.player_thread.start()

    def _player_loop(self) -> None:
        while self.player_running:
            try:
                chunk = self.audio_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if chunk is None:
                continue
            try:
                if self.output_stream:
                    self.output_stream.write(chunk)
            except Exception as exc:
                self._audio_playback_failed = True
                self._emit(
                    "error",
                    {"message": f"Audio playback error: {exc}; {self._describe_output_stream()}"},
                )
                self._emit("status", {"message": "Audio playback failed"})
                time.sleep(0.05)

    def play_greeting(self) -> None:
        self.start()
        self._submit(self._play_greeting())

    async def _play_greeting(self) -> None:
        if self._greeting_in_progress:
            return
        if not await self._ensure_client_session():
            self._emit("error", {"message": "Greeting failed: session is not connected."})
            return
        try:
            self._greeting_in_progress = True
            await self.client.say_hello()
            self._emit("status", {"message": "Greeting"})
            self._emit("log", {"message": "Greeting sent."})
            self._schedule_greeting_timeout()
        except Exception as exc:
            self._greeting_in_progress = False
            self._cancel_greeting_timeout()
            self._emit("error", {"message": f"Greeting failed: {exc}"})

    def _schedule_greeting_timeout(self, timeout_seconds: float = 15.0) -> None:
        self._cancel_greeting_timeout()
        self._greeting_timeout_task = asyncio.create_task(self._greeting_timeout_loop(timeout_seconds))

    def _cancel_greeting_timeout(self) -> None:
        task = self._greeting_timeout_task
        self._greeting_timeout_task = None
        if task and not task.done():
            task.cancel()

    async def _greeting_timeout_loop(self, timeout_seconds: float) -> None:
        try:
            await asyncio.sleep(timeout_seconds)
        except asyncio.CancelledError:
            return
        if self._greeting_in_progress and not self.shutting_down:
            self._greeting_in_progress = False
            self._emit("error", {"message": "Greeting timed out."})

    def start_mic(self) -> None:
        self.start()
        self._submit(self._start_mic())

    async def _start_mic(self) -> None:
        if self.recording:
            self._emit("log", {"message": "Microphone is already recording."})
            return
        if not self.client or not self.client.is_ws_open():
            self._emit("error", {"message": "Start microphone failed: not connected yet."})
            return
        try:
            cfg = config.input_audio_config
            self.input_stream = self.audio.open(
                format=cfg["bit_size"],
                channels=cfg["channels"],
                rate=cfg["sample_rate"],
                input=True,
                frames_per_buffer=cfg["chunk"],
            )
            self.recording = True
            self.mic_task = asyncio.create_task(self._mic_loop())
            self._emit("recording_started", {})
            self._emit("status", {"message": "Recording"})
            self._emit("log", {"message": "Microphone capture started."})
        except Exception as exc:
            self._emit("error", {"message": f"Start microphone failed: {exc}"})

    async def _mic_loop(self) -> None:
        while self.recording:
            try:
                audio_data = self.input_stream.read(
                    config.input_audio_config["chunk"],
                    exception_on_overflow=False,
                )
                if not audio_data:
                    await asyncio.sleep(0.01)
                    continue
                if not self.client or not self.client.is_ws_open():
                    self._handle_session_disconnect("microphone uplink disconnected")
                    break
                await self.client.task_request(audio_data)
                await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._handle_session_disconnect(str(exc))
                self._emit("error", {"message": f"Microphone capture error: {exc}"})
                break

    def stop_mic(self) -> None:
        self._submit(self._stop_mic())

    async def _stop_mic(self) -> None:
        if not self.recording:
            return
        self.recording = False
        if self.mic_task:
            self.mic_task.cancel()
            try:
                await self.mic_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            self.mic_task = None

        self._close_input_stream()

        if self.client:
            try:
                # Send enough silence to trigger end-of-speech (about 2 seconds).
                silence_chunk = b"\x00" * (config.input_audio_config["chunk"] * 2)
                for _ in range(10):
                    await self.client.task_request(silence_chunk)
                    await asyncio.sleep(0.02)
            except Exception:
                pass

        self._emit("recording_stopped", {})
        self._emit("status", {"message": "Connected"})
        self._emit("log", {"message": "Microphone capture stopped."})

    def send_text(self, text: str) -> None:
        self._submit(self._send_text(text))

    def set_voice_config(self, speaker: str, speed_ratio: float) -> None:
        self._submit(self._set_voice_config(speaker, speed_ratio=speed_ratio))

    def set_voice_config_by_speech_rate(self, speaker: str, speech_rate: int) -> None:
        self._submit(self._set_voice_config(speaker, speech_rate=speech_rate))

    async def _set_voice_config(
        self,
        speaker: str,
        speed_ratio: Optional[float] = None,
        speech_rate: Optional[int] = None,
    ) -> None:
        if self.shutting_down:
            return
        if self._voice_cfg_lock is None:
            self._voice_cfg_lock = asyncio.Lock()

        async with self._voice_cfg_lock:
            await self._set_voice_config_locked(
                speaker=speaker,
                speed_ratio=speed_ratio,
                speech_rate=speech_rate,
            )

    async def _set_voice_config_locked(
        self,
        speaker: str,
        speed_ratio: Optional[float] = None,
        speech_rate: Optional[int] = None,
    ) -> None:
        if speech_rate is None:
            if speed_ratio is None:
                speed = self.current_speed_ratio
            else:
                speed = config.clamp_speed_for_voice(speaker, float(speed_ratio))
            speech_rate = config.speed_ratio_to_speech_rate(speed)
        else:
            speech_rate = int(min(max(int(speech_rate), -50), 100))
            speed = config.speech_rate_to_speed_ratio(speech_rate)
            speed = config.clamp_speed_for_voice(speaker, speed)

        if speaker == self.current_speaker and speech_rate == self.current_speech_rate:
            return

        if not self.connected or not self.client:
            self.current_speaker = speaker
            self.current_speed_ratio = speed
            self.current_speech_rate = speech_rate
            self._emit(
                "log",
                {"message": f"Voice config staged: speaker={speaker}, speed_ratio={speed:.1f}, speech_rate={speech_rate}"},
            )
            self._emit(
                "voice_config_applied",
                {"speaker": speaker, "speed_ratio": speed, "speech_rate": speech_rate},
            )
            return

        # Try in-session hot update first so voice speed changes take effect immediately.
        try:
            await self.client.update_session_tts(speaker=speaker, speed_ratio=speed, speech_rate=speech_rate)
            self.current_speaker = speaker
            self.current_speed_ratio = speed
            self.current_speech_rate = speech_rate
            self._emit(
                "log",
                {"message": f"Voice config applied live: speaker={speaker}, speed_ratio={speed:.1f}, speech_rate={speech_rate}"},
            )
            self._emit(
                "voice_config_applied",
                {"speaker": speaker, "speed_ratio": speed, "speech_rate": speech_rate},
            )
            return
        except Exception as live_exc:
            self._emit("log", {"message": f"Live voice config update failed, fallback to session restart: {live_exc}"})

        was_recording = self.recording
        if was_recording:
            await self._stop_mic()

        if self.recv_task:
            self.recv_task.cancel()
            try:
                await self.recv_task
            except Exception:
                pass
            self.recv_task = None

        try:
            await self.client.restart_session(speaker=speaker, speed_ratio=speed, speech_rate=speech_rate)
            self.current_speaker = speaker
            self.current_speed_ratio = speed
            self.current_speech_rate = speech_rate
            self.hello_sent = False
            self.connected = True
            self.recv_task = asyncio.create_task(self._receive_loop())
            self._emit(
                "log",
                {"message": f"Voice config applied: speaker={speaker}, speed_ratio={speed:.1f}, speech_rate={speech_rate}"},
            )
            self._emit(
                "voice_config_applied",
                {"speaker": speaker, "speed_ratio": speed, "speech_rate": speech_rate},
            )
        except Exception as exc:
            self._emit("voice_config_failed", {"message": f"Apply voice config failed: {exc}"})
            if self.connected and not self.shutting_down and not self.recv_task:
                self.recv_task = asyncio.create_task(self._receive_loop())
        finally:
            if was_recording:
                await self._start_mic()

    async def _send_text(self, text: str) -> None:
        if not self.client:
            self._emit("error", {"message": "Not connected yet."})
            return
        content = text.strip()
        if not content:
            self._emit("error", {"message": "Input text is empty."})
            return
        try:
            if not await self._ensure_client_session():
                return
            await self.client.chat_text_query(content)
            self._emit("log", {"message": f"Text sent: {content}"})
            self._emit("status", {"message": "Waiting response"})
        except Exception as exc:
            self._emit("error", {"message": f"Send text failed: {exc}"})

    async def _ensure_client_session(self) -> bool:
        if not self.client:
            self._emit("error", {"message": "Client is not initialized."})
            return False
        if self.client.is_ws_open():
            return True

        if self._reconnect_lock is None:
            self._reconnect_lock = asyncio.Lock()

        now = time.time()
        if now < self._reconnect_block_until_ts:
            wait_s = max(1, int(self._reconnect_block_until_ts - now))
            self._emit(
                "voice_config_failed",
                {"message": f"Reconnect is temporarily blocked due to server quota limit, retry after about {wait_s}s."},
            )
            return False

        async with self._reconnect_lock:
            # Double-check after acquiring lock to avoid concurrent reconnect storms.
            if self.client and self.client.is_ws_open():
                return True

            now = time.time()
            if now < self._reconnect_block_until_ts:
                wait_s = max(1, int(self._reconnect_block_until_ts - now))
                self._emit(
                    "voice_config_failed",
                    {"message": f"Reconnect is temporarily blocked due to server quota limit, retry after about {wait_s}s."},
                )
                return False

            self._emit("log", {"message": "Session disconnected, reconnecting..."})
            try:
                self.client.session_id = str(uuid.uuid4())
                await self.client.connect()
                self.connected = True
                self._connecting = False
                self.hello_sent = False
                self._emit("status", {"message": "Connected"})
                self._emit("log", {"message": f"Reconnected. logid={self.client.logid}"})
                if not self.recv_task or self.recv_task.done():
                    self.recv_task = asyncio.create_task(self._receive_loop())
                return True
            except Exception as exc:
                self.connected = False
                self._connecting = False
                msg = str(exc)
                if "45000292" in msg or "quota exceeded" in msg.lower() or "qpm" in msg.lower():
                    # Back off to avoid hammering StartSession when server-side QPM is exceeded.
                    self._reconnect_block_until_ts = time.time() + 12
                    self._emit(
                        "voice_config_failed",
                        {"message": f"Reconnect failed due to server QPM limit. Please retry in ~12s. Detail: {exc}"},
                    )
                else:
                    self._emit("error", {"message": f"Reconnect failed: {exc}"})
                self._emit("status", {"message": "Disconnected"})
                return False

    def send_audio_file(self, path: str) -> None:
        self._submit(self._send_audio_file(path))

    async def _send_audio_file(self, path: str) -> None:
        if not os.path.isfile(path):
            self._emit("error", {"message": f"Audio file not found: {path}"})
            return
        self._emit("status", {"message": "Uploading audio file"})
        self._emit("log", {"message": f"Preparing file: {path}"})

        temp_path = None
        file_client: Optional[RealtimeDialogClient] = None
        try:
            # Use dedicated audio_file mode session to avoid mixed-mode state conflicts.
            file_client = self._client_factory(
                config=self._new_ws_config(),
                session_id=str(uuid.uuid4()),
                output_audio_format="pcm_s16le",
                mod="audio_file",
                recv_timeout=120,
                tts_speaker=self.current_speaker,
                tts_speed_ratio=self.current_speed_ratio,
                tts_speech_rate=self.current_speech_rate,
            )
            await file_client.connect()
            self._emit("log", {"message": "Audio file session connected."})
            temp_path = self._convert_audio_to_wav(path)
            await self._stream_wav_file(file_client, temp_path)
            await self._drain_audio_file_responses(file_client)
            self._emit("log", {"message": "Audio file upload finished."})
            self._emit("status", {"message": "Waiting response"})
        except Exception as exc:
            detail = str(exc).strip() or repr(exc)
            self._emit("error", {"message": f"Send audio file failed: {detail}"})
        finally:
            if file_client:
                try:
                    await file_client.finish_session()
                except Exception:
                    pass
                try:
                    await file_client.finish_connection()
                except Exception:
                    pass
                try:
                    await file_client.close()
                except Exception:
                    pass
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    def _convert_audio_to_wav(self, path: str) -> str:
        ext = os.path.splitext(path)[1].lower()
        if ext not in config.audio_upload_config["allowed_extensions"]:
            allowed = ", ".join(config.audio_upload_config["allowed_extensions"])
            raise ValueError(f"Unsupported file type: {ext}. Allowed: {allowed}")

        segment = AudioSegment.from_file(path)
        segment = segment.set_channels(config.audio_upload_config["target_channels"])
        segment = segment.set_frame_rate(config.audio_upload_config["target_sample_rate"])
        segment = segment.set_sample_width(config.audio_upload_config["target_sample_width"])

        fd, temp_path = tempfile.mkstemp(suffix=".wav", prefix="dialog_upload_")
        os.close(fd)
        segment.export(temp_path, format="wav")
        return temp_path

    async def _stream_wav_file(self, client: RealtimeDialogClient, wav_path: str) -> None:
        with wave.open(wav_path, "rb") as wf:
            chunk_size = config.input_audio_config["chunk"]
            framerate = wf.getframerate()
            sleep_seconds = chunk_size / float(framerate)
            while True:
                audio_data = wf.readframes(chunk_size)
                if not audio_data:
                    break
                await client.task_request(audio_data)
                await asyncio.sleep(sleep_seconds)
            # tail silence to mark end of user speech
            silence_chunk = b"\x00" * (config.input_audio_config["chunk"] * 2)
            for _ in range(10):
                await client.task_request(silence_chunk)
                await asyncio.sleep(0.02)

    async def _drain_audio_file_responses(self, client: RealtimeDialogClient) -> None:
        end_events = {359, 152, 153}
        timeout_hits = 0
        for _ in range(80):
            try:
                response = await asyncio.wait_for(client.receive_server_response(), timeout=3)
                timeout_hits = 0
            except asyncio.TimeoutError:
                timeout_hits += 1
                if timeout_hits >= 5:
                    raise TimeoutError("Audio file response timeout: no server response for 15s")
                continue
            except Exception as exc:
                raise RuntimeError(f"Audio file session disconnected: {exc}") from exc
            if not response:
                continue
            message_type = response.get("message_type")
            if message_type == "SERVER_ACK" and isinstance(response.get("payload_msg"), bytes):
                audio_chunk = response["payload_msg"]
                if audio_chunk:
                    self.audio_queue.put(audio_chunk)
                    self.audio_bytes_received += len(audio_chunk)
                    self._emit("audio_playing", {})
                    self._emit("status", {"message": "Playing audio"})
                continue

            event = response.get("event")
            payload = response.get("payload_msg")
            self._emit("log", {"message": f"Server event={event}, payload={payload}"})
            text = self._extract_text_from_payload(payload)
            if text and text != self.last_server_text:
                self.last_server_text = text
                self._emit("server_text", {"text": text})
            if event in end_events:
                return
            if event == 599:
                raise RuntimeError(f"Server error event 599: {payload}")
        raise TimeoutError("Audio file response timeout")

    def _close_input_stream(self) -> None:
        if self.input_stream:
            try:
                self.input_stream.stop_stream()
                self.input_stream.close()
            except Exception:
                pass
            self.input_stream = None

    def _handle_session_disconnect(self, reason: str) -> None:
        self.connected = False
        self._connecting = False
        self.hello_sent = False
        self._cancel_greeting_timeout()
        was_greeting = self._greeting_in_progress
        self._greeting_in_progress = False
        was_recording = self.recording
        self.recording = False
        self._close_input_stream()
        if self.mic_task:
            self.mic_task.cancel()
            self.mic_task = None
        self._emit("status", {"message": "Disconnected"})
        self._emit("log", {"message": f"Session disconnected: {reason}"})
        self._emit("disconnected", {"reason": reason, "was_recording": was_recording, "was_greeting": was_greeting})

    async def _receive_loop(self) -> None:
        if not self.client:
            return
        while not self.shutting_down:
            try:
                response = await self.client.receive_server_response()
                self._handle_server_response(response)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                if self.shutting_down:
                    break
                message = str(exc)
                self._handle_session_disconnect(message)
                if not self.shutting_down:
                    self._emit("error", {"message": f"Receive loop error: {exc}"})
                break

    def _handle_server_response(self, response: Dict[str, Any]) -> None:
        if not response:
            return

        message_type = response.get("message_type")
        if message_type == "SERVER_ACK" and isinstance(response.get("payload_msg"), bytes):
            if self._ignore_remote_audio or self.shutting_down:
                return
            audio_chunk = response["payload_msg"]
            if audio_chunk and not self._audio_packet_received:
                self._audio_packet_received = True
                self._emit("status", {"message": "Received remote audio"})
                self._emit("log", {"message": "First remote audio packet received."})
            self.audio_queue.put(audio_chunk)
            self.audio_bytes_received += len(audio_chunk)
            now = time.time()
            if now - self.last_audio_event_ts > 0.5:
                self.last_audio_event_ts = now
                self._emit("audio_playing", {})
                self._emit("status", {"message": "Playing audio"})
                self._emit("log", {"message": f"Received audio bytes total={self.audio_bytes_received}"})
            return

        if "code" in response:
            self._emit("error", {"message": f"Server error: {response}"})
            return

        if message_type == "SERVER_FULL_RESPONSE":
            event = response.get("event")
            payload = response.get("payload_msg")
            self._emit("log", {"message": f"Server event={event}, payload={payload}"})
            text = self._extract_text_from_payload(payload)
            if text:
                if text != self.last_server_text:
                    self.last_server_text = text
                    self._emit("server_text", {"text": text})
                if (not self._audio_packet_received) and (not self._audio_playback_failed):
                    self._emit("status", {"message": "Connected, waiting for remote audio"})

            if event == 450:
                self._clear_audio_queue()
            if event in (359, 152, 153):
                if event == 359 and self._greeting_in_progress:
                    self._cancel_greeting_timeout()
                    self._greeting_in_progress = False
                    self._emit("greeting_finished", {"event": event})
                self._emit("response_done", {"event": event})
                self._emit("status", {"message": "Connected"})

    def _clear_audio_queue(self) -> None:
        while True:
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break

    def _new_ws_config(self) -> Dict[str, Any]:
        ws_cfg = copy.deepcopy(config.ws_connect_config)
        ws_cfg["headers"]["X-Api-Connect-Id"] = str(uuid.uuid4())
        return ws_cfg

    def _extract_text_from_payload(self, payload: Any) -> str:
        if payload is None:
            return ""
        if isinstance(payload, str):
            return payload.strip()
        if isinstance(payload, dict):
            # Prefer common fields used by response payloads.
            for key in ("content", "text", "message", "answer", "reply", "tts_text"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            # Deep scan nested payloads.
            for value in payload.values():
                text = self._extract_text_from_payload(value)
                if text:
                    return text
            return ""
        if isinstance(payload, list):
            for item in payload:
                text = self._extract_text_from_payload(item)
                if text:
                    return text
            return ""
        return ""

    def interrupt_and_shutdown(self) -> None:
        if self.shutting_down:
            return
        self._ignore_remote_audio = True
        self.shutting_down = True
        self.recording = False
        self._greeting_in_progress = False
        self._cancel_greeting_timeout()
        if self.mic_task:
            self.mic_task.cancel()
        self._close_input_stream()
        self._clear_audio_queue()
        self.player_running = False
        self.audio_queue.put(None)
        if self.output_stream:
            try:
                self.output_stream.stop_stream()
            except Exception:
                pass
            try:
                self.output_stream.close()
            except Exception:
                pass
            self.output_stream = None
            self.output_stream_info = {}
        if self.loop and self.loop.is_running():
            future = self._submit(self._shutdown_async())
            try:
                future.result(timeout=8)
            except Exception:
                pass
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self.loop_thread:
            self.loop_thread.join(timeout=2)

    def shutdown(self) -> None:
        self.interrupt_and_shutdown()

    async def _shutdown_async(self) -> None:
        try:
            await self._stop_mic()
        except Exception:
            pass

        self._cancel_greeting_timeout()
        if self.mic_task:
            self.mic_task.cancel()
            try:
                await self.mic_task
            except Exception:
                pass
            self.mic_task = None
        self._close_input_stream()
        if self.recv_task:
            self.recv_task.cancel()
            try:
                await self.recv_task
            except Exception:
                pass
            self.recv_task = None

        if self.client:
            try:
                await self.client.finish_session()
            except Exception:
                pass
            try:
                await self.client.finish_connection()
            except Exception:
                pass
            try:
                await self.client.close()
            except Exception:
                pass

        self.connected = False
        self._connecting = False
        self.player_running = False
        self.audio_queue.put(None)
        if self.player_thread:
            self.player_thread.join(timeout=1)
            self.player_thread = None

        if self.output_stream:
            try:
                self.output_stream.stop_stream()
                self.output_stream.close()
            except Exception:
                pass
            self.output_stream = None
        self.output_stream_info = {}
        self.client = None

        try:
            self.audio.terminate()
        except Exception:
            pass

        self._emit("log", {"message": "Worker shutdown complete."})
