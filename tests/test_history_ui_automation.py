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
        main.wx.WXK_SPACE: 0x39,
    }
    virtual_keys = {
        main.wx.WXK_UP: 0x26,
        main.wx.WXK_DOWN: 0x28,
        main.wx.WXK_HOME: 0x24,
        main.wx.WXK_END: 0x23,
        main.wx.WXK_SPACE: 0x20,
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
    frame._current_chat_state = {"id": "chat-active", "title": "active", "updated_at": 10.0, "turns": []}
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
    focus_before = _focused_control()
    frame._mark_primary_interaction()
    frame._navigation_quiet_until = time.monotonic() + 2.0

    frame._on_done(0, "background answer", "", main.DEFAULT_CODEX_MODEL, "", "chat-old")
    wx_app.Yield()

    assert frame.history_list_model.selected_id() == "chat-active"
    assert frame.history_ids == ["chat-old", "chat-active"]
    assert frame.history_list.GetString(0)
    assert frame._navigation_quiet_active()
    assert frame.history_ids == ["chat-old", "chat-active"]
    assert frame.history_list_model.selected_id() == "chat-active"
    assert _focused_control() is focus_before


def test_restored_chat_new_chat_first_send_keeps_unique_real_wx_rows_and_input_focus(
    frame, wx_app, monkeypatch
):
    frame.Show()
    frame.active_chat_id = "chat-restored"
    frame.current_chat_id = "chat-restored"
    restored_turns = [{"question": "old q", "answer_md": "old a", "model": main.DEFAULT_CODEX_MODEL}]
    frame.active_session_turns = restored_turns
    frame._current_chat_state = {
        "id": "chat-restored", "title": "restored", "turns": restored_turns, "updated_at": 2.0
    }
    frame.archived_chats = [
        {"id": " chat-restored ", "title": "stale restored", "pinned": True, "updated_at": 1.0}
    ]
    starts = []
    monkeypatch.setattr(frame, "_start_codex_worker_for_turn", lambda *args: starts.append(args))
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *args, **kwargs: None)

    class _NoOpThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(main.threading, "Thread", _NoOpThread)

    frame._on_new_chat_clicked(None)
    new_chat_id = frame.active_chat_id
    assert new_chat_id in frame.history_ids
    assert frame.history_ids[frame.history_list.GetSelection()] == new_chat_id
    assert frame.input_edit.HasFocus()
    ok, message = frame._submit_question("first question", source="local", model=main.DEFAULT_CODEX_MODEL)
    wx_app.Yield()

    assert (ok, message) == (True, "")
    assert len(starts) == 1
    assert len(frame.active_session_turns) == 1
    assert frame.input_edit.HasFocus()
    frame._refresh_history(new_chat_id)
    assert frame.history_list.GetCount() == len(frame.history_ids)
    assert len(frame.history_ids) == len(set(frame.history_ids))
    assert frame.history_ids.count("chat-restored") == 1

    ok, message = frame._submit_question("later question", source="local", model=main.DEFAULT_CODEX_MODEL)
    assert (ok, message) == (True, "")
    assert len(frame.active_session_turns) == 2
    assert len(starts) == 2
    assert frame.input_edit.HasFocus()

    frame._on_new_chat_clicked(None)
    final_chat_id = frame.active_chat_id
    assert final_chat_id != new_chat_id
    assert {new_chat_id, final_chat_id}.issubset(set(frame.history_ids))
    assert frame.history_ids[frame.history_list.GetSelection()] == final_chat_id
    assert frame.input_edit.HasFocus()
    ok, message = frame._submit_question("another first question", source="local", model=main.DEFAULT_CODEX_MODEL)
    wx_app.Yield()

    assert (ok, message) == (True, "")
    assert len(frame.active_session_turns) == 1
    assert len(starts) == 3
    assert frame.input_edit.HasFocus()
    frame._refresh_history(final_chat_id)
    assert frame.history_list.GetCount() == len(frame.history_ids)
    assert len(frame.history_ids) == len(set(frame.history_ids))


