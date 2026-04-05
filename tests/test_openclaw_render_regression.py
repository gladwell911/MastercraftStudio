import time

import main


def test_apply_openclaw_sync_batch_does_not_render_for_user_only_event(frame, monkeypatch):
    frame._stop_openclaw_sync()
    frame.active_session_turns = [
        {
            "question": "hello",
            "answer_md": "",
            "model": "openclaw/main",
            "created_at": 100.0,
            "origin": "local",
            "question_origin": "local",
        }
    ]
    rendered = {"n": 0}
    monkeypatch.setattr(frame, "_render_answer_list", lambda: rendered.__setitem__("n", rendered["n"] + 1))
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)

    frame._apply_openclaw_sync_batch(
        {
            "session_id": "zgwd-chat-001",
            "session_file": r"C:\tmp\main.jsonl",
            "offset": 110,
            "updated_at": time.time(),
            "file_changed": False,
            "session_changed": False,
        },
        [main.OpenClawSyncEvent(event_id="u-only", role="user", text="hello", timestamp=101.0)],
    )

    assert rendered["n"] == 0


def test_apply_openclaw_sync_batch_renders_when_assistant_arrives(frame, monkeypatch):
    frame._stop_openclaw_sync()
    rendered = {"n": 0}
    monkeypatch.setattr(frame, "_render_answer_list", lambda: rendered.__setitem__("n", rendered["n"] + 1))
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)

    frame._apply_openclaw_sync_batch(
        {
            "session_id": "zgwd-chat-001",
            "session_file": r"C:\tmp\main.jsonl",
            "offset": 120,
            "updated_at": time.time(),
            "file_changed": False,
            "session_changed": False,
        },
        [main.OpenClawSyncEvent(event_id="a-only", role="assistant", text="done", timestamp=102.0)],
    )

    assert rendered["n"] == 1
    assert frame.active_session_turns[-1]["answer_md"] == "done"
