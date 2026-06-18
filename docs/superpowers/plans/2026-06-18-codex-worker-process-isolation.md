# Codex Worker Process Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move Codex app-server lifecycle, stdio reading, event parsing, turn orchestration, and pending-input replies out of the wx UI process into a dedicated worker process.

**Architecture:** The UI process will own wx controls, visible chat state, persistence, and the reader quiet window. A new worker process will own `CodexAppServerClient` and communicate with the UI over UTF-8 JSON Lines on stdio. The UI will receive worker events through the existing `_dispatch_codex_event_to_ui` gateway so current chat-scoped rendering, quiet-window deferral, and execution-process tail behavior remain authoritative.

**Tech Stack:** Python 3, wxPython, subprocess stdio JSON Lines, threading, pytest, existing `codex_client.CodexAppServerClient`, existing UI automation fixtures.

---

## File Structure

- Create `codex_worker_protocol.py`: JSON Lines message types, validation, serialization, deserialization, and `CodexEvent` payload conversion helpers.
- Create `codex_worker_client.py`: UI-side process manager, stdout/stderr reader threads, bounded event queue, command send helpers, worker crash detection.
- Create `codex_worker_process.py`: worker entry point, command loop, per-chat Codex client registry, turn orchestration, pending input response routing.
- Modify `main.py`: replace direct UI-process `CodexAppServerClient` construction with `CodexWorkerClient`, route pending input replies through IPC, preserve `_dispatch_codex_event_to_ui`.
- Modify `tests/conftest.py` only if a reusable fake worker path fixture is needed.
- Create `tests/test_codex_worker_protocol.py`: protocol and validation tests.
- Create `tests/test_codex_worker_client.py`: fake subprocess/client queue tests.
- Create `tests/test_codex_worker_process.py`: worker command-loop tests with a fake Codex client.
- Modify `tests/test_main_unit.py`: UI integration, pending input, crash isolation, and no direct Codex client construction tests.
- Modify `tests/test_codex_integration.py`: update Codex routing tests to use fake worker client.
- Modify `tests/test_codex_ui_responsiveness_automation.py`: add worker-event responsiveness coverage.
- Modify `tests/test_history_ui_automation.py`: keep clear-context and cross-chat regressions green with worker events.

---

### Task 1: Protocol Module

**Files:**
- Create: `codex_worker_protocol.py`
- Test: `tests/test_codex_worker_protocol.py`

- [ ] **Step 1: Write failing protocol tests**

Create `tests/test_codex_worker_protocol.py`:

```python
import json

import pytest

from codex_client import CodexEvent
from codex_worker_protocol import (
    CodexWorkerProtocolError,
    decode_worker_line,
    encode_worker_message,
    event_from_payload,
    event_to_payload,
    make_ui_request,
    make_worker_event,
    validate_chat_scoped_message,
)


def test_encode_decode_roundtrip_preserves_ascii_and_chinese_text():
    message = make_ui_request(
        "req-1",
        "start_turn",
        {
            "chat_id": "chat-c",
            "turn_idx": 2,
            "question": "继续分析这个问题",
            "model": "codex/main",
        },
    )

    line = encode_worker_message(message)
    assert line.endswith("\n")
    assert "\\u7ee7" not in line
    decoded = decode_worker_line(line)

    assert decoded == message


def test_decode_rejects_invalid_json_line():
    with pytest.raises(CodexWorkerProtocolError):
        decode_worker_line("{not json}\n")


def test_chat_scoped_message_requires_chat_id():
    message = make_worker_event(
        "event",
        {
            "turn_idx": 0,
            "event": {"type": "turn_completed", "text": "done"},
        },
    )

    with pytest.raises(CodexWorkerProtocolError):
        validate_chat_scoped_message(message)


def test_codex_event_payload_roundtrip():
    event = CodexEvent(
        type="item_completed",
        text="执行完成",
        raw_text="raw text",
        subtype="command",
        phase="final_answer",
        status="completed",
        request_id=42,
        method="item/tool/requestUserInput",
        params={"x": 1},
        data={"thread_id": "thread-1", "turn_id": "turn-1"},
    )

    payload = event_to_payload(event)
    restored = event_from_payload(payload)

    assert restored.type == "item_completed"
    assert restored.text == "执行完成"
    assert restored.raw_text == "raw text"
    assert restored.subtype == "command"
    assert restored.phase == "final_answer"
    assert restored.status == "completed"
    assert restored.request_id == 42
    assert restored.method == "item/tool/requestUserInput"
    assert restored.params == {"x": 1}
    assert restored.data == {"thread_id": "thread-1", "turn_id": "turn-1"}
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
pytest tests/test_codex_worker_protocol.py -q
```

Expected: import failure for `codex_worker_protocol`.

- [ ] **Step 3: Implement the protocol module**

Create `codex_worker_protocol.py`:

```python
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import Any

from codex_client import CodexEvent


class CodexWorkerProtocolError(ValueError):
    pass


CHAT_SCOPED_TYPES = {
    "start_turn",
    "reply_user_input",
    "cancel_turn",
    "event",
    "thread_state",
    "request_user_input",
    "turn_finished",
    "error",
}


def make_ui_request(request_id: str, message_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"id": str(request_id), "type": str(message_type), "payload": dict(payload or {})}


def make_worker_event(message_type: str, payload: dict[str, Any] | None = None, request_id: str | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"type": str(message_type), "payload": dict(payload or {})}
    if request_id is not None:
        message["id"] = str(request_id)
    return message


def encode_worker_message(message: dict[str, Any]) -> str:
    if not isinstance(message, dict):
        raise CodexWorkerProtocolError("worker message must be a dict")
    if not str(message.get("type") or "").strip():
        raise CodexWorkerProtocolError("worker message missing type")
    payload = message.get("payload")
    if payload is not None and not isinstance(payload, dict):
        raise CodexWorkerProtocolError("worker message payload must be a dict")
    return json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"


def decode_worker_line(line: str) -> dict[str, Any]:
    try:
        message = json.loads(str(line or "").strip())
    except Exception as exc:
        raise CodexWorkerProtocolError("invalid worker JSON line") from exc
    if not isinstance(message, dict):
        raise CodexWorkerProtocolError("worker JSON line must decode to dict")
    if not str(message.get("type") or "").strip():
        raise CodexWorkerProtocolError("worker message missing type")
    payload = message.get("payload")
    if payload is not None and not isinstance(payload, dict):
        raise CodexWorkerProtocolError("worker message payload must be dict")
    return message


def validate_chat_scoped_message(message: dict[str, Any]) -> dict[str, Any]:
    message_type = str((message or {}).get("type") or "").strip()
    payload = (message or {}).get("payload")
    if message_type in CHAT_SCOPED_TYPES:
        if not isinstance(payload, dict) or not str(payload.get("chat_id") or "").strip():
            raise CodexWorkerProtocolError(f"{message_type} message requires payload.chat_id")
    return message


def event_to_payload(event: CodexEvent) -> dict[str, Any]:
    if is_dataclass(event):
        return asdict(event)
    return dict(getattr(event, "__dict__", {}) or {})


def event_from_payload(payload: dict[str, Any]) -> CodexEvent:
    if not isinstance(payload, dict):
        raise CodexWorkerProtocolError("Codex event payload must be dict")
    allowed = CodexEvent.__dataclass_fields__.keys()
    return CodexEvent(**{key: payload.get(key) for key in allowed})
```

