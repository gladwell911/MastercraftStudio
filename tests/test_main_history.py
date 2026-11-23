import copy
import main
import pytest
import time
import wx
from main_test_support import _prepare_switched_chat_final, _switched_chat_final_event
from pathlib import Path
from types import SimpleNamespace


def test_background_history_chat_event_defers_visible_refresh_during_quiet(frame, monkeypatch):
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.view_mode = "history"
    frame.view_history_id = "chat-history"
    frame.archived_chats = [
        {
            "id": "chat-history",
            "title": "history",
            "turns": [
                {
                    "question": "q",
                    "answer_md": main.REQUESTING_TEXT,
                    "model": main.DEFAULT_CODEX_MODEL,
                    "created_at": 1.0,
                    "request_status": "pending",
                    "codex_turn_id": "turn-1",
                }
            ],
            "execution_steps": [],
            "codex_turn_id": "turn-1",
        }
    ]
    frame._background_ui_update_depth = 1
    frame._navigation_quiet_until = time.monotonic() + 3.0
    monkeypatch.setattr(frame, "_refresh_history", lambda *_args, **_kwargs: pytest.fail("history refresh must be deferred during quiet"))
    monkeypatch.setattr(
        frame,
        "_refresh_answer_list_preserving_selection",
        lambda *_args, **_kwargs: pytest.fail("history answer refresh must be deferred during quiet"),
    )

    event = main.CodexEvent(type="subagent_result", turn_id="turn-1", text="history answer")
    frame._on_codex_event_for_chat("chat-history", event)

    archived = frame._find_archived_chat("chat-history")
    assert archived["turns"][0]["answer_md"] == "history answer"
    assert frame._background_answer_list_dirty is True
    assert "chat-history" in frame._background_history_dirty_ids
    assert frame._history_list_dirty is True
    assert frame._pending_history_keep_id is None


def test_idle_history_flush_waits_for_navigation_quiet_window(frame, monkeypatch):
    frame._history_list_dirty = True
    frame._pending_history_keep_id = "chat-old"
    frame._navigation_quiet_until = time.monotonic() + 3.0
    delayed = []
    monkeypatch.setattr(frame, "_refresh_history", lambda *args, **kwargs: pytest.fail("quiet window should defer idle history refresh"))
    monkeypatch.setattr(frame, "_call_later_if_alive", lambda delay_ms, fn, *args: delayed.append((delay_ms, fn, args)) or object())

    frame._flush_idle_ui_refreshes()

    assert frame._history_list_dirty is True
    assert frame._pending_history_keep_id == "chat-old"
    assert delayed
    delay_ms, fn, args = delayed[-1]
    assert 1 <= delay_ms <= 3000
    assert fn == frame._flush_idle_ui_refreshes
    assert args == ()


def test_ctrl_history_navigation_keeps_focus_on_origin_control(frame):
    frame.active_chat_id = "chat-b"
    frame.current_chat_id = "chat-b"
    frame._current_chat_state["id"] = "chat-b"
    frame._current_chat_state["title"] = "聊天B"
    frame._current_chat_state["turns"] = [{"question": "当前问题", "answer_md": "当前回答"}]
    frame._current_chat_state["updated_at"] = 2.0
    frame.archived_chats = [
        {"id": "chat-a", "title": "聊天A", "turns": [], "created_at": 1.0, "updated_at": 1.0},
        {"id": "chat-c", "title": "聊天C", "turns": [], "created_at": 3.0, "updated_at": 3.0},
    ]
    frame._refresh_history()
    frame.input_edit.SetFocus()

    class E:
        def __init__(self):
            self.skipped = 0

        def GetKeyCode(self):
            return wx.WXK_DOWN

        def ControlDown(self):
            return True

        def AltDown(self):
            return False

        def Skip(self):
            self.skipped += 1

    event = E()
    frame._on_input_key_down(event)

    assert event.skipped == 0
    assert frame.input_edit.HasFocus()
    assert frame.view_mode == "history"
    assert frame.view_history_id == "chat-a"
    assert frame.current_chat_id == "chat-b"


def test_adjacent_history_chat_id_uses_global_chat_order_including_current_position(frame):
    frame.current_chat_id = "chat-current"
    frame._current_chat_state["id"] = "chat-current"
    frame._current_chat_state["title"] = "当前聊天"
    frame._current_chat_state["updated_at"] = 4.0
    frame.archived_chats = [
        {"id": "chat-a", "title": "聊天A", "turns": [], "created_at": 1.0, "updated_at": 1.0},
        {"id": "chat-b", "title": "聊天B", "turns": [], "created_at": 2.0, "updated_at": 2.0},
        {"id": "chat-c", "title": "聊天C", "turns": [], "created_at": 3.0, "updated_at": 3.0},
    ]
    frame._refresh_history()

    assert frame._adjacent_history_chat_id(-1) is None
    assert frame._adjacent_history_chat_id(1) == "chat-c"


def test_adjacent_history_chat_id_keeps_advancing_after_switch(frame):
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

    assert frame._switch_current_chat("chat-c") is True
    assert frame.current_chat_id == "chat-c"
    assert frame._adjacent_history_chat_id(1) == "chat-b"


def test_refresh_history_keeps_switched_chat_in_sorted_position(frame):
    frame.current_chat_id = "chat-current"
    frame.active_chat_id = "chat-current"
    frame._current_chat_state["id"] = "chat-current"
    frame._current_chat_state["title"] = "当前聊天"
    frame._current_chat_state["turns"] = [{"question": "当前问题", "answer_md": "当前回答"}]
    frame._current_chat_state["updated_at"] = 6.0
    frame.archived_chats = [
        {"id": "chat-b", "title": "聊天B", "turns": [{"question": "B", "answer_md": "B"}], "created_at": 4.0, "updated_at": 4.0},
        {"id": "chat-f", "title": "置顶F", "turns": [{"question": "F", "answer_md": "F"}], "created_at": 5.0, "updated_at": 5.0, "pinned": True},
        {"id": "chat-c", "title": "置顶C", "turns": [{"question": "C", "answer_md": "C"}], "created_at": 3.0, "updated_at": 3.0, "pinned": True},
        {"id": "chat-a", "title": "聊天A", "turns": [{"question": "A", "answer_md": "A"}], "created_at": 2.0, "updated_at": 2.0},
        {"id": "chat-g", "title": "聊天G", "turns": [{"question": "G", "answer_md": "G"}], "created_at": 9.0, "updated_at": 9.0},
    ]

    frame._refresh_history("chat-current")
    assert frame._show_history_chat("chat-b", focus_answer_list=False) is True

    assert frame.history_ids == ["chat-f", "chat-c", "chat-g", "chat-current", "chat-b", "chat-a"]
    assert list(frame.history_list.GetStrings()) == ["[置顶] 置顶F", "[置顶] 置顶C", "聊天G", "当前聊天", "聊天B", "聊天A"]
    assert frame.history_list.GetSelection() == frame.history_ids.index("chat-b")


def test_show_history_chat_selects_existing_row_without_refreshing_history(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state["id"] = "chat-current"
    frame._current_chat_state["title"] = "当前聊天"
    frame._current_chat_state["turns"] = [{"question": "当前问题", "answer_md": "当前回答"}]
    frame.archived_chats = [
        {"id": "chat-a", "title": "聊天A", "turns": [{"question": "A", "answer_md": "A"}], "created_at": 1.0, "updated_at": 1.0},
        {"id": "chat-b", "title": "聊天B", "turns": [{"question": "B", "answer_md": "B"}], "created_at": 2.0, "updated_at": 2.0},
    ]
    frame._refresh_history("chat-current")
    before_ids = list(frame.history_ids)
    before_rows = list(frame.history_list.GetStrings())
    monkeypatch.setattr(frame, "_refresh_history", lambda *args, **kwargs: pytest.fail("switching to an existing history row should not refresh the whole list"))
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)

    assert frame._show_history_chat("chat-b", focus_answer_list=False) is True

    assert frame.view_mode == "history"
    assert frame.view_history_id == "chat-b"
    assert frame.history_ids == before_ids
    assert list(frame.history_list.GetStrings()) == before_rows
    assert frame.history_list.GetSelection() == frame.history_ids.index("chat-b")


