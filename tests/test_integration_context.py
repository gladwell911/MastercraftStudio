import os
import json
import time
import threading

import main


class ImmediateThread:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        if self._target:
            self._target(*self._args, **self._kwargs)


class NoopThread:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        return None


def test_loaded_history_is_sent_as_context(frame, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(threading, "Thread", ImmediateThread)

    captured = {}

    class FakeChatClient:
        def __init__(self, api_key, model):
            self.api_key = api_key
            self.model = model
            captured["model"] = model

        def stream_chat(self, user_text, on_delta, history_turns=None):
            captured["user_text"] = user_text
            captured["history"] = list(history_turns or [])
            on_delta("测试增量")
            return "测试完成"

    monkeypatch.setattr(main, "ChatClient", FakeChatClient)

    frame.archived_chats = [
        {
            "id": "hist-1",
            "title": "历史会话",
            "pinned": False,
            "created_at": time.time(),
            "turns": [
                {"question": "历史问题1", "answer_md": "历史回答1", "model": "openai/gpt-5.2", "created_at": time.time()},
                {"question": "历史问题2", "answer_md": "历史回答2", "model": "openai/gpt-5.2", "created_at": time.time()},
            ],
        }
    ]
    frame._refresh_history("hist-1")
    idx = frame.history_ids.index("hist-1")
    frame.history_list.SetSelection(idx)
    assert frame._activate_selected_history()

    frame.input_edit.SetValue("继续问历史")
    frame.selected_model = "openai/gpt-5.2"
    frame.model_combo.SetValue("deepseek/deepseek-r1-0528")
    frame._on_send_clicked(None)

    assert captured["user_text"] == "继续问历史"
    assert captured["model"] == "deepseek/deepseek-r1-0528"
    assert len(captured["history"]) == 2
    assert captured["history"][0]["question"] == "历史问题1"
    assert captured["history"][1]["question"] == "历史问题2"


def test_controls_remain_focusable_after_send(frame, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")
    monkeypatch.setattr(threading, "Thread", NoopThread)

    frame.model_combo.SetValue("openai/gpt-5.2")
    frame.input_edit.SetValue("测试发送")
    frame._on_send_clicked(None)

    assert frame.is_running
    assert frame.model_combo.IsEnabled()
    assert frame.history_list.IsEnabled()


def test_activate_history_enters_history_view_without_reordering_or_archiving(frame):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state["id"] = "chat-current"
    frame._current_chat_state["title"] = "当前聊天"
    frame._current_chat_state["turns"] = [{"question": "当前问题", "answer_md": "当前回答", "model": "openai/gpt-5.2", "created_at": time.time()}]
    frame.active_session_turns = list(frame._current_chat_state["turns"])
    frame.archived_chats = [
        {
            "id": "hist-1",
            "title": "更早聊天",
            "pinned": False,
            "created_at": 1.0,
            "updated_at": 99.0,
            "turns": [{"question": "更早问题", "answer_md": "更早回答", "model": "openai/gpt-5.2", "created_at": 1.0}],
        },
        {
            "id": "hist-2",
            "title": "历史2",
            "pinned": True,
            "created_at": 2.0,
            "updated_at": 1.0,
            "turns": [{"question": "历史问题", "answer_md": "历史回答", "model": "openai/gpt-5.2", "created_at": 2.0}],
        }
    ]
    frame._refresh_history("hist-2")
    frame.history_list.SetSelection(frame.history_ids.index("hist-2"))

    seen = {"archived": 0}
    frame._archive_active_session = lambda **kwargs: seen.__setitem__("archived", seen["archived"] + 1)

    assert frame._activate_selected_history()
    assert seen["archived"] == 0
    assert frame.current_chat_id == "chat-current"
    assert frame.view_mode == "history"
    assert frame.view_history_id == "hist-2"
    assert frame.history_ids == ["chat-current", "hist-2", "hist-1"]
    assert frame.history_list.GetSelection() == frame.history_ids.index("hist-2")
    assert frame.answer_list.GetString(1) == "历史问题"


def test_history_enter_switches_view_while_current_reply_is_pending(frame, monkeypatch):
    frame.is_running = True
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state["id"] = "chat-current"
    frame._current_chat_state["title"] = "当前聊天"
    frame._current_chat_state["turns"] = [{"question": "当前问题", "answer_md": main.REQUESTING_TEXT, "model": "openai/gpt-5.2", "created_at": 4.0, "request_status": "pending"}]
    frame.active_session_turns = list(frame._current_chat_state["turns"])
    frame.archived_chats = [
        {
            "id": "hist-1",
            "title": "历史会话",
            "pinned": False,
            "created_at": 1.0,
            "updated_at": 1.0,
            "turns": [{"question": "历史问题", "answer_md": "历史回答", "model": "openai/gpt-5.2", "created_at": 1.0}],
        }
    ]
    frame._refresh_history("hist-1")
    frame.history_list.SetSelection(frame.history_ids.index("hist-1"))
    shown = {"dialog": 0}
    monkeypatch.setattr(frame, "_show_ok_dialog", lambda *_args, **_kwargs: shown.__setitem__("dialog", shown["dialog"] + 1))

    class E:
        def GetKeyCode(self):
            return main.wx.WXK_RETURN

        def Skip(self):
            return None

    frame._on_history_key_down(E())

    assert shown["dialog"] == 0
    assert frame.is_running is True
    assert frame.view_mode == "history"
    assert frame.view_history_id == "hist-1"
    assert frame.answer_list.GetString(1) == "历史问题"


def test_history_activation_preserves_empty_new_chat_and_can_switch_back(frame):
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
            "title": "历史会话",
            "pinned": False,
            "created_at": 1.0,
            "updated_at": 1.0,
            "turns": [{"question": "历史问题", "answer_md": "历史回答", "model": "openai/gpt-5.2", "created_at": 1.0}],
        }
    ]

    frame._refresh_history("hist-1")
    frame.history_list.SetSelection(frame.history_ids.index("hist-1"))

    assert frame._activate_selected_history() is True
    assert frame.current_chat_id == "chat-new"
    assert frame.active_chat_id == "chat-new"
    assert frame.view_mode == "history"
    assert frame.view_history_id == "hist-1"
    assert frame.answer_list.GetString(1) == "历史问题"

    frame.history_list.SetSelection(frame.history_ids.index("chat-new"))

    assert frame._activate_selected_history() is True
    assert frame.current_chat_id == "chat-new"
    assert frame.active_chat_id == "chat-new"
    assert frame.view_mode == "active"
    assert frame.view_history_id is None
    assert "历史问题" not in list(frame.answer_list.GetStrings())


