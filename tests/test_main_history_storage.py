import copy
import json
import main
import pytest
import sqlite3
import time
import wx
from main_test_support import _prepare_switched_chat_final, _switched_chat_final_event
from pathlib import Path
from types import SimpleNamespace


def test_switch_current_chat_restores_archived_runtime_state(frame):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
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
            "codex_thread_id": "thread-archived",
            "codex_turn_id": "turn-archived",
            "codex_turn_active": True,
            "codex_pending_prompt": "pending archived",
            "codex_pending_request": {"request_id": 1},
            "codex_request_queue": [{"request_id": 2}],
            "codex_thread_flags": ["waitingOnUserInput"],
            "codex_latest_assistant_text": "archived answer",
            "codex_latest_assistant_phase": "complete",
            "claudecode_session_id": "claude-archived",
            "openclaw_session_key": "agent:main:main",
            "openclaw_session_id": "openclaw-archived",
            "openclaw_session_file": r"C:\\tmp\\archived.jsonl",
            "openclaw_sync_offset": 12,
            "openclaw_last_event_id": "evt-archived",
            "openclaw_last_synced_at": 22.0,
        }
    ]

    assert frame._switch_current_chat("chat-archived") is True
    assert frame.active_chat_id == "chat-archived"
    assert frame.current_chat_id == "chat-archived"
    assert frame.active_session_turns[0]["question"] == "历史问题"
    assert frame.active_codex_thread_id == "thread-archived"
    assert frame.active_codex_turn_id == "turn-archived"
    assert frame.active_codex_turn_active is True
    assert frame.active_codex_pending_prompt == "pending archived"
    assert frame.active_claudecode_session_id == "claude-archived"
    assert frame.active_openclaw_session_id == "openclaw-archived"


def test_char_hook_ctrl_history_navigation_noops_without_archived_chats(frame):
    frame.active_chat_id = "chat-only"
    frame.archived_chats = []
    seen = {"switched": False, "skipped": False}
    frame._switch_current_chat = lambda _chat_id: seen.__setitem__("switched", True) or True

    class E:
        def GetKeyCode(self):
            return wx.WXK_UP

        def ControlDown(self):
            return True

        def AltDown(self):
            return False

        def Skip(self):
            seen["skipped"] = True

    frame._on_char_hook(E())

    assert seen["switched"] is False
    assert seen["skipped"] is False


def test_history_view_uses_viewed_archived_chat_detail_panel_mode(frame):
    frame.view_mode = "history"
    frame.view_history_id = "chat-archived"
    frame._current_chat_state = {
        "id": "chat-active",
        "title": "当前聊天",
        "turns": [],
        "detail_panel_mode": "answers",
        "execution_steps": [],
    }
    frame.archived_chats = [
        {
            "id": "chat-archived",
            "title": "归档聊天",
            "turns": [{"question": "q", "answer_md": "a", "model": main.DEFAULT_MODEL_ID, "created_at": 1.0}],
            "created_at": 1.0,
            "updated_at": 1.0,
            "detail_panel_mode": "execution",
            "execution_steps": [{"step": "归档步骤"}],
        }
    ]

    frame._render_answer_list()

    assert frame.detail_title_label.GetLabel() == "执行过程："
    assert frame.execution_list.IsShown() is True
    assert frame.answer_list.IsShown() is False


def test_background_final_answer_updates_matching_archived_turn(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.archived_chats = [
        {
            "id": "chat-background",
            "title": "后台聊天",
            "turns": [
                {
                    "question": "第一问",
                    "answer_md": "第一答",
                    "model": main.DEFAULT_CODEX_MODEL,
                    "created_at": 1.0,
                    "request_status": "done",
                },
                {
                    "question": "第二问",
                    "answer_md": main.REQUESTING_TEXT,
                    "model": main.DEFAULT_CODEX_MODEL,
                    "created_at": 2.0,
                    "request_status": "pending",
                },
            ],
            "created_at": 1.0,
            "updated_at": 1.0,
            "detail_panel_mode": "answers",
            "execution_steps": [],
        }
    ]
    calls = []
    monkeypatch.setattr(main.wx, "GetApp", lambda: object())
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *args, **kwargs: calls.append((fn, args, kwargs)))
    monkeypatch.setattr(frame, "_call_later_if_alive", lambda *args, **kwargs: None)

    frame._dispatch_codex_event_to_ui(
        "chat-background",
        main.CodexEvent(type="item_completed", phase="final_answer", thread_id="thread-background", turn_id="turn-2", text="第二答"),
    )

    assert len(calls) == 1
    calls[0][0](*calls[0][1], **calls[0][2])
    archived = frame._find_archived_chat("chat-background")
    assert archived["turns"][0]["answer_md"] == "第一答"
    assert archived["turns"][1]["answer_md"] == "第二答"
    assert len(archived["execution_steps"]) == 1
    assert archived["execution_steps"][0]["list_text"] == "已生成最终回答"


def test_save_load_preserves_context_usage_for_active_and_archived_chats(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "resolve_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(main.ChatFrame, "_legacy_state_paths", lambda self: [self.state_path])
    active_usage = {
        "used_tokens": 12000,
        "context_window": 128000,
        "source": "api",
        "exact": True,
        "fresh": True,
        "model": "openai/gpt-5.2",
        "updated_at": 1.0,
    }
    archived_usage = {
        "used_tokens": 44176,
        "context_window": 258400,
        "source": "codex",
        "exact": False,
        "fresh": True,
        "model": "gpt-5-codex",
        "updated_at": 2.0,
    }

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
            "context_usage": active_usage,
            "updated_at": 1.0,
        }
        first.archived_chats = [
            {
                "id": "chat-archived",
                "title": "归档聊天",
                "turns": [
                    {"question": "归档问题", "answer_md": "归档回答", "model": "codex/main", "created_at": 1.0}
                ],
                "context_usage": archived_usage,
                "created_at": 1.0,
                "updated_at": 2.0,
            }
        ]
        first._save_state()
    finally:
        first.Destroy()

    second = main.ChatFrame()
    try:
        assert second._current_chat_state["context_usage"] == active_usage
        assert second._find_archived_chat("chat-archived")["context_usage"] == archived_usage

        second._render_answer_list()
        assert second.answer_list.GetString(0) == "12k / 128k"

        second.view_mode = "history"
        second.view_history_id = "chat-archived"
        second._render_answer_list()
        assert second.answer_list.GetString(0) == "44k / 258k"
    finally:
        second.Destroy()


def test_archive_active_session_preserves_context_usage_for_history_render(frame, monkeypatch):
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_session_turns = [
        {"question": "q", "answer_md": "a", "model": "codex/main", "created_at": 1.0}
    ]
    frame._current_chat_state = {
        "id": "chat-current",
        "title": "当前聊天",
        "turns": frame.active_session_turns,
        "context_usage": {
            "used_tokens": 44176,
            "context_window": 258400,
            "source": "codex",
            "exact": False,
            "fresh": True,
            "model": "gpt-5-codex",
            "updated_at": 1.0,
        },
    }

    archived = frame._archive_active_session(quick_title=True, save_after_archive=False)
    frame.view_mode = "history"
    frame.view_history_id = archived["id"]
    frame._render_answer_list()

    assert archived["context_usage"]["source"] == "codex"
    assert frame.answer_list.GetString(0) == "44k / 258k"


def test_archive_active_session_clears_active_codex_runtime_state_consistently(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_session_started_at = 1.0
    frame.active_session_turns = [
        {
            "question": "旧问题",
            "answer_md": "旧回答",
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": 1.0,
        }
    ]
    frame.active_codex_thread_id = "thread-old"
    frame.active_codex_turn_id = "turn-old"
    frame.active_codex_turn_active = True
    frame.active_codex_pending_prompt = "pending"
    frame.active_codex_pending_request = {"kind": "user_input"}
    frame.active_codex_request_queue = [{"prompt": "queued"}]
    frame.active_codex_thread_flags = ["waitingOnUserInput"]
    frame.active_codex_latest_assistant_text = "partial"
    frame.active_codex_latest_assistant_phase = "answer"
    frame._current_chat_state = {
        "id": "chat-current",
        "title": "当前聊天",
        "turns": frame.active_session_turns,
        "updated_at": 1.0,
    }

    monkeypatch.setattr(frame, "_flush_chat_state_save", lambda: None)
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    archived = frame._archive_active_session(save_after_archive=False, flush_before_archive=False)

    assert archived["codex_thread_id"] == "thread-old"
    assert archived["codex_turn_id"] == "turn-old"
    assert archived["codex_turn_active"] is True
    assert archived["codex_pending_prompt"] == "pending"
    assert archived["codex_pending_request"] == {"kind": "user_input"}
    assert archived["codex_request_queue"] == [{"prompt": "queued"}]
    assert archived["codex_thread_flags"] == ["waitingOnUserInput"]
    assert archived["codex_latest_assistant_text"] == "partial"
    assert archived["codex_latest_assistant_phase"] == "answer"
    assert frame.active_codex_thread_id == ""
    assert frame.active_codex_turn_id == ""
    assert frame.active_codex_turn_active is False
    assert frame.active_codex_pending_prompt == ""
    assert frame.active_codex_pending_request is None
    assert frame.active_codex_request_queue == []
    assert frame.active_codex_thread_flags == []
    assert frame.active_codex_latest_assistant_text == ""
    assert frame.active_codex_latest_assistant_phase == ""


def test_codex_worker_thread_state_updates_matching_archived_chat_only(frame, monkeypatch):
    frame.active_chat_id = "chat-a"
    frame.current_chat_id = "chat-a"
    frame.active_codex_thread_id = "thread-a"
    frame.active_codex_turn_id = "turn-a"
    frame.active_codex_turn_active = True
    frame.active_session_turns = [
        {
            "question": "active",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": 1.0,
            "request_status": "pending",
            "codex_thread_id": "thread-a",
            "codex_turn_id": "turn-a",
        }
    ]
    frame._current_chat_state = {
        "id": "chat-a",
        "turns": frame.active_session_turns,
        "codex_thread_id": "thread-a",
        "codex_turn_id": "turn-a",
        "codex_turn_active": True,
        "execution_steps": [],
    }
    frame.archived_chats = [
        {
            "id": "chat-c",
            "turns": [
                {
                    "question": "archived",
                    "answer_md": main.REQUESTING_TEXT,
                    "model": main.DEFAULT_CODEX_MODEL,
                    "created_at": 2.0,
                    "request_status": "pending",
                    "codex_thread_id": "",
                        "codex_turn_id": "",
                        "codex_context_generation": 0,
                }
            ],
            "codex_thread_id": "",
            "codex_turn_id": "",
            "codex_context_generation": 0,
            "codex_turn_active": False,
            "execution_steps": [],
        }
    ]
    deferred = []
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: deferred.append(True))

    frame._on_codex_worker_message(
        "chat-c",
        {
            "type": "thread_state",
            "payload": {
                "chat_id": "chat-c",
                "turn_idx": 0,
                "thread_id": "thread-c-new",
                    "turn_id": "turn-c-new",
                    "context_generation": 0,
                "active": True,
            },
        },
    )

    archived = frame._find_archived_chat("chat-c")
    assert archived["codex_thread_id"] == "thread-c-new"
    assert archived["codex_turn_id"] == "turn-c-new"
    assert archived["codex_turn_active"] is True
    assert archived["turns"][0]["codex_thread_id"] == "thread-c-new"
    assert archived["turns"][0]["codex_turn_id"] == "turn-c-new"
    assert frame.active_codex_thread_id == "thread-a"
    assert frame.active_codex_turn_id == "turn-a"
    assert frame.active_session_turns[0]["codex_thread_id"] == "thread-a"
    assert deferred == [True]


