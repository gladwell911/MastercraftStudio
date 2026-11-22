import copy
import main
import pytest
import time
import wx
from main_test_support import assert_request_metadata
from types import SimpleNamespace


def test_clear_context_adds_tail_notice_and_keeps_current_chat_identity(frame, monkeypatch):
    frame.active_chat_id = "chat-old"
    frame.current_chat_id = "chat-old"
    frame.active_session_started_at = time.time()
    frame.active_session_turns = [
        {"question": "old question", "answer_md": "old answer", "model": "openclaw/main", "created_at": time.time()}
    ]
    frame._current_chat_state = {
        "id": "chat-old",
        "title": "old",
        "turns": frame.active_session_turns,
        "created_at": frame.active_session_started_at,
        "updated_at": frame.active_session_started_at,
    }
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda force_replay=False: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda _chat_id: None)
    archived_before = list(frame.archived_chats)

    frame._clear_context_and_start_new_chat()

    assert frame.active_chat_id == "chat-old"
    assert frame.current_chat_id == "chat-old"
    assert frame._current_chat_state["id"] == "chat-old"
    assert frame._current_chat_state["title"] == "old"
    assert frame.archived_chats == archived_before
    assert frame.active_session_turns == []
    assert frame.answer_list.GetCount() >= 1
    assert frame.answer_list.GetString(frame.answer_list.GetCount() - 1) == "以开启新会话"


def test_remote_new_chat_preserves_dynamic_claudecode_model(frame, monkeypatch):
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    chat = frame._start_remote_new_chat({"model": "claudecode/opus"})

    assert chat["model"] == "claudecode/opus"
    assert frame.selected_model == "claudecode/opus"
    assert frame._current_chat_state["model"] == "claudecode/opus"


def test_remote_worker_status_updates_are_marshaled_to_ui(frame, monkeypatch):
    posted = []
    calls = []
    monkeypatch.setattr(frame, "_call_after_if_alive", lambda fn, *args, **kwargs: posted.append((fn, args, kwargs)) or True)
    monkeypatch.setattr(frame, "SetStatusText", lambda text: calls.append(text))
    main_thread = main.threading.main_thread()
    monkeypatch.setattr(main.threading, "current_thread", lambda: object())
    monkeypatch.setattr(main.threading, "main_thread", lambda: main_thread)

    frame._set_status_text_safe("ready")

    assert calls == []
    assert posted and posted[0][0] == frame.SetStatusText
    assert posted[0][1] == ("ready",)


@pytest.mark.parametrize("outcome", [(False, "rejected"), RuntimeError("boom")])
def test_story_1_3_false_or_exception_restores_and_allows_retry(frame, monkeypatch, outcome):
    frame.Show()
    frame.input_edit.SetValue("  exact question  ")
    frame.input_edit.SetSelection(2, 7)
    frame.input_edit.SetFocus()
    monkeypatch.setattr(main.wx, "MessageBox", lambda *a, **k: wx.ID_OK)
    calls = []
    def submit(value, **_kwargs):
        calls.append(value)
        if isinstance(outcome, Exception): raise outcome
        return outcome
    frame._submit_question = submit
    assert frame._request_question_submit() is False
    assert frame.input_edit.GetValue() == "  exact question  "
    assert frame.input_edit.GetSelection() == (2, 7)
    assert frame.input_edit.HasFocus()
    frame._submit_question = lambda value, **_kwargs: calls.append(value) or (True, "")
    assert frame._request_question_submit() is True
    assert calls == ["exact question", "exact question"]


def test_story_1_3_question_latch_blocks_recursive_duplicate(frame):
    frame.Show(); frame.input_edit.SetValue("once"); frame.input_edit.SetFocus()
    calls = []
    def submit(value, **_kwargs):
        calls.append(value)
        assert frame._request_question_submit() is False
        return True, ""
    frame._submit_question = submit
    assert frame._request_question_submit() is True
    assert calls == ["once"]


def test_send_click_empty_input_shows_message(frame, monkeypatch):
    seen = {}
    monkeypatch.setattr(main.wx, "MessageBox", lambda message, title, flags: seen.update({"message": message, "title": title}))
    frame.input_edit.SetValue("   ")
    frame._on_send_clicked(None)
    assert seen == {"message": "请输入问题，输入框内容为空", "title": "提示"}


def test_submit_openrouter_model_uses_embedded_key_when_env_has_stale_key(frame, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "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")
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda force_replay=False: None)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_schedule_first_question_auto_title", lambda *args, **kwargs: None)
    started = {}

    class _CaptureThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            started["target"] = target
            started["args"] = args

        def start(self):
            return None

    monkeypatch.setattr(main.threading, "Thread", _CaptureThread)

    ok, message = frame._submit_question("测试 Claude", source="local", model="anthropic/claude-sonnet-4.6")

    assert ok is True
    assert message == ""
    assert started["args"][0] == main.DEFAULT_OPENROUTER_API_KEY


def test_submit_question_renders_question_immediately_without_stealing_focus(frame, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    frame._refresh_openclaw_sync_lifecycle = lambda force_replay=False: None
    frame._play_send_sound = lambda: None

    class _NoOpThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}

        def start(self):
            return None

    monkeypatch.setattr(main.threading, "Thread", _NoOpThread)
    focused = {"n": 0}
    monkeypatch.setattr(frame.answer_list, "SetFocus", lambda: focused.__setitem__("n", focused["n"] + 1))

    ok, message = frame._submit_question("马上显示的问题", source="local", model="openai/gpt-5.2")

    assert ok is True
    assert message == ""
    rows = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert "马上显示的问题" in rows
    assert focused["n"] == 0


def test_submit_question_keeps_new_chat_button_enabled_while_waiting(frame, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda force_replay=False: None)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)

    class _NoOpThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}

        def start(self):
            return None

    monkeypatch.setattr(main.threading, "Thread", _NoOpThread)

    ok, message = frame._submit_question("等待中的问题", source="local", model="openai/gpt-5.2")

    assert ok is True
    assert message == ""
    assert frame.is_running is True
    assert frame.new_chat_button.IsEnabled() is True


def test_new_chat_allowed_while_waiting_for_reply(frame, monkeypatch):
    frame.is_running = True
    frame.active_chat_id = "chat-old"
    frame.current_chat_id = "chat-old"
    frame.active_session_turns = [
        {"question": "旧问题", "answer_md": main.REQUESTING_TEXT, "model": "openai/gpt-5.2", "created_at": time.time()}
    ]
    frame._current_chat_state = {"id": "chat-old", "turns": frame.active_session_turns}
    seen = {}

    def fake_archive(quick_title=False, schedule_async_rename=False, **_kwargs):
        seen["archive"] = (quick_title, schedule_async_rename)
        return {"id": "chat-old"}

    monkeypatch.setattr(frame, "_archive_active_session", fake_archive)
    monkeypatch.setattr(frame, "_refresh_history", lambda *args, **kwargs: seen.setdefault("history", True))
    monkeypatch.setattr(frame, "_render_answer_list", lambda: seen.setdefault("render", True))
    monkeypatch.setattr(frame.input_edit, "SetFocus", lambda: seen.setdefault("focus", True))
    monkeypatch.setattr(frame, "SetStatusText", lambda text: seen.setdefault("status", text))
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda chat_id="": seen.setdefault("push", chat_id))

    frame._on_new_chat_clicked(None)

    assert seen["archive"] == (True, True)
    assert frame.current_chat_id == frame.active_chat_id
    assert frame.active_chat_id != "chat-old"
    assert seen["status"] == "已开始新聊天"


def test_new_chat_projects_selected_placeholder_row_immediately(frame, monkeypatch):
    frame.active_chat_id = ""
    frame.current_chat_id = ""
    frame.active_session_turns = []
    frame.archived_chats = []
    frame._current_chat_state = {}

    frame._on_new_chat_clicked(None)

    assert list(frame.history_list.GetStrings()) == [main.EMPTY_CURRENT_CHAT_TITLE]
    assert frame.history_ids == [frame.active_chat_id]
    assert frame.history_list.GetSelection() == 0
    assert frame.input_edit.HasFocus()


def test_new_chat_initializes_detail_panel_defaults(frame):
    frame.active_chat_id = ""
    frame.current_chat_id = ""
    frame.active_session_turns = []
    frame.archived_chats = []
    frame._current_chat_state = {}

    frame._on_new_chat_clicked(None)

    assert frame._current_chat_state["detail_panel_mode"] == "answers"
    assert frame._current_chat_state["execution_steps"] == []


def test_new_chat_returns_with_confirmed_durable_zero_turn_owner(frame, tmp_path):
    frame.chat_store = main.ChatStore(tmp_path / "immediate-new-chat.db")
    frame.chat_store.initialize()
    frame._chat_store_enabled = True
    frame.active_chat_id = ""
    frame.current_chat_id = ""
    frame.active_session_turns = []
    frame.archived_chats = []
    frame._current_chat_state = {}
    frame.selected_model = "kimi/main"
    frame.model_combo.SetValue(main.model_display_name("kimi/main"))

    frame._on_new_chat_clicked(None)

    loaded = frame.chat_store.load_chat(frame.active_chat_id)
    assert loaded is not None
    assert loaded["title"] == main.EMPTY_CURRENT_CHAT_TITLE
    assert loaded["model"] == "kimi/main"
    assert loaded["title_revision"] == 1
    assert loaded["turns"] == []
    assert frame.history_ids[frame.history_list.GetSelection()] == frame.active_chat_id
    assert frame.input_edit.HasFocus()


def test_new_chat_commit_precedes_projection_and_failure_restores_owner(frame, monkeypatch):
    old_id = frame.active_chat_id
    old_state = copy.deepcopy(frame._current_chat_state)
    events = []
    real_upsert = frame.chat_store.upsert_chat
    real_load = frame.chat_store.load_chat
    monkeypatch.setattr(frame.chat_store, "upsert_chat", lambda chat: (events.append("commit"), real_upsert(chat))[1])
    monkeypatch.setattr(frame, "_upsert_history_row", lambda *_args, **_kwargs: events.append("project") or True)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *_args, **_kwargs: events.append("publish"))

    assert frame._on_new_chat_clicked(None) is True
    assert events.index("commit") < events.index("project") < events.index("publish")

    created_id = frame.active_chat_id
    turns_ref = frame.active_session_turns
    live_turn = {"question": "pending", "answer_md": main.REQUESTING_TEXT, "model": "kimi/main"}
    turns_ref.append(live_turn)
    state_ref = frame._current_chat_state
    state_ref["turns"] = turns_ref
    execution_ref = []
    state_ref["execution_steps"] = execution_ref
    pending_ref = [{"prompt": "pending"}]
    frame.active_codex_request_queue = pending_ref
    archived_ref = frame.archived_chats
    archived_entry_refs = list(frame.archived_chats)
    archived_turn_refs = [entry.get("turns") for entry in archived_entry_refs]
    monkeypatch.setattr(frame.chat_store, "load_chat", lambda *_args, **_kwargs: None)
    assert frame._on_new_chat_clicked(None) is False
    assert frame.active_chat_id == created_id
    assert frame.active_session_turns is turns_ref
    assert frame._current_chat_state is state_ref
    assert frame._current_chat_state["turns"] is frame.active_session_turns
    assert frame._current_chat_state["execution_steps"] is execution_ref
    assert frame.active_codex_request_queue is pending_ref
    assert frame.archived_chats is archived_ref
    assert all(actual is expected for actual, expected in zip(frame.archived_chats, archived_entry_refs))
    assert all(entry.get("turns") is expected for entry, expected in zip(frame.archived_chats, archived_turn_refs))
    live_turn["answer_md"] = "callback completed"
    assert frame.active_session_turns[0] is live_turn
    assert frame._current_chat_state["turns"][0]["answer_md"] == "callback completed"
    frame.chat_store.replace_turns(created_id, frame.active_session_turns)
    assert real_load(created_id)["turns"][0]["answer_md"] == "callback completed"
    state_ref["callback_result"] = "completed"
    assert frame._current_chat_state["callback_result"] == "completed"