def test_history_activation_preserves_answered_new_chat_and_can_switch_back(frame):
    frame.active_chat_id = "chat-new"
    frame.current_chat_id = "chat-new"
    frame._current_chat_state.update(
        {
            "id": "chat-new",
            "title": "自动化测试方案整理",
            "title_manual": False,
            "title_source": "auto",
            "turns": [
                {
                    "question": "帮我整理自动化测试方案",
                    "answer_md": "先列测试层级，再补回归用例。",
                    "model": "openai/gpt-5.2",
                    "created_at": 10.0,
                }
            ],
        }
    )
    frame.active_session_turns = list(frame._current_chat_state["turns"])
    frame.archived_chats = [
        {
            "id": "hist-1",
            "title": "旧聊天标题",
            "pinned": False,
            "created_at": 1.0,
            "updated_at": 1.0,
            "turns": [{"question": "历史问题", "answer_md": "历史回答", "model": "openai/gpt-5.2", "created_at": 1.0}],
        }
    ]

    frame._refresh_history("hist-1")
    frame.history_list.SetSelection(frame.history_ids.index("hist-1"))

    assert frame._activate_selected_history() is True
    assert frame.current_chat_id == "chat-new"
    assert frame.active_chat_id == "chat-new"
    assert frame.view_mode == "history"
    assert frame.view_history_id == "hist-1"
    assert frame.answer_list.GetString(1) == "历史问题"

    frame.history_list.SetSelection(frame.history_ids.index("chat-new"))

    assert frame._activate_selected_history() is True
    assert frame.current_chat_id == "chat-new"
    assert frame.active_chat_id == "chat-new"
    assert frame.view_mode == "active"
    assert frame.view_history_id is None
    rows = list(frame.answer_list.GetStrings())
    assert "帮我整理自动化测试方案" in rows
    assert "先列测试层级，再补回归用例。" in rows
    assert "历史问题" not in rows


