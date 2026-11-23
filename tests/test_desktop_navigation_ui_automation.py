"""Native Alt+C and quiet history activity with isolated controlled providers."""
import ctypes
import threading
import time
from types import SimpleNamespace

import pytest
import wx

import main
from codex_client import CodexAppServerClient, CodexEvent
from kimi_server_client import KimiEvent
from kimi_test_support import _make_fake_client
from test_model_session_recovery_ui_automation import (
    native_gui_loop, native_key_input_batch, native_modifier_state, native_user32, pump, run_native_for, wait_for,
)


CONTINUE = "好的，继续"


def test_chat_shortcuts_wait_for_current_title_then_focus_last_message(frame, monkeypatch):
    owner = seed_history(frame)
    frame.archived_chats[0]["detail_panel_mode"] = "execution"
    for chat in [frame._current_chat_state, frame.archived_chats[0]]:
        chat["turns"].extend([
            {"question": "answered question", "answer_md": "old answer", "model": main.DEFAULT_CODEX_MODEL},
            {"question": "latest unanswered question", "answer_md": main.REQUESTING_TEXT,
             "model": main.DEFAULT_CODEX_MODEL},
        ])
        frame._mark_chat_turns_dirty(chat["id"])
    frame._save_state()
    focus = [frame.input_edit]
    monkeypatch.setattr(wx.Window, "FindFocus", lambda: focus[0])
    monkeypatch.setattr(frame.answer_list, "SetFocus", lambda: focus.__setitem__(0, frame.answer_list))
    callbacks = []
    call_later = wx.CallLater

    class Timer:
        def Stop(self):
            pass

    def schedule(delay, callback, *args, **kwargs):
        if callback.__name__ == "poll":
            callbacks.append(callback)
            return Timer()
        return call_later(delay, callback, *args, **kwargs)

    monkeypatch.setattr(wx, "CallLater", schedule)

    class ScreenReader:
        state = False
        titles = []
        stops = 0

        def speak_chat_navigation(self, title):
            self.titles.append(title)
            return True

        def speak_state(self):
            return self.state

        def stop(self):
            self.stops += 1

    reader = ScreenReader()
    frame._zdsr_tts = reader
    clock = [100.0]
    monkeypatch.setattr(main.time, "monotonic", lambda: clock[0])
    event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
    event.SetControlDown(True)
    event.SetKeyCode(wx.WXK_LEFT)
    frame._on_char_hook(event)
    assert frame.view_mode == "active" and reader.titles == []
    event.SetKeyCode(wx.WXK_UP)
    frame._on_char_hook(event)
    assert frame.view_history_id == "recent" and reader.titles == ["recent"]
    callbacks.pop(0)()  # Idle before audio starts must not move focus.
    assert focus[0] is frame.input_edit
    clock[0] += .5
    callbacks.pop(0)()
    assert focus[0] is frame.input_edit
    stale = callbacks.pop(0)
    frame.ProcessEvent(wx.CommandEvent(wx.wxEVT_MENU, int(frame._chat_navigation_right_id)))
    assert frame.view_mode == "active" and frame.active_chat_id == owner
    assert reader.titles[-1] == "activity owner"
    assert reader.stops == 0
    stale()
    reader.state = True
    callbacks.pop(0)()
    assert focus[0] is frame.input_edit
    reader.state = False
    callbacks.pop(0)()
    clock[0] += .08
    callbacks.pop(0)()
    assert focus[0] is frame.answer_list
    assert "latest unanswered question" in frame.answer_list.GetString(frame.answer_list.GetSelection())

    focus[0] = frame.input_edit
    frame._on_char_hook(event)
    interrupted = callbacks.pop(0)
    focus[0] = frame.history_list
    frame._on_chat_navigation_child_focus(wx.ChildFocusEvent(frame.history_list))
    assert reader.stops == 1
    focus[0] = frame.input_edit
    interrupted()
    assert focus[0] is frame.input_edit

    frame._handle_vertical_chat_shortcut(1)
    clock[0] += 2.1
    callbacks.pop(0)()  # A bundled SDK can lose the first accepted request.
    assert reader.titles[-2:] == ["activity owner", "activity owner"]
    clock[0] += 2.1
    callbacks.pop(0)()
    assert focus[0] is frame.input_edit and reader.stops == 2

    class ReplacementReader(ScreenReader):
        titles = []

        def speak(self, text):
            assert text == "voice input"
            return True

    replacement = ReplacementReader()
    monkeypatch.setattr(main, "ZDSRTTSClient", lambda: replacement)
    frame._speak_text_via_screen_reader("voice input")
    assert frame.voice_screen_reader_status["last_success"] is True
    assert frame._zdsr_tts is replacement
    frame._on_char_hook(event)
    assert replacement.titles == ["recent"]
    frame._cancel_chat_navigation_focus()
    assert replacement.stops == 1 and reader.stops == 2


def test_chat_shortcut_reveals_answers_only_after_long_title(frame, monkeypatch):
    seed_history(frame)
    target = frame._find_archived_chat("recent")
    target["turns"].append({"question": "question", "answer_md": "answer"})
    target["detail_panel_mode"] = "execution"
    frame._mark_chat_turns_dirty("recent")
    frame._save_state()
    frame._apply_detail_panel_mode("execution")
    frame.execution_list_model.replace_visible_page([("old-step", "old owner body")], selected_id="old-step")
    frame.execution_meta = [("execution", 0, "old owner body", "old owner details")]
    frame._execution_applied_owner = frame._execution_scan_key()[:3]
    focus = [frame.execution_list]
    monkeypatch.setattr(wx.Window, "FindFocus", lambda: focus[0])
    monkeypatch.setattr(frame.answer_list, "SetFocus", lambda: focus.__setitem__(0, frame.answer_list))
    pending = []
    call_later = wx.CallLater

    class Timer:
        def Stop(self):
            pass

    def schedule(delay, callback, *args, **kwargs):
        if callback.__name__ == "poll":
            pending.append(callback)
            return Timer()
        return call_later(delay, callback, *args, **kwargs)

    class Reader:
        speaking = True

        def speak_chat_navigation(self, _):
            return True

        def speak_state(self):
            return self.speaking

    clock = [100.0]
    monkeypatch.setattr(wx, "CallLater", schedule)
    monkeypatch.setattr(main.time, "monotonic", lambda: clock[0])
    frame._zdsr_tts = Reader()
    assert frame._handle_vertical_chat_shortcut(-1)
    assert focus[0] is frame.execution_list
    assert frame._execution_applied_owner == frame._execution_scan_key()[:3]
    assert frame.execution_list.FindString("old owner body") == wx.NOT_FOUND
    assert frame._selected_execution_text_viewer_content() is None
    pending.pop(0)()
    clock[0] += 20
    pending.pop(0)()
    assert pending and focus[0] is frame.execution_list
    frame._zdsr_tts.speaking = False
    pending.pop(0)()
    clock[0] += .08
    pending.pop(0)()
    assert frame.answer_list.IsShown()
    assert focus[0] is frame.answer_list
    assert "answer" in frame.answer_list.GetString(frame.answer_list.GetSelection())