def test_background_archived_final_answer_updates_answer_and_execution_steps(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
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
            "detail_panel_mode": "answers",
            "execution_steps": [],
            "codex_thread_id": "thread-background",
            "codex_turn_id": "turn-background",
        }
    ]
    calls = []
    monkeypatch.setattr(main.wx, "GetApp", lambda: object())
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *args, **kwargs: calls.append((fn, args, kwargs)))
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

    assert len(calls) == 1
    calls[0][0](*calls[0][1], **calls[0][2])

    archived = frame._find_archived_chat("chat-background")
    assert archived["turns"][0]["answer_md"] == "后台最终回答"
    assert len(archived["execution_steps"]) == 1
    assert archived["execution_steps"][0]["list_text"] == "已生成最终回答"


def test_background_turn_completed_marks_archived_turn_done(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
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
                    "request_error": "stale",
                }
            ],
            "created_at": 1.0,
            "updated_at": 1.0,
            "detail_panel_mode": "answers",
            "execution_steps": [],
        }
    ]
    calls = []
    monkeypatch.setattr(main.wx, "GetApp", lambda: object())
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *args, **kwargs: calls.append((fn, args, kwargs)))
    monkeypatch.setattr(frame, "_call_later_if_alive", lambda *args, **kwargs: None)

    frame._dispatch_codex_event_to_ui(
        "chat-background",
        main.CodexEvent(
            type="turn_completed",
            thread_id="thread-background",
            turn_id="turn-background",
            text="后台最终回答",
        ),
    )

    assert len(calls) == 1
    calls[0][0](*calls[0][1], **calls[0][2])

    archived = frame._find_archived_chat("chat-background")
    assert archived["turns"][0]["request_status"] == "done"
    assert archived["turns"][0]["request_error"] == ""
    assert archived["turns"][0]["answer_md"] == "后台最终回答"