def test_pending_current_reply_does_not_rename_viewed_history_chat(frame, monkeypatch):
    frame.active_chat_id = "chat-new"
    frame.current_chat_id = "chat-new"
    frame._current_chat_state.update(
        {
            "id": "chat-new",
            "title": main.EMPTY_CURRENT_CHAT_TITLE,
            "title_manual": False,
            "title_source": "default",
            "title_revision": 1,
            "turns": [
                {
                    "question": "帮我整理自动化测试方案",
                    "answer_md": main.REQUESTING_TEXT,
                    "model": "openai/gpt-5.2",
                    "created_at": 10.0,
                    "request_status": "pending",
                }
            ],
        }
    )
    frame.active_session_turns = frame._current_chat_state["turns"]
    frame.archived_chats = [
        {
            "id": "hist-1",
            "title": "旧聊天标题",
            "pinned": False,
            "created_at": 1.0,
            "updated_at": 1.0,
            "turns": [{"question": "历史问题", "answer_md": "历史回答", "model": "openai/gpt-5.2", "created_at": 1.0}],
        }
    ]
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)
    monkeypatch.setattr(frame, "_focus_latest_answer", lambda: None)
    monkeypatch.setattr(frame, "_call_later_if_alive", lambda *_args, **_kwargs: None)

    frame._refresh_history("hist-1")
    frame.history_list.SetSelection(frame.history_ids.index("hist-1"))
    assert frame._activate_selected_history() is True

    frame._on_done(0, "自动化方案如下", "", "openai/gpt-5.2", "", "chat-new")

    assert frame.archived_chats[0]["title"] == "旧聊天标题"
    assert frame.active_session_turns[0]["answer_md"] == "自动化方案如下"
    assert frame.view_mode == "history"
    assert frame.view_history_id == "hist-1"


def test_history_activation_does_not_overwrite_other_chat_title(frame):
    frame.active_chat_id = "chat-new"
    frame.current_chat_id = "chat-new"
    frame._current_chat_state.update(
        {
            "id": "chat-new",
            "title": "自动化测试方案整理",
            "title_manual": False,
            "title_source": "auto",
            "turns": [
                {
                    "question": "帮我整理自动化测试方案",
                    "answer_md": "先列测试层级，再补回归用例。",
                    "model": "openai/gpt-5.2",
                    "created_at": 10.0,
                }
            ],
        }
    )
    frame.active_session_turns = list(frame._current_chat_state["turns"])
    frame.archived_chats = [
        {
            "id": "hist-1",
            "title": "旧聊天标题",
            "pinned": False,
            "created_at": 1.0,
            "updated_at": 1.0,
            "turns": [{"question": "历史问题", "answer_md": "历史回答", "model": "openai/gpt-5.2", "created_at": 1.0}],
        }
    ]

    frame._refresh_history("hist-1")
    assert list(frame.history_list.GetStrings()) == ["自动化测试方案整理", "旧聊天标题"]

    frame.history_list.SetSelection(frame.history_ids.index("hist-1"))

    assert frame._activate_selected_history() is True
    assert list(frame.history_list.GetStrings()) == ["自动化测试方案整理", "旧聊天标题"]
    assert frame.archived_chats[0]["title"] == "旧聊天标题"