def test_native_first_accepted_send_renames_only_its_immediate_empty_row(frame, wx_app, monkeypatch):
    frame.Show()
    monkeypatch.setattr(frame, "_start_codex_worker_for_turn", lambda *args, **kwargs: True)
    monkeypatch.setattr(frame, "_generate_first_question_title", lambda _question: "generated title")
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda *args, **kwargs: None)

    class _ImmediateThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            self.target, self.args, self.kwargs = target, args, kwargs or {}

        def start(self):
            self.target(*self.args, **self.kwargs)

    monkeypatch.setattr(main.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(main, "wx_call_after_if_alive", lambda fn, *args: fn(*args))

    assert frame._on_new_chat_clicked(None) is True
    owner = frame.active_chat_id
    assert frame.history_ids[frame.history_list.GetSelection()] == owner
    assert frame.input_edit.HasFocus()

    assert frame._submit_question("first accepted question", model=main.DEFAULT_CODEX_MODEL) == (True, "")
    wx_app.Yield()

    assert frame._current_chat_state["title"] == "generated title"
    assert frame._current_chat_state["title_revision"] == 2
    assert frame.chat_store.load_chat(owner)["title"] == "generated title"
    assert frame.history_ids[frame.history_list.GetSelection()] == owner
    assert frame.input_edit.HasFocus()


def test_ui_automation_space_from_history_moves_focus_to_input_without_activating_history(frame, wx_app, monkeypatch):
    frame.Show()
    frame.Raise()
    ctypes.WinDLL("user32", use_last_error=True).SetForegroundWindow(int(frame.GetHandle()))
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame._current_chat_state = {"id": "chat-active", "title": "active", "turns": []}
    frame.archived_chats = [{"id": "chat-older", "title": "older", "turns": [], "created_at": 1.0, "updated_at": 1.0}]
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    frame._refresh_history("chat-active")
    selected_before = frame.history_ids.index("chat-older")
    frame.history_list.SetSelection(selected_before)
    frame.input_edit.SetFocus()
    wx_app.Yield()
    for _ in range(8):
        if _focused_control() is frame.history_list:
            break
        assert _focused_control().Navigate(main.wx.NavigationKeyEvent.IsForward)
        wx_app.Yield()
    assert _focused_control() is frame.history_list
    monkeypatch.setattr(frame, "_activate_selected_history", lambda: pytest.fail("Space must not activate history"))

    _send_listbox_key(frame.history_list, main.wx.WXK_SPACE)
    wx_app.Yield()

    assert _focused_control() is frame.input_edit
    assert frame.history_list.GetSelection() == selected_before
    assert frame.view_mode == "active"


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


class _AltAEvent:
    def __init__(self, event_object=None):
        self._event_object = event_object

    def GetKeyCode(self):
        return ord("A")

    def GetEventObject(self):
        return self._event_object

    def ShiftDown(self):
        return False

    def ControlDown(self):
        return False

    def AltDown(self):
        return True

    def Skip(self):
        raise AssertionError("Alt+A should clear the visible chat context")


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


def test_ui_automation_alt_a_clears_visible_history_chat_without_clearing_running_active_chat(frame, wx_app, monkeypatch, wait_for_wx):
    frame.Show()
    frame.active_chat_id = "chat-a"
    frame.current_chat_id = "chat-a"
    frame.active_session_turns = [
        {
            "question": "A running question",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL,
            "request_status": "pending",
            "created_at": 2.0,
        }
    ]
    frame.active_codex_thread_id = "thread-a"
    frame.active_codex_turn_active = True
    frame._current_chat_state = {
        "id": "chat-a",
        "title": "chat a",
        "turns": frame.active_session_turns,
        "codex_thread_id": "thread-a",
        "codex_turn_active": True,
        "execution_steps": [{"list_text": "A execution remains"}],
        "created_at": 2.0,
        "updated_at": 2.0,
    }
    frame.archived_chats = [
        {
            "id": "chat-b",
            "title": "chat b",
            "turns": [
                {
                    "question": "B old question",
                    "answer_md": "B old answer",
                    "model": main.DEFAULT_CODEX_MODEL,
                    "created_at": 1.0,
                }
            ],
            "codex_thread_id": "thread-b",
            "codex_turn_active": True,
            "execution_steps": [{"list_text": "B execution clears"}],
            "created_at": 1.0,
            "updated_at": 1.0,
        }
    ]
    frame._mark_chat_metadata_dirty(frame.archived_chats[0])
    frame._save_state()
    monkeypatch.setattr(frame, "_start_codex_worker_for_turn", lambda *args: None)
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_state", lambda *args, **kwargs: None)

    assert frame._show_history_chat("chat-b")
    assert frame.view_mode == "history"
    assert frame.view_history_id == "chat-b"
    assert any("B old question" in frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount()))
    frame.answer_list.SetFocusFromKbd()
    wx_app.Yield()

    frame._on_char_hook(_AltAEvent(frame.answer_list))
    wait_for_wx(lambda: not frame._shortcut_clear_pending, "archived keyboard clear")

    archived = frame._find_archived_chat("chat-b")
    assert frame.active_chat_id == "chat-a"
    assert frame.current_chat_id == "chat-a"
    assert frame.active_session_turns[0]["question"] == "A running question"
    assert frame.active_codex_thread_id == "thread-a"
    assert frame.active_codex_turn_active is True
    assert len(archived["turns"]) == 1
    assert archived["turns"][0]["question"] == "B old question"
    assert archived["turns"][0]["clear_operation_id"]
    assert archived["codex_thread_id"] == ""
    assert archived["codex_turn_active"] is False
    assert archived["execution_steps"] == []
    rows = list(frame.answer_list.GetStrings())
    assert "B old answer" not in rows
    assert rows[-1] == "以开启新会话"

    assert frame._show_history_chat("chat-a")
    active_rows = list(frame.answer_list.GetStrings())
    assert "以开启新会话" not in active_rows
    assert any("A running question" in row for row in active_rows)


