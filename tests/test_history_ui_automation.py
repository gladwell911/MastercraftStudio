import ctypes
import time

import main
import pytest


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


def test_history_navigation_stays_stable_when_background_chat_title_updates(frame, wx_app, monkeypatch):
    frame.Show()
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame._current_chat_state = {"id": "chat-active", "title": "active", "turns": []}
    frame.archived_chats = [
        {
            "id": "chat-old",
            "title": "old",
            "turns": [{"question": "q", "answer_md": main.REQUESTING_TEXT, "model": main.DEFAULT_CODEX_MODEL}],
            "created_at": 1.0,
            "updated_at": 1.0,
        }
    ]
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    frame._refresh_history("chat-active")
    frame.history_list.SetSelection(0)
    frame.history_list.SetFocusFromKbd()
    wx_app.Yield()

    frame._on_done(0, "background answer", "", main.DEFAULT_CODEX_MODEL, "", "chat-old")
    wx_app.Yield()

    assert frame.history_list.GetSelection() == 0
    assert frame.history_ids == ["chat-active", "chat-old"]
    assert frame.history_list.GetString(1)


class _EnterEvent:
    def GetKeyCode(self):
        return main.wx.WXK_RETURN

    def ShiftDown(self):
        return False

    def ControlDown(self):
        return False

    def AltDown(self):
        return False

    def Skip(self):
        return None


class _ShiftEnterEvent:
    def __init__(self, event_object=None):
        self._event_object = event_object

    def GetKeyCode(self):
        return main.wx.WXK_RETURN

    def GetEventObject(self):
        return self._event_object

    def ShiftDown(self):
        return True

    def ControlDown(self):
        return False

    def AltDown(self):
        return False

    def Skip(self):
        raise AssertionError("Shift+Enter should be handled")


class _MenuEvent:
    def GetKeyCode(self):
        return main.wx.WXK_MENU

    def Skip(self):
        raise AssertionError("application key should open history menu")


class _F2Event:
    def GetKeyCode(self):
        return main.wx.WXK_F2

    def Skip(self):
        raise AssertionError("F2 should open history rename")


def test_ui_automation_application_menu_opens_for_current_chat(frame, wx_app, monkeypatch):
    frame.Show()
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state.update({"id": "chat-current", "title": "current", "turns": []})
    frame._refresh_history("chat-current")
    frame.history_list.SetFocusFromKbd()
    frame.history_list.SetSelection(frame.history_ids.index("chat-current"))
    wx_app.Yield()
    opened = []
    monkeypatch.setattr(frame, "PopupMenu", lambda menu: opened.append(menu.GetMenuItemCount()) or True)

    frame._on_history_key_down(_MenuEvent())
    wx_app.Yield()

    assert opened == [4]
    assert frame.history_ids[frame.history_list.GetSelection()] == "chat-current"


def test_ui_automation_history_f2_opens_rename_for_selected_chat(frame, wx_app, monkeypatch):
    frame.Show()
    frame.archived_chats = [
        {"id": "chat-a", "title": "chat a", "turns": [], "created_at": 1.0, "updated_at": 1.0}
    ]
    frame._refresh_history("chat-a")
    frame.history_list.SetFocusFromKbd()
    frame.history_list.SetSelection(frame.history_ids.index("chat-a"))
    wx_app.Yield()
    calls = {"rename": 0}
    monkeypatch.setattr(frame, "_history_rename", lambda _event: calls.__setitem__("rename", calls["rename"] + 1))

    frame._on_history_key_down(_F2Event())
    wx_app.Yield()

    assert calls["rename"] == 1
    assert frame.history_ids[frame.history_list.GetSelection()] == "chat-a"


def test_ui_automation_new_chat_button_shift_enter_creates_named_chat(frame, wx_app, monkeypatch):
    frame.Show()
    frame.new_chat_button.SetFocusFromKbd()
    wx_app.Yield()
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda *args, **kwargs: None)

    class _Dialog:
        def __init__(self, *_args, **_kwargs):
            pass

        def ShowModal(self):
            return main.wx.ID_OK

        def GetValue(self):
            return "命名聊天"

        def Destroy(self):
            pass

    monkeypatch.setattr(main.wx, "TextEntryDialog", _Dialog)

    frame._on_generic_key_down(_ShiftEnterEvent(frame.new_chat_button))
    wx_app.Yield()

    assert frame._current_chat_state["title"] == "命名聊天"
    assert frame._current_chat_state["title_manual"] is True
    assert frame._current_chat_state["title_source"] == "manual"
    assert frame.input_edit.HasFocus()


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
        frame.codex_speed_combo,
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
    rows = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert rows[:4] == ["我", "history question", "小诸葛", "history answer"]
    assert frame.answer_meta[0][0] == "user"
    question_rows = [idx for idx, meta in enumerate(frame.answer_meta) if meta[0] == "question"]
    assert question_rows
    assert frame.answer_list.GetString(question_rows[0]) == "history question"
    assert frame.answer_list.HasFocus()