def test_ctrl_history_navigation_keeps_history_order_unchanged(frame):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state["id"] = "chat-current"
    frame._current_chat_state["title"] = "当前聊天"
    frame._current_chat_state["turns"] = [{"question": "当前问题", "answer_md": "当前回答"}]
    frame._current_chat_state["updated_at"] = 6.0
    frame.archived_chats = [
        {"id": "chat-b", "title": "聊天B", "turns": [], "created_at": 4.0, "updated_at": 4.0},
        {"id": "chat-f", "title": "置顶F", "turns": [], "created_at": 5.0, "updated_at": 5.0, "pinned": True},
        {"id": "chat-c", "title": "置顶C", "turns": [], "created_at": 3.0, "updated_at": 3.0, "pinned": True},
        {"id": "chat-a", "title": "聊天A", "turns": [], "created_at": 2.0, "updated_at": 2.0},
        {"id": "chat-g", "title": "聊天G", "turns": [], "created_at": 9.0, "updated_at": 9.0},
    ]
    frame._refresh_history()
    before_ids = list(frame.history_ids)
    before_rows = list(frame.history_list.GetStrings())
    frame.input_edit.SetFocus()

    class E:
        def __init__(self):
            self.skipped = 0

        def GetKeyCode(self):
            return wx.WXK_DOWN

        def ControlDown(self):
            return True

        def AltDown(self):
            return False

        def Skip(self):
            self.skipped += 1

    event = E()
    frame._on_input_key_down(event)

    assert event.skipped == 0
    assert frame.view_mode == "history"
    assert frame.view_history_id == "chat-b"
    assert frame.history_ids == before_ids
    assert list(frame.history_list.GetStrings()) == before_rows
    assert frame.history_list.GetSelection() == frame.history_ids.index("chat-b")


def test_adjacent_history_chat_id_uses_selected_history_when_no_current_chat(frame):
    frame.current_chat_id = None
    frame.active_chat_id = ""
    frame.archived_chats = [
        {"id": "chat-b", "title": "聊天B", "turns": [], "created_at": 4.0, "updated_at": 4.0},
        {"id": "chat-f", "title": "置顶F", "turns": [], "created_at": 5.0, "updated_at": 5.0, "pinned": True},
        {"id": "chat-c", "title": "置顶C", "turns": [], "created_at": 3.0, "updated_at": 3.0, "pinned": True},
        {"id": "chat-a", "title": "聊天A", "turns": [], "created_at": 2.0, "updated_at": 2.0},
        {"id": "chat-g", "title": "聊天G", "turns": [], "created_at": 9.0, "updated_at": 9.0},
    ]
    frame._refresh_history()
    frame.history_list.SetSelection(frame.history_ids.index("chat-a"))

    assert frame._adjacent_history_chat_id(1) is None


def test_adjacent_history_chat_id_stops_at_boundaries_without_wrap(frame):
    frame.current_chat_id = "chat-current"
    frame.active_chat_id = "chat-current"
    frame._current_chat_state["id"] = "chat-current"
    frame._current_chat_state["title"] = "当前聊天"
    frame._current_chat_state["updated_at"] = 10.0
    frame.archived_chats = [
        {"id": "chat-b", "title": "聊天B", "turns": [], "created_at": 4.0, "updated_at": 4.0},
        {"id": "chat-f", "title": "置顶F", "turns": [], "created_at": 5.0, "updated_at": 5.0, "pinned": True},
        {"id": "chat-c", "title": "置顶C", "turns": [], "created_at": 3.0, "updated_at": 3.0, "pinned": True},
        {"id": "chat-a", "title": "聊天A", "turns": [], "created_at": 2.0, "updated_at": 2.0},
        {"id": "chat-g", "title": "聊天G", "turns": [], "created_at": 9.0, "updated_at": 9.0},
    ]
    frame._refresh_history()

    assert frame._adjacent_history_chat_id(-1) == "chat-c"
    assert frame._show_history_chat("chat-a", focus_answer_list=False) is True
    assert frame._adjacent_history_chat_id(1) is None


def test_ctrl_history_navigation_stops_at_last_chat_without_wrap(frame):
    frame.current_chat_id = "chat-current"
    frame.active_chat_id = "chat-current"
    frame._current_chat_state["id"] = "chat-current"
    frame._current_chat_state["title"] = "当前聊天"
    frame._current_chat_state["updated_at"] = 10.0
    frame.archived_chats = [
        {"id": "chat-b", "title": "聊天B", "turns": [], "created_at": 4.0, "updated_at": 4.0},
        {"id": "chat-a", "title": "聊天A", "turns": [], "created_at": 2.0, "updated_at": 2.0},
    ]
    frame._refresh_history()
    frame._show_history_chat("chat-a", focus_answer_list=False)

    class E:
        def __init__(self):
            self.skipped = 0

        def GetKeyCode(self):
            return wx.WXK_DOWN

        def ControlDown(self):
            return True

        def AltDown(self):
            return False

        def Skip(self):
            self.skipped += 1

    event = E()
    frame._on_input_key_down(event)

    assert event.skipped == 0
    assert frame.view_mode == "history"
    assert frame.view_history_id == "chat-a"


def test_switch_current_chat_refreshes_answers_list_in_answers_mode(frame):
    frame.current_chat_id = "chat-current"
    frame.active_chat_id = "chat-current"
    frame.view_mode = "active"
    frame._current_chat_state = {
        "id": "chat-current",
        "title": "当前聊天",
        "turns": [],
        "detail_panel_mode": "answers",
        "execution_steps": [],
    }
    frame.active_session_turns = [
        {"question": "当前问题", "answer_md": "当前回答", "model": "codex/main", "created_at": 1.0}
    ]
    frame.archived_chats = [
        {
            "id": "chat-archived",
            "source_chat_id": "chat-archived",
            "title": "旧聊天",
            "turns": [{"question": "历史问题", "answer_md": "历史回答", "model": "codex/main", "created_at": 2.0}],
            "created_at": 2.0,
            "updated_at": 2.0,
            "detail_panel_mode": "answers",
            "execution_steps": [],
        }
    ]

    assert frame._switch_current_chat("chat-archived") is True

    rows = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert any("历史问题" == row for row in rows)
    assert any("历史回答" == row for row in rows)


def test_answer_key_down_ctrl_down_navigates_history_view(frame):
    frame.active_chat_id = "chat-b"
    frame.current_chat_id = "chat-b"
    frame._current_chat_state["id"] = "chat-b"
    frame._current_chat_state["title"] = "聊天B"
    frame._current_chat_state["updated_at"] = 2.0
    frame._current_chat_state["turns"] = [{"question": "问题B", "answer_md": "回答B"}]
    frame.archived_chats = [
        {"id": "chat-a", "title": "聊天A", "turns": [], "created_at": 1.0, "updated_at": 1.0},
        {"id": "chat-c", "title": "聊天C", "turns": [], "created_at": 3.0, "updated_at": 3.0},
    ]
    frame._refresh_history()
    frame._render_answer_list()
    frame.answer_list.SetSelection(0)

    class E:
        def __init__(self):
            self.skipped = 0

        def GetKeyCode(self):
            return wx.WXK_DOWN

        def ControlDown(self):
            return True

        def AltDown(self):
            return False

        def Skip(self):
            self.skipped += 1

    event = E()
    frame._on_answer_key_down(event)

    assert frame.view_mode == "history"
    assert frame.view_history_id == "chat-a"
    assert event.skipped == 0


def test_accelerator_ctrl_down_navigates_history_view_from_button_focus(frame):
    frame.Show()
    frame.active_chat_id = "chat-b"
    frame.current_chat_id = "chat-b"
    frame._current_chat_state["id"] = "chat-b"
    frame._current_chat_state["title"] = "聊天B"
    frame._current_chat_state["updated_at"] = 2.0
    frame.archived_chats = [
        {"id": "chat-a", "title": "聊天A", "turns": [], "created_at": 1.0, "updated_at": 1.0},
        {"id": "chat-c", "title": "聊天C", "turns": [], "created_at": 3.0, "updated_at": 3.0},
    ]
    frame._refresh_history()
    frame.send_button.SetFocus()

    event = wx.CommandEvent(wx.wxEVT_MENU, int(frame._chat_navigation_right_id))
    assert frame.ProcessEvent(event)
    assert frame.view_mode == "history"
    assert frame.view_history_id == "chat-a"