- [ ] **Step 4: Run protocol tests**

Run:

```powershell
pytest tests/test_codex_worker_protocol.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add codex_worker_protocol.py tests/test_codex_worker_protocol.py
git commit -m "feat: add codex worker protocol"
```

---

### Task 2: UI-Side Worker Client Skeleton

**Files:**
- Create: `codex_worker_client.py`
- Test: `tests/test_codex_worker_client.py`

- [ ] **Step 1: Write failing client tests**

Create `tests/test_codex_worker_client.py`:

```python
import io
import queue
import threading

from codex_worker_client import CodexWorkerClient


class FakeProcess:
    def __init__(self):
        self.stdin = io.StringIO()
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        self.returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


def test_worker_client_send_start_turn_writes_json_line():
    proc = FakeProcess()
    client = CodexWorkerClient(process_factory=lambda args: proc, start_reader_threads=False)
    client.start()

    client.start_turn(
        chat_id="chat-c",
        turn_idx=0,
        question="问题",
        model="codex/main",
        cwd="c:/code/sj",
        thread_id="",
        turn_id="",
        input_items=[{"type": "text", "text": "问题"}],
        attachments=[],
        service_tier="fast",
    )

    written = proc.stdin.getvalue()
    assert '"type":"start_turn"' in written
    assert '"chat_id":"chat-c"' in written
    assert '"service_tier":"fast"' in written


def test_worker_client_reply_user_input_writes_ipc_message():
    proc = FakeProcess()
    client = CodexWorkerClient(process_factory=lambda args: proc, start_reader_threads=False)
    client.start()

    client.reply_user_input("chat-c", "request-7", {"reply": ["ok"]})

    written = proc.stdin.getvalue()
    assert '"type":"reply_user_input"' in written
    assert '"request_id":"request-7"' in written
    assert '"reply":["ok"]' in written


def test_worker_client_close_terminates_process():
    proc = FakeProcess()
    client = CodexWorkerClient(process_factory=lambda args: proc, start_reader_threads=False)
    client.start()

    client.close()

    assert proc.terminated is True


def test_worker_client_enqueue_compacts_answer_delta_but_keeps_execution_rows():
    observed = []
    client = CodexWorkerClient(process_factory=lambda args: FakeProcess(), on_message=observed.append, start_reader_threads=False)

    for idx in range(20):
        client._enqueue_worker_message(
            {
                "type": "event",
                "payload": {
                    "chat_id": "chat-c",
                    "turn_idx": 0,
                    "event": {"type": "agent_message_delta", "text": f"delta-{idx}", "data": {"turn_id": "turn-1"}},
                },
            }
        )
    client._enqueue_worker_message(
        {
            "type": "event",
            "payload": {
                "chat_id": "chat-c",
                "turn_idx": 0,
                "event": {"type": "item_started", "text": "step 1", "data": {"turn_id": "turn-1", "step_seq": 1}},
            },
        }
    )
    client._enqueue_worker_message(
        {
            "type": "event",
            "payload": {
                "chat_id": "chat-c",
                "turn_idx": 0,
                "event": {"type": "item_completed", "text": "step 2", "data": {"turn_id": "turn-1", "step_seq": 2}},
            },
        }
    )

    drained = client.drain_pending_messages(limit=10)

    event_texts = [item["payload"]["event"].get("text") for item in drained if item["type"] == "event"]
    assert "delta-19" in event_texts
    assert "delta-0" not in event_texts
    assert "step 1" in event_texts
    assert "step 2" in event_texts
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
pytest tests/test_codex_worker_client.py -q
```

Expected: import failure for `codex_worker_client`.

- [ ] **Step 3: Implement `CodexWorkerClient` skeleton**

Create `codex_worker_client.py`:

```python
from __future__ import annotations

import queue
import subprocess
import sys
import threading
import uuid
from collections import OrderedDict, deque
from pathlib import Path
from typing import Callable

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
        on_message: Callable[[dict], None] | None = None,
        on_exit: Callable[[int | None], None] | None = None,
        process_factory: Callable[[list[str]], subprocess.Popen] | None = None,
        start_reader_threads: bool = True,
        worker_module: str = "codex_worker_process",
        queue_limit: int = 1000,
    ) -> None:
        self.on_message = on_message
        self.on_exit = on_exit
        self.process_factory = process_factory
        self.start_reader_threads = bool(start_reader_threads)
        self.worker_module = worker_module
        self.queue_limit = max(int(queue_limit or 1000), 100)
        self._proc = None
        self._send_lock = threading.Lock()
        self._pending = deque()
        self._latest_delta_by_key: OrderedDict[tuple[str, str], dict] = OrderedDict()
        self._closed = False

    def start(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        args = [sys.executable, "-m", self.worker_module]
        if self.process_factory is not None:
            self._proc = self.process_factory(args)
        else:
            self._proc = subprocess.Popen(
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(Path(__file__).resolve().parent),
                bufsize=1,
                shell=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        if self.start_reader_threads:
            threading.Thread(target=self._stdout_loop, daemon=True).start()
            threading.Thread(target=self._stderr_loop, daemon=True).start()

    def close(self) -> None:
        self._closed = True
        proc = self._proc
        if proc is None:
            return
        try:
            self._send({"id": self._next_request_id(), "type": "shutdown", "payload": {}})
        except Exception:
            pass
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def start_turn(self, **payload) -> str:
        request_id = self._next_request_id()
        self._send(make_ui_request(request_id, "start_turn", payload))
        return request_id

    def reply_user_input(self, chat_id: str, request_id: str | int, answers: dict) -> str:
        message_id = self._next_request_id()
        self._send(
            make_ui_request(
                message_id,
                "reply_user_input",
                {"chat_id": str(chat_id or ""), "request_id": request_id, "answers": dict(answers or {})},
            )
        )
        return message_id

    def cancel_turn(self, chat_id: str, thread_id: str, turn_id: str) -> str:
        request_id = self._next_request_id()
        self._send(make_ui_request(request_id, "cancel_turn", {"chat_id": chat_id, "thread_id": thread_id, "turn_id": turn_id}))
        return request_id

    def drain_pending_messages(self, limit: int = 100) -> list[dict]:
        items = []
        while self._latest_delta_by_key and len(items) < limit:
            _, message = self._latest_delta_by_key.popitem(last=False)
            items.append(message)
        while self._pending and len(items) < limit:
            items.append(self._pending.popleft())
        return items

    def _send(self, message: dict) -> None:
        proc = self._proc
        if proc is None:
            raise RuntimeError("Codex worker is not running.")
        stdin = getattr(proc, "stdin", None)
        if stdin is None:
            raise RuntimeError("Codex worker stdin is unavailable.")
        line = encode_worker_message(message)
        with self._send_lock:
            stdin.write(line)
            stdin.flush()

    def _enqueue_worker_message(self, message: dict) -> None:
        try:
            validate_chat_scoped_message(message)
        except CodexWorkerProtocolError:
            return
        payload = message.get("payload") if isinstance(message, dict) else {}
        event = payload.get("event") if isinstance(payload, dict) else {}
        if isinstance(event, dict) and event.get("type") == "agent_message_delta":
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            key = (str(payload.get("chat_id") or ""), str(data.get("turn_id") or payload.get("turn_idx") or ""))
            self._latest_delta_by_key[key] = message
        else:
            self._pending.append(message)
        while len(self._pending) + len(self._latest_delta_by_key) > self.queue_limit and self._pending:
            self._pending.popleft()
        if self.on_message is not None:
            self.on_message(message)

    def _stdout_loop(self) -> None:
        proc = self._proc
        stdout = getattr(proc, "stdout", None)
        if stdout is None:
            return
        try:
            for raw_line in stdout:
                if self._closed:
                    return
                try:
                    self._enqueue_worker_message(decode_worker_line(raw_line))
                except CodexWorkerProtocolError:
                    continue
        finally:
            if self.on_exit is not None and proc is not None:
                self.on_exit(proc.poll())

    def _stderr_loop(self) -> None:
        proc = self._proc
        stderr = getattr(proc, "stderr", None)
        if stderr is None:
            return
        for _line in stderr:
            if self._closed:
                return

    @staticmethod
    def _next_request_id() -> str:
        return f"worker-{uuid.uuid4().hex}"
```

- [ ] **Step 4: Run client tests**

Run:

```powershell
pytest tests/test_codex_worker_client.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add codex_worker_client.py tests/test_codex_worker_client.py
git commit -m "feat: add codex worker client"
```

---

### Task 3: Worker Process Command Loop with Fake Codex Client

**Files:**
- Create: `codex_worker_process.py`
- Test: `tests/test_codex_worker_process.py`

- [ ] **Step 1: Write failing worker process tests**

Create `tests/test_codex_worker_process.py`:

```python
import io

from codex_worker_process import CodexWorkerRuntime
from codex_worker_protocol import decode_worker_line, encode_worker_message, make_ui_request


class FakeCodexClient:
    def __init__(self, on_event=None, codex_model="codex/main"):
        self.on_event = on_event
        self.codex_model = codex_model
        self.started_threads = []
        self.started_turns = []
        self.replies = []
        self.closed = False

    def start_thread(self, **kwargs):
        self.started_threads.append(kwargs)
        return {"thread": {"id": "thread-1"}}

    def start_turn_items(self, thread_id, items, service_tier=None):
        self.started_turns.append((thread_id, items, service_tier))
        return {"turn": {"id": "turn-1"}}

    def respond_tool_request_user_input(self, request_id, answers):
        self.replies.append((request_id, answers))

    def close(self):
        self.closed = True


def test_worker_runtime_start_turn_emits_thread_state_and_turn_finished():
    output = io.StringIO()
    created = []

    runtime = CodexWorkerRuntime(
        client_factory=lambda on_event, codex_model: created.append(FakeCodexClient(on_event, codex_model)) or created[-1],
        output=output,
    )

    runtime.handle_message(
        make_ui_request(
            "req-1",
            "start_turn",
            {
                "chat_id": "chat-c",
                "turn_idx": 0,
                "question": "问题",
                "model": "codex/main",
                "cwd": "c:/code/sj",
                "thread_id": "",
                "turn_id": "",
                "input_items": [{"type": "text", "text": "问题"}],
                "attachments": [],
                "service_tier": "fast",
            },
        )
    )

    messages = [decode_worker_line(line + "\n") for line in output.getvalue().splitlines()]
    assert any(item["type"] == "thread_state" and item["payload"]["chat_id"] == "chat-c" for item in messages)
    assert any(item["type"] == "turn_finished" and item["payload"]["turn_id"] == "turn-1" for item in messages)
    assert created[0].started_threads[0]["cwd"] == "c:/code/sj"
    assert created[0].started_turns[0][2] == "fast"


def test_worker_runtime_reply_user_input_routes_to_matching_chat_client():
    output = io.StringIO()
    created = []
    runtime = CodexWorkerRuntime(
        client_factory=lambda on_event, codex_model: created.append(FakeCodexClient(on_event, codex_model)) or created[-1],
        output=output,
    )
    runtime.handle_message(
        make_ui_request(
            "req-1",
            "start_turn",
            {
                "chat_id": "chat-c",
                "turn_idx": 0,
                "question": "问题",
                "model": "codex/main",
                "cwd": "c:/code/sj",
                "thread_id": "",
                "turn_id": "",
                "input_items": [{"type": "text", "text": "问题"}],
                "attachments": [],
                "service_tier": "",
            },
        )
    )

    runtime.handle_message(make_ui_request("req-2", "reply_user_input", {"chat_id": "chat-c", "request_id": "ask-1", "answers": {"reply": ["ok"]}}))

    assert created[0].replies == [("ask-1", {"reply": ["ok"]})]


def test_worker_process_source_does_not_import_wx():
    import pathlib

    source = pathlib.Path("codex_worker_process.py").read_text(encoding="utf-8")
    assert "import wx" not in source
    assert "from wx" not in source
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
pytest tests/test_codex_worker_process.py -q
```