def test_new_chat_clears_stale_active_codex_state_without_turns(frame, monkeypatch):
    frame.active_chat_id = "chat-old"
    frame.current_chat_id = "chat-old"
    frame.active_session_turns = []
    frame.active_codex_thread_id = "thread-stale"
    frame.active_codex_turn_id = "turn-stale"
    frame.active_codex_turn_active = True
    frame.active_codex_pending_prompt = "pending"
    frame.active_codex_pending_request = {"kind": "user_input"}
    frame.active_codex_request_queue = [{"prompt": "queued"}]
    frame.active_codex_thread_flags = ["waitingOnUserInput"]
    frame.active_codex_latest_assistant_text = "partial"
    frame.active_codex_latest_assistant_phase = "answer"
    frame._current_chat_state = {"id": "chat-old", "turns": []}

    monkeypatch.setattr(frame, "_archive_active_session", lambda **_kwargs: None)
    monkeypatch.setattr(frame, "_sync_codex_speed_combo_from_chat", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_mark_history_list_dirty", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_render_answer_list", lambda: None)
    monkeypatch.setattr(frame.input_edit, "SetFocus", lambda: None)
    monkeypatch.setattr(frame, "SetStatusText", lambda _text: None)
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: None)
    monkeypatch.setattr(frame, "_mark_openclaw_lifecycle_dirty", lambda: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *_args, **_kwargs: None)

    frame._on_new_chat_clicked(None)

    assert frame.active_codex_thread_id == ""
    assert frame.active_codex_turn_id == ""
    assert frame.active_codex_turn_active is False
    assert frame.active_codex_pending_prompt == ""
    assert frame.active_codex_pending_request is None
    assert frame.active_codex_request_queue == []
    assert frame.active_codex_thread_flags == []
    assert frame.active_codex_latest_assistant_text == ""
    assert frame.active_codex_latest_assistant_phase == ""
    assert frame._current_chat_state["codex_thread_id"] == ""
    assert frame._current_chat_state["codex_turn_id"] == ""
    assert frame._current_chat_state["codex_turn_active"] is False
    assert frame._current_chat_state["codex_pending_prompt"] == ""
    assert frame._current_chat_state["codex_pending_request"] is None
    assert frame._current_chat_state["codex_request_queue"] == []
    assert frame._current_chat_state["codex_thread_flags"] == []
    assert frame._current_chat_state["codex_latest_assistant_text"] == ""
    assert frame._current_chat_state["codex_latest_assistant_phase"] == ""


def test_openclaw_new_chat_resets_detail_panel_defaults(frame, monkeypatch):
    frame.active_session_turns = []
    frame.archived_chats = []
    frame._current_chat_state = {
        "id": "chat-old",
        "title": "旧聊天",
        "turns": [],
        "detail_panel_mode": "execution",
        "execution_steps": [{"step": "1"}],
    }
    monkeypatch.setattr(frame, "_resolve_current_model", lambda: "openclaw/main")
    monkeypatch.setattr(frame, "_seek_openclaw_sync_to_current_tail", lambda: None)
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_render_answer_list", lambda: None)
    monkeypatch.setattr(frame, "_send_openclaw_hidden_message", lambda _msg: None)
    monkeypatch.setattr(frame.input_edit, "SetFocus", lambda: None)
    monkeypatch.setattr(frame, "SetStatusText", lambda _text: None)

    frame._on_new_chat_clicked(None)

    assert frame._current_chat_state["detail_panel_mode"] == "answers"
    assert frame._current_chat_state["execution_steps"] == []


def test_new_chat_clears_active_claudecode_client_before_next_submit(frame, monkeypatch):
    class _ActiveClaudeClient:
        def __init__(self):
            self.inputs = []

        def send_user_input(self, text):
            self.inputs.append(text)

    stale_client = _ActiveClaudeClient()
    frame._active_claudecode_client = stale_client
    frame._active_claudecode_chat_id = "chat-old"
    frame.active_claudecode_session_id = "session-old"
    frame.active_chat_id = "chat-old"
    frame.current_chat_id = "chat-old"
    frame.active_session_turns = []
    frame._current_chat_state = {"id": "chat-old", "turns": frame.active_session_turns}
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_refresh_history", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "SetStatusText", lambda _text: None)
    monkeypatch.setattr(frame.input_edit, "SetFocus", lambda: None)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_render_answer_list", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_set_input_hint_idle", lambda: None)
    monkeypatch.setattr(frame.model_combo, "SetValue", lambda _value: None)
    monkeypatch.setattr(frame, "_resolve_current_model", lambda: "claudecode/default")
    seen = {"worker": []}
    monkeypatch.setattr(
        frame,
        "_start_claudecode_worker_for_turn",
        lambda chat_id, turn_idx, question, session_id, model: seen["worker"].append(
            (chat_id, turn_idx, question, session_id, model)
        ),
    )

    frame._on_new_chat_clicked(None)
    ok, message = frame._submit_question("你好", source="local", model="claudecode/default")

    assert ok is True
    assert message == ""
    assert stale_client.inputs == []
    assert seen["worker"]
    assert frame._active_claudecode_client is None
    assert frame._active_claudecode_chat_id == ""


def test_new_chat_from_history_clears_history_view_and_resets_context_usage(frame, monkeypatch):
    frame.view_mode = "history"
    frame.view_history_id = "chat-history"
    frame.archived_chats = [
        {
            "id": "chat-history",
            "title": "历史会话",
            "context_usage": {
                "used_tokens": 31000,
                "context_window": 200000,
                "source": "claudecode",
                "exact": True,
                "fresh": True,
                "model": "claudecode/default",
                "updated_at": 1.0,
            },
            "turns": [
                {"question": "历史问题", "answer_md": "历史回答", "model": "claudecode/default", "created_at": 1.0},
            ],
            "created_at": 1.0,
            "updated_at": 1.0,
        }
    ]
    frame.active_session_turns = []
    frame.current_chat_id = "chat-current"
    frame.active_chat_id = "chat-current"
    frame._current_chat_state = {"id": "chat-current", "turns": []}
    frame._pending_context_usage_by_turn = {
        ("chat-current", 0): {
            "used_tokens": 123,
            "context_window": 200000,
            "source": "claudecode",
            "exact": True,
            "fresh": True,
            "model": "claudecode/default",
            "updated_at": 1.0,
        }
    }
    frame._refresh_openclaw_sync_lifecycle = lambda *args, **kwargs: None
    frame._seek_openclaw_sync_to_current_tail = lambda: None
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    frame._on_new_chat_clicked(None)

    assert frame.view_mode == "active"
    assert frame.view_history_id is None
    assert frame._pending_context_usage_by_turn == {}
    assert "暂无" in list(frame.answer_list.GetStrings())


@pytest.mark.parametrize("model", ["openai/gpt-5.2", "claudecode/default", "codex/main", "openclaw/main"])
def test_new_chat_clicked_resets_context_usage_for_model_variants(frame, monkeypatch, model):
    frame.view_mode = "history"
    frame.view_history_id = "chat-history"
    frame.archived_chats = [
        {
            "id": "chat-history",
            "title": "历史会话",
            "context_usage": {
                "used_tokens": 31000,
                "context_window": 200000,
                "source": "claudecode",
                "exact": True,
                "fresh": True,
                "model": "claudecode/default",
                "updated_at": 1.0,
            },
            "turns": [],
            "created_at": 1.0,
            "updated_at": 1.0,
        }
    ]
    frame.active_session_turns = []
    frame.current_chat_id = "chat-current"
    frame.active_chat_id = "chat-current"
    frame._current_chat_state = {
        "id": "chat-current",
        "title": "当前聊天",
        "turns": [],
        "context_usage": {
            "used_tokens": 31000,
            "context_window": 200000,
            "source": "openclaw",
            "exact": True,
            "fresh": True,
            "model": model,
            "updated_at": 1.0,
        },
    }
    frame._pending_context_usage_by_turn = {
        ("chat-current", 0): {
            "used_tokens": 123,
            "context_window": 200000,
            "source": "openclaw",
            "exact": True,
            "fresh": True,
            "model": model,
            "updated_at": 1.0,
        }
    }
    frame._refresh_openclaw_sync_lifecycle = lambda *args, **kwargs: None
    frame._seek_openclaw_sync_to_current_tail = lambda: None
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    frame._on_new_chat_clicked(None)

    assert frame.view_mode == "active"
    assert frame.view_history_id is None
    assert frame._pending_context_usage_by_turn == {}
    assert frame._current_chat_state.get("context_usage") is None
    assert frame.answer_list.GetString(0) == "暂无"


def test_submit_question_marks_turn_pending_with_recovery_metadata(frame, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda force_replay=False: None)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_render_answer_list", lambda: None)
    monkeypatch.setattr(frame, "_update_busy_state", lambda: None)

    class _NoOpThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}

        def start(self):
            return None

    monkeypatch.setattr(main.threading, "Thread", _NoOpThread)
    monkeypatch.setattr(main.time, "time", lambda: 1234.5)

    calls = []
    snapshot = {}

    def _save_state():
        calls.append("save")
        snapshot["turn"] = copy.deepcopy(frame.active_session_turns[-1])

    monkeypatch.setattr(frame, "_save_state", _save_state)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: calls.append("sound"))
    monkeypatch.setattr(frame, "SetStatusText", lambda text: calls.append(("status", text)))
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda force_replay=False: calls.append("sync"))
    monkeypatch.setattr(frame, "_render_answer_list", lambda: calls.append("render"))

    ok, message = frame._submit_question("恢复这条回答", source="local", model=main.DEFAULT_MODEL_ID)

    assert ok is True
    assert message == ""
    assert calls[0] == "save"
    assert calls.index("save") < calls.index("sound")
    assert calls.index("save") < calls.index(("status", "已发送"))
    assert calls.index("save") < calls.index("render")
    assert calls.index("save") < calls.index("sync")
    turn = snapshot["turn"]
    assert_request_metadata(
        turn,
        status="pending",
        model=main.DEFAULT_MODEL_ID,
        question="恢复这条回答",
        recoverable=True,
        mode="retry",
        resume_token={},
        attempt_count=1,
    )
    assert turn["request_started_at"] == 1234.5
    assert turn["request_last_attempt_at"] == 1234.5
    assert turn["request_error"] == ""
    assert turn["request_recovered_after_restart"] is False


def test_claudecode_submit_sets_resume_recovery_mode(frame, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda force_replay=False: None)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_render_answer_list", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    class _NoOpThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}

        def start(self):
            return None

    monkeypatch.setattr(main.threading, "Thread", _NoOpThread)
    monkeypatch.setattr(main.time, "time", lambda: 3456.5)

    frame.active_claudecode_session_id = "session-123"

    ok, message = frame._submit_question("ClaudeCode 续问", source="local", model=main.DEFAULT_CLAUDECODE_MODEL)

    assert ok is True
    assert message == ""
    turn = frame.active_session_turns[-1]
    assert_request_metadata(
        turn,
        status="pending",
        model=main.DEFAULT_CLAUDECODE_MODEL,
        question="ClaudeCode 续问",
        recoverable=True,
        mode="resume",
        resume_token={"session_id": "session-123"},
        attempt_count=1,
    )
    assert turn["request_resume_token"] == {"session_id": "session-123"}


def test_add_system_message_to_chat_adds_recovery_metadata(frame, monkeypatch):
    monkeypatch.setattr(main.time, "time", lambda: 6789.5)

    frame._add_system_message_to_chat("系统提示")

    turn = frame.active_session_turns[-1]
    assert_request_metadata(
        turn,
        status="done",
        model="system",
        question="",
        recoverable=False,
        mode="retry",
        resume_token={},
        attempt_count=0,
    )
    assert turn["answer_md"] == "系统提示"


