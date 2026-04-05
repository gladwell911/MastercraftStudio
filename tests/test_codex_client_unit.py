import threading
import time
from types import SimpleNamespace

import codex_client


def test_codex_model_helper():
    assert codex_client.is_codex_model("codex/main")
    assert not codex_client.is_codex_model("openai/gpt-5.2")


def test_codex_client_start_turn_sends_request_and_returns_result(monkeypatch):
    client = codex_client.CodexAppServerClient()
    sent = []
    monkeypatch.setattr(client, "_ensure_started", lambda: None)
    monkeypatch.setattr(client, "_send_json", lambda payload: sent.append(payload))

    def _reply():
        time.sleep(0.01)
        client._handle_message({"id": 1, "result": {"turn": {"id": "turn-1"}}})

    threading.Thread(target=_reply, daemon=True).start()
    result = client.start_turn("thread-1", "hello")

    assert result["turn"]["id"] == "turn-1"
    assert sent == [
        {
            "id": 1,
            "method": "turn/start",
            "params": {
                "threadId": "thread-1",
                "input": [{"type": "text", "text": "hello"}],
            },
        }
    ]


def test_codex_client_steer_turn_sends_expected_payload(monkeypatch):
    client = codex_client.CodexAppServerClient()
    sent = []
    monkeypatch.setattr(client, "_ensure_started", lambda: None)
    monkeypatch.setattr(client, "_send_json", lambda payload: sent.append(payload))

    def _reply():
        time.sleep(0.01)
        client._handle_message({"id": 1, "result": {"turnId": "turn-1"}})

    threading.Thread(target=_reply, daemon=True).start()
    client.steer_turn("thread-1", "turn-1", "补充信息")

    assert sent[0]["method"] == "turn/steer"
    assert sent[0]["params"]["expectedTurnId"] == "turn-1"
    assert sent[0]["params"]["input"][0]["text"] == "补充信息"


def test_codex_client_maps_server_request_to_event():
    seen = []
    client = codex_client.CodexAppServerClient(on_event=seen.append)

    client._handle_message(
        {
            "id": 9,
            "method": "item/tool/requestUserInput",
            "params": {"threadId": "th", "turnId": "tu", "questions": [{"id": "q1"}]},
        }
    )

    assert len(seen) == 1
    assert seen[0].type == "server_request"
    assert seen[0].request_id == 9
    assert seen[0].method == "item/tool/requestUserInput"


def test_codex_client_maps_agent_message_delta_to_event():
    seen = []
    client = codex_client.CodexAppServerClient(on_event=seen.append)

    client._handle_message(
        {
            "method": "item/agentMessage/delta",
            "params": {
                "threadId": "th",
                "turnId": "tu",
                "itemId": "msg-1",
                "delta": "hello",
            },
        }
    )

    assert len(seen) == 1
    assert seen[0].type == "agent_message_delta"
    assert seen[0].text == "hello"


def test_resolve_codex_launch_command_uses_powershell_for_ps1(monkeypatch):
    def _which(name):
        mapping = {
            "codex.ps1": r"C:\Users\test\AppData\Roaming\npm\codex.ps1",
            "powershell.exe": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        }
        return mapping.get(name)

    monkeypatch.setattr(codex_client.os, "name", "nt")
    monkeypatch.setattr(codex_client.shutil, "which", _which)
    monkeypatch.delenv("CODEX_BIN", raising=False)

    command = codex_client.resolve_codex_launch_command()

    assert command == [
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        r"C:\Users\test\AppData\Roaming\npm\codex.ps1",
    ]


def test_ensure_started_uses_resolved_launch_command(monkeypatch):
    client = codex_client.CodexAppServerClient()
    seen = {}

    class _Proc:
        stdin = None
        stdout = None
        stderr = None

        def poll(self):
            return None

    def _popen(args, **kwargs):
        seen["call"] = {"args": args, "kwargs": kwargs}
        return _Proc()

    monkeypatch.setattr(codex_client, "resolve_codex_launch_command", lambda: ["codex.cmd"])
    monkeypatch.setattr(codex_client.subprocess, "Popen", _popen)
    monkeypatch.setattr(codex_client.threading, "Thread", lambda *args, **kwargs: SimpleNamespace(start=lambda: None))
    monkeypatch.setattr(client, "_initialize", lambda: seen.setdefault("initialized", True))

    client._ensure_started()

    assert seen["call"]["args"] == [
        "codex.cmd",
        "--dangerously-bypass-approvals-and-sandbox",
        "-C",
        str(codex_client.Path.cwd()),
        "app-server",
        "--listen",
        "stdio://",
    ]
    assert seen["initialized"] is True


def test_build_codex_app_server_command_includes_bypass_and_cwd(monkeypatch):
    monkeypatch.setattr(codex_client, "resolve_codex_launch_command", lambda: ["codex.cmd"])

    command = codex_client.build_codex_app_server_command(r"C:\code\codex")

    assert command == [
        "codex.cmd",
        "--dangerously-bypass-approvals-and-sandbox",
        "-C",
        r"C:\code\codex",
        "app-server",
        "--listen",
        "stdio://",
    ]
