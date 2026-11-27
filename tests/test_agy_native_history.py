import json
import sys

import pytest

from agy_acp import AgyAcpClient
from agy_history import _AgyReplay
from chat_store import ChatStore
from native_history import NativeSessionIdentity, NativeSessionSnapshot


FAKE_AGY = r"""
import json, os, sys
from pathlib import Path

def write(payload):
    print(json.dumps({"jsonrpc": "2.0", **payload}), flush=True)

def update(session, update_kind, **fields):
    write({"method": "session/update", "params": {"sessionId": session,
           "update": {"sessionUpdate": update_kind, **fields}}})

for line in sys.stdin:
    request = json.loads(line)
    method = request["method"]
    params = request["params"]
    with open(os.environ["AGY_TRACE"], "a", encoding="utf-8") as stream:
        stream.write(json.dumps({**request, 'fixturePid': os.getpid()}) + '\n')
    source = json.loads(Path(os.environ["AGY_SOURCE"]).read_text(encoding="utf-8"))
    session = params.get("sessionId", "app-session")
    if method == "initialize":
        result = {"protocolVersion": 1, "agentCapabilities": {"loadSession": True,
                  "sessionCapabilities": {"list": {}, "resume": {}}},
                  "_meta": {"accountId": "fixture-account", "storageId": "fixture-store"}}
    elif method == "session/list":
        if not params.get("cursor"):
            result = {"sessions": [], "nextCursor": "second-page"}
        else:
            result = {"sessions": [source["session"]]}
    elif method == "session/load":
        assert params["cwd"] == source.get("expected_cwd", source["session"]["cwd"])
        for index, pair in enumerate(source["turns"]):
            update(session, "user_message_chunk", messageId=f"u{index}",
                   content={"type": "text", "text": pair[0]})
            update(session, "tool_call", toolCallId=f"tool{index}", title="read fixture",
                   kind="read", status="in_progress")
            update(session, "tool_call_update", toolCallId=f"tool{index}", status="completed",
                   rawOutput="fixture output")
            update(session, "agent_message_chunk", messageId=f"a{index}",
                   content={"type": "text", "text": pair[1]})
        result = {"sessionId": session}
    elif method == "session/prompt":
        assert session == source["session"]["sessionId"]
        update(session, "session_info_update", title="AGY title from live event")
        update(session, "agent_message_chunk", content={"type": "text", "text": "same native context"})
        result = {"stopReason": "end_turn"}
    else:
        result = {"sessionId": session}
    write({"id": request["id"], "result": result})
"""