def test_remote_pending_request_reply_uses_matching_chat_worker(frame, monkeypatch):
    calls = []

    class _FakeWorkerClient:
        def __init__(self, chat_id):
            self.chat_id = chat_id

        def start(self):
            calls.append(("start", self.chat_id))

        def reply_user_input(self, chat_id, request_id, answers):
            calls.append(("reply", self.chat_id, chat_id, request_id, answers))

    frame.active_codex_pending_request = {
        "chat_id": "chat-c",
        "model": main.DEFAULT_CODEX_MODEL,
        "request_id": "req-remote",
    }
    monkeypatch.setattr(frame, "_ensure_codex_client", lambda *_args, **_kwargs: pytest.fail("remote reply must use chat-bound worker client"))
    monkeypatch.setattr(frame, "_get_or_create_codex_client", lambda chat_id, model="": _FakeWorkerClient(chat_id))
    monkeypatch.setattr(frame, "_save_state", lambda: calls.append(("save",)))

    ok, message = frame._handle_remote_pending_request_reply("remote ok")

    assert ok is True
    assert "已提交" in message
    assert ("start", "chat-c") in calls
    assert ("reply", "chat-c", "chat-c", "req-remote", {"reply": ["remote ok"]}) in calls
    assert frame.active_codex_pending_request is None


def test_remote_pending_request_reply_uses_worker_ipc(frame, monkeypatch):
    replies = []

    class FakeWorker:
        def start(self):
            pass

        def reply_user_input(self, chat_id, request_id, answers):
            replies.append((chat_id, request_id, answers))

    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_codex_pending_request = {"request_id": "ask-1", "chat_id": "chat-current"}
    monkeypatch.setattr(frame, "_get_or_create_codex_client", lambda chat_id, model="": FakeWorker())
    monkeypatch.setattr(
        frame,
        "_ensure_codex_client",
        lambda *a, **k: pytest.fail("pending reply must not use singleton codex client"),
    )

    ok, message = frame._handle_remote_pending_request_reply("答案")

    assert ok is True
    assert replies == [("chat-current", "ask-1", {"reply": ["答案"]})]
    assert "已提交" in message


def test_worker_exit_marks_only_matching_chat_failed(frame, monkeypatch):
    saved = []
    monkeypatch.setattr(frame, "_save_state", lambda: saved.append(True))
    monkeypatch.setattr(frame, "_call_after_if_alive", lambda func, *args, **kwargs: func(*args, **kwargs) or True)

    frame.active_chat_id = "chat-a"
    frame.current_chat_id = "chat-a"
    frame._current_chat_state["id"] = "chat-a"
    frame._current_chat_state["turns"] = [
        {
            "question": "A",
            "answer_md": main.REQUESTING_TEXT,
            "request_status": "running",
            "request_error": "",
            "codex_turn_id": "turn-a",
        }
    ]
    frame.active_session_turns = frame._current_chat_state["turns"]
    chat_b = {
        "id": "chat-b",
        "title": "B",
        "turns": [
            {
                "question": "B",
                "answer_md": main.REQUESTING_TEXT,
                "request_status": "running",
                "request_error": "",
                "codex_turn_id": "turn-b",
            }
        ],
    }
    frame.archived_chats = [chat_b]

    frame._apply_codex_worker_thread_state("chat-b", {"chat_id": "chat-b", "turn_idx": 0, "turn_id": "turn-b", "active": True})
    frame._on_codex_worker_exit("chat-b", -9)

    assert frame.active_session_turns[0]["request_status"] == "running"
    assert chat_b["turns"][0]["request_status"] == "failed"
    assert "exited" in chat_b["turns"][0]["request_error"]


def test_submit_question_allows_attachment_only_send_for_codex(frame, monkeypatch, tmp_path):
    image_path = tmp_path / "upload.png"
    image_path.write_text("image", encoding="utf-8")
    frame._pending_input_attachments = [
        {
            "name": "upload.png",
            "path": str(image_path),
            "kind": "image",
            "direction": "outgoing",
            "status": "queued",
            "open_path": str(image_path),
        }
    ]
    seen = {"render": 0, "worker": []}
    monkeypatch.setattr(frame, "_render_answer_list", lambda *args, **kwargs: seen.__setitem__("render", seen["render"] + 1))
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda force_replay=False: None)
    monkeypatch.setattr(
        frame,
        "_start_codex_worker_for_turn",
        lambda chat_id, turn_idx, question, model: seen["worker"].append((chat_id, turn_idx, question, model)),
    )

    ok, message = frame._submit_question("", source="local", model=main.DEFAULT_CODEX_MODEL)

    assert ok is True
    assert message == ""
    assert seen["render"] == 1
    assert frame._pending_input_attachments == []
    turn = frame.active_session_turns[0]
    assert turn["question"] == ""
    assert turn["attachments"][0]["status"] == "success"
    assert seen["worker"] == [(frame.active_chat_id or frame.current_chat_id or "", 0, "", main.DEFAULT_CODEX_MODEL)]


def test_input_key_down_alt_n_shortcut_suppresses_tools_menu_and_triggers_new_chat(frame):
    seen = {"new_chat": 0}
    frame._alt_menu_armed = True
    frame._alt_menu_suppressed = False
    frame._trigger_new_chat = lambda: seen.__setitem__("new_chat", seen["new_chat"] + 1)

    class E:
        def GetKeyCode(self):
            return ord("N")

        def ControlDown(self):
            return False

        def AltDown(self):
            return True

        def Skip(self):
            return None

    frame._on_input_key_down(E())

    assert seen["new_chat"] == 1
    assert frame._alt_menu_suppressed is True


def test_submit_question_schedules_revision_fenced_title_generation(frame, monkeypatch):
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda force_replay=False: None)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    frame.active_session_turns = []
    frame._current_chat_state = {"id": "chat-current", "title": "新聊天", "title_manual": False, "turns": frame.active_session_turns}

    class _NoOpThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}

        def start(self):
            return None

    monkeypatch.setattr(main.threading, "Thread", _NoOpThread)

    ok, message = frame._submit_question("帮我整理安卓自动化测试方案", source="local", model="openai/gpt-5.2")

    assert ok is True
    assert message == ""
    assert frame._is_default_chat_title(frame._current_chat_state["title"])
    assert frame._current_chat_state.get("first_question_auto_title_scheduled") is True


def test_provider_rejection_does_not_schedule_title_and_accepted_retry_does(frame, monkeypatch):
    frame.active_session_turns = []
    frame._current_chat_state.update(
        {"id": frame.active_chat_id, "title": "新聊天", "title_source": "default", "title_revision": 1,
         "turns": frame.active_session_turns}
    )
    scheduled = []
    monkeypatch.setattr(frame, "_schedule_first_question_auto_title", lambda *args: scheduled.append(args))
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_start_codex_worker_for_turn", lambda *args, **kwargs: False)

    ok, _ = frame._submit_question("retry title", model=main.DEFAULT_CODEX_MODEL)
    assert ok is False
    assert scheduled == []
    assert frame._current_chat_state["title"] == "新聊天"

    monkeypatch.setattr(frame, "_start_codex_worker_for_turn", lambda *args, **kwargs: True)
    assert frame._submit_question("retry title", model=main.DEFAULT_CODEX_MODEL) == (True, "")
    assert len(scheduled) == 1


def test_submit_question_continues_when_chat_title_rules_are_missing(frame, monkeypatch, tmp_path):
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda force_replay=False: None)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(main, "_CHAT_TITLE_RULES_CACHE", None)
    monkeypatch.setattr(main, "shared_chat_title_rules_path", lambda: tmp_path / "missing.json")
    frame.active_session_turns = []
    frame._current_chat_state = {
        "id": "chat-current",
        "title": "新聊天",
        "title_manual": False,
        "turns": frame.active_session_turns,
    }

    class _NoOpThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            pass

        def start(self):
            return None

    monkeypatch.setattr(main.threading, "Thread", _NoOpThread)

    ok, message = frame._submit_question("缺少标题规则也要发送", source="local", model=main.DEFAULT_CODEX_MODEL)

    assert ok is True
    assert message == ""
    assert frame.active_session_turns[0]["request_status"] == "pending"


def test_submit_question_keeps_placeholder_until_generated_title_arrives(frame, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda force_replay=False: None)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    frame.active_chat_id = ""
    frame.current_chat_id = ""
    frame.active_session_turns = []
    frame.archived_chats = []
    frame._current_chat_state = {}

    class _NoOpThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}

        def start(self):
            return None

    monkeypatch.setattr(main.threading, "Thread", _NoOpThread)

    frame._on_new_chat_clicked(None)
    assert list(frame.history_list.GetStrings()) == [main.EMPTY_CURRENT_CHAT_TITLE]

    ok, message = frame._submit_question("帮我整理安卓自动化测试方案", source="local", model="openai/gpt-5.2")

    assert ok is True
    assert message == ""
    items = list(frame.history_list.GetStrings())
    assert items == [main.EMPTY_CURRENT_CHAT_TITLE]


def test_restored_current_new_chat_first_send_has_unique_history_and_one_worker(frame, monkeypatch):
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *args, **kwargs: None)
    starts = []
    monkeypatch.setattr(frame, "_start_codex_worker_for_turn", lambda *args: starts.append(args))

    class _NoOpThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(main.threading, "Thread", _NoOpThread)
    restored_turns = [{"question": "restored q", "answer_md": "restored a", "model": main.DEFAULT_CODEX_MODEL}]
    frame.active_chat_id = "chat-restored"
    frame.current_chat_id = "chat-restored"
    frame.active_session_turns = restored_turns
    frame._current_chat_state = {
        "id": "chat-restored", "title": "restored", "turns": restored_turns, "updated_at": 2.0
    }
    frame.archived_chats = [
        {"id": " chat-restored ", "title": "stale restored", "pinned": True, "updated_at": 1.0}
    ]

    frame._on_new_chat_clicked(None)
    new_chat_id = frame.active_chat_id
    ok, message = frame._submit_question("first question", source="local", model=main.DEFAULT_CODEX_MODEL)

    assert (ok, message) == (True, "")
    assert len(starts) == 1
    assert len(frame.active_session_turns) == 1
    assert frame.active_session_turns[0]["question"] == "first question"
    assert frame.input_edit.HasFocus()
    frame._refresh_history(new_chat_id)
    assert len(frame.history_ids) == len(set(frame.history_ids))
    assert frame.history_ids.count("chat-restored") == 1


def test_failed_submit_restores_native_rows_models_selection_and_allows_retry(frame, monkeypatch):
    frame.active_session_turns = [{"question": "old", "answer_md": "answer", "model": main.DEFAULT_CODEX_MODEL}]
    frame._current_chat_state["turns"] = frame.active_session_turns
    frame._render_answer_list()
    frame.archived_chats = [{"id": "chat-old", "title": "old", "turns": [], "updated_at": 1.0}]
    frame._refresh_history(frame.active_chat_id)
    frame.answer_list.SetSelection(frame.answer_list.GetCount() - 1)
    frame.history_list.SetSelection(frame.history_ids.index("chat-old"))
    frame.input_edit.ChangeValue("retry me")
    frame.input_edit.SetFocus()
    expected_answer_strings = list(frame.answer_list.GetStrings())
    original_answer_ids = list(frame.answer_list_model.visible_ids)
    expected_answer_labels = dict(frame.answer_list_model.labels_by_id)
    expected_answer_selection = frame.answer_list.GetSelection()
    expected_history_strings = list(frame.history_list.GetStrings())
    original_history_ids = list(frame.history_list_model.visible_ids)
    expected_history_labels = dict(frame.history_list_model.labels_by_id)
    expected_history_selection = frame.history_list.GetSelection()
    calls = []

    def flaky_submit(*_args, **_kwargs):
        calls.append(True)
        if len(calls) == 1:
            frame.answer_list.Set(["partial answer"])
            frame.answer_list_model.visible_ids = ["partial"]
            frame.answer_list_model.labels_by_id = {"partial": "partial answer"}
            frame.history_list.Set(["partial history"])
            frame.history_list_model.visible_ids = ["partial"]
            frame.history_list_model.labels_by_id = {"partial": "partial history"}
            return False, "failed"
        return True, ""

    monkeypatch.setattr(frame, "_submit_question", flaky_submit)
    monkeypatch.setattr(main.wx, "MessageBox", lambda *args, **kwargs: main.wx.ID_OK)

    assert frame._request_question_submit(require_focus=False) is False
    assert list(frame.answer_list.GetStrings()) == expected_answer_strings
    assert frame.answer_list_model.visible_ids == original_answer_ids
    assert frame.answer_list_model.labels_by_id == expected_answer_labels
    assert frame.answer_list.GetSelection() == expected_answer_selection
    assert list(frame.history_list.GetStrings()) == expected_history_strings
    assert frame.history_list_model.visible_ids == original_history_ids
    assert frame.history_list_model.labels_by_id == expected_history_labels
    assert frame.history_list.GetSelection() == expected_history_selection
    assert frame.history_ids == original_history_ids
    assert frame.input_edit.GetValue() == "retry me"
    frame.archived_chats[0]["title"] = "old updated"
    assert frame._upsert_history_row("chat-old", allow_reorder=False) is True
    assert frame.history_ids == frame.history_list_model.visible_ids
    assert len(frame.history_ids) == len(set(frame.history_ids)) == frame.history_list.GetCount()
    assert list(frame.history_list.GetStrings()) == [
        frame.history_list_model.labels_by_id[item_id] for item_id in frame.history_ids
    ]
    assert frame._request_question_submit(require_focus=False) is True
    assert len(calls) == 2


