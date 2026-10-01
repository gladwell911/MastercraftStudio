import ctypes
import time

import main
import pytest
from owned_window_qa import activate_owned_frame


def pump(app, seconds):
    loop = app._epic1_qa_loop
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        while loop.Pending():
            loop.Dispatch()
        app.ProcessPendingEvents()
        loop.ProcessIdle()
        time.sleep(.01)


@pytest.fixture(autouse=True)
def native_gui_loop(wx_app):
    loop = main.wx.GUIEventLoop()
    activator = main.wx.EventLoopActivator(loop)
    wx_app._epic1_qa_loop = loop
    yield loop
    del activator


@pytest.fixture(autouse=True)
def isolate_provider_reads(monkeypatch):
    monkeypatch.setattr(main.ChatFrame, "_request_codex_chat_information", lambda *a, **kw: None)
    monkeypatch.setattr(main.ChatFrame, "_request_kimi_chat_information", lambda *a, **kw: None)
    monkeypatch.setattr(main.ChatFrame, "_request_kimi_quota", lambda *a, **kw: None)


def prepare(frame, app, provider):
    app.SetTopWindow(frame)
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = provider + "/main"
    frame.active_chat_id = frame.current_chat_id = "independent-qa"
    frame._current_chat_state = {"id": "independent-qa", "model": provider + "/main",
                                "codex_thread_id": "native", "kimi_session_id": "native"}
    frame._apply_detail_panel_mode("answers")
    frame.Raise()
    activate_owned_frame(frame)
    pump(app, .1)


@pytest.mark.parametrize("provider", ["codex", "kimi"])
@pytest.mark.parametrize("control", ["input_edit", "answer_list", "history_list", "notes_editor"])
def test_native_alt_y_and_escape(frame, wx_app, native_gui_loop, provider, control):
    prepare(frame, wx_app, provider)
    if control == "notes_editor":
        frame.notes_controller.notes_view = "note_edit"
        frame._notes_sync_view_visibility()
    target = getattr(frame, control)
    target.SetFocus()
    pump(wx_app, .1)
    assert main.wx.Window.FindFocus() is target
    simulator = main.wx.UIActionSimulator()
    assert simulator.Char(ord("Y"), main.wx.MOD_ALT)
    pump(wx_app, .2)
    dialog = frame._chat_information_dialog
    assert dialog is not None
    assert dialog.focus_target is target
    assert main.wx.Window.FindFocus() is dialog.information_list
    assert simulator.Char(main.wx.WXK_ESCAPE)
    pump(wx_app, .2)
    assert frame._chat_information_dialog is None
    assert main.wx.Window.FindFocus() is target


@pytest.mark.parametrize("provider", ["codex", "kimi"])
def test_real_ten_second_timer_stops_after_close(frame, wx_app, native_gui_loop, monkeypatch, provider):
    prepare(frame, wx_app, provider)
    calls = []
    method = "_request_" + provider + "_chat_information"
    monkeypatch.setattr(frame, method, lambda *a, **kw: calls.append((time.monotonic(), kw.get("context_only", False))))
    assert frame._show_chat_information()
    dialog = frame._chat_information_dialog
    assert len(calls) == 1 and calls[0][1] is False
    assert dialog.context_timer.IsRunning() and dialog.context_timer.GetInterval() == 10000
    assert dialog.refresh_timer.IsRunning() and dialog.refresh_timer.GetInterval() == 60000
    pump(wx_app, 10.5)
    assert len(calls) == 2 and calls[1][1] is True
    assert 9.5 <= calls[1][0] - calls[0][0] <= 10.5
    dialog.Close()
    pump(wx_app, .1)
    count = len(calls)
    pump(wx_app, 10.5)
    assert len(calls) == count
    assert frame._chat_information_dialog is None
