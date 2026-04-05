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


def test_openclaw_new_chat_clears_list_and_sends_new(frame, monkeypatch, tmp_path):
    frame._stop_openclaw_sync()
    session_file = tmp_path / "main.jsonl"
    session_file.write_text("old history", encoding="utf-8")
    frame.selected_model = "openclaw/main"
    frame.model_combo.SetValue("openclaw/main")
    frame.active_session_turns = [
        {
            "question": "旧问题",
            "answer_md": "旧回答",
            "model": "openclaw/main",
            "created_at": time.time(),
        }
    ]
    frame.active_openclaw_session_file = str(session_file)
    frame.active_openclaw_session_id = "zgwd-old"

    sent = {}
    focused = {"n": 0}
    monkeypatch.setattr(main.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda force_replay=False: None)
    monkeypatch.setattr(frame, "_render_answer_list", lambda: None)
    monkeypatch.setattr(frame, "_set_input_hint_idle", lambda: None)
    monkeypatch.setattr(frame.input_edit, "SetFocus", lambda: focused.__setitem__("n", focused["n"] + 1))

    def fake_stream_chat(self, user_text, session_id, on_delta=None):
        sent["question"] = user_text
        sent["session_id"] = session_id
        return ""

    monkeypatch.setattr(main.OpenClawClient, "stream_chat", fake_stream_chat)

    frame._on_new_chat_clicked(None)

    assert frame.active_session_turns == []
    assert frame.active_openclaw_sync_offset == session_file.stat().st_size
    assert sent["question"] == "/new"
    assert sent["session_id"] == "zgwd-old"
    assert focused["n"] >= 1