def test_alt_a_retires_busy_without_resend_and_continues_after_remote_clear(frame, monkeypatch, wait_for_wx):
    owner = seed_history(frame)
    frame.active_session_turns[:] = [{
        "question": "", "attachments": [{"name": "voice.wav"}], "answer_md": main.REQUESTING_TEXT,
        "origin": "voice", "question_origin": "voice", "model": main.DEFAULT_CODEX_MODEL,
    }]
    frame.is_running = True
    frame._active_request_count = 1
    errors = []
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_speak_text_via_screen_reader", errors.append)
    assert frame._trigger_shortcut_clear()
    wait_for_wx(lambda: not frame._shortcut_clear_pending, "first local clear")
    assert not frame.active_session_turns
    assert not frame.is_running and frame._active_request_count == 0
    local = frame._current_chat_state["revision"]
    status, result = frame._remote_api_clear_context_ui({"chat_id": owner, "request_id": "remote-between"})
    assert status == 200 and result["revision"] == local + 1
    subsequent = {"question": "accepted after clear", "answer_md": "answer", "model": main.DEFAULT_CODEX_MODEL}
    frame.active_session_turns.append(subsequent)
    replay, authority = frame._remote_api_clear_context_ui({"chat_id": owner, "request_id": "remote-between"})
    assert replay == 200 and authority["revision"] == local + 1
    assert frame.active_session_turns == [subsequent]
    frame.active_session_turns.clear()
    assert frame._trigger_shortcut_clear()
    wait_for_wx(lambda: not frame._shortcut_clear_pending, "second local clear")
    assert not errors
    assert frame._current_chat_state["revision"] == local + 2


def test_alt_a_preserves_title_committed_after_snapshot(frame, monkeypatch, wait_for_wx):
    owner = seed_history(frame)
    entered, release = threading.Event(), threading.Event()
    original = frame.chat_store.begin_clear_operation

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(frame.chat_store, "begin_clear_operation", blocked)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    assert frame._trigger_shortcut_clear()
    try:
        assert entered.wait(1)
        frame._bump_chat_title_revision(frame._current_chat_state, "manual", title="committed title")
        frame.chat_store.upsert_chat(frame._slim_active_chat_state())
    finally:
        release.set()
    wait_for_wx(lambda: not frame._shortcut_clear_pending, "clear after rename")
    assert frame.chat_store.load_chat(owner)["title"] == "committed title"


def test_close_releases_keyboard_clear_claim_that_never_dispatched(frame, monkeypatch, wait_for_wx):
    owner = seed_history(frame)
    frame.active_session_turns[:] = [{"question": "resend me", "answer_md": "answer", "model": main.DEFAULT_CODEX_MODEL}]
    frame._mark_chat_turns_dirty()
    frame._save_state()
    claimed, release = threading.Event(), threading.Event()
    original = frame.chat_store.claim_clear_resend_dispatch

    def hold_claim(*args, **kwargs):
        operation = original(*args, **kwargs)
        claimed.set()
        assert release.wait(3)
        return operation

    launches = []
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_start_codex_worker_for_turn", lambda *args: launches.append(args))
    monkeypatch.setattr(frame.chat_store, "claim_clear_resend_dispatch", hold_claim)
    store = frame.chat_store
    assert frame._trigger_shortcut_clear()
    try:
        assert claimed.wait(1)
        frame.Close()
    finally:
        release.set()
    wait_for_wx(lambda: getattr(frame, "_close_complete", False), "close after claim")
    assert launches == []
    [operation] = main.ChatStore(store.db_path).recoverable_clear_operations()
    assert operation["chat_id"] == owner
    assert operation["state"] == "clear_acknowledged"


def test_alt_a_acknowledges_before_blocked_clear_and_resends_only_original_owner(frame, monkeypatch):
    owner = seed_history(frame)
    frame.active_session_turns.append({"question": "original question", "answer_md": "original answer",
                                      "model": main.DEFAULT_CODEX_MODEL, "origin": "local"})
    frame._mark_chat_turns_dirty(owner)
    frame._save_state()
    entered, release = threading.Event(), threading.Event()
    order = []
    original = frame.chat_store.begin_clear_operation

    def block_clear(*args, **kwargs):
        order.append("store")
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(frame.chat_store, "begin_clear_operation", block_clear)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: order.append("sound"))
    monkeypatch.setattr(frame, "_start_codex_worker_for_turn", lambda *args: order.append(args))
    try:
        frame.ProcessEvent(wx.CommandEvent(wx.wxEVT_MENU, int(frame._clear_context_id)))
        assert entered.wait(1)
        assert order == ["sound", "store"]
        assert frame._show_history_chat("recent", focus_answer_list=False)
        release.set()
        wait_for(lambda: not frame._shortcut_clear_pending, "keyboard-clear-completed")
        assert frame.view_history_id == "recent"
        assert order[2][0] == owner and order[2][2] == "original question"
        assert len(order) == 3
        assert frame.active_session_turns[0]["clear_operation_id"]
        assert frame.is_running and frame._active_request_count == 1
        assert frame._find_archived_chat("recent")["turns"] == []
    finally:
        release.set()


