"""Native wx send -> real loopback Kimi REST -> authoritative owner final.

The provider process/WS is controlled; REST serialization, socket reset recovery,
worker ownership, persistence and visible history are production paths.
"""
import json
import ctypes
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests
import main
from kimi_server_client import KimiServerClient, KimiEvent, event_to_payload
from owned_window_qa import activate_owned_frame
from test_codex_ui_responsiveness_automation import _track_ui_timers_for_test, _yield_until


@pytest.mark.parametrize("provider_failure", [False, True], ids=["get_reset_recovers", "provider_error_stays_failed"])
def test_native_send_local_rest_owner_final(request, wx_app, monkeypatch, provider_failure):
    cleanup = _track_ui_timers_for_test(monkeypatch)
    frame = request.getfixturevalue("frame")
    app_loop = main.wx.GUIEventLoop()
    activator = main.wx.EventLoopActivator(app_loop)
    calls, resets = [], []
    accepted = threading.Event()
    prompt = "QA native same prompt"
    final = "QA authoritative same prompt final"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def respond(self, data):
            body = json.dumps({"code": 0, "data": data}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            calls.append(("POST", self.path, body))
            if self.path.endswith("/prompts"):
                self.respond({"prompt_id": "qa-prompt"})
                accepted.set()
            else:
                self.respond({"id": "qa-session"})

        def do_GET(self):
            calls.append(("GET", self.path, None))
            if self.path.endswith("/messages"):
                if not resets:
                    resets.append(True)
                    # Abort an actual accepted TCP connection. Windows reports
                    # WSAECONNRESET (10054), rather than a text-only fake error.
                    self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("HH", 1, 0))
                    self.connection.close()
                    self.close_connection = True
                    return
                self.respond({"items": [
                    {"id": "qa-prompt", "role": "user", "created_at": "2026-10-04T00:00:00Z", "content": [{"type": "text", "text": prompt}]},
                    {"id": "qa-final", "role": "assistant", "created_at": "2026-10-04T00:00:01Z", "content": [{"type": "text", "text": final}]},
                ]})
            elif self.path.endswith("/status"):
                self.respond({"busy": False, "status": "completed"})
            else:
                self.respond({})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    class LocalClient(KimiServerClient):
        def start(self):
            if self._http is None:
                self._http = requests.Session()
                self._http.trust_env = False
                self.base_url = f"http://127.0.0.1:{server.server_port}"

        def subscribe(self, *_args, **_kwargs):
            pass

    client = LocalClient(on_message=frame._on_kimi_client_message)
    frame._kimi_client = client
    for name in ("_play_send_sound", "_play_finish_sound", "_schedule_first_question_auto_title", "_schedule_async_archive_rename", "_refresh_openclaw_sync_lifecycle"):
        monkeypatch.setattr(frame, name, lambda *_a, **_k: None)
    monkeypatch.setattr(main, "_wx_app_allows_ui_timers", lambda: True)
    frame._chat_store_enabled = True
    frame._on_new_chat_clicked(None)
    owner = frame.active_chat_id or frame.current_chat_id
    frame._current_chat_state.update(title="QA target", model="kimi/main", updated_at=1.0)
    frame.selected_model = "kimi/main"
    frame.model_combo.SetValue("Kimi Code")
    competitor = {"id": "qa-newer", "title": "QA newer", "model": "kimi/main", "created_at": 2.0, "updated_at": 2.0, "turns": []}
    frame.chat_store.upsert_chat(competitor)
    frame.archived_chats = [competitor]
    frame._refresh_history(owner)
    activate_owned_frame(frame)
    frame.input_edit.SetFocusFromKbd()
    wx_app.Yield()
    assert frame.history_ids[0] == "qa-newer"
    try:
        for char in prompt:
            ctypes.windll.user32.SendMessageW(int(frame.input_edit.GetHandle()), 0x0102, ord(char), 1)
            wx_app.Yield()
        assert frame.input_edit.GetValue() == prompt
        frame.send_button.SetFocusFromKbd()
        wx_app.Yield()
        ctypes.windll.user32.SendMessageW(int(frame.send_button.GetHandle()), 0x00F5, 0, 0)
        assert _yield_until(wx_app, lambda: accepted.is_set() and bool(frame.active_session_turns) and frame.active_session_turns[-1].get("kimi_prompt_id") == "qa-prompt", timeout=8), (calls, frame.active_session_turns)
        assert frame.history_ids[0] == owner
        frame.input_edit.SetFocusFromKbd()
        wx_app.Yield()
        focus = main.wx.Window.FindFocus()
        # A genuinely newer row separates receive activity from send activity.
        competitor["updated_at"] = time.time() + .001
        frame._record_chat_activity(competitor, competitor["updated_at"])
        assert frame.history_ids[0] == "qa-newer"
        frame._mark_primary_interaction()
        if provider_failure:
            client.on_message({"type": "event", "payload": {"event": event_to_payload(KimiEvent(type="error", thread_id="qa-session", turn_id="qa-prompt", text="provider rejected: 10054", data={"prompt_id": "qa-prompt", "agent_id": "main"}))}})
            assert _yield_until(wx_app, lambda: frame.active_session_turns[-1].get("request_status") == "failed", timeout=5)
            assert "provider rejected: 10054" in frame.active_session_turns[-1]["request_error"]
            assert not resets
            assert frame.history_ids[0] == "qa-newer"
        else:
            client.on_message({"type": "transport_error", "payload": {"error": "controlled stream interruption", "session_ids": ["qa-session"]}})
            assert _yield_until(wx_app, lambda: frame.active_session_turns[-1].get("request_status") == "done", timeout=8)
            assert _yield_until(wx_app, lambda: any(final in row for row in frame.answer_list.GetStrings()), timeout=5)
            assert frame.active_session_turns[-1]["answer_md"] == final
            assert frame.history_ids[0] == owner
            assert resets == [True]
            assert sum(method == "GET" and path.endswith("/messages") for method, path, _ in calls) == 2
        assert main.wx.Window.FindFocus() is focus
        submissions = [body for method, path, body in calls if method == "POST" and path.endswith("/prompts")]
        assert submissions == [{"content": [{"type": "text", "text": prompt}]}]
        frame._persist_chat_history_to_store()
        assert frame.chat_store.load_chat(owner)["turns"][-1]["question"] == prompt
    finally:
        client.close()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)
        cleanup(frame)
        del activator
