from __future__ import annotations

from pathlib import Path

import pytest

from nats_runtime import NatsRuntimeConfig, NatsServerProcess, _read_nats_info_ready


class _FakeConnection:
    def __init__(self, data: bytes = b"INFO {}\r\n") -> None:
        self.data = data

    def __enter__(self) -> "_FakeConnection":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def recv(self, size: int) -> bytes:
        return self.data


class _ExitingProcess:
    returncode = 2

    def __init__(self) -> None:
        self._poll_results = [None, 2]

    def poll(self) -> int | None:
        return self._poll_results.pop(0)

    def terminate(self) -> None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode


def test_write_creates_nats_server_config(tmp_path: Path) -> None:
    config = NatsRuntimeConfig(
        app_data_dir=tmp_path,
        token="secret",
        host="0.0.0.0",
        port=4222,
        websocket_host="127.0.0.1",
        websocket_port=18080,
    )

    config_path = config.write()

    expected_store = (tmp_path / "nats" / "jetstream").as_posix()
    assert config_path == tmp_path / "nats" / "nats-server.conf"
    assert config_path.exists()
    assert config_path.read_text(encoding="utf-8")
    contents = config_path.read_text(encoding="utf-8")
    assert "port: 4222" in contents
    assert 'host: "0.0.0.0"' in contents
    assert "jetstream" in contents
    assert f'store_dir: "{expected_store}"' in contents
    assert "timeout: 30" in contents
    assert 'token: "secret"' in contents
    assert "websocket" in contents
    assert "port: 18080" in contents
    assert "no_tls: true" in contents


def test_build_command_uses_env_server_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_server = tmp_path / "nats-server.exe"
    fake_server.write_text("", encoding="utf-8")
    monkeypatch.setenv("ZGWD_NATS_SERVER_PATH", str(fake_server))

    process = NatsServerProcess(NatsRuntimeConfig(app_data_dir=tmp_path, token="secret"))

    command = process.build_command()

    assert command[0] == str(fake_server)
    assert "-c" in command