def test_ui_automation_history_enter_focuses_answer_before_background_work(frame, monkeypatch):
    frame.Show()
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state = {"id": "chat-current", "title": "current", "turns": []}
    frame.archived_chats = [
        {
            "id": "hist-fast",
            "title": "history fast",
            "pinned": False,
            "created_at": 1.0,
            "updated_at": 1.0,
            "turns": [
                {"question": "q1", "answer_md": "a1", "model": "openai/gpt-5.2", "created_at": 1.0},
                {"question": "q2", "answer_md": "a2", "model": "openai/gpt-5.2", "created_at": 2.0},
            ],
        }
    ]
    monkeypatch.setattr(frame, "_refresh_history", lambda *_args, **_kwargs: pytest.fail("Enter must not synchronously refresh history"))
    monkeypatch.setattr(frame, "_save_state", lambda *_args, **_kwargs: pytest.fail("Enter must not synchronously save state"))
    deferred = []
    monkeypatch.setattr(frame, "_mark_history_list_dirty", lambda keep_id=None: deferred.append(("history", keep_id)))
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: deferred.append(("save", None)))

    frame.history_ids = ["hist-fast"]
    frame.history_list.Clear()
    frame.history_list.Append("history fast")
    frame.history_list.SetSelection(0)
    frame.history_list.SetFocus()

    frame._on_history_key_down(_EnterEvent())

    assert frame.view_mode == "history"
    assert frame.view_history_id == "hist-fast"
    assert frame.answer_list.HasFocus()
    selected = frame.answer_list.GetSelection()
    assert selected != main.wx.NOT_FOUND
    assert frame.answer_meta[selected][0] == "answer"
    assert frame.answer_list.GetStringSelection() == "a2"
    assert ("history", "hist-fast") in deferred
    assert ("save", None) in deferred


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


def test_ui_automation_history_enter_restores_selected_chat_model_in_combo(frame, monkeypatch):
    frame.Show()
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)

    frame.active_chat_id = "chat-b"
    frame.current_chat_id = "chat-b"
    frame.selected_model = main.DEFAULT_CODEX_MODEL
    frame.model_combo.SetValue(main.model_display_name(main.DEFAULT_CODEX_MODEL))
    frame.active_session_turns = [
        {
            "question": "codex question",
            "answer_md": "codex answer",
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": 2.0,
        }
    ]
    frame._current_chat_state = {
        "id": "chat-b",
        "title": "chat b",
        "model": main.DEFAULT_CODEX_MODEL,
        "created_at": 2.0,
        "updated_at": 2.0,
        "turns": frame.active_session_turns,
    }
    frame.archived_chats = [
        {
            "id": "chat-a",
            "title": "chat a",
            "model": "anthropic/claude-sonnet-4.6",
            "pinned": False,
            "created_at": 1.0,
            "updated_at": 1.0,
            "turns": [
                {
                    "question": "claude question",
                    "answer_md": "claude answer",
                    "model": "anthropic/claude-sonnet-4.6",
                    "created_at": 1.0,
                }
            ],
        }
    ]

    frame._refresh_history("chat-a")
    frame.history_list.SetFocus()
    frame.history_list.SetSelection(frame.history_ids.index("chat-a"))
    frame._on_history_key_down(_EnterEvent())

    assert frame.view_mode == "history"
    assert frame.view_history_id == "chat-a"
    assert frame.selected_model == "anthropic/claude-sonnet-4.6"
    assert frame.model_combo.GetValue() == main.model_display_name("anthropic/claude-sonnet-4.6")
    assert frame.answer_list.HasFocus()


