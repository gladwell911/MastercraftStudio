import json
import main
import pytest
import speech_input
import wx


def test_global_ctrl_callback_forwarding(frame):
    seen = []
    frame._voice_input.on_ctrl_keyup = lambda combo_used=False, side="left": seen.append((combo_used, side))
    frame._on_global_ctrl_keyup(True, "right")
    frame._on_global_ctrl_keyup(False, "left")
    assert seen == [(True, "right"), (False, "left")]


def test_on_done_plays_finish_sound_before_focusing_latest_answer(frame, monkeypatch):
    frame.Show()
    frame.active_chat_id = "chat-finish-order"
    frame.current_chat_id = "chat-finish-order"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {"question": "当前问题", "answer_md": main.REQUESTING_TEXT, "model": "openai/gpt-5.2", "created_at": 1.0},
    ]
    frame._current_chat_state.update({"id": "chat-finish-order", "turns": frame.active_session_turns})
    calls = []
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_refresh_history", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_final_answer", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_play_finish_sound", lambda *args, **kwargs: calls.append("sound"))
    monkeypatch.setattr(frame, "_focus_latest_answer", lambda: calls.append("focus"))

    frame._on_done(0, "最终回答", "", "openai/gpt-5.2", "", "chat-finish-order")

    assert calls == ["sound", "focus"]


def test_voice_ctrl_double_tap_window_defaults_to_200ms():
    detector = speech_input.CtrlTapDetector()
    assert detector.double_tap_s == pytest.approx(0.2)


def test_voice_result_prefers_global_paste(frame, monkeypatch):
    injected = {"ok": False}
    typed = {"n": 0}

    frame._inject_text_to_foreground_window = lambda text: injected.__setitem__("ok", text == "语音文本") or True
    frame._type_text_to_system_focus = lambda text: typed.__setitem__("n", typed["n"] + 1) or True
    frame._speak_text_via_screen_reader = lambda _text: None
    frame._play_voice_end_sound = lambda: (_ for _ in ()).throw(AssertionError("should not play here"))
    frame._on_voice_stop_recording = lambda: None
    monkeypatch.setattr(main.wx.Window, "FindFocus", lambda: None)
    monkeypatch.setattr(frame, "IsActive", lambda: False)

    frame._on_voice_result("语音文本！！！", mode=main.MODE_DIRECT)
    assert injected["ok"]
    assert typed["n"] == 0


def test_voice_result_fallbacks_to_sendinput(frame, monkeypatch):
    injected = {"n": 0}
    typed = {"n": 0}
    frame._inject_text_to_foreground_window = lambda text: injected.__setitem__("n", injected["n"] + 1) or False
    frame._type_text_to_system_focus = lambda text: typed.__setitem__("n", typed["n"] + 1) or True
    frame._speak_text_via_screen_reader = lambda _text: None
    frame._play_voice_end_sound = lambda: (_ for _ in ()).throw(AssertionError("should not play here"))
    monkeypatch.setattr(main.wx.Window, "FindFocus", lambda: None)
    monkeypatch.setattr(frame, "IsActive", lambda: False)
    frame._on_voice_result("语音文本", mode=main.MODE_DIRECT)
    assert injected["n"] == 1
    assert typed["n"] == 1


def test_voice_result_writes_then_speaks_after_200ms(frame, monkeypatch):
    order = []
    frame._insert_text_to_system_focus = lambda text: order.append(("insert", text)) or True
    frame._speak_text_via_screen_reader = lambda text: order.append(("speak", text))
    monkeypatch.setattr(main.wx.Window, "FindFocus", lambda: None)
    monkeypatch.setattr(frame, "IsActive", lambda: False)
    monkeypatch.setattr(
        main.wx,
        "CallLater",
        lambda ms, fn, *args: order.append(("delay", ms)) or fn(*args) or object(),
    )

    frame._on_voice_result("语音文本！！！", mode=main.MODE_DIRECT)
    assert order == [("insert", "语音文本！！！"), ("delay", 200), ("speak", "语音文本！！！")]


def test_voice_result_speaks_immediately_when_delay_schedule_unavailable(frame, monkeypatch):
    order = []
    frame._insert_text_to_system_focus = lambda text: order.append(("insert", text)) or True
    frame._speak_text_via_screen_reader = lambda text: order.append(("speak", text))
    monkeypatch.setattr(main.wx.Window, "FindFocus", lambda: None)
    monkeypatch.setattr(frame, "IsActive", lambda: False)
    monkeypatch.setattr(main.wx, "CallLater", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("timer unavailable")))

    frame._on_voice_result("语音文本！！！", mode=main.MODE_DIRECT)

    assert order == [("insert", "语音文本！！！"), ("speak", "语音文本！！！")]