def test_ui_automation_worker_notice_stays_with_cleared_history_chat(frame, monkeypatch):
    chat_a = {
        "id": "chat-a",
        "title": "A",
        "turns": [{"question": "A", "answer_md": "running"}],
        "codex_thread_id": "thread-a",
    }
    chat_c = {
        "id": "chat-c",
        "title": "C",
        "turns": [{"question": "C", "answer_md": "old"}],
        "codex_thread_id": "thread-c",
    }
    frame.archived_chats = [chat_a, chat_c]
    frame.view_mode = "history"
    frame.view_history_id = "chat-c"
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_state", lambda *args, **kwargs: None)

    assert frame._clear_context_and_start_new_chat() is True

    assert chat_c["codex_thread_id"] == ""
    assert chat_a["codex_thread_id"] == "thread-a"
    assert frame._answer_list_tail_notice_chat_id == "chat-c"


def test_ui_automation_background_events_do_not_mutate_lists_during_reader_quiet(frame, wx_app, monkeypatch):
    frame.Show()
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {
            "question": "q",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": 1.0,
            "request_status": "pending",
            "codex_turn_id": "turn-1",
        }
    ]
    frame._current_chat_state = {
        "id": "chat-active",
        "turns": frame.active_session_turns,
        "detail_panel_mode": "answers",
        "execution_steps": [],
        "codex_turn_id": "turn-1",
    }
    frame._render_answer_list()
    frame.answer_list.SetFocus()
    wx_app.Yield()
    focused_before = _focused_control()

    frame._touch_navigation_quiet_window(_AltAEvent(frame.answer_list))
    forbidden = []
    monkeypatch.setattr(frame.answer_list, "Refresh", lambda *args, **kwargs: forbidden.append("answer.Refresh"))
    monkeypatch.setattr(frame.answer_list, "SetSelection", lambda *args, **kwargs: forbidden.append("answer.SetSelection"))
    monkeypatch.setattr(frame.execution_list, "Refresh", lambda *args, **kwargs: forbidden.append("execution.Refresh"))
    monkeypatch.setattr(frame.execution_list, "Append", lambda *args, **kwargs: forbidden.append("execution.Append"))

    frame._background_ui_update_depth = 1
    frame._on_codex_event_for_chat(
        "chat-active",
        main.CodexEvent(type="subagent_result", turn_id="turn-1", text="background answer"),
    )
    frame._on_codex_event_for_chat(
        "chat-active",
        main.CodexEvent(type="item_started", turn_id="turn-1", status="commandExecution", data={"title": "run tests"}),
    )

    assert forbidden == []
    assert _focused_control() is focused_before


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
        frame.tool_combo,
        frame.model_combo,
        frame.reasoning_combo,
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
    assert frame.answer_meta[0][0] == "time"
    assert rows[1:5] == ["我", "history question", "小诸葛", "history answer"]
    assert frame.answer_meta[1][0] == "user"
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


