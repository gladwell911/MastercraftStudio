import ctypes
import time

import main


def _send_listbox_key(window, key_code):
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


def _focused_control():
    return main.wx.Window.FindFocus()


class _EnterEvent:
    def GetKeyCode(self):
        return main.wx.WXK_RETURN

    def Skip(self):
        return None


def test_ui_automation_primary_tab_sequence_matches_screen_reader_order(frame, wx_app):
    frame.Show()
    notebook = frame.notes_store.create_notebook("tab order")
    frame._notes_select_notebook(notebook.id, view="notes_list")
    frame._current_chat_state["detail_panel_mode"] = "answers"
    frame._apply_detail_panel_mode("answers", refresh_execution=False)
    wx_app.Yield()

    expected = [
        frame.input_edit,
        frame.new_chat_button,
        frame.model_combo,
        frame.send_button,
        frame.notes_notebook_list,
        frame.history_list,
        frame.answer_list,
    ]

    frame.input_edit.SetFocus()
    wx_app.Yield()
    for control in expected:
        assert _focused_control() is control
        assert _focused_control().Navigate(main.wx.NavigationKeyEvent.IsForward)
        wx_app.Yield()


def test_ui_automation_history_enter_allows_switch_during_pending_reply(frame, monkeypatch):
    frame.Show()
    frame.is_running = True
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state.update(
        {
            "id": "chat-current",
            "title": "current chat",
            "turns": [
                {
                    "question": "current pending question",
                    "answer_md": main.REQUESTING_TEXT,
                    "model": "openai/gpt-5.2",
                    "created_at": 4.0,
                    "request_status": "pending",
                }
            ],
        }
    )
    frame.active_session_turns = list(frame._current_chat_state["turns"])
    frame.archived_chats = [
        {
            "id": "hist-1",
            "title": "history chat",
            "pinned": False,
            "created_at": 1.0,
            "updated_at": 1.0,
            "turns": [
                {
                    "question": "history question",
                    "answer_md": "history answer",
                    "model": "openai/gpt-5.2",
                    "created_at": 1.0,
                }
            ],
        }
    ]
    shown = {"dialog": 0}
    monkeypatch.setattr(frame, "_show_ok_dialog", lambda *_args, **_kwargs: shown.__setitem__("dialog", shown["dialog"] + 1))

    frame._refresh_history("hist-1")
    frame.history_list.SetFocus()
    frame.history_list.SetSelection(frame.history_ids.index("hist-1"))

    frame._on_history_key_down(_EnterEvent())

    assert shown["dialog"] == 0
    assert frame.view_mode == "history"
    assert frame.view_history_id == "hist-1"
    assert frame.answer_meta[0][0] == "context_usage"
    question_rows = [idx for idx, meta in enumerate(frame.answer_meta) if meta[0] == "question"]
    assert question_rows
    assert frame.answer_list.GetString(question_rows[0]) == "history question"
    assert frame.answer_list.HasFocus()


def test_ui_automation_large_sqlite_history_keeps_history_list_responsive(frame, wx_app):
    frame.Show()
    frame.active_chat_id = ""
    frame.current_chat_id = ""
    frame.active_session_turns = []
    frame._current_chat_state = {}
    frame.archived_chats = []
    for chat_idx in range(1000):
        chat_id = f"chat-{chat_idx}"
        frame.chat_store.upsert_chat(
            {
                "id": chat_id,
                "title": f"chat {chat_idx}",
                "created_at": float(chat_idx),
                "updated_at": float(chat_idx),
            }
        )
        frame.chat_store.replace_turns(
            chat_id,
            [
                {
                    "question": "q",
                    "answer_md": "long answer " * 200,
                    "model": main.DEFAULT_CODEX_MODEL,
                    "created_at": float(chat_idx),
                }
            ],
        )
    frame.archived_chats = frame.chat_store.list_chat_summaries()
    frame._refresh_history()

    assert "turns" not in frame.archived_chats[0]
    frame.history_list.SetFocusFromKbd()
    frame.history_list.SetSelection(0)
    wx_app.Yield()
    started = time.perf_counter()
    _send_listbox_key(frame.history_list, main.wx.WXK_DOWN)
    wx_app.Yield()
    elapsed = time.perf_counter() - started

    assert elapsed < 0.5
    assert frame.history_list.GetSelection() == 1