def test_history_click_selection_only_changes_selection_without_activation(frame):
    frame.active_chat_id = "chat-new"
    frame.current_chat_id = "chat-new"
    frame._current_chat_state.update(
        {
            "id": "chat-new",
            "title": "自动化测试方案整理",
            "title_manual": False,
            "turns": [
                {
                    "question": "帮我整理自动化测试方案",
                    "answer_md": "先列测试层级，再补回归用例。",
                    "model": "openai/gpt-5.2",
                    "created_at": 10.0,
                }
            ],
        }
    )
    frame.active_session_turns = list(frame._current_chat_state["turns"])
    frame.archived_chats = [
        {
            "id": "hist-1",
            "title": "旧聊天标题",
            "pinned": False,
            "created_at": 1.0,
            "updated_at": 1.0,
            "turns": [{"question": "历史问题", "answer_md": "历史回答", "model": "openai/gpt-5.2", "created_at": 1.0}],
        }
    ]
    frame._refresh_history("hist-1")
    frame._render_answer_list()

    class _Event:
        def __init__(self):
            self.skipped = False

        def Skip(self):
            self.skipped = True

    frame.history_list.SetSelection(frame.history_ids.index("hist-1"))
    history_event = _Event()
    frame._on_history_selected(history_event)

    assert history_event.skipped is True
    assert frame.view_mode == "active"
    assert frame.view_history_id is None
    rows = list(frame.answer_list.GetStrings())
    assert "帮我整理自动化测试方案" in rows
    assert "历史问题" not in rows

    frame.history_list.SetSelection(frame.history_ids.index("chat-new"))
    current_event = _Event()
    frame._on_history_selected(current_event)

    assert current_event.skipped is True
    assert frame.view_mode == "active"
    assert frame.view_history_id is None
    assert list(frame.answer_list.GetStrings()) == rows


def test_ctrl_history_navigation_walks_through_all_chats_in_order(frame):
    frame.current_chat_id = "chat-current"
    frame._current_chat_state["id"] = "chat-current"
    frame._current_chat_state["title"] = "当前聊天"
    frame._current_chat_state["turns"] = [{"question": "当前问题", "answer_md": "当前回答", "created_at": 4.0}]
    frame._current_chat_state["updated_at"] = 4.0
    frame.archived_chats = [
        {"id": "chat-a", "title": "聊天A", "turns": [], "created_at": 1.0, "updated_at": 1.0},
        {"id": "chat-b", "title": "聊天B", "turns": [], "created_at": 2.0, "updated_at": 2.0},
        {"id": "chat-c", "title": "聊天C", "turns": [], "created_at": 3.0, "updated_at": 3.0},
    ]

    assert frame._adjacent_history_chat_id(1) == "chat-c"
    assert frame._switch_current_chat("chat-c") is True
    assert frame._adjacent_history_chat_id(1) == "chat-b"
    assert frame._switch_current_chat("chat-b") is True
    assert frame._adjacent_history_chat_id(1) == "chat-a"
    assert frame._switch_current_chat("chat-a") is True
    assert frame._adjacent_history_chat_id(1) is None


def test_ctrl_history_navigation_reaches_all_chats_with_pinned_history(frame):
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

    seen = [frame.current_chat_id]
    for _ in range(6):
        target = frame._adjacent_history_chat_id(1)
        assert target
        assert frame._switch_current_chat(target) is True
        seen.append(frame.current_chat_id)

    assert seen == ["chat-e", "chat-g", "chat-f", "chat-b", "chat-c", "chat-a", "chat-d"]
    assert frame._adjacent_history_chat_id(1) is None


