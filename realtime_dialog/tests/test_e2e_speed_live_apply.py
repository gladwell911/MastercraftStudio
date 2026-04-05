import asyncio

import config
from dialog_worker import DialogWorker


class _FakeRealtimeClient:
    def __init__(self):
        self.update_calls = []
        self.restart_calls = []

    async def update_session_tts(self, speaker: str, speed_ratio: float, speech_rate=None):
        self.update_calls.append(
            {"speaker": speaker, "speed_ratio": speed_ratio, "speech_rate": speech_rate}
        )

    async def restart_session(self, speaker: str, speed_ratio: float, speech_rate=None):
        self.restart_calls.append(
            {"speaker": speaker, "speed_ratio": speed_ratio, "speech_rate": speech_rate}
        )


def test_e2e_speed_change_applies_live_during_recording():
    # Build worker without opening real audio devices.
    worker = object.__new__(DialogWorker)
    events = []
    worker._emit = lambda et, payload=None: events.append((et, payload or {}))
    worker.shutting_down = False
    worker._voice_cfg_lock = None
    worker.current_speaker = config.DEFAULT_SPEAKER
    worker.current_speed_ratio = config.DEFAULT_SPEED_RATIO
    worker.current_speech_rate = config.speed_ratio_to_speech_rate(config.DEFAULT_SPEED_RATIO)
    worker.connected = True
    worker.recording = True
    worker.client = _FakeRealtimeClient()
    worker.recv_task = None

    stop_mic_calls = {"count": 0}
    start_mic_calls = {"count": 0}

    async def _fake_stop_mic():
        stop_mic_calls["count"] += 1

    async def _fake_start_mic():
        start_mic_calls["count"] += 1

    worker._stop_mic = _fake_stop_mic
    worker._start_mic = _fake_start_mic

    asyncio.run(worker._set_voice_config(worker.current_speaker, speech_rate=35))

    assert len(worker.client.update_calls) == 1
    assert len(worker.client.restart_calls) == 0
    assert stop_mic_calls["count"] == 0
    assert start_mic_calls["count"] == 0
    assert any(et == "voice_config_applied" for et, _ in events)
