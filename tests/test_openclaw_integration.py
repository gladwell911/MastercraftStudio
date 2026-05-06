import json
import time

import main


def test_model_combo_contains_openclaw(frame):
    choices = [frame.model_combo.GetString(i) for i in range(frame.model_combo.GetCount())]
    assert "openclaw" in choices
    assert "codex" in choices
    assert "claudeCode" in choices


def test_load_state_restores_openclaw_sync_state(monkeypatch, tmp_path):
    state = {
        "selected_model_id": "openclaw/main",
        "archived_chats": [],
        "active_session_turns": [
            {
                "question": "浣犲ソ",
                "answer_md": "涓栫晫",
                "model": "openclaw/main",
                "created_at": time.time(),
            }
        ],
        "active_chat_id": "chat-123",
        "active_openclaw_session_key": "agent:main:main",
        "active_openclaw_session_id": "zgwd-chat-123",
        "active_openclaw_session_file": r"C:\tmp\main.jsonl",
        "active_openclaw_sync_offset": 321,
        "active_openclaw_last_event_id": "evt-9",
        "active_openclaw_last_synced_at": 123.0,
        "active_session_started_at": 1.0,
    }
    (tmp_path / "app_state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(main, "resolve_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(main.ChatFrame, "_refresh_openclaw_sync_lifecycle", lambda self, force_replay=False: None)
    loaded = main.ChatFrame()
    try:
        assert loaded.selected_model == "openclaw/main"
        assert loaded.active_chat_id == "chat-123"
        assert loaded.active_openclaw_session_key == "agent:main:main"
        assert loaded.active_openclaw_session_id == "zgwd-chat-123"
        assert loaded.active_openclaw_session_file == r"C:\tmp\main.jsonl"
        assert loaded.active_openclaw_sync_offset == 321
        assert loaded.active_openclaw_last_event_id == "evt-9"
    finally:
        loaded.Destroy()


def test_worker_routes_openclaw_without_overwriting_pending_answer(frame, monkeypatch):
    frame._stop_openclaw_sync()
    frame.active_session_turns = [
        {
            "question": "\u8bf7\u6267\u884c",
            "answer_md": "",
            "model": "openclaw/main",
            "created_at": time.time(),
        }
    ]
    frame.active_chat_id = "chat-001"
    frame.active_openclaw_session_id = "zgwd-chat-001"
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda force_replay=False: None)
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))

    seen = {}

    def fake_stream_chat(self, user_text, session_id, on_delta=None):
        seen["question"] = user_text
        seen["session_id"] = session_id
        return "OpenClaw done"

    monkeypatch.setattr(main.OpenClawClient, "stream_chat", fake_stream_chat)
    frame._worker("", 0, "\u8bf7\u6267\u884c", "openclaw/main")
    assert seen == {"question": "\u8bf7\u6267\u884c", "session_id": "zgwd-chat-001"}
    assert frame.active_session_turns[0]["answer_md"] == ""


def test_openclaw_worker_uses_submitted_chat_session_after_switch(frame, monkeypatch):
    frame._stop_openclaw_sync()
    frame.selected_model = "openclaw/main"
    frame.model_combo.SetValue("openclaw")
    frame.active_chat_id = "chat-a"
    frame.current_chat_id = "chat-a"
    frame.active_openclaw_session_id = "zgwd-chat-a"
    frame.active_session_turns = []
    frame._current_chat_state = {"id": "chat-a", "title": "chat a", "turns": frame.active_session_turns}
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda force_replay=False: None)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))

    seen = []

    def fake_stream_chat(self, user_text, session_id, on_delta=None):
        seen.append((user_text, session_id))
        return ""

    class _SwitchBeforeWorkerThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}

        def start(self):
            frame.active_chat_id = "chat-b"
            frame.current_chat_id = "chat-b"
            frame.active_openclaw_session_id = "zgwd-chat-b"
            if self._target:
                self._target(*self._args, **self._kwargs)

    monkeypatch.setattr(main.threading, "Thread", _SwitchBeforeWorkerThread)
    monkeypatch.setattr(main.OpenClawClient, "stream_chat", fake_stream_chat)

    ok, message = frame._submit_question("draw a", model="openclaw/main")

    assert ok is True
    assert message == ""
    assert seen == [("draw a", "zgwd-chat-a")]