def test_submit_question_replaces_empty_answer_state_with_delete_then_append(frame, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda *args, **kwargs: None)

    class _NoOpThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            pass

        def start(self):
            pass

    monkeypatch.setattr(main.threading, "Thread", _NoOpThread)
    frame._render_answer_list()
    assert frame.answer_list.GetString(0) == "暂无"

    original_delete = frame.answer_list.Delete
    original_append = frame.answer_list.Append
    operations = []

    def record_delete(index):
        operations.append(("Delete", index))
        return original_delete(index)

    def record_append(label):
        operations.append(("Append", label))
        return original_append(label)

    monkeypatch.setattr(frame.answer_list, "Clear", lambda: pytest.fail("send should not clear answer list"))
    monkeypatch.setattr(frame.answer_list, "SetString", lambda *args: pytest.fail("empty content row should be removed, not rewritten"))
    monkeypatch.setattr(frame.answer_list, "Delete", record_delete)
    monkeypatch.setattr(frame.answer_list, "Append", record_append)

    ok, message = frame._submit_question("首次提问", source="local", model="openai/gpt-5.2")

    assert ok is True
    assert message == ""
    assert [op for op in operations if op[0] == "Delete"] == [("Delete", 1), ("Delete", 0)]
    time_label = main.wechat_time_label(frame.active_session_turns[0]["created_at"], time.time())
    assert [op for op in operations if op[0] == "Append"] == [
        ("Append", time_label), ("Append", "我"), ("Append", "首次提问")
    ]
    rows = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert rows == [time_label, "我", "首次提问"]
    assert "小诸葛" not in rows
    assert main.REQUESTING_TEXT not in rows


def test_clear_context_shortcut_clears_current_chat_backend_thread_state(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_session_turns = [
        {"question": "旧问题", "answer_md": "旧回答", "model": main.DEFAULT_CODEX_MODEL}
    ]
    frame.active_openclaw_session_key = "openclaw-key"
    frame.active_openclaw_session_id = "openclaw-session"
    frame.active_openclaw_session_file = "session.jsonl"
    frame.active_openclaw_sync_offset = 42
    frame.active_openclaw_last_event_id = "event-old"
    frame.active_openclaw_last_synced_at = 123.0
    frame.active_codex_thread_id = "thread-old"
    frame.active_codex_turn_id = "turn-old"
    frame.active_codex_turn_active = True
    frame.active_codex_pending_prompt = "pending"
    frame.active_codex_pending_request = {"kind": "user_input"}
    frame.active_codex_request_queue = [{"prompt": "queued"}]
    frame.active_codex_thread_flags = ["waitingOnUserInput"]
    frame.active_codex_latest_assistant_text = "partial"
    frame.active_codex_latest_assistant_phase = "answer"
    frame.active_claudecode_session_id = "claude-session"
    frame._current_chat_state = {
        "id": "chat-current",
        "turns": frame.active_session_turns,
        "openclaw_session_key": "openclaw-key",
        "openclaw_session_id": "openclaw-session",
        "openclaw_session_file": "session.jsonl",
        "openclaw_sync_offset": 42,
        "openclaw_last_event_id": "event-old",
        "openclaw_last_synced_at": 123.0,
        "codex_thread_id": "thread-old",
        "codex_turn_id": "turn-old",
        "codex_turn_active": True,
        "codex_pending_prompt": "pending",
        "codex_pending_request": {"kind": "user_input"},
        "codex_request_queue": [{"prompt": "queued"}],
        "codex_thread_flags": ["waitingOnUserInput"],
        "codex_latest_assistant_text": "partial",
        "codex_latest_assistant_phase": "answer",
        "claudecode_session_id": "claude-session",
    }
    monkeypatch.setattr(frame, "_render_answer_list", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: None)
    monkeypatch.setattr(frame, "_mark_openclaw_lifecycle_dirty", lambda: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *_args, **_kwargs: None)

    assert frame._clear_context_and_start_new_chat() is True

    assert frame._current_chat_state["turns"] == []
    assert frame._current_chat_state["codex_thread_id"] == ""
    assert frame._current_chat_state["codex_turn_id"] == ""
    assert frame._current_chat_state["codex_turn_active"] is False
    assert frame._current_chat_state["codex_pending_prompt"] == ""
    assert frame._current_chat_state["codex_pending_request"] is None
    assert frame._current_chat_state["codex_request_queue"] == []
    assert frame._current_chat_state["codex_thread_flags"] == []
    assert frame._current_chat_state["codex_latest_assistant_text"] == ""
    assert frame._current_chat_state["codex_latest_assistant_phase"] == ""
    assert frame._current_chat_state["openclaw_session_key"] == main.DEFAULT_OPENCLAW_SESSION_KEY
    assert frame._current_chat_state["openclaw_session_id"] == ""
    assert frame._current_chat_state["openclaw_session_file"] == ""
    assert frame._current_chat_state["openclaw_sync_offset"] == 0
    assert frame._current_chat_state["openclaw_last_event_id"] == ""
    assert frame._current_chat_state["openclaw_last_synced_at"] == 0.0
    assert frame._current_chat_state["claudecode_session_id"] == ""


def test_busy_state_keeps_new_chat_enabled_while_request_running(frame):
    frame._active_request_count = 1

    frame._update_busy_state()

    assert frame.is_running is True
    assert frame.new_chat_button.IsEnabled()


def test_worker_preserves_chat_id_in_done_callback(frame, monkeypatch):
    seen = {"calls": []}

    class FakeChatClient:
        def __init__(self, api_key, model):
            self.model = model

        def stream_chat(self, user_text, on_delta, history_turns=None):
            on_delta("片段")
            return "完成"

    monkeypatch.setattr(main, "ChatClient", FakeChatClient)
    monkeypatch.setattr(frame, "_call_after_if_alive", lambda fn, *args: seen["calls"].append((fn.__name__, args)))
    monkeypatch.setattr(frame, "_candidate_fallback_models", lambda _model: [])

    frame._worker("dummy", 0, "处理录音", "openai/gpt-5.2", False, "chat-audio")

    assert seen["calls"][-1][0] == "_on_done"
    assert seen["calls"][-1][1][-1] == "chat-audio"


def test_openrouter_background_worker_uses_target_chat_history(frame, monkeypatch):
    captured = {}
    frame.active_chat_id = "chat-visible"
    frame.current_chat_id = "chat-visible"
    frame.active_session_turns = [
        {"question": "visible old", "answer_md": "visible old answer", "model": "openai/gpt-5.2", "created_at": 3.0}
    ]
    frame._current_chat_state.update({"id": "chat-visible", "turns": frame.active_session_turns})
    archived_turns = [
        {"question": "target old", "answer_md": "target old answer", "model": "openai/gpt-5.2", "created_at": 1.0},
        {"question": "target new", "answer_md": main.REQUESTING_TEXT, "model": "openai/gpt-5.2", "created_at": 2.0},
    ]
    frame.archived_chats = [{"id": "chat-target", "title": "target", "turns": archived_turns, "created_at": 1.0, "updated_at": 1.0}]

    class FakeChatClient:
        last_context_usage = None

        def __init__(self, api_key, model):
            pass

        def stream_chat(self, user_text, on_delta, history_turns=None):
            captured["history_turns"] = list(history_turns or [])
            return "target final"

    monkeypatch.setattr(main, "ChatClient", FakeChatClient)
    monkeypatch.setattr(frame, "_call_after_if_alive", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_candidate_fallback_models", lambda _model: [])

    frame._worker("dummy", 1, "target new", "openai/gpt-5.2", False, "chat-target")

    assert captured["history_turns"] == [archived_turns[0]]


def test_claudecode_generic_worker_keeps_background_chat_isolated(frame, monkeypatch):
    captured = {}
    frame.active_chat_id = "chat-visible"
    frame.current_chat_id = "chat-visible"
    frame.active_claudecode_session_id = "session-visible"
    frame.active_session_turns = [
        {"question": "visible question", "answer_md": "visible answer", "model": "claudecode/default", "created_at": 2.0}
    ]
    frame._current_chat_state.update({"id": "chat-visible", "turns": frame.active_session_turns})
    frame.archived_chats = [
        {
            "id": "chat-target",
            "title": "target",
            "turns": [
                {
                    "question": "target question",
                    "answer_md": main.REQUESTING_TEXT,
                    "model": "claudecode/default",
                    "created_at": 1.0,
                    "request_status": "pending",
                }
            ],
            "created_at": 1.0,
            "updated_at": 1.0,
            "claudecode_session_id": "session-target-old",
        }
    ]

    class FakeClaudeCodeClient:
        last_context_usage = None

        def __init__(self, *args, **kwargs):
            pass

        def stream_chat(self, question, session_id="", on_delta=None, on_user_input=None, on_approval=None):
            captured["session_id"] = session_id
            on_user_input({"prompt": "input needed"})
            on_approval({"tool": "write"})
            on_delta("target streaming")
            return "target final", "session-target-new"

    monkeypatch.setattr(main, "ClaudeCodeClient", FakeClaudeCodeClient)
    monkeypatch.setattr(main, "wx_call_after_if_alive", lambda fn, *args, **kwargs: fn(*args, **kwargs))
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: None)
    monkeypatch.setattr(frame, "_push_remote_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_final_answer", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_refresh_history", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)

    frame._worker("", 0, "target question", "claudecode/default", False, "chat-target")

    archived = frame._find_archived_chat("chat-target")
    assert captured["session_id"] == "session-target-old"
    assert archived["turns"][0]["answer_md"] == "target final"
    assert archived["claudecode_session_id"] == "session-target-new"
    assert frame.active_claudecode_session_id == "session-visible"
    assert frame.active_session_turns[0]["answer_md"] == "visible answer"


def test_openclaw_background_worker_uses_target_session(frame, monkeypatch):
    captured = {}
    frame.active_chat_id = "chat-visible"
    frame.current_chat_id = "chat-visible"
    frame.active_openclaw_session_id = "openclaw-visible"
    frame.active_session_turns = [
        {"question": "visible question", "answer_md": "visible answer", "model": "openclaw/main", "created_at": 2.0}
    ]
    frame._current_chat_state.update({"id": "chat-visible", "turns": frame.active_session_turns})
    frame.archived_chats = [
        {
            "id": "chat-target",
            "title": "target",
            "turns": [{"question": "target question", "answer_md": main.REQUESTING_TEXT, "model": "openclaw/main", "created_at": 1.0}],
            "created_at": 1.0,
            "updated_at": 1.0,
            "openclaw_session_id": "openclaw-target",
        }
    ]

    class FakeOpenClawClient:
        last_context_usage = None

        def __init__(self, model, cli_manager=None):
            pass

        def stream_chat(self, question, session_id="", on_delta=None):
            captured["session_id"] = session_id
            return ""

    monkeypatch.setattr(main, "OpenClawClient", FakeOpenClawClient)
    monkeypatch.setattr(frame, "_ensure_active_openclaw_session_id", lambda: "openclaw-visible")
    monkeypatch.setattr(frame, "_call_after_if_alive", lambda *args, **kwargs: None)

    frame._worker("", 0, "target question", "openclaw/main", False, "chat-target")

    assert captured["session_id"] == "openclaw-target"
    assert frame.active_openclaw_session_id == "openclaw-visible"


def test_codex_worker_uses_target_chat_runtime_state_instead_of_current_chat(frame, monkeypatch):
    current_chat_id = frame.active_chat_id
    archived_chat_id = "chat-archived"
    frame.active_codex_thread_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    frame.active_codex_turn_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    frame.archived_chats = [
        {
            "id": archived_chat_id,
            "title": "旧聊天",
            "title_manual": False,
            "pinned": False,
            "model": "codex/main",
            "turns": [{"question": "旧问题", "answer_md": "", "model": "codex/main", "created_at": 1.0}],
            "created_at": 1.0,
            "updated_at": 1.0,
            "codex_thread_id": "11111111-1111-1111-1111-111111111111",
            "codex_turn_id": "22222222-2222-2222-2222-222222222222",
            "codex_turn_active": True,
            "codex_pending_prompt": "",
            "codex_pending_request": None,
            "codex_request_queue": [],
            "codex_thread_flags": [],
            "codex_latest_assistant_text": "",
            "codex_latest_assistant_phase": "",
        }
    ]

    sent = []

    class _Client:
        codex_model = main.DEFAULT_CODEX_MODEL

        def start(self):
            pass

        def start_turn(self, **payload):
            sent.append(payload)
            return "req-1"

    monkeypatch.setattr(frame, "_get_or_create_codex_client", lambda _chat_id: _Client())
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *args, **kwargs: None)

    _, request_turns, _ = frame._chat_target_for_request(archived_chat_id)
    request_turns[0].update({"request_status": "pending", "request_started_at": 1.0, "request_attempt_count": 1})
    frame._worker("", 0, "恢复旧聊天", "codex/main", False, archived_chat_id)

    assert sent
    assert sent[0]["chat_id"] == archived_chat_id
    assert sent[0]["thread_id"] == "11111111-1111-1111-1111-111111111111"
    assert sent[0]["turn_id"] == "22222222-2222-2222-2222-222222222222"
    assert sent[0]["question"] == "恢复旧聊天"
    assert sent[0]["should_steer"] is False
    assert frame.active_chat_id == current_chat_id
    archived = frame._find_archived_chat(archived_chat_id)
    assert archived is not None
    assert archived["codex_turn_id"] == "22222222-2222-2222-2222-222222222222"


def test_codex_worker_does_not_reuse_stale_thread_after_new_chat_reset(frame, monkeypatch):
    frame.active_chat_id = "chat-old"
    frame.current_chat_id = "chat-old"
    frame.active_session_turns = []
    frame.active_codex_thread_id = "thread-stale"
    frame.active_codex_turn_id = "turn-stale"
    frame.active_codex_turn_active = True
    frame._current_chat_state = {"id": "chat-old", "turns": []}

    monkeypatch.setattr(frame, "_archive_active_session", lambda **_kwargs: None)
    monkeypatch.setattr(frame, "_sync_codex_speed_combo_from_chat", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_mark_history_list_dirty", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_render_answer_list", lambda: None)
    monkeypatch.setattr(frame.input_edit, "SetFocus", lambda: None)
    monkeypatch.setattr(frame, "SetStatusText", lambda _text: None)
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: None)
    monkeypatch.setattr(frame, "_mark_openclaw_lifecycle_dirty", lambda: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *_args, **_kwargs: None)

    frame._on_new_chat_clicked(None)

    frame.active_session_turns = [
        {
            "question": "新问题",
            "answer_md": main.REQUESTING_TEXT,
            "model": "codex/gpt-5.4-medium",
            "created_at": 1.0,
        }
    ]
    frame.active_turn_idx = 0
    frame._current_chat_state["id"] = frame.active_chat_id
    frame._current_chat_state["model"] = "codex/gpt-5.4-medium"
    frame._current_chat_state["turns"] = frame.active_session_turns

    sent = []

    class _Client:
        def start(self):
            pass

        def start_turn(self, **payload):
            sent.append(payload)
            return "req-1"

    monkeypatch.setattr(frame, "_get_or_create_codex_client", lambda _chat_id, _model="": _Client())
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *args, **kwargs: None)

    _, request_turns, _ = frame._chat_target_for_request(frame.active_chat_id)
    request_turns[0].update({"request_status": "pending", "request_started_at": 1.0, "request_attempt_count": 1})
    frame._run_codex_turn_worker(frame.active_chat_id, 0, "新问题", "codex/gpt-5.4-medium")

    assert sent[0]["thread_id"] == ""