@pytest.mark.parametrize("provider_drive_path", [
    False,
    pytest.param(True, marks=pytest.mark.skipif(sys.platform != "win32", reason="Windows AGY path")),
])
def test_discovers_replays_and_resumes_native_history(
        frame, tmp_path, monkeypatch, wait_for_wx, provider_drive_path):
    script = tmp_path / "agy_fixture.py"
    script.write_text(FAKE_AGY, encoding="utf-8")
    trace = tmp_path / "trace.jsonl"
    fixture = tmp_path / "source.json"
    project = tmp_path / "native project"
    project.mkdir()
    fixture.write_text(json.dumps({
        "expected_cwd": str(project),
        "session": {"sessionId": "terminal-id",
                    "cwd": "/" + project.as_posix() if provider_drive_path else str(project),
                    "title": "原生标题",
                    "updatedAt": "2026-11-26T06:00:00Z"},
        "turns": [["同文问题", "完整正文和尾标"], ["同文问题", "完整正文和尾标"]],
    }), encoding="utf-8")
    monkeypatch.setenv("AGY_TRACE", str(trace))
    monkeypatch.setenv("AGY_SOURCE", str(fixture))
    monkeypatch.setenv("MC_AGY_EXECUTABLE", sys._base_executable)
    monkeypatch.setattr(AgyAcpClient, "command", staticmethod(
        lambda executable: [executable, "-u", str(script)]))

    frame.chat_store.notification_sync(pair_id="default", device_id="private-device",
                                       baseline=0, incarnation="test", secret="test")

    def listed():
        return [row for row in frame._remote_api_history_list_ui()[1]["chats"]
                if row.get("title") == "原生标题"]

    wait_for_wx(listed, "native history in the existing MC collection")
    row, = listed()
    chat_id = row["chat_id"]
    assert len(frame._remote_api_history_collection_ui({"collection_page": True})[1]["chats"]) >= 1
    history = frame._remote_api_history_read_ui({"chat_id": chat_id, "limit": 1})[1]
    assert history["has_more"]
    assert not frame.chat_store.pending_outbox(pair_id="default", lane="notification")
    turn, = history["chat"]["turns"]
    assert (turn["question"], turn["answer"]) == ("同文问题", "完整正文和尾标")
    assert frame.chat_store.load_execution_steps(chat_id)[-1]["detail_text"] == "fixture output"
    requests = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    assert "session/prompt" not in {item["method"] for item in requests}
    assert len({turn["turn_id"] for turn in frame.chat_store.load_turns(chat_id)}) == 2
    status, alias = frame._remote_api_rename_chat_ui({"chat_id": chat_id, "title": "共享显示名"})
    assert status == 200, alias

    if provider_drive_path:
        # A row imported before normalization keeps its original workspace across refresh.
        legacy = frame._find_archived_chat(chat_id)
        legacy["agy_workspace"] = legacy["native_cwd"] = "/" + project.as_posix()
        frame.chat_store.upsert_chat(legacy)
        assert frame.chat_store.load_chat_summary(chat_id)["agy_workspace"] == legacy["agy_workspace"]

    status, accepted = frame._remote_api_message_ui({"chat_id": chat_id, "text": "续聊", "model": "agy/main"})
    assert status == 200, accepted
    wait_for_wx(lambda: frame._remote_api_state_ui({"chat_id": chat_id})[1]["turns"][-1]["answer"]
                == "same native context", "continuation of the original native ID")
    frame._flush_chat_state_save()
    live = frame._remote_api_history_read_ui({"chat_id": chat_id})[1]["chat"]
    assert (live["title"], live["agy_native_title"]) == ("共享显示名", "AGY title from live event")
    requests = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    resumed, = [item["params"] for item in requests if item["method"] == "session/resume"]
    assert (resumed["sessionId"], resumed["cwd"]) == ("terminal-id", str(project))

    # An independent terminal advanced the store after the in-process session
    # was already open. Refresh must use a fresh ACP process to replay it.
    source = json.loads(fixture.read_text(encoding="utf-8"))
    source["session"]["updatedAt"] = "2026-11-26T06:05:00Z"
    source["session"]["title"] = "新终端标题"
    source["turns"].extend([["续聊", "same native context"], ["独立终端续聊", "新的已结算回答"]])
    fixture.write_text(json.dumps(source), encoding="utf-8")
    def refreshed():
        rows = frame._remote_api_history_list_ui()[1]["chats"]
        return [row for row in rows if row["chat_id"] == chat_id and row["turn_count"] == 4]

    wait_for_wx(refreshed, "new external history", timeout=6)
    history = frame._remote_api_history_read_ui({"chat_id": chat_id, "limit": 1})[1]
    assert history["chat"]["turns"][0]["answer"] == "新的已结算回答"
    assert history["chat"]["title"] == "共享显示名"
    assert history["chat"]["agy_native_title"] == "新终端标题"
    assert frame.chat_store.load_chat_summary(chat_id)["title"] == "共享显示名"
    assert len(refreshed()) == 1
    # The application continuation produced one live final. ACP replay gives
    # neither a settled terminal event nor enough evidence to notify its tail.
    fact, = frame.chat_store.pending_outbox(pair_id="default", lane="notification")
    assert frame.chat_store.readable_answer(chat_id, turn_index=2)["message_id"] in fact["event_id"]
    requests = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    replay_pids = {item["fixturePid"] for item in requests if item["method"] == "session/load"}
    assert len(replay_pids) == 2
    assert sum(item["method"] == "session/prompt" for item in requests) == 1




def test_native_history_scope_and_revision_preserve_existing_cache(tmp_path):
    from chat_store import ChatStore
    from native_history import NativeSessionIdentity, NativeSessionSnapshot

    store = ChatStore(tmp_path / "identities.db")
    store.initialize()
    scopes = [("default", "agy", "standard"), ("laptop", "agy", "standard"),
              ("default", "agy", "alternate"), ("default", "codex", "standard")]
    owners = []
    for pair, provider, storage in scopes:
        entry = NativeSessionSnapshot(NativeSessionIdentity(pair, provider, storage, "same-id"),
                                      "account", f"{provider}/main", str(tmp_path), "same title", 1,
                                      turns=[{"question": "same question", "answer_md": "full answer",
                                              "request_status": "done", "provider_user_message_id": "m1"}])
        owner, version = store.native_history_target(entry)
        assert version == 0
        assert store.merge_native_history(entry, expected_chat_id=owner)["turn_count"] == 1
        assert store.get_chat_read_state(owner, pair_id=pair)["latest_readable_seq"] > 0
        owners.append(owner)

    assert len(set(owners)) == len(scopes)
    assert len(store.list_chat_summaries()) == len(scopes)

    owner = owners[-1]
    revision = store.allocate_chat_revision(owner)
    entry.updated_at = 2
    entry.turns[0]["answer_md"] = "full"
    entry.turns.append(dict(entry.turns[0], provider_user_message_id="m2"))
    assert store.merge_native_history(entry, expected_chat_id=owner, expected_revision=revision - 1) is None
    assert len(store.load_turns(owner)) == 1
    assert store.merge_native_history(entry, expected_chat_id=owner, expected_revision=revision)["turn_count"] == 2
    assert store.load_turns(owner)[0]["answer_md"] == "full answer"
    store.delete_chat(owner)
    assert store.native_history_target(entry) == ("", 0)


