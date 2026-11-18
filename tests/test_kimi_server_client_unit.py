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


@pytest.mark.parametrize("event_type", ["tool.call.started", "tool.result", "turn.step.started", "session.meta.updated"])
def test_visible_and_lifecycle_events_retain_envelope_origin_timestamp(event_type):
    payload = {"type": event_type, "turnId": "turn-1", "toolCallId": "tool-1", "stepId": "step-1"}
    event = kimi_server_client.map_session_event({
        "type": event_type, "session_id": "session-1", "timestamp": "2026-09-21T08:30:00Z", "payload": payload,
    })
    assert event is not None
    assert event.origin_timestamp == pytest.approx(1_789_979_400.0)


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), "2026-09-21T08:30:00", "12.5", 12.5])
def test_kimi_origin_timestamp_rejects_ambiguous_or_implausible_values(value):
    assert kimi_server_client._provider_origin_timestamp({"timestamp": value}) is None


def test_kimi_origin_timestamp_normalizes_seconds_milliseconds_and_precedence():
    seconds = 1_795_000_000.0
    assert kimi_server_client._provider_origin_timestamp({"timestamp": seconds}) == seconds
    assert kimi_server_client._provider_origin_timestamp({"createdAt": str(int(seconds * 1000))}) == seconds
    assert kimi_server_client._provider_origin_timestamp(
        {"timestamp": seconds}, {"timestamp": seconds + 1}
    ) == seconds
    assert kimi_server_client._provider_origin_timestamp({"time": seconds}) is None


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

    def request(self, method, url, json=None, headers=None, timeout=None, params=None):
        self.calls.append({"method": method, "url": url, "json": json, "headers": headers, "timeout": timeout, "params": params})
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

    with pytest.raises(KimiServerError, match="SHA256"):
        resolve_kimi_launch_command()


def test_resolve_launch_command_env_override_missing_file(monkeypatch, tmp_path):
    monkeypatch.setenv("KIMI_BIN", str(tmp_path / "no-such-kimi.exe"))
    monkeypatch.setattr(kimi_server_client.shutil, "which", lambda name: r"C:\elsewhere\kimi.exe")

    with pytest.raises(KimiServerError):
        resolve_kimi_launch_command()


def test_resolve_launch_command_does_not_use_shared_path(monkeypatch):
    monkeypatch.delenv("KIMI_BIN", raising=False)
    monkeypatch.setattr(kimi_server_client.shutil, "which", lambda name: "/usr/bin/kimi")

    with pytest.raises(KimiServerError, match="runtime missing"):
        resolve_kimi_launch_command()


def test_resolve_launch_command_does_not_use_shared_user_install(monkeypatch, tmp_path):
    monkeypatch.delenv("KIMI_BIN", raising=False)
    monkeypatch.setattr(kimi_server_client.shutil, "which", lambda name: None)
    monkeypatch.setattr(kimi_server_client.os, "name", "nt")
    candidate = tmp_path / ".kimi-code" / "bin" / "kimi.exe"
    candidate.parent.mkdir(parents=True)
    candidate.write_text("", encoding="utf-8")
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    with pytest.raises(KimiServerError, match="runtime missing"):
        resolve_kimi_launch_command()


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


def test_snapshot_total_uses_only_input_and_output_tokens():
    client, _, http, _, _ = started_client()
    data = {"session": {"usage": {"input_tokens": 100, "output_tokens": 20,
                                 "cache_read_tokens": 90, "cache_creation_tokens": 40}}}
    http.routes[("GET", "/api/v1/sessions/session-1/snapshot")] = FakeResponse(
        200, {"code": 0, "data": data},
    )
    assert client.get_snapshot("session-1") == data
    assert kimi_server_client.kimi_snapshot_total_tokens(data) == 120
    assert kimi_server_client.kimi_snapshot_total_tokens({"session": {"usage": {"input_tokens": 100}}}) is None
    assert kimi_server_client.kimi_snapshot_total_tokens({"session": {"usage": {
        "input_tokens": -1, "output_tokens": 20}}}) is None


def test_oauth_usage_reads_quota_endpoint():
    client, _, http, _, _ = started_client()
    data = {"kind": "ok", "quota": {"usages": {"limit5h": {"usedRatio": 0.25}}}}
    http.routes[("GET", "/api/v1/oauth/usage")] = FakeResponse(200, {"code": 0, "data": data})
    assert client.get_oauth_usage() == data