def test_alt_a_blocks_archived_send_and_does_not_recreate_deleted_owner(frame, monkeypatch):
    seed_history(frame)
    assert frame._show_history_chat("recent", focus_answer_list=False)
    entered, release = threading.Event(), threading.Event()
    original = frame.chat_store.begin_clear_operation
    spoken = []

    def hold(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(frame.chat_store, "begin_clear_operation", hold)
    monkeypatch.setattr(frame, "_speak_text_via_screen_reader", spoken.append)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    try:
        assert frame._trigger_shortcut_clear()
        assert entered.wait(1)
        assert frame._remote_api_message_ui({"chat_id": "recent", "text": "late"})[0] == 409
        assert not frame._submit_archived_remote_question(frame.archived_chats[0], "late", main.DEFAULT_CODEX_MODEL)[0]
        frame.history_list.SetSelection(frame.history_ids.index("recent"))
        frame._history_delete(None)
        assert frame.chat_store.load_chat("recent") is not None
        frame.chat_store.delete_chat("recent")
        release.set()
        wait_for(lambda: bool(spoken), "keyboard clear storage failure")
        frame._save_state()
        assert frame.chat_store.load_chat("recent") is None
        assert "无法安全清空" in spoken[0]
    finally:
        release.set()


@pytest.mark.parametrize("provider", ["generic", "claudecode"])
def test_provider_callbacks_are_bound_to_turn_at_launch(frame, monkeypatch, provider):
    owner = seed_history(frame)
    turns = frame.active_session_turns
    turns[:] = [{"question": "old"}]
    jobs, callbacks, results = [], [], []

    class HeldThread:
        def __init__(self, target, args=(), kwargs=None, **_):
            jobs.append(lambda: target(*args, **(kwargs or {})))
        def start(self):
            pass

    class Provider:
        last_context_usage = None
        def __init__(self, **_):
            pass
        def stream_chat(self, question, *args, **kwargs):
            results.append(question)
            if provider == "claudecode":
                kwargs["on_delta"]("stale delta")
                return "stale final", "stale session"
            args[0]("stale delta")
            return "stale final"

    monkeypatch.setattr(main.threading, "Thread", HeldThread)
    monkeypatch.setattr(main, "ChatClient", Provider)
    monkeypatch.setattr(main, "ClaudeCodeClient", Provider)
    monkeypatch.setattr(frame, "_call_after_if_alive", lambda callback, *a: callbacks.append(lambda: callback(*a)) or True)
    monkeypatch.setattr(frame, "_on_done", lambda *_: pytest.fail("stale final reached replacement turn"))
    monkeypatch.setattr(frame, "_on_delta", lambda *_: pytest.fail("stale delta reached replacement turn"))

    def launch():
        if provider == "claudecode":
            frame._start_claudecode_worker_for_turn(owner, 0, "old", "old-session")
        else:
            frame._launch_generic_worker(("unused", 0, "old", "openai/gpt-5.2", False, owner))

    launch()
    launch()
    jobs[0]()  # Provider has queued its output but wx has not consumed it.
    turns[:] = [{"question": "replacement"}]
    jobs[1]()  # This provider was launched before replacement but starts now.
    for callback in callbacks:
        callback()
    assert results == ["old"]
    assert turns == [{"question": "replacement"}]


def seed_history(frame, *, pinned=False, active_pinned=False):
    frame._on_new_chat_clicked(None)
    owner = frame._ensure_active_chat_id()
    assert owner and frame.current_chat_id == owner and frame._current_chat_state["id"] == owner
    frame._current_chat_state.update(
        id=owner, title="activity owner", created_at=1.0, updated_at=1.0,
        pinned=active_pinned, turns=frame.active_session_turns,
    )
    frame.archived_chats = [
        {"id": "recent", "title": "recent", "created_at": 20.0, "updated_at": 20.0,
         "pinned": False, "model": main.DEFAULT_CODEX_MODEL, "turns": []},
    ]
    if pinned:
        frame.archived_chats.insert(0, {
            "id": "pinned", "title": "pinned", "created_at": 30.0,
            "updated_at": 30.0, "pinned": True, "model": main.DEFAULT_CODEX_MODEL, "turns": [],
        })
    frame._refresh_history(owner)
    frame._save_state()
    assert frame.chat_store.load_chat(owner) is not None
    assert frame.chat_store.load_chat(owner)["pinned"] == frame._current_chat_state["pinned"]
    assert frame.active_chat_id == frame.current_chat_id == frame._current_chat_state["id"] == owner
    assert frame.history_list_model.selected_id() == owner
    return owner


def activate(frame, control):
    user32 = native_user32()
    for attempt in range(3):
        pump()
        frame.Show()
        frame.Raise()
        user32.SetForegroundWindow(ctypes.c_void_p(int(frame.GetHandle())))
        control.SetFocus()
        deadline = time.monotonic() + .5
        while time.monotonic() < deadline:
            pump()
            focus = wx.Window.FindFocus()
            if (int(user32.GetForegroundWindow() or 0) == int(frame.GetHandle())
                    and focus is not None and int(focus.GetHandle()) == int(control.GetHandle())
                    and control.HasFocus()):
                pump()
                focus = wx.Window.FindFocus()
                if (int(user32.GetForegroundWindow() or 0) == int(frame.GetHandle())
                        and focus is not None and int(focus.GetHandle()) == int(control.GetHandle())
                        and control.HasFocus()):
                    return
            time.sleep(.01)
    focus = wx.Window.FindFocus()
    raise AssertionError({"phase": "native-activation", "frame": int(frame.GetHandle()),
                          "control": int(control.GetHandle()), "has_focus": control.HasFocus(),
                          "actual_focus": int(focus.GetHandle()) if focus else None,
                          "foreground": int(user32.GetForegroundWindow() or 0)})


@pytest.mark.parametrize("origin", ["input_edit", "answer_list", "history_list"])
def test_native_chat_navigation_replaces_titles_and_delays_native_rows(frame, monkeypatch, origin):
    owner = seed_history(frame, pinned=True)
    for chat in [frame._current_chat_state, *frame.archived_chats]:
        chat["turns"].append({"question": "question", "answer_md": f"answer for {chat['title']}"})
        frame._mark_chat_turns_dirty(chat["id"])
    frame._save_state()
    frame._render_answer_list()

    class Reader:
        def __init__(self):
            self.titles = []
            self.voice = []
            self.speaking = False
            self.stops = 0

        def speak(self, text):
            self.voice.append(text)
            return True

        def speak_chat_navigation(self, title):
            self.titles.append(title)
            self.speaking = True
            return True

        def speak_state(self):
            return self.speaking

        def stop(self):
            self.stops += 1
            self.speaking = False

    reader = frame._zdsr_tts = Reader()
    frame._speak_text_via_screen_reader("voice input")
    assert reader.voice == ["voice input"]
    control = getattr(frame, origin)
    activate(frame, control)
    frame.answer_list.SetSelection(frame.answer_list.GetCount() - 1)
    original_answer = frame.answer_list.GetString(frame.answer_list.GetSelection())
    history_selections = []
    set_history_selection = frame.history_list.SetSelection

    def select_history(idx):
        history_selections.append(frame.history_ids[idx])
        set_history_selection(idx)

    monkeypatch.setattr(frame.history_list, "SetSelection", select_history)

    def send(keys, phase):
        assert native_user32().GetForegroundWindow() == frame.GetHandle()
        assert native_key_input_batch(keys, phase) == len(keys)
        run_native_for(120)

    def navigate(key):
        send([(0xA2, 0), (key, 1), (key, 3), (0xA2, 2)], "chat-navigation")

    navigate(0x26)
    navigate(0x26)
    navigate(0x28)
    assert reader.titles == ["recent", "pinned", "recent"] and reader.stops == 0
    assert wx.Window.FindFocus() is control
    assert frame.history_list_model.selected_id() == owner
    assert frame.answer_list.GetString(frame.answer_list.GetSelection()) == original_answer
    assert history_selections == []
    assert frame._open_selected_answer_text_viewer() is False
    assert frame._selected_read_target() is None
    if origin == "answer_list":
        assert frame.view_history_id == "recent"
        assert "activity owner" in original_answer
    reader.speaking = False
    run_native_for(180)
    assert wx.Window.FindFocus() is frame.answer_list
    assert "answer for recent" in frame.answer_list.GetString(frame.answer_list.GetSelection())
    assert frame.history_list_model.selected_id() == owner
    assert history_selections == []

    if origin == "answer_list":
        opened = []
        monkeypatch.setattr(frame, "_open_answer_text_viewer", lambda _title, value, *_args: opened.append(value) or True)
        navigate(0x28)
        assert "answer for recent" in frame.answer_list.GetString(frame.answer_list.GetSelection())
        send([(0x0D, 0), (0x0D, 2)], "ordinary-enter-key")
        assert reader.stops == 1 and opened == ["answer for activity owner"]
        navigate(0x26)
        reader.speaking = False
        run_native_for(180)

    send([(0xA0, 0), (0x09, 0), (0x09, 2), (0xA0, 2)], "return-to-history")
    assert frame.history_list.HasFocus()
    assert frame.history_list_model.selected_id() == "recent"
    assert history_selections == ["recent"]
    send([(0x26, 1), (0x26, 3)], "ordinary-history-arrow")
    assert frame.history_list_model.selected_id() == "pinned"
    send([(0x0D, 0), (0x0D, 2)], "ordinary-history-enter")
    assert frame.view_history_id == "pinned" and frame.answer_list.HasFocus()

    activate(frame, frame.input_edit)
    navigate(0x28)
    navigate(0x28)
    assert reader.titles[-1] == "activity owner"
    frame.input_edit.ChangeValue("ordinary input")
    frame.input_edit.SetInsertionPointEnd()
    before_stops = reader.stops
    send([(0x24, 1), (0x24, 3)], "ordinary-home-key")
    assert reader.stops == before_stops + 1 and wx.Window.FindFocus() is frame.input_edit
    assert frame.input_edit.GetInsertionPoint() == 0


@pytest.mark.parametrize("completion", ["normal", "unavailable", "key", "up", "blank"])
def test_history_origin_keeps_old_selection_until_user_operates_list(frame, monkeypatch, completion):
    owner = seed_history(frame, pinned=True)
    activate(frame, frame.history_list)

    class Reader:
        speaking = True
        stops = 0

        def speak_chat_navigation(self, _title):
            return completion != "unavailable"

        def speak_state(self):
            return self.speaking

        def stop(self):
            self.stops += 1
            self.speaking = False

    reader = frame._zdsr_tts = Reader()
    assert frame._navigate_history_chats(-1)
    frame._refresh_history("recent")
    assert frame.history_list_model.selected_id() == owner
    if completion == "normal":
        run_native_for(70)
        assert frame.history_list_model.selected_id() == owner
        reader.speaking = False
        run_native_for(180)
    elif completion == "unavailable":
        run_native_for(90)
    assert frame.history_list_model.selected_id() == owner and frame.view_history_id == "recent"
    assert wx.Window.FindFocus() is frame.history_list
    if completion == "blank":
        monkeypatch.setattr(frame.history_list, "HitTest", lambda _: wx.NOT_FOUND)
        event = wx.MouseEvent(wx.wxEVT_LEFT_DOWN)
        event.SetEventObject(frame.history_list)
        frame.history_list.GetEventHandler().ProcessEvent(event)
        assert reader.stops == 1 and frame._chat_navigation_timer is None
        assert frame.history_list_model.selected_id() == owner and frame.history_list.HasFocus()
    if completion in {"up", "blank"}:
        assert native_key_input_batch([(0x26, 1), (0x26, 3)], "history-up-while-focused") == 2
        run_native_for(90)
        assert reader.stops == 1 and frame._chat_navigation_timer is None
        assert frame.view_history_id == "recent" and frame.history_list.HasFocus()
        assert frame.history_list_model.selected_id() == "pinned"
        return
    assert native_key_input_batch([(0x0D, 0), (0x0D, 2)], "history-enter") == 2
    run_native_for(90)
    assert frame.history_list_model.selected_id() == frame.view_history_id == "recent"
    assert wx.Window.FindFocus() is frame.answer_list


def test_answer_mouse_discards_frozen_coordinates_then_reads_next_click(frame, monkeypatch):
    reads, skipped, hit = [], [], []
    event = SimpleNamespace(Skip=lambda: skipped.append(True), GetPosition=lambda: (10, 20))
    monkeypatch.setattr(frame.answer_list, "HitTest", lambda point: hit.append(point) or 0)
    monkeypatch.setattr(frame, "_selected_read_target", lambda row: {"row": row})
    monkeypatch.setattr(frame, "_confirm_answer_read", reads.append)
    frame._chat_navigation_answer_rows_deferred = True
    frame._on_answer_mouse_read(event)
    assert not frame._chat_navigation_answer_rows_deferred
    assert not skipped and not hit and not reads
    frame._on_answer_mouse_read(event)
    assert skipped == [True] and hit == [(10, 20)] and reads == [{"row": 0}]


def test_answer_mouse_release_after_focus_refresh_discards_press_on_old_rows(frame, monkeypatch):
    seed_history(frame)
    for chat in (frame._current_chat_state, frame.archived_chats[0]):
        chat["turns"].append({"question": "question", "answer_md": f"answer for {chat['title']}"})
        frame._mark_chat_turns_dirty(chat["id"])
    frame._save_state()
    frame._render_answer_list()
    activate(frame, frame.input_edit)
    stops = []
    frame._zdsr_tts = SimpleNamespace(speak_chat_navigation=lambda title: True,
                                     speak_state=lambda: True,
                                     stop=lambda: stops.append(True))
    reads = []
    hits = []
    answer = frame.answer_list
    monkeypatch.setattr(answer, "HitTest", lambda point: hits.append(point) or answer.GetCount() - 1)
    monkeypatch.setattr(frame, "_selected_read_target", lambda index: answer.GetString(index))
    monkeypatch.setattr(frame, "_confirm_answer_read", reads.append)

    def dispatch(event_type):
        event = wx.MouseEvent(event_type)
        event.SetEventObject(answer)
        answer.GetEventHandler().ProcessEvent(event)

    assert frame._navigate_history_chats(-1)
    assert "answer for activity owner" in answer.GetString(answer.GetCount() - 1)
    dispatch(wx.wxEVT_LEFT_DOWN)
    answer.SetFocus()
    run_native_for(60)
    assert answer.HasFocus()
    assert stops == [True] and not frame._chat_navigation_answer_rows_deferred
    refreshed = answer.GetString(answer.GetCount() - 1)
    assert "answer for recent" in refreshed
    dispatch(wx.wxEVT_LEFT_UP)
    assert not hits and not reads
    dispatch(wx.wxEVT_LEFT_DOWN)
    dispatch(wx.wxEVT_LEFT_UP)
    assert len(hits) == 1 and reads == [refreshed]


def alt_c():
    inserted = []
    timer = wx.CallLater(30, lambda: inserted.append(native_key_input_batch(
        [(0x12, 0), (0x43, 0), (0x43, 2), (0x12, 2)], "alt-c")))
    try:
        run_native_for(200)
        assert inserted == [4]
    finally:
        timer.Stop()
        native_key_input_batch([(0x43, 2), (0x12, 2)], "alt-c-release")


@pytest.mark.parametrize("control_name", ["history_list", "answer_list", "input_edit", "model_combo"])
@pytest.mark.parametrize("provider", ["codex", "kimi"])
def test_native_alt_c_sends_exactly_once_and_displays_owned_answer(
    frame, monkeypatch, control_name, provider,
):
    owner = seed_history(frame)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)
    monkeypatch.setattr(frame, "_schedule_first_question_auto_title", lambda *a: None)
    model = main.DEFAULT_CODEX_MODEL if provider == "codex" else main.DEFAULT_KIMI_MODEL
    frame.selected_model = model
    frame.model_combo.SetValue(main.model_display_name(model))
    starts = []
    fake = _make_fake_client(monkeypatch) if provider == "kimi" else None
    if provider == "codex":
        monkeypatch.setattr(frame, "_start_codex_worker_for_turn", lambda *a: starts.append(a))
    activate(frame, getattr(frame, control_name))
    alt_c()
    wait_for(lambda: len(frame.active_session_turns) == 1, "native-alt-c-one-turn")
    turn = frame.active_session_turns[0]
    assert turn["question"] == CONTINUE
    assert frame.active_chat_id == owner
    sent_at = frame._current_chat_state["updated_at"]
    assert sent_at == turn["created_at"]
    answer = provider + " controlled continued answer"
    if fake is not None:
        wait_for(lambda: len(fake.submitted) == 1, "kimi-one-submission")
        assert fake.submitted[0]["blocks"] == [{"type": "text", "text": CONTINUE}]
        session = fake.submitted[0]["session_id"]
        fake.push_event(KimiEvent(type="turn_started", thread_id=session, turn_id="1"))
        fake.push_event(KimiEvent(type="agent_message_delta", thread_id=session,
                                 turn_id="1", text=answer, display_kind="assistant"))
        completion = KimiEvent(type="turn_completed", thread_id=session, turn_id="1", status="completed")
        fake.push_event(completion)
    else:
        assert len(starts) == 1 and starts[0][0] == owner and starts[0][2] == CONTINUE
        turn["codex_turn_id"] = "native-continue"
        frame._on_codex_event_for_chat(owner, CodexEvent(
            type="item_completed", turn_id="native-continue", phase="final_answer", text=answer,
            data={"turn_idx": 0},
        ))
        assert frame._current_chat_state["updated_at"] == sent_at
        completion = CodexEvent(type="turn_completed", turn_id="native-continue", status="completed",
                                data={"turn_idx": 0})
        frame._on_codex_event_for_chat(owner, completion)
    wait_for(lambda: turn.get("request_status") == "done" and any(
        answer in frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())
    ), "native-alt-c-visible-answer")
    completed_at = frame._current_chat_state["updated_at"]
    assert completed_at >= sent_at
    if fake is not None:
        fake.push_event(completion)
    else:
        frame._on_codex_event_for_chat(owner, completion)
    run_native_for(100)
    assert frame._current_chat_state["updated_at"] == completed_at
    frame._save_state()
    persisted = frame.chat_store.load_chat(owner)
    assert [t["question"] for t in persisted["turns"]] == [CONTINUE]
    assert persisted["turns"][0]["answer_md"] == answer


