import copy
import json
import main
import pytest
import time
import wx
from pathlib import Path


def test_answer_list_ctrl_c_still_copies_answer_text(frame, monkeypatch):
    copied = {"text": None}

    class _Clipboard:
        def Open(self):
            return True

        def SetData(self, data):
            copied["text"] = data.GetText()
            return True

        def Close(self):
            return True

    monkeypatch.setattr(main.wx, "TheClipboard", _Clipboard())
    statuses = []
    monkeypatch.setattr(frame, "SetStatusText", lambda text: statuses.append(text))
    monkeypatch.setattr(frame, "_on_any_key_down_escape_minimize", lambda _event: False)

    frame.answer_meta = [("answer", 0, "plain answer", "rich answer")]
    frame.active_session_turns = [{"question": "q", "answer_md": "rich answer", "model": main.DEFAULT_MODEL_ID}]
    frame.answer_list.Clear()
    frame.answer_list.Append("answer row")
    frame.answer_list.SetSelection(0)

    class E:
        def GetKeyCode(self):
            return ord("C")

        def ControlDown(self):
            return True

        def StopPropagation(self):
            return None

        def Skip(self):
            raise AssertionError("should not skip")

    frame._on_answer_key_down(E())

    assert copied["text"] == "rich answer"
    assert statuses[-1] == "已复制"


def test_answer_list_ctrl_c_copies_canonical_markdown_for_any_model(frame, monkeypatch):
    copied = []
    monkeypatch.setattr(frame, "_set_clipboard_text", lambda text: copied.append(text) or True)
    for model in [main.DEFAULT_CODEX_MODEL, main.DEFAULT_CLAUDECODE_MODEL, "openclaw/main", "openai/gpt-5.2"]:
        frame.selected_model = model
        frame.active_session_turns = [
            {
                "question": "q",
                "answer_md": "## 标题\n\n第一段 **加粗**。\n\n- 第二段",
                "model": model,
                "created_at": time.time(),
            }
        ]
        frame.view_mode = "active"
        frame._render_answer_list()
        answer_row = next(i for i, meta in enumerate(frame.answer_meta) if meta[0] == "answer")
        frame.answer_list.SetSelection(answer_row)

        assert frame._copy_selected_answer_to_clipboard() is True

    assert copied == ["## 标题\n\n第一段 **加粗**。\n\n- 第二段"] * 4


def test_answer_list_down_at_last_row_does_not_reset_selection(frame, monkeypatch):
    frame.answer_meta = [
        ("question", 0, "q", ""),
        ("answer", 0, "a", "a"),
    ]
    frame.answer_list.Clear()
    frame.answer_list.Append("q")
    frame.answer_list.Append("a")
    frame.answer_list.SetSelection(1)
    monkeypatch.setattr(
        frame.answer_list,
        "SetSelection",
        lambda _idx: (_ for _ in ()).throw(AssertionError("unchanged Down should not reset selection")),
    )

    class E:
        def GetKeyCode(self):
            return main.wx.WXK_DOWN

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            raise AssertionError("Down at the last row should be consumed without native re-announcement")

    frame._on_answer_key_down(E())

    assert frame.answer_list.GetSelection() == 1


def test_render_answer_list_requests_listbox_repaint(frame, monkeypatch):
    frame.active_session_turns = [
        {"question": "问题", "answer_md": "回答", "model": "openai/gpt-5.2", "created_at": 1.0}
    ]
    seen = {"calls": []}
    monkeypatch.setattr(frame, "_request_listbox_repaint", lambda *controls: seen["calls"].append(controls), raising=False)

    frame._render_answer_list()

    assert seen["calls"] == [(frame.answer_list,)]


def test_render_answer_list_applies_execution_mode_visibility(frame):
    frame.active_session_turns = []
    frame._current_chat_state = {
        "id": "chat-1",
        "title": "执行测试",
        "turns": [],
        "detail_panel_mode": "execution",
        "execution_steps": [{"step": "1"}],
    }
    visibility = {}
    frame.answer_list.Show = lambda visible: visibility.__setitem__("answer", visible)
    frame.execution_list.Show = lambda visible: visibility.__setitem__("execution", visible)

    frame._render_answer_list()

    assert frame.detail_title_label.GetLabel() == "执行过程："
    assert visibility["answer"] is False
    assert visibility["execution"] is True


def test_render_answer_list_hides_requesting_placeholder_until_done(frame):
    frame.active_session_turns = [
        {"question": "问题", "answer_md": main.REQUESTING_TEXT, "model": "openai/gpt-5.2", "created_at": 1.0}
    ]

    frame._render_answer_list()

    rows = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert "问题" in rows
    assert "正在请求..." not in rows
    assert "小诸葛" not in rows


def test_render_answer_list_inserts_context_usage_row_first(frame):
    frame._current_chat_state["context_usage"] = {
        "used_tokens": 113260,
        "context_window": 272000,
        "source": "openclaw",
        "exact": True,
        "fresh": True,
        "model": "gpt-5.4",
        "updated_at": 1.0,
    }
    frame.active_session_turns = [
        {"question": "你好", "answer_md": "你好，有什么可以帮你？", "model": "openai/gpt-5.2", "created_at": 1.0}
    ]

    frame._render_answer_list()

    assert frame.answer_list.GetString(0) == "113k / 272k"
    assert frame.answer_meta[0] == ("context_usage", -1, "113k / 272k", "")
    assert all(meta[0] != "current_model" for meta in frame.answer_meta)
    assert frame.answer_meta[1][0] == "time"
    assert frame.answer_list.GetString(2) == "我"


def test_render_answer_list_keeps_empty_state_below_context_row(frame):
    frame._current_chat_state["context_usage"] = None
    frame.active_session_turns = []

    frame._render_answer_list()

    assert frame.answer_list.GetString(0) == "暂无"
    assert frame.answer_meta[0] == ("context_usage", -1, "暂无", "")
    assert all(meta[0] != "current_model" for meta in frame.answer_meta)
    assert frame.answer_list.GetString(1) == "暂无对话内容"


def test_render_answer_list_history_context_fallback_uses_latest_turn_model(frame):
    frame.archived_chats = [
        {
            "id": "chat-history",
            "title": "历史聊天",
            "context_usage": None,
            "turns": [
                {
                    "question": "历史问题",
                    "answer_md": "历史回答",
                    "model": "anthropic/claude-sonnet-4.6",
                    "created_at": 1.0,
                }
            ],
        }
    ]
    frame.view_mode = "history"
    frame.view_history_id = "chat-history"
    frame.selected_model = "openai/gpt-5.2"

    frame._render_answer_list()

    assert frame.answer_meta[0][0] == "time"
    assert frame.answer_list.GetString(1) == "我"
    assert all(meta[0] != "current_model" for meta in frame.answer_meta)
    assert frame.answer_list.GetString(2) == "历史问题"


def test_render_answer_list_does_not_estimate_openclaw_without_usage(frame):
    frame.selected_model = "openclaw/main"
    frame.active_session_turns = [
        {"question": "你好", "answer_md": "同步回答", "model": "openclaw/main", "created_at": 1.0}
    ]
    frame._current_chat_state = {
        "id": "chat-openclaw",
        "turns": frame.active_session_turns,
        "context_usage": None,
    }

    frame._render_answer_list()

    assert frame.answer_meta[0][0] == "time"
    assert frame.answer_list.GetString(1) == "我"
    assert all(meta[0] != "context_usage" for meta in frame.answer_meta)