def test_ui_automation_history_enter_restores_selected_chat_codex_speed(frame, monkeypatch):
    frame.Show()
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)

    frame.active_chat_id = "chat-b"
    frame.current_chat_id = "chat-b"
    frame.selected_model = main.DEFAULT_CODEX_MODEL
    frame.model_combo.SetValue(main.model_display_name(main.DEFAULT_CODEX_MODEL))
    frame.active_session_turns = [
        {
            "question": "fast codex question",
            "answer_md": "fast codex answer",
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": 2.0,
            "codex_service_tier": "fast",
        }
    ]
    frame._current_chat_state = {
        "id": "chat-b",
        "title": "chat b",
        "model": main.DEFAULT_CODEX_MODEL,
        "codex_service_tier": "fast",
        "created_at": 2.0,
        "updated_at": 2.0,
        "turns": frame.active_session_turns,
    }
    frame._sync_codex_speed_combo_from_chat(frame._current_chat_state)
    assert frame.codex_speed_combo.GetValue() == "快速"
    frame.archived_chats = [
        {
            "id": "chat-a",
            "title": "chat a",
            "model": main.DEFAULT_CODEX_MODEL,
            "codex_service_tier": "",
            "pinned": False,
            "created_at": 1.0,
            "updated_at": 1.0,
            "turns": [
                {
                    "question": "standard codex question",
                    "answer_md": "standard codex answer",
                    "model": main.DEFAULT_CODEX_MODEL,
                    "created_at": 1.0,
                    "codex_service_tier": "",
                }
            ],
        }
    ]

    frame._refresh_history("chat-a")
    frame.history_list.SetFocus()
    frame.history_list.SetSelection(frame.history_ids.index("chat-a"))
    frame._on_history_key_down(_EnterEvent())

    assert frame.view_mode == "history"
    assert frame.view_history_id == "chat-a"
    assert frame.selected_model == main.DEFAULT_CODEX_MODEL
    assert frame.model_combo.GetValue() == main.model_display_name(main.DEFAULT_CODEX_MODEL)
    assert frame.codex_speed_combo.GetValue() == "标准"
    assert frame.codex_speed_combo.IsEnabled() is True
    assert frame.answer_list.HasFocus()


def test_ui_automation_switching_chat_restores_that_chat_model_without_losing_focus(frame, monkeypatch):
    frame.Show()
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda *args, **kwargs: None)

    frame.active_chat_id = "chat-a"
    frame.current_chat_id = "chat-a"
    frame.active_session_turns = [
        {
            "question": "claude question",
            "answer_md": "claude answer",
            "model": "anthropic/claude-sonnet-4.6",
            "created_at": 1.0,
        }
    ]
    frame._current_chat_state = {
        "id": "chat-a",
        "title": "chat a",
        "model": "anthropic/claude-sonnet-4.6",
        "created_at": 1.0,
        "updated_at": 1.0,
        "turns": frame.active_session_turns,
    }
    frame.selected_model = "anthropic/claude-sonnet-4.6"
    frame.model_combo.SetValue(main.model_display_name("anthropic/claude-sonnet-4.6"))
    frame._archive_active_session(quick_title=True, save_after_archive=False)

    frame.active_chat_id = "chat-b"
    frame.current_chat_id = "chat-b"
    frame.active_session_turns = [
        {
            "question": "codex question",
            "answer_md": "codex answer",
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": 2.0,
        }
    ]
    frame._current_chat_state = {
        "id": "chat-b",
        "title": "chat b",
        "model": main.DEFAULT_CODEX_MODEL,
        "created_at": 2.0,
        "updated_at": 2.0,
        "turns": frame.active_session_turns,
    }
    frame.selected_model = main.DEFAULT_CODEX_MODEL
    frame.model_combo.SetValue(main.model_display_name(main.DEFAULT_CODEX_MODEL))
    frame.answer_list.SetFocus()

    assert frame._switch_current_chat("chat-a") is True

    assert frame.selected_model == "anthropic/claude-sonnet-4.6"
    assert frame.model_combo.GetValue() == main.model_display_name("anthropic/claude-sonnet-4.6")
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


