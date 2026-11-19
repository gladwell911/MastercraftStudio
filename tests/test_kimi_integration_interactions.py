import main
import pytest
import wx
from codex_client import CodexEvent
from kimi_server_client import KimiEvent
from kimi_test_support import (
    TEST_TURN_ID,
    _active_chat_id,
    _setup_kimi_frame,
    _submit,
    running_kimi_control_server,
)


def test_approval_request_opens_dialog_and_replies(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "需要批准的操作")
    session_id = fake.created_sessions[0]["session_id"]
    fake.push_event(KimiEvent(type="turn_started", thread_id=session_id, turn_id=TEST_TURN_ID))

    class _Dialog:
        shown = {"questions": None}

        def __init__(self, _parent, questions):
            _Dialog.shown["questions"] = questions

        def ShowModal(self):
            return wx.ID_OK

        def get_answers(self):
            return {"approval": ["approved"]}

        def Destroy(self):
            pass

    monkeypatch.setattr(main, "CodexUserInputDialog", _Dialog)

    fake.push_event(
        KimiEvent(
            type="server_request",
            thread_id=session_id,
            turn_id=TEST_TURN_ID,
            method="approval",
            params={"approvalId": "approval-9", "description": "运行 pytest"},
        )
    )

    assert fake.approval_answers == [(session_id, "approval-9", "approved")]
    questions = _Dialog.shown["questions"]
    assert questions and "运行 pytest" in str(questions[0].get("question") or "")


