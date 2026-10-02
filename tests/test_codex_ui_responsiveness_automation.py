import ctypes
import time
import threading

import pytest

import main


def test_real_ui_switched_chat_final_visible_after_actual_switch(request, wx_app, monkeypatch):
    from owned_window_qa import activate_owned_frame

    cleanup = _track_ui_timers_for_test(monkeypatch)
    frame = request.getfixturevalue("frame")
    activate_owned_frame(frame)
    frame._chat_store_enabled = True
    frame.active_chat_id = frame.current_chat_id = "switch-owner"
    frame.active_session_turns = [{
        "question": "second question", "answer_md": main.REQUESTING_TEXT,
        "model": main.DEFAULT_CODEX_MODEL, "request_status": "pending",
        "codex_context_generation": 1,
    }]
    frame._current_chat_state = {
        "id": "switch-owner", "title": "owner", "model": main.DEFAULT_CODEX_MODEL,
        "turns": frame.active_session_turns, "codex_context_generation": 1,
    }
    monkeypatch.setattr(frame, "_request_codex_chat_information", lambda *_args: None)
    monkeypatch.setattr(frame, "_schedule_async_archive_rename", lambda *_args: None)
    frame._apply_codex_worker_thread_state("switch-owner", {
        "chat_id": "switch-owner", "turn_idx": 0, "thread_id": "switch-thread",
        "turn_id": "switch-turn", "context_generation": 1, "active": True,
    })
    assert frame._current_chat_state["codex_context_generation"] == 2
    frame.chat_store.upsert_chat({"id": "other-owner", "title": "other", "model": main.DEFAULT_CODEX_MODEL})
    frame.archived_chats = [frame.chat_store.load_chat("other-owner")]
    try:
        assert frame._switch_current_chat("other-owner")
        frame.input_edit.SetValue("unsent other draft")
        frame.input_edit.SetFocus()
        wx_app.Yield()
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        assert int(user32.GetForegroundWindow() or 0) == int(frame.GetHandle())
        assert frame.input_edit.HasFocus()
        for kind, kwargs in (
            ("item_completed", {"phase": "final_answer", "text": "switched final answer"}),
            ("turn_completed", {"status": "completed"}),
        ):
            frame._dispatch_codex_event_to_ui("switch-owner", main.CodexEvent(
                type=kind, thread_id="switch-thread", turn_id="switch-turn",
                data={"turn_idx": 0, "context_generation": 1}, **kwargs,
            ))
        assert _yield_until(wx_app, lambda: frame._find_archived_chat("switch-owner")["turns"][0].get("request_status") == "done")
        assert frame.current_chat_id == "other-owner"
        assert frame.input_edit.HasFocus()
        assert int(user32.GetForegroundWindow() or 0) == int(frame.GetHandle())
        assert frame.input_edit.GetValue() == "unsent other draft"
        frame._persist_chat_history_to_store()
        loaded = main.ChatStore(frame.chat_store.db_path).load_chat("switch-owner")
        assert loaded["turns"][0]["answer_md"] == "switched final answer"
        assert loaded["turns"][0]["request_status"] == "done"
        assert frame._switch_current_chat("switch-owner")
        assert _yield_until(wx_app, lambda: any(
            "switched final answer" in frame.answer_list.GetString(idx)
            for idx in range(frame.answer_list.GetCount())
        ))
        assert frame.answer_list.IsShown()
    finally:
        cleanup(frame)


def _send_listbox_key(window, key_code):
    _send_window_key(window, key_code)


def _send_window_key(window, key_code, *, shift=False, ctrl=False, alt=False):
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    wm_keydown = 0x0100
    wm_keyup = 0x0101
    scan_codes = {
        main.wx.WXK_UP: 0x48,
        main.wx.WXK_DOWN: 0x50,
        main.wx.WXK_HOME: 0x47,
        main.wx.WXK_END: 0x4F,
        main.wx.WXK_RETURN: 0x1C,
        main.wx.WXK_NUMPAD_ENTER: 0x1C,
        main.wx.WXK_F1: 0x3B,
    }
    virtual_keys = {
        main.wx.WXK_UP: 0x26,
        main.wx.WXK_DOWN: 0x28,
        main.wx.WXK_HOME: 0x24,
        main.wx.WXK_END: 0x23,
        main.wx.WXK_RETURN: 0x0D,
        main.wx.WXK_NUMPAD_ENTER: 0x0D,
        main.wx.WXK_F1: 0x70,
    }
    scan = scan_codes.get(key_code, 0)
    virtual_key = virtual_keys.get(key_code, int(key_code))
    down_lparam = 1 | (scan << 16)
    up_lparam = 1 | (scan << 16) | (1 << 30) | (1 << 31)
    if key_code in (
        main.wx.WXK_LEFT, main.wx.WXK_RIGHT, main.wx.WXK_UP, main.wx.WXK_DOWN,
        main.wx.WXK_HOME, main.wx.WXK_END, main.wx.WXK_PAGEUP, main.wx.WXK_PAGEDOWN,
        main.wx.WXK_DELETE,
    ):
        down_lparam |= 1 << 24
        up_lparam |= 1 << 24
    hwnd = int(window.GetHandle())
    original_keys = (ctypes.c_ubyte * 256)()
    if not user32.GetKeyboardState(original_keys):
        raise ctypes.WinError(ctypes.get_last_error())
    keys = (ctypes.c_ubyte * 256)(*original_keys)
    # SendMessage reads the sending UI thread's keyboard state. Do not inherit
    # a physical modifier held by the user, or change global keyboard state.
    for modifier in (0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5):
        keys[modifier] = 0
    if shift:
        keys[0x10] = keys[0xA0] = 0x80
    if ctrl:
        keys[0x11] = keys[0xA2] = 0x80
    if alt:
        keys[0x12] = keys[0xA4] = 0x80
    try:
        if not user32.SetKeyboardState(keys):
            raise ctypes.WinError(ctypes.get_last_error())
        down_message = 0x0104 if alt else wm_keydown
        up_message = 0x0105 if alt else wm_keyup
        if alt:
            down_lparam |= 1 << 29
            up_lparam |= 1 << 29
        user32.SendMessageW(hwnd, down_message, virtual_key, down_lparam)
        if key_code in (main.wx.WXK_RETURN, main.wx.WXK_NUMPAD_ENTER) and (shift or ctrl):
            user32.SendMessageW(hwnd, 0x0102, 13, 1)
        user32.SendMessageW(hwnd, up_message, virtual_key, up_lparam)
    finally:
        if not user32.SetKeyboardState(original_keys):
            raise ctypes.WinError(ctypes.get_last_error())


def _send_text_char(window, character):
    ctypes.WinDLL("user32", use_last_error=True).SendMessageW(
        int(window.GetHandle()), 0x0102, ord(character), 1
    )


def _activate_frame(frame, wx_app):
    frame.Show()
    frame.Raise()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetForegroundWindow(int(frame.GetHandle()))
    wx_app.Yield()


def _destroy_dialog_after_native_callback(dlg, wx_app):
    """Release a dialog only after any synchronous Win32 input callback unwinds."""
    if dlg is None:
        return
    dlg.Hide()
    main.wx.CallAfter(dlg.Destroy)


