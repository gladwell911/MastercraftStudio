import main
import time
import threading
import pytest
from voice_fakes import VoiceRig, wait_until


def _pump_until(wx_app, predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while not predicate():
        wx_app.Yield()
        if time.monotonic() > deadline:
            raise AssertionError('GUI condition did not converge')
        time.sleep(.002)
    wx_app.Yield()


def test_local_ready_and_gui_messages_while_asr_connection_is_gated(frame, wx_app, monkeypatch):
    rig = VoiceRig(monkeypatch)
    cues, spoken, messages = [], [], []
    monkeypatch.setattr(frame, '_play_voice_begin_sound', lambda: cues.append('ready'))
    monkeypatch.setattr(frame, '_play_voice_end_sound', lambda: cues.append('stop'))
    monkeypatch.setattr(frame, '_speak_text_via_screen_reader', spoken.append)
    monkeypatch.setattr(frame, '_call_later_if_alive', lambda _delay, fn, *a: fn(*a) or object())
    monkeypatch.setattr(main.wx.Window, 'FindFocus', lambda: frame.input_edit)
    c = frame._voice_input
    try:
        frame._on_global_ctrl_keyup(False, 'left')
        frame._on_global_ctrl_keyup(False, 'left')
        main.wx.CallAfter(messages.append, 'responsive')
        _pump_until(wx_app, lambda: cues == ['ready'] and bool(messages))
        assert not rig.sockets
        frame._on_global_ctrl_keyup(False, 'left')
        _pump_until(wx_app, lambda: cues == ['ready', 'stop'])
        rig.connect.set()
        _pump_until(wx_app, lambda: frame.input_edit.GetValue() == rig.text)
        assert spoken == [rig.text]
    finally:
        c.shutdown()
        assert c._shutdown_done.wait(3)
        c._audio_thread.join(2)
        rig.assert_closed()


def test_queued_ready_is_suppressed_by_close_even_when_persistence_vetoes(frame, monkeypatch):
    rig = VoiceRig(monkeypatch)
    queued, cues = [], []
    c = frame._voice_input
    c._dispatch = queued.append
    monkeypatch.setattr(frame, '_play_voice_begin_sound', lambda: cues.append(True))
    monkeypatch.setattr(frame, '_flush_execution_step_persists_sync', lambda: False)
    monkeypatch.setattr(frame, '_defer_close_for_persistence_worker', lambda: None)
    try:
        c.start_recording()
        wait_until(lambda: c.state == 'recording')
        class Event:
            def Veto(self):
                self.vetoed = True
        event = Event()
        frame._on_close(event)
        assert event.vetoed and c.state == 'closed'
        for callback in queued:
            callback()
        assert cues == []
    finally:
        c.shutdown()
        assert c._shutdown_done.wait(3)
        c._audio_thread.join(2)
        rig.assert_closed()


@pytest.mark.parametrize('invalidate', ['none', 'new_session', 'cancel', 'close'])
def test_optimize_worker_gui_delivery_respects_generation(frame, wx_app, monkeypatch, invalidate):
    rig = VoiceRig(monkeypatch)
    entered, release, delivered = threading.Event(), threading.Event(), threading.Event()
    worker = []
    insertions = []
    c = frame._voice_input
    original_dispatch = main.wx_call_after_if_alive
    def optimize(_text):
        worker.append(threading.get_ident())
        entered.set()
        assert release.wait(3)
        return 'optimized phrase'
    def dispatch(func, *args, **kwargs):
        if worker and threading.get_ident() == worker[0]:
            def gui_delivery():
                try:
                    func(*args, **kwargs)
                finally:
                    delivered.set()
            return original_dispatch(gui_delivery)
        return original_dispatch(func, *args, **kwargs)
    monkeypatch.setattr(main, 'wx_call_after_if_alive', dispatch)
    monkeypatch.setattr(frame, '_optimize_voice_text', optimize)
    original_insert = frame._finalize_voice_input_with_feedback
    def insert(text):
        insertions.append(text)
        original_insert(text)
    monkeypatch.setattr(frame, '_finalize_voice_input_with_feedback', insert)
    monkeypatch.setattr(frame, '_speak_text_via_screen_reader', lambda *_: None)
    monkeypatch.setattr(frame, '_call_later_if_alive', lambda *_: object())
    monkeypatch.setattr(main.wx.Window, 'FindFocus', lambda: frame.input_edit)
    try:
        rig.connect.set()
        c.start_recording(main.MODE_OPTIMIZE)
        wait_until(lambda: c.state == 'recording')
        c.stop_and_transcribe()
        _pump_until(wx_app, entered.is_set)
        assert c.state == 'idle'
        if invalidate == 'new_session':
            c.start_recording()
        elif invalidate == 'cancel':
            c.cancel()
        elif invalidate == 'close':
            c.shutdown()
        release.set()
        _pump_until(wx_app, delivered.is_set)
        wx_app.Yield()
        assert insertions == (['optimized phrase'] if invalidate == 'none' else [])
        assert frame.input_edit.GetValue() == ('optimized phrase' if invalidate == 'none' else '')
    finally:
        release.set()
        c.shutdown()
        assert c._shutdown_done.wait(3)
        c._audio_thread.join(2)
        rig.assert_closed()


def test_controlled_device_software_ready_reaches_gui_within_500ms(frame, wx_app, monkeypatch):
    """Software scheduling only: no acoustic onset or physical-key P95 claim."""
    rig = VoiceRig(monkeypatch)
    cue_times = []
    monkeypatch.setattr(frame, '_play_voice_begin_sound', lambda: cue_times.append(time.monotonic()))
    c = frame._voice_input
    try:
        c.start_recording()
        assert rig.started.wait(2)
        _pump_until(wx_app, lambda: bool(cue_times), timeout=.5)
        marks = c._session.milestones
        # A practical CI bound catches an added 800ms scheduling delay. Field
        # acceptance retains its separate 50ms goal and physical/acoustic checks.
        local_ready = max(marks['device_start'], marks['first_frame'])
        assert cue_times[0] - local_ready < .5
        assert not rig.sockets
    finally:
        c.shutdown()
        assert c._shutdown_done.wait(3)
        c._audio_thread.join(2)
        rig.assert_closed()


def test_voice_input_ui_automation_inserts_verbatim_when_editor_focused(frame, monkeypatch):
    frame.Show()
    frame.input_edit.SetValue("")
    frame.input_edit.SetFocus()
    frame._speak_text_via_screen_reader = lambda _text: None
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *args, **kwargs: fn(*args, **kwargs))
    monkeypatch.setattr(main.wx.Window, "FindFocus", lambda: frame.input_edit)

    frame._voice_input.on_result("浠婂ぉ澶╂皵涓嶉敊浠婂ぉ澶╂皵涓嶉敊", main.MODE_DIRECT)

    assert frame.input_edit.GetValue() == "浠婂ぉ澶╂皵涓嶉敊浠婂ぉ澶╂皵涓嶉敊"