def test_auth_and_userinfo_read_account_endpoints():
    client, _, http, _, _ = started_client()
    auth = {"managed_provider": {"status": "authenticated"}}
    info = {"kind": "ok", "userInfo": {"userId": "user-1"}}
    http.routes[("GET", "/api/v1/auth")] = FakeResponse(200, {"code": 0, "data": auth})
    http.routes[("GET", "/api/v1/oauth/userinfo")] = FakeResponse(200, {"code": 0, "data": info})
    assert client.get_auth() == auth
    assert client.get_oauth_userinfo() == info


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


def test_sdk_lifecycle_isolates_steps_agents_and_stale_volatile_frames():
    client = KimiServerClient()

    def push(kind, seq, *, agent="main", offset=None, **body):
        message = {"type": kind, "session_id": "s", "epoch": "e", "seq": seq,
                   "payload": {"type": kind, "turnId": 0, "agentId": agent, **body}}
        if offset is not None:
            message.update(volatile=True, offset=offset)
        client._handle_ws_message(message)
        return message

    push("turn.step.started", 1, step=1, stepId="step-a")
    first = push("thinking.delta", 1, offset=0, delta="A")
    push("thinking.delta", 1, offset=1, delta="B")
    push("turn.step.started", 2, agent="worker", step=1, stepId="worker-step")
    push("thinking.delta", 2, agent="worker", offset=0, delta="C")
    push("turn.step.completed", 3, step=1, stepId="step-a")
    push("turn.step.started", 4, step=2, stepId="step-b")
    push("thinking.delta", 4, offset=0, delta="D")
    push("thinking.delta", 1, offset=2, delta="STALE")
    client._handle_ws_message(first)
    deltas = [message["payload"]["event"] for message in client.drain_pending_messages(30)
              if message["payload"]["event"]["type"] == "agent_message_delta"]
    assert [event["text"] for event in deltas] == ["AB", "C", "D"]
    assert len({event["item_id"] for event in deltas}) == 3
    assert [event["data"]["step_id"] for event in deltas] == ["step-a", "worker-step", "step-b"]


def test_coalesced_sdk_thinking_keeps_a_later_hidden_marker():
    client = KimiServerClient()
    client._handle_ws_message({"type": "turn.step.started", "session_id": "s", "epoch": "e", "seq": 1,
                               "payload": {"type": "turn.step.started", "turnId": 0, "agentId": "main", "step": 1, "stepId": "step"}})
    for offset, hidden in ((0, False), (1, True)):
        client._handle_ws_message({"type": "thinking.delta", "session_id": "s", "epoch": "e", "seq": 1,
                                  "offset": offset, "volatile": True,
                                  "payload": {"type": "thinking.delta", "turnId": 0, "agentId": "main", "delta": "X", "hidden": hidden}})
    delta = client.drain_pending_messages(10)[-1]["payload"]["event"]
    assert delta["text"] == "XX"
    assert delta["data"]["hidden"] is True


@pytest.mark.parametrize("flag,value,mapped", [
    ("hidden", True, "hidden"), ("isPrivate", True, "private"),
    ("nonDisclosable", True, "non_disclosable"), ("isDisclosable", False, "disclosable"),
    ("visibility", "private", "visibility"), ("reasoningVisibility", "hidden", "reasoning_visibility"),
])
@pytest.mark.parametrize("closed_step", [False, True])
def test_sdk_same_offset_replay_accepts_restrictive_metadata_once(flag, value, mapped, closed_step):
    client = KimiServerClient()
    client._handle_ws_message({"type": "turn.step.started", "session_id": "s", "epoch": "e", "seq": 1,
                               "payload": {"type": "turn.step.started", "turnId": 0, "agentId": "main", "step": 1}})
    original = {"type": "thinking.delta", "session_id": "s", "epoch": "e", "seq": 1,
                "offset": 0, "volatile": True,
                "payload": {"type": "thinking.delta", "turnId": 0, "agentId": "main", "delta": "SECRET"}}
    client._handle_ws_message(original)
    first = client.drain_pending_messages(10)[-1]["payload"]["event"]
    if closed_step:
        client._handle_ws_message({"type": "turn.step.completed", "session_id": "s", "epoch": "e", "seq": 2,
                                   "payload": {"type": "turn.step.completed", "turnId": 0, "agentId": "main", "step": 1}})
        client.drain_pending_messages(10)
    restricted = dict(original, payload={**original["payload"], flag: value})
    client._handle_ws_message(restricted)
    changed = client.drain_pending_messages(10)
    assert len(changed) == 1
    assert changed[0]["payload"]["event"]["data"][mapped] == value
    assert changed[0]["payload"]["event"]["item_id"] == first["item_id"]
    client._handle_ws_message(restricted)
    assert not client.drain_pending_messages(10)


