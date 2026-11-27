"""History selection, source routing, and durable native suppression."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import os
import threading

import pytest

from chat_store import ChatStore
from mc_frame_agy import AgyRun
from native_history import NativeSessionIdentity, NativeSessionSnapshot
from remote_nats import RemoteNatsTransport
from test_codex_native_history import codex_homes, real_codex
from test_kimi_runtime_usage_e2e import gated_reply, loopback_kimi_home


@pytest.mark.parametrize("provider,model", [("kimi", "kimi/main"), ("agy", "agy/main"), ("codex", "codex/main")])
def test_history_hide_before_first_discovery_preserves_other_sources(provider, model, frame, monkeypatch, tmp_path):
    storage = str(tmp_path / "kimi")
    fields = {
        "kimi": {"kimi_session_id": "equal-id", "kimi_storage_id": storage},
        "codex": {"codex_thread_id": "equal-id", "codex_storage_id": storage},
        "agy": {"agy_session_id": "equal-id", "native_identity":
                NativeSessionIdentity("default", "agy", storage, "equal-id").chat_id},
    }[provider]
    chat = {"id": "new-chat", "title": "Before discovery", "model": model, **fields}
    frame.chat_store.upsert_chat(chat)
    frame.archived_chats = frame.chat_store.list_chat_summaries()
    frame._refresh_history()
    frame.history_list.SetSelection(frame.history_ids.index(chat["id"]))
    monkeypatch.setattr(frame, "_confirm", lambda *_: True)
    frame._history_delete(None)
    assert chat["id"] not in frame.history_ids

    reopened = ChatStore(frame.chat_store.db_path)
    for pair in ("default", "laptop"):
        session = NativeSessionSnapshot(
            NativeSessionIdentity(pair, provider, storage, "equal-id"), "synthetic",
            model, str(tmp_path), "From CLI",
        )
        target, _ = reopened.native_history_target(session)
        assert target == ("" if pair == "default" else session.identity.chat_id)


def test_busy_native_session_survives_remote_and_bulk_hide(frame, monkeypatch, tmp_path):
    native_id = NativeSessionIdentity("default", "agy", str(tmp_path), "original").chat_id
    busy = {"id": "busy-agy", "model": "agy/main", "title": "Waiting for input",
            "agy_session_id": "original", "native_identity": native_id}
    idle = {"id": "idle-chat", "model": "test/model", "title": "Idle"}
    for chat in (busy, idle):
        frame.chat_store.upsert_chat(chat)
    frame.archived_chats = frame.chat_store.list_chat_summaries()
    frame._refresh_history()
    run = AgyRun(0, lambda: True, frame._agy_chat_scope(busy))
    run.interactions["synthetic-input"] = ("elicitation/create", {})
    frame._agy_busy[busy["id"]] = run
    monkeypatch.setattr(frame, "_confirm", lambda *_: True)
    try:
        transport = RemoteNatsTransport(pair_id="default", token="synthetic",
                                        on_history_mutation=frame._remote_api_history_mutation)
        status, response = transport._route_command({"type": "hide_history_chat", "chat_id": busy["id"]})
        assert status == 409 and response["accepted"] is False
        frame._history_clear_non_pinned(None)
        assert busy["id"] in frame.history_ids and idle["id"] not in frame.history_ids
        assert frame.chat_store.load_chat_summary(busy["id"])["native_identity"] == native_id
        assert not frame.chat_store.get_meta(f"hidden-native-history:{native_id}")
        assert frame._agy_busy[busy["id"]] is run
        assert "synthetic-input" in run.interactions
    finally:
        frame._agy_busy.pop(busy["id"], None)


def test_remote_native_delete_requires_exact_confirmation_and_authority(frame, monkeypatch, tmp_path, wait_for_wx):
    chat = {"id": "delete-kimi", "title": "Synthetic session", "model": "kimi/main",
            "kimi_session_id": "synthetic-target", "kimi_storage_id": str(tmp_path)}
    frame.chat_store.upsert_chat(chat)
    frame.archived_chats = frame.chat_store.list_chat_summaries()
    frame._refresh_history()
    transport = RemoteNatsTransport(pair_id="default", token="synthetic",
                                    on_history_mutation=frame._remote_api_history_mutation)
    request = {"type": "native_delete_target", "chat_id": chat["id"]}
    status, response = transport._route_command(request)
    assert status == 200
    assert response["target"]["session_id"] == "synthetic-target"
    delete = {**request, "type": "delete_native_session", "target": response["target"]}
    assert transport._route_command(delete)[0] == 409
    assert transport._route_command({**delete, "confirmed": True,
        "target": {**delete["target"], "pair_id": "laptop"}})[0] == 409
    assert chat["id"] in frame.history_ids
    called = []

    def delete_native(*_):
        called.append(True)
        return len(called) == 2

    monkeypatch.setattr(frame, "_delete_or_verify_provider_session", delete_native)
    frame._find_archived_chat(chat["id"])["kimi_turn_active"] = True
    assert transport._route_command({**delete, "confirmed": True})[0] == 409
    assert not called
    frame._find_archived_chat(chat["id"])["kimi_turn_active"] = False
    with ThreadPoolExecutor() as worker:
        for expected in (503, 200):
            pending = worker.submit(transport._route_command, {**delete, "confirmed": True})
            wait_for_wx(pending.done, "remote native delete")
            assert pending.result()[0] == expected
            assert (chat["id"] in frame.history_ids) == (expected == 503)


def test_close_drains_a_confirmed_native_delete_before_the_write_fence(
        frame, monkeypatch, tmp_path, wait_for_wx):
    owner = "delete-during-close"
    chat = {"id": owner, "title": "Synthetic session", "model": "kimi/main",
            "kimi_session_id": "synthetic-target", "kimi_storage_id": str(tmp_path)}
    db_path = frame.chat_store.db_path
    frame.chat_store.upsert_chat(chat)
    frame.archived_chats = frame.chat_store.list_chat_summaries()
    frame._refresh_history()
    entered, release = threading.Event(), threading.Event()

    def delete_native(*_):
        entered.set()
        assert release.wait(5)
        assert not getattr(frame, "_close_producer_fenced", False)
        return True

    monkeypatch.setattr(frame, "_delete_or_verify_provider_session", delete_native)
    transport = RemoteNatsTransport(pair_id="default", token="synthetic",
                                    on_history_mutation=frame._remote_api_history_mutation)
    status, detail = transport._route_command({"type": "native_delete_target", "chat_id": owner})
    assert status == 200
    with ThreadPoolExecutor() as caller:
        pending = caller.submit(transport._route_command, {
            "type": "delete_native_session", "chat_id": owner,
            "target": detail["target"], "confirmed": True,
        })
        wait_for_wx(entered.is_set, "provider started the confirmed native delete")
        try:
            frame.Close()
            assert not frame.IsShown()
            assert not frame._close_producers_done.wait(.3)
        finally:
            release.set()
            wait_for_wx(pending.done, "native deletion remote response")
        assert pending.result()[0] == 200
    wait_for_wx(lambda: getattr(frame, "_close_complete", False), "close completes after confirmed delete")
    assert ChatStore(db_path).load_chat_summary(owner) is None


@pytest.mark.parametrize(("version", "supported"), [("1.5.17", True), ("1.5.18", True), ("1.5.99", False)])
def test_agy_confirmation_reads_original_directory_and_never_sends_delete(
        frame, tmp_path, monkeypatch, wait_for_wx, version, supported):
    import agy_history
    import mc_frame_native_history
    from agy_acp import AGY_MODEL_ID, AgyError

    owner, sid = "agy-fixture", "synthetic-agy-session"
    account, storage = "fixture-account", "fixture-storage"
    identity = NativeSessionIdentity("default", "agy", storage, sid)
    frame.chat_store.upsert_chat({
        "id": owner, "title": "AGY fixture", "model": AGY_MODEL_ID, "agy_session_id": sid,
        "native_cwd": str(tmp_path), "native_account": account, "native_identity": identity.chat_id,
    })
    frame.archived_chats = frame.chat_store.list_chat_summaries()
    frame._refresh_history()
    requests = []

    class Authority:
        capabilities = {"loadSession": True, "sessionCapabilities": {"list": {}}}
        initialization = {"agentInfo": {"version": version}}

        def native_storage(self):
            return storage

        def native_account(self):
            return account

        def request(self, method, params):
            requests.append((method, params))
            if method == "session/list":
                return {"sessions": []}
            if method == "session/load":
                raise AgyError("Synthetic native error", code=-32002, rpc_message=f"Session not found: {sid}")
            raise AssertionError(method)

    @contextmanager
    def original_directory(_, cwd):
        assert cwd == str(tmp_path)
        yield Authority()

    monkeypatch.setattr(mc_frame_native_history, "find_agy_executable", lambda: "synthetic-agy")
    monkeypatch.setattr(agy_history, "_reading_client", original_directory)
    status, detail = frame._native_delete_target_ui(owner)
    assert status == 200
    assert detail["manual"] is True
    assert "F4" in detail["description"]
    response = _confirm_remote_removal(frame, owner, wait_for_wx, expected_status=200 if supported else 409)
    if supported:
        assert [method for method, _ in requests] == ["session/list", "session/load"]
    else:
        assert "当前 AGY 版本" in response["message"]
        assert requests == []


def _confirm_remote_removal(frame, owner, wait_for_wx, *, expected_status=200):
    transport = RemoteNatsTransport(pair_id="default", token="synthetic",
                                    on_history_mutation=frame._remote_api_history_mutation)
    status, detail = transport._route_command({"type": "native_delete_target", "chat_id": owner})
    assert status == 200, detail
    with ThreadPoolExecutor() as worker:
        pending = worker.submit(transport._route_command, {
            "type": "delete_native_session", "chat_id": owner,
            "target": detail["target"], "confirmed": True,
        })
        wait_for_wx(pending.done, "provider confirmed removal", timeout=35)
        status, response = pending.result()
    assert status == expected_status, response
    assert (frame.chat_store.load_chat_summary(owner) is None) == (expected_status == 200)
    return response


@real_codex
def test_codex_native_deletion_is_proven_in_original_store(codex_homes, frame, wait_for_wx):
    from codex_client import CodexAppServerClient

    ordinary, private, work, _, _, native = codex_homes
    thread_id = native(ordinary, work, "Owned synthetic deletion fixture")
    retained = native(private, work, "Separate synthetic store")
    lookup = CodexAppServerClient(native_home=ordinary, use_native_config=True)
    other = CodexAppServerClient(native_home=private, use_native_config=True)
    try:
        def owner():
            frame._remote_api_history_list_ui()
            return next((row["id"] for row in frame.chat_store.list_chat_summaries()
                         if row.get("codex_thread_id") == thread_id and row["id"] in frame.history_ids), "")

        wait_for_wx(owner, "Codex synthetic history", timeout=20)
        _confirm_remote_removal(frame, owner(), wait_for_wx)
        rows = lookup.request("thread/list", {"sourceKinds": ["exec"], "modelProviders": []})["data"]
        assert all(row["id"] != thread_id for row in rows)
        assert other.read_thread(retained, include_turns=False)["thread"]["id"] == retained
    finally:
        lookup.close()
        other.close()


@pytest.mark.skipif(os.getenv("MC_KIMI_RUNTIME_TEST") != "1", reason="requires an isolated Kimi runtime")
def test_kimi_native_deletion_is_proven_by_server_readback(loopback_kimi_home, frame, wait_for_wx):
    from kimi_server_client import KimiServerClient

    workspace, _ = loopback_kimi_home
    lookup = KimiServerClient()
    try:
        lookup.start(timeout=10)
        sid = lookup.create_session(cwd=str(workspace))
        lookup.close()

        def owner():
            frame._remote_api_history_list_ui()
            return next((row["id"] for row in frame.chat_store.list_chat_summaries()
                         if row.get("kimi_session_id") == sid and row["id"] in frame.history_ids), "")

        wait_for_wx(owner, "Kimi synthetic history", timeout=20)
        _confirm_remote_removal(frame, owner(), wait_for_wx)
        lookup.close()
        lookup = KimiServerClient()
        lookup.start(timeout=10)
        assert lookup.session_exists(sid) is False
    finally:
        lookup.close()
