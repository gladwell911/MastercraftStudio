import json
import sys

import pytest
import wx

from agy_acp import AgyAcpClient
from codex_client import DEFAULT_CODEX_MODEL
from test_chat_tool_settings_ui_automation import choose, send


@pytest.fixture
def handoff_runtime(frame, tmp_path, monkeypatch, request):
    request.addfinalizer(frame._close_agy_clients)
    project = tmp_path / "source-project"
    project.mkdir()
    log = project / "requests.jsonl"
    script = tmp_path / "agy_handoff.py"
    script.write_text("""
import json, sys, uuid
from pathlib import Path

settings = {"model": "gemini-4-neon", "thought_level": "high", "mode": "auto"}
session = uuid.uuid4().hex
for line in sys.stdin:
    request = json.loads(line)
    method = request["method"]
    params = request.get("params", {})
    with Path("requests.jsonl").open("a", encoding="utf-8") as log:
        print(json.dumps(request), file=log)
    if method == "initialize":
        result = {"protocolVersion": 1, "agentCapabilities": {
            "promptCapabilities": {"embeddedContext": True}, "sessionCapabilities": {"resume": {}}}}
    elif method in ("session/new", "session/resume", "session/set_config_option"):
        if method == "session/resume":
            session = params["sessionId"]
        if method == "session/set_config_option":
            settings[params["configId"]] = params["value"]
        result = {"sessionId": session, "configOptions": [
            {"type": "select", "id": key, "category": key, "currentValue": value,
             "options": [{"value": value}]} for key, value in settings.items()]}
    elif method == "session/prompt":
        text = params["prompt"][0]["text"]
        if "CONTEXT_LIMIT_PROBE" in text:
            print(json.dumps({"id": request["id"], "error": {"code": -32000,
                             "message": "maximum context length exceeded"}}), flush=True)
            continue
        if "UNKNOWN_RESULT_PROBE" in text:
            sys.exit(0)
        if "CANCEL_RESULT_PROBE" in text:
            print(json.dumps({"id": request["id"], "result": {"stopReason": "cancelled"}}), flush=True)
            continue
        if "REJECTED_RESULT_PROBE" in text:
            print(json.dumps({"id": request["id"], "error": {"code": -32000,
                             "message": "AGY provider declined the request"}}), flush=True)
            continue
        print(json.dumps({"method": "session/update", "params": {"sessionId": session, "update": {
            "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "AGY answer"},
            "turnId": "native-turn", "messageId": "native-answer"}}}), flush=True)
        result = {"stopReason": "end_turn"}
    else:
        result = {}
    print(json.dumps({"id": request["id"], "result": result}), flush=True)
""", encoding="utf-8")
    monkeypatch.setenv("MC_AGY_EXECUTABLE", sys._base_executable)
    monkeypatch.setattr(AgyAcpClient, "command", staticmethod(
        lambda executable: [executable, "-X", "utf8", "-u", str(script)],
    ))
    monkeypatch.setattr(frame, "_workspace_dir_for_codex", lambda: str(tmp_path))
    monkeypatch.setattr(frame, "_schedule_first_question_auto_title", lambda *_: None)
    monkeypatch.setattr(frame, "_schedule_async_archive_rename", lambda *_: None)
    monkeypatch.setattr(wx, "MessageBox", lambda message, *_args, **_kwargs: (
        frame.SetStatusText(message), wx.OK)[1])
    choose(frame.tool_combo, "Codex")
    assert frame._on_new_chat_clicked(None)
    source = frame.active_chat_id
    frame._current_chat_state.update(model=DEFAULT_CODEX_MODEL, native_cwd=str(project),
                                     codex_thread_id="original-codex")
    frame.active_codex_thread_id = "original-codex"
    frame.active_session_turns.extend([
        {"question": "  第一问\r\n原文  ", "answer_md": "  第一答\n保留结尾  ", "request_status": "done",
         "attachments": [{"name": "OLD_BINARY", "path": "ignored"}], "model": DEFAULT_CODEX_MODEL},
        {"question": "第二问\n🧪", "answer_md": "第二答\r\n𠀋", "request_status": "done",
         "model": DEFAULT_CODEX_MODEL},
        {"question": "PENDING_QUESTION", "answer_md": "PENDING_OUTPUT", "request_status": "pending",
         "model": DEFAULT_CODEX_MODEL},
    ])
    frame.chat_store.upsert_chat(frame._current_chat_state)
    frame.chat_store.replace_turns(source, frame.active_session_turns)
    frame.Show()
    return source, project, log


def click_send(frame):
    event = wx.CommandEvent(wx.wxEVT_BUTTON, frame.send_button.GetId())
    event.SetEventObject(frame.send_button)
    frame.send_button.GetEventHandler().ProcessEvent(event)


def requests(log, method):
    return [entry["params"] for line in log.read_text(encoding="utf-8").splitlines()
            if (entry := json.loads(line))["method"] == method] if log.exists() else []