def test_openclaw_done_does_not_render_placeholder_before_sync(frame, monkeypatch):
    frame._stop_openclaw_sync()
    frame.active_session_turns = [
        {
            "question": "\u6253\u5f00\u63a7\u5236\u53f0",
            "answer_md": "",
            "model": "openclaw/main",
            "created_at": time.time(),
        }
    ]
    rendered = {"n": 0}
    monkeypatch.setattr(frame, "_render_answer_list", lambda: rendered.__setitem__("n", rendered["n"] + 1))
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)
    frame._on_done(0, "", "", "openclaw/main", "")
    assert rendered["n"] == 0
    assert frame.active_session_turns[0]["answer_md"] == ""


def test_apply_openclaw_sync_batch_merges_local_user_and_fills_reply(frame):
    frame._stop_openclaw_sync()
    frame.active_session_turns = [
        {
            "question": "\u6253\u5f00\u63a7\u5236\u53f0",
            "answer_md": main.REQUESTING_TEXT,
            "model": "openclaw/main",
            "created_at": 100.0,
            "origin": "local",
            "question_origin": "local",
        }
    ]
    events = [
        main.OpenClawSyncEvent(event_id="u1", role="user", text="\u6253\u5f00\u63a7\u5236\u53f0", timestamp=101.0),
        main.OpenClawSyncEvent(event_id="a1", role="assistant", text="opened", timestamp=102.0),
    ]
    frame._apply_openclaw_sync_batch(
        {
            "session_id": "zgwd-chat-001",
            "session_file": r"C:\tmp\main.jsonl",
            "offset": 200,
            "updated_at": 103.0,
            "file_changed": True,
            "session_changed": False,
        },
        events,
    )
    assert len(frame.active_session_turns) == 1
    turn = frame.active_session_turns[0]
    assert turn["question_external_event_id"] == "u1"
    assert turn["answer_external_event_id"] == "a1"
    assert turn["answer_md"] == "opened"
    assert frame.active_openclaw_session_id == "zgwd-chat-001"
    assert frame.active_openclaw_session_file == r"C:\tmp\main.jsonl"
    assert frame.active_openclaw_sync_offset == 200


def test_sync_once_switches_to_new_session_file(frame, monkeypatch, tmp_path):
    sessions_dir = tmp_path / ".openclaw" / "agents" / "main" / "sessions"
    sessions_dir.mkdir(parents=True)
    session_file = sessions_dir / "main-2.jsonl"
    session_file.write_text(
        json.dumps(
            {
                "type": "message",
                "id": "a2",
                "timestamp": "2026-03-16T02:37:04.505Z",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "鍚屾鎴愬姛"}]},
            }
        ),
        encoding="utf-8",
    )
    (sessions_dir / "sessions.json").write_text(
        json.dumps(
            {
                "agent:main:main": {
                    "sessionId": "zgwd-new",
                    "sessionFile": str(session_file),
                    "updatedAt": 1773672820158,
                }
            }
        ),
        encoding="utf-8",
    )
    frame.selected_model = "openclaw/main"
    frame.active_session_turns = []
    frame.active_openclaw_session_file = str(sessions_dir / "old.jsonl")
    frame.active_openclaw_sync_offset = 999
    monkeypatch.setattr(main, "resolve_openclaw_sessions_dir", lambda _agent="main": sessions_dir)
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))
    frame._sync_openclaw_once()
    assert frame.active_openclaw_session_id == "zgwd-new"
    assert frame.active_openclaw_session_file == str(session_file)
    assert frame.active_session_turns[-1]["answer_md"] == "鍚屾鎴愬姛"


