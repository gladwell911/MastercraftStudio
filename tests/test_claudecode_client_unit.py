import json
from types import SimpleNamespace

from cli_agent_manager import CliRunResult
import claudecode_client


class _FakeProcess:
    def __init__(self, stdout_lines, stderr_lines=None, returncode=0):
        self.stdout = iter(stdout_lines)
        self.stderr = iter(stderr_lines or [])
        self.returncode = returncode

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


class _ImmediateThread:
    def __init__(self, target, daemon=False):
        self._target = target

    def start(self):
        self._target()

    def join(self, timeout=None):
        return None


class _Manager:
    def __init__(self, stdout: str, stderr: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.requests = []

    def run(self, request, on_output=None):
        self.requests.append(request)
        if on_output:
            for line in self.stdout.splitlines(True):
                on_output(line)
        return CliRunResult(returncode=self.returncode, stdout=self.stdout, stderr=self.stderr)


class _UsageResult:
    returncode = 0
    stderr = ""


class _UsageManager:
    def run(self, request, on_output=None):
        payloads = [
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "text", "text": "完成"}],
                    "usage": {"input_tokens": 31747, "output_tokens": 1},
                },
                "session_id": "sid-1",
            },
            {
                "type": "result",
                "session_id": "sid-1",
                "modelUsage": {
                    "claude-haiku-4-5-20251001": {
                        "inputTokens": 795,
                        "outputTokens": 99,
                        "cacheReadInputTokens": 0,
                        "cacheCreationInputTokens": 29798,
                        "contextWindow": 200000,
                    }
                },
            },
        ]
        for payload in payloads:
            on_output(json.dumps(payload, ensure_ascii=False) + "\n")
        return _UsageResult()


def test_claudecode_stream_chat_records_model_usage():
    client = claudecode_client.ClaudeCodeClient(cli_manager=_UsageManager())

    full, session_id = client.stream_chat("修复问题")

    assert full == "完成"
    assert session_id == "sid-1"
    assert client.last_context_usage["used_tokens"] == 30692
    assert client.last_context_usage["context_window"] == 200000
    assert client.last_context_usage["source"] == "claudecode"
    assert client.last_context_usage["model"] == "claude-haiku-4-5-20251001"


def test_stream_chat_uses_plain_stdout_when_stream_json_is_not_emitted(monkeypatch):
    plain_stdout = [
        "completed changes:",
        "openclaw/main -> openclaw",
        "codex/main -> codex",
        "step 1 done",
        "step 2 done",
        "step 3 done",
        "step 4 done",
        "step 5 done",
        "step 6 done",
        "step 7 done",
        "step 8 done",
    ]
    manager = _Manager("\n".join(plain_stdout) + "\n")

    monkeypatch.setattr(claudecode_client, "resolve_claudecode_command", lambda: ["claude.cmd"])

    seen = []
    text, session_id = claudecode_client.ClaudeCodeClient(cli_manager=manager).stream_chat(
        "rename model display labels",
        on_delta=seen.append,
    )

    assert text == "\n".join(plain_stdout)
    assert seen == [text]
    assert session_id == ""
    assert manager.requests[0].command == [
        "claude.cmd",
        "--print",
        "rename model display labels",
        "--output-format",
        "stream-json",
        "--verbose",
    ]
