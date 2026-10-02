"""Information command contracts with real routing and an in-memory publisher.

These tests do not exercise a network socket. The Android Local workflow covers
the actual NATS transport separately.
"""

import asyncio
import json
import threading
import time

import pytest

from remote_nats import RemoteNatsTransport


class RecordingJetStream:
    def __init__(self):
        self.events = []

    async def publish(self, subject, raw):
        self.events.append(json.loads(raw))


def invoke_command(frame, wx_app, payload):
    publisher = RecordingJetStream()
    transport = RemoteNatsTransport(pair_id="default", token="fixture-only",
        jetstream=publisher, on_chat_information=lambda request:
            frame._run_remote_ui_route(frame._remote_api_chat_information_ui, request))
    failures = []
    def run():
        try:
            asyncio.run(transport.handle_command(payload))
        except Exception as exc:
            failures.append(exc)
    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    deadline = time.monotonic() + 3
    while worker.is_alive() and time.monotonic() < deadline:
        wx_app.Yield()
    worker.join(timeout=0)
    assert not worker.is_alive() and not failures
    return next(event for event in publisher.events if event.get("request_id") == payload["id"])


@pytest.fixture
def information_owner(frame, monkeypatch):
    desktop = {"id": "desktop-b", "model": "codex/main"}
    target = {"id": "qa-a", "model": "codex/main", "codex_thread_id": "native-a",
        "codex_account_id": "account-a", "codex_chat_information_account_owner": "account-a",
        "codex_account_label": "Codex 账号：测试账号", "codex_weekly_quota_label": "Codex 周额度：剩余 75.0%"}
    frame._current_chat_state = desktop
    frame.active_chat_id = frame.current_chat_id = "desktop-b"
    monkeypatch.setattr(frame, "_find_archived_chat", lambda key: target if key == "qa-a" else None)
    return desktop, target


def command(**body):
    return {"id": "qa-information", "type": "chat_information", "chat_id": "qa-a",
            "body": {"subscription_id": "qa-page", "generation": 1, **body}}


@pytest.mark.parametrize("model", ["codex/main", "kimi/main"])
def test_information_api_200_preserves_desktop_four_rows(frame, wx_app, monkeypatch, information_owner, model):
    desktop, target = information_owner
    target["model"] = model
    target["kimi_session_id"] = "kimi-native-a"
    provider = model.split("/")[0]
    target[provider + "_session_total_tokens"] = 12345
    frame._set_chat_context_usage(target, {"source": provider, "used_tokens": 250, "context_window": 1000})
    expected = frame._chat_information_rows(target, model)
    monkeypatch.setattr(frame._information_subscriptions(), "request", lambda *_a, **_kw: True)
    response = invoke_command(frame, wx_app, command())
    assert response["status"] == 200 and response["ok"] is True
    body = response["body"]
    assert body["chat_id"] == "qa-a" and body["subscription_id"] == "qa-page" and body["generation"] == 1
    assert body["rows"] == expected and len(body["rows"]) == 4
    assert "25.0%" in body["rows"][0] and "12,345" in body["rows"][1]
    assert body["identity"][0:2] == ["qa-a", model]
    assert isinstance(body["owner_generation"], int) and isinstance(body["revision"], int)
    assert frame._current_chat_state is desktop and frame._chat_information_dialog is None


@pytest.mark.parametrize(("payload", "status", "error"), [
    (command(subscription_id=""), 400, "invalid_information_subscription"),
    ({**command(), "chat_id": "missing-chat"}, 404, "unsupported_chat_information"),
])
def test_information_api_rejects_invalid_subscription_and_unknown_chat(frame, wx_app, information_owner, payload, status, error):
    response = invoke_command(frame, wx_app, payload)
    assert response["status"] == status and response["ok"] is False
    assert response["body"]["error"] == error
    assert not frame._information_subscriptions().subscriptions


@pytest.mark.parametrize("model", ["codex/main", "kimi/main"])
def test_information_api_provider_failure_is_200_with_error_rows(frame, wx_app, monkeypatch, information_owner, model):
    desktop, target = information_owner
    target["model"] = model
    target["kimi_session_id"] = "kimi-native-a"
    changed = []
    monkeypatch.setattr(frame, "_publish_remote_nats_event", changed.append)
    class UnavailableProvider:
        def start(self):
            raise RuntimeError("controlled provider failure")
    monkeypatch.setattr(frame, "_get_or_create_codex_client", lambda *_a: UnavailableProvider())
    monkeypatch.setattr(frame, "_ensure_kimi_client", lambda: UnavailableProvider())
    opening = invoke_command(frame, wx_app, command())
    assert opening["status"] == 200
    deadline = time.monotonic() + 3
    while (not changed or not all("查询失败" in row for row in changed[-1]["rows"])) and time.monotonic() < deadline:
        wx_app.Yield()
    assert changed and all("查询失败" in row for row in changed[-1]["rows"])
    response = invoke_command(frame, wx_app, command(context_only=True))
    assert response["status"] == 200 and response["ok"] is True
    assert len(response["body"]["rows"]) == 4
    assert all("查询失败" in row for row in response["body"]["rows"])
    assert frame._current_chat_state is desktop and frame._chat_information_dialog is None
