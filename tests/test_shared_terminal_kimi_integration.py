import concurrent.futures
import uuid

import pytest

import main
from kimi_test_support import FakeKimiServerClient, _sdk_push
from kimi_server_client import KimiServerClient
from remote_nats import RemoteNatsTransport
from shared_terminal import TerminalClient, TerminalServer
from shared_terminal_routes import dispatch_terminal


def test_terminal_reuses_kimi_session_and_the_remote_chat_observes_its_turn(frame, monkeypatch, tmp_path, wait_for_wx):
    fake = FakeKimiServerClient(on_message=frame._on_kimi_client_message)
    fake.get_auth = lambda *, timeout: {"managed_provider": {"status": "authenticated"}}
    model = main.DEFAULT_KIMI_MODEL
    session = fake.create_session(cwd=str(tmp_path), model=model, title="Existing Kimi session")
    chat = {"id": "existing", "title": "Existing Kimi session", "model": model, "kimi_session_id": session}
    frame.chat_store.upsert_chat(chat)
    frame.archived_chats = frame.chat_store.list_chat_summaries()
    monkeypatch.setattr(frame, "_ensure_kimi_client", lambda: fake)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)

    frame.input_edit.SetValue("Unsent desktop draft")
    scope = {"provider": "kimi", "chat_id": "existing", "session_id": session, "request_id": uuid.uuid4().hex}
    with TerminalServer(tmp_path / "endpoint.json", lambda op, body: dispatch_terminal(frame, op, body)) as server:
        client = TerminalClient.from_endpoint(server.endpoint)
        with concurrent.futures.ThreadPoolExecutor() as pool:
            def request(op, payload):
                result = pool.submit(client.request, op, payload)
                wait_for_wx(result.done, op)
                return result.result()

            code, listing = request("list", {"provider": "kimi"})
            assert code == 200
            assert listing["chats"][0]["session_id"] == session
            assert request("send", {**scope, "session_id": "wrong", "text": "unwanted"})[0] == 409
            payload = {**scope, "text": "terminal question"}
            assert request("send", payload)[0] == 200
            assert request("send", payload)[0] == 200
            wait_for_wx(lambda: bool(fake.submitted), "one Kimi prompt")
            assert len(fake.submitted) == 1
            assert fake.submitted[0]["session_id"] == session
            assert frame.input_edit.GetValue() == "Unsent desktop draft"
            prompt = fake.submitted[0]["prompt_id"]
            wait_for_wx(lambda: bool(frame._find_kimi_prompt_owner(prompt, session_id=session, chat_id="existing")),
                        "Kimi prompt owner")
            status, pending = request("watch", scope)
            assert status == 200 and pending["state"] == "pending"
            assert frame._remote_api_history_read_ui({"chat_id": "existing"})[1]["chat"]["turns"][-1]["pending"]
            kimi_wire = KimiServerClient(start_reader_thread=False)
            _sdk_push(frame, kimi_wire, session, "turn.started", 1, chat_id="existing", promptId=prompt)
            _sdk_push(frame, kimi_wire, session, "turn.step.started", 2, chat_id="existing", step=1, stepId="wire-step")
            _sdk_push(frame, kimi_wire, session, "thinking.delta", 2, chat_id="existing", offset=0, delta="公开过程")
            wait_for_wx(lambda: bool(frame.chat_store.load_recent_execution_steps("existing", visible_only=True)[1]),
                        "public Kimi step")
            status, pending = request("watch", scope)
            assert status == 200 and pending["state"] == "pending"
            assert "公开过程" in [step["text"] for step in pending["steps"]]
            remote_client = RemoteNatsTransport(
                pair_id="default", token="synthetic", on_history_read=frame._remote_api_history_read_ui,
                on_execution_page=lambda payload: frame._remote_api_execution_page_ui(payload, secret="synthetic"),
            )

            def remote_has_step():
                status, page = remote_client._route_command({"type": "execution_page_v3", "chat_id": "existing"})
                return status == 200 and "公开过程" in [row["list_text"] for row in page["rows"]]

            wait_for_wx(remote_has_step, "RC projection before Kimi completion")

            native_turn = {"kind": "turn", "turnId": "1", "triggerPromptId": prompt, "ordinal": 0,
                           "origin": {"kind": "user"}, "state": "completed"}
            fake.push_event(kimi_wire._v2_final_event(session, native_turn, "完整回答 尾标"))
            wait_for_wx(lambda: frame._find_archived_chat("existing")["turns"][-1]["request_status"] == "done",
                        "the authoritative Kimi answer")
            assert request("watch", scope)[1]["answer"] == "完整回答 尾标"
            status, remote = remote_client._route_command({"type": "history_read", "chat_id": "existing"})
            assert status == 200
            assert remote["chat"]["turns"][-1]["answer"] == "完整回答 尾标"



@pytest.mark.parametrize("must_log_in", [True, False])
def test_terminal_kimi_reports_login_or_missing_session_without_forking_history(frame, monkeypatch, wait_for_wx, must_log_in):
    fake = FakeKimiServerClient(on_message=frame._on_kimi_client_message)
    fake.get_auth = lambda *, timeout: {
        "managed_provider": {"status": "expired" if must_log_in else "authenticated"},
    }
    owner = "archived-kimi"
    session = "original-native-id"
    chat = {"id": owner, "model": main.DEFAULT_KIMI_MODEL, "kimi_session_id": session}
    frame.chat_store.upsert_chat(chat)
    frame.archived_chats = frame.chat_store.list_chat_summaries()
    monkeypatch.setattr(frame, "_ensure_kimi_client", lambda: fake)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_kimi_reconcile_attempts", 1)
    scope = {"provider": "kimi", "chat_id": owner, "session_id": session, "request_id": uuid.uuid4().hex}

    assert dispatch_terminal(frame, "send", {**scope, "text": "same original session"})[0] == 200

    def outcome():
        return dispatch_terminal(frame, "watch", scope)[1]

    wait_for_wx(lambda: outcome().get("state") == "failed", "actionable Kimi failure")
    assert ("官方 Kimi CLI 登录" if must_log_in else "无法打开原生会话") in outcome()["message"]
    assert fake.created_sessions == [] and fake.submitted == []
    assert frame._find_archived_chat(owner)["kimi_session_id"] == session
