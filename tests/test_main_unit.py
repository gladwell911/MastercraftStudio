import json
import copy
import time
import threading
from pathlib import Path
from types import SimpleNamespace
import asyncio

import wx
import pytest
from aiohttp import ClientSession

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


def test_remote_ws_defaults_to_fixed_domain_when_host_is_unset(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_TOKEN", "secret")
    monkeypatch.delenv("REMOTE_CONTROL_HOST", raising=False)
    monkeypatch.delenv("REMOTE_CONTROL_DOMAIN", raising=False)
    monkeypatch.delenv("REMOTE_CONTROL_PORT", raising=False)

    url = frame._build_remote_ws_url()

    assert url == "wss://rc.tingyou.cc/ws?token=secret"


def test_remote_ws_server_binds_publicly_by_default(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_TOKEN", "secret")
    monkeypatch.delenv("REMOTE_CONTROL_HOST", raising=False)
    monkeypatch.delenv("REMOTE_CONTROL_DOMAIN", raising=False)
    monkeypatch.delenv("REMOTE_CONTROL_PORT", raising=False)

    captured = {}

    class _FakeServer:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.host = kwargs["host"]
            self.port = kwargs["port"]
            self.bound_port = kwargs["port"]

        def start(self):
            return None

        def stop(self):
            return None

    monkeypatch.setattr(main, "RemoteWebSocketServer", _FakeServer)
    monkeypatch.setattr(frame, "SetStatusText", lambda _text: None)

    frame._start_remote_ws_server_if_configured()

    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 18080
    assert frame.remote_control_runtime_url == "wss://rc.tingyou.cc/ws?token=secret"


def test_remote_ws_server_supports_legacy_claudecode_env_names(frame, monkeypatch):
    monkeypatch.delenv("REMOTE_CONTROL_TOKEN", raising=False)
    monkeypatch.delenv("REMOTE_CONTROL_HOST", raising=False)
    monkeypatch.delenv("REMOTE_CONTROL_PORT", raising=False)
    monkeypatch.setenv("CLAUDECODE_REMOTE_CONTROL_TOKEN", "secret")
    monkeypatch.setenv("CLAUDECODE_REMOTE_CONTROL_PORT", "0")
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))

    assert frame._build_remote_ws_url() == "ws://127.0.0.1:18080/ws?token=secret"

    frame._start_remote_ws_server_if_configured(ensure_connectivity=True)
    try:
        assert frame._remote_ws_server.host == "127.0.0.1"
        assert frame._remote_ws_server.bound_port > 0

        async def _run():
            session = ClientSession()
            try:
                ws = await session.ws_connect(f"http://127.0.0.1:{frame._remote_ws_server.bound_port}/ws?token=secret")
                try:
                    connected = (await ws.receive()).json()
                    assert connected["type"] == "connected"
                finally:
                    await ws.close()
            finally:
                await session.close()

        asyncio.run(_run())
    finally:
        frame._remote_ws_server.stop()
        frame._remote_ws_server = None
        if frame._remote_http_server is not None:
            frame._remote_http_server.stop()
            frame._remote_http_server = None


