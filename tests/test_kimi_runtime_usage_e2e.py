"""Opt-in real Kimi server against a loopback model with known usage."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import subprocess
import sys
from pathlib import Path
import threading

import pytest
import wx

from kimi_runtime import child_environment
from remote_nats import RemoteNatsTransport
from shared_terminal import TerminalServer
from shared_terminal_command import terminal_endpoint
from shared_terminal_routes import dispatch_terminal
from test_voice_input_ui_automation import _pump_until


pytestmark = pytest.mark.skipif(
    os.getenv("MC_KIMI_RUNTIME_TEST") != "1",
    reason="set MC_KIMI_RUNTIME_TEST=1 to spawn the local runtime",
)


@pytest.fixture
def gated_reply():
    return threading.Event()


@pytest.fixture
def loopback_kimi_home(tmp_path, monkeypatch, gated_reply):
    requests = []

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            if self.path != "/v1/chat/completions":
                self.send_error(404)
                return
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            chunks = [
                {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "QA_RUNTIME_OK"}, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 10,
                                          "total_tokens": 110, "prompt_tokens_details": {"cached_tokens": 20}}},
            ]
            last = next((message for message in reversed(body["messages"])
                         if message.get("role") == "tool" or any(marker in str(message.get("content"))
                            for marker in ("QA_NATIVE_TOOL_CALL", "QA_SHARED_TOOL_GATE"))), {})
            if last.get("role") == "user":
                chunks[:2] = [
                    {"choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": [
                        {"index": 0, "id": "call_qa_native_glob", "type": "function", "function": {
                            "name": "Glob", "arguments": '{"pattern":"qa-native-*.txt"}',
                        }},
                    ]}, "finish_reason": None}]},
                    {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
                ]
            if (last.get("role") == "tool" and "qa-native-gated-tool.txt" in str(last.get("content"))
                    and any("QA_SHARED_TOOL_GATE" in str(message.get("content")) for message in body["messages"])):
                if not gated_reply.wait(25):
                    self.send_error(503)
                    return
                chunks[0]["choices"][0]["delta"]["content"] = "完整回复。" * 350 + " QA_SHARED_FINAL_TAIL"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for chunk in chunks:
                packet = {"id": "chatcmpl-qa", "object": "chat.completion.chunk", "created": 1,
                          "model": "kimi-for-coding", **chunk}
                self.wfile.write(f"data: {json.dumps(packet)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")

    with ThreadingHTTPServer(("127.0.0.1", 0), Model) as server:
        port = server.server_port
        runtime = Path(os.getenv("MC_KIMI_RUNTIME_BIN") or Path.home() / ".kimi-code/bin/kimi.exe")
        if not runtime.is_file():
            pytest.skip("local Kimi runtime is unavailable")
        home = tmp_path / "isolated-home"
        config = home / ".kimi-code"
        config.mkdir(parents=True)
        (config / "config.toml").write_text(
            f'''default_model = "kimi-code/kimi-for-coding"
[providers."qa-loopback"]
type = "openai"
base_url = "http://127.0.0.1:{port}/v1"
api_key = "synthetic"
[models."kimi-code/kimi-for-coding"]
provider = "qa-loopback"
model = "kimi-for-coding"
protocol = "openai"
max_context_size = 32768
''', encoding="utf-8")
        for name, value in {
            "KIMI_BIN": runtime, "KIMI_CODE_HOME": config, "HOME": home, "USERPROFILE": home,
            "APPDATA": home / "Roaming", "LOCALAPPDATA": home / "Local",
        }.items():
            monkeypatch.setenv(name, str(value))
        for name in ("KIMI_API_KEY", "KIMI_BASE_URL", "KIMI_REGISTRY_API_KEY"):
            monkeypatch.delenv(name, raising=False)
        workspace = tmp_path / "empty-workspace"
        workspace.mkdir()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield workspace, requests
        finally:
            server.shutdown()
            thread.join(timeout=5)


def test_reopened_kimi_chat_displays_nonempty_usage_in_alt_y(
    loopback_kimi_home, frame, wx_app, monkeypatch, caplog
):
    caplog.set_level("DEBUG", logger="kimi_server_v2")
    workspace, requests = loopback_kimi_home
    monkeypatch.setattr(frame, "_workspace_dir_for_kimi", lambda: str(workspace))
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda **_kwargs: None)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)
    monkeypatch.setattr(frame, "_schedule_first_question_auto_title", lambda *_args, **_kwargs: None)
    frame.model_combo.SetValue("Kimi Code")
    frame.selected_model = "kimi/main"
    frame.input_edit.SetValue("Return the QA marker without tools.")

    try:
        frame._on_send_clicked(None)
        _pump_until(wx_app, lambda: frame.active_session_turns and
                    frame.active_session_turns[-1].get("request_status") in {"done", "failed", "error"}, timeout=25)
        turn = frame.active_session_turns[-1]
        assert turn["request_status"] == "done", turn.get("request_error") or turn.get("answer_md")
        assert turn["answer_md"] == "QA_RUNTIME_OK"
        assert len(requests) == 1
        session = turn["kimi_session_id"]
        chat_id = frame.active_chat_id
        first = frame._kimi_client
        assert first.protocol_version == 2
        assert first.get_v2_usage_totals(session)["session_total_tokens"] == 110
        frame._save_state()
        first.close()
        frame._kimi_client = None

        frame._on_new_chat_clicked(None, title="Separate test chat")
        assert frame._show_history_chat(chat_id)
        item = frame.GetMenuBar().FindItemById(int(frame._chat_information_menu_id))
        assert "Alt+Y" in item.GetItemLabel()
        event = wx.CommandEvent(wx.EVT_MENU.typeId, int(frame._chat_information_menu_id))
        assert frame.ProcessEvent(event)
        dialog = frame._chat_information_dialog
        assert dialog is not None and dialog.identity[2] == session

        def rows():
            return list(dialog.information_list.GetStrings())

        def fresh_usage_applied():
            client = frame._kimi_client
            chat = frame._find_archived_chat(chat_id)
            stamp = chat.get("kimi_v2_usage_stamp") if chat is not None else None
            return client is not None and client.protocol_version == 2 and stamp and stamp[0][0] == id(client)

        _pump_until(wx_app, fresh_usage_applied, timeout=20)
        reopened = frame._kimi_client
        assert reopened is not first
        transcript = reopened.get_transcript(session)
        evidence = [
            {"state": item.get("state"), "usage": item.get("usage"),
             "steps": [step.get("usage") for step in item.get("steps", [])]}
            for item in transcript.get("items", []) if item.get("kind") == "turn"
        ]
        totals = reopened.get_v2_usage_totals(session)
        assert totals["session_total_tokens"] == 110, evidence
        assert totals["cache_hits"]["total"] == {"input_tokens": 100, "cached_tokens": 20}
        assert totals["cache_hits"]["last"] == {"input_tokens": 100, "cached_tokens": 20}
        assert any("会话累计 token：110" in line for line in rows()), rows()
        assert any(line == "缓存命中：20.0%" for line in rows()), rows()
        assert any(line == "累计缓存命中：20.0%" for line in rows()), rows()
        assert any("stages=subscribe_ack,rest_complete pages=1" in line for line in caplog.messages)
        assert len(requests) == 1, "opening the previous chat must use persisted history"
        print(f"KIMI_REOPEN_USAGE_OK rows={rows()[:4]}")
    finally:
        if frame._chat_information_dialog is not None:
            frame._chat_information_dialog.Close()
        if frame._kimi_client is not None:
            frame._kimi_client.close()


def test_powershell_uses_the_real_shared_kimi_turn_before_its_final(
    loopback_kimi_home, frame, monkeypatch, wait_for_wx, gated_reply,
):
    workspace, requests = loopback_kimi_home
    seed = subprocess.run([os.environ["KIMI_BIN"], "-p", "QA_SHARED_SEED"], cwd=workspace,
                          env=child_environment(), timeout=20, capture_output=True, check=True, encoding="utf-8")
    assert "QA_RUNTIME_OK" in seed.stdout
    native = subprocess.run([os.environ["KIMI_BIN"], "session", "list", "--json"], cwd=workspace,
                            env=child_environment(), timeout=20, capture_output=True, check=True, encoding="utf-8")
    native_session, = json.loads(native.stdout)
    (workspace / "qa-native-gated-tool.txt").write_text("synthetic fixture", encoding="utf-8")
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)
    monkeypatch.setattr(frame, "_schedule_first_question_auto_title", lambda *_args, **_kwargs: None)

    def current_chats():
        return [chat for chat in frame._remote_api_history_list_ui()[1]["chats"]
                if chat["model"] == "kimi/main" and chat["turn_count"] == 1]

    def remote():
        return RemoteNatsTransport(
            pair_id="default", token="synthetic", durable_store=frame.chat_store, on_state=frame._remote_api_state_ui,
            on_history_read=frame._remote_api_history_read_ui,
            on_execution_page=lambda payload: frame._remote_api_execution_page_ui(payload, secret="synthetic"),
        )

    output_path = workspace / "terminal-stdout.log"
    errors_path = workspace / "terminal-stderr.log"

    def run_power_shell(prompt):
        env = dict(os.environ, MC_QA_TERMINAL_PYTHON=sys.executable,
                   MC_QA_TERMINAL_SOURCE=str(Path(__file__).resolve().parents[1] / "worker_launcher.py"),
                   MC_QA_TERMINAL_SESSION=native_session["id"], MC_QA_TERMINAL_PROMPT=prompt)
        with output_path.open("wb") as stdout, errors_path.open("wb") as stderr:
            return subprocess.Popen([
                "powershell.exe", "-NoProfile", "-Command", "& $env:MC_QA_TERMINAL_PYTHON "
                "$env:MC_QA_TERMINAL_SOURCE --shared-terminal kimi --chat "
                "$env:MC_QA_TERMINAL_SESSION --send $env:MC_QA_TERMINAL_PROMPT",
            ], cwd=workspace, env=env, stdout=stdout, stderr=stderr)

    command = None
    try:
        wait_for_wx(current_chats, "discovered Kimi native session", timeout=15)
        owner, = current_chats()
        chat_id = owner["chat_id"]
        endpoint = terminal_endpoint(Path(sys.executable).resolve().with_name("mc.exe"))
        assert endpoint.is_relative_to(Path(os.environ["LOCALAPPDATA"]))
        with TerminalServer(endpoint, lambda op, body: dispatch_terminal(frame, op, body)):
            command = run_power_shell("QA_SHARED_TOOL_GATE")
            first = remote()

            def remote_command(reader, command_type):
                status, body = reader._route_command({"type": command_type, "chat_id": chat_id})
                assert status == 200, body
                return body

            def observed_tool(reader):
                status, body = reader._route_command({"type": "execution_page_v3", "chat_id": chat_id})
                if status == 409 and body.get("error") == "EXECUTION_SOURCE_PENDING":
                    return None
                assert status == 200, body
                return next((row for row in body["rows"] if row["list_text"].startswith("Glob")), None)

            wait_for_wx(lambda: observed_tool(first) and len(requests) == 3,
                        "real runtime executed Glob and blocked in the model", timeout=18)
            state = remote_command(first, "state")
            assert state["running"] and state["native_turn_id"]
            assert command.poll() is None
            assert frame._show_history_chat(chat_id)
            assert frame.view_history_id == chat_id
            reconnect = remote()
            assert remote_command(reconnect, "state")["native_turn_id"] == state["native_turn_id"]
            row_id = observed_tool(first)["row_id"]
            assert observed_tool(reconnect)["row_id"] == row_id
            desktop_rows, _ = frame._execution_page_projection()
            assert row_id in {identity for identity, _ in desktop_rows}
            wait_for_wx(lambda: any("qa-native-gated-tool.txt" in row["detail_text"]
                                   for row in frame.chat_store.load_execution_steps(chat_id)),
                        "tool output replaces the in-progress row")
            wait_for_wx(lambda: observed_tool(reconnect) and observed_tool(reconnect)["row_id"] == row_id,
                        "reconnected execution updates the same row")
            gated_reply.set()
            wait_for_wx(lambda: command.poll() is not None, "PowerShell final answer", timeout=20)
            assert command.returncode == 0, errors_path.read_text(encoding="utf-8", errors="replace")
            assert "QA_SHARED_FINAL_TAIL" in output_path.read_text(encoding="utf-8")
            history = remote_command(reconnect, "history_read")["chat"]["turns"]
            assert [turn["question"] for turn in history] == ["QA_SHARED_SEED", "QA_SHARED_TOOL_GATE"]
            assert history[-1]["client_send_id"].startswith("terminal:")
            def remote_final():
                status, page = reconnect._route_command({"type": "execution_page_v3", "chat_id": chat_id})
                if status != 200:
                    return None
                return next((row for row in page["rows"] if row.get("kind") == "final"
                             and row.get("turn_index") == 1), None)

            try:
                wait_for_wx(remote_final, "durable execution includes final answer")
            except AssertionError as exc:
                raise AssertionError((frame._chat_state_flush_dirty, frame._chat_state_flush_scheduled,
                    frame._codex_background_flush_dirty, frame._codex_background_flush_scheduled,
                    frame._chat_turn_dirty_from, frame._kimi_execution_write_counts,
                    frame._close_started, remote_command(reconnect, "execution_page_v3")["rows"])) from exc
            answer = remote_final()
            text = answer["answer_text"]
            cursor = answer["answer_text_continuation"]
            assert cursor
            while cursor:
                status, chunk = reconnect._route_command({"type": "content_page", "chat_id": chat_id,
                                                           "body": {"cursor": cursor}})
                assert status == 200, chunk
                text += chunk["text"]
                cursor = chunk["cursor"]
            assert text == "完整回复。" * 350 + " QA_SHARED_FINAL_TAIL"
            assert not remote_command(reconnect, "state")["running"]
            assert len(requests) == 3
    finally:
        gated_reply.set()
        if command is not None and command.poll() is None:
            command.kill()
            command.wait(timeout=5)
        if frame._kimi_client is not None:
            frame._kimi_client.close()
