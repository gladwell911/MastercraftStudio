import asyncio
import time

from aiohttp import ClientSession

import main
from codex_client import CodexEvent


async def _ws_connect(port: int, token: str):
    session = ClientSession()
    ws = await session.ws_connect(f"http://127.0.0.1:{port}/ws?token={token}")
    return session, ws


async def _wait_for_type(ws, event_type: str, timeout: float = 5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = await ws.receive(timeout=timeout)
        data = msg.json()
        if data.get("type") == event_type:
            return data
    raise AssertionError(f"did not receive event type {event_type}")


def test_remote_ws_message_routes_to_submit(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_TOKEN", "secret")
    monkeypatch.setenv("REMOTE_CONTROL_PORT", "0")
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))
    frame.selected_model = "codex/main"
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state["id"] = "chat-current"
    seen = {}
    monkeypatch.setattr(
        frame,
        "_submit_question",
        lambda question, **kwargs: seen.setdefault("call", (question, kwargs)) or (True, ""),
    )

    frame._start_remote_ws_server_if_configured()
    try:
        async def _run():
            session, ws = await _ws_connect(frame._remote_ws_server.bound_port, "secret")
            try:
                connected = await _wait_for_type(ws, "connected")
                assert connected["body"]["accepted"] is True
                assert "last_event_id" in connected["body"]
                await ws.send_json({"id": "m1", "type": "message", "text": "hello remote"})
                response = await _wait_for_type(ws, "response")
                assert response["id"] == "m1"
                assert response["ok"] is True
            finally:
                await ws.close()
                await session.close()

        asyncio.run(_run())
        question, kwargs = seen["call"]
        assert question == "hello remote"
        assert kwargs["source"] == "remote-ws"
        assert kwargs["model"] == "codex/main"
    finally:
        frame._remote_ws_server.stop()
        frame._remote_ws_server = None


def test_remote_message_with_chat_id_switches_target_chat(frame, monkeypatch):
    frame.active_chat_id = "chat-a"
    frame.active_session_turns = [{"question": "A", "answer_md": "AA", "model": "codex/main", "created_at": time.time()}]
    frame.archived_chats = [
        {"id": "chat-b", "title": "聊天B", "turns": [], "created_at": time.time(), "updated_at": time.time(), "pinned": False}
    ]
    seen = {}
    monkeypatch.setattr(
        frame,
        "_submit_question",
        lambda question, **kwargs: (seen.setdefault("call", (question, kwargs, frame.current_chat_id)), (True, ""))[1],
    )
    status, body = frame._remote_api_message_ui({"chat_id": "chat-b", "text": "hello B"})

    assert status == 200
    assert body["accepted"] is True
    assert body["chat_id"] == "chat-b"
    question, kwargs, current_chat_id = seen["call"]
    assert question == "hello B"
    assert kwargs["chat_id"] == "chat-b"
    assert current_chat_id == "chat-b"


def test_remote_message_with_chat_id_switches_archived_context_off_main_thread(
    frame, monkeypatch
):
    frame.active_chat_id = "chat-a"
    frame.current_chat_id = "chat-a"
    frame.active_session_turns = [
        {
            "question": "当前问题A",
            "answer_md": "当前回答A",
            "model": "codex/main",
            "created_at": time.time(),
        }
    ]
    frame._current_chat_state["id"] = "chat-a"
    frame._current_chat_state["turns"] = frame.active_session_turns
    frame.archived_chats = [
        {
            "id": "chat-b",
            "title": "聊天B",
            "turns": [
                {
                    "question": "历史问题B",
                    "answer_md": "历史回答B",
                    "model": "codex/main",
                    "created_at": time.time(),
                }
            ],
            "created_at": time.time(),
            "updated_at": time.time(),
            "pinned": False,
        }
    ]
    fake_thread = object()
    main_thread = object()
    monkeypatch.setattr(main.threading, "current_thread", lambda: fake_thread)
    monkeypatch.setattr(main.threading, "main_thread", lambda: main_thread)
    seen = {}
    monkeypatch.setattr(
        frame,
        "_submit_question",
        lambda question, **kwargs: (
            seen.setdefault(
                "call",
                (
                    question,
                    kwargs,
                    frame.current_chat_id,
                    frame.active_session_turns[0]["question"],
                ),
            ),
            (True, ""),
        )[1],
    )

    status, body = frame._remote_api_message_ui({"chat_id": "chat-b", "text": "hello B"})

    assert status == 200
    assert body["accepted"] is True
    assert body["chat_id"] == "chat-b"
    question, kwargs, current_chat_id, loaded_question = seen["call"]
    assert question == "hello B"
    assert kwargs["chat_id"] == "chat-b"
    assert current_chat_id == "chat-b"
    assert loaded_question == "历史问题B"