def test_on_done_defers_regular_model_estimate_when_api_usage_missing(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_session_turns = [{"question": "你好", "answer_md": "", "model": "openai/gpt-5.2", "created_at": 1.0}]
    frame._current_chat_state["id"] = "chat-current"
    frame._current_chat_state["turns"] = frame.active_session_turns
    scheduled = []
    monkeypatch.setattr(
        frame,
        "_schedule_context_usage_estimate",
        lambda chat, turns, turn_idx, model: scheduled.append((chat.get("id"), turn_idx, model)),
    )
    monkeypatch.setattr(frame, "_refresh_answer_list_preserving_selection", lambda *args, **kwargs: None)

    frame._on_done(0, "你好，有什么可以帮你？", "", "openai/gpt-5.2", "", frame.active_chat_id)

    assert "context_usage" not in frame._current_chat_state
    assert scheduled == [("chat-current", 0, "openai/gpt-5.2")]


def test_on_done_renders_final_answer_after_hidden_requesting_placeholder(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_session_turns = [
        {"question": "问题", "answer_md": main.REQUESTING_TEXT, "model": "openai/gpt-5.2", "created_at": 1.0, "request_status": "pending"}
    ]
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)
    monkeypatch.setattr(frame, "_focus_latest_answer", lambda: None)

    frame._render_answer_list()
    before = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert "正在请求..." not in before

    frame._on_done(0, "最终回答", "", "openai/gpt-5.2", "", "chat-current")

    after = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert "小诸葛" in after
    assert "最终回答" in after


def test_codex_final_answer_appends_without_refreshing_answer_list(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_turn_idx = 1
    frame.active_codex_thread_id = "thread-current"
    frame.active_codex_turn_id = "turn-current"
    frame.active_session_turns = [
        {"question": "上一条问题", "answer_md": "上一条回复", "model": main.DEFAULT_CODEX_MODEL, "created_at": 1.0},
        {
            "question": "当前 Codex 问题",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": 2.0,
            "codex_thread_id": "thread-current",
            "codex_turn_id": "turn-current",
        },
    ]
    frame._current_chat_state.update({
        "id": "chat-current",
        "turns": frame.active_session_turns,
        "codex_thread_id": "thread-current",
        "codex_turn_id": "turn-current",
    })
    frame.view_mode = "active"
    frame._render_answer_list()
    old_answer_row = next(idx for idx, meta in enumerate(frame.answer_meta) if meta[0] == "answer" and meta[1] == 0)
    frame.answer_list.SetSelection(old_answer_row)

    operations = []
    original_append = frame.answer_list.Append

    def record_append(label):
        operations.append(("Append", label))
        return original_append(label)

    monkeypatch.setattr(frame, "_render_answer_list_compat", lambda *args, **kwargs: pytest.fail("Codex final answer should append, not rerender answer list"))
    monkeypatch.setattr(frame, "_refresh_answer_list_preserving_selection", lambda *args, **kwargs: pytest.fail("Codex final answer should not preserve old selection"))
    monkeypatch.setattr(frame, "_push_remote_final_answer", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda *args, **kwargs: None)
    focused = []
    monkeypatch.setattr(frame, "_focus_latest_answer", lambda: focused.append(True))
    monkeypatch.setattr(frame.answer_list, "Append", record_append)
    monkeypatch.setattr(frame.answer_list, "SetSelection", lambda *args, **kwargs: pytest.fail("Codex final answer should not reselect old answer"))
    monkeypatch.setattr(frame.answer_list, "Refresh", lambda *args, **kwargs: pytest.fail("Codex final answer should not refresh answer list"))

    frame._on_codex_event_for_chat(
        "chat-current",
        main.CodexEvent(
            type="item_completed",
            phase="final_answer",
            thread_id="thread-current",
            turn_id="turn-current",
            text="Codex 最终回答",
        ),
    )

    assert operations == [("Append", "小诸葛"), ("Append", "Codex 最终回答")]
    assert frame.active_session_turns[1]["answer_md"] == "Codex 最终回答"
    rows = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert rows[-2:] == ["小诸葛", "Codex 最终回答"]
    assert frame.answer_meta[-2:] == [("ai", 1, "小诸葛", ""), ("answer", 1, "Codex 最终回答", "Codex 最终回答")]
    assert focused == [True]


def test_subagent_result_updates_answer_list_without_focus_steal(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_codex_thread_id = "thread-current"
    frame.active_codex_turn_id = "turn-current"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {
            "question": "q",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": 1.0,
        }
    ]
    frame._current_chat_state = {
        "id": "chat-current",
        "title": "当前聊天",
        "turns": frame.active_session_turns,
        "detail_panel_mode": "answers",
        "execution_steps": [],
    }
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_push_remote_final_answer", lambda *args, **kwargs: None)
    frame._render_answer_list()
    frame.input_edit.SetFocus()

    frame._on_codex_event_for_chat(
        "chat-current",
        main.CodexEvent(
            type="subagent_result",
            thread_id="thread-current",
            turn_id="turn-current",
            text="Ada（worker）\nDONE\n\n子代理结果",
            subtype="collab_waiting_end",
            display_kind="commentary",
        ),
    )

    assert frame.active_session_turns[0]["answer_md"] == "Ada（worker）\nDONE\n\n子代理结果"
    assert frame.active_session_turns[0]["answer_origin"] == "codex-subagent-result"
    rows = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert any("子代理结果" in row for row in rows)
    assert frame.input_edit.HasFocus()


def test_detail_page_split_question_and_answer(frame):
    frame.active_session_turns = [
        {
            "question": "测试问题",
            "answer_md": "测试回答",
            "model": main.DEFAULT_MODEL_ID,
            "created_at": time.time(),
        }
    ]
    frame.view_mode = "active"
    frame._render_answer_list()

    opened = {}
    frame._open_local_webpage = lambda p: opened.__setitem__("path", str(p))

    q_row = next(i for i, m in enumerate(frame.answer_meta) if m[0] == "question")
    frame.answer_list.SetSelection(q_row)
    assert frame._try_open_selected_answer_detail()
    q_html = Path(opened["path"]).read_text(encoding="utf-8")
    assert "问题详情" in q_html
    assert "回答详情" not in q_html

    a_row = next(i for i, m in enumerate(frame.answer_meta) if m[0] == "answer")
    frame.answer_list.SetSelection(a_row)
    assert frame._try_open_selected_answer_detail()
    a_html = Path(opened["path"]).read_text(encoding="utf-8")
    assert "回答详情" in a_html
    assert "问题详情" not in a_html


def test_answer_enter_opens_plain_text_viewer_instead_of_web_detail(frame, monkeypatch):
    frame.active_session_turns = [
        {
            "question": "测试问题",
            "answer_md": "## 标题\n\n第一段 **加粗**。\n\n第二段",
            "model": main.DEFAULT_MODEL_ID,
            "created_at": time.time(),
        }
    ]
    frame.view_mode = "active"
    frame._render_answer_list()
    answer_row = next(i for i, m in enumerate(frame.answer_meta) if m[0] == "answer")
    frame.answer_list.SetSelection(answer_row)
    opened_viewer = []
    opened_web = []
    monkeypatch.setattr(frame, "_open_answer_text_viewer", lambda title, text, *args: opened_viewer.append((title, text)) or True)
    monkeypatch.setattr(frame, "_try_open_selected_answer_detail", lambda: opened_web.append(True) or True)

    class E:
        def GetKeyCode(self):
            return wx.WXK_RETURN

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def ShiftDown(self):
            return False

        def Skip(self):
            raise AssertionError("Enter should be handled")

    frame._on_answer_key_down(E())

    assert opened_web == []
    assert opened_viewer == [("回答详情", "## 标题\n\n第一段 **加粗**。\n\n第二段")]


def test_answer_text_viewer_preserves_ordered_list_markers(frame):
    frame.active_session_turns = [
        {
            "question": "测试问题",
            "answer_md": "1. 第一项\n2. 第二项",
            "model": main.DEFAULT_MODEL_ID,
            "created_at": time.time(),
        }
    ]
    frame.view_mode = "active"
    frame._render_answer_list()
    answer_row = next(i for i, m in enumerate(frame.answer_meta) if m[0] == "answer")
    frame.answer_list.SetSelection(answer_row)

    assert frame._selected_answer_text_viewer_content() == ("回答详情", "1. 第一项\n2. 第二项")


@pytest.mark.parametrize(
    "owner_chat_id,is_history",
    (("chat-active", False), ("chat-archived", True)),
)
def test_answer_text_viewer_continue_rejects_legacy_chat_only_owner(frame, monkeypatch, owner_chat_id, is_history):
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.active_session_turns = [{"question": "active", "answer_md": "answer", "model": main.DEFAULT_MODEL_ID}]
    frame._current_chat_state = {"id": "chat-active", "turns": frame.active_session_turns}
    frame.archived_chats = [
        {
            "id": "chat-archived",
            "title": "archived",
            "turns": [{"question": "archived", "answer_md": "answer", "model": main.DEFAULT_MODEL_ID}],
        }
    ]
    focused = []
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: None)
    monkeypatch.setattr(frame, "_focus_input_box", lambda: focused.append(True) or True)
    monkeypatch.setattr(frame, "_submit_question", lambda *_args, **_kwargs: pytest.fail("Continue must not submit a question"))

    assert frame._continue_from_answer_text_viewer(owner_chat_id) is False
    assert focused == []


def test_answer_text_viewer_continue_restores_current_state_owner_when_active_ids_are_empty(frame, monkeypatch):
    frame.active_chat_id = ""
    frame.current_chat_id = ""
    frame.active_session_turns = [{"question": "active", "answer_md": "answer", "model": main.DEFAULT_MODEL_ID}]
    frame._current_chat_state = {"id": "chat-current-state", "turns": frame.active_session_turns}
    focused = []
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: None)
    monkeypatch.setattr(frame, "_focus_input_box", lambda: focused.append(True) or True)
    monkeypatch.setattr(frame, "_submit_question", lambda *_args, **_kwargs: pytest.fail("Continue must not submit a question"))

    assert frame._continue_from_answer_text_viewer("chat-current-state") is False
    assert focused == []


def test_answer_text_viewer_continue_missing_owner_keeps_visible_detail_and_does_not_submit(frame, monkeypatch):
    focused = []
    monkeypatch.setattr(frame, "_show_history_chat", lambda *_args, **_kwargs: pytest.fail("missing owner must not switch detail"))
    monkeypatch.setattr(frame, "_focus_input_box", lambda: focused.append(True) or True)
    monkeypatch.setattr(frame, "_submit_question", lambda *_args, **_kwargs: pytest.fail("Continue must not submit a question"))

    assert frame._continue_from_answer_text_viewer("") is False

    assert focused == []


def test_answer_text_viewer_continue_unavailable_owner_keeps_visible_detail_and_does_not_submit(frame, monkeypatch):
    focused = []
    monkeypatch.setattr(frame, "_show_history_chat", lambda *_args, **_kwargs: pytest.fail("unavailable owner must not switch detail"))
    monkeypatch.setattr(frame, "_focus_input_box", lambda: focused.append(True) or True)
    monkeypatch.setattr(frame, "_submit_question", lambda *_args, **_kwargs: pytest.fail("Continue must not submit a question"))

    assert frame._continue_from_answer_text_viewer("chat-deleted") is False

    assert focused == []


def test_answer_text_viewer_captures_owner_when_opened(frame, monkeypatch):
    continued = []

    class _Viewer:
        def __init__(self, _parent, _title, _text, **_kwargs):
            pass

        def ShowModal(self):
            return wx.ID_OK

        def Destroy(self):
            return None

    frame.view_mode = "history"
    frame.view_history_id = "chat-owned-by-viewer"
    monkeypatch.setattr(main, "AnswerTextViewerDialog", _Viewer)
    monkeypatch.setattr(frame, "_continue_from_answer_text_viewer", lambda owner: continued.append(owner) or True)

    assert frame._open_answer_text_viewer("回答详情", "内容") is True
    assert continued == []


