"""Unit tests for kimi_server_client (test plan section A, items 1-26).

No real process, socket server, or network: the client is driven through its
injected process_factory / http_session_factory / ws_factory hooks with fakes
that mimic subprocess.Popen, requests.Session, and websocket-client.
"""

import json
import queue
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

import kimi_server_client
from kimi_server_client import (
    KimiEvent,
    KimiServerClient,
    KimiServerError,
    parse_token_from_banner,
    pick_free_port,
    resolve_kimi_launch_command,
)


# ----------------------------------------------------------------------
# fakes


class FakeResponse:
    """Mimics the requests.Response surface the client uses."""

    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no JSON payload")
        return self._payload


class FakeHttpSession:
    """Mimics requests.Session's .get/.request surface used by the client."""

    def __init__(self, *, health_failures=0, health_ok=True):
        self.calls = []
        self.routes = {}
        self.health_failures = health_failures
        self.health_ok = health_ok
        self.before_health_ok = None

    def get(self, url, headers=None, timeout=None):
        self.calls.append({"method": "GET", "url": url, "json": None, "headers": headers, "timeout": timeout})
        if "/healthz" in url:
            if not self.health_ok:
                raise ConnectionError("connection refused")
            if self.health_failures > 0:
                self.health_failures -= 1
                raise ConnectionError("connection refused")
            if self.before_health_ok is not None:
                self.before_health_ok()
            return FakeResponse(200, {"status": "ok"})
        return self._route("GET", url)

    def request(self, method, url, json=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "json": json, "headers": headers, "timeout": timeout})
        return self._route(method, url)

    def _route(self, method, url):
        path = url[url.find("/api"):]
        handler = self.routes.get((method, path))
        if handler is None:
            return FakeResponse(200, {"code": 0, "msg": "success", "data": {}})
        return handler


class ControlledStdout:
    """Line stream for the banner thread; None in the queue marks EOF."""

    def __init__(self, lines=(), *, eof=True):
        self._queue = queue.Queue()
        for line in lines:
            self._queue.put(line)
        if eof:
            self._queue.put(None)

    def push(self, line):
        self._queue.put(line)

    def close(self):
        self._queue.put(None)

    def __iter__(self):
        while True:
            item = self._queue.get()
            if item is None:
                return
            yield item


class FakeProcess:
    def __init__(self, stdout=None):
        self.stdout = stdout if stdout is not None else ControlledStdout()
        self.returncode = None
        self.terminated = False
        self.killed = False
        self.wait_calls = []

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.killed = True
        self.returncode = -9


class GracefulProcess(FakeProcess):
    """Exits cleanly on the first wait after the shutdown POST."""

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        self.returncode = 0
        return self.returncode


class HangingProcess(FakeProcess):
    """Never exits on its own: every wait times out until kill."""

    def __init__(self, stdout=None):
        super().__init__(stdout)
        self.calls = []

    def wait(self, timeout=None):
        self.calls.append(("wait", timeout))
        raise subprocess.TimeoutExpired("kimi", timeout)

    def terminate(self):
        self.calls.append(("terminate", None))

    def kill(self):
        self.calls.append(("kill", None))
        self.returncode = -9


class FakeWebSocket:
    """Mimics the websocket-client send/recv/close surface."""

    def __init__(self, inbound=(), recv_error=None, send_results=()):
        self.sent = []
        self._inbound = list(inbound)
        self._recv_error = recv_error
        self._send_results = list(send_results)
        self.closed = False
        self.recv_calls = 0

    def send(self, line):
        if self._send_results:
            result = self._send_results.pop(0)
            if isinstance(result, BaseException):
                raise result
        self.sent.append(line)

    def recv(self):
        self.recv_calls += 1
        if self._inbound:
            return self._inbound.pop(0)
        if self._recv_error is not None:
            raise self._recv_error
        raise ConnectionError("websocket closed")

    def close(self):
        self.closed = True


class BlockingWebSocket(FakeWebSocket):
    """Socket whose reader stays alive until a test feeds or closes it."""

    def __init__(self, *, send_results=()):
        super().__init__(send_results=send_results)
        self.recv_started = threading.Event()
        self._recv_queue = queue.Queue()

    def recv(self):
        self.recv_calls += 1
        self.recv_started.set()
        result = self._recv_queue.get(timeout=5)
        if isinstance(result, BaseException):
            raise result
        return result

    def feed(self, result):
        self._recv_queue.put(result)

    def close(self):
        if not self.closed:
            self.closed = True
            self.feed(ConnectionError("websocket closed"))


def make_client(proc=None, http=None, ws=None, **kwargs):
    """Build a client wired to fakes; kwargs override constructor defaults."""
    proc = proc if proc is not None else FakeProcess()
    http = http if http is not None else FakeHttpSession()
    ws = ws if ws is not None else FakeWebSocket()
    ws_calls = []

    def ws_factory(url, headers):
        ws_calls.append((url, headers))
        return ws

    kwargs.setdefault("launch_command", ["/fake/kimi"])
    kwargs.setdefault("token", "test-token")
    kwargs.setdefault("start_reader_thread", False)
    client = KimiServerClient(
        process_factory=lambda args: proc,
        http_session_factory=lambda: http,
        ws_factory=ws_factory,
        **kwargs,
    )
    return client, proc, http, ws, ws_calls


def started_client(**kwargs):
    client, proc, http, ws, ws_calls = make_client(**kwargs)
    client.start()
    return client, proc, http, ws, ws_calls


def sent_ws_messages(ws):
    return [json.loads(line) for line in ws.sent]