def test_remote_ws_start_also_starts_remote_http_server(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_TOKEN", "secret")
    monkeypatch.setenv("REMOTE_CONTROL_PORT", "0")

    frame._start_remote_ws_server_if_configured()
    try:
        assert frame._remote_http_server is not None
        assert frame._remote_http_server.bound_port > 0
    finally:
        if frame._remote_ws_server is not None:
            frame._remote_ws_server.stop()
            frame._remote_ws_server = None
        if frame._remote_http_server is not None:
            frame._remote_http_server.stop()
            frame._remote_http_server = None


def test_remote_ws_url_strips_invalid_port_zero_from_domain(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_TOKEN", "secret")
    monkeypatch.setenv("REMOTE_CONTROL_DOMAIN", "https://rc.tingyou.cc:0/ws#")

    url = frame._build_remote_ws_url()

    assert url == "wss://rc.tingyou.cc/ws?token=secret"


def test_remote_control_settings_normalize_dirty_domain(frame):
    frame.remote_control_domain = "https://rc.tingyou.cc:0/ws#"

    frame._initialize_remote_control_settings()

    assert frame.remote_control_domain == "wss://rc.tingyou.cc/ws"


def test_fixed_domain_mode_forces_public_host_and_stable_port(frame):
    frame.remote_control_domain = "rc.tingyou.cc"
    frame.remote_control_host = "127.0.0.1"
    frame.remote_control_port = 0

    frame._initialize_remote_control_settings()

    assert frame.remote_control_domain == "wss://rc.tingyou.cc/ws"
    assert frame.remote_control_host == "0.0.0.0"
    assert frame.remote_control_port == 18080


def test_fixed_domain_server_uses_public_runtime_and_status(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_TOKEN", "secret")
    monkeypatch.setenv("REMOTE_CONTROL_DOMAIN", "rc.tingyou.cc")
    monkeypatch.setenv("REMOTE_CONTROL_HOST", "127.0.0.1")
    monkeypatch.setenv("REMOTE_CONTROL_PORT", "0")

    captured = {}

    class _FakeServer:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.host = kwargs["host"]
            self.port = kwargs["port"]
            self.bound_port = kwargs["port"]

        def start(self):
            return None

        def stop(self):
            return None

    monkeypatch.setattr(main, "RemoteWebSocketServer", _FakeServer)
    statuses = []
    monkeypatch.setattr(frame, "SetStatusText", lambda text: statuses.append(text))

    frame._start_remote_ws_server_if_configured()

    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 18080
    assert statuses
    assert "0.0.0.0:18080" in statuses[-1]
    assert "wss://rc.tingyou.cc/ws?token=secret" in statuses[-1]


def test_remote_ws_startup_runs_fixed_domain_connectivity_check(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_TOKEN", "secret")
    monkeypatch.setenv("REMOTE_CONTROL_DOMAIN", "rc.tingyou.cc")

    class _FakeServer:
        def __init__(self, **kwargs):
            self.host = kwargs["host"]
            self.port = kwargs["port"]
            self.bound_port = kwargs["port"]

        def start(self):
            return None

        def stop(self):
            return None

    seen = {}
    monkeypatch.setattr(main, "RemoteWebSocketServer", _FakeServer)
    monkeypatch.setattr(frame, "SetStatusText", lambda _text: None)
    monkeypatch.setattr(
        frame,
        "_ensure_remote_ws_startup_connectivity",
        lambda **kwargs: seen.update(kwargs),
    )

    frame._start_remote_ws_server_if_configured(ensure_connectivity=True)

    assert seen == {
        "token": "secret",
        "published_url": "wss://rc.tingyou.cc/ws?token=secret",
    }


def test_remote_startup_connectivity_restarts_cloudflared_after_public_probe_failure(frame, monkeypatch):
    frame.remote_control_host = "0.0.0.0"
    frame.remote_control_runtime_bind = "ws://0.0.0.0:18080/ws"
    frame._remote_ws_server = SimpleNamespace(bound_port=18080)
    monkeypatch.setattr(
        frame,
        "_remote_runtime_config",
        lambda: {"fixed_domain_mode": True, "port": 18080},
    )
    monkeypatch.setattr(frame, "_remote_local_listener_ready", lambda _port: True)
    monkeypatch.setattr(frame, "_verify_remote_local_health", lambda _token, _port: (True, ""))
    monkeypatch.setattr(frame, "_start_cloudflared_service", lambda: True)
    probe_results = iter([(False, "公网隧道握手失败"), (True, "")])
    monkeypatch.setattr(frame, "_verify_remote_public_ws", lambda _url: next(probe_results))
    restarted = {"calls": 0}
    monkeypatch.setattr(
        frame,
        "_restart_cloudflared_service",
        lambda: restarted.__setitem__("calls", restarted["calls"] + 1) or True,
    )
    statuses = []
    monkeypatch.setattr(frame, "SetStatusText", lambda text: statuses.append(text))

    frame._ensure_remote_ws_startup_connectivity(
        token="secret",
        published_url="wss://rc.tingyou.cc/ws?token=secret",
    )

    assert restarted["calls"] == 1
    assert statuses
    assert "cloudflared 已重启并恢复 rc.tingyou.cc 连接" in statuses[-1]


def test_restart_cloudflared_service_kills_stuck_process_before_restart(frame, monkeypatch):
    states = [
        {"exists": True, "running": True, "detail": "running"},
        {"exists": True, "running": True, "detail": "running"},
        {"exists": True, "running": True, "detail": "running"},
        {"exists": True, "running": False, "detail": "stopped"},
        {"exists": True, "running": False, "detail": "stopped"},
    ]
    monkeypatch.setattr(
        frame,
        "_query_cloudflared_service",
        lambda: states.pop(0) if len(states) > 1 else states[0],
    )
    commands = []
    monkeypatch.setattr(
        frame,
        "_run_remote_check_command",
        lambda args, timeout=10.0: commands.append(tuple(args)) or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(frame, "_start_cloudflared_service", lambda: True)
    monkeypatch.setattr(main.time, "sleep", lambda _seconds: None)

    assert frame._restart_cloudflared_service() is True
    assert ("sc.exe", "stop", "cloudflared") in commands
    assert ("taskkill", "/F", "/IM", "cloudflared.exe") in commands


def test_remote_ws_server_autostarts_without_env_and_uses_default_token(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "resolve_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(main.ChatFrame, "_legacy_state_paths", lambda self: [self.state_path])
    monkeypatch.setattr(main.ChatFrame, "_migrate_legacy_state_if_needed", lambda self: None)
    monkeypatch.setattr(main.ChatFrame, "_ensure_remote_ws_startup_connectivity", lambda self, **_kwargs: None)
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setenv("REMOTE_CONTROL_AUTOSTART", "1")
    monkeypatch.setenv("REMOTE_CONTROL_PORT", "0")

    frame = main.ChatFrame()
    try:
        assert frame.remote_control_token == main.DEFAULT_REMOTE_CONTROL_TOKEN
        assert frame._remote_ws_server is not None
        assert frame._remote_ws_server.bound_port > 0

        async def _run():
            session = ClientSession()
            try:
                ws = await session.ws_connect(
                    f"http://127.0.0.1:{frame._remote_ws_server.bound_port}/ws?token={frame.remote_control_token}"
                )
                try:
                    connected = (await ws.receive()).json()
                    assert connected["type"] == "connected"
                    assert connected["body"]["accepted"] is True
                finally:
                    await ws.close()
            finally:
                await session.close()

        asyncio.run(_run())
        saved_state = json.loads(frame.state_path.read_text(encoding="utf-8"))
        assert saved_state["remote_control_token"] == main.DEFAULT_REMOTE_CONTROL_TOKEN
    finally:
        if frame._remote_ws_server is not None:
            frame._remote_ws_server.stop()
            frame._remote_ws_server = None
        frame.Destroy()


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


def test_answer_list_ctrl_c_still_copies_answer_text(frame, monkeypatch):
    copied = {"text": None}

    class _Clipboard:
        def Open(self):
            return True

        def SetData(self, data):
            copied["text"] = data.GetText()
            return True

        def Close(self):
            return True

    monkeypatch.setattr(main.wx, "TheClipboard", _Clipboard())
    statuses = []
    monkeypatch.setattr(frame, "SetStatusText", lambda text: statuses.append(text))
    monkeypatch.setattr(frame, "_on_any_key_down_escape_minimize", lambda _event: False)

    frame.answer_meta = [("answer", 0, "plain answer", "rich answer")]
    frame.answer_list.Clear()
    frame.answer_list.Append("answer row")
    frame.answer_list.SetSelection(0)

    class E:
        def GetKeyCode(self):
            return ord("C")

        def ControlDown(self):
            return True

        def StopPropagation(self):
            return None

        def Skip(self):
            raise AssertionError("should not skip")

    frame._on_answer_key_down(E())

    assert copied["text"] == "rich answer"
    assert statuses[-1] == "已复制"


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

    assert frame._switch_current_chat("chat-b") is True

    assert frame.history_ids == ["chat-f", "chat-c", "chat-g", "chat-b", "chat-a"]
    assert list(frame.history_list.GetStrings()) == ["[置顶] 置顶F", "[置顶] 置顶C", "聊天G", "聊天B", "聊天A"]
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
    frame.history_list.SetSelection(frame.history_ids.index("chat-g"))

    assert frame._adjacent_history_chat_id(1) == "chat-f"


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


def test_generic_key_down_ctrl_right_switches_to_next_chat(frame):
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

    frame._on_generic_key_down(E())

    assert seen["chat_id"] == "chat-c"
    assert seen["skipped"] == 1


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

    def fake_archive(quick_title=False, schedule_async_rename=False):
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


def test_new_chat_immediately_appears_in_history_with_placeholder_title(frame):
    frame.active_chat_id = ""
    frame.current_chat_id = ""
    frame.active_session_turns = []
    frame.archived_chats = []
    frame._current_chat_state = {}

    frame._on_new_chat_clicked(None)

    assert list(frame.history_list.GetStrings()) == ["心聊天"]


def test_render_answer_list_hides_requesting_placeholder_until_done(frame):
    frame.active_session_turns = [
        {"question": "问题", "answer_md": main.REQUESTING_TEXT, "model": "openai/gpt-5.2", "created_at": 1.0}
    ]

    frame._render_answer_list()

    rows = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert "问题" in rows
    assert "正在请求..." not in rows
    assert "小诸葛" not in rows


def test_on_done_renders_final_answer_after_hidden_requesting_placeholder(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_session_turns = [
        {"question": "问题", "answer_md": main.REQUESTING_TEXT, "model": "openai/gpt-5.2", "created_at": 1.0, "request_status": "pending"}
    ]
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)
    monkeypatch.setattr(frame, "_focus_latest_answer", lambda: None)

    frame._render_answer_list()
    before = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert "正在请求..." not in before

    frame._on_done(0, "最终回答", "", "openai/gpt-5.2", "", "chat-current")

    after = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert "小诸葛" in after
    assert "最终回答" in after


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


def test_background_codex_feedback_events_batch_state_flush(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
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

    scheduled = {"count": 0, "delay": None, "callback": None}
    saves = {"count": 0}
    monkeypatch.setattr(frame, "_save_state", lambda: saves.__setitem__("count", saves["count"] + 1))
    monkeypatch.setattr(
        frame,
        "_call_later_if_alive",
        lambda delay, fn, *args, **kwargs: scheduled.update({"count": scheduled["count"] + 1, "delay": delay, "callback": fn}) or object(),
    )

    frame._on_codex_event_for_chat(
        "chat-background",
        main.CodexEvent(type="stderr", thread_id="thread-background", turn_id="turn-background", text="a"),
    )
    frame._on_codex_event_for_chat(
        "chat-background",
        main.CodexEvent(type="plan_updated", thread_id="thread-background", turn_id="turn-background", text="b"),
    )

    assert scheduled["count"] == 1
    assert scheduled["delay"] == main.CODEX_BACKGROUND_FLUSH_DELAY_MS
    assert saves["count"] == 0

    scheduled["callback"]()

    assert saves["count"] == 1


def test_current_chat_codex_delta_still_queues_ui_work(frame, monkeypatch):
    call_after_calls = []
    monkeypatch.setattr(main.wx, "GetApp", lambda: object())
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *args, **kwargs: call_after_calls.append((fn, args, kwargs)))

    frame._dispatch_codex_event_to_ui(
        frame.current_chat_id,
        main.CodexEvent(type="agent_message_delta", thread_id=frame.active_codex_thread_id, turn_id=frame.active_codex_turn_id, text="delta"),
    )

    assert len(call_after_calls) == 1


def test_current_chat_codex_delta_updates_row_without_full_rerender(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {"question": "正在改代码", "answer_md": "", "model": main.DEFAULT_CODEX_MODEL, "created_at": 1.0}
    ]
    frame.view_mode = "active"
    frame._render_answer_list()

    seen = {"render": 0, "save": 0}
    original_update = frame._update_active_answer_row
    monkeypatch.setattr(frame, "_render_answer_list", lambda: seen.__setitem__("render", seen["render"] + 1))
    monkeypatch.setattr(frame, "_save_state", lambda: seen.__setitem__("save", seen["save"] + 1))

    frame._on_codex_event_for_chat(
        "chat-current",
        main.CodexEvent(
            type="agent_message_delta",
            thread_id="thread-current",
            turn_id="turn-current",
            text="stream chunk",
        ),
    )

    original_update(0)

    assert seen["render"] == 0
    assert seen["save"] == 1
    assert frame.active_session_turns[0]["answer_md"] == main.REQUESTING_TEXT
    answer_row = next(i for i, meta in enumerate(frame.answer_meta) if meta[0] == "answer")
    assert frame.answer_list.GetString(answer_row) == main.REQUESTING_TEXT


def test_current_chat_codex_delta_with_hidden_placeholder_does_not_rerender_question_rows(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {
            "question": "Codex 正在处理的问题",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": 1.0,
            "request_status": "pending",
        }
    ]
    frame.view_mode = "active"
    frame._render_answer_list()
    before_rows = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert before_rows == ["我", "Codex 正在处理的问题"]

    seen = {"render": 0, "save": 0}
    monkeypatch.setattr(frame, "_render_answer_list", lambda: seen.__setitem__("render", seen["render"] + 1))
    monkeypatch.setattr(frame, "_save_state", lambda: seen.__setitem__("save", seen["save"] + 1))

    frame._on_codex_event_for_chat(
        "chat-current",
        main.CodexEvent(
            type="agent_message_delta",
            thread_id="thread-current",
            turn_id="turn-current",
            text="后台增量",
        ),
    )

    after_rows = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert seen["render"] == 0
    assert seen["save"] == 1
    assert frame.active_session_turns[0]["answer_md"] == main.REQUESTING_TEXT
    assert after_rows == before_rows


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


def test_try_open_selected_answer_detail_opens_attachment_path(frame, monkeypatch, tmp_path):
    attachment = tmp_path / "report.txt"
    attachment.write_text("attachment body", encoding="utf-8")
    frame.answer_meta = [("attachment", 0, "report.txt", str(attachment))]
    frame.answer_list.Append("report.txt")
    frame.answer_list.SetSelection(0)
    opened = {}
    monkeypatch.setattr(main.os, "startfile", lambda path: opened.__setitem__("path", path), raising=False)

    assert frame._try_open_selected_answer_detail()
    assert opened["path"] == str(attachment)


def test_render_answer_list_shows_uploaded_and_received_attachment_rows(frame, tmp_path):
    uploaded = tmp_path / "uploaded.png"
    uploaded.write_text("uploaded", encoding="utf-8")
    received = tmp_path / "received.txt"
    received.write_text("received", encoding="utf-8")
    frame.active_session_turns = [
        {
            "question": "uploaded.png 图片已成功上传",
            "answer_md": "",
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": time.time(),
            "suppress_empty_answer_row": True,
            "attachments": [
                {
                    "name": "uploaded.png",
                    "path": str(uploaded),
                    "kind": "image",
                    "direction": "outgoing",
                    "status": "success",
                    "open_path": str(uploaded),
                }
            ],
            "received_attachments": [
                {
                    "name": "received.txt",
                    "path": str(received),
                    "kind": "file",
                    "direction": "incoming",
                    "status": "success",
                    "open_path": str(received),
                }
            ],
        }
    ]
    frame.view_mode = "active"

    frame._render_answer_list()

    rows = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    meta_types = [meta[0] for meta in frame.answer_meta]
    assert "图片上传成功" in rows
    assert "uploaded.png 图片已成功上传" not in rows
    assert "已上传：uploaded.png" not in rows
    assert "CLI 发来文件：received.txt" in rows
    assert meta_types.count("attachment") == 2


def test_render_answer_list_shows_each_uploaded_attachment_on_its_own_line(frame, tmp_path):
    image_one = tmp_path / "one.png"
    image_two = tmp_path / "two.png"
    file_one = tmp_path / "alpha.txt"
    file_two = tmp_path / "beta.txt"
    for path in (image_one, image_two, file_one, file_two):
        path.write_text(path.name, encoding="utf-8")
    frame.active_session_turns = [
        {
            "question": "",
            "answer_md": "",
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": time.time(),
            "suppress_empty_answer_row": True,
            "attachments": [
                {"name": image_one.name, "path": str(image_one), "kind": "image", "direction": "outgoing", "status": "success", "open_path": str(image_one)},
                {"name": image_two.name, "path": str(image_two), "kind": "image", "direction": "outgoing", "status": "success", "open_path": str(image_two)},
                {"name": file_one.name, "path": str(file_one), "kind": "file", "direction": "outgoing", "status": "success", "open_path": str(file_one)},
                {"name": file_two.name, "path": str(file_two), "kind": "file", "direction": "outgoing", "status": "success", "open_path": str(file_two)},
            ],
        }
    ]

    frame._render_answer_list()

    rows = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert rows == ["我", "图片上传成功", "图片上传成功", "alpha.txt 上传成功", "beta.txt 上传成功"]


def test_render_answer_list_keeps_standard_qa_structure_for_attachment_only_turn(frame, tmp_path):
    image_path = tmp_path / "only-image.png"
    image_path.write_text("img", encoding="utf-8")
    frame.active_session_turns = [
        {
            "question": "",
            "answer_md": "cli 的回答",
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": time.time(),
            "attachments": [
                {
                    "name": image_path.name,
                    "path": str(image_path),
                    "kind": "image",
                    "direction": "outgoing",
                    "status": "success",
                    "open_path": str(image_path),
                }
            ],
        }
    ]

    frame._render_answer_list()

    rows = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    meta_types = [meta[0] for meta in frame.answer_meta]
    assert rows == ["我", "图片上传成功", "小诸葛", "cli 的回答"]
    assert meta_types == ["user", "attachment", "ai", "answer"]


def test_on_done_keeps_attachment_only_turn_answer_at_bottom(frame, monkeypatch, tmp_path):
    image_path = tmp_path / "done-image.png"
    image_path.write_text("img", encoding="utf-8")
    frame.active_chat_id = "chat-1"
    frame.current_chat_id = "chat-1"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {
            "question": "",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CLAUDECODE_MODEL,
            "created_at": time.time(),
            "request_status": "pending",
            "attachments": [
                {
                    "name": image_path.name,
                    "path": str(image_path),
                    "kind": "image",
                    "direction": "outgoing",
                    "status": "success",
                    "open_path": str(image_path),
                }
            ],
        }
    ]
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)
    monkeypatch.setattr(frame, "_focus_latest_answer", lambda: None)
    monkeypatch.setattr(frame, "_refresh_history", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_set_input_hint_idle", lambda: None)
    monkeypatch.setattr(frame, "_push_remote_state", lambda *_args, **_kwargs: None)

    frame._on_done(0, "最终回答", "", main.DEFAULT_CLAUDECODE_MODEL, "", "chat-1")

    rows = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    meta_types = [meta[0] for meta in frame.answer_meta]
    assert rows == ["我", "图片上传成功", "小诸葛", "最终回答"]
    assert meta_types == ["user", "attachment", "ai", "answer"]


def test_codex_image_item_event_records_received_attachment(frame, monkeypatch, tmp_path):
    image_path = tmp_path / "from-cli.png"
    image_path.write_text("image", encoding="utf-8")
    frame.active_chat_id = "chat-1"
    frame.current_chat_id = "chat-1"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {
            "question": "看图",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": time.time(),
            "request_status": "pending",
        }
    ]
    seen = {"render": 0}
    monkeypatch.setattr(frame, "_render_answer_list", lambda: seen.__setitem__("render", seen["render"] + 1))

    frame._on_codex_event_for_chat(
        "chat-1",
        main.CodexEvent(
            type="item_completed",
            thread_id="thread-1",
            turn_id="turn-1",
            item_id="image-1",
            status="imageView",
            data={"id": "image-1", "type": "imageView", "path": str(image_path)},
        ),
    )

    attachments = frame.active_session_turns[0].get("received_attachments") or []
    assert attachments == [
        {
            "name": "from-cli.png",
            "path": str(image_path),
            "kind": "image",
            "direction": "incoming",
            "status": "success",
            "open_path": str(image_path),
            "source": "codex",
        }
    ]
    assert seen["render"] == 1


def test_codex_final_answer_keeps_attachment_only_turn_answer_at_bottom(frame, monkeypatch, tmp_path):
    image_path = tmp_path / "final-image.png"
    image_path.write_text("img", encoding="utf-8")
    frame.active_chat_id = "chat-1"
    frame.current_chat_id = "chat-1"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {
            "question": "",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": time.time(),
            "request_status": "pending",
            "attachments": [
                {
                    "name": image_path.name,
                    "path": str(image_path),
                    "kind": "image",
                    "direction": "outgoing",
                    "status": "success",
                    "open_path": str(image_path),
                }
            ],
        }
    ]
    monkeypatch.setattr(frame, "_push_remote_final_answer", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_focus_latest_answer", lambda: None)

    frame._on_codex_event_for_chat(
        "chat-1",
        main.CodexEvent(
            type="item_completed",
            thread_id="thread-1",
            turn_id="turn-1",
            item_id="msg-1",
            status="agentMessage",
            phase="final_answer",
            text="codex 最终回答",
        ),
    )

    rows = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    meta_types = [meta[0] for meta in frame.answer_meta]
    assert rows == ["我", "图片上传成功", "小诸葛", "codex 最终回答"]
    assert meta_types == ["user", "attachment", "ai", "answer"]


def test_on_done_extracts_received_file_attachment_from_cli_text(frame, monkeypatch, tmp_path):
    file_path = tmp_path / "from-claudecode.txt"
    file_path.write_text("received", encoding="utf-8")
    frame.active_chat_id = "chat-1"
    frame.current_chat_id = "chat-1"
    frame.active_session_turns = [
        {
            "question": "请生成文件",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CLAUDECODE_MODEL,
            "created_at": time.time(),
            "request_status": "pending",
        }
    ]
    frame.active_turn_idx = 0
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)
    monkeypatch.setattr(frame, "_focus_latest_answer", lambda: None)
    monkeypatch.setattr(frame, "_refresh_history", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_set_input_hint_idle", lambda: None)
    monkeypatch.setattr(frame, "_push_remote_state", lambda *_args, **_kwargs: None)

    frame._on_done(0, f"已生成文件：{file_path}", "", main.DEFAULT_CLAUDECODE_MODEL, "", "chat-1")

    attachments = frame.active_session_turns[0].get("received_attachments") or []
    assert attachments == [
        {
            "name": "from-claudecode.txt",
            "path": str(file_path),
            "kind": "file",
            "direction": "incoming",
            "status": "success",
            "open_path": str(file_path),
            "source": main.DEFAULT_CLAUDECODE_MODEL,
        }
    ]


def test_update_active_answer_row_skips_repaint_when_text_is_unchanged(frame, monkeypatch):
    frame.view_mode = "active"
    frame.active_session_turns = [
        {
            "question": "",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": time.time(),
            "request_status": "pending",
        }
    ]
    frame.answer_list.Append(main.REQUESTING_TEXT)
    frame.answer_meta = [("answer", 0, main.REQUESTING_TEXT, main.REQUESTING_TEXT)]
    frame._active_answer_row_index = 0
    seen = {"repaint": 0}
    monkeypatch.setattr(frame, "_request_listbox_repaint", lambda *_controls: seen.__setitem__("repaint", seen["repaint"] + 1))

    assert frame._update_active_answer_row(0) is True
    assert seen["repaint"] == 0


def test_codex_non_final_delta_does_not_rerender_when_visible_text_is_unchanged(frame, monkeypatch):
    frame.active_chat_id = "chat-1"
    frame.current_chat_id = "chat-1"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {
            "question": "",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": time.time(),
            "request_status": "pending",
        }
    ]
    frame.answer_list.Append(main.REQUESTING_TEXT)
    frame.answer_meta = [("answer", 0, main.REQUESTING_TEXT, main.REQUESTING_TEXT)]
    frame._active_answer_row_index = 0
    seen = {"render": 0, "repaint": 0}
    monkeypatch.setattr(frame, "_render_answer_list", lambda: seen.__setitem__("render", seen["render"] + 1))
    monkeypatch.setattr(frame, "_request_listbox_repaint", lambda *_controls: seen.__setitem__("repaint", seen["repaint"] + 1))

    frame._on_codex_event_for_chat(
        "chat-1",
        main.CodexEvent(
            type="agent_message_delta",
            thread_id="thread-1",
            turn_id="turn-1",
            text="still streaming",
            phase="draft",
        ),
    )

    assert frame.active_session_turns[0]["answer_md"] == main.REQUESTING_TEXT
    assert seen["render"] == 0
    assert seen["repaint"] == 0


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


def test_global_ctrl_arrow_navigates_history_when_app_is_foreground(frame, monkeypatch):
    seen = []
    monkeypatch.setattr(frame, "_global_chat_navigation_target_state", lambda: (True, {"frame_hwnd": 1, "fg_hwnd": 1, "root_hwnd": 1}))
    monkeypatch.setattr(frame, "_navigate_history_chats", lambda direction: seen.append(direction) or True)

    frame._on_global_ctrl_arrow("right")
    frame._on_global_ctrl_arrow("left")

    assert seen == [1, -1]


def test_global_ctrl_arrow_noops_when_app_is_not_foreground(frame, monkeypatch):
    seen = []
    monkeypatch.setattr(frame, "_global_chat_navigation_target_state", lambda: (False, {"frame_hwnd": 1, "fg_hwnd": 2, "root_hwnd": 2}))
    monkeypatch.setattr(frame, "_navigate_history_chats", lambda direction: seen.append(direction) or True)

    frame._on_global_ctrl_arrow("right")

    assert seen == []


def test_global_ctrl_arrow_logs_reason_when_app_is_not_foreground(frame, monkeypatch):
    statuses = []
    monkeypatch.setattr(frame, "_global_chat_navigation_target_state", lambda: (False, {"frame_hwnd": 1, "fg_hwnd": 2, "root_hwnd": 2}))
    monkeypatch.setattr(frame, "SetStatusText", statuses.append)

    frame._on_global_ctrl_arrow("right")

    log_text = (frame.app_data_dir / "ctrl_navigation_debug.log").read_text(encoding="utf-8")
    assert "direction=right" in log_text
    assert "reason=inactive_target" in log_text
    assert statuses[-1] == "Ctrl+左右未生效：当前前台焦点不属于本程序"


def test_global_ctrl_arrow_logs_successful_navigation(frame, monkeypatch):
    statuses = []
    seen = []
    monkeypatch.setattr(frame, "_global_chat_navigation_target_state", lambda: (True, {"frame_hwnd": 1, "fg_hwnd": 1, "root_hwnd": 1}))
    monkeypatch.setattr(frame, "_navigate_history_chats", lambda direction: seen.append(direction) or True)
    monkeypatch.setattr(frame, "SetStatusText", statuses.append)

    frame._on_global_ctrl_arrow("left")

    log_text = (frame.app_data_dir / "ctrl_navigation_debug.log").read_text(encoding="utf-8")
    assert seen == [-1]
    assert "direction=left" in log_text
    assert "result=navigated" in log_text
    assert statuses == []


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


def test_voice_result_speaks_immediately_when_delay_schedule_unavailable(frame, monkeypatch):
    order = []
    frame._insert_text_to_system_focus = lambda text: order.append(("insert", text)) or True
    frame._speak_text_via_screen_reader = lambda text: order.append(("speak", text))
    monkeypatch.setattr(main.wx, "CallLater", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("timer unavailable")))

    frame._on_voice_result("语音文本！！！", mode=main.MODE_DIRECT)

    assert order == [("insert", "语音文本！！！"), ("speak", "语音文本！！！")]


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
    control_bindings = {}

    class _Ctrl:
        def __init__(self, name):
            self.name = name

        def Bind(self, event, *_args, **_kwargs):
            control_bindings.setdefault(self.name, []).append(event)
            return None

    class _DummyFrame:
        def __init__(self):
            self.send_button = _Ctrl("send_button")
            self.new_chat_button = _Ctrl("new_chat_button")
            self.notes_button = _Ctrl("notes_button")
            self.model_combo = _Ctrl("model_combo")
            self.input_edit = _Ctrl("input_edit")
            self.answer_list = _Ctrl("answer_list")
            self.history_list = _Ctrl("history_list")
            self.notes_search_ctrl = _Ctrl("notes_search_ctrl")
            self.notes_search_button = _Ctrl("notes_search_button")
            self.notes_clear_search_button = _Ctrl("notes_clear_search_button")
            self.notes_notebook_list = _Ctrl("notes_notebook_list")
            self.notes_new_notebook_button = _Ctrl("notes_new_notebook_button")
            self.notes_rename_notebook_button = _Ctrl("notes_rename_notebook_button")
            self.notes_delete_notebook_button = _Ctrl("notes_delete_notebook_button")
            self.notes_back_button = _Ctrl("notes_back_button")
            self.notes_entry_list = _Ctrl("notes_entry_list")
            self.notes_new_entry_button = _Ctrl("notes_new_entry_button")
            self.notes_edit_entry_button = _Ctrl("notes_edit_entry_button")
            self.notes_delete_entry_button = _Ctrl("notes_delete_entry_button")
            self.notes_pin_entry_button = _Ctrl("notes_pin_entry_button")
            self.notes_bottom_entry_button = _Ctrl("notes_bottom_entry_button")
            self.notes_import_file_button = _Ctrl("notes_import_file_button")
            self.notes_import_clipboard_button = _Ctrl("notes_import_clipboard_button")
            self.notes_editor = _Ctrl("notes_editor")
            self.notes_save_button = _Ctrl("notes_save_button")
            self.notes_cancel_button = _Ctrl("notes_cancel_button")

        def Bind(self, _event, _handler, id=None):
            frame_bind_calls.append(id)

        def _on_send_clicked(self, *_args):
            return None

        def _on_new_chat_clicked(self, *_args):
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

        def _on_notes_key_down(self, *_args):
            return None

        def _on_notes_context(self, *_args):
            return None

        def _on_notes_notebook_selected(self, *_args):
            return None

        def _on_notes_entry_selected(self, *_args):
            return None

        def _on_notes_editor_changed(self, *_args):
            return None

    dummy = _DummyFrame()
    main.ChatFrame._bind_events(dummy)

    assert main.HOTKEY_ID_SHOW in frame_bind_calls
    assert main.HOTKEY_ID_REALTIME_CALL in frame_bind_calls
    assert main.HOTKEY_ID_REALTIME_CALL_ALT in frame_bind_calls
    assert main.HOTKEY_ID_REALTIME_CALL_ALT2 in frame_bind_calls
    assert main.wx.EVT_KEY_UP in control_bindings["input_edit"]
    assert main.wx.EVT_KEY_UP in control_bindings["send_button"]
    assert main.wx.EVT_KEY_UP in control_bindings["new_chat_button"]
    assert main.wx.EVT_KEY_UP in control_bindings["model_combo"]
    assert main.wx.EVT_KEY_UP in control_bindings["answer_list"]
    assert main.wx.EVT_KEY_UP in control_bindings["history_list"]


def test_char_hook_alt_arms_tools_menu_without_opening(frame):
    seen = {"opened": 0}
    frame._show_tools_menu = lambda: seen.__setitem__("opened", seen["opened"] + 1)

    class E:
        def GetKeyCode(self):
            return wx.WXK_ALT

        def ControlDown(self):
            return False

        def AltDown(self):
            return True

        def Skip(self):
            raise AssertionError("should not skip")

    frame._on_char_hook(E())

    assert seen["opened"] == 0
    assert frame._alt_menu_armed is True
    assert frame._alt_menu_suppressed is False


def test_input_key_up_alt_opens_tools_menu_when_armed(frame):
    seen = {"opened": 0}
    frame._alt_menu_armed = True
    frame._alt_menu_suppressed = False
    frame._show_tools_menu = lambda: seen.__setitem__("opened", seen["opened"] + 1)

    class E:
        def GetKeyCode(self):
            return wx.WXK_ALT

        def Skip(self):
            return None

    frame._on_input_key_up(E())

    assert seen["opened"] == 1
    assert frame._alt_menu_armed is False
    assert frame._alt_menu_suppressed is False


def test_tools_menu_includes_load_image_or_file_action(frame, monkeypatch):
    captured = {"items": []}

    def _popup(menu, *_args):
        captured["items"] = [
            (item.GetItemLabelText(), item.GetId())
            for item in menu.GetMenuItems()
            if not item.IsSeparator()
        ]

    monkeypatch.setattr(frame, "PopupMenu", _popup)

    frame._show_tools_menu()

    labels = [label for label, _item_id in captured["items"]]
    assert "载入图片或文件" in labels


def test_input_key_down_ctrl_v_pastes_clipboard_attachments_when_input_has_focus(frame):
    seen = {"pasted": 0}
    frame.input_edit.SetFocus()
    frame._try_paste_clipboard_attachments_to_input = lambda: seen.__setitem__("pasted", seen["pasted"] + 1) or True

    class E:
        def GetKeyCode(self):
            return ord("V")

        def ControlDown(self):
            return True

        def AltDown(self):
            return False

        def Skip(self):
            seen["skipped"] = True

    frame._on_input_key_down(E())

    assert seen["pasted"] == 1


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
    monkeypatch.setattr(frame, "_render_answer_list", lambda: seen.__setitem__("render", seen["render"] + 1))
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


def test_char_hook_alt_c_suppresses_tools_menu_and_submits_continue(frame):
    seen = {"opened": 0, "submitted": 0}
    frame._alt_menu_armed = True
    frame._alt_menu_suppressed = False
    frame._show_tools_menu = lambda: seen.__setitem__("opened", seen["opened"] + 1)
    frame._submit_question = lambda question, **kwargs: seen.__setitem__("submitted", seen["submitted"] + 1) or (True, "")

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

    assert seen["opened"] == 0
    assert seen["submitted"] == 1
    assert frame._alt_menu_armed is True
    assert frame._alt_menu_suppressed is True


def test_input_key_up_alt_does_not_open_tools_menu_after_continue_shortcut(frame):
    seen = {"opened": 0}
    frame._alt_menu_armed = True
    frame._alt_menu_suppressed = True
    frame._show_tools_menu = lambda: seen.__setitem__("opened", seen["opened"] + 1)

    class E:
        def GetKeyCode(self):
            return wx.WXK_ALT

        def Skip(self):
            return None

    frame._on_input_key_up(E())

    assert seen["opened"] == 0
    assert frame._alt_menu_armed is False
    assert frame._alt_menu_suppressed is False


def test_input_key_down_alt_s_shortcut_suppresses_tools_menu_and_triggers_send(frame):
    seen = {"send": 0}
    frame._alt_menu_armed = True
    frame._alt_menu_suppressed = False
    frame._trigger_send = lambda: seen.__setitem__("send", seen["send"] + 1)

    class E:
        def GetKeyCode(self):
            return ord("S")

        def ControlDown(self):
            return False

        def AltDown(self):
            return True

        def Skip(self):
            return None

    frame._on_input_key_down(E())

    assert seen["send"] == 1
    assert frame._alt_menu_suppressed is True


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


def test_input_key_up_alt_does_not_open_tools_menu_after_other_key(frame):
    seen = {"opened": 0}
    frame._alt_menu_armed = True
    frame._alt_menu_suppressed = True
    frame._show_tools_menu = lambda: seen.__setitem__("opened", seen["opened"] + 1)

    class E:
        def GetKeyCode(self):
            return wx.WXK_ALT

        def Skip(self):
            return None

    frame._on_input_key_up(E())

    assert seen["opened"] == 0


def test_toggle_answer_filter_updates_state_and_rerenders_visible_answers(frame, monkeypatch):
    frame.codex_answer_english_filter_enabled = False
    frame.view_mode = "active"
    seen = {"save": 0, "render": 0, "remote": 0}
    monkeypatch.setattr(frame, "_save_state", lambda: seen.__setitem__("save", seen["save"] + 1))
    monkeypatch.setattr(frame, "_render_answer_list", lambda: seen.__setitem__("render", seen["render"] + 1))
    monkeypatch.setattr(frame, "_push_remote_state", lambda *_args, **_kwargs: seen.__setitem__("remote", seen["remote"] + 1))

    frame._toggle_codex_answer_filter()

    assert frame.codex_answer_english_filter_enabled is True
    assert seen == {"save": 1, "render": 1, "remote": 1}


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


def test_submit_question_sets_auto_title_from_first_question(frame, monkeypatch):
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
    assert frame._current_chat_state["title"] == "新聊天"


def test_submit_question_renames_placeholder_history_chat_immediately(frame, monkeypatch):
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
    assert list(frame.history_list.GetStrings()) == ["心聊天"]

    ok, message = frame._submit_question("帮我整理安卓自动化测试方案", source="local", model="openai/gpt-5.2")

    assert ok is True
    assert message == ""
    items = list(frame.history_list.GetStrings())
    assert items == ["心聊天"]


def test_next_default_chat_title_uses_xinliaotian_sequence(frame):
    frame.archived_chats = [
        {"id": "chat-a", "title": "心聊天", "turns": [], "created_at": 1.0, "updated_at": 1.0},
        {"id": "chat-b", "title": "心聊天1", "turns": [], "created_at": 2.0, "updated_at": 2.0},
    ]
    frame._current_chat_state["title"] = "别的标题"

    assert frame._next_default_chat_title() == "心聊天2"


def test_next_default_chat_title_treats_legacy_placeholder_as_default(frame):
    frame.archived_chats = [
        {"id": "chat-a", "title": "新聊天", "turns": [], "created_at": 1.0, "updated_at": 1.0},
        {"id": "chat-b", "title": "心聊天1", "turns": [], "created_at": 2.0, "updated_at": 2.0},
    ]

    assert frame._next_default_chat_title() == "心聊天2"


def test_refresh_history_normalizes_legacy_placeholder_title(frame):
    frame.archived_chats = [
        {"id": "chat-a", "title": "新聊天", "turns": [], "created_at": 1.0, "updated_at": 1.0},
    ]

    frame._refresh_history()

    assert list(frame.history_list.GetStrings()) == ["心聊天"]


def test_generate_first_question_title_retries_three_times_then_keeps_default(frame, monkeypatch):
    seen = {"calls": 0}

    class _FailingClient:
        def generate_chat_title(self, _prompt):
            seen["calls"] += 1
            return ""

    monkeypatch.setattr(main, "ChatClient", lambda *args, **kwargs: _FailingClient())

    title = frame._generate_first_question_title("帮我整理安卓自动化测试方案")

    assert title == "自动化测试"
    assert seen["calls"] == 3


def test_apply_generated_first_question_title_accepts_legacy_default_title(frame, monkeypatch):
    saved = []
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
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda chat_id="": pushed.append(chat_id))

    frame._apply_generated_first_question_title("chat-current", "帮我整理安卓自动化测试方案", "自动化测试")

    assert frame._current_chat_state["title"] == "自动化测试"
    assert frame._current_chat_state["title_source"] == "auto"
    assert saved == [True]
    assert pushed == ["chat-current"]


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


def test_resolve_chat_target_returns_current_chat_for_blank_id(frame):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state["id"] = "chat-current"
    frame._current_chat_state["title"] = "当前聊天"

    chat, is_current = frame._resolve_chat_target("")

    assert chat is frame._current_chat_state
    assert is_current is True


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


def test_shared_chat_title_rules_path_points_to_repo_assets():
    assert main.shared_chat_title_rules_path() == Path(r"c:\code\rc\assets\chat_title_rules.json")


def test_load_state_restores_notes_ui_state(frame, tmp_path):
    notebook = frame.notes_store.create_notebook("恢复测试")
    entry = frame.notes_store.create_entry(notebook.id, "保存过的草稿", source="manual")

    class _Editor:
        def __init__(self):
            self.value = ""
            self.cursor = None
            self.scroll = None
            self.scroll_get_calls = 0
            self.scroll_set_calls = []

        def SetValue(self, value):
            self.value = value

        def SetInsertionPoint(self, cursor):
            self.cursor = cursor

        def GetScrollPos(self, orientation):
            self.scroll_get_calls += 1
            return self.scroll

        def SetScrollPos(self, orientation, pos, refresh=True):
            self.scroll_set_calls.append((orientation, pos, refresh))
            self.scroll = pos

        def GetValue(self):
            return self.value

    frame.notes_editor = _Editor()
    frame.state_path = tmp_path / "app_state.json"
    frame._current_notes_state = {}
    frame.state_path.write_text(
        json.dumps(
            {
                "notes_ui_state": {
                    "active_root_tab": "notes",
                    "notes_view": "note_edit",
                    "active_notebook_id": notebook.id,
                    "active_entry_id": entry.id,
                    "entry_editor_draft": "未保存草稿",
                    "entry_editor_dirty": True,
                    "entry_editor_cursor": 2,
                    "entry_editor_scroll": 8,
                    "last_sync_cursor": "42",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    frame._load_state()

    assert frame._current_notes_state["active_root_tab"] == "notes"
    assert frame._current_notes_state["notes_view"] == "note_edit"
    assert frame._current_notes_state["entry_editor_draft"] == "未保存草稿"
    assert frame.notes_editor.value == "未保存草稿"
    assert frame.notes_editor.cursor == 2
    assert frame.notes_editor.scroll == 8
    assert frame.notes_editor.scroll_set_calls[-1][1] == 8
    assert frame._current_notes_state["last_sync_cursor"] == "42"


def test_load_state_restores_note_detail_view_for_existing_notebook(frame, tmp_path):
    notebook = frame.notes_store.create_notebook("detail restore notebook")
    entry = frame.notes_store.create_entry(notebook.id, "detail restore entry", source="manual")
    frame.state_path = tmp_path / "app_state.json"
    frame._current_notes_state = {}
    frame.state_path.write_text(
        json.dumps(
            {
                "notes_ui_state": {
                    "active_root_tab": "notes",
                    "notes_view": "note_detail",
                    "active_notebook_id": notebook.id,
                    "active_entry_id": entry.id,
                    "entry_editor_draft": "",
                    "entry_editor_dirty": False,
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    frame._load_state()

    assert frame._current_notes_state["active_root_tab"] == "notes"
    assert frame._current_notes_state["notes_view"] == "note_detail"
    assert frame.notes_controller.notes_view == "note_detail"
    assert frame.notes_controller.active_notebook_id == notebook.id
    assert frame.notes_controller.active_entry_id == entry.id


def test_save_state_captures_notes_editor_cursor_and_scroll(frame):
    class _Editor:
        def __init__(self):
            self.value = ""
            self.cursor = 0
            self.scroll = 0
            self.scroll_get_calls = 0
            self.scroll_set_calls = []

        def SetValue(self, value):
            self.value = value

        def GetValue(self):
            return self.value

        def SetInsertionPoint(self, cursor):
            self.cursor = cursor

        def GetInsertionPoint(self):
            return self.cursor

        def SetInsertionPointEnd(self):
            self.cursor = len(self.value)

        def GetScrollPos(self, orientation):
            self.scroll_get_calls += 1
            return self.scroll

        def SetScrollPos(self, orientation, pos, refresh=True):
            self.scroll_set_calls.append((orientation, pos, refresh))
            self.scroll = pos

    notebook = frame.notes_store.create_notebook("save cursor notebook")
    entry = frame.notes_store.create_entry(notebook.id, "save cursor entry", source="manual")
    frame.notes_editor = _Editor()
    frame._notes_select_entry(entry.id, view="note_edit")
    frame.notes_editor.value = "edited cursor draft"
    frame.notes_editor.cursor = 7
    frame.notes_editor.scroll = 13
    frame._on_notes_editor_changed(None)
    frame._save_state()

    saved_state = json.loads(frame.state_path.read_text(encoding="utf-8"))["notes_ui_state"]
    assert saved_state["entry_editor_draft"] == "edited cursor draft"
    assert saved_state["entry_editor_cursor"] == 7
    assert saved_state["entry_editor_scroll"] == 13


def test_notes_transport_broadcasts_conflict_and_sync_status(frame):
    seen = []

    class _Server:
        def broadcast_event(self, payload):
            seen.append(payload)

    frame._remote_ws_server = _Server()

    frame._push_remote_notes_conflict({"entry_id": "e1", "message": "conflict"})
    frame._push_remote_notes_sync_status("synced", cursor="44", message="ok")

    types = [item["type"] for item in seen]
    assert "notes_conflict" in types
    assert "notes_sync_status" in types


def test_remote_notes_apis_return_retired(frame):
    status, body = frame._remote_api_notes_snapshot(None)
    assert status == 410
    assert body["error"] == "retired"

    status, body = frame._remote_api_notes_pull_since({"cursor": "7"})
    assert status == 410
    assert body["error"] == "retired"

    status, body = frame._remote_api_notes_push_ops({"ops": [{"entity_type": "entry"}]})
    assert status == 410
    assert body["error"] == "retired"

    status, body = frame._remote_api_notes_subscribe({"cursor": "1"})
    assert status == 410
    assert body["error"] == "retired"

    status, body = frame._remote_api_notes_ack({"op_ids": ["op-1"]})
    assert status == 410
    assert body["error"] == "retired"

    status, body = frame._remote_api_notes_ping({"cursor": "9"})
    assert status == 410
    assert body["error"] == "retired"

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


def test_summarize_last_turn_locally_uses_first_question(frame):
    turns = [
        {"question": "多聊天 CLI 改造怎么设计", "answer_md": "先拆会话状态", "model": "codex/main", "created_at": time.time()},
        {"question": "再补手机端路由协议", "answer_md": "需要显式 chat_id", "model": "codex/main", "created_at": time.time()},
    ]
    title = frame._summarize_last_turn_locally(turns)
    assert "多聊天 CLI 改造"[:8] in title
    assert "手机端路由协议"[:6] not in title


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


def test_focus_latest_answer_skips_when_completion_window_is_not_foreground(frame, monkeypatch):
    frame.answer_meta = [("answer", 0, "小诸葛", ""), ("answer", 1, "小诸葛", "最新回答")]
    calls = {"selection": 0, "focus": 0}
    monkeypatch.setattr(frame, "_can_focus_completion_result", lambda: False)
    monkeypatch.setattr(
        frame.answer_list,
        "SetSelection",
        lambda *_args, **_kwargs: calls.__setitem__("selection", calls["selection"] + 1),
    )
    monkeypatch.setattr(
        frame.answer_list,
        "SetFocus",
        lambda *_args, **_kwargs: calls.__setitem__("focus", calls["focus"] + 1),
    )

    frame._focus_latest_answer()

    assert calls["selection"] == 0
    assert calls["focus"] == 0


def test_focus_latest_answer_only_focuses_when_completion_window_is_foreground(frame, monkeypatch):
    frame.answer_meta = [("answer", 0, "小诸葛", ""), ("answer", 1, "小诸葛", "最新回答")]
    calls = {"selection": 0, "focus": 0}
    monkeypatch.setattr(frame, "_can_focus_completion_result", lambda: True)
    monkeypatch.setattr(
        frame.answer_list,
        "SetSelection",
        lambda *_args, **_kwargs: calls.__setitem__("selection", calls["selection"] + 1),
    )
    monkeypatch.setattr(
        frame.answer_list,
        "SetFocus",
        lambda *_args, **_kwargs: calls.__setitem__("focus", calls["focus"] + 1),
    )

    frame._focus_latest_answer()

    assert calls["selection"] == 1
    assert calls["focus"] == 1


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


@pytest.mark.parametrize("model", ["codex/main", "claudecode/default", "openclaw/main"])
def test_answer_detail_html_filters_cli_models(frame, model):
    frame.codex_answer_english_filter_enabled = True

    codex_html = frame._build_answer_detail_html(
        "请查看 C:/code/codex/main.py 和 test_answer_detail_html_filters_cli_models",
        model,
    )
    assert "[文件路径]" in codex_html
    assert "main.py" not in codex_html
    assert "test_answer_detail_html_filters_cli_models" not in codex_html

    other_html = frame._build_answer_detail_html(
        "请查看 C:/code/codex/main.py 和 test_answer_detail_html_filters_cli_models",
        "openai/gpt-5.2",
    )
    assert "main.py" in other_html
    assert "test_answer_detail_html_filters_cli_models" in other_html


@pytest.mark.parametrize("model", ["codex/main", "claudecode/default", "openclaw/main"])
def test_remote_turn_payload_filters_cli_models(frame, model):
    frame.codex_answer_english_filter_enabled = True
    codex_turn = {
        "question": "测试",
        "answer_md": "参考 C:/code/codex/main.py 和 test_remote_turn_payload_filters_cli_models",
        "model": model,
        "created_at": 1.0,
    }
    other_turn = {
        "question": "测试",
        "answer_md": "参考 C:/code/codex/main.py 和 test_remote_turn_payload_filters_cli_models",
        "model": "openai/gpt-5.2",
        "created_at": 1.0,
    }

    codex_payload = frame._remote_turn_payload(codex_turn)
    other_payload = frame._remote_turn_payload(other_turn)

    assert "[文件路径]" in codex_payload["answer"]
    assert "main.py" not in codex_payload["answer"]
    assert "test_remote_turn_payload_filters_cli_models" not in codex_payload["answer"]
    assert "main.py" in other_payload["answer"]
    assert "test_remote_turn_payload_filters_cli_models" in other_payload["answer"]


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


@pytest.mark.parametrize("model", ["claudecode/default", "openclaw/main"])
def test_answer_filter_applies_to_other_cli_models(frame, model):
    frame.codex_answer_english_filter_enabled = True

    text = frame._answer_markdown_for_output(
        "请查看 [main.py](/c:/code/codex/main.py#L1098) 和 `tests/test_main_unit.py`",
        model,
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

    class _Server:
        def broadcast_event(self, payload):
            seen.append(payload)

    frame._remote_ws_server = _Server()

    frame._history_delete(None)

    assert any(payload.get("type") == "history_changed" for payload in seen)


def test_history_rename_broadcasts_remote_history_changed(frame, monkeypatch):
    frame.archived_chats = [
        {"id": "chat-old", "title": "旧聊天", "turns": [], "created_at": 1.0, "updated_at": 1.0, "pinned": False}
    ]
    frame.history_ids = ["chat-old"]
    frame.history_list.Set(["旧聊天"])
    frame.history_list.SetSelection(0)
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    seen = []

    class _Server:
        def broadcast_event(self, payload):
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

    frame._remote_ws_server = _Server()
    monkeypatch.setattr(main, "RenameDialog", _Dialog)

    frame._history_rename(None)

    assert any(payload.get("type") == "history_changed" for payload in seen)


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
    assert "doubao-2.0-pro" in main.MODEL_IDS
    assert "doubao-2.0-lite" in main.MODEL_IDS
    assert "doubao-2.0-mini" in main.MODEL_IDS
    assert "doubao-2.0-pro" in main.VISIBLE_MODEL_IDS
    assert "doubao-2.0-lite" in main.VISIBLE_MODEL_IDS
    assert "doubao-2.0-mini" in main.VISIBLE_MODEL_IDS


def test_first_run_default_model_is_codex(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "resolve_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(main.ChatFrame, "_legacy_state_paths", lambda self: [self.state_path])
    monkeypatch.setattr(main.ChatFrame, "_migrate_legacy_state_if_needed", lambda self: None)
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
    monkeypatch.setattr(main.ChatFrame, "_legacy_state_paths", lambda self: [self.state_path])
    monkeypatch.setattr(main.ChatFrame, "_migrate_legacy_state_if_needed", lambda self: None)
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
    monkeypatch.setattr(main.ChatFrame, "_legacy_state_paths", lambda self: [self.state_path])
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


def test_codex_worker_recovers_missing_thread_by_creating_new_one(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state["id"] = "chat-current"
    frame.active_session_turns = [
        {
            "question": "继续当前 Codex 对话",
            "answer_md": main.REQUESTING_TEXT,
            "model": "codex/main",
            "created_at": 1.0,
        }
    ]
    frame.active_turn_idx = 0
    frame._current_chat_state["codex_thread_id"] = "thread-old"
    frame._current_chat_state["codex_turn_id"] = "turn-old"
    frame._current_chat_state["codex_turn_active"] = False
    frame._current_chat_state["codex_pending_prompt"] = "stale prompt"
    frame._current_chat_state["codex_thread_flags"] = ["waitingOnUserInput"]
    frame.active_codex_thread_id = "thread-old"
    frame.active_codex_turn_id = "turn-old"
    frame.active_codex_turn_active = False
    frame.active_codex_pending_prompt = "stale prompt"
    frame.active_codex_thread_flags = ["waitingOnUserInput"]

    seen = {"start_thread": 0, "turns": []}

    class _Client:
        def start_thread(self, **kwargs):
            seen["start_thread"] += 1
            seen["thread_kwargs"] = kwargs
            return {"thread": {"id": "thread-new"}}

        def start_turn(self, thread_id, text):
            seen["turns"].append((thread_id, text))
            if len(seen["turns"]) == 1:
                raise RuntimeError("Codex app-server request failed: turn/start: thread not found: thread-old")
            return {"turn": {"id": "turn-new"}}

        def steer_turn(self, thread_id, expected_turn_id, text):
            raise AssertionError("should not steer when the turn is inactive")

    monkeypatch.setattr(frame, "_ensure_codex_client", lambda: _Client())
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *args, **kwargs: None)

    frame._run_codex_turn_worker("chat-current", 0, "新的 Codex 问题", "codex/main")

    assert seen["start_thread"] == 1
    assert seen["turns"] == [("thread-old", "新的 Codex 问题"), ("thread-new", "新的 Codex 问题")]
    assert frame.active_codex_thread_id == "thread-new"
    assert frame.active_codex_turn_id == "turn-new"
    assert frame.active_codex_turn_active is True
    assert frame.active_session_turns[0]["codex_thread_id"] == "thread-new"
    assert frame.active_session_turns[0]["codex_turn_id"] == "turn-new"


def test_codex_worker_resumes_existing_thread_before_new_turn_after_restart(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state["id"] = "chat-current"
    frame.active_session_turns = [
        {
            "question": "第一轮",
            "answer_md": "第一轮回答",
            "model": "codex/main",
            "created_at": 1.0,
        },
        {
            "question": "第二轮",
            "answer_md": main.REQUESTING_TEXT,
            "model": "codex/main",
            "created_at": 2.0,
        },
    ]
    frame.active_turn_idx = 1
    frame._current_chat_state["codex_thread_id"] = "thread-saved"
    frame.active_codex_thread_id = "thread-saved"
    frame.active_codex_turn_id = ""
    frame.active_codex_turn_active = False

    seen = {"resume": [], "turn": []}

    class _Client:
        def resume_thread(self, thread_id, **kwargs):
            seen["resume"].append((thread_id, kwargs))
            return {"thread": {"id": thread_id}}

        def start_turn(self, thread_id, text):
            seen["turn"].append((thread_id, text))
            return {"turn": {"id": "turn-new"}}

    monkeypatch.setattr(frame, "_ensure_codex_client", lambda: _Client())
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *args, **kwargs: None)

    frame._run_codex_turn_worker("chat-current", 1, "第三轮", "codex/main")

    assert seen["resume"] == [
        (
            "thread-saved",
            {
                "approval_policy": "never",
                "sandbox": "danger-full-access",
                "personality": "pragmatic",
                "cwd": frame._workspace_dir_for_codex(),
            },
        )
    ]
    assert seen["turn"] == [("thread-saved", "第三轮")]
    assert frame.active_codex_thread_id == "thread-saved"
    assert frame.active_codex_turn_id == "turn-new"


def test_codex_worker_rebuilds_context_when_saved_rollout_is_missing(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state["id"] = "chat-current"
    frame.active_session_turns = [
        {
            "question": "第一轮问题",
            "answer_md": "第一轮回答",
            "model": "codex/main",
            "created_at": 1.0,
        },
        {
            "question": "第二轮问题",
            "answer_md": "第二轮回答",
            "model": "codex/main",
            "created_at": 2.0,
        },
        {
            "question": "第三轮问题",
            "answer_md": main.REQUESTING_TEXT,
            "model": "codex/main",
            "created_at": 3.0,
        },
    ]
    frame.active_turn_idx = 2
    frame._current_chat_state["codex_thread_id"] = "thread-stale"
    frame.active_codex_thread_id = "thread-stale"
    frame.active_codex_turn_id = ""
    frame.active_codex_turn_active = False

    seen = {"resume": [], "start_thread": 0, "turn": []}

    class _Client:
        def resume_thread(self, thread_id, **kwargs):
            seen["resume"].append((thread_id, kwargs))
            raise RuntimeError("Codex app-server request failed: thread/resume: no rollout found for thread id thread-stale")

        def start_thread(self, **kwargs):
            seen["start_thread"] += 1
            return {"thread": {"id": "thread-new"}}

        def start_turn(self, thread_id, text):
            seen["turn"].append((thread_id, text))
            return {"turn": {"id": "turn-new"}}

    monkeypatch.setattr(frame, "_ensure_codex_client", lambda: _Client())
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *args, **kwargs: None)

    frame._run_codex_turn_worker("chat-current", 2, "第三轮问题", "codex/main")

    assert len(seen["resume"]) == 1
    assert seen["start_thread"] == 1
    assert seen["turn"][0][0] == "thread-new"
    assert "第一轮问题" in seen["turn"][0][1]
    assert "第一轮回答" in seen["turn"][0][1]
    assert "第二轮问题" in seen["turn"][0][1]
    assert "第三轮问题" in seen["turn"][0][1]
    assert frame.active_codex_thread_id == "thread-new"
    assert frame.active_codex_turn_id == "turn-new"


def test_codex_worker_sends_local_image_items_for_successful_attachments(frame, monkeypatch, tmp_path):
    image_path = tmp_path / "worker-image.png"
    image_path.write_text("img", encoding="utf-8")
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state["id"] = "chat-current"
    frame.active_session_turns = [
        {
            "question": "worker-image.png 图片已成功上传",
            "answer_md": main.REQUESTING_TEXT,
            "model": "codex/main",
            "created_at": 1.0,
            "attachments": [
                {
                    "name": image_path.name,
                    "path": str(image_path),
                    "kind": "image",
                    "direction": "outgoing",
                    "status": "success",
                    "open_path": str(image_path),
                }
            ],
        }
    ]
    frame.active_turn_idx = 0
    seen = {"items": []}

    class _Client:
        def start_thread(self, **_kwargs):
            return {"thread": {"id": "thread-new"}}

        def start_turn_items(self, thread_id, items):
            seen["items"].append((thread_id, items))
            return {"turn": {"id": "turn-new"}}

    monkeypatch.setattr(frame, "_ensure_codex_client", lambda: _Client())
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *args, **kwargs: None)

    frame._run_codex_turn_worker("chat-current", 0, "", "codex/main")

    assert seen["items"] == [
        (
            "thread-new",
            [{"type": "localImage", "path": str(image_path)}],
        )
    ]


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
    assert main.model_display_name("openclaw/main") == "openclaw"
    assert main.model_display_name("codex/main") == "codex"
    assert main.model_display_name("claudecode/default") == "claudeCode"
    assert main.model_id_from_display_name("openclaw") == "openclaw/main"
    assert main.model_id_from_display_name("codex") == "codex/main"
    assert main.model_id_from_display_name("claudeCode") == "claudecode/default"


def test_model_combo_shows_display_names(frame):
    choices = list(frame.model_combo.GetItems())
    assert "openclaw" in choices
    assert "codex" in choices
    assert "claudeCode" in choices
    assert "openclaw/main" not in choices
    assert "codex/main" not in choices
    assert "claudecode/default" not in choices


def test_resolve_current_model_uses_cached_value_off_main_thread(frame, monkeypatch):
    frame.selected_model = "codex/main"

    class _ForbiddenCombo:
        def GetStringSelection(self):
            raise AssertionError("background thread should not touch combobox")

        def GetSelection(self):
            raise AssertionError("background thread should not touch combobox")

        def GetValue(self):
            raise AssertionError("background thread should not touch combobox")

    monkeypatch.setattr(frame, "model_combo", _ForbiddenCombo())

    result = {}

    def _worker():
        result["value"] = frame._resolve_current_model()

    thread = threading.Thread(target=_worker)
    thread.start()
    thread.join(timeout=5)

    assert thread.is_alive() is False
    assert result["value"] == "codex/main"


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


def test_codex_ui_callbacks_skip_when_frame_is_being_deleted(frame, monkeypatch):
    frame.active_chat_id = "chat-1"
    frame.current_chat_id = "chat-1"
    frame.view_mode = "active"
    frame.active_session_turns = [{"question": "q", "answer_md": "", "model": "codex/main", "created_at": 1.0}]
    frame.active_turn_idx = 0
    frame.active_session_turns[0]["request_status"] = "pending"
    frame.active_session_turns[0]["request_recoverable"] = True

    scheduled = []
    monkeypatch.setattr(frame, "IsBeingDeleted", lambda: True, raising=False)
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *args, **kwargs: scheduled.append(("after", fn.__name__)))
    monkeypatch.setattr(main.wx, "CallLater", lambda delay, fn, *args, **kwargs: scheduled.append(("later", delay, fn.__name__)) or type("T", (), {"IsRunning": lambda self: False, "Stop": lambda self: None})())
    monkeypatch.setattr(frame, "_render_answer_list", lambda: scheduled.append(("render",)))
    monkeypatch.setattr(frame, "_save_state", lambda: scheduled.append(("save",)))
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: scheduled.append(("sound",)))
    monkeypatch.setattr(frame, "_push_remote_state", lambda *args, **kwargs: scheduled.append(("remote",)))
    monkeypatch.setattr(frame, "SetStatusText", lambda *args, **kwargs: scheduled.append(("status",)))

    frame._dispatch_codex_event_to_ui("chat-1", main.CodexEvent(type="agent_message_delta", text="x"))
    frame._queue_answer_char_redirect("x")
    frame._on_done(0, "完成", "", "codex/main", "", "chat-1")

    assert all(item[0] not in {"after", "later"} for item in scheduled)
    assert scheduled == [("status",), ("remote",), ("save",), ("render",), ("sound",)]

def test_resolve_app_data_dir_frozen_uses_executable_sibling_history(monkeypatch):
    monkeypatch.setattr(main.sys, "frozen", True, raising=False)
    monkeypatch.setattr(main.sys, "executable", r"C:\code\cx\ms\ms.exe", raising=False)
    p = main.resolve_app_data_dir()
    assert p == Path(r"C:\code\cx\history")


def test_wx_call_later_if_alive_skips_when_top_window_handle_is_invalid(monkeypatch):
    class _TopWindow:
        def IsBeingDeleted(self):
            return False

        def GetHandle(self):
            return 0

    class _App:
        def GetTopWindow(self):
            return _TopWindow()

    called = {"later": False}
    monkeypatch.setattr(main.wx, "GetApp", lambda: _App())
    monkeypatch.setattr(main.wx, "CallLater", lambda *args, **kwargs: called.__setitem__("later", True))

    result = main.wx_call_later_if_alive(120, lambda: None)

    assert result is None
    assert called["later"] is False


def test_wx_call_after_if_alive_skips_when_top_window_handle_is_invalid(monkeypatch):
    class _TopWindow:
        def IsBeingDeleted(self):
            return False

        def GetHandle(self):
            return 0

    class _App:
        def GetTopWindow(self):
            return _TopWindow()

    called = {"after": False}
    monkeypatch.setattr(main.wx, "GetApp", lambda: _App())
    monkeypatch.setattr(main.wx, "CallAfter", lambda *args, **kwargs: called.__setitem__("after", True))

    result = main.wx_call_after_if_alive(lambda: None)

    assert result is False
    assert called["after"] is False


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