def test_show_history_chat_switching_away_flushes_pending_delta_for_viewed_archived_chat(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.view_mode = "history"
    frame.view_history_id = "chat-a"
    frame._current_chat_state = {
        "id": "chat-current",
        "title": "当前聊天",
        "turns": [],
        "detail_panel_mode": "answers",
        "execution_steps": [],
    }
    frame.archived_chats = [
        {
            "id": "chat-a",
            "title": "历史A",
            "turns": [],
            "detail_panel_mode": "answers",
            "execution_steps": [],
            "created_at": 2.0,
            "updated_at": 2.0,
        },
        {
            "id": "chat-b",
            "title": "历史B",
            "turns": [],
            "detail_panel_mode": "answers",
            "execution_steps": [],
            "created_at": 3.0,
            "updated_at": 3.0,
        },
    ]
    frame._buffer_execution_delta(
        "chat-a",
        main.CodexEvent(type="agent_message_delta", thread_id="thread-a", turn_id="turn-a", item_id="msg-1", text="离开前先落地"),
    )
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    assert frame._show_history_chat("chat-b", focus_answer_list=False) is True

    archived_a = next(chat for chat in frame.archived_chats if chat["id"] == "chat-a")
    assert archived_a["execution_steps"][0]["detail_text"] == "离开前先落地"
    assert frame.view_mode == "history"
    assert frame.view_history_id == "chat-b"
    assert frame._execution_delta_buffer == {}


def test_shared_codex_turn_completed_marks_original_archived_chat_done_after_switch(frame, monkeypatch):
    frame.active_chat_id = "chat-b"
    frame.current_chat_id = "chat-b"
    frame._current_chat_state = {
        "id": "chat-b",
        "title": "聊天B",
        "turns": [
            {
                "question": "B的问题",
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
    frame.active_session_turns = frame._current_chat_state["turns"]
    frame.archived_chats = [
        {
            "id": "chat-a",
            "title": "聊天A",
            "turns": [
                {
                    "question": "A的问题",
                    "answer_md": main.REQUESTING_TEXT,
                    "model": main.DEFAULT_CODEX_MODEL,
                    "created_at": 1.0,
                    "request_status": "pending",
                    "request_error": "stale",
                    "codex_thread_id": "thread-a",
                    "codex_turn_id": "turn-a",
                }
            ],
            "created_at": 1.0,
            "updated_at": 1.0,
            "detail_panel_mode": "answers",
            "execution_steps": [],
            "codex_thread_id": "thread-a",
            "codex_turn_id": "turn-a",
        }
    ]
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_call_later_if_alive", lambda *args, **kwargs: None)

    frame._on_codex_event(
        main.CodexEvent(
            type="turn_completed",
            thread_id="thread-a",
            turn_id="turn-a",
            text="A 的最终回答",
        )
    )

    archived = frame._find_archived_chat("chat-a")
    assert archived["turns"][0]["request_status"] == "done"
    assert archived["turns"][0]["request_error"] == ""
    assert archived["turns"][0]["answer_md"] == "A 的最终回答"
    assert frame.active_session_turns[0]["answer_md"] == main.REQUESTING_TEXT


def test_history_title_change_updates_single_row_without_full_refresh(frame, monkeypatch):
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame._current_chat_state = {"id": "chat-active", "title": "old title", "turns": []}
    frame.archived_chats = [
        {"id": "chat-old", "title": "old archived", "turns": [], "created_at": 1.0, "updated_at": 1.0}
    ]
    frame._refresh_history("chat-active")
    frame.history_list.SetSelection(frame.history_ids.index("chat-active"))

    monkeypatch.setattr(frame.history_list, "Clear", lambda: pytest.fail("history title update must not clear the list"))
    frame._current_chat_state["title"] = "new title"

    changed = frame._upsert_history_row("chat-active", allow_reorder=False)

    assert changed is True
    assert frame.history_list.GetString(frame.history_ids.index("chat-active")) == "new title"
    assert frame.history_ids == ["chat-old", "chat-active"]
    assert frame.history_list_model.selected_id() == "chat-active"


def test_save_state_excludes_large_chat_history_after_store_migration(frame, tmp_path):
    frame.state_path = tmp_path / "app_state.json"
    frame.archived_chats = [
        {
            "id": "chat-old",
            "title": "old",
            "turns": [{"question": "q", "answer_md": "a" * 10000, "model": "codex/main"}],
            "execution_steps": [{"detail_text": "x" * 10000}],
        }
    ]
    frame.active_chat_id = "chat-active"
    frame.active_session_turns = [{"question": "active", "answer_md": "answer", "model": "codex/main"}]
    frame._current_chat_state = {
        "id": "chat-active",
        "title": "active",
        "turns": frame.active_session_turns,
        "execution_steps": [{"detail_text": "active step"}],
    }
    frame._chat_store_enabled = True

    frame._save_state()

    data = json.loads(frame.state_path.read_text(encoding="utf-8"))
    assert "archived_chats" not in data
    assert "chats" not in data
    assert "active_session_turns" not in data
    assert "turns" not in data.get("active_chat", {})
    assert "execution_steps" not in data.get("active_chat", {})
    assert frame.state_path.stat().st_size < 200_000


def test_save_state_persists_chats_to_chat_store(frame, tmp_path):
    frame.state_path = tmp_path / "app_state.json"
    frame.chat_db_path = tmp_path / "chat_history.db"
    frame.chat_store = main.ChatStore(frame.chat_db_path)
    frame.chat_store.initialize()
    frame._chat_store_enabled = True
    frame.active_chat_id = "chat-active"
    frame.active_session_turns = [{"question": "active q", "answer_md": "active a", "model": "codex/main"}]
    frame._current_chat_state = {"id": "chat-active", "title": "active", "turns": frame.active_session_turns}
    frame.archived_chats = [
        {"id": "chat-old", "title": "old", "turns": [{"question": "old q", "answer_md": "old a"}]},
    ]

    frame._save_state()

    summaries = frame.chat_store.list_chat_summaries()
    assert {item["id"] for item in summaries} == {"chat-active", "chat-old"}
    assert frame.chat_store.load_turns("chat-active") == frame.active_session_turns
    assert frame.chat_store.load_turns("chat-old") == frame.archived_chats[0]["turns"]


def test_history_answer_turns_hydrate_without_loading_execution_steps(frame, tmp_path, monkeypatch):
    frame.chat_db_path = tmp_path / "chat_history.db"
    frame.chat_store = main.ChatStore(frame.chat_db_path)
    frame.chat_store.initialize()
    frame._chat_store_enabled = True
    frame.chat_store.upsert_chat({"id": "chat-old", "title": "old", "updated_at": 10.0})
    frame.chat_store.replace_turns("chat-old", [{"question": "q", "answer_md": "a"}])
    for idx in range(50):
        frame.chat_store.append_execution_step("chat-old", {"turn_idx": 0, "list_text": f"step {idx}"})
    frame.archived_chats = frame.chat_store.list_chat_summaries()
    frame.view_mode = "history"
    frame.view_history_id = "chat-old"

    monkeypatch.setattr(
        frame.chat_store,
        "load_execution_steps",
        lambda *args, **kwargs: pytest.fail("answer/history hydration should not load execution steps"),
    )

    assert frame._get_view_turns() == [{"question": "q", "answer_md": "a"}]
    assert "execution_steps" not in frame.archived_chats[0]


def test_load_state_uses_chat_store_summaries_without_full_turns(frame, tmp_path):
    frame.state_path = tmp_path / "app_state.json"
    frame.chat_db_path = tmp_path / "chat_history.db"
    frame.chat_store = main.ChatStore(frame.chat_db_path)
    frame.chat_store.initialize()
    frame._chat_store_enabled = True
    frame.chat_store.upsert_chat({"id": "chat-old", "title": "old", "updated_at": 10.0})
    frame.chat_store.replace_turns("chat-old", [{"question": "q", "answer_md": "a" * 10000}])
    frame.state_path.write_text(
        json.dumps(
            {
                "selected_model_id": "codex/main",
                "active_chat_id": "chat-old",
                "active_chat": {"id": "chat-old"},
            }
        ),
        encoding="utf-8",
    )

    frame._load_state()

    assert frame.archived_chats == [frame.chat_store.list_chat_summaries()[0]]
    assert "turns" not in frame.archived_chats[0]
    assert frame.active_session_turns == [{"question": "q", "answer_md": "a" * 10000}]


def test_archive_title_uses_first_question_only(frame):
    frame._current_chat_state["title"] = "新聊天"
    frame._current_chat_state["title_manual"] = False
    frame.active_session_turns = [
        {"question": "安卓自动化测试方案怎么做", "answer_md": "先按层次划分", "model": "openai/gpt-5.2", "created_at": time.time()},
        {"question": "再帮我整理成执行清单", "answer_md": "可以整理成清单", "model": "openai/gpt-5.2", "created_at": time.time()},
    ]
    frame.active_session_started_at = time.time()
    archived = frame._archive_active_session(quick_title=True)
    assert archived is not None
    assert archived["title"] == "新聊天"
    assert "整理成执行清单"[:6] not in archived["title"]


def test_archive_active_session_uses_updated_at_for_recency_sort(frame):
    frame.archived_chats = [
        {"id": "chat-old", "title": "旧聊天", "turns": [], "created_at": 9.0, "updated_at": 9.0},
    ]
    frame.active_chat_id = "chat-recent"
    frame.current_chat_id = "chat-recent"
    frame._current_chat_state["title"] = "最近聊天"
    frame._current_chat_state["title_manual"] = False
    frame._current_chat_state["updated_at"] = 20.0
    frame.active_session_started_at = 1.0
    frame.active_session_turns = [
        {"question": "刚发的新问题", "answer_md": "回答", "model": "openai/gpt-5.2", "created_at": 20.0}
    ]

    archived = frame._archive_active_session(quick_title=True, schedule_async_rename=False, save_after_archive=False)

    assert archived is not None
    assert archived["updated_at"] == 20.0
    assert [chat["id"] for chat in frame.archived_chats] == ["chat-recent", "chat-old"]


def test_archive_active_session_upserts_normalized_id_and_preserves_pinned(frame):
    frame.archived_chats = [
        {
            "id": "chat-current",
            "title": "stale summary",
            "pinned": True,
            "turns": [{"question": "stale", "answer_md": "stale"}],
            "created_at": 1.0,
            "updated_at": 1.0,
        },
        {"id": " chat-current ", "title": "duplicate", "pinned": False, "duplicate_only": True},
    ]
    current_turns = [{"question": "current", "answer_md": "answer", "model": main.DEFAULT_CODEX_MODEL}]
    frame.active_chat_id = "   "
    frame.current_chat_id = " chat-current "
    frame.active_session_turns = current_turns
    frame.active_session_started_at = 2.0
    frame._current_chat_state.update(
        {"id": " chat-current ", "title": "current title", "turns": current_turns, "updated_at": 3.0}
    )

    archived = frame._archive_active_session(
        quick_title=True,
        schedule_async_rename=False,
        save_after_archive=False,
        refresh_lifecycle_after_archive=False,
    )

    assert archived["id"] == "chat-current"
    assert archived["pinned"] is True
    assert archived["title"] == "current title"
    assert archived["turns"] == current_turns
    assert "duplicate_only" not in archived
    assert [str(chat.get("id") or "").strip() for chat in frame.archived_chats] == ["chat-current"]


def test_load_chat_as_current_coerces_string_title_manual_false(frame):
    frame._load_chat_as_current(
        {
            "id": "chat-a",
            "title": "神匠工坊",
            "title_manual": "false",
            "turns": [
                {"question": "安卓自动化测试方案怎么做", "answer_md": "先按层次划分", "model": "openai/gpt-5.2", "created_at": 1.0},
                {"question": "再帮我整理成执行清单", "answer_md": "可以整理成清单", "model": "openai/gpt-5.2", "created_at": 2.0},
            ],
        }
    )

    archived = frame._archive_active_session(quick_title=True)

    assert archived is not None
    assert archived["title_manual"] is False
    assert archived["title"] == "神匠工坊"
    assert "整理成执行清单"[:6] not in archived["title"]


def test_next_default_chat_title_uses_xinliaotian_sequence(frame):
    frame.archived_chats = [
        {"id": "chat-a", "title": "新聊天", "turns": [], "created_at": 1.0, "updated_at": 1.0},
        {"id": "chat-b", "title": "新聊天1", "turns": [], "created_at": 2.0, "updated_at": 2.0},
    ]
    frame._current_chat_state["title"] = "别的标题"

    assert frame._next_default_chat_title() == "新聊天2"


def test_next_default_chat_title_treats_legacy_placeholder_as_default(frame):
    frame.archived_chats = [
        {"id": "chat-a", "title": "新聊天", "turns": [], "created_at": 1.0, "updated_at": 1.0},
        {"id": "chat-b", "title": "心聊天1", "turns": [], "created_at": 2.0, "updated_at": 2.0},
    ]

    assert frame._next_default_chat_title() == "新聊天1"


def test_refresh_history_normalizes_legacy_placeholder_title(frame):
    frame.archived_chats = [
        {"id": "chat-a", "title": "新聊天", "turns": [], "created_at": 1.0, "updated_at": 1.0},
    ]

    frame._refresh_history()

    assert list(frame.history_list.GetStrings()) == [main.EMPTY_CURRENT_CHAT_TITLE]


def test_load_state_keeps_current_summary_but_projects_one_history_row(frame, monkeypatch):
    frame.state_path.write_text(
        json.dumps(
            {
                "selected_model_id": main.DEFAULT_CODEX_MODEL,
                "active_chat_id": "chat-current",
                "active_chat": {"id": "chat-current", "title": "current", "turns": []},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        frame.chat_store,
        "list_chat_summaries",
        lambda: [
            {"id": " chat-current ", "title": "summary current", "updated_at": 2.0},
            {"id": "chat-old", "title": "old", "updated_at": 1.0},
        ],
    )
    monkeypatch.setattr(frame.chat_store, "load_turns", lambda chat_id: [])

    frame._load_state()
    frame._refresh_history("chat-current")

    assert len(frame.archived_chats) == 2
    assert frame.history_ids == ["chat-current", "chat-old"]
    assert frame.history_list.GetCount() == 2


def test_generate_first_question_title_retries_three_times_then_keeps_default(frame, monkeypatch):
    seen = {"calls": 0}

    class _FailingClient:
        def generate_chat_title(self, _prompt):
            seen["calls"] += 1
            return ""

    monkeypatch.setattr(main, "ChatClient", lambda *args, **kwargs: _FailingClient())

    title = frame._generate_first_question_title("帮我整理安卓自动化测试方案")

    assert title == ""
    assert seen["calls"] == 3


def test_apply_generated_first_question_title_accepts_legacy_default_title(frame, monkeypatch):
    saved = []
    deferred = []
    pushed = []
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state.update(
        {
            "id": "chat-current",
            "title": "新聊天",
            "title_manual": False,
            "title_source": "default",
            "title_updated_at": 1.0,
            "title_revision": 1,
            "turns": [],
        }
    )
    monkeypatch.setattr(frame, "_save_state", lambda: saved.append(True))
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: deferred.append(True))
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda chat_id="": pushed.append(chat_id))

    frame._apply_generated_first_question_title("chat-current", "帮我整理安卓自动化测试方案", "自动化测试", 1)

    assert frame._current_chat_state["title"] == "自动化测试"
    assert frame._current_chat_state["title_source"] == "auto"
    assert saved == []
    assert deferred == []
    assert pushed == ["chat-current"]


def test_generated_first_question_title_rejects_stale_revision_without_ui_mutation(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state.update(
        {
            "id": "chat-current",
            "title": "manual title",
            "title_manual": True,
            "title_source": "manual",
            "title_revision": 2,
        }
    )
    monkeypatch.setattr(frame, "_upsert_history_row", lambda *_args, **_kwargs: pytest.fail("stale callback repainted"))
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *_args, **_kwargs: pytest.fail("stale callback published"))

    frame._apply_generated_first_question_title(
        "chat-current", "first question", "generated title", expected_title_revision=1
    )

    assert frame._current_chat_state["title"] == "manual title"
    assert frame._current_chat_state["title_revision"] == 2


def test_generated_title_updates_offscreen_owner_without_moving_visible_selection(frame, monkeypatch):
    frame._chat_store_enabled = True
    frame.active_chat_id = frame.current_chat_id = "chat-a"
    frame._current_chat_state.update({"id": "chat-a", "title": "A", "title_revision": 1})
    owner_b = {
        "id": "chat-b", "title": "新聊天", "title_manual": False, "title_source": "default",
        "title_revision": 1, "title_updated_at": 1.0, "created_at": 1.0, "updated_at": 1.0,
        "model": "codex/main", "turns": [],
    }
    frame.archived_chats = [owner_b]
    frame.chat_store.upsert_chat(owner_b)
    frame._refresh_history("chat-a")
    selected_before = frame.history_ids[frame.history_list.GetSelection()]
    frame.input_edit.SetFocus()

    frame._apply_generated_first_question_title("chat-b", "first question", "B title", 1)

    assert owner_b["title"] == "B title"
    assert owner_b["title_revision"] == 2
    assert frame.chat_store.load_chat("chat-b")["title_revision"] == 2
    assert frame.history_ids[frame.history_list.GetSelection()] == selected_before
    assert frame.input_edit.HasFocus()


def test_generated_title_for_deleted_owner_is_noop(frame, monkeypatch):
    frame.active_chat_id = frame.current_chat_id = "chat-a"
    frame.archived_chats = []
    monkeypatch.setattr(frame, "_upsert_history_row", lambda *_args, **_kwargs: pytest.fail("deleted owner projected"))
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *_args, **_kwargs: pytest.fail("deleted owner published"))

    frame._apply_generated_first_question_title("deleted-chat", "first question", "generated", 1)

    assert frame._find_archived_chat("deleted-chat") is None


def test_generated_title_transient_persist_failure_resets_schedule_and_retry_succeeds(frame, monkeypatch):
    frame._chat_store_enabled = True
    frame.active_chat_id = frame.current_chat_id = "chat-retry"
    frame._current_chat_state.update(
        {"id": "chat-retry", "title": "新聊天", "title_manual": False, "title_source": "default",
         "title_revision": 1, "title_updated_at": 1.0, "created_at": 1.0, "updated_at": 1.0,
         "model": "codex/main", "turns": [], "first_question_auto_title_scheduled": True}
    )
    frame.chat_store.upsert_chat(frame._slim_active_chat_state())
    real_upsert = frame.chat_store.upsert_chat
    published = []
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *args: published.append(args))

    def fail_auto(chat):
        if str(chat.get("title_source") or "") == "auto":
            raise OSError("transient")
        return real_upsert(chat)

    monkeypatch.setattr(frame.chat_store, "upsert_chat", fail_auto)
    frame._apply_generated_first_question_title("chat-retry", "question", "generated", 1)
    assert frame._current_chat_state["title"] == "新聊天"
    assert frame._current_chat_state["first_question_auto_title_scheduled"] is False
    assert published == []

    monkeypatch.setattr(frame.chat_store, "upsert_chat", real_upsert)
    frame._apply_generated_first_question_title("chat-retry", "question", "generated", 1)
    assert frame._current_chat_state["title_revision"] == 2
    assert frame.chat_store.load_chat("chat-retry")["title"] == "generated"
    assert len(published) == 1


def test_generated_title_mismatched_reload_is_silent_and_retryable(frame, monkeypatch):
    frame._chat_store_enabled = True
    frame.active_chat_id = frame.current_chat_id = "chat-mismatch"
    frame._current_chat_state.update(
        {"id": "chat-mismatch", "title": "新聊天", "title_manual": False, "title_source": "default",
         "title_revision": 1, "title_updated_at": 1.0, "created_at": 1.0, "updated_at": 1.0,
         "model": "codex/main", "turns": [], "first_question_auto_title_scheduled": True}
    )
    frame.chat_store.upsert_chat(frame._slim_active_chat_state())
    real_load = frame.chat_store.load_chat
    published = []
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *args: published.append(args))
    monkeypatch.setattr(frame.chat_store, "load_chat", lambda *_args, **_kwargs: {"id": "chat-mismatch", "title": "wrong", "title_source": "auto", "title_revision": 2})

    frame._apply_generated_first_question_title("chat-mismatch", "question", "generated", 1)
    assert frame._current_chat_state["title"] == "新聊天"
    assert frame._current_chat_state["first_question_auto_title_scheduled"] is False
    assert published == []

    monkeypatch.setattr(frame.chat_store, "load_chat", real_load)
    frame._apply_generated_first_question_title("chat-mismatch", "question", "generated", 1)
    assert frame._current_chat_state["title_revision"] == 2
    assert len(published) == 1


def test_default_title_allocation_uses_durable_inventory_and_reuses_smallest_gap(frame, monkeypatch):
    frame._chat_store_enabled = True
    frame.archived_chats = []
    frame._current_chat_state = {"id": "current", "title": "other"}
    monkeypatch.setattr(
        frame.chat_store,
        "list_chat_summaries",
        lambda: [
            {"id": "a", "title": "新聊天"},
            {"id": "c", "title": "新聊天2"},
            {"id": "similar", "title": "新聊天x"},
        ],
    )

    assert frame._next_default_chat_title() == "新聊天1"


def test_schedule_first_question_auto_title_respects_manual_lock(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state.update(
        {
            "id": "chat-current",
            "title": "手动标题",
            "title_manual": True,
            "title_source": "manual",
            "title_updated_at": 20.0,
            "title_revision": 4,
        }
    )
    pushed = []
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda chat_id="": pushed.append(chat_id))

    class _ImmediateThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}

        def start(self):
            self._target(*self._args, **self._kwargs)

    monkeypatch.setattr(main.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(frame, "_generate_first_question_title", lambda _question: "自动标题")

    frame._schedule_first_question_auto_title("chat-current", "首次提问")

    assert frame._current_chat_state["title"] == "手动标题"
    assert pushed == []


def test_resolve_chat_target_returns_archived_chat_for_history_id(frame):
    archived = {
        "id": "hist-1",
        "title": "历史聊天",
        "turns": [],
        "created_at": 1.0,
        "updated_at": 1.0,
    }
    frame.archived_chats = [archived]

    chat, is_current = frame._resolve_chat_target("hist-1")

    assert chat is archived
    assert is_current is False


def test_compact_first_question_title_removes_request_wrappers(frame):
    title = frame._compact_first_question_title("帮我梳理桌面端聊天命名规则怎么改")

    assert title == "桌面端聊天命名规则"


def test_compact_first_question_title_removes_what_is_wrapper(frame):
    title = frame._compact_first_question_title("请问一下什么是 MCP 协议")

    assert title == "MCP 协议"


def test_compact_first_question_title_prefers_concise_topic_phrase(frame):
    title = frame._compact_first_question_title("用户需要介绍好吃的")

    assert title == "美食推荐"


def test_generate_first_question_title_compacts_verbose_model_output(frame, monkeypatch):
    class _VerboseClient:
        def generate_chat_title(self, _prompt):
            return "用户需要介绍好吃的"

    monkeypatch.setattr(main, "ChatClient", lambda *args, **kwargs: _VerboseClient())

    title = frame._generate_first_question_title("给我推荐一些好吃的")

    assert title == "美食推荐"


def test_generate_first_question_title_ignores_answer_style_output_for_question(frame, monkeypatch):
    class _AnsweringClient:
        def generate_chat_title(self, _prompt):
            return "MCP 是一种模型上下文协议"

    monkeypatch.setattr(main, "ChatClient", lambda *args, **kwargs: _AnsweringClient())

    title = frame._generate_first_question_title("什么是 MCP 协议？")

    assert title == "MCP 协议"


def test_unique_chat_title_appends_numeric_suffix_for_duplicates(frame):
    frame.archived_chats = [
        {"id": "chat-a", "title": "项目", "turns": []},
        {"id": "chat-b", "title": "项目1", "turns": []},
    ]
    frame._current_chat_state = {"id": "current", "title": "当前", "turns": []}

    assert frame._unique_chat_title("项目") == "项目2"


def test_new_named_chat_uses_unique_manual_title(frame, monkeypatch):
    frame.archived_chats = [
        {"id": "chat-a", "title": "项目", "turns": [], "created_at": 1.0, "updated_at": 1.0}
    ]
    monkeypatch.setattr(frame, "_archive_active_session", lambda **_kwargs: None)
    monkeypatch.setattr(frame, "_ensure_active_chat_id", lambda: "chat-new")
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_mark_openclaw_lifecycle_dirty", lambda: None)

    frame._on_new_chat_clicked(None, title="项目", title_manual=True)

    assert frame._current_chat_state["title"] == "项目1"
    assert frame._current_chat_state["title_manual"] is True


def test_remote_api_clear_context_clears_requested_archived_chat_without_touching_active_chat(frame, monkeypatch):
    frame.active_chat_id = "chat-a"
    frame.current_chat_id = "chat-a"
    frame.active_session_turns = [
        {
            "question": "A 正在执行",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL,
            "request_status": "pending",
            "created_at": 1.0,
        }
    ]
    frame.active_codex_thread_id = "thread-a"
    frame.active_codex_turn_id = "turn-a"
    frame.active_codex_turn_active = True
    frame._current_chat_state = {
        "id": "chat-a",
        "title": "聊天 A",
        "turns": frame.active_session_turns,
        "codex_thread_id": "thread-a",
        "codex_turn_id": "turn-a",
        "codex_turn_active": True,
        "execution_steps": [{"list_text": "A 执行过程"}],
    }
    frame.archived_chats = [
        {
            "id": "chat-b",
            "title": "聊天 B",
            "turns": [
                {
                    "question": "B 旧问题",
                    "answer_md": "B 旧回答",
                    "model": main.DEFAULT_CODEX_MODEL,
                    "created_at": 2.0,
                }
            ],
            "codex_thread_id": "thread-b",
            "codex_turn_id": "turn-b",
            "codex_turn_active": True,
            "codex_pending_prompt": "prompt-b",
            "codex_pending_request": {"kind": "user_input"},
            "codex_request_queue": [{"prompt": "queued-b"}],
            "codex_thread_flags": ["waitingOnUserInput"],
            "codex_latest_assistant_text": "partial-b",
            "codex_latest_assistant_phase": "answer",
            "openclaw_session_key": "openclaw-b",
            "openclaw_session_id": "openclaw-session-b",
            "openclaw_session_file": "openclaw-b.jsonl",
            "openclaw_sync_offset": 9,
            "openclaw_last_event_id": "openclaw-event-b",
            "openclaw_last_synced_at": 123.0,
            "claudecode_session_id": "claude-b",
            "context_usage": {"input_tokens": 1},
            "execution_steps": [{"list_text": "B 执行过程"}],
            "created_at": 2.0,
            "updated_at": 3.0,
        }
    ]
    pushed = []
    monkeypatch.setattr(frame, "_clear_context_and_start_new_chat", lambda: pytest.fail("remote clear for archived chat must not clear active chat"))
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda chat_id: pushed.append(chat_id))
    monkeypatch.setattr(frame, "_push_remote_state", lambda chat_id: pushed.append(f"state:{chat_id}"))
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    status, body = frame._remote_api_clear_context_ui({"chat_id": "chat-b"})

    archived = frame._find_archived_chat("chat-b")
    assert status == 200
    assert body["accepted"] is True
    assert body["chat_id"] == "chat-b"
    assert isinstance(body["revision"], int)
    assert set(body["clear_operation"]) == {"operation_id", "revision", "state"}
    assert body["clear_operation"]["revision"] == body["revision"]
    assert body["clear_operation"]["operation_id"] == archived["turns"][0]["clear_operation_id"]
    assert not ({"snapshot", "attachments", "path"} & set(body))
    assert frame.active_chat_id == "chat-a"
    assert frame.current_chat_id == "chat-a"
    assert frame.active_session_turns[0]["question"] == "A 正在执行"
    assert frame.active_codex_thread_id == "thread-a"
    assert frame.active_codex_turn_active is True
    assert frame._current_chat_state["execution_steps"] == [{"list_text": "A 执行过程"}]
    assert len(archived["turns"]) == 1
    assert archived["turns"][0]["question"].startswith("B ")
    assert archived["turns"][0]["clear_operation_id"]
    assert archived["codex_thread_id"] == ""
    assert archived["codex_turn_id"] == ""
    assert archived["codex_turn_active"] is False
    assert archived["codex_pending_prompt"] == ""
    assert archived["codex_pending_request"] is None
    assert archived["codex_request_queue"] == []
    assert archived["codex_thread_flags"] == []
    assert archived["codex_latest_assistant_text"] == ""
    assert archived["codex_latest_assistant_phase"] == ""
    assert archived["openclaw_session_key"] == main.DEFAULT_OPENCLAW_SESSION_KEY
    assert archived["openclaw_session_id"] == ""
    assert archived["openclaw_session_file"] == ""
    assert archived["openclaw_sync_offset"] == 0
    assert archived["openclaw_last_event_id"] == ""
    assert archived["openclaw_last_synced_at"] == 0.0
    assert archived["claudecode_session_id"] == ""
    assert archived["context_usage"] is None
    assert archived["execution_steps"] == []
    assert pushed == ["chat-b", "state:chat-b"]


