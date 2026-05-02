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
