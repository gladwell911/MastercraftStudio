import asyncio
import base64
import json
import sys
import threading
from types import SimpleNamespace
from urllib.request import Request, urlopen

import pytest
import wx

from agy_acp import AgyAcpClient
from chat_store import ChatStore
from file_transfer import DesktopFileHttpService, DesktopFileLibrary
from remote_nats import RemoteNatsTransport
from remote_nats_protocol import rpc_reply_subject


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a+xkAAAAASUVORK5CYII="
)


@pytest.fixture
def agy_attachment_runtime(tmp_path, monkeypatch, frame, request):
    request.addfinalizer(frame._close_agy_clients)
    provider = tmp_path / "attachment_provider.py"
    provider.write_text("""
import json, sys, time, uuid
from pathlib import Path

def update(kind, **facts):
    print(json.dumps({"method": "session/update", "params": {"sessionId": session,
          "update": {"sessionUpdate": kind, **facts}}}), flush=True)

session = uuid.uuid4().hex
for line in sys.stdin:
    request = json.loads(line)
    method = request["method"]
    request_id = request.get("id")
    params = request.get("params", {})
    if method == "initialize":
        capabilities = {"embeddedContext": True, "image": not Path("disable-image").exists()}
        result = {"protocolVersion": 1, "agentCapabilities": {
            "promptCapabilities": capabilities, "sessionCapabilities": {"resume": {}}}}
    elif method in {"session/new", "session/resume"}:
        session = params.get("sessionId", session)
        result = {"sessionId": session}
    elif method == "session/prompt":
        assert params["sessionId"] == session
        with open("prompts.jsonl", "a", encoding="utf-8") as log:
            log.write(json.dumps(params["prompt"]) + "\\n")
        if params["prompt"][0].get("text") == "execution probe":
            update("agent_thought_chunk", content={"type": "text", "text": "public reasoning fragment"})
            update("plan", entries=[{"content": "继续执行", "status": "in_progress"}])
            update("tool_call", toolCallId="run-a", title="Run command", status="in_progress")
            while not Path("finish-execution").exists():
                time.sleep(0.01)
            for _ in range(30):
                update("tool_call_update", toolCallId="run-a", status="completed",
                       rawOutput={"exit_code": 7, "output": "failure output"},
                       content=[{"type": "content", "content": {"type": "text", "text": "failure output"}}])
                update("usage_update", used=145, size=2048)
            update("tool_call_update", toolCallId="run-a", status="in_progress")
            for key in ("run-b", "run-c"):
                update("tool_call", toolCallId=key, title="Run command", status="completed",
                       rawOutput={"exit_code": 0, "output": "success output"})
            update("tool_call", toolCallId="tool-error", title="Bad argument", status="failed")
            update("tool_call", toolCallId="edit", title="Edit file", status="completed",
                   content=[{"type": "diff", "path": "notes.txt", "oldText": "old\\n", "newText": "new\\n"}])
            for chunk in ("  正文𠀋\\r\\n", "内容🧪\\n" * 2100, "\\r\\nT06_END  "):
                update("agent_message_chunk", content={"type": "text", "text": chunk})
        else:
            update("agent_message_chunk", content={"type": "text", "text": "Received"})
        result = {"stopReason": "end_turn"}
        if params["prompt"][0].get("text") == "execution probe":
            result["usage"] = {"inputTokens": 90, "outputTokens": 21, "cachedReadTokens": 43,
                               "cachedWriteTokens": 0, "thoughtTokens": 9, "totalTokens": 120}
        elif params["prompt"][0].get("text") == "usage second turn":
            result["usage"] = {"cachedReadTokens": 0, "totalTokens": 17}
    print(json.dumps({"id": request_id, "result": result}), flush=True)
""", encoding="utf-8")
    monkeypatch.setenv("MC_AGY_EXECUTABLE", sys._base_executable)
    monkeypatch.setattr(AgyAcpClient, "command", staticmethod(
        lambda executable: [executable, "-X", "utf8", "-u", str(provider)],
    ))
    monkeypatch.setattr(frame, "_schedule_first_question_auto_title", lambda *args: None)
    monkeypatch.setattr(frame, "_schedule_async_archive_rename", lambda *args: None)
    return tmp_path / "prompts.jsonl"