def test_refresh_history_requests_listbox_repaint(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame._current_chat_state["title"] = "当前聊天"
    frame._current_chat_state["turns"] = [{"question": "问题", "answer_md": "回答", "model": "openai/gpt-5.2"}]
    frame.archived_chats = [
        {"id": "chat-old", "title": "旧聊天", "turns": [], "created_at": 1.0, "updated_at": 1.0}
    ]
    seen = {"calls": []}
    monkeypatch.setattr(frame, "_request_listbox_repaint", lambda *controls: seen["calls"].append(controls), raising=False)

    frame._refresh_history()

    assert seen["calls"] == [(frame.history_list,)]


def test_google_chat_remains_visible_in_history_after_done(frame, monkeypatch):
    frame.active_chat_id = "chat-google"
    frame.current_chat_id = "chat-google"
    frame._current_chat_state = {
        "id": "chat-google",
        "title": "新聊天",
        "title_manual": False,
        "turns": frame.active_session_turns,
        "created_at": 1.0,
        "updated_at": 1.0,
    }
    frame.active_session_turns = [
        {
            "question": "解释一下 Gemini 模型",
            "answer_md": main.REQUESTING_TEXT,
            "model": "google/gemini-3.1-pro-preview",
            "created_at": 1.0,
            "request_status": "pending",
        }
    ]
    frame.archived_chats = []
    frame._current_chat_state["turns"] = frame.active_session_turns
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)
    monkeypatch.setattr(frame, "_focus_latest_answer", lambda: None)

    frame._on_done(0, "Gemini 是 Google 的模型", "", "google/gemini-3.1-pro-preview", "", "chat-google")

    choices = list(frame.history_list.GetStrings())
    assert frame.history_ids[0] == "chat-google"
    assert choices[0] == "新聊天"
    assert frame.archived_chats == []


def test_new_chat_uses_incremental_history_projection_and_deferred_full_save(frame, monkeypatch):
    frame.active_chat_id = "chat-old"
    frame.current_chat_id = "chat-old"
    frame.active_session_turns = [
        {"question": "旧问题", "answer_md": "旧回答", "model": "openai/gpt-5.2", "created_at": time.time()}
    ]
    frame._current_chat_state = {
        "id": "chat-old",
        "title": "旧聊天",
        "turns": frame.active_session_turns,
        "updated_at": time.time(),
    }
    delayed = []
    monkeypatch.setattr(frame, "_flush_chat_state_save", lambda: pytest.fail("new chat click should not flush pending state synchronously"))
    monkeypatch.setattr(frame, "_refresh_history", lambda *args, **kwargs: pytest.fail("new chat click should not refresh history synchronously"))
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: pytest.fail("new chat click should not save state synchronously"))
    monkeypatch.setattr(frame, "_call_later_if_alive", lambda delay_ms, fn, *args: delayed.append((delay_ms, fn, args)) or object())
    monkeypatch.setattr(frame.input_edit, "SetFocus", lambda: None)
    monkeypatch.setattr(frame, "SetStatusText", lambda _text: None)
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda: pytest.fail("new chat click should not refresh OpenClaw lifecycle synchronously"))
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda _chat_id="": None)

    frame._on_new_chat_clicked(None)

    assert frame.active_chat_id and frame.active_chat_id != "chat-old"
    assert frame.active_session_turns == []
    assert [chat.get("id") for chat in frame.archived_chats] == ["chat-old"]
    assert frame._history_list_dirty is False
    assert frame.history_ids[frame.history_list.GetSelection()] == frame.active_chat_id
    assert frame._openclaw_lifecycle_dirty is True
    assert any(delay == main.IDLE_UI_REFRESH_DELAY_MS and fn == frame._flush_idle_ui_refreshes for delay, fn, _args in delayed)
    assert frame._chat_state_flush_dirty is True


def test_idle_history_flush_defers_while_primary_control_has_focus(frame, monkeypatch):
    frame._history_list_dirty = True
    frame._pending_history_keep_id = "chat-old"
    delayed = []
    monkeypatch.setattr(frame, "_primary_navigation_control_has_focus", lambda: True)
    monkeypatch.setattr(frame, "_refresh_history", lambda *args, **kwargs: pytest.fail("focused controls should defer idle history refresh"))
    monkeypatch.setattr(frame, "_call_later_if_alive", lambda delay_ms, fn, *args: delayed.append((delay_ms, fn, args)) or object())

    frame._flush_idle_ui_refreshes()

    assert frame._history_list_dirty is True
    assert frame._pending_history_keep_id == "chat-old"
    assert delayed == [(main.IDLE_UI_REFRESH_DELAY_MS, frame._flush_idle_ui_refreshes, ())]


def test_idle_history_flush_runs_when_focused_control_has_been_idle(frame, monkeypatch):
    frame._history_list_dirty = True
    frame._pending_history_keep_id = "chat-old"
    calls = []
    frame._last_primary_interaction_at = main.time.monotonic() - ((main.IDLE_UI_REFRESH_DELAY_MS + 200) / 1000.0)
    monkeypatch.setattr(frame, "_primary_navigation_control_has_focus", lambda: True)
    monkeypatch.setattr(frame, "_refresh_history", lambda keep_id=None: calls.append(keep_id))

    frame._flush_idle_ui_refreshes()

    assert calls == ["chat-old"]
    assert frame._history_list_dirty is False
    assert frame._pending_history_keep_id is None


def test_idle_history_flush_refreshes_when_user_is_idle(frame, monkeypatch):
    frame._history_list_dirty = True
    frame._pending_history_keep_id = "chat-old"
    calls = []
    monkeypatch.setattr(frame, "_primary_navigation_control_has_focus", lambda: False)
    monkeypatch.setattr(frame, "_refresh_history", lambda keep_id=None: calls.append(keep_id))

    frame._flush_idle_ui_refreshes()

    assert calls == ["chat-old"]
    assert frame._history_list_dirty is False
    assert frame._pending_history_keep_id is None