def test_mc_history_excludes_old_and_new_terminal_sessions(frame, wx_app, monkeypatch):
    import json
    from native_history import NativeSessionIdentity, NativeSessionSnapshot
    from remote_nats import RemoteNatsTransport

    store = frame.chat_store
    imported = NativeSessionSnapshot(
        NativeSessionIdentity("default", "codex", "storage", "terminal-session"),
        "account", main.DEFAULT_CODEX_MODEL, "", "terminal", 20,
    )
    imported_id = imported.identity.chat_id
    store.merge_native_history(imported, expected_chat_id=imported_id)
    store.upsert_chat({**store.load_chat_summary(imported_id), "pinned": True})
    terminal_turn = {"question": "terminal question", "answer_md": "terminal answer", "request_status": "done"}
    store.replace_turns(imported_id, [terminal_turn])
    imported_before = store.load_chat_summary(imported_id)
    terminal_turns_before = store.load_turns(imported_id)
    continued = NativeSessionSnapshot(
        NativeSessionIdentity("default", "codex", "storage", "continued-session"),
        "account", main.DEFAULT_CODEX_MODEL, "", "continued", 10,
        chat_fields={"codex_thread_id": "continued-session", "codex_storage_id": "storage"},
        legacy_fields={"codex_thread_id": "continued-session"},
    )
    store.upsert_chat({"id": "mc-continued", "title": "continued", "model": main.DEFAULT_CODEX_MODEL,
                       "codex_thread_id": "continued-session", "codex_storage_id": "storage", "updated_at": 10})
    store.upsert_chat({"id": "mc-local", "title": "local", "updated_at": 5})
    frame.state_path.write_text(json.dumps({"hide_terminal_chats": False, "active_chat_id": imported_id,
        "active_chat": {"id": imported_id}, "active_codex_thread_id": "terminal-session"}), encoding="utf-8")
    frame._load_state()
    frame._refresh_history()
    frame.Show()
    frame.history_list.SetFocusFromKbd()
    wx_app.Yield()

    assert frame.history_ids == ["mc-continued", "mc-local"]
    assert frame.active_chat_id != imported_id and not frame.active_codex_thread_id
    assert frame._history_chat_for_id(imported_id) is None
    assert not frame._show_history_chat(imported_id)
    assert store.load_chat_summary(imported_id)["pinned"]
    assert all(item.GetItemLabelText() != "隐藏终端聊天"
               for item in frame.GetMenuBar().GetMenu(0).GetMenuItems())
    menu_labels = []
    monkeypatch.setattr(frame, "PopupMenu",
        lambda menu, *_: menu_labels.extend(item.GetItemLabelText() for item in menu.GetMenuItems()))
    frame._show_tools_menu()
    assert "隐藏终端聊天" not in menu_labels
    monkeypatch.setattr(frame, "_announce_chat_navigation", lambda *_: None)
    frame.history_list.SetSelection(0)
    frame.history_list.SetFocusFromKbd()
    wx_app.Yield()
    focus_before = _focused_control()
    frame.ProcessEvent(main.wx.CommandEvent(main.wx.EVT_MENU.typeId, int(frame._chat_navigation_right_id)))
    assert frame.view_history_id == "mc-local"
    assert _focused_control() is focus_before

    new = NativeSessionSnapshot(
        NativeSessionIdentity("default", "codex", "storage", "new-session"),
        "account", main.DEFAULT_CODEX_MODEL, "", "new terminal", 30,
    )
    reads = []
    monkeypatch.setattr(frame, "_run_remote_ui_route", lambda callback, *args, **kwargs: callback())
    monkeypatch.setattr(frame, "_call_after_if_alive", lambda callback, *args: callback(*args))
    frame._scan_native_history([(lambda: [imported, continued, new], reads.append)])
    assert reads == [continued]
    assert store.load_chat_summary("mc-continued")["native_identity"] == continued.identity.chat_id
    assert store.load_chat_summary(new.identity.chat_id) is None
    assert frame.history_ids == ["mc-continued", "mc-local"]

    assert {chat["chat_id"] for chat in frame._remote_api_history_list_ui()[1]["chats"]} == {"mc-continued", "mc-local"}
    payload = {"collection_page": True, "limit": 1}
    first = frame._remote_api_history_collection_worker(payload)[1]
    assert [chat["chat_id"] for chat in first["chats"]] == ["mc-continued"]
    second = frame._remote_api_history_collection_worker({**payload, "cursor": first["next_cursor"]})[1]
    assert [chat["chat_id"] for chat in second["chats"]] == ["mc-local"]
    assert not second["has_more"]
    legacy_target = {"chat_id": imported_id, "model": main.DEFAULT_CODEX_MODEL}
    assert frame._remote_api_history_read_ui(legacy_target)[0] == 404
    assert frame._remote_api_state_ui(legacy_target)[0] == 404
    assert frame._remote_api_message_ui({**legacy_target, "text": "stale RC question"})[0] == 404
    assert frame._remote_api_new_chat_ui(legacy_target)[0] == 409
    assert frame._remote_api_new_chat({**legacy_target, "model": "agy/main"})[0] == 409
    assert not frame._remote_attachment_scope({**legacy_target, "_attachment_source": ("default", "rc")}, "send-id")
    assert frame._remote_api_execution_page_ui(legacy_target, secret="isolated")[0] == 404
    transport = RemoteNatsTransport(pair_id="default", token="isolated", durable_store=store)
    for kind in ("content_page", "execution_tail", "mark_chat_read"):
        assert transport._route_command({**legacy_target, "type": kind})[0] == 404
    assert store.load_chat_summary(imported_id) == imported_before
    assert store.load_turns(imported_id) == terminal_turns_before
    frame.active_chat_id = frame.current_chat_id = "mc-local"
    frame._current_chat_state = {**store.load_chat_summary("mc-local"), "title": "live local title"}
    frame._invalidate_remote_history_list_cache()
    legacy_rows = frame._remote_api_history_list_ui()[1]["chats"]
    assert [row["chat_id"] for row in legacy_rows].count("mc-local") == 1
    assert legacy_rows[0]["title"] == "live local title"
    frame.active_chat_id = frame.current_chat_id = ""
    frame._current_chat_state = {}
    frame._save_state()
    assert "hide_terminal_chats" not in frame.state_path.read_text(encoding="utf-8")
    monkeypatch.setattr(frame, "_confirm", lambda *_: True)
    frame._history_clear_non_pinned(None)
    assert frame.history_ids == []
    assert store.load_chat_summary(imported_id)["pinned"]


