"""Desktop AGY settings exercised through the real ACP runtime."""

import os
from pathlib import Path
import shutil
import threading
import uuid

import pytest
import wx

from agy_acp import AgyAcpClient, find_agy_executable
from chat_store import ChatStore
from test_chat_tool_settings_ui_automation import choose, send


@pytest.fixture
def isolated_agy(frame, monkeypatch, tmp_path):
    if not os.getenv("MC_TEST_AGY_NATIVE"):
        pytest.skip("requires explicitly enabled, authenticated AGY")
    monkeypatch.delenv("MC_AGY_EXECUTABLE", raising=False)
    assert find_agy_executable(), "AGY must be installed for the native acceptance run"
    profile = "mc-settings-test-" + uuid.uuid4().hex
    root = Path.home() / ".gemini"
    profile_dir = root / profile
    workspace = tmp_path / "agy workspace"
    workspace.mkdir()
    command = AgyAcpClient.command
    monkeypatch.setattr(AgyAcpClient, "command", staticmethod(
        lambda executable: [*command(executable), "--app-data-dir", profile,
                            "--log-file", str(tmp_path / "agy.log")],
    ))
    monkeypatch.setattr(frame, "_workspace_dir_for_codex", lambda: str(workspace))
    try:
        yield workspace
    finally:
        frame._close_agy_clients()
        if profile_dir.is_dir():
            assert profile_dir.resolve().parent == root.resolve()
            shutil.rmtree(profile_dir)