def test_answer_text_viewer_modal_ok_captures_active_owner_when_opened(frame, monkeypatch):
    continued = []

    class _Viewer:
        def __init__(self, _parent, _title, _text, **_kwargs):
            pass

        def ShowModal(self):
            return wx.ID_OK

        def Destroy(self):
            return None

    frame.view_mode = "active"
    frame.active_chat_id = "chat-active-owner"
    frame.current_chat_id = "chat-active-owner"
    frame._current_chat_state = {"id": "chat-active-owner", "turns": []}
    monkeypatch.setattr(main, "AnswerTextViewerDialog", _Viewer)
    monkeypatch.setattr(frame, "_continue_from_answer_text_viewer", lambda owner: continued.append(owner) or True)

    assert frame._open_answer_text_viewer("回答详情", "内容") is True
    assert continued == []


def test_answer_text_viewer_dialog_esc_closes_and_continue_callback_runs(frame):
    payload = main.AnswerViewerPayload("chat", 0, "turn", "", main.DEFAULT_MODEL_ID, "第一段\n\n第二段")
    continued = []
    dlg = main.AnswerTextViewerDialog(
        frame, "回答详情", payload=payload, on_continue=lambda owner: continued.append(owner) or True
    )
    original_finish = dlg._finish
    original_is_shown = dlg.IsShown
    try:
        dlg.IsShown = lambda: True
        closed = []
        dlg._finish = lambda code: closed.append(code)
        assert dlg.GetTitle() == "回答详情"
        assert dlg.text_ctrl.GetValue() == "\n第一段\n\n第二段"
        assert dlg.text_ctrl.IsEditable()
        assert dlg.close_button.GetLabel() == "关闭"
        assert dlg.continue_button.GetLabel() == "继续"
        dlg._on_continue_clicked()
        assert closed == [wx.ID_OK]
        assert continued == [payload]

        class E:
            def GetKeyCode(self):
                return wx.WXK_ESCAPE

            def Skip(self):
                raise AssertionError("Esc should close the viewer")

        dlg._on_char_hook(E())
        assert closed == [wx.ID_OK, wx.ID_CLOSE]
    finally:
        if dlg:
            dlg._finish = original_finish
            dlg.IsShown = original_is_shown
            dlg._closing = False
            dlg.Destroy()


@pytest.mark.parametrize("canonical", ["", "answer", "\nanswer", "\r\n\nanswer"])
def test_answer_text_viewer_normalizes_only_display_leading_newlines(frame, canonical):
    dlg = main.AnswerTextViewerDialog(frame, "回答详情", canonical)
    try:
        assert dlg.canonical_text == canonical
        assert dlg.text_ctrl.GetValue() == "\n" + canonical.lstrip("\r\n")
    finally:
        dlg.Destroy()


@pytest.mark.parametrize(
    "scratch,selection,expected",
    [
        ("\nedited whole", (0, 0), "edited whole"),
        ("edited without prefix", (0, 0), "edited without prefix"),
        ("\nalpha beta", (7, 11), "beta"),
        ("\n\nkept", (0, 0), "\nkept"),
    ],
)
def test_answer_text_viewer_copy_uses_scratch_selection_or_normalized_whole_text(
    frame, monkeypatch, scratch, selection, expected
):
    copied = []
    monkeypatch.setattr(frame, "_set_clipboard_text", lambda text: copied.append(text) or True)
    dlg = main.AnswerTextViewerDialog(frame, "回答详情", "canonical")
    try:
        dlg.text_ctrl.SetValue(scratch)
        dlg.text_ctrl.SetSelection(*selection)
        dlg._on_copy_clicked()

        assert copied == [expected]
        assert dlg.canonical_text == "canonical"
    finally:
        dlg.Destroy()


def test_answer_text_viewer_failed_copy_keeps_scratch_selection_and_canonical(frame, monkeypatch):
    statuses = []
    monkeypatch.setattr(frame, "_set_clipboard_text", lambda _text: False)
    monkeypatch.setattr(frame, "SetStatusText", lambda text: statuses.append(text))
    payload = main.AnswerViewerPayload("chat", 0, "turn", "session", main.DEFAULT_MODEL_ID, "canonical")
    dlg = main.AnswerTextViewerDialog(frame, "回答详情", payload=payload)
    try:
        dlg.text_ctrl.SetValue("\nscratch value")
        dlg.text_ctrl.SetSelection(1, 8)
        dlg._on_copy_clicked()

        assert dlg.text_ctrl.GetValue() == "\nscratch value"
        assert dlg.text_ctrl.GetSelection() == (1, 8)
        assert dlg.canonical_text == "canonical"
        assert dlg.payload == payload
        assert statuses == []
    finally:
        dlg.Destroy()


def test_answer_text_viewer_tracks_synthetic_newline_through_edits_by_identity(frame, monkeypatch):
    copied = []
    monkeypatch.setattr(frame, "_set_clipboard_text", lambda text: copied.append(text) or True)
    dlg = main.AnswerTextViewerDialog(frame, "回答详情", "canonical")
    try:
        dlg.text_ctrl.SetInsertionPoint(0)
        dlg.text_ctrl.WriteText("before")
        assert dlg._display_marker_offset == len("before")
        dlg._on_copy_clicked()
        assert copied.pop() == "beforecanonical"

        marker = dlg._display_marker_offset
        dlg.text_ctrl.Remove(marker, marker + 1)
        assert dlg._display_marker_offset is None
        dlg.text_ctrl.SetInsertionPoint(marker)
        dlg.text_ctrl.WriteText("\n")
        dlg._on_copy_clicked()
        assert copied.pop() == "before\ncanonical"
    finally:
        dlg.Destroy()


def test_answer_text_viewer_selection_copy_removes_synthetic_only_when_selected(frame, monkeypatch):
    copied = []
    monkeypatch.setattr(frame, "_set_clipboard_text", lambda text: copied.append(text) or True)
    dlg = main.AnswerTextViewerDialog(frame, "回答详情", "alpha\nbeta")
    try:
        dlg.text_ctrl.SetSelection(0, 7)
        dlg._on_copy_clicked()
        dlg.text_ctrl.SetSelection(7, dlg.text_ctrl.GetLastPosition())
        dlg._on_copy_clicked()
        assert copied == ["alpha\n", "beta"]
    finally:
        dlg.Destroy()


@pytest.mark.parametrize("replacement", ["replace", "set_value"])
def test_answer_text_viewer_same_text_replacement_surrenders_synthetic_marker(
    frame, monkeypatch, replacement
):
    copied = []
    monkeypatch.setattr(frame, "_set_clipboard_text", lambda text: copied.append(text) or True)
    dlg = main.AnswerTextViewerDialog(frame, "回答详情", "canonical")
    try:
        if replacement == "replace":
            dlg.text_ctrl.Replace(0, 1, "\n")
        else:
            dlg.text_ctrl.SetValue(dlg.text_ctrl.GetValue())
            # Some wx builds suppress EVT_TEXT for an identical SetValue;
            # exercise the conservative handler contract explicitly.
            if dlg._display_marker_offset is not None:
                event = type("TextEvent", (), {"Skip": lambda self: None})()
                dlg._on_scratch_text_changed(event)
        dlg._on_copy_clicked()

        assert dlg._display_marker_offset is None
        assert copied == ["\ncanonical"]
    finally:
        dlg.Destroy()


@pytest.mark.parametrize("close_path", ["button", "escape", "window"])
def test_answer_text_viewer_close_paths_discard_scratch_and_reopen_canonical(frame, close_path):
    canonical = "\ncanonical answer"
    dlg = main.AnswerTextViewerDialog(frame, "回答详情", canonical)
    try:
        dlg.text_ctrl.SetValue("\nscratch mutation")
        dlg._finish = lambda _code: None
        if close_path == "button":
            dlg._on_close()
        elif close_path == "escape":
            event = type("Escape", (), {"GetKeyCode": lambda self: wx.WXK_ESCAPE})()
            dlg._on_char_hook(event)
        else:
            dlg.Destroy()
            dlg = None
    finally:
        if dlg is not None:
            dlg.Destroy()

    reopened = main.AnswerTextViewerDialog(frame, "回答详情", canonical)
    try:
        assert reopened.text_ctrl.GetValue() == "\ncanonical answer"
        assert reopened.canonical_text == canonical
    finally:
        reopened.Destroy()


@pytest.mark.parametrize("close_path", ["button", "escape", "title"])
def test_open_selected_answer_viewer_modal_lifecycle_discards_scratch_without_side_effects(
    frame, monkeypatch, close_path
):
    canonical = "canonical answer"
    turn = {
        "question": "q",
        "answer_md": canonical,
        "model": main.DEFAULT_CODEX_MODEL,
        "codex_thread_id": "thread",
        "codex_turn_id": "turn",
    }
    frame.active_chat_id = frame.current_chat_id = "chat"
    frame.active_session_turns = [turn]
    frame._current_chat_state = {"id": "chat", "model": main.DEFAULT_CODEX_MODEL, "turns": frame.active_session_turns}
    frame.answer_meta = [("answer", 0, canonical, canonical)]
    frame.answer_list.Set([canonical])
    frame.answer_list.SetSelection(0)
    snapshots = []
    real_viewer = main.AnswerTextViewerDialog

    def viewer_factory(*args, **kwargs):
        dlg = real_viewer(*args, **kwargs)
        snapshots.append((dlg.canonical_text, dlg.payload))

        def show_modal():
            dlg.text_ctrl.SetValue("\nscratch mutation")
            dlg._finish = lambda _code: None
            if close_path == "button":
                dlg._on_close()
            elif close_path == "escape":
                event = type("Escape", (), {"GetKeyCode": lambda self: wx.WXK_ESCAPE})()
                dlg._on_char_hook(event)
            return wx.ID_CLOSE

        dlg.ShowModal = show_modal
        return dlg

    monkeypatch.setattr(main, "AnswerTextViewerDialog", viewer_factory)
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: pytest.fail("scratch must not save"))
    monkeypatch.setattr(frame, "_continue_from_answer_text_viewer", lambda _payload: pytest.fail("scratch must not continue"))
    monkeypatch.setattr(frame, "_try_open_selected_answer_detail", lambda: pytest.fail("scratch must not open detail"))

    assert frame._open_selected_answer_text_viewer() is True
    assert frame._open_selected_answer_text_viewer() is True
    assert turn["answer_md"] == canonical
    assert [item[0] for item in snapshots] == [canonical, canonical]
    assert all(item[1].answer_md == canonical for item in snapshots)


