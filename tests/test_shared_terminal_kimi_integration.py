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
    replies = []
    fake.interaction_state = lambda *_args: "pending"
    fake.answer_interaction = lambda *args: replies.append(args)
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
            frame.chat_store.notification_sync(pair_id="default", device_id="private-device",
                                               baseline=0, incarnation="test", secret="test")
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
                on_state=frame._remote_api_state_ui, on_message=frame._remote_api_message_ui,
                on_reply_request=frame._remote_api_reply_request_ui,
                on_execution_page=lambda payload: frame._remote_api_execution_page_ui(payload, secret="synthetic"),
            )

            def remote_has_step():
                status, page = remote_client._route_command({"type": "execution_page_v3", "chat_id": "existing"})
                return status == 200 and "公开过程" in [row["list_text"] for row in page["rows"]]

            wait_for_wx(remote_has_step, "RC projection before Kimi completion")

            code, current = remote_client._route_command({"type": "state", "chat_id": "existing"})
            assert code == 200 and current["running"]
            assert not frame.chat_store.pending_outbox(pair_id="default", lane="notification")
            scope_token = current["native_turn_id"]
            assert scope_token
            code, _ = remote_client._route_command({
                "type": "message", "chat_id": "existing", "text": "/stop", "expected_native_turn_id": "old",
            })
            assert code == 409
            assert len(fake.submitted) == 1

            interaction = {"type": "interaction", "session_id": session, "agent_id": "main",
                           "interaction_id": "approval-exact", "status": "pending", "kind": "approval",
                           "request": {"tool_name": "Shell", "action": "Inspect temporary file"}}
            def push_interaction():
                kimi_wire._handle_ws_message(interaction)
                for event in kimi_wire.drain_pending_messages():
                    frame._on_kimi_client_message(event)

            kimi_wire.protocol_version = 2
            push_interaction()
            wait_for_wx(lambda: remote_client._route_command({"type": "state", "chat_id": "existing"})[1]
                        .get("pending_request"), "scoped remote approval", timeout=3)
            request_body = {"type": "reply_request", "chat_id": "existing",
                            "interaction_id": "approval-exact", "text": "rejected"}
            assert remote_client._route_command({**request_body, "chat_id": "unrelated"})[0] == 400
            assert remote_client._route_command(request_body)[0] == 200
            assert remote_client._route_command(request_body)[0] == 400
            wait_for_wx(lambda: len(replies) == 1, "one exact native rejection")
            assert replies == [(session, "approval-exact", {"kind": "approval", "decision": "rejected"})]
            push_interaction()
            assert remote_client._route_command(request_body)[0] == 400

            native_turn = {"kind": "turn", "turnId": "1", "triggerPromptId": prompt, "ordinal": 0,
                           "origin": {"kind": "user"}, "state": "completed"}
            fake.push_event(kimi_wire._v2_final_event(session, native_turn, "完整回答 尾标"))
            wait_for_wx(lambda: frame._find_archived_chat("existing")["turns"][-1]["request_status"] == "done",
                        "the authoritative Kimi answer")
            assert request("watch", scope)[1]["answer"] == "完整回答 尾标"
            assert frame._flush_execution_step_persists_sync()
            frame._flush_chat_state_save()
            status, remote = remote_client._route_command({"type": "history_read", "chat_id": "existing"})
            assert status == 200
            wait_for_wx(lambda: frame.chat_store.pending_outbox(pair_id="default", lane="notification"),
                        "the durable Kimi notification")
            message = remote["chat"]["turns"][-1]
            fact, = frame.chat_store.pending_outbox(pair_id="default", lane="notification")
            assert message["message_id"] in fact["event_id"]
            assert remote["chat"]["turns"][-1]["answer"] == "完整回答 尾标"



@pytest.mark.parametrize(("auth", "message"), [
    ({"managed_provider": {"status": "expired"}}, "官方 Kimi CLI 登录"),
    ({"managed_provider": None, "models_ready": False, "providers_count": 0}, "官方 Kimi CLI 登录"),
    ({"managed_provider": None, "models_ready": True, "providers_count": 1}, "无法打开原生会话"),
])
def test_terminal_kimi_reports_login_or_missing_session_without_forking_history(
    frame, monkeypatch, wait_for_wx, auth, message,
):
    fake = FakeKimiServerClient(on_message=frame._on_kimi_client_message)
    fake.get_auth = lambda *, timeout: auth
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
    assert message in outcome()["message"]
    assert fake.created_sessions == [] and fake.submitted == []
    assert frame._find_archived_chat(owner)["kimi_session_id"] == session
