import copy
import main
import pytest
import threading
import time
import wx
from main_test_support import (
    _execution_content_ids,
    _execution_content_labels,
    _execution_row_index,
    _prepare_execution_async_history,
)
from pathlib import Path


def test_execution_list_ctrl_c_copies_plain_text_without_markdown(frame, monkeypatch):
    copied = []
    monkeypatch.setattr(frame, "_set_clipboard_text", lambda text: copied.append(text) or True)
    frame.execution_list.Set(["执行步骤"])
    frame.execution_meta = [("execution", 0, "执行步骤", "## 标题\n\n第一段 **加粗**。\n\n第二段")]
    frame.execution_list.SetSelection(0)

    class E:
        def GetKeyCode(self):
            return ord("C")

        def ControlDown(self):
            return True

        def AltDown(self):
            return False

        def StopPropagation(self):
            pass

        def Skip(self):
            raise AssertionError("Ctrl+C should be handled")

    frame._on_execution_key_down(E())

    assert copied == ["标题\n\n第一段 加粗。\n\n第二段"]


def test_submit_question_in_execution_mode_keeps_input_focus_and_current_execution_selection(frame, monkeypatch):
    frame.active_chat_id = "chat-1"
    frame.current_chat_id = "chat-1"
    frame.active_session_turns = []
    frame._current_chat_state = {
        "id": "chat-1",
        "title": "执行中发送",
        "turns": frame.active_session_turns,
        "detail_panel_mode": "execution",
        "execution_steps": [{"step": "第一步"}, {"step": "第二步"}],
    }
    frame._render_execution_list()
    frame.execution_list.SetSelection(1)
    frame.input_edit.SetFocus()
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_play_send_sound", lambda: None)
    monkeypatch.setattr(frame, "_refresh_openclaw_sync_lifecycle", lambda force_replay=False: None)
    monkeypatch.setattr(frame, "_start_codex_worker_for_turn", lambda *_args, **_kwargs: None)

    ok, message = frame._submit_question("继续观察执行过程", source="local", model=main.DEFAULT_CODEX_MODEL)

    assert ok is True
    assert message == ""
    assert frame.input_edit.HasFocus()
    assert frame.execution_list.GetSelection() == 1


def test_chat_detail_panel_f1_toggles_mode(frame, monkeypatch):
    frame.active_chat_id = "chat-1"
    frame.current_chat_id = "chat-1"
    frame._current_chat_state = {
        "id": "chat-1",
        "title": "切换测试",
        "turns": [],
        "detail_panel_mode": "answers",
        "execution_steps": [],
    }
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    visibility = {}
    monkeypatch.setattr(frame.answer_list, "Show", lambda visible: visibility.__setitem__("answer", visible))
    monkeypatch.setattr(frame.execution_list, "Show", lambda visible: visibility.__setitem__("execution", visible))
    frame._apply_detail_panel_mode()

    class E:
        def GetKeyCode(self):
            return wx.WXK_F1

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            raise AssertionError("should not skip")

    frame._on_char_hook(E())
    assert frame._current_chat_state["detail_panel_mode"] == "execution"
    assert frame.detail_title_label.GetLabel() == "执行过程："
    assert visibility["answer"] is False
    assert visibility["execution"] is True

    frame._on_char_hook(E())
    assert frame._current_chat_state["detail_panel_mode"] == "answers"
    assert frame.detail_title_label.GetLabel() == "回答："
    assert visibility["answer"] is True
    assert visibility["execution"] is False


def test_render_execution_list_shows_execution_steps(frame):
    frame._current_chat_state = {
        "id": "chat-1",
        "title": "执行测试",
        "turns": [],
        "detail_panel_mode": "execution",
        "execution_steps": [{"step": "第一步"}, {"text": "第二步"}],
    }

    frame._render_execution_list()

    rows = _execution_content_labels(frame)
    assert rows == ["第一步", "第二步"]


def test_render_execution_list_prefers_detailed_execution_step_message(frame):
    frame._current_chat_state = {
        "id": "chat-1",
        "title": "执行测试",
        "turns": [],
        "detail_panel_mode": "execution",
        "execution_steps": [
            {
                "message": "执行命令：pytest tests/test_main_unit.py -k codex",
                "step": "开始执行：commandExecution",
            }
        ],
    }

    frame._render_execution_list()

    rows = _execution_content_labels(frame)
    assert rows == ["执行命令：pytest tests/test_main_unit.py -k codex"]


def test_render_execution_list_uses_single_line_list_text_and_keeps_full_detail_meta(frame):
    frame._current_chat_state = {
        "id": "chat-1",
        "title": "执行测试",
        "turns": [],
        "detail_panel_mode": "execution",
        "execution_steps": [
            {
                "display_kind": "command",
                "detail_text": "运行测试\npytest tests/test_main_unit.py -k codex",
                "list_text": "命令：运行测试 pytest tests/test_main_unit.py -k codex",
            }
        ],
    }

    frame._render_execution_list()

    rows = _execution_content_labels(frame)
    assert rows == ["暂无执行过程"]
    assert frame.execution_meta == [("info", -1, "", "")]


def test_render_execution_list_filters_fixed_lifecycle_and_command_noise(frame):
    frame._current_chat_state = {
        "id": "chat-1",
        "title": "执行测试",
        "turns": [],
        "detail_panel_mode": "execution",
        "execution_steps": [
            {"event_type": "turn_started", "display_kind": "status", "detail_text": "开始处理本轮请求", "list_text": "开始处理本轮请求"},
            {"event_type": "item_started", "display_kind": "command", "detail_text": "开始执行：reasoning", "list_text": "命令：开始执行 reasoning"},
            {"event_type": "agent_message_delta", "display_kind": "commentary", "detail_text": "我先检查 main.py，再处理过滤逻辑。", "list_text": "我先检查 main.py，再处理过滤逻辑。"},
            {"event_type": "plan_updated", "display_kind": "plan", "detail_text": "先整理结构", "list_text": "计划：先整理结构"},
            {"event_type": "item_completed", "phase": "final_answer", "display_kind": "status", "detail_text": "已生成最终回答", "list_text": "已生成最终回答"},
            {"event_type": "turn_completed", "display_kind": "status", "detail_text": "本轮处理结束", "list_text": "本轮处理结束"},
        ],
    }

    frame._render_execution_list()

    rows = _execution_content_labels(frame)
    assert rows == ["我先检查 main.py，再处理过滤逻辑。", "计划：先整理结构"]


def test_render_execution_list_includes_question_and_final_answer_with_execution_labels(frame):
    frame.active_turn_idx = 0
    frame._current_chat_state = {
        "id": "chat-1",
        "title": "执行测试",
        "turns": [
            {
                "question": "请总结项目",
                "answer_md": "最终 **回复**",
                "model": main.DEFAULT_CODEX_MODEL,
                "created_at": 1.0,
            }
        ],
        "detail_panel_mode": "execution",
        "execution_steps": [
            {
                "event_type": "agent_message_delta",
                "display_kind": "commentary",
                "detail_text": "处理中",
                "list_text": "处理中",
                "turn_idx": 0,
            }
        ],
    }

    frame._render_execution_list()

    rows = _execution_content_labels(frame)
    assert rows == ["我：请总结项目", "处理中", "小诸葛：最终 回复"]


def test_render_execution_list_filters_phase_only_commentary_noise(frame):
    frame._current_chat_state = {
        "id": "chat-1",
        "title": "执行测试",
        "turns": [],
        "detail_panel_mode": "execution",
        "execution_steps": [
            {"event_type": "item_started", "display_kind": "commentary", "detail_text": "开始执行：阶段：commentary", "list_text": "开始执行：阶段：commentary"},
            {"event_type": "item_completed", "display_kind": "commentary", "detail_text": "完成执行：阶段：final_answer", "list_text": "完成执行：阶段：final_answer"},
            {"event_type": "agent_message_delta", "display_kind": "commentary", "detail_text": "保留这条说明。", "list_text": "保留这条说明。"},
        ],
    }

    frame._render_execution_list()

    rows = _execution_content_labels(frame)
    assert rows == ["保留这条说明。"]


def test_render_execution_list_hides_error_summary_even_after_stack_noise_is_removed(frame):
    frame._current_chat_state = {
        "id": "chat-1",
        "title": "执行测试",
        "turns": [],
        "detail_panel_mode": "execution",
        "execution_steps": [
            {
                "event_type": "stderr",
                "display_kind": "error",
                "detail_text": "Output:\nAt line:2 char:916\n+ ... lue) } }; $items = $items | Sort-Object installs -Descending\n+ ~\nMissing argument in parameter list.\nCategoryInfo: ParserError: (:) [], ParentContainsErrorRecordException\nFullyQualifiedErrorId: MissingArgument",
                "list_text": "错误：Output: At line:2 char:916 Missing argument in parameter list. CategoryInfo: ParserError: (:) [], ParentContainsErrorRecordException FullyQualifiedErrorId: MissingArgument",
            }
        ],
    }

    frame._render_execution_list()

    rows = _execution_content_labels(frame)
    assert rows == ["暂无执行过程"]
    assert frame.execution_meta == [("info", -1, "", "")]


