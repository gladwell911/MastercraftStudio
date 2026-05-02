# NATS JetStream Remote Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the primary desktop/mobile remote sync transport with a bundled NATS JetStream path while preserving the existing remote chat protocol and keeping WebSocket fallback.

**Architecture:** The desktop app starts `nats-server.exe`, initializes JetStream streams, consumes mobile commands, and publishes durable events. The mobile app selects a NATS transport for `nats://` and `/nats` WebSocket endpoints, sends commands through JetStream, consumes durable events, and reuses existing `RemoteSessionStore` event application logic.

**Tech Stack:** Python 3.11, `nats-py`, bundled `nats-server.exe`, wxPython, pytest, Flutter/Dart, `dart_nats`, Android Kotlin background service.

---

## File Structure

Desktop repository `c:\code\mc`:

- Create `remote_nats_protocol.py`: subject naming, event ids, JSON encode/decode, response envelopes.
- Create `nats_runtime.py`: generate NATS config, locate bundled/dev `nats-server.exe`, start/stop process, readiness probe.
- Create `remote_nats.py`: NATS/JetStream transport, stream initialization, command consumer, event publisher.
- Modify `requirements.txt`: add `nats-py`.
- Modify `main.py`: initialize NATS runtime/transport, publish remote events to both transports during the migration period, include NATS runtime info in state.
- Modify `zgwd.spec` and `ZhugeQA_A11y.spec`: include bundled `nats-server.exe` when present.
- Create `scripts/download_nats_server.ps1`: developer helper to download the NATS Server Windows AMD64 release artifact.
- Test `tests/test_remote_nats_protocol.py`: protocol helpers.
- Test `tests/test_nats_runtime.py`: config generation and process command construction.
- Test `tests/test_remote_nats_unit.py`: command routing and event publishing with a fake NATS connection.

Mobile repository `c:\code\rc`:

- Modify `pubspec.yaml`: add `dart_nats`.
- Create `lib/remote_transport_selector.dart`: choose WebSocket or NATS based on endpoint shape.
- Create `lib/remote_nats_protocol.dart`: subject naming, request/event payload helpers.
- Create `lib/remote_nats_client.dart`: minimal NATS JetStream API wrapper over `dart_nats`.
- Create `lib/remote_nats_chat_service.dart`: `CodexChatService` implementation backed by NATS.
- Modify `lib/codex_chat_service.dart`: share event application helpers where useful, or keep existing WebSocket service and add a NATS sibling.
- Modify `lib/main.dart`: instantiate NATS service when settings endpoint is NATS-shaped.
- Modify `lib/remote_control_settings.dart`: preserve `nats://` and `/nats` endpoints without converting them to `/ws`.
- Modify `lib/remote_background_service.dart` and Android Kotlin background service only after foreground NATS is passing; first pass may keep background WebSocket if endpoint is not NATS.
- Test `test/remote_transport_selector_test.dart`: endpoint selection.
- Test `test/remote_nats_protocol_test.dart`: subject/payload helpers.
- Test `test/remote_nats_chat_service_test.dart`: command send, response event, event dedupe.

## Task 1: Desktop Protocol Helpers

**Files:**
- Create: `c:\code\mc\remote_nats_protocol.py`
- Test: `c:\code\mc\tests\test_remote_nats_protocol.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_remote_nats_protocol.py`:

```python
import json

from remote_nats_protocol import (
    DEFAULT_PAIR_ID,
    NatsSubjects,
    build_error_response,
    build_response_event,
    decode_payload,
    encode_payload,
    make_event_id,
    normalize_pair_id,
)


def test_subjects_are_scoped_by_pair_id():
    subjects = NatsSubjects.from_pair_id("Phone 1")

    assert subjects.pair_id == "phone-1"
    assert subjects.commands == "zgwd.phone-1.commands"
    assert subjects.events == "zgwd.phone-1.events"
    assert subjects.files == "zgwd.phone-1.files"
    assert subjects.command_stream == "ZGWD_COMMANDS_phone_1"
    assert subjects.event_stream == "ZGWD_EVENTS_phone_1"


def test_pair_id_falls_back_to_default():
    assert normalize_pair_id("") == DEFAULT_PAIR_ID
    assert normalize_pair_id("中文 pair") == "pair"


def test_payload_round_trip_rejects_invalid_json():
    payload = {"type": "state", "chat_id": "c1"}
    assert decode_payload(encode_payload(payload)) == payload

    try:
        decode_payload(b"{bad")
    except ValueError as exc:
        assert "invalid_json" in str(exc)
    else:
        raise AssertionError("invalid json should fail")


def test_response_event_shape_is_stable():
    event = build_response_event(
        request_id="state-1",
        status=200,
        body={"accepted": True, "chat_id": "c1"},
        chat_id="c1",
    )

    assert event["type"] == "response"
    assert event["request_id"] == "state-1"
    assert event["event_id"] == "response-state-1"
    assert event["ok"] is True
    assert event["chat_id"] == "c1"


def test_error_response_shape_is_stable():
    event = build_error_response("bad-1", 400, "invalid_payload")

    assert event["type"] == "response"
    assert event["ok"] is False
    assert event["status"] == 400
    assert event["body"]["error"] == "invalid_payload"


def test_make_event_id_is_unique_but_prefixed():
    first = make_event_id("state")
    second = make_event_id("state")

    assert first.startswith("state-")
    assert second.startswith("state-")
    assert first != second
    json.dumps({"event_id": first})
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
pytest tests/test_remote_nats_protocol.py -q
```

Expected: FAIL because `remote_nats_protocol` does not exist.

- [ ] **Step 3: Implement protocol helpers**

Create `remote_nats_protocol.py`:

```python
from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any


DEFAULT_PAIR_ID = "default"


def normalize_pair_id(value: str) -> str:
    raw = str(value or "").strip().lower()
    normalized = re.sub(r"[^a-z0-9_-]+", "-", raw).strip("-_")
    normalized = re.sub(r"[-_]{2,}", "-", normalized)
    return normalized or DEFAULT_PAIR_ID


def stream_name(prefix: str, pair_id: str) -> str:
    safe = normalize_pair_id(pair_id).replace("-", "_")
    return f"{prefix}_{safe}"


@dataclass(frozen=True)
class NatsSubjects:
    pair_id: str
    commands: str
    events: str
    files: str
    acks: str
    command_stream: str
    event_stream: str

    @classmethod
    def from_pair_id(cls, pair_id: str) -> "NatsSubjects":
        normalized = normalize_pair_id(pair_id)
        prefix = f"zgwd.{normalized}"
        return cls(
            pair_id=normalized,
            commands=f"{prefix}.commands",
            events=f"{prefix}.events",
            files=f"{prefix}.files",
            acks=f"{prefix}.acks",
            command_stream=stream_name("ZGWD_COMMANDS", normalized),
            event_stream=stream_name("ZGWD_EVENTS", normalized),
        )


def now_ts() -> float:
    return time.time()


def make_event_id(prefix: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(prefix or "event")).strip("-")
    return f"{clean or 'event'}-{uuid.uuid4().hex}"


def encode_payload(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def decode_payload(data: bytes | str) -> dict[str, Any]:
    try:
        text = data.decode("utf-8") if isinstance(data, bytes) else str(data)
        decoded = json.loads(text)
    except Exception as exc:
        raise ValueError("invalid_json") from exc
    if not isinstance(decoded, dict):
        raise ValueError("invalid_payload")
    return decoded


def build_response_event(
    *,
    request_id: str,
    status: int,
    body: dict[str, Any],
    chat_id: str = "",
) -> dict[str, Any]:
    clean_request_id = str(request_id or "").strip()
    response_chat_id = str(chat_id or body.get("chat_id") or "").strip()
    return {
        "event_id": f"response-{clean_request_id}" if clean_request_id else make_event_id("response"),
        "request_id": clean_request_id,
        "type": "response",
        "ok": int(status) < 400,
        "status": int(status),
        "body": body,
        "chat_id": response_chat_id,
        "created_at": now_ts(),
    }


def build_error_response(request_id: str, status: int, error: str) -> dict[str, Any]:
    return build_response_event(
        request_id=request_id,
        status=status,
        body={"accepted": False, "error": str(error or "error")},
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
pytest tests/test_remote_nats_protocol.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add remote_nats_protocol.py tests/test_remote_nats_protocol.py
git commit -m "feat: add NATS remote protocol helpers"
```

## Task 2: Desktop NATS Runtime Manager

**Files:**
- Create: `c:\code\mc\nats_runtime.py`
- Test: `c:\code\mc\tests\test_nats_runtime.py`
- Modify: `c:\code\mc\requirements.txt`

- [ ] **Step 1: Write failing tests**

Create `tests/test_nats_runtime.py`:

```python
from pathlib import Path

from nats_runtime import NatsRuntimeConfig, NatsServerProcess


def test_runtime_config_writes_jetstream_websocket_and_auth(tmp_path):
    config = NatsRuntimeConfig(
        app_data_dir=tmp_path,
        token="secret",
        host="0.0.0.0",
        port=4222,
        websocket_host="127.0.0.1",
        websocket_port=8081,
    )

    path = config.write()
    text = path.read_text(encoding="utf-8")

    assert "port: 4222" in text
    assert "host: \"0.0.0.0\"" in text
    assert "jetstream" in text
    assert str((tmp_path / "nats" / "jetstream")).replace("\\", "/") in text
    assert "token: \"secret\"" in text
    assert "websocket" in text
    assert "port: 8081" in text
    assert "no_tls: true" in text


def test_runtime_process_uses_env_binary(monkeypatch, tmp_path):
    fake_binary = tmp_path / "nats-server.exe"
    fake_binary.write_text("binary", encoding="utf-8")
    monkeypatch.setenv("ZGWD_NATS_SERVER_PATH", str(fake_binary))

    process = NatsServerProcess(
        config=NatsRuntimeConfig(app_data_dir=tmp_path, token="secret"),
    )

    assert process.server_path == fake_binary
    assert process.build_command()[0] == str(fake_binary)
    assert "-c" in process.build_command()


def test_runtime_process_reports_missing_binary(monkeypatch, tmp_path):
    monkeypatch.delenv("ZGWD_NATS_SERVER_PATH", raising=False)
    process = NatsServerProcess(
        config=NatsRuntimeConfig(app_data_dir=tmp_path, token="secret"),
        bundled_dir=tmp_path / "missing",
    )

    try:
        _ = process.server_path
    except FileNotFoundError as exc:
        assert "nats-server.exe" in str(exc)
    else:
        raise AssertionError("missing binary should fail")
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
pytest tests/test_nats_runtime.py -q
```

Expected: FAIL because `nats_runtime` does not exist.

- [ ] **Step 3: Add dependency**

Modify `requirements.txt` by adding:

```text
nats-py>=2.7,<3.0
```

- [ ] **Step 4: Implement runtime manager**

Create `nats_runtime.py`:

```python
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class NatsRuntimeConfig:
    app_data_dir: Path
    token: str
    host: str = "0.0.0.0"
    port: int = 4222
    websocket_host: str = "127.0.0.1"
    websocket_port: int = 8081

    @property
    def runtime_dir(self) -> Path:
        return Path(self.app_data_dir) / "nats"

    @property
    def store_dir(self) -> Path:
        return self.runtime_dir / "jetstream"

    @property
    def config_path(self) -> Path:
        return self.runtime_dir / "nats-server.conf"

    def write(self) -> Path:
        self.store_dir.mkdir(parents=True, exist_ok=True)
        token = str(self.token or "").replace('"', '\\"')
        store = str(self.store_dir).replace("\\", "/")
        text = f'''port: {int(self.port)}
host: "{self.host}"

jetstream {{
  store_dir: "{store}"
}}

authorization {{
  token: "{token}"
}}

websocket {{
  host: "{self.websocket_host}"
  port: {int(self.websocket_port)}
  no_tls: true
}}
'''
        self.config_path.write_text(text, encoding="utf-8")
        return self.config_path


class NatsServerProcess:
    def __init__(
        self,
        *,
        config: NatsRuntimeConfig,
        bundled_dir: Path | None = None,
    ) -> None:
        self.config = config
        self.bundled_dir = Path(bundled_dir) if bundled_dir is not None else self._default_bundled_dir()
        self._process: subprocess.Popen | None = None

    @property
    def server_path(self) -> Path:
        override = os.environ.get("ZGWD_NATS_SERVER_PATH", "").strip()
        candidates = []
        if override:
            candidates.append(Path(override))
        candidates.append(self.bundled_dir / "nats-server.exe")
        candidates.append(Path.cwd() / "tools" / "nats-server" / "nats-server.exe")
        for candidate in candidates:
            if candidate.exists():
                return candidate
        raise FileNotFoundError("nats-server.exe not found; set ZGWD_NATS_SERVER_PATH or run scripts/download_nats_server.ps1")

    def build_command(self) -> list[str]:
        return [str(self.server_path), "-c", str(self.config.write())]

    def start(self, *, timeout: float = 10.0) -> None:
        if self._process and self._process.poll() is None:
            return
        startupinfo = None
        if sys.platform.startswith("win"):
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        self._process = subprocess.Popen(
            self.build_command(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            startupinfo=startupinfo,
        )
        self.wait_until_ready(timeout=timeout)

    def wait_until_ready(self, *, timeout: float = 10.0) -> None:
        deadline = time.time() + timeout
        last_error: Exception | None = None
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", int(self.config.port)), timeout=0.5):
                    return
            except Exception as exc:
                last_error = exc
                time.sleep(0.1)
        raise RuntimeError(f"NATS server did not become ready: {last_error}")

    def stop(self) -> None:
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()

    @staticmethod
    def _default_bundled_dir() -> Path:
        if getattr(sys, "frozen", False):
            return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "nats-server"
        return Path.cwd() / "tools" / "nats-server"
```

- [ ] **Step 5: Run tests**

Run:

```powershell
pytest tests/test_nats_runtime.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add nats_runtime.py requirements.txt tests/test_nats_runtime.py
git commit -m "feat: manage bundled NATS server runtime"
```

## Task 3: Desktop NATS Transport

**Files:**
- Create: `c:\code\mc\remote_nats.py`
- Test: `c:\code\mc\tests\test_remote_nats_unit.py`

- [ ] **Step 1: Write failing unit tests with a fake JetStream**

Create `tests/test_remote_nats_unit.py`:

```python
import asyncio

from remote_nats import RemoteNatsTransport


class FakeJetStream:
    def __init__(self):
        self.streams = []
        self.published = []

    async def add_stream(self, **kwargs):
        self.streams.append(kwargs)

    async def publish(self, subject, payload):
        self.published.append((subject, payload))


def test_transport_initializes_streams():
    async def run():
        js = FakeJetStream()
        transport = RemoteNatsTransport(pair_id="default", token="secret", jetstream=js)
        await transport.initialize_streams()
        assert js.streams[0]["name"] == "ZGWD_COMMANDS_default"
        assert js.streams[1]["name"] == "ZGWD_EVENTS_default"

    asyncio.run(run())


def test_transport_routes_state_command_and_publishes_response():
    async def run():
        js = FakeJetStream()
        transport = RemoteNatsTransport(
            pair_id="default",
            token="secret",
            jetstream=js,
            on_state=lambda payload: (200, {"accepted": True, "status": "idle", "chat_id": payload.get("chat_id")}),
        )

        await transport.handle_command({"id": "state-1", "type": "state", "chat_id": "c1"})

        assert len(js.published) == 1
        subject, raw = js.published[0]
        assert subject == "zgwd.default.events"
        assert b'"request_id":"state-1"' in raw
        assert b'"status":"idle"' in raw

    asyncio.run(run())


def test_transport_publishes_push_event():
    async def run():
        js = FakeJetStream()
        transport = RemoteNatsTransport(pair_id="default", token="secret", jetstream=js)

        await transport.publish_event({"type": "history_changed", "chat_id": "c1"})

        assert js.published[0][0] == "zgwd.default.events"
        assert b'"event_id":"history_changed-' in js.published[0][1]

    asyncio.run(run())
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
pytest tests/test_remote_nats_unit.py -q
```

Expected: FAIL because `remote_nats` does not exist.

- [ ] **Step 3: Implement transport shell**

Create `remote_nats.py`:

```python
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from remote_nats_protocol import (
    NatsSubjects,
    build_error_response,
    build_response_event,
    encode_payload,
    make_event_id,
)


Callback = Callable[[dict[str, Any]], tuple[int, dict[str, Any]]]


class RemoteNatsTransport:
    def __init__(
        self,
        *,
        pair_id: str,
        token: str,
        jetstream: Any | None = None,
        on_message: Callback | None = None,
        on_new_chat: Callback | None = None,
        on_reply_request: Callback | None = None,
        on_state: Callback | None = None,
        on_rename_chat: Callback | None = None,
        on_update_settings: Callback | None = None,
        on_history_list: Callable[[], tuple[int, dict[str, Any]]] | None = None,
        on_history_read: Callback | None = None,
    ) -> None:
        self.subjects = NatsSubjects.from_pair_id(pair_id)
        self.token = token
        self.jetstream = jetstream
        self.on_message = on_message
        self.on_new_chat = on_new_chat
        self.on_reply_request = on_reply_request
        self.on_state = on_state
        self.on_rename_chat = on_rename_chat
        self.on_update_settings = on_update_settings
        self.on_history_list = on_history_list
        self.on_history_read = on_history_read

    async def initialize_streams(self) -> None:
        js = self.jetstream
        if js is None:
            return
        await js.add_stream(
            name=self.subjects.command_stream,
            subjects=[self.subjects.commands],
            storage="file",
        )
        await js.add_stream(
            name=self.subjects.event_stream,
            subjects=[self.subjects.events, self.subjects.files],
            storage="file",
        )

    async def handle_command(self, payload: dict[str, Any]) -> None:
        request_id = str(payload.get("id") or "").strip()
        message_type = str(payload.get("type") or "").strip()
        try:
            status, body = await asyncio.to_thread(self._route_command, message_type, payload)
            event = build_response_event(
                request_id=request_id,
                status=status,
                body=body,
                chat_id=str(payload.get("chat_id") or body.get("chat_id") or ""),
            )
        except Exception as exc:
            event = build_error_response(request_id, 500, str(exc) or "handler_error")
        await self.publish_event(event)

    async def publish_event(self, payload: dict[str, Any]) -> None:
        event = dict(payload)
        event.setdefault("event_id", make_event_id(str(event.get("type") or "event")))
        js = self.jetstream
        if js is None:
            return
        await js.publish(self.subjects.events, encode_payload(event))

    def _route_command(self, message_type: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if message_type == "message" and self.on_message:
            return self.on_message(payload)
        if message_type == "new_chat" and self.on_new_chat:
            return self.on_new_chat(payload)
        if message_type == "reply_request" and self.on_reply_request:
            return self.on_reply_request(payload)
        if message_type == "state" and self.on_state:
            return self.on_state(payload)
        if message_type == "rename_chat" and self.on_rename_chat:
            return self.on_rename_chat(payload)
        if message_type == "update_settings" and self.on_update_settings:
            return self.on_update_settings(payload)
        if message_type == "history_list" and self.on_history_list:
            return self.on_history_list()
        if message_type == "history_read" and self.on_history_read:
            return self.on_history_read(payload)
        return 404, {"accepted": False, "error": "unknown_type"}
```

- [ ] **Step 4: Run tests**

Run:

```powershell
pytest tests/test_remote_nats_unit.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add remote_nats.py tests/test_remote_nats_unit.py
git commit -m "feat: add desktop NATS remote transport"
```

## Task 4: Wire Desktop NATS Into Main App

**Files:**
- Modify: `c:\code\mc\main.py`
- Test: `c:\code\mc\tests\test_main_unit.py`

- [ ] **Step 1: Add focused tests**

Append tests to `tests/test_main_unit.py`:

```python
def test_remote_state_includes_nats_runtime_status(frame):
    frame.remote_nats_runtime_url = "wss://rc.tingyou.cc/nats"
    frame.remote_nats_runtime_status = {
        "enabled": True,
        "tcp_url": "nats://127.0.0.1:4222",
        "websocket_url": "ws://127.0.0.1:8081/nats",
        "cloudflared_url": "wss://rc.tingyou.cc/nats",
        "last_error": "",
    }

    status, body = frame._remote_api_state_ui({})

    assert status == 200
    assert body["remote_nats_runtime"]["enabled"] is True
    assert body["remote_nats_runtime_url"] == "wss://rc.tingyou.cc/nats"


def test_push_remote_state_also_publishes_nats_event(frame, monkeypatch):
    published = []

    class Transport:
        def publish_event_threadsafe(self, payload):
            published.append(payload)

    frame._remote_nats_transport = Transport()
    frame._remote_ws_server = None
    monkeypatch.setattr(frame, "_remote_api_state_ui", lambda payload: (200, {"chat_id": "c1", "status": "idle"}))

    frame._push_remote_state("c1")

    assert published
    assert published[0]["type"] == "state_changed"
    assert published[0]["chat_id"] == "c1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
pytest tests/test_main_unit.py::test_remote_state_includes_nats_runtime_status tests/test_main_unit.py::test_push_remote_state_also_publishes_nats_event -q
```

Expected: FAIL because `main.py` does not expose NATS runtime status or publish NATS events.

- [ ] **Step 3: Modify `main.py` initialization**

In `ChatFrame.__init__`, near existing `_remote_ws_server` and runtime fields, add:

```python
self._remote_nats_process = None
self._remote_nats_transport = None
self.remote_nats_runtime_url = ""
self.remote_nats_runtime_status = {
    "enabled": False,
    "tcp_url": "",
    "websocket_url": "",
    "cloudflared_url": "",
    "last_error": "",
}
```

- [ ] **Step 4: Include NATS status in state response**

In `_remote_api_state_ui`, add these keys next to existing remote runtime keys:

```python
"remote_nats_runtime": dict(getattr(self, "remote_nats_runtime_status", {}) or {}),
"remote_nats_runtime_url": str(getattr(self, "remote_nats_runtime_url", "") or ""),
```

- [ ] **Step 5: Add a helper to publish NATS events without blocking UI**

Add a method near existing `_push_remote_*` methods:

```python
def _publish_remote_nats_event(self, payload: dict) -> None:
    transport = getattr(self, "_remote_nats_transport", None)
    if transport is None:
        return
    try:
        publish = getattr(transport, "publish_event_threadsafe", None)
        if callable(publish):
            publish(payload)
    except Exception:
        pass
```

- [ ] **Step 6: Call helper from push methods**

In `_push_remote_state`, after building the event payload, call:

```python
self._publish_remote_nats_event(payload)
```

Repeat for `_push_remote_final_answer`, `_push_remote_history_changed`, `_push_remote_status`, and `_push_remote_notes_sync_status` only where the existing payload already matches mobile event handling.

- [ ] **Step 7: Run focused tests**

Run:

```powershell
pytest tests/test_main_unit.py::test_remote_state_includes_nats_runtime_status tests/test_main_unit.py::test_push_remote_state_also_publishes_nats_event -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```powershell
git add main.py tests/test_main_unit.py
git commit -m "feat: expose NATS remote runtime in desktop state"
```

## Task 5: NATS Packaging Helper

**Files:**
- Create: `c:\code\mc\scripts\download_nats_server.ps1`
- Modify: `c:\code\mc\zgwd.spec`
- Modify: `c:\code\mc\ZhugeQA_A11y.spec`

- [ ] **Step 1: Create download helper**

Create `scripts/download_nats_server.ps1`:

```powershell
$ErrorActionPreference = "Stop"

$Version = $env:NATS_SERVER_VERSION
if ([string]::IsNullOrWhiteSpace($Version)) {
  $Version = "v2.12.8"
}

$Root = Split-Path -Parent $PSScriptRoot
$TargetDir = Join-Path $Root "tools\nats-server"
$ZipPath = Join-Path $TargetDir "nats-server.zip"
$Url = "https://github.com/nats-io/nats-server/releases/download/$Version/nats-server-$Version-windows-amd64.zip"

New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null
Invoke-WebRequest -Uri $Url -OutFile $ZipPath
Expand-Archive -Path $ZipPath -DestinationPath $TargetDir -Force

$Exe = Get-ChildItem -Path $TargetDir -Recurse -Filter "nats-server.exe" | Select-Object -First 1
if ($null -eq $Exe) {
  throw "nats-server.exe was not found in downloaded archive"
}
Copy-Item -Path $Exe.FullName -Destination (Join-Path $TargetDir "nats-server.exe") -Force
Remove-Item -Path $ZipPath -Force
Write-Host "Installed $($Exe.FullName) to $TargetDir"
```

- [ ] **Step 2: Modify PyInstaller specs**

In both spec files, add `tools/nats-server/nats-server.exe` to datas when present:

```python
from pathlib import Path

nats_server = Path("tools/nats-server/nats-server.exe")
nats_datas = [(str(nats_server), "nats-server")] if nats_server.exists() else []
```

Then append `+ nats_datas` to the existing `datas` list.

- [ ] **Step 3: Verify syntax**

Run:

```powershell
python -m py_compile nats_runtime.py remote_nats.py remote_nats_protocol.py
```

Expected: no output and exit code 0.

- [ ] **Step 4: Commit**

```powershell
git add scripts/download_nats_server.ps1 zgwd.spec ZhugeQA_A11y.spec
git commit -m "build: package bundled NATS server"
```

## Task 6: Mobile Endpoint Selection And Settings

**Files:**
- Create: `c:\code\rc\lib\remote_transport_selector.dart`
- Modify: `c:\code\rc\lib\remote_control_settings.dart`
- Test: `c:\code\rc\test\remote_transport_selector_test.dart`
- Test: `c:\code\rc\test\remote_control_settings_test.dart`

- [ ] **Step 1: Write failing selector tests**

Create `test/remote_transport_selector_test.dart`:

```dart
import 'package:flutter_test/flutter_test.dart';
import 'package:zhuge_qa/remote_transport_selector.dart';

void main() {
  test('selects nats for nats scheme', () {
    expect(selectRemoteTransport('nats://127.0.0.1:4222'), RemoteTransportKind.nats);
  });

  test('selects nats for cloudflared nats websocket path', () {
    expect(selectRemoteTransport('wss://rc.tingyou.cc/nats'), RemoteTransportKind.nats);
    expect(selectRemoteTransport('ws://127.0.0.1:8081/nats'), RemoteTransportKind.nats);
  });

  test('selects websocket for legacy ws path', () {
    expect(selectRemoteTransport('wss://rc.tingyou.cc/ws'), RemoteTransportKind.websocket);
  });
}
```

- [ ] **Step 2: Add settings test**

Append to `test/remote_control_settings_test.dart`:

```dart
test('settings preserve nats endpoint schemes and paths', () {
  expect(
    RemoteControlSettings.normalizeEndpoint('nats://example.com:4222'),
    'nats://example.com:4222',
  );
  expect(
    RemoteControlSettings.normalizeEndpoint('wss://rc.tingyou.cc/nats'),
    'wss://rc.tingyou.cc/nats',
  );
});
```

- [ ] **Step 3: Run tests to verify they fail**

Run from `c:\code\rc`:

```powershell
flutter test test/remote_transport_selector_test.dart test/remote_control_settings_test.dart
```

Expected: FAIL because selector does not exist and settings do not preserve `nats://`.

- [ ] **Step 4: Implement selector**

Create `lib/remote_transport_selector.dart`:

```dart
enum RemoteTransportKind { websocket, nats }

RemoteTransportKind selectRemoteTransport(String endpoint) {
  final String value = endpoint.trim().toLowerCase();
  if (value.startsWith('nats://')) {
    return RemoteTransportKind.nats;
  }
  final Uri? uri = Uri.tryParse(value);
  if (uri != null && (uri.scheme == 'ws' || uri.scheme == 'wss')) {
    if (uri.path.trim().toLowerCase() == '/nats') {
      return RemoteTransportKind.nats;
    }
  }
  return RemoteTransportKind.websocket;
}
```

- [ ] **Step 5: Modify endpoint normalization**

In `RemoteControlSettings.normalizeEndpoint`, return early for `nats://`:

```dart
if (normalized.toLowerCase().startsWith('nats://')) {
  final Uri uri = Uri.parse(normalized);
  final String port = uri.hasPort && uri.port > 0 ? ':${uri.port}' : '';
  return 'nats://${uri.host}$port';
}
```

Keep the existing WebSocket normalization for `ws://` and `wss://`, but do not force `/nats` to `/ws`.

- [ ] **Step 6: Run tests**

Run:

```powershell
flutter test test/remote_transport_selector_test.dart test/remote_control_settings_test.dart
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add lib/remote_transport_selector.dart lib/remote_control_settings.dart test/remote_transport_selector_test.dart test/remote_control_settings_test.dart
git commit -m "feat: select NATS remote transport on mobile"
```