def test_cd_first_question_auto_title_uses_directory_name_and_unique_suffix(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.archived_chats = [{"id": "chat-old", "title": "sj", "turns": []}]
    frame._current_chat_state = {
        "id": "chat-current",
        "title": main.EMPTY_CURRENT_CHAT_TITLE,
        "title_manual": False,
        "title_source": "default",
        "title_revision": 1,
        "turns": [],
    }
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: None)
    monkeypatch.setattr(frame, "_refresh_history", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main.threading, "Thread", lambda *args, **kwargs: type("_NoThread", (), {"start": lambda self: None})())

    frame._schedule_first_question_auto_title("chat-current", r"cd c:\code\sj")

    assert frame._current_chat_state["title"] == "sj1"
    assert frame._current_chat_state["title_source"] == "auto"


def test_summarize_recent_topic_uses_normalized_default_title(frame):
    assert frame._summarize_recent_topic([], "") == main.EMPTY_CURRENT_CHAT_TITLE


def test_apply_archived_title_normalizes_legacy_placeholder_fallback(frame, monkeypatch):
    frame.archived_chats = [
        {
            "id": "chat-old",
            "title": "新聊天",
            "title_manual": False,
            "turns": [],
            "created_at": 1.0,
            "updated_at": 1.0,
        }
    ]
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_refresh_history", lambda *_args, **_kwargs: None)

    frame._apply_archived_title("chat-old", "")

    archived = frame._find_archived_chat("chat-old")
    assert archived["title"] == main.EMPTY_CURRENT_CHAT_TITLE


