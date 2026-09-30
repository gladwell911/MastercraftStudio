import ctypes
import threading
import time
from types import SimpleNamespace

import main
import pytest


_REAL_CODEX_INFORMATION_REQUEST = main.ChatFrame._request_codex_chat_information
_REAL_KIMI_INFORMATION_REQUEST = main.ChatFrame._request_kimi_chat_information
_REAL_KIMI_QUOTA_REQUEST = main.ChatFrame._request_kimi_quota


@pytest.mark.parametrize("model", ["codex/main", "kimi/main"])
@pytest.mark.parametrize("control", ["input_edit", "answer_list", "history_list", "notes_editor"])
def test_epic1_alt_y_restores_every_focus(frame, wx_app, model, control):
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = model
    frame.active_chat_id = "epic1"
    frame._current_chat_state = {"id": "epic1", "model": model, "codex_thread_id": "native", "kimi_session_id": "native"}
    frame._apply_detail_panel_mode("answers")
    if control == "notes_editor":
        frame.notes_controller.notes_view = "note_edit"
        frame._notes_sync_view_visibility()
    frame.Raise()
    ctypes.WinDLL("user32", use_last_error=True).SetForegroundWindow(int(frame.GetHandle()))
    wx_app.Yield()
    target = getattr(frame, control)
    target.SetFocus()
    wx_app.Yield()
    assert target.IsShownOnScreen() and target.IsEnabled()
    assert main.wx.Window.FindFocus() is target
    event = main.wx.KeyEvent(main.wx.wxEVT_CHAR_HOOK)
    event.SetKeyCode(ord("Y"))
    event.SetAltDown(True)
    frame._on_char_hook(event)
    dialog = frame._chat_information_dialog
    assert dialog is not None
    assert dialog.focus_target is target
    wx_app.Yield()
    assert main.wx.Window.FindFocus() is dialog.information_list
    assert dialog.context_timer.GetInterval() == 10000
    dialog._on_char_hook(SimpleNamespace(GetKeyCode=lambda: main.wx.WXK_ESCAPE))
    wx_app.Yield()
    assert frame._chat_information_dialog is None
    assert main.wx.Window.FindFocus() is target


@pytest.mark.parametrize("model", ["codex/main", "kimi/main"])
def test_epic1_context_timer_and_closed_lifetime(frame, wx_app, monkeypatch, model):
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = model
    frame.active_chat_id = "epic1"
    frame._current_chat_state = {"id": "epic1", "model": model, "codex_thread_id": "native", "kimi_session_id": "native"}
    calls = []
    method = "_request_codex_chat_information" if model.startswith("codex") else "_request_kimi_chat_information"
    monkeypatch.setattr(frame, method, lambda *_a, **kw: calls.append(kw.get("context_only", False)))
    assert frame._show_chat_information()
    assert calls == [False]
    dialog = frame._chat_information_dialog
    dialog._on_context_timer(None)
    assert calls == [False, True]
    frame._chat_information_request = (None, 3, dialog.identity)
    frame._kimi_information_requests["epic1"] = {"visible_only": True}
    dialog.Close()
    wx_app.Yield()
    assert frame._chat_information_request is None
    assert "epic1" not in frame._kimi_information_requests
    dialog._on_context_timer(None)
    assert calls == [False, True]


def test_epic1_codex_pull_rejects_event_revision_and_keeps_failure_cache(frame, wx_app):
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = "codex/main"
    frame.active_chat_id = "epic1"
    chat = {"id": "epic1", "model": "codex/main", "codex_thread_id": "native"}
    frame._current_chat_state = chat
    assert frame._show_chat_information()
    identity = frame._chat_information_dialog.identity
    def apply(generation, usage=None, error=None):
        frame._chat_information_request = (None, generation, identity)
        frame._apply_codex_chat_information("epic1", {}, {"identity": list(identity), "generation": generation,
            "context_only": True, "native_usage": usage or {}, "usage_error": error})
    native = {"context_usage": {"source": "codex", "used_tokens": 120, "context_window": 1000},
              "session_total_tokens": 900, "observed_at": "2026-09-30T01:00:00Z"}
    apply(1, native)
    assert chat["codex_session_total_tokens"] == 900
    assert "12.0%" in frame._chat_information_dialog.information_list.GetString(0)
    apply(2, error="offline")
    assert "查询失败" in frame._chat_information_dialog.information_list.GetString(0)
    assert chat["codex_session_total_tokens"] == 900
    chat["codex_usage_revision"] = 1
    apply(3, {"session_total_tokens": 9999})
    assert chat["codex_session_total_tokens"] == 900
    frame._codex_information_revision = 1
    apply(4, native)
    assert "查询失败" not in frame._chat_information_dialog.information_list.GetString(0)
    frame._chat_information_dialog.Close()
    wx_app.Yield()


@pytest.mark.parametrize("provider", ["codex", "kimi"])
@pytest.mark.parametrize("field", ["session", "account"])
def test_epic1_cached_usage_is_invalidated_without_callback(frame, provider, field):
    model = provider + "/main"
    native_key = "codex_thread_id" if provider == "codex" else "kimi_session_id"
    chat = {"id": "epic1", "model": model, native_key: "old", provider + "_account_id": "a",
            provider + "_session_total_tokens": 900}
    frame._set_chat_context_usage(chat, {"source": provider, "used_tokens": 120, "context_window": 1000})
    chat[native_key if field == "session" else provider + "_account_id"] = "new"
    rows = frame._chat_information_rows(chat, model)
    assert "暂不可用" in rows[0] and "暂不可用" in rows[1]
    assert chat[provider + "_session_total_tokens"] is None


def test_epic1_old_kimi_callback_does_not_clear_new_session_cache(frame):
    chat = {"id": "epic1", "model": "kimi/main", "kimi_session_id": "new", "kimi_session_total_tokens": 900}
    frame._current_chat_state = chat
    frame.active_chat_id = frame.current_chat_id = "epic1"
    frame._set_chat_context_usage(chat, {"source": "kimi", "used_tokens": 120, "context_window": 1000})
    identity = ("epic1", "kimi/main", "old", "")
    frame._kimi_information_requests["epic1"] = {"generation": 1, "identity": identity}
    frame._apply_kimi_chat_information_result("epic1", 1, identity, "snapshot", None, True)
    assert chat["kimi_session_total_tokens"] == 900


def test_epic1_real_kimi_context_only_updates_status_without_snapshot(frame, wx_app, monkeypatch):
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = "kimi/main"
    frame.active_chat_id = frame.current_chat_id = "epic1"
    chat = {"id": "epic1", "model": "kimi/main", "kimi_session_id": "native"}
    frame._current_chat_state = chat
    assert frame._show_chat_information()
    calls = []
    class Client:
        def start(self):
            pass
        def get_status(self, session):
            calls.append("status")
            return {"context_tokens": 120, "max_context_tokens": 1000}
        def get_snapshot(self, session):
            calls.append("snapshot")
            return {}
    monkeypatch.setattr(frame, "_ensure_kimi_client", lambda: Client())
    monkeypatch.setattr(main.ChatFrame, "_request_kimi_chat_information", _REAL_KIMI_INFORMATION_REQUEST)
    frame._chat_information_dialog._on_context_timer(None)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and "12.0%" not in frame._chat_information_dialog.information_list.GetString(0):
        wx_app.Yield()
        time.sleep(.01)
    assert calls == ["status"]
    assert "12.0%" in frame._chat_information_dialog.information_list.GetString(0)
    frame._chat_information_dialog.Close()
    wx_app.Yield()


