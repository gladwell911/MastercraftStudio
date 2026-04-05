"""
稳定的程序运行管理器
- 进程生命周期管理
- 线程安全的状态跟踪
- 自动清理资源
- 错误恢复机制
"""

import os
import sys
import subprocess
import threading
import time
import psutil
from typing import Optional, Callable
from dataclasses import dataclass
from enum import Enum


class RunState(Enum):
    """运行状态"""
    IDLE = "idle"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass
class RunResult:
    """运行结果"""
    state: RunState
    returncode: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    error_msg: str = ""
    pid: Optional[int] = None
    duration: float = 0.0


class ProgramRunner:
    """稳定的程序运行管理器"""

    def __init__(self, on_state_changed: Optional[Callable[[RunState, str], None]] = None):
        self.on_state_changed = on_state_changed
        self._current_process: Optional[subprocess.Popen] = None
        self._current_pid: Optional[int] = None
        self._state = RunState.IDLE
        self._lock = threading.Lock()
        self._result: Optional[RunResult] = None
        self._start_time: float = 0.0

    @property
    def state(self) -> RunState:
        """获取当前状态"""
        with self._lock:
            return self._state

    @property
    def is_running(self) -> bool:
        """是否正在运行"""
        return self.state == RunState.RUNNING

    def _set_state(self, new_state: RunState, message: str = "") -> None:
        """设置状态并触发回调"""
        with self._lock:
            if self._state != new_state:
                self._state = new_state
                if self.on_state_changed:
                    self.on_state_changed(new_state, message)

    def stop(self, timeout: float = 5.0) -> bool:
        """停止当前运行的程序"""
        with self._lock:
            if not self._current_process or not self._current_pid:
                return False

            try:
                # 尝试优雅关闭
                self._current_process.terminate()
                try:
                    self._current_process.wait(timeout=timeout)
                    return True
                except subprocess.TimeoutExpired:
                    # 强制杀死
                    self._current_process.kill()
                    self._current_process.wait(timeout=2.0)
                    return True
            except Exception as e:
                print(f"[错误] 停止进程失败: {e}")
                return False
            finally:
                self._current_process = None
                self._current_pid = None

    def run_console_program(
        self,
        python_cmd: str,
        main_file: str,
        cwd: str,
        timeout: float = 300.0,
    ) -> RunResult:
        """运行控制台程序（阻塞式）"""
        # 检查是否已有程序在运行
        if self.is_running:
            return RunResult(
                state=RunState.FAILED,
                error_msg="已有程序在运行，请先停止"
            )

        self._set_state(RunState.RUNNING, f"正在运行: {os.path.basename(main_file)}")
        self._start_time = time.time()

        try:
            with self._lock:
                self._current_process = subprocess.Popen(
                    [python_cmd, main_file],
                    cwd=cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                self._current_pid = self._current_process.pid

            try:
                stdout, stderr = self._current_process.communicate(timeout=timeout)
                returncode = self._current_process.returncode

                duration = time.time() - self._start_time

                if returncode == 0:
                    self._set_state(RunState.COMPLETED, "运行成功")
                    result = RunResult(
                        state=RunState.COMPLETED,
                        returncode=returncode,
                        stdout=stdout,
                        stderr=stderr,
                        pid=self._current_pid,
                        duration=duration,
                    )
                else:
                    self._set_state(RunState.FAILED, f"运行失败（退出码 {returncode}）")
                    result = RunResult(
                        state=RunState.FAILED,
                        returncode=returncode,
                        stdout=stdout,
                        stderr=stderr,
                        pid=self._current_pid,
                        duration=duration,
                    )

                self._result = result
                return result

            except subprocess.TimeoutExpired:
                # 超时处理
                self._current_process.kill()
                self._current_process.wait()
                duration = time.time() - self._start_time

                self._set_state(RunState.TIMEOUT, f"程序运行超时（{timeout}秒）")
                result = RunResult(
                    state=RunState.TIMEOUT,
                    error_msg=f"程序运行超时（{timeout}秒）",
                    pid=self._current_pid,
                    duration=duration,
                )
                self._result = result
                return result

        except Exception as e:
            duration = time.time() - self._start_time
            self._set_state(RunState.FAILED, f"运行出错: {str(e)}")
            result = RunResult(
                state=RunState.FAILED,
                error_msg=str(e),
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
        """运行 GUI 程序（非阻塞式）"""
        # 检查是否已有程序在运行
        if self.is_running:
            return RunResult(
                state=RunState.FAILED,
                error_msg="已有程序在运行，请先停止"
            )

        self._set_state(RunState.RUNNING, f"正在启动: {os.path.basename(main_file)}")
        self._start_time = time.time()

        try:
            with self._lock:
                if os.name == 'nt':
                    CREATE_NEW_CONSOLE = 0x00000010
                    self._current_process = subprocess.Popen(
                        [python_cmd, main_file],
                        cwd=cwd,
                        creationflags=CREATE_NEW_CONSOLE,
                        shell=False,
                    )
                else:
                    self._current_process = subprocess.Popen(
                        [python_cmd, main_file],
                        cwd=cwd,
                        shell=False,
                    )
                self._current_pid = self._current_process.pid

            # 启动监控线程
            threading.Thread(
                target=self._monitor_gui_process,
                args=(self._current_pid,),
                daemon=True
            ).start()

            self._set_state(RunState.RUNNING, f"已启动 (PID: {self._current_pid})")
            result = RunResult(
                state=RunState.RUNNING,
                pid=self._current_pid,
            )
            self._result = result
            return result

        except Exception as e:
            self._set_state(RunState.FAILED, f"启动失败: {str(e)}")
            result = RunResult(
                state=RunState.FAILED,
                error_msg=str(e),
            )
            self._result = result
            return result

    def _monitor_gui_process(self, pid: int) -> None:
        """监控 GUI 进程"""
        try:
            process = psutil.Process(pid)
            process.wait()  # 等待进程结束
            duration = time.time() - self._start_time
            self._set_state(RunState.COMPLETED, "GUI 程序已关闭")
            with self._lock:
                if self._result:
                    self._result.state = RunState.COMPLETED
                    self._result.duration = duration
        except psutil.NoSuchProcess:
            pass
        except Exception as e:
            print(f"[错误] 监控进程失败: {e}")

    def get_result(self) -> Optional[RunResult]:
        """获取最后的运行结果"""
        with self._lock:
            return self._result