def test_ctrl_history_navigation_does_not_ping_pong_between_two_recent_chats(frame):
    frame.current_chat_id = "chat-e"
    frame.active_chat_id = "chat-e"
    frame._current_chat_state["id"] = "chat-e"
    frame._current_chat_state["title"] = "聊天E"
    frame._current_chat_state["turns"] = [{"question": "当前问题", "answer_md": "当前回答", "created_at": 6.0}]
    frame._current_chat_state["updated_at"] = 6.0
    frame.active_session_turns = list(frame._current_chat_state["turns"])
    frame.archived_chats = [
        {"id": "chat-b", "title": "聊天B", "turns": [{"question": "B", "answer_md": "B"}], "created_at": 4.0, "updated_at": 4.0},
        {"id": "chat-f", "title": "置顶F", "turns": [{"question": "F", "answer_md": "F"}], "created_at": 5.0, "updated_at": 5.0, "pinned": True},
        {"id": "chat-c", "title": "置顶C", "turns": [{"question": "C", "answer_md": "C"}], "created_at": 3.0, "updated_at": 3.0, "pinned": True},
        {"id": "chat-a", "title": "聊天A", "turns": [{"question": "A", "answer_md": "A"}], "created_at": 2.0, "updated_at": 2.0},
        {"id": "chat-d", "title": "聊天D", "turns": [{"question": "D", "answer_md": "D"}], "created_at": 1.0, "updated_at": 1.0},
        {"id": "chat-g", "title": "聊天G", "turns": [{"question": "G", "answer_md": "G"}], "created_at": 9.0, "updated_at": 9.0},
    ]
    frame._refresh_history()

    seen = [frame.current_chat_id]
    for _ in range(6):
        assert frame._navigate_history_chats(1) is True
        seen.append(frame.current_chat_id)

    assert seen == ["chat-e", "chat-g", "chat-f", "chat-b", "chat-c", "chat-a", "chat-d"]


def test_ctrl_history_navigation_from_new_chat_keeps_recency_order_while_viewing_history(frame):
    frame.current_chat_id = "chat-new"
    frame.active_chat_id = "chat-new"
    frame._current_chat_state["id"] = "chat-new"
    frame._current_chat_state["title"] = main.EMPTY_CURRENT_CHAT_TITLE
    frame._current_chat_state["turns"] = []
    frame._current_chat_state["updated_at"] = 6.0
    frame.archived_chats = [
        {"id": "chat-b", "title": "聊天B", "turns": [], "created_at": 4.0, "updated_at": 4.0},
        {"id": "chat-f", "title": "置顶F", "turns": [], "created_at": 5.0, "updated_at": 5.0, "pinned": True},
        {"id": "chat-c", "title": "置顶C", "turns": [], "created_at": 3.0, "updated_at": 3.0, "pinned": True},
        {"id": "chat-a", "title": "聊天A", "turns": [], "created_at": 2.0, "updated_at": 2.0},
        {"id": "chat-d", "title": "聊天D", "turns": [], "created_at": 2.0, "updated_at": 2.0},
        {"id": "chat-g", "title": "聊天G", "turns": [], "created_at": 9.0, "updated_at": 9.0},
    ]
    frame.view_mode = "history"
    frame.view_history_id = "chat-f"
    frame._refresh_history("chat-f")

    assert frame._navigation_chat_ids() == ["chat-new", "chat-g", "chat-f", "chat-b", "chat-c", "chat-a", "chat-d"]
    assert frame._adjacent_history_chat_id(1) == "chat-g"
    assert frame._switch_current_chat("chat-g") is True
    assert frame.current_chat_id == "chat-g"
    assert frame.view_mode == "history"
    assert frame.view_history_id == "chat-f"
    assert frame._adjacent_history_chat_id(1) == "chat-f"