def test_provider_busy_blocks_an_original_agy_session_until_a_fresh_check(
        frame, tmp_path, monkeypatch, wait_for_wx):
    script = tmp_path / "agy_fixture.py"
    script.write_text(FAKE_AGY, encoding="utf-8")
    trace = tmp_path / "trace.jsonl"
    source_path = tmp_path / "source.json"
    source = {"session": {"sessionId": "busy-original", "cwd": str(tmp_path),
                          "title": "External busy", "isRunning": True}, "turns": []}
    source_path.write_text(json.dumps(source), encoding="utf-8")
    monkeypatch.setenv("AGY_TRACE", str(trace))
    monkeypatch.setenv("AGY_SOURCE", str(source_path))
    monkeypatch.setenv("MC_AGY_EXECUTABLE", sys._base_executable)
    monkeypatch.setattr(AgyAcpClient, "command", staticmethod(
        lambda executable: [executable, "-u", str(script)]))

    def row():
        return next((chat for chat in frame._remote_api_history_list_ui()[1]["chats"]
                     if chat["title"] == "External busy"), None)

    wait_for_wx(row, "discovered externally busy AGY session")
    chat_id = row()["chat_id"]

    def send(text):
        return frame._remote_api_message_ui({"chat_id": chat_id, "text": text, "model": "agy/main"})

    def turn():
        return frame._remote_api_state_ui({"chat_id": chat_id})[1]["turns"][-1]

    assert send("do not submit while busy")[0] == 200
    wait_for_wx(lambda: turn().get("request_status") != "pending", "busy result")
    assert "另一进程" in turn()["request_error"]
    assert not any(json.loads(line)["method"] == "session/prompt"
                   for line in trace.read_text(encoding="utf-8").splitlines())

    source["session"]["isRunning"] = False
    source_path.write_text(json.dumps(source), encoding="utf-8")
    assert send("explicitly retry after idle")[0] == 200
    wait_for_wx(lambda: turn().get("request_status") == "done", "same session becomes available")
    sent, = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()
             if json.loads(line)["method"] == "session/prompt"]
    assert sent["params"]["sessionId"] == "busy-original"


@pytest.mark.parametrize(("mismatch", "stored_status", "tool_status", "affirmative"), [
    ("", "unknown", "completed", False),
    ("", "unknown", "in_progress", False),
    ("", "cancelled", "completed", False),
    ("", "failed", "failed", False),
    ("", "unknown", "completed", True),
    *((mismatch, "unknown", "completed", True) for mismatch in ("session", "turn", "user", "answer")),
])
def test_native_replay_requires_affirmative_evidence_for_unknown_agy_turn(
        tmp_path, mismatch, stored_status, tool_status, affirmative):
    store = ChatStore(tmp_path / "chat.db")
    store.initialize()
    identity = NativeSessionIdentity("default", "agy", "fixture-store", "session")
    owner = identity.chat_id
    snapshot = NativeSessionSnapshot(
        identity, "fixture-account", "agy/main", str(tmp_path), "AGY",
        updated_at=100, chat_fields={"agy_session_id": identity.session_id},
    )
    chat = store.merge_native_history(snapshot, expected_chat_id=owner)
    error = "ACP disconnected before confirmation"
    chat.update(native_unresolved_turn={"index": 0, "message": error}, native_history_status=error)
    store.upsert_chat(chat)
    stored = {
        "question": "first", "answer_md": "partial", "request_status": stored_status,
        "provider_user_message_id": "user1", "agy_native_turn_id": "turn1",
        "agy_observed_message_id": "answer1", "terminal_notification_pair": identity.pair_id,
    }
    if mismatch == "session":
        stored["session_id"] = "other-session"
    elif field := {"turn": "agy_native_turn_id", "user": "provider_user_message_id",
                   "answer": "agy_observed_message_id"}.get(mismatch):
        stored[field] = "other-id"
    store.replace_turns(owner, [stored])
    store.append_execution_step(owner, {
        "turn_idx": 0, "thread_id": identity.session_id, "provider": "agy",
        "item_id": "tool:observed", "display_kind": "tool", "list_text": "Saved live tool",
    })

    def replay(*, with_successor):
        history = _AgyReplay(identity.session_id)
        rows = [("user", "turn1", "user1", "first"),
                ("agent", "turn1", "answer1", "partial then complete")]
        if with_successor:
            rows += [("user", "turn2", "user2", "second"),
                     ("agent", "turn2", "answer2", "second final")]
        for role, turn, message, body in rows:
            history.update({"sessionUpdate": f"{role}_message_chunk", "turnId": turn,
                            "messageId": message, "content": {"type": "text", "text": body}})
            if message == "answer1":
                for tool in ("observed", "replay-only"):
                    history.update({"sessionUpdate": "tool_call", "turnId": turn,
                                    "toolCallId": tool, "status": tool_status})
        snapshot.turns, snapshot.execution_steps = history.completed_history()
        if affirmative and with_successor:
            # Normalized evidence from a trusted adapter. Ordinary ACP replay
            # in this fixture emits neither a completion nor a stop reason.
            snapshot.turns[0]["native_final_explicit"] = True
        snapshot.updated_at += 100
        store.merge_native_history(snapshot, expected_chat_id=owner)
        return [(row["request_status"], row["answer_md"]) for row in store.load_turns(owner)]

    assert replay(with_successor=False) == [(stored_status, "partial")]
    assert store.load_chat_summary(owner)["native_history_status"] == error
    expected = [(stored_status, "partial")]
    if not mismatch:
        if affirmative:
            expected = [("done", "partial then complete")]
        expected.append(("done", "second final"))
    assert replay(with_successor=True) == expected
    assert store.load_chat_summary(owner)["native_history_status"] == ("" if affirmative and not mismatch else error)
    assert replay(with_successor=True) == expected
    steps = store.load_execution_steps(owner, 0)
    assert [step["item_id"] for step in steps] == (
        ["tool:observed"] if mismatch else ["tool:observed", "tool:replay-only"]
    )
    if not mismatch:
        assert steps[1]["status"] == tool_status


