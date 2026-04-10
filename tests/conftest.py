import sys
from pathlib import Path

import wx
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main


@pytest.fixture(scope="session", autouse=True)
def wx_app():
    app = wx.App(False)
    yield app
    app.Destroy()


@pytest.fixture(autouse=True)
def disable_system_hooks(monkeypatch):
    monkeypatch.setattr(main.GlobalCtrlTapHook, "start", lambda self: None)
    monkeypatch.setattr(main.GlobalCtrlTapHook, "stop", lambda self: None)
    monkeypatch.setenv("AUTO_START_QUICK_TUNNEL", "0")
    monkeypatch.setenv("REMOTE_CONTROL_AUTOSTART", "0")
    monkeypatch.delenv("REMOTE_CONTROL_TOKEN", raising=False)
    monkeypatch.delenv("REMOTE_CONTROL_HOST", raising=False)
    monkeypatch.delenv("REMOTE_CONTROL_PORT", raising=False)
    monkeypatch.delenv("REMOTE_CONTROL_DOMAIN", raising=False)
    monkeypatch.delenv("CLAUDECODE_REMOTE_CONTROL_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDECODE_REMOTE_CONTROL_HOST", raising=False)
    monkeypatch.delenv("CLAUDECODE_REMOTE_CONTROL_PORT", raising=False)
    monkeypatch.delenv("CLAUDECODE_REMOTE_CONTROL_DOMAIN", raising=False)


@pytest.fixture
def frame(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "resolve_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(main.ChatFrame, "_legacy_state_paths", lambda self: [self.state_path])
    f = main.ChatFrame()
    f.Hide()
    yield f
    f.Destroy()