@pytest.mark.parametrize("pinned,active_pinned", [(False, False), (True, False), (True, True)])
@pytest.mark.parametrize("fact", ["send", "completion"])
def test_history_activity_keeps_selection_focus_and_pinned_partition(
    frame, monkeypatch, pinned, active_pinned, fact,
):
    owner = seed_history(frame, pinned=pinned, active_pinned=active_pinned)
    monkeypatch.setattr(frame, "_schedule_first_question_auto_title", lambda *a: None)
    monkeypatch.setattr(frame, "_start_codex_worker_for_turn", lambda *a: None)
    frame.selected_model = main.DEFAULT_CODEX_MODEL
    frame.model_combo.SetValue(main.model_display_name(main.DEFAULT_CODEX_MODEL))
    frame._navigation_quiet_until = time.monotonic() + 2.0
    if fact == "send":
        assert frame._submit_question("activity")[0]
    else:
        turn = {"question": "activity", "answer_md": main.REQUESTING_TEXT,
                "model": main.DEFAULT_CODEX_MODEL, "request_status": "pending", "created_at": 1.0}
        frame.active_session_turns.append(turn)
        frame._on_done(0, "authoritative answer", "", main.DEFAULT_CODEX_MODEL, "", owner)
    # Select a different identity after the activity. The delayed refresh must
    # preserve that user's selection rather than selecting the activity owner.
    frame.history_list.SetSelection(frame.history_ids.index("recent"))
    activate(frame, frame.history_list)
    frame._navigation_quiet_until = time.monotonic() + .4
    before = list(frame.history_ids)
    frame._flush_idle_ui_refreshes()
    assert frame.history_ids == before
    frame._navigation_quiet_until = 0
    frame._last_primary_interaction_at = 0
    frame._flush_idle_ui_refreshes()
    pump()
    expected = ["pinned", owner, "recent"] if pinned and not active_pinned else (
        [owner, "pinned", "recent"] if pinned else [owner, "recent"])
    assert frame.history_ids == expected
    assert frame.history_list_model.selected_id() == "recent"
    assert frame.history_list.HasFocus()
    frame._save_state()
    stored = [c["id"] for c in frame.chat_store.list_chat_summaries()]
    assert stored == expected