def test_delayed_state_save_only_upserts_changed_owners(frame, wx_app, monkeypatch, wait_for_wx_main_loop):
    store = frame.chat_store
    with store._connect() as conn:
        for index in range(1435):
            store._upsert_chat_conn(conn, {"id": f"archive-{index}", "title": f"archive {index}",
                                           "updated_at": index})
    frame.archived_chats = store.list_chat_summaries(mc_owned_only=True)
    frame.Show()
    frame._refresh_history()
    frame.history_list.SetSelection(0)
    frame.history_list.SetFocusFromKbd()
    wx_app.Yield()
    unchanged = {row["id"] for row in frame.archived_chats}
    changed = {"archive-0", "archive-1"}
    writes = []
    real_upsert = store.upsert_chat
    monkeypatch.setattr(store, "upsert_chat", lambda row: (writes.append(row["id"]), real_upsert(row))[1])

    frame._on_new_chat_clicked(None)
    assert not unchanged.intersection(writes)
    frame.history_list.SetFocusFromKbd()
    wx_app.Yield()
    callbacks = []
    key_enqueued_at = []
    key_delay = []
    frame.history_list.Bind(main.wx.EVT_KEY_DOWN,
        lambda event: (callbacks.append("keyboard"), key_delay.append(time.perf_counter() - key_enqueued_at[0]),
                       event.Skip()))
    for owner in changed:
        row = frame._find_archived_chat(owner)
        row["title"] = f"updated {owner}"
        frame._mark_chat_metadata_dirty(row)
    save = frame._save_state
    def observe_save(*args, **kwargs):
        key_enqueued_at.append(time.perf_counter())
        main.wx.CallAfter(_send_listbox_key, frame.history_list, main.wx.WXK_DOWN)
        result = save(*args, **kwargs)
        callbacks.append("saved")
        return result
    monkeypatch.setattr(frame, "_save_state", observe_save)
    main.wx.CallAfter(frame._defer_chat_state_save)
    wait_for_wx_main_loop(lambda: "keyboard" in callbacks, "300 ms history save and following wx callback")
    assert changed == unchanged.intersection(writes)
    assert callbacks[:2] == ["saved", "keyboard"]
    assert key_delay[0] < .25
    fresh = type(store)(store.db_path)
    assert {owner: fresh.load_chat_summary(owner)["title"] for owner in changed} == {
        owner: f"updated {owner}" for owner in changed}