def test_agy_new_chat_settings_and_fifo_keep_a_single_native_session(
    frame, isolated_agy, monkeypatch, wait_for_wx,
):
    workspace = isolated_agy
    marker = workspace / "input.txt"
    marker.write_text("NATIVE_TOOL_READ_831", encoding="utf-8")
    started = threading.Event()
    prompts = []
    approvals = []
    begin_prompt = AgyAcpClient.begin_prompt

    def record_prompt(client, question, on_update, on_interaction, *, attachments=()):
        def interaction(request_id, method, params):
            approvals.append(method)
            on_interaction(request_id, method, params)

        result = begin_prompt(client, question, on_update, interaction, attachments=attachments)
        prompts.append((client.session_id, {field["id"]: field["current"] for field in client.options}, question))
        if "HOLD_836" in question:
            started.set()
        return result

    def unexpected_dialog(*_args, **_kwargs):
        pytest.fail("desktop AGY creation or sending opened an unexpected dialog")

    monkeypatch.setattr(AgyAcpClient, "begin_prompt", record_prompt)
    monkeypatch.setattr(wx, "DirDialog", unexpected_dialog)
    monkeypatch.setattr(wx, "MessageBox", unexpected_dialog)
    frame.Show()
    choose(frame.tool_combo, "AGY")
    before = frame.active_chat_id
    click = wx.CommandEvent(wx.wxEVT_BUTTON, frame.new_chat_button.GetId())
    click.SetEventObject(frame.new_chat_button)
    frame.new_chat_button.GetEventHandler().ProcessEvent(click)
    wait_for_wx(lambda: frame.active_chat_id != before and bool(frame._current_chat_state.get("agy_session_id")),
                "new desktop AGY chat", timeout=25)
    owner = frame.active_chat_id
    native_id = frame._current_chat_state["agy_session_id"]
    assert frame._current_chat_state["agy_workspace"] == str(workspace)
    assert frame.model_combo.GetValue() == "Gemini 4 Neon"
    assert frame.reasoning_combo.GetValue() == "高"
    assert frame.codex_speed_combo.GetValue() == "标准"
    modes = {item.GetItemLabelText(): item for item in frame._tool_mode_menu.GetMenuItems()}
    assert modes["自动"].IsChecked()
    plan = wx.CommandEvent(wx.wxEVT_MENU, modes["计划"].GetId())
    frame._tool_mode_menu.ProcessEvent(plan)
    assert modes["计划"].IsChecked()
    auto = wx.CommandEvent(wx.wxEVT_MENU, modes["自动"].GetId())
    frame._tool_mode_menu.ProcessEvent(auto)

    def completed(index):
        wait_for_wx(lambda: len(frame.active_session_turns) > index
                    and frame.active_session_turns[index].get("request_status") in {"done", "failed", "unknown"},
                    f"AGY result at index {index}", timeout=70)
        turn = frame.active_session_turns[index]
        assert turn["request_status"] == "done", turn.get("request_error")
        return turn["answer_md"]

    send(frame, "Read the file input.txt from the current workspace with a tool. Reply with exactly its content.")
    assert "NATIVE_TOOL_READ_831" in completed(0)
    send(frame, "Remember this second seed: NATIVE_SEED_832. Reply with just that seed.")
    assert "NATIVE_SEED_832" in completed(1)
    recall = ("Return the exact marker from the answer to my first question and the seed from the answer to my "
              "second question, based on this conversation. No tool use. End with ")
    choose(frame.model_combo, "Gemini 4 Carbon")
    assert frame.reasoning_combo.GetItems() == ["低", "中", "高"]
    assert frame.reasoning_combo.GetValue() == "高"
    send(frame, recall + "MODEL_ONLY_833.")
    choose(frame.reasoning_combo, "低")
    for marker_text in ("NATIVE_TOOL_READ_831", "NATIVE_SEED_832", "MODEL_ONLY_833"):
        assert marker_text in completed(2)
    send(frame, recall + "EFFORT_ONLY_834.")
    assert all(value in completed(3) for value in ("NATIVE_TOOL_READ_831", "NATIVE_SEED_832", "EFFORT_ONLY_834"))
    choose(frame.model_combo, "Gemini 4 Neon")
    choose(frame.reasoning_combo, "中")
    send(frame, recall + "BOTH_835.")
    assert all(value in completed(4) for value in ("NATIVE_TOOL_READ_831", "NATIVE_SEED_832", "BOTH_835"))
    send(frame, recall + "HOLD_836.")
    wait_for_wx(started.is_set, "a live AGY prompt", timeout=30)
    choose(frame.model_combo, "Gemini 4 Carbon")
    choose(frame.reasoning_combo, "低")
    send(frame, recall + "FIRST_837.")
    choose(frame.model_combo, "Gemini 4 Neon")
    choose(frame.reasoning_combo, "高")
    send(frame, recall + "SECOND_838.")
    assert [turn["request_status"] for turn in frame.active_session_turns[-2:]] == ["queued", "queued"]
    assert len(prompts) == 6
    for index, token in enumerate(("HOLD_836", "FIRST_837", "SECOND_838"), 5):
        assert all(value in completed(index) for value in ("NATIVE_TOOL_READ_831", "NATIVE_SEED_832", token))

    assert {session for session, _settings, _question in prompts} == {native_id}
    assert [(settings["model"], settings["thought_level"], settings["mode"])
            for _session, settings, _question in prompts] == [
        ("gemini-4-neon", "high", "auto"), ("gemini-4-neon", "high", "auto"),
        ("gemini-4-carbon", "high", "auto"), ("gemini-4-carbon", "low", "auto"),
        ("gemini-4-neon", "medium", "auto"), ("gemini-4-neon", "medium", "auto"),
        ("gemini-4-carbon", "low", "auto"), ("gemini-4-neon", "high", "auto"),
    ]
    assert [question.rsplit(" ", 1)[-1] for _session, _settings, question in prompts[-2:]] == [
        "FIRST_837.", "SECOND_838.",
    ]
    assert approvals == []
    saved = ChatStore(frame.chat_store.db_path).load_chat(owner)
    assert saved["agy_session_id"] == native_id
    assert [turn["request_status"] for turn in saved["turns"]] == ["done"] * 8
    assert [turn["tool_settings"]["model"] for turn in saved["turns"]] == [
        settings["model"] for _session, settings, _question in prompts
    ]