Expected: import failure for `codex_worker_process`.

- [ ] **Step 3: Implement worker runtime and command loop**

Create `codex_worker_process.py`:

```python
from __future__ import annotations

import sys
import traceback
from typing import Callable, TextIO

from codex_client import CodexAppServerClient, CodexEvent
from codex_worker_protocol import (
    CodexWorkerProtocolError,
    decode_worker_line,
    encode_worker_message,
    event_to_payload,
    make_worker_event,
    validate_chat_scoped_message,
)


class CodexWorkerRuntime:
    def __init__(
        self,
        client_factory: Callable | None = None,
        output: TextIO | None = None,
    ) -> None:
        self.output = output or sys.stdout
        self.client_factory = client_factory or (lambda on_event, codex_model: CodexAppServerClient(on_event=on_event, codex_model=codex_model))
        self.clients: dict[tuple[str, str], CodexAppServerClient] = {}
        self.thread_state: dict[str, dict] = {}

    def emit(self, message_type: str, payload: dict | None = None, request_id: str | None = None) -> None:
        self.output.write(encode_worker_message(make_worker_event(message_type, payload or {}, request_id=request_id)))
        self.output.flush()

    def handle_message(self, message: dict) -> None:
        message_type = str(message.get("type") or "")
        if message_type in {"start_turn", "reply_user_input", "cancel_turn"}:
            validate_chat_scoped_message(message)
        if message_type == "start_turn":
            self._handle_start_turn(message)
        elif message_type == "reply_user_input":
            self._handle_reply_user_input(message)
        elif message_type == "shutdown":
            self.close()
            self.emit("fatal", {"reason": "shutdown"})
        elif message_type == "ping":
            self.emit("pong", {}, request_id=str(message.get("id") or ""))

    def _client_for(self, chat_id: str, model: str):
        key = (str(chat_id or ""), str(model or "codex/main"))
        client = self.clients.get(key)
        if client is not None:
            return client

        def _on_event(event: CodexEvent, cid=key[0]) -> None:
            self.emit("event", {"chat_id": cid, "event": event_to_payload(event)})

        client = self.client_factory(_on_event, key[1])
        self.clients[key] = client
        return client

    def _handle_start_turn(self, message: dict) -> None:
        payload = dict(message.get("payload") or {})
        request_id = str(message.get("id") or "")
        chat_id = str(payload.get("chat_id") or "")
        model = str(payload.get("model") or "codex/main")
        client = self._client_for(chat_id, model)
        thread_id = str(payload.get("thread_id") or "").strip()
        service_tier = str(payload.get("service_tier") or "").strip()
        if not thread_id:
            thread_resp = client.start_thread(
                cwd=str(payload.get("cwd") or ""),
                approval_policy="never",
                sandbox="danger-full-access",
                personality="pragmatic",
                service_tier=service_tier,
            )
            thread_id = str((thread_resp.get("thread") or {}).get("id") or "").strip()
        input_items = list(payload.get("input_items") or [{"type": "text", "text": str(payload.get("question") or "")}])
        turn_resp = client.start_turn_items(thread_id, input_items, service_tier=service_tier)
        turn_id = str((turn_resp.get("turn") or {}).get("id") or "").strip()
        state = {"chat_id": chat_id, "turn_idx": int(payload.get("turn_idx") or 0), "thread_id": thread_id, "turn_id": turn_id, "active": True, "model": model}
        self.thread_state[chat_id] = state
        self.emit("thread_state", state, request_id=request_id)
        self.emit("turn_finished", {**state, "active": False}, request_id=request_id)

    def _handle_reply_user_input(self, message: dict) -> None:
        payload = dict(message.get("payload") or {})
        chat_id = str(payload.get("chat_id") or "")
        request_id = payload.get("request_id")
        answers = dict(payload.get("answers") or {})
        matching = [client for (cid, _model), client in self.clients.items() if cid == chat_id]
        if not matching:
            self.emit("error", {"chat_id": chat_id, "message": "No Codex worker client for pending input reply."})
            return
        matching[0].respond_tool_request_user_input(request_id, answers)

    def close(self) -> None:
        for client in list(self.clients.values()):
            try:
                client.close()
            except Exception:
                pass
        self.clients.clear()


def main() -> int:
    runtime = CodexWorkerRuntime()
    runtime.emit("ready", {})
    for raw_line in sys.stdin:
        try:
            runtime.handle_message(decode_worker_line(raw_line))
        except Exception as exc:
            runtime.emit("fatal", {"reason": str(exc), "traceback": traceback.format_exc()})
    runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run worker process tests**

Run:

```powershell
pytest tests/test_codex_worker_process.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```powershell
git add codex_worker_process.py tests/test_codex_worker_process.py
git commit -m "feat: add codex worker process runtime"
```

---

### Task 4: UI Worker Adapter Without Direct Codex Client Construction

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_unit.py`

- [ ] **Step 1: Write failing UI adapter tests**

Append tests to `tests/test_main_unit.py`:

```python
def test_ui_get_or_create_codex_client_returns_worker_client(frame, monkeypatch):
    created = []

    class FakeWorkerClient:
        codex_model = main.DEFAULT_CODEX_MODEL

        def __init__(self, *args, **kwargs):
            created.append(kwargs)
            self.closed = False

        def start(self):
            pass

        def close(self):
            self.closed = True

    monkeypatch.setattr(main, "CodexWorkerClient", FakeWorkerClient)
    monkeypatch.setattr(main, "CodexAppServerClient", lambda *a, **k: pytest.fail("UI process must not instantiate CodexAppServerClient"))

    client = frame._get_or_create_codex_client("chat-c", main.DEFAULT_CODEX_MODEL)

    assert isinstance(client, FakeWorkerClient)
    assert created


def test_ui_source_no_longer_constructs_codex_app_server_client_directly():
    source = Path("main.py").read_text(encoding="utf-8")
    assert "CodexAppServerClient(" not in source