def test_save_load_preserves_active_chat_detail_panel_state(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "resolve_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(main.ChatFrame, "_legacy_state_paths", lambda self: [self.state_path])

    first = main.ChatFrame()
    try:
        first.active_chat_id = "chat-active"
        first.current_chat_id = "chat-active"
        first.active_session_turns = [
            {"question": "活动问题", "answer_md": "活动回答", "model": "openai/gpt-5.2", "created_at": 1.0}
        ]
        first._current_chat_state = {
            "id": "chat-active",
            "title": "活动聊天",
            "turns": first.active_session_turns,
            "detail_panel_mode": "execution",
            "execution_steps": [{"step": "第一步"}],
            "updated_at": 1.0,
        }
        first.archived_chats = [
            {
                "id": "chat-archived",
                "title": "归档聊天",
                "turns": [],
                "created_at": 1.0,
                "updated_at": 1.0,
                "detail_panel_mode": "execution",
                "execution_steps": [{"step": "归档步骤"}],
            }
        ]
        first._save_state()
    finally:
        first.Destroy()

    second = main.ChatFrame()
    try:
        assert second.active_chat_id == "chat-active"
        assert second.current_chat_id == "chat-active"
        assert second._current_chat_state["detail_panel_mode"] == "execution"
        assert second._current_chat_state["execution_steps"] == [{"step": "第一步"}]

        assert second._switch_current_chat("chat-archived") is True
        second._render_answer_list()
        second._render_execution_list()
        rows = [second.execution_list.GetString(i) for i in range(second.execution_list.GetCount())]
        assert rows == ["归档步骤"]
    finally:
        second.Destroy()


def test_switch_current_chat_flushes_active_chat_pending_delta_while_history_view_open(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.view_mode = "history"
    frame.view_history_id = "chat-viewed"
    frame._current_chat_state = {
        "id": "chat-current",
        "title": "当前聊天",
        "turns": [],
        "detail_panel_mode": "answers",
        "execution_steps": [],
    }
    frame.active_session_turns = [{"question": "当前问题", "answer_md": "当前回答", "model": "codex/main", "created_at": 1.0}]
    frame.archived_chats = [
        {
            "id": "chat-viewed",
            "title": "正在查看",
            "turns": [],
            "created_at": 2.0,
            "updated_at": 2.0,
            "detail_panel_mode": "answers",
            "execution_steps": [],
        },
        {
            "id": "chat-target",
            "title": "目标聊天",
            "turns": [],
            "created_at": 3.0,
            "updated_at": 3.0,
            "detail_panel_mode": "answers",
            "execution_steps": [],
        },
    ]
    frame._buffer_execution_delta(
        "chat-current",
        main.CodexEvent(type="agent_message_delta", thread_id="thread-current", turn_id="turn-current", item_id="msg-1", text="切换前先落地"),
    )
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    assert frame._switch_current_chat("chat-target") is True

    archived_current = next(chat for chat in frame.archived_chats if chat["id"] == "chat-current")
    assert archived_current["execution_steps"][0]["detail_text"] == "切换前先落地"
    assert frame._execution_delta_buffer == {}


def test_late_codex_token_count_event_updates_visible_history_state_without_refreshing_context_row(frame, monkeypatch):
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.active_session_turns = [
        {"question": "active", "answer_md": "active", "model": "openai/gpt-5.2", "created_at": 1.0}
    ]
    frame._current_chat_state = {"id": "chat-active", "turns": frame.active_session_turns}
    frame.archived_chats = [
        {
            "id": "chat-visible",
            "title": "正在查看",
            "codex_thread_id": "thread-1",
            "turns": [
                {
                    "question": "q",
                    "answer_md": "done",
                    "model": "codex/main",
                    "created_at": 1.0,
                        "codex_turn_id": "turn-1",
                        "codex_thread_id": "thread-1",
                        "request_status": "done",
                }
            ],
        }
    ]
    frame.view_mode = "history"
    frame.view_history_id = "chat-visible"
    usage = {
        "used_tokens": 44176,
        "context_window": 258400,
        "source": "codex",
        "exact": False,
        "fresh": True,
        "model": "gpt-5-codex",
        "updated_at": 1.0,
    }
    monkeypatch.setattr(frame, "_call_later_if_alive", lambda *_args, **_kwargs: None)

    frame._render_answer_list()
    rows_before = list(frame.answer_list.GetStrings())
    frame.answer_list.SetSelection(0)
    assert frame.answer_meta[0][0] == "time"

    frame._on_codex_event_for_chat(
        "chat-visible",
        main.CodexEvent(type="token_count", thread_id="thread-1", turn_id="turn-1", data={"turn_idx": 0, "context_generation": 0}, usage=usage),
    )

    assert frame._find_archived_chat("chat-visible")["context_usage"] == usage
    assert list(frame.answer_list.GetStrings()) == rows_before
    assert frame.answer_list.GetSelection() == 0
    assert frame.answer_meta[0][0] == "time"


def test_background_final_answer_refreshes_visible_history_view(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.view_mode = "history"
    frame.view_history_id = "chat-background"
    frame.archived_chats = [
        {
            "id": "chat-background",
            "title": "后台聊天",
            "title_manual": False,
            "pinned": False,
            "turns": [
                {
                    "question": "后台任务",
                    "answer_md": main.REQUESTING_TEXT,
                    "model": main.DEFAULT_CODEX_MODEL,
                    "created_at": 1.0,
                    "request_status": "pending",
                    "codex_thread_id": "thread-background",
                    "codex_turn_id": "turn-background",
                }
            ],
            "created_at": 1.0,
            "updated_at": 1.0,
            "detail_panel_mode": "execution",
            "execution_steps": [{"step": "旧步骤"}],
            "codex_thread_id": "thread-background",
            "codex_turn_id": "turn-background",
        }
    ]
    refresh_counts = {"history": 0, "answer": 0, "execution": 0}
    original_refresh_history = frame._refresh_history
    original_render_answer_list = frame._render_answer_list
    original_render_execution_list = frame._render_execution_list
    monkeypatch.setattr(frame, "_refresh_history", lambda keep_id=None: (refresh_counts.__setitem__("history", refresh_counts["history"] + 1), original_refresh_history(keep_id))[1])
    monkeypatch.setattr(frame, "_render_answer_list", lambda *args, **kwargs: (refresh_counts.__setitem__("answer", refresh_counts["answer"] + 1), original_render_answer_list(*args, **kwargs))[1])
    monkeypatch.setattr(frame, "_render_execution_list", lambda: (refresh_counts.__setitem__("execution", refresh_counts["execution"] + 1), original_render_execution_list())[1])
    monkeypatch.setattr(main.wx, "GetApp", lambda: object())
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *args, **kwargs: fn(*args, **kwargs))
    monkeypatch.setattr(frame, "_call_later_if_alive", lambda *args, **kwargs: None)

    frame._dispatch_codex_event_to_ui(
        "chat-background",
        main.CodexEvent(
            type="item_completed",
            phase="final_answer",
            thread_id="thread-background",
            turn_id="turn-background",
            text="后台最终回答",
        ),
    )

    archived = frame._find_archived_chat("chat-background")
    rows = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert archived["turns"][0]["answer_md"] == "后台最终回答"
    assert archived["execution_steps"][0] == {"step": "旧步骤"}
    assert archived["execution_steps"][1]["list_text"] == "已生成最终回答"
    assert refresh_counts["history"] >= 1
    assert refresh_counts["answer"] >= 1
    assert refresh_counts["execution"] == 0
    assert "后台最终回答" in rows


def test_show_history_chat_flushes_pending_execution_delta_before_switch(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.view_mode = "active"
    frame._current_chat_state = {
        "id": "chat-current",
        "title": "当前聊天",
        "turns": [],
        "detail_panel_mode": "answers",
        "execution_steps": [],
    }
    frame.archived_chats = [
        {
            "id": "chat-history",
            "title": "历史聊天",
            "turns": [],
            "detail_panel_mode": "execution",
            "execution_steps": [],
            "created_at": 2.0,
            "updated_at": 2.0,
        }
    ]
    frame._buffer_execution_delta(
        "chat-current",
            main.CodexEvent(type="agent_message_delta", thread_id="thread-current", turn_id="turn-current", item_id="msg-1", fragment_id="f1", text="切换前先落地"),
    )
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    assert frame._show_history_chat("chat-history", focus_answer_list=False) is True

    assert frame._current_chat_state["execution_steps"][0]["detail_text"] == "切换前先落地"
    assert frame.view_mode == "history"
    assert frame.view_history_id == "chat-history"
    assert frame._execution_delta_buffer == {}


def test_show_history_chat_active_chat_flushes_active_pending_delta_while_history_view_open(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.view_mode = "history"
    frame.view_history_id = "chat-viewed"
    frame._current_chat_state = {
        "id": "chat-current",
        "title": "当前聊天",
        "turns": [],
        "detail_panel_mode": "execution",
        "execution_steps": [],
    }
    frame.archived_chats = [
        {
            "id": "chat-viewed",
            "title": "历史聊天",
            "turns": [],
            "detail_panel_mode": "answers",
            "execution_steps": [],
            "created_at": 2.0,
            "updated_at": 2.0,
        }
    ]
    frame._buffer_execution_delta(
        "chat-current",
        main.CodexEvent(type="agent_message_delta", thread_id="thread-current", turn_id="turn-current", item_id="msg-1", text="回到当前前先落地"),
    )
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    assert frame._show_history_chat("chat-current", focus_answer_list=False) is True

    assert frame._current_chat_state["execution_steps"][0]["detail_text"] == "回到当前前先落地"
    assert frame.view_mode == "active"
    assert frame.view_history_id is None
    assert frame._execution_delta_buffer == {}


def test_history_enter_handlers_call_activate(frame):
    called = {"n": 0}

    def _activate():
        called["n"] += 1
        return True

    frame._activate_selected_history = _activate

    class E:
        def __init__(self, key):
            self.key = key

        def GetKeyCode(self):
            return self.key

        def Skip(self):
            pass

    frame._on_history_key_down(E(wx.WXK_RETURN))
    frame._on_history_char(E(wx.WXK_RETURN))
    assert called["n"] == 2


def test_history_selected_does_not_activate_chat(frame):
    called = {"n": 0, "skipped": False}

    def _activate():
        called["n"] += 1
        return True

    frame._activate_selected_history = _activate

    class E:
        def Skip(self):
            called["skipped"] = True

    frame._on_history_selected(E())

    assert called["n"] == 0
    assert called["skipped"] is True


def test_global_ctrl_arrow_navigates_history_when_app_is_foreground(frame, monkeypatch):
    seen = []
    monkeypatch.setattr(frame, "_global_chat_navigation_target_state", lambda: (True, {"frame_hwnd": 1, "fg_hwnd": 1, "root_hwnd": 1}))
    monkeypatch.setattr(frame, "_navigate_history_chats", lambda direction: seen.append(direction) or True)

    frame._on_global_ctrl_arrow("left")
    frame._on_global_ctrl_arrow("right")
    frame._on_global_ctrl_arrow("down")
    frame._on_global_ctrl_arrow("up")

    assert seen == [1, -1]


def test_background_history_update_defers_reorder_while_primary_control_has_focus(frame, monkeypatch):
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame._current_chat_state = {"id": "chat-active", "title": "active", "turns": [], "updated_at": 10.0}
    frame.archived_chats = [
        {"id": "chat-old", "title": "old", "turns": [], "created_at": 1.0, "updated_at": 1.0}
    ]
    frame._refresh_history("chat-active")
    monkeypatch.setattr(frame, "_primary_navigation_control_has_focus", lambda: True)
    frame._mark_primary_interaction()
    frame.history_list.SetSelection(0)

    frame.archived_chats[0]["updated_at"] = 99.0
    changed = frame._upsert_history_row("chat-old", allow_reorder=True)

    assert changed is False
    assert frame.history_ids == ["chat-active", "chat-old"]
    assert frame._pending_history_reorder is True
    assert frame.history_list.GetSelection() == 0
    frame._last_primary_interaction_at = 0.0
    frame._flush_idle_ui_refreshes()
    assert frame.history_ids == ["chat-old", "chat-active"]
    assert frame.history_list_model.selected_id() == "chat-active"
    assert frame._pending_history_reorder is False


def test_answer_update_preserves_history_focus(frame, monkeypatch):
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {"question": "q", "answer_md": main.REQUESTING_TEXT, "model": main.DEFAULT_CODEX_MODEL}
    ]
    frame._current_chat_state = {"id": "chat-active", "turns": frame.active_session_turns, "detail_panel_mode": "answers"}
    frame._render_answer_list(refresh_execution=False)
    monkeypatch.setattr(frame, "_can_focus_completion_result", lambda: False)
    monkeypatch.setattr(frame.answer_list, "SetFocus", lambda: pytest.fail("answer completion must not steal focus"))

    frame._on_done(0, "final answer", "", main.DEFAULT_CODEX_MODEL, "", "chat-active")

    assert frame.active_session_turns[0]["answer_md"] == "final answer"


def test_on_done_upserts_history_row_without_full_refresh(frame, monkeypatch):
    frame.active_chat_id = "chat-done-history"
    frame.current_chat_id = "chat-done-history"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {
            "question": "当前问题",
            "answer_md": main.REQUESTING_TEXT,
            "model": "openai/gpt-5.2",
            "request_status": "pending",
            "created_at": 1.0,
        }
    ]
    frame._current_chat_state.update({"id": "chat-done-history", "title": "done", "turns": frame.active_session_turns})
    upserts = []
    monkeypatch.setattr(frame, "_refresh_history", lambda *args, **kwargs: pytest.fail("completion should not refresh the whole history list"))
    monkeypatch.setattr(frame, "_upsert_history_row", lambda chat_id, **kwargs: upserts.append((chat_id, kwargs)) or True)
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: None)
    monkeypatch.setattr(frame, "_push_remote_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_final_answer", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_play_finish_sound", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_append_completed_answer_to_answer_list", lambda *args, **kwargs: True)
    monkeypatch.setattr(frame, "_focus_latest_answer", lambda: None)

    frame._on_done(0, "最终回答", "", "openai/gpt-5.2", "", "chat-done-history")

    assert upserts == [("chat-done-history", {"allow_reorder": not frame._primary_navigation_control_has_focus()})]
    assert frame.active_session_turns[0]["answer_md"] == "最终回答"


def test_refresh_history_skips_list_rebuild_when_items_unchanged(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state = {"id": "chat-current", "title": "current", "turns": []}
    frame.archived_chats = [{"id": "chat-old", "title": "old", "updated_at": 1.0}]

    frame._refresh_history("chat-current")
    clears = []
    appends = []
    monkeypatch.setattr(frame.history_list, "Clear", lambda: clears.append(True))
    monkeypatch.setattr(frame.history_list, "Append", lambda value: appends.append(value))

    frame._refresh_history("chat-current")

    assert clears == []
    assert appends == []


def test_show_history_chat_does_not_load_execution_steps_for_answer_view(frame, tmp_path, monkeypatch):
    frame.chat_db_path = tmp_path / "chat_history.db"
    frame.chat_store = main.ChatStore(frame.chat_db_path)
    frame.chat_store.initialize()
    frame._chat_store_enabled = True
    frame.chat_store.upsert_chat({"id": "chat-heavy", "title": "heavy", "updated_at": 10.0})
    frame.chat_store.replace_turns("chat-heavy", [{"question": "q", "answer_md": "a"}])
    for idx in range(500):
        frame.chat_store.append_execution_step("chat-heavy", {"turn_idx": 0, "list_text": f"step {idx}"})
    frame.archived_chats = frame.chat_store.list_chat_summaries()
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        frame.chat_store,
        "load_execution_steps",
        lambda *args, **kwargs: pytest.fail("history answer view should not load execution steps"),
    )

    assert frame._show_history_chat("chat-heavy", focus_answer_list=False) is True

    assert frame.view_mode == "history"
    assert frame.view_history_id == "chat-heavy"
    assert "execution_steps" not in frame.archived_chats[0]


def test_load_chat_as_current_avoids_deepcopying_turns_when_chat_store_enabled(frame, monkeypatch):
    class NoDeepcopyList(list):
        def __deepcopy__(self, memo):
            raise AssertionError("chat store load should not deepcopy all turns")

    turns = NoDeepcopyList(
        [{"question": f"q{idx}", "answer_md": f"a{idx}", "model": main.DEFAULT_MODEL_ID} for idx in range(200)]
    )
    frame._chat_store_enabled = True
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)

    frame._load_chat_as_current({"id": "chat-heavy", "title": "heavy", "title_manual": False, "turns": turns})

    assert len(frame.active_session_turns) == 200
    assert frame._current_chat_state["turns"] is frame.active_session_turns


