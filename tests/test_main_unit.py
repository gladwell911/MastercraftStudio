import json
import copy
import time
from pathlib import Path

import wx
import pytest

import main


REQUEST_METADATA_FIELDS = {
    "request_status",
    "request_model",
    "request_question",
    "request_started_at",
    "request_last_attempt_at",
    "request_attempt_count",
    "request_recoverable",
    "request_recovery_mode",
    "request_resume_token",
    "request_error",
    "request_recovered_after_restart",
}


def assert_request_metadata(turn, *, status, model, question, recoverable, mode, resume_token, attempt_count):
    assert REQUEST_METADATA_FIELDS.issubset(turn.keys())
    assert turn["request_status"] == status
    assert turn["request_model"] == model
    assert turn["request_question"] == question
    assert turn["request_recoverable"] is recoverable
    assert turn["request_recovery_mode"] == mode
    assert turn["request_resume_token"] == resume_token
    assert turn["request_attempt_count"] == attempt_count


def test_send_shortcut_mapping(frame):
    assert frame._is_send_shortcut(wx.WXK_RETURN, ctrl=False, alt=False)
    assert frame._is_send_shortcut(wx.WXK_NUMPAD_ENTER, ctrl=False, alt=False)
    assert not frame._is_send_shortcut(wx.WXK_RETURN, ctrl=True, alt=False)


def test_continue_shortcut_mapping(frame):
    assert frame._is_continue_shortcut(ord("C"), alt=True)
    assert frame._is_continue_shortcut(ord("c"), alt=True)
    assert not frame._is_continue_shortcut(ord("C"), alt=False)


def test_remote_ws_defaults_to_localhost_when_host_is_unset(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_TOKEN", "secret")
    monkeypatch.delenv("REMOTE_CONTROL_HOST", raising=False)
    monkeypatch.setenv("REMOTE_CONTROL_PORT", "18080")

    url = frame._build_remote_ws_url()

    assert url == "ws://127.0.0.1:18080/ws?token=secret"


def test_remote_ws_server_binds_to_localhost_by_default(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_TOKEN", "secret")
    monkeypatch.delenv("REMOTE_CONTROL_HOST", raising=False)
    monkeypatch.setenv("REMOTE_CONTROL_PORT", "0")
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))

    frame._start_remote_ws_server_if_configured()
    try:
        assert frame._remote_ws_server.host == "127.0.0.1"
        assert frame._remote_ws_server.bound_port > 0
    finally:
        frame._remote_ws_server.stop()
        frame._remote_ws_server = None


def test_char_hook_alt_c_submits_continue_from_any_focus(frame):
    seen = {}
    frame._submit_question = lambda question, **kwargs: seen.setdefault("call", (question, kwargs)) or (True, "")
    frame.history_list.SetFocus()

    class E:
        def GetKeyCode(self):
            return ord("C")

        def ControlDown(self):
            return False

        def AltDown(self):
            return True

        def Skip(self):
            raise AssertionError("should not skip")

    frame._on_char_hook(E())

    assert seen["call"][0] == "继续"
    assert seen["call"][1]["source"] == "local"


def test_char_hook_ctrl_left_switches_to_previous_chat_from_any_focus(frame):
    frame.active_chat_id = "chat-b"
    frame.archived_chats = [
        {"id": "chat-a", "title": "聊天A", "turns": [], "created_at": 1.0, "updated_at": 1.0},
        {"id": "chat-c", "title": "聊天C", "turns": [], "created_at": 3.0, "updated_at": 3.0},
    ]
    frame._refresh_history()
    seen = {}
    frame.answer_list.SetFocus()
    frame._switch_current_chat = lambda chat_id: seen.setdefault("chat_id", chat_id) or True

    class E:
        def GetKeyCode(self):
            return wx.WXK_LEFT

        def ControlDown(self):
            return True

        def AltDown(self):
            return False

        def Skip(self):
            raise AssertionError("should not skip")

    frame._on_char_hook(E())

    assert seen["chat_id"] == "chat-a"


def test_char_hook_ctrl_right_switches_to_next_chat_from_any_focus(frame):
    frame.active_chat_id = "chat-b"
    frame.archived_chats = [
        {"id": "chat-a", "title": "聊天A", "turns": [], "created_at": 1.0, "updated_at": 1.0},
        {"id": "chat-c", "title": "聊天C", "turns": [], "created_at": 3.0, "updated_at": 3.0},
    ]
    frame._refresh_history()
    seen = {}
    frame.input_edit.SetFocus()
    frame._switch_current_chat = lambda chat_id: seen.setdefault("chat_id", chat_id) or True

    class E:
        def GetKeyCode(self):
            return wx.WXK_RIGHT

        def ControlDown(self):
            return True

        def AltDown(self):
            return False

        def Skip(self):
            raise AssertionError("should not skip")

    frame._on_char_hook(E())

    assert seen["chat_id"] == "chat-c"


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

    assert frame._adjacent_history_chat_id(-1) == "chat-a"
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


def test_char_hook_ctrl_history_navigation_noops_without_archived_chats(frame):
    frame.active_chat_id = "chat-only"
    frame.archived_chats = []
    seen = {"switched": False, "skipped": False}
    frame._switch_current_chat = lambda _chat_id: seen.__setitem__("switched", True) or True

    class E:
        def GetKeyCode(self):
            return wx.WXK_LEFT

        def ControlDown(self):
            return True

        def AltDown(self):
            return False

        def Skip(self):
            seen["skipped"] = True

    frame._on_char_hook(E())

    assert seen["switched"] is False
    assert seen["skipped"] is True


def test_input_key_down_ctrl_left_switches_to_previous_chat(frame):
    frame.active_chat_id = "chat-b"
    frame.archived_chats = [
        {"id": "chat-a", "title": "聊天A", "turns": [], "created_at": 1.0, "updated_at": 1.0},
        {"id": "chat-c", "title": "聊天C", "turns": [], "created_at": 3.0, "updated_at": 3.0},
    ]
    frame._refresh_history()
    seen = {"skipped": 0}
    frame._switch_current_chat = lambda chat_id: seen.setdefault("chat_id", chat_id) or True

    class E:
        def GetKeyCode(self):
            return wx.WXK_LEFT

        def ControlDown(self):
            return True

        def AltDown(self):
            return False

        def Skip(self):
            seen["skipped"] += 1

    frame._on_input_key_down(E())

    assert seen["chat_id"] == "chat-a"
    assert seen["skipped"] == 1


def test_input_key_down_ctrl_right_switches_to_next_chat(frame):
    frame.active_chat_id = "chat-b"
    frame.archived_chats = [
        {"id": "chat-a", "title": "聊天A", "turns": [], "created_at": 1.0, "updated_at": 1.0},
        {"id": "chat-c", "title": "聊天C", "turns": [], "created_at": 3.0, "updated_at": 3.0},
    ]
    frame._refresh_history()
    seen = {"skipped": 0}
    frame._switch_current_chat = lambda chat_id: seen.setdefault("chat_id", chat_id) or True

    class E:
        def GetKeyCode(self):
            return wx.WXK_RIGHT

        def ControlDown(self):
            return True

        def AltDown(self):
            return False

        def Skip(self):
            seen["skipped"] += 1

    frame._on_input_key_down(E())

    assert seen["chat_id"] == "chat-c"
    assert seen["skipped"] == 1


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


def test_render_answer_list_requests_listbox_repaint(frame, monkeypatch):
    frame.active_session_turns = [
        {"question": "问题", "answer_md": "回答", "model": "openai/gpt-5.2", "created_at": 1.0}
    ]
    seen = {"calls": []}
    monkeypatch.setattr(frame, "_request_listbox_repaint", lambda *controls: seen["calls"].append(controls), raising=False)

    frame._render_answer_list()

    assert seen["calls"] == [(frame.answer_list,)]


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