```

If `Path` is not imported in `tests/test_main_unit.py`, add:

```python
from pathlib import Path
```

- [ ] **Step 2: Run targeted tests and verify they fail**

Run:

```powershell
pytest tests/test_main_unit.py -q -k "ui_get_or_create_codex_client_returns_worker_client or ui_source_no_longer_constructs_codex_app_server_client_directly"
```

Expected: failure because `main.py` still constructs `CodexAppServerClient`.

- [ ] **Step 3: Import `CodexWorkerClient` and remove direct constructor import**

Modify the import area in `main.py`.

Replace the current `codex_client` import that includes `CodexAppServerClient` with:

```python
from codex_client import (
    CodexEvent,
    DEFAULT_CODEX_MODEL,
    DEFAULT_CODEX_SERVICE_TIER,
    normalize_codex_service_tier,
)
from codex_worker_client import CodexWorkerClient
```

Keep any other existing imported names from `codex_client`; remove only `CodexAppServerClient` from `main.py`.

- [ ] **Step 4: Replace `_get_or_create_codex_client` and `_ensure_codex_client` constructors**

Modify the two methods so they construct `CodexWorkerClient`:

```python
    def _get_or_create_codex_client(self, chat_id: str, model: str = ""):
        key = str(chat_id or self.active_chat_id or self.current_chat_id or "").strip() or self._ensure_active_chat_id()
        codex_model = str(model or self.selected_model or DEFAULT_CODEX_MODEL).strip() or DEFAULT_CODEX_MODEL
        client = self._codex_clients.get(key)
        if client is not None and getattr(client, "codex_model", DEFAULT_CODEX_MODEL) == codex_model:
            return client
        if client is not None:
            client.close()
        client = CodexWorkerClient(
            on_message=lambda message, cid=key: self._on_codex_worker_message(cid, message),
            on_exit=lambda code, cid=key: self._on_codex_worker_exit(cid, code),
        )
        client.codex_model = codex_model
        client.start()
        self._codex_clients[key] = client
        return client

    def _ensure_codex_client(self, model: str = ""):
        codex_model = str(model or self.selected_model or DEFAULT_CODEX_MODEL).strip() or DEFAULT_CODEX_MODEL
        client = getattr(self, "_codex_client", None)
        if client is None or getattr(client, "codex_model", DEFAULT_CODEX_MODEL) != codex_model:
            if client is not None:
                client.close()
            client = CodexWorkerClient(
                on_message=lambda message: self._on_codex_worker_message(self.active_chat_id or self.current_chat_id or "", message),
                on_exit=lambda code: self._on_codex_worker_exit(self.active_chat_id or self.current_chat_id or "", code),
            )
            client.codex_model = codex_model
            client.start()
            self._codex_client = client
        return client
```

- [ ] **Step 5: Add worker message bridge methods**

Add these methods near `_dispatch_codex_event_to_ui`:

```python
    def _on_codex_worker_message(self, default_chat_id: str, message: dict) -> None:
        if not isinstance(message, dict):
            return
        payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
        chat_id = str(payload.get("chat_id") or default_chat_id or "").strip()
        if not chat_id and str(message.get("type") or "") in {"event", "thread_state", "turn_finished", "error"}:
            return
        message_type = str(message.get("type") or "").strip()
        if message_type == "event":
            event_payload = payload.get("event") if isinstance(payload.get("event"), dict) else {}
            try:
                from codex_worker_protocol import event_from_payload
                event = event_from_payload(event_payload)
            except Exception:
                return
            self._dispatch_codex_event_to_ui(chat_id, event)
        elif message_type == "thread_state":
            self._call_after_if_alive(self._apply_codex_worker_thread_state, chat_id, payload)
        elif message_type == "turn_finished":
            self._call_after_if_alive(self._apply_codex_worker_turn_finished, chat_id, payload)
        elif message_type == "error":
            self._call_after_if_alive(self._apply_codex_worker_error, chat_id, str(payload.get("message") or "Codex worker error."))

    def _apply_codex_worker_thread_state(self, chat_id: str, payload: dict) -> None:
        target_chat = self._current_chat_state if chat_id in {self.active_chat_id, self.current_chat_id, ""} else self._find_archived_chat(chat_id)
        if not isinstance(target_chat, dict):
            return
        target_chat["codex_thread_id"] = str(payload.get("thread_id") or "")
        target_chat["codex_turn_id"] = str(payload.get("turn_id") or "")
        target_chat["codex_turn_active"] = bool(payload.get("active"))
        if target_chat is self._current_chat_state:
            self.active_codex_thread_id = target_chat["codex_thread_id"]
            self.active_codex_turn_id = target_chat["codex_turn_id"]
            self.active_codex_turn_active = bool(target_chat["codex_turn_active"])
        self._defer_codex_state_save()

    def _apply_codex_worker_turn_finished(self, chat_id: str, payload: dict) -> None:
        self._apply_codex_worker_thread_state(chat_id, {**dict(payload or {}), "active": False})

    def _apply_codex_worker_error(self, chat_id: str, message: str) -> None:
        target_chat = self._current_chat_state if chat_id in {self.active_chat_id, self.current_chat_id, ""} else self._find_archived_chat(chat_id)
        turns = target_chat.get("turns") if isinstance(target_chat, dict) and isinstance(target_chat.get("turns"), list) else []
        turn_idx = len(turns) - 1
        if 0 <= turn_idx < len(turns) and isinstance(turns[turn_idx], dict):
            self._mark_turn_request_failed(turns[turn_idx], message)
            self._mark_chat_turns_dirty(chat_id, turn_idx)
        self._defer_codex_state_save()

    def _on_codex_worker_exit(self, chat_id: str, returncode: int | None) -> None:
        if getattr(self, "_closing", False):
            return
        self._call_after_if_alive(self._apply_codex_worker_error, chat_id, f"Codex worker exited: {returncode}")
