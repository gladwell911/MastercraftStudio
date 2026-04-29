from cli_agent_manager import CliRunResult
import claudecode_client


class _Manager:
    def __init__(self):
        self.requests = []

    def run(self, request, on_output=None):
        self.requests.append(request)
        chunks = [
            '{"type":"assistant","message":{"content":[{"type":"text","text":"hello"}]}}\n',
            '{"type":"result","session_id":"session-new"}\n',
        ]
        for chunk in chunks:
            if on_output:
                on_output(chunk)
        return CliRunResult(returncode=0, stdout="".join(chunks), stderr="")


def test_claudecode_client_runs_through_cli_agent_manager(monkeypatch):
    manager = _Manager()
    monkeypatch.setattr(claudecode_client, "resolve_claudecode_command", lambda: ["claude.cmd"])
    client = claudecode_client.ClaudeCodeClient(cli_manager=manager)

    seen = []
    text, session_id = client.stream_chat("hello", session_id="session-old", on_delta=seen.append)

    assert text == "hello"
    assert seen == ["hello"]
    assert session_id == "session-new"
    request = manager.requests[0]
    assert request.agent_id == "claudecode"
    assert request.prefer_pty is True
    assert request.command[:5] == [
        "claude.cmd",
        "--print",
        "hello",
        "--output-format",
        "stream-json",
    ]
    assert request.command[-2:] == ["--resume", "session-old"]
