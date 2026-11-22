"""Independent note entry edit window.

The window owns its draft and control lifecycle. Plain Enter (or numpad Enter)
saves and closes; Ctrl+Enter (or Shift/Alt+Enter) inserts a newline. IME
candidate confirmation is consumed by the IME itself on Windows and never
reaches the key handler, so it cannot trigger a save. Escape asks before
discarding unsaved edits. A failed save keeps the window, the draft, and focus
so the user can retry.
"""

from __future__ import annotations

import wx


def should_save_on_enter(keycode: int, *, ctrl: bool, shift: bool, alt: bool) -> bool:
    """Plain Return and numpad Enter save; any modifier falls through to a newline."""
    if keycode not in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
        return False
    return not (ctrl or shift or alt)


class NoteEntryEditWindow(wx.Frame):
    def __init__(self, parent, *, title: str, content: str, on_save):
        super().__init__(parent, title=title, size=(560, 420))
        self._on_save = on_save
        self._saved = False
        self._initial = str(content or "")
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        self.editor = wx.TextCtrl(panel, value=self._initial, style=wx.TE_MULTILINE)
        self.status_label = wx.StaticText(panel, label="回车保存并关闭，Ctrl+回车换行，Esc 放弃关闭")
        sizer.Add(self.editor, 1, wx.EXPAND | wx.ALL, 8)
        sizer.Add(self.status_label, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        panel.SetSizer(sizer)
        self.editor.Bind(wx.EVT_KEY_DOWN, self._on_key_down)
        self.Bind(wx.EVT_CLOSE, self._on_close)
        self.Layout()
        self.editor.SetFocus()

    def _on_key_down(self, event) -> None:
        key = event.GetKeyCode()
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            if should_save_on_enter(
                key,
                ctrl=event.ControlDown(),
                shift=event.ShiftDown(),
                alt=event.AltDown(),
            ):
                self._save()
                return
            event.Skip()
            return
        if key == wx.WXK_ESCAPE:
            self.Close()
            return
        event.Skip()

    def _save(self) -> bool:
        content = str(self.editor.GetValue() or "").strip()
        try:
            ok, error = self._on_save(content)
        except Exception as exc:  # keep draft and window on any store failure
            ok, error = False, str(exc)
        if ok:
            self._saved = True
            self.Close()
            return True
        self.status_label.SetLabel(str(error or "保存失败，可修改后重试"))
        return False

    def _on_close(self, event) -> None:
        if not self._saved and str(self.editor.GetValue() or "") != self._initial:
            # Dirty drafts are never dropped silently: confirm first and keep
            # the window alive when the user cancels.
            dlg = wx.MessageDialog(
                self,
                "内容已修改，关闭将丢弃修改。是否关闭？",
                "确认关闭",
                wx.YES_NO | wx.ICON_QUESTION,
            )
            try:
                confirmed = dlg.ShowModal() == wx.ID_YES
            finally:
                dlg.Destroy()
            if not confirmed:
                if event.CanVeto():
                    event.Veto(True)
                return
        event.Skip()