def _delta(text, *, thread="s", turn="t", item="i", kind="assistant", data=None):
    return KimiEvent(
        type="agent_message_delta",
        thread_id=thread,
        turn_id=turn,
        item_id=item,
        display_kind=kind,
        text=text,
        raw_text=text,
        data=dict(data or {}),
    )


# ----------------------------------------------------------------------
# A1-A2: launch command resolution


def test_resolve_launch_command_env_override(monkeypatch, tmp_path):
    kimi_bin = tmp_path / "kimi.exe"
    kimi_bin.write_text("", encoding="utf-8")
    monkeypatch.setenv("KIMI_BIN", str(kimi_bin))
    monkeypatch.setattr(kimi_server_client.shutil, "which", lambda name: r"C:\elsewhere\kimi.exe")

    assert resolve_kimi_launch_command() == [str(kimi_bin)]


def test_resolve_launch_command_env_override_missing_file(monkeypatch, tmp_path):
    monkeypatch.setenv("KIMI_BIN", str(tmp_path / "no-such-kimi.exe"))
    monkeypatch.setattr(kimi_server_client.shutil, "which", lambda name: r"C:\elsewhere\kimi.exe")

    with pytest.raises(KimiServerError):
        resolve_kimi_launch_command()


def test_resolve_launch_command_path_fallback(monkeypatch):
    monkeypatch.delenv("KIMI_BIN", raising=False)
    monkeypatch.setattr(kimi_server_client.shutil, "which", lambda name: "/usr/bin/kimi")

    assert resolve_kimi_launch_command() == ["/usr/bin/kimi"]


def test_resolve_launch_command_user_install_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv("KIMI_BIN", raising=False)
    monkeypatch.setattr(kimi_server_client.shutil, "which", lambda name: None)
    monkeypatch.setattr(kimi_server_client.os, "name", "nt")
    candidate = tmp_path / ".kimi-code" / "bin" / "kimi.exe"
    candidate.parent.mkdir(parents=True)
    candidate.write_text("", encoding="utf-8")
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    assert resolve_kimi_launch_command() == [str(candidate)]


def test_resolve_launch_command_not_found(monkeypatch, tmp_path):
    monkeypatch.delenv("KIMI_BIN", raising=False)
    monkeypatch.setattr(kimi_server_client.shutil, "which", lambda name: None)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    with pytest.raises(KimiServerError):
        resolve_kimi_launch_command()


# ----------------------------------------------------------------------
# A3: free port


def test_pick_free_port_returns_bindable_port():
    port = pick_free_port()
    assert isinstance(port, int) and port > 0
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", port))


# ----------------------------------------------------------------------
# A4: spawn args


def test_start_spawns_server_with_expected_args():
    spawned = []
    proc = FakeProcess()
    client = KimiServerClient(
        process_factory=lambda args: spawned.append(list(args)) or proc,
        http_session_factory=lambda: FakeHttpSession(),
        ws_factory=lambda url, headers: FakeWebSocket(),
        launch_command=["/fake/kimi"],
        token="t",
        port=51234,
        start_reader_thread=False,
    )

    client.start()

    assert spawned == [["/fake/kimi", "web", "--no-open", "--port", "51234"]]
    assert client.base_url == "http://127.0.0.1:51234"


def test_start_picks_free_port_when_unspecified():
    spawned = []
    proc = FakeProcess()
    client = KimiServerClient(
        process_factory=lambda args: spawned.append(list(args)) or proc,
        http_session_factory=lambda: FakeHttpSession(),
        ws_factory=lambda url, headers: FakeWebSocket(),
        launch_command=["/fake/kimi"],
        token="t",
        start_reader_thread=False,
    )

    client.start()

    args = spawned[0]
    port = int(args[args.index("--port") + 1])
    assert port > 0
    assert client.base_url == f"http://127.0.0.1:{port}"
    assert "127.0.0.1" in client.base_url


# ----------------------------------------------------------------------
# A5: health waiting


def test_start_waits_for_healthz():
    http = FakeHttpSession(health_failures=2)
    client, proc, http, ws, _ = started_client(http=http)

    health_calls = [c for c in http.calls if "/healthz" in c["url"]]
    assert len(health_calls) == 3
    assert client.process is proc


def test_start_health_timeout_raises_and_kills_process():
    http = FakeHttpSession(health_ok=False)
    client, proc, http, ws, _ = make_client(http=http, health_timeout=0.7)

    with pytest.raises(KimiServerError, match="timed out"):
        client.start()

    assert proc.terminated or proc.killed


# ----------------------------------------------------------------------
# A6-A7: token resolution


def test_parse_token_from_banner_variants():
    assert parse_token_from_banner("server ready. Token: abc123 trailing") == "abc123"
    assert parse_token_from_banner("http://127.0.0.1:3000/#token=tok-456") == "tok-456"
    assert parse_token_from_banner("no token here") == ""


def test_token_from_startup_banner(monkeypatch, tmp_path):
    # server.token exists too; the banner token must win.
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path))
    (tmp_path / "server.token").write_text("file-token", encoding="utf-8")
    stream = ControlledStdout(["kimi web listening on http://127.0.0.1:9999  Token: banner-token"])
    proc = FakeProcess(stdout=stream)
    http = FakeHttpSession()
    client, proc, http, ws, _ = make_client(proc=proc, http=http, token=None)
    http.before_health_ok = lambda: client._banner_thread.join(timeout=5)

    client.start()

    assert client.token == "banner-token"


