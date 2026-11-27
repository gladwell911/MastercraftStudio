"""Desktop settings through a real Kimi Server and a loopback model endpoint."""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import wx

from test_chat_tool_settings_ui_automation import choose, send


pytestmark = pytest.mark.skipif(
    os.getenv("MC_KIMI_RUNTIME_TEST") != "1",
    reason="set MC_KIMI_RUNTIME_TEST=1 and MC_KIMI_RUNTIME_BIN to exercise the local Kimi runtime",
)


@pytest.fixture
def loopback_kimi_home(tmp_path, monkeypatch):
    runtime = Path(os.environ["MC_KIMI_RUNTIME_BIN"])
    started, release = threading.Event(), threading.Event()
    calls = []

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            if self.path != "/v1/chat/completions":
                self.send_error(404)
                return
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append(body)
            question = next(message.get("content") for message in reversed(body["messages"])
                            if message.get("role") == "user"
                            and not str(message.get("content")).startswith("<system-reminder>"))
            if question == "RUN_TOOL" and body["messages"][-1].get("role") != "tool":
                tool = next(tool["function"]["name"] for tool in body["tools"]
                            if tool["function"]["name"] in {"Bash", "Shell"})
                chunks = [({"role": "assistant", "tool_calls": [{
                    "index": 0, "id": "call_write_mark", "type": "function", "function": {
                        "name": tool, "arguments": json.dumps({"command": "echo KIMI_APPROVED > approved.txt"}),
                    },
                }]}, None), ({}, "tool_calls")]
            else:
                chunks = [({"role": "assistant", "content": f"ANSWER_{question}"}, None), ({}, "stop")]
            if question == "HOLD_REQUEST":
                started.set()
                if not release.wait(35):
                    self.send_error(503)
                    return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for delta, finish in chunks:
                chunk = {"id": "qa", "object": "chat.completion.chunk", "created": 1, "model": body["model"],
                         "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")

    with ThreadingHTTPServer(("127.0.0.1", 0), Model) as server:
        home = tmp_path / "isolated-home"
        config = home / ".kimi-code"
        config.mkdir(parents=True)
        provider = f'''
default_model = "qa/model-a"
default_plan_mode = true
default_permission_mode = "manual"

[providers.qa]
type = "openai"
base_url = "http://127.0.0.1:{server.server_port}/v1"
api_key = "synthetic"
'''
        models = "".join(f'''
[models."qa/model-{suffix}"]
provider = "qa"
model = "qa-model-{suffix}"
protocol = "openai"
max_context_size = 32768
capabilities = ["thinking", "tool_use"]
support_efforts = ["low", "high"]
default_effort = "low"
''' for suffix in ("a", "b"))
        (config / "config.toml").write_text(provider + models, encoding="utf-8")
        for name, value in {
            "KIMI_BIN": runtime, "KIMI_CODE_HOME": config, "HOME": home, "USERPROFILE": home,
            "APPDATA": home / "Roaming", "LOCALAPPDATA": home / "Local",
        }.items():
            monkeypatch.setenv(name, str(value))
        for name in ("KIMI_API_KEY", "KIMI_BASE_URL", "KIMI_REGISTRY_API_KEY"):
            monkeypatch.delenv(name, raising=False)
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield workspace, calls, started, release
        finally:
            release.set()
            server.shutdown()
            thread.join(timeout=5)


