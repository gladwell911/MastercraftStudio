from types import SimpleNamespace

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


def test_stream_chat_uses_plain_stdout_when_stream_json_is_not_emitted(monkeypatch):
    popen_calls = []
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

    def _popen(command, **kwargs):
        popen_calls.append(SimpleNamespace(command=command, kwargs=kwargs))
        return _FakeProcess([f"{line}\n" for line in plain_stdout])

    monkeypatch.setattr(claudecode_client, "resolve_claudecode_command", lambda: ["claude.cmd"])
    monkeypatch.setattr(claudecode_client.subprocess, "Popen", _popen)
    monkeypatch.setattr(claudecode_client.threading, "Thread", _ImmediateThread)

    seen = []
    text, session_id = claudecode_client.ClaudeCodeClient().stream_chat(
        "rename model display labels",
        on_delta=seen.append,
    )

    assert text == "\n".join(plain_stdout)
    assert seen == [text]
    assert session_id == ""
    assert popen_calls[0].command == [
        "claude.cmd",
        "--print",
        "rename model display labels",
        "--output-format",
        "stream-json",
        "--verbose",
    ]