def test_render_execution_list_hides_error_when_only_stack_noise_remains(frame):
    frame._current_chat_state = {
        "id": "chat-1",
        "title": "执行测试",
        "turns": [],
        "detail_panel_mode": "execution",
        "execution_steps": [
            {
                "event_type": "stderr",
                "display_kind": "error",
                "detail_text": "Output:\nAt line:2 char:916\n+ ~\nCategoryInfo: ParserError: (:) [], ParentContainsErrorRecordException\nFullyQualifiedErrorId: MissingArgument",
                "list_text": "错误：Output: At line:2 char:916 CategoryInfo: ParserError: (:) [], ParentContainsErrorRecordException FullyQualifiedErrorId: MissingArgument",
            }
        ],
    }

    frame._render_execution_list()

    rows = _execution_content_labels(frame)
    assert rows == ["暂无执行过程"]
    assert frame.execution_meta == [("info", -1, "", "")]


def test_render_execution_list_hides_single_line_environment_warning_noise(frame):
    frame._current_chat_state = {
        "id": "chat-1",
        "title": "执行测试",
        "turns": [],
        "detail_panel_mode": "execution",
        "execution_steps": [
            {
                "event_type": "stderr",
                "display_kind": "error",
                "detail_text": "DEPRECATION: Running PyInstaller as admin is not necessary nor sensible. Run PyInstaller from a non-administrator terminal. PyInstaller 7.0 will block this.",
                "list_text": "错误：DEPRECATION: Running PyInstaller as admin is not necessary nor sensible. Run PyInstaller from a non-administrator terminal. PyInstaller 7.0 will block this.",
            }
        ],
    }

    frame._render_execution_list()

    rows = _execution_content_labels(frame)
    assert rows == ["暂无执行过程"]
    assert frame.execution_meta == [("info", -1, "", "")]


def test_render_execution_list_hides_codex_loader_warning_noise_with_ansi(frame):
    noisy_warning = "\x1b[2m2026-05-05T03:13:03.752275Z\x1b[0m \x1b[33mWARN\x1b[0m \x1b[2mcodex_core_skills::loader\x1b[0m\x1b[2m:\x1b[0m ignoring interface metadata for skill"
    frame._current_chat_state = {
        "id": "chat-1",
        "title": "执行测试",
        "turns": [],
        "detail_panel_mode": "execution",
        "execution_steps": [
            {
                "event_type": "stderr",
                "display_kind": "error",
                "detail_text": noisy_warning,
                "list_text": f"错误：{noisy_warning}",
            }
        ],
    }

    frame._render_execution_list()

    rows = _execution_content_labels(frame)
    assert rows == ["暂无执行过程"]
    assert frame.execution_meta == [("info", -1, "", "")]


def test_render_execution_list_hides_single_line_error_prefix_entry(frame):
    frame._current_chat_state = {
        "id": "chat-1",
        "title": "执行测试",
        "turns": [],
        "detail_panel_mode": "execution",
        "execution_steps": [
            {
                "event_type": "stderr",
                "display_kind": "error",
                "detail_text": "Permission denied while opening C:/tmp/config.toml.",
                "list_text": "错误：Permission denied while opening C:/tmp/config.toml.",
            }
        ],
    }

    frame._render_execution_list()

    rows = _execution_content_labels(frame)
    assert rows == ["暂无执行过程"]
    assert frame.execution_meta == [("info", -1, "", "")]


def test_render_execution_list_hides_error_detail_even_without_stored_list_text(frame):
    frame._current_chat_state = {
        "id": "chat-1",
        "title": "执行测试",
        "turns": [],
        "detail_panel_mode": "execution",
        "execution_steps": [
            {
                "event_type": "stderr",
                "display_kind": "error",
                "detail_text": "This was caught by the test expectation on the following line:",
                "list_text": "",
            }
        ],
    }

    frame._render_execution_list()

    rows = _execution_content_labels(frame)
    assert rows == ["暂无执行过程"]
    assert frame.execution_meta == [("info", -1, "", "")]


def test_render_execution_list_rebuilds_command_entry_without_list_text_using_concise_summary(frame):
    frame._current_chat_state = {
        "id": "chat-1",
        "title": "执行测试",
        "turns": [],
        "detail_panel_mode": "execution",
        "execution_steps": [
            {
                "event_type": "item_completed",
                "display_kind": "command",
                "title": "运行测试",
                "command": "pytest tests/test_main_unit.py -k codex",
                "exit_code": 1,
                "detail_text": "运行测试\npytest tests/test_main_unit.py -k codex\n失败输出第1行\n失败输出第2行",
            }
        ],
    }

    frame._render_execution_list()

    rows = _execution_content_labels(frame)
    assert rows == ["暂无执行过程"]
    assert frame.execution_meta == [("info", -1, "", "")]


def test_render_execution_list_sets_default_selection(frame):
    frame._current_chat_state = {
        "id": "chat-1",
        "title": "执行测试",
        "turns": [],
        "detail_panel_mode": "execution",
        "execution_steps": [{"step": "第一步"}],
    }

    frame._render_execution_list()

    assert frame.execution_list.GetSelection() == 0


def test_apply_detail_panel_mode_updates_title_and_visibility(frame):
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

    frame._apply_detail_panel_mode()

    assert frame.detail_title_label.GetLabel() == "执行过程："
    assert visibility["answer"] is False
    assert visibility["execution"] is True


def test_apply_detail_panel_mode_only_rebuilds_when_entering_execution_mode(frame, monkeypatch):
    frame._current_chat_state = {
        "id": "chat-1",
        "title": "执行测试",
        "turns": [],
        "detail_panel_mode": "execution",
        "execution_steps": [{"step": "第一步"}],
    }
    render_count = {"n": 0}
    monkeypatch.setattr(frame, "_render_execution_list", lambda **kwargs: render_count.__setitem__("n", render_count["n"] + 1))

    frame._apply_detail_panel_mode("execution")
    frame._apply_detail_panel_mode("answers")
    frame._apply_detail_panel_mode("execution")

    assert render_count["n"] == 1


def test_apply_detail_panel_mode_noop_does_not_layout_or_toggle_lists(frame, monkeypatch):
    frame._current_chat_state = {
        "id": "chat-current",
        "turns": [],
        "detail_panel_mode": "answers",
        "execution_steps": [],
    }
    calls = []
    monkeypatch.setattr(frame.answer_list, "Show", lambda shown: calls.append(("answer", shown)))
    monkeypatch.setattr(frame.execution_list, "Show", lambda shown: calls.append(("execution", shown)))
    monkeypatch.setattr(frame, "Layout", lambda: calls.append(("layout",)))

    mode = frame._apply_detail_panel_mode()

    assert mode == "answers"
    assert calls == []


def test_append_execution_step_refreshes_visible_execution_list_without_stealing_focus(frame, monkeypatch):
    frame.active_chat_id = "chat-1"
    frame.current_chat_id = "chat-1"
    frame._current_chat_state = {
        "id": "chat-1",
        "title": "执行测试",
        "turns": [],
        "detail_panel_mode": "execution",
        "execution_steps": [],
    }
    frame._render_execution_list()
    clear_count = {"n": 0}
    append_count = {"n": 0}
    focus_count = {"n": 0}
    original_clear = frame.execution_list.Clear
    original_append = frame.execution_list.Append
    monkeypatch.setattr(
        frame.execution_list,
        "Clear",
        lambda: clear_count.__setitem__("n", clear_count["n"] + 1) or original_clear(),
    )
    monkeypatch.setattr(
        frame.execution_list,
        "Append",
        lambda text: append_count.__setitem__("n", append_count["n"] + 1) or original_append(text),
    )
    monkeypatch.setattr(frame.execution_list, "SetFocus", lambda: focus_count.__setitem__("n", focus_count["n"] + 1))
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    frame._append_execution_step_to_chat("chat-1", "计划更新：整理步骤")

    assert [step["step"] for step in frame._current_chat_state["execution_steps"]] == ["计划更新：整理步骤"]
    assert frame._current_chat_state["execution_steps"][0]["id"]
    assert _execution_content_labels(frame) == ["计划更新：整理步骤"]
    assert frame.execution_list.GetSelection() == 0
    assert clear_count["n"] == 0
    assert append_count["n"] == 2
    assert focus_count["n"] == 0


def test_append_execution_entry_does_not_steal_focus_from_foreground_control(frame, monkeypatch):
    frame.active_chat_id = "chat-1"
    frame.current_chat_id = "chat-1"
    frame._current_chat_state = {
        "id": "chat-1",
        "title": "执行测试",
        "turns": [],
        "detail_panel_mode": "execution",
        "execution_steps": [],
    }
    frame._render_execution_list()
    focus_count = {"execution": 0}
    monkeypatch.setattr(frame.input_edit, "HasFocus", lambda: True)
    monkeypatch.setattr(frame.execution_list, "HasFocus", lambda: False)
    monkeypatch.setattr(frame.execution_list, "SetFocus", lambda: focus_count.__setitem__("execution", focus_count["execution"] + 1))
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    frame._append_execution_entry_to_chat(
        "chat-1",
        {
            "event_type": "plan_updated",
            "display_kind": "plan",
            "detail_text": "后台计划更新",
            "list_text": "计划：后台计划更新",
        },
    )

    assert _execution_content_labels(frame) == ["计划：后台计划更新"]
    assert frame.execution_list.GetSelection() == 0
    assert focus_count["execution"] == 0