def test_kimi_native_settings_keep_history_and_queue_snapshots(loopback_kimi_home, frame, wait_for_wx, monkeypatch):
    workspace, calls, started, release = loopback_kimi_home
    monkeypatch.setattr(frame, "_workspace_dir_for_kimi", lambda: str(workspace))
    monkeypatch.setattr(frame, "_schedule_first_question_auto_title", lambda *_args: None)
    frame.Show()

    def submit(question):
        send(frame, question)
        turn = frame.active_session_turns[-1]
        wait_for_wx(lambda: turn["request_status"] in {"done", "failed"}, question, timeout=25)
        assert turn["request_status"] == "done", turn.get("request_error")
        assert turn["answer_md"] == f"ANSWER_{question}"

    try:
        choose(frame.tool_combo, "Kimi Code")
        wait_for_wx(lambda: frame.model_combo.GetItems() == ["qa-model-a", "qa-model-b"],
                    "Kimi model catalog", timeout=20)
        assert frame.model_combo.GetStringSelection() == "qa-model-a"
        assert frame.reasoning_combo.GetItems() == ["低", "高"]
        assert frame.reasoning_combo.GetStringSelection() == "低"
        assert frame.codex_speed_combo.GetItems() == ["标准"]

        submit("SEED_ONE")
        submit("SEED_TWO")
        choose(frame.model_combo, "qa-model-b")
        submit("MODEL_ONLY")
        choose(frame.reasoning_combo, "高")
        submit("EFFORT_ONLY")
        choose(frame.model_combo, "qa-model-a")
        choose(frame.reasoning_combo, "低")
        submit("BOTH_SETTINGS")

        send(frame, "HOLD_REQUEST")
        wait_for_wx(started.is_set, "held native model request", timeout=20)
        choose(frame.model_combo, "qa-model-b")
        send(frame, "QUEUE_FIRST")
        choose(frame.model_combo, "qa-model-a")
        choose(frame.reasoning_combo, "高")
        send(frame, "QUEUE_SECOND")
        assert [turn["request_status"] for turn in frame.active_session_turns[-2:]] == ["queued", "queued"]
        assert len(calls) == 6
        release.set()
        wait_for_wx(lambda: all(turn["request_status"] in {"done", "failed"} for turn in frame.active_session_turns),
                    "queued Kimi answers", timeout=30)

        turns = frame.active_session_turns
        assert all(turn["request_status"] == "done" for turn in turns), [turn.get("request_error") for turn in turns]
        assert [turn["answer_md"] for turn in turns] == [f"ANSWER_{turn['question']}" for turn in turns]
        session_id = turns[0]["kimi_session_id"]
        assert session_id and {turn["kimi_session_id"] for turn in turns} == {session_id}
        assert len({turn["kimi_turn_id"] for turn in turns}) == 8
        assert [(call["model"], call["reasoning_effort"]) for call in calls] == [
            ("qa-model-a", "low"), ("qa-model-a", "low"), ("qa-model-b", "low"),
            ("qa-model-b", "high"), ("qa-model-a", "low"), ("qa-model-a", "low"),
            ("qa-model-b", "low"), ("qa-model-a", "high"),
        ]
        assert all("ANSWER_SEED_ONE" in str(call["messages"]) and "ANSWER_SEED_TWO" in str(call["messages"])
                   for call in calls[2:])
        assert "Auto permission mode is active" in str(calls[0]["messages"])
        evidence = frame._kimi_client.get_session(session_id)
        assert evidence["agent_config"]["model"] == "qa/model-a"
        frame._save_state()
        reopened = frame.chat_store.load_turns(frame.active_chat_id)
        assert [turn["answer_md"] for turn in reopened] == [turn["answer_md"] for turn in turns]
        assert [turn["tool_settings"] for turn in reopened] == [turn["tool_settings"] for turn in turns]
    finally:
        release.set()
        if frame._kimi_client is not None:
            frame._kimi_client.close()


def test_kimi_new_desktop_chat_uses_auto_approval_with_plan_off(loopback_kimi_home, frame, wait_for_wx, monkeypatch):
    workspace, calls, _started, _release = loopback_kimi_home
    monkeypatch.setattr(frame, "_workspace_dir_for_kimi", lambda: str(workspace))
    monkeypatch.setattr(frame, "_schedule_first_question_auto_title", lambda *_args: None)
    monkeypatch.setattr(frame, "_show_pending_kimi_interaction", lambda *_args: None)
    frame.Show()
    try:
        choose(frame.tool_combo, "Kimi Code")
        wait_for_wx(lambda: frame.model_combo.GetStringSelection() == "qa-model-a",
                    "Kimi model catalog", timeout=20)
        menu = frame._tool_mode_menu
        off, plan, permissions = menu.GetMenuItems()
        auto, manual, _yolo = permissions.GetSubMenu().GetMenuItems()
        assert off.IsChecked() and auto.IsChecked()
        plan_menu = plan.GetMenu()
        plan_menu.ProcessEvent(wx.CommandEvent(wx.wxEVT_MENU, plan.GetId()))
        assert plan.IsChecked() and auto.IsChecked()
        manual_menu = manual.GetMenu()
        manual_menu.ProcessEvent(wx.CommandEvent(wx.wxEVT_MENU, manual.GetId()))
        assert plan.IsChecked() and manual.IsChecked()
        manual_menu.ProcessEvent(wx.CommandEvent(wx.wxEVT_MENU, auto.GetId()))
        plan_menu.ProcessEvent(wx.CommandEvent(wx.wxEVT_MENU, off.GetId()))
        assert off.IsChecked() and auto.IsChecked()
        send(frame, "RUN_TOOL")
        turn = frame.active_session_turns[-1]
        wait_for_wx(lambda: turn["request_status"] in {"done", "failed"} or frame._kimi_interactions,
                    "automatic Kimi tool approval", timeout=20)
        assert turn["request_status"] == "done", (turn.get("request_error"), list(frame._kimi_interactions))
        assert turn["answer_md"] == "ANSWER_RUN_TOOL"
        assert "KIMI_APPROVED" in (workspace / "approved.txt").read_text(encoding="utf-8")
        assert not frame._kimi_interactions
        assert "Auto permission mode is active" in str(calls[0]["messages"])
        assert any(message.get("role") == "tool" for message in calls[-1]["messages"])
        assert frame._kimi_client.get_session(turn["kimi_session_id"])["pending_interaction"] == "none"
    finally:
        if frame._kimi_client is not None:
            frame._kimi_client.close()