def test_token_fallback_server_token_file(monkeypatch, tmp_path):
    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path))
    (tmp_path / "server.token").write_text("file-token\n", encoding="utf-8")
    proc = FakeProcess(stdout=ControlledStdout(["some banner line without token"]))
    http = FakeHttpSession()
    client, proc, http, ws, _ = make_client(proc=proc, http=http, token=None)
    http.before_health_ok = lambda: client._banner_thread.join(timeout=5)

    client.start()

    assert client.token == "file-token"


# ----------------------------------------------------------------------
# A8: bearer header


def test_rest_calls_send_bearer_header():
    client, _, http, _, _ = started_client(token="secret-token")

    client.get_status("session-1")

    assert http.calls
    for call in http.calls:
        assert call["headers"].get("Authorization") == "Bearer secret-token"


# ----------------------------------------------------------------------
# A9-A10: close escalation and idempotence


def test_close_sends_shutdown_and_waits_gracefully():
    proc = GracefulProcess()
    client, _, http, ws, _ = started_client(proc=proc)

    client.close()

    shutdowns = [c for c in http.calls if c["url"].endswith("/api/v1/shutdown")]
    assert len(shutdowns) == 1
    assert shutdowns[0]["method"] == "POST"
    assert proc.wait_calls == [kimi_server_client.DEFAULT_SHUTDOWN_TIMEOUT]
    assert proc.terminated is False
    assert proc.killed is False
    assert ws.closed is True


def test_close_escalates_to_terminate_and_kill_when_shutdown_stalls():
    proc = HangingProcess()
    client, _, http, _, _ = started_client(proc=proc)

    client.close()

    shutdowns = [c for c in http.calls if c["url"].endswith("/api/v1/shutdown")]
    assert len(shutdowns) == 1
    kinds = [name for name, _ in proc.calls]
    assert kinds == ["wait", "terminate", "wait", "kill", "wait"]


def test_close_escalates_when_shutdown_request_fails():
    proc = HangingProcess()
    http = FakeHttpSession()
    http.routes[("POST", "/api/v1/shutdown")] = FakeResponse(500, text="shutdown unsupported")
    client, _, http, _, _ = started_client(proc=proc, http=http)

    client.close()

    kinds = [name for name, _ in proc.calls]
    assert kinds == ["wait", "terminate", "wait", "kill", "wait"]


def test_close_idempotent():
    proc = GracefulProcess()
    client, _, http, ws, _ = started_client(proc=proc)

    client.close()
    client.close()
    client.close()

    shutdowns = [c for c in http.calls if c["url"].endswith("/api/v1/shutdown")]
    assert len(shutdowns) == 1
    assert proc.wait_calls == [kimi_server_client.DEFAULT_SHUTDOWN_TIMEOUT]


# ----------------------------------------------------------------------
# A11-A15: REST wrappers


def test_create_session_body():
    client, _, http, ws, _ = started_client()
    http.routes[("POST", "/api/v1/sessions")] = FakeResponse(
        200, {"code": 0, "msg": "success", "data": {"id": "session-1"}}
    )

    session_id = client.create_session(cwd="D:/work/proj", model="kimi/k3", permission_mode="auto")

    assert session_id == "session-1"
    creates = [c for c in http.calls if c["method"] == "POST" and c["url"].endswith("/api/v1/sessions")]
    assert len(creates) == 1
    body = creates[0]["json"]
    assert body["metadata"] == {"cwd": "D:/work/proj"}
    assert body["agent_config"] == {"permission_mode": "auto", "model": "kimi-code/k3"}
    assert body["title"] == "kimi chat"
    profiles = [c for c in http.calls if c["url"].endswith("/api/v1/sessions/session-1/profile")]
    assert profiles
    assert profiles[0]["json"] == {"agent_config": {"permission_mode": "auto", "model": "kimi-code/k3"}}
    subscribes = [m for m in sent_ws_messages(ws) if m["type"] == "subscribe"]
    assert subscribes[-1]["payload"]["session_ids"] == ["session-1"]


def test_create_session_keeps_created_id_when_first_subscribe_send_fails(monkeypatch):
    client, _, http, _, _ = started_client()
    http.routes[("POST", "/api/v1/sessions")] = FakeResponse(
        200, {"code": 0, "data": {"id": "session-created"}}
    )

    def failed_subscribe(session_ids):
        client._subscribed_sessions.update(session_ids)
        raise KimiServerError("temporary websocket send failure")

    monkeypatch.setattr(client, "subscribe", failed_subscribe)

    assert client.create_session(cwd="D:/work") == "session-created"
    assert client._subscribed_sessions == {"session-created"}


def test_submit_prompt_content_blocks():
    client, _, http, _, _ = started_client()
    http.routes[("POST", "/api/v1/sessions/session-1/prompts")] = FakeResponse(
        200, {"code": 0, "data": {"prompt_id": "p-1"}}
    )
    blocks = [
        {"type": "text", "text": "describe this"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "aGVsbG8="}},
    ]

    prompt_id = client.submit_prompt("session-1", blocks)

    assert prompt_id == "p-1"
    call = [c for c in http.calls if c["url"].endswith("/api/v1/sessions/session-1/prompts")][-1]
    assert call["method"] == "POST"
    assert call["json"] == {"content": blocks}


@pytest.mark.parametrize("data", [{}, {"unrelated": "value"}])
def test_submit_prompt_requires_authoritative_prompt_handle(data):
    client, _, http, _, _ = started_client()
    http.routes[("POST", "/api/v1/sessions/session-1/prompts")] = FakeResponse(
        200, {"code": 0, "data": data}
    )

    with pytest.raises(KimiServerError, match="missing prompt_id/user_message_id"):
        client.submit_prompt("session-1", [{"type": "text", "text": "hello"}])


