import concurrent.futures
import os
import socket
import subprocess
import sys
import threading
import uuid
import winreg
from pathlib import Path

import pytest

from shared_terminal import PROTOCOL_VERSION, TerminalClient, TerminalError, TerminalServer
from shared_terminal_command import ensure_terminal, register_commands, terminal_directory


def test_local_service_authenticates_both_ends_before_dispatch(tmp_path):
    received = []

    def dispatch(operation, payload):
        received.append((operation, payload))
        return 200, {"answer": "共享中文"}

    with TerminalServer(tmp_path / "endpoint.json", dispatch) as server:
        client = TerminalClient.from_endpoint(server.endpoint)
        assert client.request("send", {"chat_id": "existing"}) == (200, {"answer": "共享中文"})

        with pytest.raises(TerminalError, match="协议"):
            client.request("send", {"chat_id": "wrong-version"}, version=PROTOCOL_VERSION + 1)

        with socket.create_connection(("127.0.0.1", client.port), timeout=2) as intruder:
            intruder.sendall(bytes(32))
            with intruder.makefile("rb") as stream:
                assert len(stream.read(64)) == 64
            intruder.sendall(bytes(32))
            assert intruder.recv(1) == b""

    assert received == [("send", {"chat_id": "existing"})]


def test_concurrent_cold_start_and_reuse_share_the_same_endpoint(tmp_path):
    endpoint = tmp_path / "endpoint.json"
    count = tmp_path / "launches.txt"
    child = tmp_path / "launch.py"
    child.write_text(
        "import sys, time\nfrom pathlib import Path\nfrom shared_terminal import TerminalServer\n"
        "with open(sys.argv[2], 'a') as f: f.write('launched\\n')\n"
        "time.sleep(.35)\n"
        "with TerminalServer(Path(sys.argv[1]), lambda op, p: (200, {'ready': True})):\n"
        " while not Path(sys.argv[3]).exists(): time.sleep(.05)\n",
        encoding="utf-8",
    )
    finished = tmp_path / "finish"
    argv = [sys.executable, str(child), str(endpoint), str(count), str(finished)]
    env = {**os.environ, "PYTHONPATH": str(Path.cwd())}
    processes = []

    def launch():
        process = subprocess.Popen(argv, env=env)
        processes.append(process)
        return process

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(ensure_terminal, endpoint, launch, timeout=5) for _ in range(3)]
            ports = {future.result().port for future in futures}
        assert len(ports) == 1
        assert ensure_terminal(endpoint, launch, timeout=5).port in ports
        assert count.read_text(encoding="utf-8") == "launched\n"
    finally:
        finished.touch()
        for process in processes:
            process.wait(timeout=5)


def test_registered_powershell_command_follows_a_moved_installation_and_preserves_conflicts(tmp_path, monkeypatch):
    key_name = f"Software\\MCSharedTerminalTest-{uuid.uuid4().hex}"
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    system_ps = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    conflict_dir = tmp_path / "conflict"
    conflict_dir.mkdir()
    existing = conflict_dir / "mc-codex.cmd"
    existing.write_bytes(b"@echo user command\r\n")
    original_path = os.environ["PATH"]
    monkeypatch.setenv("PATH", str(conflict_dir) + ";" + original_path)
    source = """
param([string]$File, [string]$Result)
$escaped = $Result.Replace('\\', '\\\\')
$code = 'public class FakeWorker { public static int Main(string[] a) { System.IO.File.WriteAllText("' +
        $escaped + '", string.Join("|", a)); return 0; } }'
Add-Type -TypeDefinition $code -Language CSharp -OutputAssembly $File -OutputType ConsoleApplication
"""
    build = tmp_path / "build-test-worker.ps1"
    build.write_text(source, encoding="utf-8")
    try:
        for install in ("first install", "新安装 %100!"):
            directory = tmp_path / install
            directory.mkdir()
            worker = directory / "mc_worker.exe"
            invocation = directory / "invocation.txt"
            subprocess.run([str(system_ps), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                            str(build), str(worker), str(invocation)], check=True, capture_output=True)
            conflicts = register_commands(worker, user_environment=key_name)
            assert len(conflicts) == 1 and "mc-codex" in conflicts[0]
            assert existing.read_bytes() == b"@echo user command\r\n"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_name) as key:
                user_path, _ = winreg.QueryValueEx(key, "Path")
            assert user_path == str(terminal_directory() / "bin")
            child_env = {**os.environ, "PATH": os.environ["PATH"] + ";" + user_path}
            available = subprocess.run([str(system_ps), "-NoProfile", "-Command", "mc-kimi"],
                                       env=child_env, check=True, capture_output=True)
            assert invocation.read_text(encoding="utf-8") == "--shared-terminal|kimi", available.stdout
    finally:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_name)


def test_initializing_service_is_verified_without_launching_another_process(tmp_path):
    endpoint = tmp_path / "endpoint.json"
    ready = threading.Event()
    with TerminalServer(endpoint, lambda *_: (200, {"ready": True}) if ready.is_set() else (503, {})):
        def unexpected_launch():
            pytest.fail("a verified MC instance already owns the endpoint")

        with pytest.raises(TerminalError, match="超时"):
            ensure_terminal(endpoint, unexpected_launch, timeout=.2)
        ready.set()
        assert ensure_terminal(endpoint, unexpected_launch, timeout=1).request("ping", {}) == (200, {"ready": True})


def test_cold_start_failure_has_recovery_guidance(tmp_path):
    def missing_executable():
        raise FileNotFoundError("missing mc.exe")

    with pytest.raises(TerminalError, match="手动运行一次 mc.exe"):
        ensure_terminal(tmp_path / "endpoint.json", missing_executable, timeout=1)
