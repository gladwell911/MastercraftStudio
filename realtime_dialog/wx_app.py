import wx

import config
from dialog_worker import DialogWorker


class MainFrame(wx.Frame):
    def __init__(self):
        super().__init__(parent=None, title="端到端语音通话", size=(980, 680))
        panel = wx.Panel(self)

        self.worker = DialogWorker(on_event=self._on_worker_event_from_thread)
        self.recording = False
        self._updating_speed_combo = False

        self.btn_mic = wx.ToggleButton(panel, label="开始录音")
        self.btn_upload = wx.Button(panel, label="上传音频文件")
        self.input_text = wx.TextCtrl(panel, style=wx.TE_PROCESS_ENTER)
        self.input_text.SetHint("输入")
        self.btn_send = wx.Button(panel, label="发送")

        self.voice_choices = self._build_voice_choices()
        self.voice_combo = wx.ComboBox(
            panel,
            choices=[item["label"] for item in self.voice_choices],
            style=wx.CB_READONLY,
        )

        self.speed_combo = wx.ComboBox(panel, style=wx.CB_READONLY)
        self.chat_text = wx.TextCtrl(
            panel,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL,
        )

        controls_sizer = wx.BoxSizer(wx.HORIZONTAL)
        controls_sizer.Add(self.btn_mic, 0, wx.ALL, 5)
        controls_sizer.Add(self.btn_upload, 0, wx.ALL, 5)
        controls_sizer.Add(self.input_text, 1, wx.ALL | wx.EXPAND, 5)
        controls_sizer.Add(self.btn_send, 0, wx.ALL, 5)

        tts_sizer = wx.BoxSizer(wx.HORIZONTAL)
        tts_sizer.Add(wx.StaticText(panel, label="音色"), 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT | wx.RIGHT, 5)
        tts_sizer.Add(self.voice_combo, 1, wx.ALL | wx.EXPAND, 5)
        tts_sizer.Add(wx.StaticText(panel, label="语速"), 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT | wx.RIGHT, 5)
        tts_sizer.Add(self.speed_combo, 0, wx.ALL, 5)

        root_sizer = wx.BoxSizer(wx.VERTICAL)
        root_sizer.Add(controls_sizer, 0, wx.EXPAND | wx.ALL, 8)
        root_sizer.Add(tts_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        root_sizer.Add(self.chat_text, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        panel.SetSizer(root_sizer)

        self.CreateStatusBar()
        self.SetStatusText("Initializing...")

        self.btn_mic.Bind(wx.EVT_TOGGLEBUTTON, self.on_toggle_mic)
        self.btn_upload.Bind(wx.EVT_BUTTON, self.on_upload_file)
        self.btn_send.Bind(wx.EVT_BUTTON, self.on_send_text)
        self.input_text.Bind(wx.EVT_TEXT_ENTER, self.on_send_text)
        self.voice_combo.Bind(wx.EVT_COMBOBOX, self.on_voice_changed)
        self.speed_combo.Bind(wx.EVT_COMBOBOX, self.on_speed_changed)
        self.Bind(wx.EVT_CLOSE, self.on_close)

        self._init_voice_and_speed()
        self._set_controls_enabled(False)
        self.worker.start()
        self.worker.connect()

    def _build_voice_choices(self):
        return [
            {
                "id": item["id"],
                "label": f"{item['name']} | {item['id']}",
            }
            for item in config.VOICE_OPTIONS
        ]

    def _init_voice_and_speed(self):
        default_index = 0
        for idx, item in enumerate(self.voice_choices):
            if item["id"] == config.DEFAULT_SPEAKER:
                default_index = idx
                break
        self.voice_combo.SetSelection(default_index)
        self._reload_speed_choices(self.selected_voice_id())
        self._set_speed_value(config.speed_ratio_to_speech_rate(config.DEFAULT_SPEED_RATIO))

    def _reload_speed_choices(self, voice_id: str):
        # Display service speech_rate levels directly: [-50, 100], step 1.
        values = [str(v) for v in range(-50, 101)]
        self._updating_speed_combo = True
        self.speed_combo.SetItems(values)
        self._updating_speed_combo = False

    def _set_speed_value(self, speech_rate: int):
        value = str(int(min(max(int(speech_rate), -50), 100)))
        idx = self.speed_combo.FindString(value)
        if idx == wx.NOT_FOUND and self.speed_combo.GetCount() > 0:
            idx = 0
        if idx != wx.NOT_FOUND:
            self._updating_speed_combo = True
            self.speed_combo.SetSelection(idx)
            self._updating_speed_combo = False

    def selected_voice_id(self) -> str:
        idx = self.voice_combo.GetSelection()
        if idx == wx.NOT_FOUND:
            return config.DEFAULT_SPEAKER
        return self.voice_choices[idx]["id"]

    def selected_speech_rate(self) -> int:
        value = self.speed_combo.GetValue().strip()
        if not value:
            return config.speed_ratio_to_speech_rate(config.DEFAULT_SPEED_RATIO)
        try:
            return int(min(max(int(value), -50), 100))
        except ValueError:
            return config.speed_ratio_to_speech_rate(config.DEFAULT_SPEED_RATIO)

    def _set_controls_enabled(self, enabled: bool) -> None:
        self.btn_mic.Enable(enabled)
        self.btn_upload.Enable(enabled)
        self.btn_send.Enable(enabled)
        self.input_text.Enable(enabled)
        self.voice_combo.Enable(enabled)
        self.speed_combo.Enable(enabled)

    def _on_worker_event_from_thread(self, event_type, payload):
        wx.CallAfter(self.on_worker_event, event_type, payload)

    def on_worker_event(self, event_type: str, payload: dict):
        if event_type == "connected":
            self._set_controls_enabled(True)
            self.SetStatusText("Connected")
            return
        if event_type == "recording_started":
            self.recording = True
            self.btn_mic.SetLabel("结束录音")
            self.btn_mic.SetValue(True)
            self.SetStatusText("Recording")
            return
        if event_type == "recording_stopped":
            self.recording = False
            self.btn_mic.SetLabel("开始录音")
            self.btn_mic.SetValue(False)
            self.SetStatusText("Connected")
            return
        if event_type == "audio_playing":
            self.SetStatusText("Playing audio")
            return
        if event_type == "response_done":
            self.SetStatusText("Connected")
            return
        if event_type == "voice_config_applied":
            return
        if event_type == "voice_config_failed":
            self.SetStatusText("Connected")
            return
        if event_type == "status":
            self.SetStatusText(payload.get("message", ""))
            return
        if event_type == "error":
            message = payload.get("message", "Unknown error")
            self.SetStatusText("Error")
            wx.MessageBox(message, "Error", wx.OK | wx.ICON_ERROR)
            return
        if event_type == "log":
            return
        if event_type == "server_text":
            text = payload.get("text", "").strip()
            if text:
                self.append_chat("豆包", text)
            return

    def on_toggle_mic(self, evt):
        is_start = self.btn_mic.GetValue()
        if is_start:
            self.worker.start_mic()
        else:
            self.worker.stop_mic()

    def on_upload_file(self, evt):
        wildcard = "Audio files (*.wav;*.mp3;*.m4a;*.flac;*.aac)|*.wav;*.mp3;*.m4a;*.flac;*.aac"
        with wx.FileDialog(
            self,
            "选择音频文件",
            wildcard=wildcard,
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        ) as dlg:
            if dlg.ShowModal() == wx.ID_CANCEL:
                return
            path = dlg.GetPath()
            self.worker.send_audio_file(path)

    def on_send_text(self, evt):
        content = self.input_text.GetValue().strip()
        if not content:
            wx.MessageBox("请输入要发送的问题。", "提示", wx.OK | wx.ICON_INFORMATION)
            return
        self.append_chat("我", content)
        self.worker.send_text(content)
        self.input_text.Clear()

    def on_voice_changed(self, evt):
        voice_id = self.selected_voice_id()
        self._reload_speed_choices(voice_id)
        speech_rate = self.selected_speech_rate()
        self._set_speed_value(speech_rate)
        self.worker.set_voice_config_by_speech_rate(voice_id, speech_rate)

    def on_speed_changed(self, evt):
        if self._updating_speed_combo:
            return
        voice_id = self.selected_voice_id()
        speech_rate = self.selected_speech_rate()
        self._set_speed_value(speech_rate)
        self.worker.set_voice_config_by_speech_rate(voice_id, speech_rate)

    def append_chat(self, role: str, message: str) -> None:
        role = (role or "").strip()
        message = (message or "").strip()
        if not role or not message:
            return
        self.chat_text.AppendText(f"{role}：{message}\n")

    def append_log(self, message: str) -> None:
        # Compatibility shim kept to avoid stale callsites in external code.
        if not message:
            return
        return

    def on_close(self, evt):
        try:
            self._set_controls_enabled(False)
            self.SetStatusText("Shutting down...")
            self.worker.shutdown()
        finally:
            self.Destroy()


class RealtimeDialogApp(wx.App):
    def OnInit(self):
        frame = MainFrame()
        frame.Show()
        return True
