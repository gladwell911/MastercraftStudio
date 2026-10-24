import json
from types import SimpleNamespace

import main
import pytest


def test_recent_collection_pages_search_and_stale_cursor_preserve_canonical_chats(frame):
    frame.active_chat_id = frame.current_chat_id = ""
    frame.active_session_turns = []
    frame.archived_chats = [{"id": f"chat-{index}", "title": f"Archive {index}",
                             "updated_at": index + 1, "turn_count": 100}
                            for index in range(100)]
    first_status, first = frame._remote_api_history_list_ui({"collection_page": True})
    assert first_status == 200 and len(first["chats"]) == 30
    assert first["partial"] and first["has_more"]
    assert len(json.dumps(first, ensure_ascii=False).encode()) < 64 * 1024
    seen = {chat["chat_id"] for chat in first["chats"]}
    cursor = first["next_cursor"]
    while cursor:
        status, page = frame._remote_api_history_list_ui({"collection_page": True, "cursor": cursor})
        assert status == 200 and len(page["chats"]) <= 30
        seen.update(chat["chat_id"] for chat in page["chats"])
        cursor = page["next_cursor"]
    assert len(seen) == len(frame.archived_chats) == 100
    _, found = frame._remote_api_history_list_ui({"collection_page": True, "query": "Archive 0"})
    assert found["chats"][0]["chat_id"] == "chat-0"
    frame._invalidate_remote_history_list_cache()
    status, stale = frame._remote_api_history_list_ui({"collection_page": True, "cursor": first["next_cursor"]})
    assert status == 409 and stale["recovery"] == "REFRESH_COLLECTION"


def test_correlated_remote_send_persists_actual_accepted_turn_and_response(frame, monkeypatch):
    target = {"id": "archived-alias", "model": "codex/main", "turns": []}
    frame.active_chat_id = frame.current_chat_id = "visible"
    frame._current_chat_state = {"id": "visible", "model": "codex/main", "turns": []}
    frame.archived_chats = [target]
    monkeypatch.setattr(frame, "_start_codex_worker_for_turn", lambda *_: True)
    monkeypatch.setattr(frame, "_push_remote_state", lambda *_: None)
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda *_: None)
    for alias in ["first", "second"]:
        status, response = frame._remote_api_message_ui({"chat_id": "archived-alias",
            "text": "same", "client_send_id": alias})
        assert status == 200 and response["accepted"] is True
        assert response["client_send_id"] == alias
    assert [turn["client_send_id"] for turn in target["turns"]] == ["first", "second"]
    assert frame._remote_turn_payload(target["turns"][0])["client_send_id"] == "first"
    assert frame.active_chat_id == "visible"


def test_correlated_rejected_provider_does_not_return_or_persist_alias(frame, monkeypatch):
    target = {"id": "rejected-alias", "model": "codex/main", "turns": []}
    frame.active_chat_id = frame.current_chat_id = "visible"
    frame._current_chat_state = {"id": "visible", "model": "codex/main", "turns": []}
    frame.archived_chats = [target]
    monkeypatch.setattr(frame, "_start_codex_worker_for_turn", lambda *_: False)
    status, response = frame._remote_api_message_ui({"chat_id": "rejected-alias",
        "text": "same", "client_send_id": "rejected"})
    assert status == 400 and response["accepted"] is False
    assert "client_send_id" not in response
    assert target["turns"] == []


def test_laptop_machine_settings_reach_actual_transport_startup(frame, monkeypatch, tmp_path):
    from remote_nats_protocol import NatsSubjects
    monkeypatch.setenv('REMOTE_CONTROL_PAIR_ID', 'laptop')
    monkeypatch.setenv('REMOTE_CONTROL_DOMAIN', 'https://laptop.example')
    monkeypatch.setenv('REMOTE_CONTROL_TOKEN', 'laptop-secret')
    frame.app_data_dir = tmp_path
    frame._initialize_remote_control_settings()
    captured = {}
    class Process:
        def __init__(self, config, bundled_dir=None): pass
        def start(self, timeout=10): return SimpleNamespace()
        def stop(self): pass
    class Transport:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.subjects = NatsSubjects.from_pair_id(kwargs['pair_id'])
        def start_threaded(self, url, timeout=10): captured['url'] = url
        def stop(self): pass
    monkeypatch.setattr(main, 'NatsServerProcess', Process)
    monkeypatch.setattr(main, 'RemoteNatsTransport', Transport)
    monkeypatch.setattr(frame, '_ensure_cloudflared_origin_bridge', lambda: None)
    frame._start_remote_nats_server_if_configured(token=frame._read_remote_control_token(), host='127.0.0.1')
    assert captured['pair_id'] == 'laptop'
    assert captured['token'] == 'laptop-secret'
    assert frame._remote_nats_transport.subjects.commands == 'zgwd.laptop.commands'
    assert frame.remote_nats_runtime_status['cloudflared_url'] == 'wss://laptop.example/nats'
    assert frame._build_remote_nats_url() == 'wss://laptop.example/nats?token=laptop-secret'
    assert frame._cloudflare_file_public_base_url() == 'https://laptop.example'


def test_v2_broadcast_commits_owner_fact_to_outbox_instead_of_direct_publish(frame, monkeypatch):
    committed = []
    direct = []
    frame.chat_store = SimpleNamespace(
        v2_writes_enabled=True,
        commit_durable_fact=lambda **kwargs: committed.append(kwargs),
        quarantine=lambda *args: None,
    )
    frame._remote_nats_transport = SimpleNamespace(
        protocol_version=2,
        subjects=SimpleNamespace(pair_id="pair"),
        _loop=None,
    )
    monkeypatch.setattr(frame, "_publish_remote_nats_event", direct.append)

    frame._broadcast_remote_event({"type": "status", "event_id": "evt", "chat_id": "off-screen", "text": "ok"})

    assert not direct
    assert committed[0]["pair_id"] == "pair"
    assert committed[0]["envelope"]["chat_id"] == "off-screen"
    assert committed[0]["envelope"]["body"]["text"] == "ok"


