import main


class _EnterEvent:
    def GetKeyCode(self):
        return main.wx.WXK_RETURN

    def Skip(self):
        return None


def test_ui_automation_history_enter_allows_switch_during_pending_reply(frame, monkeypatch):
    frame.Show()
    frame.is_running = True
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state.update(
        {
            "id": "chat-current",
            "title": "褰撳墠鑱婂ぉ",
            "turns": [
                {
                    "question": "褰撳墠闂",
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
            "title": "鍘嗗彶浼氳瘽",
            "pinned": False,
            "created_at": 1.0,
            "updated_at": 1.0,
            "turns": [{"question": "鍘嗗彶闂", "answer_md": "鍘嗗彶鍥炵瓟", "model": "openai/gpt-5.2", "created_at": 1.0}],
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
    assert frame.answer_list.GetString(1) == "鍘嗗彶闂"
    assert frame.answer_list.HasFocus()


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
            "title": "鍘嗗彶浼氳瘽",
            "pinned": False,
            "created_at": 1.0,
            "updated_at": 1.0,
            "turns": [{"question": "鍘嗗彶闂", "answer_md": "鍘嗗彶鍥炵瓟", "model": "openai/gpt-5.2", "created_at": 1.0}],
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
    assert "鍘嗗彶闂" not in list(frame.answer_list.GetStrings())
    assert frame.answer_list.HasFocus()


def test_ui_automation_switched_visible_chat_does_not_receive_late_codex_answer_from_previous_chat(frame, monkeypatch):
    frame.Show()
    frame.active_chat_id = "chat-b"
    frame.current_chat_id = "chat-b"
    frame._current_chat_state.update(
        {
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
    )
    frame.active_session_turns = frame._current_chat_state["turns"]
    frame.archived_chats = [
        {
            "id": "chat-a",
            "title": "聊天A",
            "pinned": False,
            "created_at": 1.0,
            "updated_at": 1.0,
            "turns": [
                {
                    "question": "A的问题",
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
            text="A 的晚到回答",
        )
    )

    after_rows = list(frame.answer_list.GetStrings())
    archived = frame._find_archived_chat("chat-a")
    assert archived["turns"][0]["answer_md"] == "A 的晚到回答"
    assert frame._current_chat_state["turns"][0]["answer_md"] == main.REQUESTING_TEXT
    assert after_rows == before_rows


def test_ui_automation_f1_execution_view_shows_detailed_codex_progress(frame, monkeypatch):
    frame.Show()
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state.update(
        {
            "id": "chat-current",
            "title": "当前聊天",
            "turns": [
                {
                    "question": "帮我修测试",
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
                "title": "运行测试",
                "command": "pytest tests/test_main_unit.py -k codex",
            },
        ),
    )

    frame._apply_detail_panel_mode("execution", refresh_execution=True)
    rows = list(frame.execution_list.GetStrings())

    assert rows == ["开始执行：运行测试 | 命令：pytest tests/test_main_unit.py -k codex"]
