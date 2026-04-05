import asyncio
import queue
import threading

import config
from dialog_worker import DialogWorker


class _FakeClient:
    def __init__(self, should_fail: bool = False, update_should_fail: bool = False):
        self.should_fail = should_fail
        self.update_should_fail = update_should_fail
        self.calls = 0
        self.update_calls = 0
        self.hello_calls = 0
        self.text_calls = []
        self.audio_calls = []
        self.session_id = "fake-session"
        self.logid = "fake-logid"
        self._ws_open = True

    async def restart_session(self, speaker: str, speed_ratio: float, speech_rate=None):
        self.calls += 1
        if self.should_fail:
            raise RuntimeError("boom")

    async def update_session_tts(self, speaker: str, speed_ratio: float, speech_rate=None):
        self.update_calls += 1
        if self.update_should_fail:
            raise RuntimeError("update-boom")

    async def say_hello(self):
        self.hello_calls += 1

    async def chat_text_query(self, content: str):
        self.text_calls.append(content)

    async def task_request(self, audio: bytes):
        self.audio_calls.append(audio)
        return None

    async def receive_server_response(self):
        return {}

    def is_ws_open(self):
        return self._ws_open

    async def connect(self):
        self._ws_open = True

    async def close(self):
        self._ws_open = False

    async def finish_session(self):
        self._ws_open = False

    async def finish_connection(self):
        return None


class _DoneTask:
    def cancel(self):
        return None

    def done(self):
        return False

    def __await__(self):
        if False:
            yield
        return None


class _FakeOutputStream:
    def __init__(self):
        self.writes = []
        self.stop_calls = 0
        self.closed = False

    def write(self, payload):
        self.writes.append(payload)

    def stop_stream(self):
        self.stop_calls += 1

    def close(self):
        self.closed = True


class _FallbackAudio:
    def __init__(self):
        self.calls = []

    def open(self, **kwargs):
        self.calls.append(kwargs)
        call_no = len(self.calls)
        if call_no in (1, 2):
            raise RuntimeError(f"boom-{call_no}")
        return _FakeOutputStream()

    def get_default_output_device_info(self):
        return {"index": 7, "name": "Test Speaker"}

    def get_device_info_by_index(self, index):
        return {"index": index, "name": f"Device-{index}"}


class _AlwaysFailAudio:
    def open(self, **kwargs):
        raise RuntimeError(f"cannot-open-{kwargs.get('channels')}")

    def get_default_output_device_info(self):
        return {"index": 3, "name": "Broken Speaker"}

    def get_device_info_by_index(self, index):
        return {"index": index, "name": f"Device-{index}"}


class _PrimeFailOutputStream(_FakeOutputStream):
    def write(self, payload):
        raise RuntimeError("prime-failed")


def build_worker_without_init():
    worker = object.__new__(DialogWorker)
    worker.on_event = lambda *_args, **_kwargs: None
    worker.loop = None
    worker.loop_thread = None
    worker.loop_ready = threading.Event()
    worker.client = None
    worker.session_id = "test-session"
    worker.audio = None
    worker.input_stream = None
    worker.output_stream = None
    worker.output_stream_info = {}
    worker.audio_queue = queue.Queue()
    worker.player_thread = None
    worker.player_running = False
    worker.last_audio_event_ts = 0.0
    worker.audio_bytes_received = 0
    worker._audio_packet_received = False
    worker._audio_playback_failed = False
    worker._connecting = False
    worker._greeting_in_progress = False
    worker._greeting_timeout_task = None
    worker._ignore_remote_audio = False
    worker.connected = False
    worker.recording = False
    worker.shutting_down = False
    worker.recv_task = None
    worker.mic_task = None
    worker.hello_sent = False
    worker.last_server_text = ""
    worker.current_speaker = config.DEFAULT_SPEAKER
    worker.current_speed_ratio = config.DEFAULT_SPEED_RATIO
    worker.current_speech_rate = config.speed_ratio_to_speech_rate(config.DEFAULT_SPEED_RATIO)
    worker.current_system_role = getattr(config, "DEFAULT_SYSTEM_ROLE", "").strip()
    worker._voice_cfg_lock = None
    worker._reconnect_lock = None
    worker._reconnect_block_until_ts = 0.0
    return worker