def test_archived_mobile_result_v2_outbox_keeps_interleaved_owners_and_unique_finals(frame, monkeypatch):
    frame._chat_store_enabled = True
    frame.active_chat_id = frame.current_chat_id = "chat-a"
    frame._current_chat_state = {"id": "chat-a", "title": "A", "updated_at": 1.0, "turns": []}
    frame._remote_nats_transport = SimpleNamespace(
        protocol_version=2, subjects=SimpleNamespace(pair_id="pair-mobile"), _loop=None)
    for owner in ("chat-b", "chat-c"):
        frame.chat_store.upsert_chat({"id": owner, "title": owner, "model": main.DEFAULT_CODEX_MODEL})
    frame.archived_chats = frame.chat_store.list_chat_summaries()
    monkeypatch.setattr(frame, "_start_codex_worker_for_turn", lambda *_args: True)
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: None)
    monkeypatch.setattr(frame, "_defer_codex_state_save", lambda: None)
    monkeypatch.setattr(frame, "_request_codex_chat_information", lambda *_args: None)
    monkeypatch.setattr(frame, "_refresh_visible_history_chat", lambda *_args: None)
    sounds = []
    monkeypatch.setattr(frame, "_play_send_sound", lambda: sounds.append("send"))
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: sounds.append("reply"))

    for owner in ("chat-b", "chat-c"):
        status, body = frame._remote_api_message_ui({
            "chat_id": owner, "text": f"phone {owner}", "model": main.DEFAULT_CODEX_MODEL})
        assert (status, body["accepted"]) == (200, True)
    frame._flush_idle_ui_refreshes()
    assert sounds == ["send", "send"]
    assert frame.history_ids[:3] == ["chat-c", "chat-b", "chat-a"]
    assert frame.active_chat_id == "chat-a"
    frame._persist_chat_history_to_store()

    def outbox():
        return [json.loads(bytes(row["payload"]).decode("utf-8"))
                for row in frame.chat_store.pending_outbox(pair_id="pair-mobile", domain="events")]

    assert not any(item["kind"] == "assistant_final" for item in outbox())
    for owner in ("chat-c", "chat-b"):
        frame._apply_codex_worker_thread_state(owner, {
            "chat_id": owner, "turn_idx": 0, "thread_id": f"thread-{owner}",
            "turn_id": f"turn-{owner}", "context_generation": 0, "active": True,
        })
        def event(kind, **kwargs):
            return main.CodexEvent(type=kind, thread_id=f"thread-{owner}",
                                   turn_id=f"turn-{owner}", data={"turn_idx": 0}, **kwargs)
        frame._on_codex_event_for_chat(owner, event("plan_updated", text=f"step {owner}"))
        frame._on_codex_event_for_chat(owner, event("item_completed", phase="final_answer",
                                                     text=f"final {owner}"))
        frame._on_codex_event_for_chat(owner, event("turn_completed", status="completed"))
        frame._persist_chat_history_to_store()
        frame._on_codex_event_for_chat(owner, event("turn_completed", status="completed"))
        frame._persist_chat_history_to_store()

    items = outbox()
    assert sounds == ["send", "send", "reply", "reply"]
    for owner in ("chat-b", "chat-c"):
        owner_items = [item for item in items if item.get("chat_id") == owner]
        assert any(item["kind"] == "state" for item in owner_items)
        assert any(item["kind"] == "history_changed" for item in owner_items)
        finals = [item for item in owner_items if item["kind"] == "assistant_final"]
        assert len(finals) == 1
        assert finals[0]["body"]["text"] == f"final {owner}"
        assert all(item["body"].get("text") != main.REQUESTING_TEXT for item in finals)


def test_v2_broadcast_quarantines_missing_owner_without_selected_chat_fallback(frame, monkeypatch):
    quarantined = []
    frame.chat_store = SimpleNamespace(
        v2_writes_enabled=True,
        commit_durable_fact=lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not commit")),
        quarantine=lambda *args: quarantined.append(args),
    )
    frame._remote_nats_transport = SimpleNamespace(protocol_version=2, subjects=SimpleNamespace(pair_id="pair"), _loop=None)
    monkeypatch.setattr(frame, "_publish_remote_nats_event", lambda payload: (_ for _ in ()).throw(AssertionError("must not publish")))

    frame._broadcast_remote_event({"type": "status", "event_id": "missing", "text": "no owner"})

    assert quarantined[0][0] == "MISSING_OWNER"