def test_global_hotkey_switches_visible_chat_content(frame, monkeypatch):
    frame.current_chat_id = "chat-current"
    frame.active_chat_id = "chat-current"
    frame._current_chat_state["id"] = "chat-current"
    frame._current_chat_state["title"] = "当前聊天"
    frame._current_chat_state["turns"] = [{"question": "当前问题", "answer_md": "当前回答", "created_at": 4.0}]
    frame._current_chat_state["updated_at"] = 4.0
    frame.archived_chats = [
        {
            "id": "chat-next",
            "title": "下一个聊天",
            "turns": [{"question": "历史问题", "answer_md": "历史回答", "model": "codex/main", "created_at": 2.0}],
            "created_at": 2.0,
            "updated_at": 3.0,
        }
    ]
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda **kwargs: None)
    frame.view_mode = "history"
    frame.view_history_id = "chat-next"
    frame._refresh_history()
    frame._render_answer_list()

    class E:
        def GetId(self):
            return main.HOTKEY_ID_CHAT_NEXT

    frame._on_global_hotkey(E())

    assert frame.current_chat_id == "chat-next"
    assert frame.view_mode == "active"
    assert frame.view_history_id is None
    assert frame.history_ids == ["chat-pinned", "chat-next", "chat-old"]
    assert frame.history_list.GetSelection() == frame.history_ids.index("chat-next")
    assert frame.answer_list.GetCount() == 4
    assert frame.answer_list.GetString(0) == "我"
    assert frame.answer_list.GetString(1) == "历史问题"
    assert frame.answer_list.GetString(2) == "小诸葛"
    assert frame.answer_list.GetString(3) == "历史回答"


def test_submit_question_from_history_view_continues_selected_chat_without_duplicate_archive(frame, monkeypatch):
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(threading, "Thread", ImmediateThread)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda **kwargs: None)
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)
    monkeypatch.setattr(frame, "_focus_latest_answer", lambda: None)
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")

    class FakeChatClient:
        def __init__(self, api_key, model):
            self.model = model

        def stream_chat(self, user_text, on_delta, history_turns=None):
            on_delta("继续回答片段")
            return "继续回答完成"

    monkeypatch.setattr(main, "ChatClient", FakeChatClient)

    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state["id"] = "chat-current"
    frame._current_chat_state["title"] = "当前聊天"
    frame.active_session_started_at = 10.0
    frame.active_session_turns = [{"question": "当前问题", "answer_md": "当前回答", "model": "openai/gpt-5.2", "created_at": 10.0}]
    frame.archived_chats = [
        {
            "id": "hist-2",
            "title": "历史2",
            "pinned": False,
            "created_at": 2.0,
            "updated_at": 2.0,
            "turns": [{"question": "历史问题", "answer_md": "历史回答", "model": "openai/gpt-5.2", "created_at": 2.0}],
        }
    ]
    frame.view_mode = "history"
    frame.view_history_id = "hist-2"
    frame._refresh_history("hist-2")
    frame.input_edit.SetValue("继续追问")

    frame._on_send_clicked(None)

    assert frame.current_chat_id == "hist-2"
    assert frame.active_chat_id == "hist-2"
    assert frame.view_mode == "active"
    assert frame.view_history_id is None
    assert len(frame.archived_chats) == 1
    assert len(frame.active_session_turns) == 2
    assert frame.active_session_turns[-1]["question"] == "继续追问"
    archived = frame._archive_active_session(quick_title=True, schedule_async_rename=False, save_after_archive=False)
    assert archived is frame._find_archived_chat("hist-2")
    archived = frame._find_archived_chat("hist-2")
    assert archived is not None
    assert archived["created_at"] == 2.0
    assert archived["turns"][-1]["question"] == "继续追问"
    frame._refresh_history()
    assert frame.history_ids == ["hist-2", "chat-current"]


def test_voice_result_empty_after_normalization_is_skipped(frame):
    called = {"inject": 0, "type": 0, "wrong": 0}
    frame._inject_text_to_foreground_window = lambda text: called.__setitem__("inject", called["inject"] + 1) or True
    frame._type_text_to_system_focus = lambda text: called.__setitem__("type", called["type"] + 1) or True
    frame._play_voice_wrong_sound = lambda: called.__setitem__("wrong", called["wrong"] + 1)
    frame._on_voice_result("。。。！！")
    assert called["inject"] == 0
    assert called["type"] == 0
    assert called["wrong"] == 1