def test_append_execution_entry_preserves_unfocused_selection_and_appends_at_end(frame, monkeypatch):
    frame.active_chat_id = "chat-1"
    frame.current_chat_id = "chat-1"
    frame._current_chat_state = {
        "id": "chat-1",
        "title": "执行测试",
        "turns": [],
        "detail_panel_mode": "execution",
        "execution_steps": [{"step": "第一步"}, {"step": "第二步"}],
    }
    frame._render_execution_list()
    frame.execution_list.SetSelection(0)
    clear_count = {"n": 0}
    append_count = {"n": 0}
    focus_count = {"n": 0}
    original_clear = frame.execution_list.Clear
    original_append = frame.execution_list.Append
    monkeypatch.setattr(
        frame.execution_list,
        "Clear",
        lambda: clear_count.__setitem__("n", clear_count["n"] + 1) or original_clear(),
    )
    monkeypatch.setattr(
        frame.execution_list,
        "Append",
        lambda text: append_count.__setitem__("n", append_count["n"] + 1) or original_append(text),
    )
    monkeypatch.setattr(frame.execution_list, "SetFocus", lambda: focus_count.__setitem__("n", focus_count["n"] + 1))
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    frame._append_execution_entry_to_chat(
        "chat-1",
        {
            "event_type": "plan_updated",
            "display_kind": "plan",
            "detail_text": "第三步",
            "list_text": "计划：第三步",
        },
    )

    assert frame.execution_list.GetSelection() == 0  # Background additions do not move the user's reading position.
    assert _execution_content_labels(frame) == ["第一步", "第二步", "计划：第三步"]
    assert frame.execution_meta[-1] == ("execution", 2, "计划：第三步", "第三步")
    assert clear_count["n"] == 0
    assert append_count["n"] == 2
    assert focus_count["n"] == 0