def test_remote_new_chat_while_running_returns_fresh_chat_id(frame, monkeypatch):
    frame.is_running = True
    frame.active_chat_id = "chat-a"
    frame.current_chat_id = "chat-a"
    frame.active_session_turns = [
        {
            "question": "A",
            "answer_md": "AA",
            "model": "codex/main",
            "created_at": time.time(),
        }
    ]
    frame._current_chat_state["id"] = "chat-a"
    frame._current_chat_state["turns"] = frame.active_session_turns
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_render_answer_list", lambda: None)
    monkeypatch.setattr(frame, "_refresh_history", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_schedule_async_archive_rename", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame.input_edit, "SetFocus", lambda: None)
    statuses = []
    monkeypatch.setattr(frame, "SetStatusText", statuses.append)
    monkeypatch.setattr(main.wx, "MessageBox", lambda *_args, **_kwargs: None)

    status, body = frame._remote_api_new_chat_ui({"model": "codex/main"})

    assert status == 200
    assert body["accepted"] is True
    assert body["chat_id"]
    assert body["chat_id"] != "chat-a"
    assert frame.active_chat_id == body["chat_id"]
    assert any(str(chat.get("id") or "") == "chat-a" for chat in frame.archived_chats)
    assert statuses[-1] == "已开始远程新聊天"


def test_remote_ws_state_reports_pending_request(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_TOKEN", "secret")
    monkeypatch.setenv("REMOTE_CONTROL_PORT", "0")
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))
    frame.codex_answer_english_filter_enabled = False
    frame.active_codex_pending_request = {
        "request_id": 1,
        "method": "item/tool/requestUserInput",
        "params": {"questions": [{"id": "q1", "header": "问题", "question": "请选择"}]},
    }

    frame._start_remote_ws_server_if_configured()
    try:
        async def _run():
            session, ws = await _ws_connect(frame._remote_ws_server.bound_port, "secret")
            try:
                connected = await _wait_for_type(ws, "connected")
                payload = connected["body"]
                assert payload["status"] == "waiting_user_input"
                assert payload["request_kind"] == "user_input"
                assert payload["settings"]["codex_answer_english_filter_enabled"] is False
                assert "last_event_id" in payload
                await ws.send_json({"id": "s1", "type": "state"})
                response = await _wait_for_type(ws, "response")
                assert response["id"] == "s1"
                assert response["body"]["status"] == "waiting_user_input"
            finally:
                await ws.close()
                await session.close()

        asyncio.run(_run())
    finally:
        frame._remote_ws_server.stop()
        frame._remote_ws_server = None


def test_remote_ws_connected_restores_current_session_snapshot(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_TOKEN", "secret")
    monkeypatch.setenv("REMOTE_CONTROL_PORT", "0")
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))
    frame.active_chat_id = "chat-1"
    frame.current_chat_id = "chat-1"
    frame._current_chat_state["id"] = "chat-1"
    frame.active_session_turns = [
        {
            "question": "历史问题",
            "answer_md": "历史回答",
            "model": "codex/main",
            "created_at": time.time(),
        },
        {
            "question": "进行中的问题",
            "answer_md": main.REQUESTING_TEXT,
            "model": "codex/main",
            "created_at": time.time(),
        },
    ]

    frame._start_remote_ws_server_if_configured()
    try:
        async def _run():
            session, ws = await _ws_connect(frame._remote_ws_server.bound_port, "secret")
            try:
                connected = await _wait_for_type(ws, "connected")
                body = connected["body"]
                assert body["chat_id"] == "chat-1"
                assert "last_event_id" in body
                assert len(body["turns"]) == 2
                assert body["turns"][0]["question"] == "历史问题"
                assert body["turns"][0]["answer"] == "历史回答"
                assert body["turns"][1]["question"] == "进行中的问题"
                assert body["turns"][1]["pending"] is True
            finally:
                await ws.close()
                await session.close()

        asyncio.run(_run())
    finally:
        frame._remote_ws_server.stop()
        frame._remote_ws_server = None


