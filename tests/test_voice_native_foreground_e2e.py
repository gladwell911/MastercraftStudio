"""Opt-in Windows UI Automation with test-owned editors and synthetic ASR."""

import ctypes
import os
from pathlib import Path
import subprocess
import sys

import pytest

from test_voice_input_ui_automation import _pump_until
from voice_fakes import VoiceRig
import windows_voice_target as native


pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("MC_NATIVE_VOICE_TEST") != "1",
    reason="requires an interactive Windows session and MC_NATIVE_VOICE_TEST=1",
)


@pytest.mark.parametrize("editor", ["notepad", "edge"])
def test_voice_replaces_external_selection_and_appends_in_background(
    editor, frame, wx_app, monkeypatch, tmp_path
):
    if editor == "notepad":
        document = tmp_path / "MC Voice QA.txt"
        document.write_text("beforeSELECTafter", encoding="utf-8")
        exe = Path(os.environ["SystemRoot"]) / "System32/notepad.exe"
        args = [str(document)]
    else:
        document = tmp_path / "MC Voice QA.html"
        profile = tmp_path / "edge-profile"
        document.write_text(
            '<title>MC Voice QA</title><input autofocus aria-label="QA Editor" '
            'value="beforeSELECTafter" onfocus="this.setSelectionRange(6,12)">',
            encoding="utf-8",
        )
        exe = Path(os.environ["ProgramFiles(x86)"]) / "Microsoft/Edge/Application/msedge.exe"
        args = [
            f"--user-data-dir={profile}", "--no-first-run",
            "--disable-background-networking", "--force-renderer-accessibility",
            "--new-window", document.as_uri(),
        ]
    if not exe.is_file():
        pytest.skip(f"{editor} unavailable")

    rig = VoiceRig(monkeypatch)
    rig.text = "voice-验证"
    effects = []
    monkeypatch.setattr(frame, "_speak_text_via_screen_reader", effects.append)
    monkeypatch.setattr(frame, "_play_voice_begin_sound", lambda: effects.append("ready"))
    monkeypatch.setattr(frame, "_play_voice_end_sound", lambda: effects.append("stop"))
    monkeypatch.setattr(frame, "_play_voice_wrong_sound", lambda: effects.append("error"))
    monkeypatch.setattr(frame, "_call_later_if_alive", lambda _delay, fn, *args: fn(*args) or object())
    frame.input_edit.SetValue("original draft")
    process = subprocess.Popen([str(exe), *args])
    original_send = native.send_unicode_text
    editor_pid = None

    def owned_browser_pids():
        command = (
            "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | Where-Object { "
            "$_.CommandLine -and (' ' + $_.CommandLine.Replace([string][char]34, '') + ' ')"
            ".Contains(' ' + $env:MC_VOICE_QA_PROFILE_ARG + ' ') -and $_.CommandLine -notmatch '--type=' "
            "} | Select-Object -ExpandProperty ProcessId"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command], capture_output=True, timeout=10,
            env={**os.environ, "MC_VOICE_QA_PROFILE_ARG": f"--user-data-dir={profile}"},
        )
        return [int(line) for line in result.stdout.splitlines() if line.strip().isdigit()]

    def owned_target():
        nonlocal editor_pid
        target = native.foreground_edit_target()
        if target:
            pid = ctypes.c_ulong()
            ctypes.windll.user32.GetWindowThreadProcessId(target[0], ctypes.byref(pid))
            if not editor_pid:
                title = ctypes.create_unicode_buffer(512)
                ctypes.windll.user32.GetWindowTextW(target[0], title, len(title))
                if "MC Voice QA" in title.value and (
                    pid.value == process.pid or (editor == "edge" and pid.value in owned_browser_pids())
                ):
                    editor_pid = pid.value
            if pid.value == editor_pid:
                return target
        return None

    def safe_send(text):
        assert owned_target() == target, "QA editor lost foreground before sending text"
        return original_send(text)

    monkeypatch.setattr(native, "send_unicode_text", safe_send)
    controller = frame._voice_input
    try:
        _pump_until(wx_app, lambda: owned_target() is not None, timeout=12)
        target = owned_target()

        def read_editor():
            assert owned_target() == target, "the test-owned editor lost keyboard focus"
            from comtypes import client
            api = client.GetModule("UIAutomationCore.dll")
            automation = client.CreateObject(api.CUIAutomation8, interface=api.IUIAutomation)
            sample, _read = native._sample_editor(api, automation)
            return sample

        def initialized():
            sample = read_editor()
            return sample is not None and sample.before.rstrip("\r\n") == "beforeSELECTafter"

        _pump_until(wx_app, initialized, timeout=10)
        if editor == "notepad":
            from comtypes import client
            api = client.GetModule("UIAutomationCore.dll")
            automation = client.CreateObject(api.CUIAutomation8, interface=api.IUIAutomation)
            element = automation.GetFocusedElement()
            text = element.GetCurrentPattern(api.UIA_TextPatternId).QueryInterface(api.IUIAutomationTextPattern)
            selection = text.DocumentRange.FindText("SELECT", False, False)
            assert selection
            selection.Select()

        before = read_editor()
        assert before.selection[1] == "SELECT"
        for count in (1, 2):
            rig.connect.clear()
            frame._on_global_ctrl_keyup(False, "left")
            frame._on_global_ctrl_keyup(False, "left")
            _pump_until(wx_app, lambda: effects.count("ready") == count)
            frame._on_global_ctrl_keyup(False, "left")
            assert controller.state == "finishing", effects
            rig.connect.set()
            _pump_until(wx_app, lambda: "error" in effects or effects.count(rig.text) == count, timeout=5)
            assert effects == ["ready", "stop", rig.text] * count
        assert frame.input_edit.GetValue() == "original draft\n" + f"{rig.text}；\n" * 2
        assert read_editor() == before
        print(f"NATIVE_VOICE_BACKGROUND_OK editor={editor}")

        rig.connect.clear()
        frame._on_global_ctrl_keyup(False, "right")
        assert controller.state == "idle"
        frame._on_global_ctrl_keyup(False, "right")
        _pump_until(wx_app, lambda: effects.count("ready") == 3)
        frame._on_global_ctrl_keyup(False, "right")
        assert controller.state == "finishing", effects
        rig.connect.set()
        _pump_until(wx_app, lambda: effects.count(rig.text) == 3 or "error" in effects, timeout=5)
        assert read_editor().before.rstrip("\r\n") == f"before{rig.text}after", effects
        assert effects == ["ready", "stop", rig.text] * 3
        print(f"NATIVE_VOICE_REPLACEMENT_OK editor={editor}")
    finally:
        try:
            controller.shutdown()
            assert controller._shutdown_done.wait(3)
            rig.assert_closed()
        finally:
            if editor == "edge":
                for pid in owned_browser_pids():
                    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=10)
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
