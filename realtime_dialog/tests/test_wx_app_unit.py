import sys
import types

config_stub = types.ModuleType("config")
config_stub.VOICE_OPTIONS = [{"id": "speaker-a", "name": "Speaker A"}]
config_stub.DEFAULT_SPEAKER = "speaker-a"
config_stub.DEFAULT_SPEED_RATIO = 0.0
config_stub.speed_ratio_to_speech_rate = lambda ratio: 0
sys.modules.setdefault("config", config_stub)

dialog_worker_stub = types.ModuleType("dialog_worker")
dialog_worker_stub.DialogWorker = object
sys.modules.setdefault("dialog_worker", dialog_worker_stub)

from realtime_dialog import wx_app


class _FakeTextCtrl:
    def __init__(self, value: str = ""):
        self._value = value
        self.appended = []

    def GetValue(self):
        return self._value

    def SetValue(self, value):
        self._value = value

    def Clear(self):
        self._value = ""

    def AppendText(self, text):
        self.appended.append(text)


class _FakeWorker:
    def __init__(self):
        self.sent_text = []

    def send_text(self, text: str):
        self.sent_text.append(text)


def _build_frame_for_unit(input_value: str = ""):
    frame = wx_app.MainFrame.__new__(wx_app.MainFrame)
    frame.input_text = _FakeTextCtrl(input_value)
    frame.chat_text = _FakeTextCtrl()
    frame.worker = _FakeWorker()
    frame.status = ""
    frame.SetStatusText = lambda msg: setattr(frame, "status", msg)
    frame._set_controls_enabled = lambda _enabled: None
    return frame


def test_on_send_text_appends_user_chat_and_clears_input():
    frame = _build_frame_for_unit("你好")
    frame.on_send_text(None)

    assert frame.worker.sent_text == ["你好"]
    assert frame.chat_text.appended == ["我：你好\n"]
    assert frame.input_text.GetValue() == ""


def test_server_text_event_appends_bot_chat_without_overwriting_input():
    frame = _build_frame_for_unit("我正在输入")
    frame.on_worker_event("server_text", {"text": "豆包回复"})

    assert frame.chat_text.appended == ["豆包：豆包回复\n"]
    assert frame.input_text.GetValue() == "我正在输入"


def test_log_like_events_do_not_append_chat():
    frame = _build_frame_for_unit("")

    frame.on_worker_event("log", {"message": "debug"})
    frame.on_worker_event("status", {"message": "Connected"})
    frame.on_worker_event("voice_config_applied", {"speaker": "x"})
    frame.on_worker_event("voice_config_failed", {"message": "warn"})
    frame.on_worker_event("response_done", {"event": 359})
    frame.on_worker_event("connected", {"logid": "abc"})

    assert frame.chat_text.appended == []


def test_worker_thread_dispatch_uses_safe_call_after(monkeypatch):
    frame = wx_app.MainFrame.__new__(wx_app.MainFrame)
    seen = {}
    monkeypatch.setattr(frame, "IsBeingDeleted", lambda: False, raising=False)
    monkeypatch.setattr(frame, "GetHandle", lambda: 1, raising=False)
    monkeypatch.setattr(wx_app.wx, "GetApp", lambda: object())
    monkeypatch.setattr(
        wx_app.wx,
        "CallAfter",
        lambda fn, *args, **kwargs: (seen.setdefault("call", (fn.__name__, args, kwargs)), fn(*args, **kwargs)),
    )

    def _on_worker_event(event_type, payload):
        seen.setdefault("event", (event_type, payload))

    monkeypatch.setattr(frame, "on_worker_event", _on_worker_event)

    frame._on_worker_event_from_thread("status", {"message": "Connected"})

    assert seen["call"][1] == ("status", {"message": "Connected"})
    assert seen["event"] == ("status", {"message": "Connected"})