def test_remote_turn_payload_marks_assistant_only(frame):
    payload = frame._remote_turn_payload(
        {
            "question": "",
            "answer_md": "只有回答",
            "model": "codex/main",
            "created_at": time.time(),
        }
    )

    assert payload["assistant_only"] is True
    assert payload["question"] == ""
    assert payload["answer"] == "只有回答"


def test_remote_ws_history_list_and_read(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_TOKEN", "secret")
    monkeypatch.setenv("REMOTE_CONTROL_PORT", "0")
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))
    frame.active_chat_id = "active-1"
    frame.current_chat_id = "active-1"
    frame.active_session_turns = [
        {"question": "当前问题", "answer_md": "当前回答", "model": "codex/main", "created_at": time.time()}
    ]
    frame._current_chat_state["id"] = "active-1"
    frame._current_chat_state["model"] = "codex/main"
    frame._current_chat_state["turns"] = frame.active_session_turns
    frame.archived_chats = [
        {
            "id": "arch-1",
            "title": "归档会话",
            "created_at": time.time(),
            "updated_at": time.time(),
            "turns": [{"question": "历史问题", "answer_md": "历史回答", "model": "codex/main", "created_at": time.time()}],
            "pinned": False,
        }
    ]

    frame._start_remote_ws_server_if_configured()
    try:
        async def _run():
            session, ws = await _ws_connect(frame._remote_ws_server.bound_port, "secret")
            try:
                await _wait_for_type(ws, "connected")

                await ws.send_json({"id": "hl1", "type": "history_list"})
                response = await _wait_for_type(ws, "response")
                assert response["id"] == "hl1"
                chats = response["body"]["chats"]
                assert chats[0]["chat_id"] == "active-1"
                assert chats[0]["model"] == "codex/main"
                assert chats[0]["current"] is True
                assert chats[0]["active"] is True
                assert "created_at" in chats[0]
                assert "updated_at" in chats[0]
                assert "turn_count" in chats[0]
                assert "pinned" in chats[0]
                assert any(chat["chat_id"] == "arch-1" for chat in chats)

                await ws.send_json({"id": "hr1", "type": "history_read", "chat_id": "arch-1"})
                response = await _wait_for_type(ws, "response")
                assert response["id"] == "hr1"
                chat = response["body"]["chat"]
                assert chat["chat_id"] == "arch-1"
                assert chat["turns"][0]["question"] == "历史问题"
                assert chat["turns"][0]["answer"] == "历史回答"
            finally:
                await ws.close()
                await session.close()

        asyncio.run(_run())
    finally:
        frame._remote_ws_server.stop()
        frame._remote_ws_server = None


def test_remote_history_read_current_chat_uses_manual_title(frame):
    frame.active_chat_id = "active-1"
    frame._current_chat_state["title"] = "手动标题"
    frame._current_chat_state["title_manual"] = True
    frame.active_session_turns = [
        {"question": "", "answer_md": "只有回答", "model": "codex/main", "created_at": time.time()}
    ]

    status, body = frame._remote_api_history_read_ui({"chat_id": "active-1"})

    assert status == 200
    assert body["chat"]["title"] == "手动标题"
    assert body["chat"]["turns"][0]["assistant_only"] is True


def test_remote_state_returns_manual_current_title_and_assistant_only(frame):
    frame.active_chat_id = "active-1"
    frame._current_chat_state["title"] = "手动标题"
    frame._current_chat_state["title_manual"] = True
    frame.active_session_turns = [
        {"question": "", "answer_md": "只有回答", "model": "codex/main", "created_at": time.time()}
    ]

    status, body = frame._remote_api_state_ui({"chat_id": "active-1"})

    assert status == 200
    assert body["title"] == "手动标题"
    assert body["turns"][0]["assistant_only"] is True


