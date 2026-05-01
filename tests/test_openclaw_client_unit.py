import json
import subprocess
from pathlib import Path

import pytest

from cli_agent_manager import CliRunResult
import openclaw_client


class _Manager:
    def __init__(self, stdout: str, stderr: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.requests = []

    def run(self, request, on_output=None):
        self.requests.append(request)
        if on_output:
            on_output(self.stdout)
        return CliRunResult(returncode=self.returncode, stdout=self.stdout, stderr=self.stderr)


def test_openclaw_model_helpers():
    assert openclaw_client.is_openclaw_model("openclaw/main")
    assert not openclaw_client.is_openclaw_model("openai/gpt-5.2")
    assert openclaw_client.model_to_agent_id("openclaw/main") == "main"
    assert openclaw_client.model_to_agent_id("openclaw/ops") == "ops"


def test_openclaw_client_stream_chat_parses_payload_text(monkeypatch):
    client = openclaw_client.OpenClawClient(
        "openclaw/main",
        timeout=12,
        cli_manager=_Manager('{"payloads":[{"text":"第一段"},{"text":"第二段"}]}'),
    )
    seen = []
    out = client.stream_chat("你好", session_id="zgwd-1", on_delta=seen.append)
    assert out == "第一段\n\n第二段"
    assert seen == ["第一段\n\n第二段"]


def test_openclaw_client_stream_chat_records_model_usage_metadata():
    payload = {
        "status": "ok",
        "result": {
            "payloads": [{"text": "ok"}],
            "modelUsage": {
                "gpt-5.4": {
                    "inputTokens": 620,
                    "outputTokens": 40,
                    "cacheReadInputTokens": 100000,
                    "cacheCreationInputTokens": 12600,
                    "contextWindow": 272000,
                }
            },
        },
    }
    client = openclaw_client.OpenClawClient(
        "openclaw/main",
        timeout=12,
        cli_manager=_Manager(json.dumps(payload, ensure_ascii=False)),
    )

    out = client.stream_chat("hello", session_id="zgwd-1")

    assert out == "ok"
    assert client.last_context_usage["used_tokens"] == 113260
    assert client.last_context_usage["context_window"] == 272000
    assert client.last_context_usage["source"] == "openclaw"
    assert client.last_context_usage["exact"] is True
    assert client.last_context_usage["fresh"] is True
    assert client.last_context_usage["model"] == "gpt-5.4"


def test_openclaw_client_stream_chat_uses_model_window_when_usage_lacks_context_window():
    payload = {
        "payloads": [{"text": "ok"}],
        "message": {
            "role": "assistant",
            "usage": {
                "input_tokens": 620,
                "output_tokens": 40,
                "cache_read_input_tokens": 100000,
                "cache_creation_input_tokens": 12600,
            },
        },
    }
    client = openclaw_client.OpenClawClient(
        "openclaw/main",
        timeout=12,
        cli_manager=_Manager(json.dumps(payload, ensure_ascii=False)),
    )

    out = client.stream_chat("hello", session_id="zgwd-1")

    assert out == "ok"
    assert client.last_context_usage["used_tokens"] == 113260
    assert client.last_context_usage["context_window"] == 272000
    assert client.last_context_usage["exact"] is False
    assert client.last_context_usage["model"] == "openclaw/main"


def test_openclaw_client_stream_chat_records_usage_from_json_event_stream():
    events = [
        {
            "type": "message",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "ok"}],
            },
        },
        {
            "type": "result",
            "modelUsage": {
                "gpt-5.4": {
                    "inputTokens": 620,
                    "outputTokens": 40,
                    "cacheReadInputTokens": 100000,
                    "cacheCreationInputTokens": 12600,
                    "contextWindow": 272000,
                }
            },
        },
    ]
    stdout = "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n"
    client = openclaw_client.OpenClawClient(
        "openclaw/main",
        timeout=12,
        cli_manager=_Manager(stdout),
    )

    out = client.stream_chat("hello", session_id="zgwd-1")

    assert out == "ok"
    assert client.last_context_usage["used_tokens"] == 113260
    assert client.last_context_usage["model"] == "gpt-5.4"


def test_openclaw_client_prefers_result_usage_over_nested_tool_usage():
    events = [
        {
            "type": "message",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_result",
                        "result": {
                            "modelUsage": {
                                "tool-model": {
                                    "inputTokens": 1,
                                    "outputTokens": 2,
                                    "contextWindow": 100,
                                }
                            }
                        },
                    },
                    {"type": "text", "text": "ok"},
                ],
            },
        },
        {
            "type": "result",
            "modelUsage": {
                "gpt-5.4": {
                    "inputTokens": 620,
                    "outputTokens": 40,
                    "cacheReadInputTokens": 100000,
                    "cacheCreationInputTokens": 12600,
                    "contextWindow": 272000,
                }
            },
        },
    ]
    stdout = "\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n"
    client = openclaw_client.OpenClawClient(
        "openclaw/main",
        timeout=12,
        cli_manager=_Manager(stdout),
    )

    out = client.stream_chat("hello", session_id="zgwd-1")

    assert out == "ok"
    assert client.last_context_usage["used_tokens"] == 113260
    assert client.last_context_usage["model"] == "gpt-5.4"