def test_sdk_epoch_reset_does_not_reuse_stream_or_accept_old_epoch():
    client = KimiServerClient()
    def event(kind, epoch, seq, **body):
        return {"type": kind, "session_id": "s", "epoch": epoch, "seq": seq,
                "payload": {"type": kind, "agentId": "main", "turnId": 0, **body}}
    client._handle_ws_message(event("turn.step.started", "old", 1, step=1, stepId="native-step"))
    old = dict(event("thinking.delta", "old", 1, delta="OLD"), offset=0, volatile=True)
    client._handle_ws_message(old)
    old_id = client.drain_pending_messages(10)[-1]["payload"]["event"]["item_id"]
    client._hello_ids[client._ws_generation] = "hello-reset"
    client._handle_ws_message({"type": "ack", "id": "hello-reset", "payload": {"cursors": {"s": {"epoch": "new", "seq": 0}}}})
    client._handle_ws_message(event("turn.step.started", "new", 1, step=1, stepId="native-step"))
    client._handle_ws_message(dict(event("thinking.delta", "new", 1, delta="NEW"), offset=0, volatile=True))
    client._handle_ws_message(old)
    deltas = [item["payload"]["event"] for item in client.drain_pending_messages(10)
              if item["type"] == "event" and item["payload"]["event"]["type"] == "agent_message_delta"]
    assert len(deltas) == 1 and deltas[0]["text"] == "NEW"
    assert deltas[0]["item_id"] != old_id


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


@pytest.mark.parametrize("fragments,expected", [
    ([(3, "BBB"), (0, "AAA")], [(3, "BBB"), (0, "AAA")]),
    ([(0, "abc"), (1, "XX")], [(0, "abc"), (1, "XX")]),
    ([(0, "abc"), (2, "cd")], [(0, "abcd")]),
    ([(None, "AAA"), (3, "BBB")], [(None, "AAA"), (3, "BBB")]),
])
def test_delta_coalescing_preserves_reverse_or_conflicting_same_batch_fragments(fragments, expected):
    client = KimiServerClient()
    for offset, text in fragments:
        client._enqueue_event(_delta(text, kind="thinking", data={"offset": offset}))
    events = [message["payload"]["event"] for message in client.drain_pending_messages(10)]
    assert [(event["data"]["offset"], event["text"]) for event in events] == expected


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


def test_v2_subscription_uses_per_session_grade_and_requires_matching_ack():
    client, _, _, ws, _ = started_client()
    client._handle_ws_message({"type": "server_hello", "payload": {"protocol_version": 2}})
    client.subscribe(["s"])
    request = sent_ws_messages(ws)[-1]
    assert request["type"] == "subscribe_v2"
    assert request["payload"] == {"session_id": "s", "transcript": {"main": "delta"}}
    with pytest.raises(KimiServerError, match="accepted"):
        client._handle_ack({"type": "ack", "id": request["id"], "code": 0, "payload": {}})


def test_v2_completed_transcript_maps_once_without_legacy_rest():
    client, _, _, _, _ = started_client()
    client._handle_ws_message({"type": "server_hello", "payload": {"protocol_version": 2}})
    # Kimi Code 2.5.3 packages/transcript schema; synthetic, no user content.
    frame = {"type": "transcript.reset", "session_id": "s", "payload": {
        "agent_id": "main", "seq": 4, "has_more_older": False,
        "snapshot": {"items": [{"kind": "turn", "turnId": "t", "triggerPromptId": "p",
            "ordinal": 0, "origin": {"kind": "user"}, "state": "completed",
            "steps": [{"kind": "step", "stepId": "st", "turnId": "t", "ordinal": 0,
                "state": "completed", "frames": [{"kind": "text", "role": "assistant",
                    "frameId": "f", "text": "synthetic answer"}]}]}]}}}
    client._handle_ws_message(frame)
    client._handle_ws_message(frame)
    events = [row["payload"]["event"] for row in client.drain_pending_messages(100) if row["type"] == "event"]
    finals = [event for event in events if event["type"] == "turn_completed"]
    assert len(finals) == 1
    assert finals[0]["text"] == "synthetic answer"
    assert finals[0]["data"]["prompt_id"] == "p"
    assert finals[0]["data"]["stream_complete"] is True


def test_v2_unknown_protocol_and_missing_subscription_ack_fail_before_post():
    client, _, http, _, _ = started_client()
    with pytest.raises(KimiServerError, match="protocol 7"):
        client._handle_ws_message({"type": "server_hello", "payload": {"protocol_version": 7}})
    client.protocol_version = 2
    client.ws_connect_timeout = .001
    with pytest.raises(KimiServerError, match="ACK timed out"):
        client.submit_prompt("s", [{"type": "text", "text": "synthetic"}])
    assert not any(call["method"] == "POST" for call in http.calls)