@pytest.mark.parametrize("first_binding", [False, True])
def test_epic1_quota_error_or_first_identity_does_not_delete_usage(frame, wx_app, first_binding):
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = "kimi/main"
    frame.active_chat_id = frame.current_chat_id = "epic1"
    chat = {"id": "epic1", "model": "kimi/main", "kimi_session_id": "native",
            "kimi_session_total_tokens": 900, "kimi_total_observed_at": 1700000000,
            "kimi_verified_account_id": "" if first_binding else "A"}
    frame._current_chat_state = chat
    frame._set_chat_context_usage(chat, {"source": "kimi", "used_tokens": 120,
                                       "context_window": 1000, "updated_at": 1700000000})
    assert frame._show_chat_information()
    dialog = frame._chat_information_dialog
    before = list(dialog.information_list.GetStrings())[:2]
    frame._kimi_quota_request = (1, dialog.identity)
    payload = {"kind": "ok", "_account_id": "A", "quota": {}} if first_binding else None
    frame._apply_kimi_quota(1, dialog.identity, payload, not first_binding)
    assert chat["kimi_verified_account_id"] == "A"
    assert list(dialog.information_list.GetStrings())[:2] == before
    if not first_binding:
        assert "查询失败" in dialog.information_list.GetString(2)
    dialog.Close()
    wx_app.Yield()


def test_epic1_total_only_native_timestamp_and_send_failure(frame, wx_app):
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = "codex/main"
    frame.active_chat_id = "epic1"
    chat = {"id": "epic1", "model": "codex/main", "codex_thread_id": "native"}
    frame._current_chat_state = chat
    assert frame._show_chat_information()
    dialog = frame._chat_information_dialog
    frame._chat_information_request = (None, 1, dialog.identity)
    frame._apply_codex_chat_information("epic1", {}, {"identity": list(dialog.identity), "generation": 1,
        "context_only": True, "native_usage": {"context_usage": None, "session_total_tokens": 900,
                                                  "observed_at": "2026-09-30T01:00:00Z"}})
    assert chat["codex_total_observed_at"] == main.datetime.fromisoformat("2026-09-30T01:00:00+00:00").timestamp()
    previous_stamp = chat["codex_total_observed_at"]
    frame._chat_information_request = (None, 2, dialog.identity)
    frame._fail_codex_chat_information_request(2, dialog.identity)
    assert "查询失败" in dialog.information_list.GetString(1)
    assert chat["codex_session_total_tokens"] == 900 and chat["codex_total_observed_at"] == previous_stamp
    dialog.Close()
    wx_app.Yield()


@pytest.fixture(autouse=True)
def _disable_external_codex_information_reads(monkeypatch):
    monkeypatch.setattr(main.ChatFrame, "_request_codex_chat_information", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main.ChatFrame, "_request_kimi_chat_information", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main.ChatFrame, "_request_kimi_quota", lambda *_args, **_kwargs: None)


def _send_key(window, key):
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    hwnd = int(window.GetHandle())
    user32.SendMessageW(hwnd, 0x0100, key, 1)
    user32.SendMessageW(hwnd, 0x0101, key, 1 | (1 << 30) | (1 << 31))


def test_kimi_quota_rows_keep_focus_and_reject_stale_owner(frame, wx_app, monkeypatch):
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = "kimi/main"
    frame.active_chat_id = "chat-q"
    chat = {"id": "chat-q", "model": "kimi/main", "kimi_session_id": "session-q",
            "kimi_account_id": "account-q"}
    frame._current_chat_state = chat
    frame.input_edit.SetFocus()
    wx_app.Yield()
    assert frame._show_chat_information()
    dialog = frame._chat_information_dialog
    dialog.information_list.SetSelection(2)
    dialog.information_list.SetFocus()
    identity = dialog.identity
    frame._kimi_quota_request = (1, identity)
    frame._apply_kimi_quota(1, identity, {
        "kind": "ok", "quota": {"usages": {
            "limit5h": {"usedRatio": .25, "resetAt": "2026-09-27T12:00:00Z"},
            "limit7d": {"usedRatio": .5, "resetAt": "2030-01-01T00:00:00Z"}}}}, False)
    rows = list(dialog.information_list.GetStrings())
    assert "已用 25.0%" in rows[2] and "本地重置" in rows[2]
    assert "已用 50.0%" in rows[3] and "距重置" in rows[3]
    assert dialog.information_list.GetSelection() == 2
    assert main.wx.Window.FindFocus() is dialog.information_list
    chat["kimi_session_id"] = "session-new"
    frame._apply_kimi_quota(1, identity, None, True)
    assert chat["kimi_quota_payload"]["kind"] == "ok"
    dialog.Close()
    wx_app.Yield()
    assert frame._show_chat_information()
    dialog = frame._chat_information_dialog
    assert "上次更新于" in dialog.information_list.GetStrings()[2]
    dialog.Close()
    wx_app.Yield()


def test_kimi_quota_without_session_and_timer_skips_session_reads(frame, wx_app, monkeypatch):
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = "kimi/main"
    frame.active_chat_id = "chat-q"
    chat = {"id": "chat-q", "model": "kimi/main"}
    frame._current_chat_state = chat
    calls = []
    monkeypatch.setattr(frame, "_request_kimi_chat_information", lambda *_a, **_k: calls.append("session"))
    monkeypatch.setattr(frame, "_request_kimi_quota", lambda *_a, **_k: calls.append("quota"))
    assert frame._show_chat_information()
    assert calls == ["session", "quota"]
    calls.clear()
    dialog = frame._chat_information_dialog
    dialog._on_refresh_timer(None)
    assert calls == ["quota"]
    dialog.Close()
    wx_app.Yield()


def test_kimi_quota_explicit_auth_states():
    assert "不适用" in main.ChatFrame._kimi_quota_labels({"kind": "not_applicable"})[0]
    assert "未登录" in main.ChatFrame._kimi_quota_labels({"kind": "unauthenticated"})[0]
    assert "未登录" in main.ChatFrame._kimi_quota_labels({"kind": "error", "error": {"code": "unauthorized"}})[0]
    assert "查询失败" in main.ChatFrame._kimi_quota_labels({"kind": "error", "error": {"code": "not_applicable"}})[0]
    assert "查询失败" in main.ChatFrame._kimi_quota_labels({"kind": "error"})[0]


def test_kimi_event_and_rest_context_share_usage_parser(frame):
    values = {"context_tokens": 300, "max_context_tokens": 1200}
    event = main.CodexEvent(type="thread_status_changed", usage=values)
    direct = frame._kimi_context_usage_from_values(values)
    via_event = frame._kimi_context_usage_payload(event)
    assert {key: value for key, value in direct.items() if key != "updated_at"} == {
        key: value for key, value in via_event.items() if key != "updated_at"}


def test_kimi_quota_account_result_replaces_previous_account(frame, wx_app):
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = "kimi/main"
    frame.active_chat_id = "chat-q"
    chat = {"id": "chat-q", "model": "kimi/main"}
    frame._current_chat_state = chat
    assert frame._show_chat_information()
    identity = frame._chat_information_dialog.identity
    for generation, user_id, ratio in ((1, "user-1", .2), (2, "user-2", .7)):
        frame._kimi_quota_request = (generation, identity)
        frame._apply_kimi_quota(generation, identity, {"kind": "ok", "_account_id": user_id,
            "quota": {"usages": {"limit5h": {"usedRatio": ratio}}}}, False)
    assert chat["kimi_quota_owner"] == "user-2"
    assert chat["kimi_verified_account_id"] == "user-2"
    assert "70.0%" in frame._chat_information_dialog.information_list.GetString(2)
    assert "上次更新于" in frame._chat_information_dialog.information_list.GetString(2)
    chat["kimi_verified_account_id"] = "user-3"
    frame._refresh_chat_information(chat)
    assert "暂不可用" in frame._chat_information_dialog.information_list.GetString(2)
    frame._kimi_quota_request = (3, identity)
    frame._apply_kimi_quota(2, identity, {"kind": "ok", "_account_id": "user-2",
        "quota": {"usages": {"limit5h": {"usedRatio": .9}}}}, False)
    assert chat["kimi_verified_account_id"] == "user-3"
    assert "暂不可用" in frame._chat_information_dialog.information_list.GetString(2)
    same_payload = chat["kimi_quota_payload"]
    frame._kimi_quota_request = (4, identity)
    frame._apply_kimi_quota(4, identity, same_payload, False)
    assert chat["kimi_verified_account_id"] == "user-2"
    assert "70.0%" in frame._chat_information_dialog.information_list.GetString(2)
    frame._chat_information_dialog.Close()
    wx_app.Yield()


