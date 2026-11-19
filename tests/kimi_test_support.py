import json
import main
import pytest
import wx
from execution_projection import project_execution_rows
from kimi_server_client import KimiEvent, KimiServerClient, event_to_payload


TEST_SESSION_ID = "session-test-1"


TEST_TURN_ID = "1"


class _ImmediateThread:
    def __init__(self, target=None, args=None, kwargs=None, daemon=None, name=None):
        self._target = target
        self._args = args or ()
        self._kwargs = kwargs or {}

    def start(self):
        if self._target:
            self._target(*self._args, **self._kwargs)

    def join(self, timeout=None):
        return None

    def is_alive(self):
        return False


class FakeKimiServerClient:
    """Records calls and lets tests push events through the on_message callback."""

    def __init__(self, on_message=None, on_exit=None, **_kwargs):
        self.on_message = on_message
        self.on_exit = on_exit
        self.started = 0
        self.closed = 0
        self.created_sessions = []
        self.subscribe_calls = []
        self.submitted = []
        self.steer_calls = []
        self.steer_result = True
        self.abort_calls = []
        self.approval_answers = []
        self.list_approval_calls = 0
        self.status_by_session = {}
        self.messages_by_session = {}
        self.list_messages_calls = []
        self.session_exists_result = True
        self.pending_messages = []
        self._prompt_counter = 0
        self._session_counter = 0

    def start(self):
        self.started += 1

    def close(self):
        self.closed += 1

    def create_session(self, *, cwd, model="", title="", permission_mode="auto", goal_objective=""):
        self._session_counter += 1
        session_id = f"session-fake-{self._session_counter}"
        self.created_sessions.append(
            {
                "session_id": session_id,
                "cwd": cwd,
                "model": model,
                "title": title,
                "permission_mode": permission_mode,
            }
        )
        return session_id

    def subscribe(self, session_ids):
        self.subscribe_calls.append(list(session_ids))

    def session_exists(self, session_id):
        known = any(entry["session_id"] == session_id for entry in self.created_sessions)
        return bool(known and self.session_exists_result)

    def submit_prompt(self, session_id, content_blocks):
        self._prompt_counter += 1
        prompt_id = f"prompt-{self._prompt_counter}"
        self.submitted.append(
            {"session_id": session_id, "blocks": list(content_blocks or []), "prompt_id": prompt_id}
        )
        return prompt_id

    def steer_prompts(self, session_id, prompt_ids):
        self.steer_calls.append((session_id, [str(p) for p in prompt_ids]))
        return self.steer_result

    def abort(self, session_id, prompt_id=""):
        self.abort_calls.append((session_id, prompt_id))

    def get_status(self, session_id):
        return dict(self.status_by_session.get(session_id) or {"context_tokens": 128, "max_context_tokens": 2048})

    def list_messages(self, session_id, before_id=None, timeout=None):
        self.list_messages_calls.append((session_id, before_id))
        messages = [dict(m) for m in (self.messages_by_session.get(session_id) or [])]
        if before_id:
            ids = [str(m.get("id") or "") for m in messages]
            if before_id in ids:
                messages = messages[: ids.index(before_id)]
        return messages[-50:]

    def list_approvals(self, session_id):
        self.list_approval_calls += 1
        return [{"id": "approval-from-list"}]

    def answer_approval(self, session_id, approval_id, decision, **_kwargs):
        self.approval_answers.append((session_id, approval_id, decision))

    def drain_pending_messages(self, limit=100):
        drained = self.pending_messages[:limit]
        del self.pending_messages[:limit]
        return drained

    # test helpers ------------------------------------------------------

    def push_event(self, event: KimiEvent):
        assert self.on_message is not None
        self.on_message({"type": "event", "payload": {"event": event_to_payload(event)}})


def _make_fake_client(monkeypatch):
    fake = FakeKimiServerClient()

    def factory(on_message=None, on_exit=None, **_kwargs):
        fake.on_message = on_message
        fake.on_exit = on_exit
        return fake

    monkeypatch.setattr(main, "KimiServerClient", factory)
    return fake