def test_v2_broadcast_transient_commit_failure_is_retried(frame, monkeypatch):
    attempts = []
    def commit(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise OSError("database busy")
    frame.chat_store = SimpleNamespace(v2_writes_enabled=True, commit_durable_fact=commit, quarantine=lambda *args: None)
    frame._remote_nats_transport = SimpleNamespace(protocol_version=2, subjects=SimpleNamespace(pair_id="pair"), _loop=None)
    monkeypatch.setattr(frame, "_publish_remote_nats_event", lambda payload: (_ for _ in ()).throw(AssertionError("no direct fallback")))
    class ImmediateTimer:
        daemon = False
        def __init__(self, _delay, callback): self.callback = callback
        def start(self): self.callback()
    monkeypatch.setattr(main.threading, "Timer", ImmediateTimer)

    frame._broadcast_remote_event({"type":"status","event_id":"retry","chat_id":"owner","text":"kept"})

    assert len(attempts) == 2
    assert attempts[1]["envelope"]["event_id"] == "retry"
    assert frame._pending_v2_remote_facts == []


def test_can_bind_loopback_tcp_port_returns_false_when_port_accepts_connections(frame, monkeypatch):
    class _ConnectedSocket:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(main.socket, "create_connection", lambda *args, **kwargs: _ConnectedSocket())

    assert frame._can_bind_loopback_tcp_port(4222) is False


def test_existing_nats_websocket_probe_authenticates_with_configured_token(frame, monkeypatch):
    seen = []
    monkeypatch.setattr(frame, "_remote_local_listener_ready", lambda port: port == 18080)
    monkeypatch.setattr(
        frame,
        "_verify_remote_public_ws",
        lambda url: seen.append(url) or (True, ""),
    )

    assert frame._probe_remote_nats_websocket_port(18080, "secret value") is True
    assert seen == ["ws://127.0.0.1:18080/nats?token=secret%20value"]
    assert frame._probe_remote_nats_websocket_port(18080, "") is False


def test_remote_nats_defaults_to_fixed_domain_when_host_is_unset(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_TOKEN", "secret")
    monkeypatch.delenv("REMOTE_CONTROL_HOST", raising=False)
    monkeypatch.delenv("REMOTE_CONTROL_DOMAIN", raising=False)
    monkeypatch.delenv("REMOTE_CONTROL_PORT", raising=False)

    url = frame._build_remote_nats_url()

    assert url == "wss://rc.tingyou.cc/nats?token=secret"


def test_remote_nats_server_starts_runtime(frame, monkeypatch):
    started = {}

    class _FakeNatsProcess:
        def __init__(self, config, bundled_dir=None):
            started["config"] = config
            started["bundled_dir"] = bundled_dir

        def start(self, timeout=10):
            started["process"] = True
            started["timeout"] = timeout
            return SimpleNamespace()

        def stop(self):
            started["process_stopped"] = True

    class _FakeNatsTransport:
        def __init__(self, **kwargs):
            started["transport_kwargs"] = kwargs

        def start_threaded(self, url, timeout=10):
            started["transport_url"] = url
            started["transport_timeout"] = timeout

        def stop(self):
            started["transport_stopped"] = True

    monkeypatch.setattr(main, "NatsServerProcess", _FakeNatsProcess)
    monkeypatch.setattr(main, "RemoteNatsTransport", _FakeNatsTransport)
    monkeypatch.setattr(frame, "_ensure_cloudflared_origin_bridge", lambda: started.setdefault("bridge", True))

    frame._start_remote_nats_server_if_configured(token="secret", host="127.0.0.1")

    assert started["process"] is True
    assert started["transport_url"] == "nats://127.0.0.1:4222"
    assert frame._remote_nats_process is not None
    assert frame._remote_nats_transport is not None
    assert frame.remote_nats_runtime_status["enabled"] is True
    assert frame.remote_nats_runtime_status["cloudflared_url"] == "wss://rc.tingyou.cc/nats"
    assert started["bridge"] is True


def test_remote_nats_server_does_not_start_legacy_public_compat_server(frame, monkeypatch):
    started = {}

    class _FakeNatsProcess:
        def __init__(self, config, bundled_dir=None):
            started["config"] = config

        def start(self, timeout=10):
            started["process"] = True
            return SimpleNamespace()

        def stop(self):
            started["process_stopped"] = True

    class _FakeNatsTransport:
        def __init__(self, **kwargs):
            started["transport_kwargs"] = kwargs

        def start_threaded(self, url, timeout=10):
            started["transport_url"] = url

        def stop(self):
            started["transport_stopped"] = True

    monkeypatch.setattr(main, "NatsServerProcess", _FakeNatsProcess)
    monkeypatch.setattr(main, "RemoteNatsTransport", _FakeNatsTransport)

    frame._start_remote_servers(token="secret", host="0.0.0.0", port=18080)

    assert started["process"] is True
    assert "compat_started" not in started
    assert "compat_kwargs" not in started


def test_remote_nats_server_reuses_existing_nats_when_port_is_already_in_use(frame, monkeypatch):
    started = {}

    class _FakeNatsProcess:
        def __init__(self, config, bundled_dir=None):
            started["config"] = config

        def start(self, timeout=10):
            raise RuntimeError("NATS port 4222 is already in use")

        def stop(self):
            started["process_stopped"] = True

    class _FakeNatsTransport:
        def __init__(self, **kwargs):
            started["transport_kwargs"] = kwargs

        def start_threaded(self, url, timeout=10):
            started["transport_url"] = url
            started["transport_timeout"] = timeout

        def stop(self):
            started["transport_stopped"] = True

    monkeypatch.setattr(main, "NatsServerProcess", _FakeNatsProcess)
    monkeypatch.setattr(main, "RemoteNatsTransport", _FakeNatsTransport)
    def probe_existing(token):
        started["probe_token"] = token
        return 18080

    monkeypatch.setattr(
        frame,
        "_probe_existing_remote_nats_websocket_port",
        probe_existing,
        raising=False,
    )

    frame._start_remote_nats_server_if_configured(token="secret", host="127.0.0.1")

    assert started["transport_url"] == "nats://127.0.0.1:4222"
    assert frame._remote_nats_process is None
    assert frame._remote_nats_transport is not None
    assert frame.remote_nats_runtime_status["enabled"] is True
    assert frame.remote_nats_runtime_status["last_error"] == ""
    assert started["probe_token"] == "secret"


def test_remote_nats_server_reuses_existing_nats_and_detects_live_websocket_port(frame, monkeypatch):
    started = {}

    class _FakeNatsProcess:
        def __init__(self, config, bundled_dir=None):
            started["config"] = config

        def start(self, timeout=10):
            raise RuntimeError("NATS port 4222 is already in use")

        def stop(self):
            started["process_stopped"] = True

    class _FakeNatsTransport:
        def __init__(self, **kwargs):
            started["transport_kwargs"] = kwargs

        def start_threaded(self, url, timeout=10):
            started["transport_url"] = url

        def stop(self):
            started["transport_stopped"] = True

    monkeypatch.setattr(main, "NatsServerProcess", _FakeNatsProcess)
    monkeypatch.setattr(main, "RemoteNatsTransport", _FakeNatsTransport)
    monkeypatch.setattr(
        frame,
        "_probe_existing_remote_nats_websocket_port",
        lambda token: 18081,
        raising=False,
    )
    monkeypatch.setattr(frame, "_ensure_cloudflared_origin_bridge", lambda: started.setdefault("bridge", True))

    frame._start_remote_nats_server_if_configured(token="secret", host="127.0.0.1")

    assert started["transport_url"] == "nats://127.0.0.1:4222"
    assert frame._remote_nats_process is None
    assert frame._remote_nats_transport is not None
    assert frame.remote_nats_runtime_status["enabled"] is True
    assert frame.remote_nats_runtime_status["websocket_url"] == "ws://127.0.0.1:18081/nats"
    assert frame._remote_nats_websocket_port == 18081
    assert started["bridge"] is True


def test_remote_nats_server_reuse_fails_when_existing_websocket_port_cannot_be_determined(frame, monkeypatch):
    class _FakeNatsProcess:
        def __init__(self, config, bundled_dir=None):
            self.config = config

        def start(self, timeout=10):
            raise RuntimeError("NATS port 4222 is already in use")

        def stop(self):
            return None

    class _FakeNatsTransport:
        def __init__(self, **kwargs):
            raise AssertionError("transport should not start when websocket port is unknown")

    monkeypatch.setattr(main, "NatsServerProcess", _FakeNatsProcess)
    monkeypatch.setattr(main, "RemoteNatsTransport", _FakeNatsTransport)
    monkeypatch.setattr(
        frame,
        "_probe_existing_remote_nats_websocket_port",
        lambda token: None,
        raising=False,
    )

    frame._start_remote_nats_server_if_configured(token="secret", host="127.0.0.1")

    assert frame._remote_nats_process is None
    assert frame._remote_nats_transport is None
    assert frame.remote_nats_runtime_status["enabled"] is False
    assert "websocket" in frame.remote_nats_runtime_status["last_error"].lower()


def test_remote_nats_server_falls_back_when_default_websocket_port_is_unavailable(frame, monkeypatch):
    started = {}

    class _FakeNatsProcess:
        def __init__(self, config, bundled_dir=None):
            started["config"] = config

        def start(self, timeout=10):
            started["process"] = True
            return SimpleNamespace()

        def stop(self):
            started["process_stopped"] = True

    class _FakeNatsTransport:
        def __init__(self, **kwargs):
            started["transport_kwargs"] = kwargs

        def start_threaded(self, url, timeout=10):
            started["transport_url"] = url

        def stop(self):
            started["transport_stopped"] = True

    monkeypatch.setattr(main, "NatsServerProcess", _FakeNatsProcess)
    monkeypatch.setattr(main, "RemoteNatsTransport", _FakeNatsTransport)
    monkeypatch.setattr(
        frame,
        "_can_bind_loopback_tcp_port",
        lambda port: port != 18080,
    )
    monkeypatch.setattr(frame, "_ensure_cloudflared_origin_bridge", lambda: started.setdefault("bridge", True))

    frame._start_remote_nats_server_if_configured(token="secret", host="127.0.0.1")

    assert started["process"] is True
    assert started["config"].websocket_port == 18081
    assert frame.remote_nats_runtime_status["websocket_url"] == "ws://127.0.0.1:18081/nats"
    assert frame._remote_nats_websocket_port == 18081


def test_remote_nats_server_starts_fresh_runtime_on_fallback_tcp_port_when_reused_runtime_auth_fails(frame, monkeypatch):
    started = {"ports": [], "transport_urls": []}
    fallback_port = main.REMOTE_NATS_PORT_FALLBACKS[0]

    class _FakeNatsProcess:
        def __init__(self, config, bundled_dir=None):
            self.config = config
            started["ports"].append((config.port, config.websocket_port))

        def start(self, timeout=10):
            if self.config.port == 4222:
                raise RuntimeError("NATS port 4222 is already in use")
            started["started_port"] = self.config.port
            return SimpleNamespace()

        def stop(self):
            started.setdefault("stopped_ports", []).append(self.config.port)

    class _FakeNatsTransport:
        def __init__(self, **kwargs):
            started.setdefault("transport_kwargs", []).append(kwargs)

        def start_threaded(self, url, timeout=10):
            started["transport_urls"].append(url)
            if url == "nats://127.0.0.1:4222":
                raise RuntimeError("nats: 'Authorization Violation'")

        def stop(self):
            started["transport_stopped"] = True

    monkeypatch.setattr(main, "NatsServerProcess", _FakeNatsProcess)
    monkeypatch.setattr(main, "RemoteNatsTransport", _FakeNatsTransport)
    monkeypatch.setattr(frame, "_probe_existing_remote_nats_websocket_port", lambda token: 18080, raising=False)
    monkeypatch.setattr(frame, "_ensure_cloudflared_origin_bridge", lambda: started.setdefault("bridge", True))
    monkeypatch.setattr(
        frame,
        "_can_bind_loopback_tcp_port",
        lambda port: port not in {4222, 18080},
    )

    frame._start_remote_nats_server_if_configured(token="secret", host="127.0.0.1")

    assert started["transport_urls"] == [
        "nats://127.0.0.1:4222",
        f"nats://127.0.0.1:{fallback_port}",
    ]
    assert started["started_port"] == fallback_port
    assert started["ports"] == [
        (4222, 18081),
        (fallback_port, 18081),
    ]
    assert frame._remote_nats_process is not None
    assert frame._remote_nats_transport is not None
    assert frame.remote_nats_runtime_status["enabled"] is True
    assert frame.remote_nats_runtime_status["tcp_url"] == f"nats://127.0.0.1:{fallback_port}"
    assert frame.remote_nats_runtime_status["websocket_url"] == "ws://127.0.0.1:18081/nats"
    assert started["bridge"] is True


def test_remote_state_includes_nats_runtime_status(frame):
    frame.remote_nats_runtime_url = "ws://127.0.0.1:18080/nats"
    frame.remote_nats_runtime_status = {
        "enabled": True,
        "tcp_url": "nats://127.0.0.1:4222",
        "websocket_url": "ws://127.0.0.1:18080/nats",
        "cloudflared_url": "wss://rc.tingyou.cc/nats",
        "last_error": "",
    }

    status, body = frame._remote_api_state_ui({})

    assert status == 200
    assert body["remote_nats_runtime"]["enabled"] is True
    assert body["remote_nats_runtime_url"] == "ws://127.0.0.1:18080/nats"


def test_remote_notes_changes_uses_couchdb_shape(frame):
    status, body = frame._remote_api_notes_changes(
        {
            "database": "zhuge_notes",
            "since": "0",
            "include_docs": True,
        }
    )

    assert status == 200
    assert "results" in body
    assert "last_seq" in body


def test_remote_notes_changes_reuses_snapshot_for_same_cursor(frame, monkeypatch):
    notebook = frame.notes_store.create_notebook("cache notes")
    frame.notes_store.create_entry(notebook.id, "cache entry", source="manual")
    status, body = frame._remote_api_notes_changes(
        {
            "database": "zhuge_notes",
            "since": "0",
            "include_docs": True,
        }
    )
    assert status == 200
    def fail_load_documents():
        raise AssertionError("same notes cursor should use cached changes response")

    monkeypatch.setattr(frame.notes_store, "load_documents", fail_load_documents)

    second_status, second_body = frame._remote_api_notes_changes(
        {
            "database": "zhuge_notes",
            "since": "0",
            "include_docs": True,
        }
    )

    assert second_status == 200
    assert second_body == body


def test_remote_notes_bulk_docs_returns_write_results(frame):
    status, body = frame._remote_api_notes_bulk_docs(
        {
            "database": "zhuge_notes",
            "docs": [
                {
                    "_id": "notebook:nats-test",
                    "type": "notebook",
                    "title": "notes via nats",
                }
            ],
        }
    )

    assert status == 201
    assert len(body["results"]) == 1
    assert body["results"][0]["id"] == "notebook:nats-test"
    assert body["results"][0]["ok"] is True
    assert str(body["results"][0]["rev"]).startswith("1-")


def test_push_remote_state_also_publishes_nats_event(frame, monkeypatch):
    published = []

    class _FakeNatsTransport:
        def publish_event_threadsafe(self, payload):
            published.append(payload)
            return True

    frame._remote_nats_transport = _FakeNatsTransport()
    monkeypatch.setattr(
        frame,
        "_remote_api_state_ui",
        lambda _payload: (200, {"chat_id": "c1", "status": "idle"}),
    )

    frame._push_remote_state("c1")

    assert len(published) == 1
    assert published[0]["type"] == "state"
    assert published[0]["chat_id"] == "c1"


def test_remote_message_pushes_state_after_accept(frame, monkeypatch):
    pushed = []
    frame.active_chat_id = "chat-e2e"
    frame.current_chat_id = "chat-e2e"
    frame._current_chat_state["id"] = "chat-e2e"
    frame.selected_model = "openai/gpt-5.2"
    monkeypatch.setattr(frame.model_combo, "SetValue", lambda _value: None)
    monkeypatch.setattr(frame.input_edit, "SetValue", lambda _value: None)
    monkeypatch.setattr(
        frame,
        "_submit_question",
        lambda question, **kwargs: (True, ""),
    )
    monkeypatch.setattr(frame, "_push_remote_state", lambda chat_id: pushed.append(("state", chat_id)))
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda chat_id=None: pushed.append(("history", chat_id)))

    status, body = frame._remote_api_message_ui({"chat_id": "chat-e2e", "text": "hello"})

    assert status == 200
    assert body["accepted"] is True
    assert pushed == [("state", "chat-e2e"), ("history", "chat-e2e")]


def test_remote_ui_route_from_worker_thread_is_marshaled_to_ui(frame, monkeypatch):
    posted = []
    main_thread = main.threading.main_thread()
    monkeypatch.setattr(main.threading, "current_thread", lambda: object())
    monkeypatch.setattr(main.threading, "main_thread", lambda: main_thread)

    def _call_after(fn, *args, **kwargs):
        posted.append((fn, args, kwargs))
        fn(*args, **kwargs)
        return True

    monkeypatch.setattr(frame, "_call_after_if_alive", _call_after)

    status, body = frame._run_remote_ui_route(lambda payload: (207, {"seen": payload["text"]}), {"text": "hello"})

    assert (status, body) == (207, {"seen": "hello"})
    assert posted


def test_remote_nats_callbacks_are_ui_marshaled(frame, monkeypatch, wx_app):
    captured = {}

    class _FakeNatsTransport:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def start_threaded(self, url):
            captured["url"] = url

    monkeypatch.setattr(main, "RemoteNatsTransport", _FakeNatsTransport)
    monkeypatch.setattr(main, "NatsServerProcess", lambda config: type("P", (), {"start": lambda self: None})())
    monkeypatch.setattr(frame, "_run_remote_ui_route", lambda callback, payload=None: (299, {"callback": getattr(callback, "__name__", "")}))

    frame._start_remote_nats_server_if_configured(token="secret", host="127.0.0.1")

    assert callable(captured["on_message"])
    assert captured["on_message"]({"type": "message"}) == (299, {"callback": "_remote_api_message_ui"})
    # Exercise production registration and the real bounded information route,
    # not a manually wired transport callback.
    desktop = {"id": "desktop-b", "model": "codex/main"}
    target = {"id": "phone-a", "model": "codex/main", "codex_thread_id": "native-a",
              "codex_session_total_tokens": 123}
    frame._current_chat_state = desktop
    monkeypatch.setattr(frame, "_find_archived_chat", lambda key: target if key == "phone-a" else None)
    monkeypatch.setattr(frame._information_subscriptions(), "request", lambda *_a, **_kw: True)
    monkeypatch.setattr(frame, "_run_remote_ui_route", main.ChatFrame._run_remote_ui_route.__get__(frame))
    monkeypatch.setattr(frame, "_call_after_if_alive", lambda fn, *args: (main.wx.CallAfter(fn, *args), True)[1])
    seen_threads = []
    real_rows = frame._chat_information_rows
    def rows(chat, model):
        seen_threads.append(main.threading.current_thread())
        return real_rows(chat, model)
    monkeypatch.setattr(frame, "_chat_information_rows", rows)
    result = []
    worker = main.threading.Thread(target=lambda: result.append(captured["on_chat_information"]({
        "type": "chat_information", "chat_id": "phone-a",
        "body": {"subscription_id": "registered-page", "generation": 1}})), daemon=True)
    worker.start()
    import time
    deadline = time.monotonic() + 2
    while worker.is_alive() and time.monotonic() < deadline:
        wx_app.Yield()
        time.sleep(.01)
    worker.join(timeout=.1)
    assert result[0][0] == 200
    assert result[0][1]["chat_id"] == "phone-a" and result[0][1]["rows"] == real_rows(target, "codex/main")
    assert len(result[0][1]["rows"]) == 4 and seen_threads == [main.threading.main_thread()]
    assert frame._current_chat_state is desktop and frame._chat_information_dialog is None


def test_on_done_generic_model_publishes_remote_completion_events(frame, monkeypatch):
    frame.active_chat_id = "chat-e2e"
    frame.current_chat_id = "chat-e2e"
    frame._current_chat_state["id"] = "chat-e2e"
    frame.active_session_turns = [
        {
            "question": "hello from emulator",
            "answer_md": main.REQUESTING_TEXT,
            "model": "openai/gpt-5.2",
            "created_at": 0.0,
            "request_status": "pending",
        }
    ]
    frame._current_chat_state["turns"] = frame.active_session_turns
    pushed = []
    sounds = []
    monkeypatch.setattr(frame.new_chat_button, "Enable", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_set_input_hint_idle", lambda: None)
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_refresh_history", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_refresh_answer_list_preserving_selection", lambda: None)
    monkeypatch.setattr(frame, "_call_later_if_alive", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: sounds.append("reply"))
    monkeypatch.setattr(frame, "_refresh_context_usage_after_done", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_is_ui_alive", lambda: False)
    monkeypatch.setattr(frame, "SetStatusText", lambda _text: None)
    monkeypatch.setattr(frame, "_push_remote_state", lambda chat_id: pushed.append(("state", chat_id)))
    monkeypatch.setattr(
        frame,
        "_push_remote_final_answer",
        lambda chat_id, text, **_kwargs: pushed.append(("final_answer", chat_id, text)),
    )
    monkeypatch.setattr(frame, "_push_remote_history_changed", lambda chat_id=None: pushed.append(("history", chat_id)))

    frame._on_done(
        0,
        "desktop received: hello from emulator",
        "",
        "openai/gpt-5.2",
        "",
        "chat-e2e",
    )

    assert frame.active_session_turns[0]["answer_md"] == "desktop received: hello from emulator"
    assert frame.active_session_turns[0]["request_status"] == "done"
    assert sounds == ["reply"]
    assert pushed == [
        ("state", "chat-e2e"),
        ("history", "chat-e2e"),
    ]
    assert ("chat-e2e", 0) in frame._pending_remote_finals

def test_clear_context_active_chat_pushes_history_and_state_events(frame, monkeypatch):
    frame.active_chat_id = "chat-e2e"
    frame.current_chat_id = "chat-e2e"
    frame.active_session_turns = [
        {"question": "hello", "answer_md": "world", "model": "openai/gpt-5.2", "created_at": 1.0}
    ]
    frame._current_chat_state["id"] = "chat-e2e"
    frame._current_chat_state["turns"] = frame.active_session_turns
    published = []

    class _FakeNatsTransport:
        def publish_event_threadsafe(self, payload):
            published.append(payload)
            return True

    frame._remote_nats_transport = _FakeNatsTransport()
    monkeypatch.setattr(frame, "_render_answer_list", lambda *args, **kwargs: None)
    monkeypatch.setattr(frame, "_defer_chat_state_save", lambda: None)
    monkeypatch.setattr(frame, "_mark_openclaw_lifecycle_dirty", lambda: None)

    assert frame._clear_context_and_start_new_chat() is True

    event_types = [event["type"] for event in published]
    assert "history_changed" in event_types
    assert "state" in event_types
    state_event = next(event for event in published if event["type"] == "state")
    assert state_event["chat_id"] == "chat-e2e"
    assert state_event["body"].get("turns") == []
def test_execution_authority_routes_and_structured_errors():
    from remote_nats import RemoteNatsTransport

    class Store:
        def __init__(self): self.calls = []
        def load_execution_page(self, **kwargs):
            self.calls.append(("page", kwargs))
            if kwargs["cursor"] == "bad": raise ValueError("INVALID_EXECUTION_CURSOR")
            return {"entries": [], "revision": 2, "sequence_domain": "events", "snapshot_high": 9}
        def load_execution_range(self, **kwargs):
            self.calls.append(("range", kwargs))
            if kwargs["revision"] != 2: raise ValueError("SNAPSHOT_REQUIRED")
            return {"entries": [], "revision": 2, "from_sequence": kwargs["start_sequence"],
                    "to_sequence": kwargs["end_sequence"]}

    store = Store()
    transport = RemoteNatsTransport(pair_id="pair", token="secret", durable_store=store)
    assert transport._route_command({"type":"execution_tail","chat_id":"chat","limit":100})[0] == 200
    assert transport._route_command({"type":"execution_history","chat_id":"chat","cursor":"opaque"})[0] == 200
    assert store.calls[-1][1]["cursor"] == "opaque"
    assert transport._route_command({"type":"execution_snapshot","chat_id":"chat"})[0] == 200
    status, body = transport._route_command({"type":"execution_backfill","chat_id":"chat",
                                              "revision":2,"from_sequence":4,"to_sequence":6})
    assert status == 200 and (body["from_sequence"], body["to_sequence"]) == (4, 6)
    for payload in (
        {"type":"execution_history","chat_id":"chat","cursor":"bad"},
        {"type":"execution_backfill","chat_id":"chat","revision":1,"from_sequence":4,"to_sequence":6},
        {"type":"execution_backfill","chat_id":"chat","revision":{},"from_sequence":4,"to_sequence":6},
    ):
        status, body = transport._route_command(payload)
        assert status == 409 and body["recovery"] == "SNAPSHOT_REQUIRED"
    unavailable = RemoteNatsTransport(pair_id="pair", token="secret")
    assert unavailable._route_command({"type":"execution_tail","chat_id":"chat"}) == (
        503, {"error":"execution_authority_unavailable"})
    versioned = RemoteNatsTransport(
        pair_id="pair", token="secret",
        on_execution_page=lambda request: (200, {"version": 3, "chat_id": request["chat_id"]}),
    )
    assert versioned._route_command({"type": "execution_page_v3", "chat_id": "chat"}) == (
        200, {"version": 3, "chat_id": "chat"})


def test_remote_ui_deadline_cancels_queued_callback(frame, monkeypatch):
    callbacks, waits = [], []
    class Event:
        value = False
        def is_set(self): return self.value
        def set(self): self.value = True
        def wait(self, timeout): waits.append(timeout); return False
    monkeypatch.setattr(main.threading, "Event", Event)
    monkeypatch.setattr(main.threading, "current_thread", lambda: object())
    monkeypatch.setattr(frame, "_call_after_if_alive", lambda callback: callbacks.append(callback) or True)
    invoked = []
    status, result = frame._run_remote_ui_route(lambda: invoked.append(True))
    assert status == 503 and result["error"] == "ui_timeout"
    assert waits == [5.0]
    callbacks[0]()
    assert invoked == []


def test_sqlite_collection_worker_budgets_escaped_titles_and_unicode_search(frame, tmp_path, monkeypatch):
    from chat_store import ChatStore
    store = ChatStore(tmp_path / "collection.db")
    store.initialize()
    for index in range(45):
        store.upsert_chat({"id": f"chat-{index}", "title": ("Stra\u00dfe" if index == 0 else "x" * 100000 + "\x01" * 10000), "updated_at": index})
    frame.chat_store = store
    frame._chat_store_enabled = True
    frame.active_chat_id = frame.current_chat_id = ""
    frame.active_session_turns = []
    frame._current_chat_state = {}
    monkeypatch.setattr(frame, "_run_remote_ui_route", lambda callback, payload=None: callback(payload))
    status, first = frame._remote_api_history_collection_worker({"collection_page": True})
    assert status == 200 and len(first["chats"]) == 30
    assert len(json.dumps(first).encode()) < 128 * 1024
    status, next_page = frame._remote_api_history_collection_worker({"collection_page": True, "cursor": first["next_cursor"]})
    assert status == 200 and len(next_page["chats"]) == 15
    _, searched = frame._remote_api_history_collection_worker({"collection_page": True, "query": "STRASSE"})
    assert searched["chats"][0]["chat_id"] == "chat-0"


def test_registered_history_handler_bounds_escaped_long_content_and_preserves_older_cursor(frame, tmp_path):
    import asyncio
    from chat_store import ChatStore
    from remote_nats import RemoteNatsTransport
    from remote_nats_protocol import encode_payload
    store = ChatStore(tmp_path / "escaped-history.db")
    store.initialize()
    store.upsert_chat({"id": "escaped", "title": "Escaped"})
    store.replace_turns("escaped", [{"question": "\x01" * 20000, "answer_md": "\x02" * 20000, "request_status": "done"} for _ in range(15)])
    frame.chat_store = store
    frame._chat_store_enabled = True
    frame.active_chat_id = frame.current_chat_id = ""
    frame.archived_chats = store.list_chat_summaries()
    frame.active_session_turns = []
    frame._current_chat_state = {}
    responses = []
    transport = RemoteNatsTransport(pair_id="fixture", token="fixture", on_history_read=frame._remote_api_history_read_ui)
    async def capture(event, reply=None): responses.append(event)
    transport._publish_response = capture
    asyncio.run(transport.handle_command({"id": "history", "type": "history_read", "chat_id": "escaped"}))
    response = responses[0]
    assert response["status"] == 200
    assert len(encode_payload(response)) < 128 * 1024
    assert response["body"]["has_more"] and response["body"]["oldest_cursor"]
    turns = response["body"]["chat"]["turns"]
    assert len(turns) <= 10 and all(turn["answer_continuation"] and turn["answer_seq"] == 0 for turn in turns)


@pytest.mark.parametrize("primary,legacy,expected", [
    (None, None, "cloudflared"), (None, "external", "external"),
    ("cloudflared", "external", "cloudflared"), ("external", None, "external"),
])
def test_remote_tunnel_mode_precedence(frame, monkeypatch, primary, legacy, expected):
    for key, value in [("REMOTE_CONTROL_TUNNEL_MODE", primary),
                       ("CLAUDECODE_REMOTE_CONTROL_TUNNEL_MODE", legacy)]:
        monkeypatch.delenv(key, raising=False)
        if value is not None:
            monkeypatch.setenv(key, value)
    assert frame._remote_tunnel_mode() == expected


def test_remote_tunnel_unknown_mode_is_configuration_error(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_TUNNEL_MODE", "invalid")
    with pytest.raises(ValueError, match="REMOTE_CONTROL_TUNNEL_MODE"):
        frame._remote_runtime_config()


@pytest.mark.parametrize("url,port", [
    ("wss://fixture:8443/nats", 8443), ("wss://fixture/nats", 443),
    ("https://fixture", 443), ("ws://fixture/nats", 80),
    ("http://fixture:8080", 8080),
    ("wss://fixture:0/nats", None), ("wss://fixture:invalid/nats", None), ("wss://fixture:70000/nats", None),
    ("ftp://fixture", None),
])
def test_remote_route_probe_uses_actual_url_port(frame, monkeypatch, url, port):
    from contextlib import nullcontext
    calls = []
    monkeypatch.setattr(main.socket, "create_connection", lambda target, **_: calls.append(target) or nullcontext())
    assert frame._remote_external_route_ready(url) is (port is not None)
    assert calls == ([] if port is None else [("fixture", port)])


@pytest.mark.parametrize("pair", ["default", "laptop"])
def test_external_ignores_stale_cloudflared_urls(frame, monkeypatch, pair):
    monkeypatch.setenv("REMOTE_CONTROL_TUNNEL_MODE", "external")
    monkeypatch.setenv("REMOTE_CONTROL_PAIR_ID", pair)
    monkeypatch.setenv("REMOTE_CONTROL_DOMAIN", f"wss://{pair}.example:8443/nats")
    monkeypatch.setattr(frame, "_read_remote_control_token", lambda: pair + "-token")
    frame.remote_nats_runtime_status = {"enabled": True, "cloudflared_url": "wss://old.example/nats"}
    assert frame._cloudflare_file_public_base_url() == f"https://{pair}.example:8443"
    assert frame._build_remote_nats_url() == f"wss://{pair}.example:8443/nats?token={pair}-token"


@pytest.mark.parametrize("public_ok", [True, False])
def test_external_startup_and_files_probe_public_without_cloudflared(frame, monkeypatch, public_ok):
    monkeypatch.setenv("REMOTE_CONTROL_TUNNEL_MODE", "external")
    monkeypatch.setenv("REMOTE_CONTROL_DOMAIN", "wss://fixture:8443/nats")
    for name in ("_query_cloudflared_service", "_start_managed_cloudflared_process",
                 "_stop_managed_cloudflared_process", "_owned_cloudflared_channel_count",
                 "_restart_cloudflared_service"):
        monkeypatch.setattr(frame, name, lambda *_, **__: pytest.fail("external touched cloudflared"))
    frame._remote_nats_transport = SimpleNamespace(stop=lambda: None)
    frame.remote_nats_runtime_status = {"enabled": True, "websocket_url": "ws://127.0.0.1:18082/nats"}
    frame.file_service = SimpleNamespace(public_base_url="https://fixture:8443")
    monkeypatch.setattr(frame, "_remote_local_listener_ready", lambda *_: True)
    monkeypatch.setattr(frame, "_verify_remote_local_health", lambda *_: (True, ""))
    monkeypatch.setattr(frame, "_start_cloudflared_origin_proxy", lambda: True)
    monkeypatch.setattr(frame, "_verify_remote_public_ws_with_retries", lambda *_: (public_ok, "public failed"))
    monkeypatch.setattr(frame, "_verify_remote_public_ws", lambda *_: (public_ok, "public failed"))
    monkeypatch.setattr(frame, "_set_status_text_safe", lambda *_: None)
    if public_ok:
        frame._ensure_remote_nats_startup_connectivity(token="fixture", published_url="wss://fixture:8443/nats")
    else:
        with pytest.raises(RuntimeError, match="public failed"):
            frame._ensure_remote_nats_startup_connectivity(token="fixture", published_url="wss://fixture:8443/nats")
    assert frame.remote_control_runtime_status["public_ws_ready"] is public_ok
    assert frame._ensure_public_file_route_ready() is public_ok
    frame._stop_remote_servers()


@pytest.mark.parametrize("port_text", ["0", "65536", "invalid"])
def test_explicit_nats_port_rejects_invalid_config(frame, monkeypatch, port_text):
    monkeypatch.setenv("REMOTE_CONTROL_NATS_PORT", port_text)
    monkeypatch.setattr(main, "NatsServerProcess", lambda *_: pytest.fail("invalid port started runtime"))
    with pytest.raises(ValueError, match="REMOTE_CONTROL_NATS_PORT"):
        frame._start_remote_nats_server_if_configured(token="fixture", host="127.0.0.1")


@pytest.mark.parametrize("failure", [None, "already in use", "unavailable for binding"])
@pytest.mark.parametrize("legacy", [False, True])
def test_explicit_nats_port_is_exclusive(frame, monkeypatch, failure, legacy):
    monkeypatch.delenv("REMOTE_CONTROL_NATS_PORT", raising=False)
    monkeypatch.delenv("CLAUDECODE_REMOTE_CONTROL_NATS_PORT", raising=False)
    monkeypatch.setenv("CLAUDECODE_REMOTE_CONTROL_NATS_PORT" if legacy else "REMOTE_CONTROL_NATS_PORT", "24622")
    ports, urls = [], []
    class Process:
        def __init__(self, config):
            ports.append(config.port)
        def start(self):
            if failure:
                raise RuntimeError(failure)
        def stop(self):
            pass
    class Transport:
        def __init__(self, **kwargs):
            pass
        def start_threaded(self, url):
            urls.append(url)
        def stop(self):
            pass
    monkeypatch.setattr(main, "NatsServerProcess", Process)
    monkeypatch.setattr(main, "RemoteNatsTransport", Transport)
    monkeypatch.setattr(frame, "_probe_existing_remote_nats_websocket_port", lambda *_: pytest.fail("explicit port reused broker"))
    monkeypatch.setattr(frame, "_ensure_cloudflared_origin_bridge", lambda: None)
    frame._start_remote_nats_server_if_configured(token="fixture", host="127.0.0.1")
    assert ports == [24622]
    assert urls == ([] if failure else ["nats://127.0.0.1:24622"])
    assert frame.remote_nats_runtime_status["enabled"] is (failure is None)
    if failure:
        assert frame.remote_nats_runtime_status["last_error"] == failure
        assert frame.remote_nats_runtime_status["tcp_url"] == "nats://127.0.0.1:24622"


@pytest.mark.parametrize("override", [False, True])
def test_file_storage_root_override_preserves_default(monkeypatch, tmp_path, request, override):
    from file_transfer import DEFAULT_DESKTOP_FILE_STORAGE_DIR
    isolated_root = tmp_path / "isolated-files"
    monkeypatch.delenv("DESKTOP_FILE_STORAGE_DIR", raising=False)
    if override:
        monkeypatch.setenv("DESKTOP_FILE_STORAGE_DIR", str(isolated_root))
    storage_frame = request.getfixturevalue("frame")
    assert storage_frame.file_library.storage_dir == (isolated_root if override else DEFAULT_DESKTOP_FILE_STORAGE_DIR)


@pytest.mark.parametrize("event_type", ["file_offer", "file_progress"])
def test_file_broadcast_outbox_hint_has_direct_file_fields(frame, monkeypatch, event_type):
    import asyncio
    from remote_nats import RemoteNatsTransport
    from remote_nats_protocol import build_file_command_event

    file_body = {"file_id": "marker-id", "name": "marker report (1).txt", "size_bytes": 27,
                 "download_url": "https://fixture:8443/marker%20report%20%281%29.txt?t=synthetic"}
    frame.active_chat_id = "file-owner"
    monkeypatch.setattr(frame, "_ensure_public_file_route_ready", lambda: True)
    frame.file_service = SimpleNamespace(download_url_for=lambda _: file_body["download_url"])
    if event_type == "file_offer":
        payload = frame._file_offer_for_record(SimpleNamespace(
            id=file_body["file_id"], name=file_body["name"], size_bytes=file_body["size_bytes"]))
    else:
        payload = build_file_command_event(request_id="progress-request", event_type=event_type,
            device_id="desktop-fixture", body=file_body, chat_id="file-owner")
    original = json.loads(json.dumps(payload))
    published = []
    class Client:
        async def publish(self, subject, raw):
            published.append((subject, json.loads(raw)))
    transport = RemoteNatsTransport(pair_id="default", token="fixture", jetstream=object(), durable_store=frame.chat_store)
    transport.protocol_version = 2
    transport._nats_client = Client()
    frame._remote_nats_transport = transport
    assert frame._broadcast_remote_event(payload) is True
    rows = frame.chat_store.pending_outbox(pair_id="default", domain="files")
    assert len(rows) == 1
    envelope = json.loads(bytes(rows[0]["payload"]))
    expected_body = dict(file_body, **{key: payload[key] for key in ("request_id", "device_id", "ts")})
    assert envelope["body"] == expected_body
    assert "body" not in envelope["body"]
    assert envelope["chat_id"] == "file-owner"
    assert payload == original
    count, retry, _ = asyncio.run(transport._drain_outbox_lane("other"))
    assert count == 1 and retry is False
    assert published == [(transport.subjects.hints, envelope)]
    assert frame.chat_store.pending_outbox(pair_id="default", domain="files") == []
    frame._remote_nats_transport = None


def test_cloudflared_channels_survive_continuous_rotation(frame, monkeypatch, tmp_path):
    import sys
    import subprocess
    from owned_logs import RotatingLog
    path = tmp_path / "cloudflared.log"
    log = RotatingLog(path, max_bytes=1024, track_channels=True)
    code = ("import sys,time; out=sys.stdout.buffer; "
            "out.write(b'Registered tunnel connection connIndex=0\\n'); "
            "out.write(b'x'*12000+b'\\n'); out.flush(); time.sleep(.3); "
            "out.write(b'Unregistered tunnel connection connIndex=0\\nRegistered tunnel connection connIndex=1\\nLATEST\\n'); out.flush(); time.sleep(5)")
    child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    frame._managed_cloudflared_process = child
    frame._managed_cloudflared_log_handle = log
    monkeypatch.setattr(frame, "_managed_cloudflared_log_path", lambda: path)
    try:
        log.attach(child.stdout)
        import time
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and "LATEST" not in frame._managed_cloudflared_log_tail():
            time.sleep(.02)
        assert "LATEST" in frame._managed_cloudflared_log_tail()
        assert frame._owned_cloudflared_channel_count() == 1
        assert log._channels == {"0": False, "1": True}
        assert sum(p.stat().st_size for p in tmp_path.glob("cloudflared.log*")) <= 4096
    finally:
        frame._stop_managed_cloudflared_process()
    assert not log._thread.is_alive() and log._pipe.closed and log._handle.closed


def test_navigation_diagnostics_default_off_and_bounded_when_enabled(frame, monkeypatch, tmp_path):
    path = tmp_path / "ctrl_navigation_debug.log"
    frame.app_data_dir = tmp_path
    monkeypatch.delenv("MC_CTRL_NAVIGATION_DEBUG", raising=False)
    frame._log_ctrl_navigation_debug("left", reason="fixture")
    assert not path.exists()
    monkeypatch.setenv("MC_CTRL_NAVIGATION_DEBUG", "1")
    frame._log_ctrl_navigation_debug("right", details={"payload": "x" * (6 * 1024 * 1024)})
    assert path.stat().st_size <= 5 * 1024 * 1024
    assert path.with_name(path.name + ".1").exists()