def test_ui_automation_background_codex_worker_does_not_reuse_visible_chat_thread(frame, monkeypatch):
    frame.Show()
    frame.active_chat_id = "chat-c"
    frame.current_chat_id = "chat-c"
    frame.active_codex_thread_id = "thread-c"
    frame.active_codex_turn_id = "turn-c"
    frame.active_session_turns = [
        {
            "question": "question d",
            "answer_md": "answer d",
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": 2.0,
            "request_status": "pending",
            "codex_thread_id": "thread-c",
            "codex_turn_id": "turn-c",
        }
    ]
    frame._current_chat_state.update(
        {
            "id": "chat-c",
            "title": "rclone上传R2",
            "turns": frame.active_session_turns,
            "detail_panel_mode": "answers",
            "execution_steps": [],
            "codex_thread_id": "thread-c",
            "codex_turn_id": "turn-c",
        }
    )
    frame.archived_chats = [
        {
            "id": "chat-a",
            "title": "切换代码目录",
            "pinned": False,
            "created_at": 1.0,
            "updated_at": 1.0,
            "turns": [
                {
                    "question": "question b",
                    "answer_md": main.REQUESTING_TEXT,
                    "model": main.DEFAULT_CODEX_MODEL,
                    "created_at": 1.0,
                    "request_status": "pending",
                }
            ],
            "detail_panel_mode": "answers",
            "execution_steps": [],
        }
    ]
    calls = []

    class _Client:
        def start_thread(self, **kwargs):
            calls.append(("start_thread", kwargs))
            return {"thread": {"id": "thread-a-new"}}

        def start_turn_items(self, thread_id, items):
            calls.append(("start_turn_items", thread_id, items))
            return {"turn": {"id": "turn-a-new"}}

    monkeypatch.setattr(frame, "_get_or_create_codex_client", lambda chat_id, model="": _Client())
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    frame._render_answer_list()
    frame.answer_list.SetFocusFromKbd()
    before_rows = list(frame.answer_list.GetStrings())

    frame._run_codex_turn_worker("chat-a", 0, "question b", main.DEFAULT_CODEX_MODEL)

    after_rows = list(frame.answer_list.GetStrings())
    archived = frame._find_archived_chat("chat-a")
    assert calls[0][0] == "start_thread"
    assert calls[1][1] == "thread-a-new"
    assert archived["codex_thread_id"] == "thread-a-new"
    assert archived["turns"][0]["codex_thread_id"] == "thread-a-new"
    assert frame.active_codex_thread_id == "thread-c"
    assert frame.active_session_turns[0]["answer_md"] == "answer d"
    assert after_rows == before_rows
    assert frame.answer_list.HasFocus()


def test_ui_automation_visible_chat_does_not_receive_late_claudecode_delta_from_previous_chat(frame, monkeypatch):
    frame.Show()
    frame.active_chat_id = "chat-c"
    frame.current_chat_id = "chat-c"
    frame.active_claudecode_session_id = "session-c"
    frame.active_session_turns = [
        {
            "question": "question d",
            "answer_md": "answer d",
            "model": "claudecode/default",
            "created_at": 2.0,
        }
    ]
    frame._current_chat_state.update(
        {
            "id": "chat-c",
            "title": "rclone上传R2",
            "turns": frame.active_session_turns,
            "detail_panel_mode": "answers",
            "execution_steps": [],
        }
    )
    frame.archived_chats = [
        {
            "id": "chat-a",
            "title": "切换代码目录",
            "turns": [
                {
                    "question": "question b",
                    "answer_md": main.REQUESTING_TEXT,
                    "model": "claudecode/default",
                    "created_at": 1.0,
                    "request_status": "pending",
                }
            ],
            "created_at": 1.0,
            "updated_at": 1.0,
            "claudecode_session_id": "session-a-old",
        }
    ]

    class _ImmediateThread:
        def __init__(self, target, daemon=False):
            self._target = target

        def start(self):
            self._target()

    class _FakeClaudeCodeClient:
        def __init__(self, *args, **kwargs):
            pass

        def stream_chat(self, question, session_id="", on_delta=None, on_user_input=None, on_approval=None):
            on_delta("late answer b delta")
            return "late answer b final", "session-a"

    monkeypatch.setattr(main.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(main, "ClaudeCodeClient", _FakeClaudeCodeClient)
    monkeypatch.setattr(main, "wx_call_after_if_alive", lambda fn, *args, **kwargs: fn(*args, **kwargs))
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: None)
    monkeypatch.setattr(frame, "_push_remote_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_final_answer", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_refresh_history", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)
    monkeypatch.setattr(frame, "_set_input_hint_idle", lambda: None)

    frame._render_answer_list()
    frame.answer_list.SetFocusFromKbd()
    before_rows = list(frame.answer_list.GetStrings())

    frame._start_claudecode_worker_for_turn("chat-a", 0, "question b", "session-a-old")

    after_rows = list(frame.answer_list.GetStrings())
    archived = frame._find_archived_chat("chat-a")
    assert archived["turns"][0]["answer_md"] == "late answer b final"
    assert archived["claudecode_session_id"] == "session-a"
    assert frame.active_session_turns[0]["answer_md"] == "answer d"
    assert after_rows == before_rows
    assert frame.answer_list.HasFocus()


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

    assert rows == ["我：please fix tests"]
