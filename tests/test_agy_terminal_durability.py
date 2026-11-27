
import json
import sqlite3
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from agy_acp import AgyAcpClient
from chat_store import ChatStore
from shared_terminal_routes import dispatch_terminal


@pytest.fixture
def agy_terminal(frame, tmp_path, monkeypatch, request):
    request.addfinalizer(frame._close_agy_clients)
    provider = tmp_path / "provider.py"
    provider.write_text("""
import json
import os
from pathlib import Path
import sys
import time

for line in sys.stdin:
    request = json.loads(line)
    method = request["method"]
    if method == "initialize":
        result = {"protocolVersion": 1}
    elif method == "session/new":
        result = {"sessionId": "native"}
    elif method == "session/prompt":
        gate = os.environ.get("AGY_TEST_COMPLETION_GATE")
        if gate:
            until = time.monotonic() + 8
            while not Path(gate).exists() and time.monotonic() < until:
                time.sleep(.01)
        with Path("prompt-count.txt").open("a", encoding="utf-8") as count:
            count.write("prompt\\n")
        print(json.dumps({"method": "session/update", "params": {"sessionId": "native", "update": {
            "sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": "synthetic verified result"}}}}), flush=True)
        result = {"stopReason": os.environ.get("AGY_TEST_END_REASON", "end_turn")}
    else:
        result = {}
    if request.get("id") is not None:
        print(json.dumps({"id": request["id"], "result": result}), flush=True)
""", encoding="utf-8")
    monkeypatch.setenv("MC_AGY_EXECUTABLE", sys._base_executable)
    monkeypatch.setattr(AgyAcpClient, "command", staticmethod(
        lambda executable: [executable, "-u", str(provider)]))
    monkeypatch.setattr(frame, "_schedule_first_question_auto_title", lambda *args: None)
    monkeypatch.setattr(frame, "_schedule_async_archive_rename", lambda *args: None)
    original_later = frame._call_later_if_alive

    def suppress_save_timer(ms, fn, *args, **kwargs):
        if fn == frame._flush_chat_state_save:
            return SimpleNamespace(Stop=lambda: None)
        return original_later(ms, fn, *args, **kwargs)

    def create(*, suppress_saves=True):
        if suppress_saves:
            monkeypatch.setattr(frame, "_call_later_if_alive", suppress_save_timer)
        status, created = frame._remote_api_new_chat({"model": "agy/main", "agy_workspace": str(tmp_path)})
        assert status == 200, created
        return {"provider": "agy", "chat_id": created["chat_id"], "session_id": "native", "request_id": "c" * 32}

    return create


def wait_terminal(frame, scope, wait_for_wx_main_loop, expected):
    wait_for_wx_main_loop(lambda: dispatch_terminal(frame, "watch", scope)[1].get("state") == expected,
                          f"AGY terminal state {expected}")
    return dispatch_terminal(frame, "watch", scope)[1]


@pytest.mark.parametrize("precommit", [False, True])
@pytest.mark.parametrize("reason", ["end_turn", "cancelled"])
def test_known_agy_terminal_is_durable_before_reported(
        precommit, reason, agy_terminal, frame, monkeypatch, wait_for_wx_main_loop):
    monkeypatch.setenv("AGY_TEST_END_REASON", reason)
    agy_terminal = agy_terminal()
    owner = agy_terminal["chat_id"]
    reopened = ChatStore(frame.chat_store.db_path)
    reopened.notification_sync(pair_id="default", device_id="phone", baseline=0,
                               incarnation="fixture", secret="synthetic")
    reopened.notification_sync(pair_id="laptop", device_id="other-phone", baseline=0,
                               incarnation="fixture", secret="synthetic")
    assert dispatch_terminal(frame, "send", {**agy_terminal, "text": "synthetic prompt"})[0] == 200
    _, live_turns, _ = frame._chat_target_for_request(owner)
    pending = dict(live_turns[0])
    if precommit:
        frame._flush_chat_state_save()

    expected = "done" if reason == "end_turn" else "cancelled"
    watch = wait_terminal(frame, agy_terminal, wait_for_wx_main_loop, expected)
    persisted, = reopened.load_turns(owner)
    assert persisted["request_status"] == expected
    if reason == "end_turn":
        assert watch["answer"] == persisted["answer_md"] == "synthetic verified result"
    else:
        assert reopened.readable_answer(owner, turn_index=0) is None

    reopened.replace_turns_from(owner, [pending])
    reopened.replace_turns(owner, [pending])
    assert reopened.load_turns(owner)[0] == persisted
    frame._flush_chat_state_save()
    facts = [json.loads(payload) for payload in reopened.replay_after(
        pair_id="default", domain="events", sync_sequence=0)]
    finals = [fact for fact in facts
              if fact.get("chat_id") == owner and fact.get("kind") == "assistant_final"]
    assert len(finals) == int(reason == "end_turn")
    assert not reopened.replay_after(pair_id="laptop", domain="events", sync_sequence=0)