def test_input_enter_during_ime_composition_does_not_send(frame):
    sent = {"n": 0}
    frame._trigger_send = lambda: sent.__setitem__("n", sent["n"] + 1)
    frame._has_input_ime_candidates = lambda: True

    class E:
        def GetKeyCode(self):
            return wx.WXK_RETURN

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            pass

    frame._on_input_key_down(E())
    assert sent["n"] == 0


def test_char_hook_enter_during_ime_composition_does_not_send(frame):
    sent = {"n": 0}
    frame._trigger_send = lambda: sent.__setitem__("n", sent["n"] + 1)
    frame.input_edit.SetFocus()

    class E:
        def GetKeyCode(self):
            return wx.WXK_RETURN

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            pass

    frame._on_char_hook(E())
    assert sent["n"] == 0


def test_char_hook_enter_without_candidates_still_does_not_send_when_input_has_focus(frame):
    sent = {"n": 0}
    frame._trigger_send = lambda: sent.__setitem__("n", sent["n"] + 1)
    frame._has_input_ime_candidates = lambda: False
    frame.input_edit.SetFocus()

    class E:
        def GetKeyCode(self):
            return wx.WXK_RETURN

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            pass

    frame._on_char_hook(E())
    assert sent["n"] == 0


def test_input_enter_without_ime_candidates_sends(frame):
    sent = {"n": 0}
    frame._trigger_send = lambda: sent.__setitem__("n", sent["n"] + 1)
    frame._has_input_ime_candidates = lambda: False

    class E:
        def GetKeyCode(self):
            return wx.WXK_RETURN

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            pass

    frame._on_input_key_down(E())
    assert sent["n"] == 1


def test_send_click_empty_input_shows_message(frame, monkeypatch):
    seen = {}
    monkeypatch.setattr(main.wx, "MessageBox", lambda message, title, flags: seen.update({"message": message, "title": title}))
    frame.input_edit.SetValue("   ")
    frame._on_send_clicked(None)
    assert seen == {"message": "请输入问题，输入框内容为空", "title": "提示"}


def test_send_click_still_sends_while_running(frame, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    frame._stop_openclaw_sync()
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda force_replay=False: None)
    monkeypatch.setattr(main.threading, "Thread", lambda target=None, args=(), kwargs=None, daemon=None: type("T", (), {"start": lambda self: target(*args, **(kwargs or {}))})())
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)
    monkeypatch.setattr(frame, "_focus_latest_answer", lambda: None)
    monkeypatch.setattr(main.wx, "CallLater", lambda _delay, fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(main.ChatClient, "stream_chat", lambda self, question, on_delta, history_turns=None: "第二条")
    frame.active_session_turns = [
        {"question": "第一条", "answer_md": "", "model": "openai/gpt-5.2", "created_at": time.time()}
    ]
    frame.is_running = True
    frame._active_request_count = 1
    frame.model_combo.SetValue("openai/gpt-5.2")
    frame.input_edit.SetValue("第二条")

    frame._on_send_clicked(None)

    assert len(frame.active_session_turns) == 2
    assert frame.active_session_turns[-1]["question"] == "第二条"
    assert frame.active_session_turns[-1]["answer_md"] == "第二条"


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


def test_codex_submit_sets_resume_recovery_mode(frame, monkeypatch):
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda force_replay=False: None)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_render_answer_list", lambda: None)
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    class _NoOpThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}

        def start(self):
            return None

    monkeypatch.setattr(main.threading, "Thread", _NoOpThread)
    monkeypatch.setattr(main.time, "time", lambda: 2345.5)

    frame.active_codex_thread_id = "11111111-1111-1111-1111-111111111111"
    frame.active_codex_turn_id = "22222222-2222-2222-2222-222222222222"
    frame.active_codex_turn_active = False

    ok, message = frame._submit_question("Codex 续问", source="local", model=main.DEFAULT_CODEX_MODEL)

    assert ok is True
    assert message == ""
    turn = frame.active_session_turns[-1]
    assert_request_metadata(
        turn,
        status="pending",
        model=main.DEFAULT_CODEX_MODEL,
        question="Codex 续问",
        recoverable=True,
        mode="resume",
        resume_token={
            "thread_id": "11111111-1111-1111-1111-111111111111",
            "turn_id": "22222222-2222-2222-2222-222222222222",
        },
        attempt_count=1,
    )
    assert turn["request_resume_token"] == {
        "thread_id": "11111111-1111-1111-1111-111111111111",
        "turn_id": "22222222-2222-2222-2222-222222222222",
    }


def test_claudecode_submit_sets_resume_recovery_mode(frame, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda force_replay=False: None)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_render_answer_list", lambda: None)
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


def test_append_codex_local_turn_adds_recovery_metadata(frame, monkeypatch):
    monkeypatch.setattr(main.time, "time", lambda: 4567.5)
    frame.active_codex_thread_id = "thread-1"
    frame.active_codex_turn_id = "turn-1"

    idx = frame._append_codex_local_turn("Codex 继续")

    assert idx == len(frame.active_session_turns) - 1
    turn = frame.active_session_turns[-1]
    assert_request_metadata(
        turn,
        status="pending",
        model=main.DEFAULT_CODEX_MODEL,
        question="Codex 继续",
        recoverable=True,
        mode="resume",
        resume_token={"thread_id": "thread-1", "turn_id": "turn-1"},
        attempt_count=1,
    )
    assert turn["request_started_at"] == 4567.5
    assert turn["request_last_attempt_at"] == 4567.5


def test_apply_openclaw_sync_event_user_turn_adds_recovery_metadata(frame, monkeypatch):
    monkeypatch.setattr(main.time, "time", lambda: 5678.5)
    frame.active_session_turns = []

    event = main.OpenClawSyncEvent(event_id="evt-1", role="user", text="同步问题", timestamp=5678.5)

    result = frame._apply_openclaw_sync_event(event)

    assert result == "visible"
    turn = frame.active_session_turns[-1]
    assert_request_metadata(
        turn,
        status="done",
        model="openclaw/main",
        question="同步问题",
        recoverable=False,
        mode="retry",
        resume_token={},
        attempt_count=0,
    )


def test_apply_openclaw_sync_event_assistant_turn_adds_recovery_metadata(frame, monkeypatch):
    monkeypatch.setattr(main.time, "time", lambda: 5678.5)
    frame.active_session_turns = []

    event = main.OpenClawSyncEvent(event_id="evt-2", role="assistant", text="同步回答", timestamp=5678.5)

    result = frame._apply_openclaw_sync_event(event)

    assert result == "visible"
    turn = frame.active_session_turns[-1]
    assert_request_metadata(
        turn,
        status="done",
        model="openclaw/main",
        question="",
        recoverable=False,
        mode="retry",
        resume_token={},
        attempt_count=0,
    )
    assert turn["answer_md"] == "同步回答"


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


def test_merge_codex_final_answer_appends_turn_with_recovery_metadata(frame, monkeypatch):
    monkeypatch.setattr(main.time, "time", lambda: 7890.5)
    frame.active_codex_thread_id = "thread-2"
    frame.active_codex_turn_id = "turn-2"
    frame.active_session_turns = []

    changed = frame._merge_codex_final_answer("最终答案")

    assert changed is True
    turn = frame.active_session_turns[-1]
    assert_request_metadata(
        turn,
        status="done",
        model=main.DEFAULT_CODEX_MODEL,
        question="",
        recoverable=False,
        mode="resume",
        resume_token={"thread_id": "thread-2", "turn_id": "turn-2"},
        attempt_count=0,
    )
    assert turn["answer_md"] == "最终答案"


def test_codex_user_input_dialog_returns_option_value_when_present(frame):
    dlg = main.CodexUserInputDialog(
        frame,
        [
            {
                "id": "choice",
                "header": "选择",
                "question": "请选择一个选项",
                "options": [
                    {"label": "Alpha", "value": "alpha-code"},
                    {"label": "Beta"},
                ],
            }
        ],
    )
    try:
        radio = dlg._controls[0]["radio"]
        radio.SetSelection(0)
        assert dlg.get_answers() == {"choice": ["alpha-code"]}

        radio.SetSelection(1)
        assert dlg.get_answers() == {"choice": ["Beta"]}
    finally:
        dlg.Destroy()


