import copy
import ctypes
import main
import pytest
import threading
import time
import wx
from main_test_support import (
    _QuietKeyEvent,
    _Story13KeyEvent,
    _UnmodifiedSpaceEvent,
    _prepare_execution_async_history,
)
from pathlib import Path
from types import SimpleNamespace


def test_focused_unknown_separator_survives_identity_preserving_reload(frame):
    row = {"step": "unknown", "detail_text": "immutable", "created_at": None}
    frame.current_chat_id = frame.active_chat_id = "focus-legacy"
    frame._current_chat_state = {
        "id": "focus-legacy", "revision": 5, "turns": [], "detail_panel_mode": "execution",
        "execution_steps": [row],
    }
    frame._render_execution_list(force=True)
    time_index = next(i for i, meta in enumerate(frame.execution_meta) if meta[0] == "time")
    frame.execution_list.SetSelection(time_index)
    selected_id = frame.execution_list_model.selected_id()
    frame._acquire_execution_focus_lease()
    frame._current_chat_state["execution_steps"] = [copy.deepcopy(row)]
    frame._render_execution_list(force=True)
    assert frame.execution_list_model.selected_id() == selected_id
    assert frame.execution_list.GetSelection() == time_index


def test_focused_second_identical_legacy_row_blocks_ambiguous_older_prepend(frame):
    duplicate = {"step": "same", "detail_text": "identical immutable legacy payload", "created_at": None}
    frame.current_chat_id = frame.active_chat_id = "duplicate-prepend"
    frame._current_chat_state = {
        "id": "duplicate-prepend", "revision": 6, "turns": [], "detail_panel_mode": "execution",
        "execution_steps": [copy.deepcopy(duplicate), copy.deepcopy(duplicate)],
    }
    frame._render_execution_list(force=True)
    content_indexes = [i for i, meta in enumerate(frame.execution_meta) if meta[0] != "time"]
    second_content_index = content_indexes[1]
    second_content_id = frame.execution_list_model.visible_ids[second_content_index]
    second_time_id = frame.execution_list_model.visible_ids[second_content_index - 1]
    frame.execution_list.SetSelection(second_content_index - 1)
    frame._acquire_execution_focus_lease()
    prior_ids = list(frame.execution_list_model.visible_ids)
    prior_labels = list(frame.execution_list.GetStrings())
    prior_meta = list(frame.execution_meta)

    frame._current_chat_state["execution_steps"] = [
        copy.deepcopy(duplicate),
        *copy.deepcopy(frame._current_chat_state["execution_steps"]),
    ]
    frame._render_execution_list(force=True)

    assert len(frame._current_chat_state["execution_steps"]) == 3
    assert frame.execution_list_model.visible_ids == prior_ids
    assert list(frame.execution_list.GetStrings()) == prior_labels
    assert frame.execution_meta == prior_meta
    assert frame.execution_list_model.selected_id() == second_time_id
    assert frame.execution_list_model.visible_ids[frame.execution_list.GetSelection() + 1] == second_content_id

    # A hydration/reload deep copy must reproduce the same end-relative IDs.
    frame._current_chat_state["execution_steps"] = copy.deepcopy(frame._current_chat_state["execution_steps"])
    frame._render_execution_list(force=True)
    assert frame.execution_list_model.selected_id() == second_time_id
    assert frame.execution_list_model.visible_ids[frame.execution_list.GetSelection() + 1] == second_content_id


def test_send_shortcut_mapping(frame):
    assert frame._is_send_shortcut(wx.WXK_RETURN, ctrl=False, alt=False)
    assert frame._is_send_shortcut(wx.WXK_NUMPAD_ENTER, ctrl=False, alt=False)
    assert not frame._is_send_shortcut(wx.WXK_RETURN, ctrl=True, alt=False)


def test_continue_shortcut_mapping(frame):
    assert frame._is_continue_shortcut(ord("C"), alt=True)
    assert frame._is_continue_shortcut(ord("c"), alt=True)
    assert not frame._is_continue_shortcut(ord("C"), alt=False)


def test_clear_context_shortcut_mapping(frame):
    assert frame._is_clear_context_shortcut(ord("A"), alt=True)
    assert frame._is_clear_context_shortcut(ord("a"), alt=True)
    assert not frame._is_clear_context_shortcut(ord("A"), alt=False)


def test_navigation_quiet_trigger_keys(frame):
    keys = [
        main.wx.WXK_TAB,
        main.wx.WXK_UP,
        main.wx.WXK_DOWN,
        main.wx.WXK_LEFT,
        main.wx.WXK_RIGHT,
        main.wx.WXK_HOME,
        main.wx.WXK_END,
        main.wx.WXK_PAGEUP,
        main.wx.WXK_PAGEDOWN,
        main.wx.WXK_RETURN,
        main.wx.WXK_NUMPAD_ENTER,
        main.wx.WXK_SPACE,
    ]
    for key in keys:
        assert frame._is_navigation_quiet_trigger(_QuietKeyEvent(key))
    assert frame._is_navigation_quiet_trigger(_QuietKeyEvent(main.wx.WXK_TAB, shift=True))
    for key in "ASDFGBCasdfgbc":
        assert frame._is_navigation_quiet_trigger(_QuietKeyEvent(ord(key), alt=True))
    assert not frame._is_navigation_quiet_trigger(_QuietKeyEvent(main.wx.WXK_TAB, ctrl=True))
    assert not frame._is_navigation_quiet_trigger(_QuietKeyEvent(main.wx.WXK_RETURN, ctrl=True))
    assert not frame._is_navigation_quiet_trigger(_QuietKeyEvent(ord("A"), alt=True, ctrl=True))
    assert not frame._is_navigation_quiet_trigger(_QuietKeyEvent(ord("X"), alt=True))
    assert not frame._is_navigation_quiet_trigger(_QuietKeyEvent(ord("A"), alt=False))


def test_navigation_quiet_window_extends_on_repeated_trigger(frame, monkeypatch):
    times = iter([100.0, 100.0, 101.5, 101.5])
    monkeypatch.setattr(main.time, "monotonic", lambda: next(times))

    frame._touch_navigation_quiet_window(_QuietKeyEvent(main.wx.WXK_DOWN))
    assert frame._navigation_quiet_until == 103.0

    frame._touch_navigation_quiet_window(_QuietKeyEvent(main.wx.WXK_UP))
    assert frame._navigation_quiet_until == 104.5


def test_char_hook_touches_quiet_window_before_alt_a_clear(frame, monkeypatch):
    frame.active_chat_id = "chat-a"
    frame.current_chat_id = "chat-a"
    frame._current_chat_state = {"id": "chat-a", "turns": []}
    observed = []

    def fake_clear(**_kwargs):
        observed.append(frame._navigation_quiet_until)
        return True

    monkeypatch.setattr(main.time, "monotonic", lambda: 200.0)
    monkeypatch.setattr(frame, "_trigger_shortcut_clear", fake_clear)

    event = _QuietKeyEvent(ord("A"), alt=True)
    frame._on_char_hook(event)

    assert observed == [203.0]


def test_input_key_down_touches_quiet_window_before_send_shortcut(frame, monkeypatch):
    observed = []
    monkeypatch.setattr(main.time, "monotonic", lambda: 300.0)
    monkeypatch.setattr(frame, "_trigger_send", lambda: observed.append(frame._navigation_quiet_until))

    event = _QuietKeyEvent(main.wx.WXK_RETURN)
    frame._on_input_key_down(event)

    assert observed == [303.0]