def test_load_chat_title_rules_reads_shared_json(tmp_path):
    custom_rules = {
        "leading_phrases": ["绝对前缀"],
        "action_prefixes": [],
        "what_is_prefixes": [],
        "question_suffixes": [],
        "trailing_punctuation": ["?"],
    }
    path = tmp_path / "chat_title_rules.json"
    path.write_text(json.dumps(custom_rules, ensure_ascii=False), encoding="utf-8")

    rules = main.load_chat_title_rules(path=path, refresh=True)

    assert rules["leading_phrases"] == ["绝对前缀"]


@pytest.mark.parametrize("content", [None, b"\xff", b"not-json", b"[]", b'{"leading_phrases": null}'])
def test_load_chat_title_rules_failure_falls_back_to_empty_rules(tmp_path, content):
    path = tmp_path / "chat_title_rules.json"
    if content is not None:
        path.write_bytes(content)

    rules = main.load_chat_title_rules(path=path, refresh=True)

    assert rules == {
        "leading_phrases": [],
        "action_prefixes": [],
        "what_is_prefixes": [],
        "question_suffixes": [],
        "trailing_punctuation": [],
    }


def test_load_chat_title_rules_retries_after_default_file_read_failure(monkeypatch, tmp_path):
    path = tmp_path / "chat_title_rules.json"
    monkeypatch.setattr(main, "_CHAT_TITLE_RULES_CACHE", None)
    monkeypatch.setattr(main, "shared_chat_title_rules_path", lambda: path)

    assert main.load_chat_title_rules()["leading_phrases"] == []
    path.write_text('{"leading_phrases": ["后来可用"]}', encoding="utf-8")

    assert main.load_chat_title_rules()["leading_phrases"] == ["后来可用"]


def test_shared_chat_title_rules_path_points_to_repo_assets():
    expected = Path(main.__file__).resolve().parents[1] / "rc" / "assets" / "chat_title_rules.json"
    assert main.shared_chat_title_rules_path() == expected


def test_shared_chat_title_rules_path_frozen_uses_bundled_asset(monkeypatch, tmp_path):
    bundled_root = tmp_path / "_internal"
    bundled_rules = bundled_root / "assets" / "chat_title_rules.json"
    bundled_rules.parent.mkdir(parents=True)
    bundled_rules.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(main.sys, "frozen", True, raising=False)
    monkeypatch.setattr(main.sys, "_MEIPASS", str(bundled_root), raising=False)

    assert main.shared_chat_title_rules_path() == bundled_rules


def test_title_source_turns_keeps_only_first_question(frame):
    turns = [
        {"question": "q1", "answer_md": "a1"},
        {"question": "q2", "answer_md": "a2"},
        {"question": "q3", "answer_md": "a3"},
        {"question": "q4", "answer_md": "a4"},
    ]
    selected = frame._title_source_turns(turns)
    assert len(selected) == 1
    assert selected[0]["question"] == "q1"


def test_title_source_turns_uses_first_question_when_short(frame):
    turns = [
        {"question": "q1", "answer_md": "a1"},
        {"question": "q2", "answer_md": "a2"},
    ]
    selected = frame._title_source_turns(turns)
    assert len(selected) == 1
    assert selected[0]["question"] == "q1"


def test_new_chat_archives_with_async_rename(frame):
    frame.active_session_turns = [
        {"question": "旧问题", "answer_md": "旧回答", "model": "openai/gpt-5.2", "created_at": time.time()}
    ]
    seen = {"quick": None, "async": None}

    def fake_archive(quick_title=False, schedule_async_rename=False, **_kwargs):
        seen["quick"] = quick_title
        seen["async"] = schedule_async_rename
        return None

    frame._archive_active_session = fake_archive
    frame._on_new_chat_clicked(None)
    assert seen["quick"] is True
    assert seen["async"] is True


def test_background_done_updates_archived_chat_without_switching_current(frame, monkeypatch):
    frame.active_chat_id = "chat-new"
    frame.active_session_turns = []
    frame.archived_chats = [
        {
            "id": "chat-old",
            "title": "旧聊天",
            "turns": [{"question": "旧问题", "answer_md": main.REQUESTING_TEXT, "model": "openai/gpt-5.2", "created_at": 1.0}],
            "created_at": 1.0,
            "updated_at": 1.0,
        }
    ]
    frame._active_request_count = 1
    rendered = {"count": 0}
    statuses = []
    monkeypatch.setattr(frame, "_render_answer_list", lambda: rendered.__setitem__("count", rendered["count"] + 1))
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_set_input_hint_idle", lambda: None)
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)
    frame.SetStatusText = lambda text: statuses.append(text)

    frame._on_done(0, "后台旧回答", "", "openai/gpt-5.2", "", "chat-old")

    assert frame.current_chat_id == "chat-new"
    archived = frame._find_archived_chat("chat-old")
    assert archived["turns"][0]["answer_md"] == "后台旧回答"
    assert rendered["count"] == 0
    assert statuses == []


def test_background_done_does_not_generate_title_over_network_on_ui_thread(frame, monkeypatch):
    frame.active_chat_id = "chat-new"
    frame.current_chat_id = "chat-new"
    frame.active_session_turns = []
    frame.archived_chats = [
        {
            "id": "chat-old",
            "title": main.EMPTY_CURRENT_CHAT_TITLE,
            "turns": [
                {
                    "question": "请帮我分析这个卡顿问题",
                    "answer_md": main.REQUESTING_TEXT,
                    "model": "openai/gpt-5.2",
                    "created_at": 1.0,
                }
            ],
            "created_at": 1.0,
            "updated_at": 1.0,
        }
    ]
    monkeypatch.setenv("OPENROUTER_API_KEY", "configured")
    monkeypatch.setattr(
        main.ChatClient,
        "generate_chat_title",
        lambda *_args, **_kwargs: pytest.fail("background completion must not call network title generation on UI thread"),
    )
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_refresh_history", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_set_input_hint_idle", lambda: None)
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)
    monkeypatch.setattr(frame, "SetStatusText", lambda _text: None)

    frame._on_done(0, "后台旧回答", "", "openai/gpt-5.2", "", "chat-old")

    archived = frame._find_archived_chat("chat-old")
    assert archived["turns"][0]["answer_md"] == "后台旧回答"
    assert archived["title"]


def test_summarize_recent_topic_never_uses_network_title_generation(frame, monkeypatch):
    monkeypatch.setattr(
        main.ChatClient,
        "generate_chat_title",
        lambda *_args, **_kwargs: pytest.fail("recent topic summary must stay local for UI responsiveness"),
    )

    title = frame._summarize_recent_topic(
        [{"question": "请继续深度审查读屏卡顿问题", "answer_md": "ok", "model": "openai/gpt-5.2"}],
        "configured-api-key",
    )

    assert title


def test_generated_title_does_not_change_history_recency_order(frame, monkeypatch):
    frame.archived_chats = [
        {
            "id": "old-chat",
            "title": "新聊天",
                "title_source": "default",
                "title_manual": False,
                "title_revision": 1,
                "title_updated_at": 1.0,
                "model": "openclaw/main",
            "created_at": 1.0,
            "updated_at": 1.0,
            "turns": [{"question": "old question", "answer_md": "old answer", "model": "openclaw/main", "created_at": 1.0}],
        },
        {
            "id": "new-chat-1",
            "title": "new one",
            "created_at": 10.0,
            "updated_at": 10.0,
            "turns": [{"question": "new one", "answer_md": "answer", "model": "openclaw/main", "created_at": 10.0}],
        },
        {
            "id": "new-chat-2",
            "title": "new two",
            "created_at": 9.0,
            "updated_at": 9.0,
            "turns": [{"question": "new two", "answer_md": "answer", "model": "openclaw/main", "created_at": 9.0}],
        },
    ]
    monkeypatch.setattr(main.time, "time", lambda: 100.0)
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *_args, **_kwargs: None)

    frame._apply_generated_first_question_title("old-chat", "old question", "old renamed", 1)
    frame._sort_archived_chats()

    assert [chat["id"] for chat in frame.archived_chats] == ["new-chat-1", "new-chat-2", "old-chat"]
    assert frame._find_archived_chat("old-chat")["updated_at"] == 1.0
    assert frame._find_archived_chat("old-chat")["title_updated_at"] == 100.0


def test_remote_title_update_does_not_change_history_recency_order(frame, monkeypatch):
    frame.archived_chats = [
        {
            "id": "old-chat",
            "title": "old",
            "title_source": "default",
            "title_manual": False,
            "title_updated_at": 1.0,
            "title_revision": 1,
            "created_at": 1.0,
            "updated_at": 1.0,
            "turns": [],
        },
        {"id": "new-chat", "title": "new", "created_at": 10.0, "updated_at": 10.0, "turns": []},
    ]
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *_args, **_kwargs: None)

    status, body = frame._remote_api_rename_chat_ui(
        {
            "chat_id": "old-chat",
            "title": "renamed remotely",
            "title_source": "manual",
            "title_updated_at": 100.0,
            "title_revision": 2,
        }
    )
    frame._sort_archived_chats()

    assert status == 200
    assert body["accepted"] is True
    assert [chat["id"] for chat in frame.archived_chats] == ["new-chat", "old-chat"]
    assert frame._find_archived_chat("old-chat")["updated_at"] == 1.0
    assert frame._find_archived_chat("old-chat")["title_updated_at"] == 100.0


