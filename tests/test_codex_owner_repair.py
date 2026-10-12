import hashlib
import json
import sqlite3

import pytest

from scripts.propose_codex_owner_repair import propose


@pytest.mark.parametrize("fault", [None, "thread", "generation", "duplicate", "boundary", "execution_thread", "execution_owner", "empty_final", "nonstring_final", "conflicting_duplicate", "selected_thread", "selected_generation", "selected_native"])
def test_snapshot_proposal_retains_originals_and_refuses_ambiguity(tmp_path, fault):
    path = tmp_path / "snapshot.db"
    identity = dict(codex_thread_id="thread", codex_turn_id="native", model="codex/main",
        codex_start_generation=37, codex_context_generation=37)
    old = {**identity, "question": "initial", "answer_md": "fixture final", "request_status": "done", "answer_at": 123,
        "answer_origin":"codex-final", "codex_completed_answers":[{"item_id":"final","text":"fixture final"}],
        "provider_message_id":"final", "answer_seq":7, "message_id":"old-readable"}
    new = {**identity, "question": "accepted append", "answer_md": "正在请求...", "request_status": "pending"}
    if fault == "thread": new["codex_thread_id"] = "unrelated"
    if fault == "generation": new.pop("codex_start_generation")
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE turns(chat_id,turn_index,payload_json)")
        db.execute("CREATE TABLE execution_steps(chat_id,step_index,payload_json)")
        for idx, turn in ((2, old), (3, new)):
            db.execute("INSERT INTO turns VALUES(?,?,?)", ("chat", idx, json.dumps(turn)))
        if fault == "duplicate":
            db.execute("INSERT INTO turns VALUES(?,?,?)", ("chat", 4, json.dumps(new)))
        for idx, kind, subtype, detail in [(98, "item_completed", "userMessage", {"id":"user", "content":[{"type":"text", "text":"wrong" if fault == "boundary" else "accepted append"}]}),
                (271,"item_completed","agentMessage", {"id":"final", "text":"fixture final"}), (274,"turn_completed","turnCompleted", {})]:
            if idx == 271 and fault in {"empty_final", "nonstring_final"}:
                detail["text"] = "" if fault == "empty_final" else None
            step = {"type":kind,"subtype":subtype,"turn_id":"native","thread_id":"thread","turn_idx":2,
                "phase":"final_answer" if idx == 271 else "", "source_detail": {**detail,"context_generation":37,"model":"codex/main"}}
            if idx == 271 and fault == "execution_thread": step["thread_id"] = "wrong-thread"
            if idx == 271 and fault == "execution_owner": step["turn_idx"] = 9
            db.execute("INSERT INTO execution_steps VALUES(?,?,?)", ("chat", idx, json.dumps(step)))
            if idx == 98:
                duplicate = json.loads(json.dumps(step))
                duplicate["type"] = "item_started"
                if fault == "conflicting_duplicate": duplicate["source_detail"]["content"][0]["text"] = "conflict"
                db.execute("INSERT INTO execution_steps VALUES(?,?,?)", ("chat", 99, json.dumps(duplicate)))
        for idx, kind in ((50,"item_started"), (120,"item_completed")):
            old_tool = {"type":kind,"subtype":"commandExecution","turn_id":"native","thread_id":"thread","turn_idx":2,
                "source_detail":{"id":"old-tool","model":"codex/main","context_generation":37}}
            db.execute("INSERT INTO execution_steps VALUES(?,?,?)", ("chat", idx, json.dumps(old_tool)))
        if fault in {"selected_thread", "selected_generation", "selected_native"}:
            new_tool = {"type":"item_completed","subtype":"commandExecution","turn_id":"native","thread_id":"thread","turn_idx":2,
                "source_detail":{"id":"new-tool","model":"codex/main","context_generation":37}}
            if fault == "selected_thread": new_tool["thread_id"] = "other-thread"
            if fault == "selected_native": new_tool["turn_id"] = "other-native"
            if fault == "selected_generation": new_tool["source_detail"]["context_generation"] = 36
            db.execute("INSERT INTO execution_steps VALUES(?,?,?)", ("chat", 150, json.dumps(new_tool)))
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    args = dict(chat_id="chat", thread_id="thread", native_turn_id="native", model="codex/main",
        generation=37, source_index=2, target_index=3)
    if fault:
        with pytest.raises(ValueError): propose(path, **args)
    else:
        report = propose(path, **args)
        assert report["turn_changes"][0]["original"] == old
        assert report["turn_changes"][0]["proposed"]["request_carried_forward_to"] == 3
        assert report["turn_changes"][1]["proposed"]["answer_md"] == "fixture final"
        assert report["turn_changes"][1]["proposed"]["request_status"] == "done"
        assert len(report["execution_changes"]) == 4
        assert all(row["step_index"] not in {50, 120} for row in report["execution_changes"])
        assert not {"answer_at", "answer_origin", "codex_completed_answers", "provider_message_id", "answer_seq", "message_id"}.intersection(report["turn_changes"][0]["proposed"])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
