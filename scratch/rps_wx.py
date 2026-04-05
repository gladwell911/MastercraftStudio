import random

import wx


MOVES = ("石头", "剪刀", "布")
WIN_RULES = {
    ("石头", "剪刀"),
    ("剪刀", "布"),
    ("布", "石头"),
}


class RockPaperScissorsFrame(wx.Frame):
    def __init__(self):
        super().__init__(parent=None, title="石头剪刀布", size=(520, 380))

        panel = wx.Panel(self)
        panel.SetBackgroundColour(wx.Colour(245, 247, 250))

        title = wx.StaticText(panel, label="石头剪刀布")
        title_font = wx.Font(
            18,
            wx.FONTFAMILY_DEFAULT,
            wx.FONTSTYLE_NORMAL,
            wx.FONTWEIGHT_BOLD,
        )
        title.SetFont(title_font)

        hint = wx.StaticText(
            panel,
            label="使用 Tab 切换按钮焦点，按空格或回车触发当前按钮。",
        )

        self.status_label = wx.StaticText(panel, label="请选择你的出拳。")
        self.player_label = wx.StaticText(panel, label="你：-")
        self.computer_label = wx.StaticText(panel, label="电脑：-")
        self.score_label = wx.StaticText(panel, label="比分 你 0 : 0 电脑")

        self.player_score = 0
        self.computer_score = 0

        button_row = wx.BoxSizer(wx.HORIZONTAL)
        self.rock_button = wx.Button(panel, label="石头")
        self.scissors_button = wx.Button(panel, label="剪刀")
        self.paper_button = wx.Button(panel, label="布")

        self.rock_button.Bind(wx.EVT_BUTTON, lambda evt: self.play_round("石头"))
        self.scissors_button.Bind(wx.EVT_BUTTON, lambda evt: self.play_round("剪刀"))
        self.paper_button.Bind(wx.EVT_BUTTON, lambda evt: self.play_round("布"))

        for button in (self.rock_button, self.scissors_button, self.paper_button):
            button.SetMinSize((100, 42))
            button_row.Add(button, 0, wx.ALL, 5)

        action_row = wx.BoxSizer(wx.HORIZONTAL)
        self.reset_button = wx.Button(panel, label="重置比分")
        self.exit_button = wx.Button(panel, label="退出")

        self.reset_button.Bind(wx.EVT_BUTTON, self.reset_game)
        self.exit_button.Bind(wx.EVT_BUTTON, lambda evt: self.Close())

        for button in (self.reset_button, self.exit_button):
            button.SetMinSize((100, 40))
            action_row.Add(button, 0, wx.ALL, 5)

        self.scissors_button.MoveAfterInTabOrder(self.rock_button)
        self.paper_button.MoveAfterInTabOrder(self.scissors_button)
        self.reset_button.MoveAfterInTabOrder(self.paper_button)
        self.exit_button.MoveAfterInTabOrder(self.reset_button)

        content = wx.BoxSizer(wx.VERTICAL)
        content.Add(title, 0, wx.ALIGN_CENTER | wx.TOP, 20)
        content.Add(hint, 0, wx.ALIGN_CENTER | wx.TOP, 10)
        content.AddSpacer(20)
        content.Add(self.status_label, 0, wx.ALIGN_CENTER | wx.BOTTOM, 10)
        content.Add(self.player_label, 0, wx.ALIGN_CENTER | wx.BOTTOM, 6)
        content.Add(self.computer_label, 0, wx.ALIGN_CENTER | wx.BOTTOM, 6)
        content.Add(self.score_label, 0, wx.ALIGN_CENTER | wx.BOTTOM, 18)
        content.Add(button_row, 0, wx.ALIGN_CENTER | wx.BOTTOM, 12)
        content.Add(action_row, 0, wx.ALIGN_CENTER)

        panel.SetSizer(content)
        self.Centre()
        self.rock_button.SetFocus()

    def play_round(self, player_move):
        computer_move = random.choice(MOVES)
        result_text = self.judge_round(player_move, computer_move)

        self.player_label.SetLabel(f"你：{player_move}")
        self.computer_label.SetLabel(f"电脑：{computer_move}")
        self.score_label.SetLabel(
            f"比分 你 {self.player_score} : {self.computer_score} 电脑"
        )
        self.status_label.SetLabel(result_text)

    def judge_round(self, player_move, computer_move):
        if player_move == computer_move:
            return "平局，再来一轮。"

        if (player_move, computer_move) in WIN_RULES:
            self.player_score += 1
            return "你赢了。"

        self.computer_score += 1
        return "你输了。"

    def reset_game(self, _event):
        self.player_score = 0
        self.computer_score = 0
        self.status_label.SetLabel("已重置，请重新出拳。")
        self.player_label.SetLabel("你：-")
        self.computer_label.SetLabel("电脑：-")
        self.score_label.SetLabel("比分 你 0 : 0 电脑")
        self.rock_button.SetFocus()


class RockPaperScissorsApp(wx.App):
    def OnInit(self):
        frame = RockPaperScissorsFrame()
        frame.Show()
        return True


if __name__ == "__main__":
    app = RockPaperScissorsApp(False)
    app.MainLoop()
