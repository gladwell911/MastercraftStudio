"""Opt-in, read-only provider checks through the real chat information dialog."""

import os
import re
import time

import pytest

import main
from kimi_server_client import KimiServerClient, kimi_snapshot_total_tokens, resolve_kimi_launch_command


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("CHAT_INFORMATION_LIVE_TEST") != "1",
                       reason="set CHAT_INFORMATION_LIVE_TEST=1 for read-only live checks"),
]


def _pump_until(wx_app, predicate, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        wx_app.Yield()
        if predicate():
            return
    pytest.fail("read-only provider response was not displayed before timeout")


def test_kimi_readonly_auth_quota_and_optional_session_in_dialog(frame, wx_app, monkeypatch):
    try:
        resolve_kimi_launch_command()
    except Exception:
        pytest.skip("Kimi CLI is unavailable")
    client = KimiServerClient()
    try:
        client.start()
        auth = client.get_auth()
        assert isinstance(auth, dict)
        provider = auth.get("managed_provider")
        auth_status = str(provider.get("status") or "unknown") if isinstance(provider, dict) else "no managed provider"
        print(f"Kimi managed OAuth status: {auth_status}")
        expected_ratios = {}
        if isinstance(provider, dict) and provider.get("status") == "authenticated":
            user = client.get_oauth_userinfo()
            usage = client.get_oauth_usage()
            assert isinstance(user, dict) and isinstance(usage, dict)
            info = user.get("userInfo") if user.get("kind") == "ok" else None
            assert isinstance(info, dict) and bool(str(info.get("userId") or "").strip())
            assert usage.get("kind") == "ok"
            quota = usage.get("quota")
            usages = quota.get("usages") if isinstance(quota, dict) else None
            assert isinstance(usages, dict)
            for key in ("limit5h", "limit7d"):
                window = usages.get(key)
                ratio = window.get("usedRatio") if isinstance(window, dict) else None
                if isinstance(ratio, (int, float)) and not isinstance(ratio, bool) and 0 <= ratio <= 1:
                    expected_ratios[key] = ratio
            assert expected_ratios

        session_id = os.getenv("KIMI_LIVE_SESSION_ID", "").strip()
        if session_id:
            status = client.get_status(session_id)
            snapshot = client.get_snapshot(session_id)
            assert isinstance(status, dict) and isinstance(snapshot, dict)
            used = status.get("context_tokens", status.get("contextTokens"))
            window = status.get("max_context_tokens", status.get("maxContextTokens"))
            total = kimi_snapshot_total_tokens(snapshot)
            assert isinstance(used, (int, float)) and not isinstance(used, bool) and used >= 0
            assert isinstance(window, (int, float)) and not isinstance(window, bool) and window > 0
            assert isinstance(total, int) and total >= 0

        monkeypatch.setattr(frame, "_ensure_kimi_client", lambda: client)
        if not session_id:
            monkeypatch.setattr(frame, "_request_kimi_chat_information", lambda *_a, **_k: None)
        frame.Show()
        frame.view_mode = "active"
        frame.selected_model = "kimi/main"
        frame.active_chat_id = "live-readonly-kimi"
        frame._current_chat_state = {"id": "live-readonly-kimi", "model": "kimi/main",
                                     "kimi_session_id": session_id}
        frame.input_edit.SetFocus()
        wx_app.Yield()
        assert frame._show_chat_information()
        dialog = frame._chat_information_dialog
        _pump_until(wx_app, lambda: frame._current_chat_state.get("kimi_quota_payload") is not None
                    or frame._current_chat_state.get("kimi_quota_error"))
        if session_id:
            _pump_until(wx_app, lambda: "live-readonly-kimi" not in frame._kimi_information_requests)
        rows = list(dialog.information_list.GetStrings())
        assert len(rows) == 4
        if expected_ratios:
            for key, row_index in (("limit5h", 2), ("limit7d", 3)):
                if key in expected_ratios:
                    assert f"已用 {expected_ratios[key] * 100:.1f}%" in rows[row_index]
                else:
                    assert "服务端未提供" in rows[row_index]
        elif auth_status in {"unauthenticated", "expired", "revoked"}:
            assert all("未登录" in row for row in rows[2:])
        elif auth_status == "no managed provider":
            assert all("不适用" in row for row in rows[2:])
        else:
            pytest.fail("Kimi auth status has no expected UI classification")
        if session_id:
            assert f"{int(used):,} / {int(window):,} token" in rows[0]
            assert f"会话累计 token：{total:,}" == rows[1]
        assert main.wx.Window.FindFocus() is dialog.information_list
    finally:
        dialog = getattr(frame, "_chat_information_dialog", None)
        if dialog is not None and not dialog.IsBeingDeleted():
            dialog.Close()
            wx_app.Yield()
        client.close()


def test_codex_readonly_account_and_rate_limits_in_dialog(frame, wx_app):
    try:
        frame.Show()
        frame.view_mode = "active"
        frame.selected_model = "codex/main"
        frame.active_chat_id = "live-readonly-codex"
        chat = {"id": "live-readonly-codex", "model": "codex/main"}
        frame._current_chat_state = chat
        frame.input_edit.SetFocus()
        wx_app.Yield()
        assert frame._show_chat_information()
        _pump_until(wx_app, lambda: frame._chat_information_request is None,
                    seconds=60)
        rows = list(frame._chat_information_dialog.information_list.GetStrings())
        assert len(rows) == 4
        assert rows[2] != "Codex 账号：未查询" and "查询失败" not in rows[2]
        assert rows[3] != "Codex 周额度：暂不可用" and "查询失败" not in rows[3]
        assert re.search(r"剩余 \d+(?:\.\d+)?%", rows[3]) or any(
            state in rows[3] for state in ("未登录", "不适用", "周窗口暂不可用"))
        print("Codex account row category: " + (
            "weekly percentage" if "剩余" in rows[3] else "non-percentage state"))
        assert main.wx.Window.FindFocus() is frame._chat_information_dialog.information_list
    finally:
        dialog = getattr(frame, "_chat_information_dialog", None)
        if dialog is not None and not dialog.IsBeingDeleted():
            dialog.Close()
            wx_app.Yield()
        for client in frame._codex_clients.values():
            client.close()