def test_handle_codex_request_dialog_does_not_submit_when_user_cancels(frame, monkeypatch):
    calls = []

    class _FakeClient:
        def respond_tool_request_user_input(self, request_id, answers):
            calls.append((request_id, answers))

    class _FakeDialog:
        def __init__(self, parent, questions):
            self.parent = parent
            self.questions = questions

        def ShowModal(self):
            return wx.ID_CANCEL

        def Destroy(self):
            calls.append("destroy")

        def get_answers(self):
            raise AssertionError("should not be called on cancel")

    monkeypatch.setattr(frame, "_ensure_codex_client", lambda: _FakeClient())
    monkeypatch.setattr(main, "CodexUserInputDialog", _FakeDialog)

    frame._handle_codex_request_dialog(
        {
            "method": "item/tool/requestUserInput",
            "request_id": "req-1",
            "params": {"questions": []},
        }
    )

    assert calls == ["destroy"]


def test_codex_client_event_bridge_ignores_events_after_wx_app_gone(frame, monkeypatch):
    seen = {"called": False}

    monkeypatch.setattr(main.wx, "GetApp", lambda: None)

    def _call_after(*_args, **_kwargs):
        raise AssertionError("CallAfter should not run without wx.App")

    monkeypatch.setattr(main.wx, "CallAfter", _call_after)
    monkeypatch.setattr(frame, "_on_codex_event_for_chat", lambda *_args, **_kwargs: seen.__setitem__("called", True))

    client = frame._get_or_create_codex_client("chat-a")

    client.on_event(main.CodexEvent(type="stderr", text="late event"))

    assert seen["called"] is False


@pytest.mark.parametrize("event_type", ["agent_message_delta", "stderr", "plan_updated", "diff_updated"])
def test_background_codex_feedback_events_do_not_queue_ui_work(frame, monkeypatch, event_type):
    frame.active_chat_id = "chat-current"
    frame.active_codex_thread_id = "thread-current"
    frame.active_codex_turn_id = "turn-current"
    frame.archived_chats = [
        {
            "id": "chat-background",
            "title": "后台聊天",
            "title_manual": False,
            "pinned": False,
            "model": main.DEFAULT_CODEX_MODEL,
            "turns": [{"question": "后台任务", "answer_md": "", "model": main.DEFAULT_CODEX_MODEL, "created_at": 1.0}],
            "created_at": 1.0,
            "updated_at": 1.0,
            "codex_thread_id": "thread-background",
            "codex_turn_id": "turn-background",
            "codex_turn_active": True,
            "codex_pending_prompt": "",
            "codex_pending_request": None,
            "codex_request_queue": [],
            "codex_thread_flags": [],
            "codex_latest_assistant_text": "",
            "codex_latest_assistant_phase": "",
        }
    ]

    call_after_calls = []
    monkeypatch.setattr(main.wx, "GetApp", lambda: object())
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *args, **kwargs: call_after_calls.append((fn, args, kwargs)))

    frame._dispatch_codex_event_to_ui(
        "chat-background",
        main.CodexEvent(
            type=event_type,
            thread_id="thread-background",
            turn_id="turn-background",
            text="stream chunk",
        ),
    )

    assert call_after_calls == []


def test_current_chat_codex_delta_still_queues_ui_work(frame, monkeypatch):
    call_after_calls = []
    monkeypatch.setattr(main.wx, "GetApp", lambda: object())
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *args, **kwargs: call_after_calls.append((fn, args, kwargs)))

    frame._dispatch_codex_event_to_ui(
        frame.current_chat_id,
        main.CodexEvent(type="agent_message_delta", thread_id=frame.active_codex_thread_id, turn_id=frame.active_codex_turn_id, text="delta"),
    )

    assert len(call_after_calls) == 1


def test_detail_page_split_question_and_answer(frame):
    frame.active_session_turns = [
        {
            "question": "测试问题",
            "answer_md": "测试回答",
            "model": main.DEFAULT_MODEL_ID,
            "created_at": time.time(),
        }
    ]
    frame.view_mode = "active"
    frame._render_answer_list()

    opened = {}
    frame._open_local_webpage = lambda p: opened.__setitem__("path", str(p))

    q_row = next(i for i, m in enumerate(frame.answer_meta) if m[0] == "question")
    frame.answer_list.SetSelection(q_row)
    assert frame._try_open_selected_answer_detail()
    q_html = Path(opened["path"]).read_text(encoding="utf-8")
    assert "问题详情" in q_html
    assert "回答详情" not in q_html

    a_row = next(i for i, m in enumerate(frame.answer_meta) if m[0] == "answer")
    frame.answer_list.SetSelection(a_row)
    assert frame._try_open_selected_answer_detail()
    a_html = Path(opened["path"]).read_text(encoding="utf-8")
    assert "回答详情" in a_html
    assert "问题详情" not in a_html


def test_openclaw_assistant_only_turn_hides_empty_user_rows(frame):
    frame._stop_openclaw_sync()
    frame.active_session_turns = [
        {
            "question": "",
            "answer_md": "外部同步的回复",
            "model": "openclaw/main",
            "created_at": time.time(),
        }
    ]
    frame.view_mode = "active"
    frame._render_answer_list()

    rows = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    meta_types = [meta[0] for meta in frame.answer_meta]

    assert rows == ["小诸葛", "外部同步的回复"]
    assert meta_types == ["ai", "answer"]


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


def test_global_ctrl_callback_forwarding(frame):
    seen = []
    frame._voice_input.on_ctrl_keyup = lambda combo_used=False, side="left": seen.append((combo_used, side))
    frame._on_global_ctrl_keyup(True, "right")
    frame._on_global_ctrl_keyup(False, "left")
    assert seen == [(True, "right"), (False, "left")]


def test_global_ctrl_combo_key_filter_ignores_ime_and_modifiers():
    hook = main.GlobalCtrlTapHook(lambda *_a, **_k: None)
    assert not hook._should_mark_combo_key(main.VK_PROCESSKEY)
    assert not hook._should_mark_combo_key(main.VK_PACKET)
    assert not hook._should_mark_combo_key(main.VK_SHIFT)
    assert not hook._should_mark_combo_key(main.VK_MENU)
    assert not hook._should_mark_combo_key(main.VK_LWIN)
    assert hook._should_mark_combo_key(ord("C"))


def test_global_ctrl_emit_deduplicates_same_side_within_window(monkeypatch):
    seen = []
    hook = main.GlobalCtrlTapHook(lambda combo_used, side: seen.append((combo_used, side)))
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))

    hook._emit_ctrl_keyup(False, "left")
    hook._emit_ctrl_keyup(True, "left")
    assert seen == [(False, "left")]


def test_global_ctrl_poller_release_enabled_when_hook_missing():
    hook = main.GlobalCtrlTapHook(lambda *_a, **_k: None)
    hook._running = True
    hook._hook = None
    assert hook._should_use_poller_release()


def test_global_ctrl_poller_release_disabled_when_hook_recent(monkeypatch):
    hook = main.GlobalCtrlTapHook(lambda *_a, **_k: None)
    hook._running = True
    hook._hook = object()
    hook._using_fallback = False
    hook._last_hook_event_at = 100.0
    hook._hook_stale_seconds = 0.45
    monkeypatch.setattr(main.time, "monotonic", lambda: 100.2)
    assert not hook._should_use_poller_release()


def test_global_ctrl_poller_release_enabled_when_hook_stale(monkeypatch):
    hook = main.GlobalCtrlTapHook(lambda *_a, **_k: None)
    hook._running = True
    hook._hook = object()
    hook._using_fallback = False
    hook._last_hook_event_at = 100.0
    hook._hook_stale_seconds = 0.45
    monkeypatch.setattr(main.time, "monotonic", lambda: 101.0)
    assert hook._should_use_poller_release()