def test_quiet_window_schedules_flush_after_expiration(frame, monkeypatch):
    scheduled = []
    monkeypatch.setattr(main.time, "monotonic", lambda: 10.0)
    monkeypatch.setattr(frame, "_call_later_if_alive", lambda delay, fn, *args: scheduled.append((delay, fn.__name__)) or object())

    frame._touch_navigation_quiet_window(_QuietKeyEvent(main.wx.WXK_DOWN))

    assert scheduled == [(3000, "_flush_pending_background_ui_updates")]


def test_fixed_domain_remote_start_rebuilds_runtime_after_connectivity_failure(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_TOKEN", "secret")
    monkeypatch.setenv("REMOTE_CONTROL_DOMAIN", "rc.tingyou.cc")

    created = []

    class _FakeNatsProcess:
        def __init__(self, config, bundled_dir=None):
            self.stopped = False
            created.append(self)

        def start(self, timeout=10):
            return SimpleNamespace()

        def stop(self):
            self.stopped = True

    class _FakeNatsTransport:
        def __init__(self, **kwargs):
            self.stopped = False

        def start_threaded(self, url, timeout=10):
            return None

        def stop(self):
            self.stopped = True

    attempts = {"n": 0}

    def _fake_connectivity(**_kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("public probe failed")
        frame.remote_control_runtime_status["public_ws_ready"] = True

    monkeypatch.setattr(main, "NatsServerProcess", _FakeNatsProcess)
    monkeypatch.setattr(main, "RemoteNatsTransport", _FakeNatsTransport)
    monkeypatch.setattr(frame, "_ensure_cloudflared_origin_bridge", lambda: None)
    monkeypatch.setattr(frame, "SetStatusText", lambda _text: None)
    monkeypatch.setattr(frame, "_ensure_remote_nats_startup_connectivity", _fake_connectivity)

    frame._start_remote_nats_runtime_if_configured(ensure_connectivity=True)

    assert attempts["n"] == 2
    assert len(created) == 2
    assert created[0].stopped is True
    assert frame.remote_control_runtime_status["public_ws_ready"] is True
    assert frame.remote_control_runtime_url == "wss://rc.tingyou.cc/nats?token=secret"


def test_char_hook_alt_c_does_not_continue_from_unrelated_focus(frame):
    seen = {}
    frame._submit_question = lambda question, **kwargs: seen.setdefault("call", (question, kwargs)) or (True, "")
    frame.history_list.SetFocus()

    class E:
        def GetKeyCode(self):
            return ord("C")

        def ControlDown(self):
            return False

        def AltDown(self):
            return True

        def Skip(self):
            seen["skipped"] = True

    frame._on_char_hook(E())

    assert "call" not in seen
    assert seen["skipped"] is True


def test_answer_list_ctrl_c_char_hook_copies_without_moving_selection(frame, monkeypatch):
    copied = {"text": None}

    class _Clipboard:
        def Open(self):
            return True

        def SetData(self, data):
            copied["text"] = data.GetText()
            return True

        def Flush(self):
            return True

        def Close(self):
            return True

    monkeypatch.setattr(main.wx, "TheClipboard", _Clipboard())
    monkeypatch.setattr(frame.answer_list, "HasFocus", lambda: True)
    monkeypatch.setattr(frame.notes_notebook_list, "HasFocus", lambda: False)
    monkeypatch.setattr(frame.notes_entry_list, "HasFocus", lambda: False)
    monkeypatch.setattr(frame.notes_editor, "HasFocus", lambda: False)

    frame.answer_meta = [
        ("question", 0, "first question", ""),
        ("answer", 0, "plain answer", "rich answer"),
    ]
    frame.active_session_turns = [{"question": "first question", "answer_md": "rich answer", "model": main.DEFAULT_MODEL_ID}]
    frame.answer_list.Clear()
    frame.answer_list.Append("first question")
    frame.answer_list.Append("answer row")
    frame.answer_list.SetSelection(1)

    class E:
        def GetKeyCode(self):
            return ord("C")

        def ControlDown(self):
            return True

        def AltDown(self):
            return False

        def Skip(self):
            raise AssertionError("Ctrl+C in the answer list should be consumed before native list handling")

    frame._on_char_hook(E())

    assert copied["text"] == "rich answer"
    assert frame.answer_list.GetSelection() == 1


def test_char_hook_ctrl_up_switches_to_previous_chat_from_any_focus(frame):
    frame.active_chat_id = "chat-b"
    frame.current_chat_id = "chat-b"
    frame._current_chat_state.update({"id": "chat-b", "updated_at": 2.0})
    frame.archived_chats = [
        {"id": "chat-a", "title": "聊天A", "turns": [], "created_at": 1.0, "updated_at": 1.0},
        {"id": "chat-c", "title": "聊天C", "turns": [], "created_at": 3.0, "updated_at": 3.0},
    ]
    frame._refresh_history()
    seen = {}
    frame.answer_list.SetFocus()
    frame._show_history_chat = lambda chat_id, **_kwargs: seen.setdefault("chat_id", chat_id) or True

    class E:
        def GetKeyCode(self):
            return wx.WXK_UP

        def ControlDown(self):
            return True

        def AltDown(self):
            return False

        def Skip(self):
            raise AssertionError("should not skip")

    frame._on_char_hook(E())

    assert seen["chat_id"] == "chat-c"


def test_char_hook_ctrl_down_switches_to_next_chat_from_any_focus(frame):
    frame.active_chat_id = "chat-b"
    frame.current_chat_id = "chat-b"
    frame._current_chat_state.update({"id": "chat-b", "updated_at": 2.0})
    frame.archived_chats = [
        {"id": "chat-a", "title": "聊天A", "turns": [], "created_at": 1.0, "updated_at": 1.0},
        {"id": "chat-c", "title": "聊天C", "turns": [], "created_at": 3.0, "updated_at": 3.0},
    ]
    frame._refresh_history()
    seen = {}
    frame.input_edit.SetFocus()
    frame._show_history_chat = lambda chat_id, **_kwargs: seen.setdefault("chat_id", chat_id) or True

    class E:
        def GetKeyCode(self):
            return wx.WXK_DOWN

        def ControlDown(self):
            return True

        def AltDown(self):
            return False

        def Skip(self):
            raise AssertionError("should not skip")

    frame._on_char_hook(E())

    assert seen["chat_id"] == "chat-a"


def test_input_key_down_ctrl_up_switches_to_previous_chat(frame):
    frame.active_chat_id = "chat-b"
    frame.current_chat_id = "chat-b"
    frame._current_chat_state.update({"id": "chat-b", "updated_at": 2.0})
    frame.archived_chats = [
        {"id": "chat-a", "title": "聊天A", "turns": [], "created_at": 1.0, "updated_at": 1.0},
        {"id": "chat-c", "title": "聊天C", "turns": [], "created_at": 3.0, "updated_at": 3.0},
    ]
    frame._refresh_history()
    seen = {"skipped": 0}
    frame._show_history_chat = lambda chat_id, **_kwargs: seen.setdefault("chat_id", chat_id) or True

    class E:
        def GetKeyCode(self):
            return wx.WXK_UP

        def ControlDown(self):
            return True

        def AltDown(self):
            return False

        def Skip(self):
            seen["skipped"] += 1

    frame._on_input_key_down(E())

    assert seen["chat_id"] == "chat-c"
    assert seen["skipped"] == 0


def test_input_key_down_ctrl_down_switches_to_next_chat(frame):
    frame.active_chat_id = "chat-b"
    frame.current_chat_id = "chat-b"
    frame._current_chat_state.update({"id": "chat-b", "updated_at": 2.0})
    frame.archived_chats = [
        {"id": "chat-a", "title": "聊天A", "turns": [], "created_at": 1.0, "updated_at": 1.0},
        {"id": "chat-c", "title": "聊天C", "turns": [], "created_at": 3.0, "updated_at": 3.0},
    ]
    frame._refresh_history()
    seen = {"skipped": 0}
    frame._show_history_chat = lambda chat_id, **_kwargs: seen.setdefault("chat_id", chat_id) or True

    class E:
        def GetKeyCode(self):
            return wx.WXK_DOWN

        def ControlDown(self):
            return True

        def AltDown(self):
            return False

        def Skip(self):
            seen["skipped"] += 1

    frame._on_input_key_down(E())

    assert seen["chat_id"] == "chat-a"
    assert seen["skipped"] == 0


def test_generic_key_down_ctrl_down_switches_to_next_chat(frame):
    frame.active_chat_id = "chat-b"
    frame.current_chat_id = "chat-b"
    frame._current_chat_state["id"] = "chat-b"
    frame._current_chat_state["title"] = "聊天B"
    frame._current_chat_state["updated_at"] = 2.0
    frame.archived_chats = [
        {"id": "chat-a", "title": "聊天A", "turns": [], "created_at": 1.0, "updated_at": 1.0},
        {"id": "chat-c", "title": "聊天C", "turns": [], "created_at": 3.0, "updated_at": 3.0},
    ]
    frame._refresh_history()
    seen = {"skipped": 0}

    class E:
        def GetKeyCode(self):
            return wx.WXK_DOWN

        def ControlDown(self):
            return True

        def AltDown(self):
            return False

        def Skip(self):
            seen["skipped"] += 1

    frame._on_generic_key_down(E())

    assert frame.view_mode == "history"
    assert frame.view_history_id == "chat-a"
    assert seen["skipped"] == 0


def test_input_enter_during_ime_composition_does_not_send(frame):
    sent = {"n": 0}
    frame._trigger_send = lambda: sent.__setitem__("n", sent["n"] + 1)
    frame._has_input_ime_candidates = lambda: True

    class E:
        def GetKeyCode(self):
            return wx.WXK_RETURN

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            pass

    frame._on_input_key_down(E())
    assert sent["n"] == 0


def test_char_hook_enter_during_ime_composition_does_not_send(frame):
    sent = {"n": 0}
    frame._trigger_send = lambda: sent.__setitem__("n", sent["n"] + 1)
    frame.input_edit.SetFocus()

    class E:
        def GetKeyCode(self):
            return wx.WXK_RETURN

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            pass

    frame._on_char_hook(E())
    assert sent["n"] == 0


def test_char_hook_enter_without_candidates_still_does_not_send_when_input_has_focus(frame):
    sent = {"n": 0}
    frame._trigger_send = lambda: sent.__setitem__("n", sent["n"] + 1)
    frame._has_input_ime_candidates = lambda: False
    frame.input_edit.SetFocus()

    class E:
        def GetKeyCode(self):
            return wx.WXK_RETURN

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            pass

    frame._on_char_hook(E())
    assert sent["n"] == 0


def test_input_enter_without_ime_candidates_sends(frame):
    sent = {"n": 0}
    frame._trigger_send = lambda: sent.__setitem__("n", sent["n"] + 1)
    frame._has_input_ime_candidates = lambda: False

    class E:
        def GetKeyCode(self):
            return wx.WXK_RETURN

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            pass

    frame._on_input_key_down(E())
    assert sent["n"] == 1


@pytest.mark.parametrize("modifier", ["shift", "ctrl", "alt"])
def test_input_key_down_story_1_3_modified_enter_is_native_and_never_sends(frame, modifier):
    event = _Story13KeyEvent(**{modifier: True})
    sent = []
    frame._has_input_ime_candidates = lambda: False
    frame._trigger_send = lambda: sent.append(True)
    frame._on_input_key_down(event)
    assert event.skipped == 1
    assert sent == []


def test_input_key_down_story_1_3_repeat_is_consumed_without_send(frame):
    event = _Story13KeyEvent(repeat=True)
    sent = []
    frame._has_input_ime_candidates = lambda: False
    frame._trigger_send = lambda: sent.append(True)
    frame._on_input_key_down(event)
    assert event.skipped == 0
    assert sent == []


@pytest.mark.parametrize("enabled,value", [(False, "question"), (True, "  ")])
def test_story_1_3_question_rejection_preserves_exact_value_selection_and_focus(frame, enabled, value):
    frame.Show()
    frame.input_edit.SetValue(value)
    frame.input_edit.SetSelection(0, min(1, len(value)))
    frame.input_edit.SetFocus()
    frame.send_button.Enable(enabled)
    before = (frame.input_edit.GetValue(), frame.input_edit.GetSelection())
    assert frame._request_question_submit() is False
    assert (frame.input_edit.GetValue(), frame.input_edit.GetSelection()) == before
    assert frame.input_edit.HasFocus()


@pytest.mark.parametrize("candidate_state", ["composition", "candidate"])
def test_story_1_3_input_ime_composition_or_candidate_owns_enter(frame, candidate_state):
    event = _Story13KeyEvent()
    sent = []
    frame._has_input_ime_candidates = lambda: True
    frame._trigger_send = lambda: sent.append(True)
    frame._on_input_key_down(event)
    assert event.skipped == 1 and sent == []


def test_f1_moves_focus_to_execution_list_then_back(frame, monkeypatch):
    frame._current_chat_state = {
        "id": "chat-1",
        "title": "切换测试",
        "turns": [],
        "detail_panel_mode": "answers",
        "execution_steps": [{"step": "第一步"}],
    }
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    focus = {"answer": True, "execution": False}
    counts = {"answer": 0, "execution": 0}
    monkeypatch.setattr(frame.answer_list, "HasFocus", lambda: focus["answer"])
    monkeypatch.setattr(frame.execution_list, "HasFocus", lambda: focus["execution"])
    monkeypatch.setattr(frame.answer_list, "SetFocus", lambda: (focus.__setitem__("answer", True), focus.__setitem__("execution", False), counts.__setitem__("answer", counts["answer"] + 1)))
    monkeypatch.setattr(frame.execution_list, "SetFocus", lambda: (focus.__setitem__("answer", False), focus.__setitem__("execution", True), counts.__setitem__("execution", counts["execution"] + 1)))

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
    assert counts["execution"] == 1
    assert counts["answer"] == 0

    frame._on_char_hook(E())
    assert frame._current_chat_state["detail_panel_mode"] == "answers"
    assert counts["answer"] == 1
    assert counts["execution"] == 1


def test_answer_shift_enter_keeps_web_detail_shortcut(frame, monkeypatch):
    frame.answer_meta = [("answer", 0, "plain", "detail")]
    frame.answer_list.Clear()
    frame.answer_list.Append("answer row")
    frame.answer_list.SetSelection(0)
    opened_viewer = []
    opened_web = []
    monkeypatch.setattr(frame, "_open_selected_answer_text_viewer", lambda: opened_viewer.append(True) or True)
    monkeypatch.setattr(frame, "_try_open_selected_answer_detail", lambda: opened_web.append(True) or True)

    class E:
        def GetKeyCode(self):
            return wx.WXK_RETURN

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def ShiftDown(self):
            return True

        def Skip(self):
            raise AssertionError("Shift+Enter should be handled")

    frame._on_answer_key_down(E())

    assert opened_viewer == []
    assert opened_web == [True]


def test_answer_char_hook_enter_opens_plain_text_viewer(frame, monkeypatch):
    frame.answer_meta = [("answer", 0, "plain", "detail")]
    frame.answer_list.Clear()
    frame.answer_list.Append("answer row")
    frame.answer_list.SetSelection(0)
    monkeypatch.setattr(frame.answer_list, "HasFocus", lambda: True)
    opened_viewer = []
    opened_web = []
    monkeypatch.setattr(frame, "_open_selected_answer_text_viewer", lambda: opened_viewer.append(True) or True)
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

    frame._on_char_hook(E())

    assert opened_viewer == [True]
    assert opened_web == []


def test_answer_char_hook_shift_enter_opens_web_detail(frame, monkeypatch):
    frame.answer_meta = [("answer", 0, "plain", "detail")]
    frame.answer_list.Clear()
    frame.answer_list.Append("answer row")
    frame.answer_list.SetSelection(0)
    monkeypatch.setattr(frame.answer_list, "HasFocus", lambda: True)
    opened_viewer = []
    opened_web = []
    monkeypatch.setattr(frame, "_open_selected_answer_text_viewer", lambda: opened_viewer.append(True) or True)
    monkeypatch.setattr(frame, "_try_open_selected_answer_detail", lambda: opened_web.append(True) or True)

    class E:
        def GetKeyCode(self):
            return wx.WXK_RETURN

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def ShiftDown(self):
            return True

        def Skip(self):
            raise AssertionError("Shift+Enter should be handled")

    frame._on_char_hook(E())

    assert opened_viewer == []
    assert opened_web == [True]


@pytest.mark.parametrize(
    "control_name",
    ("notes_notebook_list", "notes_entry_list", "history_list", "answer_list", "model_combo"),
)
def test_char_hook_space_from_named_navigation_control_focuses_input_and_consumes(frame, monkeypatch, control_name):
    control = getattr(frame, control_name)
    focused = []
    monkeypatch.setattr(frame.input_edit, "SetFocus", lambda: focused.append(True))
    control.SetFocus()
    event = _UnmodifiedSpaceEvent()

    frame._on_char_hook(event)

    assert focused == [True]
    assert event.skipped == 0


def test_char_hook_space_with_disabled_input_is_consumed_without_scheduling_quiet_work(frame, monkeypatch):
    frame.history_list.SetFocus()
    monkeypatch.setattr(frame.input_edit, "IsEnabled", lambda: False)
    monkeypatch.setattr(frame.input_edit, "SetFocus", lambda: pytest.fail("disabled input must not receive focus"))
    monkeypatch.setattr(frame, "_touch_navigation_quiet_window", lambda _event: pytest.fail("recognized Space must not schedule quiet work"))
    event = _UnmodifiedSpaceEvent()

    frame._on_char_hook(event)

    assert event.skipped == 0


@pytest.mark.parametrize(
    "handler_name,control_name",
    (
        ("_on_notes_key_down", "notes_notebook_list"),
        ("_on_notes_key_down", "notes_entry_list"),
        ("_on_history_key_down", "history_list"),
        ("_on_answer_key_down", "answer_list"),
        ("_on_generic_key_down", "model_combo"),
    ),
)
def test_named_navigation_key_handlers_consume_space_before_default_behavior(frame, monkeypatch, handler_name, control_name):
    control = getattr(frame, control_name)
    focused = []
    monkeypatch.setattr(frame, "_focus_input_box", lambda: focused.append(True) or True)
    control.SetFocus()
    event = _UnmodifiedSpaceEvent()

    getattr(frame, handler_name)(event)

    assert focused == [True]
    assert event.skipped == 0


@pytest.mark.parametrize(
    "control_name,event_kwargs",
    (
        ("input_edit", {}),
        ("notes_editor", {}),
        ("codex_speed_combo", {}),
        ("answer_list", {"ctrl": True}),
        ("history_list", {"alt": True}),
        ("model_combo", {"shift": True}),
    ),
)
def test_char_hook_space_keeps_ordinary_behavior_outside_shortcut_scope(frame, monkeypatch, control_name, event_kwargs):
    focused = []
    monkeypatch.setattr(frame, "_focus_input_box", lambda: focused.append(True) or True)
    getattr(frame, control_name).SetFocus()
    event = _UnmodifiedSpaceEvent(**event_kwargs)

    frame._on_char_hook(event)

    assert focused == []
    assert event.skipped == 1


def test_answer_text_viewer_dialog_copy_button_copies_current_text_and_tab_moves_focus(frame, monkeypatch):
    copied = []
    monkeypatch.setattr(frame, "_set_clipboard_text", lambda text: copied.append(text) or True)
    dlg = main.AnswerTextViewerDialog(frame, "回答详情", "原始内容")
    try:
        dlg.text_ctrl.SetValue("\n编辑后的当前内容")
        skipped = []

        class E:
            def GetKeyCode(self):
                return wx.WXK_TAB

            def ControlDown(self):
                return False

            def AltDown(self):
                return False

            def ShiftDown(self):
                return False

            def Skip(self):
                skipped.append(True)

        dlg._on_char_hook(E())

        assert copied == []
        assert skipped == [True]
        assert dlg.copy_button.GetLabel() == "复制"
        dlg._on_copy_clicked()
        assert copied == ["编辑后的当前内容"]
        assert dlg.text_ctrl.GetWindowStyleFlag() & wx.TE_DONTWRAP
        assert dlg.canonical_text == "原始内容"
    finally:
        if dlg:
            dlg.Destroy()


def test_answer_text_viewer_clipboard_exception_keeps_scratch_selection_and_canonical(frame, monkeypatch):
    def fail(_text):
        raise RuntimeError("clipboard unavailable")

    monkeypatch.setattr(frame, "_set_clipboard_text", fail)
    dlg = main.AnswerTextViewerDialog(frame, "回答详情", "canonical")
    try:
        dlg.text_ctrl.SetValue("\nscratch")
        dlg.text_ctrl.SetSelection(1, 4)

        assert dlg._copy_scratch_to_clipboard() is False
        assert dlg.text_ctrl.GetValue() == "\nscratch"
        assert dlg.text_ctrl.GetSelection() == (1, 4)
        assert dlg.canonical_text == "canonical"
    finally:
        dlg.Destroy()


@pytest.mark.parametrize("composition,candidates", [(True, False), (False, True)])
def test_native_ime_helper_detects_composition_or_candidates(frame, monkeypatch, composition, candidates):
    class Imm32:
        def ImmGetContext(self, _hwnd): return 1
        def ImmReleaseContext(self, *_args): return 1
        def ImmGetCompositionStringW(self, *_args): return 2 if composition else 0
        def ImmGetCandidateListCountW(self, _himc, count, length):
            count._obj.value = 1 if candidates else 0
            length._obj.value = 24 if candidates else 0
            return 1
        def ImmGetCandidateListW(self, _himc, _idx, buf, _size):
            if buf is None:
                return 24
            raw = ctypes.cast(buf, ctypes.POINTER(ctypes.c_uint32))
            raw[0] = 24
            raw[2] = 1
            return 24

    monkeypatch.setattr(main.ctypes, "windll", SimpleNamespace(imm32=Imm32()))
    assert frame._has_native_ime_composition(frame.input_edit) is True


@pytest.mark.parametrize("shift", [False, True])
def test_common_command_edit_dialog_title_enter_focuses_content(frame, monkeypatch, shift):
    dlg = main.CommonCommandEditDialog(frame, dialog_title="Add")
    try:
        monkeypatch.setattr(main.wx.Window, "FindFocus", lambda: dlg.title_edit)
        dlg._has_ime_composition = lambda: False
        focused = []
        monkeypatch.setattr(dlg.content_edit, "SetFocus", lambda: focused.append(True))
        event = _Story13KeyEvent(shift=shift)
        dlg._on_char_hook(event)
        assert focused == [True] and event.skipped == 0
    finally: dlg.Destroy()


def test_common_command_edit_dialog_ime_and_ordinary_keys_are_isolated(frame, monkeypatch):
    dlg = main.CommonCommandEditDialog(frame, dialog_title="Add", initial_content="x")
    try:
        monkeypatch.setattr(main.wx.Window, "FindFocus", lambda: dlg.content_edit)
        dlg._has_ime_composition=lambda: True
        ime = _Story13KeyEvent(); dlg._on_char_hook(ime); assert ime.skipped == 1
        dlg._has_ime_composition=lambda: False
        ordinary = _Story13KeyEvent(ord("A")); dlg._on_char_hook(ordinary); assert ordinary.skipped == 1
    finally: dlg.Destroy()


@pytest.mark.parametrize("control_name", ["title_edit", "content_edit"])
def test_common_command_edit_dialog_delegates_ime_for_focused_text_control(frame, monkeypatch, control_name):
    dlg = main.CommonCommandEditDialog(frame, dialog_title="Add")
    try:
        control = getattr(dlg, control_name); seen = []
        monkeypatch.setattr(main.wx.Window, "FindFocus", lambda: control)
        monkeypatch.setattr(frame, "_has_native_ime_composition", lambda target: seen.append(target) or True)
        assert dlg._has_ime_composition() is True
        assert seen == [control]
    finally: dlg.Destroy()


@pytest.mark.parametrize("event", [_Story13KeyEvent(ctrl=True, repeat=True), _Story13KeyEvent(ord("S"), alt=True, repeat=True)])
def test_common_command_edit_dialog_repeated_save_shortcut_is_noop(frame, monkeypatch, event):
    calls=[]; dlg=main.CommonCommandEditDialog(frame, dialog_title="Add", initial_content="x", on_save=lambda *v: calls.append(v) or True)
    try:
        monkeypatch.setattr(main.wx.Window, "FindFocus", lambda: dlg.content_edit)
        dlg._has_ime_composition=lambda: False; dlg._on_char_hook(event); assert calls == []
    finally: dlg.Destroy()


@pytest.mark.parametrize("event", [_Story13KeyEvent(), _Story13KeyEvent(ord("S"), alt=True)])
def test_story_1_3_frame_char_hook_never_submits_from_non_input(frame, monkeypatch, event):
    calls=[]; monkeypatch.setattr(frame, "_trigger_send", lambda: calls.append(True))
    frame.history_list.SetFocus(); frame._on_char_hook(event); assert calls == []


def test_global_ctrl_arrow_noops_when_app_is_not_foreground(frame, monkeypatch):
    seen = []
    monkeypatch.setattr(frame, "_global_chat_navigation_target_state", lambda: (False, {"frame_hwnd": 1, "fg_hwnd": 2, "root_hwnd": 2}))
    monkeypatch.setattr(frame, "_navigate_history_chats", lambda direction: seen.append(direction) or True)

    frame._on_global_ctrl_arrow("down")

    assert seen == []


def test_global_ctrl_arrow_logs_reason_when_app_is_not_foreground(frame, monkeypatch):
    monkeypatch.setenv("MC_CTRL_NAVIGATION_DEBUG", "1")
    statuses = []
    monkeypatch.setattr(frame, "_global_chat_navigation_target_state", lambda: (False, {"frame_hwnd": 1, "fg_hwnd": 2, "root_hwnd": 2}))
    monkeypatch.setattr(frame, "SetStatusText", statuses.append)

    frame._on_global_ctrl_arrow("down")

    log_text = (frame.app_data_dir / "ctrl_navigation_debug.log").read_text(encoding="utf-8")
    assert "direction=down" in log_text
    assert "reason=inactive_target" in log_text
    assert statuses[-1] == "Ctrl+上下未生效：当前前台焦点不属于本程序"


def test_global_ctrl_arrow_logs_successful_navigation(frame, monkeypatch):
    monkeypatch.setenv("MC_CTRL_NAVIGATION_DEBUG", "1")
    statuses = []
    seen = []
    monkeypatch.setattr(frame, "_global_chat_navigation_target_state", lambda: (True, {"frame_hwnd": 1, "fg_hwnd": 1, "root_hwnd": 1}))
    monkeypatch.setattr(frame, "_navigate_history_chats", lambda direction: seen.append(direction) or True)
    monkeypatch.setattr(frame, "SetStatusText", statuses.append)

    frame._on_global_ctrl_arrow("up")

    log_text = (frame.app_data_dir / "ctrl_navigation_debug.log").read_text(encoding="utf-8")
    assert seen == [-1]
    assert "direction=up" in log_text
    assert "result=navigated" in log_text
    assert statuses == []


def test_f1_focuses_latest_execution_item(frame, monkeypatch):
    frame.Show()
    frame.active_chat_id = "chat-1"
    frame.current_chat_id = "chat-1"
    frame._current_chat_state = {
        "id": "chat-1",
        "title": "执行焦点",
        "turns": [],
        "detail_panel_mode": "answers",
        "execution_steps": [
            {"event_type": "plan_updated", "display_kind": "plan", "list_text": "第一步", "detail_text": "第一步详情"},
            {"event_type": "plan_updated", "display_kind": "plan", "list_text": "第二步", "detail_text": "第二步详情"},
        ],
    }
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)

    class F1Event:
        def GetKeyCode(self):
            return wx.WXK_F1

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            raise AssertionError("F1 should be handled")

    frame.input_edit.SetFocus()
    frame._on_char_hook(F1Event())

    assert frame.execution_list.HasFocus()
    assert frame.execution_list.GetSelection() == frame.execution_list.GetCount() - 1
    assert frame.execution_list.GetStringSelection() == "第二步"


def test_f1_focuses_empty_execution_placeholder(frame, monkeypatch):
    frame.Show()
    frame.active_chat_id = "chat-empty"
    frame.current_chat_id = "chat-empty"
    frame._current_chat_state = {
        "id": "chat-empty",
        "title": "空执行列表",
        "turns": [],
        "detail_panel_mode": "answers",
        "execution_steps": [],
    }
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)

    class F1Event:
        def GetKeyCode(self):
            return wx.WXK_F1

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            raise AssertionError("F1 should be handled")

    frame.input_edit.SetFocus()
    frame._on_char_hook(F1Event())

    assert frame.execution_list.HasFocus()
    assert frame.execution_list.GetSelection() == 0
    assert frame.execution_list.GetString(0) == "暂无执行过程"


