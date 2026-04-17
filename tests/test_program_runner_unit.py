import subprocess
from unittest.mock import Mock

import program_runner
from program_runner import ProgramRunner, RunResult, RunState


def test_program_runner_imports_without_psutil():
    assert hasattr(program_runner, "ProgramRunner")


def test_monitor_gui_process_falls_back_to_popen_wait(monkeypatch):
    runner = ProgramRunner()
    runner._start_time = 1.0
    runner._result = RunResult(state=RunState.RUNNING, pid=42)
    states = []
    runner.on_state_changed = lambda state, message: states.append((state, message))

    fake_process = Mock(spec=subprocess.Popen)
    fake_process.pid = 42
    fake_process.wait.return_value = 0

    monkeypatch.setattr(program_runner, "psutil", None)
    monkeypatch.setattr(program_runner.time, "time", lambda: 4.5)

    runner._monitor_gui_process(fake_process)

    fake_process.wait.assert_called_once_with()
    assert runner.get_result().state == RunState.COMPLETED
    assert runner.get_result().duration == 3.5
    assert states[-1] == (RunState.COMPLETED, "GUI 程序已关闭")