def _setup_kimi_frame(frame, monkeypatch):
    fake = _make_fake_client(monkeypatch)
    monkeypatch.setattr(main.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(main.wx, "CallLater", lambda _delay, fn, *a, **k: (fn(*a, **k), True)[1])
    # Presentation deadlines stay deferred even though this provider fixture
    # executes legacy callbacks immediately. Keep a real timer-handle contract.
    immediate_call_later = frame._call_later_if_alive
    frame._test_answer_deadline_handles = []

    class DeferredDeadline:
        def __init__(self, delay, callback, args, kwargs):
            self.delay = delay
            self.callback = callback
            self.args = args
            self.kwargs = kwargs
            self.running = True

        def Stop(self):
            self.running = False

        def IsRunning(self):
            return self.running

    def call_later(delay, callback, *args, **kwargs):
        if callback == frame._flush_accepted_answer_refresh:
            handle = DeferredDeadline(delay, callback, args, kwargs)
            frame._test_answer_deadline_handles.append(handle)
            return handle
        return immediate_call_later(delay, callback, *args, **kwargs)

    monkeypatch.setattr(frame, "_call_later_if_alive", call_later)
    frame._refresh_openclaw_sync_lifecycle = lambda force_replay=False: None
    frame._play_send_sound = lambda: None
    frame._schedule_first_question_auto_title = lambda *a, **k: None
    frame._kimi_reconcile_backoff = 0
    frame.model_combo.SetValue("Kimi Code")
    frame.selected_model = "kimi/main"
    return fake


def _submit(frame, text):
    frame.input_edit.SetValue(text)
    frame._on_send_clicked(None)


def _active_chat_id(frame):
    return str(frame.active_chat_id or frame.current_chat_id or "").strip()


def _sdk_push(frame, transport, session_id, kind, seq, *, agent="main", offset=None, chat_id=None, **body):
    message = {"type": kind, "session_id": session_id, "epoch": "sdk-epoch", "seq": seq,
               "payload": {"type": kind, "agentId": agent, "turnId": 1, **body}}
    if offset is not None:
        message.update(offset=offset, volatile=True)
    transport._handle_ws_message(message)
    for queued in transport.drain_pending_messages(100):
        frame._on_kimi_event_for_chat(chat_id or _active_chat_id(frame), main.CodexEvent(**queued["payload"]["event"]))
    return message


def _canonical_kimi_rows(frame):
    frame._flush_execution_step_persists_sync()
    source = frame.chat_store.read_execution_projection_source(_active_chat_id(frame))
    rows = project_execution_rows(
        chat_id=_active_chat_id(frame), revision=source["revision"], steps=source["steps"], turns=source["turns"],
        view_mode="history", active_turn_index=-1, selected_model="kimi/main", requesting_text=main.REQUESTING_TEXT,
        answer_to_plain=lambda answer, _model: answer,
    )
    return [row for row in rows if ":logical:" in row["row_id"]]


def _injection(message_id="injected", **fields):
    return {"id": message_id, "role": "user", "content": "ordinary reminder text",
            "metadata": {"origin": {"kind": "injection", "variant": "agents"}}, **fields}


@pytest.fixture
def running_kimi_control_server(monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    import requests

    requests_seen = []
    interaction_reads = []
    abort_results = []

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, data, *, code=0, status=200):
            payload = json.dumps({"code": code, "data": data, "msg": "synthetic rejection" if code else ""}).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            requests_seen.append((self.path, "GET"))
            state = interaction_reads.pop(0)
            if state is None:
                self._reply({}, status=500)
                return
            post_path = next(path for path, body in reversed(requests_seen) if body != "GET")
            self._reply({"interactions": [{"interactionId": post_path.rsplit("/", 1)[-1], "state": state}]})

        def do_POST(self):
            size = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(size)) if size else None
            requests_seen.append((self.path, body))
            if "interaction-retry" in self.path and sum(path == self.path for path, _ in requests_seen) == 1:
                if self.path.endswith(("interaction-retry", "retry_ended_wire")):
                    self._reply({}, code=40001)
                else:
                    self._reply({}, status=500)
                return
            if self.path.endswith(":abort"):
                data = {"aborted": abort_results.pop(0) if abort_results else True}
            elif body["kind"] == "approval":
                data = {"outcome": body["decision"], "ended_at": "2026-11-19T08:00:00Z"}
            else:
                data = {"outcome": "dismissed" if body.get("dismissed") else "answered",
                        "ended_at": "2026-11-19T08:00:00Z"}
            self._reply(data)

        def log_message(self, _format, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = KimiServerClient()
    client.protocol_version = 2
    client.base_url = f"http://127.0.0.1:{server.server_port}"
    client._http = requests.Session()
    client._http.trust_env = False
    monkeypatch.setattr(client, "start", lambda: None)
    try:
        yield client, requests_seen, interaction_reads, abort_results
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        thread.join(2)


def _retry_kimi_interaction_menu(frame):
    menu_id = int(frame._kimi_retry_interaction_menu_id)
    frame._on_chat_information_menu_open(wx.MenuEvent(wx.wxEVT_MENU_OPEN))
    assert frame.GetMenuBar().FindItemById(menu_id).IsEnabled()
    frame.GetEventHandler().ProcessEvent(wx.CommandEvent(wx.wxEVT_MENU, menu_id))
