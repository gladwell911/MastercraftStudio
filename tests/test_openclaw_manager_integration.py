from cli_agent_manager import CliRunResult
import openclaw_client


class _Manager:
    def __init__(self):
        self.requests = []

    def run(self, request, on_output=None):
        self.requests.append(request)
        stdout = '{"payloads":[{"text":"ok"}]}'
        if on_output:
            on_output(stdout)
        return CliRunResult(returncode=0, stdout=stdout, stderr="")


def test_openclaw_client_runs_through_cli_agent_manager(monkeypatch):
    manager = _Manager()
    client = openclaw_client.OpenClawClient("openclaw/main", timeout=12, cli_manager=manager)
    monkeypatch.setattr(client, "_resolve_openclaw_command", lambda: "openclaw.cmd")

    out = client.stream_chat("hello", session_id="zgwd-1")

    assert out == "ok"
    request = manager.requests[0]
    assert request.agent_id == "openclaw"
    assert request.prefer_pty is True
    assert request.timeout == 42
    assert request.command[:6] == [
        "openclaw.cmd",
        "--no-color",
        "agent",
        "--agent",
        "main",
        "--session-id",
    ]
