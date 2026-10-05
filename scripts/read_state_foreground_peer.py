"""Separate QA-owned process for real global hotkey foreground tests."""
import argparse
import json
from pathlib import Path
import wx

parser = argparse.ArgumentParser()
parser.add_argument("--hwnd-file", type=Path, required=True)
parser.add_argument("--request-file", type=Path)
args = parser.parse_args()
app = wx.App(False)
frame = wx.Frame(None, title="Read E2E Other Program", size=(400, 180))
frame.Show()
args.hwnd_file.write_text(str(frame.GetHandle()), encoding="ascii")
timer = wx.Timer(frame)
def check_request(_event):
    if args.request_file and args.request_file.exists():
        request = json.loads(args.request_file.read_text(encoding="utf-8"))
        args.request_file.unlink()
        registered = frame.RegisterHotKey(0xBB12, wx.MOD_CONTROL | wx.MOD_SHIFT | 0x4000, ord("X")) if request["action"] == "acquire" else frame.UnregisterHotKey(0xBB12)
        args.request_file.with_suffix(".ack").write_text(json.dumps({"registered": bool(registered)}), encoding="utf-8")
frame.Bind(wx.EVT_TIMER, check_request, timer)
timer.Start(50)
app.MainLoop()