def test_send_uses_current_combobox_model_every_time(frame, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(threading, "Thread", ImmediateThread)

    used_models = []

    class FakeChatClient:
        def __init__(self, api_key, model):
            used_models.append(model)

        def stream_chat(self, user_text, on_delta, history_turns=None):
            on_delta("x")
            return "ok"

    monkeypatch.setattr(main, "ChatClient", FakeChatClient)

    frame.input_edit.SetValue("q1")
    frame.model_combo.SetValue("openai/gpt-5.2")
    frame._on_send_clicked(None)

    frame.input_edit.SetValue("q2")
    frame.model_combo.SetValue("qwen/qwen3.5-plus-02-15")
    frame._on_send_clicked(None)

    assert used_models[0] == "openai/gpt-5.2"
    assert used_models[1] == "qwen/qwen3.5-plus-02-15"


def test_same_session_can_switch_models_per_question(frame, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(threading, "Thread", ImmediateThread)

    used_models = []

    class FakeChatClient:
        def __init__(self, api_key, model):
            used_models.append(model)

        def stream_chat(self, user_text, on_delta, history_turns=None):
            on_delta("ok")
            return "ok"

    monkeypatch.setattr(main, "ChatClient", FakeChatClient)

    frame.input_edit.SetValue("第一个问题")
    frame.model_combo.SetStringSelection("google/gemini-3.1-pro-preview")
    frame._on_send_clicked(None)

    frame.input_edit.SetValue("第二个问题")
    frame.model_combo.SetStringSelection("openai/gpt-5.2")
    frame._on_send_clicked(None)

    assert used_models == ["google/gemini-3.1-pro-preview", "openai/gpt-5.2"]
    assert frame.active_session_turns[-2]["model"] == "google/gemini-3.1-pro-preview"
    assert frame.active_session_turns[-1]["model"] == "openai/gpt-5.2"


def test_unavailable_model_falls_back_and_still_returns_answer(frame, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(threading, "Thread", ImmediateThread)

    calls = []

    class FakeChatClient:
        def __init__(self, api_key, model):
            self.model = model
            calls.append(model)

        def stream_chat(self, user_text, on_delta, history_turns=None):
            if self.model == "deepseek/deepseek-r1-0528-qwen3-8b":
                raise RuntimeError("请求失败：HTTP 404。错误信息：No endpoints found for deepseek/deepseek-r1-0528-qwen3-8b.")
            on_delta("done")
            return "fallback answer"

    monkeypatch.setattr(main, "ChatClient", FakeChatClient)

    frame.input_edit.SetValue("q")
    frame.model_combo.SetValue("deepseek/deepseek-r1-0528-qwen3-8b")
    frame._on_send_clicked(None)

    assert calls[0] == "deepseek/deepseek-r1-0528-qwen3-8b"
    assert calls[1] == "deepseek/deepseek-r1-0528"
    assert frame.active_session_turns[-1]["model"] == "deepseek/deepseek-r1-0528"
    assert "回退" in frame.GetStatusBar().GetStatusText()


def test_startup_model_prefers_saved_selection_over_startup_default(monkeypatch, tmp_path):
    state = {
        "selected_model_id": "z-ai/glm-5",
        "archived_chats": [],
        "active_session_turns": [],
        "active_session_started_at": 0.0,
    }
    (tmp_path / "app_state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(main, "resolve_app_data_dir", lambda: tmp_path)
    f = main.ChatFrame()
    try:
        assert f.selected_model == "z-ai/glm-5"
        assert f.model_combo.GetValue() == "z-ai/glm-5"
    finally:
        f.Destroy()

def test_enter_send_path_respects_ime_candidates(frame):
    sent = {"n": 0}
    frame._trigger_send = lambda: sent.__setitem__("n", sent["n"] + 1)

    class E:
        def GetKeyCode(self):
            return main.wx.WXK_RETURN

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            pass

    frame._has_input_ime_candidates = lambda: True
    frame._on_input_key_down(E())
    assert sent["n"] == 0

    frame._has_input_ime_candidates = lambda: False
    frame._on_input_key_down(E())
    assert sent["n"] == 1