def test_background_delta_updates_archived_chat_without_switching_current(frame, monkeypatch):
    frame.active_chat_id = "chat-new"
    frame.active_session_turns = []
    frame.archived_chats = [
        {
            "id": "chat-old",
            "title": "旧聊天",
            "turns": [{"question": "旧问题", "answer_md": main.REQUESTING_TEXT, "model": "openai/gpt-5.2", "created_at": 1.0}],
            "created_at": 1.0,
            "updated_at": 1.0,
        }
    ]
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    frame._on_delta(0, "旧聊天流式片段", "chat-old")

    assert frame.current_chat_id == "chat-new"
    archived = frame._find_archived_chat("chat-old")
    assert archived["turns"][0]["answer_md"] == "旧聊天流式片段"


def test_archive_active_session_preserves_manual_current_title(frame):
    frame.active_chat_id = "chat-manual"
    frame._current_chat_state["title"] = "手动标题"
    frame._current_chat_state["title_manual"] = True
    frame.active_session_turns = [
        {"question": "旧问题", "answer_md": "旧回答", "model": "codex/main", "created_at": time.time()}
    ]

    archived = frame._archive_active_session(quick_title=True, schedule_async_rename=False)

    assert archived is not None
    assert archived["title"] == "手动标题"
    assert archived["title_manual"] is True


def test_history_delete_removes_current_chat_from_persisted_state(frame, monkeypatch, tmp_path):
    frame.state_path = tmp_path / "app_state.json"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state["id"] = "chat-current"
    frame._current_chat_state["title"] = "当前聊天"
    frame.active_session_turns = [
        {"question": "当前问题", "answer_md": "当前回答", "model": "codex/main", "created_at": time.time()}
    ]
    frame.archived_chats = [
        {"id": "chat-old", "title": "旧聊天", "turns": [], "created_at": 1.0, "updated_at": 1.0, "pinned": False}
    ]
    frame.history_ids = ["chat-current", "chat-old"]
    frame.history_list.Set(["[当前] 当前聊天", "旧聊天"])
    frame.history_list.SetSelection(0)
    monkeypatch.setattr(frame, "_confirm", lambda *_args, **_kwargs: True)

    frame._history_delete(None)

    data = json.loads(frame.state_path.read_text(encoding="utf-8"))
    archived_ids = {str(chat.get("id") or "") for chat in data.get("archived_chats") or []}
    assert "chat-current" not in archived_ids
    assert data.get("active_chat_id") != "chat-current"
    assert data.get("active_session_turns") == []


def test_on_close_marks_archived_chat_pending_turns_before_save(frame, wait_for_wx):
    frame.active_chat_id = "chat-current"
    frame._current_chat_state["id"] = "chat-current"
    frame.active_session_turns = []
    frame.archived_chats = [
        {
            "id": "chat-old",
            "title": "旧聊天",
            "turns": [
                {
                    "question": "历史可恢复请求",
                    "answer_md": main.REQUESTING_TEXT,
                    "model": "openai/gpt-5.2",
                    "created_at": 1.0,
                    "request_status": "pending",
                    "request_recoverable": True,
                },
                {
                    "question": "历史不可恢复请求",
                    "answer_md": main.REQUESTING_TEXT,
                    "model": "openai/gpt-5.2",
                    "created_at": 2.0,
                    "request_status": "pending",
                    "request_recoverable": False,
                },
            ],
            "created_at": 1.0,
            "updated_at": 1.0,
            "pinned": False,
        }
    ]
    frame.history_ids = ["chat-old"]

    database = frame.chat_store.db_path
    frame.Close()
    wait_for_wx(lambda: getattr(frame, "_close_complete", False), "close-persistence")
    turns = main.ChatStore(database).load_turns("chat-old")
    assert turns[0]["request_status"] == "pending"
    assert turns[0]["request_recoverable"] is True
    assert turns[1]["request_status"] == "pending"
    assert turns[1]["request_recoverable"] is False


def test_close_persists_other_rows_and_attention_after_retired_notification_conflict(tmp_path):
    from mc_frame_history import ChatHistoryMixin

    store = main.ChatStore(tmp_path / "history.sqlite3")
    store.initialize()
    owner, other = "owner", "other"
    old = {"question": "same question", "answer_md": "old answer",
           "request_status": "done", "question_origin": "mc"}
    store.upsert_chat({"id": owner, "title": "Owner"})
    store.replace_turns(owner, [old])
    ChatHistoryMixin._persist_close_turn_facts(store, "default", owner, "Owner", 0, old)
    store.begin_clear_operation(owner, idempotency_key="local-before-close")
    new = dict(old, answer_md="new answer")
    failed = {"question": "other question", "request_status": "failed",
              "request_error": "provider failed", "client_send_id": "failed-request"}
    path = tmp_path / "state.json"
    rows = [{"id": owner, "title": "Owner", "turns": [new]},
            {"id": other, "title": "Other", "turns": [failed]}]
    ChatHistoryMixin._write_close_batch(
        (store, path, {"saved": True}, rows, {owner: 0, other: 0}, "default", "default", (), (), ()),
    )

    reopened = main.ChatStore(store.db_path)
    assert reopened.load_turns(owner)[0]["answer_md"] == "new answer"
    assert reopened.load_turns(other)[0]["request_status"] == "failed"
    assert json.loads(path.read_text(encoding="utf-8")) == {"saved": True}
    events = [json.loads(bytes(row["payload"]))
              for row in reopened.pending_outbox(pair_id="default", domain="events")]
    assert [event["body"]["text"] for event in events if event["kind"] == "failed"] == ["provider failed"]


def test_cli_auto_title_updates_after_background_answer(frame, monkeypatch):
    frame.active_chat_id = "chat-new"
    frame.active_session_turns = []
    frame.archived_chats = [
        {
            "id": "chat-old",
            "title": "新聊天",
            "title_manual": False,
            "turns": [{"question": "帮我修复 CLI 刷新问题", "answer_md": main.REQUESTING_TEXT, "model": "codex/main", "created_at": 1.0}],
            "created_at": 1.0,
            "updated_at": 1.0,
        }
    ]
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_refresh_history", lambda *args, **kwargs: None)

    frame._on_done(0, "已修复，并补了测试", "", "codex/main", "", "chat-old")

    archived = frame._find_archived_chat("chat-old")
    assert archived["title"] != "新聊天"
    assert "CLI刷新问题" in archived["title"].replace(" ", "")


def test_claudecode_auto_title_updates_after_background_answer(frame, monkeypatch):
    frame.active_chat_id = "chat-new"
    frame.active_session_turns = []
    frame.archived_chats = [
        {
            "id": "chat-old",
            "title": "新聊天",
            "title_manual": False,
            "turns": [{"question": "帮我整理 ClaudeCode 命名规则", "answer_md": "", "model": "claudecode/default", "created_at": 1.0}],
            "created_at": 1.0,
            "updated_at": 1.0,
        }
    ]
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_refresh_history", lambda *args, **kwargs: None)

    frame._on_done(0, "已按规则整理完成", "", "claudecode/default", "", "chat-old")

    archived = frame._find_archived_chat("chat-old")
    assert archived["title"] != "新聊天"
    assert "ClaudeCode 命名规则"[:8] in archived["title"]