def test_openclaw_client_nonzero_result_leaves_last_context_usage_empty():
    payload = {
        "payloads": [{"text": "failed"}],
        "modelUsage": {
            "gpt-5.4": {
                "inputTokens": 620,
                "outputTokens": 40,
                "contextWindow": 272000,
            }
        },
    }
    client = openclaw_client.OpenClawClient(
        "openclaw/main",
        timeout=12,
        cli_manager=_Manager(json.dumps(payload, ensure_ascii=False), returncode=1),
    )

    with pytest.raises(RuntimeError, match="failed"):
        client.stream_chat("hello", session_id="zgwd-1")

    assert client.last_context_usage is None


def test_openclaw_client_callback_error_clears_last_context_usage():
    payload = {
        "payloads": [{"text": "ok"}],
        "modelUsage": {
            "gpt-5.4": {
                "inputTokens": 620,
                "outputTokens": 40,
                "contextWindow": 272000,
            }
        },
    }
    client = openclaw_client.OpenClawClient(
        "openclaw/main",
        timeout=12,
        cli_manager=_Manager(json.dumps(payload, ensure_ascii=False)),
    )

    def _raise(_delta):
        raise RuntimeError("callback failed")

    with pytest.raises(RuntimeError, match="callback failed"):
        client.stream_chat("hello", session_id="zgwd-1", on_delta=_raise)

    assert client.last_context_usage is None


def test_openclaw_client_stream_chat_ignores_malformed_usage_metadata():
    payload = {
        "payloads": [{"text": "ok"}],
        "message": {
            "role": "assistant",
            "usage": {
                "input_tokens": "bad",
                "output_tokens": 40,
                "cache_read_input_tokens": 100,
            },
        },
    }
    client = openclaw_client.OpenClawClient(
        "openclaw/main",
        timeout=12,
        cli_manager=_Manager(json.dumps(payload, ensure_ascii=False)),
    )

    out = client.stream_chat("hello", session_id="zgwd-1")

    assert out == "ok"
    assert client.last_context_usage is None


def test_openclaw_client_stream_chat_blank_input_clears_last_context_usage():
    client = openclaw_client.OpenClawClient(
        "openclaw/main",
        timeout=12,
        cli_manager=_Manager('{"payloads":[{"text":"ok"}]}'),
    )
    client.last_context_usage = {"used_tokens": 123}

    out = client.stream_chat("   ", session_id="zgwd-1")

    assert out == ""
    assert client.last_context_usage is None


def test_openclaw_client_stream_chat_parses_nested_result_payloads(monkeypatch):
    client = openclaw_client.OpenClawClient(
        "openclaw/main",
        timeout=12,
        cli_manager=_Manager(
            '{"status":"ok","result":{"payloads":[{"text":"[[reply_to_current]] 你好"}]}}',
            stderr="[plugins] warning",
        ),
    )
    out = client.stream_chat("你好", session_id="zgwd-1")
    assert out == "你好"


def test_openclaw_client_stream_chat_parses_json_after_plugin_logs(monkeypatch):
    client = openclaw_client.OpenClawClient(
        "openclaw/main",
        timeout=12,
        cli_manager=_Manager(
            "[plugins] feishu_doc: Registered feishu_doc\n"
            "[plugins] feishu_chat: Registered feishu_chat tool\n"
            '{"status":"ok","result":{"payloads":[{"text":"日志后面的正常回复"}]}}'
        ),
    )
    out = client.stream_chat("你好", session_id="zgwd-1")
    assert out == "日志后面的正常回复"


def test_openclaw_client_stream_chat_uses_plain_stdout_when_json_is_not_emitted(monkeypatch):
    client = openclaw_client.OpenClawClient(
        "openclaw/main",
        timeout=12,
        cli_manager=_Manager("completed changes:\nopenclaw/main -> openclaw\ncodex/main -> codex\n"),
    )
    seen = []
    out = client.stream_chat("rename model display labels", session_id="zgwd-1", on_delta=seen.append)
    assert out == "completed changes:\nopenclaw/main -> openclaw\ncodex/main -> codex"
    assert seen == [out]


def test_openclaw_client_stream_chat_raises_with_payload_error(monkeypatch):
    client = openclaw_client.OpenClawClient(
        "openclaw/main",
        timeout=12,
        cli_manager=_Manager('{"payloads":[{"text":"OpenClaw 超时"}]}', stderr="stderr detail", returncode=1),
    )
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
    manager = _Manager('{"payloads":[{"text":"ok"}]}')
    client = openclaw_client.OpenClawClient("openclaw/main", timeout=12, cli_manager=manager)
    monkeypatch.setattr(client, "_resolve_openclaw_command", lambda: r"C:\Users\test\AppData\Roaming\npm\openclaw.cmd")
    out = client.stream_chat("你好", session_id="zgwd-1")
    assert out == "ok"
    assert manager.requests[0].command[0].endswith("openclaw.cmd")


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