def test_sync_once_uses_active_chat_session_file_over_global_pointer(frame, monkeypatch, tmp_path):
    sessions_dir = tmp_path / ".openclaw" / "agents" / "main" / "sessions"
    sessions_dir.mkdir(parents=True)
    active_file = sessions_dir / "chat-a.jsonl"
    active_file.write_text(
        json.dumps(
            {
                "type": "message",
                "id": "a-chat-a",
                "timestamp": time.time(),
                "message": {"role": "assistant", "content": [{"type": "text", "text": "chat a reply"}]},
            }
        ),
        encoding="utf-8",
    )
    global_file = sessions_dir / "chat-b.jsonl"
    global_file.write_text(
        json.dumps(
            {
                "type": "message",
                "id": "a-chat-b",
                "timestamp": time.time(),
                "message": {"role": "assistant", "content": [{"type": "text", "text": "chat b reply"}]},
            }
        ),
        encoding="utf-8",
    )
    (sessions_dir / "sessions.json").write_text(
        json.dumps(
            {
                "agent:main:main": {
                    "sessionId": "zgwd-chat-b",
                    "sessionFile": str(global_file),
                    "updatedAt": 1773672820158,
                }
            }
        ),
        encoding="utf-8",
    )
    frame.selected_model = "openclaw/main"
    frame.active_session_turns = []
    frame.active_openclaw_session_id = "zgwd-chat-a"
    frame.active_openclaw_session_file = str(active_file)
    frame.active_openclaw_sync_offset = 0
    monkeypatch.setattr(main, "resolve_openclaw_sessions_dir", lambda _agent="main": sessions_dir)
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)

    frame._sync_openclaw_once()

    assert frame.active_openclaw_session_id == "zgwd-chat-a"
    assert frame.active_openclaw_session_file == str(active_file)
    assert frame.active_session_turns[-1]["answer_md"] == "chat a reply"


def test_sync_once_finds_session_file_by_active_session_id(frame, monkeypatch, tmp_path):
    sessions_dir = tmp_path / ".openclaw" / "agents" / "main" / "sessions"
    sessions_dir.mkdir(parents=True)
    active_file = sessions_dir / "chat-c.jsonl"
    active_file.write_text(
        json.dumps(
            {
                "type": "message",
                "id": "a-chat-c",
                "timestamp": time.time(),
                "message": {"role": "assistant", "content": [{"type": "text", "text": "chat c reply"}]},
            }
        ),
        encoding="utf-8",
    )
    global_file = sessions_dir / "main.jsonl"
    global_file.write_text("", encoding="utf-8")
    (sessions_dir / "sessions.json").write_text(
        json.dumps(
            {
                "agent:main:main": {
                    "sessionId": "zgwd-main",
                    "sessionFile": str(global_file),
                    "updatedAt": 1773672820158,
                },
                "agent:main:webchat:c": {
                    "sessionId": "zgwd-chat-c",
                    "sessionFile": str(active_file),
                    "updatedAt": 1773672821999,
                },
            }
        ),
        encoding="utf-8",
    )
    frame.selected_model = "openclaw/main"
    frame.active_session_turns = []
    frame.active_openclaw_session_id = "zgwd-chat-c"
    frame.active_openclaw_session_file = ""
    frame.active_openclaw_sync_offset = 0
    monkeypatch.setattr(main, "resolve_openclaw_sessions_dir", lambda _agent="main": sessions_dir)
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)

    frame._sync_openclaw_once()

    assert frame.active_openclaw_session_id == "zgwd-chat-c"
    assert frame.active_openclaw_session_file == str(active_file)
    assert frame.active_session_turns[-1]["answer_md"] == "chat c reply"


