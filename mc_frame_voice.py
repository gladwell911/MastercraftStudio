"""Voice input, realtime calls, and global hotkeys."""

import ctypes
import winsound
import wx
from ctypes import wintypes
from mc_frame_dialogs import RealtimeCallSettingsDialog
from mc_frame_keyboard import (
    CHILDID_SELF,
    EVENT_OBJECT_VALUECHANGE,
    HOTKEY_ID_REALTIME_CALL,
    HOTKEY_ID_REALTIME_CALL_ALT,
    HOTKEY_ID_REALTIME_CALL_ALT2,
    HOTKEY_ID_SHOW,
    HOTKEY_ID_UNREAD,
    INPUT_KEYBOARD,
    KEYEVENTF_KEYUP,
    KEYEVENTF_UNICODE,
    OBJID_CLIENT,
    VK_OEM_102,
    VK_OEM_5,
    WM_CHAR,
)
from mc_frame_presentation import remove_trailing_punctuation, sanitize_optimized_text
from mc_frame_settings import VOICE_OPTIMIZE_MODEL, VOICE_OPTIMIZE_PROMPT
from realtime_call import RealtimeCallSettings
from speech_input import MODE_DIRECT, MODE_OPTIMIZE

import mc_frame_bindings as bindings


class VoiceInputMixin:
    def _resolve_sound_path(self, name: str):
        base = bindings.app.os.path.dirname(bindings.app.os.path.abspath(bindings.app.__file__))
        for n in (f"sound\\{name}", f"sound\\{name}.wav", name, f"{name}.wav", "Alarm02", "Alarm02.wav"):
            p = bindings.app.os.path.join(base, n)
            if bindings.app.os.path.isfile(p):
                return p
        sound_dir = bindings.app.os.path.join(base, "sound")
        if bindings.app.os.path.isdir(sound_dir):
            token = str(name or "").strip().lower()
            suffix_match = None
            contains_match = None
            for entry in bindings.app.os.listdir(sound_dir):
                stem, ext = bindings.app.os.path.splitext(entry)
                if ext.lower() not in {".wav", ".mp3", ".m4a"}:
                    continue
                stem_lower = stem.lower()
                full_path = bindings.app.os.path.join(sound_dir, entry)
                if stem_lower == token:
                    return full_path
                if stem_lower.endswith(token):
                    suffix_match = suffix_match or full_path
                if token and token in stem_lower:
                    contains_match = contains_match or full_path
            if suffix_match:
                return suffix_match
            if contains_match:
                return contains_match
        return None

    def _on_global_ctrl_keyup(self, combo_used: bool, side: str) -> None:
        self._voice_input.on_ctrl_keyup(combo_used=combo_used, side=side)

    def _on_global_ctrl_hook_error(self, message: str) -> None:
        self.global_ctrl_backend_status = getattr(self._global_ctrl_hook, "backend_state", "unknown")
        self.SetStatusText(message)

    def _register_global_hotkey(self):
        try:
            if not getattr(self, "_unread_hotkey_registered", False):
                # wxWidgets maps only its four modifier flags and drops
                # Win32 MOD_NOREPEAT. Register this hotkey on the same HWND
                # directly; WM_HOTKEY still reaches the existing wx binding.
                register = ctypes.WinDLL("user32", use_last_error=True).RegisterHotKey
                register.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
                register.restype = ctypes.c_int
                self._unread_hotkey_registered = bool(register(self.GetHandle(), HOTKEY_ID_UNREAD, 0x4006, ord("X")))
                self._unread_hotkey_registration_error = 0 if self._unread_hotkey_registered else ctypes.get_last_error()
        except Exception:
            self._unread_hotkey_registered = False
        if not self._unread_hotkey_registered:
            self.SetStatusText("Ctrl+Shift+X 注册失败，快捷键可能已被占用")
        try:
            if not self._show_hotkey_registered:
                self._show_hotkey_registered = bool(self.RegisterHotKey(HOTKEY_ID_SHOW, wx.MOD_CONTROL, wx.WXK_F12))
        except Exception:
            self._show_hotkey_registered = False
        realtime_call_hotkeys = [
            (HOTKEY_ID_REALTIME_CALL, VK_OEM_5),
            (HOTKEY_ID_REALTIME_CALL_ALT, self._resolve_backslash_hotkey_vk()),
            (HOTKEY_ID_REALTIME_CALL_ALT2, VK_OEM_102),
        ]
        registered_ids = set()
        seen_vks = set()
        for hotkey_id, vk_code in realtime_call_hotkeys:
            if not isinstance(vk_code, int) or vk_code <= 0 or vk_code in seen_vks:
                continue
            seen_vks.add(vk_code)
            try:
                if self.RegisterHotKey(hotkey_id, wx.MOD_CONTROL, vk_code):
                    registered_ids.add(hotkey_id)
            except Exception:
                pass
        self._realtime_call_hotkey_registered_ids = registered_ids

    def _resolve_backslash_hotkey_vk(self):
        try:
            mapped = int(ctypes.windll.user32.VkKeyScanW(ord("\\")))
        except Exception:
            return VK_OEM_5
        if mapped < 0:
            return VK_OEM_5
        vk_code = mapped & 0xFF
        return vk_code or VK_OEM_5

    def _unregister_global_hotkey(self):
        if getattr(self, "_unread_hotkey_registered", False):
            self.UnregisterHotKey(HOTKEY_ID_UNREAD)
            self._unread_hotkey_registered = False
        try:
            if self._show_hotkey_registered:
                self.UnregisterHotKey(HOTKEY_ID_SHOW)
        except Exception:
            pass
        self._show_hotkey_registered = False
        for hotkey_id in tuple(self._realtime_call_hotkey_registered_ids):
            try:
                self.UnregisterHotKey(hotkey_id)
            except Exception:
                pass
        self._realtime_call_hotkey_registered_ids = set()

    def _on_global_hotkey(self, event):
        hotkey_id = event.GetId()
        if hotkey_id == HOTKEY_ID_UNREAD:
            self._jump_to_unread_chat()
            return
        if hotkey_id == HOTKEY_ID_SHOW:
            self._restore_or_raise()
            return
        if hotkey_id in {
            HOTKEY_ID_REALTIME_CALL,
            HOTKEY_ID_REALTIME_CALL_ALT,
            HOTKEY_ID_REALTIME_CALL_ALT2,
        }:
            self._toggle_realtime_call()

    def _on_show_sync_tray_state(self, event):
        # When the window is restored externally (e.g. second EXE launch), keep tray state in sync.
        if event.IsShown():
            self._is_in_tray = False
        event.Skip()

    def _deliver_voice_event(self, deliver):
        if not getattr(self, "_closing", False):
            deliver()

    def _on_voice_state(self, text: str):
        if text == "开始录音":
            self._play_voice_begin_sound()

    def _on_voice_error(self, msg: str):
        self.SetStatusText(msg)
        self._speak_text_via_screen_reader(msg)
        self._play_voice_wrong_sound()

    def _on_realtime_call_status(self, message: str):
        if message:
            self.SetStatusText(message)

    def _on_realtime_call_error(self, message: str):
        self.SetStatusText(message)
        self._play_voice_wrong_sound()

    def _on_realtime_call_active_changed(self, active: bool):
        if active:
            self.SetStatusText("实时语音通话中")

    def _toggle_realtime_call(self):
        action = self._realtime_call.toggle()
        if action == "start":
            self._play_voice_begin_sound()
            return
        if action == "stop":
            self._play_voice_end_sound()

    def _on_open_realtime_call_settings(self, _event):
        dlg = RealtimeCallSettingsDialog(
            self,
            role=self.realtime_call_role,
            speech_rate=self.realtime_call_speech_rate,
        )
        if dlg.ShowModal() == wx.ID_OK:
            settings = dlg.get_settings()
            self._apply_realtime_call_settings(settings)
        dlg.Destroy()

    def _apply_realtime_call_settings(self, settings: RealtimeCallSettings):
        normalized = settings.normalized()
        self.realtime_call_role = normalized.role
        self.realtime_call_speech_rate = normalized.speech_rate
        message = self._realtime_call.update_settings(normalized)
        bindings.app.wx_call_after_if_alive(self._realtime_call.prepare)
        self._save_state()
        self.SetStatusText(message)

    def _append_text_to_focused_editor(self, text: str):
        if not text:
            return
        focus = wx.Window.FindFocus()
        target = focus if isinstance(focus, wx.TextCtrl) and focus.IsEditable() else self.input_edit
        target.SetFocus()
        try:
            target.SetInsertionPointEnd()
        except Exception:
            pass
        wrote = False
        writer = getattr(target, "WriteText", None)
        if callable(writer):
            try:
                writer(text)
                wrote = True
            except Exception:
                wrote = False
        if not wrote:
            old = target.GetValue()
            target.SetValue(old + text)
        try:
            target.SetInsertionPointEnd()
        except Exception:
            pass
        self._notify_accessible_text_update(target)

    def _notify_accessible_text_update(self, window: wx.Window | None) -> None:
        if window is None:
            return
        try:
            hwnd = int(window.GetHandle() or 0)
        except Exception:
            hwnd = 0
        self._notify_accessible_value_change(hwnd)

    def _notify_accessible_value_change(self, hwnd: int | None) -> None:
        try:
            hwnd = int(hwnd or 0)
        except Exception:
            hwnd = 0
        if hwnd <= 0:
            return
        try:
            user32 = ctypes.windll.user32
            user32.NotifyWinEvent(EVENT_OBJECT_VALUECHANGE, wintypes.HWND(hwnd), OBJID_CLIENT, CHILDID_SELF)
        except Exception:
            return

    def _inject_text_to_foreground_window(self, text: str) -> bool:
        if not text:
            return False
        try:
            user32 = ctypes.windll.user32
            fg_hwnd = user32.GetForegroundWindow()
            if not fg_hwnd:
                return False
            pid = wintypes.DWORD(0)
            fg_tid = user32.GetWindowThreadProcessId(fg_hwnd, ctypes.byref(pid))

            class GUITHREADINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("flags", wintypes.DWORD),
                    ("hwndActive", wintypes.HWND),
                    ("hwndFocus", wintypes.HWND),
                    ("hwndCapture", wintypes.HWND),
                    ("hwndMenuOwner", wintypes.HWND),
                    ("hwndMoveSize", wintypes.HWND),
                    ("hwndCaret", wintypes.HWND),
                    ("rcCaret", wintypes.RECT),
                ]

            gti = GUITHREADINFO()
            gti.cbSize = ctypes.sizeof(GUITHREADINFO)
            target_hwnd = fg_hwnd
            if fg_tid and user32.GetGUIThreadInfo(fg_tid, ctypes.byref(gti)):
                if gti.hwndFocus:
                    target_hwnd = gti.hwndFocus

            data = text.encode("utf-16-le")
            sent = 0
            for i in range(0, len(data), 2):
                unit = int.from_bytes(data[i:i + 2], "little")
                if unit == 0:
                    continue
                if user32.PostMessageW(target_hwnd, WM_CHAR, unit, 0):
                    sent += 1
            if sent > 0:
                self._notify_accessible_value_change(target_hwnd)
            return sent > 0
        except Exception:
            return False

    def _type_text_to_system_focus(self, text: str) -> bool:
        if not text:
            return False
        try:
            user32 = ctypes.windll.user32

            class KEYBDINPUT(ctypes.Structure):
                _fields_ = [
                    ("wVk", wintypes.WORD),
                    ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
                ]

            class _INPUTUNION(ctypes.Union):
                _fields_ = [("ki", KEYBDINPUT)]

            class INPUT(ctypes.Structure):
                _fields_ = [("type", wintypes.DWORD), ("union", _INPUTUNION)]

            events = []
            for ch in text:
                cp = ord(ch)
                if cp > 0xFFFF:
                    continue
                down = INPUT(
                    type=INPUT_KEYBOARD,
                    union=_INPUTUNION(
                        ki=KEYBDINPUT(
                            wVk=0,
                            wScan=cp,
                            dwFlags=KEYEVENTF_UNICODE,
                            time=0,
                            dwExtraInfo=None,
                        )
                    ),
                )
                up = INPUT(
                    type=INPUT_KEYBOARD,
                    union=_INPUTUNION(
                        ki=KEYBDINPUT(
                            wVk=0,
                            wScan=cp,
                            dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
                            time=0,
                            dwExtraInfo=None,
                        )
                    ),
                )
                events.extend([down, up])
            if not events:
                return False

            n_inputs = len(events)
            arr_type = INPUT * n_inputs
            sent = user32.SendInput(n_inputs, arr_type(*events), ctypes.sizeof(INPUT))
            if sent != n_inputs:
                return False
            return True
        except Exception:
            return False

    def _insert_text_to_system_focus(self, text: str) -> bool:
        norm = remove_trailing_punctuation(text)
        if not norm:
            return False
        if self._inject_text_to_foreground_window(norm):
            return True
        if self._type_text_to_system_focus(norm):
            return True
        return False

    def _optimize_voice_text(self, text: str) -> str:
        api_key = bindings.app.openrouter_api_key_for_app()
        if not api_key:
            return sanitize_optimized_text(text)
        try:
            # 使用更短的超时时间提高响应速度
            c = bindings.app.ChatClient(api_key=api_key, model=VOICE_OPTIMIZE_MODEL, timeout=15)
            out = c.rewrite_text(text=text, instruction=VOICE_OPTIMIZE_PROMPT, model=VOICE_OPTIMIZE_MODEL)
            return sanitize_optimized_text(out or text)
        except Exception:
            return sanitize_optimized_text(text)

    def _on_voice_result(self, text: str, mode: str = MODE_DIRECT):
        if mode == MODE_OPTIMIZE:
            generation = self._voice_input.generation
            def _worker():
                optimized = self._optimize_voice_text(text)
                def deliver():
                    if self._voice_input.is_generation_current(generation):
                        self._on_voice_optimized_result(optimized)
                bindings.app.wx_call_after_if_alive(self._deliver_voice_event, deliver)

            bindings.app.threading.Thread(target=_worker, daemon=True).start()
            return

        self._finalize_voice_input_with_feedback(text)

    def _on_voice_optimized_result(self, text: str):
        self._finalize_voice_input_with_feedback(text)

    def _finalize_voice_input_with_feedback(self, text: str):
        # 顺序：写入焦点 -> 延迟朗读写入内容（结束音效在单击 Ctrl 停止录音时立即播放）
        focus = wx.Window.FindFocus()
        if isinstance(focus, wx.TextCtrl) and focus.IsEditable():
            self._append_text_to_focused_editor(text)
        elif self.IsActive():
            self._append_text_to_focused_editor(text)
        elif not self._insert_text_to_system_focus(text):
            self._play_voice_wrong_sound()
            return
        if self._call_later_if_alive(200, self._speak_text_via_screen_reader, text) is None:
            self._speak_text_via_screen_reader(text)

    def _on_voice_stop_recording(self):
        self._play_voice_end_sound()

    def _speak_text_via_screen_reader(self, text: str):
        content = remove_trailing_punctuation(text)
        if not content:
            self.voice_screen_reader_status = {
                "last_text": "",
                "last_success": False,
                "last_error": "empty after normalization",
            }
            return
        self.voice_screen_reader_status = {
            "last_text": content,
            "last_success": None,
            "last_error": "",
        }
        errors = []
        try:
            ok = bool(self._zdsr_tts.speak(content))
        except Exception as exc:
            ok = False
            errors.append(str(exc))
        if not ok:
            try:
                self._zdsr_tts = bindings.app.ZDSRTTSClient()
                ok = bool(self._zdsr_tts.speak(content))
            except Exception as exc:
                ok = False
                errors.append(str(exc))
        self.voice_screen_reader_status = {
            "last_text": content,
            "last_success": ok,
            "last_error": "" if ok else ("; ".join(err for err in errors if err) or "speak returned false"),
        }

    def _play_finish_sound(self):
        if self.reply_sound:
            try:
                winsound.PlaySound(self.reply_sound, winsound.SND_FILENAME | winsound.SND_ASYNC)
                return
            except RuntimeError:
                pass
        winsound.MessageBeep(winsound.MB_ICONASTERISK)

    def _play_send_sound(self):
        if self.send_sound:
            try:
                winsound.PlaySound(self.send_sound, winsound.SND_FILENAME | winsound.SND_ASYNC)
                return
            except RuntimeError:
                pass
        winsound.MessageBeep(winsound.MB_OK)

    def _play_voice_begin_sound(self):
        if self.voice_begin_sound:
            try:
                winsound.PlaySound(self.voice_begin_sound, winsound.SND_FILENAME | winsound.SND_ASYNC)
                return
            except RuntimeError:
                pass
        winsound.MessageBeep(winsound.MB_OK)

    def _play_voice_end_sound(self):
        if self.voice_end_sound:
            try:
                winsound.PlaySound(self.voice_end_sound, winsound.SND_FILENAME | winsound.SND_ASYNC)
                return
            except RuntimeError:
                pass
        winsound.MessageBeep(winsound.MB_ICONASTERISK)

    def _play_voice_wrong_sound(self):
        if self.voice_wrong_sound:
            try:
                winsound.PlaySound(self.voice_wrong_sound, winsound.SND_FILENAME | winsound.SND_ASYNC)
                return
            except RuntimeError:
                pass
        winsound.MessageBeep(winsound.MB_ICONHAND)
