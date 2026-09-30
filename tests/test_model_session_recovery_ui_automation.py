"""Native desktop recovery against private real subprocess/protocol fixtures."""
import ctypes
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest
import wx

import main
from codex_worker_client import CodexWorkerClient
from kimi_server_client import KimiServerClient

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/epic3_model_session_recovery_qa.py"


@pytest.fixture(autouse=True)
def native_gui_loop(wx_app):
    loop = wx.GUIEventLoop()
    activator = wx.EventLoopActivator(loop)
    wx_app._epic3_qa_loop = loop
    yield
    del activator


def pump():
    app = wx.GetApp()
    loop = app._epic3_qa_loop
    while loop.Pending():
        loop.Dispatch()
    app.ProcessPendingEvents()
    loop.ProcessIdle()


def wait_for(predicate, phase, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pump()
        if predicate():
            print("QA_PHASE", phase, "passed", flush=True)
            return
        time.sleep(.01)
    raise AssertionError("deadline exceeded: " + phase)


def events(folder):
    path = folder / "protocol.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def send(frame, text):
    frame.input_edit.ChangeValue(text)
    frame.input_edit.SetFocus()
    event = wx.CommandEvent(wx.wxEVT_BUTTON, frame.send_button.GetId())
    event.SetEventObject(frame.send_button)
    wx.PostEvent(frame.send_button, event)
    pump()


def alt_a(frame):
    frame.Raise()
    frame.input_edit.SetFocus()
    pump()
    simulator = wx.UIActionSimulator()
    ctypes.WinDLL("user32", use_last_error=True).SetForegroundWindow(int(frame.GetHandle()))
    pump()
    assert simulator.Char(ord("A"), wx.MOD_ALT)
    pump()


def ready(frame, monkeypatch, model):
    frame._refresh_openclaw_sync_lifecycle = lambda **kw: None
    frame._schedule_first_question_auto_title = lambda *a, **kw: None
    frame._workspace_dir_for_codex = lambda: str(Path(frame.state_path).parent)
    frame._workspace_dir_for_kimi = lambda: str(Path(frame.state_path).parent)
    frame._play_send_sound = lambda: None
    frame._play_finish_sound = lambda: None
    frame.model_combo.SetValue(main.model_display_name(model))
    frame.selected_model = model
    wx.GetApp().SetTopWindow(frame)
    frame.Show()
    frame.Raise()
    ctypes.WinDLL("user32", use_last_error=True).SetForegroundWindow(int(frame.GetHandle()))
    pump()


def stop_owned(frame, clients):
    for client in clients:
        client.close()
    for value in vars(frame).values():
        if isinstance(value, wx.Timer):
            value.Stop()
    frame.Hide()
    pump()


def test_kimi_actual_startup_exit_and_recovery_visible(frame, monkeypatch, tmp_path):
    clients = []
    folder = tmp_path / "kimi-private"
    folder.mkdir()
    (folder / "fail-start").touch()
    def factory(on_message=None, on_exit=None, **kw):
        client = KimiServerClient(on_message=on_message, on_exit=on_exit,
            launch_command=[sys.executable, str(SCRIPT), "--kimi-fixture", str(folder)],
            token="private-epic3", health_timeout=3, rest_timeout=3,
            recovery_attempts=1, recovery_backoff=0)
        clients.append(client)
        return client
    monkeypatch.setattr(main, "KimiServerClient", factory)
    ready(frame, monkeypatch, "kimi/main")
    frame._kimi_reconcile_backoff = 0
    frame._kimi_reconcile_attempts = 2
    try:
        send(frame, "startup-fault")
        wait_for(lambda: frame.active_session_turns and frame.active_session_turns[-1].get("request_status") == "failed", "kimi-startup-terminal")
        chat_id = frame.active_chat_id
        assert not frame.is_running
        assert "startup" in frame.active_session_turns[-1]["answer_md"]
        assert frame.input_edit.IsEnabled() and frame.send_button.IsEnabled()
        assert any("startup" in row for row in frame.answer_list.GetStrings())
        assert sum(item["kind"] == "startup_exit" for item in events(folder)) >= 2
        assert not any(item["kind"] == "kimi_submit" for item in events(folder))
        (folder / "fail-start").unlink()
        send(frame, "recovered-kimi-unique")
        wait_for(lambda: any(item["kind"] == "kimi_submit" for item in events(folder)), "kimi-submit-after-recovery")
        (folder / "release-kimi").touch()
        wait_for(lambda: frame.active_session_turns[-1].get("request_status") == "done", "kimi-authoritative-final")
        assert frame.active_chat_id == chat_id
        assert frame.active_session_turns[-1]["answer_md"] == "ANSWER:recovered-kimi-unique"
        assert any("ANSWER:recovered-kimi-unique" in row for row in frame.answer_list.GetStrings())
        assert not frame.is_running
        assert frame.input_edit.HasFocus()
        assert any(item["kind"] == "kimi_control" and item["type"] == "client_hello" for item in events(folder))
        assert any(item["kind"] == "kimi_control" and item["type"] == "subscribe" for item in events(folder))
        print("QA_KIMI", chat_id, frame.active_kimi_session_id, "real-process-http-ws", flush=True)
    finally:
        stop_owned(frame, clients)


@pytest.mark.parametrize("active", [False, True], ids=["idle", "active"])
def test_codex_native_alt_a_new_thread_and_stale_terminal(frame, monkeypatch, tmp_path, active):
    clients = []
    folder = tmp_path / "codex-private"
    folder.mkdir()
    def factory(on_message=None, on_exit=None, **kw):
        def launch(_args):
            return subprocess.Popen([sys.executable, str(SCRIPT), "--worker-fixture", str(folder)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", bufsize=1, cwd=str(SCRIPT.parent.parent),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        client = CodexWorkerClient(on_message=on_message, on_exit=on_exit, process_factory=launch)
        clients.append(client)
        return client
    monkeypatch.setattr(main, "CodexWorkerClient", factory)
    ready(frame, monkeypatch, "codex/main")
    received_terminals = []
    original_receive = frame._on_codex_worker_message
    def observe_receive(chat_id, message, client=None):
        original_receive(chat_id, message, client)
        event = (message.get("payload") or {}).get("event") or {}
        if event.get("type") == "turn_completed":
            received_terminals.append(event.get("turn_id"))
    monkeypatch.setattr(frame, "_on_codex_worker_message", observe_receive)
    try:
        for prompt in ["first", "second"]:
            send(frame, prompt)
            wait_for(lambda: frame.active_session_turns and frame.active_session_turns[-1].get("request_status") == "done", "codex-multiturn-"+prompt)
            assert frame.active_session_turns[-1]["answer_md"] == "ANSWER:"+prompt
        chat_id = frame.active_chat_id
        old_thread = frame.active_codex_thread_id
        generation = int(frame._current_chat_state.get("codex_context_generation") or 0)
        if active:
            send(frame, "HOLD")
            wait_for(lambda: any(item["kind"] == "codex_submit" and item["question"] == "HOLD" for item in events(folder)), "codex-active-gate")
            assert frame.is_running
        # Real keyboard failure path must leave the durable context intact.
        original = frame._begin_durable_clear_operation
        operations = []
        def capture_operation(*args, **kwargs):
            result = original(*args, **kwargs)
            operations.append(result)
            return result
        monkeypatch.setattr(frame, "_begin_durable_clear_operation", lambda *a, **kw: (_ for _ in ()).throw(OSError("controlled durable failure")))
        alt_a(frame)
        wait_for(lambda: "Unable to clear context safely" in frame.GetStatusBar().GetStatusText(), "clear-failure-visible")
        assert frame.active_codex_thread_id == old_thread
        assert len(frame.active_session_turns) == (3 if active else 2)
        monkeypatch.setattr(frame, "_begin_durable_clear_operation", capture_operation)
        alt_a(frame)
        wait_for(lambda: operations and len(frame.active_session_turns) == 1 and frame.active_session_turns[0].get("request_status") == "done", "native-alt-a-clear-and-resend")
        assert frame.active_session_turns[0]["question"] == "first"
        assert frame.active_session_turns[0]["answer_md"] == "ANSWER:first"
        assert frame.active_codex_thread_id != old_thread
        assert frame.active_chat_id == chat_id
        assert int(frame._current_chat_state.get("codex_context_generation") or 0) > generation
        operation_id = str(operations[-1]["operation_id"])
        operation = frame.chat_store.get_clear_operation(operation_id)
        assert operation and operation["state"] == "completed_with_resend"
        assert frame.answer_list.GetCount() > 0
        send(frame, "post-clear-unique")
        wait_for(lambda: frame.active_session_turns and frame.active_session_turns[-1].get("request_status") == "done", "codex-new-context-final")
        assert frame.active_session_turns[-1]["answer_md"] == "ANSWER:post-clear-unique"
        assert frame.active_codex_thread_id != old_thread
        wait_for(lambda: any("ANSWER:post-clear-unique" in row for row in frame.answer_list.GetStrings()), "codex-visible-final")
        assert any("ANSWER:post-clear-unique" in row for row in frame.answer_list.GetStrings())
        (folder / "release-old-codex").touch()
        if active:
            old_turn_id = next(item["turn"] for item in events(folder) if item["kind"] == "codex_submit" and item["question"] == "HOLD")
            wait_for(lambda: old_turn_id in received_terminals, "old-terminal-received-by-desktop")
            pump()
        assert len(frame.active_session_turns) == 2
        assert frame.active_session_turns[-1]["answer_md"] == "ANSWER:post-clear-unique"
        assert not frame.is_running
        assert frame.input_edit.HasFocus()
        print("QA_CODEX", "active" if active else "idle", chat_id, operation_id, operation["revision"], old_thread, frame.active_codex_thread_id, flush=True)
    finally:
        (folder / "release-old-codex").touch()
        stop_owned(frame, clients)