def test_answer_text_viewer_payload_captures_historical_codex_turn_owner(frame):
    turn = {
        "question": "q",
        "answer_md": "\n**canonical** 😀 C:/work/tool.py",
        "model": main.DEFAULT_CODEX_MODEL,
        "codex_thread_id": "thread-selected",
        "codex_turn_id": "turn-selected",
    }
    frame.archived_chats = [{"id": "chat-owner", "model": main.DEFAULT_CODEX_MODEL, "turns": [turn]}]
    frame.view_mode = "history"
    frame.view_history_id = "chat-owner"
    frame.answer_meta = [("answer", 0, "lossy", "mutable")]
    frame.answer_list.Clear()
    frame.answer_list.Append("answer")
    frame.answer_list.SetSelection(0)

    payload = frame._selected_answer_viewer_payload()

    assert payload == main.AnswerViewerPayload(
        "chat-owner", 0, "turn-selected", "thread-selected", main.DEFAULT_CODEX_MODEL, turn["answer_md"]
    )


def test_answer_text_viewer_continue_dispatches_once_to_captured_owner(frame, monkeypatch):
    model = "openai/gpt-5.2"
    turn = {"question": "q", "answer_md": "canonical", "model": model, "turn_id": "turn-stable"}
    frame.archived_chats = [{"id": "chat-owner", "model": model, "turns": [turn]}]
    payload = main.AnswerViewerPayload("chat-owner", 0, "turn-stable", "", model, "canonical")
    submitted = []
    monkeypatch.setattr(
        frame,
        "_submit_question",
        lambda question, **kwargs: submitted.append((question, kwargs)) or (True, ""),
    )

    assert frame._continue_from_answer_text_viewer(payload) is True
    assert submitted == [("继续", {"source": "local", "model": model, "chat_id": "chat-owner"})]


def test_answer_text_viewer_continue_rejects_stale_canonical_owner(frame, monkeypatch):
    model = "openai/gpt-5.2"
    frame.archived_chats = [{
        "id": "chat-owner",
        "model": model,
        "turns": [{"question": "q", "answer_md": "replaced", "model": model, "turn_id": "turn-stable"}],
    }]
    payload = main.AnswerViewerPayload("chat-owner", 0, "turn-stable", "", model, "captured")
    monkeypatch.setattr(frame, "_submit_question", lambda *_a, **_k: pytest.fail("stale owner must not dispatch"))

    assert frame._continue_from_answer_text_viewer(payload) is False


@pytest.mark.parametrize(
    "model,session_key,turn_key",
    ((main.DEFAULT_CODEX_MODEL, "codex_thread_id", "codex_turn_id"),
     (main.DEFAULT_KIMI_MODEL, "kimi_session_id", "kimi_turn_id")),
)
def test_answer_viewer_current_provider_adopts_captured_active_ids(frame, monkeypatch, model, session_key, turn_key):
    turn = {"question": "q", "answer_md": "canonical", "model": model, session_key: "captured-session", turn_key: "captured-turn"}
    frame.active_chat_id = frame.current_chat_id = "chat-current"
    frame.active_session_turns = [turn]
    frame._current_chat_state = {"id": "chat-current", "model": model, "turns": frame.active_session_turns, session_key: "latest-session", turn_key: "latest-turn"}
    frame._active_request_count = 0
    frame.active_codex_turn_active = frame.active_kimi_turn_active = False
    frame.active_codex_thread_id = frame.active_kimi_session_id = "active-wrong-session"
    frame.active_codex_turn_id = frame.active_kimi_turn_id = "active-wrong-turn"
    payload = main.AnswerViewerPayload("chat-current", 0, "captured-turn", "captured-session", model, "canonical")
    seen = []

    def submit(*_args, **_kwargs):
        if main.is_codex_model(model):
            seen.append((frame.active_codex_thread_id, frame.active_codex_turn_id))
        else:
            seen.append((frame.active_kimi_session_id, frame.active_kimi_turn_id))
        return True, ""

    monkeypatch.setattr(frame, "_submit_question", submit)
    assert frame._continue_from_answer_text_viewer(payload) is True
    assert seen == [("captured-session", "captured-turn")]


@pytest.mark.parametrize("model", [main.DEFAULT_CODEX_MODEL, main.DEFAULT_KIMI_MODEL])
def test_answer_viewer_historical_provider_turn_without_ids_disables_continue(frame, model):
    turn = {"question": "q", "answer_md": "canonical", "model": model}
    chat = {"id": "chat-history", "model": model, "turns": [turn]}
    if main.is_codex_model(model):
        chat.update(codex_thread_id="latest-thread", codex_turn_id="latest-turn")
    else:
        chat.update(kimi_session_id="latest-session", kimi_turn_id="latest-turn")
    frame.archived_chats = [chat]
    frame.view_mode = "history"
    frame.view_history_id = "chat-history"
    frame.answer_meta = [("answer", 0, "plain", "detail")]
    frame.answer_list.Set(["answer"])
    frame.answer_list.SetSelection(0)

    assert frame._selected_answer_text_viewer_content() == ("回答详情", "canonical")
    assert frame._selected_answer_viewer_payload() is None


def test_answer_viewer_claudecode_rejects_unrelated_active_client(frame, monkeypatch):
    model = main.DEFAULT_CLAUDECODE_MODEL
    turn = {"question": "q", "answer_md": "canonical", "model": model, "turn_id": "turn", "claudecode_session_id": "session"}
    frame.archived_chats = [{"id": "chat-owner", "model": model, "turns": [turn], "claudecode_session_id": "session"}]
    frame._active_claudecode_client = object()
    frame._active_claudecode_chat_id = "chat-other"
    payload = main.AnswerViewerPayload("chat-owner", 0, "turn", "session", model, "canonical")
    monkeypatch.setattr(frame, "_submit_question", lambda *_a, **_k: pytest.fail("must not inject"))
    assert frame._continue_from_answer_text_viewer(payload) is False


def test_answer_viewer_claudecode_routes_captured_historical_session_when_idle(frame, monkeypatch):
    model = main.DEFAULT_CLAUDECODE_MODEL
    turn = {"question": "q", "answer_md": "canonical", "model": model, "turn_id": "turn", "claudecode_session_id": "captured-session"}
    chat = {"id": "chat-owner", "model": model, "turns": [turn], "claudecode_session_id": "latest-session"}
    frame.archived_chats = [chat]
    frame.active_claudecode_session_id = "unrelated-active-session"
    seen = []
    monkeypatch.setattr(
        frame, "_submit_question",
        lambda question, **kwargs: seen.append((question, kwargs, frame.active_claudecode_session_id, chat["claudecode_session_id"])) or (True, ""),
    )
    payload = main.AnswerViewerPayload("chat-owner", 0, "turn", "captured-session", model, "canonical")
    assert frame._continue_from_answer_text_viewer(payload) is True
    assert seen[0][2:] == ("captured-session", "captured-session")


@pytest.mark.parametrize("outcome", ["false", "exception"])
def test_answer_viewer_submit_failure_is_atomic(frame, monkeypatch, outcome):
    model = main.DEFAULT_CODEX_MODEL
    turn = {"question": "q", "answer_md": "canonical", "model": model, "codex_thread_id": "captured-thread", "codex_turn_id": "captured-turn"}
    frame.active_chat_id = frame.current_chat_id = "chat-current"
    frame.active_session_turns = [turn]
    frame._current_chat_state = {"id": "chat-current", "model": model, "turns": frame.active_session_turns, "codex_thread_id": "latest-thread", "codex_turn_id": "latest-turn"}
    frame.active_codex_thread_id = "active-thread"
    frame.active_codex_turn_id = "active-turn"
    before = (copy.deepcopy(frame._current_chat_state), frame.active_codex_thread_id, frame.active_codex_turn_id, frame.view_mode, frame.input_edit.GetValue())

    def fail(*_args, **_kwargs):
        frame._current_chat_state["model"] = "mutated"
        frame.active_session_turns.append({"answer_md": "mutated"})
        frame.view_mode = "history"
        frame.input_edit.SetValue("mutated")
        if outcome == "exception":
            raise RuntimeError("boom")
        return False, "failed"

    monkeypatch.setattr(frame, "_submit_question", fail)
    payload = main.AnswerViewerPayload("chat-current", 0, "captured-turn", "captured-thread", model, "canonical")
    assert frame._continue_from_answer_text_viewer(payload) is False
    assert (frame._current_chat_state, frame.active_codex_thread_id, frame.active_codex_turn_id, frame.view_mode, frame.input_edit.GetValue()) == before


def test_answer_viewer_identical_content_replacement_without_stable_id_is_stale(frame, monkeypatch):
    model = "openai/gpt-5.2"
    frame.archived_chats = [{"id": "chat", "model": model, "turns": [{"question": "new", "answer_md": "same", "model": model}]}]
    payload = main.AnswerViewerPayload("chat", 0, "turn:0", "", model, "same")
    monkeypatch.setattr(frame, "_submit_question", lambda *_a, **_k: pytest.fail("synthetic identity must not dispatch"))
    assert frame._continue_from_answer_text_viewer(payload) is False