def test_char_hook_enter_on_execution_list_opens_text_viewer_without_sending(frame, monkeypatch):
    frame.Show()
    frame._current_chat_state["detail_panel_mode"] = "execution"
    frame.execution_list.Set(["执行步骤"])
    frame.execution_meta = [("execution", 0, "执行步骤", "详情")]
    frame.execution_list.SetSelection(0)
    frame.execution_list.SetFocus()
    opened_viewer = []
    opened_web = []
    sends = []
    monkeypatch.setattr(frame, "_open_selected_execution_text_viewer", lambda: opened_viewer.append(True) or True)
    monkeypatch.setattr(frame, "_try_open_selected_execution_detail", lambda: opened_web.append(True) or True)
    monkeypatch.setattr(frame, "_trigger_send", lambda: sends.append(True))

    class EnterEvent:
        def GetKeyCode(self):
            return wx.WXK_RETURN

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            raise AssertionError("Enter should be handled by execution list")

    frame._on_char_hook(EnterEvent())

    assert opened_viewer == [True]
    assert opened_web == []
    assert sends == []


def test_char_hook_shift_enter_on_execution_list_opens_web_detail(frame, monkeypatch):
    frame.Show()
    frame._current_chat_state["detail_panel_mode"] = "execution"
    frame.execution_list.Set(["执行步骤"])
    frame.execution_meta = [("execution", 0, "执行步骤", "详情")]
    frame.execution_list.SetSelection(0)
    frame.execution_list.SetFocus()
    opened_viewer = []
    opened_web = []
    monkeypatch.setattr(frame, "_open_selected_execution_text_viewer", lambda: opened_viewer.append(True) or True)
    monkeypatch.setattr(frame, "_try_open_selected_execution_detail", lambda: opened_web.append(True) or True)

    class EnterEvent:
        def GetKeyCode(self):
            return wx.WXK_RETURN

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def ShiftDown(self):
            return True

        def Skip(self):
            raise AssertionError("Shift+Enter should be handled by execution list")

    frame._on_char_hook(EnterEvent())

    assert opened_viewer == []
    assert opened_web == [True]


