"""Prepare only QA-owned frames using a real native caption click."""
import ctypes
from ctypes import wintypes
import time

import pytest
import wx


def activate_owned_frame(frame):
    from test_model_session_recovery_ui_automation import _Input, native_user32
    user32 = native_user32()
    user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.RECT)]
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_Input), ctypes.c_int]
    hwnd = int(frame.GetHandle())
    frame.Show()
    frame.Raise()
    wx.GetApp().Yield()
    rect = wintypes.RECT()
    old = wintypes.POINT()
    assert user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rect))
    assert user32.GetCursorPos(ctypes.byref(old))
    # Only the test-owned window changes z-order. No user window is clicked.
    assert user32.SetWindowPos(ctypes.c_void_p(hwnd), ctypes.c_void_p(-1), 0, 0, 0, 0, 0x13)
    x, y = rect.left + (rect.right - rect.left) // 2, rect.top + 12
    try:
        assert user32.SetCursorPos(x, y)
        batch = (_Input * 2)()
        batch[0].type = batch[1].type = 0
        batch[0].data.mi.dwFlags = 2
        batch[1].data.mi.dwFlags = 4
        count = user32.SendInput(2, batch, ctypes.sizeof(_Input))
        assert count == 2
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and int(user32.GetForegroundWindow() or 0) != hwnd:
            wx.GetApp().Yield()
            time.sleep(.01)
        actual = int(user32.GetForegroundWindow() or 0)
        print('QA_OWNED_CAPTION_CLICK', 'count=', count, 'expected=', hwnd, 'actual=', actual, flush=True)
        assert actual == hwnd, 'real test-owned caption click did not activate the QA frame'
    finally:
        user32.SetWindowPos(ctypes.c_void_p(hwnd), ctypes.c_void_p(-2), 0, 0, 0, 0, 0x13)
        user32.SetCursorPos(old.x, old.y)


@pytest.fixture(autouse=True)
def prepare_only_native_test_helpers(monkeypatch):
    import test_model_session_recovery_ui_automation as recovery
    import test_desktop_navigation_ui_automation as navigation
    original_input = recovery.activate_input
    original_control = navigation.activate

    def activate_input(frame, phase):
        activate_owned_frame(frame)
        return original_input(frame, phase)

    def activate_control(frame, control):
        activate_owned_frame(frame)
        return original_control(frame, control)

    monkeypatch.setattr(recovery, 'activate_input', activate_input)
    monkeypatch.setattr(navigation, 'activate', activate_control)
