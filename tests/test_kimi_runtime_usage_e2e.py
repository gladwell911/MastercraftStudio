"""Opt-in real Kimi server against a loopback model with known usage."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading

import pytest
import wx

from test_voice_input_ui_automation import _pump_until


pytestmark = pytest.mark.skipif(
    os.getenv("MC_KIMI_RUNTIME_TEST") != "1",
    reason="set MC_KIMI_RUNTIME_TEST=1 to spawn the local runtime",
)


@pytest.fixture
def loopback_kimi_home(tmp_path, monkeypatch):
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
    frame, wx_app, monkeypatch, loopback_kimi_home, caplog
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