def test_real_ui_story_1_3_question_enter_and_native_modifiers(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    calls = []
    monkeypatch.setattr(frame, "_submit_question", lambda value, **kwargs: calls.append(value) or (True, ""))
    monkeypatch.setattr(frame, "_has_input_ime_candidates", lambda: False)
    frame.input_edit.SetValue("question")
    frame.input_edit.SetInsertionPointEnd()
    frame.input_edit.SetFocus()
    wx_app.Yield()

    _send_window_key(frame.input_edit, main.wx.WXK_RETURN)
    wx_app.Yield()
    assert calls == ["question"]
    assert frame.input_edit.HasFocus()

    for modifiers in ({"shift": True}, {"ctrl": True}):
        probe = type("ModifierProbe", (), {
            "GetKeyCode": lambda self: main.wx.WXK_RETURN,
            "ShiftDown": lambda self: bool(modifiers.get("shift")),
            "ControlDown": lambda self: bool(modifiers.get("ctrl")),
            "AltDown": lambda self: False,
            "Skip": lambda self: setattr(self, "skipped", getattr(self, "skipped", 0) + 1),
        })()
        frame._on_input_key_down(probe)
        assert probe.skipped == 1  # WM_CHAR below is valid only because the app delegated keydown.
        before = frame.input_edit.GetValue()
        frame.input_edit.SetInsertionPointEnd()
        _send_window_key(frame.input_edit, main.wx.WXK_RETURN, **modifiers)
        wx_app.Yield()
        assert frame.input_edit.GetValue() == before + "\n"
        assert calls == ["question"]
        assert frame.input_edit.HasFocus()

    before = frame.input_edit.GetValue()
    _send_window_key(frame.input_edit, main.wx.WXK_RETURN, alt=True)
    wx_app.Yield()
    assert frame.input_edit.GetValue() == before
    assert calls == ["question"]


def _send_foreground_key(key_code, wx_app):
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    keyeventf_keyup = 0x0002
    virtual_keys = {
        main.wx.WXK_RETURN: 0x0D,
        main.wx.WXK_NUMPAD_ENTER: 0x0D,
        main.wx.WXK_F1: 0x70,
    }
    vk = virtual_keys.get(key_code, int(key_code))
    user32.keybd_event(vk, 0, 0, 0)
    wx_app.Yield()
    user32.keybd_event(vk, 0, keyeventf_keyup, 0)
    wx_app.Yield()


def _track_ui_timers_for_test(monkeypatch):
    timers = []
    original = main.wx_call_later_if_alive
    def call_later(*args, **kwargs):
        timer = original(*args, **kwargs)
        if timer is not None:
            timers.append(timer)
        return timer
    monkeypatch.setattr(main, "wx_call_later_if_alive", call_later)
    def cleanup(frame):
        frame._invalidate_execution_scan()
        for timer in timers:
            stop = getattr(timer, "Stop", None)
            if callable(stop):
                stop()
    return cleanup


@pytest.fixture
def _tracked_ui_timers(monkeypatch):
    # Install before frame construction, which can itself schedule saves.
    return _track_ui_timers_for_test(monkeypatch)


@pytest.fixture(autouse=True)
def _cleanup_test_ui_timers(_tracked_ui_timers, frame):
    # Depending on frame makes this teardown run before the frame is destroyed.
    yield
    _tracked_ui_timers(frame)


def test_real_ui_execution_native_tab_and_shift_tab_remain_single_hops_during_bursts(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    frame.active_chat_id = frame.current_chat_id = "chat-execution-native"
    frame.active_turn_idx = 0
    frame.active_session_turns = [{"question": "q", "answer_md": main.REQUESTING_TEXT,
                                  "model": main.DEFAULT_CODEX_MODEL, "codex_turn_id": "turn-native"}]
    frame._current_chat_state = {"id": "chat-execution-native", "turns": frame.active_session_turns,
        "detail_panel_mode": "answers", "execution_steps": [{"step": f"step {i}", "turn_idx": 0} for i in range(30)]}
    monkeypatch.setattr(frame, "_save_state", lambda *a, **kw: None)
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda *a, **kw: None)
    monkeypatch.setattr(frame, "_broadcast_remote_event", lambda *a, **kw: None)
    frame.input_edit.SetFocusFromKbd()
    wx_app.Yield()
    _dispatch_frame_key(frame, main.wx.WXK_F1)
    assert _yield_until(wx_app, lambda: frame.execution_list.HasFocus(), timeout=2.0)
    syncs = []
    original_sync = frame.execution_list_model.replace_visible_page
    def sync(*args, **kwargs):
        syncs.append(1)
        return original_sync(*args, **kwargs)
    monkeypatch.setattr(frame.execution_list_model, "replace_visible_page", sync)
    monkeypatch.setattr(frame.execution_list, "Clear", lambda: (_ for _ in ()).throw(AssertionError("normal burst cleared list")))
    def shift_tab():
        _send_window_key(main.wx.Window.FindFocus(), main.wx.WXK_TAB, shift=True)
        wx_app.Yield()
    durations = []
    for iteration in range(10):
        frame.execution_list.SetFocusFromKbd()
        frame.execution_list.SetSelection(15)
        wx_app.Yield()
        selected = frame.execution_list_model.selected_id()
        frame._navigation_quiet_until = 0.0
        start = time.perf_counter()
        before_syncs = len(syncs)
        frame._pending_codex_ui_events = [("chat-execution-native", main.CodexEvent(
            type="plan_updated", turn_id="turn-native", text=f"burst {iteration}-{i}", data={"turn_idx": 0},
        )) for i in range(4)]
        main.wx.CallAfter(frame._drain_codex_ui_events)
        # Put the native key behind the pending drain, measuring time from
        # enqueue rather than only timing an already-completed handler.
        main.wx.CallAfter(_send_window_key, frame.execution_list, main.wx.WXK_TAB)
        assert _yield_until(wx_app, lambda: frame.input_edit.HasFocus(), timeout=0.5), (iteration, main.wx.Window.FindFocus().GetName())
        assert len(syncs) - before_syncs == 1
        assert frame.execution_list_model.selected_id() == selected
        assert frame.input_edit.HasFocus(), (main.wx.Window.FindFocus().GetName(), iteration)
        shift_tab()
        assert frame.execution_list.HasFocus()
        shift_tab()
        assert frame.history_list.HasFocus()
        elapsed = time.perf_counter() - start
        durations.append(elapsed)
        assert elapsed < 0.5
    print(f"execution native navigation: samples=10, events=40, syncs={len(syncs)}, "
          f"median_ms={sorted(durations)[5] * 1000:.2f}, max_ms={max(durations) * 1000:.2f}")


def _dispatch_frame_key(frame, key_code):
    event = main.wx.KeyEvent(main.wx.wxEVT_CHAR_HOOK)
    event.SetKeyCode(key_code)
    frame.ProcessEvent(event)


def test_real_ui_execution_hidden_history_scan_releases_navigation_before_read_finishes(frame, wx_app, monkeypatch, tmp_path):
    event_loop = main.wx.GUIEventLoop()
    activator = main.wx.EventLoopActivator(event_loop)
    monkeypatch.setattr(main, "_wx_app_allows_ui_timers", lambda: True)
    _activate_frame(frame, wx_app)
    frame.current_chat_id = frame.active_chat_id = "owner-a"
    frame._current_chat_state = {"id": "owner-a", "detail_panel_mode": "execution", "turns": [],
                                 "execution_steps": [{"step": "private owner A text"}]}
    frame._apply_detail_panel_mode("execution", refresh_execution=True)
    frame.execution_list.SetSelection(0)
    store = main.ChatStore(tmp_path / "hidden.db", max_execution_steps_per_turn=1000)
    store.initialize()
    store.upsert_chat({"id": "hidden", "detail_panel_mode": "execution"})
    store.replace_turns("hidden", [{"question": "q", "answer_md": "a"}])
    for i in range(450):
        store.append_execution_step("hidden", {"turn_idx": 0, "created_at": i + 1,
            "display_kind": "command" if i >= 120 else "commentary", "list_text": f"step {i}"})
    frame.chat_store, frame._chat_store_enabled = store, True
    frame.archived_chats = [{"id": "hidden"}]
    frame.view_mode, frame.view_history_id = "history", "hidden"
    blocked, release, finished = threading.Event(), threading.Event(), threading.Event()
    reads = []
    original = store.load_recent_execution_steps
    def read(*args, **kwargs):
        if args[0] != "hidden":
            return original(*args, **kwargs)
        reads.append((threading.current_thread() is threading.main_thread(), kwargs))
        if len(reads) == 3:
            blocked.set()
            assert release.wait(5), "test did not release background query"
        result = original(*args, **kwargs)
        if len(reads) >= 5:
            finished.set()
        return result
    monkeypatch.setattr(store, "load_recent_execution_steps", read)
    monkeypatch.setattr(store, "load_execution_steps", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("full loader")))
    try:
        frame._apply_detail_panel_mode("execution", refresh_execution=True)
        assert _yield_until(wx_app, blocked.is_set)
        assert [on_ui for on_ui, _ in reads] == [True, True, False]
        assert list(frame.execution_list.GetStrings()) == ["正在加载执行过程"]
        assert frame._selected_execution_text_viewer_content() is None
        assert frame._try_open_selected_execution_detail() is False
        copied = []
        monkeypatch.setattr(frame, "_set_clipboard_text", lambda text: copied.append(text) or True)
        class CopyEvent:
            def GetKeyCode(self): return ord("C")
            def ControlDown(self): return True
            def AltDown(self): return False
            def ShiftDown(self): return False
            def Skip(self): pass
            def StopPropagation(self): pass
        frame._on_execution_key_down(CopyEvent())
        assert all("private owner A text" not in text for text in copied)
        frame.execution_list.SetFocusFromKbd()
        wx_app.Yield()
        started = time.perf_counter()
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        main.wx.CallAfter(_send_window_key, frame.execution_list, main.wx.WXK_TAB)
        assert _yield_until(wx_app, lambda: frame.input_edit.HasFocus(), timeout=0.5)
        elapsed = time.perf_counter() - started
        assert not release.is_set() and not finished.is_set()
        assert elapsed < 0.5
        release.set()
        assert _yield_until(wx_app, lambda: getattr(frame, "_execution_scan_result", None) is not None, timeout=1)
        assert frame._navigation_quiet_active()
        assert frame._execution_list_dirty
        assert list(frame.execution_list.GetStrings()) == ["正在加载执行过程"]
        # Expire only the quiet clock: the existing timer must publish the
        # completed scan without a manual render or a longer recovery budget.
        frame._navigation_quiet_until = time.monotonic() - 1
        assert _yield_until(wx_app, lambda: not frame._execution_list_dirty, timeout=3), (
            frame._execution_scan_pending, frame._execution_scan_key(),
            getattr(frame, "_execution_scan_result", None) is not None,
            frame._idle_ui_refresh_scheduled, frame._detail_panel_mode(), len(reads))
        assert frame.input_edit.HasFocus()
        assert list(frame.execution_list.GetStrings()) == (
            ["更多", "1970年1月1日 08:00"]
            + [f"step {i}" for i in range(21, 120)]
            + ["时间未知", "小诸葛：a"]
        )
        assert len(reads) == 5
        assert all(kwargs["limit"] == 100 for _, kwargs in reads)
        assert all(kwargs.get("include_total") is False for _, kwargs in reads[1:])
        print(f"execution blocked history: ui_queries=2, total_queries={len(reads)}, native_tab_ms={elapsed * 1000:.2f}")
    finally:
        release.set()
        del activator