def test_background_chat_execution_event_does_not_append_to_visible_execution_list(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame._current_chat_state = {
        "id": "chat-current",
        "title": "当前聊天",
        "turns": [],
        "detail_panel_mode": "execution",
        "execution_steps": [{"step": "当前步骤"}],
    }
    frame.archived_chats = [
        {
            "id": "chat-background",
            "title": "后台聊天",
            "turns": [],
            "created_at": 1.0,
            "updated_at": 1.0,
            "detail_panel_mode": "execution",
            "execution_steps": [],
            "codex_thread_id": "thread-background",
            "codex_turn_id": "turn-background",
        }
    ]
    frame._render_execution_list()
    before_rows = [frame.execution_list.GetString(i) for i in range(frame.execution_list.GetCount())]
    clear_count = {"n": 0}
    append_count = {"n": 0}
    original_clear = frame.execution_list.Clear
    original_append = frame.execution_list.Append
    monkeypatch.setattr(
        frame.execution_list,
        "Clear",
        lambda: clear_count.__setitem__("n", clear_count["n"] + 1) or original_clear(),
    )
    monkeypatch.setattr(
        frame.execution_list,
        "Append",
        lambda text: append_count.__setitem__("n", append_count["n"] + 1) or original_append(text),
    )
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    frame._on_codex_event_for_chat(
        "chat-background",
        main.CodexEvent(type="plan_updated", thread_id="thread-background", turn_id="turn-background", text="后台整理"),
    )

    archived = frame._find_archived_chat("chat-background")
    assert len(archived["execution_steps"]) == 1
    assert archived["execution_steps"][0]["list_text"] == "计划：后台整理"
    assert [frame.execution_list.GetString(i) for i in range(frame.execution_list.GetCount())] == before_rows
    assert clear_count["n"] == 0
    assert append_count["n"] == 0


def test_execution_list_enter_and_double_click_open_execution_detail(frame, monkeypatch):
    frame._current_chat_state = {
        "id": "chat-1",
        "title": "执行测试",
        "turns": [],
        "detail_panel_mode": "execution",
        "execution_steps": [{"step": "第一步"}],
    }
    frame._render_execution_list()
    called = {"n": 0}
    monkeypatch.setattr(
        frame,
        "_open_selected_execution_text_viewer",
        lambda: called.__setitem__("n", called["n"] + 1) or True,
    )
    monkeypatch.setattr(frame, "_on_any_key_down_escape_minimize", lambda _event: False)

    class KeyEvent:
        def GetKeyCode(self):
            return wx.WXK_RETURN

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            return None

    class ClickEvent:
        pass

    frame._on_execution_key_down(KeyEvent())
    frame._on_execution_activate(ClickEvent())

    assert called["n"] == 2


def test_execution_list_ctrl_c_copies_selected_detail_text(frame, monkeypatch):
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
    monkeypatch.setattr(frame, "_on_any_key_down_escape_minimize", lambda _event: False)
    monkeypatch.setattr(frame, "SetStatusText", lambda _text: None)
    frame._current_chat_state = {
        "id": "chat-1",
        "title": "执行测试",
        "turns": [],
        "detail_panel_mode": "execution",
        "execution_steps": [
            {
                "step": "第一步",
                "list_text": "计划：第一步",
                "detail_text": "第一步\n完整细节",
            }
        ],
    }
    frame._render_execution_list()
    frame.execution_list.SetSelection(_execution_row_index(frame, "计划：第一步"))

    class KeyEvent:
        def GetKeyCode(self):
            return ord("C")

        def ControlDown(self):
            return True

        def AltDown(self):
            return False

        def Skip(self):
            raise AssertionError("should not skip")

    frame._on_execution_key_down(KeyEvent())

    assert copied["text"] == "第一步\n完整细节"


def test_try_open_selected_execution_detail_opens_generated_detail_page(frame, monkeypatch):
    frame._current_chat_state = {
        "id": "chat-1",
        "title": "执行测试",
        "turns": [],
        "detail_panel_mode": "execution",
        "execution_steps": [
            {
                "step": "运行测试",
                "list_text": "命令：运行测试 pytest tests/test_main_unit.py -k codex",
                "detail_text": "运行测试\npytest tests/test_main_unit.py -k codex\n第 2 行输出",
            }
        ],
    }
    frame._render_execution_list()
    frame.execution_list.SetSelection(
        _execution_row_index(frame, "命令：运行测试 pytest tests/test_main_unit.py -k codex")
    )
    opened = {}
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    frame._open_local_webpage = lambda p: opened.__setitem__("path", str(p))

    assert frame._try_open_selected_execution_detail()
    html = Path(opened["path"]).read_text(encoding="utf-8")
    assert "执行过程详情" in html
    assert "pytest tests/test_main_unit.py -k codex" in html


def test_try_open_selected_execution_detail_reuses_page_path_and_cleanup_removes_it(frame, monkeypatch):
    frame._current_chat_state = {
        "id": "chat-1",
        "title": "执行测试",
        "turns": [],
        "detail_panel_mode": "execution",
        "execution_steps": [
            {
                "step": "运行测试",
                "list_text": "命令：运行测试 pytest tests/test_main_unit.py -k codex",
                "detail_text": "运行测试\npytest tests/test_main_unit.py -k codex\n第 2 行输出",
                "created_at": 1.0,
            }
        ],
    }
    frame._render_execution_list()
    frame.execution_list.SetSelection(
        _execution_row_index(frame, "命令：运行测试 pytest tests/test_main_unit.py -k codex")
    )
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    opened = []
    frame._open_local_webpage = lambda p: opened.append(str(p))

    assert frame._try_open_selected_execution_detail()
    first_path = Path(opened[-1])
    assert first_path.is_file()

    assert frame._try_open_selected_execution_detail()
    second_path = Path(opened[-1])
    assert second_path == first_path
    assert list(frame.detail_pages_dir.glob(f"{first_path.stem}*")) == [first_path]

    frame._cleanup_chat_detail_pages(frame._current_chat_state)

    assert not first_path.exists()


def test_switch_current_chat_refreshes_execution_list_in_execution_mode(frame):
    frame.current_chat_id = "chat-a"
    frame.active_chat_id = "chat-a"
    frame.view_mode = "active"
    frame._current_chat_state = {
        "id": "chat-a",
        "title": "聊天A",
        "turns": [],
        "detail_panel_mode": "execution",
        "execution_steps": [{"step": "A1"}],
    }
    frame.archived_chats = [
        {
            "id": "chat-b",
            "title": "聊天B",
            "turns": [],
            "created_at": 2.0,
            "updated_at": 2.0,
            "detail_panel_mode": "execution",
            "execution_steps": [{"step": "B1"}, {"step": "B2"}],
        }
    ]

    assert frame._switch_current_chat("chat-b") is True

    rows = _execution_content_labels(frame)
    assert rows == ["B1", "B2"]
    assert frame.execution_list.GetSelection() == 0


def test_active_turn_completed_in_execution_mode_preserves_question_and_adds_answer(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_turn_idx = 0
    frame.active_codex_turn_active = True
    frame.view_mode = "active"
    frame.active_session_turns = [
        {
            "question": "q",
            "answer_md": main.REQUESTING_TEXT,
            "model": "codex/main",
            "created_at": 1.0,
            "codex_thread_id": "thread-1",
            "codex_turn_id": "turn-1",
            "request_status": "pending",
        }
    ]
    frame._current_chat_state = {
        "id": "chat-current",
        "turns": frame.active_session_turns,
        "detail_panel_mode": "execution",
        "execution_steps": [],
    }
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)
    monkeypatch.setattr(frame, "_push_remote_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    frame._render_answer_list()
    execution_rebuilds = {"n": 0}
    original_render_execution_list = frame._render_execution_list
    monkeypatch.setattr(
        frame,
        "_render_execution_list",
        lambda: (execution_rebuilds.__setitem__("n", execution_rebuilds["n"] + 1), original_render_execution_list())[1],
    )

    frame._on_codex_event_for_chat(
        "chat-current",
        main.CodexEvent(type="turn_completed", thread_id="thread-1", turn_id="turn-1", text="done", status="completed"),
    )

    rows = _execution_content_labels(frame)
    assert execution_rebuilds["n"] == 0
    assert frame.active_session_turns[0]["request_status"] == "done"
    assert rows == ["我：q", "小诸葛：done"]


def test_switch_to_execution_mode_flushes_pending_delta_before_rebuild(frame, monkeypatch):
    frame.active_chat_id = "chat-1"
    frame.current_chat_id = "chat-1"
    frame._current_chat_state = {
        "id": "chat-1",
        "title": "当前聊天",
        "turns": [],
        "detail_panel_mode": "answers",
        "execution_steps": [],
    }
    frame._buffer_execution_delta(
        "chat-1",
            main.CodexEvent(type="agent_message_delta", thread_id="thread-1", turn_id="turn-1", item_id="msg-1", fragment_id="f1", text="待落地过程"),
    )
    rebuilt = {"count": 0}
    monkeypatch.setattr(frame, "_rebuild_execution_list_from_state", lambda: rebuilt.__setitem__("count", rebuilt["count"] + 1))
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    frame._apply_detail_panel_mode("execution", refresh_execution=True)

    assert frame._current_chat_state["execution_steps"][0]["detail_text"] == "待落地过程"
    assert rebuilt["count"] == 1
    assert frame._execution_delta_buffer == {}


def test_history_view_execution_mode_flushes_pending_delta_for_viewed_archived_chat(frame, monkeypatch):
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.view_mode = "history"
    frame.view_history_id = "chat-history"
    frame._current_chat_state = {
        "id": "chat-current",
        "title": "当前聊天",
        "turns": [],
        "detail_panel_mode": "answers",
        "execution_steps": [],
    }
    frame.archived_chats = [
        {
            "id": "chat-history",
            "title": "历史聊天",
            "turns": [],
            "detail_panel_mode": "answers",
            "execution_steps": [],
            "created_at": 2.0,
            "updated_at": 2.0,
        }
    ]
    frame._buffer_execution_delta(
        "chat-history",
            main.CodexEvent(type="agent_message_delta", thread_id="thread-history", turn_id="turn-history", item_id="msg-1", fragment_id="f1", text="历史缓冲"),
    )
    rebuilt = {"count": 0}
    monkeypatch.setattr(frame, "_rebuild_execution_list_from_state", lambda: rebuilt.__setitem__("count", rebuilt["count"] + 1))
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    frame._apply_detail_panel_mode("execution", refresh_execution=True)

    assert frame.archived_chats[0]["execution_steps"][0]["detail_text"] == "历史缓冲"
    assert rebuilt["count"] == 1
    assert frame._execution_delta_buffer == {}


def test_final_answer_live_event_in_execution_mode_preserves_question_and_adds_answer(frame, monkeypatch):
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
            "request_status": "pending",
        }
    ]
    frame._current_chat_state = {
        "id": "chat-current",
        "title": "当前聊天",
        "turns": frame.active_session_turns,
        "detail_panel_mode": "execution",
        "execution_steps": [],
    }
    monkeypatch.setattr(frame, "_push_remote_final_answer", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_call_later_if_alive", lambda *args, **kwargs: None)

    frame._render_answer_list()
    execution_rebuilds = {"n": 0}
    original_render_execution_list = frame._render_execution_list

    def _counted_render_execution_list():
        execution_rebuilds["n"] += 1
        return original_render_execution_list()

    monkeypatch.setattr(frame, "_render_execution_list", _counted_render_execution_list)

    frame._on_codex_event_for_chat(
        "chat-current",
        main.CodexEvent(type="item_completed", phase="final_answer", thread_id="thread-current", turn_id="turn-current", text="最终回答"),
    )

    rows = _execution_content_labels(frame)
    answer_rows = [frame.answer_list.GetString(i) for i in range(frame.answer_list.GetCount())]
    assert execution_rebuilds["n"] == 0
    assert rows == ["我：q", "小诸葛：最终回答"]
    assert "最终回答" in answer_rows


def test_codex_image_item_event_in_execution_mode_does_not_rebuild_execution_list(frame, monkeypatch, tmp_path):
    image_path = tmp_path / "from-cli.png"
    image_path.write_text("image", encoding="utf-8")
    frame.active_chat_id = "chat-1"
    frame.current_chat_id = "chat-1"
    frame.active_turn_idx = 0
    frame.view_mode = "active"
    frame.active_session_turns = [
        {
            "question": "看图",
            "answer_md": main.REQUESTING_TEXT,
            "model": main.DEFAULT_CODEX_MODEL,
            "created_at": time.time(),
            "request_status": "pending",
        }
    ]
    frame._current_chat_state = {
        "id": "chat-1",
        "turns": frame.active_session_turns,
        "detail_panel_mode": "execution",
        "execution_steps": [],
    }

    frame._render_answer_list()
    execution_rebuilds = {"n": 0}
    original_render_execution_list = frame._render_execution_list
    monkeypatch.setattr(
        frame,
        "_render_execution_list",
        lambda: (execution_rebuilds.__setitem__("n", execution_rebuilds["n"] + 1), original_render_execution_list())[1],
    )

    frame._on_codex_event_for_chat(
        "chat-1",
        main.CodexEvent(
            type="item_completed",
            thread_id="thread-1",
            turn_id="turn-1",
            item_id="image-1",
            status="imageView",
            data={"id": "image-1", "type": "imageView", "path": str(image_path)},
        ),
    )

    attachments = frame.active_session_turns[0].get("received_attachments") or []
    assert execution_rebuilds["n"] == 0
    assert attachments == [
        {
            "name": "from-cli.png",
            "path": str(image_path),
            "kind": "image",
            "direction": "incoming",
            "status": "success",
            "open_path": str(image_path),
            "source": "codex",
        }
    ]


def test_execution_list_render_populates_incremental_model(frame):
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.active_turn_idx = 0
    frame._current_chat_state = {
        "id": "chat-active",
        "title": "active",
        "turns": [{"question": "q", "answer_md": main.REQUESTING_TEXT, "model": main.DEFAULT_CODEX_MODEL}],
        "detail_panel_mode": "execution",
        "execution_steps": [{"turn_idx": 0, "display_kind": "commentary", "list_text": "old step"}],
    }

    frame._render_execution_list()

    assert frame.execution_list_model.visible_ids
    assert len(frame.execution_list_model.visible_ids) == frame.execution_list.GetCount()


def test_execution_event_appends_single_row_without_clearing_visible_list(frame, monkeypatch):
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.active_turn_idx = 0
    frame._current_chat_state = {
        "id": "chat-active",
        "title": "active",
        "turns": [{"question": "q", "answer_md": main.REQUESTING_TEXT, "model": main.DEFAULT_CODEX_MODEL}],
        "detail_panel_mode": "execution",
        "execution_steps": [],
    }
    frame._apply_detail_panel_mode("execution", refresh_execution=True)
    monkeypatch.setattr(frame.execution_list, "Clear", lambda: pytest.fail("execution append must not clear visible list"))

    frame._on_codex_event_for_chat(
        "chat-active",
        main.CodexEvent(type="plan_updated", thread_id="", turn_id="", text="step one"),
    )

    assert any(frame.execution_list.GetString(i) for i in range(frame.execution_list.GetCount()))
    assert frame.execution_list_model.visible_ids


def test_execution_append_does_not_select_latest_while_execution_list_has_focus(frame, monkeypatch):
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.active_turn_idx = 0
    frame._current_chat_state = {
        "id": "chat-active",
        "title": "active",
        "turns": [{"question": "q", "answer_md": main.REQUESTING_TEXT, "model": main.DEFAULT_CODEX_MODEL}],
        "detail_panel_mode": "execution",
        "execution_steps": [{"turn_idx": 0, "display_kind": "commentary", "list_text": "old step"}],
    }
    frame._apply_detail_panel_mode("execution", refresh_execution=True)
    frame.execution_list.SetSelection(0)
    monkeypatch.setattr(frame.execution_list, "HasFocus", lambda: True)

    frame._on_codex_event_for_chat(
        "chat-active",
        main.CodexEvent(type="plan_updated", thread_id="", turn_id="", text="new step"),
    )

    assert frame.execution_list.GetSelection() == 0


def test_f1_execution_mode_keeps_detail_slot_in_primary_tab_order(frame, monkeypatch):
    frame.Show()
    notebook = frame.notes_store.create_notebook("execution tab notebook")
    frame._notes_select_notebook(notebook.id, view="notes_list")
    frame.active_chat_id = "chat-1"
    frame.current_chat_id = "chat-1"
    frame._current_chat_state = {
        "id": "chat-1",
        "title": "切换测试",
        "turns": [],
        "detail_panel_mode": "answers",
        "execution_steps": [{"step": "第一步"}],
    }
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    frame._apply_detail_panel_mode("answers", refresh_execution=False)
    frame.history_list.SetFocus()

    class F1Event:
        def GetKeyCode(self):
            return wx.WXK_F1

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            raise AssertionError("should not skip")

    class TabEvent:
        def __init__(self):
            self.skipped = 0

        def GetKeyCode(self):
            return wx.WXK_TAB

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def ShiftDown(self):
            return False

        def Skip(self):
            self.skipped += 1

    frame._on_char_hook(F1Event())
    assert frame._current_chat_state["detail_panel_mode"] == "execution"
    assert frame.execution_list.HasFocus()
    assert frame.execution_list.GetSelection() == frame.execution_list.GetCount() - 1


def test_execution_list_tab_uses_primary_tab_navigation(frame, monkeypatch):
    frame.current_chat_id = "chat-1"
    frame.active_chat_id = "chat-1"
    frame._current_chat_state = {
        "id": "chat-1",
        "title": "执行列表 Tab",
        "turns": [],
        "detail_panel_mode": "execution",
        "execution_steps": [{"step": "第一步"}],
    }
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    frame._apply_detail_panel_mode("execution", refresh_execution=False)
    frame.execution_list.SetFocus()

    class TabEvent:
        def __init__(self):
            self.skipped = 0

        def GetKeyCode(self):
            return wx.WXK_TAB

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def ShiftDown(self):
            return False

        def Skip(self):
            self.skipped += 1

    event = TabEvent()
    frame._on_execution_key_down(event)

    assert frame.input_edit.HasFocus()
    assert event.skipped == 0


def test_f1_switch_to_execution_writes_no_rows_when_projection_is_current(frame, monkeypatch):
    frame.Show()
    frame.active_chat_id = "chat-f1-fast"
    frame.current_chat_id = "chat-f1-fast"
    frame._current_chat_state = {
        "id": "chat-f1-fast",
        "title": "F1 fast",
        "turns": [],
        "detail_panel_mode": "answers",
        "execution_steps": [{"step": "第一步"}],
    }
    frame._apply_detail_panel_mode("execution", refresh_execution=True)
    frame._apply_detail_panel_mode("answers", refresh_execution=False)
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    for method in ("Clear", "Append", "Insert", "Delete", "SetString"):
        monkeypatch.setattr(frame.execution_list, method,
                            lambda *args: pytest.fail("F1 must not rewrite an unchanged page"))

    class F1Event:
        def GetKeyCode(self):
            return wx.WXK_F1

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            raise AssertionError("F1 should be handled")

    frame.answer_list.SetFocus()
    frame._on_char_hook(F1Event())

    assert frame._current_chat_state["detail_panel_mode"] == "execution"
    assert frame.execution_list.HasFocus()
    assert frame.execution_list.GetStringSelection() == "第一步"


def test_execution_list_keydown_f1_toggles_back_to_answers(frame, monkeypatch):
    frame.Show()
    frame.active_chat_id = "chat-f1-execution-keydown"
    frame.current_chat_id = "chat-f1-execution-keydown"
    frame._current_chat_state = {
        "id": "chat-f1-execution-keydown",
        "title": "执行列表 F1",
        "turns": [],
        "detail_panel_mode": "execution",
        "execution_steps": [{"step": "第一步"}],
    }
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    frame._apply_detail_panel_mode("execution", refresh_execution=True)
    frame.execution_list.SetFocus()

    class F1KeyDownEvent:
        def GetKeyCode(self):
            return wx.WXK_F1

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            raise AssertionError("F1 should be handled by execution list keydown")

    frame._on_execution_key_down(F1KeyDownEvent())

    assert frame._current_chat_state["detail_panel_mode"] == "answers"
    assert frame.answer_list.HasFocus()


def test_f1_toggles_from_visible_execution_list_even_if_saved_mode_is_stale(frame, monkeypatch):
    frame.Show()
    frame.active_chat_id = "chat-f1-stale"
    frame.current_chat_id = "chat-f1-stale"
    frame._current_chat_state = {
        "id": "chat-f1-stale",
        "title": "F1 stale",
        "turns": [],
        "detail_panel_mode": "execution",
        "execution_steps": [{"step": "第一步"}],
    }
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    frame._apply_detail_panel_mode("execution", refresh_execution=True)
    frame.execution_list.SetFocus()
    frame._current_chat_state["detail_panel_mode"] = "answers"

    frame._toggle_detail_panel_mode(focus_detail=True)

    assert frame._current_chat_state["detail_panel_mode"] == "answers"
    assert frame.answer_list.IsShown()
    assert frame.answer_list.HasFocus()


def test_execution_enter_opens_plain_text_viewer_instead_of_web_detail(frame, monkeypatch):
    frame._current_chat_state["detail_panel_mode"] = "execution"
    frame.execution_list.Set(["执行步骤"])
    frame.execution_meta = [("execution", 0, "执行步骤", "# 标题\n\n第一段 **加粗**。\n\n第二段")]
    frame.execution_list.SetSelection(0)
    opened_viewer = []
    opened_web = []
    monkeypatch.setattr(frame, "_open_answer_text_viewer", lambda title, text: opened_viewer.append((title, text)) or True)
    monkeypatch.setattr(frame, "_try_open_selected_execution_detail", lambda: opened_web.append(True) or True)

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

    frame._on_execution_key_down(E())

    assert opened_viewer == [("回答详情", "标题\n\n第一段 加粗。\n\n第二段")]
    assert opened_web == []


def test_remote_execution_projection_page_uses_canonical_steps(frame, tmp_path):
    store = main.ChatStore(tmp_path / "execution-v3.db")
    store.initialize()
    store.upsert_chat({"id": "chat-v3", "title": "测试", "model": main.DEFAULT_MODEL_ID,
                       "created_at": 1.0, "updated_at": 1.0,
                       "turns": [{"question": "问题", "answer_md": "回答", "model": main.DEFAULT_MODEL_ID,
                                  "created_at": 1.0}]})
    store.replace_turns("chat-v3", [{"question": "问题", "answer_md": "回答",
                                     "model": main.DEFAULT_MODEL_ID, "created_at": 1.0}])
    store.replace_execution_steps("chat-v3", [
        {"turn_idx": 0, "display_kind": "commentary", "list_text": "可见过程", "detail_text": "完整过程"},
        {"turn_idx": 0, "display_kind": "command", "list_text": "隐藏命令", "detail_text": "命令详情"},
    ])
    frame.chat_store = store
    frame.active_chat_id = "chat-v3"
    frame.current_chat_id = "chat-v3"
    frame.active_turn_idx = 0
    frame._current_chat_state = {"id": "chat-v3", "turns": store.load_turns("chat-v3"),
                                 "execution_steps": store.load_execution_steps("chat-v3"),
                                 "detail_panel_mode": "execution"}
    frame.view_mode = "active"
    status, tail = frame._remote_api_execution_page_ui(
        {"chat_id": "chat-v3", "body": {"limit": 10}}, secret="secret"
    )
    assert status == 200
    assert [row["list_text"] for row in tail["rows"]] == ["我：问题", "可见过程", "小诸葛：回答"]
    assert all(row["row_id"].startswith("execution:chat-v3:") for row in tail["rows"])
    frame._current_execution_steps_for_render = lambda: (2, store.load_execution_steps("chat-v3"))
    desktop_rows, desktop_meta = frame._execution_page_projection()
    desktop_content = [(row_id, text) for (row_id, text), meta in zip(desktop_rows, desktop_meta)
                       if meta[0] == "execution"]
    assert desktop_content == [(row["row_id"], row["list_text"]) for row in tail["rows"]]


def test_focus_latest_answer_does_not_steal_focus_from_execution_list_when_find_focus_is_empty(frame, monkeypatch):
    frame.answer_meta = [("answer", 0, "小诸葛", "最新回答")]
    monkeypatch.setattr(frame, "IsActive", lambda: True)
    monkeypatch.setattr(frame, "IsIconized", lambda: False)
    monkeypatch.setattr(wx.Window, "FindFocus", lambda: None)
    monkeypatch.setattr(frame.execution_list, "HasFocus", lambda: True)
    monkeypatch.setattr(frame.answer_list, "SetFocus", lambda: pytest.fail("completion focus should not steal focus from execution list"))

    frame._focus_latest_answer()


def test_codex_ui_event_drain_coalesces_execution_list_repaints(frame, monkeypatch):
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
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
        "id": "chat-active",
        "turns": frame.active_session_turns,
        "detail_panel_mode": "execution",
        "execution_steps": [],
    }
    frame._render_execution_list()
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    repaint_calls = []
    monkeypatch.setattr(frame, "_request_listbox_repaint", lambda *controls: repaint_calls.append(controls))

    for idx in range(5):
        frame._pending_codex_ui_events.append(
            (
                "chat-active",
                main.CodexEvent(
                    type="plan_updated",
                    thread_id="thread-active",
                    turn_id="turn-active",
                    text=f"step {idx}",
                ),
            )
        )

    frame._codex_ui_event_flush_scheduled = True
    frame._drain_codex_ui_events()

    assert len(_execution_content_labels(frame)) == 6
    assert repaint_calls.count((frame.execution_list,)) == 1


