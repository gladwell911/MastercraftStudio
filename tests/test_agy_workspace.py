import json
import sys
import time

import wx

from remote_nats import RemoteNatsTransport


def test_agy_remote_create_requires_host_directory_and_preserves_workspace(frame, tmp_path, monkeypatch):
    project = tmp_path / "中文 项目"
    project.mkdir()
    fake_cli = tmp_path / "fake_agy.py"
    trace = tmp_path / "agy.jsonl"
    fake_cli.write_text("""
import json, os, sys, time
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    params = request.get("params")
    with open(os.environ["AGY_TEST_TRACE"], "a", encoding="utf-8") as trace:
        trace.write(json.dumps(request, ensure_ascii=False) + "\\n")
    if method == "initialize":
        result = {"protocolVersion": 1, "agentCapabilities": {"sessionCapabilities": {"resume": {}}}}
    elif method in ("session/new", "session/resume"):
        result = {"sessionId": "native-123", "configOptions": [
            {"id": "model", "name": "Model", "category": "model", "type": "select",
             "currentValue": "default", "options": [{"value": "default", "name": "默认"},
                                                   {"value": "alternate", "name": "另一个"}]}]}
    elif method == "session/prompt":
        print(json.dumps({"jsonrpc": "2.0", "method": "session/update", "params": {
            "sessionId": params["sessionId"], "update": {"sessionUpdate": "agent_message_chunk",
             "content": {"type": "text", "text": "完整中文回答"}}}}), flush=True)
        time.sleep(0.25)
        print(json.dumps({"jsonrpc": "2.0", "method": "session/update", "params": {
            "sessionId": params["sessionId"], "update": {"sessionUpdate": "agent_message_chunk",
             "content": {"type": "text", "text": "和尾部"}}}}), flush=True)
        result = {"stopReason": "end_turn"}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
""", encoding="utf-8")
    monkeypatch.setenv("AGY_TEST_TRACE", str(trace))
    monkeypatch.setenv("MC_AGY_EXECUTABLE", sys._base_executable)
    from agy_acp import AgyAcpClient
    monkeypatch.setattr(AgyAcpClient, "command", staticmethod(lambda _executable: [sys._base_executable, "-u", str(fake_cli)]))

    transport = RemoteNatsTransport(
        pair_id="default", token="test", on_new_chat=frame._remote_api_new_chat,
        on_agy_configure=frame._remote_api_agy_configure_ui,
        on_message=frame._remote_api_message_ui, on_state=frame._remote_api_state_ui,
        on_model_list=frame._remote_api_model_list_ui,
    )

    def route(command, **payload):
        return transport._route_command({"type": command, **payload})

    def wait_for_answer(chat_id, expected, *, pending, turn_count=1):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            wx.GetApp().ProcessPendingEvents()
            status, response = route("state", chat_id=chat_id)
            assert status == 200
            turn = response["turns"][-1]
            if len(response["turns"]) == turn_count and turn["answer"] == expected and turn["pending"] == pending:
                assert response["status"] == ("running" if pending else "idle")
                return
            time.sleep(0.01)
        raise AssertionError(response)

    try:
        status, failure = route("new_chat", model="agy/main")
        assert status == 400 and "目录" in failure["message"]
        status, created = route("new_chat", model="agy/main", agy_workspace=str(project))
        assert status == 200
        assert created["model"] == "agy/main"
        assert created["agy_workspace"] == str(project)
        assert created["agy_options"][0]["options"][1]["value"] == "alternate"

        chat_id = created["chat_id"]
        status, unsupported = route("agy_configure", chat_id=chat_id,
                                    body={"agy_config": {"model": "unsupported"}})
        assert status == 400 and "不支持" in unsupported["message"]
        status, configured = route("agy_configure", chat_id=chat_id,
                                   body={"agy_config": {"model": "alternate"}})
        assert status == 200, configured
        status, accepted = route("message", chat_id=chat_id, text="第一轮")
        assert status == 200, accepted
        wait_for_answer(chat_id, "完整中文回答", pending=True)
        wait_for_answer(chat_id, "完整中文回答和尾部", pending=False)
        frame._flush_chat_state_save()
        assert frame.chat_store.load_chat(chat_id)["agy_session_id"] == "native-123"
        status, models = route("model_list")
        assert next(row for row in models["models"] if row["id"] == "agy/main")["agy_workspace"]["cwd"] == str(project)

        frame._close_agy_clients()
        status, _ = route("new_chat", model="codex/main", title="另一个聊天")
        assert status == 200
        status, accepted = route("message", chat_id=chat_id, text="第二轮")
        assert status == 200, accepted
        wait_for_answer(chat_id, "完整中文回答和尾部", pending=False, turn_count=2)
        frame._flush_chat_state_save()
        assert len(frame.chat_store.load_chat(chat_id)["turns"]) == 2

        frame._close_agy_clients()
        project.rename(project.with_name("移动后"))
        status, failure = route("message", chat_id=chat_id, text="目录已移走")
        assert status == 400 and "项目目录" in failure["message"]
        status, original = route("state", chat_id=chat_id)
        assert status == 200 and len(original["turns"]) == 2
        assert original["turns"][0]["answer"] == "完整中文回答和尾部"
        assert frame.model_combo.SetStringSelection("AGY")
        status, legacy_chat = route("new_chat")
        assert status == 200 and legacy_chat["model"] == "codex/main"

        requests = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
        assert [req["params"] for req in requests if req["method"] == "session/resume"] == [
            {"sessionId": "native-123", "cwd": str(project), "mcpServers": []},
        ]
        assert [req["params"]["value"] for req in requests if req["method"] == "session/set_config_option"] == ["alternate", "alternate"]
        assert [req["params"]["sessionId"] for req in requests if req["method"] == "session/prompt"] == ["native-123", "native-123"]
    finally:
        frame._close_agy_clients()