@pytest.mark.parametrize("signal", ["pending", "codex_active", "kimi_active", "kimi_queue", "frame_active", "claude_active"])
def test_answer_viewer_busy_signals_are_noops(frame, monkeypatch, signal):
    if signal.startswith("kimi"):
        model, session_key, turn_key = main.DEFAULT_KIMI_MODEL, "kimi_session_id", "kimi_turn_id"
    elif signal == "claude_active":
        model, session_key, turn_key = main.DEFAULT_CLAUDECODE_MODEL, "claudecode_session_id", "turn_id"
    else:
        model, session_key, turn_key = main.DEFAULT_CODEX_MODEL, "codex_thread_id", "codex_turn_id"
    turn = {"question": "q", "answer_md": "canonical", "model": model, session_key: "session", turn_key: "turn"}
    chat = {"id": "chat", "model": model, "turns": [turn], session_key: "session"}
    frame.archived_chats = [chat]
    if signal == "pending": turn["request_status"] = "pending"
    elif signal == "codex_active": chat["codex_turn_active"] = True
    elif signal == "kimi_active": chat["kimi_turn_active"] = True
    elif signal == "kimi_queue": chat["kimi_request_queue"] = [{}]
    elif signal == "frame_active":
        frame.active_chat_id = frame.current_chat_id = "chat"
        frame._current_chat_state = chat
        frame.active_session_turns = chat["turns"]
        frame._active_request_count = 1
    else:
        frame._active_claudecode_client = object()
        frame._active_claudecode_chat_id = "chat"
    before = copy.deepcopy(chat)
    monkeypatch.setattr(frame, "_submit_question", lambda *_a, **_k: pytest.fail("busy owner must not submit"))
    payload = main.AnswerViewerPayload("chat", 0, "turn", "session", model, "canonical")
    assert frame._continue_from_answer_text_viewer(payload) is False
    assert chat == before


def test_selected_answer_open_passes_full_payload_and_enables_continue(frame, monkeypatch):
    turn = {"question": "q", "answer_md": "canonical", "model": main.DEFAULT_CODEX_MODEL,
            "codex_thread_id": "thread", "codex_turn_id": "turn"}
    frame.active_chat_id = frame.current_chat_id = "chat"
    frame.active_session_turns = [turn]
    frame._current_chat_state = {"id": "chat", "model": main.DEFAULT_CODEX_MODEL, "turns": frame.active_session_turns}
    frame.answer_meta = [("answer", 0, "plain", "detail")]
    frame.answer_list.Set(["answer"])
    frame.answer_list.SetSelection(0)
    opened = []
    monkeypatch.setattr(frame, "_open_answer_text_viewer", lambda title, text, payload: opened.append((title, text, payload)) or True)

    assert frame._open_selected_answer_text_viewer() is True
    assert opened[0][1] == "canonical"
    assert opened[0][2] == main.AnswerViewerPayload("chat", 0, "turn", "thread", main.DEFAULT_CODEX_MODEL, "canonical")


def test_answer_viewer_duplicate_guard_is_owner_scoped_and_allows_different_answer(frame):
    frame.active_chat_id = "chat"
    frame.answer_meta = [("answer", 0, "a", "a"), ("answer", 1, "b", "b")]
    frame.answer_list.Set(["a", "b"])
    frame.answer_list.SetSelection(0)
    frame._answer_viewer_duplicate_owner = frame._answer_viewer_selection_signature()
    frame._answer_viewer_duplicate_until = time.monotonic() + 1
    frame._answer_viewer_duplicate_events = 2
    assert frame._consume_answer_viewer_duplicate_event() is True
    frame.answer_list.SetSelection(1)
    assert frame._consume_answer_viewer_duplicate_event() is False


def test_answer_viewer_open_overlap_and_modal_exception_recover(frame, monkeypatch):
    frame._answer_viewer_open = True
    assert frame._open_answer_text_viewer("title", "one") is False
    frame._answer_viewer_open = False

    class Broken:
        def __init__(self, *_a, **_k): pass
        def ShowModal(self): raise RuntimeError("modal failed")
        def Destroy(self): pass

    monkeypatch.setattr(main, "AnswerTextViewerDialog", Broken)
    assert frame._open_answer_text_viewer("title", "one") is False
    assert frame._answer_viewer_open is False


def test_answer_viewer_dialog_callback_false_then_success_and_exception(frame):
    payload = main.AnswerViewerPayload("chat", 0, "turn", "session", main.DEFAULT_CODEX_MODEL, "canonical")
    outcomes = [False, RuntimeError("boom"), True]

    def callback(_payload):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    dlg = main.AnswerTextViewerDialog(frame, "title", payload=payload, on_continue=callback)
    try:
        dlg.Show()
        dlg._finish = lambda _code: None
        assert dlg._request_continue() is False
        assert dlg.continue_button.IsEnabled()
        assert dlg._request_continue() is False
        assert dlg.continue_button.IsEnabled()
        assert dlg._request_continue() is True
        assert not dlg.continue_button.IsEnabled()
    finally:
        dlg.Destroy()


def test_try_open_selected_answer_detail_opens_attachment_path(frame, monkeypatch, tmp_path):
    attachment = tmp_path / "report.txt"
    attachment.write_text("attachment body", encoding="utf-8")
    frame.answer_meta = [("attachment", 0, "report.txt", str(attachment))]
    frame.answer_list.Append("report.txt")
    frame.answer_list.SetSelection(0)
    opened = {}
    monkeypatch.setattr(main.os, "startfile", lambda path: opened.__setitem__("path", path), raising=False)

    assert frame._try_open_selected_answer_detail()
    assert opened["path"] == str(attachment)


def test_render_answer_list_shows_uploaded_and_received_attachment_rows(frame, tmp_path):
    uploaded = tmp_path / "uploaded.png"
    uploaded.write_text("uploaded", encoding="utf-8")
    received = tmp_path / "received.txt"
    received.write_text("received", encoding="utf-8")
    frame.active_session_turns = [
        {
            "question": "uploaded.png 图片已成功上传",
            "answer_md": "",
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": time.time(),
            "suppress_empty_answer_row": True,
            "attachments": [
                {
                    "name": "uploaded.png",
                    "path": str(uploaded),
                    "kind": "image",
                    "direction": "outgoing",
                    "status": "success",
                    "open_path": str(uploaded),
                }
            ],
            "received_attachments": [
                {
                    "name": "received.txt",
                    "path": str(received),
                    "kind": "file",
                    "direction": "incoming",
                    "status": "success",
                    "open_path": str(received),
                }
            ],
        }
    ]
    frame.view_mode = "active"

    frame._render_answer_list()

    rows = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    meta_types = [meta[0] for meta in frame.answer_meta]
    assert "图片上传成功" in rows
    assert "uploaded.png 图片已成功上传" not in rows
    assert "已上传：uploaded.png" not in rows
    assert "CLI 发来文件：received.txt" in rows
    assert meta_types.count("attachment") == 2


def test_render_answer_list_shows_each_uploaded_attachment_on_its_own_line(frame, tmp_path):
    image_one = tmp_path / "one.png"
    image_two = tmp_path / "two.png"
    file_one = tmp_path / "alpha.txt"
    file_two = tmp_path / "beta.txt"
    for path in (image_one, image_two, file_one, file_two):
        path.write_text(path.name, encoding="utf-8")
    frame.active_session_turns = [
        {
            "question": "",
            "answer_md": "",
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": time.time(),
            "suppress_empty_answer_row": True,
            "attachments": [
                {"name": image_one.name, "path": str(image_one), "kind": "image", "direction": "outgoing", "status": "success", "open_path": str(image_one)},
                {"name": image_two.name, "path": str(image_two), "kind": "image", "direction": "outgoing", "status": "success", "open_path": str(image_two)},
                {"name": file_one.name, "path": str(file_one), "kind": "file", "direction": "outgoing", "status": "success", "open_path": str(file_one)},
                {"name": file_two.name, "path": str(file_two), "kind": "file", "direction": "outgoing", "status": "success", "open_path": str(file_two)},
            ],
        }
    ]

    frame._render_answer_list()

    rows = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert frame.answer_meta[0][0] == "time"
    assert rows[1:] == ["我", "图片上传成功", "图片上传成功", "alpha.txt 上传成功", "beta.txt 上传成功"]


def test_render_answer_list_keeps_standard_qa_structure_for_attachment_only_turn(frame, tmp_path):
    image_path = tmp_path / "only-image.png"
    image_path.write_text("img", encoding="utf-8")
    frame.active_session_turns = [
        {
            "question": "",
            "answer_md": "cli 的回答",
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": time.time(),
            "attachments": [
                {
                    "name": image_path.name,
                    "path": str(image_path),
                    "kind": "image",
                    "direction": "outgoing",
                    "status": "success",
                    "open_path": str(image_path),
                }
            ],
        }
    ]

    frame._render_answer_list()

    rows = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    meta_types = [meta[0] for meta in frame.answer_meta]
    assert rows[1:] == ["我", "图片上传成功", "小诸葛", "cli 的回答"]
    assert meta_types == ["time", "user", "attachment", "ai", "answer"]


def test_update_active_answer_row_skips_repaint_when_text_is_unchanged(frame, monkeypatch):
    frame.view_mode = "active"
    frame.active_session_turns = [
        {
            "question": "",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": time.time(),
            "request_status": "pending",
        }
    ]
    frame.answer_list.Append(main.REQUESTING_TEXT)
    frame.answer_meta = [("answer", 0, main.REQUESTING_TEXT, main.REQUESTING_TEXT)]
    frame._active_answer_row_index = 0
    seen = {"repaint": 0}
    monkeypatch.setattr(frame, "_request_listbox_repaint", lambda *_controls: seen.__setitem__("repaint", seen["repaint"] + 1))

    assert frame._update_active_answer_row(0) is True
    assert seen["repaint"] == 0


