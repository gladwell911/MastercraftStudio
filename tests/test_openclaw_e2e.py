import json
import time

from cli_agent_manager import CliRunResult
import main


class _ImmediateThread:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        if self._target:
            self._target(*self._args, **self._kwargs)

    def is_alive(self):
        return False

    def join(self, timeout=None):
        return None


class _PluginOnlyManager:
    def __init__(self):
        self.requests = []

    def run(self, request, on_output=None):
        self.requests.append(request)
        stdout = (
            "[plugins] feishu_doc: Registered feishu_doc, feishu_app_scopes\n"
            "[plugins] feishu_chat: Registered feishu_chat tool\n"
            "[plugins] feishu_wiki: Registered feishu_wiki tool\n"
            "[plugins] feishu_drive: Registered feishu_drive tool\n"
            "[plugins] feishu_bitable: Registered bitable tools\n"
        )
        if on_output:
            on_output(stdout)
        return CliRunResult(returncode=1, stdout=stdout, stderr="")


def _prepare_openclaw_frame(frame, monkeypatch):
    frame._stop_openclaw_sync()
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(main.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)
    monkeypatch.setattr(frame, "_focus_latest_answer", lambda: None)
    monkeypatch.setattr(main.wx, "CallLater", lambda _delay, fn, *a, **k: fn(*a, **k))
    frame.active_session_turns = []
    frame.active_chat_id = ""
    frame.active_openclaw_session_id = ""
    frame.active_openclaw_session_file = ""
    frame.active_openclaw_sync_offset = 0
    frame.active_session_started_at = 0.0
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda force_replay=False: None)


def _write_session_pointer(sessions_dir, session_file, session_id="zgwd-main"):
    sessions_dir.mkdir(parents=True, exist_ok=True)
    if not session_file.exists():
        session_file.write_text("", encoding="utf-8")
    (sessions_dir / "sessions.json").write_text(
        json.dumps(
            {
                "agent:main:main": {
                    "sessionId": session_id,
                    "sessionFile": str(session_file),
                    "updatedAt": 1773672820158,
                }
            }
        ),
        encoding="utf-8",
    )


def _write_session_reply(session_file, user_text, assistant_text, base_time=None):
    now = float(base_time or time.time())
    session_file.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "message",
                        "id": "u1",
                        "timestamp": now + 1,
                        "message": {"role": "user", "content": [{"type": "text", "text": user_text}]},
                    }
                ),
                json.dumps(
                    {
                        "type": "message",
                        "id": "a1",
                        "timestamp": now + 2,
                        "message": {"role": "assistant", "content": [{"type": "text", "text": assistant_text}]},
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )


def test_openclaw_send_flow_updates_from_session_sync(frame, monkeypatch, tmp_path):
    _prepare_openclaw_frame(frame, monkeypatch)
    sessions_dir = tmp_path / ".openclaw" / "agents" / "main" / "sessions"
    session_file = sessions_dir / "main.jsonl"
    _write_session_pointer(sessions_dir, session_file)

    def fake_stream_chat(self, user_text, session_id, on_delta=None):
        assert user_text == "\u6253\u5f00\u63a7\u5236\u53f0"
        assert session_id.startswith("zgwd-")
        _write_session_pointer(sessions_dir, session_file, session_id=session_id)
        _write_session_reply(
            session_file,
            user_text,
            "OpenClaw \u56de\u590d",
            frame.active_session_turns[-1].get("created_at"),
        )
        return "OpenClaw \u56de\u590d"

    monkeypatch.setattr(main, "resolve_openclaw_sessions_dir", lambda _agent="main": sessions_dir)
    monkeypatch.setattr(main.OpenClawClient, "stream_chat", fake_stream_chat)

    frame.model_combo.SetSelection(frame.model_combo.FindString("openclaw"))
    frame.input_edit.SetValue("\u6253\u5f00\u63a7\u5236\u53f0")
    frame._on_send_clicked(None)
    frame._sync_openclaw_once()

    assert frame.selected_model == "openclaw/main"
    assert len(frame.active_session_turns) == 1
    assert frame.active_session_turns[0]["question"] == "\u6253\u5f00\u63a7\u5236\u53f0"
    assert frame.active_session_turns[0]["answer_md"] == "OpenClaw \u56de\u590d"

    answers = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert "OpenClaw \u56de\u590d" in answers


def test_openclaw_send_flow_ignores_plugin_only_cli_output_and_syncs_reply(frame, monkeypatch, tmp_path):
    _prepare_openclaw_frame(frame, monkeypatch)
    monkeypatch.setattr(main.OpenClawClient, "_resolve_openclaw_command", lambda self: "openclaw.cmd")

    sessions_dir = tmp_path / ".openclaw" / "agents" / "main" / "sessions"
    session_file = sessions_dir / "main.jsonl"
    _write_session_pointer(sessions_dir, session_file)
    monkeypatch.setattr(main, "resolve_openclaw_sessions_dir", lambda _agent="main": sessions_dir)
    manager = _PluginOnlyManager()
    frame._cli_agent_manager = manager

    frame.model_combo.SetSelection(frame.model_combo.FindString("openclaw"))
    frame.input_edit.SetValue("\u4f60\u597d")
    frame._on_new_chat_clicked(None)
    frame._on_send_clicked(None)

    assert manager.requests
    assert "\u65e0\u6cd5\u89e3\u6790\u7684 JSON" not in str(frame.active_session_turns[0].get("answer_md") or "")

    _write_session_reply(
        session_file,
        "\u4f60\u597d",
        "OpenClaw reply",
        frame.active_session_turns[-1].get("created_at"),
    )
    _write_session_pointer(sessions_dir, session_file, session_id=manager.requests[-1].command[6])
    frame._sync_openclaw_once()

    assert frame.selected_model == "openclaw/main"
    assert frame.active_session_turns[0]["question"] == "\u4f60\u597d"
    assert frame.active_session_turns[0]["answer_md"] == "OpenClaw reply"