def test_voice_result_prefers_global_paste(frame, monkeypatch):
    injected = {"ok": False}
    typed = {"n": 0}

    frame._inject_text_to_foreground_window = lambda text: injected.__setitem__("ok", text == "语音文本") or True
    frame._type_text_to_system_focus = lambda text: typed.__setitem__("n", typed["n"] + 1) or True
    frame._speak_text_via_screen_reader = lambda _text: None
    frame._play_voice_end_sound = lambda: (_ for _ in ()).throw(AssertionError("should not play here"))
    frame._on_voice_stop_recording = lambda: None
    monkeypatch.setattr(main.wx.Window, "FindFocus", lambda: None)
    monkeypatch.setattr(frame, "IsActive", lambda: False)

    frame._on_voice_result("语音文本！！！", mode=main.MODE_DIRECT)
    assert injected["ok"]
    assert typed["n"] == 0


def test_voice_result_fallbacks_to_sendinput(frame, monkeypatch):
    injected = {"n": 0}
    typed = {"n": 0}
    frame._inject_text_to_foreground_window = lambda text: injected.__setitem__("n", injected["n"] + 1) or False
    frame._type_text_to_system_focus = lambda text: typed.__setitem__("n", typed["n"] + 1) or True
    frame._speak_text_via_screen_reader = lambda _text: None
    frame._play_voice_end_sound = lambda: (_ for _ in ()).throw(AssertionError("should not play here"))
    monkeypatch.setattr(main.wx.Window, "FindFocus", lambda: None)
    monkeypatch.setattr(frame, "IsActive", lambda: False)
    frame._on_voice_result("语音文本", mode=main.MODE_DIRECT)
    assert injected["n"] == 1
    assert typed["n"] == 1


def test_voice_result_writes_then_speaks_after_200ms(frame, monkeypatch):
    order = []
    frame._insert_text_to_system_focus = lambda text: order.append(("insert", text)) or True
    frame._speak_text_via_screen_reader = lambda text: order.append(("speak", text))
    monkeypatch.setattr(
        main.wx,
        "CallLater",
        lambda ms, fn, *args: order.append(("delay", ms)) or fn(*args),
    )

    frame._on_voice_result("语音文本！！！", mode=main.MODE_DIRECT)
    assert order == [("insert", "语音文本！！！"), ("delay", 200), ("speak", "语音文本！！！")]


def test_voice_result_prefers_local_editor_without_system_injection(frame, monkeypatch):
    injected = {"n": 0}
    typed = {"n": 0}
    frame.input_edit.SetValue("")
    frame._inject_text_to_foreground_window = lambda text: injected.__setitem__("n", injected["n"] + 1) or True
    frame._type_text_to_system_focus = lambda text: typed.__setitem__("n", typed["n"] + 1) or True
    frame._speak_text_via_screen_reader = lambda _text: None
    monkeypatch.setattr(main.wx.Window, "FindFocus", lambda: frame.input_edit)

    frame._on_voice_result("今天天气不错", mode=main.MODE_DIRECT)

    assert frame.input_edit.GetValue() == "今天天气不错"
    assert injected["n"] == 0
    assert typed["n"] == 0


def test_voice_result_keeps_repeated_transcript_verbatim(frame, monkeypatch):
    frame.input_edit.SetValue("")
    frame._speak_text_via_screen_reader = lambda _text: None
    monkeypatch.setattr(main.wx.Window, "FindFocus", lambda: frame.input_edit)

    frame._on_voice_result("今天天气不错今天天气不错", mode=main.MODE_DIRECT)

    assert frame.input_edit.GetValue() == "今天天气不错今天天气不错"


def test_voice_result_repeated_callback_appends_again(frame, monkeypatch):
    frame.input_edit.SetValue("")
    frame._speak_text_via_screen_reader = lambda _text: None
    monkeypatch.setattr(main.wx.Window, "FindFocus", lambda: frame.input_edit)

    frame._on_voice_result("今天天气不错", mode=main.MODE_DIRECT)
    frame._on_voice_result("今天天气不错", mode=main.MODE_DIRECT)

    assert frame.input_edit.GetValue() == "今天天气不错今天天气不错"


def test_voice_result_appends_when_editor_already_contains_same_text(frame, monkeypatch):
    frame.input_edit.SetValue("今天天气不错")
    frame._speak_text_via_screen_reader = lambda _text: None
    monkeypatch.setattr(main.wx.Window, "FindFocus", lambda: frame.input_edit)
    monkeypatch.setattr(frame, "IsActive", lambda: True)

    frame._on_voice_result("今天天气不错", mode=main.MODE_DIRECT)

    assert frame.input_edit.GetValue() == "今天天气不错今天天气不错"


def test_voice_result_prefers_local_input_when_app_active_and_focus_temporarily_missing(frame, monkeypatch):
    injected = {"n": 0}
    typed = {"n": 0}
    frame.input_edit.SetValue("")
    frame._speak_text_via_screen_reader = lambda _text: None
    frame._inject_text_to_foreground_window = lambda text: injected.__setitem__("n", injected["n"] + 1) or True
    frame._type_text_to_system_focus = lambda text: typed.__setitem__("n", typed["n"] + 1) or True
    monkeypatch.setattr(main.wx.Window, "FindFocus", lambda: None)
    monkeypatch.setattr(frame, "IsActive", lambda: True)

    frame._on_voice_result("今天天气不错", mode=main.MODE_DIRECT)

    assert frame.input_edit.GetValue() == "今天天气不错"
    assert injected["n"] == 0
    assert typed["n"] == 0


def test_voice_stop_recording_plays_end_sound(frame):
    called = {"end": 0}
    frame._play_voice_end_sound = lambda: called.__setitem__("end", called["end"] + 1)
    frame._on_voice_stop_recording()
    assert called["end"] == 1


def test_voice_error_no_modal_beep_only(frame, monkeypatch):
    called = {"wrong_sound": 0, "msgbox": 0}
    frame._play_voice_wrong_sound = lambda: called.__setitem__("wrong_sound", called["wrong_sound"] + 1)
    monkeypatch.setattr(main.wx, "MessageBox", lambda *_a, **_k: called.__setitem__("msgbox", called["msgbox"] + 1))
    frame._on_voice_error("err")
    assert called["wrong_sound"] == 1
    assert called["msgbox"] == 0


def test_voice_state_recording_plays_begin_sound(frame):
    called = {"begin": 0}
    frame._play_voice_begin_sound = lambda: called.__setitem__("begin", called["begin"] + 1)
    frame._voice_input.state = "recording"
    frame._on_voice_state("开始录音")
    assert called["begin"] == 1


def test_voice_state_non_recording_does_not_play_begin_sound(frame):
    called = {"begin": 0}
    frame._play_voice_begin_sound = lambda: called.__setitem__("begin", called["begin"] + 1)
    frame._voice_input.state = "transcribing"
    frame._on_voice_state("正在识别")
    assert called["begin"] == 0


def test_apply_realtime_call_settings_updates_controller_and_state(frame, tmp_path):
    seen = {}
    frame.state_path = tmp_path / "app_state.json"
    prepared = {"n": 0}
    orig_call_after = main.wx.CallAfter

    def fake_update(settings):
        seen["payload"] = (settings.role, settings.speech_rate)
        return "设置已保存"

    frame._realtime_call.update_settings = fake_update
    frame._realtime_call.prepare = lambda: prepared.__setitem__("n", prepared["n"] + 1)
    main.wx.CallAfter = lambda fn, *args, **kwargs: fn(*args, **kwargs)

    try:
        frame._apply_realtime_call_settings(main.RealtimeCallSettings(role="新的角色", speech_rate=25))
    finally:
        main.wx.CallAfter = orig_call_after

    assert frame.realtime_call_role == "新的角色"
    assert frame.realtime_call_speech_rate == 25
    assert seen["payload"] == ("新的角色", 25)
    assert prepared["n"] == 1
    data = json.loads(frame.state_path.read_text(encoding="utf-8"))
    assert data["realtime_call_role"] == "新的角色"
    assert data["realtime_call_speech_rate"] == 25


