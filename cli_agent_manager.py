from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol


OutputCallback = Callable[[str], None]


@dataclass
class CliRunRequest:
    agent_id: str
    command: list[str]
    timeout: float
    cwd: str = ""
    env: dict[str, str] | None = None
    input_text: str = ""
    prefer_pty: bool = False
    check: bool = True
    metadata: dict = field(default_factory=dict)


@dataclass
class CliRunResult:
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    runtime: str = ""


class CliRuntime(Protocol):
    name: str

    def is_available(self) -> bool:
        ...

    def run(self, request: CliRunRequest, on_output: OutputCallback | None = None) -> CliRunResult:
        ...


class SubprocessCliRuntime:
    name = "subprocess"

    def is_available(self) -> bool:
        return True

    def run(self, request: CliRunRequest, on_output: OutputCallback | None = None) -> CliRunResult:
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        proc = subprocess.Popen(
            list(request.command or []),
            cwd=request.cwd or None,
            env=request.env or os.environ.copy(),
            stdin=subprocess.PIPE if request.input_text else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            bufsize=1,
            **_windows_hidden_popen_kwargs(),
        )

        def _read_stdout() -> None:
            if proc.stdout is None:
                return
            for chunk in proc.stdout:
                stdout_parts.append(chunk)
                if callable(on_output):
                    on_output(chunk)

        def _read_stderr() -> None:
            if proc.stderr is None:
                return
            for chunk in proc.stderr:
                stderr_parts.append(chunk)

        stdout_thread = threading.Thread(target=_read_stdout, daemon=True)
        stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
        stdout_thread.start()
        stderr_thread.start()
        if request.input_text and proc.stdin is not None:
            try:
                proc.stdin.write(request.input_text)
                proc.stdin.flush()
            finally:
                try:
                    proc.stdin.close()
                except Exception:
                    pass
        try:
            proc.wait(timeout=max(float(request.timeout or 1), 1.0))
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2.0)
            raise RuntimeError(f"{request.agent_id} CLI request timed out after {request.timeout} seconds.")
        stdout_thread.join(timeout=2.0)
        stderr_thread.join(timeout=2.0)
        return CliRunResult(
            returncode=proc.returncode,
            stdout="".join(stdout_parts),
            stderr="".join(stderr_parts),
            runtime=self.name,
        )


class WinPtyCliRuntime:
    name = "pywinpty"

    def __init__(self) -> None:
        self._pty_process_cls = None
        try:
            from winpty import PtyProcess  # type: ignore

            self._pty_process_cls = PtyProcess
        except Exception:
            self._pty_process_cls = None

    def is_available(self) -> bool:
        return os.name == "nt" and self._pty_process_cls is not None

    def run(self, request: CliRunRequest, on_output: OutputCallback | None = None) -> CliRunResult:
        if not self.is_available():
            raise RuntimeError("pywinpty runtime is not available.")
        command = subprocess.list2cmdline(list(request.command or []))
        proc = self._pty_process_cls.spawn(  # type: ignore[union-attr]
            command,
            cwd=request.cwd or None,
            env=request.env or os.environ.copy(),
            dimensions=(120, 40),
        )
        if request.input_text:
            proc.write(request.input_text)
        stdout_parts: list[str] = []
        deadline = time.time() + max(float(request.timeout or 1), 1.0)
        exitstatus = None
        while proc.isalive():
            if time.time() >= deadline:
                try:
                    proc.terminate(force=True)
                except Exception:
                    pass
                raise RuntimeError(f"{request.agent_id} CLI request timed out after {request.timeout} seconds.")
            try:
                chunk = proc.read(4096)
            except Exception:
                time.sleep(0.02)
                continue
            if not chunk:
                time.sleep(0.02)
                continue
            text = _strip_ansi(str(chunk))
            stdout_parts.append(text)
            if callable(on_output):
                on_output(text)
        try:
            exitstatus = proc.exitstatus
        except Exception:
            exitstatus = None
        return CliRunResult(returncode=exitstatus, stdout="".join(stdout_parts), stderr="", runtime=self.name)


class CliAgentManager:
    def __init__(
        self,
        *,
        pty_runtime: CliRuntime | None = None,
        process_runtime: CliRuntime | None = None,
        os_name: str | None = None,
    ) -> None:
        self.pty_runtime = pty_runtime if pty_runtime is not None else WinPtyCliRuntime()
        self.process_runtime = process_runtime if process_runtime is not None else SubprocessCliRuntime()
        self.os_name = os_name if os_name is not None else os.name

    def run(self, request: CliRunRequest, on_output: OutputCallback | None = None) -> CliRunResult:
        runtime = self._select_runtime(request)
        result = runtime.run(request, on_output=on_output)
        result.runtime = result.runtime or getattr(runtime, "name", "")
        if request.check and result.returncode not in (0, None):
            detail = (result.stderr or result.stdout or f"exit code {result.returncode}").strip()
            raise RuntimeError(detail)
        return result

    def _select_runtime(self, request: CliRunRequest) -> CliRuntime:
        if request.prefer_pty and self.os_name == "nt" and self.pty_runtime.is_available():
            return self.pty_runtime
        return self.process_runtime


_DEFAULT_MANAGER: CliAgentManager | None = None


def get_default_cli_agent_manager() -> CliAgentManager:
    global _DEFAULT_MANAGER
    if _DEFAULT_MANAGER is None:
        _DEFAULT_MANAGER = CliAgentManager()
    return _DEFAULT_MANAGER


def _windows_hidden_popen_kwargs() -> dict:
    if os.name != "nt":
        return {}
    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0)
    return {"creationflags": creationflags} if creationflags else {}


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", str(text or ""))