def test_v2_recovery_reads_exact_prompt_and_ignores_later_assistant():
    client, _, http, _, _ = started_client()
    client.protocol_version = 2
    def turn(prompt, text, state="completed"):
        return {"kind": "turn", "turnId": prompt, "triggerPromptId": prompt,
                "state": state, "steps": [{"stepId": "st", "ordinal": 0, "state": "completed",
                    "frames": [{"kind": "text", "role": "assistant", "frameId": "f", "text": text}]}]}
    http.routes[("GET", "/api/v1/sessions/s/transcript")] = FakeResponse(200, {
        "code": 0, "data": {"agent_id": "main", "items": [turn("p", "correct"), turn("later", "wrong")], "has_more": False}})
    event = client.reconcile_v2("s", "p")
    assert event.text == "correct"
    assert event.data["prompt_id"] == "p"
    assert client.reconcile_v2("s", "missing") is None
    assert not any(call["url"].endswith(("/status", "/snapshot", "/messages")) for call in http.calls)


def test_v2_transcript_gap_and_tool_step_never_publish_partial():
    from kimi_transcript import Transcript
    transcript = Transcript()
    transcript.reset({"items": [{"kind": "turn", "turnId": "t", "triggerPromptId": "p", "state": "completed",
        "steps": [{"stepId": "st", "state": "completed", "frames": [
            {"frameId": "f", "kind": "text", "role": "assistant", "text": "intermediate"},
            {"frameId": "tool", "kind": "tool", "state": "done"}]}]}]}, 1)
    assert transcript.results() == []
    assert not transcript.apply([], 3)
    assert transcript.incomplete
    assert transcript.results() == []


def test_v2_client_gap_uses_journal_before_publishing_final():
    client, _, http, _, _ = started_client()
    client.protocol_version = 2
    client._handle_ws_message({"type": "transcript.reset", "session_id": "s", "payload": {
        "agent_id": "main", "seq": 1, "snapshot": {"items": []}}})
    batches = [
        {"seq": 2, "ops": [{"op": "turn.upsert", "turn": {
            "kind": "turn", "turnId": "t", "triggerPromptId": "p", "state": "completed"}}]},
        {"seq": 3, "ops": [{"op": "step.upsert", "turnId": "t", "step": {
            "kind": "step", "stepId": "st", "turnId": "t", "ordinal": 0, "state": "completed"}}]},
        {"seq": 4, "ops": [{"op": "frame.upsert", "turnId": "t", "stepId": "st", "frame": {
            "kind": "text", "role": "assistant", "frameId": "f", "text": "journal final"}}]},
    ]
    http.routes[("GET", "/api/v1/sessions/s/transcript/ops")] = FakeResponse(200, {
        "code": 0, "data": {"agent_id": "main", "complete": True, "latest_seq": 4, "batches": batches}})
    client._handle_ws_message({"type": "transcript.ops", "session_id": "s", "payload": {
        "agent_id": "main", "seq": 4, "ops": batches[-1]["ops"]}})
    finals = [row["payload"]["event"] for row in client.drain_pending_messages(100) if row["type"] == "event"]
    assert len(finals) == 1
    assert finals[0]["text"] == "journal final"
    assert http.calls[-1]["params"] == {"agent_id": "main", "since_seq": 1}


def _v2_snapshot(text="", state="running", prompt="p", turn="t"):
    return {"items": [{"kind": "turn", "turnId": turn, "triggerPromptId": prompt, "state": state,
        "steps": [{"stepId": "st", "ordinal": 0, "state": "completed", "frames": [
            {"kind": "text", "role": "assistant", "frameId": "f", "text": text}]}]}]}


def _steered_rest_snapshot():
    snapshot = _v2_snapshot("final public answer", "completed")
    step = snapshot["items"][0]["steps"][0]
    step.update(state="interrupted", endReason="steered")
    step["frames"][:0] = [
        {"kind": "text", "role": "assistant", "text": "old partial"},
        {"kind": "text", "role": "user", "text": "follow-up"},
        {"kind": "thinking", "text": "private reasoning"},
    ]
    return snapshot