def test_on_done_focuses_latest_answer_item_after_appending(frame, monkeypatch):
    frame.Show()
    frame.active_chat_id = "chat-answer-focus"
    frame.current_chat_id = "chat-answer-focus"
    frame.active_turn_idx = 0
    frame.active_session_turns = [
        {"question": "问题", "answer_md": main.REQUESTING_TEXT, "model": "openai/gpt-5.2", "created_at": time.time()}
    ]
    frame._current_chat_state.update({"id": "chat-answer-focus", "turns": frame.active_session_turns})
    monkeypatch.setattr(frame, "_save_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_refresh_history", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_final_answer", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_play_finish_sound", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_can_focus_completion_result", lambda: True)

    frame._on_done(0, "最终回答", "", "openai/gpt-5.2", "", "chat-answer-focus")

    assert frame.answer_list.HasFocus()
    assert frame.answer_list.GetStringSelection() == "最终回答"


def test_global_ctrl_combo_key_filter_ignores_ime_and_modifiers():
    hook = main.GlobalCtrlTapHook(lambda *_a, **_k: None)
    assert not hook._should_mark_combo_key(main.VK_PROCESSKEY)
    assert not hook._should_mark_combo_key(main.VK_PACKET)
    assert not hook._should_mark_combo_key(main.VK_SHIFT)
    assert not hook._should_mark_combo_key(main.VK_MENU)
    assert not hook._should_mark_combo_key(main.VK_LWIN)
    assert hook._should_mark_combo_key(ord("C"))


def test_accessible_text_update_notifies_value_change_without_focus_event(frame, monkeypatch):
    calls = []

    class _User32:
        def NotifyWinEvent(self, event, hwnd, objid, childid):
            calls.append((event, objid, childid))

    class _Window:
        def GetHandle(self):
            return 123

    monkeypatch.setattr(main.ctypes, "windll", type("Windll", (), {"user32": _User32()})())

    frame._notify_accessible_text_update(_Window())

    assert calls == [
        (main.EVENT_OBJECT_VALUECHANGE, main.OBJID_CLIENT, main.CHILDID_SELF),
    ]


def test_char_hook_alt_skips_to_native_menu_without_opening_tools(frame):
    seen = {"opened": 0, "skipped": 0}
    frame._show_tools_menu = lambda: seen.__setitem__("opened", seen["opened"] + 1)

    class E:
        def GetKeyCode(self):
            return wx.WXK_ALT

        def ControlDown(self):
            return False

        def AltDown(self):
            return True

        def Skip(self):
            seen["skipped"] += 1

    frame._on_char_hook(E())

    assert seen["opened"] == 0
    assert seen["skipped"] == 1
    assert frame._alt_menu_armed is False
    assert frame._alt_menu_suppressed is False


def test_input_key_up_alt_does_not_open_tools_menu_when_armed(frame):
    seen = {"opened": 0, "skipped": 0}
    frame._alt_menu_armed = True
    frame._alt_menu_suppressed = False
    frame._show_tools_menu = lambda: seen.__setitem__("opened", seen["opened"] + 1)

    class E:
        def GetKeyCode(self):
            return wx.WXK_ALT

        def Skip(self):
            seen["skipped"] += 1

    frame._on_input_key_up(E())

    assert seen["opened"] == 0
    assert seen["skipped"] == 1
    assert frame._alt_menu_armed is False
    assert frame._alt_menu_suppressed is False


def test_tools_menu_includes_load_image_or_file_action(frame, monkeypatch):
    captured = {"items": []}

    def _popup(menu, *_args):
        captured["items"] = [
            (item.GetItemLabelText(), item.GetId())
            for item in menu.GetMenuItems()
            if not item.IsSeparator()
        ]

    monkeypatch.setattr(frame, "PopupMenu", _popup)

    frame._show_tools_menu()

    labels = [label for label, _item_id in captured["items"]]
    assert "载入图片或文件" in labels


def test_char_hook_alt_c_outside_viewer_does_not_submit_continue(frame):
    seen = {"opened": 0, "submitted": 0}
    frame._alt_menu_armed = True
    frame._alt_menu_suppressed = False
    frame._show_tools_menu = lambda: seen.__setitem__("opened", seen["opened"] + 1)
    frame._submit_question = lambda question, **kwargs: seen.__setitem__("submitted", seen["submitted"] + 1) or (True, "")

    class E:
        def GetKeyCode(self):
            return ord("C")

        def ControlDown(self):
            return False

        def AltDown(self):
            return True

        def Skip(self):
            seen["skipped"] = True

    frame._on_char_hook(E())

    assert seen["opened"] == 0
    assert seen["submitted"] == 0
    assert seen["skipped"] is True
    assert frame._alt_menu_armed is True
    assert frame._alt_menu_suppressed is True


def test_input_key_up_alt_does_not_open_tools_menu_after_continue_shortcut(frame):
    seen = {"opened": 0}
    frame._alt_menu_armed = True
    frame._alt_menu_suppressed = True
    frame._show_tools_menu = lambda: seen.__setitem__("opened", seen["opened"] + 1)

    class E:
        def GetKeyCode(self):
            return wx.WXK_ALT

        def Skip(self):
            return None

    frame._on_input_key_up(E())

    assert seen["opened"] == 0
    assert frame._alt_menu_armed is False
    assert frame._alt_menu_suppressed is False


def test_input_key_down_alt_s_does_not_submit_from_question_input(frame):
    seen = {"send": 0}
    frame._alt_menu_armed = True
    frame._alt_menu_suppressed = False
    frame._trigger_send = lambda: seen.__setitem__("send", seen["send"] + 1)

    class E:
        def GetKeyCode(self):
            return ord("S")

        def ControlDown(self):
            return False

        def AltDown(self):
            return True

        def Skip(self):
            return None

    frame._on_input_key_down(E())

    assert seen["send"] == 0
    assert frame._alt_menu_suppressed is True


def test_input_key_up_alt_does_not_open_tools_menu_after_other_key(frame):
    seen = {"opened": 0}
    frame._alt_menu_armed = True
    frame._alt_menu_suppressed = True
    frame._show_tools_menu = lambda: seen.__setitem__("opened", seen["opened"] + 1)

    class E:
        def GetKeyCode(self):
            return wx.WXK_ALT

        def Skip(self):
            return None

    frame._on_input_key_up(E())

    assert seen["opened"] == 0


def test_listbox_diff_uses_cached_labels_without_rescanning_control(frame, monkeypatch):
    frame._replace_listbox_items_if_changed(frame.history_list, ["A", "B"], 1)
    monkeypatch.setattr(frame.history_list, "GetString", lambda _idx: pytest.fail("unchanged diff should use cached labels"))

    changed = frame._replace_listbox_items_if_changed(frame.history_list, ["A", "B"], 1)

    assert changed is False


def test_focus_latest_answer_skips_when_completion_window_is_not_foreground(frame, monkeypatch):
    frame.answer_meta = [("answer", 0, "小诸葛", ""), ("answer", 1, "小诸葛", "最新回答")]
    calls = {"selection": 0, "focus": 0}
    monkeypatch.setattr(frame, "_can_focus_completion_result", lambda: False)
    monkeypatch.setattr(
        frame.answer_list,
        "SetSelection",
        lambda *_args, **_kwargs: calls.__setitem__("selection", calls["selection"] + 1),
    )
    monkeypatch.setattr(
        frame.answer_list,
        "SetFocus",
        lambda *_args, **_kwargs: calls.__setitem__("focus", calls["focus"] + 1),
    )

    frame._focus_latest_answer()

    assert calls["selection"] == 0
    assert calls["focus"] == 0


def test_focus_latest_answer_only_focuses_when_completion_window_is_foreground(frame, monkeypatch):
    frame.answer_meta = [("answer", 0, "小诸葛", ""), ("answer", 1, "小诸葛", "最新回答")]
    calls = {"selection": 0, "focus": 0}
    monkeypatch.setattr(frame, "_can_focus_completion_result", lambda: True)
    monkeypatch.setattr(
        frame.answer_list,
        "SetSelection",
        lambda *_args, **_kwargs: calls.__setitem__("selection", calls["selection"] + 1),
    )
    monkeypatch.setattr(
        frame.answer_list,
        "SetFocus",
        lambda *_args, **_kwargs: calls.__setitem__("focus", calls["focus"] + 1),
    )

    frame._focus_latest_answer()

    assert calls["selection"] == 1
    assert calls["focus"] == 1


def test_focus_latest_answer_does_not_steal_focus_after_user_tabs_away(frame, monkeypatch):
    frame.answer_meta = [("answer", 0, "小诸葛", ""), ("answer", 1, "小诸葛", "最新回答")]
    monkeypatch.setattr(frame, "IsActive", lambda: True)
    monkeypatch.setattr(frame, "IsIconized", lambda: False)
    monkeypatch.setattr(wx.Window, "FindFocus", lambda: frame.history_list)
    monkeypatch.setattr(frame.answer_list, "SetFocus", lambda: pytest.fail("completion focus should not steal focus from history"))

    frame._focus_latest_answer()


def test_escape_in_input_does_not_minimize_via_char_hook(frame):
    frame.input_edit.SetFocus()
    minimized = {"n": 0}
    frame._minimize_to_tray = lambda: minimized.__setitem__("n", minimized["n"] + 1)

    class E:
        def GetKeyCode(self):
            return wx.WXK_ESCAPE

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            pass

    frame._on_char_hook(E())
    assert minimized["n"] == 0


def test_real_escape_keydown_minimizes(frame):
    minimized = {"n": 0}
    frame._minimize_to_tray = lambda: minimized.__setitem__("n", minimized["n"] + 1)

    class E:
        def GetKeyCode(self):
            return wx.WXK_ESCAPE

        def GetRawKeyCode(self):
            return 27

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            pass

    frame._on_any_key_down_escape_minimize(E())
    assert minimized["n"] == 1


def test_escape_keydown_with_raw_188_does_not_minimize(frame):
    minimized = {"n": 0}
    frame._minimize_to_tray = lambda: minimized.__setitem__("n", minimized["n"] + 1)

    class E:
        def GetKeyCode(self):
            return wx.WXK_ESCAPE

        def GetRawKeyCode(self):
            return 188  # VK_OEM_COMMA

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            pass

    frame._on_any_key_down_escape_minimize(E())
    assert minimized["n"] == 0


def test_escape_keydown_with_raw_0_does_not_minimize(frame):
    minimized = {"n": 0}
    frame._minimize_to_tray = lambda: minimized.__setitem__("n", minimized["n"] + 1)

    class E:
        def GetKeyCode(self):
            return wx.WXK_ESCAPE

        def GetRawKeyCode(self):
            return 0

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            pass

    frame._on_any_key_down_escape_minimize(E())
    assert minimized["n"] == 0


def test_non_escape_keydown_with_raw_27_does_not_minimize(frame):
    minimized = {"n": 0}
    frame._minimize_to_tray = lambda: minimized.__setitem__("n", minimized["n"] + 1)
    skipped = {"n": 0}

    class E:
        def GetKeyCode(self):
            return ord("A")

        def GetRawKeyCode(self):
            return 27

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            skipped["n"] += 1

    frame._on_any_key_down_escape_minimize(E())
    assert minimized["n"] == 0
    assert skipped["n"] == 0


def test_frame_key_down_still_skips_non_escape_for_native_handling(frame):
    skipped = {"n": 0}

    class E:
        def GetKeyCode(self):
            return ord("A")

        def GetRawKeyCode(self):
            return 65

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def ShiftDown(self):
            return False

        def Skip(self):
            skipped["n"] += 1

    frame._on_frame_key_down(E())

    assert skipped["n"] == 1


def test_packaging_spec_excludes_missing_realtime_dialog_hidden_imports():
    spec_text = Path("zgwd.spec").read_text(encoding="utf-8")
    assert "realtime_dialog" not in spec_text


def test_window_focus_shortcuts_route_to_expected_controls(frame, monkeypatch):
    focused = []
    for name in ("answer_list", "execution_list", "input_edit", "history_list", "notes_notebook_list", "notes_entry_list"):
        control = getattr(frame, name)
        monkeypatch.setattr(control, "SetFocus", lambda n=name: focused.append(n))

    class AltEvent:
        def __init__(self, key):
            self.key = key
            self.skipped = 0

        def GetKeyCode(self):
            return self.key

        def ControlDown(self):
            return False

        def AltDown(self):
            return True

        def Skip(self):
            self.skipped += 1

    frame._current_chat_state["detail_panel_mode"] = "answers"
    frame._on_char_hook(AltEvent(ord("F")))
    frame._on_char_hook(AltEvent(ord("D")))
    frame._on_char_hook(AltEvent(ord("G")))
    shortcut_focuses = focused[:]

    frame._notes_select_notebook("", view="notes_list")
    focused[:] = shortcut_focuses
    frame._on_char_hook(AltEvent(ord("B")))

    notebook = frame.notes_store.create_notebook("shortcut notebook")
    frame.notes_store.create_entry(notebook.id, "shortcut entry", source="manual")
    frame._notes_select_notebook(notebook.id, view="note_detail")
    focused[:] = shortcut_focuses + ["notes_notebook_list"]
    frame._on_char_hook(AltEvent(ord("B")))

    frame._current_chat_state["detail_panel_mode"] = "execution"
    frame._on_char_hook(AltEvent(ord("F")))

    assert focused == [
        "answer_list",
        "input_edit",
        "history_list",
        "notes_notebook_list",
        "notes_entry_list",
        "execution_list",
    ]


def test_notes_lists_count_as_primary_navigation_focus(frame, monkeypatch):
    for control_name in (
        "execution_list",
        "answer_list",
        "history_list",
        "input_edit",
        "model_combo",
        "notes_notebook_list",
        "notes_entry_list",
    ):
        control = getattr(frame, control_name)
        monkeypatch.setattr(control, "HasFocus", lambda c=control: c is frame.notes_notebook_list)

    assert frame._primary_navigation_control_has_focus() is True

    for control_name in (
        "execution_list",
        "answer_list",
        "history_list",
        "input_edit",
        "model_combo",
        "notes_notebook_list",
        "notes_entry_list",
    ):
        control = getattr(frame, control_name)
        monkeypatch.setattr(control, "HasFocus", lambda c=control: c is frame.notes_entry_list)

    assert frame._primary_navigation_control_has_focus() is True


def test_model_changed_noops_without_state_save_when_selection_is_unchanged(frame, monkeypatch):
    frame.selected_model = "codex/main"
    frame.model_combo.SetValue("codex")
    frame.active_chat_id = "chat-model-unchanged"
    frame.current_chat_id = "chat-model-unchanged"
    frame._current_chat_state = {"id": "chat-model-unchanged", "model": "codex/main"}
    deferred = []
    pushes = []
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: deferred.append(True))
    monkeypatch.setattr(frame, "_push_remote_state", lambda *args, **kwargs: pushes.append((args, kwargs)))

    frame._on_model_changed(None)

    assert deferred == []
    assert pushes == []


