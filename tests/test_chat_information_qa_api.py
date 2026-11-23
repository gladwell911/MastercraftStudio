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


def test_late_usage_updates_only_own_chat_without_refetching_or_quota(frame, monkeypatch, information_owner):
    desktop, target = information_owner
    events = []
    monkeypatch.setattr(frame, "_publish_remote_nats_event", events.append)
    status, before = frame._remote_api_state_ui({"chat_id": "qa-a"})
    assert status == 200 and before["session_usage"]["tokens"] is None
    target["codex_session_total_tokens"] = 0
    frame._refresh_chat_information(target)
    target["codex_session_total_tokens"] = 15240
    frame._refresh_chat_information(target)
    frame._refresh_chat_information(target)
    _, after = frame._remote_api_state_ui({"chat_id": "qa-a"})
    assert [event["session_usage"]["tokens"] for event in events] == [0, 15240]
    assert all(event["type"] == "chat_session_usage" and event["chat_id"] == "qa-a" for event in events)
    assert before["session_usage"]["stamp"] < events[0]["session_usage"]["stamp"] < after["session_usage"]["stamp"]
    assert after["session_usage"] == events[-1]["session_usage"]
    assert frame._session_total_tokens(desktop, "codex/main") is None
    target["codex_session_total_tokens"] = None
    target["codex_context_generation"] = 1
    frame._refresh_chat_information(target)
    assert events[-1]["session_usage"]["tokens"] is None
    assert events[-1]["session_usage"]["stamp"] > after["session_usage"]["stamp"]


def test_legacy_kimi_final_reads_session_snapshot_without_quota(frame, wx_app, monkeypatch, information_owner):
    _desktop, target = information_owner
    target.update(model="kimi/main", kimi_session_id="kimi-native-a", turns=[{"answer_md": "final"}])
    events = []
    calls = []

    class LegacyClient:
        protocol_version = 1

        def start(self):
            pass

        def get_status(self, session):
            calls.append(("status", session))
            return {"context_tokens": 10, "max_context_tokens": 100}

        def get_snapshot(self, session):
            calls.append(("snapshot", session))
            return {"session": {"usage": {"input_tokens": 12000, "output_tokens": 345}}}

    monkeypatch.setattr(frame, "_kimi_client", LegacyClient())
    monkeypatch.setattr(frame, "_request_kimi_quota", lambda *_args: pytest.fail("cumulative usage queried quota"))
    monkeypatch.setattr(frame, "_publish_remote_nats_event", events.append)
    frame._remote_api_state_ui({"chat_id": "qa-a"})
    frame._refresh_context_usage_after_done(target, target["turns"], 0, "kimi/main")
    deadline = time.monotonic() + 3
    while not events and time.monotonic() < deadline:
        wx_app.Yield()
    assert calls == [("status", "kimi-native-a"), ("snapshot", "kimi-native-a")]
    assert events[-1]["chat_id"] == "qa-a" and events[-1]["session_usage"]["tokens"] == 12345


@pytest.mark.parametrize("model", ["codex/main", "kimi/main"])
def test_information_api_200_keeps_legacy_four_rows_and_all_desktop_details(frame, wx_app, monkeypatch, information_owner, model):
    desktop, target = information_owner
    target["model"] = model
    target["kimi_session_id"] = "kimi-native-a"
    provider = model.split("/")[0]
    target[provider + "_session_total_tokens"] = 12345
    target[provider + "_cache_hits"] = {"last": {"input_tokens": 20, "cached_tokens": 3},
                                        "total": {"input_tokens": 50, "cached_tokens": 10}}
    frame._set_chat_context_usage(target, {"source": provider, "used_tokens": 250, "context_window": 1000})
    expected = frame._chat_information_rows(target, model)
    monkeypatch.setattr(frame._information_subscriptions(), "request", lambda *_a, **_kw: True)
    response = invoke_command(frame, wx_app, command())
    assert response["status"] == 200 and response["ok"] is True
    body = response["body"]
    assert body["chat_id"] == "qa-a" and body["subscription_id"] == "qa-page" and body["generation"] == 1
    assert body["rows"] == expected[:2] + expected[-2:] and len(body["rows"]) == 4
    assert body["detail_rows"] == expected and len(body["detail_rows"]) == 6
    assert body["detail_rows"][2:4] == ["缓存命中：15.0%", "累计缓存命中：20.0%"]
    assert body["session_total_tokens"] == 12345
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