def _send_listbox_ctrl_c(window, wx_app):
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    keyeventf_keyup = 0x0002
    vk_control = 0x11
    user32.keybd_event(vk_control, 0x1D, 0, 0)
    wx_app.Yield()
    try:
        event = main.wx.KeyEvent(main.wx.wxEVT_KEY_DOWN)
        event.SetKeyCode(ord("C"))
        set_control_down = getattr(event, "SetControlDown", None)
        if callable(set_control_down):
            set_control_down(True)
        window.ProcessEvent(event)
        wx_app.Yield()
    finally:
        user32.keybd_event(vk_control, 0x1D, keyeventf_keyup, 0)
        wx_app.Yield()


def _yield_until(wx_app, predicate, timeout=2.0):
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        wx_app.Yield()
        if predicate():
            return True
        time.sleep(0.01)
    wx_app.Yield()
    return predicate()


def test_real_ui_answer_list_navigation_stays_responsive_during_codex_event_burst(frame, wx_app, monkeypatch):
    frame.Show()
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.active_turn_idx = 19
    frame.active_session_turns = [
        {
            "question": f"question {idx}",
            "answer_md": f"answer {idx}",
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": float(idx),
        }
        for idx in range(20)
    ]
    frame._current_chat_state = {"id": "chat-active", "turns": frame.active_session_turns}
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    frame._render_answer_list()
    frame.answer_list.SetSelection(2)
    frame.answer_list.SetFocusFromKbd()
    wx_app.Yield()

    for idx in range(main.CODEX_UI_EVENT_BATCH_SIZE * 6):
        frame._dispatch_codex_event_to_ui(
            "chat-active",
            main.CodexEvent(type="plan_updated", text=f"background progress {idx}"),
        )

    started = time.perf_counter()
    _send_listbox_key(frame.answer_list, main.wx.WXK_DOWN)
    wx_app.Yield()
    elapsed = time.perf_counter() - started

    assert elapsed < 0.5
    assert frame.answer_list.GetSelection() == 3
    assert not frame._pending_codex_ui_events or frame._codex_ui_event_drain_timer is not None


