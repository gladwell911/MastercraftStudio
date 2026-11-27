import json
import threading

import pytest
import wx

from agy_acp import AGY_MODEL_ID
from codex_client import CodexEvent, DEFAULT_CODEX_MODEL
from mc_frame_settings import REQUESTING_TEXT
from test_codex_native_history import codex_homes, real_codex


def test_chat_tools_have_keyboard_order_and_accessible_names(frame, wait_for_wx, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODEX_BIN", str(tmp_path / "unavailable.exe"))
    frame.Show()
    frame.Raise()
    frame.new_chat_button.SetFocus()
    wait_for_wx(frame.new_chat_button.HasFocus, "focus on new chat")
    for name in ("工具", "模型", "推理强度", "速度"):
        control = wx.Window.FindFocus()
        tab = wx.KeyEvent(wx.wxEVT_KEY_DOWN)
        tab.SetKeyCode(wx.WXK_TAB)
        tab.SetEventObject(control)
        control.GetEventHandler().ProcessEvent(tab)
        wait_for_wx(lambda: wx.Window.FindFocus() and wx.Window.FindFocus().GetName() == name,
                    f"focus on {name}")

    assert frame.tool_combo.GetItems() == ["Codex", "AGY", "Kimi Code", "其他"]


def choose(combo, label):
    assert combo.SetStringSelection(label)
    event = wx.CommandEvent(wx.wxEVT_COMBOBOX, combo.GetId())
    event.SetEventObject(combo)
    combo.GetEventHandler().ProcessEvent(event)


def send(frame, question):
    frame.input_edit.SetValue(question)
    event = wx.CommandEvent(wx.wxEVT_BUTTON, frame.send_button.GetId())
    event.SetEventObject(frame.send_button)
    frame.send_button.GetEventHandler().ProcessEvent(event)
    assert frame.input_edit.GetValue() == ""


@pytest.fixture
def codex_model_request_gate():
    started, release = threading.Event(), threading.Event()
    yield started, release
    release.set()


@real_codex
def test_codex_keeps_completed_history_after_model_and_effort_changes(
    codex_homes, frame, wait_for_wx, monkeypatch, codex_model_request_gate,
):
    _ordinary, _private, work, _other_work, calls, _native = codex_homes
    monkeypatch.setattr(frame, "_workspace_dir_for_codex", lambda: str(work))
    frame.Show()
    frame.model_combo.SetFocus()
    wait_for_wx(lambda: {"GPT-6.2-Sol", "GPT-6.2-Astra"} <= set(frame.model_combo.GetItems()),
                "native Codex model catalog", timeout=30)

    def submit(question):
        count = len(frame.active_session_turns)
        send(frame, question)
        wait_for_wx(lambda: len(frame.active_session_turns) == count + 1
                    and frame.active_session_turns[-1].get("request_status") in {"done", "failed"},
                    f"the answer to {question}", timeout=20)
        turn = frame.active_session_turns[-1]
        assert turn["request_status"] == "done", turn.get("request_error")
        assert "CODX_08_FULL_TAIL" in turn["answer_md"]
        return turn

    choose(frame.model_combo, "GPT-6.2-Sol")
    choose(frame.reasoning_combo, "中")
    first = submit("Seed phrase ORCHARD_801")
    second = submit("Seed phrase RIVER_802")
    choose(frame.model_combo, "GPT-6.2-Astra")
    model_only = submit("Check MODEL_ONLY_803")
    choose(frame.reasoning_combo, "高")
    effort_only = submit("Check EFFORT_ONLY_804")
    choose(frame.model_combo, "GPT-6.2-Sol")
    choose(frame.reasoning_combo, "极高")
    both = submit("Check BOTH_805")

    native_ids = {turn.get("codex_thread_id") for turn in (first, second, model_only, effort_only, both)}
    assert None not in native_ids and "" not in native_ids and len(native_ids) == 1
    assert len({turn["codex_turn_id"] for turn in (first, second, model_only, effort_only, both)}) == 5
    assert [(call["model"], call["reasoning"]["effort"]) for call in calls] == [
        ("gpt-6.2-sol", "medium"), ("gpt-6.2-sol", "medium"), ("gpt-6.2-astra", "medium"),
        ("gpt-6.2-astra", "high"), ("gpt-6.2-sol", "xhigh"),
    ]
    assert all("ORCHARD_801" in str(call["input"]) and "RIVER_802" in str(call["input"]) for call in calls[2:])

    started, release = codex_model_request_gate
    send(frame, "MC_SETTINGS_HOLD")
    wait_for_wx(started.is_set, "an outstanding native turn", timeout=10)
    choose(frame.model_combo, "GPT-6.2-Astra")
    choose(frame.reasoning_combo, "低")
    choose(frame.codex_speed_combo, "快速")
    send(frame, "Queued change FIRST_807")
    choose(frame.model_combo, "GPT-6.2-Sol")
    choose(frame.reasoning_combo, "高")
    choose(frame.codex_speed_combo, "标准")
    send(frame, "Queued change SECOND_808")
    assert [turn["request_status"] for turn in frame.active_session_turns[-2:]] == ["queued", "queued"]
    assert len(calls) == 6
    release.set()
    wait_for_wx(lambda: all(turn.get("request_status") == "done" for turn in frame.active_session_turns[-3:]),
                "both queued requests and the held turn complete", timeout=25)
    assert {turn["codex_thread_id"] for turn in frame.active_session_turns} == native_ids
    assert len({turn["codex_turn_id"] for turn in frame.active_session_turns}) == 8
    assert "FIRST_807" in str(calls[6]["input"][-1])
    assert "SECOND_808" in str(calls[7]["input"][-1])
    assert [(call["model"], call["reasoning"]["effort"], call.get("service_tier")) for call in calls[-2:]] == [
        ("gpt-6.2-astra", "low", "priority"), ("gpt-6.2-sol", "high", None),
    ]


@real_codex
def test_explicit_config_queues_behind_turn_started_with_native_defaults(
    codex_homes, frame, wait_for_wx, monkeypatch, codex_model_request_gate,
):
    _ordinary, _private, work, _other_work, calls, _native = codex_homes
    monkeypatch.setattr(frame, "_workspace_dir_for_codex", lambda: str(work))
    started, release = codex_model_request_gate
    frame.Show()
    send(frame, "MC_SETTINGS_HOLD with legacy config")
    wait_for_wx(started.is_set, "the legacy-config native turn", timeout=10)
    frame.model_combo.SetFocus()
    wait_for_wx(lambda: "GPT-6.2-Astra" in frame.model_combo.GetItems(), "native Codex model catalog", timeout=20)
    choose(frame.model_combo, "GPT-6.2-Astra")
    choose(frame.reasoning_combo, "高")
    send(frame, "Independent new settings MC_SETTINGS_LEGACY_FOLLOWUP")
    assert frame.active_session_turns[-1]["request_status"] == "queued"
    release.set()
    wait_for_wx(lambda: all(turn["request_status"] == "done" for turn in frame.active_session_turns),
                "the original and independent Codex turns", timeout=20)
    first, last = frame.active_session_turns
    assert first["codex_thread_id"] == last["codex_thread_id"]
    assert first["codex_turn_id"] != last["codex_turn_id"]
    assert len(calls) == 2
    assert (calls[-1]["model"], calls[-1]["reasoning"]["effort"]) == ("gpt-6.2-astra", "high")

@pytest.fixture
def codex_queue(frame, monkeypatch):
    chat_id = "queued-codex-chat"
    thread_id = "019d36ab-804a-73a2-a2dd-7a17e181628f"
    turn_id = "019d36b3-0a1c-7c61-aed9-387f6afbb9f9"
    turns = [
        {"question": "Previous question", "answer_md": "Previous answer",
         "model": DEFAULT_CODEX_MODEL, "request_status": "done",
         "codex_thread_id": thread_id, "codex_turn_id": turn_id},
        {"question": "First queued question", "request_question": "First queued question",
         "model": DEFAULT_CODEX_MODEL, "request_status": "queued", "codex_settings_force_new": True,
         "tool_settings": {"tool": "codex", "model": "gpt-6.2-astra", "effort": "low"}},
        {"question": "Second queued question", "request_question": "Second queued question",
         "model": DEFAULT_CODEX_MODEL, "request_status": "queued", "codex_settings_force_new": True,
         "tool_settings": {"tool": "codex", "model": "gpt-6.2-sol", "effort": "high"}},
    ]
    chat = {"id": chat_id, "title": "Codex queue", "model": DEFAULT_CODEX_MODEL,
            "codex_thread_id": thread_id, "codex_turn_id": turn_id, "turns": turns}
    frame.chat_store.upsert_chat(chat)
    frame.chat_store.replace_turns(chat_id, turns)
    started = []
    submitted = threading.Event()

    def record_start(*args, **kwargs):
        started.append(args)
        submitted.set()

    monkeypatch.setattr(frame, "_run_codex_turn_worker", record_start)
    monkeypatch.setattr(frame, "_request_codex_chat_information", lambda *_: None)
    monkeypatch.setattr(frame, "_schedule_first_question_auto_title", lambda *_: None)
    return chat, started, submitted


def test_codex_queue_starts_while_status_is_pending(frame, codex_queue, monkeypatch, wx_app):
    chat, started, submitted = codex_queue
    frame.active_chat_id = frame.current_chat_id = chat["id"]
    frame.active_session_turns = chat["turns"]
    frame._current_chat_state = chat
    chat["codex_turn_active"] = frame.active_codex_turn_active = True
    chat["turns"][0].update(request_status="pending", answer_md=REQUESTING_TEXT)
    monkeypatch.setattr(frame, "_start_codex_local_command_worker_for_turn", lambda *_: None)
    send(frame, "/status")
    assert chat["turns"][3]["local_command"] == "status"
    frame._on_codex_event_for_chat(chat["id"], CodexEvent(
        type="turn_completed", status="completed", text="Previous answer",
        thread_id=chat["codex_thread_id"], turn_id=chat["codex_turn_id"],
        data={"turn_idx": 0, "completion_owners": [{"turn_idx": 0, "context_generation": 0}]},
    ))
    wx_app.ProcessPendingEvents()
    assert submitted.wait(timeout=1)
    assert len(started) == 1
    assert started[0][0:3] == (chat["id"], 1, "First queued question")
    assert [turn["request_status"] for turn in chat["turns"]] == ["done", "pending", "queued", "pending"]


@pytest.mark.parametrize("restore", ["startup", "history"])
def test_persisted_codex_queue_resumes_in_order(frame, codex_queue, wx_app, restore):
    chat, started, submitted = codex_queue
    # Old builds could persist the status command as active Codex work.
    frame.chat_store.upsert_chat({**chat, "codex_turn_active": True})
    if restore == "startup":
        state = {"active_chat_id": chat["id"], "active_codex_turn_active": True}
        frame.state_path.write_text(json.dumps(state), encoding="utf-8")
        frame._load_state()
    else:
        frame.archived_chats = frame.chat_store.list_chat_summaries()
        assert frame._show_history_chat(chat["id"], focus_answer_list=False)

    wx_app.ProcessPendingEvents()
    assert submitted.wait(timeout=1)
    assert len(started) == 1
    assert started[0][0:3] == (chat["id"], 1, "First queued question")
    _, turns, _ = frame._chat_target_for_request(chat["id"])
    assert [turn["request_status"] for turn in turns] == ["done", "pending", "queued"]
    assert turns[1]["tool_settings"]["model"] == "gpt-6.2-astra"


def test_codex_clear_cancels_previous_settings_queue(frame, codex_queue, monkeypatch, wx_app):
    chat, started, _ = codex_queue
    frame.active_chat_id = frame.current_chat_id = chat["id"]
    frame.active_session_turns = chat["turns"]
    frame._current_chat_state = chat
    commands = []
    monkeypatch.setattr(frame, "_start_codex_local_command_worker_for_turn", lambda *args: commands.append(args))
    frame._schedule_next_codex_tool_turn(chat["id"])
    send(frame, "/clear")
    wx_app.ProcessPendingEvents()
    assert [turn["request_status"] for turn in chat["turns"]] == ["done", "queued", "queued", "pending"]

    frame._run_codex_local_command_worker(*commands[0])
    wx_app.ProcessPendingEvents()

    assert not started
    assert [turn["request_status"] for turn in chat["turns"]] == ["done", "cancelled", "cancelled", "done"]


def test_agy_chat_rejects_codex_selection_and_keeps_draft(frame, monkeypatch, tmp_path):
    assert frame._on_new_chat_clicked(None)
    chat = frame._current_chat_state
    chat.update(model=AGY_MODEL_ID, agy_workspace=str(tmp_path), agy_session_id="agy-existing")
    original_turns = list(frame.active_session_turns)
    frame._apply_selected_chat_model_to_combo(chat)
    choose(frame.tool_combo, "Codex")
    messages = []
    monkeypatch.setattr(wx, "MessageBox", lambda message, *_: messages.append(message))
    started = []
    monkeypatch.setattr(frame, "_start_agy_worker_for_turn", lambda *args, **kwargs: started.append(args))
    source = frame.active_chat_id
    draft = "  保留原聊天里的提问  "
    frame.input_edit.ChangeValue(draft)
    frame.input_edit.SetSelection(2, 5)

    event = wx.CommandEvent(wx.wxEVT_BUTTON, frame.send_button.GetId())
    event.SetEventObject(frame.send_button)
    frame.send_button.GetEventHandler().ProcessEvent(event)

    assert messages == ["此聊天使用 AGY。请选回 AGY，或新建聊天后使用所选工具。"]
    assert frame.input_edit.GetValue() == draft
    assert frame.input_edit.GetSelection() == (2, 5)
    assert frame.active_chat_id == source
    assert frame.active_session_turns == original_turns
    assert not started
