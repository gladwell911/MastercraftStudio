import realtime_call


class _FakeWorker:
    instances = []

    def __init__(self, on_event, speaker, speech_rate, system_role):
        self.on_event = on_event
        self.speaker = speaker
        self.speech_rate = speech_rate
        self.system_role = system_role
        self.start_calls = 0
        self.connect_calls = 0
        self.start_mic_calls = 0
        self.shutdown_calls = 0
        self.interrupt_calls = 0
        self.play_greeting_calls = 0
        self.voice_cfg_calls = []
        type(self).instances.append(self)

    def start(self):
        self.start_calls += 1

    def connect(self):
        self.connect_calls += 1

    def start_mic(self):
        self.start_mic_calls += 1

    def play_greeting(self):
        self.play_greeting_calls += 1

    def interrupt_and_shutdown(self):
        self.interrupt_calls += 1

    def shutdown(self):
        self.shutdown_calls += 1

    def set_voice_config_by_speech_rate(self, speaker, speech_rate):
        self.voice_cfg_calls.append((speaker, speech_rate))

    def emit(self, event_type, payload=None):
        self.on_event(event_type, payload or {})


class _ImmediateThread:
    def __init__(self, target, args=(), daemon=None):
        self._target = target
        self._args = args
        self.daemon = daemon

    def start(self):
        self._target(*self._args)


def _build_controller():
    statuses = []
    errors = []
    active_changes = []
    controller = realtime_call.RealtimeCallController(
        settings=realtime_call.RealtimeCallSettings(),
        on_status=statuses.append,
        on_error=errors.append,
        on_active_change=active_changes.append,
    )
    return controller, statuses, errors, active_changes


def test_prepare_starts_worker_runtime_without_connecting(monkeypatch):
    _FakeWorker.instances.clear()
    monkeypatch.setattr(realtime_call.RealtimeCallController, "_load_worker_class", lambda self: _FakeWorker)
    monkeypatch.setattr(realtime_call.threading, "Thread", _ImmediateThread)
    controller, statuses, errors, _active_changes = _build_controller()

    controller.prepare()

    worker = _FakeWorker.instances[-1]
    assert worker.start_calls == 1
    assert worker.connect_calls == 0
    assert statuses == []
    assert errors == []


def test_first_connected_session_plays_greeting_before_starting_mic(monkeypatch):
    _FakeWorker.instances.clear()
    monkeypatch.setattr(realtime_call.RealtimeCallController, "_load_worker_class", lambda self: _FakeWorker)
    controller, statuses, _errors, active_changes = _build_controller()

    controller.toggle()

    worker = _FakeWorker.instances[-1]
    assert worker.start_calls == 1
    assert worker.connect_calls == 1
    assert worker.start_mic_calls == 0
    assert statuses[-1] == "正在建立实时语音通话"

    worker.emit("connected", {"logid": "fake-logid"})

    assert worker.play_greeting_calls == 1
    assert worker.start_mic_calls == 0
    assert controller.is_starting is True
    assert statuses[-1] == "已连接，豆包正在打招呼"
    assert active_changes == []


def test_first_connected_session_starts_mic_after_greeting_finished(monkeypatch):
    _FakeWorker.instances.clear()
    monkeypatch.setattr(realtime_call.RealtimeCallController, "_load_worker_class", lambda self: _FakeWorker)
    controller, statuses, _errors, active_changes = _build_controller()

    controller.start()
    worker = _FakeWorker.instances[-1]
    worker.emit("connected", {})
    worker.emit("greeting_finished", {"event": 359})

    assert worker.play_greeting_calls == 1
    assert worker.start_mic_calls == 1
    assert statuses[-1] == "豆包打招呼结束，正在开启麦克风"

    worker.emit("recording_started", {})

    assert controller.is_active is True
    assert active_changes == [True]
    assert statuses[-1] == "实时语音通话中"


def test_second_connected_session_greets_again(monkeypatch):
    _FakeWorker.instances.clear()
    monkeypatch.setattr(realtime_call.RealtimeCallController, "_load_worker_class", lambda self: _FakeWorker)
    monkeypatch.setattr(realtime_call.threading, "Thread", _ImmediateThread)
    controller, statuses, _errors, active_changes = _build_controller()

    controller.start()
    first_worker = _FakeWorker.instances[-1]
    first_worker.emit("connected", {})
    first_worker.emit("greeting_finished", {"event": 359})
    first_worker.emit("recording_started", {})
    controller.stop()

    controller.start()
    second_worker = _FakeWorker.instances[-1]
    second_worker.emit("connected", {})

    assert first_worker.play_greeting_calls == 1
    assert second_worker.play_greeting_calls == 1
    assert second_worker.start_mic_calls == 0
    assert statuses[-1] == "已连接，豆包正在打招呼"
    assert active_changes == [True, False]


def test_stop_interrupts_and_destroys_worker(monkeypatch):
    _FakeWorker.instances.clear()
    monkeypatch.setattr(realtime_call.RealtimeCallController, "_load_worker_class", lambda self: _FakeWorker)
    monkeypatch.setattr(realtime_call.threading, "Thread", _ImmediateThread)
    controller, statuses, _errors, active_changes = _build_controller()

    controller.start()
    worker = _FakeWorker.instances[-1]
    worker.emit("connected", {})
    worker.emit("greeting_finished", {"event": 359})
    worker.emit("recording_started", {})

    controller.stop()

    assert worker.interrupt_calls == 1
    assert worker.shutdown_calls == 0
    assert controller.is_active is False
    assert active_changes == [True, False]
    assert statuses[-1] == "实时语音通话已结束"


def test_disconnect_during_greeting_reports_error_and_keeps_greeting_for_next_call(monkeypatch):
    _FakeWorker.instances.clear()
    monkeypatch.setattr(realtime_call.RealtimeCallController, "_load_worker_class", lambda self: _FakeWorker)
    monkeypatch.setattr(realtime_call.threading, "Thread", _ImmediateThread)
    controller, _statuses, errors, active_changes = _build_controller()

    controller.start()
    first_worker = _FakeWorker.instances[-1]
    first_worker.emit("connected", {})
    first_worker.emit("disconnected", {"reason": "socket closed", "was_recording": False, "was_greeting": True})

    controller.start()
    second_worker = _FakeWorker.instances[-1]
    second_worker.emit("connected", {})

    assert "实时语音通话已断开：socket closed" in errors
    assert first_worker.interrupt_calls == 1
    assert second_worker.play_greeting_calls == 1
    assert active_changes == []