def test_extract_text_from_payload_prefers_content():
    worker = build_worker_without_init()
    payload = {"content": "你好", "text": "hello"}
    assert worker._extract_text_from_payload(payload) == "你好"


def test_extract_text_from_payload_nested():
    worker = build_worker_without_init()
    payload = {"meta": {"id": 1}, "data": [{"x": 1}, {"answer": "这是服务端回答"}]}
    assert worker._extract_text_from_payload(payload) == "这是服务端回答"


def test_extract_text_from_payload_empty():
    worker = build_worker_without_init()
    assert worker._extract_text_from_payload(None) == ""
    assert worker._extract_text_from_payload({"a": 1}) == ""


def test_clamp_speed_for_o_series():
    speaker = "zh_male_yunzhou_jupiter_bigtts"
    assert config.clamp_speed_for_voice(speaker, 0.05) == 0.2
    assert config.clamp_speed_for_voice(speaker, 3.9) == 3.0
    assert config.clamp_speed_for_voice(speaker, 1.04) == 1.0


def test_speed_ratio_to_speech_rate_mapping():
    assert config.speed_ratio_to_speech_rate(0.2) == -50
    assert config.speed_ratio_to_speech_rate(3.0) == 100
    mid = config.speed_ratio_to_speech_rate(1.6)
    assert -50 <= mid <= 100


def test_speech_rate_to_speed_ratio_mapping():
    assert config.speech_rate_to_speed_ratio(-50) == 0.2
    assert config.speech_rate_to_speed_ratio(100) == 3.0
    mid = config.speech_rate_to_speed_ratio(25)
    assert 0.2 <= mid <= 3.0


def test_set_voice_config_deduplicates_same_value():
    worker = build_worker_without_init()
    events = []
    worker._emit = lambda et, payload=None: events.append((et, payload or {}))
    worker.shutting_down = False
    worker.connected = True
    worker.recording = False
    worker.client = _FakeClient(should_fail=False)
    worker.recv_task = None

    asyncio.run(worker._set_voice_config(config.DEFAULT_SPEAKER, config.DEFAULT_SPEED_RATIO))

    assert worker.client.calls == 0
    assert events == []


def test_set_voice_config_emit_failed_event_instead_of_error(monkeypatch):
    worker = build_worker_without_init()
    events = []
    worker._emit = lambda et, payload=None: events.append((et, payload or {}))
    worker.shutting_down = False
    worker.connected = True
    worker.recording = False
    worker.client = _FakeClient(should_fail=True, update_should_fail=True)
    worker.recv_task = _DoneTask()

    def fake_create_task(coro):
        coro.close()
        return _DoneTask()

    monkeypatch.setattr("dialog_worker.asyncio.create_task", fake_create_task)

    asyncio.run(worker._set_voice_config("zh_female_vv_jupiter_bigtts", 1.2))
    event_types = [item[0] for item in events]
    assert "voice_config_failed" in event_types
    assert "error" not in event_types


def test_set_voice_config_prefers_live_update_without_restart():
    worker = build_worker_without_init()
    events = []
    worker._emit = lambda et, payload=None: events.append((et, payload or {}))
    worker.shutting_down = False
    worker.connected = True
    worker.recording = False
    worker.client = _FakeClient(should_fail=False, update_should_fail=False)
    worker.recv_task = None

    asyncio.run(worker._set_voice_config("zh_female_vv_jupiter_bigtts", speed_ratio=1.5))

    assert worker.client.update_calls == 1
    assert worker.client.calls == 0
    event_types = [item[0] for item in events]
    assert "voice_config_applied" in event_types


