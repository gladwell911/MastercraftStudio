"""Isolated Local RC to desktop history and durable result checks."""

import json
from types import SimpleNamespace

import main


def test_local_rc_archived_codex_interleaving_preserves_owner_sound_and_history(frame, monkeypatch):
    frame.Show()
    frame._chat_store_enabled = True
    frame.active_chat_id = frame.current_chat_id = "a"
    frame._current_chat_state = {"id": "a", "title": "A", "updated_at": 1.0, "turns": []}
    frame.active_session_turns = frame._current_chat_state["turns"]
    frame._remote_nats_transport = SimpleNamespace(
        protocol_version=2, subjects=SimpleNamespace(pair_id="isolated-local"), _loop=None
    )
    for owner in ("b", "c"):
        frame.chat_store.upsert_chat({"id": owner, "title": owner, "model": main.DEFAULT_CODEX_MODEL})
    frame.archived_chats = frame.chat_store.list_chat_summaries()
    frame._refresh_history("a")
    frame.input_edit.SetValue("foreground draft")
    frame.input_edit.SetSelection(2, 6)
    frame.input_edit.SetFocus()
    focus_before = main.wx.Window.FindFocus()
    sounds = []
    monkeypatch.setattr(frame, "_start_codex_worker_for_turn", lambda *_args: True)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: sounds.append("send"))
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: sounds.append("reply"))
    monkeypatch.setattr(frame, "_request_codex_chat_information", lambda *_args: None)
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: None)
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)

    assert frame._remote_api_message_ui({"chat_id": "b", "text": ""})[0] == 400
    for owner in ("b", "c"):
        status, body = frame._remote_api_message_ui(
            {"chat_id": owner, "text": f"phone {owner}", "model": main.DEFAULT_CODEX_MODEL}
        )
        assert (status, body["accepted"]) == (200, True)
    frame._last_primary_interaction_at = 0.0
    frame._flush_idle_ui_refreshes()
    assert sounds == ["send", "send"]
    assert frame.history_ids[:3] == ["c", "b", "a"]
    assert frame._get_all_chat_ids_in_order()[:3] == frame.history_ids[:3]
    assert frame.history_list_model.selected_id() == "a"
    assert frame.active_chat_id == "a"
    assert frame.input_edit.GetValue() == "foreground draft"
    assert frame.input_edit.GetSelection() == (2, 6)
    assert main.wx.Window.FindFocus() is focus_before

    for owner in ("b", "c"):
        frame._apply_codex_worker_thread_state(owner, {
            "chat_id": owner, "turn_idx": 0, "thread_id": f"thread-{owner}",
            "turn_id": f"turn-{owner}", "context_generation": 0, "active": True,
        })
    wrong = main.CodexEvent(
        type="turn_completed", thread_id="thread-c", turn_id="turn-c",
        data={"turn_idx": 0}, status="completed", text="wrong owner",
    )
    frame._on_codex_event_for_chat("b", wrong)
    assert sounds == ["send", "send"]
    for owner in ("c", "b"):
        def event(kind, **kwargs):
            return main.CodexEvent(
                type=kind, thread_id=f"thread-{owner}", turn_id=f"turn-{owner}",
                data={"turn_idx": 0}, **kwargs,
            )
        frame._on_codex_event_for_chat(owner, event("item_completed", phase="final_answer", text=f"answer {owner}"))
        frame._on_codex_event_for_chat(owner, event("turn_completed", status="completed"))
        frame._on_codex_event_for_chat(owner, event("turn_completed", status="completed"))
    frame._persist_chat_history_to_store()

    assert sounds == ["send", "send", "reply", "reply"]
    assert frame.active_chat_id == "a"
    assert frame.input_edit.GetValue() == "foreground draft"
    for owner in ("b", "c"):
        stored = frame.chat_store.load_chat(owner)
        assert stored["turns"][0]["answer_md"] == f"answer {owner}"
        assert stored["turns"][0]["request_status"] == "done"
    outbox = [
        json.loads(bytes(row["payload"]).decode("utf-8"))
        for row in frame.chat_store.pending_outbox(pair_id="isolated-local", domain="events")
    ]
    for owner in ("b", "c"):
        finals = [item for item in outbox if item.get("chat_id") == owner and item["kind"] == "assistant_final"]
        assert len(finals) == 1
        assert finals[0]["body"]["text"] == f"answer {owner}"
