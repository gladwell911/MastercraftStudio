import json
from types import SimpleNamespace

import main


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