def test_set_voice_config_fallbacks_to_restart_when_live_update_fails(monkeypatch):
    worker = build_worker_without_init()
    events = []
    worker._emit = lambda et, payload=None: events.append((et, payload or {}))
    worker.shutting_down = False
    worker.connected = True
    worker.recording = False
    worker.client = _FakeClient(should_fail=False, update_should_fail=True)
    worker.recv_task = None

    def fake_create_task(coro):
        coro.close()
        return _DoneTask()

    monkeypatch.setattr("dialog_worker.asyncio.create_task", fake_create_task)

    asyncio.run(worker._set_voice_config("zh_female_vv_jupiter_bigtts", speed_ratio=1.5))

    assert worker.client.update_calls == 1
    assert worker.client.calls == 1
    event_types = [item[0] for item in events]
    assert "voice_config_applied" in event_types


def test_send_text_does_not_trigger_hello():
    worker = build_worker_without_init()
    events = []
    worker._emit = lambda et, payload=None: events.append((et, payload or {}))
    worker.connected = True
    worker.client = _FakeClient()
    worker.hello_sent = False

    asyncio.run(worker._send_text("hello"))

    assert worker.client.hello_calls == 0
    assert worker.client.text_calls == ["hello"]


def test_play_greeting_calls_say_hello_and_starts_timeout():
    worker = build_worker_without_init()
    events = []
    scheduled = []
    worker._emit = lambda et, payload=None: events.append((et, payload or {}))
    worker.connected = True
    worker.client = _FakeClient()
    worker._schedule_greeting_timeout = lambda timeout_seconds=15.0: scheduled.append(timeout_seconds)

    asyncio.run(worker._play_greeting())

    assert worker.client.hello_calls == 1
    assert worker._greeting_in_progress is True
    assert scheduled == [15.0]
    assert ("status", {"message": "Greeting"}) in events


def test_start_mic_requires_connected_session():
    worker = build_worker_without_init()
    events = []
    worker._emit = lambda et, payload=None: events.append((et, payload or {}))
    worker.client = None

    asyncio.run(worker._start_mic())

    assert ("error", {"message": "Start microphone failed: not connected yet."}) in events


def test_open_output_stream_fallbacks_to_default_device_then_stereo():
    worker = build_worker_without_init()
    events = []
    worker._emit = lambda et, payload=None: events.append((et, payload or {}))
    worker.audio = _FallbackAudio()

    worker._open_output_stream()

    assert worker.output_stream is not None
    assert worker.output_stream_info["attempt"] == 3
    assert worker.output_stream_info["channels"] == 2
    assert worker.output_stream_info["device_name"] == "Test Speaker"
    assert len(worker.audio.calls) == 3
    assert worker.audio.calls[1]["output_device_index"] == 7
    assert any("attempt=3" in item[1].get("message", "") for item in events if item[0] == "log")


def test_open_output_stream_raises_detailed_error_after_all_attempts():
    worker = build_worker_without_init()
    worker._emit = lambda *_args, **_kwargs: None
    worker.audio = _AlwaysFailAudio()

    try:
        worker._open_output_stream()
        raised = False
    except RuntimeError as exc:
        raised = True
        text = str(exc)
        assert "Broken Speaker" in text
        assert "channels=1" in text
        assert "attempt=1" in text
        assert "attempt=3" in text

    assert raised


def test_prime_output_stream_writes_silence_for_first_playback():
    worker = build_worker_without_init()
    events = []
    stream = _FakeOutputStream()
    worker._emit = lambda et, payload=None: events.append((et, payload or {}))
    worker.output_stream = stream
    worker.output_stream_info = {
        "device_name": "Test Speaker",
        "rate": 24000,
        "channels": 1,
        "format": "pcm_s16le",
    }

    worker._prime_output_stream()

    assert len(stream.writes) == 1
    assert len(stream.writes[0]) == 5760
    assert any("primed" in item[1].get("message", "") for item in events if item[0] == "log")