def test_kimi_quota_http_401_is_unlogged_and_reopen_clears_cache(frame, wx_app, monkeypatch):
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = "kimi/main"
    frame.active_chat_id = "chat-q"
    chat = {"id": "chat-q", "model": "kimi/main"}
    frame._current_chat_state = chat
    assert frame._show_chat_information()
    monkeypatch.setattr(main.ChatFrame, "_request_kimi_quota", _REAL_KIMI_QUOTA_REQUEST)

    class ImmediateThread:
        def __init__(self, target=None, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    class Client:
        def start(self):
            pass

        def get_auth(self):
            return {"managed_provider": {"status": "authenticated"}}

        def get_oauth_userinfo(self):
            return {"kind": "ok", "userInfo": {"userId": "user-1"}}

        def get_oauth_usage(self):
            raise main.KimiServerError("unauthorized", status_code=401)

    monkeypatch.setattr(main.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(frame, "_ensure_kimi_client", lambda: Client())
    frame._request_kimi_quota(chat, "kimi/main")
    wx_app.Yield()
    assert "未登录" in frame._chat_information_dialog.information_list.GetString(2)
    frame._chat_information_dialog.Close()
    wx_app.Yield()
    monkeypatch.setattr(main.ChatFrame, "_request_kimi_quota", lambda *_a, **_k: None)
    assert frame._show_chat_information()
    assert "暂不可用" in frame._chat_information_dialog.information_list.GetString(2)
    frame._chat_information_dialog.Close()
    wx_app.Yield()


def test_kimi_quota_dialog_timer_account_switch_rejects_late_a_without_session(frame, wx_app, monkeypatch):
    jobs, callbacks, calls = [], [], []
    account = ["A"]
    frame.Show()
    wx_app.Yield()

    class QueuedThread:
        def __init__(self, target=None, **_kwargs):
            self.target = target

        def start(self):
            jobs.append(self.target)

    class Client:
        def start(self):
            calls.append("start")

        def get_auth(self):
            calls.append("auth")
            return {"managed_provider": {"status": "authenticated"}}

        def get_oauth_userinfo(self):
            calls.append("userinfo")
            return {"kind": "ok", "userInfo": {"userId": account[0]}}

        def get_oauth_usage(self):
            calls.append("usage")
            ratio = .2 if account[0] == "A" else .7
            return {"kind": "ok", "quota": {"usages": {"limit5h": {"usedRatio": ratio}}}}

    monkeypatch.setattr(main.ChatFrame, "_request_kimi_quota", _REAL_KIMI_QUOTA_REQUEST)
    monkeypatch.setattr(main.threading, "Thread", QueuedThread)
    monkeypatch.setattr(frame, "_ensure_kimi_client", lambda: Client())
    monkeypatch.setattr(frame, "_call_after_if_alive", lambda callback, *args: callbacks.append((callback, args)))
    frame.view_mode = "active"
    frame.selected_model = "kimi/main"
    frame.active_chat_id = "chat-q"
    frame._current_chat_state = {"id": "chat-q", "model": "kimi/main"}
    frame.input_edit.SetFocus()
    wx_app.Yield()
    assert frame._show_chat_information()
    dialog = frame._chat_information_dialog
    dialog.information_list.SetSelection(2)
    dialog.information_list.SetFocus()
    assert len(jobs) == 1
    jobs.pop(0)()
    assert len(callbacks) == 1
    account[0] = "B"
    dialog._on_refresh_timer(None)
    assert len(jobs) == 1
    jobs.pop(0)()
    # Apply B first, then the delayed A callback.
    callback_b, args_b = callbacks.pop(1)
    callback_b(*args_b)
    callback_a, args_a = callbacks.pop(0)
    callback_a(*args_a)
    wx_app.Yield()
    assert frame._current_chat_state["kimi_quota_owner"] == "B"
    assert "70.0%" in dialog.information_list.GetString(2)
    assert dialog.information_list.GetSelection() == 2
    assert main.wx.Window.FindFocus() is dialog.information_list
    assert "status" not in calls and "snapshot" not in calls
    assert calls.count("usage") == 2
    dialog.Close()
    wx_app.Yield()


def test_kimi_quota_real_thread_delivers_to_wx_dialog(frame, wx_app, monkeypatch):
    account = ["A"]
    calls = []

    class Client:
        def start(self):
            pass

        def get_auth(self):
            return {"managed_provider": {"status": "authenticated"}}

        def get_oauth_userinfo(self):
            return {"kind": "ok", "userInfo": {"userId": account[0]}}

        def get_oauth_usage(self):
            calls.append("usage")
            return {"kind": "ok", "quota": {"usages": {"limit5h": {
                "usedRatio": .2 if account[0] == "A" else .7}}}}

    monkeypatch.setattr(main.ChatFrame, "_request_kimi_quota", _REAL_KIMI_QUOTA_REQUEST)
    monkeypatch.setattr(frame, "_ensure_kimi_client", lambda: Client())
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = "kimi/main"
    frame.active_chat_id = "chat-q"
    chat = {"id": "chat-q", "model": "kimi/main"}
    frame._current_chat_state = chat
    frame.input_edit.SetFocus()
    wx_app.Yield()
    assert frame._show_chat_information()
    dialog = frame._chat_information_dialog
    dialog.information_list.SetSelection(2)
    dialog.information_list.SetFocus()

    def pump_until_owner(expected):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            wx_app.Yield()
            if chat.get("kimi_quota_owner") == expected:
                return
        pytest.fail(f"quota callback for {expected} was not delivered")

    pump_until_owner("A")
    assert "20.0%" in dialog.information_list.GetString(2)
    account[0] = "B"
    dialog._on_refresh_timer(None)
    pump_until_owner("B")
    assert "70.0%" in dialog.information_list.GetString(2)
    assert calls == ["usage", "usage"]
    assert dialog.information_list.GetSelection() == 2
    assert main.wx.Window.FindFocus() is dialog.information_list
    dialog.Close()
    wx_app.Yield()


def test_kimi_seven_day_countdown_timer_changes_only_visible_text(frame, wx_app, monkeypatch):
    from datetime import datetime, timezone

    clock = [datetime(2029, 12, 31, 22, 0, tzinfo=timezone.utc)]

    class ClockDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz) if tz else clock[0].replace(tzinfo=None)

    monkeypatch.setattr(main, "datetime", ClockDateTime)
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = "kimi/main"
    frame.active_chat_id = "chat-q"
    chat = {"id": "chat-q", "model": "kimi/main"}
    frame._current_chat_state = chat
    assert frame._show_chat_information()
    dialog = frame._chat_information_dialog
    dialog.information_list.SetSelection(3)
    dialog.information_list.SetFocus()
    identity = dialog.identity
    frame._kimi_quota_request = (1, identity)
    frame._apply_kimi_quota(1, identity, {"kind": "ok", "_account_id": "A", "quota": {"usages": {
        "limit7d": {"usedRatio": .5, "resetAt": "2030-01-01T00:00:00Z"}}}}, False)
    first = dialog.information_list.GetString(3)
    calls = []
    monkeypatch.setattr(frame, "_request_kimi_quota", lambda *_a: calls.append("quota"))
    clock[0] = datetime(2029, 12, 31, 22, 1, tzinfo=timezone.utc)
    dialog._on_refresh_timer(None)
    second = dialog.information_list.GetString(3)
    assert first != second and "1 小时 59 分钟" in second
    assert calls == ["quota"]
    assert dialog.information_list.GetSelection() == 3
    assert main.wx.Window.FindFocus() is dialog.information_list
    dialog.Close()
    wx_app.Yield()


def test_kimi_identical_quota_reply_updates_query_time_without_same_minute_repaint(frame, wx_app, monkeypatch):
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = "kimi/main"
    frame.active_chat_id = "chat-q"
    chat = {"id": "chat-q", "model": "kimi/main"}
    frame._current_chat_state = chat
    assert frame._show_chat_information()
    dialog = frame._chat_information_dialog
    identity = dialog.identity
    payload = {"kind": "ok", "_account_id": "A", "quota": {"usages": {
        "limit5h": {"usedRatio": .2}}}}
    now = [1_700_000_000.0]
    with monkeypatch.context() as patch:
        patch.setattr(main.time, "time", lambda: now[0])
        frame._kimi_quota_request = (1, identity)
        frame._apply_kimi_quota(1, identity, payload, False)
        first_rows = list(dialog.information_list.GetStrings())
        now[0] += 20
        frame._kimi_quota_request = (2, identity)
        frame._apply_kimi_quota(2, identity, payload, False)
        assert chat["kimi_quota_updated_at"] == now[0]
        assert list(dialog.information_list.GetStrings()) == first_rows
        now[0] += 60
        frame._kimi_quota_request = (3, identity)
        frame._apply_kimi_quota(3, identity, payload, False)
        assert "上次更新于" in dialog.information_list.GetString(2)
        assert list(dialog.information_list.GetStrings()) != first_rows
        now[0] += 60
        frame._kimi_quota_request = (4, identity)
        frame._apply_kimi_quota(4, identity, {"kind": "error"}, False)
        assert "kimi_quota_updated_at" not in chat
    dialog.Close()
    wx_app.Yield()


def test_kimi_turn_completion_requests_visible_quota(frame, wx_app, monkeypatch):
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = "kimi/main"
    frame.active_chat_id = frame.current_chat_id = "chat-q"
    turn = {"question": "q", "model": "kimi/main", "request_status": "pending",
            "kimi_turn_id": "turn-q"}
    chat = {"id": "chat-q", "model": "kimi/main", "kimi_session_id": "session-q",
            "turns": [turn], "kimi_turn_active": True}
    frame._current_chat_state = chat
    frame.active_session_turns = chat["turns"]
    assert frame._show_chat_information()
    calls = []
    monkeypatch.setattr(frame, "_finalize_kimi_turn_state", lambda *_a: [0])
    monkeypatch.setattr(frame, "_request_kimi_quota", lambda *_a: calls.append("quota"))
    monkeypatch.setattr(frame, "_request_execution_list_sync", lambda *_a: None)
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: None)
    monkeypatch.setattr(frame, "_refresh_context_usage_after_done", lambda *_a: None)
    monkeypatch.setattr(frame, "_update_active_answer_row", lambda *_a: None)
    monkeypatch.setattr(frame, "_mark_chat_turns_dirty", lambda *_a, **_k: None)
    monkeypatch.setattr(frame, "_append_completed_answer_to_answer_list", lambda *_a: False)
    monkeypatch.setattr(frame, "_refresh_answer_list_preserving_selection", lambda **_k: None)
    monkeypatch.setattr(frame, "_build_execution_entry", lambda *_a: None)
    frame._on_kimi_event_for_chat("chat-q", main.CodexEvent(
        type="turn_completed", thread_id="session-q", turn_id="turn-q", status="completed"))
    assert calls == ["quota"]
    frame._chat_information_dialog.Close()
    wx_app.Yield()