def test_answer_list_render_populates_incremental_model(frame):
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.active_session_turns = [
        {"question": "q", "answer_md": "answer", "model": main.DEFAULT_CODEX_MODEL}
    ]
    frame._current_chat_state = {"id": "chat-active", "turns": frame.active_session_turns, "detail_panel_mode": "answers"}

    frame._render_answer_list(refresh_execution=False)

    assert frame.answer_list_model.visible_ids
    assert len(frame.answer_list_model.visible_ids) == frame.answer_list.GetCount()


def test_final_answer_updates_existing_pending_answer_row_without_clearing(frame, monkeypatch):
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {"question": "q", "answer_md": main.REQUESTING_TEXT, "model": main.DEFAULT_CODEX_MODEL}
    ]
    frame._current_chat_state = {"id": "chat-active", "turns": frame.active_session_turns, "detail_panel_mode": "answers"}
    frame._render_answer_list(refresh_execution=False)
    monkeypatch.setattr(frame.answer_list, "Clear", lambda: pytest.fail("final answer must not clear answer list"))

    frame._on_done(0, "final answer", "", main.DEFAULT_CODEX_MODEL, "", "chat-active")

    labels = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert "final answer" in labels
    assert len(frame.answer_list_model.visible_ids) == frame.answer_list.GetCount()


def test_completion_focus_does_not_return_from_execution_to_answer_list(frame, monkeypatch):
    frame.Show()
    frame.active_chat_id = "chat-execution-completion"
    frame.current_chat_id = "chat-execution-completion"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {"question": "问题", "answer_md": main.REQUESTING_TEXT, "model": "openai/gpt-5.2", "created_at": time.time()}
    ]
    frame._current_chat_state = {
        "id": "chat-execution-completion",
        "title": "执行模式完成",
        "turns": frame.active_session_turns,
        "detail_panel_mode": "execution",
        "execution_steps": [{"step": "执行中"}],
    }
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_refresh_history", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_final_answer", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_play_finish_sound", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_can_focus_completion_result", lambda: True)
    monkeypatch.setattr(main.wx, "CallLater", lambda _delay, fn, *args, **kwargs: fn(*args, **kwargs))
    immediate_call_later = frame._call_later_if_alive

    class DeferredDeadline:
        def __init__(self, delay, callback, args, kwargs):
            self.delay = delay
            self.callback = callback
            self.args = args
            self.kwargs = kwargs
            self.running = True

        def Stop(self):
            self.running = False

        def IsRunning(self):
            return self.running

    def call_later(delay, callback, *args, **kwargs):
        if callback == frame._flush_accepted_answer_refresh:
            return DeferredDeadline(delay, callback, args, kwargs)
        return immediate_call_later(delay, callback, *args, **kwargs)

    monkeypatch.setattr(frame, "_call_later_if_alive", call_later)
    frame._apply_detail_panel_mode("execution", refresh_execution=True)
    frame.execution_list.SetFocus()

    frame._on_done(0, "最终回答", "", "openai/gpt-5.2", "", "chat-execution-completion")

    assert frame._current_chat_state["detail_panel_mode"] == "execution"
    assert frame.execution_list.IsShown()
    assert frame.execution_list.HasFocus()
    assert not frame.answer_list.HasFocus()


def test_on_done_appends_final_answer_without_refreshing_or_reselecting_old_answer(frame, monkeypatch):
    frame.Show()
    frame.active_chat_id = "chat-answer-append"
    frame.current_chat_id = "chat-answer-append"
    frame.active_turn_idx = 1
    frame.active_session_turns = [
        {"question": "上一条问题", "answer_md": "上一条回复", "model": "openai/gpt-5.2", "created_at": 1.0},
        {"question": "当前问题", "answer_md": main.REQUESTING_TEXT, "model": "openai/gpt-5.2", "created_at": 2.0},
    ]
    frame._current_chat_state.update({"id": "chat-answer-append", "turns": frame.active_session_turns})
    frame._render_answer_list()
    old_answer_row = next(idx for idx, meta in enumerate(frame.answer_meta) if meta[0] == "answer" and meta[1] == 0)
    frame.answer_list.SetSelection(old_answer_row)

    operations = []
    original_append = frame.answer_list.Append

    def record_append(label):
        operations.append(("Append", label))
        return original_append(label)

    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_refresh_history", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_final_answer", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_play_finish_sound", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_refresh_answer_list_preserving_selection", lambda *args, **kwargs: pytest.fail("completion should append final answer, not refresh answer list"))
    focused = []
    monkeypatch.setattr(frame, "_focus_latest_answer", lambda: focused.append(True))
    monkeypatch.setattr(frame.answer_list, "Append", record_append)
    monkeypatch.setattr(frame.answer_list, "SetSelection", lambda *args, **kwargs: pytest.fail("completion should not reselect old answer"))
    monkeypatch.setattr(frame.answer_list, "Refresh", lambda *args, **kwargs: pytest.fail("completion should not refresh answer list"))

    frame._on_done(1, "最终回答", "", "openai/gpt-5.2", "", "chat-answer-append")

    assert operations == [("Append", "小诸葛"), ("Append", "最终回答")]
    rows = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert rows[-2:] == ["小诸葛", "最终回答"]
    assert frame.answer_meta[-2:] == [("ai", 1, "小诸葛", ""), ("answer", 1, "最终回答", "最终回答")]
    assert focused == [True]


def test_answer_list_typing_redirects_after_committed_chars(frame):
    frame.input_edit.SetValue("")
    frame._queue_answer_char_redirect("好")
    frame._queue_answer_char_redirect("的")
    frame._flush_answer_committed_buffer_to_input()
    assert frame.input_edit.GetValue().endswith("好的")


def test_answer_list_non_printable_does_not_redirect(frame):
    class E:
        def GetKeyCode(self):
            return wx.WXK_UP

        def GetUnicodeKey(self):
            return wx.WXK_UP

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            pass

    assert frame._extract_committed_char(E()) == ""


def test_answer_list_ascii_process_keys_do_not_redirect(frame):
    class E:
        def GetKeyCode(self):
            return ord("h")

        def GetUnicodeKey(self):
            return ord("h")

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            pass

    assert frame._extract_committed_char(E()) == ""


def test_streaming_delta_defers_state_save(frame, monkeypatch):
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.active_session_turns = [{"question": "q", "answer_md": main.REQUESTING_TEXT, "model": "openai/gpt-5.2"}]
    frame._current_chat_state = {"id": "chat-active", "turns": frame.active_session_turns}
    immediate_saves = []
    deferred_saves = []
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: immediate_saves.append(kwargs))
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: deferred_saves.append(True))

    frame._on_delta_for_chat(0, "a", "chat-active")
    frame._on_delta_for_chat(0, "b", "chat-active")

    assert immediate_saves == []
    assert deferred_saves == [True, True]
    assert frame.active_session_turns[0]["answer_md"] == "ab"


def test_submit_question_appends_answer_rows_without_clearing_existing_answer_list(frame, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda *args, **kwargs: None)

    class _NoOpThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            pass

        def start(self):
            pass

    monkeypatch.setattr(main.threading, "Thread", _NoOpThread)
    frame._render_answer_list()
    assert frame.answer_list.GetString(0) == "暂无"
    monkeypatch.setattr(frame.answer_list, "Clear", lambda: pytest.fail("send should not clear answer list"))

    ok, message = frame._submit_question("马上显示的问题", source="local", model="openai/gpt-5.2")

    assert ok is True
    assert message == ""
    rows = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert "马上显示的问题" in rows


def test_submit_question_appends_new_question_without_refreshing_answer_list(frame, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda *args, **kwargs: None)

    class _NoOpThread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None):
            pass

        def start(self):
            pass

    monkeypatch.setattr(main.threading, "Thread", _NoOpThread)
    frame.active_session_turns = [
        {"question": "上一条问题", "answer_md": "上一条回复", "model": "openai/gpt-5.2", "created_at": 1.0}
    ]
    frame._current_chat_state["turns"] = frame.active_session_turns
    frame._render_answer_list()
    old_answer_row = next(idx for idx, meta in enumerate(frame.answer_meta) if meta[0] == "answer")
    frame.answer_list.SetSelection(old_answer_row)

    operations = []
    original_append = frame.answer_list.Append

    def record_append(label):
        operations.append(("Append", label))
        return original_append(label)

    monkeypatch.setattr(frame, "_render_answer_list", lambda *args, **kwargs: pytest.fail("send should append the new question, not refresh answer list"))
    monkeypatch.setattr(frame, "_render_answer_list_compat", lambda *args, **kwargs: pytest.fail("send should append the new question, not refresh answer list"))
    monkeypatch.setattr(frame.answer_list, "Append", record_append)
    monkeypatch.setattr(frame.answer_list, "SetSelection", lambda *args: pytest.fail("send should not reselect the old answer row"))
    monkeypatch.setattr(frame.answer_list, "Refresh", lambda *args, **kwargs: pytest.fail("send should not refresh answer list"))

    ok, message = frame._submit_question("新问题", source="local", model="openai/gpt-5.2")

    assert ok is True
    assert message == ""
    time_label = main.wechat_time_label(frame.active_session_turns[1]["created_at"], time.time())
    assert operations == [("Append", time_label), ("Append", "我"), ("Append", "新问题")]
    rows = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert rows[-3:] == [time_label, "我", "新问题"]
    assert frame.answer_meta[-3:] == [("time", 1, time_label, ""), ("user", 1, "我", ""), ("question", 1, "新问题", "")]