def test_model_changed_updates_only_candidate_without_committing_or_publishing(frame, monkeypatch):
    frame.selected_model = "codex/main"
    frame.active_chat_id = "chat-model-selection"
    frame.current_chat_id = "chat-model-selection"
    frame._current_chat_state = {
        "id": "chat-model-selection",
        "title": "model selection",
        "model": "codex/main",
        "turns": frame.active_session_turns,
    }
    deferred = []
    published = []
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: deferred.append(True))
    monkeypatch.setattr(frame, "_broadcast_remote_event", lambda payload: published.append(payload))

    for model in ("openai/gpt-5.2", "google/gemini-3.1-pro-preview", "codex/main"):
        frame.model_combo.SetValue(main.model_display_name(model))
        frame._on_model_changed(None)

        assert frame.selected_model == model
        assert frame._current_chat_state["model"] == "codex/main"

    assert deferred == []
    assert published == []

    frame._on_model_changed(None)

    assert deferred == []
    assert published == []


def test_model_changed_ignores_unsupported_model_without_persisting_or_pushing(frame, monkeypatch):
    frame.selected_model = "codex/main"
    frame.active_chat_id = "chat-model-hidden"
    frame.current_chat_id = "chat-model-hidden"
    frame._current_chat_state = {"id": "chat-model-hidden", "model": "codex/main"}
    deferred = []
    published = []
    monkeypatch.setattr(frame, "_resolve_current_model", lambda: "openai/gpt-5.2-codex")
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: deferred.append(True))
    monkeypatch.setattr(frame, "_broadcast_remote_event", lambda payload: published.append(payload))

    frame._on_model_changed(None)

    assert frame.selected_model == "codex/main"
    assert frame._current_chat_state["model"] == "codex/main"
    assert deferred == []
    assert published == []