def test_kimi_archived_turn_completion_requests_visible_quota(frame, wx_app, monkeypatch):
    frame.Show()
    frame.view_mode = "history"
    frame.view_history_id = "chat-old"
    frame.active_chat_id = frame.current_chat_id = "chat-current"
    turn = {"question": "q", "model": "kimi/main", "request_status": "pending",
            "kimi_turn_id": "turn-old"}
    archived = {"id": "chat-old", "model": "kimi/main", "kimi_session_id": "session-old", "turns": [turn]}
    monkeypatch.setattr(frame, "_find_archived_chat", lambda chat_id: archived if chat_id == "chat-old" else None)
    monkeypatch.setattr(frame, "_kimi_event_is_compatible_with_chat", lambda *_a: True)
    monkeypatch.setattr(frame, "_kimi_event_turn_index", lambda *_a: 0)
    assert frame._show_chat_information()
    calls = []
    monkeypatch.setattr(frame, "_finalize_kimi_turn_state", lambda *_a: [0])
    monkeypatch.setattr(frame, "_request_kimi_quota", lambda *_a: calls.append("quota"))
    monkeypatch.setattr(frame, "_mark_chat_turns_dirty", lambda *_a, **_k: None)
    monkeypatch.setattr(frame, "_refresh_visible_history_chat", lambda *_a: None)
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)
    monkeypatch.setattr(frame, "_build_execution_entry", lambda *_a: None)
    frame._on_kimi_event_for_chat("chat-old", main.CodexEvent(
        type="turn_completed", thread_id="session-old", turn_id="turn-old", status="completed"))
    assert calls == ["quota"]
    frame._chat_information_dialog.Close()
    wx_app.Yield()