def test_refresh_history_deduplicates_normalized_ids_in_recency_order(frame):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state = {"id": "chat-current", "title": "current", "turns": []}
    frame.archived_chats = [
        {"id": " chat-current ", "title": "stale current", "updated_at": 9.0},
        {"id": " chat-old ", "title": "first old", "updated_at": 8.0},
        {"id": "chat-old", "title": "later old", "updated_at": 7.0},
        {"id": "", "title": "empty", "updated_at": 6.0},
    ]

    frame._refresh_history(" chat-old ")

    assert frame.history_ids == ["chat-old", "chat-current"]
    assert list(frame.history_list.GetStrings()) == ["first old", "current"]
    assert frame.history_list.GetSelection() == 0


def test_padded_legacy_history_row_supports_activate_pin_rename_and_delete(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state = {"id": "chat-current", "title": "current", "turns": []}
    frame.archived_chats = [
        {"id": " chat-activate ", "title": "activate", "turns": [], "updated_at": 4.0},
        {"id": " chat-pin ", "title": "pin", "turns": [], "updated_at": 3.0},
        {"id": " chat-rename ", "title": "rename", "turns": [], "updated_at": 2.0},
        {"id": " chat-delete ", "title": "delete", "turns": [], "updated_at": 1.0},
    ]
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_confirm", lambda *args, **kwargs: True)
    frame._refresh_history(" chat-activate ")

    assert frame._activate_selected_history() is True
    assert frame.view_history_id == "chat-activate"

    frame.history_list.SetSelection(frame.history_ids.index("chat-pin"))
    frame._history_pin(None)
    assert frame._find_archived_chat("chat-pin")["pinned"] is True

    class _Dialog:
        def __init__(self, *_args, **_kwargs):
            pass

        def ShowModal(self):
            return main.wx.ID_OK

        def get_value(self):
            return "renamed"

        def Destroy(self):
            pass

    monkeypatch.setattr(main, "RenameDialog", _Dialog)
    frame.history_list.SetSelection(frame.history_ids.index("chat-rename"))
    frame._history_rename(None)
    assert frame._find_archived_chat("chat-rename")["title"] == "renamed"

    frame.history_list.SetSelection(frame.history_ids.index("chat-delete"))
    frame._history_delete(None)
    assert frame._find_archived_chat("chat-delete") is None


def test_enter_history_view_updates_view_state_and_focus(frame, monkeypatch):
    frame.archived_chats = [
        {"id": "hist-1", "title": "聊天1", "turns": [], "created_at": 1.0, "updated_at": 1.0},
    ]
    rendered = []
    monkeypatch.setattr(frame, "_render_answer_list", lambda: rendered.append(True))

    assert frame._enter_history_view("hist-1") is True

    assert frame.view_mode == "history"
    assert frame.view_history_id == "hist-1"
    assert rendered == [True]
    assert frame.answer_list.HasFocus()


def test_enter_active_view_clears_history_view_state(frame, monkeypatch):
    frame.view_mode = "history"
    frame.view_history_id = "hist-1"
    frame.archived_chats = [
        {"id": "hist-1", "title": "聊天1", "turns": [], "created_at": 1.0, "updated_at": 1.0},
    ]
    rendered = []
    monkeypatch.setattr(frame, "_render_answer_list", lambda: rendered.append(True))

    frame._enter_active_view(focus_answer_list=True)

    assert frame.view_mode == "active"
    assert frame.view_history_id is None
    assert rendered == [True]
    assert frame.answer_list.HasFocus()


def test_history_refresh_updates_renamed_row_without_clearing_list(frame, monkeypatch):
    frame.archived_chats = [
        {"id": "chat-a", "title": "旧标题", "turns": [], "created_at": 1.0, "updated_at": 1.0}
    ]
    frame._refresh_history("chat-a")
    monkeypatch.setattr(frame.history_list, "Clear", lambda: pytest.fail("history rename should not clear whole list"))

    frame.archived_chats[0]["title"] = "新标题"
    frame._refresh_history("chat-a")

    assert frame.history_list.GetString(0) == "新标题"


def test_summarize_last_turn_locally_uses_first_question(frame):
    turns = [
        {"question": "多聊天 CLI 改造怎么设计", "answer_md": "先拆会话状态", "model": "codex/main", "created_at": time.time()},
        {"question": "再补手机端路由协议", "answer_md": "需要显式 chat_id", "model": "codex/main", "created_at": time.time()},
    ]
    title = frame._summarize_last_turn_locally(turns)
    assert "多聊天 CLI 改造"[:8] in title
    assert "手机端路由协议"[:6] not in title


def test_background_done_does_not_rerender_different_viewed_history_chat(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_session_turns = [
        {"question": "当前问题", "answer_md": "当前回答", "model": "openai/gpt-5.2", "created_at": 1.0}
    ]
    frame._current_chat_state["id"] = "chat-current"
    frame._current_chat_state["title"] = "当前聊天"
    frame.archived_chats = [
        {
            "id": "chat-viewed",
            "title": "查看中的聊天",
            "turns": [{"question": "查看中的问题", "answer_md": "查看中的回答", "model": "openai/gpt-5.2", "created_at": 2.0}],
            "created_at": 2.0,
            "updated_at": 2.0,
        },
        {
            "id": "chat-old",
            "title": "后台聊天",
            "turns": [{"question": "旧问题", "answer_md": main.REQUESTING_TEXT, "model": "openai/gpt-5.2", "created_at": 1.0}],
            "created_at": 1.0,
            "updated_at": 1.0,
        },
    ]
    frame.view_mode = "history"
    frame.view_history_id = "chat-viewed"
    rendered = {"count": 0}
    monkeypatch.setattr(frame, "_render_answer_list", lambda: rendered.__setitem__("count", rendered["count"] + 1))
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_set_input_hint_idle", lambda: None)
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)
    frame.SetStatusText = lambda _text: None

    frame._on_done(0, "后台旧回答", "", "openai/gpt-5.2", "", "chat-old")

    assert frame.view_mode == "history"
    assert frame.view_history_id == "chat-viewed"
    assert frame._find_archived_chat("chat-old")["turns"][0]["answer_md"] == "后台旧回答"
    assert rendered["count"] == 0


def test_background_history_answer_dirty_flushes_visible_history_answer_after_quiet(frame, monkeypatch):
    frame.view_mode = "history"
    frame.view_history_id = "chat-history"
    frame._background_answer_list_dirty = True
    frame._background_history_dirty_ids = {"chat-history"}
    refreshed = []
    monkeypatch.setattr(frame, "_refresh_answer_list_preserving_selection", lambda **kwargs: refreshed.append(kwargs))

    frame._flush_pending_background_ui_updates()

    assert refreshed == [{"refresh_execution": frame._detail_panel_mode() != "execution"}]
    assert frame._background_answer_list_dirty is False
    assert "chat-history" not in frame._background_history_dirty_ids


def test_idle_history_flush_also_flushes_visible_history_answer_dirty_after_quiet(frame, monkeypatch):
    frame.view_mode = "history"
    frame.view_history_id = "chat-history"
    frame._history_list_dirty = True
    frame._pending_history_keep_id = "chat-history"
    frame._background_answer_list_dirty = True
    frame._background_history_dirty_ids = {"chat-history"}
    frame._navigation_quiet_until = 0.0
    history_refreshed = []
    answer_refreshed = []
    monkeypatch.setattr(frame, "_refresh_history", lambda keep_id=None: history_refreshed.append(keep_id))
    monkeypatch.setattr(frame, "_refresh_answer_list_preserving_selection", lambda **kwargs: answer_refreshed.append(kwargs))

    frame._flush_idle_ui_refreshes()

    assert history_refreshed == ["chat-history"]
    assert answer_refreshed == [{"refresh_execution": frame._detail_panel_mode() != "execution"}]
    assert frame._background_answer_list_dirty is False
    assert "chat-history" not in frame._background_history_dirty_ids


def test_claudecode_streaming_delta_keeps_background_chat_isolated(frame, monkeypatch):
    frame.active_chat_id = "chat-c"
    frame.current_chat_id = "chat-c"
    frame.active_claudecode_session_id = "session-c"
    frame.active_session_turns = [
        {
            "question": "问题 d",
            "answer_md": "问题 d 的回答",
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
                    "question": "问题 b",
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
            on_delta("问题 b 的流式片段")
            return "问题 b 的最终回答", "session-a"

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

    frame._start_claudecode_worker_for_turn("chat-a", 0, "问题 b", "session-a")

    archived = frame._find_archived_chat("chat-a")
    assert archived["turns"][0]["answer_md"] == "问题 b 的最终回答"
    assert archived["claudecode_session_id"] == "session-a"
    assert frame.active_claudecode_session_id == "session-c"
    assert frame.active_session_turns[0]["answer_md"] == "问题 d 的回答"


def test_history_delete_broadcasts_remote_history_changed(frame, monkeypatch):
    frame.archived_chats = [
        {"id": "chat-old", "title": "旧聊天", "turns": [], "created_at": 1.0, "updated_at": 1.0, "pinned": False}
    ]
    frame.history_ids = ["chat-old"]
    frame.history_list.Set(["旧聊天"])
    frame.history_list.SetSelection(0)
    monkeypatch.setattr(frame, "_confirm", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    seen = []

    class _Transport:
        def publish_event_threadsafe(self, payload):
            seen.append(payload)

    frame._remote_nats_transport = _Transport()

    frame._history_delete(None)

    assert any(payload.get("type") == "history_changed" for payload in seen)


def test_history_delete_removes_chat_from_chat_store(frame, monkeypatch, tmp_path):
    frame.chat_db_path = tmp_path / "chat_history.db"
    frame.chat_store = main.ChatStore(frame.chat_db_path)
    frame.chat_store.initialize()
    frame._chat_store_enabled = True
    frame.chat_store.upsert_chat({"id": "chat-old", "title": "旧聊天", "updated_at": 1.0, "pinned": False})
    frame.chat_store.replace_turns("chat-old", [{"question": "q", "answer_md": "a"}])
    frame.archived_chats = frame.chat_store.list_chat_summaries()
    frame.history_ids = ["chat-old"]
    frame.history_list.Set(["旧聊天"])
    frame.history_list.SetSelection(0)
    monkeypatch.setattr(frame, "_confirm", lambda *_args, **_kwargs: True)

    frame._history_delete(None)

    assert frame.chat_store.load_chat("chat-old") is None
    frame.archived_chats = frame.chat_store.list_chat_summaries()
    frame._refresh_history()
    assert "chat-old" not in frame.history_ids


def test_history_delete_removes_pinned_chat_from_chat_store(frame, monkeypatch, tmp_path):
    frame.chat_db_path = tmp_path / "chat_history.db"
    frame.chat_store = main.ChatStore(frame.chat_db_path)
    frame.chat_store.initialize()
    frame._chat_store_enabled = True
    frame.chat_store.upsert_chat({"id": "chat-pin", "title": "置顶", "updated_at": 1.0, "pinned": True})
    frame.archived_chats = frame.chat_store.list_chat_summaries()
    frame.history_ids = ["chat-pin"]
    frame.history_list.Set(["[置顶] 置顶"])
    frame.history_list.SetSelection(0)
    monkeypatch.setattr(frame, "_confirm", lambda *_args, **_kwargs: True)

    frame._history_delete(None)

    assert frame.chat_store.load_chat("chat-pin") is None
    frame.archived_chats = frame.chat_store.list_chat_summaries()
    frame._refresh_history()
    assert "chat-pin" not in frame.history_ids


def test_history_delete_current_chat_removes_it_from_chat_store(frame, monkeypatch, tmp_path):
    frame.chat_db_path = tmp_path / "chat_history.db"
    frame.chat_store = main.ChatStore(frame.chat_db_path)
    frame.chat_store.initialize()
    frame._chat_store_enabled = True
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_session_turns = [{"question": "q", "answer_md": "a"}]
    frame._current_chat_state = {"id": "chat-current", "title": "当前", "turns": frame.active_session_turns}
    frame.chat_store.upsert_chat(frame._current_chat_state)
    frame.chat_store.replace_turns("chat-current", frame.active_session_turns)
    frame.history_ids = ["chat-current"]
    frame.history_list.Set(["当前"])
    frame.history_list.SetSelection(0)
    monkeypatch.setattr(frame, "_confirm", lambda *_args, **_kwargs: True)

    frame._history_delete(None)

    assert frame.chat_store.load_chat("chat-current") is None
    assert frame.active_chat_id == ""
    assert frame.current_chat_id == ""
    assert frame.active_session_turns == []


def test_history_clear_non_pinned_removes_cleared_chats_from_chat_store(frame, monkeypatch, tmp_path):
    frame.chat_db_path = tmp_path / "chat_history.db"
    frame.chat_store = main.ChatStore(frame.chat_db_path)
    frame.chat_store.initialize()
    frame._chat_store_enabled = True
    frame.chat_store.upsert_chat({"id": "chat-old", "title": "旧聊天", "updated_at": 1.0, "pinned": False})
    frame.chat_store.upsert_chat({"id": "chat-pin", "title": "置顶", "updated_at": 2.0, "pinned": True})
    frame.archived_chats = frame.chat_store.list_chat_summaries()
    monkeypatch.setattr(frame, "_confirm", lambda *_args, **_kwargs: True)

    frame._history_clear_non_pinned(None)

    assert frame.chat_store.load_chat("chat-old") is None
    assert frame.chat_store.load_chat("chat-pin") is not None
    frame.archived_chats = frame.chat_store.list_chat_summaries()
    frame._refresh_history()
    assert "chat-old" not in frame.history_ids
    assert "chat-pin" in frame.history_ids


def test_history_rename_broadcasts_remote_history_changed(frame, monkeypatch):
    frame.archived_chats = [
        {"id": "chat-old", "title": "旧聊天", "turns": [], "created_at": 1.0, "updated_at": 1.0, "pinned": False}
    ]
    frame.history_ids = ["chat-old"]
    frame.history_list.Set(["旧聊天"])
    frame.history_list.SetSelection(0)
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    seen = []

    class _Transport:
        def publish_event_threadsafe(self, payload):
            seen.append(payload)

    class _Dialog:
        def __init__(self, *_args, **_kwargs):
            pass

        def ShowModal(self):
            return wx.ID_OK

        def get_value(self):
            return "新标题"

        def Destroy(self):
            pass

    frame._remote_nats_transport = _Transport()
    monkeypatch.setattr(main, "RenameDialog", _Dialog)

    frame._history_rename(None)

    assert any(payload.get("type") == "history_changed" for payload in seen)


def test_history_menu_opens_for_current_chat_row(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state.update({"id": "chat-current", "title": "当前聊天", "turns": []})
    frame.history_ids = ["chat-current"]
    frame.history_list.Set(["当前聊天"])
    frame.history_list.SetSelection(0)
    popup = []
    monkeypatch.setattr(frame, "PopupMenu", lambda menu: popup.append(menu.GetMenuItemCount()) or True)

    frame._show_history_menu()

    assert popup == [4]


def test_history_f2_opens_rename_dialog_for_selected_chat(frame, monkeypatch):
    frame.archived_chats = [
        {"id": "chat-old", "title": "旧聊天", "turns": [], "created_at": 1.0, "updated_at": 1.0, "pinned": False}
    ]
    frame.history_ids = ["chat-old"]
    frame.history_list.Set(["旧聊天"])
    frame.history_list.SetSelection(0)
    calls = {"rename": 0}
    monkeypatch.setattr(frame, "_history_rename", lambda _event: calls.__setitem__("rename", calls["rename"] + 1))

    class E:
        def GetKeyCode(self):
            return wx.WXK_F2

        def Skip(self):
            raise AssertionError("F2 should open rename without falling through")

    frame._on_history_key_down(E())

    assert calls["rename"] == 1


def test_history_rename_current_chat_updates_active_state(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state.update({"id": "chat-current", "title": "当前聊天", "turns": []})
    frame.history_ids = ["chat-current"]
    frame.history_list.Set(["当前聊天"])
    frame.history_list.SetSelection(0)
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    pushed = []
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda chat_id="": pushed.append(chat_id))

    class _Dialog:
        def __init__(self, *_args, **_kwargs):
            pass

        def ShowModal(self):
            return wx.ID_OK

        def get_value(self):
            return "新标题"

        def Destroy(self):
            pass

    monkeypatch.setattr(main, "RenameDialog", _Dialog)

    frame._history_rename(None)

    assert frame._current_chat_state["title"] == "新标题"
    assert frame._current_chat_state["title_manual"] is True
    assert pushed == ["chat-current"]


def test_on_close_marks_current_chat_pending_turns_before_save(frame, wait_for_wx):
    frame.active_chat_id = "chat-current"
    frame._current_chat_state["id"] = "chat-current"
    frame.active_session_turns = [
        {
            "question": "当前可恢复请求",
            "answer_md": main.REQUESTING_TEXT,
            "model": "openai/gpt-5.2",
            "created_at": 1.0,
            "request_status": "pending",
            "request_recoverable": True,
        },
        {
            "question": "当前非目标请求",
            "answer_md": "done",
            "model": "openai/gpt-5.2",
            "created_at": 2.0,
            "request_status": "done",
            "request_recoverable": False,
        },
    ]
    frame.archived_chats = []
    frame.history_ids = ["chat-current"]

    database = frame.chat_store.db_path
    frame.Close()
    wait_for_wx(lambda: getattr(frame, "_close_complete", False), "close-persistence")
    turns = main.ChatStore(database).load_turns("chat-current")
    assert turns[0]["request_status"] == "pending"
    assert turns[0]["request_recoverable"] is True
    assert turns[1]["request_status"] == "done"
    assert turns[1]["request_recoverable"] is False


def test_resolve_app_data_dir_is_dist_history():
    p = main.resolve_app_data_dir()
    assert str(p).lower().endswith("dist\\history")


def test_resolve_app_data_dir_frozen_uses_executable_sibling_history(monkeypatch):
    monkeypatch.setattr(main.sys, "frozen", True, raising=False)
    monkeypatch.setattr(main.sys, "executable", r"C:\code\cx\mc\mc.exe", raising=False)
    p = main.resolve_app_data_dir()
    assert p == Path(r"C:\code\cx\history")


def test_model_changed_in_history_is_candidate_only(frame, monkeypatch):
    active = {"id": "chat-active", "model": "codex/main", "turns": []}
    archived = {"id": "chat-history", "model": "codex/main", "turns": []}
    frame._current_chat_state = active
    frame.archived_chats = [archived]
    frame.view_mode = "history"
    frame.view_history_id = "chat-history"
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.selected_model = "codex/main"
    frame.model_combo.SetValue(main.model_display_name("openai/gpt-5.2"))
    saved = []
    pushed = []
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: saved.append(True))
    monkeypatch.setattr(frame, "_push_remote_state", lambda chat_id: pushed.append(chat_id))

    frame._on_model_changed(None)

    assert active["model"] == "codex/main"
    assert archived["model"] == "codex/main"
    assert frame.selected_model == "openai/gpt-5.2"
    assert saved == []
    assert pushed == []


def test_empty_chat_model_restores_from_restarted_store(frame, monkeypatch, tmp_path):
    path = tmp_path / "empty-model-restart.db"
    first = main.ChatStore(path)
    first.initialize()
    first.upsert_chat({"id": "chat-empty", "title": "Empty", "model": "kimi/main", "created_at": 1.0, "updated_at": 1.0})
    restarted = main.ChatStore(path)
    restarted.initialize()
    loaded = restarted.load_chat("chat-empty")
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)

    frame._load_chat_as_current(loaded)

    assert frame.active_session_turns == []
    assert frame._current_chat_state["model"] == "kimi/main"
    assert frame.selected_model == "kimi/main"
    assert frame.model_combo.GetValue() == main.model_display_name("kimi/main")


def test_invalid_legacy_chat_model_uses_only_own_turn_then_startup_default(frame):
    own_turn = {"model": "invalid/provider", "turns": [{"model": "kimi/main"}]}
    no_valid_turn = {"model": "invalid/provider", "turns": [{"model": "also-invalid"}]}
    frame.selected_model = "openai/gpt-5.2"

    assert frame._model_for_chat_selection(own_turn) == "kimi/main"
    assert (frame._model_for_chat_selection(no_valid_turn) or main.STARTUP_DEFAULT_MODEL_ID) == main.STARTUP_DEFAULT_MODEL_ID


def test_switch_current_chat_restores_chat_level_model_and_combo(frame, monkeypatch):
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

    archived = frame._archive_active_session(quick_title=True, save_after_archive=False)
    assert archived["model"] == "anthropic/claude-sonnet-4.6"

    frame.active_chat_id = "chat-b"
    frame.current_chat_id = "chat-b"
    frame.active_session_turns = [
        {
            "question": "codex question",
            "answer_md": "codex answer",
            "model": "codex/main",
            "created_at": 2.0,
        }
    ]
    frame._current_chat_state = {
        "id": "chat-b",
        "title": "chat b",
        "model": "codex/main",
        "created_at": 2.0,
        "updated_at": 2.0,
        "turns": frame.active_session_turns,
    }
    frame.selected_model = "codex/main"
    frame.model_combo.SetValue(main.model_display_name("codex/main"))

    assert frame._switch_current_chat("chat-a") is True

    assert frame.selected_model == "anthropic/claude-sonnet-4.6"
    assert frame._current_chat_state["model"] == "anthropic/claude-sonnet-4.6"
    assert frame.model_combo.GetValue() == main.model_display_name("anthropic/claude-sonnet-4.6")


def test_switch_current_chat_avoids_deepcopying_turns_when_chat_store_enabled(frame, monkeypatch):
    class NoDeepcopyList(list):
        def __deepcopy__(self, memo):
            raise AssertionError("chat store switch should not deepcopy all turns")

    turns = NoDeepcopyList(
        [{"question": f"q{idx}", "answer_md": f"a{idx}", "model": main.DEFAULT_MODEL_ID} for idx in range(200)]
    )
    chat = {"id": "chat-heavy", "title": "heavy", "turns": turns, "execution_steps": []}
    frame._chat_store_enabled = True
    frame.archived_chats = [chat]
    monkeypatch.setattr(frame, "_find_archived_chat", lambda chat_id: chat)
    monkeypatch.setattr(frame, "_hydrate_chat_from_store", lambda source, **kwargs: source)
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda *args, **kwargs: None)

    assert frame._switch_current_chat("chat-heavy") is True

    assert len(frame.active_session_turns) == 200
    assert frame._current_chat_state["turns"] is frame.active_session_turns


def test_submit_fences_final_history_destination_not_previously_active_chat(frame, monkeypatch):
    frame._chat_store_enabled = True
    frame.chat_store = SimpleNamespace()
    frame.active_chat_id = frame.current_chat_id = "blocked-active"
    frame.view_mode = "history"
    frame.view_history_id = "open-history"
    history_state = {"id": "open-history", "turns": [], "model": main.DEFAULT_CODEX_MODEL}
    checked = []

    def switch(owner):
        assert owner == "open-history"
        frame.active_chat_id = frame.current_chat_id = owner
        frame._current_chat_state = history_state
        frame.active_session_turns = history_state["turns"]
        return True

    monkeypatch.setattr(frame, "_switch_current_chat", switch)
    monkeypatch.setattr(
        frame, "_reconcile_clear_operation_for_owner",
        lambda owner, **_kwargs: checked.append(owner) or None,
    )
    monkeypatch.setattr(frame, "_send_active_claudecode_input", lambda *_args: False)
    starts = []
    monkeypatch.setattr(frame, "_start_codex_worker_for_turn", lambda *args: starts.append(args))

    ok, message = frame._submit_question("send to history", source="local", model=main.DEFAULT_CODEX_MODEL)

    assert (ok, message) == (True, "")
    assert checked == ["open-history"]
    assert starts and starts[0][0] == "open-history"


def test_blocked_history_destination_does_not_switch_active_chat(frame, monkeypatch):
    frame._chat_store_enabled = True
    frame.chat_store = SimpleNamespace()
    frame.active_chat_id = frame.current_chat_id = "active-owner"
    frame.view_mode = "history"
    frame.view_history_id = "blocked-history"
    frame.archived_chats = [{"id": "blocked-history", "turns": []}]
    switches = []
    monkeypatch.setattr(
        frame, "_reconcile_clear_operation_for_owner",
        lambda owner, **_kwargs: {"operation_id": "blocked"} if owner == "blocked-history" else None,
    )
    monkeypatch.setattr(frame, "_switch_current_chat", lambda owner: switches.append(owner) or True)

    ok, message = frame._submit_question("must stay put", model=main.DEFAULT_CODEX_MODEL)

    assert ok is False
    assert message == "Clear/resend recovery is still pending for this chat."
    assert switches == []
    assert frame.active_chat_id == frame.current_chat_id == "active-owner"


def test_switched_chat_final_active_save_reload_keeps_ownership(frame, monkeypatch):
    _prepare_switched_chat_final(frame, monkeypatch)
    frame._persist_chat_history_to_store()
    loaded = main.ChatStore(frame.chat_store.db_path).load_chat("switch-owner")
    frame.archived_chats = [loaded]
    frame.active_session_turns = []
    frame.active_chat_id = frame.current_chat_id = "other-owner"
    frame._current_chat_state = {"id": "other-owner", "turns": []}
    turn = loaded["turns"][0]
    frame._on_codex_event_for_chat("switch-owner", _switched_chat_final_event(
        "item_completed", phase="final_answer", text="reloaded final"))
    frame._on_codex_event_for_chat("switch-owner", _switched_chat_final_event(
        "turn_completed", status="completed"))
    assert turn["answer_md"] == "reloaded final"
    assert turn["request_status"] == "done"
    frame._persist_chat_history_to_store()
    reopened = main.ChatStore(frame.chat_store.db_path).load_chat("switch-owner")
    assert reopened["codex_context_generation"] == 2
    assert reopened["turns"][0]["answer_md"] == "reloaded final"
    assert reopened["turns"][0]["request_status"] == "done"