## Task 7: Mobile NATS Protocol And Client Shell

**Files:**
- Modify: `c:\code\rc\pubspec.yaml`
- Create: `c:\code\rc\lib\remote_nats_protocol.dart`
- Create: `c:\code\rc\lib\remote_nats_client.dart`
- Test: `c:\code\rc\test\remote_nats_protocol_test.dart`

- [ ] **Step 1: Add dependency**

Add to `pubspec.yaml` dependencies:

```yaml
  dart_nats: ^0.6.0
```

Run:

```powershell
flutter pub get
```

Expected: dependency resolves. If the latest compatible version differs, use the version selected by `flutter pub add dart_nats` and keep `pubspec.lock`.

- [ ] **Step 2: Write protocol tests**

Create `test/remote_nats_protocol_test.dart`:

```dart
import 'package:flutter_test/flutter_test.dart';
import 'package:zhuge_qa/remote_nats_protocol.dart';

void main() {
  test('subjects are scoped by pair id', () {
    final RemoteNatsSubjects subjects = RemoteNatsSubjects.fromPairId('Phone 1');
    expect(subjects.pairId, 'phone-1');
    expect(subjects.commands, 'zgwd.phone-1.commands');
    expect(subjects.events, 'zgwd.phone-1.events');
    expect(subjects.files, 'zgwd.phone-1.files');
  });

  test('command payload includes stable fields', () {
    final Map<String, dynamic> payload = buildNatsCommand(
      id: 'state-1',
      type: 'state',
      chatId: 'c1',
      deviceId: 'device-1',
      body: const <String, dynamic>{},
    );
    expect(payload['id'], 'state-1');
    expect(payload['type'], 'state');
    expect(payload['chat_id'], 'c1');
    expect(payload['device_id'], 'device-1');
    expect(payload.containsKey('created_at'), isTrue);
  });
}
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```powershell
flutter test test/remote_nats_protocol_test.dart
```

Expected: FAIL because protocol file does not exist.

- [ ] **Step 4: Implement Dart protocol helpers**

Create `lib/remote_nats_protocol.dart`:

```dart
String normalizeNatsPairId(String value) {
  final String lower = value.trim().toLowerCase();
  final String normalized = lower
      .replaceAll(RegExp(r'[^a-z0-9_-]+'), '-')
      .replaceAll(RegExp(r'[-_]{2,}'), '-')
      .replaceAll(RegExp(r'^[-_]+|[-_]+$'), '');
  return normalized.isEmpty ? 'default' : normalized;
}

class RemoteNatsSubjects {
  const RemoteNatsSubjects({
    required this.pairId,
    required this.commands,
    required this.events,
    required this.files,
  });

  final String pairId;
  final String commands;
  final String events;
  final String files;

  factory RemoteNatsSubjects.fromPairId(String pairId) {
    final String normalized = normalizeNatsPairId(pairId);
    final String prefix = 'zgwd.$normalized';
    return RemoteNatsSubjects(
      pairId: normalized,
      commands: '$prefix.commands',
      events: '$prefix.events',
      files: '$prefix.files',
    );
  }
}

Map<String, dynamic> buildNatsCommand({
  required String id,
  required String type,
  required String chatId,
  required String deviceId,
  required Map<String, dynamic> body,
}) {
  return <String, dynamic>{
    'id': id,
    'type': type,
    if (chatId.trim().isNotEmpty) 'chat_id': chatId.trim(),
    'device_id': deviceId,
    'created_at': DateTime.now().millisecondsSinceEpoch / 1000.0,
    ...body,
  };
}
```

- [ ] **Step 5: Create client shell**

Create `lib/remote_nats_client.dart` with a testable interface first:

```dart
import 'dart:async';
import 'dart:convert';

import 'remote_control_settings.dart';
import 'remote_nats_protocol.dart';

abstract class RemoteNatsConnection {
  Future<void> connect(RemoteControlSettings settings);
  Future<void> publish(String subject, Map<String, dynamic> payload);
  Stream<Map<String, dynamic>> subscribe(String subject, {required String durableName});
  Future<void> close();
}

class RemoteNatsClient {
  RemoteNatsClient({
    required this.settingsStore,
    required this.connection,
    this.pairId = 'default',
    this.deviceId = 'mobile',
  });

  final RemoteControlSettingsStore settingsStore;
  final RemoteNatsConnection connection;
  final String pairId;
  final String deviceId;

  late final RemoteNatsSubjects subjects = RemoteNatsSubjects.fromPairId(pairId);

  Future<void> connect() async {
    final RemoteControlSettings settings = await settingsStore.load();
    await connection.connect(settings);
  }

  Future<void> publishCommand(Map<String, dynamic> payload) async {
    await connection.publish(subjects.commands, payload);
  }

  Stream<Map<String, dynamic>> events() {
    return connection.subscribe(subjects.events, durableName: deviceId);
  }

  String encode(Map<String, dynamic> payload) => jsonEncode(payload);
}
```

- [ ] **Step 6: Run tests**

Run:

```powershell
flutter test test/remote_nats_protocol_test.dart
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add pubspec.yaml pubspec.lock lib/remote_nats_protocol.dart lib/remote_nats_client.dart test/remote_nats_protocol_test.dart
git commit -m "feat: add mobile NATS protocol shell"
```

## Task 8: Mobile NATS Chat Service

**Files:**
- Create: `c:\code\rc\lib\remote_nats_chat_service.dart`
- Test: `c:\code\rc\test\remote_nats_chat_service_test.dart`

- [ ] **Step 1: Write service tests with fake NATS client**

Create `test/remote_nats_chat_service_test.dart`:

```dart
import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:zhuge_qa/remote_control_settings.dart';
import 'package:zhuge_qa/remote_nats_chat_service.dart';
import 'package:zhuge_qa/remote_nats_client.dart';

