import ctypes
import time

import main


def _send_listbox_key(window, key_code):
    _send_window_key(window, key_code)


def _send_window_key(window, key_code):
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    wm_keydown = 0x0100
    wm_keyup = 0x0101
    scan_codes = {
        main.wx.WXK_UP: 0x48,
        main.wx.WXK_DOWN: 0x50,
        main.wx.WXK_HOME: 0x47,
        main.wx.WXK_END: 0x4F,
    }
    virtual_keys = {
        main.wx.WXK_UP: 0x26,
        main.wx.WXK_DOWN: 0x28,
        main.wx.WXK_HOME: 0x24,
        main.wx.WXK_END: 0x23,
    }
    scan = scan_codes.get(key_code, 0)
    virtual_key = virtual_keys.get(key_code, int(key_code))
    down_lparam = 1 | (scan << 16)
    up_lparam = 1 | (scan << 16) | (1 << 30) | (1 << 31)
    hwnd = int(window.GetHandle())
    user32.SendMessageW(hwnd, wm_keydown, virtual_key, down_lparam)
    user32.SendMessageW(hwnd, wm_keyup, virtual_key, up_lparam)


def _send_listbox_ctrl_c(window, wx_app):
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    wm_keydown = 0x0100
    wm_keyup = 0x0101
    keyeventf_keyup = 0x0002
    vk_control = 0x11
    vk_c = ord("C")
    c_scan = 0x2E
    down_lparam = 1 | (c_scan << 16)
    up_lparam = 1 | (c_scan << 16) | (1 << 30) | (1 << 31)
    hwnd = int(window.GetHandle())
    user32.keybd_event(vk_control, 0x1D, 0, 0)
    wx_app.Yield()
    try:
        user32.SendMessageW(hwnd, wm_keydown, vk_c, down_lparam)
        user32.SendMessageW(hwnd, wm_keyup, vk_c, up_lparam)
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
    assert frame._pending_codex_ui_events
    assert frame._codex_ui_event_drain_timer is not None


def test_real_ui_answer_list_ctrl_c_keeps_selection_and_focus(frame, wx_app, monkeypatch):
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
    assert frame._pending_codex_ui_events
    assert frame._codex_ui_event_drain_timer is not None
    assert frame.input_edit.HasFocus()

    frame.input_edit.WriteText("x")
    assert frame.input_edit.GetValue().endswith("x")


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
    frame._codex_ui_event_flush_scheduled = True
    frame._drain_codex_ui_events()
    assert frame._pending_codex_ui_events

    frame.execution_list.SetSelection(0)
    frame.execution_list.SetFocusFromKbd()
    wx_app.Yield()
    started = time.perf_counter()
    _send_listbox_key(frame.execution_list, main.wx.WXK_DOWN)
    wx_app.Yield()
    assert time.perf_counter() - started < 0.5
    assert frame.execution_list.GetSelection() == 1

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

    assert frame._pending_codex_ui_events
    assert frame._codex_ui_event_drain_timer is not None
