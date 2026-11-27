import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from types import SimpleNamespace
import uuid

import pytest
import wx

import agy_acp
from agy_acp import AgyAcpClient, find_agy_executable
from codex_client import DEFAULT_CODEX_MODEL
from test_chat_tool_settings_ui_automation import choose, send
from test_codex_native_history import codex_homes, real_codex


REAL_USERPROFILE = os.environ.get("USERPROFILE", "")


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
         "attachments": [{"name": "prior.bin", "path": "PRIVATE_OLD_BINARY_PATH"}], "model": DEFAULT_CODEX_MODEL},
        {"question": "第二问\n🧪", "answer_md": "第二答\r\n𠀋", "request_status": "done",
         "model": DEFAULT_CODEX_MODEL},
        {"question": "PENDING_QUESTION", "answer_md": "PENDING_OUTPUT", "request_status": "pending",
         "model": DEFAULT_CODEX_MODEL},
    ])
    frame.chat_store.upsert_chat(frame._current_chat_state)
    frame.chat_store.replace_turns(source, frame.active_session_turns)
    frame.Show()
    return source, project, log


@pytest.fixture
def native_agy_for_handoff(frame, monkeypatch, tmp_path):
    monkeypatch.delenv("MC_AGY_EXECUTABLE", raising=False)
    assert find_agy_executable(), "AGY must be installed for the native acceptance run"
    profile = "mc-settings-test-" + uuid.uuid4().hex
    root = Path(REAL_USERPROFILE) / ".gemini"
    data = root / profile
    command = AgyAcpClient.command

    def launch(*args, **kwargs):
        env = dict(os.environ, USERPROFILE=REAL_USERPROFILE)
        return subprocess.Popen(*args, env=env, **kwargs)

    # Keep the Codex test process and its background catalog on their fixture profile.
    monkeypatch.setattr(agy_acp, "subprocess", SimpleNamespace(**dict(vars(subprocess), Popen=launch)))
    monkeypatch.setattr(AgyAcpClient, "command", staticmethod(
        lambda executable: [*command(executable), "--app-data-dir", profile,
                            "--log-file", str(tmp_path / "agy.log")],
    ))
    try:
        yield
    finally:
        frame._close_agy_clients()
        if data.is_dir():
            assert data.resolve().parent == root.resolve()
            shutil.rmtree(data)


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
        "用户：\n  第一问\r\n原文  \n附件：prior.bin\n\nCodex：\n  第一答\n保留结尾  \n\n"
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
    wait_for_wx(lambda: "待确认" in frame.GetStatusBar().GetStatusText(), "blocked repeated handoff")
    assert len(requests(log, "session/new")) == len(requests(log, "session/prompt")) == 1


def test_handoff_waits_for_storage_off_ui_and_keeps_the_send_cutoff(
    frame, handoff_runtime, wait_for_wx, monkeypatch,
):
    source, _project, log = handoff_runtime
    frame.chat_store.replace_turns(source, frame.active_session_turns[:1])
    reading = threading.Event()
    continued = threading.Event()
    load_chat = frame.chat_store.load_chat

    def delay_source_read(chat_id, **kwargs):
        if chat_id == source and not continued.is_set():
            reading.set()
            assert continued.wait(4), "durable history read blocked the wx send event"
        return load_chat(chat_id, **kwargs)

    monkeypatch.setattr(frame.chat_store, "load_chat", delay_source_read)
    frame.input_edit.ChangeValue("接续截止点")
    choose(frame.tool_combo, "AGY")
    try:
        click_send(frame)
        wait_for_wx(reading.is_set, "background source history lookup")
        assert frame.active_chat_id == source
        assert frame.input_edit.GetValue() == "接续截止点"
        frame.active_session_turns[-1].update(answer_md="LATER_COMPLETED_OUTPUT", request_status="done")
        frame.chat_store.replace_turns(source, [frame.active_session_turns[0], {}, frame.active_session_turns[-1]])
        wx.CallAfter(continued.set)
        wait_for_wx(continued.is_set, "wx event while source lookup is delayed")
    finally:
        continued.set()
    wait_for_wx(lambda: frame.active_chat_id != source, "published AGY handoff")
    prompt, = requests(log, "session/prompt")
    assert "第二问\n🧪\n\nCodex：\n第二答\r\n𠀋" in prompt["prompt"][0]["text"]
    assert "LATER_COMPLETED_OUTPUT" not in prompt["prompt"][0]["text"]


