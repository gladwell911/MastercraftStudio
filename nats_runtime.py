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
  timeout: 30
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
        self._log_handle = None

    @property
    def log_path(self) -> Path:
        return self.config.runtime_dir / "nats-server.log"

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
        if not self._port_can_bind():
            raise RuntimeError(f"NATS port {self.config.port} is unavailable for binding")

        self.config.runtime_dir.mkdir(parents=True, exist_ok=True)
        self._close_log_handle()
        self._log_handle = self.log_path.open("ab", buffering=0)
        popen_kwargs: dict[str, object] = {
            "stdin": subprocess.DEVNULL,
            "stdout": self._log_handle,
            "stderr": self._log_handle,
        }
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            popen_kwargs["startupinfo"] = startupinfo
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        try:
            self._process = subprocess.Popen(self.build_command(), **popen_kwargs)
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
        raise TimeoutError(
            f"nats-server did not accept connections on 127.0.0.1:{self.config.port}; "
            f"log: {self.log_path}; tail: {self._log_tail()}"
        ) from last_error

    def stop(self) -> None:
        process = self._process
        try:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        finally:
            self._process = None
            self._close_log_handle()

    def _raise_if_process_exited(self) -> None:
        if self._process and self._process.poll() is not None:
            raise RuntimeError(
                f"NATS server exited with code {self._process.returncode}; "
                f"log: {self.log_path}; tail: {self._log_tail()}"
            )

    def _log_tail(self, max_bytes: int = 4096) -> str:
        handle = self._log_handle
        if handle is not None:
            try:
                handle.flush()
            except Exception:
                pass
        try:
            with self.log_path.open("rb") as stream:
                stream.seek(0, os.SEEK_END)
                size = stream.tell()
                stream.seek(max(0, size - max_bytes), os.SEEK_SET)
                text = stream.read().decode("utf-8", errors="replace")
        except OSError:
            return "<unavailable>"
        token = str(self.config.token or "")
        if token:
            text = text.replace(token, "<redacted>")
        return " | ".join(text.splitlines()[-20:]).strip() or "<empty>"

    def _close_log_handle(self) -> None:
        handle = self._log_handle
        self._log_handle = None
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass

    def _port_accepts_connections(self) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", self.config.port), timeout=0.25):
                return True
        except OSError:
            return False

    def _port_can_bind(self) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                if os.name == "nt":
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                else:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((self.config.host, self.config.port))
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
