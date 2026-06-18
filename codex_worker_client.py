from __future__ import annotations

import os
import subprocess
import sys
import threading
import uuid
from collections import deque
from copy import deepcopy
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
        self._queue: deque[dict[str, Any]] = deque()
        self._lock = threading.Lock()
        self._lifecycle_lock = threading.RLock()
        self._send_lock = threading.Lock()
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._closed = False
        self._pending_notification = False

    def start(self) -> None:
        with self._lifecycle_lock:
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
        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            proc = self.process
            if proc is None:
                return
            try:
                self._send_message(make_ui_request(self._next_id(), "shutdown", {}), allow_closed=True)
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
            while remaining and self._queue:
                entry = self._queue.popleft()
                if entry["kind"] == "delta":
                    drained.append(entry["message"])
                else:
                    drained.append(entry["message"])
                remaining -= 1
            if drained:
                self._pending_notification = False
        return drained

    def _enqueue_worker_message(self, message: dict[str, Any]) -> None:
        validated = validate_chat_scoped_message(message)
        notify_pending = False
        with self._lock:
            if self._is_agent_message_delta(validated):
                key = self._delta_key(validated)
                last_entry = self._queue[-1] if self._queue else None
                if last_entry and last_entry["kind"] == "delta" and last_entry["key"] == key:
                    last_entry["message"] = self._merge_delta_message(last_entry["message"], validated)
                else:
                    self._queue.append({"kind": "delta", "key": key, "message": deepcopy(validated)})
                    self._enforce_queue_limit_locked()
            else:
                self._queue.append({"kind": "message", "message": validated})
                self._enforce_queue_limit_locked()
            if not self._pending_notification:
                self._pending_notification = True
                notify_pending = True
        if notify_pending and self.on_message is not None:
            self.on_message({"type": "messages_pending"})

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
            self._notify_exit(proc)

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

    def _send_message(self, message: dict[str, Any], allow_closed: bool = False) -> None:
        with self._lifecycle_lock:
            if self._closed and not allow_closed:
                raise RuntimeError("Codex worker client is closed.")
            proc = self.process
            if proc is None:
                raise RuntimeError("Codex worker process is not started.")
            stdin = getattr(proc, "stdin", None)
            if stdin is None:
                raise RuntimeError("Codex worker process has no stdin.")
            line = encode_worker_message(message)
            with self._send_lock:
                stdin.write(line)
                stdin.flush()

    def _notify_exit(self, proc: Any | None = None) -> None:
        if self.on_exit is None:
            return
        if proc is None:
            proc = self.process
        returncode = proc.poll() if proc is not None else None
        self.on_exit(returncode)

    def _delta_key(self, message: dict[str, Any]) -> tuple[str, ...]:
        payload = message.get("payload") or {}
        event = payload.get("event") or {}
        turn_key = self._event_turn_id(event)
        if not turn_key:
            turn_idx = payload.get("turn_idx")
            turn_key = "" if turn_idx is None else str(turn_idx)
        item_id = self._event_item_id(event)
        if item_id:
            return str(payload.get("chat_id") or ""), turn_key, item_id
        return str(payload.get("chat_id") or ""), turn_key

    def _enforce_queue_limit_locked(self) -> None:
        while len(self._queue) > self.queue_limit:
            self._queue.popleft()

    @staticmethod
    def _merge_delta_message(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
        merged = deepcopy(incoming)
        existing_event = ((existing.get("payload") or {}).get("event") or {})
        merged_event = ((merged.get("payload") or {}).get("event") or {})
        incoming_event = ((incoming.get("payload") or {}).get("event") or {})
        if not isinstance(existing_event, dict) or not isinstance(merged_event, dict) or not isinstance(incoming_event, dict):
            return merged
        existing_text = str(existing_event.get("text") or "")
        incoming_text = str(incoming_event.get("text") or "")
        merged_event["text"] = existing_text + incoming_text
        if "raw_text" in existing_event or "raw_text" in incoming_event:
            existing_raw = str(existing_event.get("raw_text") or "")
            incoming_raw = str(incoming_event.get("raw_text") or "")
            merged_event["raw_text"] = existing_raw + incoming_raw
        return merged

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
    def _event_item_id(event: Any) -> str:
        if not isinstance(event, dict):
            return ""
        item_id = str(event.get("item_id") or event.get("itemId") or "").strip()
        if item_id:
            return item_id
        data = event.get("data")
        if isinstance(data, dict):
            return str(data.get("item_id") or data.get("itemId") or "").strip()
        return ""

    @staticmethod
    def _creationflags() -> int:
        if os.name != "nt":
            return 0
        return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