def test_agy_sqlite_lock_delays_publication_then_completion(
        agy_terminal, frame, tmp_path, monkeypatch, wait_for_wx_main_loop):
    gate = tmp_path / "finish"
    monkeypatch.setenv("AGY_TEST_COMPLETION_GATE", str(gate))
    agy_terminal = agy_terminal(suppress_saves=False)
    reached = threading.Event()
    commit = frame.chat_store.commit_agy_terminal

    def observe_commit(*args):
        reached.set()
        return commit(*args)

    monkeypatch.setattr(frame.chat_store, "commit_agy_terminal", observe_commit)
    assert dispatch_terminal(frame, "send", {**agy_terminal, "text": "prompt under lock"})[0] == 200
    with sqlite3.connect(frame.chat_store.db_path) as lock:
        lock.execute("BEGIN IMMEDIATE")
        gate.touch()
        wait_for_wx_main_loop(reached.is_set, "terminal write attempted")
        started = time.monotonic()
        wait_for_wx_main_loop(lambda: time.monotonic() - started >= .45, "wx stays responsive past the save timer")
        assert time.monotonic() - started < 2
        assert dispatch_terminal(frame, "watch", agy_terminal)[1].get("state") != "done"
    wait_terminal(frame, agy_terminal, wait_for_wx_main_loop, "done")
    assert ChatStore(frame.chat_store.db_path).load_turns(agy_terminal["chat_id"])[0]["request_status"] == "done"


@pytest.mark.parametrize("failures", [1, 2])
def test_agy_retries_a_write_without_resending_prompt(
        failures, agy_terminal, frame, tmp_path, monkeypatch, wait_for_wx_main_loop):
    agy_terminal = agy_terminal()
    attempts = []
    commit = frame.chat_store.commit_agy_terminal

    def failing_commit(*args):
        attempts.append(args)
        if len(attempts) <= failures:
            raise sqlite3.OperationalError("synthetic lock")
        return commit(*args)

    monkeypatch.setattr(frame.chat_store, "commit_agy_terminal", failing_commit)
    assert dispatch_terminal(frame, "send", {**agy_terminal, "text": "one prompt"})[0] == 200
    expected = "done" if failures == 1 else "unknown"
    watch = wait_terminal(frame, agy_terminal, wait_for_wx_main_loop, expected)
    assert len(attempts) == 2
    assert attempts[0] == attempts[1]
    assert (tmp_path / "prompt-count.txt").read_text(encoding="utf-8").splitlines() == ["prompt"]
    if failures == 2:
        assert "本地保存未确认" in watch["message"]
        assert frame.chat_store.readable_answer(agy_terminal["chat_id"], turn_index=0) is None


@pytest.mark.parametrize("changed", ["revision", "session", "request", "deleted"])
def test_agy_terminal_commit_is_fenced_by_current_saved_owner(tmp_path, changed):
    store = ChatStore(tmp_path / "fenced.db")
    store.initialize()
    owner = "owner"
    scope = {"model": "agy/main", "agy_session_id": "original", "native_account": "owner-account"}
    chat = {"id": owner, **scope}
    turn = {"model": "agy/main", "question": "first", "_provider_request_id": "accepted",
            "request_status": "pending", "answer_md": ""}
    replacement = {"model": "agy/main", "question": "newer", "_provider_request_id": "newer",
                   "request_status": "pending", "answer_md": ""}
    store.upsert_chat(chat)
    store.replace_turns(owner, [turn])
    if changed == "revision":
        store.allocate_chat_revision(owner)
    elif changed == "session":
        store.upsert_chat(dict(chat, agy_session_id="new-session"))
    elif changed == "request":
        store.replace_turns(owner, [replacement])
    else:
        store.delete_chat(owner)
    before = store.load_turns(owner)
    assert not store.commit_agy_terminal(owner, 0, scope, 1, turn,
        {"request_status": "done", "answer_md": "obsolete reply", "agy_native_terminal_reason": "end_turn"})
    assert store.load_turns(owner) == before