def test_new_openclaw_chat_sync_ignores_stale_events_before_local_turn(frame, monkeypatch, tmp_path):
    frame._stop_openclaw_sync()
    sessions_dir = tmp_path / ".openclaw" / "agents" / "main" / "sessions"
    sessions_dir.mkdir(parents=True)
    session_file = sessions_dir / "mixed.jsonl"
    local_created = time.time()
    session_file.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "message",
                        "id": "old-u",
                        "timestamp": local_created - 120,
                        "message": {"role": "user", "content": [{"type": "text", "text": "old question"}]},
                    }
                ),
                json.dumps(
                    {
                        "type": "message",
                        "id": "old-a",
                        "timestamp": local_created - 119,
                        "message": {"role": "assistant", "content": [{"type": "text", "text": "old answer"}]},
                    }
                ),
                json.dumps(
                    {
                        "type": "message",
                        "id": "new-u",
                        "timestamp": local_created + 1,
                        "message": {"role": "user", "content": [{"type": "text", "text": "new question"}]},
                    }
                ),
                json.dumps(
                    {
                        "type": "message",
                        "id": "new-a",
                        "timestamp": local_created + 2,
                        "message": {"role": "assistant", "content": [{"type": "text", "text": "new answer"}]},
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    frame.selected_model = "openclaw/main"
    frame.active_chat_id = "chat-new"
    frame.current_chat_id = "chat-new"
    frame.active_session_turns = [
        {
            "question": "new question",
            "answer_md": "",
            "model": "openclaw/main",
            "created_at": local_created,
            "request_status": "pending",
        }
    ]
    frame._current_chat_state = {"id": "chat-new", "title": "new", "turns": frame.active_session_turns}
    frame.active_openclaw_session_id = "zgwd-chat-new"
    frame.active_openclaw_session_file = ""
    frame.active_openclaw_sync_offset = 0
    (sessions_dir / "sessions.json").write_text(
        json.dumps(
            {
                "agent:main:webchat:new": {
                    "sessionId": "zgwd-chat-new",
                    "sessionFile": str(session_file),
                    "updatedAt": 1773672821999,
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "resolve_openclaw_sessions_dir", lambda _agent="main": sessions_dir)
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)

    frame._sync_openclaw_once()

    assert len(frame.active_session_turns) == 1
    assert frame.active_session_turns[0]["question"] == "new question"
    assert frame.active_session_turns[0]["answer_md"] == "new answer"


def test_shared_openclaw_session_file_routes_only_matching_local_turns(frame, monkeypatch, tmp_path):
    frame._stop_openclaw_sync()
    sessions_dir = tmp_path / ".openclaw" / "agents" / "main" / "sessions"
    sessions_dir.mkdir(parents=True)
    session_file = sessions_dir / "shared.jsonl"
    base_time = time.time()
    session_file.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "message",
                        "id": "user-c",
                        "timestamp": base_time + 1,
                        "message": {"role": "user", "content": [{"type": "text", "text": "question c"}]},
                    }
                ),
                json.dumps(
                    {
                        "type": "message",
                        "id": "answer-c",
                        "timestamp": base_time + 2,
                        "message": {"role": "assistant", "content": [{"type": "text", "text": "answer c"}]},
                    }
                ),
                json.dumps(
                    {
                        "type": "message",
                        "id": "user-d",
                        "timestamp": base_time + 3,
                        "message": {"role": "user", "content": [{"type": "text", "text": "question d"}]},
                    }
                ),
                json.dumps(
                    {
                        "type": "message",
                        "id": "answer-d",
                        "timestamp": base_time + 4,
                        "message": {"role": "assistant", "content": [{"type": "text", "text": "answer d"}]},
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    (sessions_dir / "sessions.json").write_text(
        json.dumps(
            {
                "agent:main:webchat:a": {
                    "sessionId": "zgwd-chat-a",
                    "sessionFile": str(session_file),
                    "updatedAt": 1773672821999,
                },
                "agent:main:webchat:b": {
                    "sessionId": "zgwd-chat-b",
                    "sessionFile": str(session_file),
                    "updatedAt": 1773672822999,
                },
            }
        ),
        encoding="utf-8",
    )
    frame.selected_model = "openclaw/main"
    frame.active_chat_id = "chat-b"
    frame.current_chat_id = "chat-b"
    frame.active_openclaw_session_id = "zgwd-chat-b"
    frame.active_openclaw_session_file = ""
    frame.active_openclaw_sync_offset = 0
    frame.active_session_turns = [
        {
            "question": "question d",
            "answer_md": "",
            "model": "openclaw/main",
            "created_at": base_time,
            "request_status": "pending",
        }
    ]
    frame._current_chat_state = {"id": "chat-b", "title": "chat b", "turns": frame.active_session_turns}
    frame.archived_chats = [
        {
            "id": "chat-a",
            "title": "chat a",
            "turns": [
                {
                    "question": "question c",
                    "answer_md": "",
                    "model": "openclaw/main",
                    "created_at": base_time,
                    "request_status": "pending",
                }
            ],
            "created_at": base_time,
            "updated_at": base_time,
            "openclaw_session_id": "zgwd-chat-a",
            "openclaw_session_file": "",
            "openclaw_sync_offset": 0,
        }
    ]
    monkeypatch.setattr(main, "resolve_openclaw_sessions_dir", lambda _agent="main": sessions_dir)
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)

    frame._sync_openclaw_once()

    archived = frame._find_archived_chat("chat-a")
    assert [(turn["question"], turn["answer_md"]) for turn in archived["turns"]] == [("question c", "answer c")]
    assert [(turn["question"], turn["answer_md"]) for turn in frame.active_session_turns] == [("question d", "answer d")]


def test_sync_once_updates_archived_openclaw_chat_by_session_id(frame, monkeypatch, tmp_path):
    sessions_dir = tmp_path / ".openclaw" / "agents" / "main" / "sessions"
    sessions_dir.mkdir(parents=True)
    archived_file = sessions_dir / "chat-a.jsonl"
    archived_file.write_text(
        json.dumps(
            {
                "type": "message",
                "id": "a-chat-a",
                "timestamp": time.time(),
                "message": {"role": "assistant", "content": [{"type": "text", "text": "archived reply"}]},
            }
        ),
        encoding="utf-8",
    )
    (sessions_dir / "sessions.json").write_text(
        json.dumps(
            {
                "agent:main:webchat:a": {
                    "sessionId": "zgwd-chat-a",
                    "sessionFile": str(archived_file),
                    "updatedAt": 1773672821999,
                }
            }
        ),
        encoding="utf-8",
    )
    frame.selected_model = "openclaw/main"
    frame.active_chat_id = "chat-b"
    frame.current_chat_id = "chat-b"
    frame.active_openclaw_session_id = "zgwd-chat-b"
    frame.active_openclaw_session_file = ""
    frame.active_session_turns = []
    frame._current_chat_state = {"id": "chat-b", "turns": frame.active_session_turns}
    frame.archived_chats = [
        {
            "id": "chat-a",
            "title": "chat a",
            "turns": [
                {
                    "question": "draw a",
                    "answer_md": "",
                    "model": "openclaw/main",
                    "created_at": time.time(),
                    "request_status": "pending",
                }
            ],
            "created_at": time.time(),
            "updated_at": time.time(),
            "openclaw_session_id": "zgwd-chat-a",
            "openclaw_session_file": "",
            "openclaw_sync_offset": 0,
        }
    ]
    monkeypatch.setattr(main, "resolve_openclaw_sessions_dir", lambda _agent="main": sessions_dir)
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)

    frame._sync_openclaw_once()

    archived = frame._find_archived_chat("chat-a")
    assert archived["turns"][0]["answer_md"] == "archived reply"
    assert archived["openclaw_session_file"] == str(archived_file)
    assert frame.active_session_turns == []


def test_openclaw_sync_once_does_not_schedule_ui_work_when_no_events(frame, monkeypatch, tmp_path):
    frame._stop_openclaw_sync()
    sessions_dir = tmp_path / ".openclaw" / "agents" / "main" / "sessions"
    sessions_dir.mkdir(parents=True)
    active_file = sessions_dir / "active.jsonl"
    archived_file = sessions_dir / "archived.jsonl"
    active_file.write_text("", encoding="utf-8")
    archived_file.write_text("", encoding="utf-8")
    (sessions_dir / "sessions.json").write_text(
        json.dumps(
            {
                "agent:main:webchat:active": {
                    "sessionId": "zgwd-active",
                    "sessionFile": str(active_file),
                    "updatedAt": 1773672821999,
                },
                "agent:main:webchat:archived": {
                    "sessionId": "zgwd-archived",
                    "sessionFile": str(archived_file),
                    "updatedAt": 1773672822999,
                },
            }
        ),
        encoding="utf-8",
    )
    frame.selected_model = "openclaw/main"
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.active_openclaw_session_id = "zgwd-active"
    frame.active_openclaw_session_file = str(active_file)
    frame.active_openclaw_sync_offset = 0
    frame.active_session_turns = [
        {"question": "active", "answer_md": "", "model": "openclaw/main", "created_at": time.time()}
    ]
    frame._current_chat_state = {"id": "chat-active", "turns": frame.active_session_turns}
    frame.archived_chats = [
        {
            "id": "chat-archived",
            "turns": [{"question": "archived", "answer_md": "", "model": "openclaw/main", "created_at": time.time()}],
            "openclaw_session_id": "zgwd-archived",
            "openclaw_session_file": str(archived_file),
            "openclaw_sync_offset": 0,
        }
    ]
    scheduled = []
    monkeypatch.setattr(main, "resolve_openclaw_sessions_dir", lambda _agent="main": sessions_dir)
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: scheduled.append((fn, a, k)))

    frame._sync_openclaw_once()

    assert scheduled == []


def test_openclaw_sync_batch_no_events_does_not_save_or_refresh_ui(frame, monkeypatch):
    frame._stop_openclaw_sync()
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.active_openclaw_session_id = "zgwd-active"
    frame.active_openclaw_session_file = r"C:\tmp\active.jsonl"
    frame.active_openclaw_sync_offset = 10
    frame.active_session_turns = [
        {"question": "active", "answer_md": "", "model": "openclaw/main", "created_at": time.time()}
    ]
    frame._current_chat_state = {"id": "chat-active", "turns": frame.active_session_turns}
    saved = {"n": 0}
    rendered = {"n": 0}
    histories = {"n": 0}
    monkeypatch.setattr(frame, "_save_state", lambda: saved.__setitem__("n", saved["n"] + 1))
    monkeypatch.setattr(frame, "_render_answer_list", lambda: rendered.__setitem__("n", rendered["n"] + 1))
    monkeypatch.setattr(frame, "_refresh_history", lambda *a, **k: histories.__setitem__("n", histories["n"] + 1))

    frame._apply_openclaw_sync_batch(
        {
            "session_id": "zgwd-active",
            "session_file": r"C:\tmp\active.jsonl",
            "offset": 10,
            "updated_at": time.time(),
            "previous_file": r"C:\tmp\active.jsonl",
            "file_changed": False,
            "session_changed": False,
        },
        [],
    )

    assert saved["n"] == 0
    assert rendered["n"] == 0
    assert histories["n"] == 0


def test_switch_current_chat_persists_restored_openclaw_runtime(frame, monkeypatch):
    frame._stop_openclaw_sync()
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_session_turns = [
        {"question": "current", "answer_md": "answer", "model": "openclaw/main", "created_at": time.time()}
    ]
    frame._current_chat_state = {"id": "chat-current", "title": "current", "turns": frame.active_session_turns}
    frame.archived_chats = [
        {
            "id": "chat-archived",
            "source_chat_id": "chat-archived",
            "title": "archived",
            "turns": [
                {"question": "archived q", "answer_md": "archived a", "model": "openclaw/main", "created_at": time.time()}
            ],
            "created_at": time.time(),
            "updated_at": time.time(),
            "openclaw_session_key": "agent:main:main",
            "openclaw_session_id": "zgwd-archived",
            "openclaw_session_file": r"C:\tmp\archived.jsonl",
            "openclaw_sync_offset": 77,
            "openclaw_last_event_id": "evt-archived",
            "openclaw_last_synced_at": 123.0,
        }
    ]
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda force_replay=False: None)

    assert frame._switch_current_chat("chat-archived") is True

    data = main.json.loads(frame.state_path.read_text(encoding="utf-8"))
    assert data["active_chat_id"] == "chat-archived"
    assert data["active_openclaw_session_id"] == "zgwd-archived"
    assert data["active_openclaw_session_file"] == r"C:\tmp\archived.jsonl"
    assert data["active_openclaw_sync_offset"] == 77