def test_steer_prompts_body():
    client, _, http, _, _ = started_client()

    assert client.steer_prompts("session-1", ["p1", "p2"]) is True

    call = [c for c in http.calls if "prompts:steer" in c["url"]][-1]
    assert call["method"] == "POST"
    assert call["json"] == {"prompt_ids": ["p1", "p2"]}


def test_steer_prompts_returns_false_on_server_error():
    client, _, http, _, _ = started_client()
    http.routes[("POST", "/api/v1/sessions/session-1/prompts:steer")] = FakeResponse(
        200, {"code": 40402, "msg": "one or more prompts are not pending", "data": None}
    )

    assert client.steer_prompts("session-1", ["p1"]) is False


def test_steer_prompts_returns_unknown_for_ambiguous_server_failure():
    client, _, http, _, _ = started_client()
    http.routes[("POST", "/api/v1/sessions/session-1/prompts:steer")] = FakeResponse(
        503, text="upstream timed out after accepting request"
    )

    assert client.steer_prompts("session-1", ["p1"]) is None


def test_rest_error_raises_with_context():
    client, _, http, _, _ = started_client()
    http.routes[("GET", "/api/v1/sessions/session-1/status")] = FakeResponse(500, text="boom internal")

    with pytest.raises(KimiServerError) as excinfo:
        client.get_status("session-1")

    message = str(excinfo.value)
    assert "500" in message
    assert "boom internal" in message
    assert "/api/v1/sessions/session-1/status" in message


def test_session_exists_only_converts_explicit_404_to_false():
    client, _, http, _, _ = started_client()
    http.routes[("GET", "/api/v1/sessions/missing")] = FakeResponse(404, text="not found")
    http.routes[("GET", "/api/v1/sessions/broken")] = FakeResponse(503, text="offline")

    assert client.session_exists("missing") is False
    with pytest.raises(KimiServerError, match="503"):
        client.session_exists("broken")


def test_answer_approval_and_question_bodies():
    client, _, http, _, _ = started_client()

    client.answer_approval("session-1", "ap-1", "approve")
    call = [c for c in http.calls if c["url"].endswith("/approvals/ap-1")][-1]
    assert call["method"] == "POST"
    assert call["json"] == {"decision": "approve"}

    client.answer_approval("session-1", "ap-2", "decline", feedback="no", selected_label="Never")
    call = [c for c in http.calls if c["url"].endswith("/approvals/ap-2")][-1]
    assert call["json"] == {"decision": "decline", "feedback": "no", "selected_label": "Never"}


# ----------------------------------------------------------------------
# A16-A17: websocket hello/subscribe and event intake


def test_ws_hello_and_subscribe_sent():
    client, _, http, ws, ws_calls = started_client(port=51235, token="tok-1")

    assert ws_calls[0][0] == "ws://127.0.0.1:51235/api/v1/ws"
    assert ws_calls[0][1] == ["Authorization: Bearer tok-1"]
    hello = sent_ws_messages(ws)[0]
    assert hello["type"] == "client_hello"
    assert hello["payload"]["client_id"] == "zgwd"

    http.routes[("POST", "/api/v1/sessions")] = FakeResponse(200, {"code": 0, "data": {"id": "session-9"}})
    client.create_session(cwd="D:/work")

    subscribes = [m for m in sent_ws_messages(ws) if m["type"] == "subscribe"]
    assert subscribes[0]["payload"]["session_ids"] == ["session-9"]


def test_cursor_is_sent_on_reconnect_and_is_monotonic_per_session():
    old_ws = FakeWebSocket()
    new_ws = FakeWebSocket()
    sockets = iter((old_ws, new_ws))
    client, *_ = make_client(ws=old_ws)
    client.ws_factory = lambda _url, _headers: next(sockets)
    client.start()
    client._handle_ws_message(
        {"type": "turn.started", "session_id": "s1", "epoch": "e1", "seq": 4, "payload": {"type": "turn.started", "turnId": 1}}
    )
    client._handle_ws_message(
        {"type": "turn.started", "session_id": "s2", "epoch": "e2", "seq": 9, "payload": {"type": "turn.started", "turnId": 2}}
    )
    assert client._invalidate_ws(old_ws, "drop", notify=False)

    client.start()

    hello = sent_ws_messages(new_ws)[0]
    assert hello["payload"]["cursors"] == {
        "s1": {"epoch": "e1", "seq": 4},
        "s2": {"epoch": "e2", "seq": 9},
    }


def test_old_stable_replay_is_dropped_but_same_seq_volatile_offsets_survive():
    client, *_ = started_client()
    stable = lambda seq: {
        "type": "turn.started", "session_id": "s", "epoch": "e", "seq": seq,
        "payload": {"type": "turn.started", "turnId": seq},
    }
    assert client._handle_ws_message(stable(5))
    assert client._handle_ws_message(stable(4))
    for offset, text in ((0, "A"), (1, "B")):
        client._handle_ws_message(
            {
                "type": "assistant.delta", "session_id": "s", "epoch": "e", "seq": 5, "offset": offset,
                "payload": {"type": "assistant.delta", "turnId": 5, "delta": text, "agentId": "main"},
            }
        )

    events = [message["payload"]["event"] for message in client.drain_pending_messages(10)]
    assert [event["type"] for event in events] == ["turn_started", "agent_message_delta"]
    assert events[-1]["text"] == "AB"