@pytest.mark.parametrize(("provider", "expected"), [
    (None, "不适用"),
    ({"status": "unauthenticated"}, "未登录"),
    ({"status": "expired"}, "未登录"),
    ({"status": "revoked"}, "未登录"),
])
def test_kimi_quota_dialog_auth_states_without_session(frame, wx_app, monkeypatch, provider, expected):
    class ImmediateThread:
        def __init__(self, target=None, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    class Client:
        def start(self):
            pass

        def get_auth(self):
            return {"managed_provider": provider}

        def get_oauth_userinfo(self):
            pytest.fail("userinfo must not be read for this auth state")

        def get_oauth_usage(self):
            pytest.fail("usage must not be read for this auth state")

    monkeypatch.setattr(main.ChatFrame, "_request_kimi_quota", _REAL_KIMI_QUOTA_REQUEST)
    monkeypatch.setattr(main.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(frame, "_ensure_kimi_client", lambda: Client())
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = "kimi/main"
    frame.active_chat_id = "chat-q"
    frame._current_chat_state = {"id": "chat-q", "model": "kimi/main"}
    assert frame._show_chat_information()
    wx_app.Yield()
    assert expected in frame._chat_information_dialog.information_list.GetString(2)
    frame._chat_information_dialog.Close()
    wx_app.Yield()


def test_codex_chat_information_list_arrows_and_escape_restore_focus(frame, wx_app):
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = "codex/main"
    frame.active_chat_id = "chat-1"
    frame.active_codex_thread_id = "thread-1"
    frame._current_chat_state = {
        "id": "chat-1", "model": "codex/main", "codex_thread_id": "thread-1",
        "context_usage": {"used_tokens": 11891, "context_window": 258400,
                          "source": "codex", "exact": True, "fresh": True},
    }
    frame.input_edit.SetFocus()
    wx_app.Yield()

    assert frame._show_chat_information()
    dialog = frame._chat_information_dialog
    wx_app.Yield()
    assert main.wx.Window.FindFocus() is dialog.information_list
    assert dialog.information_list.GetStrings()[0] == "当前上下文：已用 4.6%（11,891 / 258,400 token）"
    assert dialog.information_list.GetCount() == 4

    _send_key(dialog.information_list, 0x28)
    wx_app.Yield()
    assert dialog.information_list.GetSelection() == 1
    _send_key(dialog.information_list, 0x26)
    wx_app.Yield()
    assert dialog.information_list.GetSelection() == 0

    _send_key(dialog.information_list, 0x1B)
    wx_app.Yield()
    assert frame._chat_information_dialog is None
    assert not frame.IsIconized()
    assert main.wx.Window.FindFocus() is frame.input_edit


def test_codex_information_timer_queries_without_unneeded_row_refresh(frame, wx_app, monkeypatch):
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = "codex/main"
    frame.active_chat_id = "chat-t"
    frame._current_chat_state = {"id": "chat-t", "model": "codex/main", "codex_thread_id": "thread-t"}
    assert frame._show_chat_information()
    calls = []
    monkeypatch.setattr(frame, "_refresh_chat_information", lambda *_a: calls.append("refresh"))
    monkeypatch.setattr(frame, "_request_codex_chat_information", lambda *_a: calls.append("request"))
    frame._chat_information_dialog._on_refresh_timer(None)
    assert calls == ["request"]
    frame._chat_information_dialog.Close()
    wx_app.Yield()


def test_chat_information_is_unavailable_for_other_models_and_unchanged_rows_keep_focus(frame, wx_app):
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = "openai/gpt-5.2"
    frame._current_chat_state = {"id": "chat-1", "model": "openai/gpt-5.2"}
    frame._on_chat_information_menu_open(SimpleNamespace(Skip=lambda: None))
    item = frame.GetMenuBar().FindItemById(int(frame._chat_information_menu_id))
    assert not item.IsEnabled()
    assert not frame._show_chat_information()

    frame.selected_model = "kimi/main"
    frame._current_chat_state = {"id": "chat-2", "model": "kimi/main", "kimi_session_id": "session-2"}
    frame._on_chat_information_menu_open(SimpleNamespace(Skip=lambda: None))
    assert item.IsEnabled()
    frame.input_edit.SetFocus()
    wx_app.Yield()
    assert frame._show_chat_information()
    dialog = frame._chat_information_dialog
    dialog.information_list.SetSelection(1)
    dialog.information_list.SetFocus()
    wx_app.Yield()
    frame._refresh_chat_information(frame._current_chat_state)
    assert dialog.information_list.GetSelection() == 1
    assert main.wx.Window.FindFocus() is dialog.information_list
    dialog.Close()
    wx_app.Yield()


@pytest.mark.parametrize("identity_change", ["chat", "thread", "account"])
def test_stale_chat_information_refresh_cannot_cross_identity(frame, wx_app, identity_change):
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = "codex/main"
    chat = {
        "id": "chat-1", "model": "codex/main", "codex_thread_id": "thread-1",
        "codex_account_id": "account-1",
        "context_usage": {"used_tokens": 1000, "context_window": 10000,
                          "source": "codex", "exact": True, "fresh": True},
    }
    frame._current_chat_state = chat
    frame.input_edit.SetFocus()
    wx_app.Yield()
    assert frame._show_chat_information()
    dialog = frame._chat_information_dialog
    old_rows = list(dialog.information_list.GetStrings())

    if identity_change == "chat":
        chat = dict(chat, id="chat-2")
        frame._current_chat_state = chat
    elif identity_change == "thread":
        chat["codex_thread_id"] = "thread-2"
    else:
        chat["codex_account_id"] = "account-2"
    chat["context_usage"] = {"used_tokens": 9000, "context_window": 10000,
                             "source": "codex", "exact": True, "fresh": True}

    frame._refresh_chat_information(chat)
    assert list(dialog.information_list.GetStrings()) == old_rows
    assert frame._show_chat_information()
    assert frame._chat_information_dialog is not dialog
    assert "9,000" in frame._chat_information_dialog.information_list.GetString(0)
    frame._chat_information_dialog.Close()
    wx_app.Yield()


def test_menu_state_is_rechecked_when_model_changes_after_menu_opens(frame):
    frame.view_mode = "active"
    frame.selected_model = "codex/main"
    frame._current_chat_state = {"id": "chat-1", "model": "codex/main"}
    frame._on_chat_information_menu_open(SimpleNamespace(Skip=lambda: None))
    item = frame.GetMenuBar().FindItemById(int(frame._chat_information_menu_id))
    assert item.IsEnabled()

    frame.selected_model = "openai/gpt-5.2"
    frame._current_chat_state = {"id": "chat-1", "model": "openai/gpt-5.2"}
    assert not frame._show_chat_information()
    assert frame._chat_information_dialog is None


@pytest.mark.parametrize("reset_via_clear", [True, False])
def test_codex_thread_reset_discards_old_usage_until_new_thread_reports(frame, wx_app, monkeypatch, reset_via_clear):
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = "codex/main"
    frame.active_chat_id = "chat-1"
    frame.current_chat_id = "chat-1"
    frame.active_codex_thread_id = "thread-1"
    chat = {
        "id": "chat-1", "model": "codex/main", "codex_thread_id": "thread-1",
        "codex_turn_id": "turn-1",
        "turns": [{"question": "q", "model": "codex/main", "request_status": "done",
                   "codex_turn_id": "turn-1", "codex_thread_id": "thread-1"}],
        "context_usage": {"used_tokens": 5000, "context_window": 10000,
                          "source": "codex", "exact": True, "fresh": True},
    }
    frame._current_chat_state = chat
    frame.active_session_turns = chat["turns"]
    frame._pending_context_usage_by_turn[("chat-1", 0)] = dict(chat["context_usage"])
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)
    frame.input_edit.SetFocus()
    wx_app.Yield()
    assert frame._show_chat_information()
    dialog = frame._chat_information_dialog
    assert "5,000" in dialog.information_list.GetString(0)

    if reset_via_clear:
        frame._handle_codex_clear_command(chat)
        wx_app.Yield()
        assert dialog.identity[2] == ""
        assert dialog.information_list.GetString(0) == "当前上下文：暂不可用"
        frame._on_codex_event_for_chat(
            "chat-1", main.CodexEvent(type="token_count", thread_id="thread-1",
                                       usage={"used_tokens": 8000, "context_window": 10000,
                                              "source": "codex", "exact": True, "fresh": True}),
        )
        assert dialog.information_list.GetString(0) == "当前上下文：暂不可用"

    chat["turns"].append({"question": "q2", "model": "codex/main", "request_status": "pending",
                          "codex_turn_id": "turn-2",
                          "codex_context_generation": int(chat.get("codex_context_generation") or 0)})
    start_generation = int(chat.get("codex_context_generation") or 0)
    frame.active_turn_idx = 1
    frame._apply_codex_worker_thread_state(
        "chat-1", {"thread_id": "thread-2", "turn_id": "turn-2", "turn_idx": 1,
                   "context_generation": start_generation},
    )
    wx_app.Yield()
    assert chat["context_usage"] is None
    assert ("chat-1", 0) not in frame._pending_context_usage_by_turn
    assert dialog.identity[2] == "thread-2"
    assert dialog.information_list.GetString(0) == "当前上下文：暂不可用"

    frame._on_codex_event_for_chat(
        "chat-1", main.CodexEvent(type="token_count", thread_id="thread-1", turn_id="turn-1",
                                   usage={"used_tokens": 8000, "context_window": 10000,
                                          "source": "codex", "exact": True, "fresh": True}),
    )
    assert dialog.information_list.GetString(0) == "当前上下文：暂不可用"
    frame._on_codex_event_for_chat(
        "chat-1", main.CodexEvent(type="token_count", thread_id="thread-2", turn_id="turn-2",
                                   data={"turn_idx": 1, "context_generation": start_generation},
                                   usage={"used_tokens": 1000, "context_window": 10000,
                                          "source": "codex", "exact": True, "fresh": True}),
    )
    assert "1,000" in dialog.information_list.GetString(0)
    dialog.Close()
    wx_app.Yield()


def test_codex_total_and_weekly_quota_rows_ignore_stale_reply(frame, wx_app, monkeypatch):
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = "codex/main"
    turn = {"question": "q", "model": "codex/main", "request_status": "done",
            "codex_turn_id": "turn-1", "codex_thread_id": "thread-1"}
    chat = {"id": "chat-1", "model": "codex/main", "codex_thread_id": "thread-1",
            "codex_turn_id": "turn-1", "codex_account_id": "account-1", "turns": [turn]}
    frame.active_chat_id = frame.current_chat_id = "chat-1"
    frame.active_codex_thread_id = "thread-1"
    frame.active_codex_turn_id = "turn-1"
    frame.active_session_turns = chat["turns"]
    frame._current_chat_state = chat
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)
    frame.input_edit.SetFocus()
    wx_app.Yield()
    assert frame._show_chat_information()
    dialog = frame._chat_information_dialog
    dialog.information_list.SetSelection(1)
    dialog.information_list.SetFocus()
    identity = dialog.identity
    frame._chat_information_request = ("req-new", 2, identity)

    stale = {"chat_id": "chat-1", "identity": list(identity), "generation": 1,
             "account": {"account": {"type": "chatgpt", "id": "account-1"}},
             "rate_limits": {"rateLimits": {"limitId": "codex", "secondary": {
                 "windowDurationMins": 10080, "usedPercent": 25, "resetsAt": 1770000000}}}}
    frame._on_codex_worker_message("chat-1", {"type": "chat_information", "id": "req-old", "payload": stale})
    assert dialog.information_list.GetString(3) == "Codex 周额度：暂不可用"

    fresh = dict(stale, generation=2)
    frame._on_codex_worker_message("chat-1", {"type": "chat_information", "id": "req-new", "payload": fresh})
    assert "剩余 75.0%" in dialog.information_list.GetString(3)
    assert "本地重置" in dialog.information_list.GetString(3)
    assert dialog.information_list.GetSelection() == 1
    assert main.wx.Window.FindFocus() is dialog.information_list

    frame._on_codex_event_for_chat("chat-1", main.CodexEvent(
        type="token_count", thread_id="thread-1", turn_id="turn-1",
        data={"turn_idx": 0, "context_generation": 0, "session_total_tokens": 789},
    ))
    assert dialog.information_list.GetString(1).startswith("会话累计 token：789（缓存，上次观测于 ")
    chat["codex_snapshot_error"] = True
    frame._refresh_chat_information(chat)
    assert "查询失败" in dialog.information_list.GetString(1)
    frame._on_codex_event_for_chat("chat-1", main.CodexEvent(
        type="token_count", thread_id="thread-1", turn_id="turn-1",
        data={"turn_idx": 0, "context_generation": 0, "session_total_tokens": 789}))
    assert "查询失败" not in dialog.information_list.GetString(1)
    frame._handle_codex_clear_command(chat)
    assert dialog.information_list.GetString(1) == "会话累计 token：暂不可用"
    frame._on_codex_event_for_chat("chat-1", main.CodexEvent(
        type="token_count", thread_id="thread-1", turn_id="turn-1",
        data={"turn_idx": 0, "context_generation": 0, "session_total_tokens": 999},
    ))
    assert dialog.information_list.GetString(1) == "会话累计 token：暂不可用"
    dialog.Close()
    wx_app.Yield()


