import json
import sys
import uuid

import pytest

from agy_acp import AgyAcpClient
from remote_nats import RemoteNatsTransport
from shared_terminal_routes import dispatch_terminal


@pytest.mark.parametrize("controller", ["remote", "desktop"])
def test_agy_shared_terminal_keeps_tool_tail_after_steer(
    frame, tmp_path, monkeypatch, wait_for_wx, controller, request,
):
    request.addfinalizer(frame._close_agy_clients)
    transcript = tmp_path / "provider.py"
    transcript.write_text("""
import json, sys, time
from pathlib import Path
def send(**data):
    print(json.dumps(data), flush=True)
def reply(id, **result):
    send(id=id, result=result)
def update(kind, **data):
    send(method="session/update", params={"sessionId":"native", "update":{"sessionUpdate":kind, **data}})
original = None
for line in sys.stdin:
    request = json.loads(line)
    method, id = request.get("method"), request.get("id")
    if method == "initialize":
        reply(id, protocolVersion=1)
    elif method == "session/new":
        reply(id, sessionId="native")
    elif method == "session/prompt":
        if original is None:
            original = id
            update("tool_call", toolCallId="delay", title="wait marker", status="in_progress")
        else:
            reply(original, stopReason="end_turn")
            Path("old-request-ended").touch()
            while not Path("complete-latest-request").exists():
                time.sleep(0.01)
            send(method="session/update", params={"sessionId":"wrong","update":{"sessionUpdate":"tool_call","toolCallId":"other","title":"another session"}})
            update("tool_call_update", toolCallId="delay", status="completed",
                   content=[{"type":"content", "content":{"type":"text","text":"tool tail marker"}}])
            update("agent_message_chunk", content={"type":"text","text":"完整中文回答 🧪 尾标"})
            reply(id, stopReason="end_turn")
""", encoding="utf-8")
    monkeypatch.setenv("MC_AGY_EXECUTABLE", sys._base_executable)
    monkeypatch.setattr(AgyAcpClient, "command", staticmethod(lambda executable: [executable, "-u", str(transcript)]))
    monkeypatch.setattr(frame, "_schedule_first_question_auto_title", lambda *args: None)
    monkeypatch.setattr(frame, "_schedule_async_archive_rename", lambda *args: None)
    transport = RemoteNatsTransport(
        pair_id="default", token="synthetic", on_new_chat=frame._remote_api_new_chat,
        on_message=frame._remote_api_message_ui, on_state=frame._remote_api_state_ui,
        on_execution_page=lambda payload: frame._remote_api_execution_page_ui(payload, secret="synthetic"),
    )

    def route(kind, **body):
        return transport._route_command({"type": kind, **body})

    code, created = route("new_chat", model="agy/main", agy_workspace=str(tmp_path))
    assert code == 200, created
    code, listing = dispatch_terminal(frame, "list", {"provider": "agy"})
    assert code == 200, listing
    chat_id = created["chat_id"]
    session = next(chat["session_id"] for chat in listing["chats"] if chat["chat_id"] == chat_id)
    scope = {"provider": "agy", "chat_id": chat_id, "session_id": session, "request_id": uuid.uuid4().hex}
    assert dispatch_terminal(frame, "send", {**scope, "text": "terminal task"})[0] == 200

    def watch():
        status, body = dispatch_terminal(frame, "watch", scope)
        assert status == 200, body
        return body

    def page():
        status, body = route("execution_page_v3", chat_id=chat_id)
        assert status == 200 or body.get("error") == "EXECUTION_SOURCE_PENDING", body
        return body.get("rows", [])

    wait_for_wx(lambda: any("wait marker" in row["text"] for row in watch()["steps"]), "live AGY execution")
    frame._flush_chat_state_save()
    assert any("wait marker" in row["list_text"] for row in page()), page()
    assert watch()["state"] == "pending"
    assert any("wait marker" in step["text"] for step in watch()["steps"])
    before = next(row for row in page() if "wait marker" in row["list_text"])
    state = route("state", chat_id=chat_id)[1]
    token = state["native_turn_id"]
    assert state["running"] and token
    assert route("message", chat_id=chat_id, text="wrong", expected_native_turn_id="old")[0] == 409
    action = {"chat_id": chat_id, "text": "append", "expected_native_turn_id": token,
              "client_send_id": uuid.uuid4().hex}
    if controller == "remote":
        assert route("message", **action)[0] == 200
        assert route("message", **action)[0] == 200
    else:
        frame.input_edit.SetValue("append")
        assert frame._on_send_clicked(None)
    wait_for_wx((tmp_path / "old-request-ended").exists, "older ACP request ended")
    assert watch()["state"] == "pending"
    assert route("state", chat_id=chat_id)[1]["native_turn_id"] != token
    assert route("message", chat_id=chat_id, text="/stop", expected_native_turn_id=token)[0] == 409
    (tmp_path / "complete-latest-request").touch()
    wait_for_wx(lambda: watch()["state"] == "done", "completed latest AGY request")
    assert watch()["answer"] == "完整中文回答 🧪 尾标"
    frame._flush_chat_state_save()
    after = [row for row in page() if "wait marker" in row["list_text"]]
    assert len(after) == 1
    assert after[0]["row_id"] == before["row_id"]
    assert "tool tail marker" in after[0]["detail_text"]
    assert not any("another session" in row["list_text"] for row in page())