def test_exact_volatile_offset_replay_is_dropped_across_queue_drains():
    client, *_ = started_client()
    delta = {
        "type": "assistant.delta",
        "session_id": "s",
        "epoch": "e",
        "seq": 5,
        "offset": 0,
        "payload": {
            "type": "assistant.delta",
            "turnId": 5,
            "messageId": "answer-1",
            "delta": "A",
            "agentId": "main",
        },
    }

    assert client._handle_ws_message(delta)
    assert len(client.drain_pending_messages(10)) == 1
    assert client._handle_ws_message(delta)
    assert client.drain_pending_messages(10) == []

    next_offset = dict(delta, offset=1)
    next_offset["payload"] = dict(delta["payload"], delta="B")
    assert client._handle_ws_message(next_offset)
    event = client.drain_pending_messages(10)[0]["payload"]["event"]
    assert event["text"] == "B"


def test_ack_resync_required_emits_explicit_control_message():
    client, *_ = started_client()

    client._handle_ws_message(
        {
            "type": "ack",
            "payload": {
                "resync_required": ["s1"],
                "cursors": {"s1": {"epoch": "e1", "seq": 8}},
            },
        }
    )

    assert client.drain_pending_messages(10) == [
        {"type": "resync_required", "payload": {"session_ids": ["s1"]}}
    ]


def test_subscribe_ack_rejection_invalidates_socket_and_enters_recovery(monkeypatch):
    client, _, _, ws, _ = started_client()
    recoveries = []
    monkeypatch.setattr(client, "_request_recovery", recoveries.append)
    client.subscribe(["s1"])
    subscribe = [message for message in sent_ws_messages(ws) if message["type"] == "subscribe"][-1]

    assert not client._handle_ws_message(
        {
            "type": "ack",
            "id": subscribe["id"],
            "payload": {"code": 0, "accepted": [], "not_found": ["s1"]},
        },
        ws=ws,
        generation=client._ws_generation,
    )

    assert client._ws is None
    assert recoveries and "subscribe rejected" in recoveries[0]


def test_session_event_dispatched_as_kimi_event():
    client, *_ = started_client()
    message = {
        "type": "assistant.delta",
        "seq": 8,
        "session_id": "session-abc",
        "payload": {"type": "assistant.delta", "turnId": 4, "delta": "你好", "messageId": "msg-1"},
    }

    client._handle_ws_message(message)

    drained = client.drain_pending_messages(limit=10)
    assert len(drained) == 1
    assert drained[0]["type"] == "event"
    event = drained[0]["payload"]["event"]
    assert event["type"] == "agent_message_delta"
    assert event["thread_id"] == "session-abc"
    assert event["turn_id"] == "4"
    assert event["item_id"] == "msg-1"
    assert event["text"] == "你好"


def test_ws_ping_replies_with_exact_nonce_without_ui_event():
    client, _, _, ws, _ = started_client()

    handled = client._handle_ws_message(
        {"type": "ping", "payload": {"nonce": 731}},
        ws=ws,
    )

    assert handled is True
    assert sent_ws_messages(ws)[-1] == {"type": "pong", "payload": {"nonce": 731}}
    assert client.drain_pending_messages(limit=10) == []


def test_repeated_ws_ping_cycles_never_enter_ui_queue():
    client, _, _, ws, _ = started_client()

    for nonce in ("n-1", "n-2", "n-3"):
        assert client._handle_ws_message(
            {"type": "ping", "payload": {"nonce": nonce}},
            ws=ws,
        ) is True

    pongs = [message for message in sent_ws_messages(ws) if message["type"] == "pong"]
    assert [message["payload"]["nonce"] for message in pongs] == ["n-1", "n-2", "n-3"]
    assert client.drain_pending_messages(limit=10) == []


def test_ws_ping_send_failure_marks_only_that_socket_stale():
    ws = FakeWebSocket(send_results=[None, ConnectionError("pong failed")])
    client, *_ = started_client(ws=ws)

    handled = client._handle_ws_message(
        {"type": "ping", "payload": {"nonce": 9}},
        ws=ws,
    )

    assert handled is False
    assert client._ws is None
    drained = client.drain_pending_messages(limit=10)
    assert [message["type"] for message in drained] == ["transport_error"]
    assert "pong failed" in drained[0]["payload"]["error"]


# ----------------------------------------------------------------------
# A18-A19: delta coalescing


def test_delta_coalescing_merges_consecutive_deltas():
    observed = []
    client, *_ = started_client(on_message=observed.append)

    for idx in range(20):
        client._enqueue_event(_delta(f"d{idx}-"))

    drained = client.drain_pending_messages(limit=100)
    assert len(drained) == 1
    event = drained[0]["payload"]["event"]
    expected = "".join(f"d{idx}-" for idx in range(20))
    assert event["text"] == expected
    assert event["raw_text"] == expected
    assert event["text"].index("d0-") < event["text"].index("d19-")
    assert observed == [{"type": "messages_pending"}]


def test_delta_not_merged_across_items_or_sessions():
    client, *_ = started_client()

    client._enqueue_event(_delta("a", item="i1"))
    client._enqueue_event(_delta("b", item="i2"))
    client._enqueue_event(_delta("c", thread="s2", item="i2"))
    client._enqueue_event(_delta("d", thread="s2", item="i2"))
    client._enqueue_event(_delta("e", thread="s2", item="i2", kind="thinking"))
    client._enqueue_event(_delta("f", thread="s2", item="i2", kind="thinking"))

    drained = client.drain_pending_messages(limit=100)
    texts = [m["payload"]["event"]["text"] for m in drained]
    assert texts == ["a", "b", "cd", "ef"]