def test_load_project_folder_appends_system_message_and_rerenders(frame, monkeypatch, tmp_path):
    selected = tmp_path / "demo-project"
    selected.mkdir()
    rendered = {"count": 0}
    saved = {"count": 0}
    statuses = []

    class _FakeDirDialog:
        def __init__(self, parent, message, defaultPath, style):
            self.parent = parent
            self.message = message
            self.defaultPath = defaultPath
            self.style = style

        def ShowModal(self):
            return main.wx.ID_OK

        def GetPath(self):
            return str(selected)

        def Destroy(self):
            return None

    monkeypatch.setattr(main.wx, "DirDialog", _FakeDirDialog)
    frame._render_answer_list = lambda: rendered.__setitem__("count", rendered["count"] + 1)
    frame._save_state = lambda: saved.__setitem__("count", saved["count"] + 1)
    frame.SetStatusText = lambda text: statuses.append(text)

    frame._load_project_folder()

    assert frame.active_project_folder == str(selected)
    assert frame.active_session_turns[-1]["model"] == "system"
    assert "已载入项目文件夹" in frame.active_session_turns[-1]["answer_md"]
    assert str(selected) in frame.active_session_turns[-1]["answer_md"]
    assert rendered["count"] == 1
    assert saved["count"] == 1
    assert statuses[-1] == "已载入项目：demo-project"


def test_fallback_model_order_for_deepseek_variant(frame):
    out = frame._candidate_fallback_models("deepseek/deepseek-r1-0528-qwen3-8b")
    assert out[0] == "deepseek/deepseek-r1-0528"
    assert "openai/gpt-5.2" in out


def test_claudecode_worker_passes_shared_cli_manager(frame, monkeypatch):
    manager = object()
    frame._cli_agent_manager = manager
    frame.active_session_turns = [{"question": "q", "answer_md": "", "model": "claudecode/default"}]
    seen = {}

    class _Client:
        def __init__(self, full_auto=False, cli_manager=None):
            seen["full_auto"] = full_auto
            seen["cli_manager"] = cli_manager

        def stream_chat(self, question, session_id="", on_delta=None, on_user_input=None, on_approval=None):
            return ("answer", "session-new")

    monkeypatch.setattr(main, "ClaudeCodeClient", _Client)
    monkeypatch.setattr(frame, "_call_after_if_alive", lambda *args, **kwargs: None)

    frame._worker("", 0, "q", "claudecode/default")

    assert seen == {"full_auto": True, "cli_manager": manager}


def test_openclaw_worker_passes_shared_cli_manager(frame, monkeypatch):
    manager = object()
    frame._cli_agent_manager = manager
    frame.active_session_turns = [{"question": "q", "answer_md": "", "model": "openclaw/main"}]
    seen = {}

    class _Client:
        def __init__(self, model, cli_manager=None):
            seen["model"] = model
            seen["cli_manager"] = cli_manager

        def stream_chat(self, question, session_id="", on_delta=None):
            return "answer"

    monkeypatch.setattr(main, "OpenClawClient", _Client)
    monkeypatch.setattr(frame, "_ensure_active_openclaw_session_id", lambda: "zgwd-test")
    monkeypatch.setattr(frame, "_call_after_if_alive", lambda *args, **kwargs: None)

    frame._worker("", 0, "q", "openclaw/main")

    assert seen == {"model": "openclaw/main", "cli_manager": manager}


def test_openclaw_worker_stores_client_context_usage(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state["id"] = "chat-current"
    frame.active_session_turns = [{"question": "q", "answer_md": "", "model": "openclaw/main"}]
    usage = {
        "used_tokens": 113260,
        "context_window": 272000,
        "source": "openclaw",
        "exact": True,
        "fresh": True,
        "model": "gpt-5.4",
        "updated_at": 1.0,
    }

    class _Client:
        def __init__(self, model, cli_manager=None):
            self.last_context_usage = None

        def stream_chat(self, question, session_id="", on_delta=None):
            self.last_context_usage = usage
            return "answer"

    calls = []
    monkeypatch.setattr(main, "OpenClawClient", _Client)
    monkeypatch.setattr(frame, "_ensure_active_openclaw_session_id", lambda: "zgwd-test")
    monkeypatch.setattr(frame, "_call_after_if_alive", lambda *args, **kwargs: calls.append(args))

    frame._worker("", 0, "q", "openclaw/main", chat_id="chat-current")

    assert frame._pending_context_usage_by_turn[("chat-current", 0)] == usage


def test_submit_question_resets_answer_list_visible_limit(frame, monkeypatch):
    frame.answer_visible_row_limit = 300
    monkeypatch.setattr(frame, "_start_codex_worker_for_turn", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda *args, **kwargs: None)

    ok, message = frame._submit_question("new question", source="local", model="codex/main")

    assert ok is True
    assert message == ""
    assert frame.answer_visible_row_limit == 100


def test_submit_question_resets_execution_list_visible_limit(frame, monkeypatch):
    frame.execution_visible_row_limit = 300
    monkeypatch.setattr(frame, "_start_codex_worker_for_turn", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda *args, **kwargs: None)

    ok, message = frame._submit_question("new question", source="local", model="codex/main")

    assert ok is True
    assert message == ""
    assert frame.execution_visible_row_limit == 100


def test_submit_question_clears_visible_execution_process_for_new_turn(frame, monkeypatch):
    frame.active_session_turns = [
        {"question": "old", "answer_md": "old answer", "model": "codex/main"},
    ]
    frame.active_turn_idx = 0
    frame._current_chat_state = {
        "id": "chat-current",
        "turns": frame.active_session_turns,
        "detail_panel_mode": "execution",
        "execution_steps": [
            {"step": "old process", "list_text": "old process", "display_kind": "commentary", "turn_idx": 0},
        ],
    }
    monkeypatch.setattr(frame, "_start_codex_worker_for_turn", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda *args, **kwargs: None)

    ok, message = frame._submit_question("new question", source="local", model="codex/main")

    assert ok is True
    assert message == ""
    visible = [frame.execution_list.GetString(idx) for idx in range(frame.execution_list.GetCount())]
    assert visible == ["暂无执行过程"]


def test_new_chat_assigns_candidate_without_mutating_previous_chat(frame, monkeypatch):
    frame.active_chat_id = "chat-a"
    frame.current_chat_id = "chat-a"
    frame.active_session_turns[:] = [{"question": "A", "answer_md": "done", "model": "codex/main"}]
    frame._current_chat_state = {
        "id": "chat-a",
        "title": "A",
        "model": "codex/main",
        "turns": frame.active_session_turns,
    }
    frame.selected_model = "codex/main"
    frame.model_combo.SetValue(main.model_display_name("kimi/main"))
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *args, **kwargs: None)

    frame._on_model_changed(None)
    frame._on_new_chat_clicked(None)

    archived_a = frame._find_archived_chat("chat-a")
    assert archived_a is not None
    assert archived_a["model"] == "codex/main"
    assert frame._current_chat_state["model"] == "kimi/main"
    assert frame.selected_model == "kimi/main"


def test_existing_chat_candidate_commits_only_after_nonempty_submit(frame, monkeypatch):
    frame.active_chat_id = frame.current_chat_id = "chat-a"
    frame._current_chat_state = {"id": "chat-a", "model": "codex/main", "turns": frame.active_session_turns}
    frame.selected_model = "kimi/main"
    monkeypatch.setattr(frame, "_start_kimi_worker_for_turn", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda *args, **kwargs: None)

    rejected, _ = frame._submit_question("", model="kimi/main")
    assert rejected is False
    assert frame._current_chat_state["model"] == "codex/main"

    accepted, message = frame._submit_question("hello", model="kimi/main")
    assert (accepted, message) == (True, "")
    assert frame._current_chat_state["model"] == "kimi/main"
    assert frame.active_session_turns[-1]["model"] == "kimi/main"


