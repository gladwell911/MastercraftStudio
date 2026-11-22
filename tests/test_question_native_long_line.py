"""Isolated Windows RichEdit probe for long single-line question drafts."""

import ctypes
import time
from ctypes import wintypes

import pytest
import wx

from owned_window_qa import activate_owned_frame


@pytest.mark.parametrize("length", (9_800, 28_000))
def test_question_native_long_line(frame, monkeypatch, length):
    from comtypes import IUnknown

    frame.SetSize((640, 520))
    activate_owned_frame(frame)
    editor = frame.input_edit
    editor.SetFocus()
    wx.GetApp().Yield()
    tail = f"TAIL_{length}"
    text = "x" * (length - len(tail)) + tail
    editor.SetValue(text)
    hwnd = editor.GetHandle()
    user32 = ctypes.windll.user32
    send = user32.SendMessageW
    send.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    send.restype = wintypes.LPARAM
    name = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(wintypes.HWND(hwnd), name, len(name))
    assert "richedit" in name.value.lower()
    native_length = send(hwnd, 0x000E, 0, 0)
    native_text = ctypes.create_unicode_buffer(native_length + 1)
    send(hwnd, 0x000D, len(native_text), ctypes.addressof(native_text))
    assert native_text.value == text
    assert editor.GetValue() == text
    for key, expected in [(0x24, 0), (0x23, length), (0x25, length - 1), (0x27, length)]:
        send(hwnd, 0x0100, key, 0)
        send(hwnd, 0x0101, key, 0)
        assert editor.GetInsertionPoint() == expected
    scroll = wintypes.POINT()
    send(hwnd, 0x04DD, 0, ctypes.addressof(scroll))
    visual_lines = send(hwnd, 0x00BA, 0, 0)
    assert visual_lines == 1
    assert scroll.x > 0
    end_rect = wintypes.POINT()
    client_rect = wintypes.RECT()
    assert user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(client_rect))
    send(hwnd, 0x00D6, ctypes.addressof(end_rect), length - len(tail))
    assert 0 <= end_rect.x < client_rect.right
    print(f"RICHEdit {name.value} length={native_length} lines={visual_lines} horizontal_offset={scroll.x} tail={tail}")

    # Preserve the pre-test clipboard IDataObject, including nontext formats.
    saved_clipboard = ctypes.POINTER(IUnknown)()
    ole32 = ctypes.WinDLL("ole32")
    ole32.OleSetClipboard.argtypes = [ctypes.c_void_p]
    assert ole32.OleGetClipboard(ctypes.byref(saved_clipboard)) == 0
    try:
        editor.SelectAll()
        send(hwnd, 0x0301, 0, 0)
        user32.GetClipboardData.restype = wintypes.HANDLE
        kernel32 = ctypes.windll.kernel32
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        assert user32.OpenClipboard(wintypes.HWND(hwnd))
        try:
            handle = user32.GetClipboardData(13)  # CF_UNICODETEXT
            assert handle
            pointer = kernel32.GlobalLock(handle)
            try:
                assert pointer and ctypes.wstring_at(pointer) == text
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()
    finally:
        for _ in range(20):
            if ole32.OleSetClipboard(saved_clipboard) == 0:
                break
            time.sleep(0.02)
        else:
            pytest.fail("Could not restore the pre-test clipboard")

    submitted = []
    monkeypatch.setattr(frame, "_submit_question", lambda question, **_kwargs: (not submitted.append(question), ""))
    assert frame._request_question_submit()
    assert submitted == [text]
    assert editor.HasFocus()
    with_newline = f"{text}\nSECOND_LINE"
    editor.SetValue(with_newline)
    assert send(hwnd, 0x00BA, 0, 0) == 2
    assert frame._request_question_submit()
    assert submitted[-1] == with_newline
