import speech_input
from speech_input import MODE_DIRECT, VoiceInputController


def _noop(*_args, **_kwargs):
    return None


def test_stop_and_transcribe_returns_last_transcript_verbatim(monkeypatch):
    seen = {"text": None, "mode": None}
    controller = VoiceInputController(
        on_state_change=_noop,
        on_result=lambda text, mode: seen.update({"text": text, "mode": mode}),
        on_error=_noop,
    )
    controller.state = "recording"
    controller._last_text = "今天天气不错今天天气不错"

    class _ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            if self._target:
                self._target()

    monkeypatch.setattr(speech_input.threading, "Thread", _ImmediateThread)

    controller.stop_and_transcribe()

    assert seen == {"text": "今天天气不错今天天气不错", "mode": MODE_DIRECT}


def test_streaming_partial_then_final_keeps_latest_text(monkeypatch):
    seen = []
    controller = VoiceInputController(
        on_state_change=_noop,
        on_result=lambda text, mode: seen.append((text, mode)),
        on_error=_noop,
    )
    controller.state = "recording"
    controller._on_stream_text("今天")
    controller._on_stream_text("今天天气")
    controller._on_stream_text("今天天气不错")

    class _ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            if self._target:
                self._target()

    monkeypatch.setattr(speech_input.threading, "Thread", _ImmediateThread)

    controller.stop_and_transcribe()

    assert seen == [("今天天气不错", MODE_DIRECT)]