def test_agy_chat_information_reads_and_reopens_provider_usage(
    frame, tmp_path, wx_app, wait_for_wx, agy_attachment_runtime, monkeypatch,
):
    import mc_frame_chat_information

    monkeypatch.setattr(mc_frame_chat_information, "read_agy_quota", lambda: ("未登录", {}, 0))
    (tmp_path / "finish-execution").touch()
    status, first = frame._remote_api_new_chat({"model": "agy/main", "agy_workspace": str(tmp_path)})
    assert status == 200, first
    assert frame._switch_current_chat(first["chat_id"])
    frame.input_edit.ChangeValue("execution probe")
    assert frame._on_send_clicked(None)
    wait_for_wx(lambda: any(turn.get("request_status") == "done" for turn in frame.active_session_turns),
                "settled AGY turn")

    def open_information():
        event = wx.CommandEvent(wx.EVT_MENU.typeId, int(frame._chat_information_menu_id))
        frame.ProcessWindowEvent(event)
        wx_app.Yield()
        return frame._chat_information_dialog

    expected = [
        "当前上下文：145 / 2,048 token",
        "会话累计 token：120",
        "当前缓存命中：43 token",
        "累计缓存命中：43 token",
    ]
    dialog = open_information()
    assert list(dialog.information_list.GetStrings())[:4] == expected
    dialog.Close()
    wx_app.Yield()
    frame.input_edit.ChangeValue("usage second turn")
    assert frame._on_send_clicked(None)
    wait_for_wx(lambda: len(frame.active_session_turns) == 2
                and frame.active_session_turns[1].get("request_status") == "done", "second settled AGY turn")
    expected[1:3] = ["会话累计 token：137", "当前缓存命中：0 token"]
    status, second = frame._remote_api_new_chat({"model": "agy/main", "agy_workspace": str(tmp_path)})
    assert status == 200, second
    assert frame._switch_current_chat(second["chat_id"])
    dialog = open_information()
    assert list(dialog.information_list.GetStrings())[:4] == [
        "当前上下文：暂不可用", "会话累计 token：暂不可用",
        "当前缓存命中：暂不可用", "累计缓存命中：暂不可用",
    ]
    dialog.Close()
    wx_app.Yield()
    frame._flush_chat_state_save()
    archived = frame._find_archived_chat(first["chat_id"])
    archived.pop("turns", None)
    assert frame._show_history_chat(first["chat_id"])
    dialog = open_information()
    assert list(dialog.information_list.GetStrings())[:4] == expected
    dialog.Close()
    wx_app.Yield()


def test_agy_quota_late_result_cannot_replace_other_chat_or_steal_focus(
    frame, tmp_path, wx_app, wait_for_wx, agy_attachment_runtime, monkeypatch,
):
    import time
    import mc_frame_chat_information

    first_started = threading.Event()
    first_ready = threading.Event()
    first_finished = threading.Event()

    def read_quota():
        if not first_started.is_set():
            first_started.set()
            first_ready.wait(5)
            first_finished.set()
            return "", {"周限额": ("93%", None)}, time.monotonic()
        return "未登录", {}, time.monotonic()

    monkeypatch.setattr(mc_frame_chat_information, "read_agy_quota", read_quota)
    frame.Show()
    status, first = frame._remote_api_new_chat({"model": "agy/main", "agy_workspace": str(tmp_path)})
    assert status == 200, first
    assert frame._show_chat_information()
    first_dialog = frame._chat_information_dialog
    assert first_started.wait(2)
    first_dialog.Close()
    wx_app.Yield()
    status, second = frame._remote_api_new_chat({"model": "agy/main", "agy_workspace": str(tmp_path)})
    assert status == 200, second
    assert frame._show_chat_information()
    dialog = frame._chat_information_dialog
    dialog.information_list.SetSelection(3)
    wait_for_wx(lambda: "周限额：未登录" in dialog.information_list.GetStrings(), "new account status")
    assert dialog.information_list.GetSelection() == 3
    assert wx.Window.FindFocus() == dialog.information_list
    first_ready.set()
    assert first_finished.wait(2)
    wx_app.Yield()
    assert "周限额：未登录" in dialog.information_list.GetStrings()
    dialog.Close()
    wx_app.Yield()