def test_merge_legacy_archived_chats_adds_missing_and_prefers_richer(frame, tmp_path, monkeypatch):
    current = {
        "id": "same-id",
        "title": "current",
        "created_at": 1.0,
        "turns": [{"question": "q1", "answer_md": "a1", "model": "openai/gpt-5.2", "created_at": 1.0}],
    }
    richer = {
        "id": "same-id",
        "title": "richer",
        "created_at": 2.0,
        "turns": [
            {"question": "q1", "answer_md": "a1", "model": "openai/gpt-5.2", "created_at": 1.0},
            {"question": "q2", "answer_md": "a2", "model": "openai/gpt-5.2", "created_at": 2.0},
        ],
    }
    extra = {
        "id": "extra-id",
        "title": "extra",
        "created_at": 3.0,
        "turns": [{"question": "x", "answer_md": "y", "model": "openai/gpt-5.2", "created_at": 3.0}],
    }
    legacy_path = tmp_path / "legacy_app_state.json"
    legacy_path.write_text(json.dumps({"archived_chats": [richer, extra]}, ensure_ascii=False), encoding="utf-8")

    frame.archived_chats = [current]
    frame.state_path = tmp_path / "current_app_state.json"
    frame.state_path.write_text(json.dumps({"archived_chats": [current]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(frame, "_legacy_state_paths", lambda: [frame.state_path, legacy_path])

    frame._merge_legacy_archived_chats()

    ids = {str(c.get("id")) for c in frame.archived_chats}
    assert ids == {"same-id", "extra-id"}
    same = next(c for c in frame.archived_chats if str(c.get("id")) == "same-id")
    assert len(same.get("turns") or []) == 2


def test_load_state_rebuilds_timestamp_like_archive_titles(monkeypatch, tmp_path):
    state = {
        "selected_model_id": "openai/gpt-5.2-chat",
        "archived_chats": [
            {
                "id": "chat-1",
                "title": "2026-03-08 10:30",
                "created_at": 1.0,
                "turns": [
                    {"question": "这是旧归档标题修复测试", "answer_md": "回答", "model": "openai/gpt-5.2", "created_at": 1.0}
                ],
            }
        ],
        "active_session_turns": [],
        "active_session_started_at": 0.0,
    }
    (tmp_path / "app_state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(main, "resolve_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(main.ChatFrame, "_migrate_legacy_state_if_needed", lambda self: None)
    monkeypatch.setattr(main.ChatFrame, "_merge_legacy_archived_chats", lambda self: None)
    f = main.ChatFrame()
    try:
        assert "这是旧归档标题修复测试"[:10] in f.archived_chats[0]["title"]
    finally:
        f.Destroy()


def test_normalize_archived_chat_preserves_manual_titles(frame):
    chat = {
        "id": "chat-1",
        "title": "手动改过的标题",
        "title_manual": True,
        "pinned": False,
        "turns": [{"question": "q", "answer_md": "a"}],
    }
    changed = frame._normalize_archived_chat(chat)
    assert changed is True
    assert chat["title"] == "手动改过的标题"
    assert chat["title_source"] == "manual"


def test_normalize_archived_chat_adds_detail_panel_defaults(frame):
    chat = {
        "id": "chat-1",
        "title": "手动改过的标题",
        "title_manual": True,
        "detail_panel_mode": "",
        "execution_steps": "legacy",
        "turns": [{"question": "q", "answer_md": "a"}],
    }

    changed = frame._normalize_archived_chat(chat)

    assert changed is True
    assert chat["detail_panel_mode"] == "answers"
    assert chat["execution_steps"] == []


def test_normalize_archived_chat_preserves_explicit_detail_panel_mode(frame):
    chat = {
        "id": "chat-2",
        "title": "执行清单聊天",
        "title_manual": True,
        "detail_panel_mode": "execution",
        "execution_steps": [{"step": "1"}],
        "turns": [{"question": "q", "answer_md": "a"}],
    }

    changed = frame._normalize_archived_chat(chat)

    assert changed is True
    assert chat["detail_panel_mode"] == "execution"
    assert chat["execution_steps"] == [{"step": "1"}]


def test_normalize_archived_chat_normalizes_invalid_detail_panel_mode_to_answers(frame):
    chat = {
        "id": "chat-3",
        "title": "旧聊天",
        "title_manual": True,
        "detail_panel_mode": "bogus",
        "execution_steps": [{"step": "1"}],
        "turns": [{"question": "q", "answer_md": "a"}],
    }

    changed = frame._normalize_archived_chat(chat)

    assert changed is True
    assert chat["detail_panel_mode"] == "answers"
    assert chat["execution_steps"] == [{"step": "1"}]


def test_archive_active_session_preserves_detail_panel_fields(frame, monkeypatch):
    frame.active_session_turns = [{"question": "q", "answer_md": "a", "model": "openai/gpt-5.2", "created_at": 1.0}]
    frame._current_chat_state = {
        "id": "chat-current",
        "title": "执行清单聊天",
        "title_manual": True,
        "title_source": "manual",
        "title_updated_at": 1.0,
        "title_revision": 3,
        "turns": frame.active_session_turns,
        "detail_panel_mode": "execution",
        "execution_steps": [{"step": "1"}],
    }
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    monkeypatch.setattr(frame, "_resolve_current_model", lambda: "openai/gpt-5.2")

    archived = frame._archive_active_session(quick_title=True)

    assert archived is not None
    assert archived["detail_panel_mode"] == "execution"
    assert archived["execution_steps"] == [{"step": "1"}]


def test_load_state_store_model_beats_stale_app_candidate(frame, tmp_path):
    frame.state_path = tmp_path / "app_state.json"
    frame.chat_store = main.ChatStore(tmp_path / "owner-model.db")
    frame.chat_store.initialize()
    frame._chat_store_enabled = True
    frame.chat_store.upsert_chat({"id": "chat-a", "title": "A", "model": "kimi/main", "created_at": 1.0, "updated_at": 2.0})
    frame.state_path.write_text(json.dumps({
        "selected_model_id": "openai/gpt-5.2",
        "active_chat_id": "chat-a",
        "active_chat": {"id": "chat-a", "model": "openai/gpt-5.2"},
    }), encoding="utf-8")

    frame._load_state()

    assert frame._current_chat_state["model"] == "kimi/main"


def test_remote_archived_owner_submission_restores_visible_owner_and_focus(frame, monkeypatch):
    frame.active_chat_id = frame.current_chat_id = "chat-visible"
    frame._current_chat_state = {"id": "chat-visible", "title": "Visible", "model": "codex/main", "turns": []}
    target = {"id": "chat-target", "title": "Target", "model": "kimi/main", "turns": []}
    frame.archived_chats = [target]
    frame.input_edit.SetValue("unsent foreground draft")
    frame.input_edit.SetSelection(2, 9)
    frame.input_edit.SetFocus()
    draft_before = frame.input_edit.GetValue()
    selection_before = frame.input_edit.GetSelection()
    insertion_before = frame.input_edit.GetInsertionPoint()
    dispatches = []
    pushes = []
    monkeypatch.setattr(frame, "_start_kimi_worker_for_turn", lambda *args: dispatches.append(args))
    monkeypatch.setattr(frame, "_switch_current_chat", lambda *_args: pytest.fail("background remote work must not switch foreground"))
    monkeypatch.setattr(frame, "_render_answer_list", lambda *_args, **_kwargs: pytest.fail("background remote work must not render"))
    monkeypatch.setattr(frame, "_refresh_history", lambda *_args, **_kwargs: pytest.fail("background remote work must not refresh history"))
    monkeypatch.setattr(frame, "_save_state", lambda *_args, **_kwargs: pytest.fail("background remote work must defer owner persistence"))
    monkeypatch.setattr(frame, "_push_remote_state", lambda chat_id: pushes.append(("state", chat_id)))
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda chat_id: pushes.append(("history", chat_id)))
    history_selection = frame.history_list.GetSelection()
    status, body = frame._remote_api_message_ui({"chat_id": "chat-target", "text": "hello"})

    assert (status, body["accepted"]) == (200, True)
    assert frame.active_chat_id == "chat-visible"
    assert frame._current_chat_state["model"] == "codex/main"
    assert frame.input_edit.HasFocus()
    assert frame.input_edit.GetValue() == draft_before
    assert frame.input_edit.GetSelection() == selection_before
    assert frame.input_edit.GetInsertionPoint() == insertion_before
    assert frame._find_archived_chat("chat-target")["model"] == "kimi/main"
    assert frame.history_list.GetSelection() == history_selection
    assert dispatches == [("chat-target", 0, "hello", "kimi/main")]
    assert pushes == [("state", "chat-target"), ("history", "chat-target")]


def test_archived_mobile_result_step_hydration_preserves_unflushed_ack(frame):
    frame._chat_store_enabled = True
    frame.active_chat_id = frame.current_chat_id = "chat-a"
    old_turn = {"question": "old", "answer_md": "old answer", "model": main.DEFAULT_CODEX_MODEL,
                "codex_thread_id": "old-thread", "codex_turn_id": "old-turn"}
    frame.chat_store.upsert_chat({"id": "chat-b", "title": "B", "model": main.DEFAULT_CODEX_MODEL,
                                  "codex_thread_id": "old-thread", "codex_turn_id": "old-turn"})
    frame.chat_store.replace_turns("chat-b", [old_turn])
    frame.chat_store.replace_execution_steps("chat-b", [
        {"turn_idx": 0, "event_type": "plan_updated", "display_kind": "commentary",
         "list_text": "saved step", "detail_text": "saved step"}
    ])
    chat = frame.chat_store.load_chat("chat-b", include_execution_steps=False)
    pending = {"question": "phone", "answer_md": main.REQUESTING_TEXT,
               "model": main.DEFAULT_CODEX_MODEL, "request_status": "pending",
               "codex_context_generation": 0}
    chat["turns"].append(pending)
    frame.archived_chats = [chat]
    turns = chat["turns"]
    frame._apply_codex_worker_thread_state("chat-b", {
        "chat_id": "chat-b", "turn_idx": 1, "thread_id": "new-thread",
        "turn_id": "new-turn", "context_generation": 0, "active": True,
    })
    assert frame.chat_store.load_chat("chat-b", include_execution_steps=False)["codex_thread_id"] == "old-thread"

    hydrated = frame._chat_state_for_execution_steps("chat-b")

    assert hydrated is chat
    assert frame.archived_chats[0] is chat
    assert chat["turns"] is turns
    assert chat["turns"][1] is pending
    assert chat["codex_thread_id"] == pending["codex_thread_id"] == "new-thread"
    assert chat["codex_turn_id"] == pending["codex_turn_id"] == "new-turn"
    assert chat["turns"][0] == old_turn
    assert chat["execution_steps"][0]["list_text"] == "saved step"


def test_archived_mobile_result_interleaved_completion_survives_store_reopen(frame, monkeypatch):
    frame._chat_store_enabled = True
    frame.active_chat_id = frame.current_chat_id = "chat-a"
    frame.active_session_turns = [{"question": "A", "answer_md": "A answer"}]
    frame._current_chat_state = {"id": "chat-a", "turns": frame.active_session_turns}
    old = {"question": "old", "answer_md": "old answer", "model": main.DEFAULT_CODEX_MODEL}
    for owner in ("chat-b", "chat-c"):
        frame.chat_store.upsert_chat({"id": owner, "title": owner, "model": main.DEFAULT_CODEX_MODEL,
                                      "codex_thread_id": f"old-{owner}"})
        frame.chat_store.replace_turns(owner, [old])
    frame.archived_chats = [frame.chat_store.load_chat(owner, include_execution_steps=False)
                            for owner in ("chat-b", "chat-c")]
    monkeypatch.setattr(frame, "_start_codex_worker_for_turn", lambda *_args: True)
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: None)
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)
    monkeypatch.setattr(frame, "_request_codex_chat_information", lambda *_args: None)
    monkeypatch.setattr(frame, "_refresh_visible_history_chat", lambda *_args: None)
    sounds = []
    monkeypatch.setattr(frame, "_play_send_sound", lambda: sounds.append("send"))
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: sounds.append("reply"))

    for owner in ("chat-b", "chat-c"):
        chat = frame._find_archived_chat(owner)
        assert frame._submit_archived_remote_question(chat, f"phone {owner}", main.DEFAULT_CODEX_MODEL) == (True, "")
        frame._apply_codex_worker_thread_state(owner, {
            "chat_id": owner, "turn_idx": 1, "thread_id": f"new-{owner}",
            "turn_id": f"turn-{owner}", "context_generation": 0, "active": True,
        })
        pending = frame._find_archived_chat(owner)["turns"][1]
        assert pending["codex_start_generation"] == 0
        assert pending["codex_context_generation"] == 1
    frame._flush_idle_ui_refreshes()
    assert frame._history_list_dirty is False
    for thread_id, turn_id in (("new-chat-c", "turn-chat-c"), ("new-chat-b", "stale-turn")):
        frame._on_codex_event_for_chat("chat-b", main.CodexEvent(
            type="item_completed", phase="final_answer", thread_id=thread_id,
            turn_id=turn_id, data={"turn_idx": 1, "context_generation": 0}, text="wrong owner answer"))
        assert frame._find_archived_chat("chat-b")["turns"][1]["answer_md"] == main.REQUESTING_TEXT
    for owner in ("chat-c", "chat-b"):
        chat = frame._find_archived_chat(owner)
        event = lambda kind, **kwargs: main.CodexEvent(
            type=kind, thread_id=f"new-{owner}", turn_id=f"turn-{owner}",
            data={"turn_idx": 1, "context_generation": 0}, **kwargs)
        frame._on_codex_event_for_chat(owner, event("plan_updated", text=f"step {owner}"))
        assert frame._find_archived_chat(owner) is chat
        frame._on_codex_event_for_chat(owner, event("item_completed", phase="final_answer", text=f"answer {owner}"))
        frame._on_codex_event_for_chat(owner, event("turn_completed", status="completed"))
        frame._on_codex_event_for_chat(owner, event("turn_completed", status="completed"))
        assert frame._history_list_dirty is True
    frame._persist_chat_history_to_store()

    reopened = main.ChatStore(frame.chat_store.db_path)
    for owner in ("chat-b", "chat-c"):
        loaded = reopened.load_chat(owner)
        assert [turn["question"] for turn in loaded["turns"]] == ["old", f"phone {owner}"]
        assert loaded["turns"][0]["answer_md"] == "old answer"
        assert loaded["turns"][1]["answer_md"] == f"answer {owner}"
        assert loaded["turns"][1]["request_status"] == "done"
        assert loaded["turns"][1]["codex_thread_id"] == f"new-{owner}"
    assert frame.active_session_turns == [{"question": "A", "answer_md": "A answer"}]
    assert sounds == ["send", "send", "reply", "reply"]


