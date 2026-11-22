"""Native keyboard constants and the global Ctrl tap hook."""

import ctypes
from ctypes import wintypes
from typing import Callable

import mc_frame_bindings as bindings


HOTKEY_ID_SHOW = 0xA112
HOTKEY_ID_REALTIME_CALL = 0xA113
HOTKEY_ID_REALTIME_CALL_ALT = 0xA114
HOTKEY_ID_REALTIME_CALL_ALT2 = 0xA115
HOTKEY_ID_UNREAD = 0xA116
WM_QUIT = 0x0012


WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_CHAR = 0x0102
VK_CONTROL = 0x11
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_SHIFT = 0x10
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_MENU = 0x12
VK_LMENU = 0xA4
VK_RMENU = 0xA5
VK_LWIN = 0x5B
VK_RWIN = 0x5C


VK_PROCESSKEY = 0xE5
VK_PACKET = 0xE7
VK_V = 0x56
VK_OEM_5 = 0xDC
VK_OEM_102 = 0xE2
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1
KEYEVENTF_UNICODE = 0x0004
EVENT_OBJECT_FOCUS = 0x8005
EVENT_OBJECT_VALUECHANGE = 0x800E
OBJID_CLIENT = -4
CHILDID_SELF = 0


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class GlobalCtrlTapHook:
    def __init__(self, on_ctrl_keyup: Callable[[bool, str], None], on_error: Callable[[str], None] | None = None):
        self.on_ctrl_keyup = on_ctrl_keyup
        self.on_error = on_error
        self._thread = None
        self._thread_id = 0
        self._hook = None
        self._hook_proc = None
        self._fallback_thread = None
        self._running = False
        self._hook_pressed_at = {}
        self._hook_chords = set()
        self._pressed_keys = set()
        self._using_fallback = False
        self._fallback_notice_sent = False
        self._last_hook_event_at = 0.0
        self._hook_stale_seconds = 0.45
        self._emit_lock = bindings.app.threading.Lock()
        self._last_emit_at_by_side: dict[str, tuple[float, str]] = {}
        self._poller_left_down = False
        self._poller_right_down = False
        self._poller_pressed_at = {}
        self._poller_chords = set()
        self._backend_state = "hook+poller"

    @staticmethod
    def _should_mark_combo_key(vk: int) -> bool:
        # Ignore IME pseudo keys. Modifiers count toward a Ctrl chord.
        if vk in {
            0,
            0xFF,
            VK_CONTROL,
            VK_LCONTROL,
            VK_RCONTROL,
            VK_PROCESSKEY,
            VK_PACKET,
        }:
            return False
        return True

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = bindings.app.threading.Thread(target=self._thread_main, daemon=True)
        self._thread.start()
        self._fallback_thread = bindings.app.threading.Thread(target=self._run_fallback_poller, daemon=True)
        self._fallback_thread.start()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        try:
            if self._thread_id:
                ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        except Exception:
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        if self._fallback_thread and self._fallback_thread.is_alive():
            self._fallback_thread.join(timeout=1.0)
        self._thread = None
        self._fallback_thread = None
        self._thread_id = 0
        self._poller_left_down = False
        self._poller_right_down = False

    def _thread_main(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = int(kernel32.GetCurrentThreadId())
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p, wintypes.HINSTANCE, wintypes.DWORD]
        user32.SetWindowsHookExW.restype = wintypes.HHOOK
        user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
        user32.CallNextHookEx.restype = wintypes.LPARAM
        user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
        user32.UnhookWindowsHookEx.restype = wintypes.BOOL

        HOOKPROC = ctypes.WINFUNCTYPE(wintypes.LPARAM, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

        def _low_level_proc(n_code, w_param, l_param):
            if n_code == 0:
                self._last_hook_event_at = bindings.app.time.monotonic()
                kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                msg = int(w_param)
                vk = int(kb.vkCode)
                self._process_hook_key(vk, msg)
            return user32.CallNextHookEx(self._hook, n_code, w_param, l_param)

        self._hook_proc = HOOKPROC(_low_level_proc)
        h_inst = kernel32.GetModuleHandleW(None)
        self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._hook_proc, h_inst, 0)
        if not self._hook:
            err = ctypes.get_last_error()
            if self.on_error:
                bindings.app.wx_call_after_if_alive(self.on_error, f"全局语音热键不可用（Windows Hook 安装失败，错误码 {err}）")
            self._using_fallback = True
            self._backend_state = "poller_only"
            return
        self._last_hook_event_at = bindings.app.time.monotonic()

        msg = wintypes.MSG()
        try:
            while self._running:
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret == -1:
                    if self.on_error:
                        bindings.app.wx_call_after_if_alive(self.on_error, "全局语音热键不可用（消息循环异常）")
                    break
                if ret == 0:
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            try:
                if self._hook:
                    user32.UnhookWindowsHookEx(self._hook)
            except Exception:
                pass
            self._hook = None
            self._hook_proc = None
            self._thread_id = 0

    def _process_hook_key(self, vk: int, message: int) -> None:
        key_down = message in (WM_KEYDOWN, WM_SYSKEYDOWN)
        if not key_down and message not in (WM_KEYUP, WM_SYSKEYUP):
            return
        if vk not in (VK_CONTROL, VK_LCONTROL, VK_RCONTROL):
            if key_down and self._should_mark_combo_key(vk):
                self._pressed_keys.add(vk)
                self._hook_chords.update(self._hook_pressed_at)
            elif not key_down:
                self._pressed_keys.discard(vk)
            return
        side = "right" if vk == VK_RCONTROL else "left"
        if not key_down and vk == VK_CONTROL and len(self._hook_pressed_at) == 1:
            side = next(iter(self._hook_pressed_at))
        now = self._last_hook_event_at
        if key_down:
            if side not in self._hook_pressed_at:
                if self._hook_pressed_at or self._pressed_keys:
                    self._hook_chords.update(self._hook_pressed_at)
                    self._hook_chords.add(side)
                self._hook_pressed_at[side] = now
            return
        started = self._hook_pressed_at.pop(side, None)
        if started is not None:
            combo = side in self._hook_chords or (side == "right" and now - started > 0.4)
            self._hook_chords.discard(side)
            self._emit_ctrl_keyup(combo, side, source="hook")

    def _run_fallback_poller(self) -> None:
        # Fallback for environments where low-level hook is blocked or unreliable.
        user32 = ctypes.windll.user32
        try:
            while self._running:
                left_down = bool(user32.GetAsyncKeyState(VK_LCONTROL) & 0x8000)
                right_down = bool(user32.GetAsyncKeyState(VK_RCONTROL) & 0x8000)
                if (not self._hook or self._using_fallback) and (not self._fallback_notice_sent) and self.on_error:
                    self._fallback_notice_sent = True
                    bindings.app.wx_call_after_if_alive(self.on_error, "全局语音热键进入兼容模式（轮询）")
                combo = (left_down and right_down) or (
                    (left_down or right_down) and self._should_use_poller_release() and any(
                        self._should_mark_combo_key(vk) and user32.GetAsyncKeyState(vk) & 0x8000
                        for vk in range(8, 255)
                    )
                )
                self._process_poller_state(left_down=left_down, right_down=right_down, combo=combo)
                bindings.app.time.sleep(0.015)
        finally:
            self._using_fallback = False

    def _should_use_poller_release(self) -> bool:
        return self._running and (
            not self._hook or self._using_fallback
            or bindings.app.time.monotonic() - self._last_hook_event_at >= self._hook_stale_seconds
        )

    def _process_poller_state(self, *, left_down: bool, right_down: bool, combo: bool = False) -> None:
        now = bindings.app.time.monotonic()
        for side, old, down in (("left", self._poller_left_down, left_down),
                                ("right", self._poller_right_down, right_down)):
            if down:
                self._poller_pressed_at.setdefault(side, now)
                if combo:
                    self._poller_chords.add(side)
            elif old:
                started = self._poller_pressed_at.pop(side, now)
                chord = side in self._poller_chords or (side == "right" and now - started > 0.4)
                self._poller_chords.discard(side)
                if self._should_use_poller_release():
                    self._emit_ctrl_keyup(chord, side, source="poller")
        self._poller_left_down = left_down
        self._poller_right_down = right_down

    def _emit_ctrl_keyup(self, combo_used: bool, side: str, *, source: str = "") -> None:
        side_key = "right" if side == "right" else "left"
        now = bindings.app.time.monotonic()
        with self._emit_lock:
            last, previous_source = self._last_emit_at_by_side.get(side_key, (0.0, None))
            # Deduplicate hook/poller double delivery for the same key-up.
            if (now - last) < 0.06 and (not source or source != previous_source):
                return
            self._last_emit_at_by_side[side_key] = (now, source)
        bindings.app.wx_call_after_if_alive(self.on_ctrl_keyup, combo_used, side_key)

    @property
    def backend_state(self) -> str:
        if self._using_fallback or not self._hook:
            return "poller_only"
        return self._backend_state
