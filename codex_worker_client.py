from __future__ import annotations

import os
import subprocess
import sys
import threading
import uuid
from collections import OrderedDict, deque
from pathlib import Path
from typing import Any, Callable

from codex_worker_protocol import (
    CodexWorkerProtocolError,
    decode_worker_line,
    encode_worker_message,
    make_ui_request,
    validate_chat_scoped_message,
)


class CodexWorkerClient:
    def __init__(
        self,
        on_message: Callable[[dict[str, Any]], None] | None = None,
        on_exit: Callable[..., None] | None = None,
        process_factory: Callable[[list[str]], Any] | None = None,
        start_reader_threads: bool = True,
        worker_module: str = "codex_worker_process",
        queue_limit: int = 1000,
    ) -> None:
        self.on_message = on_message
        self.on_exit = on_exit
        self.process_factory = process_factory
        self.start_reader_threads = bool(start_reader_threads)
        self.worker_module = str(worker_module or "codex_worker_process")
        self.queue_limit = max(int(queue_limit or 0), 1)
        self.process = None
        self._pending_messages: deque[dict[str, Any]] = deque()
        self._compacted_deltas: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._closed = False

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        args = [sys.executable, "-m", self.worker_module]
        if self.process_factory is not None:
            self.process = self.process_factory(args)
        else:
            self.process = subprocess.Popen(
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(Path(__file__).resolve().parent),
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                shell=False,
                creationflags=self._creationflags(),
            )
        self._closed = False
        if self.start_reader_threads:
            self._stdout_thread = threading.Thread(target=self._stdout_loop, daemon=True)
            self._stderr_thread = threading.Thread(target=self._stderr_loop, daemon=True)
            self._stdout_thread.start()
            self._stderr_thread.start()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        proc = self.process
        if proc is None:
            return
        try:
            self._send_message(make_ui_request(self._next_id(), "shutdown", {}))
        except Exception:
            pass
        if proc.poll() is not None:
            return
        try:
            if proc.wait(timeout=1) is not None:
                return
        except Exception:
            pass
        if proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=2)
            return
        except Exception:
            pass
        try:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=2)
        except Exception:
            pass

    def start_turn(self, **payload: Any) -> str:
        request_id = self._next_id()
        self._send_message(make_ui_request(request_id, "start_turn", payload))
        return request_id

    def reply_user_input(self, chat_id: str, request_id: str, answers: dict[str, Any]) -> str:
        message_id = self._next_id()
        payload = {"chat_id": chat_id, "request_id": request_id, "answers": dict(answers or {})}
        self._send_message(make_ui_request(message_id, "reply_user_input", payload))
        return message_id

    def cancel_turn(self, chat_id: str, thread_id: str, turn_id: str) -> str:
        request_id = self._next_id()
        self._send_message(
            make_ui_request(
                request_id,
                "cancel_turn",
                {"chat_id": chat_id, "thread_id": thread_id, "turn_id": turn_id},
            )
        )
        return request_id

    def drain_pending_messages(self, limit: int = 100) -> list[dict[str, Any]]:
        remaining = max(int(limit or 0), 0)
        drained: list[dict[str, Any]] = []
        if remaining == 0:
            return drained
        with self._lock:
            while remaining and self._compacted_deltas:
                _, message = self._compacted_deltas.popitem(last=False)
                drained.append(message)
                remaining -= 1
            while remaining and self._pending_messages:
                drained.append(self._pending_messages.popleft())
                remaining -= 1
        return drained

    def _enqueue_worker_message(self, message: dict[str, Any]) -> None:
        validated = validate_chat_scoped_message(message)
        if self._is_agent_message_delta(validated):
            key = self._delta_key(validated)
            with self._lock:
                self._compacted_deltas[key] = validated
                self._compacted_deltas.move_to_end(key)
        else:
            with self._lock:
                while len(self._pending_messages) >= self.queue_limit:
                    self._pending_messages.popleft()
                self._pending_messages.append(validated)
        if self.on_message is not None:
            self.on_message(validated)

    def _stdout_loop(self) -> None:
        proc = self.process
        stream = getattr(proc, "stdout", None)
        if stream is None:
            return
        try:
            for line in stream:
                try:
                    self._enqueue_worker_message(decode_worker_line(line))
                except CodexWorkerProtocolError:
                    continue
                except Exception:
                    continue
        finally:
            self._notify_exit()

    def _stderr_loop(self) -> None:
        proc = self.process
        stream = getattr(proc, "stderr", None)
        if stream is None:
            return
        try:
            for _line in stream:
                pass
        except Exception:
            pass

    def _send_message(self, message: dict[str, Any]) -> None:
        proc = self.process
        if proc is None:
            raise RuntimeError("Codex worker process is not started.")
        stdin = getattr(proc, "stdin", None)
        if stdin is None:
            raise RuntimeError("Codex worker process has no stdin.")
        stdin.write(encode_worker_message(message))
        stdin.flush()

    def _notify_exit(self) -> None:
        if self.on_exit is None:
            return
        proc = self.process
        returncode = proc.poll() if proc is not None else None
        try:
            self.on_exit(returncode)
        except TypeError:
            self.on_exit()

    def _delta_key(self, message: dict[str, Any]) -> tuple[str, str]:
        payload = message.get("payload") or {}
        event = payload.get("event") or {}
        turn_key = self._event_turn_id(event)
        if not turn_key:
            turn_key = str(payload.get("turn_idx") or "")
        return str(payload.get("chat_id") or ""), turn_key

    @staticmethod
    def _is_agent_message_delta(message: dict[str, Any]) -> bool:
        payload = message.get("payload") or {}
        event = payload.get("event") if isinstance(payload, dict) else None
        return isinstance(event, dict) and event.get("type") == "agent_message_delta"

    @staticmethod
    def _next_id() -> str:
        return uuid.uuid4().hex

    @staticmethod
    def _event_turn_id(event: Any) -> str:
        if not isinstance(event, dict):
            return ""
        turn_id = str(event.get("turn_id") or event.get("turnId") or "").strip()
        if turn_id:
            return turn_id
        data = event.get("data")
        if isinstance(data, dict):
            return str(data.get("turn_id") or "").strip()
        return ""

    @staticmethod
    def _creationflags() -> int:
        if os.name != "nt":
            return 0
        return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