def test_delta_not_merged_across_kimi_agent_or_source_identity():
    client, *_ = started_client()

    client._enqueue_event(_delta("think-a", item="", kind="thinking", data={"agent_id": "a", "source_kind": "thinking.delta"}))
    client._enqueue_event(_delta("think-b", item="", kind="thinking", data={"agent_id": "b", "source_kind": "thinking.delta"}))
    client._enqueue_event(_delta("tool-a", item="", kind="commentary", data={"agent_id": "a", "source_kind": "tool.progress"}))

    drained = client.drain_pending_messages(limit=10)
    assert [message["payload"]["event"]["text"] for message in drained] == ["think-a", "think-b", "tool-a"]


def test_delta_not_merged_across_non_delta_entries():
    client, *_ = started_client()

    client._enqueue_event(_delta("A"))
    client._enqueue_event(KimiEvent(type="item_started", text="step"))
    client._enqueue_event(_delta("B"))

    drained = client.drain_pending_messages(limit=10)
    assert [m["payload"]["event"]["text"] for m in drained] == ["A", "step", "B"]


def test_delta_coalescing_preserves_spaces_and_ignores_offset_replay():
    client, *_ = started_client()

    client._enqueue_event(_delta("The", kind="thinking", data={"offset": 0}))
    client._enqueue_event(_delta(" user", kind="thinking", data={"offset": 3}))
    client._enqueue_event(_delta(" user", kind="thinking", data={"offset": 3}))
    client._enqueue_event(_delta("The", kind="thinking", data={"offset": 0}))

    drained = client.drain_pending_messages(limit=10)
    assert len(drained) == 1
    assert drained[0]["payload"]["event"]["text"] == "The user"


def test_delta_coalescing_preserves_offset_gap_for_owner_completeness_check():
    client, *_ = started_client()

    client._enqueue_event(_delta("abc", kind="assistant", data={"offset": 0}))
    client._enqueue_event(_delta("F", kind="assistant", data={"offset": 5}))

    drained = client.drain_pending_messages(limit=10)
    assert [message["payload"]["event"]["text"] for message in drained] == ["abc", "F"]
    assert [message["payload"]["event"]["data"]["offset"] for message in drained] == [0, 5]


# ----------------------------------------------------------------------
# A20-A21: notification coalescing and queue cap


def test_messages_pending_notification_coalesced():
    observed = []
    client, *_ = started_client(on_message=observed.append)

    for idx in range(50):
        client._enqueue_event(KimiEvent(type="item_started", text=f"step {idx}"))

    assert observed == [{"type": "messages_pending"}]

    drained = client.drain_pending_messages(limit=100)
    assert len(drained) == 50

    client._enqueue_event(KimiEvent(type="item_completed", text="done"))
    assert observed == [{"type": "messages_pending"}, {"type": "messages_pending"}]


def test_queue_cap_drops_oldest_with_warning():
    client = KimiServerClient(queue_limit=3)

    for idx in range(5):
        client._enqueue_event(KimiEvent(type="item_started", text=f"e{idx}"))

    drained = client.drain_pending_messages(limit=10)
    warning = drained[0]["payload"]["event"]
    assert warning["type"] == "notification"
    assert warning["display_kind"] == "warning"
    assert "dropped 2" in warning["text"]
    assert [m["payload"]["event"]["text"] for m in drained[1:]] == ["e2", "e3", "e4"]


def test_default_queue_cap_2000_drops_oldest_with_warning():
    client = KimiServerClient()
    assert client.queue_limit == 2000

    for idx in range(2005):
        client._enqueue_event(KimiEvent(type="item_started", text=f"e{idx}"))

    drained = client.drain_pending_messages(limit=3000)
    assert len(drained) == 2001
    warning = drained[0]["payload"]["event"]
    assert warning["type"] == "notification"
    assert warning["display_kind"] == "warning"
    assert "dropped 5" in warning["text"]
    assert drained[1]["payload"]["event"]["text"] == "e5"
    assert drained[-1]["payload"]["event"]["text"] == "e2004"


# ----------------------------------------------------------------------
# A22-A24: abort, transport error, process exit


def test_abort_sends_ws_message():
    client, _, _, ws, _ = started_client()

    client.abort("session-1", "prompt-7")
    client.abort("session-1")

    aborts = [m for m in sent_ws_messages(ws) if m["type"] == "abort"]
    assert len(aborts) == 2
    assert aborts[0]["payload"] == {"session_id": "session-1", "prompt_id": "prompt-7"}
    assert aborts[1]["payload"] == {"session_id": "session-1"}


def test_transport_error_surfaces_event():
    inbound = json.dumps(
        {
            "type": "assistant.delta",
            "seq": 1,
            "session_id": "s",
            "payload": {"type": "assistant.delta", "turnId": 1, "delta": "x"},
        }
    )
    ws = FakeWebSocket(inbound=[inbound], recv_error=ConnectionError("socket dropped"))
    client, *_ = started_client(ws=ws, start_reader_thread=True)

    client._ws_thread.join(timeout=5)

    assert not client._ws_thread.is_alive()
    drained = client.drain_pending_messages(limit=10)
    types = [m["type"] for m in drained]
    assert types == ["event", "transport_error"]
    assert "socket dropped" in drained[1]["payload"]["error"]


def test_empty_recv_stops_reader_without_spinning():
    ws = FakeWebSocket(inbound=[""])
    client, *_ = started_client(ws=ws, start_reader_thread=True)

    client._ws_thread.join(timeout=5)

    assert not client._ws_thread.is_alive()
    assert ws.recv_calls == 1
    drained = client.drain_pending_messages(limit=10)
    assert [message["type"] for message in drained] == ["transport_error"]
    assert client._ws is None