def test_real_ui_answer_list_ctrl_c_keeps_selection_and_focus(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.active_session_turns = [
        {
            "question": "question",
            "answer_md": "answer detail",
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": 1.0,
        }
    ]
    frame._current_chat_state = {"id": "chat-active", "turns": frame.active_session_turns}
    copied = []
    monkeypatch.setattr(frame, "_set_clipboard_text", lambda text: copied.append(text) or True)

    frame._render_answer_list()
    answer_row = next(idx for idx, meta in enumerate(frame.answer_meta) if meta[0] == "answer")
    frame.answer_list.SetSelection(answer_row)
    frame.answer_list.SetFocusFromKbd()
    wx_app.Yield()

    _send_listbox_ctrl_c(frame.answer_list, wx_app)

    assert copied == ["answer detail"]
    assert frame.answer_list.GetSelection() == answer_row


def test_real_ui_answer_list_down_at_end_does_not_reset_selection(frame, wx_app, monkeypatch):
    frame.Show()
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.active_session_turns = [
        {
            "question": "question",
            "answer_md": "answer detail",
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": 1.0,
        }
    ]
    frame._current_chat_state = {"id": "chat-active", "turns": frame.active_session_turns}

    frame._render_answer_list()
    last_row = frame.answer_list.GetCount() - 1
    frame.answer_list.SetSelection(last_row)
    frame.answer_list.SetFocusFromKbd()
    wx_app.Yield()

    set_selection_calls = []
    original_set_selection = frame.answer_list.SetSelection
    monkeypatch.setattr(
        frame.answer_list,
        "SetSelection",
        lambda idx: set_selection_calls.append(idx) or original_set_selection(idx),
    )

    _send_listbox_key(frame.answer_list, main.wx.WXK_DOWN)
    wx_app.Yield()

    assert set_selection_calls == []
    assert frame.answer_list.GetSelection() == last_row


def test_real_ui_answer_enter_opens_text_viewer_and_shift_enter_opens_web_detail(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    frame.active_chat_id = "chat-answer-viewer"
    frame.current_chat_id = "chat-answer-viewer"
    frame.active_session_turns = [
        {
            "question": "question",
            "answer_md": "## 标题\n\n第一段\n\n第二段",
            "model": main.DEFAULT_MODEL_ID,
            "created_at": 1.0,
        }
    ]
    frame._current_chat_state = {"id": "chat-answer-viewer", "turns": frame.active_session_turns}
    opened_viewer = []
    opened_web = []
    monkeypatch.setattr(frame, "_open_answer_text_viewer", lambda title, text, *args: opened_viewer.append((title, text)) or True)
    monkeypatch.setattr(frame, "_try_open_selected_answer_detail", lambda: opened_web.append(True) or True)

    frame._render_answer_list()
    answer_row = next(idx for idx, meta in enumerate(frame.answer_meta) if meta[0] == "answer")
    frame.answer_list.SetSelection(answer_row)
    frame.answer_list.SetFocusFromKbd()
    wx_app.Yield()

    _send_window_key(frame.answer_list, main.wx.WXK_RETURN)
    wx_app.Yield()

    assert opened_viewer == [("回答详情", "## 标题\n\n第一段\n\n第二段")]
    assert opened_web == []
    assert frame.answer_list.HasFocus()

    shift_enter = main.wx.KeyEvent(main.wx.wxEVT_KEY_DOWN)
    shift_enter.SetKeyCode(main.wx.WXK_RETURN)
    shift_enter.SetShiftDown(True)
    frame.answer_list.ProcessEvent(shift_enter)
    wx_app.Yield()

    assert opened_web == [True]


def test_real_ui_answer_viewer_keeps_single_lines_and_copy_button_copies_scratch_text(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    frame.active_chat_id = "chat-answer-viewer-tab-copy"
    frame.current_chat_id = "chat-answer-viewer-tab-copy"
    frame.active_session_turns = [
        {
            "question": "first question",
            "answer_md": "first answer",
            "model": main.DEFAULT_MODEL_ID,
            "created_at": 1.0,
        },
        {
            "question": "second question",
            "answer_md": "1. 第一项\n2. 第二项",
            "model": main.DEFAULT_MODEL_ID,
            "created_at": 2.0,
        },
    ]
    frame._current_chat_state = {"id": frame.active_chat_id, "turns": frame.active_session_turns}
    opened_viewer = []
    monkeypatch.setattr(frame, "_open_answer_text_viewer", lambda title, text, *args: opened_viewer.append((title, text)) or True)

    frame._render_answer_list()
    second_answer_row = next(idx for idx, meta in enumerate(frame.answer_meta) if meta[0] == "answer" and meta[1] == 1)
    frame.answer_list.SetSelection(second_answer_row - 1)
    frame.answer_list.SetFocusFromKbd()
    wx_app.Yield()

    _send_listbox_key(frame.answer_list, main.wx.WXK_DOWN)
    wx_app.Yield()
    assert frame.answer_list.GetSelection() == second_answer_row

    _send_window_key(frame.answer_list, main.wx.WXK_RETURN)
    wx_app.Yield()
    assert opened_viewer == [("回答详情", "1. 第一项\n2. 第二项")]

    copied = []
    monkeypatch.setattr(frame, "_set_clipboard_text", lambda text: copied.append(text) or True)
    dlg = main.AnswerTextViewerDialog(frame, "回答详情", opened_viewer[0][1])
    try:
        dlg.text_ctrl.SetValue("\n当前编辑框内容")
        skipped = []

        class _TabEvent:
            def GetKeyCode(self): return main.wx.WXK_TAB
            def ControlDown(self): return False
            def AltDown(self): return False
            def ShiftDown(self): return False
            def Skip(self): skipped.append(True)

        dlg._on_char_hook(_TabEvent())

        assert copied == []
        assert skipped == [True]
        assert dlg.copy_button.GetLabel() == "复制"
        dlg._on_copy_clicked()
        assert copied == ["当前编辑框内容"]
        assert dlg.text_ctrl.IsEditable()
        assert dlg.text_ctrl.GetWindowStyleFlag() & main.wx.TE_DONTWRAP
    finally:
        if dlg:
            _destroy_dialog_after_native_callback(dlg, wx_app)


def test_real_ui_answer_viewer_native_edit_copy_marker_and_tab_navigation(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    copied = []
    monkeypatch.setattr(frame, "_set_clipboard_text", lambda text: copied.append(text) or True)
    dlg = main.AnswerTextViewerDialog(frame, "回答详情", "canonical")
    try:
        dlg.Show()
        dlg.Raise()
        ctypes.WinDLL("user32", use_last_error=True).SetForegroundWindow(int(dlg.GetHandle()))
        dlg.text_ctrl.SetFocus()
        dlg.text_ctrl.SetInsertionPoint(0)
        wx_app.Yield()

        _send_text_char(dlg.text_ctrl, "X")
        wx_app.Yield()
        assert dlg.text_ctrl.GetValue() == "X\ncanonical"
        assert dlg._display_marker_offset == 1

        _send_window_key(dlg.text_ctrl, ord("A"), ctrl=True)
        assert dlg._display_marker_offset == 1
        for key_code in (main.wx.WXK_LEFT, main.wx.WXK_HOME, main.wx.WXK_F2):
            navigation = main.wx.KeyEvent(main.wx.wxEVT_KEY_DOWN)
            navigation.SetKeyCode(key_code)
            dlg.text_ctrl.ProcessEvent(navigation)
            assert dlg._display_marker_offset == 1
        wx_app.Yield()
        assert dlg._display_marker_offset == 1
        dlg._on_copy_clicked()
        assert copied.pop() == "Xcanonical"

        _send_window_key(dlg.text_ctrl, ord("A"), ctrl=True)
        _send_window_key(dlg.text_ctrl, ord("C"), ctrl=True)
        wx_app.Yield()
        assert copied.pop() == "Xcanonical"

        dlg.text_ctrl.SetInsertionPoint(2)
        _send_window_key(dlg.text_ctrl, main.wx.WXK_BACK)
        wx_app.Yield()
        assert dlg._display_marker_offset is None
        dlg.text_ctrl.WriteText("\n")
        wx_app.Yield()
        _send_window_key(dlg.text_ctrl, ord("A"), ctrl=True)
        _send_window_key(dlg.text_ctrl, ord("C"), ctrl=True)
        wx_app.Yield()
        assert copied.pop() == "X\ncanonical"

        dlg.text_ctrl.SetFocus()
        dlg.text_ctrl.Navigate(main.wx.NavigationKeyEvent.IsForward)
        wx_app.Yield()
        assert dlg.copy_button.HasFocus()
        dlg.copy_button.Navigate(main.wx.NavigationKeyEvent.IsBackward)
        wx_app.Yield()
        assert dlg.text_ctrl.HasFocus()

        dlg.text_ctrl.SetSelection(2, dlg.text_ctrl.GetLastPosition())
        dlg.copy_button.SetFocus()
        _send_window_key(dlg.copy_button, main.wx.WXK_SPACE)
        wx_app.Yield()
        assert copied.pop() == "canonical"
    finally:
        _destroy_dialog_after_native_callback(dlg, wx_app)


def test_real_ui_answer_viewer_noop_background_drain_preserves_modal_scratch_owner_and_focus(
    frame, wx_app, monkeypatch
):
    _activate_frame(frame, wx_app)
    payload = main.AnswerViewerPayload(
        "chat-owner", 0, "turn-owner", "thread-owner", main.DEFAULT_CODEX_MODEL, "canonical"
    )
    dlg = main.AnswerTextViewerDialog(frame, "回答详情", payload=payload, on_continue=lambda _payload: True)

    class EmptyClient:
        def drain_pending_messages(self):
            return []

    repaint_calls = []
    monkeypatch.setattr(frame, "_render_answer_list", lambda *args, **kwargs: repaint_calls.append((args, kwargs)))
    try:
        dlg.Show()
        dlg.Raise()
        dlg.text_ctrl.SetValue("\nscratch")
        dlg.text_ctrl.SetSelection(2, 5)
        dlg.text_ctrl.SetFocus()
        wx_app.Yield()
        before = (dlg.text_ctrl.GetValue(), dlg.text_ctrl.GetSelection(), dlg.payload, main.wx.Window.FindFocus())

        frame._drain_codex_worker_client_messages("chat-owner", EmptyClient())
        wx_app.Yield()

        assert (dlg.text_ctrl.GetValue(), dlg.text_ctrl.GetSelection(), dlg.payload, main.wx.Window.FindFocus()) == before
        assert repaint_calls == []
    finally:
        _destroy_dialog_after_native_callback(dlg, wx_app)


def test_real_ui_answer_viewer_same_newline_replacement_is_not_treated_as_synthetic(
    frame, wx_app, monkeypatch
):
    _activate_frame(frame, wx_app)
    copied = []
    monkeypatch.setattr(frame, "_set_clipboard_text", lambda text: copied.append(text) or True)
    dlg = main.AnswerTextViewerDialog(frame, "回答详情", "canonical")
    try:
        dlg.Show()
        dlg.Raise()
        dlg.text_ctrl.SetFocus()
        dlg.text_ctrl.SetSelection(0, 1)
        dlg.text_ctrl.Replace(0, 1, "\n")
        wx_app.Yield()

        dlg.text_ctrl.SetSelection(0, 0)
        dlg._on_copy_clicked()
        assert dlg._display_marker_offset is None
        assert copied == ["\ncanonical"]
    finally:
        _destroy_dialog_after_native_callback(dlg, wx_app)


def test_real_ui_answer_viewer_continue_button_is_once_latched(frame, wx_app):
    _activate_frame(frame, wx_app)
    payload = main.AnswerViewerPayload(
        "chat-owner", 0, "turn-owner", "thread-owner", main.DEFAULT_CODEX_MODEL, "\r\ncanonical"
    )
    dispatched = []
    dlg = main.AnswerTextViewerDialog(
        frame, "回答详情", payload=payload, on_continue=lambda owner: dispatched.append(owner) or True
    )
    original_is_shown = dlg.IsShown
    try:
        dlg.IsShown = lambda: True
        dlg._finish = lambda _code: None
        dlg.text_ctrl.SetValue("\nscratch must not be continued")
        dlg._on_continue_clicked()
        dlg._on_continue_clicked()

        assert dispatched == [payload]
        assert dispatched[0].answer_md == "\r\ncanonical"
        assert dlg.text_ctrl.GetValue() == "\nscratch must not be continued"
        assert not dlg.continue_button.IsEnabled()
    finally:
        dlg.IsShown = original_is_shown
        _destroy_dialog_after_native_callback(dlg, wx_app)


def test_real_ui_answer_viewer_alt_c_respects_ime_modal_and_disabled_boundaries(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    payload = main.AnswerViewerPayload(
        "chat-owner", 0, "turn-owner", "thread-owner", main.DEFAULT_CODEX_MODEL, "canonical"
    )
    dispatched = []
    dlg = main.AnswerTextViewerDialog(
        frame, "回答详情", payload=payload, on_continue=lambda owner: dispatched.append(owner) or True
    )

    class _AltC:
        def IsAutoRepeat(self):
            return False

    try:
        monkeypatch.setattr(dlg, "IsShown", lambda: True)
        monkeypatch.setattr(dlg.text_ctrl, "HasFocus", lambda: True)
        monkeypatch.setattr(main.wx.Window, "FindFocus", lambda: dlg.text_ctrl)
        monkeypatch.setattr(main.wx, "GetActiveWindow", lambda: dlg)
        monkeypatch.setattr(frame, "_has_native_ime_composition", lambda _window: True)
        assert dlg._request_continue(from_shortcut=True, event=_AltC()) is False

        monkeypatch.setattr(frame, "_has_native_ime_composition", lambda _window: False)
        other = main.wx.Dialog(frame, title="other modal")
        try:
            monkeypatch.setattr(main.wx, "GetActiveWindow", lambda: other)
            assert dlg._request_continue(from_shortcut=True, event=_AltC()) is False
        finally:
            _destroy_dialog_after_native_callback(other, wx_app)

        dlg.continue_button.Disable()
        assert dlg._request_continue() is False
        assert dispatched == []
        assert dlg.text_ctrl.HasFocus()
    finally:
        _destroy_dialog_after_native_callback(dlg, wx_app)


def test_real_ui_answer_viewer_alt_c_key_path_rejects_repeat_then_dispatches(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    payload = main.AnswerViewerPayload(
        "chat-owner", 0, "turn-owner", "thread-owner", main.DEFAULT_CODEX_MODEL, "canonical"
    )
    dispatched = []
    dlg = main.AnswerTextViewerDialog(
        frame, "回答详情", payload=payload, on_continue=lambda owner: dispatched.append(owner) or True
    )

    class AltC:
        def __init__(self, repeat): self.repeat, self.skipped = repeat, 0
        def GetKeyCode(self): return ord("C")
        def AltDown(self): return True
        def ControlDown(self): return False
        def IsAutoRepeat(self): return self.repeat
        def Skip(self): self.skipped += 1

    try:
        monkeypatch.setattr(dlg, "IsShown", lambda: True)
        monkeypatch.setattr(main.wx.Window, "FindFocus", lambda: dlg.text_ctrl)
        monkeypatch.setattr(main.wx, "GetActiveWindow", lambda: dlg)
        monkeypatch.setattr(frame, "_has_native_ime_composition", lambda _window: False)
        repeated = AltC(True)
        dlg._on_char_hook(repeated)
        assert dispatched == []
        assert repeated.skipped == 1

        dlg._finish = lambda _code: None
        first = AltC(False)
        dlg._on_char_hook(first)
        assert dispatched == [payload]
        assert first.skipped == 0
    finally:
        _destroy_dialog_after_native_callback(dlg, wx_app)


def test_real_ui_codex_speed_combo_arrow_key_is_responsive_and_keeps_focus(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    frame.active_chat_id = "chat-speed"
    frame.current_chat_id = "chat-speed"
    frame.selected_model = main.DEFAULT_CODEX_MODEL
    frame.model_combo.SetValue(main.model_display_name(main.DEFAULT_CODEX_MODEL))
    frame._current_chat_state = {
        "id": "chat-speed",
        "model": main.DEFAULT_CODEX_MODEL,
        "codex_service_tier": "",
        "turns": [],
    }
    frame.active_session_turns = frame._current_chat_state["turns"]
    frame._sync_codex_speed_combo_from_chat(frame._current_chat_state)
    frame.codex_speed_combo.SetSelection(0)
    frame.codex_speed_combo.SetFocusFromKbd()
    wx_app.Yield()
    saves = []
    deferred = []
    refreshes = []
    renders = []
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: saves.append(True))
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: deferred.append(True))
    monkeypatch.setattr(frame, "_refresh_history", lambda *args, **kwargs: refreshes.append(True))
    monkeypatch.setattr(frame, "_render_answer_list", lambda *args, **kwargs: renders.append(True))

    started = time.perf_counter()
    _send_window_key(frame.codex_speed_combo, main.wx.WXK_DOWN)
    wx_app.Yield()
    elapsed = time.perf_counter() - started

    assert elapsed < 0.5
    assert frame.codex_speed_combo.HasFocus()
    assert frame.codex_speed_combo.GetValue() == "快速"
    assert frame._current_chat_state["codex_service_tier"] == "fast"
    assert saves == []
    assert deferred == [True]
    assert refreshes == []
    assert renders == []


def test_real_ui_history_model_selection_is_candidate_only_and_keeps_focus(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    active = {"id": "chat-active", "model": "codex/main", "turns": []}
    history = {"id": "chat-history", "model": "codex/main", "turns": []}
    frame._current_chat_state = active
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.archived_chats = [history]
    frame.view_mode = "history"
    frame.view_history_id = "chat-history"
    frame.selected_model = "codex/main"
    deferred = []
    pushed = []
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: deferred.append(True))
    monkeypatch.setattr(frame, "_push_remote_state", lambda chat_id: pushed.append(chat_id))

    frame.model_combo.SetValue(main.model_display_name("openai/gpt-5.2"))
    frame.model_combo.SetFocusFromKbd()
    event = main.wx.CommandEvent(main.wx.wxEVT_COMBOBOX, frame.model_combo.GetId())
    frame.model_combo.ProcessEvent(event)
    wx_app.Yield()

    assert frame.model_combo.HasFocus()
    assert active["model"] == "codex/main"
    assert history["model"] == "codex/main"
    assert frame.selected_model == "openai/gpt-5.2"
    assert deferred == []
    assert pushed == []


def test_real_ui_completion_focuses_latest_answer_without_refreshing_old_selection(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    frame.active_chat_id = "chat-answer-focus"
    frame.current_chat_id = "chat-answer-focus"
    frame.active_turn_idx = 1
    frame.active_session_turns = [
        {
            "question": "previous question",
            "answer_md": "previous answer",
            "model": "openai/gpt-5.2",
            "created_at": 0.5,
        },
        {
            "question": "question",
            "answer_md": main.REQUESTING_TEXT,
            "model": "openai/gpt-5.2",
            "created_at": 1.0,
        }
    ]
    frame._current_chat_state = {"id": "chat-answer-focus", "turns": frame.active_session_turns}
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_refresh_history", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_final_answer", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *args, **kwargs: None)
    scheduled = []
    monkeypatch.setattr(frame, "_call_later_if_alive", lambda *args, **kwargs: scheduled.append(args))
    frame._render_answer_list()
    old_answer_row = next(idx for idx, meta in enumerate(frame.answer_meta) if meta[0] == "answer" and meta[1] == 0)
    frame.answer_list.SetSelection(old_answer_row)
    operations = []
    original_append = frame.answer_list.Append
    original_set_selection = frame.answer_list.SetSelection
    original_set_focus = frame.answer_list.SetFocus

    def record_append(label):
        operations.append(("Append", label))
        return original_append(label)

    def record_set_selection(index):
        operations.append(("SetSelection", index))
        return original_set_selection(index)

    def record_set_focus():
        operations.append(("SetFocus",))
        return original_set_focus()

    monkeypatch.setattr(frame.answer_list, "Append", record_append)
    monkeypatch.setattr(frame.answer_list, "SetSelection", record_set_selection)
    monkeypatch.setattr(frame.answer_list, "Refresh", lambda *args, **kwargs: operations.append(("Refresh",)))
    monkeypatch.setattr(frame.answer_list, "SetFocus", record_set_focus)
    monkeypatch.setattr(frame, "_play_finish_sound", lambda *args, **kwargs: operations.append(("Sound",)))

    frame.input_edit.SetFocus()
    wx_app.Yield()
    frame._on_done(1, "final answer", "", "openai/gpt-5.2", "", "chat-answer-focus")

    wx_app.Yield()
    rows = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert "final answer" in rows
    assert operations[0] == ("Sound",)
    assert ("Append", "final answer") in operations
    assert rows.count("final answer") == 1
    assert ("SetSelection", frame.answer_list.GetCount() - 1) in operations
    assert ("SetFocus",) in operations
    assert ("Refresh",) not in operations
    assert scheduled == []
    assert frame.answer_list.HasFocus()
    assert frame.answer_list.GetStringSelection() == "final answer"


def test_real_ui_f1_focuses_execution_latest_enter_opens_text_and_shift_enter_opens_detail(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    frame.active_chat_id = "chat-execution-focus"
    frame.current_chat_id = "chat-execution-focus"
    frame._current_chat_state = {
        "id": "chat-execution-focus",
        "turns": [],
        "detail_panel_mode": "answers",
        "execution_steps": [
            {"event_type": "plan_updated", "display_kind": "plan", "list_text": "first step", "detail_text": "first detail"},
            {"event_type": "plan_updated", "display_kind": "plan", "list_text": "second step", "detail_text": "second detail"},
        ],
    }
    opened_viewer = []
    opened_web = []
    sends = []
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_open_answer_text_viewer", lambda title, text: opened_viewer.append((title, text)) or True)
    monkeypatch.setattr(frame, "_try_open_selected_execution_detail", lambda: opened_web.append(True) or True)
    monkeypatch.setattr(frame, "_trigger_send", lambda: sends.append(True))

    frame.input_edit.SetFocusFromKbd()
    wx_app.Yield()
    _dispatch_frame_key(frame, main.wx.WXK_F1)
    wx_app.Yield()

    assert _yield_until(
        wx_app,
        lambda: frame.execution_list.HasFocus()
        and frame.execution_list.GetSelection() == frame.execution_list.GetCount() - 1
        and frame.execution_list.GetStringSelection() == "second step",
        timeout=2.0,
    )

    _send_listbox_key(frame.execution_list, main.wx.WXK_UP)
    _send_listbox_key(frame.execution_list, main.wx.WXK_UP)
    wx_app.Yield()
    assert frame.execution_list.GetStringSelection() == "first step"

    _send_window_key(frame.execution_list, main.wx.WXK_RETURN)
    wx_app.Yield()

    assert opened_viewer == [("回答详情", "first detail")]
    assert opened_web == []
    assert sends == []
    assert frame.execution_list.HasFocus()

    shift_enter = main.wx.KeyEvent(main.wx.wxEVT_KEY_DOWN)
    shift_enter.SetKeyCode(main.wx.WXK_RETURN)
    shift_enter.SetShiftDown(True)
    frame.execution_list.ProcessEvent(shift_enter)
    wx_app.Yield()

    assert opened_web == [True]
    assert opened_viewer == [("回答详情", "first detail")]
    assert sends == []


def test_real_ui_f1_focuses_empty_execution_placeholder(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    frame.active_chat_id = "chat-empty-execution"
    frame.current_chat_id = "chat-empty-execution"
    frame._current_chat_state = {
        "id": "chat-empty-execution",
        "turns": [],
        "detail_panel_mode": "answers",
        "execution_steps": [],
    }
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)

    frame.input_edit.SetFocusFromKbd()
    wx_app.Yield()
    _dispatch_frame_key(frame, main.wx.WXK_F1)
    wx_app.Yield()

    assert _yield_until(
        wx_app,
        lambda: frame.execution_list.HasFocus()
        and frame.execution_list.GetSelection() == 0
        and frame.execution_list.GetString(0) == "暂无执行过程",
        timeout=2.0,
    )


def test_real_ui_execution_mode_survives_completion_and_f1_toggles_back(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    frame.active_chat_id = "chat-execution-f1-regression"
    frame.current_chat_id = "chat-execution-f1-regression"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {
            "question": "question",
            "answer_md": main.REQUESTING_TEXT,
            "model": "openai/gpt-5.2",
            "created_at": 1.0,
        }
    ]
    frame._current_chat_state = {
        "id": "chat-execution-f1-regression",
        "turns": frame.active_session_turns,
        "detail_panel_mode": "answers",
        "execution_steps": [
            {"event_type": "plan_updated", "display_kind": "plan", "list_text": "execution step", "detail_text": "detail"}
        ],
    }
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_refresh_history", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_final_answer", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_play_finish_sound", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_can_focus_completion_result", lambda: True)
    monkeypatch.setattr(frame, "_call_later_if_alive", lambda _delay, fn, *args, **kwargs: fn(*args, **kwargs))

    frame.input_edit.SetFocusFromKbd()
    wx_app.Yield()
    _dispatch_frame_key(frame, main.wx.WXK_F1)
    wx_app.Yield()
    assert _yield_until(wx_app, lambda: frame.execution_list.HasFocus(), timeout=2.0)

    frame._on_done(0, "final answer", "", "openai/gpt-5.2", "", "chat-execution-f1-regression")
    wx_app.Yield()
    assert frame._current_chat_state["detail_panel_mode"] == "execution"
    assert frame.execution_list.IsShown()
    assert frame.execution_list.HasFocus()

    _send_window_key(frame.execution_list, main.wx.WXK_F1)
    wx_app.Yield()
    assert _yield_until(
        wx_app,
        lambda: frame._current_chat_state["detail_panel_mode"] == "answers"
        and frame.answer_list.IsShown()
        and frame.answer_list.HasFocus(),
        timeout=2.0,
    )


def test_real_ui_execution_updates_do_not_steal_input_focus_during_event_burst(frame, wx_app, monkeypatch):
    frame.Show()
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.active_codex_thread_id = "thread-active"
    frame.active_codex_turn_id = "turn-active"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {
            "question": "q",
            "answer_md": "",
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": 1.0,
            "codex_thread_id": "thread-active",
            "codex_turn_id": "turn-active",
        }
    ]
    frame._current_chat_state = {
        "id": "chat-active",
        "turns": frame.active_session_turns,
        "detail_panel_mode": "execution",
        "execution_steps": [],
    }
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    frame._apply_detail_panel_mode("execution", refresh_execution=True)
    frame.input_edit.SetFocus()
    wx_app.Yield()

    for idx in range(main.CODEX_UI_EVENT_BATCH_SIZE * 4):
        frame._dispatch_codex_event_to_ui(
            "chat-active",
            main.CodexEvent(
                type="plan_updated",
                thread_id="thread-active",
                turn_id="turn-active",
                text=f"background plan {idx}",
            ),
        )

    wx_app.Yield()
    assert not frame._pending_codex_ui_events or frame._codex_ui_event_drain_timer is not None
    assert frame.input_edit.HasFocus()

    frame.input_edit.WriteText("x")
    assert frame.input_edit.GetValue().endswith("x")


def test_real_ui_archived_mobile_result_keeps_foreground_and_browses_completed_owner(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    frame._chat_store_enabled = True
    frame.active_chat_id = frame.current_chat_id = "chat-a"
    frame.active_session_turns = [{"question": "A", "answer_md": "A answer"}]
    frame._current_chat_state = {"id": "chat-a", "title": "A", "turns": frame.active_session_turns}
    for owner in ("chat-b", "chat-c"):
        frame.chat_store.upsert_chat({"id": owner, "title": owner, "model": main.DEFAULT_CODEX_MODEL})
        frame.chat_store.replace_turns(owner, [{
            "question": f"phone {owner}", "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL, "request_status": "pending",
            "codex_context_generation": 0,
        }])
    frame.archived_chats = [frame.chat_store.load_chat(owner, include_execution_steps=False)
                            for owner in ("chat-b", "chat-c")]
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)
    monkeypatch.setattr(frame, "_request_codex_chat_information", lambda *_args: None)
    frame._refresh_history("chat-a")
    frame._render_answer_list(refresh_execution=False)
    frame.answer_list.SetSelection(0)
    frame.input_edit.SetValue("unsent A draft")
    frame.input_edit.SetSelection(2, 7)
    frame.input_edit.SetFocus()
    wx_app.Yield()
    selected_history = frame.history_ids[frame.history_list.GetSelection()]
    draft_selection = frame.input_edit.GetSelection()

    for owner in ("chat-b", "chat-c"):
        frame._apply_codex_worker_thread_state(owner, {
            "chat_id": owner, "turn_idx": 0, "thread_id": f"thread-{owner}",
            "turn_id": f"turn-{owner}", "context_generation": 0, "active": True,
        })
    for owner in ("chat-c", "chat-b"):
        def event(kind, **kwargs):
            return main.CodexEvent(type=kind, thread_id=f"thread-{owner}",
                                   turn_id=f"turn-{owner}", data={"turn_idx": 0}, **kwargs)
        frame._on_codex_event_for_chat(owner, event("plan_updated", text=f"step {owner}"))
        frame._on_codex_event_for_chat(owner, event("item_completed", phase="final_answer",
                                                     text=f"answer {owner}"))
        frame._on_codex_event_for_chat(owner, event("turn_completed", status="completed"))
    wx_app.Yield()
    assert frame.active_chat_id == "chat-a"
    assert frame.input_edit.HasFocus()
    assert frame.input_edit.GetValue() == "unsent A draft"
    assert frame.input_edit.GetSelection() == draft_selection
    assert frame.history_ids[frame.history_list.GetSelection()] == selected_history
    assert frame.answer_list.GetSelection() == 0
    frame._last_primary_interaction_at = 0.0
    frame._flush_idle_ui_refreshes()
    assert frame.history_ids[0] == "chat-b"
    assert frame.history_list_model.selected_id() == selected_history
    monkeypatch.setattr(frame, "_refresh_history", lambda *_args: pytest.fail("unchanged idle polling must not redraw history"))
    frame._flush_idle_ui_refreshes()

    assert frame._show_history_chat("chat-b", focus_answer_list=False)
    wx_app.Yield()
    assert frame.active_chat_id == "chat-a"
    assert frame.input_edit.GetValue() == "unsent A draft"
    assert frame._find_archived_chat("chat-b")["turns"][0]["answer_md"] == "answer chat-b"
    frame.history_list.SetFocusFromKbd()
    wx_app.Yield()
    _send_listbox_key(frame.history_list, main.wx.WXK_HOME)
    _send_listbox_key(frame.history_list, main.wx.WXK_END)
    wx_app.Yield()
    assert frame.history_list.HasFocus()
    assert frame.history_ids[frame.history_list.GetSelection()] == frame.history_ids[-1]


def test_real_ui_worker_events_do_not_mutate_lists_during_navigation_quiet(frame, wx_app, monkeypatch):
    frame.Show()
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.view_mode = "active"
    frame.active_codex_thread_id = "thread-current"
    frame.active_codex_turn_id = "turn-1"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {
            "question": "q",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": 1.0,
            "request_status": "pending",
            "codex_thread_id": "thread-current",
            "codex_turn_id": "turn-1",
        }
    ]
    frame._current_chat_state = {
        "id": "chat-current",
        "turns": frame.active_session_turns,
        "detail_panel_mode": "execution",
        "execution_steps": [],
        "codex_thread_id": "thread-current",
        "codex_turn_id": "turn-1",
    }
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_push_remote_final_answer", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_call_after_if_alive", lambda fn, *args, **kwargs: bool(fn(*args, **kwargs)) or True)
    monkeypatch.setattr(frame, "_call_later_if_alive", lambda _delay, fn, *args, **kwargs: None)

    frame._apply_detail_panel_mode("execution", refresh_execution=True)
    wx_app.Yield()

    append_entry_calls = []
    original_append_execution_entry = frame._append_execution_entry_to_chat

    def _append_execution_entry_spy(*args, **kwargs):
        append_entry_calls.append((args, kwargs))
        return original_append_execution_entry(*args, **kwargs)

    monkeypatch.setattr(frame, "_append_execution_entry_to_chat", _append_execution_entry_spy)
    execution_appends = []
    immediate_answer_mutations = []
    monkeypatch.setattr(frame.execution_list_model, "append", lambda *args, **kwargs: execution_appends.append(args))
    monkeypatch.setattr(
        frame,
        "_update_active_answer_row",
        lambda *args, **kwargs: immediate_answer_mutations.append(("_update_active_answer_row", args)),
    )
    monkeypatch.setattr(
        frame,
        "_append_completed_answer_to_answer_list",
        lambda *args, **kwargs: immediate_answer_mutations.append(("_append_completed_answer_to_answer_list", args)),
    )
    monkeypatch.setattr(
        frame,
        "_refresh_answer_list_preserving_selection",
        lambda *args, **kwargs: immediate_answer_mutations.append(("_refresh_answer_list_preserving_selection", args)),
    )
    monkeypatch.setattr(
        frame,
        "_render_answer_list_compat",
        lambda *args, **kwargs: immediate_answer_mutations.append(("_render_answer_list_compat", args)),
    )

    frame._navigation_quiet_until = time.monotonic() + 3.0
    frame._on_codex_worker_message(
        "chat-current",
        {
            "type": "event",
            "payload": {
                "chat_id": "chat-current",
                "turn_idx": 0,
                "event": {
                    "type": "plan_updated",
                    "thread_id": "thread-current",
                    "turn_id": "turn-1",
                    "text": "run pytest",
                    "data": {"step_seq": 1},
                },
            },
        },
    )
    frame._on_codex_worker_message(
        "chat-current",
        {
            "type": "event",
            "payload": {
                "chat_id": "chat-current",
                "turn_idx": 0,
                "event": {
                    "type": "item_completed",
                    "thread_id": "thread-current",
                    "turn_id": "turn-1",
                    "phase": "final_answer",
                    "text": "final answer",
                    "data": {"step_seq": 2},
                },
            },
        },
    )
    wx_app.Yield()

    assert execution_appends == []
    assert immediate_answer_mutations == []
    assert append_entry_calls
    assert frame._pending_execution_tail_appends.get("chat-current")
    assert frame._background_answer_list_dirty is True
    assert frame.active_session_turns[0]["answer_md"] == "final answer"


def test_real_ui_subagent_result_appears_in_answer_list_without_stealing_input_focus(frame, wx_app, monkeypatch):
    frame.Show()
    frame.active_chat_id = "chat-subagent"
    frame.current_chat_id = "chat-subagent"
    frame.active_codex_thread_id = "thread-subagent"
    frame.active_codex_turn_id = "turn-subagent"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {
            "question": "q",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": 1.0,
            "codex_thread_id": "thread-subagent",
            "codex_turn_id": "turn-subagent",
        }
    ]
    frame._current_chat_state = {
        "id": "chat-subagent",
        "turns": frame.active_session_turns,
        "detail_panel_mode": "answers",
        "execution_steps": [],
    }
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_push_remote_final_answer", lambda *args, **kwargs: None)

    frame._render_answer_list()
    frame.input_edit.SetFocus()
    wx_app.Yield()

    frame._dispatch_codex_event_to_ui(
        "chat-subagent",
        main.CodexEvent(
            type="subagent_result",
            thread_id="thread-subagent",
            turn_id="turn-subagent",
            text="Ada (worker)\nDONE\n\nsubagent result",
            subtype="collab_waiting_end",
            display_kind="commentary",
        ),
    )

    assert _yield_until(
        wx_app,
        lambda: any("subagent result" in frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount()))
        and frame.input_edit.HasFocus(),
        timeout=2.0,
    )