@pytest.mark.parametrize("action", [
    "approve", "reject", "cancel", "question", "question_many_options", "question_long_option", "question_other",
    "dismiss", "ended", "switch", "expired",
    "retry", "retry_unknown", "retry_approved", "retry_answered", "retry_ended_wire",
    "retry_recheck_pending", "retry_recheck_ended", "retry_recheck_unknown",
])
def test_v2_wire_interaction_reaches_native_controls_and_exact_http(
    frame, monkeypatch, running_kimi_control_server, action
):
    native_call_later = wx.CallLater
    _setup_kimi_frame(frame, monkeypatch)
    client, requests_seen, interaction_reads, _ = running_kimi_control_server
    frame._kimi_client = client
    client.on_message = frame._on_kimi_client_message
    frame.active_chat_id = frame.current_chat_id = "chat-wire"
    frame.active_kimi_session_id = "session-wire"
    frame._current_chat_state = {"id": "chat-wire", "kimi_session_id": "session-wire", "turns": []}
    if action == "switch":
        frame.archived_chats.append({"id": "chat-other", "kimi_session_id": "session-other", "turns": []})
        assert frame._switch_current_chat("chat-other")
    if action == "expired":
        now = [100.0]
        monkeypatch.setattr(main.time, "time", lambda: now[0])
        monkeypatch.setattr(client, "start", lambda: now.__setitem__(0, 102.0))
    interaction_reads.extend({
        "retry_unknown": ["pending"],
        "retry_approved": ["approved"],
        "retry_answered": ["answered"],
        "retry_recheck_pending": [None, "pending"],
        "retry_recheck_ended": [None, "rejected"],
        "retry_recheck_unknown": [None, None],
    }.get(action, []))
    questions = [
        {"id": "single-provider", "question": "Select one", "allow_other": True,
         "options": [{"id": "real-a", "label": "A"}, {"id": "real-b", "label": "B"}]},
        {"id": "multi-provider", "question": "Select several", "multi_select": True, "allow_other": True,
         "options": [{"id": "real-x", "label": "X"}, {"id": "real-y", "label": "Y"}]},
        {"id": "other-provider", "question": "Add a detail", "allow_other": True, "options": []},
    ]
    many_options = action in {"question_many_options", "question_long_option"}
    long_description = ("This is a long native radio option description. " * 6) + "End of the description."
    if many_options:
        questions[0]["options"] = [
            {"id": f"real-{i}", "label": f"Option {i}", "description": f"Description for option {i}"}
            for i in range(1, 7)
        ]
        questions[0]["allow_other"] = False
        if action == "question_long_option":
            questions[0]["options"][-1]["description"] = long_description
    kind = "question" if many_options or action in {"question", "question_other", "dismiss", "retry_answered"} else "approval"
    interaction_id = f"interaction-{action}" if action.startswith("retry") else "interaction-wire"
    pending = {
        "type": "interaction", "session_id": "session-wire", "agent_id": "main",
        "event_created_at": "2026-11-19T08:00:00Z", "status": "pending", "tool_call_id": "tool-wire",
        "interaction_id": interaction_id, "kind": kind,
        "request": {"questions": questions} if kind == "question" else
                   {"tool_name": "Shell", "action": "Run the local checks",
                    "tool_input_display": {"command": "pytest -q"}},
    }
    if action == "expired":
        pending["request"]["expires_at"] = 101.0
    shown = []
    approval_body = []
    last_option_geometry = []
    native_show_modal = main.CodexUserInputDialog.ShowModal

    def show_modal(dialog):
        shown.append(dialog.GetTitle())

        def perform_action():
            if kind == "approval":
                panel = dialog._controls[0]["radio"].GetParent()
                approval_body.extend(
                    (child.GetLabel(), dialog.GetClientRect().Contains(panel.GetRect())
                     and panel.GetClientRect().Contains(child.GetRect())
                     and child.GetSize().width >= child.GetBestSize().width
                     and child.GetSize().height >= child.GetBestSize().height)
                    for child in panel.GetChildren() if isinstance(child, wx.StaticText) and child.IsShownOnScreen()
                )
            # Replay pending while the native modal is open. The same session
            # and interaction must not display a second modal.
            client._handle_ws_message(pending)
            if action == "ended":
                client._handle_ws_message({**pending, "status": "ended", "outcome": "cancelled"})
                return
            if action in {"cancel", "dismiss"}:
                dialog.Close()
                return
            if many_options or action in {"question", "question_other", "retry_answered"}:
                single, multi, other = dialog._controls
                if many_options:
                    radio = single["choices"]
                    scroll = radio.GetParent()
                    x_units, y_units = scroll.GetScrollPixelsPerUnit()
                    bottom = max(0, (radio.GetRect().GetBottom() - scroll.GetClientSize().height + y_units) // y_units)
                    if action == "question_long_option":
                        needed = radio.GetBestSize().width > scroll.GetClientSize().width
                        scroll.Scroll(scroll.GetVirtualSize().width // max(1, x_units), bottom)
                        fits_width = (needed and radio.GetSize().width >= radio.GetBestSize().width
                                      and scroll.GetViewStart()[0] > 0
                                      and radio.GetRect().GetRight() < scroll.GetClientSize().width)
                    else:
                        scroll.Scroll(0, bottom)
                        fits_width = radio.GetBestSize().width <= scroll.GetClientSize().width
                    last_option_geometry.append((
                        radio.GetRowCount(), radio.GetColumnCount(), radio.GetString(5),
                        fits_width,
                        radio.GetRect().GetBottom() < scroll.GetClientSize().height,
                    ))
                    radio.SetSelection(5)
                elif action == "question_other":
                    assert single["choices"].GetSelection() == 0
                    single["other"].SetValue("instead of the first option")
                    assert single["choices"].GetSelection() == 2
                else:
                    single["choices"].SetSelection(1)
                multi["choices"].Check(0)
                multi["choices"].Check(1)
                multi["other"].SetValue("more")
                other["other"].SetValue("extra detail")
            elif action == "reject":
                dialog._controls[0]["radio"].SetSelection(1)
            button = dialog.FindWindow(wx.ID_OK)
            assert button.GetLabel()
            wx.PostEvent(button, wx.CommandEvent(wx.wxEVT_BUTTON, wx.ID_OK))

        action_timer = native_call_later(1, perform_action)
        timeout_timer = native_call_later(3000, dialog.EndModal, wx.ID_CANCEL)
        try:
            return native_show_modal(dialog)
        finally:
            action_timer.Stop()
            timeout_timer.Stop()

    monkeypatch.setattr(main.CodexUserInputDialog, "ShowModal", show_modal)
    client._handle_ws_message(pending)
    client._handle_ws_message(pending)
    if action == "switch":
        assert _active_chat_id(frame) == "chat-other"
        assert shown == [] and requests_seen == []
        assert frame._switch_current_chat("chat-wire")
        assert _active_chat_id(frame) == "chat-wire"
        client._handle_ws_message(pending)
    assert len(shown) == 1
    if kind == "approval":
        assert any(fits_viewport and all(part in label for part in ("Shell", "Run the local checks", "pytest -q"))
                   for label, fits_viewport in approval_body)
    if many_options:
        assert len(last_option_geometry) == 1
        rows, columns, label, fits_width, can_reach_bottom = last_option_geometry[0]
        assert (rows, columns) == (6, 1)
        expected_description = long_description if action == "question_long_option" else "Description for option 6"
        assert expected_description in label
        assert fits_width and can_reach_bottom
    if action in {"ended", "expired"}:
        assert requests_seen == []
        return

    expected_answers = {
        "single-provider": ({"kind": "other", "text": "instead of the first option"} if action == "question_other"
                            else {"kind": "single", "option_id": "real-6" if many_options else "real-b"}),
        "multi-provider": {"kind": "multi_with_other", "option_ids": ["real-x", "real-y"], "other_text": "more"},
        "other-provider": {"kind": "other", "text": "extra detail"},
    }
    if action.startswith("retry"):
        key = ("session-wire", interaction_id)
        request = frame._kimi_interactions[key]
        response = {"kind": "approval", "decision": "approved"} if kind == "approval" else {
            "kind": "question", "answers": expected_answers,
        }
        post = (f"/api/v1/sessions/session-wire/interactions/{interaction_id}", response)
        get = ("/api/v1/sessions/session-wire/transcript?agent_id=main&page_size=100", "GET")
        menu_id = int(frame._kimi_retry_interaction_menu_id)
        menu = frame.GetMenuBar().FindItemById(menu_id)

        def select_retry():
            frame._on_chat_information_menu_open(wx.MenuEvent(wx.wxEVT_MENU_OPEN))
            assert menu.IsEnabled()
            frame.GetEventHandler().ProcessEvent(wx.CommandEvent(wx.wxEVT_MENU, menu_id))

        if action in {"retry_approved", "retry_answered"}:
            assert request["state"] == "ended"
            assert frame.GetStatusBar().GetStatusText() == "已回复 Kimi Code 请求。"
            assert requests_seen == [post, get]
            frame._on_chat_information_menu_open(wx.MenuEvent(wx.wxEVT_MENU_OPEN))
            assert not menu.IsEnabled()
            return
        if action == "retry_ended_wire":
            assert request["state"] == "retryable"
            other = (key[0], "other-interaction")
            frame._kimi_interactions[other] = {
                "state": "retryable", "chat_id": "chat-wire", "error": "another failure",
            }
            frame._report_kimi_interaction_error(other, "chat-wire", "another failure", True)
            client._handle_ws_message({**pending, "status": "ended", "outcome": "cancelled"})
            assert request["state"] == "ended"
            assert list(frame._current_chat_state["kimi_interaction_errors"]) == ["/".join(other)]
            assert "another failure" in frame.GetStatusBar().GetStatusText()
            assert requests_seen == [post]
            return
        if action.startswith("retry_recheck"):
            assert request["state"] == "uncertain"
            assert requests_seen == [post, get]
            select_retry()
            assert len(shown) == 1
            assert requests_seen == [post, get, get]
            if action == "retry_recheck_ended":
                assert request["state"] == "ended"
                assert frame.GetStatusBar().GetStatusText() == "Kimi Code 请求已结束。"
                assert not frame._current_chat_state.get("kimi_interaction_errors")
                frame._on_chat_information_menu_open(wx.MenuEvent(wx.wxEVT_MENU_OPEN))
                assert not menu.IsEnabled()
                return
            if action == "retry_recheck_unknown":
                assert request["state"] == "uncertain"
                frame._on_chat_information_menu_open(wx.MenuEvent(wx.wxEVT_MENU_OPEN))
                assert menu.IsEnabled()
                return
        assert request["state"] == "retryable"
        assert "重试 Kimi 请求" in frame.GetStatusBar().GetStatusText()
        before_retry = {
            "retry": [post], "retry_unknown": [post, get], "retry_recheck_pending": [post, get, get]
        }[action]
        assert requests_seen == before_retry
        select_retry()
        assert frame.GetStatusBar().GetStatusText() == "已回复 Kimi Code 请求。"
        assert not frame._current_chat_state.get("kimi_interaction_errors")
        assert requests_seen == [*before_retry, post]
        client._handle_ws_message(pending)
        assert len(shown) == 2
        return
    if kind == "approval":
        expected = {"kind": "approval", "decision": {"approve": "approved", "switch": "approved", "reject": "rejected", "cancel": "cancelled"}[action]}
    elif action == "dismiss":
        expected = {"kind": "question", "dismissed": True}
    else:
        expected = {"kind": "question", "answers": expected_answers}
    assert requests_seen == [("/api/v1/sessions/session-wire/interactions/interaction-wire", expected)]


def test_http_first_kimi_interaction_retention_and_late_error_feedback(frame, monkeypatch):
    _setup_kimi_frame(frame, monkeypatch)
    _submit(frame, "start")
    chat_id = _active_chat_id(frame)
    session_id = frame.active_kimi_session_id
    jobs = []
    notifications = []
    monkeypatch.setattr(frame, "_call_after_if_alive", lambda callback, *args: jobs.append((callback, args)))
    monkeypatch.setattr(frame, "_push_remote_attention", lambda *args: notifications.append(args))

    def add_interaction(interaction_id):
        key = (session_id, interaction_id)
        request = {"state": "responding", "chat_id": chat_id, "params": {"tool_input_display": "body"}}
        frame._kimi_interactions[key] = request
        return key, request

    def ended(key):
        frame._receive_kimi_interaction(CodexEvent(type="server_request_ended", thread_id=key[0], request_id=key[1]))

    long_key, long_pending = add_interaction("first to start, last to retire")
    active_key, active = add_interaction("still active")
    late_key, late = add_interaction("error ended before callback")
    frame._complete_kimi_interaction_attempt(late_key, late, "responding", "retryable", error="offline")
    ended(late_key)
    for callback, args in jobs:
        callback(*args)
    assert notifications == []
    jobs.clear()
    frame._complete_kimi_interaction_attempt(active_key, active, "responding", "retryable", error="still offline")
    for callback, args in jobs:
        callback(*args)
    assert len(notifications) == 1
    assert notifications[0][:3] == (chat_id, "action_required", "/".join(active_key) + "/error")

    for i in range(513):
        key, request = add_interaction(str(i))
        frame._complete_kimi_interaction_attempt(key, request, "responding", "ended", success=True)
        ended(key)
    assert frame._kimi_interactions[active_key] is active
    assert list(frame._kimi_interactions) == [long_key, active_key, *((session_id, str(i)) for i in range(1, 513))]
    frame._complete_kimi_interaction_attempt(long_key, long_pending, "responding", "ended", success=True)
    ended(long_key)
    assert frame._kimi_interactions[active_key] is active
    assert list(frame._kimi_interactions) == [active_key, *((session_id, str(i)) for i in range(2, 513)), long_key]
    frame._receive_kimi_interaction(CodexEvent(
        type="server_request", thread_id=session_id, request_id=long_key[1], method="approval",
    ))
    assert frame._kimi_interactions[long_key]["state"] == "ended"
    frame._receive_kimi_interaction(CodexEvent(
        type="server_request", thread_id=session_id, request_id="512", method="approval",
    ))
    assert frame._kimi_interactions[(session_id, "512")]["state"] == "ended"


def test_kimi_archived_interaction_error_is_pruned_on_reload_and_pending_can_replay(frame, monkeypatch):
    fake = _setup_kimi_frame(frame, monkeypatch)
    frame._chat_store_enabled = True
    _submit(frame, "start")
    chat_id = _active_chat_id(frame)
    session_id = frame.active_kimi_session_id
    key = (session_id, "retry")
    frame.archived_chats.append({"id": "other-chat", "turns": []})
    assert frame._switch_current_chat("other-chat")
    request = {"state": "responding", "chat_id": chat_id, "kind": "approval", "params": {}}
    frame._kimi_interactions[key] = request
    frame._complete_kimi_interaction_attempt(key, request, "responding", "uncertain", error="lost response")
    frame._save_state()
    stored = frame.chat_store.load_chat(chat_id)
    assert "lost response" in stored["kimi_interaction_error"]
    assert "lost response" not in frame.GetStatusBar().GetStatusText()
    assert frame._switch_current_chat(chat_id)
    assert frame._visible_kimi_interaction("uncertain") == (key, request)
    assert "lost response" in frame.GetStatusBar().GetStatusText()
    frame._on_chat_information_menu_open(wx.MenuEvent(wx.wxEVT_MENU_OPEN))
    assert frame.GetMenuBar().FindItemById(frame._kimi_retry_interaction_menu_id).IsEnabled()
    assert frame._switch_current_chat("other-chat")

    # Interaction objects only live in memory. Reload the actual SQLite history.
    frame._kimi_interactions.clear()
    frame._load_state()
    assert frame._switch_current_chat(chat_id)
    assert "kimi_interaction_error" not in frame._current_chat_state
    assert "重试 Kimi 请求" not in frame.GetStatusBar().GetStatusText()
    frame._on_chat_information_menu_open(wx.MenuEvent(wx.wxEVT_MENU_OPEN))
    assert not frame.GetMenuBar().FindItemById(frame._kimi_retry_interaction_menu_id).IsEnabled()

    responses = []
    monkeypatch.setattr(main.CodexUserInputDialog, "ShowModal", lambda _dialog: wx.ID_OK)
    monkeypatch.setattr(fake, "answer_interaction", lambda *args: responses.append(args), raising=False)
    pending = CodexEvent(type="server_request", thread_id=session_id, request_id=key[1], method="approval")
    frame._receive_kimi_interaction(pending)
    frame._receive_kimi_interaction(pending)
    assert responses == [(session_id, key[1], {"kind": "approval", "decision": "approved"})]
