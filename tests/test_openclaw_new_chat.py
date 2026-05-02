import time

import main


def test_openclaw_new_chat_archives_current_chat_without_sending_new(frame, monkeypatch, tmp_path):
    frame._stop_openclaw_sync()
    session_file = tmp_path / "old-openclaw.jsonl"
    session_file.write_text("old history", encoding="utf-8")
    frame.selected_model = "openclaw/main"
    frame.model_combo.SetValue("openclaw")
    frame.active_chat_id = "chat-old"
    frame.current_chat_id = "chat-old"
    frame.active_session_started_at = time.time()
    frame.active_session_turns = [
        {
            "question": "\u65e7\u95ee\u9898",
            "answer_md": "\u65e7\u56de\u7b54",
            "model": "openclaw/main",
            "created_at": time.time(),
        }
    ]
    frame._current_chat_state = {
        "id": "chat-old",
        "title": "old openclaw",
        "title_manual": False,
        "title_source": "default",
        "title_updated_at": time.time(),
        "title_revision": 1,
        "turns": frame.active_session_turns,
        "created_at": frame.active_session_started_at,
        "updated_at": frame.active_session_started_at,
        "detail_panel_mode": "answers",
        "execution_steps": [],
    }
    frame.active_openclaw_session_file = str(session_file)
    frame.active_openclaw_session_id = "zgwd-chat-old"
    frame.active_openclaw_sync_offset = 12

    sent = []
    focused = {"n": 0}
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda force_replay=False: None)
    monkeypatch.setattr(frame, "_render_answer_list", lambda: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda _chat_id: None)
    monkeypatch.setattr(frame.input_edit, "SetFocus", lambda: focused.__setitem__("n", focused["n"] + 1))

    def fake_stream_chat(self, user_text, session_id, on_delta=None):
        sent.append((user_text, session_id))
        return ""

    monkeypatch.setattr(main.OpenClawClient, "stream_chat", fake_stream_chat)

    frame._on_new_chat_clicked(None)

    assert sent == []
    assert frame.active_session_turns == []
    assert frame.active_chat_id and frame.active_chat_id != "chat-old"
    assert frame.current_chat_id == frame.active_chat_id
    assert frame.active_openclaw_session_id == ""
    assert frame.active_openclaw_session_file == ""
    assert frame.active_openclaw_sync_offset == 0
    assert focused["n"] >= 1
    archived = next(chat for chat in frame.archived_chats if chat.get("id") == "chat-old")
    assert archived["openclaw_session_id"] == "zgwd-chat-old"
    assert archived["openclaw_session_file"] == str(session_file)
