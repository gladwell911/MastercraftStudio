"""Pure cloudflared argv and restart policy; importing never manages processes."""
from __future__ import annotations

import base64
import json
import uuid
import ctypes
import os
from pathlib import PureWindowsPath
import shlex
import hashlib
from pathlib import Path


def split_command_line(command_line: str) -> list[str]:
    if os.name != "nt":
        return shlex.split(command_line)
    count = ctypes.c_int()
    shell = ctypes.windll.shell32
    shell.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    argv = shell.CommandLineToArgvW(command_line, ctypes.byref(count))
    if not argv:
        raise ValueError("INVALID_TUNNEL_COMMAND")
    try:
        return [argv[index] for index in range(count.value)]
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(argv, ctypes.c_void_p))


def connector_argv(command_line: str, *, origin_port: int, protocol: str = "auto") -> list[str]:
    if protocol not in {"auto", "quic", "http2"}:
        raise ValueError("INVALID_TUNNEL_PROTOCOL")
    if isinstance(origin_port, bool) or not 1 <= int(origin_port) <= 65535:
        raise ValueError("INVALID_TUNNEL_ORIGIN")
    argv = split_command_line(command_line)
    if not argv or PureWindowsPath(argv[0]).name.lower() not in {"cloudflared", "cloudflared.exe"}:
        raise ValueError("INVALID_TUNNEL_EXECUTABLE")
    if "tunnel" not in argv or "run" not in argv or argv.index("run") < argv.index("tunnel"):
        raise ValueError("NAMED_TUNNEL_REQUIRED")
    cleaned = []
    skip = False
    for token in argv:
        if skip:
            skip = False
            continue
        if token in {"--url", "--protocol"}:
            skip = True
            continue
        if token.startswith(("--url=", "--protocol=")):
            continue
        cleaned.append(token)
    if skip:
        raise ValueError("MISSING_TUNNEL_OPTION_VALUE")
    run = cleaned.index("run")
    cleaned[run:run] = ["--protocol", protocol]
    cleaned.extend(["--url", f"http://127.0.0.1:{int(origin_port)}"])
    return cleaned


class TunnelRestartBudget:
    def __init__(self) -> None:
        self.failed_since: float | None = None
        self.restarts: list[float] = []

    def allow(self, now: float, *, origin_healthy: bool, network_available: bool,
              channels_healthy: bool) -> bool:
        if channels_healthy:
            self.failed_since = None
            return False
        if not origin_healthy or not network_available:
            self.failed_since = None
            return False
        if self.failed_since is None:
            self.failed_since = now
        self.restarts = [stamp for stamp in self.restarts if now - stamp < 600]
        if now - self.failed_since < 60 or len(self.restarts) >= 2:
            return False
        self.restarts.append(now)
        self.failed_since = now
        return True


class ConnectorLease:
    """An OS-held lease, scoped to pair and tunnel identity, released on crash."""
    def __init__(self, directory: Path, pair_id: str, command_line: str) -> None:
        argv = split_command_line(command_line)
        def option(name: str) -> str:
            for index, value in enumerate(argv):
                if value == name:
                    if index + 1 >= len(argv) or argv[index + 1].startswith("--"):
                        raise ValueError("MISSING_TUNNEL_OPTION_VALUE")
                    return argv[index + 1]
                if value.startswith(name + "="):
                    return value.split("=", 1)[1]
            return ""

        token = option("--token")
        token_file = option("--token-file")
        if token_file and not token:
            try:
                # Read only this explicitly supplied credential, never enumerate.
                with Path(token_file).open("rb") as source:
                    raw = source.read(65537)
                if len(raw) > 65536:
                    raise ValueError("TUNNEL_TOKEN_FILE_INVALID")
                token = raw.decode("utf-8-sig").strip()
            except (OSError, UnicodeError) as exc:
                raise ValueError("TUNNEL_TOKEN_FILE_UNREADABLE") from None
            if not token:
                raise ValueError("TUNNEL_TOKEN_FILE_INVALID")
        identity = ""
        if token:
            # Cloudflare tunnel tokens encode {a: account, t: tunnel UUID,
            # s: secret}. The UUID remains stable when the secret rotates.
            try:
                payload = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
                identity = "tunnel:" + str(uuid.UUID(str(payload["t"])))
            except (ValueError, TypeError, KeyError, AttributeError):
                # Opaque tokens cannot reveal a stable tunnel ID. Equivalent
                # files/inline values still share a lease; rotation changes it.
                identity = "opaque-token:" + hashlib.sha256(token.encode()).hexdigest()
        if not identity:
            credentials_file = option("--credentials-file")
            if credentials_file:
                try:
                    with Path(credentials_file).open("rb") as source:
                        raw = source.read(65537)
                    if len(raw) > 65536:
                        raise ValueError("TUNNEL_CREDENTIALS_FILE_INVALID")
                    identity = "tunnel:" + str(uuid.UUID(str(json.loads(raw)["TunnelID"])))
                except (OSError, ValueError, TypeError, KeyError, UnicodeError):
                    raise ValueError("TUNNEL_CREDENTIALS_FILE_INVALID") from None
            else:
                run = argv.index("run") if "run" in argv else -1
                if run >= 0 and len(argv) > run + 1 and not argv[run + 1].startswith("-"):
                    name = argv[run + 1]
                    try:
                        identity = "tunnel:" + str(uuid.UUID(name))
                    except ValueError:
                        identity = "name:" + name
        if not identity:
            raise ValueError("TUNNEL_IDENTITY_REQUIRED")
        digest = hashlib.sha256((pair_id + "\x00" + identity).encode()).hexdigest()
        self.path = Path(directory) / f"connector-{digest}.lock"
        self.handle = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            if self.path.stat().st_size == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.handle = handle
        except BaseException:
            handle.close()
            raise

    def close(self) -> None:
        handle, self.handle = self.handle, None
        if handle is not None:
            handle.close()