@pytest.mark.parametrize("provider", ["codex", "kimi"])
def test_provider_start_rejection_restores_committed_model(frame, monkeypatch, provider):
    frame.active_chat_id = frame.current_chat_id = "chat-a"
    frame._current_chat_state = {"id": "chat-a", "model": "openai/gpt-5.2", "turns": frame.active_session_turns}
    candidate = main.DEFAULT_CODEX_MODEL if provider == "codex" else "kimi/main"
    monkeypatch.setattr(frame, f"_start_{provider}_worker_for_turn", lambda *args, **kwargs: False)
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda *args, **kwargs: None)

    ok, _ = frame._submit_question("hello", model=candidate)

    assert ok is False
    assert frame._current_chat_state["model"] == "openai/gpt-5.2"
    assert frame.selected_model == "openai/gpt-5.2"
    assert frame.active_session_turns == []


@pytest.mark.parametrize(
    ("provider", "candidate", "command"),
    [("codex", main.DEFAULT_CODEX_MODEL, "/status"), ("kimi", "kimi/main", "/status")],
)
def test_local_command_start_exception_restores_committed_model(frame, monkeypatch, provider, candidate, command):
    frame.active_chat_id = frame.current_chat_id = "chat-a"
    frame._current_chat_state = {"id": "chat-a", "model": "openai/gpt-5.2", "turns": frame.active_session_turns}
    monkeypatch.setattr(
        frame, f"_start_{provider}_local_command_worker_for_turn",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("rejected")),
    )
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)

    ok, message = frame._submit_question(command, model=candidate)

    assert ok is False
    assert "rejected" in message
    assert frame._current_chat_state["model"] == "openai/gpt-5.2"
    assert frame.selected_model == "openai/gpt-5.2"
    assert frame.active_session_turns == []
    assert frame.input_edit.GetValue() == command


def test_active_claude_continuation_rejects_wrong_owner_without_commit(frame, monkeypatch):
    frame.active_chat_id = frame.current_chat_id = "chat-a"
    frame._current_chat_state = {"id": "chat-a", "model": "codex/main", "turns": []}
    monkeypatch.setattr(frame, "_send_active_claudecode_input", lambda *_args: pytest.fail("wrong owner must not receive input"))
    monkeypatch.setattr(frame, "_find_archived_chat", lambda chat_id: None)
    monkeypatch.setattr(frame, "_start_claudecode_worker_for_turn", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda *args, **kwargs: None)

    ok, _ = frame._submit_question("hello", model="claudecode/default", chat_id="chat-b")

    assert ok is True
    assert frame.active_chat_id == "chat-b"


def test_offscreen_remote_submit_respects_pending_clear_fence_without_mutation(frame, monkeypatch):
    target = {"id": "chat-target", "model": "kimi/main", "turns": []}
    frame.archived_chats = [target]
    frame._chat_store_enabled = True
    checked = []
    monkeypatch.setattr(
        frame,
        "_reconcile_clear_operation_for_owner",
        lambda owner, **kwargs: checked.append((owner, kwargs)) or {"state": "resend_blocked"},
    )
    monkeypatch.setattr(frame, "_start_kimi_worker_for_turn", lambda *_args: pytest.fail("fenced owner must not dispatch"))
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: pytest.fail("fenced owner must not persist"))

    ok, message = frame._submit_archived_remote_question(target, "new", "kimi/main")

    assert ok is False
    assert "recovery" in message
    assert checked == [("chat-target", {"surface_recovery": False})]
    assert target["turns"] == []


def test_rejected_remote_new_chat_removes_orphan_and_restores_visible_owner(frame, monkeypatch):
    frame.active_chat_id = frame.current_chat_id = "chat-visible"
    frame._current_chat_state = {"id": "chat-visible", "title": "Visible", "model": "codex/main", "turns": []}
    monkeypatch.setattr(frame, "_submit_question", lambda *_args, **_kwargs: (False, "rejected"))
    monkeypatch.setattr(frame, "_push_remote_state", lambda *args, **kwargs: pytest.fail("rejection must not publish"))
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *args, **kwargs: None)

    status, body = frame._remote_api_message_ui({"chat_id": "remote-new", "text": "hello", "model": "kimi/main"})

    assert (status, body["accepted"]) == (400, False)
    assert frame.active_chat_id == "chat-visible"
    assert frame._find_archived_chat("remote-new") is None
    assert frame._current_chat_state["model"] == "codex/main"


def test_new_chat_preserves_current_codex_speed_combo_selection(frame, monkeypatch):
    frame.selected_model = main.DEFAULT_CODEX_MODEL
    frame.model_combo.SetValue(main.model_display_name(main.DEFAULT_CODEX_MODEL))
    frame._current_chat_state["model"] = main.DEFAULT_CODEX_MODEL
    frame.codex_speed_combo.SetValue("快速")
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda *args, **kwargs: None)

    frame._on_new_chat_clicked(None)

    assert frame.selected_model == main.DEFAULT_CODEX_MODEL
    assert frame.codex_speed_combo.GetValue() == "快速"
    assert frame._current_chat_state["codex_service_tier"] == "fast"


def test_new_chat_button_shift_enter_prompts_for_manual_title_and_blocks_auto_rename(frame, monkeypatch):
    frame.new_chat_button.SetFocusFromKbd()
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)

    class _Dialog:
        def __init__(self, *_args, **_kwargs):
            pass

        def ShowModal(self):
            return wx.ID_OK

        def GetValue(self):
            return "手动命名聊天"

        def Destroy(self):
            pass

    monkeypatch.setattr(main.wx, "TextEntryDialog", _Dialog)

    class E:
        def GetKeyCode(self):
            return wx.WXK_RETURN

        def ShiftDown(self):
            return True

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            raise AssertionError("Shift+Enter on new chat should be handled")

    frame._on_generic_key_down(E())

    assert frame._current_chat_state["title"] == "手动命名聊天"
    assert frame._current_chat_state["title_manual"] is True
    assert frame._current_chat_state["title_source"] == "manual"

    class _NoOpThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            pass

        def start(self):
            pass

    monkeypatch.setattr(main.threading, "Thread", _NoOpThread)

    ok, message = frame._submit_question("帮我整理安卓自动化测试方案", source="local", model="openai/gpt-5.2")

    assert ok is True
    assert message == ""
    assert frame._current_chat_state["title"] == "手动命名聊天"
    assert frame._current_chat_state["title_manual"] is True
    assert frame._current_chat_state["title_source"] == "manual"


def test_submit_question_defers_state_save_after_send(frame, monkeypatch):
    frame.selected_model = main.DEFAULT_CODEX_MODEL
    frame.model_combo.SetValue(main.model_display_name(main.DEFAULT_CODEX_MODEL))
    deferred = []
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: pytest.fail("send path should not synchronously save state"))
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: deferred.append(True))
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_schedule_first_question_auto_title", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_start_codex_worker_for_turn", lambda *args, **kwargs: None)

    ok, message = frame._submit_question("hello", source="remote", model=main.DEFAULT_CODEX_MODEL)

    assert ok is True
    assert message == ""
    assert deferred == [True]


def test_clear_context_auto_resend_first_question_only_when_requested(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_session_turns = [
        {"question": "第一条问题", "answer_md": "回答一", "model": main.DEFAULT_CODEX_MODEL},
        {"question": "第二条问题", "answer_md": "回答二", "model": main.DEFAULT_CODEX_MODEL},
    ]
    frame._current_chat_state = {"id": "chat-current", "turns": frame.active_session_turns}
    monkeypatch.setattr(frame, "_render_answer_list", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: None)
    monkeypatch.setattr(frame, "_mark_openclaw_lifecycle_dirty", lambda: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_state", lambda *args, **kwargs: None)
    submitted = []
    monkeypatch.setattr(
        frame,
        "_submit_question",
        lambda question, **kwargs: submitted.append((question, kwargs)) or (True, ""),
    )

    assert frame._clear_context_and_start_new_chat() is True
    assert submitted == []

    frame.active_session_turns = [
        {"question": "第一条问题", "answer_md": "回答一", "model": main.DEFAULT_CODEX_MODEL},
        {"question": "第二条问题", "answer_md": "回答二", "model": main.DEFAULT_CODEX_MODEL},
    ]
    frame._current_chat_state["turns"] = frame.active_session_turns

    assert frame._clear_context_and_start_new_chat(auto_resend_first=True) is True
    assert frame.active_session_turns == []
    assert frame._current_chat_state["turns"] == []
    assert submitted == [("第一条问题", {"source": "local", "model": main.DEFAULT_CODEX_MODEL, "chat_id": "chat-current"})]


def test_clear_context_auto_resend_skips_local_command_turns(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_session_turns = [
        {"question": "本地命令", "local_command": True, "model": main.DEFAULT_CODEX_MODEL},
        {"question": "真正的问题", "answer_md": "回答", "model": main.DEFAULT_CODEX_MODEL},
    ]
    frame._current_chat_state = {"id": "chat-current", "turns": frame.active_session_turns}
    monkeypatch.setattr(frame, "_render_answer_list", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: None)
    monkeypatch.setattr(frame, "_mark_openclaw_lifecycle_dirty", lambda: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_state", lambda *args, **kwargs: None)
    submitted = []
    monkeypatch.setattr(
        frame,
        "_submit_question",
        lambda question, **kwargs: submitted.append(question) or (True, ""),
    )

    assert frame._clear_context_and_start_new_chat(auto_resend_first=True) is True
    assert submitted == ["真正的问题"]


@pytest.mark.parametrize("model,starter_name", [
    (main.DEFAULT_CODEX_MODEL, "_start_codex_worker_for_turn"),
    (main.DEFAULT_KIMI_MODEL, "_start_kimi_worker_for_turn"),
])
def test_active_clear_resend_sound_follows_worker_acceptance(frame, monkeypatch, model, starter_name):
    frame.active_chat_id = frame.current_chat_id = "owner"
    frame.active_session_turns = []
    frame._current_chat_state = {"id": "owner", "turns": frame.active_session_turns, "model": model}
    events = []
    state = {"value": "clear_acknowledged"}

    def claim(_op, **_identity):
        state["value"] = "resend_dispatched"
        return {"operation_id": "op", "state": state["value"]}

    frame.chat_store = SimpleNamespace(
        get_clear_operation=lambda _op: {**operation, "snapshot": {"question": "first", "model": model}},
        claim_clear_resend_dispatch=claim,
        list_chat_summaries=lambda: [],
        consume_clear_resend_sound=lambda _op, *, chat_id, revision: (
            {"operation_id": "op"} if (chat_id, revision) == ("owner", 7) else None
        ),
        transition_clear_operation=lambda _op, new_state, **_kw: state.__setitem__("value", new_state),
    )
    monkeypatch.setattr(frame, starter_name, lambda *_args: events.append("accepted"))
    monkeypatch.setattr(frame, "_play_send_sound", lambda: events.append("sound"))
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: None)

    operation = {"operation_id": "op", "chat_id": "owner", "revision": 7}
    assert frame._dispatch_clear_operation_resend(operation, {"question": "first", "model": model}) is True
    assert events == ["accepted", "sound"]


def _offscreen_resend_store(model: str, session_id: str = "") -> SimpleNamespace:
    archived = {"id": "chat-archived", "turns": [{"question": "old memory"}],
                "model": model, "claudecode_session_id": session_id}
    return SimpleNamespace(
        archived=archived,
        get_clear_operation=lambda _op: {
            "operation_id": "op", "chat_id": "chat-archived", "revision": 7,
            "snapshot": {"question": "first", "model": model},
        },
        claim_clear_resend_dispatch=lambda _op, **_identity: {"operation_id": "op", "state": "resend_dispatched"},
        consume_clear_resend_sound=lambda _op, *, chat_id, revision: {"operation_id": "op"},
        transition_clear_operation=lambda _op, _state, **_kw: None,
    )


def _prepare_offscreen_resend(frame, store) -> None:
    frame.archived_chats = [store.archived]
    frame.active_chat_id = frame.current_chat_id = "chat-active"
    frame.active_session_turns = [{"question": "active"}]
    frame._current_chat_state = {"id": "chat-active", "turns": frame.active_session_turns}
    frame.chat_store = store


def test_offscreen_clear_resend_claude_passes_session_and_model_as_named_identity(frame, monkeypatch):
    store = _offscreen_resend_store(main.DEFAULT_CLAUDECODE_MODEL, session_id="session-42")
    _prepare_offscreen_resend(frame, store)
    events = []
    captured = {}

    def starter(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        events.append("accepted")

    monkeypatch.setattr(frame, "_start_claudecode_worker_for_turn", starter)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: events.append("sound"))
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: None)

    operation = {"operation_id": "op", "chat_id": "chat-archived", "revision": 7}
    assert frame._dispatch_clear_operation_resend(
        operation, {"question": "first", "model": main.DEFAULT_CLAUDECODE_MODEL}) is True

    # chat_id, turn_idx, question, session_id, model — model must reach the
    # model parameter instead of falling into the session_id slot.
    assert captured["args"][0] == "chat-archived"
    assert captured["args"][2] == "first"
    assert captured["args"][3] == "session-42"
    assert captured["args"][4] == main.DEFAULT_CLAUDECODE_MODEL
    assert events == ["accepted", "sound"]
    assert frame.active_chat_id == "chat-active"
    assert frame.active_session_turns == [{"question": "active"}]


def test_offscreen_clear_resend_generic_thread_sound_once_and_failure_silent(frame, monkeypatch):
    store = _offscreen_resend_store("doubao-2.0-lite")
    _prepare_offscreen_resend(frame, store)
    events = []
    spawned = []

    class _FakeThread:
        def __init__(self, *args, **kwargs):
            spawned.append((args, kwargs))

        def start(self):
            events.append("accepted")

    monkeypatch.setattr(main.threading, "Thread", _FakeThread)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: events.append("sound"))
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: None)

    operation = {"operation_id": "op", "chat_id": "chat-archived", "revision": 7}
    assert frame._dispatch_clear_operation_resend(
        operation, {"question": "first", "model": "doubao-2.0-lite"}) is True
    assert spawned, "generic providers must dispatch through the worker thread"
    thread_args = spawned[0][1].get("args") or spawned[0][0]
    assert thread_args[3] == "doubao-2.0-lite"
    assert events == ["accepted", "sound"]