def test_late_worker_completion_only_moves_its_owner_once(frame, monkeypatch):
    owner = seed_history(frame)
    background = frame.archived_chats[0]
    background["updated_at"] = .5
    background["turns"] = [{"question": "old request", "answer_md": main.REQUESTING_TEXT,
                             "model": main.DEFAULT_CODEX_MODEL, "request_status": "pending"}]
    frame._refresh_history(owner)
    activate(frame, frame.input_edit)
    foreground_time = frame._current_chat_state["updated_at"]
    frame._on_done(0, "late answer", "", main.DEFAULT_CODEX_MODEL, "", "recent")
    accepted_at = background["updated_at"]
    assert frame._current_chat_state["updated_at"] == foreground_time
    frame._on_done(0, "late answer", "", main.DEFAULT_CODEX_MODEL, "", "recent")
    assert background["updated_at"] == accepted_at
    assert frame.input_edit.HasFocus()
    redraws = []
    original = frame._request_listbox_repaint
    monkeypatch.setattr(frame, "_request_listbox_repaint", lambda *a, **k: (redraws.append(1), original(*a, **k))[1])
    frame._navigation_quiet_until = 0
    frame._last_primary_interaction_at = 0
    frame._flush_idle_ui_refreshes()
    redraws.clear()
    frame._mark_history_list_dirty()
    frame._flush_idle_ui_refreshes()
    assert redraws == []