def test_real_ui_history_answer_navigation_does_not_load_execution_history(frame, wx_app, tmp_path, monkeypatch):
    _activate_frame(frame, wx_app)
    frame.chat_db_path = tmp_path / "chat_history.db"
    frame.chat_store = main.ChatStore(frame.chat_db_path)
    frame.chat_store.initialize()
    frame._chat_store_enabled = True
    frame.chat_store.upsert_chat({"id": "chat-heavy", "title": "heavy", "updated_at": 10.0})
    frame.chat_store.replace_turns(
        "chat-heavy",
        [
            {"question": f"question {idx}", "answer_md": f"answer {idx}", "model": main.DEFAULT_CODEX_MODEL}
            for idx in range(30)
        ],
    )
    for idx in range(500):
        frame.chat_store.append_execution_step(
            "chat-heavy",
            {"turn_idx": idx % 30, "display_kind": "commentary", "list_text": f"background step {idx}"},
        )
    frame.archived_chats = frame.chat_store.list_chat_summaries()
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        frame.chat_store,
        "load_execution_steps",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("history answer view loaded execution steps")),
    )

    assert frame._show_history_chat("chat-heavy", focus_answer_list=False) is True
    frame.answer_list.SetSelection(0)
    frame.answer_list.SetFocusFromKbd()
    wx_app.Yield()

    started = time.perf_counter()
    _send_listbox_key(frame.answer_list, main.wx.WXK_DOWN)
    wx_app.Yield()
    assert time.perf_counter() - started < 0.5
    assert frame.answer_list.GetSelection() == 1