def test_answer_arrow_selection_does_not_refocus_when_list_already_has_focus(frame, monkeypatch):
    frame.answer_list.Append("first")
    frame.answer_list.Append("second")
    frame.answer_list.SetSelection(0)
    monkeypatch.setattr(frame.answer_list, "HasFocus", lambda: True)
    monkeypatch.setattr(frame.answer_list, "SetFocus", lambda: pytest.fail("arrow navigation should not emit redundant focus events"))

    assert frame._move_answer_list_selection_for_key(main.wx.WXK_DOWN) is True

    assert frame.answer_list.GetSelection() == 1


@pytest.mark.parametrize("leave", [False, True])
def test_execution_async_f1_consumes_latest_intent_without_recapturing_focus(frame, monkeypatch, tmp_path, wx_app, leave):
    event_loop = wx.GUIEventLoop()
    activator = wx.EventLoopActivator(event_loop)
    frame.Show()
    wx_app.Yield()
    store = _prepare_execution_async_history(frame, tmp_path, monkeypatch)
    blocked, release = threading.Event(), threading.Event()
    original = store.load_recent_execution_steps
    def read(*args, **kwargs):
        if threading.current_thread() is not threading.main_thread():
            blocked.set()
            assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(store, "load_recent_execution_steps", read)
    try:
        event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
        event.SetKeyCode(wx.WXK_F1)
        frame._on_char_hook(event)
        assert blocked.wait(1)
        assert frame.execution_list.HasFocus()
        assert frame._execution_latest_intent is not None
        if leave:
            frame.input_edit.SetFocus()
        release.set()
        deadline = time.monotonic() + 3
        while frame._execution_list_dirty and time.monotonic() < deadline:
            wx_app.Yield()
        assert not frame._execution_list_dirty
        assert frame._execution_latest_intent is None
        if leave:
            assert frame.input_edit.HasFocus()
        else:
            assert frame.execution_list.HasFocus()
            assert frame.execution_list.GetStringSelection() == "小诸葛：B answer"
    finally:
        release.set()