def test_start_reconnects_same_process_and_replays_subscriptions():
    proc = FakeProcess()
    http = FakeHttpSession()
    old_ws = FakeWebSocket()
    new_ws = FakeWebSocket()
    sockets = iter((old_ws, new_ws))
    spawn_calls = []
    ws_calls = []

    def ws_factory(url, headers):
        ws_calls.append((url, headers))
        return next(sockets)

    client = KimiServerClient(
        process_factory=lambda args: spawn_calls.append(list(args)) or proc,
        http_session_factory=lambda: http,
        ws_factory=ws_factory,
        launch_command=["/fake/kimi"],
        token="test-token",
        start_reader_thread=False,
    )
    client.start()
    client.subscribe(["session-2", "session-1"])
    assert client._invalidate_ws(old_ws, "idle close", notify=False) is True

    client.start()

    assert len(spawn_calls) == 1
    assert len(ws_calls) == 2
    assert client.process is proc
    assert client._ws is new_ws
    replay = sent_ws_messages(new_ws)
    assert [message["type"] for message in replay] == ["client_hello", "subscribe"]
    assert replay[1]["payload"]["session_ids"] == ["session-1", "session-2"]


def test_send_failure_reconnects_replays_and_stale_reader_cannot_clear_replacement():
    proc = FakeProcess()
    http = FakeHttpSession()
    old_ws = BlockingWebSocket(
        send_results=[None, None, ConnectionError("socket is already closed")]
    )
    new_ws = BlockingWebSocket()
    sockets = iter((old_ws, new_ws))
    spawn_calls = []

    client = KimiServerClient(
        process_factory=lambda args: spawn_calls.append(list(args)) or proc,
        http_session_factory=lambda: http,
        ws_factory=lambda url, headers: next(sockets),
        launch_command=["/fake/kimi"],
        token="test-token",
        start_reader_thread=True,
    )
    client.start()
    assert old_ws.recv_started.wait(timeout=5)
    client.subscribe(["session-1"])

    client.abort("session-1", "prompt-7")
    assert new_ws.recv_started.wait(timeout=5)

    assert len(spawn_calls) == 1
    assert client.process is proc
    assert client._ws is new_ws
    replacement_messages = sent_ws_messages(new_ws)
    assert [message["type"] for message in replacement_messages] == [
        "client_hello",
        "subscribe",
        "abort",
    ]
    assert replacement_messages[1]["payload"]["session_ids"] == ["session-1"]
    assert replacement_messages[2]["payload"] == {
        "session_id": "session-1",
        "prompt_id": "prompt-7",
    }

    deadline = time.monotonic() + 5
    while old_ws.recv_calls < 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert client.drain_pending_messages(limit=10) == []
    assert client._ws is new_ws
    client.close()


def test_terminal_send_failure_is_wrapped_and_reported_once():
    proc = FakeProcess()
    sockets = iter(
        (
            FakeWebSocket(send_results=[None, ConnectionError("first closed")]),
            FakeWebSocket(send_results=[None, ConnectionError("second closed")]),
        )
    )
    spawn_calls = []
    client = KimiServerClient(
        process_factory=lambda args: spawn_calls.append(list(args)) or proc,
        http_session_factory=FakeHttpSession,
        ws_factory=lambda url, headers: next(sockets),
        launch_command=["/fake/kimi"],
        token="test-token",
        start_reader_thread=False,
    )
    client.start()

    with pytest.raises(KimiServerError, match="second closed"):
        client.abort("session-1")

    assert len(spawn_calls) == 1
    drained = client.drain_pending_messages(limit=10)
    assert [message["type"] for message in drained] == ["transport_error"]
    assert "second closed" in drained[0]["payload"]["error"]


def test_recovery_exhaustion_uses_bounded_exponential_backoff(monkeypatch):
    client, _, _, old_ws, _ = started_client(
        recovery_attempts=3,
        recovery_backoff=0.25,
    )
    sleeps = []
    monkeypatch.setattr(kimi_server_client.time, "sleep", lambda seconds: sleeps.append(seconds))
    client.ws_factory = lambda _url, _headers: (_ for _ in ()).throw(ConnectionError("still down"))
    assert client._invalidate_ws(old_ws, "reader down", notify=False)

    client._recovery_generation += 1
    client._recovery_loop(client._recovery_generation, "reader down")

    assert sleeps == [0.25, 0.5]
    drained = client.drain_pending_messages(limit=10)
    assert [message["type"] for message in drained] == ["transport_error"]
    assert "recovery exhausted" in drained[0]["payload"]["error"]


def test_recovery_hands_off_when_replacement_reader_fails_during_worker(monkeypatch):
    client, _, _, old_ws, _ = started_client(recovery_attempts=1, recovery_backoff=0)
    assert client._invalidate_ws(old_ws, "reader down", notify=False)
    handoffs = []

    def replacement_fails():
        client._recovery_requested = True
        raise ConnectionError("replacement reader failed")

    monkeypatch.setattr(client, "_connect_ws_locked", replacement_fails)
    monkeypatch.setattr(client, "_request_recovery", handoffs.append)
    client._recovery_generation += 1

    client._recovery_loop(client._recovery_generation, "reader down")

    assert handoffs == ["replacement reader failed"]
    assert client.drain_pending_messages(limit=10) == []