def test_load_state_restores_realtime_call_settings(frame, tmp_path):
    frame.state_path = tmp_path / "app_state.json"
    frame.state_path.write_text(
        json.dumps(
            {
                "selected_model_id": main.DEFAULT_MODEL_ID,
                "realtime_call_role": "测试角色",
                "realtime_call_speech_rate": 18,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    frame.realtime_call_role = main.DEFAULT_REALTIME_CALL_ROLE
    frame.realtime_call_speech_rate = main.DEFAULT_REALTIME_CALL_SPEECH_RATE
    frame._load_state()

    assert frame.realtime_call_role == "测试角色"
    assert frame.realtime_call_speech_rate == 18


def test_realtime_call_hotkey_toggles_call_and_plays_sound(frame):
    calls = []
    frame._realtime_call.toggle = lambda: calls.append("toggle") or "start"
    frame._play_voice_begin_sound = lambda: calls.append("begin")
    frame._play_voice_end_sound = lambda: calls.append("end")

    class E:
        def GetId(self):
            return main.HOTKEY_ID_REALTIME_CALL

    frame._on_global_hotkey(E())
    assert calls == ["toggle", "begin"]


def test_bind_events_registers_both_hotkey_ids():
    frame_bind_calls = []

    class _Ctrl:
        def Bind(self, *_args, **_kwargs):
            return None

    class _DummyFrame:
        def __init__(self):
            self.send_button = _Ctrl()
            self.new_chat_button = _Ctrl()
            self.tools_menu_button = _Ctrl()
            self.model_combo = _Ctrl()
            self.input_edit = _Ctrl()
            self.answer_list = _Ctrl()
            self.history_list = _Ctrl()

        def Bind(self, _event, _handler, id=None):
            frame_bind_calls.append(id)

        def _on_send_clicked(self, *_args):
            return None

        def _on_new_chat_clicked(self, *_args):
            return None

        def _on_tools_menu_clicked(self, *_args):
            return None

        def _on_open_realtime_call_settings(self, *_args):
            return None

        def _on_model_changed(self, *_args):
            return None

        def _on_input_key_down(self, *_args):
            return None

        def _on_input_key_up(self, *_args):
            return None

        def _on_char_hook(self, *_args):
            return None

        def _on_show_sync_tray_state(self, *_args):
            return None

        def _on_close(self, *_args):
            return None

        def _on_global_hotkey(self, *_args):
            return None

        def _on_any_key_down_escape_minimize(self, *_args):
            return None

        def _on_answer_key_down(self, *_args):
            return None

        def _on_answer_char(self, *_args):
            return None

        def _on_answer_activate(self, *_args):
            return None

        def _activate_selected_history(self, *_args):
            return None

        def _on_history_key_down(self, *_args):
            return None

        def _on_history_char(self, *_args):
            return None

        def _on_history_selected(self, *_args):
            return None

        def _on_history_context(self, *_args):
            return None

    dummy = _DummyFrame()
    main.ChatFrame._bind_events(dummy)

    assert main.HOTKEY_ID_SHOW in frame_bind_calls
    assert main.HOTKEY_ID_REALTIME_CALL in frame_bind_calls
    assert main.HOTKEY_ID_REALTIME_CALL_ALT in frame_bind_calls
    assert main.HOTKEY_ID_REALTIME_CALL_ALT2 in frame_bind_calls


def test_register_global_hotkey_registers_backslash_variants(monkeypatch):
    calls = []

    class _DummyUser32:
        @staticmethod
        def VkKeyScanW(_char_code):
            return 0xE2

    class _DummyFrame:
        _show_hotkey_registered = False
        _realtime_call_hotkey_registered_ids = set()

        def RegisterHotKey(self, hotkey_id, modifiers, vk_code):
            calls.append((hotkey_id, modifiers, vk_code))
            return True

        def _resolve_backslash_hotkey_vk(self):
            return main.ChatFrame._resolve_backslash_hotkey_vk(self)

    monkeypatch.setattr(main.ctypes, "windll", type("W", (), {"user32": _DummyUser32()})())

    dummy = _DummyFrame()
    main.ChatFrame._register_global_hotkey(dummy)

    registered_ids = {hotkey_id for hotkey_id, _mods, _vk in calls}
    registered_vks = {vk_code for _hotkey_id, _mods, vk_code in calls}
    assert main.HOTKEY_ID_SHOW in registered_ids
    assert main.HOTKEY_ID_REALTIME_CALL in registered_ids
    assert main.HOTKEY_ID_REALTIME_CALL_ALT in registered_ids
    assert registered_vks == {wx.WXK_F12, main.VK_OEM_5, main.VK_OEM_102}


def test_remove_trailing_punctuation():
    assert main.remove_trailing_punctuation("你好！！！") == "你好"
    assert main.remove_trailing_punctuation("hello...") == "hello"
    assert main.remove_trailing_punctuation("。！？") == ""
    assert main.remove_trailing_punctuation("正常文本") == "正常文本"


def test_answer_list_typing_redirects_after_committed_chars(frame):
    frame.input_edit.SetValue("")
    frame._queue_answer_char_redirect("好")
    frame._queue_answer_char_redirect("的")
    frame._flush_answer_committed_buffer_to_input()
    assert frame.input_edit.GetValue().endswith("好的")


def test_answer_list_non_printable_does_not_redirect(frame):
    class E:
        def GetKeyCode(self):
            return wx.WXK_UP

        def GetUnicodeKey(self):
            return wx.WXK_UP

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            pass

    assert frame._extract_committed_char(E()) == ""


def test_answer_list_ascii_process_keys_do_not_redirect(frame):
    class E:
        def GetKeyCode(self):
            return ord("h")

        def GetUnicodeKey(self):
            return ord("h")

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            pass

    assert frame._extract_committed_char(E()) == ""


def test_archive_title_prefers_topic_plus_action(frame):
    frame._current_chat_state["title"] = "新聊天"
    frame._current_chat_state["title_manual"] = False
    frame.active_session_turns = [
        {"question": "安卓自动化测试方案怎么做", "answer_md": "先按层次划分", "model": "openai/gpt-5.2", "created_at": time.time()},
        {"question": "再帮我整理成执行清单", "answer_md": "可以整理成清单", "model": "openai/gpt-5.2", "created_at": time.time()},
    ]
    frame.active_session_started_at = time.time()
    archived = frame._archive_active_session(quick_title=True)
    assert archived is not None
    assert "安卓自动化测试方案怎么做"[:8] in archived["title"]
    assert "整理成执行清单"[:6] in archived["title"]


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
    assert "安卓自动化测试方案怎么做"[:8] in archived["title"]
    assert "整理成执行清单"[:6] in archived["title"]


def test_title_source_turns_prefers_front_topic_and_recent_action(frame):
    turns = [
        {"question": "q1", "answer_md": "a1"},
        {"question": "q2", "answer_md": "a2"},
        {"question": "q3", "answer_md": "a3"},
        {"question": "q4", "answer_md": "a4"},
    ]
    selected = frame._title_source_turns(turns)
    assert len(selected) == 4
    assert selected[0]["question"] == "q1"
    assert selected[-1]["question"] == "q4"


def test_title_source_turns_uses_all_when_short(frame):
    turns = [
        {"question": "q1", "answer_md": "a1"},
        {"question": "q2", "answer_md": "a2"},
    ]
    selected = frame._title_source_turns(turns)
    assert len(selected) == 2
    assert selected[0]["question"] == "q1"
    assert selected[1]["question"] == "q2"


def test_summarize_last_turn_locally_uses_topic_and_action(frame):
    turns = [
        {"question": "多聊天 CLI 改造怎么设计", "answer_md": "先拆会话状态", "model": "codex/main", "created_at": time.time()},
        {"question": "再补手机端路由协议", "answer_md": "需要显式 chat_id", "model": "codex/main", "created_at": time.time()},
    ]
    title = frame._summarize_last_turn_locally(turns)
    assert "多聊天 CLI 改造"[:8] in title
    assert "手机端路由协议"[:6] in title


def test_resolve_current_model_uses_combo_selection(frame):
    frame.selected_model = "openai/gpt-5.2"
    frame.model_combo.SetValue("deepseek/deepseek-r1-0528")
    assert frame._resolve_current_model() == "deepseek/deepseek-r1-0528"


def test_new_chat_archives_with_async_rename(frame):
    frame.active_session_turns = [
        {"question": "旧问题", "answer_md": "旧回答", "model": "openai/gpt-5.2", "created_at": time.time()}
    ]
    seen = {"quick": None, "async": None}

    def fake_archive(quick_title=False, schedule_async_rename=False):
        seen["quick"] = quick_title
        seen["async"] = schedule_async_rename
        return None

    frame._archive_active_session = fake_archive
    frame._on_new_chat_clicked(None)
    assert seen["quick"] is True
    assert seen["async"] is True


def test_new_chat_broadcasts_remote_history_changed(frame, monkeypatch):
    frame.active_session_turns = []
    frame.active_chat_id = "chat-old"
    frame.current_chat_id = "chat-old"
    frame._current_chat_state["id"] = "chat-old"
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_render_answer_list", lambda: None)
    monkeypatch.setattr(frame, "_refresh_history", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame.input_edit, "SetFocus", lambda: None)
    monkeypatch.setattr(frame, "SetStatusText", lambda *_args, **_kwargs: None)
    seen = []

    class _Server:
        def broadcast_event(self, payload):
            seen.append(payload)

    frame._remote_ws_server = _Server()

    frame._on_new_chat_clicked(None)

    assert any(payload.get("type") == "history_changed" for payload in seen)
    status, body = frame._remote_api_history_list_ui()
    assert status == 200
    assert any(chat.get("chat_id") == frame.active_chat_id for chat in body["chats"])


def test_busy_state_keeps_new_chat_enabled_while_request_running(frame):
    frame._active_request_count = 1

    frame._update_busy_state()

    assert frame.is_running is True
    assert frame.new_chat_button.IsEnabled()


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
    assert statuses[-1] == "答复完成"


def test_claudecode_done_does_not_rerender_ui_while_cli_runs(frame, monkeypatch):
    frame.active_chat_id = "chat-new"
    frame.active_session_turns = [
        {"question": "旧问题", "answer_md": "", "model": "claudecode/default", "created_at": 1.0}
    ]
    frame.active_turn_idx = 0
    frame._active_request_count = 1
    rendered = {"count": 0}
    focused = {"count": 0}
    monkeypatch.setattr(frame, "_render_answer_list", lambda: rendered.__setitem__("count", rendered["count"] + 1))
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_set_input_hint_idle", lambda: None)
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)
    monkeypatch.setattr(main.wx, "CallLater", lambda _delay, fn, *args, **kwargs: fn(*args, **kwargs))
    monkeypatch.setattr(frame, "_focus_latest_answer", lambda: focused.__setitem__("count", focused["count"] + 1))

    frame._on_done(0, "Claude Code 回答", "", "claudecode/default", "", "chat-new")

    assert frame.active_session_turns[0]["answer_md"] == "Claude Code 回答"
    assert rendered["count"] == 1
    assert focused["count"] == 1


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


def test_render_answer_list_hides_blank_user_for_assistant_only_turn(frame):
    frame.active_session_turns = [
        {"question": "", "answer_md": "只有回答", "model": "codex/main", "created_at": time.time()}
    ]

    frame._render_answer_list()

    items = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert items == ["小诸葛", "只有回答"]


def test_codex_answer_filter_menu_label_changes_with_state(frame):
    frame.codex_answer_english_filter_enabled = False
    assert frame._codex_answer_filter_menu_label() == "在回答中过滤英文内容"
    frame.codex_answer_english_filter_enabled = True
    assert frame._codex_answer_filter_menu_label() == "取消过滤英文内容"


def test_render_answer_list_filters_codex_file_tokens_when_enabled(frame):
    frame.codex_answer_english_filter_enabled = True
    frame.active_session_turns = [
        {
            "question": "测试",
            "answer_md": "请检查 C:/code/codex/main.py 和 test_render_answer_list_filters_codex_file_tokens_when_enabled",
            "model": "codex/main",
            "created_at": time.time(),
        }
    ]

    frame._render_answer_list()

    items = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert any("[文件路径]" in item for item in items)
    assert all("main.py" not in item for item in items)
    assert all("test_render_answer_list_filters_codex_file_tokens_when_enabled" not in item for item in items)


def test_render_answer_list_filters_pytest_command_and_test_paths(frame):
    frame.codex_answer_english_filter_enabled = True
    frame.active_session_turns = [
        {
            "question": "测试",
            "answer_md": '已执行并通过：\npytest tests/test_voice_input_ui_automation.py tests/test_voice_input_e2e.py -k "voice or realtime_asr"\n请查看 tests/test_voice_input_ui_automation.py',
            "model": "codex/main",
            "created_at": time.time(),
        }
    ]

    frame._render_answer_list()

    items = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    answer_rows = [item for item in items if item not in ("我", "小诸葛", "测试")]
    assert any("已执行并通过：" in item for item in answer_rows)
    assert all("pytest tests/" not in item for item in answer_rows)
    assert all("test_voice_input_ui_automation.py" not in item for item in answer_rows)


def test_render_answer_list_does_not_filter_non_codex_answers(frame):
    frame.codex_answer_english_filter_enabled = True
    frame.active_session_turns = [
        {
            "question": "测试",
            "answer_md": "请检查 C:/code/codex/main.py 和 test_render_answer_list_filters_codex_file_tokens_when_enabled",
            "model": "openai/gpt-5.2",
            "created_at": time.time(),
        }
    ]

    frame._render_answer_list()

    items = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert any("main.py" in item for item in items)
    assert any("test_render_answer_list_filters_codex_file_tokens_when_enabled" in item for item in items)


def test_answer_detail_html_filters_codex_only(frame):
    frame.codex_answer_english_filter_enabled = True

    codex_html = frame._build_answer_detail_html(
        "请查看 C:/code/codex/main.py 和 test_answer_detail_html_filters_codex_only",
        "codex/main",
    )
    assert "[文件路径]" in codex_html
    assert "main.py" not in codex_html
    assert "test_answer_detail_html_filters_codex_only" not in codex_html

    other_html = frame._build_answer_detail_html(
        "请查看 C:/code/codex/main.py 和 test_answer_detail_html_filters_codex_only",
        "openai/gpt-5.2",
    )
    assert "main.py" in other_html
    assert "test_answer_detail_html_filters_codex_only" in other_html


def test_remote_turn_payload_filters_codex_only(frame):
    frame.codex_answer_english_filter_enabled = True
    codex_turn = {
        "question": "测试",
        "answer_md": "参考 C:/code/codex/main.py 和 test_remote_turn_payload_filters_codex_only",
        "model": "codex/main",
        "created_at": 1.0,
    }
    other_turn = {
        "question": "测试",
        "answer_md": "参考 C:/code/codex/main.py 和 test_remote_turn_payload_filters_codex_only",
        "model": "openai/gpt-5.2",
        "created_at": 1.0,
    }

    codex_payload = frame._remote_turn_payload(codex_turn)
    other_payload = frame._remote_turn_payload(other_turn)

    assert "[文件路径]" in codex_payload["answer"]
    assert "main.py" not in codex_payload["answer"]
    assert "test_remote_turn_payload_filters_codex_only" not in codex_payload["answer"]
    assert "main.py" in other_payload["answer"]
    assert "test_remote_turn_payload_filters_codex_only" in other_payload["answer"]


def test_codex_answer_filter_preserves_plain_english_sentences(frame):
    frame.codex_answer_english_filter_enabled = True

    text = frame._answer_markdown_for_output(
        "Use the latest model and keep the command name pytest unchanged.",
        "codex/main",
    )

    assert "latest model" in text
    assert "pytest" in text


def test_codex_answer_filter_replaces_markdown_file_links(frame):
    frame.codex_answer_english_filter_enabled = True

    text = frame._answer_markdown_for_output(
        "请查看 [main.py](/c:/code/codex/main.py#L1098) 和 `tests/test_main_unit.py`",
        "codex/main",
    )

    assert "[文件路径]" in text
    assert "main.py" not in text
    assert "test_main_unit.py" not in text


def test_answer_ctrl_c_copies_filtered_codex_text(frame, monkeypatch):
    copied = {}

    class _Clipboard:
        def Open(self):
            return True

        def SetData(self, data):
            copied["text"] = data.GetText()
            return True

        def Close(self):
            return True

    frame.codex_answer_english_filter_enabled = True
    frame.active_session_turns = [
        {
            "question": "测试",
            "answer_md": "参考 C:/code/codex/main.py 和 test_answer_ctrl_c_copies_filtered_codex_text",
            "model": "codex/main",
            "created_at": time.time(),
        }
    ]
    frame._render_answer_list()
    monkeypatch.setattr(main.wx, "TheClipboard", _Clipboard())
    answer_row = next(i for i, meta in enumerate(frame.answer_meta) if meta[0] == "answer")
    frame.answer_list.SetSelection(answer_row)

    class E:
        def GetKeyCode(self):
            return ord("C")

        def ControlDown(self):
            return True

        def StopPropagation(self):
            return None

        def Skip(self):
            return None

    frame._on_answer_key_down(E())

    assert "C:/code/codex/main.py" in copied["text"]
    assert "test_answer_ctrl_c_copies_filtered_codex_text" in copied["text"]


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


def test_on_close_marks_current_chat_pending_turns_before_save(frame, monkeypatch):
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

    seen = {}

    def _save_state():
        seen["current"] = copy.deepcopy(frame.active_session_turns)
        seen["archived"] = copy.deepcopy(frame.archived_chats)

    monkeypatch.setattr(frame, "_save_state", _save_state)
    monkeypatch.setattr(frame._voice_input, "cancel", lambda: None)
    monkeypatch.setattr(frame._realtime_call, "shutdown", lambda: None)
    monkeypatch.setattr(frame, "_stop_openclaw_sync", lambda: None)
    monkeypatch.setattr(frame, "_global_ctrl_hook", type("H", (), {"stop": lambda self: None})())
    monkeypatch.setattr(frame, "_unregister_global_hotkey", lambda: None)
    monkeypatch.setattr(frame, "_tray_icon", None, raising=False)

    class _Event:
        def Skip(self):
            seen["skip"] = True

    frame._on_close(_Event())

    assert seen["current"][0]["request_status"] == "pending"
    assert seen["current"][0]["request_recoverable"] is True
    assert seen["current"][1]["request_status"] == "done"
    assert seen["archived"] == []
    assert seen["skip"] is True


def test_on_close_marks_archived_chat_pending_turns_before_save(frame, monkeypatch):
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

    seen = {}

    def _save_state():
        seen["archived"] = copy.deepcopy(frame.archived_chats)

    monkeypatch.setattr(frame, "_save_state", _save_state)
    monkeypatch.setattr(frame._voice_input, "cancel", lambda: None)
    monkeypatch.setattr(frame._realtime_call, "shutdown", lambda: None)
    monkeypatch.setattr(frame, "_stop_openclaw_sync", lambda: None)
    monkeypatch.setattr(frame, "_global_ctrl_hook", type("H", (), {"stop": lambda self: None})())
    monkeypatch.setattr(frame, "_unregister_global_hotkey", lambda: None)
    monkeypatch.setattr(frame, "_tray_icon", None, raising=False)

    class _Event:
        def Skip(self):
            pass

    frame._on_close(_Event())

    archived = next(chat for chat in seen["archived"] if chat.get("id") == "chat-old")
    turns = archived["turns"]
    assert turns[0]["request_status"] == "pending"
    assert turns[0]["request_recoverable"] is True
    assert turns[1]["request_status"] == "pending"
    assert turns[1]["request_recoverable"] is False


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
    assert "CLI 刷新问题"[:6] in archived["title"]


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


def test_load_state_restores_codex_answer_filter_flag(frame, tmp_path):
    frame.state_path = tmp_path / "app_state.json"
    frame.state_path.write_text(
        json.dumps(
            {
                "selected_model_id": main.DEFAULT_MODEL_ID,
                "codex_answer_english_filter_enabled": True,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    frame.codex_answer_english_filter_enabled = False
    frame._load_state()

    assert frame.codex_answer_english_filter_enabled is True


def test_resolve_app_data_dir_is_dist_history():
    p = main.resolve_app_data_dir()
    assert str(p).lower().endswith("dist\\history")


def test_model_ids_contains_new_models():
    assert "stepfun/step-3.5-flash" in main.MODEL_IDS
    assert "meta-llama/llama-3.1-8b-instruct" in main.MODEL_IDS
    assert "z-ai/glm-4.5-air" in main.MODEL_IDS
    assert "deepseek/deepseek-r1-0528-qwen3-8b" in main.MODEL_IDS
    assert "qwen/qwen3-8b" in main.MODEL_IDS


def test_first_run_default_model_is_codex(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "resolve_app_data_dir", lambda: tmp_path)
    f = main.ChatFrame()
    try:
        assert f.selected_model == "openai/gpt-5.2"
        assert f.model_combo.GetValue() == "openai/gpt-5.2"
    finally:
        f.Destroy()


def test_load_state_still_defaults_to_codex(monkeypatch, tmp_path):
    state = {
        "selected_model_id": "qwen/qwen3-8b",
        "archived_chats": [],
        "active_session_turns": [],
        "active_session_started_at": 0.0,
    }
    (tmp_path / "app_state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(main, "resolve_app_data_dir", lambda: tmp_path)
    f = main.ChatFrame()
    try:
        assert f.selected_model == "openai/gpt-5.2"
        assert f.model_combo.GetValue() == "openai/gpt-5.2"
    finally:
        f.Destroy()


def test_startup_shows_last_active_turns_in_answer_list(monkeypatch, tmp_path):
    state = {
        "selected_model_id": "openai/gpt-5.2-chat",
        "archived_chats": [],
        "active_session_turns": [
            {
                "question": "上次问题",
                "answer_md": "上次回答",
                "model": "codex/main",
                "created_at": 1.0,
            }
        ],
        "active_session_started_at": 1.0,
    }
    (tmp_path / "app_state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(main, "resolve_app_data_dir", lambda: tmp_path)
    f = main.ChatFrame()
    try:
        assert f.selected_model == "codex/main"
        assert f.answer_list.GetCount() >= 1
        items = [f.answer_list.GetString(i) for i in range(f.answer_list.GetCount())]
        assert any("上次回答" in item for item in items)
    finally:
        f.Destroy()


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

    seen = {}

    class _Client:
        def start_thread(self, **_kwargs):
            raise AssertionError("should reuse archived chat thread id")

        def start_turn(self, thread_id, text):
            seen["turn"] = (thread_id, text)
            return {"turn": {"id": "33333333-3333-3333-3333-333333333333"}}

    monkeypatch.setattr(frame, "_get_or_create_codex_client", lambda _chat_id: _Client())
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *args, **kwargs: None)

    frame._worker("", 0, "恢复旧聊天", "codex/main", False, archived_chat_id)

    assert seen["turn"] == ("11111111-1111-1111-1111-111111111111", "恢复旧聊天")
    assert frame.active_chat_id == current_chat_id
    archived = frame._find_archived_chat(archived_chat_id)
    assert archived is not None
    assert archived["codex_turn_id"] == "33333333-3333-3333-3333-333333333333"


@pytest.mark.parametrize(
    ("request_status", "request_error", "expected"),
    [
        ("failed", "", "上次未完成回答恢复失败，可手动继续"),
        ("failed", "boom", "boom"),
    ],
)
def test_turn_answer_markdown_shows_recovery_status_for_empty_answer(frame, request_status, request_error, expected):
    answer_md, clean_answer_md = frame._turn_answer_markdown(
        {
            "question": "q",
            "answer_md": "",
            "model": "openai/gpt-5.2",
            "request_status": request_status,
            "request_error": request_error,
        }
    )

    assert answer_md == ""
    assert clean_answer_md == expected


def test_render_answer_list_shows_recovery_status_for_empty_answer(frame):
    frame.active_session_turns = [
        {
            "question": "失败的问题",
            "answer_md": "",
            "model": "openai/gpt-5.2",
            "created_at": 1.0,
            "request_status": "failed",
            "request_error": "boom",
        },
    ]

    frame._render_answer_list()

    items = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert "boom" in items
    assert all("正在恢复上次未完成的回答" not in item for item in items)


def test_on_done_marks_pending_turn_done(frame):
    frame.active_session_turns = [
        {
            "question": "待完成问题",
            "answer_md": "",
            "model": "openai/gpt-5.2",
            "created_at": 1.0,
            "request_status": "pending",
            "request_recoverable": True,
            "request_model": "openai/gpt-5.2",
            "request_question": "待完成问题",
            "request_last_attempt_at": 4567.0,
            "request_attempt_count": 2,
            "request_error": "stale",
            "request_recovered_after_restart": False,
        }
    ]

    frame._on_done(0, "完成", "", "openai/gpt-5.2", "", frame.active_chat_id)

    turn = frame.active_session_turns[0]
    assert turn["request_status"] == "done"
    assert turn["request_error"] == ""
    assert turn["request_recovered_after_restart"] is False


def test_on_done_marks_pending_turn_failed_and_sets_request_error(frame):
    frame.active_session_turns = [
        {
            "question": "待失败问题",
            "answer_md": "",
            "model": "openai/gpt-5.2",
            "created_at": 1.0,
            "request_status": "pending",
            "request_recoverable": True,
            "request_model": "openai/gpt-5.2",
            "request_question": "待失败问题",
            "request_last_attempt_at": 4567.0,
            "request_attempt_count": 2,
            "request_error": "",
            "request_recovered_after_restart": False,
        }
    ]

    frame._on_done(0, "", "boom", "openai/gpt-5.2", "", frame.active_chat_id)

    turn = frame.active_session_turns[0]
    assert turn["request_status"] == "failed"
    assert turn["request_error"] == "boom"
    assert turn["request_recovered_after_restart"] is False


def test_model_display_name_maps_codex_and_claudecode():
    assert main.model_display_name("codex/main") == "codex"
    assert main.model_display_name("claudecode/default") == "claudeCode"
    assert main.model_id_from_display_name("codex") == "codex/main"
    assert main.model_id_from_display_name("claudeCode") == "claudecode/default"


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


def test_sanitize_optimized_text_removes_markdown_and_blank_lines():
    src = "# 标题\n\n- 第一条\n- 第二条\n\n**加粗** `代码` 😀\n"
    out = main.sanitize_optimized_text(src)
    assert "#" not in out
    assert "- " not in out
    assert "**" not in out
    assert "`" not in out
    assert "😀" not in out
    assert "\n\n" not in out


def test_sanitize_optimized_text_dedupes_near_duplicate_lines():
    src = "今天天气很好。\n今天天气很好！\n今天天气很好\n"
    out = main.sanitize_optimized_text(src)
    lines = [x for x in out.split("\n") if x.strip()]
    assert len(lines) == 1
    assert "今天天气很好" in lines[0]


def test_optimize_voice_text_without_api_key_still_sanitizes(frame, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    out = frame._optimize_voice_text("# 标题\n\n- 内容 😀")
    assert "#" not in out
    assert "- " not in out
    assert "😀" not in out


def test_fallback_model_order_for_deepseek_variant(frame):
    out = frame._candidate_fallback_models("deepseek/deepseek-r1-0528-qwen3-8b")
    assert out[0] == "deepseek/deepseek-r1-0528"
    assert "openai/gpt-5.2" in out


def test_turn_model_updates_to_actual_used_model_on_fallback(frame):
    frame.active_session_turns = [{"question": "q", "answer_md": "", "model": "deepseek/deepseek-r1-0528-qwen3-8b", "created_at": 1}]
    frame._on_done(0, "ok", "", "deepseek/deepseek-r1-0528", "模型 x 当前不可用，已回退到 y")
    assert frame.active_session_turns[0]["model"] == "deepseek/deepseek-r1-0528"


def test_voice_optimize_model_switched_to_gpt_chat():
    assert main.VOICE_OPTIMIZE_MODEL == "openai/gpt-5.2-chat"


def test_escape_in_input_does_not_minimize_via_char_hook(frame):
    frame.input_edit.SetFocus()
    minimized = {"n": 0}
    frame._minimize_to_tray = lambda: minimized.__setitem__("n", minimized["n"] + 1)

    class E:
        def GetKeyCode(self):
            return wx.WXK_ESCAPE

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            pass

    frame._on_char_hook(E())
    assert minimized["n"] == 0


def test_real_escape_keydown_minimizes(frame):
    minimized = {"n": 0}
    frame._minimize_to_tray = lambda: minimized.__setitem__("n", minimized["n"] + 1)
    
    class E:
        def GetKeyCode(self):
            return wx.WXK_ESCAPE

        def GetRawKeyCode(self):
            return 27

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            pass

    frame._on_any_key_down_escape_minimize(E())
    assert minimized["n"] == 1


def test_escape_keydown_with_raw_188_does_not_minimize(frame):
    minimized = {"n": 0}
    frame._minimize_to_tray = lambda: minimized.__setitem__("n", minimized["n"] + 1)

    class E:
        def GetKeyCode(self):
            return wx.WXK_ESCAPE

        def GetRawKeyCode(self):
            return 188  # VK_OEM_COMMA

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            pass

    frame._on_any_key_down_escape_minimize(E())
    assert minimized["n"] == 0


def test_escape_keydown_with_raw_0_does_not_minimize(frame):
    minimized = {"n": 0}
    frame._minimize_to_tray = lambda: minimized.__setitem__("n", minimized["n"] + 1)

    class E:
        def GetKeyCode(self):
            return wx.WXK_ESCAPE

        def GetRawKeyCode(self):
            return 0

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            pass

    frame._on_any_key_down_escape_minimize(E())
    assert minimized["n"] == 0


def test_non_escape_keydown_with_raw_27_does_not_minimize(frame):
    minimized = {"n": 0}
    frame._minimize_to_tray = lambda: minimized.__setitem__("n", minimized["n"] + 1)

    class E:
        def GetKeyCode(self):
            return ord("A")

        def GetRawKeyCode(self):
            return 27

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            pass

    frame._on_any_key_down_escape_minimize(E())
    assert minimized["n"] == 0


def test_close_not_vetoed_while_running(frame):
    frame.is_running = True
    calls = {"skip": 0, "veto": 0}

    class CloseEvent:
        def Skip(self):
            calls["skip"] += 1

        def Veto(self):
            calls["veto"] += 1

    frame._on_close(CloseEvent())
    assert calls["skip"] == 1
    assert calls["veto"] == 0

def test_resolve_app_data_dir_frozen_uses_dist_history(monkeypatch):
    monkeypatch.setattr(main.sys, "frozen", True, raising=False)
    monkeypatch.setattr(main.sys, "executable", r"C:\code\windowsZhuge\dist\zgwd\zgwd.exe", raising=False)
    p = main.resolve_app_data_dir()
    assert str(p).lower().endswith("dist\\history")


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
    assert changed is False
    assert chat["title"] == "手动改过的标题"