def test_prime_output_stream_logs_and_continues_when_prime_write_fails():
    worker = build_worker_without_init()
    events = []
    worker._emit = lambda et, payload=None: events.append((et, payload or {}))
    worker.output_stream = _PrimeFailOutputStream()
    worker.output_stream_info = {
        "device_name": "Test Speaker",
        "rate": 24000,
        "channels": 1,
        "format": "pcm_s16le",
    }

    worker._prime_output_stream()

    assert any("prime skipped" in item[1].get("message", "") for item in events if item[0] == "log")


def test_handle_server_response_marks_first_remote_audio_and_queues_bytes():
    worker = build_worker_without_init()
    events = []
    worker._emit = lambda et, payload=None: events.append((et, payload or {}))

    worker._handle_server_response({"message_type": "SERVER_ACK", "payload_msg": b"abc"})

    assert worker.audio_queue.qsize() == 1
    assert worker.audio_bytes_received == 3
    assert worker._audio_packet_received is True
    statuses = [payload.get("message") for et, payload in events if et == "status"]
    assert "Received remote audio" in statuses
    assert "Playing audio" in statuses


def test_handle_server_response_ignores_audio_after_interrupt():
    worker = build_worker_without_init()
    worker._emit = lambda *_args, **_kwargs: None
    worker._ignore_remote_audio = True

    worker._handle_server_response({"message_type": "SERVER_ACK", "payload_msg": b"abc"})

    assert worker.audio_queue.qsize() == 0
    assert worker.audio_bytes_received == 0


def test_handle_server_response_text_without_audio_sets_waiting_status():
    worker = build_worker_without_init()
    events = []
    worker._emit = lambda et, payload=None: events.append((et, payload or {}))

    worker._handle_server_response(
        {"message_type": "SERVER_FULL_RESPONSE", "event": 351, "payload_msg": {"text": "你好"}}
    )

    assert ("server_text", {"text": "你好"}) in events
    statuses = [payload.get("message") for et, payload in events if et == "status"]
    assert "Connected, waiting for remote audio" in statuses


def test_handle_server_response_event_359_finishes_greeting():
    worker = build_worker_without_init()
    events = []
    cancelled = []
    worker._emit = lambda et, payload=None: events.append((et, payload or {}))
    worker._greeting_in_progress = True
    worker._cancel_greeting_timeout = lambda: cancelled.append(True)

    worker._handle_server_response({"message_type": "SERVER_FULL_RESPONSE", "event": 359, "payload_msg": {}})

    assert worker._greeting_in_progress is False
    assert cancelled == [True]
    assert ("greeting_finished", {"event": 359}) in events


def test_player_loop_reports_detailed_playback_error(monkeypatch):
    worker = build_worker_without_init()
    events = []
    worker._emit = lambda et, payload=None: events.append((et, payload or {}))
    worker.player_running = True
    worker.output_stream_info = {
        "device_name": "Test Speaker",
        "rate": 24000,
        "channels": 1,
        "format": "pcm_s16le",
    }
    worker.audio_queue.put(b"boom")

    class _FailingStream:
        def write(self, _payload):
            worker.player_running = False
            raise RuntimeError("write-failed")

    worker.output_stream = _FailingStream()
    monkeypatch.setattr("dialog_worker.time.sleep", lambda *_args, **_kwargs: None)

    worker._player_loop()

    assert worker._audio_playback_failed is True
    error_messages = [payload.get("message", "") for et, payload in events if et == "error"]
    assert any("write-failed" in msg and "Test Speaker" in msg and "rate=24000" in msg for msg in error_messages)
    assert ("status", {"message": "Audio playback failed"}) in events


def test_interrupt_and_shutdown_stops_output_and_clears_queue():
    worker = build_worker_without_init()
    stream = _FakeOutputStream()
    worker.output_stream = stream
    worker.output_stream_info = {"device_name": "Test Speaker"}
    worker.audio_queue.put(b"queued-audio")
    worker.player_running = True

    worker.interrupt_and_shutdown()

    assert worker.shutting_down is True
    assert worker._ignore_remote_audio is True
    assert worker.player_running is False
    assert stream.stop_calls == 1
    assert stream.closed is True
    assert worker.output_stream is None
