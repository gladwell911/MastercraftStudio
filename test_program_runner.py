"""
ProgramRunner 的单元测试
"""

import os
import sys
import time
import tempfile
import unittest
from pathlib import Path

from program_runner import ProgramRunner, RunState, RunResult


class TestProgramRunner(unittest.TestCase):
    """测试 ProgramRunner 类"""

    def setUp(self):
        """测试前准备"""
        self.runner = ProgramRunner()
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        """测试后清理"""
        if self.runner.is_running:
            self.runner.stop()

    def _create_test_script(self, name: str, content: str) -> str:
        """创建测试脚本"""
        script_path = os.path.join(self.temp_dir, name)
        with open(script_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return script_path

    def test_console_program_success(self):
        """测试成功运行控制台程序"""
        script = self._create_test_script('test_success.py', '''
print("Hello, World!")
print("Test output")
sys.exit(0)
''')

        result = self.runner.run_console_program(
            sys.executable,
            script,
            self.temp_dir,
            timeout=10.0
        )

        self.assertEqual(result.state, RunState.COMPLETED)
        self.assertEqual(result.returncode, 0)
        self.assertIn("Hello, World!", result.stdout)

    def test_console_program_failure(self):
        """测试失败的控制台程序"""
        script = self._create_test_script('test_failure.py', '''
print("Before error")
raise ValueError("Test error")
''')

        result = self.runner.run_console_program(
            sys.executable,
            script,
            self.temp_dir,
            timeout=10.0
        )

        self.assertEqual(result.state, RunState.FAILED)
        self.assertNotEqual(result.returncode, 0)

    def test_console_program_timeout(self):
        """测试超时的控制台程序"""
        script = self._create_test_script('test_timeout.py', '''
import time
print("Starting long task...")
time.sleep(10)
print("Done")
''')

        result = self.runner.run_console_program(
            sys.executable,
            script,
            self.temp_dir,
            timeout=1.0
        )

        self.assertEqual(result.state, RunState.TIMEOUT)
        self.assertIn("超时", result.error_msg)

    def test_cannot_run_multiple_programs(self):
        """测试不能同时运行多个程序"""
        script = self._create_test_script('test_multi.py', '''
import time
time.sleep(5)
''')

        # 启动第一个程序
        result1 = self.runner.run_console_program(
            sys.executable,
            script,
            self.temp_dir,
            timeout=10.0
        )

        # 尝试启动第二个程序（应该失败）
        result2 = self.runner.run_console_program(
            sys.executable,
            script,
            self.temp_dir,
            timeout=10.0
        )

        self.assertEqual(result2.state, RunState.FAILED)
        self.assertIn("已有程序在运行", result2.error_msg)

    def test_state_callback(self):
        """测试状态回调"""
        states = []

        def on_state_changed(state, message):
            states.append((state, message))

        runner = ProgramRunner(on_state_changed=on_state_changed)
        script = self._create_test_script('test_callback.py', 'print("test")')

        runner.run_console_program(
            sys.executable,
            script,
            self.temp_dir,
            timeout=10.0
        )

        # 应该有状态变化
        self.assertGreater(len(states), 0)
        self.assertEqual(states[0][0], RunState.RUNNING)

    def test_stop_running_program(self):
        """测试停止运行中的程序"""
        script = self._create_test_script('test_stop.py', '''
import time
print("Starting...")
time.sleep(10)
print("Done")
''')

        # 在线程中启动程序
        import threading
        result_holder = []

        def run():
            result = self.runner.run_console_program(
                sys.executable,
                script,
                self.temp_dir,
                timeout=30.0
            )
            result_holder.append(result)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()

        # 等待程序启动
        time.sleep(0.5)

        # 停止程序
        stopped = self.runner.stop(timeout=5.0)
        self.assertTrue(stopped)

        # 等待线程结束
        thread.join(timeout=5.0)

    def test_get_result(self):
        """测试获取运行结果"""
        script = self._create_test_script('test_result.py', 'print("test")')

        result1 = self.runner.run_console_program(
            sys.executable,
            script,
            self.temp_dir,
            timeout=10.0
        )

        result2 = self.runner.get_result()

        self.assertEqual(result1.state, result2.state)
        self.assertEqual(result1.returncode, result2.returncode)


class TestRunResult(unittest.TestCase):
    """测试 RunResult 数据类"""

    def test_run_result_creation(self):
        """测试创建 RunResult"""
        result = RunResult(
            state=RunState.COMPLETED,
            returncode=0,
            stdout="output",
            stderr="",
            pid=1234,
            duration=1.5
        )

        self.assertEqual(result.state, RunState.COMPLETED)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "output")
        self.assertEqual(result.pid, 1234)
        self.assertEqual(result.duration, 1.5)


if __name__ == '__main__':
    unittest.main()
