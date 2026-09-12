import sys
import ctypes
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
    monkeypatch.setenv("DESKTOP_FILE_SERVICE_AUTOSTART", "0")
    monkeypatch.delenv("REMOTE_CONTROL_TOKEN", raising=False)
    monkeypatch.delenv("REMOTE_CONTROL_HOST", raising=False)
    monkeypatch.delenv("REMOTE_CONTROL_PORT", raising=False)
    monkeypatch.delenv("REMOTE_CONTROL_DOMAIN", raising=False)
    monkeypatch.delenv("CLAUDECODE_REMOTE_CONTROL_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDECODE_REMOTE_CONTROL_HOST", raising=False)
    monkeypatch.delenv("CLAUDECODE_REMOTE_CONTROL_PORT", raising=False)
    monkeypatch.delenv("CLAUDECODE_REMOTE_CONTROL_DOMAIN", raising=False)


@pytest.fixture
def frame(tmp_path, monkeypatch, wx_app, request):
    monkeypatch.setattr(main, "resolve_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(main, "resolve_notes_data_dir", lambda: tmp_path / "notes")
    monkeypatch.setattr(main.ChatFrame, "_legacy_state_paths", lambda self: [self.state_path])
    assert Path(main.resolve_notes_data_dir()).resolve() != Path(r"D:\code\note").resolve()
    f = main.ChatFrame()
    f.Hide()
    yield f
    # Most tests intentionally bypass the full application close handler.
    # Still honor its persistence barrier before destroying wx/native state so
    # no daemon SQLite worker survives the fixture that owns its ChatStore.
    f._closing = True
    f._flush_execution_step_persists_sync()
    try:
        f.Hide()
    except Exception:
        pass
    if sys.platform == "win32":
        try:
            ctypes.windll.user32.SetFocus(None)
        except Exception:
            pass
    # Destroy the native frame, then pump the GUI loop so wx releases menu and
    # window handles before the next fixture. Hundreds of serial native frames
    # otherwise exhaust the process handle pool on Windows.
    try:
        being_deleted = bool(f.IsBeingDeleted())
    except Exception:
        being_deleted = True
    if not being_deleted:
        f.Destroy()
    # wx defers real window-handle release to the event loop; without pumping
    # events, hundreds of frames in one test process exhaust handles and
    # later fixtures start failing (e.g. Menu.AppendCheckItem returns None).
    # Some tests monkeypatch wx.GetApp, so resolve it defensively.
    app = wx.GetApp()
    process_pending = getattr(app, "ProcessPendingEvents", None)
    if callable(process_pending):
        try:
            process_pending()
        except Exception:
            pass
    # This test intentionally exercises a live dialog callback; yielding while
    # that callback's COM stack unwinds can raise RPC_E_CANTCALLOUT_ININPUTSYNCCALL.
    # The following fixture's yield drains its already-destroyed handles.
    unsafe_dialog_teardown = (
        "answer_viewer" in request.node.name
        or "answer_text_viewer" in request.node.name
    )
    if not unsafe_dialog_teardown:
        try:
            app.Yield()
        except Exception:
            pass