def test_offscreen_clear_resend_dispatches_once_without_active_chat_or_focus_change(frame, monkeypatch):
    archived = {"id": "chat-archived", "model": main.DEFAULT_CODEX_MODEL, "turns": []}
    frame.archived_chats = [archived]
    frame.active_chat_id = "chat-active"
    frame.current_chat_id = "chat-active"
    frame._current_chat_state = {"id": "chat-active", "turns": [{"question": "active stays"}]}
    frame.active_session_turns = frame._current_chat_state["turns"]
    claims = []
    transitions = []
    frame.chat_store = SimpleNamespace(
        get_clear_operation=lambda _op: {**operation, "snapshot": payload},
        claim_clear_resend_dispatch=lambda op, **_identity: claims.append(op) or ({"operation_id": op} if len(claims) == 1 else None),
        consume_clear_resend_sound=lambda op, *, chat_id, revision: (
            {"operation_id": op} if (chat_id, revision) == ("chat-archived", 2) else None
        ),
        transition_clear_operation=lambda op, state, **kw: transitions.append((op, state, kw)),
    )
    starts = []
    sounds = []
    monkeypatch.setattr(frame, "_start_codex_worker_for_turn", lambda *args: starts.append(args))
    monkeypatch.setattr(frame, "_play_send_sound", lambda: sounds.append("send"))
    monkeypatch.setattr(frame, "_mark_chat_turns_dirty", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: None)
    operation = {"operation_id": "op-1", "chat_id": "chat-archived", "revision": 2}
    payload = {"question": "archived first", "model": main.DEFAULT_CODEX_MODEL}

    assert frame._dispatch_clear_operation_resend(operation, payload) is True
    assert frame._dispatch_clear_operation_resend(operation, payload) is False
    assert starts == [("chat-archived", 0, "archived first", main.DEFAULT_CODEX_MODEL)]
    assert archived["turns"][0]["clear_operation_id"] == "op-1"
    assert frame.active_chat_id == frame.current_chat_id == "chat-active"
    assert frame.active_session_turns == [{"question": "active stays"}]
    assert transitions == []
    assert sounds == ["send"]


def test_set_clipboard_text_flushes_only_when_requested(frame, monkeypatch):
    counts = {"flush": 0}

    class _Clipboard:
        def Open(self):
            return True

        def SetData(self, data):
            return True

        def Flush(self):
            counts["flush"] += 1
            return True

        def Close(self):
            return True

    monkeypatch.setattr(main.wx, "TheClipboard", _Clipboard())
    assert frame._set_clipboard_text("普通复制") is True
    assert counts["flush"] == 0
    assert frame._set_clipboard_text("导出删除", flush=True) is True
    assert counts["flush"] == 1


def test_set_clipboard_text_flush_failure_returns_false(frame, monkeypatch):
    class _Clipboard:
        def Open(self):
            return True

        def SetData(self, data):
            return True

        def Flush(self):
            return False

        def Close(self):
            return True

    monkeypatch.setattr(main.wx, "TheClipboard", _Clipboard())
    assert frame._set_clipboard_text("导出删除", flush=True) is False