def test_claudecode_done_does_not_rerender_ui_while_cli_runs(frame, monkeypatch):
    frame.active_chat_id = "chat-new"
    frame.active_session_turns = [
        {"question": "旧问题", "answer_md": "", "model": "claudecode/default", "created_at": 1.0}
    ]
    frame.active_turn_idx = 0
    frame._active_request_count = 1
    rendered = {"count": 0}
    focused = {"count": 0}
    monkeypatch.setattr(frame, "_render_answer_list", lambda: rendered.__setitem__("count", rendered["count"] + 1))
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_set_input_hint_idle", lambda: None)
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)
    monkeypatch.setattr(main.wx, "CallLater", lambda _delay, fn, *args, **kwargs: fn(*args, **kwargs))
    monkeypatch.setattr(frame, "_focus_latest_answer", lambda: focused.__setitem__("count", focused["count"] + 1))

    frame._on_done(0, "Claude Code 回答", "", "claudecode/default", "", "chat-new")

    assert frame.active_session_turns[0]["answer_md"] == "Claude Code 回答"
    assert rendered["count"] == 1
    assert focused["count"] == 1


def test_missing_background_delta_does_not_update_visible_chat(frame, monkeypatch):
    frame.active_chat_id = "chat-visible"
    frame.current_chat_id = "chat-visible"
    frame.active_session_turns = [
        {
            "question": "visible question",
            "answer_md": "visible answer",
            "model": "openai/gpt-5.2",
            "created_at": 2.0,
        }
    ]
    frame._current_chat_state.update({"id": "chat-visible", "turns": frame.active_session_turns})
    frame.archived_chats = []
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: pytest.fail("visible chat should not be saved"))

    frame._on_delta(0, "late background delta", "chat-missing")

    assert frame.active_session_turns[0]["answer_md"] == "visible answer"


def test_missing_background_done_does_not_update_visible_chat(frame, monkeypatch):
    frame.active_chat_id = "chat-visible"
    frame.current_chat_id = "chat-visible"
    frame.active_session_turns = [
        {
            "question": "visible question",
            "answer_md": "visible answer",
            "model": "openai/gpt-5.2",
            "created_at": 2.0,
            "request_status": "done",
        }
    ]
    frame._current_chat_state.update({"id": "chat-visible", "turns": frame.active_session_turns})
    frame.archived_chats = []
    monkeypatch.setattr(frame, "_push_remote_state", lambda *args, **kwargs: pytest.fail("missing background chat should not push remote state"))
    monkeypatch.setattr(frame, "_push_remote_final_answer", lambda *args, **kwargs: pytest.fail("missing background chat should not push final answer"))
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *args, **kwargs: pytest.fail("missing background chat should not push history"))
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: pytest.fail("missing background chat should not save active state"))
    monkeypatch.setattr(frame, "_refresh_history", lambda *args, **kwargs: pytest.fail("missing background chat should not refresh history"))
    monkeypatch.setattr(frame, "_refresh_answer_list_preserving_selection", lambda *args, **kwargs: pytest.fail("visible chat should not rerender"))
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)

    frame._on_done(0, "late background final", "", "openai/gpt-5.2", "", "chat-missing")

    assert frame.active_session_turns[0]["answer_md"] == "visible answer"


def test_render_answer_list_hides_blank_user_for_assistant_only_turn(frame):
    frame.active_session_turns = [
        {"question": "", "answer_md": "只有回答", "model": "codex/main", "created_at": time.time()}
    ]

    frame._render_answer_list()

    items = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert frame.answer_meta[0][0] == "time"
    assert items[1:] == ["小诸葛", "只有回答"]


def test_render_answer_list_filters_codex_file_tokens_when_enabled(frame):
    frame.codex_answer_english_filter_enabled = True
    frame.active_session_turns = [
        {
            "question": "测试",
            "answer_md": "请检查 C:/code/codex/main.py 和 test_render_answer_list_filters_codex_file_tokens_when_enabled",
            "model": "codex/main",
            "created_at": time.time(),
        }
    ]

    frame._render_answer_list()

    items = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert any("[文件路径]" in item for item in items)
    assert all("main.py" not in item for item in items)
    assert all("test_render_answer_list_filters_codex_file_tokens_when_enabled" not in item for item in items)


def test_render_answer_list_filters_pytest_command_and_test_paths(frame):
    frame.codex_answer_english_filter_enabled = True
    frame.active_session_turns = [
        {
            "question": "测试",
            "answer_md": '已执行并通过：\npytest tests/test_voice_input_ui_automation.py tests/test_voice_input_e2e.py -k "voice or realtime_asr"\n请查看 tests/test_voice_input_ui_automation.py',
            "model": "codex/main",
            "created_at": time.time(),
        }
    ]

    frame._render_answer_list()

    items = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    answer_rows = [item for item in items if item not in ("我", "小诸葛", "测试")]
    assert any("已执行并通过：" in item for item in answer_rows)
    assert all("pytest tests/" not in item for item in answer_rows)
    assert all("test_voice_input_ui_automation.py" not in item for item in answer_rows)


def test_render_answer_list_does_not_filter_non_codex_answers(frame):
    frame.codex_answer_english_filter_enabled = True
    frame.active_session_turns = [
        {
            "question": "测试",
            "answer_md": "请检查 C:/code/codex/main.py 和 test_render_answer_list_filters_codex_file_tokens_when_enabled",
            "model": "openai/gpt-5.2",
            "created_at": time.time(),
        }
    ]

    frame._render_answer_list()

    items = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert any("main.py" in item for item in items)
    assert any("test_render_answer_list_filters_codex_file_tokens_when_enabled" in item for item in items)


@pytest.mark.parametrize("model", ["codex/main", "claudecode/default", "openclaw/main"])
def test_answer_detail_html_filters_cli_models(frame, model):
    frame.codex_answer_english_filter_enabled = True

    codex_html = frame._build_answer_detail_html(
        "请查看 C:/code/codex/main.py 和 test_answer_detail_html_filters_cli_models",
        model,
    )
    assert "[文件路径]" in codex_html
    assert "main.py" not in codex_html
    assert "test_answer_detail_html_filters_cli_models" not in codex_html

    other_html = frame._build_answer_detail_html(
        "请查看 C:/code/codex/main.py 和 test_answer_detail_html_filters_cli_models",
        "openai/gpt-5.2",
    )
    assert "main.py" in other_html
    assert "test_answer_detail_html_filters_cli_models" in other_html


def test_codex_answer_filter_replaces_markdown_file_links(frame):
    frame.codex_answer_english_filter_enabled = True

    text = frame._answer_markdown_for_output(
        "请查看 [main.py](/c:/code/codex/main.py#L1098) 和 `tests/test_main_unit.py`",
        "codex/main",
    )

    assert "[文件路径]" in text
    assert "main.py" not in text
    assert "test_main_unit.py" not in text


@pytest.mark.parametrize("model", ["claudecode/default", "openclaw/main"])
def test_answer_filter_applies_to_other_cli_models(frame, model):
    frame.codex_answer_english_filter_enabled = True

    text = frame._answer_markdown_for_output(
        "请查看 [main.py](/c:/code/codex/main.py#L1098) 和 `tests/test_main_unit.py`",
        model,
    )

    assert "[文件路径]" in text
    assert "main.py" not in text
    assert "test_main_unit.py" not in text