def test_agy_controls_bind_the_exact_native_form_or_permission(frame, tmp_path, monkeypatch, wait_for_wx):
    transcript = tmp_path / "interactions.py"
    transcript.write_text("""
import json, sys, uuid
def send(**data):
    print(json.dumps(data), flush=True)
def reply(id, **result):
    send(id=id, result=result)
def ask(method, session, **params):
    send(id="provider-id", method=method, params={"sessionId":session, **params})
session = uuid.uuid4().hex
for line in sys.stdin:
    request = json.loads(line)
    method, id = request.get("method"), request.get("id")
    params = request.get("params") or {}
    if method == "initialize":
        assert params["clientCapabilities"]["elicitation"] == {"form": {}}
        reply(id, protocolVersion=1, agentCapabilities={"sessionCapabilities":{"resume":{}}})
    elif method == "session/new":
        reply(id, sessionId=session)
    elif method == "session/resume":
        session = params["sessionId"]
        reply(id, sessionId=session)
    elif method == "session/prompt":
        active = id
        variant = params["prompt"][0]["text"]
        if variant == "permission":
            ask("session/request_permission", session, toolCall={"title":"guarded test tool"},
                options=[{"optionId":"allow-once", "name":"Allow", "kind":"allow_once"},
                         {"optionId":"reject-once", "name":"Reject", "kind":"reject_once"}])
        else:
            ask("elicitation/create", session, message="Test form", mode="form", requestedSchema={
                "type":"object", "properties":{
                    "q1":{"type":"string", "title":"Test color", "oneOf":[
                        {"const":"blue-id", "title":"Blue"}, {"const":"yellow-id", "title":"Yellow"}]},
                    "q1_other":{"type":"string", "title":"Other"},
                },
            })
    elif method == "session/cancel":
        cancelled = True
    elif id == "provider-id":
        response = request["result"]
        if response.get("action") == "cancel":
            assert cancelled
            reply(active, stopReason="cancelled")
        else:
            answer = response["outcome"]["optionId"] if variant == "permission" else "exact yellow"
            if variant != "permission":
                assert response == {"action":"accept", "content":{"q1":"yellow-id"}}, response
            send(method="session/update", params={"sessionId":session, "update":{
                "sessionUpdate":"agent_message_chunk", "content":{"type":"text", "text":answer}}})
            reply(active, stopReason="end_turn")
""", encoding="utf-8")
    monkeypatch.setenv("MC_AGY_EXECUTABLE", sys._base_executable)
    monkeypatch.setattr(AgyAcpClient, "command", staticmethod(lambda executable: [executable, "-u", str(transcript)]))
    monkeypatch.setattr(frame, "_schedule_first_question_auto_title", lambda *args: None)
    monkeypatch.setattr(frame, "_schedule_async_archive_rename", lambda *args: None)
    transport = RemoteNatsTransport(
        pair_id="default", token="synthetic", on_new_chat=frame._remote_api_new_chat,
        on_state=frame._remote_api_state_ui, on_message=frame._remote_api_message_ui,
        on_reply_request=frame._remote_api_reply_request_ui,
        on_history_read=frame._remote_api_history_read_ui,
    )

    def route(kind, **body):
        return transport._route_command({"type": kind, **body})

    def create():
        code, chat = route("new_chat", model="agy/main", agy_workspace=str(tmp_path))
        assert code == 200, chat
        return chat["chat_id"]

    def submit(chat, text):
        _, listing = dispatch_terminal(frame, "list", {"provider": "agy"})
        session = next(row["session_id"] for row in listing["chats"] if row["chat_id"] == chat)
        scope = {"provider": "agy", "chat_id": chat, "session_id": session, "request_id": uuid.uuid4().hex}
        assert dispatch_terminal(frame, "send", {**scope, "text": text})[0] == 200

        def watch():
            code, body = dispatch_terminal(frame, "watch", scope)
            assert code == 200, body
            return body

        return watch

    try:
        target = create()
        other = create()
        watch_form = submit(target, "form")
        wait_for_wx(lambda: "pending_request" in route("state", chat_id=target)[1], "AGY native form")
        state = route("state", chat_id=target)[1]
        stored_view = route("history_read", chat_id=target)[1]["chat"]
        interaction = state["pending_request"]
        assert stored_view["pending_request"]["event_id"] == interaction["event_id"]
        assert "pending_request" not in route("state", chat_id=other)[1]
        body = interaction["body"]
        question = body["params"]["questions"][0]
        assert question["header"] == "Test color"
        answer = {"q1": next(option["value"] for option in question["options"] if option["label"] == "Yellow")}
        token = body["interaction_id"]
        assert route("reply_request", chat_id=other, interaction_id=token, answers=answer)[0] == 400
        assert route("reply_request", chat_id=target, interaction_id=token, answers={"wrong": "scope"})[0] == 400
        assert route("reply_request", chat_id=target, interaction_id=token, answers=answer)[0] == 200
        assert route("reply_request", chat_id=target, interaction_id=token, answers=answer)[0] == 400
        wait_for_wx(lambda: watch_form()["state"] == "done", "form response continued AGY")
        assert watch_form()["answer"] == "exact yellow"

        for decision in ("allow-once", "reject-once"):
            watch_permission = submit(target, "permission")
            wait_for_wx(lambda: "pending_request" in route("state", chat_id=target)[1], "AGY tool approval")
            pending = route("state", chat_id=target)[1]["pending_request"]
            assert pending["request_kind"] == "approval"
            assert {opt["value"] for opt in pending["body"]["params"]["options"]} == {"allow-once", "reject-once"}
            token = pending["body"]["interaction_id"]
            assert route("reply_request", chat_id=target, interaction_id=token, text=decision)[0] == 200
            wait_for_wx(lambda: watch_permission()["state"] == "done", "approval reached the original request")
            assert watch_permission()["answer"] == decision

        for controller in ("remote", "desktop"):
            watch_cancel = submit(other, "form")
            wait_for_wx(lambda: "pending_request" in route("state", chat_id=other)[1], "pending form to cancel")
            state = route("state", chat_id=other)[1]
            pending = state["pending_request"]["body"]["interaction_id"]
            assert watch_cancel()["interaction_id"] == pending
            stop = {"text": "/stop", "expected_native_turn_id": state["native_turn_id"]}
            assert route("message", chat_id=target, **stop)[0] == 409
            if controller == "remote":
                assert route("message", chat_id=other, **stop)[0] == 200
            else:
                frame.input_edit.SetValue("/stop")
                assert frame._on_send_clicked(None)
            wait_for_wx(lambda: watch_cancel()["state"] == "cancelled", "AGY stopped")
            assert route("reply_request", chat_id=other, interaction_id=pending, answers=answer)[0] == 400
    finally:
        frame._close_agy_clients()
