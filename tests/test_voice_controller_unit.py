import speech_input
from speech_input import MODE_DIRECT, VoiceInputController


def _noop(*_a, **_k):
    return None


def test_right_ctrl_double_tap_starts_recording(monkeypatch):
    c = VoiceInputController(on_state_change=_noop, on_result=lambda *_a, **_k: None, on_error=_noop)
    started = {"mode": None}

    def fake_start(mode=MODE_DIRECT):
        started["mode"] = mode

    monkeypatch.setattr(c, "start_recording", fake_start)
    c.on_ctrl_keyup(combo_used=False, side="right")
    c.on_ctrl_keyup(combo_used=False, side="right")
    assert started["mode"] == MODE_DIRECT


def test_left_ctrl_double_tap_starts_recording(monkeypatch):
    c = VoiceInputController(on_state_change=_noop, on_result=lambda *_a, **_k: None, on_error=_noop)
    started = {"mode": None}

    def fake_start(mode=MODE_DIRECT):
        started["mode"] = mode

    monkeypatch.setattr(c, "start_recording", fake_start)
    c.on_ctrl_keyup(combo_used=False, side="left")
    c.on_ctrl_keyup(combo_used=False, side="left")
    assert started["mode"] == MODE_DIRECT


def test_single_right_ctrl_tap_does_not_start(monkeypatch):
    c = VoiceInputController(on_state_change=_noop, on_result=lambda *_a, **_k: None, on_error=_noop)
    started = {"mode": None}

    def fake_start(mode=MODE_DIRECT):
        started["mode"] = mode

    monkeypatch.setattr(c, "start_recording", fake_start)
    c.on_ctrl_keyup(combo_used=False, side="right")
    assert started["mode"] is None


def test_recording_stops_on_any_ctrl_tap(monkeypatch):
    c = VoiceInputController(on_state_change=_noop, on_result=lambda *_a, **_k: None, on_error=_noop)
    c.state = "recording"
    called = {"stop": 0}
    monkeypatch.setattr(c, "stop_and_transcribe", lambda: called.__setitem__("stop", called["stop"] + 1))
    c.on_ctrl_keyup(combo_used=False, side="left")
    assert called["stop"] == 1


def test_double_tap_still_starts_when_combo_used_true(monkeypatch):
    c = VoiceInputController(on_state_change=_noop, on_result=lambda *_a, **_k: None, on_error=_noop)
    started = {"mode": None}

    def fake_start(mode=MODE_DIRECT):
        started["mode"] = mode

    monkeypatch.setattr(c, "start_recording", fake_start)
    c.on_ctrl_keyup(combo_used=True, side="right")
    c.on_ctrl_keyup(combo_used=True, side="right")
    assert started["mode"] == MODE_DIRECT


def test_stop_and_transcribe_emits_stop_callback_immediately(monkeypatch):
    called = {"stop": 0}
    c = VoiceInputController(
        on_state_change=_noop,
        on_result=lambda *_a, **_k: None,
        on_error=_noop,
        on_stop_recording=lambda: called.__setitem__("stop", called["stop"] + 1),
    )
    c.state = "recording"

    class _DummyThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            return None

    monkeypatch.setattr(speech_input.threading, "Thread", _DummyThread)
    c.stop_and_transcribe()
    assert called["stop"] == 1