def test_startup_shows_last_active_turns_in_answer_list(monkeypatch, tmp_path):
    state = {
        "selected_model_id": "openai/gpt-5.2-chat",
        "archived_chats": [],
        "active_session_turns": [
            {
                "question": "上次问题",
                "answer_md": "上次回答",
                "model": "codex/main",
                "created_at": 1.0,
            }
        ],
        "active_session_started_at": 1.0,
    }
    (tmp_path / "app_state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(main, "resolve_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(main.ChatFrame, "_legacy_state_paths", lambda self: [self.state_path])
    f = main.ChatFrame()
    try:
        assert f.selected_model == "codex/main"
        assert f.answer_list.GetCount() >= 1
        items = [f.answer_list.GetString(i) for i in range(f.answer_list.GetCount())]
        assert any("上次回答" in item for item in items)
    finally:
        f.Destroy()


@pytest.mark.parametrize(
    ("request_status", "request_error", "expected"),
    [
        ("failed", "", "上次未完成回答恢复失败，可手动继续"),
        ("failed", "boom", "boom"),
    ],
)
def test_turn_answer_markdown_shows_recovery_status_for_empty_answer(frame, request_status, request_error, expected):
    answer_md, clean_answer_md = frame._turn_answer_markdown(
        {
            "question": "q",
            "answer_md": "",
            "model": "openai/gpt-5.2",
            "request_status": request_status,
            "request_error": request_error,
        }
    )

    assert answer_md == ""
    assert clean_answer_md == expected


def test_render_answer_list_shows_recovery_status_for_empty_answer(frame):
    frame.active_session_turns = [
        {
            "question": "失败的问题",
            "answer_md": "",
            "model": "openai/gpt-5.2",
            "created_at": 1.0,
            "request_status": "failed",
            "request_error": "boom",
        },
    ]

    frame._render_answer_list()

    items = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert "boom" in items
    assert all("正在恢复上次未完成的回答" not in item for item in items)


def test_on_done_marks_pending_turn_done(frame):
    frame.active_session_turns = [
        {
            "question": "待完成问题",
            "answer_md": "",
            "model": "openai/gpt-5.2",
            "created_at": 1.0,
            "request_status": "pending",
            "request_recoverable": True,
            "request_model": "openai/gpt-5.2",
            "request_question": "待完成问题",
            "request_last_attempt_at": 4567.0,
            "request_attempt_count": 2,
            "request_error": "stale",
            "request_recovered_after_restart": False,
        }
    ]

    frame._on_done(0, "完成", "", "openai/gpt-5.2", "", frame.active_chat_id)

    turn = frame.active_session_turns[0]
    assert turn["request_status"] == "done"
    assert turn["request_error"] == ""
    assert turn["request_recovered_after_restart"] is False


def test_on_done_marks_pending_turn_failed_and_sets_request_error(frame):
    frame.active_session_turns = [
        {
            "question": "待失败问题",
            "answer_md": "",
            "model": "openai/gpt-5.2",
            "created_at": 1.0,
            "request_status": "pending",
            "request_recoverable": True,
            "request_model": "openai/gpt-5.2",
            "request_question": "待失败问题",
            "request_last_attempt_at": 4567.0,
            "request_attempt_count": 2,
            "request_error": "",
            "request_recovered_after_restart": False,
        }
    ]

    frame._on_done(0, "", "boom", "openai/gpt-5.2", "", frame.active_chat_id)

    turn = frame.active_session_turns[0]
    assert turn["request_status"] == "failed"
    assert turn["request_error"] == "boom"
    assert turn["request_recovered_after_restart"] is False


def test_sanitize_optimized_text_removes_markdown_and_blank_lines():
    src = "# 标题\n\n- 第一条\n- 第二条\n\n**加粗** `代码` 😀\n"
    out = main.sanitize_optimized_text(src)
    assert "#" not in out
    assert "- " not in out
    assert "**" not in out
    assert "`" not in out
    assert "😀" not in out
    assert "\n\n" not in out


def test_turn_model_updates_to_actual_used_model_on_fallback(frame):
    frame.active_session_turns = [{"question": "q", "answer_md": "", "model": "deepseek/deepseek-r1-0528-qwen3-8b", "created_at": 1}]
    frame._on_done(0, "ok", "", "deepseek/deepseek-r1-0528", "模型 x 当前不可用，已回退到 y")
    assert frame.active_session_turns[0]["model"] == "deepseek/deepseek-r1-0528"


def test_answer_list_defaults_to_latest_100_rows_and_shows_more(frame):
    frame.active_session_turns = [
        {"question": f"q {idx}", "answer_md": f"a {idx}", "model": "openai/gpt-5.2", "created_at": float(idx)}
        for idx in range(60)
    ]
    frame._current_chat_state = {
        "id": "chat-current",
        "turns": frame.active_session_turns,
        "detail_panel_mode": "answers",
    }

    frame._render_answer_list()

    visible = [frame.answer_list.GetString(idx) for idx in range(frame.answer_list.GetCount())]
    assert visible[0] == "更多"
    assert frame.answer_meta[0] == ("more", -1, "更多", "")
    assert "q 0" not in visible
    assert "q 59" in visible
    assert frame.answer_total_content_rows > 100


def test_render_answer_list_does_not_format_all_old_turns_when_bounded(frame, monkeypatch):
    frame.active_session_turns = [
        {"question": f"q {idx}", "answer_md": f"a {idx}", "model": "openai/gpt-5.2", "created_at": float(idx)}
        for idx in range(500)
    ]
    frame._current_chat_state = {"id": "chat-current", "turns": frame.active_session_turns, "detail_panel_mode": "answers"}
    formatted = []
    original = frame._turn_answer_markdown

    def _counting_markdown(turn):
        formatted.append(turn["question"])
        return original(turn)

    monkeypatch.setattr(frame, "_turn_answer_markdown", _counting_markdown)

    frame._render_answer_list()

    assert len(formatted) <= frame.answer_visible_row_limit
    visible = [frame.answer_list.GetString(idx) for idx in range(frame.answer_list.GetCount())]
    assert visible[0] == "更多"
    assert "q 0" not in visible
    assert "q 499" in visible


def test_answer_list_more_item_expands_upward_and_hides_when_all_rows_visible(frame):
    frame.active_session_turns = [
        {"question": f"q {idx}", "answer_md": f"a {idx}", "model": "openai/gpt-5.2", "created_at": float(idx)}
        for idx in range(60)
    ]
    frame._current_chat_state = {
        "id": "chat-current",
        "turns": frame.active_session_turns,
        "detail_panel_mode": "answers",
    }
    frame._render_answer_list()
    frame.answer_list.SetSelection(0)

    assert frame._try_open_selected_answer_detail() is True
    visible_after_one = [frame.answer_list.GetString(idx) for idx in range(frame.answer_list.GetCount())]
    assert "q 10" in visible_after_one
    assert "q 0" not in visible_after_one
    assert visible_after_one[0] == "更多"

    frame.answer_list.SetSelection(0)
    assert frame._try_open_selected_answer_detail() is True
    visible_after_two = [frame.answer_list.GetString(idx) for idx in range(frame.answer_list.GetCount())]
    assert "q 0" in visible_after_two
    assert "更多" not in visible_after_two


def test_render_answer_list_does_not_schedule_context_usage_estimate(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_session_turns = [{"question": "q", "answer_md": "a", "model": "openai/gpt-5.2"}]
    frame._current_chat_state = {"id": "chat-current", "turns": frame.active_session_turns}
    scheduled = []
    monkeypatch.setattr(main, "estimate_turns_tokens", lambda *args, **kwargs: pytest.fail("rendering must not estimate context usage on UI thread"))
    monkeypatch.setattr(
        frame,
        "_schedule_context_usage_estimate",
        lambda chat, turns, turn_idx, model: scheduled.append((chat.get("id"), turn_idx, model)),
    )

    frame._render_answer_list()

    assert any(meta[0] == "user" and meta[2] == "我" for meta in frame.answer_meta)
    assert scheduled == []


def test_render_answer_list_does_not_clear_when_rows_are_unchanged(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_session_turns = [{"question": "q", "answer_md": "a", "model": "openai/gpt-5.2"}]
    frame._current_chat_state = {
        "id": "chat-current",
        "turns": frame.active_session_turns,
        "context_usage": {
            "used_tokens": 1500,
            "context_window": 128000,
            "source": "estimated",
            "exact": False,
            "fresh": True,
            "model": "openai/gpt-5.2",
        },
    }
    frame._render_answer_list()
    monkeypatch.setattr(frame.answer_list, "Clear", lambda: pytest.fail("unchanged answer rows should not rebuild the listbox"))

    frame._render_answer_list()


def test_append_submitted_question_removes_all_initial_empty_rows_with_incremental_model(frame):
    frame.active_session_turns = []
    frame._current_chat_state = {"id": "chat-current", "turns": frame.active_session_turns, "context_usage": None}
    frame._render_answer_list()

    assert [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())] == ["暂无", "暂无对话内容"]

    turn = {"question": "新问题", "answer_md": main.REQUESTING_TEXT, "model": main.DEFAULT_MODEL_ID}
    frame.active_session_turns.append(turn)
    changed = frame._append_submitted_question_to_answer_list(0, turn)

    assert changed is True
    assert [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())] == [main.UNKNOWN_TIME_LABEL, "我", "新问题"]
    assert frame.answer_meta == [("time", 0, main.UNKNOWN_TIME_LABEL, ""), ("user", 0, "我", ""), ("question", 0, "新问题", "")]
    assert frame.answer_list_model.visible_ids == ["time:0", "answer:0:user", "answer:0:question"]


def test_answer_reconciliation_removes_stale_physical_tail_and_keeps_meta_aligned(frame):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_session_turns = [
        {"question": "current question", "answer_md": "current answer", "model": main.DEFAULT_MODEL_ID},
    ]
    frame._current_chat_state = {"id": "chat-current", "turns": frame.active_session_turns}
    frame._render_answer_list()

    frame.answer_list.Append("stale answer tail")
    frame.answer_meta.append(("answer", 99, "stale answer tail", "stale answer tail"))

    frame._render_answer_list()

    rows = list(frame.answer_list.GetStrings())
    assert "stale answer tail" not in rows
    assert len(rows) == len(frame.answer_meta)
    assert frame.answer_list_model.visible_ids == [frame._answer_row_id(meta) for meta in frame.answer_meta]


def test_answer_reconciliation_repairs_same_length_stale_physical_tail(frame):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_session_turns = [
        {"question": "current question", "answer_md": "current answer", "model": main.DEFAULT_MODEL_ID},
    ]
    frame._current_chat_state = {"id": "chat-current", "turns": frame.active_session_turns}
    frame._render_answer_list()
    tail_index = frame.answer_list.GetCount() - 1
    frame.answer_list.SetString(tail_index, "stale answer tail")

    frame._render_answer_list()

    assert frame.answer_list.GetString(tail_index) == "current answer"
    assert len(frame.answer_list.GetStrings()) == len(frame.answer_meta)


def test_repeated_current_answer_completion_reconciles_instead_of_duplicate_append(frame):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_session_turns = [
        {"question": "current question", "answer_md": "current answer", "model": main.DEFAULT_MODEL_ID},
    ]
    frame._current_chat_state = {"id": "chat-current", "turns": frame.active_session_turns}
    frame._render_answer_list()

    assert frame._append_completed_answer_to_answer_list(0, frame.active_session_turns[0]) is False
    assert frame._append_completed_answer_to_answer_list(0, frame.active_session_turns[0]) is False

    rows = list(frame.answer_list.GetStrings())
    assert rows.count("current answer") == 1
    assert len(rows) == len(frame.answer_meta)
    assert frame.answer_list_model.visible_ids == [frame._answer_row_id(meta) for meta in frame.answer_meta]


def test_authoritative_answer_delta_updates_one_row_without_full_rerender(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {"question": "question", "answer_md": "initial answer", "model": main.DEFAULT_MODEL_ID},
    ]
    frame._current_chat_state = {"id": "chat-current", "turns": frame.active_session_turns}
    frame._render_answer_list(refresh_execution=False)
    frame.active_session_turns[0]["answer_md"] = "streamed answer"
    renders = []
    original_render = frame._render_answer_list
    monkeypatch.setattr(
        frame,
        "_render_answer_list",
        lambda *args, **kwargs: renders.append((args, kwargs)) or original_render(*args, **kwargs),
    )
    assert frame._find_answer_row_index(0) >= 0

    assert frame._update_active_answer_row(0) is True

    assert renders == []
    row = frame._find_answer_row_index(0)
    assert row >= 0
    assert frame.answer_list.GetString(row) == "streamed answer"