def test_offscreen_clear_resend_sound_grant_failure_keeps_dispatch_silent(frame, monkeypatch):
    store = _offscreen_resend_store("doubao-2.0-lite")
    store.consume_clear_resend_sound = lambda _op, *, chat_id, revision: None
    _prepare_offscreen_resend(frame, store)
    events = []

    class _FakeThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            events.append("accepted")

    monkeypatch.setattr(main.threading, "Thread", _FakeThread)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: pytest.fail("missing grant must stay silent"))
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: None)

    operation = {"operation_id": "op", "chat_id": "chat-archived", "revision": 7}
    assert frame._dispatch_clear_operation_resend(
        operation, {"question": "first", "model": "doubao-2.0-lite"}) is True
    assert events == ["accepted"]


def test_active_clear_resend_rejected_start_is_silent(frame, monkeypatch):
    frame.active_chat_id = frame.current_chat_id = "owner"
    frame.active_session_turns = []
    frame._current_chat_state = {"id": "owner", "turns": frame.active_session_turns}
    transitions = []
    frame.chat_store = SimpleNamespace(
        claim_clear_resend_dispatch=lambda _op, **_identity: {"operation_id": "op"},
        get_clear_operation=lambda _op: {
            "operation_id": "op", "chat_id": "owner", "revision": 7,
            "state": "clear_acknowledged",
            "snapshot": {"question": "first", "model": main.DEFAULT_CODEX_MODEL},
        },
        transition_clear_operation=lambda op, state, **kw: transitions.append((op, state, kw)),
    )
    monkeypatch.setattr(frame, "_start_codex_worker_for_turn", lambda *_args: (_ for _ in ()).throw(RuntimeError("reject")))
    monkeypatch.setattr(frame, "_play_send_sound", lambda: pytest.fail("rejected starts must be silent"))
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: None)

    operation = {"operation_id": "op", "chat_id": "owner", "revision": 7}
    assert frame._dispatch_clear_operation_resend(
        operation, {"question": "first", "model": main.DEFAULT_CODEX_MODEL}
    ) is False
    assert transitions[-1][1:] == ("resend_blocked", {"failure_code": "PROVIDER_REJECTED"})


def test_non_durable_clear_resend_text_fallback_fails_closed_and_is_silent(frame, monkeypatch):
    monkeypatch.setattr(frame, "_submit_question", lambda *_args, **_kwargs: pytest.fail("must not submit"))
    monkeypatch.setattr(frame, "_play_send_sound", lambda: pytest.fail("must stay silent"))

    assert frame._dispatch_clear_operation_resend(
        None, {"question": " ", "text": "legacy fallback text"}
    ) is False


def test_clear_resend_malformed_identity_store_and_payload_mismatch_fail_closed(frame, tmp_path, monkeypatch):
    store = main.ChatStore(tmp_path / "fail-closed.db")
    store.initialize()
    store.upsert_chat({"id": "owner"})
    store.replace_turns("owner", [{"question": "first", "model": main.DEFAULT_CODEX_MODEL}])
    operation = store.begin_clear_operation("owner", idempotency_key="request")
    frame.chat_store = store
    frame.active_chat_id = frame.current_chat_id = "owner"
    frame.active_session_turns = []
    frame._current_chat_state = {"id": "owner", "turns": frame.active_session_turns}
    monkeypatch.setattr(frame, "_submit_question", lambda *_args, **_kwargs: pytest.fail("must not submit"))
    snapshot = operation["snapshot"]

    assert frame._dispatch_clear_operation_resend(None, snapshot) is False
    assert frame._dispatch_clear_operation_resend({**operation, "operation_id": ""}, snapshot) is False
    assert frame._dispatch_clear_operation_resend({**operation, "chat_id": ""}, snapshot) is False
    assert frame._dispatch_clear_operation_resend({**operation, "revision": None}, snapshot) is False
    assert frame._dispatch_clear_operation_resend({**operation, "chat_id": "wrong"}, snapshot) is False
    assert frame._dispatch_clear_operation_resend({**operation, "revision": operation["revision"] + 1}, snapshot) is False
    assert frame._dispatch_clear_operation_resend(operation, {**snapshot, "question": "tampered"}) is False
    frame.chat_store = None
    assert frame._dispatch_clear_operation_resend(operation, snapshot) is False
    assert store.get_clear_operation(operation["operation_id"])["state"] == "clear_acknowledged"


def test_accepted_worker_sound_store_exception_remains_dispatched_and_silent(frame, tmp_path, monkeypatch):
    store = main.ChatStore(tmp_path / "sound-error.db")
    store.initialize()
    store.upsert_chat({"id": "owner"})
    store.replace_turns("owner", [{"question": "first", "model": main.DEFAULT_CODEX_MODEL}])
    operation = store.begin_clear_operation("owner", idempotency_key="request")
    frame.chat_store = store
    frame.active_chat_id = frame.current_chat_id = "owner"
    frame.active_session_turns = []
    frame._current_chat_state = {"id": "owner", "turns": frame.active_session_turns}
    accepted = []
    monkeypatch.setattr(frame, "_start_codex_worker_for_turn", lambda *_args: accepted.append(True))
    monkeypatch.setattr(store, "consume_clear_resend_sound", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk")))
    monkeypatch.setattr(frame, "_play_send_sound", lambda: pytest.fail("must stay silent"))

    assert frame._dispatch_clear_operation_resend(operation, operation["snapshot"]) is True
    assert accepted == [True]
    assert len(frame.active_session_turns) == 1
    assert store.get_clear_operation(operation["operation_id"])["state"] == "resend_dispatched"


def test_starter_rejection_removes_pending_projection(frame, tmp_path, monkeypatch):
    store = main.ChatStore(tmp_path / "starter-reject.db")
    store.initialize()
    store.upsert_chat({"id": "owner"})
    store.replace_turns("owner", [{"question": "first", "model": main.DEFAULT_CODEX_MODEL}])
    operation = store.begin_clear_operation("owner", idempotency_key="request")
    frame.chat_store = store
    frame.active_chat_id = frame.current_chat_id = "owner"
    frame.active_session_turns = []
    frame._current_chat_state = {"id": "owner", "turns": frame.active_session_turns}
    monkeypatch.setattr(frame, "_start_codex_worker_for_turn", lambda *_args: (_ for _ in ()).throw(RuntimeError("reject")))
    monkeypatch.setattr(frame, "_play_send_sound", lambda: pytest.fail("must stay silent"))

    assert frame._dispatch_clear_operation_resend(operation, operation["snapshot"]) is False
    assert frame.active_session_turns == []
    assert store.get_clear_operation(operation["operation_id"])["state"] == "resend_blocked"


@pytest.mark.parametrize("state", ["superseded", "resend_blocked", "completed_with_resend"])
def test_non_dispatchable_clear_operation_never_plays_acceptance_sound(frame, monkeypatch, state):
    frame.chat_store = SimpleNamespace(
        consume_clear_resend_sound=lambda _op, *, chat_id, revision: None
    )
    monkeypatch.setattr(frame, "_play_send_sound", lambda: pytest.fail("stale or terminal operation must stay silent"))

    assert frame._play_clear_resend_accepted_sound("op", "owner", 3) is False


def test_remote_offscreen_clear_uses_operation_snapshot_and_preserves_active_owner(frame, monkeypatch):
    archived = {"id": "chat-archived", "turns": [{"question": "old memory"}]}
    frame.archived_chats = [archived]
    frame.active_chat_id = frame.current_chat_id = "chat-active"
    frame.active_session_turns = [{"question": "active"}]
    frame._current_chat_state = {"id": "chat-active", "turns": frame.active_session_turns}
    operation = {"operation_id": "op-remote", "chat_id": "chat-archived", "revision": 3,
                 "snapshot": {"question": "durable first", "model": main.DEFAULT_CODEX_MODEL}}
    begin_calls = []
    dispatches = []
    monkeypatch.setattr(frame, "_begin_durable_clear_operation",
                        lambda owner, request_id: begin_calls.append((owner, request_id)) or operation)
    monkeypatch.setattr(frame, "_dispatch_clear_operation_resend",
                        lambda op, payload: dispatches.append((op, payload)) or True)
    monkeypatch.setattr(frame, "_mark_chat_turns_dirty", lambda *a, **k: None)
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *a: None)
    monkeypatch.setattr(frame, "_push_remote_state", lambda *a: None)
    frame._chat_store_enabled = False

    assert frame._clear_context_for_chat_id("chat-archived", auto_resend_first=True, request_id="request-remote") == "cleared"
    assert begin_calls == [("chat-archived", "request-remote")]
    assert dispatches == [(operation, operation["snapshot"])]
    assert frame.active_chat_id == frame.current_chat_id == "chat-active"
    assert frame.active_session_turns == [{"question": "active"}]


def test_restart_completed_turn_reconciles_without_redispatch(frame, tmp_path, monkeypatch):
    store = main.ChatStore(tmp_path / "restart-reconcile.db")
    store.initialize()
    store.upsert_chat({"id": "owner"})
    store.replace_turns("owner", [{"question": "first"}])
    operation = store.begin_clear_operation("owner", idempotency_key="request")
    store.claim_clear_resend_dispatch(operation["operation_id"], chat_id="owner", revision=operation["revision"])
    store.replace_turns("owner", [{
        "request_status": "done", "clear_operation_id": operation["operation_id"],
        "clear_revision": operation["revision"],
    }])
    frame.chat_store = store
    frame._pending_clear_recoveries = [operation]
    monkeypatch.setattr(
        frame, "_dispatch_clear_operation_resend",
        lambda *_args, **_kwargs: pytest.fail("completed persisted resend must not redispatch"),
    )

    assert frame._reconcile_clear_operation_for_owner("owner") is None
    assert store.get_clear_operation(operation["operation_id"])["state"] == "completed_with_resend"