class ControlledCodexClient:
    """Push controlled worker messages through the real source-client callback."""
    def __init__(self, frame, owner):
        self.frame = frame
        self.owner = owner

    def close(self):
        pass

    def push(self, kind, *, generation=0, text="late A authoritative answer"):
        event = CodexEvent(type=kind, turn_id="turn-" + self.owner,
                           thread_id="thread-" + self.owner, phase="final_answer", text=text,
                           status="completed", data={"turn_idx": 0, "context_generation": generation})
        self.frame._on_codex_worker_message(self.owner, {
            "type": "event", "payload": {"chat_id": self.owner, "turn_idx": 0,
            "context_generation": generation, "event": main.codex_worker_protocol.event_to_payload(event)},
        }, self)


def controlled_codex_starts(frame, monkeypatch):
    clients = {}
    def start(owner, index, question, model):
        chat = frame._current_chat_state if owner == frame.active_chat_id else frame._find_archived_chat(owner)
        turn = chat["turns"][index]
        turn.update(codex_turn_id="turn-" + owner, codex_thread_id="thread-" + owner,
                    codex_start_generation=0, codex_context_generation=0)
        chat.update(codex_thread_id="thread-" + owner, codex_context_generation=0)
        client = ControlledCodexClient(frame, owner)
        clients[owner] = client
        frame._codex_clients[owner] = client
    monkeypatch.setattr(frame, "_start_codex_worker_for_turn", start)
    monkeypatch.setattr(frame, "_schedule_first_question_auto_title", lambda *a: None)
    monkeypatch.setattr(frame, "_request_codex_chat_information", lambda *a: None)
    frame.selected_model = main.DEFAULT_CODEX_MODEL
    frame.model_combo.SetValue(main.model_display_name(main.DEFAULT_CODEX_MODEL))
    return clients


