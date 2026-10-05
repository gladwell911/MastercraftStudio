"""Isolated real Windows MC / NATS runtime for the read-state device E2E.

Only the Kimi model process is controlled. Commands, provider event ownership,
completion, persistence, desktop input and remote publication are product paths.
The loopback controller never writes read state or cancels Android notifications.
"""
from __future__ import annotations

import argparse
import asyncio
import ctypes
from ctypes import wintypes
import json
import hashlib
import os
import sys
import subprocess
import threading
import time
import traceback
import uuid
import re
import xml.etree.ElementTree as ET
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
import wx
import main
from kimi_server_client import KimiEvent
from nats_runtime import NatsRuntimeConfig, NatsServerProcess
from remote_nats import RemoteNatsTransport
from owned_window_qa import activate_owned_frame
from test_kimi_integration import FakeKimiServerClient
from test_model_session_recovery_ui_automation import native_key_input_batch, native_modifier_state, native_user32
from real_desktop_remote_e2e_runtime import _choose_available_port


def source_evidence(root):
    """Identify the actual working tree, including newly added source files."""
    def git(*arguments):
        return subprocess.check_output(["git", "-C", str(root), *arguments])
    digest = hashlib.sha256()
    files = sorted(set(git("ls-files", "-z", "--cached", "--others", "--exclude-standard").split(b"\0")) - {b""})
    for name in files:
        path = root / os.fsdecode(name)
        if path.is_file():
            digest.update(name + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    return {"head": git("rev-parse", "HEAD").decode().strip(),
        "diff_sha256": hashlib.sha256(git("diff", "HEAD", "--binary")).hexdigest(),
        "source_sha256": digest.hexdigest()}


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    folder = args.directory.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    os.environ.update(REMOTE_CONTROL_AUTOSTART="0", AUTO_START_QUICK_TUNNEL="0", DESKTOP_FILE_SERVICE_AUTOSTART="0")
    main.resolve_app_data_dir = lambda: folder / "app"
    main.resolve_notes_data_dir = lambda: folder / "notes"
    main.ChatFrame._legacy_state_paths = lambda self: [self.state_path]
    main.GlobalCtrlTapHook.start = lambda self: None
    main.GlobalCtrlTapHook.stop = lambda self: None
    key_observations = []
    key_injections = []
    ctypes.windll.user32.GetMessageExtraInfo.restype = ctypes.c_ssize_t
    for name in ("_on_char_hook", "_on_answer_key_down"):
        original = getattr(main.ChatFrame, name)
        def observe(self, event, method=original, label=name):
            key_observations.append({"entry": label, "key": event.GetKeyCode(), "raw": event.GetRawKeyCode(),
                "selection": self.answer_list.GetSelection(), "answer_focused": self.answer_list.HasFocus(),
                "input_focused": self.input_edit.HasFocus(), "time": time.time(),
                "message_extra_info": int(ctypes.windll.user32.GetMessageExtraInfo()),
                "fixture_injection_count": len(key_injections)})
            result = method(self, event)
            focus = wx.Window.FindFocus()
            key_observations.append({"entry": label + ":after", "key": event.GetKeyCode(),
                "focus_hwnd": int(focus.GetHandle()) if focus else 0, "skipped": event.GetSkipped(), "time": time.time()})
            return result
        setattr(main.ChatFrame, name, observe)
    app = wx.App(False)
    frame = main.ChatFrame()
    frame.SetTitle("MC Read State E2E")
    for control_name in ("answer_list", "input_edit", "history_list"):
        def observe_focus(event, name=control_name):
            key_observations.append({"entry": "focus:" + name, "time": time.time(),
                "trace": traceback.format_stack(limit=6)})
            event.Skip()
        getattr(frame, control_name).Bind(wx.EVT_SET_FOCUS, observe_focus)
    for name in ("_play_send_sound", "_play_finish_sound", "_schedule_first_question_auto_title", "_schedule_async_archive_rename", "_refresh_openclaw_sync_lifecycle"):
        setattr(frame, name, lambda *a, **k: None)
    frame._chat_store_enabled = True
    tts = []
    real_speak = frame._speak_text_via_screen_reader
    def speak(text):
        tts.append(text)
        return real_speak(text)
    frame._speak_text_via_screen_reader = speak
    browser_evidence = []
    def proxy(operation, parameters, expression=None):
        request = Request("http://127.0.0.1:3456/" + operation + "?" + urlencode(parameters),
            data=expression.encode("utf-8") if expression is not None else None)
        with urlopen(request, timeout=15) as response:
            return json.load(response)
    def open_detail(page_path):
        url = page_path.resolve().as_uri()
        target = proxy("new", {"url": url})["targetId"]
        try:
            observed = json.loads(proxy("eval", {"target": target},
                "JSON.stringify({url:location.href,ready:document.readyState,text:document.body.innerText})")["value"])
            expected = frame._selected_answer_source_text()
            if observed["url"] != url or observed["ready"] != "complete" or expected not in observed["text"]:
                raise RuntimeError("Actual headless browser did not load selected answer detail")
            browser_evidence.append({"path": str(page_path), "target": target, **observed})
            return True
        finally:
            proxy("close", {"target": target})
    # This is the external browser adapter only. Product key handling and HTML
    # generation remain real; the shared skill-owned headless browser is kept.
    frame._open_local_webpage = open_detail

    class ControlledKimi(FakeKimiServerClient):
        def submit_prompt(self, session_id, content_blocks):
            prompt = super().submit_prompt(session_id, content_blocks)
            question = content_blocks[0].get("text", "")
            answer = "READ E2E answer " + question
            def finish():
                self.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id=prompt))
                self.push_event(KimiEvent(type="agent_message_delta", thread_id=session_id, turn_id=prompt, text=answer, display_kind="assistant"))
                self.push_event(KimiEvent(type="turn_completed", thread_id=session_id, turn_id=prompt, status="completed"))
            threading.Timer(.35, finish).start()
            return prompt
    main.KimiServerClient = ControlledKimi

    token = "read-e2e-" + uuid.uuid4().hex
    tcp = _choose_available_port(4522, (4523, 4524))
    websocket = _choose_available_port(18082, (18083, 18084))
    server = NatsServerProcess(NatsRuntimeConfig(app_data_dir=folder / "nats", token=token,
        host="127.0.0.1", port=tcp, websocket_host="127.0.0.1", websocket_port=websocket))
    server.start(timeout=20)
    route = lambda name: lambda payload: frame._run_remote_ui_route(getattr(frame, name), payload)
    history_delay = {"seconds": 0, "started": 0, "finished": 0}
    read_race = {"armed": False, "started": False, "finished": False}
    real_confirm = frame._confirm_answer_read
    def confirm_during_completion(target):
        if target and read_race["armed"]:
            read_race.update(armed=False, started=True)
            old_seq = frame.chat_store.get_chat_read_state(target["chat_id"])["latest_readable_seq"]
            frame._remote_api_message_ui({"chat_id": target["chat_id"], "text": "new final during native read", "model": "kimi/main"})
            deadline = time.monotonic() + 10
            while frame.chat_store.get_chat_read_state(target["chat_id"])["latest_readable_seq"] <= old_seq:
                if time.monotonic() > deadline: raise RuntimeError("Concurrent actual completion timed out")
                app.Yield()
                time.sleep(.01)
            read_race["finished"] = True
        real_confirm(target)
    frame._confirm_answer_read = confirm_during_completion
    def history_read(payload):
        result = route("_remote_api_history_read_ui")(payload)
        seconds = history_delay["seconds"]
        if seconds:
            history_delay["started"] += 1
            time.sleep(seconds)
            history_delay["finished"] += 1
        return result
    transport = RemoteNatsTransport(pair_id="default", token=token, durable_store=frame.chat_store,
        on_message=route("_remote_api_message_ui"), on_new_chat=route("_remote_api_new_chat_ui"),
        on_state=route("_remote_api_state_ui"), on_history_read=history_read,
        on_history_list=lambda: frame._run_remote_ui_route(frame._remote_api_history_list_ui),
        on_model_list=lambda: frame._run_remote_ui_route(frame._remote_api_model_list_ui),
        on_rename_chat=route("_remote_api_rename_chat_ui"),
        on_chat_read_changed=lambda state: wx.CallAfter(frame._on_chat_read_changed, state))
    frame._remote_nats_transport = transport
    transport.start_threaded(f"nats://127.0.0.1:{tcp}")
    chat_a, chat_b = "read-a-" + uuid.uuid4().hex, "read-b-" + uuid.uuid4().hex
    frame._remote_api_new_chat_ui({"chat_id": chat_a, "title": "Read E2E A", "model": "kimi/main"})
    frame._remote_api_new_chat_ui({"chat_id": chat_b, "title": "Read E2E B", "model": "kimi/main"})
    peer_hwnd_file = folder / "peer.hwnd"
    peer_request_file = folder / "peer.request"
    peer = subprocess.Popen([sys.executable, str(ROOT / "scripts/read_state_foreground_peer.py"), "--hwnd-file", str(peer_hwnd_file), "--request-file", str(peer_request_file)])
    deadline = time.monotonic() + 10
    while not peer_hwnd_file.exists() and time.monotonic() < deadline:
        time.sleep(.05)
    class ForeignWindow:
        def GetHandle(self): return int(peer_hwnd_file.read_text())
        def Show(self): ctypes.windll.user32.ShowWindow(ctypes.c_void_p(self.GetHandle()), 5)
        def Raise(self): ctypes.windll.user32.SetForegroundWindow(ctypes.c_void_p(self.GetHandle()))
    foreign = ForeignWindow()

    def snapshot():
        states = {owner: frame.chat_store.get_chat_read_state(owner) for owner in (chat_a, chat_b)}
        focus = wx.Window.FindFocus()
        ctypes.windll.user32.GetForegroundWindow.restype = ctypes.c_void_p
        user32 = native_user32()
        class GuiInfo(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hwndActive", wintypes.HWND), ("hwndFocus", wintypes.HWND), ("hwndCapture", wintypes.HWND),
                ("hwndMenuOwner", wintypes.HWND), ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND), ("rcCaret", wintypes.RECT)]
        info = GuiInfo(); info.cbSize = ctypes.sizeof(info)
        thread = user32.GetWindowThreadProcessId(ctypes.c_void_p(frame.GetHandle()), None)
        user32.GetGUIThreadInfo(thread, ctypes.byref(info))
        native_class = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(ctypes.c_void_p(info.hwndFocus), native_class, len(native_class))
        focus_controls = {}
        for name in ("answer_list", "input_edit", "history_list", "execution_list", "send_button", "new_chat_button", "model_combo"):
            widget = getattr(frame, name, None)
            if widget is not None:
                focus_controls[name] = {"hwnd": int(widget.GetHandle()), "class": type(widget).__name__, "name": widget.GetName()}
        native_name = next((item["name"] for item in focus_controls.values() if item["hwnd"] == int(info.hwndFocus or 0)), "")
        result = {"states": states, "foreground": int(ctypes.windll.user32.GetForegroundWindow() or 0),
            "frame_hwnd": int(frame.GetHandle()), "focus_hwnd": int(focus.GetHandle()) if focus else 0,
            "answer_hwnd": int(frame.answer_list.GetHandle()), "owner": frame._visible_answer_owner_chat_id(),
            "input_hwnd": int(frame.input_edit.GetHandle()), "running": bool(frame.is_running),
            "viewer_open": bool(getattr(frame, "_answer_viewer_open", False)),
            "history_delay": dict(history_delay),
            "read_race": dict(read_race),
            "selection": frame.answer_list.GetSelection(), "count": frame.answer_list.GetCount(),
            "page_size": max(1, frame.answer_list.GetClientSize().height // max(1, frame.answer_list.GetCharHeight())),
            "client_height": frame.answer_list.GetClientSize().height, "char_height": frame.answer_list.GetCharHeight(),
            "targets": [frame._selected_read_target(index) for index in range(frame.answer_list.GetCount())],
            "selected_target": frame._selected_read_target(), "rows": list(frame.answer_list.GetStrings()),
            "tail_type": frame.answer_meta[-1][0] if frame.answer_meta else "",
            "head_more": any(meta[0] == "more" for meta in frame.answer_meta),
            "turn_count": len(frame._get_view_turns()), "visible_turn_limit": main.ANSWER_LIST_DEFAULT_VISIBLE_ROWS,
            "history_labels": list(frame.history_list.GetStrings()), "tts": list(tts),
            "tts_status": dict(getattr(frame, "voice_screen_reader_status", {}) or {}),
            "audio_available": bool(os.environ.get("READ_E2E_AUDIO_PYTHON") and os.environ.get("READ_E2E_AUDIO_PROBE")),
            "browser": list(browser_evidence),
            "hotkey_registered": bool(getattr(frame, "_unread_hotkey_registered", False)),
            "maximized": frame.IsMaximized(), "status_text": frame.GetStatusBar().GetStatusText(),
            "native_focus": int(info.hwndFocus or 0), "native_active": int(info.hwndActive or 0),
            "native_focus_class": native_class.value, "native_focus_name": native_name,
            "wx_focus_class": type(focus).__name__ if focus else "", "wx_focus_name": focus.GetName() if focus else "",
            "focus_controls": focus_controls, "key_injections": list(key_injections),
            "modifiers": native_modifier_state(user32), "key_observations": list(key_observations)}
        (folder / "desktop.snapshot.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        return result

    def control(body):
        action = body.get("action")
        if action == "send":
            status, result = frame._remote_api_message_ui({"chat_id": body["chat_id"], "text": body["text"], "model": "kimi/main"})
            return {"status": status, "result": result}
        if action == "show":
            frame._show_history_chat(body["chat_id"], focus_answer_list=False)
            frame._apply_detail_panel_mode("answers")
            ctypes.windll.user32.GetForegroundWindow.restype = ctypes.c_void_p
            if ctypes.windll.user32.GetForegroundWindow() != frame.GetHandle():
                activate_owned_frame(frame)
            # Caption SendInput must leave the native queue before choosing the
            # fixture's focus; an already-foreground frame needs no wait inside
            # activate_owned_frame and can otherwise restore the previous input.
            app.Yield()
            control = frame.input_edit if body.get("input") else frame.answer_list
            control.SetFocusFromKbd()
            if body.get("selection") is not None:
                frame.answer_list.SetSelection(int(body["selection"]))
        elif action == "key":
            if body.get("expected"):
                actual = snapshot()
                mismatches = {name: {"expected": value, "actual": actual.get(name)}
                              for name, value in body["expected"].items() if actual.get(name) != value}
                if mismatches:
                    return {"inserted": 0, "not_sent": True, "mismatches": mismatches, "snapshot": actual}
            key = int(body["vk"])
            batch = [(0x10, 0)] if body.get("shift") else []
            flags = 1 if key in {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28} else 0
            batch += [(key, flags), (key, flags | 2)]
            if body.get("shift"): batch.append((0x10, 2))
            key_injections.append({"action": "key", "vk": key, "shift": bool(body.get("shift")), "time": time.time()})
            return {"inserted": native_key_input_batch(batch, "read-e2e")}
        elif action == "select":
            frame.answer_list.SetSelection(int(body["selection"]))
            frame.answer_list.SetFocusFromKbd()
        elif action == "tail_notice":
            frame._answer_list_tail_notice = "Read E2E actual renderer notice"
            frame._answer_list_tail_notice_chat_id = body["chat_id"]
            frame._render_answer_list()
        elif action == "hotkey":
            if body.get("prepare"):
                frame._minimize_to_tray() if body.get("tray") else frame.Iconize(True)
                activate_owned_frame(foreign)
            batch = [(0x11, 0), (0x10, 0), (0x58, 0)]
            if body.get("repeat"):
                batch += [(0x58, 0)] * 4
            batch += [(0x58, 2), (0x10, 2), (0x11, 2)]
            if body.get("rapid"):
                batch += [(0x11, 0), (0x10, 0), (0x58, 0), (0x58, 2), (0x10, 2), (0x11, 2)]
            key_injections.append({"action": "hotkey", "repeat": bool(body.get("repeat")), "rapid": bool(body.get("rapid")), "time": time.time()})
            return {"inserted": native_key_input_batch(batch, "global-read-e2e")}
        elif action == "hotkey_collision":
            frame._unregister_global_hotkey()
            ack_file = peer_request_file.with_suffix(".ack")
            if ack_file.exists(): ack_file.unlink()
            peer_request_file.write_text(json.dumps({"action": "acquire" if body.get("acquire") else "release"}), encoding="utf-8")
            deadline = time.monotonic() + 3
            while not ack_file.exists() and time.monotonic() < deadline: time.sleep(.05)
            if not ack_file.exists(): raise RuntimeError("Other process hotkey registration timed out")
            if not json.loads(ack_file.read_text())["registered"]: raise RuntimeError("Other process hotkey registration failed")
            frame._register_global_hotkey()
        elif action == "stop":
            wx.CallAfter(app.ExitMainLoop)
        elif action == "nats_off":
            server.stop()
        elif action == "nats_on":
            server.start(timeout=20)
        elif action == "history_delay":
            history_delay.update(seconds=int(body.get("seconds", 0)), started=0, finished=0)
        elif action == "read_race":
            read_race.update(armed=True, started=False, finished=False)
        elif action == "replay_read_answer":
            state = frame.chat_store.get_chat_read_state(body["chat_id"])
            events = [json.loads(raw) for raw in frame.chat_store.replay_after(pair_id="default", domain="events", sync_sequence=0)]
            event = next(event for event in events if event.get("kind") == "assistant_final"
                and event.get("chat_id") == body["chat_id"]
                and event.get("body", {}).get("generation") == state["generation"]
                and event.get("body", {}).get("answer_seq", 0) <= state["read_seq"])
            if body.get("publish", True):
                asyncio.run_coroutine_threadsafe(transport.publish_event(event), transport._loop).result(timeout=5)
            return {"event_id": event["event_id"], "message_id": event["body"]["message_id"], "answer_seq": event["body"]["answer_seq"]}
        return snapshot()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            if body.get("action") == "audio_hotkey":
                try:
                    process = subprocess.run([os.environ["READ_E2E_AUDIO_PYTHON"], os.environ["READ_E2E_AUDIO_PROBE"],
                        "--output", str(folder / "no-unread-zdsr.wav"), "--seconds", "4", "--trigger-url",
                        f"http://127.0.0.1:{self.server.server_port}"], check=True, capture_output=True, timeout=22, text=True)
                    audio_path = folder / "no-unread-zdsr.wav"
                    result = {"value": {"audio": {**json.loads(process.stdout), "file_exists": audio_path.is_file(),
                        "file_bytes": audio_path.stat().st_size if audio_path.is_file() else 0}}}
                except subprocess.CalledProcessError as exc:
                    # This helper only captures local output and injects the
                    # owned native hotkey; it receives no NATS credentials.
                    (folder / "audio-probe.stderr.log").write_text(exc.stderr or "", encoding="utf-8")
                    (folder / "audio-probe.stdout.log").write_text(exc.stdout or "", encoding="utf-8")
                    diagnostic = {"returncode": exc.returncode, "stderr": (exc.stderr or "")[-2000:]}
                    (folder / "audio-probe.failure.json").write_text(json.dumps(diagnostic), encoding="utf-8")
                    result = {"error": f"Audio probe exited {exc.returncode}: {diagnostic['stderr']}"}
                except Exception as exc:
                    result = {"error": repr(exc)}
                raw = json.dumps(result).encode()
                self.send_response(200); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
                return
            if body.get("action") == "device":
                serial = os.environ["READ_E2E_DEVICE"]
                device_action = body["device_action"]
                commands = {
                    "background": [["shell", "input", "keyevent", "3"]],
                    "lock": [["shell", "input", "keyevent", "223"]],
                    "unlock": [["shell", "input", "keyevent", "224"],
                        ["shell", "input", "swipe", "300", "1100", "300", "300"],
                        ["shell", "input", "text", "1357"], ["shell", "input", "keyevent", "66"]],
                    "resume": [["shell", "am", "start", "-n", "com.example.zhuge_qa/.MainActivity"]],
                    "permission_off": [["shell", "cmd", "appops", "set", "--user", "0", "--uid", "com.example.zhuge_qa", "POST_NOTIFICATION", "ignore"]],
                    "permission_on": [["shell", "cmd", "appops", "set", "--user", "0", "--uid", "com.example.zhuge_qa", "POST_NOTIFICATION", "allow"]],
                    "close_shade": [["shell", "input", "keyevent", "4"]],
                }
                try:
                    adb_command = [os.environ.get("READ_E2E_ADB", "adb"), "-s", serial]
                    device_value = {"device_action": device_action}
                    if device_action == "event_count":
                        pid = subprocess.check_output([*adb_command, "shell", "pidof", "com.example.zhuge_qa"], timeout=5).decode().strip()
                        log = subprocess.check_output([*adb_command, "logcat", "-d", "--pid", pid, "-s", "RemoteBackgroundService:D", "*:S"], timeout=10).decode("utf-8", "replace")
                        (folder / "native-replay.log").write_text(log, encoding="utf-8")
                        device_value["count"] = sum("Notification event id=" + body["event_id"] in line and "outcome=ACK" in line for line in log.splitlines())
                        commands[device_action] = []
                    if device_action == "dismiss_battery_prompt":
                        subprocess.run([*adb_command, "shell", "uiautomator", "dump", "/sdcard/read-state-e2e-window.xml"], check=True, capture_output=True, timeout=15)
                        xml = subprocess.check_output([*adb_command, "exec-out", "cat", "/sdcard/read-state-e2e-window.xml"], timeout=15)
                        root = ET.fromstring(xml)
                        owned_dialog = any("远程控制" in node.get("text", "") and node.get("package") == "com.android.settings" for node in root.iter("node"))
                        deny = next((node for node in root.iter("node") if node.get("resource-id") == "android:id/button2"), None)
                        commands[device_action] = []
                        if owned_dialog and deny is not None:
                            bounds = [int(value) for value in re.findall(r"\d+", deny.get("bounds", ""))]
                            commands[device_action] = [["shell", "input", "tap", str((bounds[0] + bounds[2]) // 2), str((bounds[1] + bounds[3]) // 2)]]
                    if device_action in ("mute_channel", "unmute_channel"):
                        subprocess.run([*adb_command, "shell", "am", "start", "-W", "-a", "android.settings.CHANNEL_NOTIFICATION_SETTINGS",
                            "--es", "android.provider.extra.APP_PACKAGE", "com.example.zhuge_qa",
                            "--es", "android.provider.extra.CHANNEL_ID", "codex_remote_background_messages_v4"], check=True, capture_output=True, timeout=15)
                        subprocess.run([*adb_command, "shell", "uiautomator", "dump", "/sdcard/read-state-e2e-window.xml"], check=True, capture_output=True, timeout=15)
                        xml = subprocess.check_output([*adb_command, "exec-out", "cat", "/sdcard/read-state-e2e-window.xml"], timeout=15)
                        (folder / (device_action + ".xml")).write_bytes(xml)
                        switches = [node for node in ET.fromstring(xml).iter("node") if node.get("resource-id", "").endswith(":id/switch_widget")]
                        if len(switches) != 1:
                            raise RuntimeError("Specific message channel master switch is not uniquely visible")
                        switch = switches[0]
                        desired = "false" if device_action == "mute_channel" else "true"
                        commands[device_action] = []
                        if switch.get("checked") != desired:
                            if device_action == "mute_channel":
                                (folder / "channel-mute-owned").write_text("restore enabled", encoding="utf-8")
                            bounds = [int(value) for value in re.findall(r"\d+", switch.get("bounds", ""))]
                            commands[device_action].append(["shell", "input", "tap", str((bounds[0] + bounds[2]) // 2), str((bounds[1] + bounds[3]) // 2)])
                        commands[device_action] += [["shell", "input", "keyevent", "4"], ["shell", "input", "keyevent", "3"]]
                    if device_action in ("notification_tap", "notification_swipe"):
                        subprocess.run([*adb_command, "shell", "cmd", "statusbar", "expand-notifications"], check=True, capture_output=True, timeout=15)
                        time.sleep(.7)
                        subprocess.run([*adb_command, "shell", "uiautomator", "dump", "/sdcard/read-state-e2e-window.xml"], check=True, capture_output=True, timeout=15)
                        xml = subprocess.check_output([*adb_command, "exec-out", "cat", "/sdcard/read-state-e2e-window.xml"], timeout=15)
                        nodes = [node for node in ET.fromstring(xml).iter("node") if str(body["text"]) in node.get("text", "")]
                        if not nodes:
                            raise RuntimeError("Actual system notification text not visible: " + str(body["text"]))
                        bounds = [int(value) for value in re.findall(r"\d+", nodes[0].get("bounds", ""))]
                        x, y = (bounds[0] + bounds[2]) // 2, (bounds[1] + bounds[3]) // 2
                        arguments = ["shell", "input", "tap", str(x), str(y)] if device_action == "notification_tap" else ["shell", "input", "swipe", str(x), str(y), str(bounds[2] + 500), str(y), "250"]
                        commands[device_action] = [arguments]
                    for arguments in commands[device_action]:
                        subprocess.run([os.environ.get("READ_E2E_ADB", "adb"), "-s", serial, *arguments], check=True, capture_output=True, timeout=15)
                        time.sleep(.2)
                    if device_action == "unmute_channel":
                        (folder / "channel-mute-owned").unlink(missing_ok=True)
                    result = {"value": device_value}
                except Exception as exc:
                    result = {"error": repr(exc)}
                raw = json.dumps(result).encode()
                self.send_response(200); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
                return
            done, result = threading.Event(), {}
            def invoke():
                try: result["value"] = control(body)
                except Exception as exc: result["error"] = repr(exc)
                finally: done.set()
            wx.CallAfter(invoke)
            if not done.wait(20): result["error"] = "MC UI controller timeout"
            raw = json.dumps(result).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
    controller = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=controller.serve_forever, daemon=True).start()
    (folder / "ready.json").write_text(json.dumps({"endpoint": f"ws://127.0.0.1:{websocket}/nats",
        "token": token, "control_port": controller.server_port, "pid": os.getpid(),
        "chat_a": chat_a, "chat_b": chat_b,
        "mc_head": subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip(),
        "mc_source": source_evidence(ROOT), "rc_source": source_evidence(ROOT.parent / "rc")}), encoding="utf-8")
    frame.Show()
    try:
        app.MainLoop()
    finally:
        controller.shutdown()
        transport.stop()
        server.stop()
        frame._closing = True
        frame._flush_execution_step_persists_sync()
        frame._unregister_global_hotkey()
        ctypes.windll.user32.PostMessageW(ctypes.c_void_p(foreign.GetHandle()), 0x0010, 0, 0)
        try: peer.wait(timeout=5)
        except subprocess.TimeoutExpired: peer.terminate()
        frame.Destroy()
        app.Destroy()


if __name__ == "__main__":
    run()