def test_apply_openclaw_sync_batch_plays_sound_for_incoming_assistant_only(frame, monkeypatch):
    frame._stop_openclaw_sync()
    frame.active_openclaw_session_file = r"C:\tmp\main.jsonl"
    frame.active_openclaw_last_synced_at = time.time()
    played = {"n": 0}
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: played.__setitem__("n", played["n"] + 1))
    frame._apply_openclaw_sync_batch(
        {
            "session_id": "zgwd-chat-001",
            "session_file": r"C:\tmp\main.jsonl",
            "offset": 120,
            "updated_at": time.time(),
            "file_changed": False,
            "session_changed": False,
        },
        [main.OpenClawSyncEvent(event_id="a-only", role="assistant", text="鏂扮殑澶栭儴鍥炲", timestamp=time.time())],
    )
    assert played["n"] == 1
    assert frame.active_session_turns[-1]["answer_md"] == "鏂扮殑澶栭儴鍥炲"


def test_openclaw_done_success_does_not_play_finish_sound(frame, monkeypatch):
    frame._stop_openclaw_sync()
    frame.active_session_turns = [
        {
            "question": "\u6253\u5f00\u63a7\u5236\u53f0",
            "answer_md": "",
            "model": "openclaw/main",
            "created_at": time.time(),
        }
    ]
    played = {"n": 0}
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: played.__setitem__("n", played["n"] + 1))
    monkeypatch.setattr(frame, "_render_answer_list", lambda: None)
    frame._on_done(0, "", "", "openclaw/main", "")
    assert played["n"] == 0