def test_real_ui_long_session_primary_controls_remain_responsive(frame, wx_app, monkeypatch):
    _activate_frame(frame, wx_app)
    frame.active_chat_id = "chat-long"
    frame.current_chat_id = "chat-long"
    frame.active_turn_idx = 19
    frame.active_session_turns = [
        {
            "question": f"question {idx}",
            "answer_md": f"answer {idx}",
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": float(idx),
        }
        for idx in range(20)
    ]
    frame._current_chat_state = {
        "id": "chat-long",
        "title": "long",
        "turns": frame.active_session_turns,
        "detail_panel_mode": "answers",
        "execution_steps": [
            {
                "turn_idx": step_idx % 20,
                "display_kind": "commentary",
                "list_text": f"step {step_idx}",
                "detail_text": f"step {step_idx}",
            }
            for step_idx in range(2000)
        ],
    }
    frame.archived_chats = [
        {"id": f"chat-{idx}", "title": f"chat {idx}", "turns": [], "created_at": float(idx), "updated_at": float(idx)}
        for idx in range(20)
    ]
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)

    frame._refresh_history("chat-long")
    frame._render_answer_list(refresh_execution=False)
    frame.answer_list.SetSelection(0)
    frame.answer_list.SetFocusFromKbd()
    wx_app.Yield()
    started = time.perf_counter()
    _send_listbox_key(frame.answer_list, main.wx.WXK_DOWN)
    wx_app.Yield()
    assert time.perf_counter() - started < 0.5
    assert frame.answer_list.GetSelection() == 1

    frame.history_list.SetSelection(0)
    frame.history_list.SetFocusFromKbd()
    wx_app.Yield()
    started = time.perf_counter()
    _send_listbox_key(frame.history_list, main.wx.WXK_DOWN)
    wx_app.Yield()
    assert time.perf_counter() - started < 0.5
    assert frame.history_list.GetSelection() == 1

    frame.model_combo.SetFocus()
    wx_app.Yield()
    started = time.perf_counter()
    _send_window_key(frame.model_combo, main.wx.WXK_DOWN)
    wx_app.Yield()
    assert time.perf_counter() - started < 0.5

    frame.input_edit.SetFocus()
    wx_app.Yield()
    started = time.perf_counter()
    frame.input_edit.WriteText("x")
    wx_app.Yield()
    assert time.perf_counter() - started < 0.5
    assert frame.input_edit.GetValue().endswith("x")

    monkeypatch.setattr(frame, "_start_codex_worker_for_turn", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_play_send_sound", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda *args, **kwargs: None)
    frame.input_edit.SetValue("long session send")
    frame.input_edit.SetFocus()
    wx_app.Yield()
    before_turns = len(frame.active_session_turns)
    started = time.perf_counter()
    frame._trigger_send()
    wx_app.Yield()
    assert time.perf_counter() - started < 0.5
    assert len(frame.active_session_turns) == before_turns + 1

    started = time.perf_counter()
    frame._on_new_chat_clicked(None)
    wx_app.Yield()
    assert time.perf_counter() - started < 0.5
    assert frame.input_edit.HasFocus()

    notebook = frame.notes_store.create_notebook("long notebook")
    for idx in range(30):
        frame.notes_store.create_entry(notebook.id, f"entry {idx}", source="manual")
    frame._notes_select_notebook(notebook.id, view="notes_list")
    frame.notes_notebook_list.SetFocusFromKbd()
    wx_app.Yield()
    started = time.perf_counter()
    _send_listbox_key(frame.notes_notebook_list, main.wx.WXK_DOWN)
    wx_app.Yield()
    assert time.perf_counter() - started < 0.5

    frame._notes_select_notebook(notebook.id, view="note_detail")
    frame.notes_entry_list.SetFocusFromKbd()
    wx_app.Yield()
    started = time.perf_counter()
    _send_listbox_key(frame.notes_entry_list, main.wx.WXK_DOWN)
    wx_app.Yield()
    assert time.perf_counter() - started < 0.5


def test_real_ui_primary_controls_stay_responsive_while_codex_events_are_pending(frame, wx_app, monkeypatch):
    frame.Show()
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.active_codex_thread_id = "thread-active"
    frame.active_codex_turn_id = "turn-active"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {
            "question": "active question",
            "answer_md": "active answer",
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": 2.0,
            "codex_thread_id": "thread-active",
            "codex_turn_id": "turn-active",
        }
    ]
    frame._current_chat_state = {
        "id": "chat-active",
        "title": "active",
        "turns": frame.active_session_turns,
        "detail_panel_mode": "execution",
        "execution_steps": [],
    }
    frame.archived_chats = [
        {"id": "chat-old-1", "title": "old 1", "turns": [], "created_at": 1.0, "updated_at": 1.0},
        {"id": "chat-old-2", "title": "old 2", "turns": [], "created_at": 0.5, "updated_at": 0.5},
    ]
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    frame._apply_detail_panel_mode("execution", refresh_execution=True)
    frame._refresh_history("chat-active")
    frame._render_answer_list(refresh_execution=False)

    for idx in range(main.CODEX_UI_EVENT_BATCH_SIZE * 4):
        frame._pending_codex_ui_events.append(
            (
                "chat-active",
                main.CodexEvent(
                    type="plan_updated",
                    thread_id="thread-active",
                    turn_id="turn-active",
                    text=f"background plan {idx}",
                ),
            )
        )
    navigation_errors = []
    def press_execution_down():
        try:
            assert frame._pending_codex_ui_events
            before = frame.execution_list.GetSelection()
            next_id = frame.execution_list_model.visible_ids[before + 1]
            started = time.perf_counter()
            _send_listbox_key(frame.execution_list, main.wx.WXK_DOWN)
            wx_app.Yield()
            assert time.perf_counter() - started < 0.5
            assert frame.execution_list.GetSelection() == before + 1
            assert frame.execution_list_model.selected_id() == next_id
            assert frame.execution_list.HasFocus()
        except BaseException as exc:
            navigation_errors.append(exc)
        finally:
            wx_app.ExitMainLoop()
    def start_execution_navigation():
        try:
            frame._codex_ui_event_flush_scheduled = True
            frame._drain_codex_ui_events()
            assert frame._codex_ui_event_drain_timer is not None
            frame.execution_list.SetSelection(0)
            frame.execution_list.SetFocusFromKbd()
            main.wx.CallAfter(press_execution_down)
        except BaseException as exc:
            navigation_errors.append(exc)
            wx_app.ExitMainLoop()
    # Run the production batch-timer path; CallAfter delivers the native key
    # while further batches remain scheduled, instead of exhausting them in Yield.
    main.wx.CallAfter(start_execution_navigation)
    wx_app.MainLoop()
    if navigation_errors:
        raise navigation_errors[0]

    frame._apply_detail_panel_mode("answers", refresh_execution=False)
    answer_row = next(idx for idx, meta in enumerate(frame.answer_meta) if meta[0] == "question")
    frame.answer_list.SetSelection(answer_row)
    frame.answer_list.SetFocusFromKbd()
    wx_app.Yield()
    started = time.perf_counter()
    _send_listbox_key(frame.answer_list, main.wx.WXK_DOWN)
    wx_app.Yield()
    assert time.perf_counter() - started < 0.5
    assert frame.answer_meta[frame.answer_list.GetSelection()][0] == "ai"

    frame.history_list.SetSelection(0)
    frame.history_list.SetFocusFromKbd()
    wx_app.Yield()
    started = time.perf_counter()
    _send_listbox_key(frame.history_list, main.wx.WXK_DOWN)
    wx_app.Yield()
    assert time.perf_counter() - started < 0.5
    assert frame.history_list.GetSelection() == 1

    frame.input_edit.SetFocus()
    wx_app.Yield()
    started = time.perf_counter()
    frame.input_edit.WriteText("z")
    wx_app.Yield()
    assert time.perf_counter() - started < 0.5
    assert frame.input_edit.GetValue().endswith("z")

    frame.model_combo.SetFocus()
    wx_app.Yield()
    started = time.perf_counter()
    _send_window_key(frame.model_combo, main.wx.WXK_DOWN)
    wx_app.Yield()
    assert time.perf_counter() - started < 0.5

    notebook = frame.notes_store.create_notebook("responsive notebook")
    frame.notes_store.create_entry(notebook.id, "responsive entry", source="manual")
    frame._notes_select_notebook(notebook.id, view="notes_list")
    frame.notes_notebook_list.SetFocusFromKbd()
    wx_app.Yield()
    started = time.perf_counter()
    _send_listbox_key(frame.notes_notebook_list, main.wx.WXK_DOWN)
    wx_app.Yield()
    assert time.perf_counter() - started < 0.5

    frame._notes_select_notebook(notebook.id, view="note_detail")
    frame.notes_entry_list.SetFocusFromKbd()
    wx_app.Yield()
    started = time.perf_counter()
    _send_listbox_key(frame.notes_entry_list, main.wx.WXK_DOWN)
    wx_app.Yield()
    assert time.perf_counter() - started < 0.5

    assert not frame._pending_codex_ui_events or frame._codex_ui_event_drain_timer is not None