def test_agy_quota_timer_keeps_recent_observation_then_shows_expiry_and_refreshes(
    frame, tmp_path, wx_app, wait_for_wx_main_loop, agy_attachment_runtime, monkeypatch,
):
    import agy_information
    import mc_frame_chat_information

    clock = SimpleNamespace(now=100)
    monkeypatch.setattr(agy_information, "time", SimpleNamespace(monotonic=lambda: clock.now))
    first_read = threading.Event()
    refresh_started = threading.Event()
    refresh_ready = threading.Event()

    def read_quota():
        if not first_read.is_set():
            first_read.set()
            return "", {"周限额": ("93%", None)}, clock.now
        refresh_started.set()
        refresh_ready.wait(5)
        return "", {"周限额": ("94%", None)}, clock.now

    monkeypatch.setattr(mc_frame_chat_information, "read_agy_quota", read_quota)
    frame.Show()
    status, created = frame._remote_api_new_chat({"model": "agy/main", "agy_workspace": str(tmp_path)})
    assert status == 200, created
    assert frame._show_chat_information()
    dialog = frame._chat_information_dialog
    rows = dialog.information_list.GetStrings
    try:
        wait_for_wx_main_loop(lambda: "周限额：剩余 93%，重置时间暂不可用" in rows(), "first account quota")
        dialog.information_list.SetFocus()
        assert wx.Window.FindFocus() == dialog.information_list
        dialog.information_list.SetSelection(5)
        previous = list(rows())
        dialog.refresh_timer.StartOnce(20)
        wait_for_wx_main_loop(refresh_started.is_set, "periodic AGY quota query")
        assert list(rows()) == previous
        clock.now += 61
        dialog.context_timer.StartOnce(20)
        wait_for_wx_main_loop(lambda: "周限额：已过期" in rows(), "expired observation during query")
        assert dialog.information_list.GetSelection() == 5
        assert wx.Window.FindFocus() == dialog.information_list
        refresh_ready.set()
        wait_for_wx_main_loop(lambda: "周限额：剩余 94%，重置时间暂不可用" in rows(), "refreshed account quota")
        assert dialog.information_list.GetSelection() == 5
        assert wx.Window.FindFocus() == dialog.information_list
    finally:
        refresh_ready.set()
        dialog.Close()
        wx_app.Yield()


