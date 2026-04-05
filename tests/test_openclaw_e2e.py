import json
import time

import main


class _ImmediateThread:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        if self._target:
            self._target(*self._args, **self._kwargs)

    def is_alive(self):
        return False

    def join(self, timeout=None):
        return None


def test_openclaw_send_flow_updates_from_session_sync(frame, monkeypatch, tmp_path):
    frame._stop_openclaw_sync()
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(main.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)
    monkeypatch.setattr(frame, "_focus_latest_answer", lambda: None)
    monkeypatch.setattr(main.wx, "CallLater", lambda _delay, fn, *a, **k: fn(*a, **k))
    frame.active_session_turns = []
    frame.active_chat_id = ""
    frame.active_openclaw_session_id = ""
    frame.active_openclaw_session_file = ""
    frame.active_openclaw_sync_offset = 0
    frame.active_session_started_at = 0.0
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda force_replay=False: None)
    now = time.time()

    sessions_dir = tmp_path / ".openclaw" / "agents" / "main" / "sessions"
    sessions_dir.mkdir(parents=True)
    session_file = sessions_dir / "main.jsonl"
    session_file.write_text("", encoding="utf-8")
    (sessions_dir / "sessions.json").write_text(
        json.dumps(
            {
                "agent:main:main": {
                    "sessionId": "zgwd-main",
                    "sessionFile": str(session_file),
                    "updatedAt": 1773672820158,
                }
            }
        ),
        encoding="utf-8",
    )

    def fake_stream_chat(self, user_text, session_id, on_delta=None):
        assert user_text == "打开控制台"
        assert session_id.startswith("zgwd-")
        session_file.write_text(
            "\n".join(
                [
                        json.dumps(
                            {
                                "type": "message",
                                "id": "u1",
                                "timestamp": now + 1,
                                "message": {"role": "user", "content": [{"type": "text", "text": "打开控制台"}]},
                            }
                        ),
                        json.dumps(
                            {
                                "type": "message",
                                "id": "a1",
                                "timestamp": now + 2,
                                "message": {"role": "assistant", "content": [{"type": "text", "text": "OpenClaw 回复"}]},
                            }
                        ),
                ]
            ),
            encoding="utf-8",
        )
        return "OpenClaw 回复"

    monkeypatch.setattr(main, "resolve_openclaw_sessions_dir", lambda _agent="main": sessions_dir)
    monkeypatch.setattr(main.OpenClawClient, "stream_chat", fake_stream_chat)

    frame.model_combo.SetSelection(frame.model_combo.FindString("openclaw/main"))
    frame.input_edit.SetValue("打开控制台")
    frame._on_send_clicked(None)
    frame._sync_openclaw_once()

    assert frame.selected_model == "openclaw/main"
    assert len(frame.active_session_turns) == 1
    assert frame.active_session_turns[0]["question"] == "打开控制台"
    assert frame.active_session_turns[0]["answer_md"] == "OpenClaw 回复"

    answers = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert "OpenClaw 回复" in answers