def test_repeated_long_answer_render_keeps_selection_and_skips_conversion(frame, monkeypatch):
    source = "## Heading\n\n" + ("**important** [file](D:/example/source.py)\n" * 650)
    frame.active_session_turns = [{"model": main.DEFAULT_CODEX_MODEL, "question": f"question {index}",
        "answer_md": f"{source}\nitem {index}", "request_status": "done"} for index in range(12)]
    convert = main.md_to_plain
    conversions = []
    monkeypatch.setattr(main, "md_to_plain", lambda text: (conversions.append(text), convert(text))[1])
    frame._render_answer_list()
    assert len(conversions) == 12
    frame.answer_list.SetSelection(frame.answer_list.GetCount() - 1)
    selected = frame.answer_list_model.selected_id()
    frame._render_answer_list()
    assert len(conversions) == 12
    assert frame.answer_list_model.selected_id() == selected
    assert frame.answer_meta[frame.answer_list.GetSelection()][3] == frame.active_session_turns[-1]["answer_md"]
    frame.codex_answer_english_filter_enabled = not frame.codex_answer_english_filter_enabled
    frame._render_answer_list()
    assert len(conversions) == 24
    conversions.clear()
    monkeypatch.setattr(main, "md_to_plain", lambda text: conversions.append(text) or text)
    frame.active_session_turns = [{"model": main.DEFAULT_CODEX_MODEL, "answer_md": f"answer {index}",
                                   "request_status": "done"} for index in range(300)]
    frame.answer_visible_row_limit = 300
    frame._render_answer_list()
    frame._render_answer_list()
    assert len(conversions) == 300
    frame.active_chat_id = "another-preview-owner"
    frame.active_session_turns = []
    frame._render_answer_list()
    assert len(frame._answer_preview_cache) == 0
    assert frame._answer_preview_cache_limit == 256


