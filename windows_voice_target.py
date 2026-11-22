"""Resolve the foreground edit target and verify voice insertion using UI Automation."""

import ctypes
import time
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import dataclass


class _GUIThreadInfo(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD), ("flags", wintypes.DWORD),
        ("active", wintypes.HWND), ("focus", wintypes.HWND),
        ("capture", wintypes.HWND), ("menu_owner", wintypes.HWND),
        ("move_size", wintypes.HWND), ("caret", wintypes.HWND), ("caret_rect", wintypes.RECT),
    ]


def foreground_edit_target() -> tuple[int, int] | None:
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.c_void_p]
    active = user32.GetForegroundWindow()
    info = _GUIThreadInfo(size=ctypes.sizeof(_GUIThreadInfo))
    if not active or not user32.GetGUIThreadInfo(0, ctypes.byref(info)) or not info.focus:
        return None
    if user32.GetForegroundWindow() != active:
        return None
    return active, info.focus


class _KeyboardInput(ctypes.Structure):
    _fields_ = [("vk", wintypes.WORD), ("scan", wintypes.WORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]


class _MouseInput(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG), ("data", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD), ("extra", ctypes.c_size_t)]


class _InputData(ctypes.Union):
    _fields_ = [("keyboard", _KeyboardInput), ("mouse", _MouseInput)]


class _Input(ctypes.Structure):
    _fields_ = [("kind", wintypes.DWORD), ("data", _InputData)]


def send_unicode_text(text: str) -> bool:
    units = text.encode("utf-16-le")
    events = [
        _Input(1, _InputData(keyboard=_KeyboardInput(0, int.from_bytes(units[i:i + 2], "little"), flags)))
        for i in range(0, len(units), 2) for flags in (0x0004, 0x0004 | 0x0002)
    ]
    if not events:
        return False
    inputs = (_Input * len(events))(*events)
    send_input = ctypes.windll.user32.SendInput
    send_input.argtypes = [wintypes.UINT, ctypes.c_void_p, ctypes.c_int]
    return send_input(len(inputs), inputs, ctypes.sizeof(_Input)) == len(inputs)


@dataclass(frozen=True)
class VoiceEditSnapshot:
    runtime_id: tuple[int, ...]
    before: str
    selection: tuple[str, str, str] | None
    caret: tuple[int, int, int, int, int] | None


def _native_caret() -> tuple[int, int, int, int, int] | None:
    info = _GUIThreadInfo(size=ctypes.sizeof(_GUIThreadInfo))
    if ctypes.windll.user32.GetGUIThreadInfo(0, ctypes.byref(info)) and info.caret:
        rect = info.caret_rect
        return info.caret, rect.left, rect.top, rect.right, rect.bottom
    return None


def _changed_by_inserting(before: str, after: str, text: str) -> bool:
    """Without selection evidence, only a pure insertion confirms success."""
    if not text or len(after) != len(before) + len(text):
        return False
    position = after.find(text)
    while position >= 0:
        if after[:position] + after[position + len(text):] == before:
            return True
        position = after.find(text, position + 1)
    return False


@contextmanager
def _focused_automation():
    import comtypes
    from comtypes import client

    comtypes.CoInitialize()
    try:
        api = client.GetModule("UIAutomationCore.dll")
        automation = client.CreateObject(api.CUIAutomation8, interface=api.IUIAutomation2)
        automation.ConnectionTimeout = 200
        automation.TransactionTimeout = 200
        yield api, automation
    finally:
        comtypes.CoUninitialize()


def _sample_editor(api, automation):
    editor = automation.GetFocusedElement()
    if not editor or editor.CurrentIsPassword or not editor.CurrentIsEnabled or not editor.CurrentHasKeyboardFocus:
        return None, None
    runtime_id = tuple(editor.GetRuntimeId() or ())
    if not runtime_id:
        return None, None
    value = editor.GetCurrentPattern(api.UIA_ValuePatternId)
    pattern = editor.GetCurrentPattern(api.UIA_TextPatternId)
    selection_parts = None
    if value:
        value = value.QueryInterface(api.IUIAutomationValuePattern)
        if value.CurrentIsReadOnly:
            return None, None
    if pattern:
        pattern = pattern.QueryInterface(api.IUIAutomationTextPattern)
        doc = pattern.DocumentRange
        if not value and doc.GetAttributeValue(api.UIA_IsReadOnlyAttributeId) is not False:
            return None, None

        def read():
            return pattern.DocumentRange.GetText(-1) or ""

        selections = pattern.GetSelection()
        if selections and selections.Length == 1:
            selection = selections.GetElement(0)
            prefix, suffix = doc.Clone(), doc.Clone()
            prefix.MoveEndpointByRange(api.TextPatternRangeEndpoint_End, selection, api.TextPatternRangeEndpoint_Start)
            suffix.MoveEndpointByRange(api.TextPatternRangeEndpoint_Start, selection, api.TextPatternRangeEndpoint_End)
            selection_parts = tuple(part.GetText(-1) or "" for part in (prefix, selection, suffix))
    elif value:
        def read():
            return value.CurrentValue or ""
    else:
        return None, None
    return VoiceEditSnapshot(runtime_id, read(), selection_parts, _native_caret()), read


def capture_voice_editor(target: tuple[int, int]) -> VoiceEditSnapshot | None:
    """Bind the actual focused element and available selection at result delivery."""
    with _focused_automation() as (api, automation):
        sample, _read = _sample_editor(api, automation)
        return sample if foreground_edit_target() == target else None


def insert_and_verify(target: tuple[int, int], snapshot: VoiceEditSnapshot, text: str, is_current) -> bool:
    """Revalidate the completion-time target before sending any keystrokes."""
    if not is_current() or foreground_edit_target() != target:
        return False
    with _focused_automation() as (api, automation):
        current, read = _sample_editor(api, automation)
        if (current != snapshot or not is_current() or foreground_edit_target() != target
                or not send_unicode_text(text)):
            return False
        expected = None
        if snapshot.selection is not None:
            prefix, _selected, suffix = snapshot.selection
            expected = prefix + text + suffix
        deadline = time.monotonic() + 0.7
        while is_current() and time.monotonic() < deadline:
            after = read()
            if (expected is not None and after == expected and after != snapshot.before) or (
                expected is None and _changed_by_inserting(snapshot.before, after, text)
            ):
                return True
            time.sleep(0.025)
    return False