def test_background_execution_step_defers_list_append_during_quiet(frame, monkeypatch):
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.active_turn_idx = 1
    frame.view_mode = "active"
    frame._current_chat_state = {"id": "chat-active", "turns": [], "execution_steps": []}
    frame._navigation_quiet_until = time.monotonic() + 3.0
    frame._background_ui_update_depth = 1
    frame._apply_detail_panel_mode("execution", refresh_execution=True)
    monkeypatch.setattr(frame, "_broadcast_remote_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        frame.execution_list_model,
        "append",
        lambda *_args, **_kwargs: pytest.fail("execution append must be deferred during quiet"),
    )

    assert frame._append_execution_entry_to_chat(
        "chat-active",
        {"turn_idx": 1, "display_kind": "commentary", "list_text": "step 1", "detail_text": "step 1"},
        save_state=False,
    )

    assert frame._current_chat_state["execution_steps"][0]["list_text"] == "step 1"
    assert frame._pending_execution_tail_appends["chat-active"]


def test_pending_execution_steps_sync_canonical_page_after_quiet_in_order(frame, monkeypatch):
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.active_turn_idx = 1
    frame.view_mode = "active"
    frame._current_chat_state = {"id": "chat-active", "turns": [], "detail_panel_mode": "execution", "execution_steps": []}
    frame._apply_detail_panel_mode("execution", refresh_execution=True)
    frame._navigation_quiet_until = 0.0
    frame._pending_execution_tail_appends = {
        "chat-active": [
            (0, {"turn_idx": 1, "display_kind": "commentary", "list_text": "step 1", "detail_text": "step 1"}),
            (1, {"turn_idx": 1, "display_kind": "commentary", "list_text": "step 2", "detail_text": "step 2"}),
        ]
    }
    # Deferred entries are notifications; their owner already holds the data.
    frame._current_chat_state["execution_steps"] = [entry for _, entry in frame._pending_execution_tail_appends["chat-active"]]
    appended = []
    original_append = frame.execution_list.Append

    def record_append(row_text):
        appended.append(row_text)
        return original_append(row_text)

    monkeypatch.setattr(frame.execution_list, "Append", record_append)

    frame._flush_pending_background_ui_updates()

    assert [label for label in appended if label != main.UNKNOWN_TIME_LABEL] == ["step 1", "step 2"]
    assert frame.execution_list.GetString(frame.execution_list.GetCount() - 1) == "step 2"