```

- [ ] **Step 6: Run targeted UI adapter tests**

Run:

```powershell
pytest tests/test_main_unit.py -q -k "ui_get_or_create_codex_client_returns_worker_client or ui_source_no_longer_constructs_codex_app_server_client_directly"
```

Expected: all targeted tests pass.

- [ ] **Step 7: Commit**

```powershell
git add main.py tests/test_main_unit.py
git commit -m "feat: route codex client creation through worker client"
```

---

### Task 5: Move Turn Orchestration to Worker Command

**Files:**
- Modify: `codex_worker_process.py`
- Modify: `main.py`
- Test: `tests/test_main_unit.py`
- Test: `tests/test_codex_worker_process.py`

- [ ] **Step 1: Write failing tests for UI sending `start_turn` instead of calling Codex methods**

Append to `tests/test_main_unit.py`:

```python
def test_run_codex_turn_worker_sends_start_turn_to_worker(frame, monkeypatch):
    sent = []

    class FakeWorker:
        codex_model = main.DEFAULT_CODEX_MODEL

        def start(self):
            pass

        def start_turn(self, **payload):
            sent.append(payload)
            return "req-1"

    monkeypatch.setattr(frame, "_get_or_create_codex_client", lambda chat_id, model="": FakeWorker())
    monkeypatch.setattr(frame, "_call_after_if_alive", lambda func, *args, **kwargs: True)

    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state["id"] = "chat-current"
    frame._current_chat_state["turns"] = [{"question": "问题", "answer_md": main.REQUESTING_TEXT, "model": main.DEFAULT_CODEX_MODEL}]
    frame.active_session_turns = frame._current_chat_state["turns"]

    frame._run_codex_turn_worker("chat-current", 0, "问题", main.DEFAULT_CODEX_MODEL)

    assert sent
    assert sent[0]["chat_id"] == "chat-current"
    assert sent[0]["turn_idx"] == 0
    assert sent[0]["question"] == "问题"
    assert sent[0]["input_items"][0]["type"] == "text"
```

- [ ] **Step 2: Write failing worker recovery test**

Append to `tests/test_codex_worker_process.py`:

```python
class MissingThreadClient(FakeCodexClient):
    def __init__(self, on_event=None, codex_model="codex/main"):
        super().__init__(on_event, codex_model)
        self.resume_attempts = 0

    def resume_thread(self, *args, **kwargs):
        self.resume_attempts += 1
        raise RuntimeError("thread not found")


def test_worker_recovers_missing_thread_by_starting_new_thread():
    output = io.StringIO()
    created = []
    runtime = CodexWorkerRuntime(
        client_factory=lambda on_event, codex_model: created.append(MissingThreadClient(on_event, codex_model)) or created[-1],
        output=output,
    )

    runtime.handle_message(
        make_ui_request(
            "req-1",
            "start_turn",
            {
                "chat_id": "chat-c",
                "turn_idx": 0,
                "question": "问题",
                "model": "codex/main",
                "cwd": "c:/code/sj",
                "thread_id": "missing-thread",
                "turn_id": "",
                "input_items": [{"type": "text", "text": "问题"}],
                "attachments": [],
                "service_tier": "",
            },
        )
    )

    messages = [decode_worker_line(line + "\n") for line in output.getvalue().splitlines()]
    assert created[0].resume_attempts == 1
    assert any(item["type"] == "thread_state" and item["payload"]["thread_id"] == "thread-1" for item in messages)
```

- [ ] **Step 3: Run targeted tests and verify they fail**

Run:

```powershell
pytest tests/test_main_unit.py tests/test_codex_worker_process.py -q -k "run_codex_turn_worker_sends_start_turn_to_worker or worker_recovers_missing_thread_by_starting_new_thread"
```

Expected: failure because `_run_codex_turn_worker` still calls Codex client methods and worker recovery is incomplete.

- [ ] **Step 4: Simplify `_run_codex_turn_worker` to prepare payload and call worker**

In `main.py`, replace the Codex transport calls inside `_run_codex_turn_worker` with a single `client.start_turn(...)` call after resolving the target chat, turn, service tier, attachments, thread id, turn id, and input items.

Use this payload shape:

```python
client.start_turn(
    chat_id=client_chat_id,
    turn_idx=turn_idx,
    question=send_question,
    model=model,
    cwd=self._workspace_dir_for_codex(),
    thread_id=thread_id,
    turn_id=turn_id,
    input_items=input_items,
    attachments=turn_attachments,
    service_tier=service_tier,
    should_steer=self._codex_should_steer_turn(target_chat, is_current_target) and bool(turn_id),
    history_turns=target_turns[:turn_idx] if turn_idx > 0 else [],
)
```

Keep the UI-owned preparation code:

```python
target_turns = self.active_session_turns if is_current_target else (target_chat.get("turns") if isinstance(target_chat.get("turns"), list) else [])
service_tier = self._codex_service_tier_for_chat(target_chat)
turn_attachments = [...]
input_items = self._build_codex_input_items(send_question, turn_attachments)
```

Remove direct calls from `main.py` to:

```python
client.start_thread(...)
client.resume_thread(...)
client.start_turn_items(...)
client.steer_turn_items(...)
```

inside `_run_codex_turn_worker`.

- [ ] **Step 5: Add worker-side resume, steer, and recovery logic**

In `codex_worker_process.py`, update `_handle_start_turn`:

```python
        should_steer = bool(payload.get("should_steer")) and bool(payload.get("turn_id"))
        if thread_id:
            try:
                if hasattr(client, "resume_thread"):
                    client.resume_thread(
                        thread_id,
                        approval_policy="never",
                        sandbox="danger-full-access",
                        personality="pragmatic",
                        cwd=str(payload.get("cwd") or ""),
                        service_tier=service_tier,
                    )
            except Exception as exc:
                text = str(exc).lower()
                if "thread not found" in text or "unknown thread" in text or "no rollout found" in text:
                    thread_id = ""
                    should_steer = False
                else:
                    raise
        if not thread_id:
            thread_resp = client.start_thread(...)
            thread_id = ...
        if should_steer and hasattr(client, "steer_turn_items"):
            turn_resp = client.steer_turn_items(thread_id, str(payload.get("turn_id") or ""), input_items)
        else:
            turn_resp = client.start_turn_items(thread_id, input_items, service_tier=service_tier)