def test_confirmed_agy_result_survives_close_without_wx_finish(
        agy_terminal, frame, monkeypatch, wait_for_wx_main_loop):
    scope = agy_terminal()
    delayed_finish = threading.Event()
    deliver = frame._call_after_if_alive

    def delay_agy_finish(callback, *args, **kwargs):
        if callback.__qualname__.endswith("_start_agy_worker_for_turn.<locals>.finish"):
            delayed_finish.set()
            return True
        return deliver(callback, *args, **kwargs)

    monkeypatch.setattr(frame, "_call_after_if_alive", delay_agy_finish)
    assert dispatch_terminal(frame, "send", {**scope, "text": "close before wx finish"})[0] == 200
    wait_for_wx_main_loop(delayed_finish.is_set, "committed completion queued for wx")
    reopened = ChatStore(frame.chat_store.db_path)
    assert reopened.load_turns(scope["chat_id"])[0]["request_status"] == "done"
    assert dispatch_terminal(frame, "watch", scope)[1].get("state") == "pending"
    frame.Close()
    wait_for_wx_main_loop(lambda: getattr(frame, "_close_complete", False), "graceful close commit")
    persisted, = reopened.load_turns(scope["chat_id"])
    assert persisted["request_status"] == "done"
    assert persisted["answer_md"] == "synthetic verified result"




@pytest.mark.parametrize("source", ["local", "remote"])
def test_known_agy_result_remains_bound_to_original_chat_after_switch(
        source, agy_terminal, frame, tmp_path, monkeypatch, wait_for_wx_main_loop):
    gate = tmp_path / "finish"
    monkeypatch.setenv("AGY_TEST_COMPLETION_GATE", str(gate))
    scope = agy_terminal()
    owner = scope["chat_id"]
    if source == "local":
        frame.input_edit.SetValue("local original prompt")
        assert frame._on_send_clicked(None)
    else:
        status, response = frame._remote_api_message_ui({"chat_id": owner, "text": "remote original prompt"})
        assert status == 200, response
    status, current = frame._remote_api_new_chat({"model": "gpt-4o-mini"})
    assert status == 200, current
    assert frame.active_chat_id == current["chat_id"] != owner
    gate.touch()
    wait_for_wx_main_loop(lambda: owner not in frame._agy_busy, "archived owner's published terminal")
    reopened = ChatStore(frame.chat_store.db_path)
    persisted, = reopened.load_turns(owner)
    assert persisted["request_status"] == "done"
    assert persisted["answer_md"] == "synthetic verified result"
    assert not reopened.load_turns(current["chat_id"])




def test_close_waits_for_already_admitted_agy_terminal_write(
        agy_terminal, frame, monkeypatch, wait_for_wx_main_loop):
    scope = agy_terminal()
    reached = threading.Event()
    release = threading.Event()
    commit = frame.chat_store.commit_agy_terminal

    def hold_commit(*args):
        reached.set()
        assert release.wait(10)
        return commit(*args)

    monkeypatch.setattr(frame.chat_store, "commit_agy_terminal", hold_commit)
    assert dispatch_terminal(frame, "send", {**scope, "text": "close while saving"})[0] == 200
    wait_for_wx_main_loop(reached.is_set, "accepted AGY terminal write")
    reopened = ChatStore(frame.chat_store.db_path)
    frame.Close()
    try:
        wait_for_wx_main_loop(frame._close_producers_done.is_set, "close producer fence reached")
        assert not frame._close_write_started
    finally:
        release.set()
    wait_for_wx_main_loop(lambda: getattr(frame, "_close_complete", False), "graceful close commit")
    persisted, = reopened.load_turns(scope["chat_id"])
    assert persisted["request_status"] == "done"
    assert persisted["answer_md"] == "synthetic verified result"




@pytest.mark.parametrize("changed", ["request", "session"])
def test_rejected_terminal_does_not_publish_or_resave_old_scope(
        changed, agy_terminal, frame, tmp_path, monkeypatch, wait_for_wx_main_loop):
    gate = tmp_path / "finish"
    monkeypatch.setenv("AGY_TEST_COMPLETION_GATE", str(gate))
    scope = agy_terminal()
    owner = scope["chat_id"]
    store = ChatStore(frame.chat_store.db_path)
    assert dispatch_terminal(frame, "send", {**scope, "text": "obsolete prompt"})[0] == 200
    frame._flush_chat_state_save()
    replacement = {"model": "agy/main", "question": "current", "_provider_request_id": "replacement",
                   "request_status": "pending", "answer_md": ""}
    store.replace_turns(owner, [replacement])
    if changed == "session":
        stored_chat = store.load_chat_summary(owner)
        store.upsert_chat(dict(stored_chat, agy_session_id="replacement-session"))
    gate.touch()
    wait_for_wx_main_loop(lambda: owner not in frame._agy_busy, "rejected terminal retired")
    frame._flush_chat_state_save()
    assert store.load_turns(owner) == [replacement]
    assert frame.active_session_turns == [replacement]
    assert store.readable_answer(owner, turn_index=0) is None
    if changed == "session":
        assert store.load_chat_summary(owner)["agy_session_id"] == "replacement-session"