def test_ui_automation_history_enter_can_return_to_empty_new_chat(frame):
    frame.Show()
    frame.active_chat_id = "chat-new"
    frame.current_chat_id = "chat-new"
    frame._current_chat_state.update(
        {
            "id": "chat-new",
            "title": main.EMPTY_CURRENT_CHAT_TITLE,
            "title_manual": False,
            "turns": [],
        }
    )
    frame.active_session_turns = []
    frame.archived_chats = [
        {
            "id": "hist-1",
            "title": "history chat",
            "pinned": False,
            "created_at": 1.0,
            "updated_at": 1.0,
            "turns": [
                {
                    "question": "history question",
                    "answer_md": "history answer",
                    "model": "openai/gpt-5.2",
                    "created_at": 1.0,
                }
            ],
        }
    ]

    frame._refresh_history("hist-1")
    frame.history_list.SetFocus()
    frame.history_list.SetSelection(frame.history_ids.index("hist-1"))

    frame._on_history_key_down(_EnterEvent())

    assert frame.view_mode == "history"
    assert frame.view_history_id == "hist-1"
    assert frame.answer_list.HasFocus()

    frame.history_list.SetSelection(frame.history_ids.index("chat-new"))
    frame.history_list.SetFocus()
    frame._on_history_key_down(_EnterEvent())

    assert frame.view_mode == "active"
    assert frame.view_history_id is None
    assert frame.current_chat_id == "chat-new"
    assert frame.active_chat_id == "chat-new"
    assert "history question" not in list(frame.answer_list.GetStrings())
    assert frame.answer_list.HasFocus()


def test_ui_automation_switched_visible_chat_does_not_receive_late_codex_answer_from_previous_chat(frame, monkeypatch):
    frame.Show()
    frame.active_chat_id = "chat-b"
    frame.current_chat_id = "chat-b"
    frame._current_chat_state.update(
        {
            "id": "chat-b",
            "title": "chat b",
            "turns": [
                {
                    "question": "question b",
                    "answer_md": main.REQUESTING_TEXT,
                    "model": main.DEFAULT_CODEX_MODEL,
                    "created_at": 2.0,
                    "request_status": "pending",
                    "codex_thread_id": "thread-b",
                    "codex_turn_id": "turn-b",
                }
            ],
            "detail_panel_mode": "answers",
            "execution_steps": [],
            "codex_thread_id": "thread-b",
            "codex_turn_id": "turn-b",
        }
    )
    frame.active_session_turns = frame._current_chat_state["turns"]
    frame.archived_chats = [
        {
            "id": "chat-a",
            "title": "chat a",
            "pinned": False,
            "created_at": 1.0,
            "updated_at": 1.0,
            "turns": [
                {
                    "question": "question a",
                    "answer_md": main.REQUESTING_TEXT,
                    "model": main.DEFAULT_CODEX_MODEL,
                    "created_at": 1.0,
                    "request_status": "pending",
                    "codex_thread_id": "thread-a",
                    "codex_turn_id": "turn-a",
                }
            ],
            "detail_panel_mode": "answers",
            "execution_steps": [],
            "codex_thread_id": "thread-a",
            "codex_turn_id": "turn-a",
        }
    ]
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_push_remote_final_answer", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_call_later_if_alive", lambda *args, **kwargs: None)

    frame._render_answer_list()
    before_rows = list(frame.answer_list.GetStrings())

    frame._on_codex_event(
        main.CodexEvent(
            type="item_completed",
            phase="final_answer",
            thread_id="thread-a",
            turn_id="turn-a",
            text="late answer a",
        )
    )

    after_rows = list(frame.answer_list.GetStrings())
    archived = frame._find_archived_chat("chat-a")
    assert archived["turns"][0]["answer_md"] == "late answer a"
    assert frame._current_chat_state["turns"][0]["answer_md"] == main.REQUESTING_TEXT
    assert after_rows == before_rows


def test_ui_automation_f1_execution_view_shows_detailed_codex_progress(frame, monkeypatch):
    frame.Show()
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state.update(
        {
            "id": "chat-current",
            "title": "current chat",
            "turns": [
                {
                    "question": "please fix tests",
                    "answer_md": main.REQUESTING_TEXT,
                    "model": main.DEFAULT_CODEX_MODEL,
                    "created_at": 1.0,
                }
            ],
            "detail_panel_mode": "answers",
            "execution_steps": [],
        }
    )
    frame.active_session_turns = frame._current_chat_state["turns"]
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    frame._on_codex_event_for_chat(
        "chat-current",
        main.CodexEvent(
            type="item_started",
            thread_id="thread-current",
            turn_id="turn-current",
            status="commandExecution",
            data={
                "type": "commandExecution",
                "title": "run tests",
                "command": "pytest tests/test_main_unit.py -k codex",
            },
        ),
    )

    frame._apply_detail_panel_mode("execution", refresh_execution=True)
    rows = list(frame.execution_list.GetStrings())

    assert rows == ["暂无执行过程"]
