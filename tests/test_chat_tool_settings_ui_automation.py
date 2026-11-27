import threading

import pytest
import wx
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