def test_voice_result_prefers_local_editor_without_system_injection(frame, monkeypatch):
    injected = {"n": 0}
    typed = {"n": 0}
    frame.input_edit.SetValue("")
    frame._inject_text_to_foreground_window = lambda text: injected.__setitem__("n", injected["n"] + 1) or True
    frame._type_text_to_system_focus = lambda text: typed.__setitem__("n", typed["n"] + 1) or True
    frame._speak_text_via_screen_reader = lambda _text: None
    monkeypatch.setattr(main.wx.Window, "FindFocus", lambda: frame.input_edit)

    frame._on_voice_result("今天天气不错", mode=main.MODE_DIRECT)

    assert frame.input_edit.GetValue() == "今天天气不错"
    assert injected["n"] == 0
    assert typed["n"] == 0


def test_voice_result_with_trailing_punctuation_prefers_local_editor_when_app_active(frame, monkeypatch):
    injected = {"n": 0}
    typed = {"n": 0}
    notified = {"n": 0}
    frame.input_edit.SetValue("")
    frame._inject_text_to_foreground_window = lambda text: injected.__setitem__("n", injected["n"] + 1) or True
    frame._type_text_to_system_focus = lambda text: typed.__setitem__("n", typed["n"] + 1) or True
    frame._speak_text_via_screen_reader = lambda _text: None
    monkeypatch.setattr(main.wx.Window, "FindFocus", lambda: frame.input_edit)
    monkeypatch.setattr(frame, "IsActive", lambda: True)
    monkeypatch.setattr(
        frame,
        "_notify_accessible_text_update",
        lambda window: notified.__setitem__("n", notified["n"] + (1 if window is frame.input_edit else 0)),
    )

    frame._on_voice_result("语音文本！！！", mode=main.MODE_DIRECT)

    assert frame.input_edit.GetValue() == "语音文本！！！"
    assert injected["n"] == 0
    assert typed["n"] == 0
    assert notified["n"] == 1


def test_inject_text_to_foreground_window_notifies_accessibility_for_target_hwnd(frame, monkeypatch):
    calls = []

    class _User32:
        def GetForegroundWindow(self):
            return 300

        def GetWindowThreadProcessId(self, hwnd, pid_ptr):
            return 0

        def GetGUIThreadInfo(self, tid, gti_ptr):
            return 0

        def PostMessageW(self, hwnd, msg, wparam, lparam):
            calls.append(("post", hwnd, msg, wparam))
            return 1

        def NotifyWinEvent(self, event, hwnd, objid, childid):
            calls.append(("notify", event, hwnd, objid, childid))

    monkeypatch.setattr(main.ctypes, "windll", type("Windll", (), {"user32": _User32()})())

    assert frame._inject_text_to_foreground_window("语音") is True
    assert calls[-1][0] == "notify"
    assert calls[-1][1] == main.EVENT_OBJECT_VALUECHANGE
    assert int(calls[-1][2].value) == 300
    assert calls[-1][3:] == (main.OBJID_CLIENT, main.CHILDID_SELF)


def test_speak_text_via_screen_reader_records_success_status(frame):
    calls = []
    frame._zdsr_tts = type(
        "FakeTTS",
        (),
        {"speak": lambda self, text: calls.append(text) or True},
    )()

    frame._speak_text_via_screen_reader("语音文本！！！")

    assert calls == ["语音文本"]
    assert frame.voice_screen_reader_status["last_text"] == "语音文本"
    assert frame.voice_screen_reader_status["last_success"] is True
    assert frame.voice_screen_reader_status["last_error"] == ""


def test_speak_text_via_screen_reader_records_failure_status(frame, monkeypatch):
    failing_tts = type(
        "FakeTTS",
        (),
        {"speak": lambda self, text: False},
    )()
    frame._zdsr_tts = failing_tts
    monkeypatch.setattr(main, "ZDSRTTSClient", lambda: failing_tts)

    frame._speak_text_via_screen_reader("语音文本")

    assert frame.voice_screen_reader_status["last_text"] == "语音文本"
    assert frame.voice_screen_reader_status["last_success"] is False
    assert "speak returned false" in frame.voice_screen_reader_status["last_error"]