def test_v2_steered_rest_recovers_exact_owner_public_final():
    client, _, http, _, _ = started_client()
    client.protocol_version = 2
    snapshot = _steered_rest_snapshot()
    snapshot["items"] += _v2_snapshot("wrong later answer", "completed", "later", "later-turn")["items"]
    http.routes[("GET", "/api/v1/sessions/s/transcript")] = FakeResponse(200, {
        "code": 0, "data": {**snapshot, "has_more": False}})
    event = client.reconcile_v2("s", "p")
    assert event is not None
    assert (event.type, event.status, event.text) == ("turn_completed", "completed", "final public answer")
    assert event.data["prompt_id"] == "p"
    assert all(event.data[key] is True for key in ("authoritative", "terminal_verified", "stream_complete"))
    assert client.reconcile_v2("s", "missing") is None
    transcript_calls = [call for call in http.calls if call["url"].endswith("/transcript")]
    assert all(call["params"] == {"agent_id": "main", "page_size": 100} for call in transcript_calls)


@pytest.mark.parametrize("case", ["aborted", "unknown", "missing_reason", "running_step", "failed_step",
    "running_turn", "failed_turn", "cancelled_turn", "tool", "thinking", "blank", "private", "hidden",
    "non_disclosable", "not_disclosable", "no_answer_after_user", "thinking_after_user", "gap"])
def test_v2_steered_rest_rejects_unsafe_terminal_candidates(case):
    from kimi_transcript import Transcript
    client, *_ = started_client()
    snapshot = _steered_rest_snapshot()
    turn = snapshot["items"][0]
    step = turn["steps"][0]
    final = step["frames"][-1]
    if case in {"aborted", "unknown"}:
        step["endReason"] = case
    elif case == "missing_reason":
        del step["endReason"]
    elif case.endswith("_step"):
        step["state"] = case.removesuffix("_step")
    elif case.endswith("_turn"):
        turn["state"] = case.removesuffix("_turn")
    elif case == "tool":
        step["frames"].append({"kind": "tool"})
    elif case == "thinking":
        final["kind"] = "thinking"
    elif case == "blank":
        final["text"] = " \n"
    elif case in {"private", "hidden", "non_disclosable"}:
        final[case] = True
    elif case == "not_disclosable":
        final["disclosable"] = False
    elif case in {"no_answer_after_user", "thinking_after_user"}:
        step["frames"].append({"kind": "text", "role": "user", "text": "last follow-up"})
        if case == "thinking_after_user":
            step["frames"].append({"kind": "thinking", "text": "still thinking"})
    # An earlier completed step must never rescue a rejected final step.
    turn["steps"].insert(0, {"stepId": "earlier", "ordinal": -1, "state": "completed", "frames": [
        {"kind": "text", "role": "assistant", "text": "earlier answer"}]})
    transcript = Transcript()
    transcript.reset(snapshot, 1)
    if case == "gap":
        assert not transcript.apply([], 3)
    results = transcript.results()
    monkey_page = {**snapshot, "has_more": False}
    client.get_transcript = lambda *a, **k: monkey_page
    event = client.reconcile_v2("s", "p")
    if case in {"failed_turn", "cancelled_turn"}:
        assert results == [(turn, "")]
        assert event.status == "failed"
    else:
        assert results == []
        # REST builds a fresh complete snapshot rather than inheriting a stream gap.
        if case != "gap":
            assert event is None or event.type == "thread_status_changed"


def test_v2_steered_rest_without_user_frame_keeps_public_assistant_text():
    client, *_ = started_client()
    snapshot = _steered_rest_snapshot()
    snapshot["items"][0]["steps"][0]["frames"] = [{
        "kind": "text", "role": "assistant", "text": "whole final"}]
    client.get_transcript = lambda *a, **k: {**snapshot, "has_more": False}
    assert client.reconcile_v2("s", "p").text == "whole final"


@pytest.mark.parametrize("prefix,fragment,overlap,expected", [
    ("hello", " world", 5, "hello world"),
    ("中文", "回复", 2, "中文回复"),
    ("😀", "x", 2, "😀x"),
    ("a😀", "😀x", 1, "a😀x"),
    ("\ud83d", "\ude00x", 1, "😀x"),
])
def test_v2_append_utf16_overlap_to_one_complete_answer(prefix, fragment, overlap, expected):
    client, *_ = started_client()
    client.protocol_version = 2
    client._handle_ws_message({"type": "transcript.reset", "session_id": "s", "payload": {
        "agent_id": "main", "seq": 1, "snapshot": _v2_snapshot(prefix)}})
    client.drain_pending_messages(100)
    append = {"op": "append", "target": {"type": "frame", "turnId": "t", "stepId": "st", "frameId": "f"},
              "offset": overlap, "text": fragment}
    for seq, ops in [(2, [append]), (3, [append]), (4, [{"op": "turn.upsert", "turn": {
            "kind": "turn", "turnId": "t", "triggerPromptId": "p", "state": "completed"}}])]:
        client._handle_ws_message({"type": "transcript.ops", "session_id": "s", "payload": {
            "agent_id": "main", "seq": seq, "ops": ops}})
    finals = [row["payload"]["event"] for row in client.drain_pending_messages(100)
              if row["type"] == "event" and row["payload"]["event"]["type"] == "turn_completed"]
    assert [event["text"] for event in finals] == [expected]
    assert not client._v2_frame_texts and not client._v2_started
    assert not client._v2_transcripts["s"].items


