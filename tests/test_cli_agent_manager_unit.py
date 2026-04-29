from types import SimpleNamespace

import pytest

from cli_agent_manager import (
    CliAgentManager,
    CliRunResult,
    CliRunRequest,
)


class _Runtime:
    def __init__(self, available=True):
        self.available = available
        self.requests = []

    def is_available(self):
        return self.available

    def run(self, request, on_output=None):
        self.requests.append(request)
        if on_output:
            on_output("runtime output\n")
        return CliRunResult(returncode=0, stdout="runtime output\n", stderr="")


def test_manager_prefers_pty_for_interactive_windows_agents():
    pty = _Runtime(available=True)
    process = _Runtime(available=True)
    manager = CliAgentManager(pty_runtime=pty, process_runtime=process, os_name="nt")

    result = manager.run(
        CliRunRequest(
            agent_id="claudecode",
            command=["claude.cmd", "--print", "hello"],
            timeout=10,
            prefer_pty=True,
        )
    )

    assert result.stdout == "runtime output\n"
    assert pty.requests[0].agent_id == "claudecode"
    assert process.requests == []


def test_manager_falls_back_to_process_when_pty_unavailable():
    pty = _Runtime(available=False)
    process = _Runtime(available=True)
    manager = CliAgentManager(pty_runtime=pty, process_runtime=process, os_name="nt")

    manager.run(
        CliRunRequest(
            agent_id="openclaw",
            command=["openclaw.cmd", "agent"],
            timeout=10,
            prefer_pty=True,
        )
    )

    assert pty.requests == []
    assert process.requests[0].agent_id == "openclaw"


def test_manager_raises_when_runtime_returns_nonzero():
    process = _Runtime(available=True)

    def _fail(request, on_output=None):
        return CliRunResult(returncode=7, stdout="", stderr="bad")

    process.run = _fail
    manager = CliAgentManager(pty_runtime=_Runtime(False), process_runtime=process, os_name="posix")

    with pytest.raises(RuntimeError, match="bad"):
        manager.run(CliRunRequest(agent_id="tool", command=["tool"], timeout=10))