```

- [ ] **Step 6: Run targeted tests**

Run:

```powershell
pytest tests/test_main_unit.py tests/test_codex_worker_process.py -q -k "run_codex_turn_worker_sends_start_turn_to_worker or worker_recovers_missing_thread_by_starting_new_thread"
```

Expected: all targeted tests pass.

- [ ] **Step 7: Run existing Codex worker regression subset**

Run:

```powershell
pytest tests/test_main_unit.py -q -k "codex_worker_uses_target_chat_runtime_state or codex_worker_recovers_missing_thread or codex_worker_resumes_existing_thread or codex_worker_passes_saved_fast_service_tier or codex_worker_rebuilds_context or codex_worker_sends_local_image_items"
```

Expected: pass after updating assertions to inspect `start_turn` payloads where previous tests inspected fake `CodexAppServerClient` method calls.

- [ ] **Step 8: Commit**

```powershell
git add main.py codex_worker_process.py tests/test_main_unit.py tests/test_codex_worker_process.py
git commit -m "feat: move codex turn orchestration into worker"
```

---

### Task 6: Pending User Input Reply Routing

**Files:**
- Modify: `main.py`
- Modify: `codex_worker_client.py`
- Test: `tests/test_main_unit.py`
- Test: `tests/test_codex_integration.py`

- [ ] **Step 1: Write failing tests for pending reply IPC**

Append to `tests/test_main_unit.py`:

```python
def test_remote_pending_request_reply_uses_worker_ipc(frame, monkeypatch):
    replies = []

    class FakeWorker:
        def reply_user_input(self, chat_id, request_id, answers):
            replies.append((chat_id, request_id, answers))

    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_codex_pending_request = {"request_id": "ask-1"}
    monkeypatch.setattr(frame, "_get_or_create_codex_client", lambda chat_id, model="": FakeWorker())

    ok, message = frame._handle_remote_pending_request_reply("答案")

    assert ok is True
    assert replies == [("chat-current", "ask-1", {"reply": ["答案"]})]
    assert "已提交" in message
```

Append to `tests/test_codex_integration.py`:

```python
def test_codex_request_user_input_dialog_replies_through_worker(frame, monkeypatch):
    replies = []

    class FakeWorker:
        def reply_user_input(self, chat_id, request_id, answers):
            replies.append((chat_id, request_id, answers))

    monkeypatch.setattr(frame, "_get_or_create_codex_client", lambda chat_id, model="": FakeWorker())
    monkeypatch.setattr(frame, "_show_codex_user_input_dialog", lambda params: {"reply": ["ok"]})

    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._handle_codex_request_dialog({"request_id": "ask-1", "method": "item/tool/requestUserInput", "params": {"questions": []}})

    assert replies == [("chat-current", "ask-1", {"reply": ["ok"]})]
```

- [ ] **Step 2: Run tests and verify they fail**

Run:

```powershell
pytest tests/test_main_unit.py tests/test_codex_integration.py -q -k "remote_pending_request_reply_uses_worker_ipc or codex_request_user_input_dialog_replies_through_worker"
```

Expected: failure because current code directly calls `respond_tool_request_user_input`.

- [ ] **Step 3: Replace direct response calls**

In `main.py`, replace both direct calls:

```python
client.respond_tool_request_user_input(...)
```

with:

```python
client.reply_user_input(chat_id, pending.get("request_id"), {"reply": [str(text or "")]})
```

for remote replies, and:

```python
client.reply_user_input(chat_id, request.get("request_id"), answers)
```

for local dialog replies.

Resolve `chat_id` with:

```python
chat_id = str(self.active_chat_id or self.current_chat_id or "").strip()
```

for current-dialog replies, and with the pending request's chat when available for remote replies:

```python
chat_id = str(pending.get("chat_id") or self.active_chat_id or self.current_chat_id or "").strip()
```

- [ ] **Step 4: Run pending reply tests**

Run:

```powershell
pytest tests/test_main_unit.py tests/test_codex_integration.py -q -k "remote_pending_request_reply_uses_worker_ipc or codex_request_user_input_dialog_replies_through_worker"
```

Expected: all targeted tests pass.

- [ ] **Step 5: Commit**

```powershell
git add main.py tests/test_main_unit.py tests/test_codex_integration.py
git commit -m "feat: route codex pending input replies through worker"
```

---

### Task 7: Worker Crash Isolation and Chat-Scoped State

**Files:**
- Modify: `main.py`
- Modify: `codex_worker_client.py`
- Test: `tests/test_main_unit.py`

- [ ] **Step 1: Write failing crash isolation test**

Append to `tests/test_main_unit.py`:

```python
def test_worker_exit_marks_only_matching_chat_failed(frame, monkeypatch):
    saved = []
    monkeypatch.setattr(frame, "_save_state", lambda: saved.append(True))

    frame.active_chat_id = "chat-a"
    frame.current_chat_id = "chat-a"
    frame._current_chat_state["id"] = "chat-a"
    frame._current_chat_state["turns"] = [{"question": "A", "answer_md": main.REQUESTING_TEXT, "request_status": "running"}]
    frame.active_session_turns = frame._current_chat_state["turns"]
    chat_b = {
        "id": "chat-b",
        "title": "B",
        "turns": [{"question": "B", "answer_md": main.REQUESTING_TEXT, "request_status": "running"}],
    }
    frame.archived_chats = [chat_b]
    monkeypatch.setattr(frame, "_call_after_if_alive", lambda func, *args, **kwargs: func(*args, **kwargs) or True)

    frame._on_codex_worker_exit("chat-b", -9)

    assert frame.active_session_turns[0]["request_status"] == "running"
    assert chat_b["turns"][0]["request_status"] == "failed"
    assert "exited" in chat_b["turns"][0]["request_error"]
```

- [ ] **Step 2: Run test and verify it fails**

Run:

```powershell
pytest tests/test_main_unit.py -q -k "worker_exit_marks_only_matching_chat_failed"
```

Expected: failure if worker exit handling still targets the active chat or latest turn.

- [ ] **Step 3: Make `_apply_codex_worker_error` use explicit chat and turn index**

Update `_apply_codex_worker_error`:

```python
    def _apply_codex_worker_error(self, chat_id: str, message: str, turn_idx: int | None = None) -> None:
        chat_id = str(chat_id or "").strip()
        target_chat = self._current_chat_state if chat_id in {self.active_chat_id, self.current_chat_id, ""} else self._find_archived_chat(chat_id)
        if not isinstance(target_chat, dict):
            return
        turns = target_chat.get("turns") if isinstance(target_chat.get("turns"), list) else []
        idx = int(turn_idx) if isinstance(turn_idx, int) else len(turns) - 1
        if 0 <= idx < len(turns) and isinstance(turns[idx], dict):
            self._mark_turn_request_failed(turns[idx], message)
            self._mark_chat_turns_dirty(chat_id, idx)
        if target_chat is self._current_chat_state:
            self.is_running = False
            self._active_request_count = 0
            self.active_codex_turn_active = False
        self._defer_codex_state_save()