def test_codex_information_account_states_and_identity_switch(frame, wx_app, monkeypatch):
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = "codex/main"
    chat = {"id": "chat-1", "model": "codex/main", "codex_thread_id": "thread-1",
            "codex_account_id": "account-1"}
    frame.active_chat_id = frame.current_chat_id = "chat-1"
    frame.active_codex_thread_id = "thread-1"
    frame._current_chat_state = chat
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)
    assert frame._show_chat_information()
    dialog = frame._chat_information_dialog
    identity = dialog.identity

    frame._chat_information_request = ("req-1", 1, identity)
    payload = {"chat_id": "chat-1", "identity": list(identity), "generation": 1,
               "account": {"account": {"type": "apiKey", "id": "account-1"}},
               "rate_limits_error": "not available"}
    frame._on_codex_worker_message("chat-1", {"type": "chat_information", "id": "req-1", "payload": payload})
    assert dialog.information_list.GetString(3) == "Codex 周额度：API key 账号不适用"

    frame._chat_information_request = ("req-2", 2, identity)
    payload = dict(payload, generation=2, account={"account": None}, rate_limits_error="")
    frame._on_codex_worker_message("chat-1", {"type": "chat_information", "id": "req-2", "payload": payload})
    assert dialog.information_list.GetString(2) == "Codex 账号：未登录"
    assert dialog.information_list.GetString(3) == "Codex 周额度：未登录"

    current_rows = list(dialog.information_list.GetStrings())
    frame._chat_information_request = ("req-3", 3, dialog.identity)
    chat["codex_thread_id"] = "thread-2"
    payload = dict(payload, identity=list(dialog.identity), generation=3,
                   account={"account": {"type": "chatgpt", "id": "account-2"}})
    frame._on_codex_worker_message("chat-1", {"type": "chat_information", "id": "req-3", "payload": payload})
    assert list(dialog.information_list.GetStrings()) == current_rows
    closed_identity = dialog.identity
    saved_account_label = chat.get("codex_account_label")
    dialog.Close()
    wx_app.Yield()
    frame._chat_information_request = ("req-late", 4, closed_identity)
    frame._on_codex_worker_message("chat-1", {"type": "chat_information", "id": "req-late",
                                              "payload": dict(payload, identity=list(closed_identity), generation=4)})
    assert chat.get("codex_account_label") == saved_account_label


def test_first_codex_information_read_starts_worker_off_ui_thread(frame, wx_app, monkeypatch):
    monkeypatch.setattr(main.ChatFrame, "_request_codex_chat_information", _REAL_CODEX_INFORMATION_REQUEST)
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = "codex/main"
    frame.active_chat_id = frame.current_chat_id = "chat-1"
    frame.active_codex_thread_id = "thread-1"
    frame._current_chat_state = {"id": "chat-1", "model": "codex/main", "codex_thread_id": "thread-1"}
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)
    started = threading.Event()
    release = threading.Event()
    calls = []

    class Client:
        def start(self):
            calls.append(threading.current_thread())
            started.set()
            assert release.wait(2)

        def read_chat_information(self, **payload):
            frame._on_codex_worker_message("chat-1", {
                "type": "chat_information", "id": "req-1", "payload": {
                    "chat_id": "chat-1", "identity": payload["identity"],
                    "generation": payload["generation"],
                    "account": {"account": {"type": "chatgpt", "id": "account-1"}},
                    "rate_limits": {"rateLimits": {"limitId": "codex", "primary": {
                        "windowDurationMins": 10080, "usedPercent": 20, "resetsAt": 1770000000}}},
                },
            })
            return "req-1"

    monkeypatch.setattr(frame, "_get_or_create_codex_client", lambda *_args: Client())
    frame.input_edit.SetFocus()
    wx_app.Yield()
    assert frame._show_chat_information()
    dialog = frame._chat_information_dialog
    assert started.wait(1)
    assert calls[0] is not threading.main_thread()
    assert main.wx.Window.FindFocus() is dialog.information_list
    assert dialog.information_list.GetString(3) == "Codex 周额度：暂不可用"
    release.set()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and "剩余 80.0%" not in dialog.information_list.GetString(3):
        wx_app.Yield()
        time.sleep(0.01)
    assert "剩余 80.0%" in dialog.information_list.GetString(3)
    assert main.wx.Window.FindFocus() is dialog.information_list
    dialog.Close()
    wx_app.Yield()


def test_codex_account_switch_retires_old_thread_and_rejects_its_tokens(frame, wx_app, monkeypatch):
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = "codex/main"
    turns = [{"question": "q", "model": "codex/main", "request_status": "done",
              "codex_thread_id": "thread-1", "codex_turn_id": "turn-1"}]
    chat = {"id": "chat-1", "model": "codex/main", "codex_thread_id": "thread-1",
            "codex_turn_id": "turn-1", "codex_account_id": "account-A", "turns": turns,
            "codex_session_total_tokens": 500,
            "context_usage": {"used_tokens": 100, "context_window": 1000,
                              "source": "codex", "exact": True, "fresh": True}}
    frame.active_chat_id = frame.current_chat_id = "chat-1"
    frame.active_codex_thread_id = "thread-1"
    frame.active_codex_turn_id = "turn-1"
    frame.active_session_turns = turns
    frame._current_chat_state = chat
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)
    assert frame._show_chat_information()
    dialog = frame._chat_information_dialog
    identity = dialog.identity
    frame._chat_information_request = ("req-switch", 1, identity)
    frame._on_codex_worker_message("chat-1", {"type": "chat_information", "id": "req-switch",
                                              "payload": {"chat_id": "chat-1", "identity": list(identity),
                                                          "generation": 1,
                                                          "account": {"account": {"type": "chatgpt", "id": "account-B"}},
                                                          "rate_limits": {"rateLimits": {}}}})
    assert chat["codex_thread_id"] == ""
    assert frame.active_codex_thread_id == ""
    assert dialog.information_list.GetString(1) == "会话累计 token：暂不可用"
    frame._on_codex_event_for_chat("chat-1", main.CodexEvent(
        type="token_count", thread_id="thread-1", turn_id="turn-1",
        data={"turn_idx": 0, "context_generation": 0, "session_total_tokens": 999},
        usage={"used_tokens": 900, "context_window": 1000,
               "source": "codex", "exact": True, "fresh": True},
    ))
    assert chat["codex_session_total_tokens"] is None
    assert chat["context_usage"] is None
    assert dialog.information_list.GetString(1) == "会话累计 token：暂不可用"
    dialog.Close()
    wx_app.Yield()


