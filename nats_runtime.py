from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import socket
import subprocess
import sys
import time


NATS_SERVER_ENV = "ZGWD_NATS_SERVER_PATH"
NATS_SERVER_EXE = "nats-server.exe"


@dataclass(frozen=True)
class NatsRuntimeConfig:
    app_data_dir: Path
    token: str
    host: str = "0.0.0.0"
    port: int = 4222
    websocket_host: str = "127.0.0.1"
    websocket_port: int = 18080

    @property
    def runtime_dir(self) -> Path:
        return self.app_data_dir / "nats"

    @property
    def store_dir(self) -> Path:
        return self.runtime_dir / "jetstream"

    @property
    def config_path(self) -> Path:
        return self.runtime_dir / "nats-server.conf"

    def write(self) -> Path:
        self.store_dir.mkdir(parents=True, exist_ok=True)
        escaped_token = self.token.replace('"', r"\"")
        store_dir = self.store_dir.as_posix()
        contents = f"""port: {self.port}
host: "{self.host}"

authorization {{
  token: "{escaped_token}"
}}

jetstream {{
  store_dir: "{store_dir}"
}}

websocket {{
  host: "{self.websocket_host}"
  port: {self.websocket_port}
  no_tls: true
}}
"""
        self.config_path.write_text(contents, encoding="utf-8")
        return self.config_path


class NatsServerProcess:
    def __init__(self, config: NatsRuntimeConfig, bundled_dir: Path | None = None) -> None:
        self.config = config
        self.bundled_dir = Path(bundled_dir) if bundled_dir is not None else _default_bundled_dir()
        self._process: subprocess.Popen[bytes] | None = None

    @property
    def server_path(self) -> str:
        env_path = os.environ.get(NATS_SERVER_ENV)
        if env_path:
            candidate = Path(env_path)
            if candidate.is_file():
                return str(candidate)

        candidates = [
            self.bundled_dir / NATS_SERVER_EXE,
            Path.cwd() / "tools" / "nats-server" / NATS_SERVER_EXE,
        ]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)

        checked = ", ".join(str(candidate) for candidate in candidates)
        if env_path:
            checked = f"{env_path}, {checked}"
        raise FileNotFoundError(
            f"Could not find {NATS_SERVER_EXE}. Set {NATS_SERVER_ENV} or install it under {checked}."
        )

    def build_command(self) -> list[object]:
        return [self.server_path, "-c", self.config.write()]

    def start(self, timeout: float = 10) -> subprocess.Popen[bytes]:
        if self._process and self._process.poll() is None:
            return self._process

        if self._port_accepts_connections():
            raise RuntimeError(f"NATS port {self.config.port} is already in use")

        popen_kwargs: dict[str, object] = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            popen_kwargs["startupinfo"] = startupinfo
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        self._process = subprocess.Popen(self.build_command(), **popen_kwargs)
        try:
            self.wait_until_ready(timeout=timeout)
            self._raise_if_process_exited()
        except Exception:
            self.stop()
            raise
        return self._process

    def wait_until_ready(self, timeout: float = 10) -> None:
        deadline = time.monotonic() + timeout
        last_error: OSError | None = None
        while time.monotonic() < deadline:
            self._raise_if_process_exited()
            try:
                with socket.create_connection(("127.0.0.1", self.config.port), timeout=0.25) as connection:
                    if not _read_nats_info_ready(connection):
                        time.sleep(0.1)
                        continue
                    self._raise_if_process_exited()
                    return
            except OSError as exc:
                last_error = exc
                time.sleep(0.1)
        raise TimeoutError(f"nats-server did not accept connections on 127.0.0.1:{self.config.port}") from last_error

    def stop(self) -> None:
        process = self._process
        if process is None or process.poll() is not None:
            self._process = None
            return

        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        finally:
            self._process = None

    def _raise_if_process_exited(self) -> None:
        if self._process and self._process.poll() is not None:
            raise RuntimeError(f"NATS server exited with code {self._process.returncode}")

    def _port_accepts_connections(self) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", self.config.port), timeout=0.25):
                return True
        except OSError:
            return False


def _read_nats_info_ready(connection: socket.socket) -> bool:
    settimeout = getattr(connection, "settimeout", None)
    if settimeout:
        settimeout(0.25)
    try:
        return connection.recv(4096).startswith(b"INFO ")
    except OSError:
        return False


def _default_bundled_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS")) / "nats-server"
    return Path.cwd() / "tools" / "nats-server"