def test_ui_automation_model_combo_prioritizes_cli_models(frame):
    choices = [frame.model_combo.GetString(idx) for idx in range(frame.model_combo.GetCount())]

    assert choices[:5] == [
        "codex",
        "codex gpt5.4 medium",
        "codex gpt5.3spark high",
        "claudeCode",
        "openclaw",
    ]


def test_voice_input_ui_automation_speaks_result_after_insert(frame, wx_app, monkeypatch):
    spoken = []
    frame.input_edit.SetFocus()
    monkeypatch.setattr(frame, "_speak_text_via_screen_reader", lambda text: spoken.append(text))
    monkeypatch.setattr(frame, "_call_later_if_alive", lambda delay_ms, fn, *args: fn(*args) or object())

    frame._voice_input.on_result("语音输入结果", main.MODE_DIRECT)
    wx_app.Yield()

    assert frame.input_edit.GetValue() == "语音输入结果"
    assert spoken == ["语音输入结果"]


def test_voice_input_ui_automation_duplicate_callbacks_repeat(frame, monkeypatch):
    frame.Show()
    frame.input_edit.SetValue("")
    frame.input_edit.SetFocus()
    frame._speak_text_via_screen_reader = lambda _text: None
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *args, **kwargs: fn(*args, **kwargs))
    monkeypatch.setattr(main.wx.Window, "FindFocus", lambda: frame.input_edit)

    frame._voice_input.on_result("浠婂ぉ澶╂皵涓嶉敊", main.MODE_DIRECT)
    frame._voice_input.on_result("浠婂ぉ澶╂皵涓嶉敊", main.MODE_DIRECT)

    assert frame.input_edit.GetValue() == "浠婂ぉ澶╂皵涓嶉敊浠婂ぉ澶╂皵涓嶉敊"


