from __future__ import annotations

import importlib
import threading
from dataclasses import dataclass
from typing import Callable


DEFAULT_REALTIME_CALL_ROLE = "你是豆包，请使用自然、简洁、友好的中文与用户进行实时语音通话。"
DEFAULT_REALTIME_CALL_SPEECH_RATE = 0
DEFAULT_REALTIME_CALL_SPEAKER = "zh_female_vv_jupiter_bigtts"


@dataclass(slots=True)
class RealtimeCallSettings:
    role: str = DEFAULT_REALTIME_CALL_ROLE
    speech_rate: int = DEFAULT_REALTIME_CALL_SPEECH_RATE
    speaker: str = DEFAULT_REALTIME_CALL_SPEAKER

    def normalized(self) -> "RealtimeCallSettings":
        role = (self.role or "").strip() or DEFAULT_REALTIME_CALL_ROLE
        speech_rate = int(min(max(int(self.speech_rate), -50), 100))
        speaker = (self.speaker or "").strip() or DEFAULT_REALTIME_CALL_SPEAKER
        return RealtimeCallSettings(role=role, speech_rate=speech_rate, speaker=speaker)


class RealtimeCallController:
    def __init__(
        self,
        settings: RealtimeCallSettings,
        on_status: Callable[[str], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        on_active_change: Callable[[bool], None] | None = None,
    ) -> None:
        self.settings = settings.normalized()
        self.on_status = on_status or (lambda _message: None)
        self.on_error = on_error or (lambda _message: None)
        self.on_active_change = on_active_change or (lambda _active: None)
        self._lock = threading.RLock()
        self._worker = None
        self._worker_token = 0
        self._active = False
        self._starting = False
        self._greeting = False
        self._connected = False
        self._shutdown = False

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._active

    @property
    def is_starting(self) -> bool:
        with self._lock:
            return self._starting or self._greeting

    @property
    def is_ready(self) -> bool:
        with self._lock:
            return self._connected and self._worker is not None and not self._shutdown

    def prepare(self) -> None:
        threading.Thread(target=self._prepare_worker_runtime, daemon=True).start()

    def toggle(self) -> str:
        with self._lock:
            should_stop = self._active or self._starting or self._greeting
        if should_stop:
            self.stop()
            return "stop"
        self.start()
        return "start"

    def update_settings(self, settings: RealtimeCallSettings) -> str:
        normalized = settings.normalized()
        with self._lock:
            role_changed = normalized.role != self.settings.role
            settings_changed = normalized != self.settings
            self.settings = normalized
            worker = self._worker
            active = self._active or self._starting or self._greeting
        if worker and active:
            try:
                worker.set_voice_config_by_speech_rate(normalized.speaker, normalized.speech_rate)
            except Exception:
                pass
            if role_changed:
                return "设置已保存，语速已应用，角色将在下次通话生效"
            return "设置已保存，已应用到当前通话"
        if worker and settings_changed:
            self._drop_worker(worker)
        return "设置已保存，将在下次通话时生效"

    def _prepare_worker_runtime(self) -> None:
        try:
            with self._lock:
                if self._shutdown or self._worker is not None:
                    return
                worker = self._ensure_worker_locked()
        except Exception:
            return
        try:
            worker.start()
        except Exception:
            self._drop_worker(worker)

    def start(self) -> None:
        worker = None
        action = "connect"
        with self._lock:
            if self._shutdown or self._active or self._starting or self._greeting:
                return
            worker = self._ensure_worker_locked()
            if self._connected:
                self._greeting = True
                action = "greet"
            else:
                self._starting = True
        if worker is None:
            return
        if action == "greet":
            self._play_greeting(worker)
            return
        if action == "start_mic":
            self._start_mic(worker)
            return
        self._connect_worker(worker, announce_status=True, report_error=True)

    def stop(self) -> None:
        worker = None
        was_active = False
        with self._lock:
            worker = self._detach_worker_locked()
            was_active = self._active
            self._active = False
            self._starting = False
            self._greeting = False
            self._connected = False
        if was_active:
            self.on_active_change(False)
        self.on_status("实时语音通话已结束")
        if worker is not None:
            threading.Thread(target=self._interrupt_worker, args=(worker,), daemon=True).start()

    def shutdown(self) -> None:
        worker = None
        with self._lock:
            self._shutdown = True
            worker = self._detach_worker_locked()
            self._active = False
            self._starting = False
            self._greeting = False
            self._connected = False
        if worker is not None:
            threading.Thread(target=self._interrupt_worker, args=(worker,), daemon=True).start()

    def _load_worker_class(self):
        try:
            module = importlib.import_module("realtime_dialog.dialog_worker")
            return module.DialogWorker
        except Exception as exc:
            raise RuntimeError(f"实时语音通话模块不可用：{exc}") from exc

    def _ensure_worker_locked(self):
        if self._worker is not None:
            return self._worker
        worker_class = self._load_worker_class()
        settings = self.settings.normalized()
        self._worker_token += 1
        token = self._worker_token
        self._worker = worker_class(
            on_event=lambda event_type, payload, _token=token: self._handle_worker_event(_token, event_type, payload),
            speaker=settings.speaker,
            speech_rate=settings.speech_rate,
            system_role=settings.role,
        )
        return self._worker

    def _detach_worker_locked(self):
        worker = self._worker
        self._worker = None
        self._worker_token += 1
        self._connected = False
        return worker

    def _drop_worker(self, worker) -> None:
        detached = None
        with self._lock:
            if self._worker is worker:
                detached = self._detach_worker_locked()
        if detached is not None:
            threading.Thread(target=self._interrupt_worker, args=(detached,), daemon=True).start()

    def _interrupt_worker(self, worker) -> None:
        try:
            interrupt = getattr(worker, "interrupt_and_shutdown", None)
            if callable(interrupt):
                interrupt()
                return
            worker.shutdown()
        except Exception:
            pass

    def _connect_worker(self, worker, announce_status: bool, report_error: bool) -> None:
        try:
            if announce_status:
                self.on_status("正在建立实时语音通话")
            worker.start()
            worker.connect()
        except Exception as exc:
            self._drop_worker(worker)
            with self._lock:
                self._active = False
                self._starting = False
                self._greeting = False
                self._connected = False
            if report_error:
                self.on_error(f"启动实时语音通话失败：{exc}")

    def _play_greeting(self, worker) -> None:
        try:
            self.on_status("已连接，豆包正在打招呼")
            worker.play_greeting()
        except Exception as exc:
            self._drop_worker(worker)
            with self._lock:
                self._active = False
                self._starting = False
                self._greeting = False
                self._connected = False
            self.on_error(f"发送欢迎语失败：{exc}")

    def _start_mic(self, worker) -> None:
        try:
            self.on_status("已连接，正在开启麦克风")
            worker.start_mic()
        except Exception as exc:
            self._drop_worker(worker)
            with self._lock:
                self._active = False
                self._starting = False
                self._greeting = False
                self._connected = False
            self.on_error(f"启动麦克风失败：{exc}")

    def _handle_worker_event(self, token: int, event_type: str, payload: dict) -> None:
        with self._lock:
            if token != self._worker_token:
                return
            worker = self._worker

        if event_type == "connected":
            if worker is None:
                return
            should_begin = False
            should_greet = False
            with self._lock:
                if token != self._worker_token:
                    return
                self._connected = True
                if self._greeting or self._active or not self._starting:
                    return
                should_begin = True
                should_greet = True
                self._starting = False
                self._greeting = should_greet
            if not should_begin:
                return
            if should_greet:
                self._play_greeting(worker)
                return
            with self._lock:
                self._starting = True
            self._start_mic(worker)
            return

        if event_type == "greeting_finished":
            with self._lock:
                if token != self._worker_token or worker is None or not self._greeting:
                    return
                self._greeting = False
                self._starting = True
            self.on_status("豆包打招呼结束，正在开启麦克风")
            try:
                worker.start_mic()
            except Exception as exc:
                self._drop_worker(worker)
                with self._lock:
                    self._active = False
                    self._starting = False
                    self._greeting = False
                    self._connected = False
                self.on_error(f"启动麦克风失败：{exc}")
            return

        if event_type == "recording_started":
            with self._lock:
                if token != self._worker_token:
                    return
                self._starting = False
                self._greeting = False
                self._active = True
                self._connected = True
            self.on_active_change(True)
            self.on_status("实时语音通话中")
            return

        if event_type == "recording_stopped":
            should_notify = False
            with self._lock:
                if token != self._worker_token:
                    return
                should_notify = self._active
                self._active = False
                self._starting = False
                self._greeting = False
                self._connected = True
            if should_notify:
                self.on_active_change(False)
                self.on_status("实时语音通话已结束")
            return

        if event_type == "disconnected":
            reason = str(payload.get("reason") or "unknown").strip() or "unknown"
            was_active = False
            was_greeting = False
            with self._lock:
                if token != self._worker_token:
                    return
                was_active = self._active
                was_greeting = self._greeting
                self._active = False
                self._starting = False
                self._greeting = False
                self._connected = False
            self._drop_worker(worker)
            if was_active:
                self.on_active_change(False)
                self.on_status("实时语音通话已结束")
            if was_active or was_greeting:
                self.on_error(f"实时语音通话已断开：{reason}")
            return

        if event_type == "voice_config_failed":
            message = payload.get("message", "实时语音通话设置失败")
            self.on_error(str(message))
            return

        if event_type == "error":
            message = str(payload.get("message") or "实时语音通话发生错误").strip()
            if message:
                self.on_error(message)