def test_archived_mobile_result_summary_hydration_merges_without_replacing_live_fields(frame):
    frame._chat_store_enabled = True
    frame.chat_store.upsert_chat({"id": "chat-b", "title": "stored title", "model": main.DEFAULT_CODEX_MODEL,
                                  "codex_thread_id": "stored-thread"})
    frame.chat_store.replace_turns("chat-b", [{"question": "old", "answer_md": "old answer"}])
    summary = frame.chat_store.list_chat_summaries()[0]
    summary["title"] = "live title"
    summary["codex_thread_id"] = "live-thread"
    frame.archived_chats = [summary]

    hydrated = frame._hydrate_chat_from_store(summary, include_execution_steps=False)

    assert hydrated is summary is frame.archived_chats[0]
    assert summary["title"] == "live title"
    assert summary["codex_thread_id"] == "live-thread"
    assert summary["model"] == main.DEFAULT_CODEX_MODEL
    assert summary["turns"][0]["answer_md"] == "old answer"
    assert frame._hydrate_chat_from_store(summary, include_execution_steps=False) is summary


def test_archived_mobile_result_empty_loaded_turns_do_not_resurrect_store_turns(frame):
    frame._chat_store_enabled = True
    frame.chat_store.upsert_chat({"id": "chat-b", "title": "B", "model": main.DEFAULT_CODEX_MODEL})
    frame.chat_store.replace_turns("chat-b", [{"question": "old", "answer_md": "old answer"}])
    frame.chat_store.replace_execution_steps("chat-b", [{
        "turn_idx": 0, "event_type": "plan_updated", "display_kind": "commentary",
        "list_text": "saved step", "detail_text": "saved step",
    }])
    empty_turns = []
    chat = {"id": "chat-b", "title": "live B", "turns": empty_turns}
    frame.archived_chats = [chat]

    hydrated = frame._hydrate_chat_from_store(chat)

    assert hydrated is chat is frame.archived_chats[0]
    assert chat["turns"] is empty_turns
    assert chat["turns"] == []
    assert chat["title"] == "live B"
    assert chat["execution_steps"][0]["list_text"] == "saved step"


@pytest.mark.parametrize("unavailable", ["missing_store", "closed_store"])
def test_archived_mobile_result_unavailable_store_preserves_live_chat(frame, monkeypatch, unavailable):
    frame._chat_store_enabled = True
    turn = {"question": "phone", "answer_md": main.REQUESTING_TEXT,
            "codex_thread_id": "new-thread", "request_status": "pending"}
    turns = [turn]
    chat = {"id": "chat-b", "turns": turns, "codex_thread_id": "new-thread"}
    frame.archived_chats = [chat]
    if unavailable == "missing_store":
        frame.chat_store = None
    else:
        monkeypatch.setattr(frame.chat_store, "load_execution_steps",
                            lambda *_args: (_ for _ in ()).throw(sqlite3.ProgrammingError("closed")))

    hydrated = frame._hydrate_chat_from_store(chat)

    assert hydrated is chat is frame.archived_chats[0]
    assert chat["turns"] is turns and turns[0] is turn
    assert chat["codex_thread_id"] == "new-thread"
    assert "execution_steps" not in chat


def test_archived_mobile_result_missing_summary_chat_stays_unchanged(frame):
    frame._chat_store_enabled = True
    summary = {"id": "missing-chat", "title": "live title", "codex_thread_id": "live-thread"}
    frame.archived_chats = [summary]

    hydrated = frame._hydrate_chat_from_store(summary)

    assert hydrated is summary is frame.archived_chats[0]
    assert summary == {"id": "missing-chat", "title": "live title", "codex_thread_id": "live-thread"}


def test_save_state_can_skip_chat_history_persistence_for_small_config_changes(frame, monkeypatch, tmp_path):
    frame.state_path = tmp_path / "app_state.json"
    frame._chat_store_enabled = True
    persists = []
    monkeypatch.setattr(frame, "_persist_chat_history_to_store", lambda: persists.append(True))

    frame._save_state(persist_chat_history=False)

    assert persists == []
    data = json.loads(frame.state_path.read_text(encoding="utf-8"))
    assert data["selected_model_id"] == frame.selected_model


def test_save_state_does_not_rewrite_clean_chat_turns(frame):
    frame._chat_store_enabled = True
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_session_turns = [
        {"question": "q0", "answer_md": "a0", "model": main.DEFAULT_MODEL_ID},
        {"question": "q1", "answer_md": "a1", "model": main.DEFAULT_MODEL_ID},
    ]
    frame._current_chat_state = {"id": "chat-current", "turns": frame.active_session_turns}

    class Store:
        def upsert_chat(self, chat):
            return None

        def replace_turns(self, chat_id, turns):
            raise AssertionError("clean state save should not rewrite all turns")

        def replace_turns_from(self, chat_id, turns, start_index=0):
            raise AssertionError("clean state save should not persist turns")

    frame.chat_store = Store()

    frame._save_state()


def test_save_state_persists_only_dirty_turn_suffix(frame):
    frame._chat_store_enabled = True
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_session_turns = [
        {"question": f"q{idx}", "answer_md": f"a{idx}", "model": main.DEFAULT_MODEL_ID}
        for idx in range(5)
    ]
    frame._current_chat_state = {"id": "chat-current", "turns": frame.active_session_turns}
    calls = []

    class Store:
        def upsert_chat(self, chat):
            return None

        def replace_turns(self, chat_id, turns):
            raise AssertionError("dirty suffix save should not rewrite all turns")

        def replace_turns_from(self, chat_id, turns, start_index=0):
            calls.append((chat_id, start_index, [turn["question"] for turn in turns]))

    frame.chat_store = Store()
    frame._mark_chat_turns_dirty("chat-current", 3)

    frame._save_state()

    assert calls == [("chat-current", 3, ["q3", "q4"])]


def test_archive_active_session_avoids_deepcopying_turns_when_chat_store_enabled(frame, monkeypatch):
    class NoDeepcopyList(list):
        def __deepcopy__(self, memo):
            raise AssertionError("chat store archive should not deepcopy all turns")

    frame._chat_store_enabled = True
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_session_turns = NoDeepcopyList(
        [{"question": f"q{idx}", "answer_md": f"a{idx}", "model": main.DEFAULT_MODEL_ID} for idx in range(200)]
    )
    frame._current_chat_state = {
        "id": "chat-current",
        "title": "current",
        "turns": frame.active_session_turns,
        "execution_steps": [{"step": "heavy"}],
    }
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)

    archived = frame._archive_active_session(quick_title=True)

    assert archived["id"] == "chat-current"
    assert len(archived["turns"]) == 200
    assert archived["turn_count"] == 200
    assert archived["execution_steps"] == [{"step": "heavy"}]


def test_archived_clear_resend_real_ack_preserves_start_generation(frame, monkeypatch):
    frame._chat_store_enabled = True
    frame.active_chat_id = frame.current_chat_id = "active"
    frame._current_chat_state = {"id": "active", "turns": []}
    frame.chat_store.upsert_chat({"id": "cleared", "model": main.DEFAULT_CODEX_MODEL,
                                  "codex_thread_id": "old-thread"})
    frame.chat_store.replace_turns("cleared", [{"question": "first", "answer_md": "old answer",
                                               "model": main.DEFAULT_CODEX_MODEL}])
    frame.archived_chats = [frame.chat_store.load_chat("cleared")]
    monkeypatch.setattr(main.threading, "Thread", lambda **_kwargs: SimpleNamespace(start=lambda: None))
    monkeypatch.setattr(frame, "_request_codex_chat_information", lambda *_args: None)
    assert frame._clear_context_for_chat_id("cleared", auto_resend_first=True) == "cleared"
    chat = frame._find_archived_chat("cleared")
    turn = chat["turns"][0]
    start_generation = turn["codex_context_generation"]
    frame._apply_codex_worker_thread_state("cleared", {
        "chat_id": "cleared", "turn_idx": 0, "thread_id": "fresh-thread",
        "turn_id": "fresh-turn", "context_generation": start_generation, "active": True,
    })
    assert turn["codex_context_generation"] == chat["codex_context_generation"]
    assert turn["codex_context_generation"] == start_generation + 1
    def event(kind, generation=start_generation, **kwargs):
        return main.CodexEvent(type=kind, thread_id="fresh-thread", turn_id="fresh-turn",
                               data={"turn_idx": 0, "context_generation": generation}, **kwargs)
    frame._on_codex_event_for_chat("cleared", event("item_completed", start_generation - 1,
                                                   phase="final_answer", text="stale"))
    assert turn["answer_md"] == main.REQUESTING_TEXT
    frame._on_codex_event_for_chat("cleared", event("item_completed", phase="final_answer", text="fresh answer"))
    frame._on_codex_event_for_chat("cleared", event("turn_completed", status="completed"))
    frame._on_codex_event_for_chat("cleared", event("turn_completed", status="completed"))
    assert turn["request_status"] == "done"
    assert frame.chat_store.get_clear_operation(turn["clear_operation_id"])["state"] == "completed_with_resend"
    frame._persist_chat_history_to_store()
    reopened = main.ChatStore(frame.chat_store.db_path)
    loaded = reopened.load_chat("cleared")
    assert len(loaded["turns"]) == 1
    assert loaded["turns"][0]["answer_md"] == "fresh answer"
    assert loaded["turns"][0]["request_status"] == "done"


@pytest.mark.parametrize("existing_generation", [None, 1])
def test_switched_chat_final_actual_archive_updates_generation(frame, monkeypatch, existing_generation):
    _prepare_switched_chat_final(frame, monkeypatch)
    if existing_generation is not None:
        frame.archived_chats = [{"id": "switch-owner", "codex_context_generation": existing_generation}]
    archived = frame._archive_active_session(quick_title=True)
    frame.active_chat_id = frame.current_chat_id = "other-owner"
    frame._current_chat_state = {"id": "other-owner", "turns": []}
    turn = archived["turns"][0]
    frame._on_codex_event_for_chat("switch-owner", _switched_chat_final_event(
        "item_completed", phase="final_answer", text="accepted final"))
    frame._on_codex_event_for_chat("switch-owner", _switched_chat_final_event(
        "turn_completed", status="completed"))
    assert turn["answer_md"] == "accepted final"
    assert turn["request_status"] == "done"
    assert archived["codex_context_generation"] == 2
    frame._persist_chat_history_to_store()
    loaded = main.ChatStore(frame.chat_store.db_path).load_chat("switch-owner")
    assert loaded["codex_context_generation"] == 2
    assert loaded["turns"][0]["answer_md"] == "accepted final"
    assert loaded["turns"][0]["request_status"] == "done"
