from types import SimpleNamespace

import main


def test_can_bind_loopback_tcp_port_returns_false_when_port_accepts_connections(frame, monkeypatch):
    class _ConnectedSocket:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(main.socket, "create_connection", lambda *args, **kwargs: _ConnectedSocket())

    assert frame._can_bind_loopback_tcp_port(4222) is False


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
    monkeypatch.setattr(
        frame,
        "_probe_existing_remote_nats_websocket_port",
        lambda: 18080,
        raising=False,
    )

    frame._start_remote_nats_server_if_configured(token="secret", host="127.0.0.1")

    assert started["transport_url"] == "nats://127.0.0.1:4222"
    assert frame._remote_nats_process is None
    assert frame._remote_nats_transport is not None
    assert frame.remote_nats_runtime_status["enabled"] is True
    assert frame.remote_nats_runtime_status["last_error"] == ""


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
        lambda: 18081,
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
        lambda: None,
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
    monkeypatch.setattr(frame, "_probe_existing_remote_nats_websocket_port", lambda: 18080, raising=False)
    monkeypatch.setattr(frame, "_ensure_cloudflared_origin_bridge", lambda: started.setdefault("bridge", True))
    monkeypatch.setattr(
        frame,
        "_can_bind_loopback_tcp_port",
        lambda port: port not in {4222, 18080},
    )

    frame._start_remote_nats_server_if_configured(token="secret", host="127.0.0.1")

    assert started["transport_urls"] == [
        "nats://127.0.0.1:4222",
        "nats://127.0.0.1:4223",
    ]
    assert started["started_port"] == 4223
    assert started["ports"] == [
        (4222, 18081),
        (4223, 18081),
    ]
    assert frame._remote_nats_process is not None
    assert frame._remote_nats_transport is not None
    assert frame.remote_nats_runtime_status["enabled"] is True
    assert frame.remote_nats_runtime_status["tcp_url"] == "nats://127.0.0.1:4223"
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


def test_remote_nats_callbacks_are_ui_marshaled(frame, monkeypatch):
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
    monkeypatch.setattr(frame.new_chat_button, "Enable", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_set_input_hint_idle", lambda: None)
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame, "_refresh_history", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_refresh_answer_list_preserving_selection", lambda: None)
    monkeypatch.setattr(frame, "_call_later_if_alive", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_play_finish_sound", lambda: None)
    monkeypatch.setattr(frame, "_refresh_context_usage_after_done", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(frame, "_is_ui_alive", lambda: False)
    monkeypatch.setattr(frame, "SetStatusText", lambda _text: None)
    monkeypatch.setattr(frame, "_push_remote_state", lambda chat_id: pushed.append(("state", chat_id)))
    monkeypatch.setattr(
        frame,
        "_push_remote_final_answer",
        lambda chat_id, text: pushed.append(("final_answer", chat_id, text)),
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
    assert pushed == [
        ("state", "chat-e2e"),
        ("final_answer", "chat-e2e", "desktop received: hello from emulator"),
        ("history", "chat-e2e"),
    ]