@pytest.mark.parametrize("supports_image", [True, False])
def test_agy_desktop_file_picker_preserves_input_on_missing_capability(
    frame, tmp_path, monkeypatch, wait_for_wx, supports_image, agy_attachment_runtime,
):
    if not supports_image:
        (tmp_path / "disable-image").touch()
    image = tmp_path / "picture.png"
    image.write_bytes(PNG)
    document = tmp_path / "原文 #%.txt"
    document.write_bytes("蓝图𠀋\r\nAGY_RESOURCE_1A47".encode("utf-8"))
    status, chat = frame._remote_api_new_chat({"model": "agy/main", "agy_workspace": str(tmp_path)})
    assert status == 200, chat
    assert frame._switch_current_chat(chat["chat_id"])

    class PickedFiles:
        def ShowModal(self):
            return wx.ID_OK

        def GetPaths(self):
            return [str(image), str(document)]

        def Destroy(self):
            pass

    monkeypatch.setattr(wx, "FileDialog", lambda *args, **kwargs: PickedFiles())
    messages = []
    monkeypatch.setattr(wx, "MessageBox", lambda text, *_args: messages.append(text))
    frame.input_edit.ChangeValue("已输入的正文")
    frame._load_chat_attachments_via_dialog()
    if not supports_image:
        wait_for_wx(lambda: bool(messages), "capability message")
        assert "图片" in messages[-1] and "picture.png" in messages[-1]
        assert frame.input_edit.GetValue() == "已输入的正文"
        assert [attachment["name"] for attachment in frame._pending_input_attachments] == [image.name, document.name]
        assert frame.active_session_turns == []
        assert not agy_attachment_runtime.exists()
        return

    wait_for_wx(lambda: any(turn.get("request_status") == "done" for turn in frame.active_session_turns),
                "native attachment response")
    assert len(frame.active_session_turns) == 1
    assert not messages
    blocks = json.loads(agy_attachment_runtime.read_text(encoding="utf-8"))
    assert blocks == [
        {"type": "text", "text": "已输入的正文"},
        {"type": "image", "mimeType": "image/png", "data": base64.b64encode(PNG).decode("ascii")},
        {"type": "resource", "resource": {
            "uri": document.as_uri(), "mimeType": "text/plain", "text": "蓝图𠀋\r\nAGY_RESOURCE_1A47",
        }},
    ]


def test_agy_rc_attachment_confirmation_and_message_retry_submit_once(
    frame, tmp_path, wait_for_wx, agy_attachment_runtime,
):
    status, chat = frame._remote_api_new_chat({"model": "agy/main", "agy_workspace": str(tmp_path)})
    assert status == 200, chat
    owner = chat["chat_id"]
    frame.file_library = DesktopFileLibrary(tmp_path / "attachments")
    frame.file_service = http_service = DesktopFileHttpService(frame.file_library)
    transport = RemoteNatsTransport(
        pair_id="default", token="test-token", durable_store=frame.chat_store,
        chat_attachment_support=True, on_file_command=frame._remote_api_file_command_ui,
        on_message=frame._remote_api_message,
    )
    responses = []

    class Connection:
        async def publish(self, subject, data):
            responses.append((subject, json.loads(data)))

    transport._nats_client = Connection()
    sequence = 0
    epoch = ""

    def rpc(kind, **fields):
        nonlocal sequence
        sequence += 1
        request = f"request-{sequence}"
        subject = rpc_reply_subject("default", "phone", "phone-session", request)
        envelope = {
            "id": request, "type": kind, "device_id": "phone", "created_at": 123,
            "session_id": "phone-session", "reply_subject": subject,
            **fields,
        }
        if kind != "hello":
            envelope.update(protocol_version=2, chat_id=owner, request_id=request, epoch=epoch, body=fields)
        message = SimpleNamespace(data=json.dumps(envelope).encode())
        worker = threading.Thread(target=lambda: asyncio.run(transport._handle_rpc_message(message)))
        worker.start()
        wait_for_wx(lambda: not worker.is_alive(), f"remote {kind} response")
        worker.join()
        reply_subject, reply = responses.pop()
        assert reply_subject == subject
        return reply["status"], reply["body"]

    status, hello = rpc("hello", protocol_versions=[2])
    assert status == 200
    epoch = hello["epoch"]
    http_service.start()
    try:
        file_ids = []
        for ordinal, (name, data) in enumerate((("picture.png", PNG), ("note.txt", b"RC_RESOURCE_58E9"))):
            status, upload = rpc(
                "file_upload_request", name=name, size_bytes=len(data),
                attachment_send_id="draft", attachment_upload_id=f"draft-{ordinal}",
            )
            assert status == 200, upload
            with urlopen(Request(upload["upload_url"], data=data, method="PUT")) as transfer:
                assert transfer.status == 201
            complete = {"file_id": upload["file_id"], "size_bytes": len(data), "attachment_send_id": "draft"}
            assert rpc("file_complete", **complete)[0] == 200
            assert rpc("file_complete", **complete)[0] == 200
            file_ids.append(upload["file_id"])

        request = {"text": "Read both attachments", "client_send_id": "draft", "attachment_file_ids": file_ids}
        status, reply = rpc("message", **request)
        assert status == 200, reply
        assert rpc("message", **request)[0] == 200
        wait_for_wx(lambda: agy_attachment_runtime.is_file(), "AGY attachment prompt")
        wait_for_wx(lambda: frame._chat_target_for_request(owner)[1][0]["request_status"] == "done",
                    "completed AGY reply")
        frame._flush_chat_state_save()
        wait_for_wx(lambda: frame.chat_store.load_chat(owner)["turns"][0]["request_status"] == "done",
                    "persisted AGY reply")
        assert frame.chat_store.count_turns(owner) == 1
        lines = agy_attachment_runtime.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        _, image, resource = json.loads(lines[0])
        assert image["type"] == "image" and base64.b64decode(image["data"]) == PNG
        assert resource["type"] == "resource" and resource["resource"]["text"] == "RC_RESOURCE_58E9"
    finally:
        http_service.stop()


