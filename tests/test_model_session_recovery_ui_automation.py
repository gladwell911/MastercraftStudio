"""Native desktop recovery against private real subprocess/protocol fixtures."""
import ctypes
from ctypes import wintypes
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


def run_native_for(milliseconds):
    loop = wx.GUIEventLoop()
    def leave():
        if loop.IsRunning():
            loop.Exit(0)
    exit_timer = wx.CallLater(milliseconds, leave)
    try:
        return loop.Run()
    finally:
        exit_timer.Stop()


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


class _KeyInput(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _MouseInput(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _HardwareInput(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _InputUnion(ctypes.Union):
    _fields_ = [("ki", _KeyInput), ("mi", _MouseInput), ("hi", _HardwareInput)]


class _Input(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("data", _InputUnion)]


def native_key_input_batch(keys, phase):
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_Input), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
    expected_size = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
    assert ctypes.sizeof(_Input) == expected_size
    batch = (_Input * len(keys))()
    for event, (vk, flags) in zip(batch, keys):
        event.type = 1
        event.data.ki = _KeyInput(vk, 0, flags, 0, 0)
    ctypes.set_last_error(0)
    inserted = user32.SendInput(len(keys), batch, ctypes.sizeof(_Input))
    print("NATIVE_SENDINPUT", phase, f"count={inserted} sizeof={ctypes.sizeof(_Input)} error={ctypes.get_last_error()}", flush=True)
    return inserted


def native_user32():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    user32.SetForegroundWindow.restype = ctypes.c_int
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    return user32


def native_modifier_state(user32):
    state = {name: bool(user32.GetAsyncKeyState(code) & 0x8000)
             for name, code in [("control", 0x11), ("left_control", 0xA2),
                                ("right_control", 0xA3), ("shift", 0x10), ("alt", 0x12)]}
    state["wx_control"] = bool(wx.GetKeyState(wx.WXK_CONTROL))
    return state


def native_focus_evidence(frame):
    focus = wx.Window.FindFocus()
    return {
        "foreground": int(native_user32().GetForegroundWindow() or 0),
        "frame_hwnd": int(frame.GetHandle()),
        "focus_type": type(focus).__name__,
        "actual_focus_hwnd": int(focus.GetHandle()) if focus is not None else None,
        "expected_input_hwnd": int(frame.input_edit.GetHandle()),
        "identity_is": focus is frame.input_edit,
        "focus_id": focus.GetId() if focus is not None else None,
        "input_focus": frame.input_edit.HasFocus(),
        "input_enabled": frame.input_edit.IsEnabled(),
        "new_chat_enabled": frame.new_chat_button.IsEnabled(),
    }


def activate_input(frame, phase):
    user32 = native_user32()
    frame._native_qa_focus_phase = phase
    deadline = time.monotonic() + 1.5
    for attempt in range(3):
        pump()
        frame.Show()
        frame.Raise()
        user32.SetForegroundWindow(ctypes.c_void_p(int(frame.GetHandle())))
        frame.input_edit.SetFocus()
        attempt_deadline = min(deadline, time.monotonic() + .5)
        while time.monotonic() < attempt_deadline:
            pump()
            focus = wx.Window.FindFocus()
            if (int(user32.GetForegroundWindow() or 0) == int(frame.GetHandle())
                    and focus is not None and int(focus.GetHandle()) == int(frame.input_edit.GetHandle())
                    and frame.input_edit.HasFocus()):
                pump()
                focus = wx.Window.FindFocus()
                if (int(user32.GetForegroundWindow() or 0) == int(frame.GetHandle())
                        and focus is not None and int(focus.GetHandle()) == int(frame.input_edit.GetHandle())
                        and frame.input_edit.HasFocus()):
                    print("NATIVE_ACTIVATION", phase, "attempt", attempt + 1, native_focus_evidence(frame), flush=True)
                    return
            time.sleep(.01)
    print("NATIVE_ACTIVATION_FAILED", phase, native_focus_evidence(frame), flush=True)
    raise AssertionError("native-activation precondition failed: " + phase)


def send(frame, text):
    activate_input(frame, "send-before:" + text)
    frame.input_edit.ChangeValue(text)
    event = wx.CommandEvent(wx.wxEVT_BUTTON, frame.send_button.GetId())
    event.SetEventObject(frame.send_button)
    wx.PostEvent(frame.send_button, event)
    frame._native_qa_focus_phase = "send-after:" + text
    pump()


def alt_a(frame):
    activate_input(frame, "alt-a-before")
    observed = frame._native_qa_observed
    before_keys = len(observed["keys"])
    before_menus = len(observed["menus"])
    before_clear = len(observed["clear"])
    user32 = native_user32()
    before_modifiers = native_modifier_state(user32)
    simulator = wx.UIActionSimulator()
    control_up = None
    if any(before_modifiers[name] for name in ("control", "left_control", "right_control")):
        control_up = simulator.KeyUp(wx.WXK_CONTROL)
    print("TEST_NATIVE_CTRL_NORMALIZE origin=unknown previous_test_or_external_state",
          "before", before_modifiers, "generic_keyup", control_up, flush=True)
    normalize_deadline = time.monotonic() + .3
    while time.monotonic() < normalize_deadline:
        run_native_for(20)
        after_modifiers = native_modifier_state(user32)
        if not any(after_modifiers.values()):
            break
    after_modifiers = native_modifier_state(user32)
    print("TEST_NATIVE_CTRL_NORMALIZE after", after_modifiers, flush=True)
    assert control_up is None or control_up
    assert not any(after_modifiers.values()), "native modifier interference after one controlled normalization"
    inserted = None
    loop = wx.GUIEventLoop()
    injected_results = []
    injected_errors = []

    def inject_once():
        try:
            active_loop = wx.EventLoopBase.GetActive()
            print("NATIVE_INJECT_ACTIVE_LOOP", f"is_target={active_loop is loop} running={loop.IsRunning()} type={type(active_loop).__name__}", flush=True)
            assert active_loop is loop and loop.IsRunning()
            frame._native_qa_focus_phase = "alt-a-running-loop-inject"
            count = native_key_input_batch([(0x12, 0), (0x41, 0), (0x41, 2), (0x12, 2)], "alt-a-single-gesture")
            injected_results.append(count)
            assert count == 4, "SendInput did not insert the complete Alt+A gesture"
        except BaseException as error:
            injected_errors.append(error)

    def leave():
        if loop.IsRunning():
            loop.Exit(0)

    inject_timer = wx.CallLater(1, inject_once)
    exit_timer = wx.CallLater(500, leave)
    try:
        try:
            loop.Run()
        finally:
            inject_timer.Stop()
            exit_timer.Stop()
        inserted = injected_results[0] if injected_results else None
        if injected_errors:
            raise injected_errors[0]
        assert injected_results == [4], "native loop did not execute exactly one complete gesture"
        menu_received = any(item["id"] == int(frame._clear_context_id) for item in observed["menus"][before_menus:])
        clear_delta = len(observed["clear"]) - before_clear
        char_received = any(item["key"] in (ord("A"), ord("a")) and item["alt"] and not item["ctrl"]
                            for item in observed["keys"][before_keys:])
        print("NATIVE_ACCELERATOR_ROUTE", f"menu_received={menu_received} clear_delta={clear_delta} supplemental_char_a_alt_ctrl_false={char_received}", flush=True)
        if clear_delta != 1:
            print("NATIVE_ALT_A_MISSING_ROUTE", observed, native_focus_evidence(frame), flush=True)
            raise AssertionError("deadline exceeded: native-alt-a-single-clear-route")
        print("QA_PHASE native-alt-a-single-clear-route passed", flush=True)
    finally:
        run_native_for(20)
        keys_still_down = bool(user32.GetAsyncKeyState(0x41) & 0x8000) or bool(user32.GetAsyncKeyState(0x12) & 0x8000)
        if inserted != 4 or keys_still_down:
            released = native_key_input_batch([(0x41, 2), (0x12, 2)], "emergency-keyup-only")
            run_native_for(20)
            assert released == 2, "SendInput keyup cleanup incomplete"
        print("NATIVE_ALT_A_RELEASE", "a_down", bool(user32.GetAsyncKeyState(0x41) & 0x8000),
              "modifiers", native_modifier_state(user32), flush=True)
        assert not (user32.GetAsyncKeyState(0x41) & 0x8000)
        assert not (user32.GetAsyncKeyState(0x12) & 0x8000)
    print("NATIVE_ALT_A_PATH", observed, native_focus_evidence(frame), flush=True)


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
    run_native_for(50)
    activate_input(frame, "ready:" + model)


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
        visible_wait_started = time.monotonic()
        try:
            wait_for(
                lambda: any("ANSWER:recovered-kimi-unique" in row for row in frame.answer_list.GetStrings()),
                "kimi-final-visible", timeout=1.6,
            )
        except AssertionError:
            print(
                "KIMI_FINAL_VISIBLE_TIMEOUT",
                f"rows={list(frame.answer_list.GetStrings())}",
                f"canonical={frame.active_session_turns[-1].get('answer_md')}",
                f"chat_id={frame.active_chat_id} expected_chat_id={chat_id}",
                f"deadline_owner={frame._answer_refresh_deadline_owner}",
                f"deadline_timer={frame._answer_refresh_deadline_timer}",
                f"quiet_until={frame._navigation_quiet_until}",
                flush=True,
            )
            raise
        visible_wait_ms = (time.monotonic() - visible_wait_started) * 1000
        print(f"KIMI_FINAL_VISIBLE completion_observed_to_visible_ms={visible_wait_ms:.1f}", flush=True)
        assert visible_wait_ms < 1600
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
    print("NATIVE_READY_BEFORE_FIRST_SEND modifiers origin=unknown", native_modifier_state(native_user32()), flush=True)
    observed = {"keys": [], "menus": [], "clear": [], "durable": [], "focus": [], "refresh": []}
    frame._native_qa_observed = observed

    def observe_char(event):
        key = event.GetKeyCode()
        if key in (ord("A"), ord("a"), wx.WXK_ALT):
            record = {"key": key, "alt": event.AltDown(), "ctrl": event.ControlDown(),
                      "at": time.monotonic(), **native_focus_evidence(frame)}
            observed["keys"].append(record)
            print("NATIVE_CHAR_HOOK", record, flush=True)
        event.Skip()

    frame.Bind(wx.EVT_CHAR_HOOK, observe_char)
    def observe_menu(event):
        record = {"id": event.GetId(), "at": time.monotonic(), **native_focus_evidence(frame)}
        observed["menus"].append(record)
        print("NATIVE_CLEAR_MENU", record, flush=True)
        event.Skip()
    frame.Bind(wx.EVT_MENU, observe_menu, id=int(frame._clear_context_id))
    actual_clear = frame._clear_context_and_start_new_chat
    def observe_clear(*args, **kwargs):
        record = {"auto_resend_first": kwargs.get("auto_resend_first", args[0] if args else False),
                  "at": time.monotonic(), **native_focus_evidence(frame)}
        observed["clear"].append(record)
        print("NATIVE_CLEAR_ENTER", record, flush=True)
        result = actual_clear(*args, **kwargs)
        print("NATIVE_CLEAR_RETURN", result, native_focus_evidence(frame), flush=True)
        return result
    monkeypatch.setattr(frame, "_clear_context_and_start_new_chat", observe_clear)

    def observe_focus(event):
        record = {"event": "set" if event.GetEventType() == wx.wxEVT_SET_FOCUS else "kill",
                  "phase": getattr(frame, "_native_qa_focus_phase", ""),
                  "at": time.monotonic(), **native_focus_evidence(frame)}
        next_window = event.GetWindow()
        record["next_type"] = type(next_window).__name__
        record["next_id"] = next_window.GetId() if next_window is not None else None
        observed["focus"].append(record)
        print("NATIVE_INPUT_FOCUS", record, flush=True)
        event.Skip()
    frame.input_edit.Bind(wx.EVT_SET_FOCUS, observe_focus)
    frame.input_edit.Bind(wx.EVT_KILL_FOCUS, observe_focus)
    for name in ("_focus_latest_answer", "_refresh_answer_list_preserving_selection"):
        original_focus_method = getattr(frame, name)
        def observe_method(*args, _name=name, _actual=original_focus_method, **kwargs):
            before = native_focus_evidence(frame)
            result = _actual(*args, **kwargs)
            record = {"method": _name, "phase": getattr(frame, "_native_qa_focus_phase", ""),
                      "before": before, "after": native_focus_evidence(frame)}
            observed["refresh"].append(record)
            print("NATIVE_REFRESH_FOCUS", record, flush=True)
            return result
        monkeypatch.setattr(frame, name, observe_method)
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
            observed["durable"].append({"attempt": "success", "at": time.monotonic()})
            print("NATIVE_DURABLE_ENTER", "success", flush=True)
            result = original(*args, **kwargs)
            operations.append(result)
            return result
        def durable_failure(*args, **kwargs):
            observed["durable"].append({"attempt": "failure", "at": time.monotonic()})
            print("NATIVE_DURABLE_ENTER", "failure OSError", flush=True)
            raise OSError("controlled durable failure")
        monkeypatch.setattr(frame, "_begin_durable_clear_operation", durable_failure)
        alt_a(frame)
        try:
            wait_for(lambda: "Unable to clear context safely" in frame.GetStatusBar().GetStatusText(), "clear-failure-visible")
        except AssertionError:
            print("NATIVE_CLEAR_FAILURE_DIAGNOSTIC", observed, "status", frame.GetStatusBar().GetStatusText(), flush=True)
            raise
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
        wait_for(lambda: any("ANSWER:first" in row for row in frame.answer_list.GetStrings()), "codex-clear-answer-visible")
        frame._on_new_chat_clicked(None)
        assert frame._switch_current_chat(chat_id)
        assert sum("ANSWER:first" in row for row in frame.answer_list.GetStrings()) == 1
        frame.input_edit.SetFocus()
        pump()
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
        settle_deadline = time.monotonic() + .3
        while time.monotonic() < settle_deadline:
            pump()
            assert int(native_user32().GetForegroundWindow() or 0) == int(frame.GetHandle()), "external foreground activation interference"
            if frame.input_edit.HasFocus():
                break
            time.sleep(.01)
        print("NATIVE_FINAL_FOCUS", native_focus_evidence(frame), "observed", observed, flush=True)
        assert frame.input_edit.HasFocus()
        print("QA_CODEX", "active" if active else "idle", chat_id, operation_id, operation["revision"], old_thread, frame.active_codex_thread_id, flush=True)
    finally:
        frame.Unbind(wx.EVT_CHAR_HOOK, handler=observe_char)
        frame.Unbind(wx.EVT_MENU, id=int(frame._clear_context_id), handler=observe_menu)
        frame.input_edit.Unbind(wx.EVT_SET_FOCUS, handler=observe_focus)
        frame.input_edit.Unbind(wx.EVT_KILL_FOCUS, handler=observe_focus)
        (folder / "release-old-codex").touch()
        stop_owned(frame, clients)


def test_kimi_clear_active_runtime_final_visible_after_switch(frame, monkeypatch, tmp_path):
    clients = []
    folder = tmp_path / "kimi-clear-private"
    folder.mkdir()
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
        send(frame, "clear-kimi-unique")
        chat_id = frame.active_chat_id
        wait_for(lambda: any(item["kind"] == "kimi_submit" for item in events(folder)), "kimi-old-active-submit")
        wait_for(lambda: bool(frame.active_kimi_session_id), "kimi-old-owner-ack")
        old_session = frame.active_kimi_session_id
        assert frame._clear_context_and_start_new_chat(auto_resend_first=True)
        wait_for(lambda: len([item for item in events(folder) if item["kind"] == "kimi_submit"]) == 2,
                 "kimi-clear-resend-submit")
        wait_for(lambda: frame.active_kimi_session_id and frame.active_kimi_session_id != old_session,
                 "kimi-clear-fresh-owner")
        frame._on_new_chat_clicked(None)
        away_id = frame.active_chat_id
        (folder / "release-kimi").touch()
        wait_for(lambda: frame._find_archived_chat(chat_id)["turns"][0].get("request_status") == "done",
                 "kimi-clear-authoritative-final")
        assert frame.active_chat_id == away_id
        assert not any("ANSWER:clear-kimi-unique" in row for row in frame.answer_list.GetStrings())
        assert frame._switch_current_chat(chat_id)
        wait_for(lambda: any("ANSWER:clear-kimi-unique" in row for row in frame.answer_list.GetStrings()),
                 "kimi-clear-final-visible")
        assert len(frame.active_session_turns) == 1
        assert sum("ANSWER:clear-kimi-unique" in row for row in frame.answer_list.GetStrings()) == 1
        turn = frame.active_session_turns[0]
        assert frame.chat_store.get_clear_operation(turn["clear_operation_id"])["state"] == "completed_with_resend"
        assert frame.active_kimi_session_id != old_session
    finally:
        stop_owned(frame, clients)
