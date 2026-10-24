"""Bounded logs for MC-owned subprocess pipes and optional diagnostics."""
from __future__ import annotations
import os
from pathlib import Path
import re
import threading

LOG_LIMIT = 5 * 1024 * 1024
LOG_BACKUPS = 3


class RotatingLog:
    def __init__(self, path: Path, *, max_bytes: int = LOG_LIMIT, backups: int = LOG_BACKUPS,
                 track_channels: bool = False):
        self.path = Path(path)
        self.max_bytes = max(1, int(max_bytes))
        self.backups = max(1, int(backups))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        # Older installations may already have oversized owned logs. Retain
        # their bounded tails before entering the continuous rotation policy.
        for candidate in [self.path] + [self.path.with_name(self.path.name + f".{i}") for i in range(1, self.backups + 1)]:
            if candidate.exists() and candidate.stat().st_size > self.max_bytes:
                with candidate.open("rb") as source:
                    source.seek(-self.max_bytes, os.SEEK_END)
                    tail = source.read(self.max_bytes)
                candidate.write_bytes(tail)
        self._handle = self.path.open("ab", buffering=0)
        self._write_failures = 0
        self._thread = None
        self._pipe = None
        self._track_channels = track_channels
        self._channels = {}
        self._partial = b""
        with self._lock:
            if self._handle.tell() > self.max_bytes:
                self._rotate()

    def _rotate(self):
        self._handle.close()
        try:
            oldest = self.path.with_name(self.path.name + f".{self.backups}")
            oldest.unlink(missing_ok=True)
            for index in range(self.backups - 1, 0, -1):
                source = self.path.with_name(self.path.name + f".{index}")
                if source.exists():
                    source.replace(self.path.with_name(self.path.name + f".{index + 1}"))
            if self.path.exists():
                self.path.replace(self.path.with_name(self.path.name + ".1"))
        finally:
            # Rename failure must not permanently poison the current handle.
            # If reopening itself fails, the next write retries it as well.
            self._handle = self.path.open("ab", buffering=0)

    @property
    def failure_note(self):
        with self._lock:
            # Fixed text and saturating count: never expose exception messages,
            # paths, child output or credentials through this indication.
            return f"log_write_failures={self._write_failures}" if self._write_failures else ""

    def _observe(self, data: bytes):
        if not self._track_channels:
            return
        lines = (self._partial + data).split(b"\n")
        self._partial = lines.pop()[-8192:]
        for raw in lines:
            line = raw.decode("utf-8", errors="replace")
            index = re.search(r"connIndex=(\d+)", line)
            if index is not None:
                if "Registered tunnel connection" in line:
                    self._channels[index.group(1)] = True
                elif "Unregistered tunnel connection" in line or "Connection terminated" in line:
                    self._channels[index.group(1)] = False

    @property
    def channel_count(self):
        with self._lock:
            return sum(self._channels.values()) if self._channels else None

    def write(self, data: bytes):
        with self._lock:
            self._observe(data)
            try:
                if self._handle.closed:
                    self._handle = self.path.open("ab", buffering=0)
                view = memoryview(data)
                while view:
                    remaining = self.max_bytes - self._handle.tell()
                    if remaining <= 0:
                        self._rotate()
                        remaining = self.max_bytes
                    chunk = view[:remaining]
                    written = self._handle.write(chunk)
                    if not isinstance(written, int) or written <= 0 or written > len(chunk):
                        raise OSError("Owned log write made no valid progress")
                    view = view[written:]
            except (OSError, ValueError):
                self._write_failures = min(9999, self._write_failures + 1)
                raise

    def attach(self, pipe):
        self._pipe = pipe
        if pipe is None:
            return
        def drain():
            try:
                while True:
                    data = pipe.read1(65536) if hasattr(pipe, "read1") else pipe.read(65536)
                    if not data:
                        break
                    try:
                        self.write(data)
                    except (OSError, ValueError):
                        # Keep draining if disk output fails: a full pipe must
                        # never deadlock the owned child or its shutdown.
                        continue
            except (OSError, ValueError):
                pass
            finally:
                pipe.close()
        self._thread = threading.Thread(target=drain, name="mc-owned-log-reader", daemon=True)
        self._thread.start()

    def wait(self, timeout=5):
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)

    def flush(self):
        with self._lock:
            self._handle.flush()

    def close(self):
        self.wait()
        thread = self._thread
        if thread is not None and thread.is_alive() and os.name == "nt":
            # A descendant can inherit stdout after the owned child exits.
            # Cancel this reader's blocking Win32 pipe read without killing any
            # unrelated process or leaving a reader/handle behind.
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenThread.restype = wintypes.HANDLE
            kernel.CancelSynchronousIo.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.OpenThread(0x0001, False, thread.native_id)
            if handle:
                try:
                    kernel.CancelSynchronousIo(handle)
                finally:
                    kernel.CloseHandle(handle)
            self.wait()
        with self._lock:
            self._handle.close()
        if thread is not None and thread.is_alive():
            raise RuntimeError("Owned log reader did not stop")


def append_diagnostic(path: Path, text: str):
    # Callers use this only for explicit diagnostics, not high-volume children.
    log = RotatingLog(path)
    try:
        log.write(text.encode("utf-8"))
    finally:
        log.close()
