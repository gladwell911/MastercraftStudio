import time
from pathlib import Path

import wx
import main

# Avoid installing real system hook in automated validation.
main.GlobalCtrlTapHook.start = lambda self: None
main.GlobalCtrlTapHook.stop = lambda self: None


def assert_true(cond, msg):
    if not cond:
        raise AssertionError(msg)


class FakeEvent:
    def __init__(self, key):
        self._key = key
        self.skipped = False

    def GetKeyCode(self):
        return self._key

    def Skip(self):
        self.skipped = True


app = wx.App(False)
frame = main.ChatFrame()
frame.Hide()

# 1) Detail page split: question row opens question-only page.
frame.active_session_turns = [
    {
        "question": "这是一个测试问题",
        "answer_md": "这是一个测试回答",
        "model": main.DEFAULT_MODEL_ID,
        "created_at": time.time(),
    }
]
frame.view_mode = "active"
frame._render_answer_list()
q_row = next(i for i, meta in enumerate(frame.answer_meta) if meta[0] == "question")
frame.answer_list.SetSelection(q_row)
opened = {}
frame._open_local_webpage = lambda p: opened.__setitem__("path", str(p))
ok = frame._try_open_selected_answer_detail()
assert_true(ok, "question row Enter should open detail page")
q_path = Path(opened["path"])
q_html = q_path.read_text(encoding="utf-8")
assert_true("问题详情" in q_html, "question detail page should contain question section")
assert_true("回答详情" not in q_html, "question detail page should not contain answer section")
assert_true("这是一个测试问题" in q_html, "question text should appear in question page")

# 2) Detail page split: answer row opens answer-only page.
a_row = next(i for i, meta in enumerate(frame.answer_meta) if meta[0] == "answer")
frame.answer_list.SetSelection(a_row)
ok = frame._try_open_selected_answer_detail()
assert_true(ok, "answer row Enter should open detail page")
a_path = Path(opened["path"])
a_html = a_path.read_text(encoding="utf-8")
assert_true("回答详情" in a_html, "answer detail page should contain answer section")
assert_true("问题详情" not in a_html, "answer detail page should not contain question section")
assert_true("这是一个测试回答" in a_html, "answer text should appear in answer page")

# 3) Global Ctrl callback forwards to voice controller.
calls = []
frame._voice_input.on_ctrl_keyup = lambda combo_used=False, side="left": calls.append((combo_used, side))
frame._on_global_ctrl_keyup(True, "right")
frame._on_global_ctrl_keyup(False, "left")
assert_true(calls == [(True, "right"), (False, "left")], "global ctrl keyup must be forwarded")

# 4) History loading behavior and model switching.
frame.active_session_turns = [
    {
        "question": "当前会话内容",
        "answer_md": "当前回答",
        "model": "openai/gpt-5.2",
        "created_at": time.time(),
    }
]
old_count = len(frame.archived_chats)
chat_id = "hist-case-1"
history_turns = [
    {
        "question": "历史问题1",
        "answer_md": "历史回答1",
        "model": "deepseek/deepseek-r1-0528",
        "created_at": time.time(),
    },
    {
        "question": "历史问题2",
        "answer_md": "历史回答2",
        "model": "deepseek/deepseek-r1-0528",
        "created_at": time.time(),
    },
]
frame.archived_chats.append(
    {
        "id": chat_id,
        "title": "待载入历史",
        "pinned": False,
        "created_at": time.time(),
        "turns": history_turns,
    }
)
frame._refresh_history(chat_id)
idx = frame.history_ids.index(chat_id)
frame.history_list.SetSelection(idx)
frame.input_edit.SetValue("这段输入应被清空")
ok = frame._activate_selected_history()
assert_true(ok, "history activation should succeed")

assert_true(frame.view_mode == "active", "after load history should switch to active mode")
assert_true(frame.view_history_id is None, "history view id should be cleared")
assert_true(frame.input_edit.GetValue() == "", "input editor should be cleared")
assert_true(len(frame.active_session_turns) == 2, "active turns should be replaced by history turns")
assert_true(frame.active_session_turns[0]["question"] == "历史问题1", "active turns must match selected history")
assert_true(frame.selected_model == "deepseek/deepseek-r1-0528", "model should switch to history model")
assert_true(frame.model_combo.GetValue() == "deepseek/deepseek-r1-0528", "model combobox should sync")
assert_true(chat_id not in [str(c.get("id")) for c in frame.archived_chats], "loaded history should be removed from history list")
assert_true(len(frame.archived_chats) >= old_count, "previous active session should be archived")

# 5) History Enter handlers should call unified activation entry.
trigger = {"called": 0}
orig_activate = frame._activate_selected_history
frame._activate_selected_history = lambda: trigger.__setitem__("called", trigger["called"] + 1) or True

frame._on_history_key_down(FakeEvent(wx.WXK_RETURN))
frame._on_history_char(FakeEvent(wx.WXK_RETURN))
assert_true(trigger["called"] == 2, "history Enter key handlers should call _activate_selected_history")

frame._activate_selected_history = orig_activate
frame.Destroy()
app.Destroy()
print("validation_passed")