def test_ui_automation_ctrl_right_can_continue_past_recent_chats(frame):
    frame.Show()
    frame.current_chat_id = "chat-current"
    frame._current_chat_state["id"] = "chat-current"
    frame._current_chat_state["title"] = "当前聊天"
    frame._current_chat_state["turns"] = [{"question": "当前问题", "answer_md": "当前回答"}]
    frame._current_chat_state["updated_at"] = 4.0
    frame.archived_chats = [
        {"id": "chat-a", "title": "聊天A", "turns": [], "created_at": 1.0, "updated_at": 1.0},
        {"id": "chat-b", "title": "聊天B", "turns": [], "created_at": 2.0, "updated_at": 2.0},
        {"id": "chat-c", "title": "聊天C", "turns": [], "created_at": 3.0, "updated_at": 3.0},
    ]
    frame._refresh_history()
    frame.answer_list.SetFocus()

    class E:
        def GetKeyCode(self):
            return main.wx.WXK_RIGHT

        def ControlDown(self):
            return True

        def AltDown(self):
            return False

        def Skip(self):
            raise AssertionError("should not skip")

    frame._on_char_hook(E())
    assert frame.view_history_id == "chat-c"
    frame._on_char_hook(E())
    assert frame.view_history_id == "chat-b"


def test_ui_automation_ctrl_right_reaches_pinned_and_older_chats(frame):
    frame.Show()
    frame.current_chat_id = "chat-e"
    frame._current_chat_state["id"] = "chat-e"
    frame._current_chat_state["title"] = "聊天E"
    frame._current_chat_state["turns"] = [{"question": "当前问题", "answer_md": "当前回答"}]
    frame._current_chat_state["updated_at"] = 6.0
    frame.archived_chats = [
        {"id": "chat-b", "title": "聊天B", "turns": [], "created_at": 4.0, "updated_at": 4.0},
        {"id": "chat-f", "title": "置顶F", "turns": [], "created_at": 5.0, "updated_at": 5.0, "pinned": True},
        {"id": "chat-c", "title": "置顶C", "turns": [], "created_at": 3.0, "updated_at": 3.0, "pinned": True},
        {"id": "chat-a", "title": "聊天A", "turns": [], "created_at": 2.0, "updated_at": 2.0},
        {"id": "chat-d", "title": "聊天D", "turns": [], "created_at": 2.0, "updated_at": 2.0},
        {"id": "chat-g", "title": "聊天G", "turns": [], "created_at": 9.0, "updated_at": 9.0},
    ]
    frame._refresh_history()
    frame.answer_list.SetFocus()

    class E:
        def GetKeyCode(self):
            return main.wx.WXK_RIGHT

        def ControlDown(self):
            return True

        def AltDown(self):
            return False

        def Skip(self):
            raise AssertionError("should not skip")

    seen = [frame.current_chat_id]
    for _ in range(6):
        frame._on_char_hook(E())
        seen.append(frame.view_history_id)

    assert seen == ["chat-e", "chat-f", "chat-c", "chat-g", "chat-b", "chat-a", "chat-d"]