def test_speak_text_via_screen_reader_recreates_tts_client_after_false(frame, monkeypatch):
    calls = []

    class _FailingTTS:
        def speak(self, text):
            calls.append(("old", text))
            return False

    class _WorkingTTS:
        def speak(self, text):
            calls.append(("new", text))
            return True

    frame._zdsr_tts = _FailingTTS()
    monkeypatch.setattr(main, "ZDSRTTSClient", lambda: _WorkingTTS())

    frame._speak_text_via_screen_reader("语音文本")

    assert calls == [("old", "语音文本"), ("new", "语音文本")]
    assert frame.voice_screen_reader_status["last_success"] is True
    assert frame.voice_screen_reader_status["last_error"] == ""


def test_voice_result_keeps_repeated_transcript_verbatim(frame, monkeypatch):
    frame.input_edit.SetValue("")
    frame._speak_text_via_screen_reader = lambda _text: None
    monkeypatch.setattr(main.wx.Window, "FindFocus", lambda: frame.input_edit)

    frame._on_voice_result("今天天气不错今天天气不错", mode=main.MODE_DIRECT)

    assert frame.input_edit.GetValue() == "今天天气不错今天天气不错"


def test_voice_result_repeated_callback_appends_again(frame, monkeypatch):
    frame.input_edit.SetValue("")
    frame._speak_text_via_screen_reader = lambda _text: None
    monkeypatch.setattr(main.wx.Window, "FindFocus", lambda: frame.input_edit)

    frame._on_voice_result("今天天气不错", mode=main.MODE_DIRECT)
    frame._on_voice_result("今天天气不错", mode=main.MODE_DIRECT)

    assert frame.input_edit.GetValue() == "今天天气不错今天天气不错"


def test_voice_result_appends_when_editor_already_contains_same_text(frame, monkeypatch):
    frame.input_edit.SetValue("今天天气不错")
    frame._speak_text_via_screen_reader = lambda _text: None
    monkeypatch.setattr(main.wx.Window, "FindFocus", lambda: frame.input_edit)
    monkeypatch.setattr(frame, "IsActive", lambda: True)

    frame._on_voice_result("今天天气不错", mode=main.MODE_DIRECT)

    assert frame.input_edit.GetValue() == "今天天气不错今天天气不错"


def test_voice_result_prefers_local_input_when_app_active_and_focus_temporarily_missing(frame, monkeypatch):
    injected = {"n": 0}
    typed = {"n": 0}
    frame.input_edit.SetValue("")
    frame._speak_text_via_screen_reader = lambda _text: None
    frame._inject_text_to_foreground_window = lambda text: injected.__setitem__("n", injected["n"] + 1) or True
    frame._type_text_to_system_focus = lambda text: typed.__setitem__("n", typed["n"] + 1) or True
    monkeypatch.setattr(main.wx.Window, "FindFocus", lambda: None)
    monkeypatch.setattr(frame, "IsActive", lambda: True)

    frame._on_voice_result("今天天气不错", mode=main.MODE_DIRECT)

    assert frame.input_edit.GetValue() == "今天天气不错"
    assert injected["n"] == 0
    assert typed["n"] == 0


def test_voice_stop_recording_plays_end_sound(frame):
    called = {"end": 0}
    frame._play_voice_end_sound = lambda: called.__setitem__("end", called["end"] + 1)
    frame._on_voice_stop_recording()
    assert called["end"] == 1


def test_voice_error_uses_accessible_status_and_sound_without_modal(frame, monkeypatch):
    called = {"wrong_sound": 0, "msgbox": 0}
    statuses = []
    spoken = []
    monkeypatch.setattr(frame, "_speak_text_via_screen_reader", spoken.append)
    frame._play_voice_wrong_sound = lambda: called.__setitem__("wrong_sound", called["wrong_sound"] + 1)
    frame.SetStatusText = lambda text: statuses.append(text)
    monkeypatch.setattr(main.wx, "MessageBox", lambda *_a, **_k: called.__setitem__("msgbox", called["msgbox"] + 1))
    frame._on_voice_error("err")
    assert called["wrong_sound"] == 1
    assert called["msgbox"] == 0
    assert statuses == ["err"]
    assert spoken == ["err"]


def test_voice_state_recording_plays_begin_sound(frame):
    called = {"begin": 0}
    statuses = []
    frame._play_voice_begin_sound = lambda: called.__setitem__("begin", called["begin"] + 1)
    frame.SetStatusText = lambda text: statuses.append(text)
    frame._voice_input.state = "recording"
    frame._on_voice_state("开始录音")
    assert called["begin"] == 1
    assert statuses == []