@pytest.mark.parametrize("frame_id,fragment", [("missing", "x"), ("f", "conflict")])
def test_v2_failed_append_is_atomic_and_client_repairs_from_previous_seq(frame_id, fragment):
    from kimi_transcript import Transcript
    snapshot = _v2_snapshot("😀", state="completed")
    op = {"op": "append", "target": {"type": "frame", "turnId": "t", "stepId": "st", "frameId": frame_id},
          "offset": 0, "text": fragment}
    transcript = Transcript()
    transcript.reset(snapshot, 1)
    assert not transcript.apply([{"op": "turn.upsert", "turn": {"turnId": "t", "state": "running"}}, op], 2)
    assert transcript.seq == 1 and transcript.items["t"]["state"] == "completed"
    client, _, http, _, _ = started_client()
    client.protocol_version = 2
    client._v2_transcripts["s"] = transcript
    http.routes[("GET", "/api/v1/sessions/s/transcript/ops")] = FakeResponse(200, {"code": 0, "data": {"complete": False}})
    http.routes[("GET", "/api/v1/sessions/s/transcript")] = FakeResponse(200, {"code": 0, "data": {
        **_v2_snapshot("😀 repaired", state="completed"), "seq": 2}})
    client._handle_ws_message({"type": "transcript.ops", "session_id": "s", "payload": {
        "agent_id": "main", "seq": 2, "ops": [op]}})
    assert http.calls[-2]["params"]["since_seq"] == 1
    events = [row["payload"]["event"] for row in client.drain_pending_messages(100) if row["type"] == "event"]
    assert [event["text"] for event in events if event["type"] == "turn_completed"] == ["😀 repaired"]


def test_v2_completed_retention_is_bounded_while_pending_text_survives():
    client, *_ = started_client()
    client.protocol_version = 2
    client._handle_ws_message({"type": "transcript.reset", "session_id": "pending", "payload": {
        "agent_id": "main", "seq": 1, "snapshot": _v2_snapshot("keep pending")}})
    latest = None
    for index in range(530):
        latest = {"type": "transcript.reset", "session_id": f"s{index}", "payload": {
            "agent_id": "main", "seq": 1, "snapshot": _v2_snapshot(f"completed {index}", "completed")}}
        client._handle_ws_message(latest)
        client.drain_pending_messages(100)
    assert len(client._v2_completed) == len(client._v2_completed_order) == 512
    assert len(client._v2_transcripts) <= 33
    assert list(client._v2_frame_texts.values()) == ["keep pending"]
    client._handle_ws_message(latest)
    assert client.drain_pending_messages(100) == []
    client.close()
    assert not any((client._v2_transcripts, client._v2_completed, client._v2_started, client._v2_frame_texts))


def test_v2_rest_completion_releases_stream_text_and_keeps_replay_dedup(monkeypatch):
    client, *_ = started_client()
    client.protocol_version = 2
    client._handle_ws_message({"type": "transcript.reset", "session_id": "s", "payload": {
        "agent_id": "main", "seq": 1, "snapshot": _v2_snapshot("pending")}})
    client.drain_pending_messages(100)
    final = _v2_snapshot("authoritative final", "completed")
    monkeypatch.setattr(client, "get_transcript", lambda *a, **k: {**final, "has_more": False})
    assert client.reconcile_v2("s", "p").text == "authoritative final"
    assert not client._v2_frame_texts and not client._v2_started and not client._v2_transcripts["s"].items
    client._handle_ws_message({"type": "transcript.reset", "session_id": "s", "payload": {
        "agent_id": "main", "seq": 2, "snapshot": final}})
    assert client.drain_pending_messages(100) == []