def test_switch_a_to_b_keeps_surface_owner_when_a_client_completes(frame, monkeypatch):
    owner_a = seed_history(frame, pinned=True)
    frame._save_state()
    clients = controlled_codex_starts(frame, monkeypatch)
    activate(frame, frame.input_edit)
    alt_c()
    wait_for(lambda: owner_a in clients, "A-real-shortcut-submitted")
    # Select B in the actual history control and activate it with Enter.
    frame.history_list.SetSelection(frame.history_ids.index("recent"))
    activate(frame, frame.history_list)
    assert native_key_input_batch([(0x0D, 0), (0x0D, 2)], "history-B-enter") == 2
    run_native_for(100)
    wait_for(lambda: frame.view_history_id == "recent", "B-history-visible")
    activate(frame, frame.input_edit)
    alt_c()
    wait_for(lambda: frame.active_chat_id == "recent" and "recent" in clients, "B-current-owner")
    before_b = frame._current_chat_state["updated_at"]
    before_answers = list(frame.answer_list.GetStrings())
    frame.history_list.SetSelection(frame.history_ids.index("recent"))
    activate(frame, frame.history_list)
    clients[owner_a].push("item_completed")
    clients[owner_a].push("turn_completed")
    wait_for(lambda: frame._find_archived_chat(owner_a)["turns"][0].get("request_status") == "done",
             "A-client-authoritative-completion")
    wait_for(lambda: frame.history_ids == ["pinned", owner_a, "recent"], "A-visible-recency")
    accepted_at = frame._find_archived_chat(owner_a)["updated_at"]
    assert frame.active_chat_id == "recent"
    assert frame._current_chat_state["updated_at"] == before_b
    assert frame.history_list_model.selected_id() == "recent"
    assert frame.history_list.HasFocus()
    assert list(frame.answer_list.GetStrings()) == before_answers
    clients[owner_a].push("turn_completed")
    # A retired source and an obsolete generation must be rejected before UI application.
    retired = clients[owner_a]
    replacement = ControlledCodexClient(frame, owner_a)
    frame._codex_clients[owner_a] = replacement
    retired.push("item_completed", text="retired source")
    replacement.push("item_completed", generation=-1, text="obsolete generation")
    run_native_for(100)
    assert frame._find_archived_chat(owner_a)["updated_at"] == accepted_at
    assert frame.history_ids == ["pinned", owner_a, "recent"]
    assert frame.history_list_model.selected_id() == "recent"
    assert frame.history_list.HasFocus()
    assert frame._current_chat_state["updated_at"] == before_b
    assert list(frame.answer_list.GetStrings()) == before_answers


def test_native_history_navigation_defers_current_completion_reorder(frame, monkeypatch):
    owner = seed_history(frame, pinned=True)
    clients = controlled_codex_starts(frame, monkeypatch)
    # Submit, then put the newer chat ahead again so completion has a visible move.
    assert frame._submit_question("navigation request")[0]
    frame.archived_chats[1]["updated_at"] = frame._current_chat_state["updated_at"] + .01
    frame._refresh_history(owner)
    activate(frame, frame.history_list)
    before = list(frame.history_ids)
    repaints = []
    original = frame._request_listbox_repaint
    monkeypatch.setattr(frame, "_request_listbox_repaint", lambda control: (
        repaints.append(control.GetHandle()), original(control))[1])
    received = []
    touch = frame._touch_navigation_quiet_window
    def observe(event):
        touch(event)
        if event.GetKeyCode() in (wx.WXK_UP, wx.WXK_DOWN):
            received.append((event.GetKeyCode(), event.ControlDown(), event.AltDown(),
                             event.ShiftDown(), frame.history_list.GetSelection(),
                             frame._navigation_quiet_active()))
    monkeypatch.setattr(frame, "_touch_navigation_quiet_window", observe)
    releases = wx.CallLater(20, lambda: native_key_input_batch(
        [(0x11, 2), (0x12, 2), (0x10, 2)], "navigation-modifier-release"))
    try:
        run_native_for(60)
    finally:
        releases.Stop()
    assert not any(native_modifier_state(native_user32()).values())
    def arrow(vk):
        inserted = []
        selected_before = frame.history_list.GetSelection()
        timer = wx.CallLater(20, lambda: inserted.append(native_key_input_batch(
            [(vk, 1), (vk, 3)], "history-navigation-extended")))
        try:
            run_native_for(100)
            assert inserted == [2]
            assert received and received[-1][0] == (wx.WXK_UP if vk == 0x26 else wx.WXK_DOWN)
            assert received[-1][1:4] == (False, False, False)
            assert received[-1][-1]
            print("NATIVE_HISTORY_ARROW", received[-1], "selection", selected_before,
                  "->", frame.history_list.GetSelection(), flush=True)
        finally:
            timer.Stop()
    arrow(0x26)
    wait_for(frame._navigation_quiet_active, "real-arrow-quiet-gate")
    clients[owner].push("item_completed")
    clients[owner].push("turn_completed")
    wait_for(lambda: frame.active_session_turns[0].get("request_status") == "done",
             "completion-during-real-navigation")
    for vk in (0x28, 0x26, 0x28, 0x26):
        arrow(vk)
        assert frame._navigation_quiet_active()
        assert frame.history_ids == before
        assert int(frame.history_list.GetHandle()) not in repaints
    selected = frame.history_list_model.selected_id()
    wait_for(lambda: frame.history_ids == ["pinned", owner, "recent"], "navigation-quiet-final-order")
    assert frame.history_list_model.selected_id() == selected
    assert frame.history_list.HasFocus()


def test_active_pinned_state_roundtrips_and_unpin_is_persisted(frame):
    owner = seed_history(frame, pinned=True, active_pinned=True)
    frame._record_chat_activity(frame._current_chat_state, time.time())
    frame._save_state()
    assert frame.chat_store.load_chat(owner)["pinned"] is True
    assert [chat["id"] for chat in frame.chat_store.list_chat_summaries()] == [owner, "pinned", "recent"]
    frame._current_chat_state["pinned"] = False
    frame._save_state()
    assert frame.chat_store.load_chat(owner)["pinned"] is False
    assert [chat["id"] for chat in frame.chat_store.list_chat_summaries()] == ["pinned", owner, "recent"]