def test_missing_server_binary_raises_useful_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("ZGWD_NATS_SERVER_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    missing_bundled_dir = tmp_path / "missing"
    process = NatsServerProcess(
        NatsRuntimeConfig(app_data_dir=tmp_path, token="secret"),
        bundled_dir=missing_bundled_dir,
    )

    with pytest.raises(FileNotFoundError, match="nats-server\\.exe"):
        _ = process.server_path


def test_wait_until_ready_raises_if_process_exits_after_socket_ready(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    process = NatsServerProcess(NatsRuntimeConfig(app_data_dir=tmp_path, token="secret"))
    process._process = _ExitingProcess()  # type: ignore[assignment]
    monkeypatch.setattr("nats_runtime.socket.create_connection", lambda *args, **kwargs: _FakeConnection())

    with pytest.raises(RuntimeError, match="NATS server exited"):
        process.wait_until_ready(timeout=1)


def test_start_raises_when_nats_port_is_already_in_use(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    process = NatsServerProcess(NatsRuntimeConfig(app_data_dir=tmp_path, token="secret"))
    monkeypatch.setattr(process, "_port_accepts_connections", lambda: True)
    monkeypatch.setattr("nats_runtime.subprocess.Popen", lambda *args, **kwargs: pytest.fail("should not spawn"))

    with pytest.raises(RuntimeError, match="NATS port .* already in use"):
        process.start(timeout=1)


def test_start_raises_before_spawn_when_nats_port_cannot_bind(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    process = NatsServerProcess(NatsRuntimeConfig(app_data_dir=tmp_path, token="secret", port=4222))
    monkeypatch.setattr(process, "_port_accepts_connections", lambda: False)
    monkeypatch.setattr(process, "_port_can_bind", lambda: False)
    monkeypatch.setattr("nats_runtime.subprocess.Popen", lambda *args, **kwargs: pytest.fail("should not spawn"))

    with pytest.raises(RuntimeError, match="NATS port 4222 is unavailable for binding"):
        process.start(timeout=1)


def test_read_nats_info_ready_requires_info_banner() -> None:
    assert _read_nats_info_ready(_FakeConnection(b"INFO {\"server_id\":\"test\"}\r\n")) is True
    assert _read_nats_info_ready(_FakeConnection(b"HELLO\r\n")) is False


def test_process_exit_reports_log_tail_without_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    token = "do-not-leak-this-token"
    process = NatsServerProcess(NatsRuntimeConfig(app_data_dir=tmp_path, token=token))
    process.config.runtime_dir.mkdir(parents=True)
    process.log_path.write_text(f"startup failed token={token}\nuseful diagnostic\n", encoding="utf-8")
    exited = _ExitingProcess()
    exited._poll_results = [2]
    process._process = exited  # type: ignore[assignment]

    with pytest.raises(RuntimeError) as caught:
        process._raise_if_process_exited()

    message = str(caught.value)
    assert "exited with code 2" in message
    assert str(process.log_path) in message
    assert "useful diagnostic" in message
    assert token not in message
    assert "<redacted>" in message


def test_owned_child_continuously_rotates_over_ten_limits_and_drains(tmp_path, monkeypatch):
    import sys
    from owned_logs import LOG_LIMIT
    process = NatsServerProcess(NatsRuntimeConfig(app_data_dir=tmp_path, token="secret"))
    code = ("import sys; out=sys.stdout.buffer; "
            "out.write(b'x' * (55 * 1024 * 1024)); out.write(b'\\nLATEST OUTPUT\\n'); out.flush()")
    monkeypatch.setattr(process, "build_command", lambda: [sys.executable, "-c", code])
    monkeypatch.setattr(process, "_port_accepts_connections", lambda: False)
    monkeypatch.setattr(process, "_port_can_bind", lambda: True)
    monkeypatch.setattr(process, "wait_until_ready", lambda **_: None)
    try:
        child = process.start()
        reader = process._log_handle
        child.wait(timeout=30)
        reader.wait()
        assert "LATEST OUTPUT" in process._log_tail()
        logs = list(process.log_path.parent.glob("nats-server.log*"))
        assert len(logs) == 4
        assert all(path.stat().st_size <= LOG_LIMIT for path in logs)
        assert sum(path.stat().st_size for path in logs) <= 4 * LOG_LIMIT
    finally:
        process.stop()
    assert not reader._thread.is_alive()
    assert reader._pipe.closed
    assert reader._handle.closed
    assert process._log_handle is None


def test_owned_child_failed_spawn_closes_logger(tmp_path, monkeypatch):
    from owned_logs import RotatingLog
    created = []
    def logger(path):
        result = RotatingLog(path)
        created.append(result)
        return result
    process = NatsServerProcess(NatsRuntimeConfig(app_data_dir=tmp_path, token="secret"))
    monkeypatch.setattr("nats_runtime.RotatingLog", logger)
    monkeypatch.setattr(process, "_port_accepts_connections", lambda: False)
    monkeypatch.setattr(process, "_port_can_bind", lambda: True)
    monkeypatch.setattr(process, "build_command", lambda: [str(tmp_path / "missing.exe")])
    with pytest.raises(OSError):
        process.start()
    assert created[0]._handle.closed and created[0]._thread is None


@pytest.mark.parametrize("failure", ["rename", "reopen"])
def test_transient_rotation_failure_restores_writable_handle_and_recovers(tmp_path, monkeypatch, failure):
    from owned_logs import RotatingLog
    path = tmp_path / "recover.log"
    log = RotatingLog(path, max_bytes=8)
    log.write(b"12345678")
    failed = [False]
    original_replace, original_open = Path.replace, Path.open
    def replace(source, target):
        if failure == "rename" and source == path and not failed[0]:
            failed[0] = True
            raise OSError("secret-must-not-be-reported")
        return original_replace(source, target)
    def opened(source, *args, **kwargs):
        if failure == "reopen" and source == path and args and args[0] == "ab" and not failed[0]:
            failed[0] = True
            raise OSError("secret-must-not-be-reported")
        return original_open(source, *args, **kwargs)
    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr(Path, "open", opened)
    try:
        with pytest.raises(OSError):
            log.write(b"lost")
        if failure == "rename":
            assert not log._handle.closed
        log.write(b"LATEST")
        assert not log._handle.closed
        assert path.read_bytes() == b"LATEST"
        assert log.failure_note == "log_write_failures=1"
    finally:
        log.close()
    assert log._handle.closed


@pytest.mark.parametrize("failure_mode", ["write", "rotation"])
def test_real_child_drains_after_rotation_write_error_and_reports_safe_failure(tmp_path, monkeypatch, failure_mode):
    import sys
    from owned_logs import RotatingLog
    process = NatsServerProcess(NatsRuntimeConfig(app_data_dir=tmp_path, token="private-token"))
    created = []
    def logger(path):
        log = RotatingLog(path, max_bytes=65536, track_channels=True)
        original = log._rotate
        failed = [False]
        def rotate():
            if not failed[0]:
                failed[0] = True
                raise OSError("private-token secret credential path")
            return original()
        if failure_mode == "rotation":
            log._rotate = rotate
        else:
            original_handle = log._handle
            class FailOnceWriter:
                def __getattr__(self, name):
                    return getattr(original_handle, name)
                def write(self, data):
                    if not failed[0]:
                        failed[0] = True
                        raise OSError("private-token secret credential path")
                    return original_handle.write(data)
            log._handle = FailOnceWriter()
        created.append(log)
        return log
    monkeypatch.setattr("nats_runtime.RotatingLog", logger)
    monkeypatch.setattr(process, "_port_accepts_connections", lambda: False)
    monkeypatch.setattr(process, "_port_can_bind", lambda: True)
    monkeypatch.setattr(process, "wait_until_ready", lambda **_: None)
    code = ("import sys,time; o=sys.stdout.buffer; o.write(b'x'*(2*1024*1024)); o.flush(); "
            "time.sleep(.1); o.write(b'\\nRegistered tunnel connection connIndex=0\\nRECOVERED LATEST\\n'); o.flush()")
    monkeypatch.setattr(process, "build_command", lambda: [sys.executable, "-c", code])
    try:
        child = process.start()
        child.wait(timeout=15)
        log = created[0]
        log.wait()
        tail = process._log_tail()
        assert "RECOVERED LATEST" in tail and "log_write_failures=1" in tail
        assert "private-token" not in tail and "secret credential path" not in tail
        assert log.channel_count == 1
    finally:
        process.stop()
    assert not log._thread.is_alive() and log._pipe.closed and log._handle.closed