@pytest.mark.parametrize("codex", [False, True])
def test_delayed_state_save_retries_sqlite_busy_after_servicing_keyboard(
        frame, monkeypatch, wait_for_wx_main_loop, codex):
    import sqlite3

    owner = frame.active_chat_id = frame.current_chat_id = "busy-delayed-save"
    frame._current_chat_state = {"id": owner, "title": "before lock"}
    frame._save_state()
    frame._current_chat_state["title"] = "after lock"
    frame.Show()
    frame._refresh_history()
    keyboard = []
    frame.history_list.Bind(main.wx.EVT_KEY_DOWN,
        lambda event: (keyboard.append(time.perf_counter()), event.Skip()))
    if codex:
        frame._codex_background_flush_dirty = True
        flush = frame._flush_codex_background_updates
    else:
        frame._chat_state_flush_dirty = True
        flush = frame._flush_chat_state_save

    connection = sqlite3.connect(frame.chat_store.db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        started = time.perf_counter()
        main.wx.CallAfter(flush)
        main.wx.CallAfter(_send_listbox_key, frame.history_list, main.wx.WXK_DOWN)
        wait_for_wx_main_loop(lambda: bool(keyboard), "native key before releasing the SQLite write lock", timeout=1)
        assert keyboard[0] - started < .25
        assert frame.chat_store.load_chat_summary(owner)["title"] == "before lock"
    finally:
        connection.rollback()
        connection.close()
    wait_for_wx_main_loop(lambda: frame.chat_store.load_chat_summary(owner)["title"] == "after lock",
                          "automatically retried SQLite save")


def test_answer_read_sqlite_writer_lock_leaves_wx_keyboard_available(frame, wx_app, wait_for_wx):
    import sqlite3

    owner = frame.active_chat_id = frame.current_chat_id = "read-lock-owner"
    frame._current_chat_state = {"id": owner, "title": "Read lock"}
    frame.active_session_turns = [{"question": "question", "answer_md": "answer", "request_status": "done"}]
    frame._persist_chat_history_to_store()
    frame.Show()
    frame._refresh_history()
    frame._render_answer_list()
    target = next(target for index in range(len(frame.answer_meta)) if (target := frame._selected_read_target(index)))
    frame.history_list.SetFocusFromKbd()
    wx_app.Yield()
    focus_before = _focused_control()
    keyboard = []
    frame.history_list.Bind(main.wx.EVT_KEY_DOWN, lambda event: (keyboard.append(event.GetKeyCode()), event.Skip()))
    database = sqlite3.connect(frame.chat_store.db_path)
    try:
        database.execute("BEGIN IMMEDIATE")
        frame._confirm_answer_read(target)
        main.wx.CallAfter(_send_listbox_key, frame.history_list, main.wx.WXK_DOWN)
        wait_for_wx(lambda: bool(keyboard), "native key before release of the SQLite write lock")
        assert _focused_control() is focus_before
        assert frame._answer_read_writer_running
        assert frame.chat_store.get_chat_read_state(owner)["read_seq"] == 0
    finally:
        database.rollback()
        database.close()
    wait_for_wx(lambda: not frame._answer_read_writer_running and
        frame.chat_store.get_chat_read_state(owner)["read_seq"] == target["answer_seq"], "persisted answer read")


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
    starts = []
    payloads = []

    class _Client:
        def start(self):
            starts.append(True)

        def start_turn(self, **payload):
            payloads.append(payload)
            return "req-1"

    monkeypatch.setattr(frame, "_get_or_create_codex_client", lambda chat_id, model="": _Client())
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    frame._render_answer_list()
    frame.answer_list.SetFocusFromKbd()
    before_rows = list(frame.answer_list.GetStrings())

    frame._run_codex_turn_worker("chat-a", 0, "question b", main.DEFAULT_CODEX_MODEL)

    after_rows = list(frame.answer_list.GetStrings())
    assert starts == [True]
    assert len(payloads) == 1
    assert payloads[0]["chat_id"] == "chat-a"
    assert payloads[0]["thread_id"] in {"", None}
    assert frame.active_codex_thread_id == "thread-c"
    assert frame.active_codex_turn_id == "turn-c"
    assert frame._current_chat_state["codex_thread_id"] == "thread-c"
    assert frame._current_chat_state["codex_turn_id"] == "turn-c"
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


def test_ui_automation_execution_reentry_reconciles_stale_tail_and_ignores_old_chat(frame, monkeypatch):
    frame.Show()
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {"question": "current question", "answer_md": main.REQUESTING_TEXT, "model": main.DEFAULT_CODEX_MODEL},
    ]
    current_step = {
        "id": "current-step",
        "turn_idx": 0,
        "display_kind": "commentary",
        "list_text": "current execution",
        "detail_text": "current execution",
    }
    frame._current_chat_state.update(
        {
            "id": "chat-current",
            "turns": frame.active_session_turns,
            "detail_panel_mode": "execution",
            "execution_steps": [current_step],
        }
    )
    frame.archived_chats = [
        {
            "id": "chat-old",
            "turns": [{"question": "old question", "answer_md": "old answer", "model": main.DEFAULT_CODEX_MODEL}],
            "execution_steps": [],
        }
    ]
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda *args, **kwargs: None)

    frame._apply_detail_panel_mode("execution", refresh_execution=True)
    frame.execution_list.Append("stale visible tail")
    frame.execution_meta.append(("execution", 99, "stale visible tail", "stale visible tail"))
    frame._append_execution_entry_to_chat(
        "chat-old",
        {"id": "old-step", "turn_idx": 0, "display_kind": "commentary", "list_text": "old execution", "detail_text": "old execution"},
    )

    frame._apply_detail_panel_mode("answers", refresh_execution=False)
    frame._apply_detail_panel_mode("execution", refresh_execution=True)

    rows = list(frame.execution_list.GetStrings())
    assert "current execution" in rows
    assert "stale visible tail" not in rows
    assert "old execution" not in rows
    assert len(rows) == len(frame.execution_meta)


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

    # created_at=1.0 is a valid Unix timestamp; only absent/invalid times use UNKNOWN_TIME_LABEL.
    assert rows == [main.wechat_time_label(1.0, time.time()), "我：please fix tests"]