def test_pending_execution_flush_skips_rows_already_rendered_from_store(frame, monkeypatch):
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame.active_turn_idx = 1
    frame.view_mode = "active"
    step = {"turn_idx": 1, "display_kind": "commentary", "list_text": "step 1", "detail_text": "step 1"}
    frame._current_chat_state = {
        "id": "chat-active",
        "turns": [],
        "detail_panel_mode": "execution",
        "execution_steps": [copy.deepcopy(step)],
    }
    frame._apply_detail_panel_mode("execution", refresh_execution=True)
    frame._render_execution_list(force=True)
    frame._pending_execution_tail_appends = {"chat-active": [(0, copy.deepcopy(step))]}
    monkeypatch.setattr(frame.execution_list_model, "append", lambda *_args, **_kwargs: pytest.fail("already-rendered pending rows must not append again"))

    frame._flush_pending_background_ui_updates()

    assert frame._pending_execution_tail_appends == {}
    assert [meta for meta in frame.execution_meta if meta[0] != "time"] == [
        ("execution", 0, "step 1", "step 1")
    ]


def test_codex_ui_event_drain_preserves_execution_selection_when_list_has_focus(frame, monkeypatch):
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
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
        "id": "chat-active",
        "turns": frame.active_session_turns,
        "detail_panel_mode": "execution",
        "execution_steps": [
            {"event_type": "plan_updated", "display_kind": "plan", "list_text": "existing 0", "detail_text": "existing 0"},
            {"event_type": "plan_updated", "display_kind": "plan", "list_text": "existing 1", "detail_text": "existing 1"},
        ],
    }
    frame._render_execution_list()
    frame.execution_list.SetSelection(0)
    monkeypatch.setattr(frame.execution_list, "HasFocus", lambda: True)
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    frame._pending_codex_ui_events.append(
        (
            "chat-active",
            main.CodexEvent(
                type="plan_updated",
                thread_id="thread-active",
                turn_id="turn-active",
                text="new step",
            ),
        )
    )

    frame._codex_ui_event_flush_scheduled = True
    frame._drain_codex_ui_events()

    assert len(_execution_content_labels(frame)) == 4
    assert frame.execution_list.GetSelection() == 0


def test_active_execution_list_shows_current_turn_question_and_steps(frame):
    frame.active_session_turns = [
        {"question": "old", "answer_md": "old answer", "model": "codex/main"},
        {"question": "new", "answer_md": main.REQUESTING_TEXT, "model": "codex/main"},
    ]
    frame.active_turn_idx = 1
    frame._current_chat_state = {
        "id": "chat-current",
        "turns": frame.active_session_turns,
        "detail_panel_mode": "execution",
        "execution_steps": [
            {"step": "old process", "list_text": "old process", "display_kind": "commentary", "turn_idx": 0},
            {"step": "new process", "list_text": "new process", "display_kind": "commentary", "turn_idx": 1},
        ],
    }

    frame._render_execution_list()

    visible = _execution_content_labels(frame)
    assert visible == ["我：new", "new process"]


def test_execution_list_defaults_to_latest_100_content_rows_including_answer(frame):
    frame.active_turn_idx = 1
    frame._current_chat_state = {
        "id": "chat-current",
        "turns": [
            {"question": "old", "answer_md": "old", "model": "codex/main"},
            {"question": "q", "answer_md": "a", "model": "codex/main"},
        ],
        "detail_panel_mode": "execution",
        "execution_steps": [
            {"step": f"process {idx}", "list_text": f"process {idx}", "display_kind": "commentary", "turn_idx": 1}
            for idx in range(120)
        ],
    }

    frame._render_execution_list()

    visible = _execution_content_labels(frame)
    assert visible[0] == "更多"
    assert "process 0" not in visible
    assert visible == ["更多"] + [f"process {idx}" for idx in range(21, 120)] + ["小诸葛：a"]
    assert "process 119" in visible
    assert frame.execution_meta[0] == ("more", -1, "更多", "")
    assert len(_execution_content_labels(frame)) == 101


def test_execution_list_does_not_show_more_when_filtered_visible_rows_are_under_limit(frame):
    frame.active_chat_id = "chat-filtered"
    frame.current_chat_id = "chat-filtered"
    frame.active_turn_idx = 1
    frame.view_mode = "active"
    frame._current_chat_state.update({
        "id": "chat-filtered",
        "detail_panel_mode": "execution",
        "execution_steps": [
            {"step": f"hidden {idx}", "display_kind": "command", "turn_idx": 1}
            if idx % 2
            else {"step": f"visible {idx}", "list_text": f"visible {idx}", "display_kind": "commentary", "turn_idx": 1}
            for idx in range(150)
        ],
    })

    frame._render_execution_list()

    visible = _execution_content_labels(frame)
    assert "更多" not in visible
    assert len(_execution_content_labels(frame)) == 75
    assert frame.execution_total_content_rows == 75


def test_execution_list_recent_store_page_counts_answer_without_full_step_load(frame, tmp_path, monkeypatch):
    frame.chat_db_path = tmp_path / "chat_history.db"
    frame.chat_store = main.ChatStore(frame.chat_db_path, max_execution_steps_per_turn=1000)
    frame.chat_store.initialize()
    frame._chat_store_enabled = True
    frame.active_chat_id = "chat-current"
    frame.current_chat_id = "chat-current"
    frame.active_turn_idx = 1
    frame.active_session_turns = [
        {"question": "old", "answer_md": "old", "model": "codex/main"},
        {"question": "q", "answer_md": "a", "model": "codex/main"},
    ]
    frame._current_chat_state = {
        "id": "chat-current",
        "turns": frame.active_session_turns,
        "detail_panel_mode": "execution",
        "execution_steps": [],
    }
    frame.chat_store.upsert_chat(frame._current_chat_state)
    frame.chat_store.replace_turns("chat-current", frame.active_session_turns)
    for idx in range(250):
        frame.chat_store.append_execution_step(
            "chat-current",
            {"turn_idx": 1, "step": f"process {idx}", "list_text": f"process {idx}", "display_kind": "commentary"},
        )
    monkeypatch.setattr(
        frame.chat_store,
        "load_execution_steps",
        lambda *args, **kwargs: pytest.fail("execution list should use paged recent store reads"),
    )

    frame._render_execution_list()

    visible = _execution_content_labels(frame)
    assert visible[0] == "更多"
    assert "process 149" not in visible
    assert visible == ["更多"] + [f"process {idx}" for idx in range(151, 250)] + ["小诸葛：a"]
    assert "process 249" in visible
    assert len(_execution_content_labels(frame)) == 101


def test_open_selected_execution_detail_uses_rendered_detail_without_full_step_load(frame, monkeypatch):
    frame._current_chat_state = {
        "id": "chat-current",
        "turns": [{"question": "q", "answer_md": "a", "model": "codex/main"}],
        "detail_panel_mode": "execution",
        "execution_steps": [],
    }
    frame.execution_list.Clear()
    frame.execution_list.Append("process row")
    frame.execution_list.SetSelection(0)
    frame.execution_meta = [("execution", 499, "process row", "full detail from rendered page")]
    monkeypatch.setattr(frame, "_current_execution_steps", lambda: pytest.fail("opening visible execution detail should not load all steps"))
    opened = []
    monkeypatch.setattr(frame, "_ensure_execution_detail_page", lambda step, step_idx: opened.append((step, step_idx)) or "detail.html")
    monkeypatch.setattr(frame, "_open_local_webpage", lambda path: opened.append(("open", path)))
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    assert frame._try_open_selected_execution_detail() is True

    assert opened[0][0]["detail_text"] == "full detail from rendered page"
    assert opened[0][1] == 499
    assert opened[1] == ("open", "detail.html")


