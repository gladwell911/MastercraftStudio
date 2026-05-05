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


def test_read_nats_info_ready_requires_info_banner() -> None:
    assert _read_nats_info_ready(_FakeConnection(b"INFO {\"server_id\":\"test\"}\r\n")) is True
    assert _read_nats_info_ready(_FakeConnection(b"HELLO\r\n")) is False