def test_ui_automation_history_execution_list_excludes_active_chat_turn_context(frame, wx_app, monkeypatch):
    frame.Show()
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_mark_openclaw_lifecycle_dirty", lambda: None)

    frame.archived_chats = [
        {
            "id": "chat-a",
            "title": "chat a",
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": 1.0,
            "updated_at": 1.0,
            "detail_panel_mode": "execution",
            "turns": [
                {
                    "question": "A 的提问",
                    "answer_md": "A 的回答",
                    "model": main.DEFAULT_CODEX_MODEL,
                    "created_at": 1.0,
                }
            ],
            "execution_steps": [
                {
                    "display_kind": "commandExecution",
                    "list_text": "A 的执行步骤",
                    "detail_text": "A 的执行步骤",
                    "turn_idx": 0,
                }
            ],
        }
    ]

    frame.archived_chats[0]["turns"].append(
        {"question": "latest history q", "answer_md": "latest history a", "created_at": 3.0}
    )

    frame._on_new_chat_clicked(None)
    frame.active_session_turns.append(
        {
            "question": "B 的提问",
            "answer_md": "B 的回答",
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": 2.0,
        }
    )
    frame.active_turn_idx = 0

    assert frame._show_history_chat("chat-a")
    frame._apply_detail_panel_mode("execution", refresh_execution=True)
    history_rows = list(frame.execution_list.GetStrings())
    assert not any("A 的提问" in row for row in history_rows)
    assert sum("latest history q" in row for row in history_rows) == 1
    assert sum("latest history a" in row for row in history_rows) == 1
    assert not any("B 的提问" in row or "B 的回答" in row for row in history_rows)

    assert frame._show_history_chat(frame.active_chat_id)
    frame._apply_detail_panel_mode("execution", refresh_execution=True)
    active_rows = list(frame.execution_list.GetStrings())
    assert any("B 的提问" in row for row in active_rows)
