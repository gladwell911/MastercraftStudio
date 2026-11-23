import zdsr_tts


class _Callable:
    def __init__(self, fn):
        self._fn = fn

    def __call__(self, *args, **kwargs):
        return self._fn(*args, **kwargs)


class _FakeApi:
    def __init__(self, *, init_ret=0, speak_ret=0):
        self.init_calls = []
        self.stop_calls = 0
        self.speak_calls = []
        self.InitTTS = _Callable(self._init)
        self.Speak = _Callable(self._speak)
        self.StopSpeak = _Callable(self._stop)
        self._init_ret = init_ret
        self._speak_ret = speak_ret

    def _init(self, tts_type, channel_name, keydown_interrupt):
        self.init_calls.append((tts_type, channel_name, keydown_interrupt))
        return self._init_ret

    def _speak(self, text, interrupt):
        self.speak_calls.append((text, interrupt))
        return self._speak_ret

    def _stop(self):
        self.stop_calls += 1


def test_zdsr_tts_retries_init_after_previous_init_failure(monkeypatch):
    failed = _FakeApi(init_ret=-1)
    working = _FakeApi(init_ret=0, speak_ret=0)
    apis = iter([failed, working])
    client = zdsr_tts.ZDSRTTSClient()
    monkeypatch.setattr(client, "_load_api", lambda: next(apis))

    assert client.speak("第一次") is False
    assert client.speak("第二次") is True
    assert len(failed.init_calls) == 1
    assert len(working.init_calls) == 1
    assert working.speak_calls == [("第二次", 1)]


def test_zdsr_tts_reinitializes_after_speak_failure(monkeypatch):
    stale = _FakeApi(init_ret=0, speak_ret=-1)
    recovered = _FakeApi(init_ret=0, speak_ret=0)
    apis = iter([stale, recovered])
    client = zdsr_tts.ZDSRTTSClient()
    monkeypatch.setattr(client, "_load_api", lambda: next(apis))

    assert client.speak("恢复朗读") is True
    assert len(stale.init_calls) == 1
    assert stale.stop_calls == 1
    assert stale.speak_calls == [("恢复朗读", 1)]
    assert len(recovered.init_calls) == 1
    assert recovered.stop_calls == 1
    assert recovered.speak_calls == [("恢复朗读", 1)]


def test_navigation_waits_for_named_channel_and_replaces_without_stopping(monkeypatch):
    api = _FakeApi()
    state = [9]
    api.GetSpeakState = _Callable(lambda: state[0])
    ordinary = zdsr_tts.ZDSRTTSClient()
    titles = zdsr_tts.ZDSRTTSClient(chat_navigation=True)
    for client in (ordinary, titles):
        monkeypatch.setattr(client, "_load_api", lambda: api)
    assert titles.speak_chat_navigation("discarded while starting") is None
    assert api.speak_calls == []
    state[0] = 4
    assert titles.speak_chat_navigation("first") is True
    assert titles.speak_chat_navigation("latest") is True
    assert api.speak_calls == [("first", 1), ("latest", 1)]
    assert len(api.init_calls) == 1 and api.init_calls[0][0::2] == (1, 0)
    assert api.init_calls[0][1].startswith("MC.ChatNavigation.")
    assert api.stop_calls == 0
    titles.stop()
    assert api.stop_calls == 1
    assert ordinary.speak("ordinary")
    assert api.init_calls[-1][0::2] == (0, 1)
    assert titles.speak_chat_navigation("another title")
    assert api.init_calls[-1] == api.init_calls[0]
    assert api.stop_calls == 2