def test_voice_state_non_recording_does_not_play_begin_sound(frame):
    called = {"begin": 0}
    statuses = []
    frame._play_voice_begin_sound = lambda: called.__setitem__("begin", called["begin"] + 1)
    frame.SetStatusText = lambda text: statuses.append(text)
    frame._voice_input.state = "transcribing"
    frame._on_voice_state("正在识别")
    assert called["begin"] == 0
    assert statuses == []


def test_apply_realtime_call_settings_updates_controller_and_state(frame, tmp_path):
    seen = {}
    frame.state_path = tmp_path / "app_state.json"
    prepared = {"n": 0}
    orig_call_after = main.wx.CallAfter

    def fake_update(settings):
        seen["payload"] = (settings.role, settings.speech_rate)
        return "设置已保存"

    frame._realtime_call.update_settings = fake_update
    frame._realtime_call.prepare = lambda: prepared.__setitem__("n", prepared["n"] + 1)
    main.wx.CallAfter = lambda fn, *args, **kwargs: fn(*args, **kwargs)

    try:
        frame._apply_realtime_call_settings(main.RealtimeCallSettings(role="新的角色", speech_rate=25))
    finally:
        main.wx.CallAfter = orig_call_after

    assert frame.realtime_call_role == "新的角色"
    assert frame.realtime_call_speech_rate == 25
    assert seen["payload"] == ("新的角色", 25)
    assert prepared["n"] == 1
    data = json.loads(frame.state_path.read_text(encoding="utf-8"))
    assert data["realtime_call_role"] == "新的角色"
    assert data["realtime_call_speech_rate"] == 25


def test_load_state_restores_realtime_call_settings(frame, tmp_path):
    frame.state_path = tmp_path / "app_state.json"
    frame.state_path.write_text(
        json.dumps(
            {
                "selected_model_id": main.DEFAULT_MODEL_ID,
                "realtime_call_role": "测试角色",
                "realtime_call_speech_rate": 18,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    frame.realtime_call_role = main.DEFAULT_REALTIME_CALL_ROLE
    frame.realtime_call_speech_rate = main.DEFAULT_REALTIME_CALL_SPEECH_RATE
    frame._load_state()

    assert frame.realtime_call_role == "测试角色"
    assert frame.realtime_call_speech_rate == 18


def test_realtime_call_hotkey_toggles_call_and_plays_sound(frame):
    calls = []
    frame._realtime_call.toggle = lambda: calls.append("toggle") or "start"
    frame._play_voice_begin_sound = lambda: calls.append("begin")
    frame._play_voice_end_sound = lambda: calls.append("end")

    class E:
        def GetId(self):
            return main.HOTKEY_ID_REALTIME_CALL

    frame._on_global_hotkey(E())
    assert calls == ["toggle", "begin"]


def test_register_global_hotkey_registers_backslash_variants(monkeypatch):
    calls = []

    class _DummyUser32:
        @staticmethod
        def VkKeyScanW(_char_code):
            return 0xE2

    class _DummyFrame:
        _show_hotkey_registered = False
        _realtime_call_hotkey_registered_ids = set()

        def RegisterHotKey(self, hotkey_id, modifiers, vk_code):
            calls.append((hotkey_id, modifiers, vk_code))
            return True

        def _resolve_backslash_hotkey_vk(self):
            return main.ChatFrame._resolve_backslash_hotkey_vk(self)

    monkeypatch.setattr(main.ctypes, "windll", type("W", (), {"user32": _DummyUser32()})())

    dummy = _DummyFrame()
    main.ChatFrame._register_global_hotkey(dummy)

    registered_ids = {hotkey_id for hotkey_id, _mods, _vk in calls}
    registered_vks = {vk_code for _hotkey_id, _mods, vk_code in calls}
    assert main.HOTKEY_ID_SHOW in registered_ids
    assert main.HOTKEY_ID_REALTIME_CALL in registered_ids
    assert main.HOTKEY_ID_REALTIME_CALL_ALT in registered_ids
    assert registered_vks == {wx.WXK_F12, main.VK_OEM_5, main.VK_OEM_102}


def test_optimize_voice_text_without_api_key_still_sanitizes(frame, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    out = frame._optimize_voice_text("# 标题\n\n- 内容 😀")
    assert "#" not in out
    assert "- " not in out
    assert "😀" not in out


def test_voice_optimize_model_switched_to_gpt_chat():
    assert main.VOICE_OPTIMIZE_MODEL == "openai/gpt-5.2-chat"
