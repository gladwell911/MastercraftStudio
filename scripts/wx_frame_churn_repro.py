"""Reproduce wx resource exhaustion from repeated ChatFrame create/destroy."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, r"D:\code\sj\mc")

import os

os.environ["AUTO_START_QUICK_TUNNEL"] = "0"
os.environ["REMOTE_CONTROL_AUTOSTART"] = "0"
os.environ["DESKTOP_FILE_SERVICE_AUTOSTART"] = "0"
for key in (
    "REMOTE_CONTROL_TOKEN",
    "REMOTE_CONTROL_HOST",
    "REMOTE_CONTROL_PORT",
    "REMOTE_CONTROL_DOMAIN",
    "CLAUDECODE_REMOTE_CONTROL_TOKEN",
    "CLAUDECODE_REMOTE_CONTROL_HOST",
    "CLAUDECODE_REMOTE_CONTROL_PORT",
    "CLAUDECODE_REMOTE_CONTROL_DOMAIN",
):
    os.environ.pop(key, None)

import wx

app = wx.App(False)

import main

main.GlobalCtrlTapHook.start = lambda self: None
main.GlobalCtrlTapHook.stop = lambda self: None
tmp = Path(tempfile.mkdtemp())
main.resolve_app_data_dir = lambda: tmp
main.resolve_notes_data_dir = lambda: tmp / "notes"
main.ChatFrame._legacy_state_paths = lambda self: [self.state_path]

mode = sys.argv[1] if len(sys.argv) > 1 else "no_yield"

for i in range(1, 601):
    try:
        f = main.ChatFrame()
        f.Hide()
        f.Destroy()
        if mode == "yield":
            app.Yield()
    except Exception as exc:
        print(f"FAILED at iteration {i}: {type(exc).__name__}: {exc}")
        sys.exit(1)
print(f"OK: 600 frames created/destroyed with mode={mode}")
