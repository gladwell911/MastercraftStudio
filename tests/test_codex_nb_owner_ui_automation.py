import io
import json
from types import SimpleNamespace

import pytest
import wx

import main
from codex_client import CodexEvent, _event_from_item
from codex_worker_process import CodexWorkerRuntime
from codex_worker_protocol import decode_worker_line, make_ui_request
from test_codex_worker_process import FakeCodexClient
from test_codex_ui_responsiveness_automation import _track_ui_timers_for_test, _yield_until


@pytest.mark.parametrize("before_ack", [False, True])
@pytest.mark.parametrize("archived", [False, True])
def test_reused_start_ack_routes_nb_final_to_actual_answer_list_and_canonical(request, wx_app, monkeypatch, before_ack, archived):
    cleanup = _track_ui_timers_for_test(monkeypatch)
    frame = request.getfixturevalue("frame")
    chat_id = "nb-fixture"
    thread = "019d36ab-804a-73a2-a2dd-7a17e181628f"
    native = "019d36b3-0a1c-7c61-aed9-387f6afbb9f9"
    turns = [{"question": f"original {idx}", "answer_md": f"answer {idx}", "model": "codex/main", "request_status": "done"} for idx in range(2)]
    turns.extend({"question": question, "answer_md": main.REQUESTING_TEXT, "request_status":"pending", "model":"codex/main",
        "codex_thread_id":thread, "codex_turn_id":native, "codex_start_generation":37, "codex_context_generation":37}
        for question in ("initial QA", "accepted append QA"))
    chat = {"id":chat_id,"title":"nb fixture","model":"codex/main","turns":turns,
        "codex_thread_id":thread,"codex_turn_id":native,"codex_context_generation":37,"codex_turn_active":True}
    frame.active_chat_id = frame.current_chat_id = chat_id
    frame.active_session_turns = turns
    frame._current_chat_state = chat
    frame.active_codex_thread_id = thread
    frame.active_codex_turn_id = native
    frame.active_codex_turn_active = True
    frame._chat_store_enabled = True
    frame.chat_store.upsert_chat(chat)
    frame._mark_chat_turns_dirty(chat_id, 0)
    frame._persist_chat_history_to_store()
    monkeypatch.setattr(frame, "_request_codex_chat_information", lambda *_: None)
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)
    monkeypatch.setattr(frame, "_schedule_async_archive_rename", lambda *_: None)
    monkeypatch.setattr(frame, "_broadcast_remote_event", lambda *_: None)
    frame._render_answer_list_compat(refresh_execution=False)
    if archived:
        frame.chat_store.upsert_chat({"id":"other","title":"other","model":"codex/main"})
        frame.archived_chats = [frame.chat_store.load_chat("other")]
        assert frame._switch_current_chat("other")
        frame.input_edit.SetValue("unsent other draft")
        frame.input_edit.SetFocus()
        wx_app.Yield()
    def boundary(client):
        client.on_event(_event_from_item("item/completed", {"threadId":thread,"turnId":native,
            "item":{"id":"accepted-user","type":"userMessage","content":[{"type":"text","text":"accepted append QA"}]}}))
    class Provider(FakeCodexClient):
        def start_turn_items(self, thread_id, items, service_tier=None):
            if self.started_turns and before_ack: boundary(self)
            self.started_turns.append(items)
            return {"turn":{"id":native}}
    output = io.StringIO()
    runtime = CodexWorkerRuntime(client_factory=lambda cb, model: Provider(cb, model), output=output)
    def drain():
        lines = output.getvalue().splitlines()
        output.seek(0); output.truncate()
        for line in lines: frame._on_codex_worker_message(chat_id, decode_worker_line(line + "\n"))
        wx_app.Yield()
    try:
        for idx in (2, 3):
            runtime.handle_message(make_ui_request(f"request-{idx}", "start_turn", {
                "chat_id":chat_id,"model":"codex/main","turn_idx":idx,"thread_id":thread,
                "context_generation":37,"question":turns[idx]["question"]}))
            drain()
        client = runtime._clients[(chat_id,"codex/main")]
        if not before_ack: boundary(client)
        client.on_event(_event_from_item("item/completed", {"threadId":thread,"turnId":native,
            "item":{"id":"nb-final","type":"agentMessage","phase":"final_answer","text":"fresh fixture final"}}))
        frame._remote_nats_transport = SimpleNamespace(protocol_version=2, subjects=SimpleNamespace(pair_id="default"), _loop=None)
        completion = CodexEvent(type="turn_completed",thread_id=thread,turn_id=native,status="completed",data={"items":[
            {"id":"accepted-user","type":"userMessage","content":[{"type":"text","text":"accepted append QA"}]},
            {"id":"nb-final","type":"agentMessage","phase":"final_answer","text":"fresh fixture final"}]})
        client.on_event(completion)
        drain()
        if archived:
            assert frame._find_archived_chat(chat_id)["turns"][3]["answer_md"] == "fresh fixture final", frame._find_archived_chat(chat_id)
            assert frame.input_edit.GetValue() == "unsent other draft"
            assert frame.input_edit.HasFocus()
            assert frame._switch_current_chat(chat_id)
        assert _yield_until(wx_app, lambda: any("fresh fixture final" in frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())))
        current = frame._current_chat_state["turns"]
        assert current[2]["request_status"] == current[3]["request_status"] == "done"
        assert current[2]["answer_md"] == ""
        assert current[2]["request_carried_forward_to"] == 3
        assert current[3]["answer_md"] == "fresh fixture final"
        texts = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
        assert texts.index("accepted append QA") < texts.index("fresh fixture final")
        frame._persist_chat_history_to_store()
        frame._flush_execution_step_persists_sync()
        frame._flush_pending_remote_finals()
        reopened = main.ChatStore(frame.chat_store.db_path).load_chat(chat_id)
        assert reopened["turns"][3]["answer_md"] == "fresh fixture final"
        assert not reopened.get("codex_turn_active")
        answer = frame.chat_store.readable_answer(chat_id, turn_index=3)
        assert answer and answer["message_id"] == frame.chat_store.resolve_canonical_message_by_turn(chat_id, role="assistant", turn_index=3)
        assert frame.chat_store.readable_answer(chat_id, turn_index=2) is None
        state = frame.chat_store.get_chat_read_state(chat_id)
        assert state["latest_readable_message_id"] == answer["message_id"]
        outbox = frame.chat_store.pending_outbox(pair_id="default", domain="events")
        facts = [json.loads(row["payload"]) for row in outbox]
        finals = [fact for fact in facts if fact["kind"] == "assistant_final"]
        assert len(finals) == 1
        fact = finals[0]
        assert fact["body"]["message_id"] == answer["message_id"]
        assert any(fact["kind"] == "execution_entry" and fact["body"]["kind"] == "final"
            and fact["body"]["turn_index"] == 3 and fact["body"]["message_id"] == answer["message_id"] for fact in facts)
        execution_count = frame.chat_store.load_recent_execution_steps(chat_id, limit=1000)[0]
        client.on_event(completion)
        drain()
        frame._persist_chat_history_to_store()
        frame._flush_execution_step_persists_sync()
        frame._flush_pending_remote_finals()
        assert frame.chat_store.load_recent_execution_steps(chat_id, limit=1000)[0] == execution_count
        assert frame.chat_store.pending_outbox(pair_id="default", domain="events") == outbox
        assert frame.chat_store.readable_answer(chat_id, turn_index=3) == answer
        assert frame.chat_store.get_chat_read_state(chat_id)["latest_readable_seq"] == state["latest_readable_seq"]
        assert frame._current_chat_state["turns"][3]["answer_md"] == "fresh fixture final"
        frame.chat_store.mark_chat_read(pair_id="default", chat_id=chat_id,
            generation=answer["generation"], message_id=answer["message_id"],
            answer_seq=answer["answer_seq"], operation_id="fixture-read")
        assert frame.chat_store.get_chat_read_state(chat_id)["read_seq"] == answer["answer_seq"]
        assert any(step.get("turn_idx") == 3 and step.get("phase") == "final_answer" for step in frame._current_chat_state.get("execution_steps", []))
    finally:
        runtime.close()
        cleanup(frame)