def test_codex_handoff_sends_verbatim_history_once_in_source_workspace(
    frame, handoff_runtime, wait_for_wx, tmp_path,
):
    source, project, log = handoff_runtime
    file = tmp_path / "new.txt"
    file.write_text("NEW_FILE_CONTENT", encoding="utf-8")
    frame.input_edit.ChangeValue("新的问题")
    frame._pending_input_attachments = [{"name": file.name, "path": str(file)}]
    choose(frame.tool_combo, "AGY")
    choose(frame.tool_combo, "Codex")
    assert frame.active_chat_id == source
    assert frame.input_edit.GetValue() == "新的问题"
    assert not requests(log, "session/new")
    choose(frame.tool_combo, "AGY")
    click_send(frame)
    wait_for_wx(lambda: frame.active_chat_id != source, "completed AGY handoff")
    target = frame.active_chat_id
    assert requests(log, "session/new")[0]["cwd"] == str(project)
    prompt, = requests(log, "session/prompt")
    assert prompt["sessionId"] != "original-codex"
    text, attached = prompt["prompt"]
    assert text["text"] == (
        "以下是已完成的 Codex 历史对话。\n\n"
        "用户：\n  第一问\r\n原文  \n\nCodex：\n  第一答\n保留结尾  \n\n"
        "用户：\n第二问\n🧪\n\nCodex：\n第二答\r\n𠀋\n\n本次提问：\n新的问题"
    )
    assert attached["resource"]["text"] == "NEW_FILE_CONTENT"
    saved_source = frame.chat_store.load_chat(source)
    assert saved_source["codex_thread_id"] == "original-codex"
    assert len(saved_source["turns"]) == 3
    saved_target = frame.chat_store.load_chat(target)
    assert [turn["question"] for turn in saved_target["turns"]] == ["新的问题"]
    assert saved_target["turns"][0]["tool_settings"]["model"] == "gemini-4-neon"
    send(frame, "独立后续提问")
    wait_for_wx(lambda: frame.active_session_turns[-1]["request_status"] == "done", "AGY follow-up")
    _, followup = requests(log, "session/prompt")
    assert followup["sessionId"] == prompt["sessionId"]
    assert followup["prompt"] == [{"type": "text", "text": "独立后续提问"}]
    assert frame._show_history_chat(source)
    assert frame.tool_combo.GetValue() == "Codex"
    assert frame.chat_store.load_chat(source)["codex_thread_id"] == "original-codex"


@pytest.mark.parametrize(("probe", "message", "count"), [
    ("CONTEXT_LIMIT_PROBE", "输入上限", 1),
    ("REJECTED_RESULT_PROBE", "provider declined", 1),
    ("CANCEL_RESULT_PROBE", "已取消", 1),
    ("INVALID_SOURCE_WORKSPACE", "目录不可用", 0),
])
def test_failed_handoff_leaves_source_and_draft_editable(
    frame, handoff_runtime, wait_for_wx, tmp_path, probe, message, count,
):
    source, project, log = handoff_runtime
    if probe == "INVALID_SOURCE_WORKSPACE":
        project.rmdir()
    before = {row["id"] for row in frame.chat_store.list_chat_summaries()}
    file = tmp_path / "new.txt"
    file.write_text("attached", encoding="utf-8")
    attachments = [{"name": file.name, "path": str(file)}]
    frame._pending_input_attachments = attachments
    draft = f"  {probe}\n继续  "
    frame.input_edit.ChangeValue(draft)
    choose(frame.tool_combo, "AGY")
    click_send(frame)
    wait_for_wx(lambda: message in frame.GetStatusBar().GetStatusText(), message)
    assert frame.active_chat_id == source
    assert frame.chat_store.load_chat(source)["codex_thread_id"] == "original-codex"
    assert {row["id"] for row in frame.chat_store.list_chat_summaries()} == before
    assert len(requests(log, "session/prompt")) == count
    assert frame.input_edit.GetValue() == draft
    assert frame._pending_input_attachments == attachments


def test_lost_handoff_response_tracks_one_native_session_without_retry(
    frame, handoff_runtime, wait_for_wx,
):
    source, _project, log = handoff_runtime
    choose(frame.tool_combo, "AGY")
    frame.input_edit.ChangeValue("UNKNOWN_RESULT_PROBE")
    click_send(frame)
    wait_for_wx(lambda: frame.active_chat_id != source, "published unresolved AGY session")
    target = frame.chat_store.load_chat(frame.active_chat_id)
    prompt, = requests(log, "session/prompt")
    assert target["agy_session_id"] == prompt["sessionId"]
    assert target["turns"][0]["request_status"] == "unknown"
    assert target["turns"][0]["request_resume_token"] == {"session_id": prompt["sessionId"]}
    frame.input_edit.ChangeValue("请再试")
    click_send(frame)
    assert "待确认" in frame.GetStatusBar().GetStatusText()
    assert frame.input_edit.GetValue() == "请再试"
    assert frame._show_history_chat(source)
    choose(frame.tool_combo, "AGY")
    click_send(frame)
    assert "待确认" in frame.GetStatusBar().GetStatusText()
    assert len(requests(log, "session/new")) == len(requests(log, "session/prompt")) == 1