def test_real_ui_common_commands_list_stays_responsive_during_remote_refresh_burst(frame, wx_app):
    _activate_frame(frame, wx_app)
    frame.common_commands_store.create_command(
        main.CommonCommandCreate(title="One", content="echo one")
    )
    frame.common_commands_store.create_command(
        main.CommonCommandCreate(title="Two", content="echo two")
    )
    frame.common_commands_store.create_command(
        main.CommonCommandCreate(title="Three", content="echo three")
    )
    assert frame._show_common_commands_surface() is True
    dialog = frame.common_commands_dialog
    dialog.common_commands_list.SetSelection(0)
    dialog.common_commands_list.SetFocusFromKbd()
    wx_app.Yield()

    for idx in range(12):
        selected = dialog.selected_command()
        updated = frame.common_commands_store.update_command(
            selected.id,
            main.CommonCommandUpdate(
                expected_version=selected.version,
                title=selected.title,
                content=f"echo selected {idx}",
            ),
        )
        frame.common_commands_store.update_command(
            dialog.common_commands_list_ids[-1],
            main.CommonCommandUpdate(
                expected_version=dialog.common_commands_by_id[dialog.common_commands_list_ids[-1]].version,
                title=f"Three {idx}",
                content=f"echo three {idx}",
            ),
        )
        dialog.refresh_commands(select_id=updated.id)
        wx_app.Yield()

    started = time.perf_counter()
    _send_listbox_key(dialog.common_commands_list, main.wx.WXK_DOWN)
    wx_app.Yield()
    elapsed = time.perf_counter() - started

    assert elapsed < 0.5
    assert dialog.common_commands_list.HasFocus()
    assert dialog.selected_command().title == "Two"