def test_kimi_status_snapshot_and_session_clear_keep_focus_and_reject_old_results(frame, wx_app, monkeypatch):
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = "kimi/main"
    chat = {"id": "chat-1", "model": "kimi/main", "kimi_session_id": "session-1",
            "kimi_account_id": "account-1", "turns": []}
    frame.active_chat_id = frame.current_chat_id = "chat-1"
    frame.active_kimi_session_id = "session-1"
    frame._current_chat_state = chat
    frame.active_session_turns = chat["turns"]
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    frame.input_edit.SetFocus()
    wx_app.Yield()


    assert frame._show_chat_information()
    dialog = frame._chat_information_dialog
    dialog.information_list.SetSelection(1)
    dialog.information_list.SetFocus()
    identity = dialog.identity
    frame._kimi_information_requests["chat-1"] = {
        "generation": 1, "identity": identity, "context_revision": 0, "visible_only": True,
        "remaining": {"status", "snapshot"},
    }
    frame._apply_kimi_chat_information_result("chat-1", 1, identity, "status",
                                               {"context_tokens": 1000, "max_context_tokens": 4000}, False)
    frame._apply_kimi_chat_information_result("chat-1", 1, identity, "snapshot",
                                               {"session": {"usage": {"input_tokens": 700,
                                                                      "output_tokens": 200,
                                                                      "cache_read_tokens": 999}}}, False)
    assert "25.0%" in dialog.information_list.GetString(0)
    assert dialog.information_list.GetString(1).startswith("会话累计 token：900（缓存，上次观测于 ")
    assert dialog.information_list.GetSelection() == 1
    assert main.wx.Window.FindFocus() is dialog.information_list

    frame._kimi_information_requests["chat-1"] = {
        "generation": 2, "identity": identity, "context_revision": 0, "visible_only": True,
        "remaining": {"status", "snapshot"},
    }
    frame._apply_kimi_chat_information_result("chat-1", 2, identity, "status",
                                               {"context_tokens": 300}, False)
    assert "300 token，窗口未知" in dialog.information_list.GetString(0)
    frame._kimi_information_requests["chat-1"] = {
        "generation": 3, "identity": identity, "context_revision": 0, "visible_only": True,
        "remaining": {"status", "snapshot"},
    }
    frame._apply_kimi_chat_information_result("chat-1", 3, identity, "status", None, True)
    frame._apply_kimi_chat_information_result("chat-1", 3, identity, "snapshot",
                                               {"session": {"usage": {"input_tokens": 80,
                                                                      "output_tokens": 20}}}, False)
    assert "300 token，窗口未知" in dialog.information_list.GetString(0)
    assert "查询失败" in dialog.information_list.GetString(0)
    assert "上次观测于" in dialog.information_list.GetString(0)
    assert dialog.information_list.GetString(1).startswith("会话累计 token：100（缓存，上次观测于 ")
    frame._kimi_information_requests["chat-1"] = {
        "generation": 4, "identity": identity, "context_revision": 0, "visible_only": True,
        "remaining": {"status", "snapshot"},
    }
    frame._apply_kimi_chat_information_result("chat-1", 4, identity, "status",
                                               {"context_tokens": 200, "max_context_tokens": 1000}, False)
    frame._apply_kimi_chat_information_result("chat-1", 4, identity, "snapshot", None, True)
    assert "20.0%" in dialog.information_list.GetString(0)
    assert dialog.information_list.GetString(1).startswith("会话累计 token：100（缓存，上次观测于 ")
    assert "查询失败" in dialog.information_list.GetString(1)
    frame._kimi_information_requests["chat-1"] = {
        "generation": 5, "identity": identity, "context_revision": 0, "visible_only": True,
        "remaining": {"status", "snapshot"},
    }
    chat["kimi_session_id"] = "session-2"
    frame._apply_kimi_chat_information_result("chat-1", 5, identity, "snapshot",
                                               {"session": {"usage": {"input_tokens": 999,
                                                                      "output_tokens": 999}}}, False)
    assert chat["kimi_session_total_tokens"] is None
    chat["kimi_account_id"] = "account-1"
    frame._handle_kimi_clear_command(chat)
    assert dialog.information_list.GetString(1) == "会话累计 token：暂不可用"
    frame._apply_kimi_chat_information_result("chat-1", 2, identity, "snapshot",
                                               {"session": {"usage": {"input_tokens": 900,
                                                                      "output_tokens": 900}}}, False)
    assert chat["kimi_session_total_tokens"] is None
    dialog.Close()
    wx_app.Yield()


def test_same_thread_old_turn_token_cannot_replace_latest_usage(frame, monkeypatch):
    turns = [
        {"question": "old", "model": "codex/main", "request_status": "done",
         "codex_turn_id": "turn-1", "codex_thread_id": "thread-1",
         "codex_start_generation": 0},
        {"question": "new", "model": "codex/main", "request_status": "pending",
         "codex_turn_id": "turn-2", "codex_thread_id": "thread-1",
         "codex_start_generation": 0},
    ]
    chat = {"id": "chat-1", "model": "codex/main", "codex_thread_id": "thread-1",
            "codex_turn_id": "turn-2", "turns": turns}
    frame.active_chat_id = "chat-1"
    frame.current_chat_id = "chat-1"
    frame.active_codex_thread_id = "thread-1"
    frame.active_codex_turn_id = "turn-2"
    frame.active_turn_idx = 1
    frame.active_session_turns = turns
    frame._current_chat_state = chat
    monkeypatch.setattr(frame, "_save_state", lambda: None)

    old_event = main.CodexEvent(type="token_count", thread_id="thread-1", turn_id="turn-1",
                                data={"turn_idx": 0, "context_generation": 0},
                                usage={"used_tokens": 9000, "context_window": 10000,
                                       "source": "codex", "exact": True, "fresh": True})
    frame._on_codex_event_for_chat("chat-1", old_event)
    assert chat.get("context_usage") is None
    assert ("chat-1", 1) not in frame._pending_context_usage_by_turn

    new_event = main.CodexEvent(type="token_count", thread_id="thread-1", turn_id="turn-2",
                                data={"turn_idx": 1, "context_generation": 0},
                                usage={"used_tokens": 1000, "context_window": 10000,
                                       "source": "codex", "exact": True, "fresh": True})
    frame._on_codex_event_for_chat("chat-1", new_event)
    assert frame._pending_context_usage_by_turn[("chat-1", 1)]["used_tokens"] == 1000
    turns[1]["request_status"] = "done"
    frame._on_codex_event_for_chat("chat-1", main.CodexEvent(
        type="token_count", thread_id="thread-1",
        data={"turn_idx": 1, "context_generation": 0}, usage=new_event.usage,
    ))
    assert chat["context_usage"]["used_tokens"] == 1000