def test_open_selected_execution_detail_with_summary_does_not_hydrate_all_steps(frame, monkeypatch):
    frame._current_chat_state = {
        "id": "chat-current",
        "detail_panel_mode": "execution",
        "execution_steps": [],
    }
    frame.execution_list.Clear()
    frame.execution_list.Append("remote summary row")
    frame.execution_list.SetSelection(0)
    frame.execution_meta = [("execution", 5000, "remote summary row", "")]
    monkeypatch.setattr(frame, "_current_execution_steps", lambda: pytest.fail("visible execution row should not hydrate all steps"))
    opened = []
    monkeypatch.setattr(frame, "_ensure_execution_detail_page", lambda step, step_idx: opened.append((step, step_idx)) or "detail.html")
    monkeypatch.setattr(frame, "_open_local_webpage", lambda path: opened.append(("open", path)))
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    assert frame._try_open_selected_execution_detail() is True

    assert opened[0][0]["detail_text"] == "remote summary row"
    assert opened[0][1] == 5000
    assert opened[1] == ("open", "detail.html")


def test_execution_list_more_expands_all_steps_and_context_without_duplicates(frame):
    frame.active_turn_idx = 1
    frame._current_chat_state = {
        "id": "chat-current",
        "turns": [
            {"question": "old", "answer_md": "old", "model": "codex/main"},
            {"question": "q", "answer_md": "a", "model": "codex/main"},
        ],
        "detail_panel_mode": "execution",
        "execution_steps": [
            {"step": f"process {idx}", "list_text": f"process {idx}", "display_kind": "commentary", "turn_idx": 1}
            for idx in range(220)
        ],
    }
    frame._render_execution_list()
    frame.execution_list.SetSelection(0)

    assert frame._try_open_selected_execution_detail() is True
    visible_after_one = _execution_content_labels(frame)
    assert visible_after_one[0] == "更多"
    assert visible_after_one == ["更多"] + [f"process {idx}" for idx in range(21, 220)] + ["小诸葛：a"]
    assert "process 0" not in visible_after_one
    assert len(_execution_content_labels(frame)) == 201

    assert frame._try_open_selected_execution_detail() is True
    visible_after_two = _execution_content_labels(frame)
    assert visible_after_two == ["我：q"] + [f"process {idx}" for idx in range(220)] + ["小诸葛：a"]
    assert "更多" not in visible_after_two
    assert len(_execution_content_labels(frame)) == 222


def test_render_execution_list_does_not_clear_when_rows_are_unchanged(frame, monkeypatch):
    frame._current_chat_state = {
        "id": "chat-current",
        "turns": [],
        "execution_steps": [{"turn_idx": 0, "display_kind": "commentary", "list_text": "step 1", "detail_text": "step 1"}],
    }
    frame._render_execution_list()
    monkeypatch.setattr(frame.execution_list, "Clear", lambda: pytest.fail("unchanged execution rows should not rebuild the listbox"))

    frame._render_execution_list()


def test_hidden_execution_render_only_marks_dirty_without_touching_listbox(frame, monkeypatch):
    frame._current_chat_state = {
        "id": "chat-current",
        "turns": [],
        "detail_panel_mode": "answers",
        "execution_steps": [{"turn_idx": 0, "display_kind": "commentary", "list_text": "hidden step", "detail_text": "hidden step"}],
    }
    frame._apply_detail_panel_mode("answers", refresh_execution=False)
    monkeypatch.setattr(
        frame.execution_list_model,
        "replace_visible_page",
        lambda *args, **kwargs: pytest.fail("hidden execution render should not update execution listbox"),
    )
    monkeypatch.setattr(frame.execution_list, "Clear", lambda: pytest.fail("hidden execution render should not clear execution listbox"))
    monkeypatch.setattr(frame.execution_list, "Append", lambda _label: pytest.fail("hidden execution render should not append execution rows"))
    monkeypatch.setattr(frame.execution_list, "Refresh", lambda *args, **kwargs: pytest.fail("hidden execution render should not repaint execution listbox"))

    frame._render_execution_list()

    assert frame._execution_list_dirty is True


def test_hidden_execution_reset_only_marks_dirty_without_touching_listbox(frame, monkeypatch):
    frame._current_chat_state = {"id": "chat-current", "turns": [], "detail_panel_mode": "answers", "execution_steps": []}
    frame._apply_detail_panel_mode("answers", refresh_execution=False)
    monkeypatch.setattr(
        frame.execution_list_model,
        "replace_visible_page",
        lambda *args, **kwargs: pytest.fail("hidden execution reset should not update execution listbox"),
    )
    monkeypatch.setattr(frame.execution_list, "Clear", lambda: pytest.fail("hidden execution reset should not clear execution listbox"))
    monkeypatch.setattr(frame.execution_list, "Append", lambda _label: pytest.fail("hidden execution reset should not append execution rows"))
    monkeypatch.setattr(frame.execution_list, "Refresh", lambda *args, **kwargs: pytest.fail("hidden execution reset should not repaint execution listbox"))

    frame._reset_current_turn_execution_view()

    assert frame._execution_list_dirty is True


def test_hidden_execution_background_event_marks_dirty_without_touching_listbox(frame, monkeypatch):
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
            "codex_thread_id": "thread-current",
            "codex_turn_id": "turn-current",
        }
    ]
    frame._current_chat_state = {
        "id": "chat-current",
        "turns": frame.active_session_turns,
        "detail_panel_mode": "answers",
        "execution_steps": [],
    }
    frame._apply_detail_panel_mode("answers", refresh_execution=False)
    monkeypatch.setattr(
        frame.execution_list_model,
        "append",
        lambda *args, **kwargs: pytest.fail("hidden background event should not append execution rows"),
    )
    monkeypatch.setattr(
        frame.execution_list_model,
        "replace_visible_page",
        lambda *args, **kwargs: pytest.fail("hidden background event should not rebuild execution rows"),
    )
    monkeypatch.setattr(frame.execution_list, "Clear", lambda: pytest.fail("hidden background event should not clear execution listbox"))
    monkeypatch.setattr(frame.execution_list, "Append", lambda _label: pytest.fail("hidden background event should not append execution listbox rows"))
    monkeypatch.setattr(frame.execution_list, "Refresh", lambda *args, **kwargs: pytest.fail("hidden background event should not repaint execution listbox"))

    frame._on_codex_event_for_chat(
        "chat-current",
        main.CodexEvent(
            type="plan_updated",
            thread_id="thread-current",
            turn_id="turn-current",
            text="hidden background plan",
        ),
    )

    assert frame._current_chat_state["execution_steps"]
    assert frame._execution_list_dirty is True


def test_idle_execution_flush_defers_while_primary_control_has_focus(frame, monkeypatch):
    frame._current_chat_state = {
        "id": "chat-current",
        "turns": [],
        "detail_panel_mode": "execution",
        "execution_steps": [{"turn_idx": 0, "display_kind": "commentary", "list_text": "idle step", "detail_text": "idle step"}],
    }
    frame._execution_list_dirty = True
    delayed = []
    monkeypatch.setattr(frame, "_primary_navigation_control_has_focus", lambda: True)
    monkeypatch.setattr(frame, "_render_execution_list", lambda *args, **kwargs: pytest.fail("focused controls should defer idle execution refresh"))
    monkeypatch.setattr(frame, "_call_later_if_alive", lambda delay_ms, fn, *args: delayed.append((delay_ms, fn, args)) or object())

    frame._flush_idle_ui_refreshes()

    assert frame._execution_list_dirty is True
    assert delayed == [(main.IDLE_UI_REFRESH_DELAY_MS, frame._flush_idle_ui_refreshes, ())]


def test_idle_execution_flush_renders_when_user_is_idle(frame, monkeypatch):
    frame._current_chat_state = {
        "id": "chat-current",
        "turns": [],
        "detail_panel_mode": "execution",
        "execution_steps": [{"turn_idx": 0, "display_kind": "commentary", "list_text": "idle step", "detail_text": "idle step"}],
    }
    frame._execution_list_dirty = True
    calls = []
    monkeypatch.setattr(frame, "_primary_navigation_control_has_focus", lambda: False)
    monkeypatch.setattr(frame, "_render_execution_list", lambda *args, **kwargs: calls.append(kwargs) or setattr(frame, "_execution_list_dirty", False))

    frame._flush_idle_ui_refreshes()

    assert calls == [{"force": True}]
    assert frame._execution_list_dirty is False


def test_execution_shift_tab_uses_primary_navigation_instead_of_forcing_input_focus(frame, monkeypatch):
    focused = []
    monkeypatch.setattr(frame.input_edit, "SetFocus", lambda: pytest.fail("Shift+Tab from execution should not jump to input"))
    monkeypatch.setattr(frame.history_list, "SetFocus", lambda: focused.append("history"))
    monkeypatch.setattr(main.wx.Window, "FindFocus", lambda: frame.execution_list)

    event = main.wx.KeyEvent(main.wx.wxEVT_KEY_DOWN)
    event.SetKeyCode(main.wx.WXK_TAB)
    event.m_shiftDown = True

    frame._on_execution_key_down(event)

    assert focused == ["history"]


@pytest.mark.parametrize("count", [99, 100, 101])
def test_execution_content_page_boundaries_include_question_and_answer(frame, count):
    frame.active_turn_idx = 0
    frame._current_chat_state = {
        "id": "chat-current", "detail_panel_mode": "execution",
        "turns": [{"question": "q", "answer_md": "a"}],
        "execution_steps": [{"step": f"step {i}", "turn_idx": 0} for i in range(count - 2)],
    }
    frame._render_execution_list()
    expected = ["我：q"] + [f"step {i}" for i in range(count - 2)] + ["小诸葛：a"]
    assert _execution_content_labels(frame) == (
        ["更多"] + expected[-100:] if count > 100 else expected
    )
    assert len(frame.execution_meta) == len(frame.execution_list_model.visible_ids) == frame.execution_list.GetCount()