class FakeSettingsStore extends RemoteControlSettingsStore {
  @override
  Future<RemoteControlSettings> load() async => const RemoteControlSettings(
    endpoint: 'wss://rc.tingyou.cc/nats',
    token: 'secret',
    authMode: RemoteAuthMode.queryToken,
  );
}

class FakeConnection implements RemoteNatsConnection {
  final StreamController<Map<String, dynamic>> controller = StreamController<Map<String, dynamic>>.broadcast();
  final List<Map<String, dynamic>> published = <Map<String, dynamic>>[];

  @override
  Future<void> close() async {}

  @override
  Future<void> connect(RemoteControlSettings settings) async {}

  @override
  Future<void> publish(String subject, Map<String, dynamic> payload) async {
    published.add(<String, dynamic>{'subject': subject, ...payload});
  }

  @override
  Stream<Map<String, dynamic>> subscribe(String subject, {required String durableName}) => controller.stream;
}

void main() {
  test('sendUserMessage publishes message command', () async {
    final FakeConnection connection = FakeConnection();
    final RemoteNatsChatService service = RemoteNatsChatService(
      natsClient: RemoteNatsClient(
        settingsStore: FakeSettingsStore(),
        connection: connection,
        deviceId: 'device-1',
      ),
    );

    await service.initialize();
    await service.sendUserMessage('chat-1', 'hello', model: 'codex/main');

    expect(connection.published.single['subject'], 'zgwd.default.commands');
    expect(connection.published.single['type'], 'message');
    expect(connection.published.single['chat_id'], 'chat-1');
    expect(connection.published.single['text'], 'hello');
  });

  test('response event completes pending state request', () async {
    final FakeConnection connection = FakeConnection();
    final RemoteNatsChatService service = RemoteNatsChatService(
      natsClient: RemoteNatsClient(
        settingsStore: FakeSettingsStore(),
        connection: connection,
        deviceId: 'device-1',
      ),
    );

    await service.initialize();
    final Future<void> pending = service.requestState('chat-1');
    final String requestId = '${connection.published.single['id']}';

    connection.controller.add(<String, dynamic>{
      'type': 'response',
      'request_id': requestId,
      'ok': true,
      'status': 200,
      'body': <String, dynamic>{
        'accepted': true,
        'status': 'idle',
        'chat_id': 'chat-1',
        'turns': <dynamic>[],
      },
      'event_id': 'response-$requestId',
      'chat_id': 'chat-1',
    });

    await pending;
    expect(service.store.snapshotForChat('chat-1').chatId, 'chat-1');
  });
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
flutter test test/remote_nats_chat_service_test.dart
```

Expected: FAIL because service file does not exist.

- [ ] **Step 3: Implement NATS chat service**

Create `lib/remote_nats_chat_service.dart` by mirroring the public methods of `RemoteCodexChatService` and using `RemoteNatsClient.publishCommand`. Include this core structure:

```dart
import 'dart:async';

import 'codex_chat_service.dart';
import 'remote_control_models.dart';
import 'remote_control_settings.dart';
import 'remote_event_deduper.dart';
import 'remote_nats_client.dart';
import 'remote_nats_protocol.dart';
import 'remote_session_store.dart';

class RemoteNatsChatService implements CodexChatService {
  RemoteNatsChatService({
    required this.natsClient,
    RemoteSessionStore? store,
    RemoteEventDeduper? deduper,
  }) : store = store ?? RemoteSessionStore(),
       deduper = deduper ?? RemoteEventDeduper();

  final RemoteNatsClient natsClient;
  final RemoteEventDeduper deduper;

  @override
  final RemoteSessionStore store;

  final Map<String, Completer<Map<String, dynamic>>> _pending = <String, Completer<Map<String, dynamic>>>{};
  StreamSubscription<Map<String, dynamic>>? _subscription;
  int _counter = 0;

  @override
  Future<void> initialize() async {
    if (store.connectionStatus == RemoteConnectionStatus.connected) {
      return;
    }
    store.setConnectionStatus(RemoteConnectionStatus.connecting);
    await natsClient.connect();
    _subscription ??= natsClient.events().listen(_handleEvent);
    store.setConnectionStatus(RemoteConnectionStatus.connected);
  }

  Future<Map<String, dynamic>> _sendRequest(String type, String chatId, Map<String, dynamic> body) async {
    await initialize();
    final String id = '$type-${++_counter}';
    final Completer<Map<String, dynamic>> completer = Completer<Map<String, dynamic>>();
    _pending[id] = completer;
    await natsClient.publishCommand(buildNatsCommand(
      id: id,
      type: type,
      chatId: chatId,
      deviceId: natsClient.deviceId,
      body: body,
    ));
    return completer.future.timeout(const Duration(seconds: 10), onTimeout: () {
      _pending.remove(id);
      throw Exception('远程 NATS 请求超时');
    });
  }

  Future<void> _sendCommand(String type, String chatId, Map<String, dynamic> body) async {
    await initialize();
    final String id = '$type-${++_counter}';
    await natsClient.publishCommand(buildNatsCommand(
      id: id,
      type: type,
      chatId: chatId,
      deviceId: natsClient.deviceId,
      body: body,
    ));
  }

  void _handleEvent(Map<String, dynamic> event) {
    final String type = '${event['type'] ?? ''}';
    final String chatId = '${event['chat_id'] ?? ''}';
    final String eventId = '${event['event_id'] ?? ''}';
    if (!deduper.shouldProcess(eventId, chatId: chatId)) {
      return;
    }
    if (type == 'response') {
      final String requestId = '${event['request_id'] ?? ''}';
      final Map<String, dynamic> body = event['body'] as Map<String, dynamic>? ?? <String, dynamic>{};
      _pending.remove(requestId)?.complete(body);
      if (body.containsKey('status')) {
        store.applySnapshot(RemoteStateSnapshot.fromJson(<String, dynamic>{...body, 'chat_id': chatId.isNotEmpty ? chatId : body['chat_id']}));
      }
      return;
    }
    if (type == 'state_changed') {
      final Map<String, dynamic> body = event['body'] as Map<String, dynamic>? ?? <String, dynamic>{};
      store.applySnapshot(RemoteStateSnapshot.fromJson(<String, dynamic>{...body, 'chat_id': chatId.isNotEmpty ? chatId : body['chat_id']}));
    }
    if (type == 'final_answer') {
      store.applyFinalAnswer(chatId: chatId, eventId: eventId, text: '${event['text'] ?? ''}');
    }
    if (type == 'status') {
      store.applyStatus(chatId: chatId, eventId: eventId, text: '${event['text'] ?? ''}');
    }
    if (type == 'request') {
      store.applyRequest(RemoteRequestPayload.fromJson(event));
    }
  }

  @override
  Future<void> sendUserMessage(String chatId, String text, {String? model}) async {
    await _sendCommand('message', chatId, <String, dynamic>{
      'text': text,
      if (model != null && model.trim().isNotEmpty) 'model': model.trim(),
    });
  }

  @override
  Future<void> requestState(String chatId) async {
    final Map<String, dynamic> body = await _sendRequest('state', chatId, const <String, dynamic>{});
    store.applySnapshot(RemoteStateSnapshot.fromJson(<String, dynamic>{...body, 'chat_id': chatId}));
  }

  @override
  Future<RemoteControlSettings> loadSettings() => natsClient.settingsStore.load();

  @override
  Future<void> dispose() async {
    await _subscription?.cancel();
    await natsClient.connection.close();
  }

  @override
  Future<void> refreshHistory() async {}

  @override
  Future<RemoteHistoryChat?> loadChat(String chatId) async => null;

  @override
  Future<String> startNewChat({String? model}) async {
    final Map<String, dynamic> body = await _sendRequest('new_chat', '', <String, dynamic>{
      if (model != null && model.trim().isNotEmpty) 'model': model.trim(),
    });
    return '${body['chat_id'] ?? ''}';
  }

  @override
  Future<void> replyRequest(String chatId, String text) => _sendCommand('reply_request', chatId, <String, dynamic>{'text': text});

  @override
  Future<void> renameChat(String chatId, String title) => _sendCommand('rename_chat', chatId, <String, dynamic>{'title': title});

  @override
  Future<void> updateSettings({bool? codexAnswerEnglishFilterEnabled}) => _sendCommand('update_settings', '', <String, dynamic>{
    if (codexAnswerEnglishFilterEnabled != null) 'codex_answer_english_filter_enabled': codexAnswerEnglishFilterEnabled,
  });
}
```

After this passes, replace the empty `refreshHistory` and `loadChat` methods with request/response handling matching `RemoteCodexChatService`.

- [ ] **Step 4: Run tests**

Run:

```powershell
flutter test test/remote_nats_chat_service_test.dart
```

Expected: PASS after filling required interface methods.

- [ ] **Step 5: Commit**

```powershell
git add lib/remote_nats_chat_service.dart test/remote_nats_chat_service_test.dart
git commit -m "feat: add mobile NATS chat service"
```

## Task 9: Verification And Smoke Test

**Files:**
- Verify: `c:\code\mc\remote_nats_protocol.py`
- Verify: `c:\code\mc\nats_runtime.py`
- Verify: `c:\code\mc\remote_nats.py`
- Verify: `c:\code\rc\lib\remote_transport_selector.dart`
- Verify: `c:\code\rc\lib\remote_nats_protocol.dart`
- Verify: `c:\code\rc\lib\remote_nats_client.dart`
- Verify: `c:\code\rc\lib\remote_nats_chat_service.dart`

- [ ] **Step 1: Run desktop unit tests**

Run from `c:\code\mc`:

```powershell
pytest tests/test_remote_nats_protocol.py tests/test_nats_runtime.py tests/test_remote_nats_unit.py tests/test_main_unit.py::test_remote_state_includes_nats_runtime_status tests/test_main_unit.py::test_push_remote_state_also_publishes_nats_event -q
```

Expected: PASS.

- [ ] **Step 2: Run mobile unit tests**

Run from `c:\code\rc`:

```powershell
flutter test test/remote_transport_selector_test.dart test/remote_nats_protocol_test.dart test/remote_nats_chat_service_test.dart test/remote_control_settings_test.dart
```

Expected: PASS.

- [ ] **Step 3: Run dependency checks**

Run:

```powershell
python -m py_compile remote_nats_protocol.py nats_runtime.py remote_nats.py
```

Expected: PASS.

Run from `c:\code\rc`:

```powershell
flutter analyze
```

Expected: no new NATS-related analyzer errors.

- [ ] **Step 4: Commit final fixes**

```powershell
git status --short
git add remote_nats_protocol.py nats_runtime.py remote_nats.py tests/test_remote_nats_protocol.py tests/test_nats_runtime.py tests/test_remote_nats_unit.py tests/test_main_unit.py requirements.txt zgwd.spec ZhugeQA_A11y.spec scripts/download_nats_server.ps1
git commit -m "test: verify NATS remote sync path"
```

Use the final commit only if verification required small fixes after earlier feature commits.

## Self-Review Notes

- Spec coverage: runtime manager, JetStream stream setup, NATS WebSocket/cloudflared path, mobile endpoint selection, command/response payloads, event publishing, and fallback are covered.
- First implementation intentionally keeps Android background WebSocket behavior until foreground NATS is passing; background NATS can be a follow-up task after the Dart service is stable.
- The mobile plan uses `dart_nats` because current pub.dev package information shows it supports Flutter, WebSocket, reconnect, request/respond, and auth. JetStream behavior is encapsulated in project code so the dependency can be swapped if its API is insufficient.