def test_remote_api_update_settings_toggles_codex_answer_filter(frame):
    frame.codex_answer_english_filter_enabled = False

    status, body = frame._remote_api_update_settings_ui({"codex_answer_english_filter_enabled": True})

    assert status == 200
    assert body["accepted"] is True
    assert body["settings"]["codex_answer_english_filter_enabled"] is True
    assert frame.codex_answer_english_filter_enabled is True


def test_remote_api_state_includes_codex_answer_filter_setting(frame):
    frame.codex_answer_english_filter_enabled = True

    status, body = frame._remote_api_state_ui({"chat_id": frame.current_chat_id})

    assert status == 200
    assert body["settings"]["codex_answer_english_filter_enabled"] is True


def test_remote_ws_reply_request_handles_pending(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_TOKEN", "secret")
    monkeypatch.setenv("REMOTE_CONTROL_PORT", "0")
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))
    seen = {}

    def _handle(text):
        seen["text"] = text
        return True, ""

    monkeypatch.setattr(frame, "_handle_remote_pending_request_reply", _handle)
    frame.active_codex_pending_request = {"request_id": 1, "method": "item/tool/requestUserInput", "params": {}}

    frame._start_remote_ws_server_if_configured()
    try:
        async def _run():
            session, ws = await _ws_connect(frame._remote_ws_server.bound_port, "secret")
            try:
                await _wait_for_type(ws, "connected")
                await ws.send_json({"id": "r1", "type": "reply_request", "text": "reply text"})
                response = await _wait_for_type(ws, "response")
                assert response["id"] == "r1"
                assert response["ok"] is True
            finally:
                await ws.close()
                await session.close()

        asyncio.run(_run())
        assert seen["text"] == "reply text"
    finally:
        frame._remote_ws_server.stop()
        frame._remote_ws_server = None


def test_remote_ws_pushes_final_answer(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_TOKEN", "secret")
    monkeypatch.setenv("REMOTE_CONTROL_PORT", "0")
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))
    frame.active_session_turns = [
        {
            "question": "远程问题",
            "answer_md": main.REQUESTING_TEXT,
            "model": "codex/main",
            "created_at": time.time(),
        }
    ]

    frame._start_remote_ws_server_if_configured()
    try:
        async def _run():
            session, ws = await _ws_connect(frame._remote_ws_server.bound_port, "secret")
            try:
                await _wait_for_type(ws, "connected")
                frame._on_codex_event(
                    CodexEvent(type="item_completed", status="agentMessage", phase="final_answer", text="远程回答")
                )
                pushed = await _wait_for_type(ws, "final_answer")
                assert pushed["text"] == "远程回答"
                assert pushed["chat_id"] == frame.current_chat_id
                assert pushed["event_id"].startswith("evt-")
                assert "ts" in pushed
            finally:
                await ws.close()
                await session.close()

        asyncio.run(_run())
    finally:
        frame._remote_ws_server.stop()
        frame._remote_ws_server = None


def test_remote_ws_question_and_answer_sync_to_local_answer_list(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_TOKEN", "secret")
    monkeypatch.setenv("REMOTE_CONTROL_PORT", "0")
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(frame, "_worker", lambda *args, **kwargs: None)

    ok, message = frame._remote_api_message_ui({"text": "远程问题"})

    assert ok == 200
    assert message["accepted"] is True

    frame._on_codex_event(CodexEvent(type="item_completed", status="agentMessage", phase="final_answer", text="远程回答"))

    items = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert any("远程问题" == item for item in items)
    assert any("远程回答" == item for item in items)


def test_remote_api_first_message_sets_title_from_first_question(frame, monkeypatch):
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_render_answer_list", lambda: None)
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    frame.active_session_turns = []
    frame._current_chat_state = {"id": "chat-current", "title": "新聊天", "title_manual": False, "turns": frame.active_session_turns}
    pushed = []
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda chat_id="": pushed.append(chat_id))

    class _NoOpThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}

        def start(self):
            return None

    monkeypatch.setattr(main.threading, "Thread", _NoOpThread)

    status, body = frame._remote_api_message_ui({"text": "帮我梳理桌面端聊天命名规则"})

    assert status == 200
    assert body["accepted"] is True
    assert frame._current_chat_state["title"] != "新聊天"
    assert "桌面端聊天命名规则"[:8] in frame._current_chat_state["title"]
    assert pushed