def test_close_during_socket_activity_is_silent_and_idempotent():
    ws = BlockingWebSocket()
    client, proc, http, _, _ = started_client(ws=ws, start_reader_thread=True)
    assert ws.recv_started.wait(timeout=5)

    client.close()
    client.close()
    client._ws_thread.join(timeout=5)

    assert not client._ws_thread.is_alive()
    assert client.drain_pending_messages(limit=10) == []
    shutdowns = [call for call in http.calls if call["url"].endswith("/api/v1/shutdown")]
    assert len(shutdowns) == 1
    assert proc.terminated is False


def test_server_process_exit_surfaces_exit_message():
    observed = []
    stream = ControlledStdout(eof=False)
    proc = FakeProcess(stdout=stream)
    client, *_ = started_client(proc=proc, on_exit=observed.append)

    assert observed == []
    proc.returncode = 7
    stream.close()
    client._banner_thread.join(timeout=5)

    assert observed == [7]


# ----------------------------------------------------------------------
# A25: thread safety smoke


def test_public_methods_thread_safe():
    errors = []
    client, *_ = started_client()

    def hammer(idx):
        try:
            for n in range(100):
                if n % 4 == 0:
                    client.subscribe(["s-%d" % (n % 5)])
                elif n % 4 == 1:
                    client.abort("s-1", "p-%d" % n)
                elif n % 4 == 2:
                    client._enqueue_event(_delta("x", item="i-%d" % (n % 7)))
                else:
                    client.drain_pending_messages(limit=10)
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=hammer, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + 20
    for thread in threads:
        thread.join(timeout=max(0.1, deadline - time.monotonic()))

    assert all(not thread.is_alive() for thread in threads), "deadlock detected"
    assert errors == []


# ----------------------------------------------------------------------
# A26: no wx import in module source


def test_module_does_not_import_wx():
    source = Path(kimi_server_client.__file__).read_text(encoding="utf-8")

    assert "import wx" not in source
    assert "from wx" not in source


def test_sequenced_frame_without_epoch_inherits_confirmed_epoch():
    client, *_ = started_client()
    client._session_cursors["session-1"] = {"epoch": "epoch-a", "seq": 4}
    client._stable_event_cursors["session-1"] = ("epoch-a", 4)

    accepted = client._accept_session_event({
        "type": "event",
        "session_id": "session-1",
        "seq": 5,
        "payload": {"type": "turn.started", "turnId": "t1"},
    })

    assert accepted is True
    assert client._session_cursors["session-1"] == {"epoch": "epoch-a", "seq": 5}
    assert client._stable_event_cursors["session-1"] == ("epoch-a", 5)


def test_same_seq_offsetless_volatile_state_keeps_distinct_phases():
    client, *_ = started_client()
    base = {"type": "event", "session_id": "session-1", "epoch": "e", "seq": 7, "volatile": True}
    first = {**base, "payload": {"type": "agent.status.updated", "phase": {"kind": "thinking"}}}
    second = {**base, "payload": {"type": "agent.status.updated", "phase": {"kind": "tool"}}}

    assert client._accept_session_event(first) is True
    assert client._accept_session_event(second) is True
    assert client._accept_session_event(dict(first)) is False


def test_rejected_subscription_is_quarantined_before_recovery():
    client, *_ = started_client()
    client._subscribed_sessions.update({"good", "bad"})
    client._subscribe_requests["req"] = {"good", "bad"}

    with pytest.raises(KimiServerError, match="bad"):
        client._handle_ack({
            "type": "ack", "id": "req",
            "payload": {"accepted": ["good"], "not_found": ["bad"]},
        })

    assert client._subscribed_sessions == {"good"}


def test_http_200_application_5xx_post_is_result_unknown():
    client, _, http, _, _ = started_client()
    http.routes[("POST", "/api/v1/sessions/session-1/prompts")] = FakeResponse(
        200, {"code": 50001, "msg": "internal", "data": None}
    )

    with pytest.raises(KimiServerError) as caught:
        client.submit_prompt("session-1", [{"type": "text", "text": "hello"}])

    assert caught.value.result_unknown is True


def test_successful_recovery_reports_actual_sessions_once(monkeypatch):
    client, _, _, old_ws, _ = started_client(recovery_attempts=1)
    client._subscribed_sessions.update({"session-b", "session-a"})
    client._invalidate_ws(old_ws, "reader reset", recover=False)

    def reconnect():
        with client._lifecycle_lock:
            client._ws = FakeWebSocket()
            ready = client._transport_ready_events.setdefault(client._ws_generation, threading.Event())
            ready.set()

    monkeypatch.setattr(client, "_connect_ws_locked", reconnect)
    client._recovery_generation += 1
    client._recovery_loop(client._recovery_generation, "reader reset")

    recovered = [
        message for message in client.drain_pending_messages(limit=10)
        if message.get("type") == "transport_recovered"
    ]
    assert recovered == [{
        "type": "transport_recovered",
        "payload": {"session_ids": ["session-a", "session-b"]},
    }]


def test_same_client_process_restart_rediscovers_token(monkeypatch):
    processes = iter((FakeProcess(), FakeProcess()))
    tokens = iter(("token-a", "token-b"))
    sockets = []
    client = KimiServerClient(
        process_factory=lambda _args: next(processes),
        http_session_factory=FakeHttpSession,
        ws_factory=lambda _url, headers: sockets.append(list(headers)) or FakeWebSocket(),
        launch_command=["/fake/kimi"],
        token=None,
        start_reader_thread=False,
    )
    monkeypatch.setattr(client, "_find_token", lambda: next(tokens))

    client.start()
    first_process = client.process
    assert client.token == "token-a"
    first_process.returncode = 1
    client.start()

    assert client.process is not first_process
    assert client.token == "token-b"
    assert sockets[-1] == ["Authorization: Bearer token-b"]
