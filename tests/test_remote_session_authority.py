import time


def test_remote_history_snapshot_exposes_canonical_metadata(frame):
    now = time.time()
    frame.active_chat_id = "active-1"
    frame.current_chat_id = "active-1"
    frame._current_chat_state.update(
        {
            "id": "active-1",
            "title": "current title",
            "title_manual": True,
            "model": "codex/main",
            "created_at": now - 10,
            "updated_at": now,
        }
    )
    frame.active_session_turns = [
        {
            "question": "current question",
            "answer_md": "current answer",
            "model": "codex/main",
            "created_at": now,
        }
    ]
    frame.archived_chats = [
        {
            "id": "draw-1",
            "title": "Desktop Drawing Title",
            "title_manual": True,
            "model": "google/gemini-3-pro-image-preview",
            "created_at": now - 30,
            "updated_at": now - 5,
            "pinned": True,
            "turns": [
                {
                    "question": "",
                    "answer_md": "![img](https://example.com/x.png)\ndrawing result",
                    "model": "google/gemini-3-pro-image-preview",
                    "created_at": now - 5,
                }
            ],
        }
    ]

    status_list, list_body = frame._remote_api_history_list_ui()
    status_read, read_body = frame._remote_api_history_read_ui({"chat_id": "draw-1"})
    status_state, state_body = frame._remote_api_state_ui({"chat_id": "active-1"})

    assert status_list == 200
    active = next(chat for chat in list_body["chats"] if chat["chat_id"] == "active-1")
    assert active["title"] == "current title"
    assert active["model"] == "codex/main"
    assert active["turn_count"] == 1
    assert active["current"] is True
    assert active["active"] is True
    assert "created_at" in active
    assert "updated_at" in active

    drawing = next(chat for chat in list_body["chats"] if chat["chat_id"] == "draw-1")
    assert drawing["title"] == "Desktop Drawing Title"
    assert drawing["model"] == "google/gemini-3-pro-image-preview"
    assert drawing["turn_count"] == 1
    assert drawing["current"] is False
    assert drawing["active"] is False
    assert drawing["pinned"] is True
    assert drawing["title"] != "A new session was"

    assert status_read == 200
    assert read_body["chat"]["title"] == "Desktop Drawing Title"
    assert read_body["chat"]["model"] == "google/gemini-3-pro-image-preview"
    assert read_body["chat"]["turns"][0]["assistant_only"] is True
    assert read_body["chat"]["turn_count"] == 1

    assert status_state == 200
    assert state_body["title"] == "current title"
    assert state_body["model"] == "codex/main"
    assert state_body["turn_count"] == 1
    assert state_body["current"] is True
    assert state_body["active"] is True