def test_handoff_keeps_an_attachment_only_exchange_and_its_original_speaker(
    frame, handoff_runtime, wait_for_wx,
):
    source, _project, log = handoff_runtime
    frame.active_session_turns.insert(2, {
        "question": "", "answer_md": "  Kimi 原文\r\n", "request_status": "done", "model": "kimi/main",
        "attachments": [{"name": "历史输入.txt", "path": "PRIVATE_STORAGE", "data": "PRIVATE_BINARY"}],
        "received_attachments": [{"name": "历史输出.txt", "path": "PRIVATE_OUTPUT", "data": "PRIVATE_BINARY"}],
    })
    frame.chat_store.replace_turns(source, frame.active_session_turns)
    choose(frame.tool_combo, "AGY")
    frame.input_edit.ChangeValue("接续这些内容")
    click_send(frame)
    wait_for_wx(lambda: frame.active_chat_id != source, "completed mixed-provider history handoff")
    prompt, = requests(log, "session/prompt")
    text, = prompt["prompt"]
    assert "用户：\n附件：历史输入.txt\n\nKimi Code：\n  Kimi 原文\r\n\n附件：历史输出.txt" in text["text"]
    assert "PRIVATE_" not in text["text"]


@real_codex
@pytest.mark.skipif(not os.getenv("MC_TEST_AGY_NATIVE"), reason="requires explicitly enabled, authenticated AGY")
def test_native_codex_handoff_into_native_agy(
    codex_homes, frame, native_agy_for_handoff, monkeypatch, wait_for_wx, wait_for_wx_main_loop,
):
    _ordinary, _private, workspace, different_default, codex_calls, _native = codex_homes
    monkeypatch.setattr(frame, "_workspace_dir_for_codex", lambda: str(workspace))
    monkeypatch.setattr(frame, "_schedule_first_question_auto_title", lambda *_: None)
    frame.Show()
    questions = ["First unique seed SEED_NATIVE_061", "Second unique seed SEED_NATIVE_062"]
    for index, question in enumerate(questions):
        send(frame, question)
        wait_for_wx(lambda: len(frame.active_session_turns) > index
                    and frame.active_session_turns[index].get("request_status") in {"done", "failed"},
                    f"Codex turn {index + 1}", timeout=30)
        assert frame.active_session_turns[index]["request_status"] == "done"
    source_id = frame.active_chat_id
    native_codex_id = frame.active_codex_thread_id
    assert native_codex_id
    traffic = []
    send_acp = AgyAcpClient._send

    def track_acp(client, payload):
        if payload.get("method") in {"session/new", "session/prompt"}:
            traffic.append(payload)
        return send_acp(client, payload)

    with monkeypatch.context() as native_profile:
        native_profile.setattr(frame, "_workspace_dir_for_codex", lambda: str(different_default))
        native_profile.setattr(AgyAcpClient, "_send", track_acp)
        question = "Reply with exactly HANDOFF_NATIVE_ACK_062."
        frame.input_edit.ChangeValue(question)
        choose(frame.tool_combo, "AGY")
        choose(frame.tool_combo, "Codex")
        assert frame.active_chat_id == source_id
        assert frame.input_edit.GetValue() == question
        assert not traffic
        choose(frame.tool_combo, "AGY")
        click_send(frame)
        wait_for_wx(lambda: frame.active_chat_id != source_id, "native AGY handoff", timeout=70)
        native_agy_id = frame._current_chat_state["agy_session_id"]
        assert native_agy_id != native_codex_id
        assert frame._current_chat_state["agy_workspace"] == str(workspace)
        assert frame.active_session_turns[0]["request_status"] == "done"
        assert "HANDOFF_NATIVE_ACK_062" in frame.active_session_turns[0]["answer_md"]
        opened, first = traffic
        assert opened["params"]["cwd"] == str(workspace)
        assert first["params"]["sessionId"] == native_agy_id
        prompt = first["params"]["prompt"][0]["text"]
        assert all(f"用户：\n{text}\n\nCodex：\nCODX_08_FULL_TAIL" in prompt for text in questions)
        assert prompt.endswith("本次提问：\n" + question)
        followup = "Reply with exactly HANDOFF_NATIVE_FOLLOWUP_063."
        send(frame, followup)
        wait_for_wx(lambda: frame.active_session_turns[-1].get("request_status") in {"done", "failed", "unknown"},
                    "native AGY follow-up", timeout=70)
        assert frame.active_session_turns[-1]["request_status"] == "done"
        assert len(traffic) == 3
        assert traffic[-1]["params"] == {
            "sessionId": native_agy_id,
            "prompt": [{"type": "text", "text": followup}],
        }

    monkeypatch.setattr(frame, "_workspace_dir_for_codex", lambda: str(workspace))
    assert frame._show_history_chat(source_id)
    assert frame.tool_combo.GetValue() == "Codex"
    send(frame, "Third Codex question after AGY handoff")
    wait_for_wx_main_loop(
        lambda: len(codex_calls) == 3 and frame.active_chat_id == source_id
        and len(frame.active_session_turns) >= 3
        and frame.active_session_turns[2].get("request_status") in {"done", "failed", "unknown"},
        "original native Codex thread still continues", timeout=20,
    )
    assert frame.active_session_turns[2]["request_status"] == "done", frame.active_session_turns[2].get("request_error")
    assert frame.active_session_turns[-1]["codex_thread_id"] == native_codex_id
    assert len(codex_calls) == 3