@pytest.mark.parametrize("valid_final", [False, True])
def test_unresolved_accepted_boundary_fails_partial_in_real_answer_list_and_reload(request, wx_app, monkeypatch, valid_final):
    cleanup = _track_ui_timers_for_test(monkeypatch)
    frame = request.getfixturevalue("frame")
    owner = frame._ensure_active_chat_id()
    turns = [{"question":f"q{idx}","answer_md":main.REQUESTING_TEXT,"request_status":"pending","model":"codex/main",
        "codex_thread_id":"thread-1","codex_turn_id":"turn-1","codex_start_generation":1,"codex_context_generation":1} for idx in (0,1)]
    frame.active_session_turns = turns
    frame._current_chat_state.update(turns=turns,codex_context_generation=1,codex_thread_id="thread-1",codex_turn_id="turn-1",codex_turn_active=True)
    frame.active_codex_thread_id, frame.active_codex_turn_id = "thread-1", "turn-1"
    frame.active_codex_turn_active = True
    monkeypatch.setattr(frame, "_request_codex_chat_information", lambda *_: None)
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)
    frame._mark_chat_turns_dirty(owner, 0)
    frame._persist_chat_history_to_store()
    frame._render_answer_list_compat(refresh_execution=False)
    output = io.StringIO()
    runtime = CodexWorkerRuntime(client_factory=lambda cb, model: FakeCodexClient(cb, model),output=output)
    def drain():
        lines = output.getvalue().splitlines(); output.seek(0); output.truncate()
        for line in lines: frame._on_codex_worker_message(owner, decode_worker_line(line+"\n"))
        wx_app.Yield()
    try:
        runtime.handle_message(make_ui_request("first","start_turn", {"chat_id":owner,"turn_idx":0,"thread_id":"thread-1","context_generation":1,"question":"q0"}))
        drain()
        client = runtime._clients[(owner,"codex/main")]
        client.on_event(CodexEvent(type="item_completed" if valid_final else "subagent_result",
            thread_id="thread-1",turn_id="turn-1",item_id="first-output",phase="final_answer" if valid_final else "",
            text="valid old final" if valid_final else "partial subagent output"))
        drain()
        runtime.handle_message(make_ui_request("append","start_turn", {"chat_id":owner,"turn_idx":1,"thread_id":"thread-1","context_generation":1,"question":"q1"}))
        drain()
        client.on_event(CodexEvent(type="turn_completed",thread_id="thread-1",turn_id="turn-1",status="completed"))
        drain()
        assert [turn["request_status"] for turn in turns] == ["done" if valid_final else "failed", "failed"]
        assert all("boundary unresolved" in turn["codex_ownership_error"] for turn in turns)
        assert _yield_until(wx_app, lambda: any("boundary unresolved" in frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())))
        if valid_final: assert turns[0]["answer_md"] == "valid old final"
        else: assert "partial subagent output" in turns[0]["answer_md"] and turns[0]["request_error"]
        frame._persist_chat_history_to_store()
        loaded = main.ChatStore(frame.chat_store.db_path).load_chat(owner)
        assert [turn["request_status"] for turn in loaded["turns"]] == [turn["request_status"] for turn in turns]
        assert all(turn["codex_ownership_error"] for turn in loaded["turns"])
        assert frame.chat_store.readable_answer(owner, turn_index=1) is None
        assert bool(frame.chat_store.readable_answer(owner, turn_index=0)) is valid_final
        assert not frame.active_codex_turn_active and not frame.is_running
    finally:
        runtime.close(); cleanup(frame)
