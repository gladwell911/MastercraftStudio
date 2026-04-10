import os
import json
import time
import threading

import main


class ImmediateThread:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        if self._target:
            self._target(*self._args, **self._kwargs)


class NoopThread:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        return None


def test_loaded_history_is_sent_as_context(frame, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(threading, "Thread", ImmediateThread)

    captured = {}

    class FakeChatClient:
        def __init__(self, api_key, model):
            self.api_key = api_key
            self.model = model
            captured["model"] = model

        def stream_chat(self, user_text, on_delta, history_turns=None):
            captured["user_text"] = user_text
            captured["history"] = list(history_turns or [])
            on_delta("测试增量")
            return "测试完成"

    monkeypatch.setattr(main, "ChatClient", FakeChatClient)

    frame.archived_chats = [
        {
            "id": "hist-1",
            "title": "历史会话",
            "pinned": False,
            "created_at": time.time(),
            "turns": [
                {"question": "历史问题1", "answer_md": "历史回答1", "model": "openai/gpt-5.2", "created_at": time.time()},
                {"question": "历史问题2", "answer_md": "历史回答2", "model": "openai/gpt-5.2", "created_at": time.time()},
            ],
        }
    ]
    frame._refresh_history("hist-1")
    idx = frame.history_ids.index("hist-1")
    frame.history_list.SetSelection(idx)
    assert frame._activate_selected_history()

    frame.input_edit.SetValue("继续问历史")
    frame.selected_model = "openai/gpt-5.2"
    frame.model_combo.SetValue("deepseek/deepseek-r1-0528")
    frame._on_send_clicked(None)

    assert captured["user_text"] == "继续问历史"
    assert captured["model"] == "deepseek/deepseek-r1-0528"
    assert len(captured["history"]) == 2
    assert captured["history"][0]["question"] == "历史问题1"
    assert captured["history"][1]["question"] == "历史问题2"


def test_controls_remain_focusable_after_send(frame, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")
    monkeypatch.setattr(threading, "Thread", NoopThread)

    frame.model_combo.SetValue("openai/gpt-5.2")
    frame.input_edit.SetValue("测试发送")
    frame._on_send_clicked(None)

    assert frame.is_running
    assert frame.model_combo.IsEnabled()
    assert frame.history_list.IsEnabled()


def test_activate_history_triggers_async_rename_for_current_chat(frame):
    frame.active_session_turns = [
        {"question": "当前问题", "answer_md": "当前回答", "model": "openai/gpt-5.2", "created_at": time.time()}
    ]
    frame.archived_chats = [
        {
            "id": "hist-2",
            "title": "历史2",
            "pinned": False,
            "created_at": time.time(),
            "turns": [{"question": "历史问题", "answer_md": "历史回答", "model": "openai/gpt-5.2", "created_at": time.time()}],
        }
    ]
    frame._refresh_history("hist-2")
    frame.history_list.SetSelection(frame.history_ids.index("hist-2"))

    seen = {"quick": None, "async": None}

    def fake_archive(quick_title=False, schedule_async_rename=False):
        seen["quick"] = quick_title
        seen["async"] = schedule_async_rename
        frame.active_session_turns = []
        return None

    frame._archive_active_session = fake_archive
    assert frame._activate_selected_history()
    assert seen["quick"] is True
    assert seen["async"] is True


def test_ctrl_history_navigation_walks_through_all_chats_in_order(frame):
    frame.current_chat_id = "chat-current"
    frame._current_chat_state["id"] = "chat-current"
    frame._current_chat_state["title"] = "当前聊天"
    frame._current_chat_state["turns"] = [{"question": "当前问题", "answer_md": "当前回答", "created_at": 4.0}]
    frame._current_chat_state["updated_at"] = 4.0
    frame.archived_chats = [
        {"id": "chat-a", "title": "聊天A", "turns": [], "created_at": 1.0, "updated_at": 1.0},
        {"id": "chat-b", "title": "聊天B", "turns": [], "created_at": 2.0, "updated_at": 2.0},
        {"id": "chat-c", "title": "聊天C", "turns": [], "created_at": 3.0, "updated_at": 3.0},
    ]

    assert frame._adjacent_history_chat_id(1) == "chat-c"
    assert frame._switch_current_chat("chat-c") is True
    assert frame._adjacent_history_chat_id(1) == "chat-b"
    assert frame._switch_current_chat("chat-b") is True
    assert frame._adjacent_history_chat_id(1) == "chat-a"


def test_ctrl_history_navigation_reaches_all_chats_with_pinned_history(frame):
    frame.current_chat_id = "chat-e"
    frame._current_chat_state["id"] = "chat-e"
    frame._current_chat_state["title"] = "聊天E"
    frame._current_chat_state["turns"] = [{"question": "当前问题", "answer_md": "当前回答"}]
    frame._current_chat_state["updated_at"] = 6.0
    frame.archived_chats = [
        {"id": "chat-b", "title": "聊天B", "turns": [], "created_at": 4.0, "updated_at": 4.0},
        {"id": "chat-f", "title": "置顶F", "turns": [], "created_at": 5.0, "updated_at": 5.0, "pinned": True},
        {"id": "chat-c", "title": "置顶C", "turns": [], "created_at": 3.0, "updated_at": 3.0, "pinned": True},
        {"id": "chat-a", "title": "聊天A", "turns": [], "created_at": 2.0, "updated_at": 2.0},
        {"id": "chat-d", "title": "聊天D", "turns": [], "created_at": 2.0, "updated_at": 2.0},
        {"id": "chat-g", "title": "聊天G", "turns": [], "created_at": 9.0, "updated_at": 9.0},
    ]
    frame._refresh_history()

    seen = [frame.current_chat_id]
    for _ in range(6):
        target = frame._adjacent_history_chat_id(1)
        assert target
        assert frame._switch_current_chat(target) is True
        seen.append(frame.current_chat_id)

    assert seen == ["chat-e", "chat-f", "chat-c", "chat-g", "chat-b", "chat-a", "chat-d"]


def test_voice_result_empty_after_normalization_is_skipped(frame):
    called = {"inject": 0, "type": 0, "wrong": 0}
    frame._inject_text_to_foreground_window = lambda text: called.__setitem__("inject", called["inject"] + 1) or True
    frame._type_text_to_system_focus = lambda text: called.__setitem__("type", called["type"] + 1) or True
    frame._play_voice_wrong_sound = lambda: called.__setitem__("wrong", called["wrong"] + 1)
    frame._on_voice_result("。。。！！")
    assert called["inject"] == 0
    assert called["type"] == 0
    assert called["wrong"] == 1


def test_send_uses_current_combobox_model_every_time(frame, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(threading, "Thread", ImmediateThread)

    used_models = []

    class FakeChatClient:
        def __init__(self, api_key, model):
            used_models.append(model)

        def stream_chat(self, user_text, on_delta, history_turns=None):
            on_delta("x")
            return "ok"

    monkeypatch.setattr(main, "ChatClient", FakeChatClient)

    frame.input_edit.SetValue("q1")
    frame.model_combo.SetValue("openai/gpt-5.2")
    frame._on_send_clicked(None)

    frame.input_edit.SetValue("q2")
    frame.model_combo.SetValue("qwen/qwen3.5-plus-02-15")
    frame._on_send_clicked(None)

    assert used_models[0] == "openai/gpt-5.2"
    assert used_models[1] == "qwen/qwen3.5-plus-02-15"


def test_same_session_can_switch_models_per_question(frame, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(threading, "Thread", ImmediateThread)

    used_models = []

    class FakeChatClient:
        def __init__(self, api_key, model):
            used_models.append(model)

        def stream_chat(self, user_text, on_delta, history_turns=None):
            on_delta("ok")
            return "ok"

    monkeypatch.setattr(main, "ChatClient", FakeChatClient)

    frame.input_edit.SetValue("第一个问题")
    frame.model_combo.SetStringSelection("google/gemini-3.1-pro-preview")
    frame._on_send_clicked(None)

    frame.input_edit.SetValue("第二个问题")
    frame.model_combo.SetStringSelection("openai/gpt-5.2")
    frame._on_send_clicked(None)

    assert used_models == ["google/gemini-3.1-pro-preview", "openai/gpt-5.2"]
    assert frame.active_session_turns[-2]["model"] == "google/gemini-3.1-pro-preview"
    assert frame.active_session_turns[-1]["model"] == "openai/gpt-5.2"


def test_unavailable_model_falls_back_and_still_returns_answer(frame, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(threading, "Thread", ImmediateThread)

    calls = []

    class FakeChatClient:
        def __init__(self, api_key, model):
            self.model = model
            calls.append(model)

        def stream_chat(self, user_text, on_delta, history_turns=None):
            if self.model == "deepseek/deepseek-r1-0528-qwen3-8b":
                raise RuntimeError("请求失败：HTTP 404。错误信息：No endpoints found for deepseek/deepseek-r1-0528-qwen3-8b.")
            on_delta("done")
            return "fallback answer"

    monkeypatch.setattr(main, "ChatClient", FakeChatClient)

    frame.input_edit.SetValue("q")
    frame.model_combo.SetValue("deepseek/deepseek-r1-0528-qwen3-8b")
    frame._on_send_clicked(None)

    assert calls[0] == "deepseek/deepseek-r1-0528-qwen3-8b"
    assert calls[1] == "deepseek/deepseek-r1-0528"
    assert frame.active_session_turns[-1]["model"] == "deepseek/deepseek-r1-0528"
    assert "回退" in frame.GetStatusBar().GetStatusText()


def test_startup_model_prefers_saved_selection_over_startup_default(monkeypatch, tmp_path):
    state = {
        "selected_model_id": "z-ai/glm-5",
        "archived_chats": [],
        "active_session_turns": [],
        "active_session_started_at": 0.0,
    }
    (tmp_path / "app_state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(main, "resolve_app_data_dir", lambda: tmp_path)
    f = main.ChatFrame()
    try:
        assert f.selected_model == "z-ai/glm-5"
        assert f.model_combo.GetValue() == "z-ai/glm-5"
    finally:
        f.Destroy()

def test_enter_send_path_respects_ime_candidates(frame):
    sent = {"n": 0}
    frame._trigger_send = lambda: sent.__setitem__("n", sent["n"] + 1)

    class E:
        def GetKeyCode(self):
            return main.wx.WXK_RETURN

        def ControlDown(self):
            return False

        def AltDown(self):
            return False

        def Skip(self):
            pass

    frame._has_input_ime_candidates = lambda: True
    frame._on_input_key_down(E())
    assert sent["n"] == 0

    frame._has_input_ime_candidates = lambda: False
    frame._on_input_key_down(E())
    assert sent["n"] == 1