def test_versioned_execution_snapshot_read_does_not_block_wx(frame, wx_app, tmp_path, monkeypatch):
    store = main.ChatStore(tmp_path / "slow-projection.db")
    store.initialize()
    store.upsert_chat({"id": "slow", "title": "slow", "created_at": 1, "updated_at": 1})
    store.replace_execution_steps("slow", [{"display_kind": "commentary", "list_text": "step"}])
    frame.chat_store = store
    frame._chat_store_enabled = True
    blocked, release = threading.Event(), threading.Event()
    original = store.read_execution_projection_source

    def slow_read(owner):
        assert threading.current_thread() is not threading.main_thread()
        blocked.set()
        assert release.wait(5)
        return original(owner)

    monkeypatch.setattr(store, "read_execution_projection_source", slow_read)
    outcome = []
    worker = threading.Thread(target=lambda: outcome.append(
        frame._remote_api_execution_page_ui({"chat_id": "slow", "body": {"limit": 10}}, secret="secret")
    ))
    worker.start()
    assert _yield_until(wx_app, blocked.is_set)
    started = time.perf_counter()
    wx_app.Yield()
    assert time.perf_counter() - started < 0.5
    release.set()
    assert _yield_until(wx_app, lambda: not worker.is_alive())
    worker.join(timeout=1)
    assert outcome[0][0] in {200, 409}