def test_execution_selection_identity_survives_context_insert_and_page_slide(frame, monkeypatch):
    frame.active_turn_idx = 0
    frame._current_chat_state = {"id": "chat-current", "detail_panel_mode": "execution",
        "turns": [{"question": "", "answer_md": ""}],
        "execution_steps": [{"step": f"step {i}", "turn_idx": 0} for i in range(99)]}
    frame._render_execution_list()
    monkeypatch.setattr(frame.execution_list, "HasFocus", lambda: True)
    frame.execution_list.SetSelection(_execution_row_index(frame, "step 20"))
    selected_id = frame.execution_list_model.selected_id()
    frame._current_chat_state["turns"][0]["question"] = "q"
    frame._rebuild_execution_list_from_state()
    assert frame.execution_list_model.selected_id() == selected_id
    frame._current_chat_state["execution_steps"].extend({"step": f"step {i}", "turn_idx": 0} for i in range(99, 110))
    frame._rebuild_execution_list_from_state()
    assert frame.execution_list.GetStringSelection() == "step 20"
    assert frame.execution_list_model.selected_id() == selected_id
    frame.execution_list.SetSelection(_execution_row_index(frame, "step 11"))
    selected_id = frame.execution_list_model.selected_id()
    frame._current_chat_state["execution_steps"].append({"step": "step 110", "turn_idx": 0})
    frame._rebuild_execution_list_from_state()
    assert frame.execution_list.GetStringSelection() == "step 11"
    assert frame.execution_list_model.selected_id() == selected_id


def test_execution_history_bounded_pages_merge_unsaved_tail_and_skip_hidden_rows(frame, tmp_path, monkeypatch, wx_app):
    store = main.ChatStore(tmp_path / "execution.db", max_execution_steps_per_turn=1000)
    store.initialize()
    store.upsert_chat({"id": "history", "detail_panel_mode": "execution"})
    store.replace_turns("history", [{"question": "q", "answer_md": "a"}])
    for i in range(350):
        store.append_execution_step("history", {"id": str(i), "turn_idx": 0, "created_at": i + 1,
            "display_kind": "command" if i >= 120 else "commentary", "list_text": f"step {i}"})
    frame.chat_store = store
    frame._chat_store_enabled = True
    frame.archived_chats = [{"id": "history"}]
    frame.view_mode = "history"
    frame.view_history_id = "history"
    monkeypatch.setattr(store, "load_execution_steps", lambda *a, **kw: pytest.fail("full history execution read"))
    reads = []
    original = store.load_recent_execution_steps
    def read_page(*args, **kwargs):
        reads.append(kwargs)
        result = original(*args, **kwargs)
        assert len(result[1]) <= kwargs["limit"]
        return result
    monkeypatch.setattr(store, "load_recent_execution_steps", read_page)
    frame._render_execution_list(force=True)
    deadline = time.monotonic() + 2
    while frame._execution_list_dirty and time.monotonic() < deadline:
        wx_app.Yield()
    assert len(reads) == 4
    assert all(read["limit"] == 100 for read in reads)
    chat = frame._find_archived_chat("history")
    chat["execution_steps"] = [{"id": "tail", "turn_idx": 0, "created_at": 400, "step": "unsaved tail"}]
    frame._mark_execution_list_dirty()
    frame._render_execution_list(force=True)
    deadline = time.monotonic() + 2
    while frame._execution_list_dirty and time.monotonic() < deadline:
        wx_app.Yield()
    assert "unsaved tail" in _execution_content_labels(frame)
    frame.execution_visible_row_limit = 200
    frame._render_execution_list(force=True)
    labels = _execution_content_labels(frame)
    assert labels == ["我：q"] + [f"step {i}" for i in range(120)] + ["unsaved tail", "小诸葛：a"]
    assert "更多" not in labels


def test_execution_history_duplicate_legacy_rows_keep_identity_when_page_expands(frame, tmp_path, monkeypatch):
    store = main.ChatStore(tmp_path / "duplicates.db")
    store.initialize()
    store.upsert_chat({"id": "history", "detail_panel_mode": "execution"})
    store.replace_turns("history", [])
    for i in range(250):
        store.append_execution_step("history", {"item_id": "reused-provider-id", "turn_idx": 0, "step": "same", "created_at": 1})
    frame.chat_store = store
    frame._chat_store_enabled = True
    frame.archived_chats = [{"id": "history"}]
    frame.view_mode = "history"
    frame.view_history_id = "history"
    frame._render_execution_list(force=True)
    monkeypatch.setattr(frame.execution_list, "HasFocus", lambda: True)
    frame.execution_list.SetSelection(20)
    selected_id = frame.execution_list_model.selected_id()
    assert len(set(_execution_content_ids(frame))) == 101
    frame.execution_visible_row_limit = 200
    frame._render_execution_list(force=True)
    assert frame.execution_list_model.selected_id() == selected_id
    assert frame.execution_list.GetSelection() == 120
    assert len(set(_execution_content_ids(frame))) == 201


@pytest.mark.parametrize("change", ["chat", "clear", "event", "answers", "close"])
def test_execution_history_scan_discards_stale_completion(frame, monkeypatch, tmp_path, wx_app, change):
    store = main.ChatStore(tmp_path / "cancel.db", max_execution_steps_per_turn=1000)
    store.initialize()
    store.upsert_chat({"id": "history", "detail_panel_mode": "execution"})
    for i in range(310):
        store.append_execution_step("history", {"step": f"old {i}", "display_kind": "command" if i > 10 else ""})
    frame.chat_store, frame._chat_store_enabled = store, True
    frame.archived_chats = [{"id": "history"}]
    frame.view_mode, frame.view_history_id = "history", "history"
    blocked, release, exited = threading.Event(), threading.Event(), threading.Event()
    original = store.load_recent_execution_steps
    def read(*args, **kwargs):
        if threading.current_thread() is not threading.main_thread():
            blocked.set()
            assert release.wait(5)
            try:
                return original(*args, **kwargs)
            finally:
                exited.set()
        return original(*args, **kwargs)
    monkeypatch.setattr(store, "load_recent_execution_steps", read)
    try:
        frame._render_execution_list(force=True)
        assert blocked.wait(1)
        before = list(frame.execution_list.GetStrings())
        if change == "chat":
            frame.view_history_id = "other"
        elif change == "answers":
            frame._apply_detail_panel_mode("answers")
        elif change == "clear":
            frame._reset_execution_visible_row_limit()
        elif change == "event":
            frame._mark_execution_list_dirty()
        else:
            destroy = frame.Destroy
            monkeypatch.setattr(frame, "Destroy", lambda: None)  # Fixture owns the same frame.
            destroy()
        release.set()
        assert exited.wait(1)
        # Drain the actual posted completion, which must not touch any list.
        if change != "close":
            monkeypatch.setattr(frame.execution_list_model, "replace_visible_page",
                                lambda *a, **kw: pytest.fail("stale scan updated list"))
        deadline = time.monotonic() + 0.1
        while time.monotonic() < deadline:
            wx_app.Yield()
        if change != "close":
            assert list(frame.execution_list.GetStrings()) == before
    finally:
        release.set()


def test_execution_background_scan_error_automatically_retries(frame, monkeypatch, tmp_path, wx_app):
    event_loop = wx.GUIEventLoop()
    activator = wx.EventLoopActivator(event_loop)
    store = _prepare_execution_async_history(frame, tmp_path, monkeypatch)
    original, failed = store.load_recent_execution_steps, threading.Event()
    def read(*args, **kwargs):
        if threading.current_thread() is not threading.main_thread() and not failed.is_set():
            failed.set()
            raise OSError("third page temporarily failed")
        return original(*args, **kwargs)
    monkeypatch.setattr(store, "load_recent_execution_steps", read)
    frame._apply_detail_panel_mode("execution", refresh_execution=True)
    deadline = time.monotonic() + 3
    while frame._execution_list_dirty and time.monotonic() < deadline:
        wx_app.Yield()
    assert failed.is_set()
    assert not frame._execution_list_dirty
    assert frame._execution_scan_pending is None
    assert frame.execution_list.GetString(frame.execution_list.GetCount() - 1) == "小诸葛：B answer"


def test_execution_changed_page_size_discards_blocked_old_scan(frame, monkeypatch, tmp_path, wx_app):
    store = _prepare_execution_async_history(frame, tmp_path, monkeypatch)
    original = store.load_recent_execution_steps
    blocked, release, exited = threading.Event(), threading.Event(), threading.Event()
    def read(*args, **kwargs):
        if threading.current_thread() is not threading.main_thread() and kwargs["limit"] == 100:
            blocked.set()
            assert release.wait(5)
            try:
                return original(*args, **kwargs)
            finally:
                exited.set()
        return original(*args, **kwargs)
    monkeypatch.setattr(store, "load_recent_execution_steps", read)
    try:
        frame._apply_detail_panel_mode("execution", refresh_execution=True)
        assert blocked.wait(1)
        frame.execution_visible_row_limit = 200
        frame._render_execution_list(force=True)
        deadline = time.monotonic() + 2
        while frame._execution_list_dirty and time.monotonic() < deadline:
            wx_app.Yield()
        assert not frame._execution_list_dirty
        labels = _execution_content_labels(frame)
        assert labels == ["我：B question"] + [f"B step {i}" for i in range(120)] + ["小诸葛：B answer"]
        release.set()
        assert exited.wait(1)
        wx_app.Yield()
        assert _execution_content_labels(frame) == labels
        assert frame._execution_scan_pending is None
    finally:
        release.set()
