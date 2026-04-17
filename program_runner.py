"""
Stable program runner used by the desktop app and tests.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

try:
    import psutil
except ImportError:  # pragma: no cover - optional dependency
    psutil = None


class RunState(Enum):
    """Execution state for the current program."""

    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass
class RunResult:
    """Result payload returned by ProgramRunner."""

    state: RunState
    returncode: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    error_msg: str = ""
    pid: Optional[int] = None
    duration: float = 0.0


class ProgramRunner:
    """Thread-safe helper for launching console or GUI Python programs."""

    def __init__(
        self,
        on_state_changed: Optional[Callable[[RunState, str], None]] = None,
    ) -> None:
        self.on_state_changed = on_state_changed
        self._current_process: Optional[subprocess.Popen] = None
        self._current_pid: Optional[int] = None
        self._state = RunState.IDLE
        self._lock = threading.Lock()
        self._result: Optional[RunResult] = None
        self._start_time: float = 0.0

    @property
    def state(self) -> RunState:
        with self._lock:
            return self._state

    @property
    def is_running(self) -> bool:
        return self.state == RunState.RUNNING

    def _set_state(self, new_state: RunState, message: str = "") -> None:
        callback = None
        with self._lock:
            if self._state == new_state:
                return
            self._state = new_state
            callback = self.on_state_changed
        if callback is not None:
            callback(new_state, message)

    def stop(self, timeout: float = 5.0) -> bool:
        with self._lock:
            process = self._current_process
            if process is None or self._current_pid is None:
                return False

        try:
            process.terminate()
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)
            return True
        except Exception as exc:
            print(f"[错误] 停止进程失败: {exc}")
            return False
        finally:
            with self._lock:
                self._current_process = None
                self._current_pid = None

    def run_console_program(
        self,
        python_cmd: str,
        main_file: str,
        cwd: str,
        timeout: float = 300.0,
    ) -> RunResult:
        if self.is_running:
            return RunResult(
                state=RunState.FAILED,
                error_msg="已有程序在运行，请先停止",
            )

        self._set_state(RunState.RUNNING, f"正在运行: {os.path.basename(main_file)}")
        self._start_time = time.time()

        try:
            with self._lock:
                process = subprocess.Popen(
                    [python_cmd, main_file],
                    cwd=cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                self._current_process = process
                self._current_pid = process.pid

            try:
                stdout, stderr = process.communicate(timeout=timeout)
                returncode = process.returncode
                duration = time.time() - self._start_time
                if returncode == 0:
                    self._set_state(RunState.COMPLETED, "运行成功")
                    result = RunResult(
                        state=RunState.COMPLETED,
                        returncode=returncode,
                        stdout=stdout,
                        stderr=stderr,
                        pid=process.pid,
                        duration=duration,
                    )
                else:
                    self._set_state(RunState.FAILED, f"运行失败（退出码 {returncode}）")
                    result = RunResult(
                        state=RunState.FAILED,
                        returncode=returncode,
                        stdout=stdout,
                        stderr=stderr,
                        pid=process.pid,
                        duration=duration,
                    )
                self._result = result
                return result
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                duration = time.time() - self._start_time
                message = f"程序运行超时（{timeout}秒）"
                self._set_state(RunState.TIMEOUT, message)
                result = RunResult(
                    state=RunState.TIMEOUT,
                    error_msg=message,
                    pid=process.pid,
                    duration=duration,
                )
                self._result = result
                return result
        except Exception as exc:
            duration = time.time() - self._start_time
            self._set_state(RunState.FAILED, f"运行出错: {exc}")
            result = RunResult(
                state=RunState.FAILED,
                error_msg=str(exc),
                duration=duration,
            )
            self._result = result
            return result
        finally:
            with self._lock:
                self._current_process = None
                self._current_pid = None

    def run_gui_program(
        self,
        python_cmd: str,
        main_file: str,
        cwd: str,
    ) -> RunResult:
        if self.is_running:
            return RunResult(
                state=RunState.FAILED,
                error_msg="已有程序在运行，请先停止",
            )

        self._set_state(RunState.RUNNING, f"正在启动: {os.path.basename(main_file)}")
        self._start_time = time.time()

        try:
            with self._lock:
                popen_kwargs = {
                    "cwd": cwd,
                    "shell": False,
                }
                if os.name == "nt":
                    popen_kwargs["creationflags"] = 0x00000010  # CREATE_NEW_CONSOLE
                process = subprocess.Popen([python_cmd, main_file], **popen_kwargs)
                self._current_process = process
                self._current_pid = process.pid

            threading.Thread(
                target=self._monitor_gui_process,
                args=(process,),
                daemon=True,
            ).start()

            self._set_state(RunState.RUNNING, f"已启动(PID: {process.pid})")
            result = RunResult(state=RunState.RUNNING, pid=process.pid)
            self._result = result
            return result
        except Exception as exc:
            self._set_state(RunState.FAILED, f"启动失败: {exc}")
            result = RunResult(state=RunState.FAILED, error_msg=str(exc))
            self._result = result
            return result

    def _monitor_gui_process(self, process: subprocess.Popen) -> None:
        try:
            if psutil is not None:
                psutil.Process(process.pid).wait()
            else:
                process.wait()
            duration = time.time() - self._start_time
            self._set_state(RunState.COMPLETED, "GUI 程序已关闭")
            with self._lock:
                if self._result is not None:
                    self._result.state = RunState.COMPLETED
                    self._result.duration = duration
        except getattr(psutil, "NoSuchProcess", ProcessLookupError):
            pass
        except Exception as exc:
            print(f"[错误] 监控进程失败: {exc}")

    def get_result(self) -> Optional[RunResult]:
        with self._lock:
            return self._result