def test_agy_native_process_rows_and_paged_literal_answer(
    frame, tmp_path, wait_for_wx, agy_attachment_runtime,
):
    frame._chat_store_enabled = True
    status, chat = frame._remote_api_new_chat({"model": "agy/main", "agy_workspace": str(tmp_path)})
    assert status == 200, chat
    owner = chat["chat_id"]
    frame.input_edit.ChangeValue("execution probe")
    assert frame._on_send_clicked(None)

    def page():
        frame._flush_chat_state_save()
        status, body = frame._remote_api_execution_page_ui({"chat_id": owner}, secret="t06-private")
        assert status == 200 or body.get("error") == "EXECUTION_SOURCE_PENDING", body
        return body.get("rows", [])

    def command_row():
        return next((row for row in page() if "执行中：Run command" == row["list_text"]), None)

    wait_for_wx(command_row, "in-progress tool row")
    before = command_row()["row_id"]
    (tmp_path / "finish-execution").touch()
    wait_for_wx(lambda: frame.active_session_turns[0].get("request_status") == "done", "literal answer")
    frame._flush_chat_state_save()
    wait_for_wx(lambda: any(row.get("kind") == "final" for row in page()), "persisted execution page")
    rows = page()
    first = next(row for row in rows if row["row_id"] == before)
    assert first["list_text"] == "已完成（退出码 7）：Run command"
    assert first["detail_text"].count("failure output") == 1
    successes = [row for row in rows if row["list_text"] == "已完成：Run command"]
    assert len({row["row_id"] for row in successes}) == 2
    assert all("success output" in row["detail_text"] for row in successes)
    assert any(row["list_text"] == "失败：Bad argument" for row in rows)
    assert any("-old\n+new" in row["detail_text"] for row in rows)
    assert any(row["detail_text"] == "public reasoning fragment" for row in rows)
    assert any(row["list_text"] == "AGY 计划" and "继续执行" in row["detail_text"] for row in rows)
    used = [row for row in rows if row["list_text"] == "AGY 上下文用量：145 / 2048 tokens"]
    assert len(used) == 1

    answer = next(row for row in rows if row.get("kind") == "final")
    reopened = ChatStore(frame.chat_store.db_path)
    reopened.initialize()
    transport = RemoteNatsTransport(pair_id="default", token="t06-private", durable_store=reopened)
    cursor = answer["answer_text_continuation"]
    assert cursor
    full = answer["answer_text"]
    while cursor:
        status, piece = transport._route_command({"type": "content_page", "chat_id": owner, "body": {"cursor": cursor}})
        assert status == 200, piece
        full += piece["text"]
        cursor = piece["cursor"]
    expected = "  正文𠀋\r\n" + "内容🧪\n" * 2100 + "\r\nT06_END  "
    assert full == expected
    assert reopened.load_chat(owner)["turns"][0]["answer_md"] == expected