def test_early_codex_usage_survives_ack_but_not_clear_generation(frame, wx_app, monkeypatch):
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = "codex/main"
    frame.active_chat_id = "chat-1"
    frame.current_chat_id = "chat-1"
    chat = {"id": "chat-1", "model": "codex/main", "turns": [
        {"question": "q1", "model": "codex/main", "request_status": "pending",
         "codex_context_generation": 0},
    ]}
    frame._current_chat_state = chat
    frame.active_session_turns = chat["turns"]
    frame.active_turn_idx = 0
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)
    assert frame._show_chat_information()
    dialog = frame._chat_information_dialog
    first_usage = {"used_tokens": 1000, "context_window": 10000,
                   "source": "codex", "exact": True, "fresh": True}
    frame._on_codex_event_for_chat(
        "chat-1", main.CodexEvent(type="token_count", thread_id="thread-1", turn_id="turn-1",
                                   data={"turn_idx": 0, "context_generation": 0}, usage=first_usage),
    )
    assert ("chat-1", 0) in frame._pending_context_usage_by_turn
    assert "1,000" in dialog.information_list.GetString(0)

    frame._apply_codex_worker_thread_state("chat-1", {"thread_id": "thread-1", "turn_id": "turn-1", "turn_idx": 0,
                                                       "context_generation": 0})
    assert ("chat-1", 0) in frame._pending_context_usage_by_turn
    assert "1,000" in dialog.information_list.GetString(0)

    frame._handle_codex_clear_command(chat)
    wx_app.Yield()
    assert dialog.information_list.GetString(0) == "当前上下文：暂不可用"
    frame._apply_codex_worker_thread_state(
        "chat-1", {"thread_id": "thread-1", "turn_id": "turn-1", "turn_idx": 0,
                   "context_generation": 0},
    )
    assert chat["codex_thread_id"] == ""
    frame._on_codex_event_for_chat(
        "chat-1", main.CodexEvent(type="token_count", thread_id="thread-1", turn_id="turn-1",
                                   data={"turn_idx": 0, "context_generation": 0}, usage=first_usage),
    )
    assert dialog.information_list.GetString(0) == "当前上下文：暂不可用"

    chat["turns"].append({"question": "q2", "model": "codex/main", "request_status": "pending",
                          "codex_context_generation": chat["codex_context_generation"]})
    frame.active_turn_idx = 1
    frame._on_codex_event_for_chat(
        "chat-1", main.CodexEvent(type="token_count", thread_id="thread-1", turn_id="turn-1",
                                   data={"turn_idx": 1, "context_generation": 0}, usage=first_usage),
    )
    assert ("chat-1", 1) not in frame._pending_context_usage_by_turn
    frame._on_codex_event_for_chat(
        "chat-1", main.CodexEvent(type="token_count", thread_id="thread-2", turn_id="turn-2",
                                   data={"turn_idx": 1, "context_generation": chat["codex_context_generation"]}, usage=first_usage),
    )
    assert ("chat-1", 1) in frame._pending_context_usage_by_turn
    frame._apply_codex_worker_thread_state("chat-1", {"thread_id": "thread-2", "turn_id": "turn-2", "turn_idx": 1,
                                                       "context_generation": chat["codex_context_generation"]})
    assert ("chat-1", 1) in frame._pending_context_usage_by_turn
    assert "1,000" in dialog.information_list.GetString(0)
    dialog.Close()
    wx_app.Yield()

def test_kimi_completion_refresh_starts_only_for_visible_information(frame, wx_app, monkeypatch):
    monkeypatch.setattr(main.ChatFrame, "_request_kimi_chat_information", _REAL_KIMI_INFORMATION_REQUEST)
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = "kimi/main"
    frame.active_chat_id = frame.current_chat_id = "chat-1"
    chat = {"id": "chat-1", "model": "kimi/main", "kimi_session_id": "session-1"}
    frame._current_chat_state = chat
    calls = []

    class ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self.target = target

        def start(self):
            self.target()

    class Client:
        def start(self):
            calls.append("start")

        def get_status(self, session_id):
            calls.append(("status", session_id))
            return {"context_tokens": 2, "max_context_tokens": 10}

        def get_snapshot(self, session_id):
            calls.append(("snapshot", session_id))
            return {"session": {"usage": {"input_tokens": 3, "output_tokens": 4}}}

    monkeypatch.setattr(main.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(frame, "_ensure_kimi_client", lambda: Client())
    frame._request_kimi_chat_information(chat, "kimi/main", visible_only=True)
    assert calls == []
    assert frame._show_chat_information()
    wx_app.Yield()
    calls.clear()
    frame._request_kimi_chat_information(chat, "kimi/main", visible_only=True)
    wx_app.Yield()
    assert calls == ["start", ("status", "session-1"), ("snapshot", "session-1")]
    assert frame._chat_information_dialog.information_list.GetString(1).startswith("会话累计 token：7（缓存，上次观测于 ")
    frame._chat_information_dialog.Close()
    wx_app.Yield()


def test_kimi_live_status_wins_over_older_panel_status_reply(frame, wx_app):
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = "kimi/main"
    frame.active_chat_id = frame.current_chat_id = "chat-1"
    frame.active_kimi_session_id = "session-1"
    turn = {"question": "q", "model": "kimi/main", "request_status": "done", "kimi_turn_id": "turn-1"}
    chat = {"id": "chat-1", "model": "kimi/main", "kimi_session_id": "session-1", "turns": [turn]}
    frame._current_chat_state = chat
    frame.active_session_turns = chat["turns"]
    frame.active_turn_idx = 0
    assert frame._show_chat_information()
    dialog = frame._chat_information_dialog
    identity = dialog.identity
    frame._kimi_information_requests["chat-1"] = {
        "generation": 1, "identity": identity, "context_revision": 0,
        "visible_only": True, "remaining": {"status", "snapshot"},
    }

    frame._on_kimi_event_for_chat("chat-1", main.CodexEvent(
        type="thread_status_changed", thread_id="session-1", turn_id="turn-1",
        usage={"context_tokens": 100, "max_context_tokens": 1000},
    ))
    assert "10.0%" in dialog.information_list.GetString(0)
    frame._apply_kimi_chat_information_result("chat-1", 1, identity, "status",
                                               {"context_tokens": 900, "max_context_tokens": 1000}, False)
    assert "10.0%" in dialog.information_list.GetString(0)
    frame._apply_kimi_chat_information_result("chat-1", 1, identity, "snapshot",
                                               {"session": {"usage": {"input_tokens": 3, "output_tokens": 4}}}, False)
    assert dialog.information_list.GetString(1).startswith("会话累计 token：7（缓存，上次观测于 ")
    dialog.Close()
    wx_app.Yield()


def test_kimi_old_session_status_cannot_replace_current_session_usage(frame, wx_app):
    frame.Show()
    frame.view_mode = "active"
    frame.selected_model = "kimi/main"
    frame.active_chat_id = frame.current_chat_id = "chat-1"
    frame.active_kimi_session_id = "session-2"
    turns = [
        {"question": "old", "model": "kimi/main", "request_status": "done",
         "kimi_session_id": "session-1", "kimi_turn_id": "turn-1"},
        {"question": "new", "model": "kimi/main", "request_status": "done",
         "kimi_session_id": "session-2", "kimi_turn_id": "turn-2"},
    ]
    chat = {"id": "chat-1", "model": "kimi/main", "kimi_session_id": "session-2", "turns": turns}
    frame._current_chat_state = chat
    frame.active_session_turns = turns
    frame.active_turn_idx = 1
    assert frame._show_chat_information()
    dialog = frame._chat_information_dialog

    frame._on_kimi_event_for_chat("chat-1", main.CodexEvent(
        type="thread_status_changed", thread_id="session-2", turn_id="turn-2",
        usage={"context_tokens": 100, "max_context_tokens": 1000},
    ))
    revision = chat.get("kimi_context_revision")
    assert "10.0%" in dialog.information_list.GetString(0)
    frame._on_kimi_event_for_chat("chat-1", main.CodexEvent(
        type="thread_status_changed", thread_id="session-1", turn_id="turn-1",
        usage={"context_tokens": 900, "max_context_tokens": 1000},
    ))
    assert chat.get("kimi_context_revision") == revision
    assert "10.0%" in dialog.information_list.GetString(0)
    dialog.Close()
    wx_app.Yield()
