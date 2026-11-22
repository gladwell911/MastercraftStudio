"""Native desktop dialogs and answer viewer payloads."""

import wx

import mc_frame_bindings as bindings
from codex_client import is_codex_model
from dataclasses import dataclass
from kimi_server_client import is_kimi_model
from listbox_model import IncrementalListBoxModel
from mc_frame_presentation import answer_detail_plain_text
from realtime_call import DEFAULT_REALTIME_CALL_ROLE, RealtimeCallSettings
from typing import Callable


class RenameDialog(wx.Dialog):
    def __init__(self, parent: wx.Window, initial_text: str):
        super().__init__(parent, title="重命名聊天", size=(420, 180))
        panel = wx.Panel(self)
        root = wx.BoxSizer(wx.VERTICAL)
        root.Add(wx.StaticText(panel, label="请输入新的聊天标题："), 0, wx.ALL, 10)
        self.input = wx.TextCtrl(panel, value=initial_text)
        root.Add(self.input, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        btn_row.AddStretchSpacer(1)
        btn_ok = wx.Button(panel, wx.ID_OK, "确定")
        btn_cancel = wx.Button(panel, wx.ID_CANCEL, "取消")
        btn_row.Add(btn_ok, 0, wx.RIGHT, 8)
        btn_row.Add(btn_cancel, 0)
        root.Add(btn_row, 0, wx.EXPAND | wx.ALL, 10)
        panel.SetSizer(root)
        btn_ok.SetDefault()

    def get_value(self) -> str:
        return self.input.GetValue().strip()


class RealtimeCallSettingsDialog(wx.Dialog):
    def __init__(self, parent: wx.Window, role: str, speech_rate: int):
        super().__init__(parent, title="语音通话设置", size=(520, 360))
        panel = wx.Panel(self)
        root = wx.BoxSizer(wx.VERTICAL)

        root.Add(wx.StaticText(panel, label="角色设定："), 0, wx.ALL, 10)
        self.role_edit = wx.TextCtrl(panel, value=role, style=wx.TE_MULTILINE, size=(-1, 170))
        self.role_edit.SetHint("输入实时语音通话的角色设定")
        root.Add(self.role_edit, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        speed_row = wx.BoxSizer(wx.HORIZONTAL)
        speed_row.Add(wx.StaticText(panel, label="语速："), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        self.speed_input = wx.SpinCtrl(panel, min=-50, max=100, initial=int(min(max(int(speech_rate), -50), 100)))
        speed_row.Add(self.speed_input, 0, wx.RIGHT, 12)
        speed_row.Add(wx.StaticText(panel, label="范围 -50 到 100，数值越大语速越快"), 0, wx.ALIGN_CENTER_VERTICAL)
        root.Add(speed_row, 0, wx.EXPAND | wx.ALL, 10)

        root.Add(wx.StaticText(panel, label="全局快捷键：Ctrl+\\ 开始或结束实时语音通话"), 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        btn_row.AddStretchSpacer(1)
        btn_ok = wx.Button(panel, wx.ID_OK, "确定")
        btn_cancel = wx.Button(panel, wx.ID_CANCEL, "取消")
        btn_row.Add(btn_ok, 0, wx.RIGHT, 8)
        btn_row.Add(btn_cancel, 0)
        root.Add(btn_row, 0, wx.EXPAND | wx.ALL, 10)

        panel.SetSizer(root)
        btn_ok.SetDefault()
        self.role_edit.SetFocus()

    def get_settings(self) -> RealtimeCallSettings:
        return RealtimeCallSettings(
            role=self.role_edit.GetValue().strip() or DEFAULT_REALTIME_CALL_ROLE,
            speech_rate=self.speed_input.GetValue(),
        ).normalized()


@dataclass(frozen=True)
class AnswerViewerPayload:
    chat_id: str
    turn_idx: int
    turn_id: str
    provider_session_id: str
    model: str
    answer_md: str


class AnswerTextViewerDialog(wx.Dialog):
    def __init__(
        self,
        parent: wx.Window,
        title: str,
        text: str = "",
        *,
        payload: AnswerViewerPayload | None = None,
        on_continue: Callable[[AnswerViewerPayload], bool] | None = None,
    ):
        super().__init__(
            parent,
            title=str(title or "文本查看"),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER | wx.MAXIMIZE_BOX,
        )
        panel = wx.Panel(self)
        root = wx.BoxSizer(wx.VERTICAL)
        self.payload = payload
        self.canonical_text = payload.answer_md if payload is not None else str(text or "")
        self._continue_callback = on_continue
        self._continue_requested = False
        self._closing = False
        self._display_prefix = "\n"
        display_text = self._display_prefix + self.canonical_text.lstrip("\r\n")
        self._display_marker_offset: int | None = 0
        self._scratch_previous_text = display_text
        self._scratch_non_edit_key_pending = False
        self.text_ctrl = wx.TextCtrl(
            panel,
            value=display_text,
            style=wx.TE_MULTILINE | wx.TE_RICH2 | wx.TE_DONTWRAP | wx.HSCROLL,
        )
        self.text_ctrl.SetName("文本内容")
        root.Add(self.text_ctrl, 1, wx.EXPAND | wx.ALL, 10)

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        btn_row.AddStretchSpacer(1)
        self.copy_button = wx.Button(panel, label="复制")
        self.close_button = wx.Button(panel, wx.ID_CLOSE, "关闭")
        self.continue_button = wx.Button(panel, label="继续")
        btn_row.Add(self.copy_button, 0, wx.RIGHT, 8)
        btn_row.Add(self.close_button, 0, wx.RIGHT, 8)
        btn_row.Add(self.continue_button, 0)
        root.Add(btn_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        panel.SetSizer(root)
        self.copy_button.MoveAfterInTabOrder(self.text_ctrl)
        self.close_button.MoveAfterInTabOrder(self.copy_button)
        self.continue_button.MoveAfterInTabOrder(self.close_button)
        self.copy_button.Bind(wx.EVT_BUTTON, self._on_copy_clicked)
        self.text_ctrl.Bind(wx.EVT_TEXT, self._on_scratch_text_changed)
        self.text_ctrl.Bind(wx.EVT_KEY_DOWN, self._on_scratch_key_down)
        self.close_button.Bind(wx.EVT_BUTTON, self._on_close)
        self.continue_button.Bind(wx.EVT_BUTTON, self._on_continue_clicked)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)
        self.SetMinSize((720, 480))
        self.SetSize(parent.GetSize() if parent is not None else (1024, 768))
        try:
            self.Maximize(True)
        except Exception:
            pass
        self.text_ctrl.SetFocus()
        self.continue_button.Enable(payload is not None and callable(on_continue))

    def _set_answer_display_text(self) -> None:
        display_text = self._display_prefix + answer_detail_plain_text(self.canonical_text, ordinary=True).lstrip("\r\n")
        self.text_ctrl.ChangeValue(display_text)
        self._display_marker_offset = 0
        self._scratch_previous_text = display_text
        self._scratch_non_edit_key_pending = False

    def _on_close(self, _event=None):
        self._closing = True
        self._finish(wx.ID_CLOSE)

    def _on_scratch_text_changed(self, event=None):
        old_text = self._scratch_previous_text
        new_text = self.text_ctrl.GetValue()
        marker = self._display_marker_offset
        if marker is not None and old_text == new_text:
            # EVT_TEXT can represent an equal-byte replacement. Without an
            # authoritative edit delta, the newline can no longer be proven
            # to be the viewer-owned character.
            if not self._scratch_non_edit_key_pending:
                marker = None
        elif marker is not None:
            prefix = 0
            common_limit = min(len(old_text), len(new_text))
            while prefix < common_limit and old_text[prefix] == new_text[prefix]:
                prefix += 1
            suffix = 0
            old_remaining = len(old_text) - prefix
            new_remaining = len(new_text) - prefix
            while (
                suffix < old_remaining
                and suffix < new_remaining
                and old_text[len(old_text) - suffix - 1] == new_text[len(new_text) - suffix - 1]
            ):
                suffix += 1
            old_end = len(old_text) - suffix
            inserted_length = len(new_text) - prefix - suffix
            if prefix <= marker < old_end:
                marker = None
            elif old_end <= marker:
                marker += inserted_length - (old_end - prefix)
        self._display_marker_offset = marker
        self._scratch_previous_text = new_text
        self._scratch_non_edit_key_pending = False
        if event is not None:
            event.Skip()

    def _scratch_copy_text(self) -> str:
        start, end = self.text_ctrl.GetSelection()
        has_selection = start != end
        if not has_selection:
            start, end = 0, self.text_ctrl.GetLastPosition()
        copied_text = self.text_ctrl.GetRange(start, end)
        marker = self._display_marker_offset
        if marker is not None and start <= marker < end:
            relative_marker = marker - start
            copied_text = copied_text[:relative_marker] + copied_text[relative_marker + 1:]
        return copied_text

    def _copy_scratch_to_clipboard(self) -> bool:
        copied_text = self._scratch_copy_text()
        setter = getattr(self.GetParent(), "_set_clipboard_text", None)
        try:
            accepted = bool(callable(setter) and setter(copied_text))
        except Exception:
            accepted = False
        if accepted:
            status = getattr(self.GetParent(), "SetStatusText", None)
            if callable(status):
                status("已复制")
        return accepted

    def _on_copy_clicked(self, _event=None):
        self._copy_scratch_to_clipboard()

    def _on_scratch_key_down(self, event):
        key_code = event.GetKeyCode()
        ctrl_down = event.ControlDown()
        alt_down = event.AltDown()
        if ctrl_down and not alt_down and key_code in (ord("C"), ord("c")):
            self._copy_scratch_to_clipboard()
            return
        marker = self._display_marker_offset
        if marker is not None:
            start, end = self.text_ctrl.GetSelection()
            replaces_selection = start <= marker < end
            unicode_key = getattr(event, "GetUnicodeKey", lambda: wx.WXK_NONE)()
            non_text_keys = {
                wx.WXK_LEFT, wx.WXK_RIGHT, wx.WXK_UP, wx.WXK_DOWN,
                wx.WXK_HOME, wx.WXK_END, wx.WXK_PAGEUP, wx.WXK_PAGEDOWN,
                wx.WXK_NUMPAD_LEFT, wx.WXK_NUMPAD_RIGHT,
                wx.WXK_NUMPAD_UP, wx.WXK_NUMPAD_DOWN,
                wx.WXK_NUMPAD_HOME, wx.WXK_NUMPAD_END,
                wx.WXK_NUMPAD_PAGEUP, wx.WXK_NUMPAD_PAGEDOWN,
                wx.WXK_TAB, wx.WXK_ESCAPE,
            }
            is_function_key = wx.WXK_F1 <= key_code <= wx.WXK_F24
            produces_text = (
                not ctrl_down
                and not alt_down
                and key_code not in non_text_keys
                and not is_function_key
                and unicode_key != wx.WXK_NONE
                and unicode_key >= 32
            )
            explicit_mutation = (
                (ctrl_down and not alt_down and key_code in (ord("V"), ord("v"), ord("X"), ord("x")))
                or key_code in (wx.WXK_BACK, wx.WXK_DELETE)
            )
            edit_hits_marker = replaces_selection and (
                explicit_mutation
                or produces_text
            )
            if start == end:
                edit_hits_marker = edit_hits_marker or key_code == wx.WXK_DELETE and start == marker
                edit_hits_marker = edit_hits_marker or key_code == wx.WXK_BACK and start == marker + 1
            if edit_hits_marker:
                self._display_marker_offset = None
            elif not produces_text and not explicit_mutation:
                self._scratch_non_edit_key_pending = True
                wx.CallAfter(setattr, self, "_scratch_non_edit_key_pending", False)
        event.Skip()

    def _on_continue_clicked(self, _event=None):
        self._request_continue()

    def _request_continue(self, *, from_shortcut: bool = False, event=None) -> bool:
        if self._continue_requested or self.payload is None or not callable(self._continue_callback):
            return False
        if self._closing or not self.continue_button.IsEnabled() or not self.IsShown() or self.IsBeingDeleted():
            return False
        if from_shortcut:
            try:
                if wx.GetTopLevelParent(wx.Window.FindFocus()) is not self or wx.GetActiveWindow() is not self:
                    return False
            except Exception:
                return False
            if event is not None and bool(getattr(event, "IsAutoRepeat", lambda: False)()):
                return False
            ime_check = getattr(self.GetParent(), "_has_native_ime_composition", None)
            if callable(ime_check) and ime_check(wx.Window.FindFocus()):
                return False
        # Acquire the latch before the callback can close the modal or post work.
        self._continue_requested = True
        self.continue_button.Disable()
        try:
            accepted = bool(self._continue_callback(self.payload))
        except Exception:
            accepted = False
        if not accepted:
            self._continue_requested = False
            if not self.IsBeingDeleted():
                self.continue_button.Enable()
            return False
        self._closing = True
        self._finish(wx.ID_OK)
        return True

    def _finish(self, code: int) -> None:
        if self.IsModal() or bool(getattr(self, "_shown_modally", False)):
            try:
                self.EndModal(code)
                return
            except Exception:
                pass
        self.Close()

    def _on_char_hook(self, event):
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self._on_close()
            return
        alt_down = bool(getattr(event, "AltDown", lambda: False)())
        ctrl_down = bool(getattr(event, "ControlDown", lambda: False)())
        if alt_down and not ctrl_down and event.GetKeyCode() in (ord("C"), ord("c")):
            if self._request_continue(from_shortcut=True, event=event):
                return
        event.Skip()


class CodexUserInputDialog(wx.Dialog):
    def __init__(self, parent: wx.Window, questions: list[dict], *, kimi_question: bool = False):
        super().__init__(parent, title="Kimi Code 提问" if kimi_question else "Codex 输入", size=(620, 420))
        self._controls: list[dict] = []
        panel = wx.Panel(self)
        root = wx.BoxSizer(wx.VERTICAL)
        scroll = wx.BoxSizer(wx.VERTICAL)
        question_parent = panel
        if kimi_question:
            question_parent = wx.ScrolledWindow(panel, style=wx.TAB_TRAVERSAL | wx.HSCROLL | wx.VSCROLL)
            question_parent.SetScrollRate(16, 16)

        for question in questions or []:
            if not isinstance(question, dict):
                continue
            block = wx.StaticBoxSizer(wx.StaticBox(question_parent, label=str(question.get("header") or "问题")), wx.VERTICAL)
            prompt = wx.StaticText(question_parent, label=str(question.get("question") or ""))
            if kimi_question:
                prompt.Wrap(540)
            block.Add(prompt, 0, wx.ALL, 6)
            if kimi_question:
                self._add_kimi_question_controls(question_parent, block, question)
                scroll.Add(block, 0, wx.EXPAND | wx.ALL, 8)
                continue
            options = question.get("options") if isinstance(question.get("options"), list) else []
            option_labels: list[str] = []
            option_values: list[str] = []
            for opt in options:
                if not isinstance(opt, dict):
                    continue
                label = str(opt.get("label") or "").strip()
                value = str(opt.get("value") or label).strip()
                if label:
                    option_labels.append(label)
                    option_values.append(value or label)
            if not option_labels:
                option_labels = [""]
                option_values = [""]
            radio = wx.RadioBox(panel, choices=option_labels, majorDimension=1, style=wx.RA_SPECIFY_ROWS)
            block.Add(radio, 0, wx.EXPAND | wx.ALL, 6)
            self._controls.append(
                {
                    "id": str(question.get("id") or ""),
                    "radio": radio,
                    "values": option_values,
                }
            )
            scroll.Add(block, 0, wx.EXPAND | wx.ALL, 8)

        if kimi_question:
            question_parent.SetSizer(scroll)
            root.Add(question_parent, 1, wx.EXPAND | wx.ALL, 8)
        else:
            root.Add(scroll, 1, wx.EXPAND | wx.ALL, 8)
        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        btn_row.AddStretchSpacer(1)
        btn_ok = wx.Button(panel, wx.ID_OK, "确定")
        btn_cancel = wx.Button(panel, wx.ID_CANCEL, "取消")
        btn_row.Add(btn_ok, 0, wx.RIGHT, 8)
        btn_row.Add(btn_cancel, 0)
        root.Add(btn_row, 0, wx.EXPAND | wx.ALL, 10)
        panel.SetSizer(root)
        btn_ok.SetDefault()

    def _add_kimi_question_controls(self, parent, block, question):
        question_text = str(question.get("question") or "")
        if question.get("body"):
            body = wx.StaticText(parent, label=str(question["body"]))
            body.Wrap(540)
            block.Add(body, 0, wx.ALL, 6)
        labels, values = [], []
        for option in question.get("options") or []:
            if isinstance(option, dict) and option.get("id") and option.get("label"):
                description = str(option.get("description") or "").strip()
                label = str(option["label"])
                labels.append(f"{label}，{description}" if description else label)
                values.append(str(option["id"]))
        multi = bool(question.get("multi_select"))
        allow_other = bool(question.get("allow_other"))
        other_label = str(question.get("other_label") or "其他")
        if allow_other and not multi and labels:
            labels.append(other_label)
            values.append(None)
        choices = None
        if labels and multi:
            choices = wx.CheckListBox(parent, choices=labels, size=(-1, min(130, 26 * len(labels) + 8)))
            choices.SetName(question_text)
        elif labels:
            choices = wx.RadioBox(parent, label=question_text, choices=labels, majorDimension=1,
                                  style=wx.RA_SPECIFY_COLS)
        if choices:
            block.Add(choices, 0, wx.EXPAND | wx.ALL, 6)
        other = None
        if allow_other:
            other_description = str(question.get("other_description") or "")
            other_caption = f"{other_label}，{other_description}" if other_description else other_label
            block.Add(wx.StaticText(parent, label=other_caption), 0, wx.LEFT | wx.RIGHT, 6)
            other = wx.TextCtrl(parent, name=f"{question_text}：{other_label}")
            block.Add(other, 0, wx.EXPAND | wx.ALL, 6)
            if choices and not multi:
                other_index = len(values) - 1

                def select_other(event):
                    if other.GetValue():
                        choices.SetSelection(other_index)
                    event.Skip()

                other.Bind(wx.EVT_TEXT, select_other)
        self._controls.append({"id": question.get("id"), "choices": choices, "values": values,
                               "multi": multi, "other": other})

    def get_kimi_answers(self) -> dict[str, dict]:
        answers = {}
        for control in self._controls:
            qid, choices, values = control["id"], control["choices"], control["values"]
            other = control["other"].GetValue().strip() if control["other"] else ""
            if control["multi"]:
                selected = [values[idx] for idx in choices.GetCheckedItems()] if choices else []
                if other:
                    answers[qid] = {"kind": "multi_with_other", "option_ids": selected, "other_text": other}
                elif selected:
                    answers[qid] = {"kind": "multi", "option_ids": selected}
                else:
                    answers[qid] = {"kind": "skipped"}
            else:
                selected = values[choices.GetSelection()] if choices and choices.GetSelection() >= 0 else None
                if selected:
                    answers[qid] = {"kind": "single", "option_id": selected}
                elif other:
                    answers[qid] = {"kind": "other", "text": other}
                else:
                    answers[qid] = {"kind": "skipped"}
        return answers

    def get_answers(self) -> dict[str, list[str]]:
        answers: dict[str, list[str]] = {}
        for control in self._controls:
            qid = str(control.get("id") or "").strip()
            radio = control.get("radio")
            values = control.get("values") if isinstance(control.get("values"), list) else []
            if not qid or radio is None:
                continue
            idx = int(radio.GetSelection())
            if idx < 0:
                continue
            if idx < len(values):
                answers[qid] = [str(values[idx] or "").strip()]
            else:
                answers[qid] = [str(radio.GetStringSelection() or "").strip()]
        return answers


class CommonCommandEditDialog(wx.Dialog):
    def __init__(self, parent, *, dialog_title: str, initial_title: str = "", initial_content: str = "", on_save=None):
        super().__init__(parent, title=dialog_title, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self._initial_title = str(initial_title or "")
        self._initial_content = str(initial_content or "")
        self._save_callback = on_save
        self._save_in_progress = False
        self._closing = False
        panel = wx.Panel(self)
        root = wx.BoxSizer(wx.VERTICAL)
        root.Add(wx.StaticText(panel, label="标题："), 0, wx.LEFT | wx.TOP | wx.RIGHT, 10)
        self.title_edit = wx.TextCtrl(panel, value=self._initial_title)
        self.title_edit.SetName("常用命令标题")
        root.Add(self.title_edit, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        root.Add(wx.StaticText(panel, label="内容："), 0, wx.LEFT | wx.RIGHT, 10)
        self.content_edit = wx.TextCtrl(panel, value=self._initial_content, style=wx.TE_MULTILINE)
        self.content_edit.SetName("常用命令内容")
        root.Add(self.content_edit, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        button_row = wx.BoxSizer(wx.HORIZONTAL)
        button_row.AddStretchSpacer(1)
        self.ok_button = wx.Button(panel, wx.ID_OK, "保存")
        self.cancel_button = wx.Button(panel, wx.ID_CANCEL, "取消")
        button_row.Add(self.ok_button, 0, wx.RIGHT, 8)
        button_row.Add(self.cancel_button, 0)
        root.Add(button_row, 0, wx.EXPAND | wx.ALL, 10)
        panel.SetSizer(root)
        self.SetMinSize((420, 320))
        self.SetSize((520, 420))
        self.ok_button.SetDefault()
        self.ok_button.Bind(wx.EVT_BUTTON, self._on_save)
        self.cancel_button.Bind(wx.EVT_BUTTON, self._on_cancel)
        self.Bind(wx.EVT_CLOSE, self._on_close)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)

    def _has_ime_composition(self) -> bool:
        focus = wx.Window.FindFocus()
        if focus not in (self.title_edit, self.content_edit):
            return False
        checker = getattr(self.GetParent(), "_has_native_ime_composition", None)
        return bool(callable(checker) and checker(focus))

    def _is_dirty(self) -> bool:
        return (str(self.title_edit.GetValue() or "") != self._initial_title
                or str(self.content_edit.GetValue() or "") != self._initial_content)

    def _request_cancel(self) -> bool:
        if self._closing:
            return False
        if self._is_dirty():
            answer = wx.MessageBox("Discard changes?", "Confirm discard",
                                   wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, parent=self)
            if answer != wx.YES:
                return False
        self._closing = True
        try:
            self.EndModal(wx.ID_CANCEL)
        except Exception:
            self._closing = False
            return False
        return True

    def _on_cancel(self, _event=None) -> None:
        self._request_cancel()

    def _on_close(self, event) -> None:
        if not self._request_cancel():
            veto = getattr(event, "Veto", None)
            if callable(veto):
                veto()
            else:
                skip = getattr(event, "Skip", None)
                if callable(skip):
                    skip(False)

    def _on_char_hook(self, event) -> None:
        key = event.GetKeyCode()
        ctrl = bool(getattr(event, "ControlDown", lambda: False)())
        alt = bool(getattr(event, "AltDown", lambda: False)())
        focus = wx.Window.FindFocus()
        if self._has_ime_composition():
            event.Skip()
            return
        if key == wx.WXK_ESCAPE and not ctrl and not alt:
            self._request_cancel()
            return
        if bool(getattr(event, "IsAutoRepeat", lambda: False)()) and (
            (ctrl and key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER))
            or (alt and not ctrl and key in (ord("S"), ord("s")))
        ):
            return
        if alt and not ctrl and key in (ord("S"), ord("s")):
            self._on_save(event)
            return
        if key not in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            event.Skip()
            return
        if bool(getattr(event, "IsAutoRepeat", lambda: False)()):
            return
        if focus is self.title_edit and not ctrl and not alt:
            self.content_edit.SetFocus()
            return
        if focus is self.content_edit:
            if ctrl and not alt:
                self._on_save(event)
                return
            event.Skip()
            return
        event.Skip()

    def _on_save(self, event=None) -> None:
        if self._save_in_progress or self._closing:
            return
        if not str(self.content_edit.GetValue() or "").strip():
            wx.MessageBox("请输入命令内容。", "提示", wx.OK | wx.ICON_WARNING)
            self.content_edit.SetFocus()
            return
        self._save_in_progress = True
        try:
            if callable(self._save_callback) and not self._save_callback(*self.values()):
                return
            self._closing = True
            self.EndModal(wx.ID_OK)
        except Exception as exc:
            self._closing = False
            wx.MessageBox(str(exc), "Error", wx.OK | wx.ICON_WARNING, parent=self)
        finally:
            if not self._closing:
                self._save_in_progress = False

    def values(self) -> tuple[str, str]:
        return (
            str(self.title_edit.GetValue() or "").strip(),
            str(self.content_edit.GetValue() or "").strip(),
        )


class ChatInformationDialog(wx.Dialog):
    def __init__(self, owner: "bindings.app.ChatFrame", identity: tuple[str, str, str, str], focus_target):
        super().__init__(owner, title="聊天信息", style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.owner = owner
        self.identity = identity
        self.focus_target = focus_target
        panel = wx.Panel(self)
        layout = wx.BoxSizer(wx.VERTICAL)
        self.information_list = wx.ListBox(panel, style=wx.LB_SINGLE)
        self.information_list.SetName("聊天信息")
        layout.Add(self.information_list, 1, wx.EXPAND | wx.ALL, 10)
        panel.SetSizer(layout)
        self.SetSize((480, 240))
        self.information_list.Bind(wx.EVT_KEY_DOWN, self._on_list_key_down)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)
        self.Bind(wx.EVT_CLOSE, self._on_close)
        self.refresh_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_refresh_timer, self.refresh_timer)
        self.refresh_timer.Start(60000)
        self.context_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_context_timer, self.context_timer)
        self.context_timer.Start(10000)

    def _on_context_timer(self, _event) -> None:
        if self.owner._chat_information_dialog is not self:
            return
        current = self.owner._visible_chat_information_owner()
        if current is None or self.owner._chat_information_identity(*current) != self.identity:
            return
        chat, model = current
        if is_codex_model(model):
            self.owner._request_codex_chat_information(chat, model, context_only=True)
        elif is_kimi_model(model):
            self.owner._request_kimi_chat_information(chat, model, visible_only=True, context_only=True)

    def _on_refresh_timer(self, _event) -> None:
        current = self.owner._visible_chat_information_owner()
        if current is None:
            return
        chat, model = current
        new_identity = self.owner._chat_information_identity(chat, model)
        if new_identity[:3] != self.identity[:3]:
            return
        if new_identity != self.identity:
            self.identity = new_identity
        if is_codex_model(model):
            self.owner._request_codex_chat_information(chat, model)
        elif is_kimi_model(model):
            self.owner._refresh_chat_information(chat)
            if self.identity[2]:
                self.owner._request_kimi_chat_information(chat, model, visible_only=True)
            self.owner._request_kimi_quota(chat, model)

    def set_rows(self, rows: list[str]) -> None:
        if list(self.information_list.GetStrings()) == rows:
            return
        selected = self.information_list.GetSelection()
        self.information_list.Set(rows)
        if rows:
            self.information_list.SetSelection(min(max(selected, 0), len(rows) - 1))

    def _on_list_key_down(self, event) -> None:
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.Close()
            return
        event.Skip()

    def _on_char_hook(self, event) -> None:
        if event.GetKeyCode() == wx.WXK_ESCAPE:
            self.Close()
            return
        event.Skip()

    def _on_close(self, _event) -> None:
        self.refresh_timer.Stop()
        self.context_timer.Stop()
        self.owner._chat_information_request = None
        self.owner._codex_information_full_refresh = None
        self.owner._kimi_quota_request = None
        request = self.owner._kimi_information_requests.get(self.identity[0])
        if isinstance(request, dict) and request.get("visible_only"):
            self.owner._kimi_information_requests.pop(self.identity[0], None)
        self.owner._chat_information_dialog = None
        target = self.focus_target
        self.Destroy()
        if target is not None:
            try:
                if not target.IsBeingDeleted() and target.IsShown():
                    wx.CallAfter(target.SetFocus)
            except Exception:
                pass


class CommonCommandsDialog(wx.Dialog):
    def __init__(self, owner: "bindings.app.ChatFrame"):
        super().__init__(owner, title="常用命令", style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.owner = owner
        self.common_commands_list_ids: list[str] = []
        self.common_commands_by_id: dict[str, object] = {}
        panel = wx.Panel(self)
        root = wx.BoxSizer(wx.VERTICAL)
        root.Add(wx.StaticText(panel, label="常用命令："), 0, wx.LEFT | wx.TOP | wx.RIGHT, 10)
        self.common_commands_list = wx.ListBox(panel, style=wx.LB_SINGLE)
        self.common_commands_list.SetName("常用命令")
        root.Add(self.common_commands_list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        button_row = wx.BoxSizer(wx.HORIZONTAL)
        self.common_commands_add_button = wx.Button(panel, label="添加(&A)")
        self.common_commands_add_button.SetName("添加常用命令")
        button_row.Add(self.common_commands_add_button, 0)
        root.Add(button_row, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        panel.SetSizer(root)
        self.common_commands_list_model = IncrementalListBoxModel(self.common_commands_list)
        self.SetMinSize((320, 280))
        self.SetSize((420, 420))
        self.common_commands_add_button.Bind(wx.EVT_BUTTON, lambda _evt: self.owner._add_common_command())
        self.common_commands_list.Bind(wx.EVT_KEY_DOWN, self.owner._monitored_ui_handler("common_commands_key_down", self.owner._on_common_commands_key_down))
        self.common_commands_list.Bind(wx.EVT_CHAR, self.owner._on_common_commands_char)
        self.common_commands_list.Bind(wx.EVT_CONTEXT_MENU, self.owner._on_common_commands_context)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)
        self.Bind(wx.EVT_CLOSE, self._on_close)

    def _on_close(self, _event) -> None:
        self.owner.common_commands_dialog = None
        self.Destroy()
        try:
            self.owner.Raise()
        except Exception:
            pass
        try:
            self.owner._focus_input_box()
        except Exception:
            pass

    def _on_char_hook(self, event) -> None:
        key = event.GetKeyCode()
        if key == wx.WXK_ESCAPE:
            self.Close()
            return
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            try:
                focus = wx.Window.FindFocus()
            except Exception:
                focus = None
            if focus is self.common_commands_list and self.owner._send_selected_common_command():
                return
        event.Skip()

    def selected_command_id(self) -> str:
        model = getattr(self, "common_commands_list_model", None)
        if model is None:
            return ""
        return str(model.selected_id() or "")

    def selected_command(self):
        command_id = self.selected_command_id()
        if not command_id:
            return None
        return self.common_commands_by_id.get(command_id)

    def refresh_commands(self, select_id: str | None = None) -> bool:
        snapshot = self.owner.common_commands_store.read_snapshot()
        commands = list(snapshot.commands)
        previous_selection = self.common_commands_list.GetSelection()
        self.common_commands_by_id = {command.id: command for command in commands}
        self.common_commands_list_ids = [command.id for command in commands]
        if not commands:
            self.common_commands_list_model.replace_visible_page([])
            return self.owner._replace_listbox_items_if_changed(self.common_commands_list, ["暂无常用命令"], None)
        target_id = str(select_id or self.selected_command_id() or "")
        if target_id not in self.common_commands_by_id:
            fallback_index = min(
                max(previous_selection, 0),
                len(commands) - 1,
            )
            target_id = commands[fallback_index].id
        rows = [(command.id, self.owner._common_command_label(command)) for command in commands]
        changed = self.common_commands_list_model.replace_visible_page(rows, target_id)
        self.common_commands_list_ids = list(self.common_commands_list_model.visible_ids)
        return changed

    def focus_default_control(self) -> bool:
        if self.common_commands_list_ids:
            return self.owner._focus_control_safely(self.common_commands_list)
        return self.owner._focus_control_safely(self.common_commands_add_button)