```

Update `_on_codex_worker_message` error branch:

```python
self._call_after_if_alive(self._apply_codex_worker_error, chat_id, str(payload.get("message") or "Codex worker error."), payload.get("turn_idx"))
```

- [ ] **Step 4: Run crash isolation test**

Run:

```powershell
pytest tests/test_main_unit.py -q -k "worker_exit_marks_only_matching_chat_failed"
```

Expected: pass.

- [ ] **Step 5: Commit**

```powershell
git add main.py tests/test_main_unit.py
git commit -m "fix: isolate codex worker crash state by chat"
```

---

### Task 8: Regression and Automation Coverage

**Files:**
- Modify: `tests/test_codex_ui_responsiveness_automation.py`
- Modify: `tests/test_history_ui_automation.py`
- Modify: `tests/test_codex_integration.py`

- [ ] **Step 1: Add worker event responsiveness test**

Append to `tests/test_codex_ui_responsiveness_automation.py`:

```python
def test_real_ui_worker_events_do_not_mutate_lists_during_navigation_quiet(frame, wx_app, monkeypatch):
    mutations = []
    monkeypatch.setattr(frame.answer_list, "Append", lambda *args, **kwargs: mutations.append(("answer_append", args)))
    monkeypatch.setattr(frame.execution_list, "Append", lambda *args, **kwargs: mutations.append(("execution_append", args)))

    frame._navigation_quiet_until = time.monotonic() + 3.0
    frame._on_codex_worker_message(
        "chat-current",
        {
            "type": "event",
            "payload": {
                "chat_id": frame.active_chat_id or frame.current_chat_id,
                "turn_idx": 0,
                "event": {
                    "type": "item_started",
                    "text": "run pytest",
                    "data": {"turn_id": frame.active_codex_turn_id or "turn-1", "step_seq": 1},
                },
            },
        },
    )
    wx_app.Yield()

    assert mutations == []
```

Ensure `time` is imported in the file:

```python
import time
```

- [ ] **Step 2: Add clear-context worker regression test**

Append to `tests/test_history_ui_automation.py`:

```python
def test_ui_automation_worker_notice_stays_with_cleared_history_chat(frame, monkeypatch):
    chat_a = {"id": "chat-a", "title": "A", "turns": [{"question": "A", "answer_md": "running"}], "codex_thread_id": "thread-a"}
    chat_c = {"id": "chat-c", "title": "C", "turns": [{"question": "C", "answer_md": "old"}], "codex_thread_id": "thread-c"}
    frame.archived_chats = [chat_a, chat_c]
    frame.view_mode = "history"
    frame._history_visible_chat_id = "chat-c"
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    assert frame._clear_context_and_start_new_chat() is True

    assert chat_c["codex_thread_id"] == ""
    assert chat_a["codex_thread_id"] == "thread-a"
    assert frame._answer_list_tail_notice_chat_id == "chat-c"
```

- [ ] **Step 3: Run automation tests and verify failures if bridge bypasses quiet window**

Run:

```powershell
pytest tests/test_codex_ui_responsiveness_automation.py tests/test_history_ui_automation.py -q -k "worker_events_do_not_mutate_lists_during_navigation_quiet or worker_notice_stays_with_cleared_history_chat"
```

Expected: pass after Task 4 and Task 7; if it fails, route worker messages through `_dispatch_codex_event_to_ui` and existing quiet-window drain only.

- [ ] **Step 4: Commit**

```powershell
git add tests/test_codex_ui_responsiveness_automation.py tests/test_history_ui_automation.py
git commit -m "test: cover codex worker UI regressions"
```

---

### Task 9: Full Verification on Simulator and Regression Suites

**Files:**
- No code files unless a preceding test exposes a bug.

- [ ] **Step 1: Run protocol, worker, and Codex unit suites**

Run:

```powershell
pytest tests/test_codex_worker_protocol.py tests/test_codex_worker_client.py tests/test_codex_worker_process.py tests/test_codex_client_unit.py -q
```

Expected: all pass.

- [ ] **Step 2: Run main regression subset**

Run:

```powershell
pytest tests/test_main_unit.py -q -k "shortcut or clear_context or navigation_quiet or pending_execution or codex_ui_event_drain or background_execution or codex_worker or pending_request"
```

Expected: all pass.

- [ ] **Step 3: Run UI automation regression suites**

Run:

```powershell
pytest tests/test_history_ui_automation.py tests/test_codex_ui_responsiveness_automation.py -q
```

Expected: all pass.

- [ ] **Step 4: Run Codex integration tests with fake worker**

Run:

```powershell
pytest tests/test_codex_integration.py -q -k "not real_roundtrip"
```

Expected: all selected tests pass.

- [ ] **Step 5: Run simulator E2E clear-context regression**

Run with the available emulator id:

```powershell
$env:NATS_CLEAR_CONTEXT_E2E_DEVICE='emulator-5556'
pytest tests/test_mobile_desktop_clear_context_e2e.py::test_mobile_emulator_clear_context_clears_desktop_chat_frame -q
```

Expected: one test passes. The cleared chat's answer list is empty, the "已开启新会话" notice appears in the cleared chat, and no other chat's Codex thread state is cleared.

- [ ] **Step 6: Run strict source assertions**

Run:

```powershell
rg -n "CodexAppServerClient\\(" main.py
rg -n "respond_tool_request_user_input" main.py
rg -n "import wx|from wx" codex_worker_process.py codex_worker_client.py codex_worker_protocol.py
```

Expected:

- First command returns no matches.
- Second command returns no matches.
- Third command returns no matches.

- [ ] **Step 7: Commit final verification notes if tests required code changes**

If Step 1-6 required fixes, commit the fixes:

```powershell
git add main.py codex_worker_client.py codex_worker_process.py codex_worker_protocol.py tests
git commit -m "fix: complete codex worker process isolation verification"
```

If Step 1-6 passed without code changes, do not create an empty commit.

---

## Self-Review

- Spec coverage: protocol, worker process, worker client, pending input, recovery ownership, state ownership, quiet-window preservation, execution-process tail behavior, crash handling, and simulator E2E are each mapped to tasks.
- Placeholder scan: the plan contains no unresolved markers or unspecified test commands.
- Type consistency: the plan consistently uses `CodexWorkerClient.start_turn(...)`, `CodexWorkerClient.reply_user_input(...)`, `CodexWorkerRuntime.handle_message(...)`, and JSON messages with `type` and `payload`.
- Scope control: the plan does not replace wxPython and does not redesign the execution-process list.