def test_agy_process_exit_keeps_observed_ids_and_never_publishes_a_final(
        frame, tmp_path, monkeypatch, wait_for_wx_main_loop):
    from chat_store import ChatStore
    from shared_terminal_routes import dispatch_terminal

    script = tmp_path / "agy_interrupted.py"
    script.write_text(FAKE_AGY.replace(
        'update(session, "agent_message_chunk", content={"type": "text", "text": "same native context"})',
        r'''update(session, "user_message_chunk", turnId="native-turn", messageId="native-user",
               content={"type": "text", "text": "interrupted question"})
        update(session, "agent_message_chunk", turnId="native-turn", messageId="native-part",
               content={"type": "text", "text": "unfinished content"})
        while not Path("exit-provider").exists():
            __import__("time").sleep(.01)
        sys.exit(3)''',
    ), encoding="utf-8")
    trace = tmp_path / "trace.jsonl"
    source_path = tmp_path / "source.json"
    source_path.write_text(json.dumps({
        "session": {"sessionId": "original", "cwd": str(tmp_path), "title": "Original AGY"},
        "turns": [],
    }), encoding="utf-8")
    monkeypatch.setenv("AGY_TRACE", str(trace))
    monkeypatch.setenv("AGY_SOURCE", str(source_path))
    monkeypatch.setenv("MC_AGY_EXECUTABLE", sys._base_executable)
    monkeypatch.setattr(AgyAcpClient, "command", staticmethod(
        lambda executable: [executable, "-u", str(script)]))
    db = ChatStore(frame.chat_store.db_path)
    db.notification_sync(pair_id="default", device_id="private-device", baseline=0,
                         incarnation="test", secret="test")
    rows = lambda: [row for row in frame._remote_api_history_list_ui()[1]["chats"]
                    if row["title"] == "Original AGY"]
    wait_for_wx_main_loop(rows, "original AGY history")
    owner = rows()[0]["chat_id"]
    scope = {"provider": "agy", "chat_id": owner, "session_id": "original", "request_id": "b" * 32}
    assert dispatch_terminal(frame, "send", {**scope, "text": "interrupted question"})[0] == 200

    def has_native_id():
        return any(turn.get("agy_native_turn_id") == "native-turn" for turn in db.load_turns(owner))

    try:
        wait_for_wx_main_loop(has_native_id, "durable AGY identity before EOF")
    finally:
        (tmp_path / "exit-provider").touch()
    wait_for_wx_main_loop(lambda: dispatch_terminal(frame, "watch", scope)[1].get("state") == "unknown",
                "typed unknown AGY outcome")
    frame._flush_chat_state_save()
    stored, = db.load_turns(owner)
    assert stored["agy_native_turn_id"] == "native-turn"
    assert stored["provider_user_message_id"] == "native-user"
    assert stored["agy_observed_message_id"] == "native-part"
    assert not stored["answer_md"] and stored["request_recoverable"] is False
    assert not db.pending_outbox(pair_id="default", lane="notification")
    assert dispatch_terminal(frame, "send", {**scope, "text": "interrupted question"})[0] == 200
    sent = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()
            if json.loads(line)["method"] == "session/prompt"]
    assert len(sent) == 1