def test_sync_once_does_not_let_global_pointer_replace_active_session_id(frame, monkeypatch, tmp_path):
    sessions_dir = tmp_path / ".openclaw" / "agents" / "main" / "sessions"
    sessions_dir.mkdir(parents=True)
    session_file = sessions_dir / "main.jsonl"
    session_file.write_text(
        json.dumps(
            {
                "type": "message",
                "id": "a-restart",
                "timestamp": time.time(),
                "message": {"role": "assistant", "content": [{"type": "text", "text": "new global reply"}]},
            }
        ),
        encoding="utf-8",
    )
    (sessions_dir / "sessions.json").write_text(
        json.dumps(
            {
                "agent:main:main": {
                    "sessionId": "zgwd-restarted",
                    "sessionFile": str(session_file),
                    "updatedAt": 1773672820158,
                }
            }
        ),
        encoding="utf-8",
    )
    frame.selected_model = "openclaw/main"
    frame.active_session_turns = []
    frame.active_openclaw_session_id = "zgwd-old"
    frame.active_openclaw_session_file = str(session_file)
    frame.active_openclaw_sync_offset = session_file.stat().st_size
    monkeypatch.setattr(main, "resolve_openclaw_sessions_dir", lambda _agent="main": sessions_dir)
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)
    frame._sync_openclaw_once()
    assert frame.active_openclaw_session_id == "zgwd-old"
    assert frame.active_session_turns == []