@pytest.mark.parametrize("archived", [False, True])
@pytest.mark.parametrize("status", ["failed", "interrupted", "completed", ""])
def test_protocol_mapped_codex_terminal_activity_is_success_only(frame, monkeypatch, archived, status):
    owner = seed_history(frame, pinned=True)
    monkeypatch.setattr(frame, "_request_codex_chat_information", lambda *a: None)
    target = frame.archived_chats[1] if archived else frame._current_chat_state
    target_id = target["id"]
    target["updated_at"] = .5
    turn = {"question": "terminal request", "answer_md": main.REQUESTING_TEXT,
            "request_status": "pending", "model": main.DEFAULT_CODEX_MODEL,
            "codex_turn_id": "terminal-turn", "codex_thread_id": "terminal-thread"}
    target["turns"] = [turn]
    target["codex_thread_id"] = "terminal-thread"
    if not archived:
        frame.active_session_turns = target["turns"]
    frame._refresh_history(owner)
    activate(frame, frame.input_edit)
    before_order = list(frame.history_ids)
    before_foreground = frame._current_chat_state["updated_at"]
    client = CodexAppServerClient(on_event=lambda event: frame._on_codex_event_for_chat(target_id, event))
    message = {"method": "turn/completed", "params": {
        "threadId": "terminal-thread", "turn": {"id": "terminal-turn", "status": status},
    }}
    client._handle_message(message)
    assert turn["request_status"] == "done"  # Retain the existing terminal handling.
    if status in {"failed", "interrupted"}:
        run_native_for(100)
        assert target["updated_at"] == .5
        assert frame.history_ids == before_order
    else:
        assert target["updated_at"] > .5
        expected = ["pinned", "recent", owner] if archived else ["pinned", owner, "recent"]
        wait_for(lambda: frame.history_ids == expected, "mapped-success-history-order")
    accepted_at = target["updated_at"]
    client._handle_message(message)
    run_native_for(100)
    assert target["updated_at"] == accepted_at
    if archived:
        assert frame._current_chat_state["updated_at"] == before_foreground


def test_openclaw_visible_sync_reorders_pinned_history_once(frame, monkeypatch):
    owner = seed_history(frame, pinned=True)
    frame._stop_openclaw_sync()
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)
    frame.active_openclaw_session_id = "openclaw-activity"
    frame._current_chat_state["openclaw_session_id"] = "openclaw-activity"
    # The sync consumer, rather than the submission ACK, owns reply activity.
    state = {"chat_id": owner, "session_id": "openclaw-activity", "offset": 100,
             "session_file": "", "file_changed": False, "session_changed": False}
    event = main.OpenClawSyncEvent(event_id="incoming-activity", role="assistant",
                                  text="OpenClaw authoritative visible answer", timestamp=time.time())
    frame.history_list.SetSelection(frame.history_ids.index("recent"))
    activate(frame, frame.history_list)
    before = frame._current_chat_state["updated_at"]
    frame._apply_openclaw_sync_batch(state, [event])
    assert frame._current_chat_state["updated_at"] > before
    wait_for(lambda: frame.history_ids == ["pinned", owner, "recent"], "openclaw-visible-history")
    assert any(event.text in text for text in frame.answer_list.GetStrings())
    assert frame.history_list_model.selected_id() == "recent"
    assert frame.history_list.HasFocus()
    accepted_at = frame._current_chat_state["updated_at"]
    repaints = []
    monkeypatch.setattr(frame, "_request_listbox_repaint", lambda control: repaints.append(control.GetHandle()))
    frame._apply_openclaw_sync_batch(state, [event])
    frame._apply_openclaw_sync_batch(state, [])
    run_native_for(100)
    assert frame._current_chat_state["updated_at"] == accepted_at
    assert frame.history_ids == ["pinned", owner, "recent"]
    assert frame.history_list_model.selected_id() == "recent"
    assert frame.history_list.HasFocus()
    assert repaints == []


def test_kimi_client_completion_moves_owner_after_competing_activity(frame, monkeypatch):
    owner = seed_history(frame, pinned=True)
    fake = _make_fake_client(monkeypatch)
    monkeypatch.setattr(frame, "_schedule_first_question_auto_title", lambda *a: None)
    frame.selected_model = main.DEFAULT_KIMI_MODEL
    frame.model_combo.SetValue(main.model_display_name(main.DEFAULT_KIMI_MODEL))
    assert frame._submit_question("Kimi receive activity")[0]
    wait_for(lambda: len(fake.submitted) == 1, "kimi-receive-provider-submitted")
    sent_at = frame._current_chat_state["updated_at"]
    session = fake.submitted[0]["session_id"]
    frame.archived_chats[1]["updated_at"] = time.time()
    frame._refresh_history("recent")
    assert frame.history_ids == ["pinned", "recent", owner]
    activate(frame, frame.history_list)
    fake.push_event(KimiEvent(type="turn_started", thread_id=session, turn_id="receipt-turn"))
    fake.push_event(KimiEvent(type="agent_message_delta", thread_id=session, turn_id="receipt-turn",
                             text="Kimi authoritative receipt", display_kind="assistant"))
    assert frame._current_chat_state["updated_at"] == sent_at
    completion = KimiEvent(type="turn_completed", thread_id=session, turn_id="receipt-turn", status="completed")
    fake.push_event(completion)
    wait_for(lambda: frame.active_session_turns[0].get("request_status") == "done", "kimi-receive-done")
    completed_at = frame._current_chat_state["updated_at"]
    assert completed_at > sent_at
    wait_for(lambda: frame.history_ids == ["pinned", owner, "recent"], "kimi-receive-partition-order")
    wait_for(lambda: any("Kimi authoritative receipt" in text for text in frame.answer_list.GetStrings()),
             "kimi-receive-visible-answer")
    assert frame.history_list_model.selected_id() == "recent"
    assert frame.history_list.HasFocus()
    fake.push_event(completion)
    run_native_for(100)
    assert frame._current_chat_state["updated_at"] == completed_at
    assert frame.history_ids == ["pinned", owner, "recent"]
    assert frame.history_list_model.selected_id() == "recent"
    assert frame.history_list.HasFocus()