def test_uncertain_restart_surfaces_recovery_choice_without_automatic_resend(frame, tmp_path, monkeypatch):
    store = main.ChatStore(tmp_path / "restart-uncertain.db")
    store.initialize()
    store.upsert_chat({"id": "owner"})
    store.replace_turns("owner", [{"question": "first"}])
    operation = store.begin_clear_operation("owner", idempotency_key="request")
    store.claim_clear_resend_dispatch(operation["operation_id"], chat_id="owner", revision=operation["revision"])
    pending = store.recoverable_clear_operations()
    frame.chat_store = store
    frame._pending_clear_recoveries = pending
    offered = []
    monkeypatch.setattr(
        frame, "_dispatch_clear_operation_resend",
        lambda *_args, **_kwargs: pytest.fail("uncertain delivery must not resend automatically"),
    )
    monkeypatch.setattr(
        frame, "_offer_clear_operation_recovery",
        lambda operation_id: offered.append(operation_id) or frame.recover_clear_operation(operation_id, "cancel"),
    )

    assert frame._reconcile_clear_operation_for_owner("owner", surface_recovery=True) is None
    assert offered == [operation["operation_id"]]
    assert store.get_clear_operation(operation["operation_id"])["state"] == "completed_clear_only"


@pytest.mark.parametrize("state", ["requested", "clear_acknowledged", "completed_clear_only"])
def test_clear_result_rejects_pre_dispatch_and_cancelled_states(frame, tmp_path, state):
    store = main.ChatStore(tmp_path / f"reject-{state}.db")
    store.initialize()
    store.upsert_chat({"id": "owner"})
    store.replace_turns("owner", [{"question": "first"}])
    operation = store.begin_clear_operation("owner", idempotency_key="request")
    if state == "requested":
        with store._connect() as conn:
            conn.execute(
                "UPDATE clear_operations SET state='requested' WHERE operation_id=?",
                (operation["operation_id"],),
            )
    elif state == "completed_clear_only":
        store.transition_clear_operation(operation["operation_id"], "completed_clear_only")
    frame.chat_store = store
    turn = {
        "clear_operation_id": operation["operation_id"],
        "clear_revision": operation["revision"],
    }

    assert frame._accept_clear_operation_result(turn, "owner") is False
    with store._connect() as conn:
        quarantine = conn.execute(
            "SELECT reason FROM identity_quarantine ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert quarantine["reason"] == "SUPERSEDED_CLEAR_RESULT"
    assert store.get_clear_operation(operation["operation_id"])["state"] == state


def test_clear_recovery_filters_malformed_pending_entries(frame, tmp_path):
    store = main.ChatStore(tmp_path / "malformed-pending.db")
    store.initialize()
    store.upsert_chat({"id": "owner"})
    store.replace_turns("owner", [{"question": "first"}])
    operation = store.begin_clear_operation("owner", idempotency_key="request")
    store.transition_clear_operation(
        operation["operation_id"], "resend_blocked", failure_code="PROVIDER_FAILURE"
    )
    frame.chat_store = store
    frame._pending_clear_recoveries = [None, "bad", {"operation_id": "other"}, operation]

    assert frame.recover_clear_operation(operation["operation_id"], "cancel") is True
    assert frame._pending_clear_recoveries == [{"operation_id": "other"}]


def test_clear_reconciliation_declines_malformed_operation_revision_without_crashing(frame):
    operation = {
        "operation_id": "op-malformed", "chat_id": "owner",
        "revision": "not-an-integer", "state": "resend_dispatched",
    }
    store = SimpleNamespace(
        active_clear_operation=lambda owner: operation,
        reconcile_completed_clear_operation=lambda *_args, **_kwargs: None,
    )
    frame.chat_store = store
    frame._pending_clear_recoveries = []

    assert frame._reconcile_clear_operation_for_owner("owner") == operation


def test_clear_context_shortcut_targets_visible_history_chat_not_running_active_chat(frame, monkeypatch):
    frame.active_chat_id = "chat-a"
    frame.current_chat_id = "chat-a"
    frame.view_mode = "history"
    frame.view_history_id = "chat-b"
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
    frame.active_codex_turn_active = True
    frame._current_chat_state = {
        "id": "chat-a",
        "title": "聊天 A",
        "turns": frame.active_session_turns,
        "codex_thread_id": "thread-a",
        "codex_turn_active": True,
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
            "codex_turn_active": True,
            "execution_steps": [{"list_text": "B 执行过程"}],
            "created_at": 2.0,
            "updated_at": 3.0,
        }
    ]
    pushed = []
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda chat_id: pushed.append(chat_id))
    monkeypatch.setattr(frame, "_push_remote_state", lambda chat_id: pushed.append(f"state:{chat_id}"))
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    assert frame._clear_context_and_start_new_chat() is True

    archived = frame._find_archived_chat("chat-b")
    assert frame.active_chat_id == "chat-a"
    assert frame.current_chat_id == "chat-a"
    assert frame.active_session_turns[0]["question"] == "A 正在执行"
    assert frame.active_codex_thread_id == "thread-a"
    assert frame.active_codex_turn_active is True
    assert frame._current_chat_state["turns"] == frame.active_session_turns
    assert archived["turns"] == []
    assert archived["codex_thread_id"] == ""
    assert archived["codex_turn_active"] is False
    assert archived["execution_steps"] == []
    assert frame.answer_list.GetCount() >= 1
    assert "B 旧问题" not in [frame.answer_list.GetString(idx) for idx in range(frame.answer_list.GetCount())]
    assert frame.answer_list.GetString(frame.answer_list.GetCount() - 1) == "以开启新会话"
    assert pushed == ["chat-b", "state:chat-b"]


def test_clear_context_tail_notice_stays_with_cleared_history_chat(frame, monkeypatch):
    frame.active_chat_id = "chat-a"
    frame.current_chat_id = "chat-a"
    frame.active_session_turns = [
        {
            "question": "A 问题",
            "answer_md": "A 回答",
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": 1.0,
        }
    ]
    frame._current_chat_state = {
        "id": "chat-a",
        "title": "聊天 A",
        "turns": frame.active_session_turns,
        "created_at": 1.0,
        "updated_at": 1.0,
    }
    frame.archived_chats = [
        {
            "id": "chat-c",
            "title": "聊天 C",
            "turns": [
                {
                    "question": "C 问题",
                    "answer_md": "C 回答",
                    "model": main.DEFAULT_CODEX_MODEL,
                    "created_at": 2.0,
                }
            ],
            "created_at": 2.0,
            "updated_at": 2.0,
        }
    ]
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)
    monkeypatch.setattr(frame, "_save_state", lambda *_args, **_kwargs: None)

    assert frame._show_history_chat("chat-c")
    assert frame._clear_context_and_start_new_chat() is True
    cleared_rows = list(frame.answer_list.GetStrings())
    assert "以开启新会话" in cleared_rows

    assert frame._show_history_chat("chat-a")
    active_rows = list(frame.answer_list.GetStrings())

    assert "以开启新会话" not in active_rows
    assert any("A 问题" in row for row in active_rows)


def test_clear_with_no_eligible_text_does_not_dispatch_or_sound(frame, monkeypatch):
    frame.active_chat_id = frame.current_chat_id = "owner"
    frame.active_session_turns = [
        {"question": "", "attachments": [{"path": "only.png"}]},
        {"voice_metadata": {"format": "wav"}},
        {"import_metadata": {"source": "archive"}},
    ]
    frame._current_chat_state = {"id": "owner", "turns": frame.active_session_turns}
    monkeypatch.setattr(frame, "_begin_durable_clear_operation", lambda *_args, **_kwargs: {
        "operation_id": "op", "chat_id": "owner", "revision": 3,
        "state": "completed_no_message", "snapshot": None,
    })
    monkeypatch.setattr(frame, "_dispatch_clear_operation_resend", lambda *_args, **_kwargs: pytest.fail("must not dispatch"))
    monkeypatch.setattr(frame, "_play_send_sound", lambda: pytest.fail("must stay silent"))
    monkeypatch.setattr(frame, "_render_answer_list", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: None)
    monkeypatch.setattr(frame, "_mark_openclaw_lifecycle_dirty", lambda: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *_args: None)
    monkeypatch.setattr(frame, "_push_remote_state", lambda *_args: None)

    assert frame._clear_context_and_start_new_chat(auto_resend_first=True) is True
    assert frame.active_session_turns == []


@pytest.mark.parametrize("model,starter_name", [
    (main.DEFAULT_CODEX_MODEL, "_start_codex_worker_for_turn"),
    (main.DEFAULT_KIMI_MODEL, "_start_kimi_worker_for_turn"),
])
def test_alt_a_real_store_claims_accepts_and_sounds_once(frame, tmp_path, monkeypatch, model, starter_name):
    store = main.ChatStore(tmp_path / f"alt-a-{model.replace('/', '-')}.db")
    store.initialize()
    frame.chat_store = store
    frame._chat_store_enabled = True
    frame.active_chat_id = frame.current_chat_id = "owner"
    frame.active_session_turns = [{"question": "first", "model": model}]
    frame._current_chat_state = {"id": "owner", "turns": frame.active_session_turns, "model": model}
    events = []
    monkeypatch.setattr(frame, starter_name, lambda *_args: events.append("accepted"))
    monkeypatch.setattr(frame, "_play_send_sound", lambda: events.append("sound"))
    monkeypatch.setattr(frame, "_render_answer_list", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_mark_openclaw_lifecycle_dirty", lambda: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *_args: None)
    monkeypatch.setattr(frame, "_push_remote_state", lambda *_args: None)

    assert frame._clear_context_and_start_new_chat(auto_resend_first=True, request_id="alt-a") is True
    operation = store.active_clear_operation("owner")
    assert operation["state"] == "resend_dispatched"
    assert events == ["accepted", "sound"]
    assert frame._clear_context_and_start_new_chat(auto_resend_first=True, request_id="alt-a") is True
    assert events == ["accepted", "sound"]


def test_submit_question_blocks_when_attachment_unreadable(frame, monkeypatch):
    frame._pending_input_attachments = [
        {
            "name": "missing.png",
            "path": r"D:\no\such\missing.png",
            "kind": "image",
            "direction": "outgoing",
            "status": "queued",
        }
    ]
    started = []

    class _NoStartThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            started.append(target)

        def start(self):
            raise AssertionError("worker must not start with a failed attachment")

    monkeypatch.setattr(main.threading, "Thread", _NoStartThread)

    ok, message = frame._submit_question("带附件的问题", source="local", model="openai/gpt-5.2")

    assert ok is False
    assert "无法读取" in message
    assert "missing.png" in message
    assert started == []
    # The draft and the attachment list stay for the user to repair.
    assert frame._pending_input_attachments


def test_submit_question_clear_resend_plays_accepted_sound_once(frame, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda force_replay=False: None)
    send_sounds = []
    monkeypatch.setattr(frame, "_play_send_sound", lambda: send_sounds.append(True))
    accepted = []
    monkeypatch.setattr(
        frame,
        "_play_clear_resend_accepted_sound",
        lambda op_id, chat_id, revision: accepted.append((op_id, chat_id, revision)) or True,
    )

    class _NoOpThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None, name=None):
            self._target = target
            self._args = args

        def start(self):
            return None

    monkeypatch.setattr(main.threading, "Thread", _NoOpThread)
    monkeypatch.setattr(frame, "_clear_resend_operation_id", "op-clear-1", raising=False)
    monkeypatch.setattr(frame, "_clear_resend_revision", 7, raising=False)

    ok, message = frame._submit_question("清除后的重发", source="local", model="openai/gpt-5.2")

    assert ok is True
    assert message == ""
    assert accepted and accepted[0][0] == "op-clear-1"
    assert send_sounds == []
    turn = frame.active_session_turns[-1]
    assert turn.get("clear_operation_id") == "op-clear-1"
