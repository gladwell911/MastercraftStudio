"""Desktop keyboard routing, focus, and native navigation."""

import ctypes
import wx
import wx.adv
from ctypes import wintypes
from mc_frame_settings import (
    APP_WINDOW_TITLE,
    NAVIGATION_QUIET_ALT_KEYS,
    NAVIGATION_QUIET_KEYS,
    NAVIGATION_QUIET_SECONDS,
)

import mc_frame_bindings as bindings


class ChatNavigationMixin:
    def _on_model_changed(self, _):
        model = self._resolve_current_model()
        # The read-only combobox only exposes visible models.  Do not let an
        # unsupported value (for example, a transient control value) overwrite
        # the active chat or produce a misleading remote state event.
        if not bindings.app.is_visible_model_id(model):
            return
        if self.selected_model == model:
            try:
                self._sync_codex_speed_combo_enabled_for_model(model)
            except Exception:
                pass
            return
        self.selected_model = model
        try:
            self._sync_codex_speed_combo_enabled_for_model(model)
        except Exception:
            pass

    def _show_tools_menu(self) -> None:
        menu = wx.Menu()
        voice_id = wx.NewIdRef()
        filter_id = wx.NewIdRef()
        attach_id = wx.NewIdRef()
        menu.Append(voice_id, "语音通话设置")
        menu.Append(attach_id, "载入图片或文件")
        filter_item = menu.AppendCheckItem(filter_id, "过滤英文内容")
        filter_item.Check(bool(self.codex_answer_english_filter_enabled))
        self._append_terminal_history_filter_item(menu)
        self.Bind(wx.EVT_MENU, self._on_open_realtime_call_settings, id=voice_id)
        self.Bind(wx.EVT_MENU, lambda _evt: self._load_chat_attachments_via_dialog(), id=attach_id)
        self.Bind(wx.EVT_MENU, lambda _evt: self._toggle_codex_answer_filter(), id=filter_id)
        try:
            self.PopupMenu(menu, (0, 0))
        finally:
            menu.Destroy()

    def _cancel_pending_tools_menu_open(self) -> None:
        self._alt_menu_armed = False
        self._alt_menu_suppressed = False

    def _arm_tools_menu_open(self) -> None:
        self._alt_menu_armed = True
        self._alt_menu_suppressed = False

    def _suppress_tools_menu_open(self) -> None:
        if getattr(self, "_alt_menu_armed", False):
            self._alt_menu_suppressed = True

    def _handle_alt_key_up(self) -> bool:
        should_open = bool(getattr(self, "_alt_menu_armed", False)) and not bool(getattr(self, "_alt_menu_suppressed", False))
        self._cancel_pending_tools_menu_open()
        return False

    def _is_context_menu_key(self, key: int) -> bool:
        menu_keys = {wx.WXK_MENU}
        windows_menu = getattr(wx, "WXK_WINDOWS_MENU", None)
        if windows_menu is not None:
            menu_keys.add(windows_menu)
        return key in menu_keys

    def _focus_control_safely(self, control) -> bool:
        if control is None:
            return False
        try:
            if hasattr(control, "IsEnabled") and not control.IsEnabled():
                return False
        except Exception:
            pass
        try:
            control.SetFocus()
            return True
        except Exception:
            return False

    def _focus_current_detail_list(self) -> bool:
        return self._focus_control_safely(self._current_detail_tab_target())

    def _focus_input_box(self) -> bool:
        return self._focus_control_safely(getattr(self, "input_edit", None))

    def _event_shift_down(self, event) -> bool:
        shift_down = getattr(event, "ShiftDown", None)
        if callable(shift_down):
            return bool(shift_down())
        try:
            return bool(wx.GetKeyState(wx.WXK_SHIFT))
        except Exception:
            return False

    def _is_input_focus_space_shortcut(self, event) -> bool:
        """Return whether Space should move focus from a named navigation control."""
        try:
            if int(event.GetKeyCode()) != wx.WXK_SPACE:
                return False
        except Exception:
            return False
        if self._event_control_down(event) or self._event_alt_down(event) or self._event_shift_down(event):
            return False
        for control_name in (
            "notes_notebook_list",
            "notes_entry_list",
            "history_list",
            "answer_list",
            "model_combo",
        ):
            control = getattr(self, control_name, None)
            try:
                if control is not None and control.HasFocus():
                    return True
            except Exception:
                continue
        return False

    def _handle_input_focus_space_shortcut(self, event) -> bool:
        if not self._is_input_focus_space_shortcut(event):
            return False
        # Consume even when input is unavailable so native list/combo Space
        # behavior cannot run after a recognized shortcut.
        self._focus_input_box()
        return True

    def _focus_history_list(self) -> bool:
        self._cancel_chat_navigation_focus()
        self._flush_chat_navigation_history_selection()
        return self._focus_control_safely(getattr(self, "history_list", None))

    def _handle_window_focus_shortcut(self, key: int, alt_down: bool, ctrl_down: bool) -> bool:
        if not alt_down or ctrl_down:
            return False
        try:
            letter = chr(int(key)).upper()
        except Exception:
            return False
        handlers = {
            "F": self._focus_current_detail_list,
            "D": self._focus_input_box,
            "G": self._focus_history_list,
            "B": self._focus_visible_notes_list,
        }
        handler = handlers.get(letter)
        if handler is None:
            return False
        self._suppress_tools_menu_open()
        return bool(handler())

    def _primary_navigation_control_has_focus(self) -> bool:
        controls = [
            getattr(self, "execution_list", None),
            getattr(self, "answer_list", None),
            getattr(self, "history_list", None),
            getattr(self, "input_edit", None),
            getattr(self, "model_combo", None),
            getattr(self, "codex_speed_combo", None),
            getattr(self, "notes_notebook_list", None),
            getattr(self, "notes_entry_list", None),
            getattr(self, "notes_editor", None),
        ]
        for control in controls:
            if control is None:
                continue
            try:
                if control.HasFocus():
                    return True
            except Exception:
                continue
        return False

    def _on_char_hook(self, event):
        if getattr(self, "_closing", False):
            return
        key = event.GetKeyCode()
        ctrl_down = self._event_control_down(event)
        alt_down = self._event_alt_down(event)
        notes_has_focus = bool(
            getattr(self, "notes_notebook_list", None)
            and (
                self.notes_notebook_list.HasFocus()
                or self.notes_entry_list.HasFocus()
                or self.notes_editor.HasFocus()
            )
        )
        if self._handle_ctrl_history_navigation(event):
            return
        if key not in (wx.WXK_CONTROL, wx.WXK_SHIFT, wx.WXK_ALT):
            self._cancel_chat_navigation_focus()
        if key == wx.WXK_ALT and alt_down and not ctrl_down:
            event.Skip()
            return
        if not ctrl_down and self._is_continue_shortcut(key, alt_down):
            self._suppress_tools_menu_open()
            accepted, error = self._submit_question("好的，继续")
            if not accepted and error:
                self.SetStatusText(error)
            return
        if alt_down and not ctrl_down and key in (ord("Y"), ord("y")):
            self._chat_information_previous_focus = wx.Window.FindFocus()
            self._show_chat_information()
            return
        if self._handle_input_focus_space_shortcut(event):
            return
        self._touch_navigation_quiet_window(event)
        self._suppress_tools_menu_open()
        if (
            ctrl_down
            and not alt_down
            and key in (ord("C"), ord("c"))
            and hasattr(self, "answer_list")
            and self.answer_list.HasFocus()
            and self._copy_selected_answer_to_clipboard()
        ):
            return
        if self._handle_window_focus_shortcut(key, alt_down, ctrl_down):
            return
        if (
            self._is_context_menu_key(key)
            and notes_has_focus
        ):
            self._show_notes_menu()
            return
        if (
            key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER)
            and notes_has_focus
            and not ctrl_down
            and not alt_down
        ):
            self._on_notes_key_down(event)
            return
        if (
            key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER)
            and self.history_list.HasFocus()
            and not ctrl_down
            and not alt_down
        ):
            if self._activate_selected_history():
                return
        if self._handle_named_new_chat_shortcut(event):
            return
        if key == wx.WXK_F2 and self.history_list.HasFocus() and not ctrl_down and not alt_down:
            self._flush_chat_navigation_history_selection()
            self._history_rename(None)
            return
        if (
            key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER)
            and self.answer_list.HasFocus()
            and not ctrl_down
            and not alt_down
        ):
            read_target = self._selected_read_target()
            shift_down = getattr(event, "ShiftDown", None)
            if callable(shift_down) and shift_down():
                handled = self._try_open_selected_answer_detail()
            else:
                handled = self._open_selected_answer_text_viewer()
            if handled:
                self._confirm_answer_read(read_target)
                return
        if (
            key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER)
            and hasattr(self, "execution_list")
            and self.execution_list.HasFocus()
            and not ctrl_down
            and not alt_down
        ):
            shift_down = getattr(event, "ShiftDown", None)
            if callable(shift_down) and shift_down():
                handled = self._try_open_selected_execution_detail()
            else:
                handled = self._open_selected_execution_text_viewer()
            if handled:
                return
        if key == wx.WXK_F1 and not ctrl_down and not alt_down:
            self._toggle_detail_panel_mode(focus_detail=True)
            return
        if self._is_clear_context_shortcut(key, alt_down):
            if not bool(getattr(event, "IsAutoRepeat", lambda: False)()):
                self._trigger_shortcut_clear()
            return
        if self._is_new_chat_shortcut(key, alt_down):
            self._trigger_new_chat()
            return
        event.Skip()

    def _event_control_down(self, event) -> bool:
        ctrl_down = getattr(event, "ControlDown", None)
        if callable(ctrl_down):
            return bool(ctrl_down())
        try:
            return bool(wx.GetKeyState(wx.WXK_CONTROL))
        except Exception:
            return False

    def _event_alt_down(self, event) -> bool:
        alt_down = getattr(event, "AltDown", None)
        if callable(alt_down):
            return bool(alt_down())
        try:
            return bool(wx.GetKeyState(wx.WXK_ALT))
        except Exception:
            return False

    def _is_navigation_quiet_trigger(self, event) -> bool:
        try:
            key = int(event.GetKeyCode())
        except Exception:
            return False
        alt_down = self._event_alt_down(event)
        ctrl_down = self._event_control_down(event)
        if ctrl_down:
            return False
        if alt_down:
            return key in NAVIGATION_QUIET_ALT_KEYS
        return key in NAVIGATION_QUIET_KEYS

    def _touch_navigation_quiet_window(self, event) -> None:
        if not self._is_navigation_quiet_trigger(event):
            return
        self._navigation_quiet_until = bindings.app.time.monotonic() + NAVIGATION_QUIET_SECONDS
        try:
            key = int(event.GetKeyCode())
        except Exception:
            key = 0
        self._navigation_quiet_last_trigger = f"key:{key}"
        self._schedule_navigation_quiet_flush()

    def _navigation_quiet_active(self) -> bool:
        return bindings.app.time.monotonic() < float(getattr(self, "_navigation_quiet_until", 0.0) or 0.0)

    def _stop_navigation_quiet_flush(self) -> None:
        timer = getattr(self, "_navigation_quiet_flush_timer", None)
        if timer is not None:
            timer.Stop()
        self._navigation_quiet_flush_timer = None

    def _schedule_navigation_quiet_flush(self) -> None:
        if not self._is_ui_alive():
            return
        self._stop_navigation_quiet_flush()
        remaining = max(0.0, float(getattr(self, "_navigation_quiet_until", 0.0) or 0.0) - bindings.app.time.monotonic())
        delay_ms = int(round(remaining * 1000))
        self._navigation_quiet_flush_timer = self._call_later_if_alive(delay_ms, self._flush_pending_background_ui_updates)

    def _in_background_ui_update(self) -> bool:
        return int(getattr(self, "_background_ui_update_depth", 0) or 0) > 0

    def _background_ui_mutations_blocked(self) -> bool:
        return self._in_background_ui_update() and self._navigation_quiet_active()

    def _is_real_escape_keydown(self, event) -> bool:
        ctrl_down = getattr(event, "ControlDown", None)
        alt_down = getattr(event, "AltDown", None)
        ctrl_pressed = bool(ctrl_down()) if callable(ctrl_down) else False
        alt_pressed = bool(alt_down()) if callable(alt_down) else False
        if ctrl_pressed or alt_pressed:
            return False
        shift_down = getattr(event, "ShiftDown", None)
        if callable(shift_down) and shift_down():
            return False
        if event.GetKeyCode() != wx.WXK_ESCAPE:
            return False
        get_raw = getattr(event, "GetRawKeyCode", None)
        if not callable(get_raw):
            return False
        try:
            raw = int(get_raw())
        except Exception:
            return False
        return raw == 27

    def _on_any_key_down_escape_minimize(self, event) -> bool:
        if getattr(self, "notes_editor", None):
            try:
                if getattr(self, "notes_controller", None) and self.notes_controller.notes_view == "note_edit":
                    return False
            except Exception:
                pass
        if self._is_real_escape_keydown(event):
            self._minimize_to_tray()
            return True
        return False

    def _on_frame_key_down(self, event) -> None:
        if self._on_any_key_down_escape_minimize(event):
            return
        event.Skip()

    def _on_input_key_down(self, event):
        if self._on_any_key_down_escape_minimize(event):
            return
        self._touch_navigation_quiet_window(event)
        if self._handle_ctrl_history_navigation(event):
            return
        if self._handle_primary_tab_navigation(event):
            return
        key = event.GetKeyCode()
        if key != wx.WXK_ALT:
            self._suppress_tools_menu_open()
        if event.ControlDown() and key in (ord("V"), ord("v")) and self.input_edit.HasFocus():
            if self._try_paste_clipboard_attachments_to_input():
                return
        ctrl_down = self._event_control_down(event)
        alt_down = self._event_alt_down(event)
        shift_down = bool(getattr(event, "ShiftDown", lambda: False)())
        is_enter = key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER)
        if is_enter and self._has_input_ime_candidates():
            event.Skip()
            return
        if is_enter:
            if alt_down or ctrl_down or shift_down:
                event.Skip()
                return
            if bool(getattr(event, "IsAutoRepeat", lambda: False)()):
                return
            self._trigger_send()
            return
        if self._is_clear_context_shortcut(key, event.AltDown()):
            if not bool(getattr(event, "IsAutoRepeat", lambda: False)()):
                self._trigger_shortcut_clear()
            return
        if self._is_new_chat_shortcut(key, event.AltDown()):
            self._trigger_new_chat()
            return
        event.Skip()

    def _is_continue_shortcut(self, key, alt):
        return alt and key in (ord("C"), ord("c"))

    def _is_clear_context_shortcut(self, key, alt):
        return alt and key in (ord("A"), ord("a"))

    def _on_input_key_up(self, event):
        if event.GetKeyCode() == wx.WXK_ALT:
            if self._handle_alt_key_up():
                return
        event.Skip()

    def _has_native_ime_composition(self, window: wx.Window | None) -> bool:
        hwnd = window.GetHandle() if window else 0
        if not hwnd:
            return False
        try:
            imm32 = ctypes.windll.imm32
            himc = imm32.ImmGetContext(wintypes.HWND(hwnd))
            if not himc:
                return False
            try:
                # GCS_COMPSTR: composition without a candidate window still
                # owns keyboard input and must not leak into app shortcuts.
                get_composition = getattr(imm32, "ImmGetCompositionStringW", None)
                if callable(get_composition) and int(get_composition(himc, 0x0008, None, 0)) > 0:
                    return True
                list_count = wintypes.DWORD(0)
                buf_len = wintypes.DWORD(0)
                if not imm32.ImmGetCandidateListCountW(himc, ctypes.byref(list_count), ctypes.byref(buf_len)):
                    return False
                if int(list_count.value) <= 0 or int(buf_len.value) <= 0:
                    return False
                size = int(imm32.ImmGetCandidateListW(himc, 0, None, 0))
                if size <= 0:
                    return False
                buf = (ctypes.c_byte * size)()
                copied = int(imm32.ImmGetCandidateListW(himc, 0, ctypes.byref(buf), size))
                if copied <= 0:
                    return False

                class _CandidateHeader(ctypes.Structure):
                    _fields_ = [
                        ("dwSize", wintypes.DWORD),
                        ("dwStyle", wintypes.DWORD),
                        ("dwCount", wintypes.DWORD),
                        ("dwSelection", wintypes.DWORD),
                        ("dwPageStart", wintypes.DWORD),
                        ("dwPageSize", wintypes.DWORD),
                    ]

                if copied < ctypes.sizeof(_CandidateHeader):
                    return False
                header = ctypes.cast(ctypes.byref(buf), ctypes.POINTER(_CandidateHeader)).contents
                return int(header.dwCount) > 0
            finally:
                imm32.ImmReleaseContext(wintypes.HWND(hwnd), himc)
        except Exception:
            return False

    def _has_input_ime_candidates(self) -> bool:
        return self._has_native_ime_composition(self.input_edit)

    def _global_chat_navigation_target_state(self) -> tuple[bool, dict]:
        try:
            frame_hwnd = int(self.GetHandle() or 0)
        except Exception:
            frame_hwnd = 0
        fg_hwnd = 0
        root_hwnd = 0
        if frame_hwnd > 0:
            try:
                user32 = ctypes.windll.user32
                fg_hwnd = int(user32.GetForegroundWindow() or 0)
                ga_root = 2
                root_hwnd = int(user32.GetAncestor(wintypes.HWND(fg_hwnd), ga_root) or 0) if fg_hwnd else 0
            except Exception:
                fg_hwnd = 0
                root_hwnd = 0
        details = {
            "frame_hwnd": frame_hwnd,
            "fg_hwnd": fg_hwnd,
            "root_hwnd": root_hwnd,
        }
        return (frame_hwnd > 0 and fg_hwnd > 0 and root_hwnd == frame_hwnd), details

    def _log_ctrl_navigation_debug(self, direction: str, *, reason: str = "", result: str = "", details: dict | None = None) -> None:
        if bindings.app.os.environ.get("MC_CTRL_NAVIGATION_DEBUG", "").strip().lower() not in {"1", "true", "on", "yes"}:
            return
        path = self.app_data_dir / "ctrl_navigation_debug.log"
        detail_text = ""
        if details:
            detail_text = " ".join(f"{key}={value}" for key, value in details.items())
        parts = [f"direction={direction}"]
        if reason:
            parts.append(f"reason={reason}")
        if result:
            parts.append(f"result={result}")
        if detail_text:
            parts.append(detail_text)
        line = " ".join(parts).strip()
        try:
            from owned_logs import append_diagnostic
            append_diagnostic(path, line + "\n")
        except Exception:
            return

    def _handle_vertical_chat_shortcut(self, direction: int) -> bool:
        notes = getattr(self, "notes_entry_list", None)
        if notes and notes.HasFocus():
            self._cancel_chat_navigation_focus()
            return self._notes_move_entry_up() if direction < 0 else self._notes_move_entry_down()
        return self._navigate_history_chats(direction)

    def _cancel_chat_navigation_focus(self, *, stop_speech=True) -> None:
        self._chat_navigation_generation = getattr(self, "_chat_navigation_generation", 0) + 1
        timer = getattr(self, "_chat_navigation_timer", None)
        if timer is not None:
            timer.Stop()
            self._chat_navigation_timer = None
        if stop_speech:
            title_accepted = getattr(self, "_chat_navigation_title_accepted", False)
            self._chat_navigation_title_accepted = False
            if title_accepted:
                self._zdsr_tts.stop()
            self._flush_chat_navigation_answer_rows()

    def _flush_chat_navigation_history_selection(self) -> None:
        if not getattr(self, "_chat_navigation_history_selection_deferred", False):
            return
        self._chat_navigation_history_selection_deferred = False
        if getattr(self, "_closing", False) or not self._is_ui_alive():
            return
        owner = self.view_history_id if self.view_mode == "history" else self._current_history_id()
        if owner and not self._select_history_row_if_present(owner):
            self._refresh_history(owner)

    def _flush_chat_navigation_answer_rows(self, *, select_latest_message=False) -> None:
        if not getattr(self, "_chat_navigation_answer_rows_deferred", False):
            return
        self._chat_navigation_answer_rows_deferred = False
        if not getattr(self, "_closing", False) and self._is_ui_alive():
            try:
                self._render_answer_list(preserve_panel=True, select_latest_message=select_latest_message)
            except Exception:
                self._chat_navigation_answer_rows_deferred = True
                raise

    def _on_chat_navigation_child_focus(self, event) -> None:
        focus = wx.Window.FindFocus()
        if (getattr(self, "_chat_navigation_timer", None) is not None
                and focus is not self._chat_navigation_origin):
            self._cancel_chat_navigation_focus()
        if focus is self.history_list and getattr(self, "_chat_navigation_timer", None) is None:
            self._flush_chat_navigation_history_selection()
        event.Skip()

    def _announce_chat_navigation(self, chat_id: str, title: str, focus) -> None:
        navigation_generation = self._chat_navigation_generation
        self._chat_navigation_origin = focus
        started = bindings.app.time.monotonic()
        speaking = False
        retried = False
        idle_since = None

        def speak_title():
            result = self._zdsr_tts.speak_chat_navigation(title)
            if result is True:
                self._chat_navigation_title_accepted = True
            return result

        spoken = speak_title()

        def finish():
            self._flush_chat_navigation_answer_rows(select_latest_message=True)
            self._focus_latest_answer_immediately(include_question=True, reveal_answers=True)

        def poll():
            nonlocal speaking, spoken, retried, idle_since
            if navigation_generation != self._chat_navigation_generation:
                return
            if (getattr(self, "_closing", False) or not self._is_ui_alive()
                    or wx.Window.FindFocus() is not focus):
                self._cancel_chat_navigation_focus()
                return
            visible = self.view_history_id if self.view_mode == "history" else self._current_history_id()
            if visible != chat_id:
                self._cancel_chat_navigation_focus()
                return
            elapsed = bindings.app.time.monotonic() - started
            if spoken is None and elapsed < 4.0:
                spoken = speak_title()
            state = self._zdsr_tts.speak_state() if spoken else None
            if state is True:
                speaking = True
                idle_since = None
            elif state is False and speaking:
                if idle_since is None:
                    idle_since = elapsed
                elif elapsed - idle_since >= 0.075:
                    self._chat_navigation_timer = None
                    self._chat_navigation_title_accepted = False
                    finish()
                    return
            elif not retried and spoken is True and state is False and elapsed >= 1.75:
                # Older DLLs can accept the first request while internally
                # initializing, then never start it or report initialization.
                spoken = speak_title()
                retried = True
            elif spoken is False or (elapsed >= 4.0 and (not speaking or state is None)):
                self._cancel_chat_navigation_focus()
                return
            # A stuck reader must never move focus while reporting speech.
            self._chat_navigation_timer = wx.CallLater(250 if elapsed >= 15 else 25, poll)

        self._chat_navigation_timer = wx.CallLater(25, poll)

    def _on_global_ctrl_arrow(self, direction: str) -> None:
        direction_key = str(direction or "").strip().lower()
        if direction_key not in {"up", "down"}:
            return
        step = -1 if direction_key == "up" else 1
        active, details = self._global_chat_navigation_target_state()
        if not active:
            self._log_ctrl_navigation_debug(direction_key, reason="inactive_target", details=details)
            self.SetStatusText("Ctrl+上下未生效：当前前台焦点不属于本程序")
            return
        navigated = bool(self._handle_vertical_chat_shortcut(step))
        self._log_ctrl_navigation_debug(
            direction_key,
            result="navigated" if navigated else "no_target",
            details=details,
        )

    def _is_send_shortcut(self, key, ctrl, alt):
        return ((not ctrl) and (not alt) and key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER)) or (alt and key in (ord("S"), ord("s")))

    def _is_new_chat_shortcut(self, key, alt):
        return alt and key in (ord("N"), ord("n"))

    def _ensure_tray_icon(self):
        if self._tray_icon:
            return
        frame = self

        class _TrayIcon(wx.adv.TaskBarIcon):
            def __init__(self):
                super().__init__()
                icon = wx.ArtProvider.GetIcon(wx.ART_INFORMATION, wx.ART_OTHER, (16, 16))
                self.SetIcon(icon, APP_WINDOW_TITLE)
                self.Bind(wx.adv.EVT_TASKBAR_LEFT_DCLICK, self._on_activate)
                self.Bind(wx.adv.EVT_TASKBAR_LEFT_UP, self._on_activate)

            def _on_activate(self, _event):
                frame._restore_from_tray()

        self._tray_icon = _TrayIcon()

    def _minimize_to_tray(self):
        self._ensure_tray_icon()
        self.Hide()
        self._is_in_tray = True

    def _restore_from_tray(self):
        self.Show()
        self.Iconize(False)
        self.Raise()
        self._is_in_tray = False
        self.input_edit.SetFocus()

    def _restore_or_raise(self):
        if self._is_in_tray or (not self.IsShown()):
            self._restore_from_tray()
            return
        self.Iconize(False)
        self.Raise()
        try:
            self.RequestUserAttention(wx.USER_ATTENTION_INFO)
        except Exception:
            pass

    def _extract_committed_char(self, event):
        if event.ControlDown() or event.AltDown():
            return ""
        key = event.GetUnicodeKey() if hasattr(event, "GetUnicodeKey") else event.GetKeyCode()
        if key in (
            wx.WXK_NONE,
            wx.WXK_RETURN,
            wx.WXK_NUMPAD_ENTER,
            wx.WXK_TAB,
            wx.WXK_ESCAPE,
            wx.WXK_UP,
            wx.WXK_DOWN,
            wx.WXK_LEFT,
            wx.WXK_RIGHT,
            wx.WXK_HOME,
            wx.WXK_END,
            wx.WXK_PAGEUP,
            wx.WXK_PAGEDOWN,
            wx.WXK_DELETE,
            wx.WXK_BACK,
        ):
            return ""
        if wx.WXK_F1 <= key <= wx.WXK_F24:
            return ""
        if key < 32:
            return ""
        try:
            ch = chr(key)
        except Exception:
            return ""
        if not ch.strip():
            return ""
        # Ignore ASCII alnum process keys (e.g. pinyin composition: h a o d e).
        if ch.isascii() and ch.isalnum():
            return ""
        return ch

    def _jump_to_unread_chat(self):
        self._restore_or_raise()
        candidates = list(getattr(self, "history_ids", []) or [])
        current = self._visible_answer_owner_chat_id()
        if current in candidates:
            candidates.remove(current)
            candidates.insert(0, current)
        for owner in candidates:
            chosen = self._chat_read_state(owner)
            if not chosen or chosen["latest_readable_seq"] <= chosen["read_seq"]:
                continue
            # _show_history_chat hydrates real turns; the renderer limits older
            # rows at the head, so its last row is always the actual tail.
            if not self._show_history_chat(owner, focus_answer_list=False):
                continue
            current_state = self._chat_read_state(owner)
            if (not current_state or current_state["generation"] != chosen["generation"]
                    or current_state["read_seq"] >= chosen["latest_readable_seq"]):
                continue
            self._apply_detail_panel_mode("answers")
            count = self.answer_list.GetCount()
            if count <= 0:
                self.SetStatusText("未读聊天定位失败：尚无实际尾页")
                return
            self.answer_list.SetSelection(count - 1)
            self.answer_list.SetFocus()
            if bindings.app.os.name == "nt":
                user32 = ctypes.windll.user32
                user32.SetForegroundWindow(ctypes.c_void_p(self.GetHandle()))
                user32.GetForegroundWindow.restype = ctypes.c_void_p
                if user32.GetForegroundWindow() != self.GetHandle() or not self.answer_list.HasFocus():
                    self.SetStatusText("未读聊天定位失败：未获得前台或回答列表焦点")
                    return
            targets = [self._selected_read_target(index) for index in range(count)]
            readable = [target for target in targets if target and target["generation"] == chosen["generation"]
                        and 0 < target.get("answer_seq", 0) <= chosen["latest_readable_seq"]]
            if readable:
                self._confirm_answer_read(max(readable, key=lambda target: target["answer_seq"]))
                return
            self.SetStatusText("未读聊天定位失败：尾页没有可读回答")
            return
        self._speak_text_via_screen_reader("无未读聊天")

    def _handle_primary_tab_navigation(self, event) -> bool:
        key = event.GetKeyCode()
        if key != wx.WXK_TAB:
            return False
        ctrl_down = getattr(event, "ControlDown", None)
        alt_down = getattr(event, "AltDown", None)
        shift_down = getattr(event, "ShiftDown", None)
        if callable(ctrl_down) and ctrl_down():
            return False
        if callable(alt_down) and alt_down():
            return False
        backwards = bool(shift_down()) if callable(shift_down) else False

        focus = wx.Window.FindFocus()
        detail_target = self._current_detail_tab_target()
        detail_controls = {ctrl for ctrl in (getattr(self, "answer_list", None), getattr(self, "execution_list", None)) if ctrl is not None}
        if backwards:
            if focus is self.input_edit:
                if detail_target is not None:
                    detail_target.SetFocus()
                    if detail_target is getattr(self, "answer_list", None):
                        self._confirm_answer_read(self._selected_read_target())
                return True
            if focus is detail_target or focus in detail_controls:
                self._focus_history_list()
                return True
            if focus is self.history_list:
                self._notes_primary_tab_target().SetFocus()
                return True
            return False

        if focus in {self.notes_notebook_list, self.notes_entry_list}:
            self._focus_history_list()
            return True
        if focus is self.history_list:
            if detail_target is not None:
                detail_target.SetFocus()
                if detail_target is getattr(self, "answer_list", None):
                    self._confirm_answer_read(self._selected_read_target())
            skip = getattr(event, "Skip", None)
            if callable(skip):
                skip()
            return True
        if focus is detail_target or focus in detail_controls:
            self.input_edit.SetFocus()
            if focus is getattr(self, "answer_list", None):
                skip = getattr(event, "Skip", None)
                if callable(skip):
                    skip()
            return True
        return False

    def _handle_named_new_chat_shortcut(self, event) -> bool:
        key = event.GetKeyCode()
        if key not in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            return False
        if not hasattr(self, "new_chat_button"):
            return False
        event_object = getattr(event, "GetEventObject", lambda: None)()
        has_new_chat_focus = False
        try:
            has_new_chat_focus = bool(self.new_chat_button.HasFocus())
        except Exception:
            has_new_chat_focus = False
        if event_object is not self.new_chat_button and not has_new_chat_focus:
            return False
        shift_down = getattr(event, "ShiftDown", None)
        ctrl_down = getattr(event, "ControlDown", None)
        alt_down = getattr(event, "AltDown", None)
        if not (callable(shift_down) and shift_down()):
            return False
        if callable(ctrl_down) and ctrl_down():
            return False
        if callable(alt_down) and alt_down():
            return False
        self._prompt_and_create_named_chat()
        return True

    def _on_generic_key_down(self, event):
        if self._on_any_key_down_escape_minimize(event):
            return
        if self._handle_input_focus_space_shortcut(event):
            return
        self._touch_navigation_quiet_window(event)
        if self._handle_named_new_chat_shortcut(event):
            return
        if self._handle_ctrl_history_navigation(event):
            return
        if self._handle_primary_tab_navigation(event):
            return
        event.Skip()

    def _listbox_strings(self, control) -> list[str]:
        try:
            return [control.GetString(i) for i in range(control.GetCount())]
        except Exception:
            return []

    def _replace_listbox_items_if_changed(self, control, labels: list[str], selected_idx: int | None = None) -> bool:
        normalized = [str(label or "") for label in labels]
        cache = getattr(self, "_listbox_label_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._listbox_label_cache = cache
        cache_key = id(control)
        normalized_tuple = tuple(normalized)
        cached_labels = cache.get(cache_key)
        current_matches = cached_labels == normalized_tuple
        if not current_matches and cached_labels is None:
            current_matches = self._listbox_strings(control) == normalized
        if current_matches:
            if selected_idx is not None:
                try:
                    if control.GetSelection() != selected_idx:
                        control.SetSelection(selected_idx)
                except Exception:
                    pass
            cache[cache_key] = normalized_tuple
            return False
        current_labels = self._listbox_strings(control)
        empty_answer_labels = {"暂无", "暂无对话内容"}
        answer_list = getattr(self, "answer_list", None)
        is_answer_list = control is answer_list
        if not is_answer_list:
            try:
                is_answer_list = bool(control == answer_list)
            except Exception:
                is_answer_list = False
        if (
            is_answer_list
            and current_labels
            and any(label in empty_answer_labels for label in current_labels)
            and not any(label in empty_answer_labels for label in normalized)
        ):
            try:
                for idx in range(len(current_labels)):
                    control.Delete(0)
                for label in normalized:
                    control.Append(label)
                if normalized and selected_idx is not None:
                    control.SetSelection(max(0, min(int(selected_idx), len(normalized) - 1)))
                cache[cache_key] = normalized_tuple
                return True
            except Exception:
                control.Clear()
                for label in normalized:
                    control.Append(label)
                cache[cache_key] = normalized_tuple
                return True
        if (
            is_answer_list
            and current_labels == ["暂无", "暂无对话内容"]
            and normalized[:1] == ["暂无"]
            and "暂无对话内容" not in normalized
        ):
            try:
                control.Delete(1)
                for label in normalized[1:]:
                    control.Append(label)
                if normalized and selected_idx is not None:
                    control.SetSelection(max(0, min(int(selected_idx), len(normalized) - 1)))
                cache[cache_key] = normalized_tuple
                return True
            except Exception:
                control.Clear()
                for label in normalized:
                    control.Append(label)
                cache[cache_key] = normalized_tuple
                return True
        common = min(len(current_labels), len(normalized))
        for idx in range(common):
            if current_labels[idx] == normalized[idx]:
                continue
            try:
                control.SetString(idx, normalized[idx])
            except Exception:
                try:
                    control.Delete(idx)
                    control.Insert(normalized[idx], idx)
                except Exception:
                    control.Clear()
                    for label in normalized:
                        control.Append(label)
                    break
        else:
            if len(current_labels) > len(normalized):
                for idx in range(len(current_labels) - 1, len(normalized) - 1, -1):
                    try:
                        control.Delete(idx)
                    except Exception:
                        control.Clear()
                        for label in normalized:
                            control.Append(label)
                        break
            elif len(normalized) > len(current_labels):
                for label in normalized[len(current_labels):]:
                    control.Append(label)
        if normalized and selected_idx is not None:
            try:
                control.SetSelection(max(0, min(int(selected_idx), len(normalized) - 1)))
            except Exception:
                pass
        cache[cache_key] = normalized_tuple
        return True

    def _set_clipboard_text(self, text: str, *, flush: bool = False) -> bool:
        payload = str(text or "")
        if not payload:
            return False
        if wx.TheClipboard.Open():
            try:
                if not wx.TheClipboard.SetData(wx.TextDataObject(payload)):
                    return False
                if flush:
                    # Only the export-and-delete path pins the clipboard: those
                    # entries are gone once the export succeeds, so a flush
                    # failure must surface instead of silently losing them.
                    # Regular copies keep the lazy-render privacy semantics.
                    if not wx.TheClipboard.Flush():
                        return False
                return True
            finally:
                wx.TheClipboard.Close()
        return False

    def _confirm(self, message: str, title: str = "确认") -> bool:
        # 默认按钮使用“是”，避免键盘焦点默认落在“否”。
        dlg = wx.MessageDialog(self, message, title, wx.YES_NO | wx.ICON_QUESTION)
        ret = dlg.ShowModal() == wx.ID_YES
        dlg.Destroy()
        return ret
