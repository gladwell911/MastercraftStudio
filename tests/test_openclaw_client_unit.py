import json
import subprocess
from pathlib import Path

import pytest

import openclaw_client


def test_openclaw_model_helpers():
    assert openclaw_client.is_openclaw_model("openclaw/main")
    assert not openclaw_client.is_openclaw_model("openai/gpt-5.2")
    assert openclaw_client.model_to_agent_id("openclaw/main") == "main"
    assert openclaw_client.model_to_agent_id("openclaw/ops") == "ops"


def test_openclaw_client_stream_chat_parses_payload_text(monkeypatch):
    client = openclaw_client.OpenClawClient("openclaw/main", timeout=12)

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout='{"payloads":[{"text":"第一段"},{"text":"第二段"}]}',
            stderr="",
        )

    monkeypatch.setattr(openclaw_client.subprocess, "run", fake_run)
    seen = []
    out = client.stream_chat("你好", session_id="zgwd-1", on_delta=seen.append)
    assert out == "第一段\n\n第二段"
    assert seen == ["第一段\n\n第二段"]


def test_openclaw_client_stream_chat_parses_nested_result_payloads(monkeypatch):
    client = openclaw_client.OpenClawClient("openclaw/main", timeout=12)

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout='{"status":"ok","result":{"payloads":[{"text":"[[reply_to_current]] 你好"}]}}',
            stderr="[plugins] warning",
        )

    monkeypatch.setattr(openclaw_client.subprocess, "run", fake_run)
    out = client.stream_chat("你好", session_id="zgwd-1")
    assert out == "你好"


def test_openclaw_client_stream_chat_parses_json_after_plugin_logs(monkeypatch):
    client = openclaw_client.OpenClawClient("openclaw/main", timeout=12)

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=(
                "[plugins] feishu_doc: Registered feishu_doc\n"
                "[plugins] feishu_chat: Registered feishu_chat tool\n"
                '{"status":"ok","result":{"payloads":[{"text":"日志后面的正常回复"}]}}'
            ),
            stderr="",
        )

    monkeypatch.setattr(openclaw_client.subprocess, "run", fake_run)
    out = client.stream_chat("你好", session_id="zgwd-1")
    assert out == "日志后面的正常回复"


def test_openclaw_client_stream_chat_raises_with_payload_error(monkeypatch):
    client = openclaw_client.OpenClawClient("openclaw/main", timeout=12)

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=1,
            stdout='{"payloads":[{"text":"OpenClaw 超时"}]}',
            stderr="stderr detail",
        )

    monkeypatch.setattr(openclaw_client.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="OpenClaw 超时"):
        client.stream_chat("你好", session_id="zgwd-1")


def test_resolve_openclaw_command_falls_back_to_appdata_npm(monkeypatch, tmp_path):
    npm_dir = tmp_path / "npm"
    npm_dir.mkdir()
    target = npm_dir / "openclaw.cmd"
    target.write_text("@echo off\n", encoding="utf-8")

    monkeypatch.setattr(openclaw_client.shutil, "which", lambda _name: None)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    client = openclaw_client.OpenClawClient("openclaw/main")
    assert Path(client._resolve_openclaw_command()) == target


def test_stream_chat_uses_resolved_openclaw_command(monkeypatch):
    client = openclaw_client.OpenClawClient("openclaw/main", timeout=12)
    monkeypatch.setattr(client, "_resolve_openclaw_command", lambda: r"C:\Users\test\AppData\Roaming\npm\openclaw.cmd")

    seen = {}

    def fake_run(*args, **kwargs):
        seen["command"] = args[0]
        return subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout='{"payloads":[{"text":"ok"}]}',
            stderr="",
        )

    monkeypatch.setattr(openclaw_client.subprocess, "run", fake_run)
    out = client.stream_chat("你好", session_id="zgwd-1")
    assert out == "ok"
    assert seen["command"][0].endswith("openclaw.cmd")


def test_load_session_pointer_reads_main_session(tmp_path):
    sessions_path = tmp_path / "sessions.json"
    sessions_path.write_text(
        json.dumps(
            {
                "agent:main:main": {
                    "sessionId": "zgwd-123",
                    "sessionFile": str(tmp_path / "main.jsonl"),
                    "updatedAt": 1773672820158,
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    pointer = openclaw_client.load_session_pointer(sessions_path)
    assert pointer is not None
    assert pointer.session_id == "zgwd-123"
    assert pointer.session_file.endswith("main.jsonl")
    assert pointer.updated_at > 0


def test_read_session_events_extracts_user_and_assistant_text(tmp_path):
    session_path = tmp_path / "main.jsonl"
    session_path.write_text(
        "\n".join(
            [
                json.dumps({"type": "session", "id": "s1"}),
                json.dumps(
                    {
                        "type": "message",
                        "id": "u1",
                        "timestamp": "2026-03-16T02:36:52.503Z",
                        "message": {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        "Sender (untrusted metadata):\n```json\n{}\n```\n\n"
                                        "[Mon 2026-03-16 10:36 GMT+8] 你好"
                                    ),
                                }
                            ],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "message",
                        "id": "a1",
                        "timestamp": "2026-03-16T02:37:04.505Z",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"type": "thinking", "thinking": "skip"},
                                {"type": "text", "text": "[[reply_to_current]] 世界"},
                            ],
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    offset, events = openclaw_client.read_session_events(session_path, 0)
    assert offset > 0
    assert [(event.event_id, event.role, event.text) for event in events] == [
        ("u1", "user", "你好"),
        ("a1", "assistant", "世界"),
    ]
