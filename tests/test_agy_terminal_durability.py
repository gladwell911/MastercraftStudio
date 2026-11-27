
import json
import sqlite3
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from agy_acp import AgyAcpClient
from chat_store import ChatStore
from native_history import NativeSessionIdentity, NativeSessionSnapshot
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


@pytest.fixture
def persisted_agy_queue(frame, monkeypatch, tmp_path, request):
    entered = threading.Event()
    release = threading.Event()
    prompts = []
    owner = "queued-agy"
    identity = NativeSessionIdentity("default", "agy", "fixture-store", "native")
    chat = {"id": owner, "model": "agy/main", "title": "AGY queue", "agy_workspace": str(tmp_path),
            "agy_session_id": identity.session_id, "agy_native_owner": "fixture-owner",
            "native_identity": identity.chat_id, "native_account": "fixture-account"}
    prior = {"question": "Previous", "answer_md": "Saved answer", "model": "agy/main",
             "request_status": "done"}
    turns = [prior, *[
        {"question": question, "request_question": question, "model": "agy/main", "request_status": "queued",
         "tool_settings": {"tool": "agy", "model": model, "effort": effort, "mode": "auto"},
         "_provider_request_id": f"request-{i}"}
        for i, (question, model, effort) in enumerate([
            ("First", "gemini-4-carbon", "low"), ("Second", "gemini-4-neon", "high"),
        ])
    ]]
    frame.chat_store.upsert_chat(chat)
    frame.chat_store.replace_turns(owner, turns)
    independent = ChatStore(frame.chat_store.db_path)

    def begin(question, update, _interaction, *, attachments):
        saved = independent.load_turns(owner)
        prompts.append((question, [turn["request_status"] for turn in saved]))
        entered.set()
        def wait():
            assert release.wait(5)
            return "cancelled"

        return SimpleNamespace(wait=wait, usage={}, request_count=1)

    class Client(SimpleNamespace):
        __hash__ = object.__hash__

    client = Client(options=[], configure_desktop=lambda *_: None, begin_prompt=begin,
                    alive=True, close=lambda: None)
    monkeypatch.setattr(frame, "_open_agy_client", lambda *_a, **_kw: client)
    monkeypatch.setattr(frame, "_watch_agy_title", lambda *_: None)
    monkeypatch.setattr(frame, "_agy_config_for_tool_settings", lambda _options, settings: settings)
    monkeypatch.setattr(frame, "_refresh_agy_chat_information", lambda *_: None)

    def cleanup():
        frame._closing = True
        release.set()
        frame._close_agy_clients()

    request.addfinalizer(cleanup)
    return owner, turns, independent, entered, prompts


@pytest.mark.parametrize("restore", ["startup", "history"])
def test_persisted_agy_queue_claims_only_the_first_turn_before_sending(
        frame, persisted_agy_queue, wx_app, restore):
    owner, _, independent, entered, prompts = persisted_agy_queue
    if restore == "startup":
        frame.state_path.write_text(json.dumps({"active_chat_id": owner}), encoding="utf-8")
        frame._load_state()
    else:
        frame.archived_chats = frame.chat_store.list_chat_summaries()
        assert frame._show_history_chat(owner, focus_answer_list=False)
    wx_app.ProcessPendingEvents()
    assert entered.wait(2)
    wx_app.ProcessPendingEvents()
    assert prompts == [("First", ["done", "pending", "queued"])]
    assert independent.load_turns(owner)[1]["_provider_request_id"] == "request-0"


def test_agy_queue_keeps_prompts_unsent_during_and_after_a_failed_claim(
        frame, persisted_agy_queue, wx_app, monkeypatch, wait_for_wx_main_loop):
    owner, original, independent, _, prompts = persisted_agy_queue
    claiming = threading.Event()
    reject = threading.Event()

    def fail_claim(*_args):
        claiming.set()
        reject.wait(2)
        raise sqlite3.OperationalError("fixture disk error")

    monkeypatch.setattr(frame.chat_store, "claim_agy_queued_turn", fail_claim)
    frame.state_path.write_text(json.dumps({"active_chat_id": owner}), encoding="utf-8")
    frame._load_state()
    wx_app.ProcessPendingEvents()
    try:
        assert claiming.wait(2)
        assert not prompts
        assert independent.load_turns(owner) == original
    finally:
        reject.set()
    wait_for_wx_main_loop(lambda: owner not in frame._agy_busy, "failed claim published")
    assert not prompts
    assert independent.load_turns(owner) == original


@pytest.mark.parametrize("evidence", ["explicit", "ordinary replay", "extra"])
def test_native_recovery_of_claimed_agy_predecessor_controls_fifo(
        frame, persisted_agy_queue, wx_app, evidence):
    owner, turns, independent, entered, prompts = persisted_agy_queue
    native = NativeSessionIdentity("default", "agy", "fixture-store", "native")
    first = dict(turns[1], request_status="pending", agy_queue_claimed=True, agy_session_id=native.session_id,
                 agy_native_turn_id="first-turn", provider_user_message_id="first-user",
                 agy_observed_message_id="first-answer")
    frame.chat_store.replace_turns(owner, [turns[0], first, turns[2]])
    # Restart sees the durable claim. A deferred queued save cannot resurrect it.
    frame.chat_store.replace_turns_from(owner, turns[1:], start_index=1)
    assert independent.load_turns(owner)[1] == first
    frame.state_path.write_text(json.dumps({"active_chat_id": owner}), encoding="utf-8")
    frame._load_state()
    wx_app.ProcessPendingEvents()
    assert owner not in frame._agy_busy
    assert not prompts

    replay = [
        {"provider": "agy", "session_id": native.session_id, "question": "Previous", "answer_md": "Saved answer"},
        {"provider": "agy", "session_id": native.session_id, "question": "First",
         "agy_native_turn_id": "first-turn", "provider_user_message_id": "first-user",
         "provider_message_id": "first-answer", "answer_md": "First native answer",
         "request_status": "done", "native_final_explicit": evidence != "ordinary replay"},
    ]
    if evidence == "extra":
        replay.append(dict(replay[-1], agy_native_turn_id="other native turn"))
    snapshot = NativeSessionSnapshot(native, "fixture-account", "agy/main", ".", "AGY",
                                     updated_at=100, turns=replay, legacy_fields={"agy_session_id": native.session_id})
    revision = independent.get_clear_reconciliation_authority(owner)["revision"]
    assert frame._native_history_authority_ui(snapshot, owner)[0] == 200
    frame._native_history_reserved.add(owner)
    changed = independent.merge_native_history(snapshot, expected_chat_id=owner, expected_revision=revision)
    saved = independent.load_turns(owner)
    assert saved[2] == turns[2]
    frame._publish_native_history_ui(owner, changed, saved, snapshot)
    wx_app.ProcessPendingEvents()
    if evidence == "explicit":
        assert entered.wait(2)
        assert prompts == [("Second", ["done", "done", "pending"])]
    else:
        assert owner not in frame._agy_busy
        assert not prompts
        assert saved[1] == first


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
    assert "agy_partial_answer_md" not in persisted
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
        _, turns, _ = frame._chat_target_for_request(agy_terminal["chat_id"])
        assert "agy_partial_answer_md" not in turns[0]
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