def test_v2_real_connection_greeting_ack_invalidation_and_new_generation(monkeypatch):
    import websocket
    class Socket(BlockingWebSocket):
        def __init__(self):
            super().__init__()
            self.subscription_sent = threading.Event()
            self.feed(json.dumps({"type": "server_hello", "payload": {"protocol_version": 2}}))
        def send(self, line):
            super().send(line)
            message = json.loads(line)
            if message["type"] == "client_hello":
                self.feed(json.dumps({"type": "ack", "id": message["id"], "code": 0, "payload": {}}))
            if message["type"] == "subscribe_v2":
                self.subscription_sent.set()
    old, new = Socket(), Socket()
    sockets = iter((old, new))
    monkeypatch.setattr(websocket, "create_connection", lambda *a, **k: next(sockets))
    http = FakeHttpSession()
    http.routes[("POST", "/api/v1/sessions/s/prompts")] = FakeResponse(200, {"code": 0, "data": {"prompt_id": "p"}})
    client = KimiServerClient(process_factory=lambda args: FakeProcess(), http_session_factory=lambda: http,
                              launch_command=["/fake/kimi"], token="test-token", ws_connect_timeout=.2)
    def post_count():
        return sum(call["method"] == "POST" and call["url"].endswith("/prompts") for call in http.calls)
    def submit_after_ack(ws):
        ws.subscription_sent.clear()
        results = []
        worker = threading.Thread(target=lambda: results.append(client.submit_prompt("s", [])))
        worker.start()
        assert ws.subscription_sent.wait(1)
        count = post_count()
        request = [m for m in sent_ws_messages(ws) if m["type"] == "subscribe_v2"][-1]
        ws.feed(json.dumps({"type": "ack", "id": request["id"], "code": 0, "payload": {"accepted": ["s"], "not_found": []}}))
        worker.join(2)
        assert not worker.is_alive() and results == ["p"]
        assert post_count() == count + 1
        return request
    try:
        client.start()
        assert client.protocol_version == 2
        request = submit_after_ack(old)
        generation = client._ws_generation
        monkeypatch.setattr(client, "_request_recovery", lambda error: None)
        assert client._invalidate_ws(old, "controlled disconnect", recover=True)
        assert not client._v2_ready_sessions
        assert not client._v2_subscription_events["s"].is_set()
        client._handle_ack({"type": "ack", "id": request["id"], "payload": {"accepted": ["s"]}}, generation=generation)
        assert not client._v2_ready_sessions
        with monkeypatch.context() as paused:
            paused.setattr(client, "_connect_ws_locked", lambda **k: (_ for _ in ()).throw(KimiServerError("reconnect paused")))
            with pytest.raises(KimiServerError):
                client.submit_prompt("s", [])
        assert post_count() == 1
        client.start()
        client._handle_ack({"type": "ack", "id": request["id"], "payload": {"accepted": ["s"]}})
        assert not client._v2_ready_sessions
        submit_after_ack(new)
        assert post_count() == 2
    finally:
        client.close()


def test_v2_live_smoke_terminal_check_is_exact_prompt_and_offline():
    from test_kimi_live_smoke import _assert_successful_prompt
    client = KimiServerClient()
    client.protocol_version = 2
    calls = []
    def reconcile(session, prompt):
        calls.append((session, prompt))
        return client._v2_final_event(session, {"turnId": "t", "triggerPromptId": prompt, "state": "completed"}, "pong")
    client.reconcile_v2 = reconcile
    client.get_status = lambda *a, **k: pytest.fail("legacy status called")
    assert _assert_successful_prompt(client, "s", "p") == "pong"
    assert calls == [("s", "p")]
    client.reconcile_v2 = lambda *a, **k: KimiEvent(type="thread_status_changed", status="running")
    with pytest.raises(AssertionError):
        _assert_successful_prompt(client, "s", "p")


def test_runtime_preparation_hash_version_and_environment_are_isolated(tmp_path, monkeypatch):
    import kimi_runtime
    source = tmp_path / "candidate.exe"
    source.write_bytes(b"synthetic runtime")
    monkeypatch.setattr(kimi_runtime, "SUPPORTED_SHA256", kimi_server_client.hashlib.sha256(source.read_bytes()).hexdigest())
    monkeypatch.setattr(kimi_runtime.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, b"2.5.3\n"))
    target = kimi_runtime.prepare_runtime(source, tmp_path / "private/2.5.3")
    monkeypatch.setattr(kimi_runtime, "runtime_path", lambda: target)
    monkeypatch.delenv("KIMI_BIN", raising=False)
    monkeypatch.setattr(kimi_server_client.shutil, "which", lambda name: "changed-shared-version")
    assert resolve_kimi_launch_command() == [str(target)]
    assert kimi_runtime.child_environment({"KIMI_CODE_NO_AUTO_UPDATE": "0"})["KIMI_CODE_NO_AUTO_UPDATE"] == "1"
    target.write_bytes(b"changed")
    with pytest.raises(KimiServerError, match="SHA256"):
        resolve_kimi_launch_command()
    target.unlink()
    with pytest.raises(KimiServerError, match="runtime missing"):
        resolve_kimi_launch_command()


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


def test_list_messages_passes_before_id_as_query_param():
    http = FakeHttpSession()
    client, _, _, _, _ = started_client(http=http)
    http.routes[("GET", "/api/v1/sessions/s1/messages")] = FakeResponse(
        200, {"code": 0, "msg": "success", "data": {"items": [], "has_more": False}}
    )

    assert client.list_messages("s1", before_id="m1") == []
    assert http.calls[-1]["params"] == {"before_id": "m1"}

    assert client.list_messages("s1") == []
    assert http.calls[-1]["params"] is None


@pytest.mark.parametrize("failure", [ConnectionResetError(10054, "controlled reset"), kimi_server_client.requests.ConnectionError("controlled reset")])
def test_local_rest_reset_get_retries_once_with_remaining_budget(failure, monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(kimi_server_client.time, "monotonic", lambda: clock[0])
    client = KimiServerClient(http_session_factory=lambda: None)
    calls = []
    class Http:
        def request(self, method, url, **kwargs):
            calls.append(kwargs["timeout"])
            if len(calls) == 1:
                clock[0] += 0.01
                raise failure
            return FakeResponse(200, {"ok": True})
    client._http = Http()
    assert client._request("GET", "/api/sessions", timeout=0.2).status_code == 200
    assert len(calls) == 2
    assert 0 < calls[1] < calls[0] <= 0.2


@pytest.mark.parametrize("method, expected_calls", [("GET", 2), ("POST", 1)])
def test_local_rest_reset_exhaustion_and_unknown_post_keep_original_error(method, expected_calls):
    client = KimiServerClient(http_session_factory=lambda: None)
    calls = []
    class Http:
        def request(self, *args, **kwargs):
            calls.append(args)
            raise ConnectionResetError(10054, "controlled reset")
    client._http = Http()
    with pytest.raises(KimiServerError) as caught:
        client._request(method, "/api/prompt", timeout=0.2)
    assert len(calls) == expected_calls
    assert caught.value.result_unknown == (method == "POST")
    assert f"local REST {method} /api/prompt" in str(caught.value)
    assert "controlled reset" in str(caught.value)


def test_owned_local_rest_session_ignores_environment_proxy(monkeypatch):
    client, _proc, http, _ws, _calls = make_client()
    client.http_session_factory = None
    http.trust_env = True
    monkeypatch.setattr(kimi_server_client.requests, "Session", lambda: http)
    client.start()
    assert http.trust_env is False
    client.close()


def test_local_rest_reset_exhausted_budget_does_not_retry(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(kimi_server_client.time, "monotonic", lambda: clock[0])
    client = KimiServerClient(http_session_factory=lambda: None)
    calls = []
    class Http:
        def request(self, *args, **kwargs):
            calls.append(kwargs["timeout"])
            clock[0] += 0.3
            raise ConnectionResetError(10054, "budget exhausted reset")
    client._http = Http()
    with pytest.raises(KimiServerError, match="budget exhausted reset"):
        client._request("GET", "/api/sessions", timeout=0.2)
    assert len(calls) == 1


@pytest.mark.parametrize("phase", ["start", "retry"])
def test_owned_local_rest_session_is_configured_before_other_owners_can_observe_it(monkeypatch, phase):
    configuring, release = threading.Event(), threading.Event()
    class Session(FakeHttpSession):
        def __init__(self):
            super().__init__()
            self._trust_env = True
        @property
        def trust_env(self): return self._trust_env
        @trust_env.setter
        def trust_env(self, value):
            configuring.set()
            assert release.wait(2)
            self._trust_env = value
    fresh = Session()
    client, _proc, old, _ws, _calls = make_client()
    client.http_session_factory = None
    monkeypatch.setattr(kimi_server_client.requests, "Session", lambda: fresh)
    if phase == "retry":
        client._http = old
        def reset(*args, **kwargs): raise ConnectionResetError(10054, "controlled reset")
        monkeypatch.setattr(old, "request", reset)
        monkeypatch.setattr(old, "close", lambda: None, raising=False)
    expected = old if phase == "retry" else None
    errors = []
    def operation():
        try:
            if phase == "start": client.start()
            else: client._request("GET", "/api/sessions", timeout=1)
        except Exception as exc: errors.append(exc)
    worker = threading.Thread(target=operation)
    worker.start()
    try:
        assert configuring.wait(1)
        assert client._http is expected
        release.set()
        worker.join(2)
        assert not worker.is_alive() and errors == []
        assert client._http is fresh and fresh.trust_env is False
    finally:
        release.set()
        worker.join(2)
        client.close()
