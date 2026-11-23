import asyncio
import json
import main
import pytest
from types import SimpleNamespace


def test_remote_nats_defaults_to_fixed_domain_when_host_is_unset(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_TOKEN", "secret")
    monkeypatch.delenv("REMOTE_CONTROL_HOST", raising=False)
    monkeypatch.delenv("REMOTE_CONTROL_DOMAIN", raising=False)
    monkeypatch.delenv("REMOTE_CONTROL_PORT", raising=False)

    url = frame._build_remote_nats_url()

    assert url == "wss://rc.tingyou.cc/nats?token=secret"


def test_remote_nats_url_strips_invalid_port_zero_from_domain(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_TOKEN", "secret")
    monkeypatch.setenv("REMOTE_CONTROL_DOMAIN", "https://rc.tingyou.cc:0/nats#")

    frame._initialize_remote_control_settings()

    url = frame._build_remote_nats_url()

    assert frame.remote_control_domain == "wss://rc.tingyou.cc/nats"
    assert url == "wss://rc.tingyou.cc/nats?token=secret"


def test_remote_control_settings_normalize_dirty_domain(frame):
    frame.remote_control_domain = "https://rc.tingyou.cc:0/nats#"

    frame._initialize_remote_control_settings()

    assert frame.remote_control_domain == "wss://rc.tingyou.cc/nats"


def test_fixed_domain_mode_forces_public_host_and_stable_port(frame):
    frame.remote_control_domain = "rc.tingyou.cc"
    frame.remote_control_host = "127.0.0.1"
    frame.remote_control_port = 0

    frame._initialize_remote_control_settings()

    assert frame.remote_control_domain == "wss://rc.tingyou.cc/nats"
    assert frame.remote_control_host == "0.0.0.0"
    assert frame.remote_control_port == 18080


def test_fixed_domain_nats_runtime_uses_public_runtime_and_status(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_TOKEN", "secret")
    monkeypatch.setenv("REMOTE_CONTROL_DOMAIN", "rc.tingyou.cc")
    monkeypatch.setenv("REMOTE_CONTROL_HOST", "127.0.0.1")
    monkeypatch.setenv("REMOTE_CONTROL_PORT", "0")

    class _FakeNatsProcess:
        def __init__(self, config, bundled_dir=None):
            self.config = config

        def start(self, timeout=10):
            return SimpleNamespace()

        def stop(self):
            return None

    class _FakeNatsTransport:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start_threaded(self, url, timeout=10):
            return None

        def stop(self):
            return None

    monkeypatch.setattr(main, "NatsServerProcess", _FakeNatsProcess)
    monkeypatch.setattr(main, "RemoteNatsTransport", _FakeNatsTransport)
    monkeypatch.setattr(frame, "_ensure_cloudflared_origin_bridge", lambda: None)
    statuses = []
    monkeypatch.setattr(frame, "SetStatusText", lambda text: statuses.append(text))

    frame._start_remote_nats_runtime_if_configured()

    assert statuses
    assert "ws://127.0.0.1:18080/nats" in statuses[-1]
    assert "wss://rc.tingyou.cc/nats?token=secret" in statuses[-1]


def test_remote_nats_startup_runs_fixed_domain_connectivity_check(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_TOKEN", "secret")
    monkeypatch.setenv("REMOTE_CONTROL_DOMAIN", "rc.tingyou.cc")

    class _FakeNatsProcess:
        def __init__(self, config, bundled_dir=None):
            self.config = config

        def start(self, timeout=10):
            return SimpleNamespace()

        def stop(self):
            return None

    class _FakeNatsTransport:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start_threaded(self, url, timeout=10):
            return None

        def stop(self):
            return None

    seen = {}
    monkeypatch.setattr(main, "NatsServerProcess", _FakeNatsProcess)
    monkeypatch.setattr(main, "RemoteNatsTransport", _FakeNatsTransport)
    monkeypatch.setattr(frame, "_ensure_cloudflared_origin_bridge", lambda: None)
    monkeypatch.setattr(frame, "SetStatusText", lambda _text: None)
    monkeypatch.setattr(
        frame,
        "_ensure_remote_nats_startup_connectivity",
        lambda **kwargs: seen.update(kwargs),
    )

    frame._start_remote_nats_runtime_if_configured(ensure_connectivity=True)

    assert seen == {
        "token": "secret",
        "published_url": "wss://rc.tingyou.cc/nats?token=secret",
    }


def test_remote_nats_startup_raises_when_runtime_did_not_start(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_TOKEN", "secret")
    monkeypatch.setenv("REMOTE_CONTROL_DOMAIN", "rc.tingyou.cc")
    monkeypatch.setattr(frame, "SetStatusText", lambda _text: None)

    def _fake_start_remote_servers(*, token, host, port):
        frame._set_remote_nats_runtime_status(
            enabled=False,
            tcp_url="nats://127.0.0.1:4222",
            websocket_url="ws://127.0.0.1:18080/nats",
            cloudflared_url="wss://rc.tingyou.cc/nats",
            last_error="Could not find nats-server.exe",
        )
        frame._remote_nats_process = None
        frame._remote_nats_transport = None

    monkeypatch.setattr(frame, "_start_remote_servers", _fake_start_remote_servers)

    with pytest.raises(RuntimeError, match="Could not find nats-server\\.exe"):
        frame._start_remote_nats_runtime_if_configured(ensure_connectivity=True)

    assert frame.remote_nats_runtime_status["enabled"] is False
    assert frame.remote_control_runtime_status["public_ws_ready"] is False


def test_fixed_domain_remote_runtime_url_stays_empty_until_connectivity_is_verified(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_TOKEN", "secret")
    monkeypatch.setenv("REMOTE_CONTROL_DOMAIN", "rc.tingyou.cc")

    class _FakeNatsProcess:
        def __init__(self, config, bundled_dir=None):
            self.config = config

        def start(self, timeout=10):
            return SimpleNamespace()

        def stop(self):
            return None

    class _FakeNatsTransport:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start_threaded(self, url, timeout=10):
            return None

        def stop(self):
            return None

    monkeypatch.setattr(main, "NatsServerProcess", _FakeNatsProcess)
    monkeypatch.setattr(main, "RemoteNatsTransport", _FakeNatsTransport)
    monkeypatch.setattr(frame, "_ensure_cloudflared_origin_bridge", lambda: None)
    monkeypatch.setattr(frame, "SetStatusText", lambda _text: None)
    monkeypatch.setattr(
        frame,
        "_ensure_remote_nats_startup_connectivity",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("public probe failed")),
    )

    with pytest.raises(RuntimeError):
        frame._start_remote_nats_runtime_if_configured(ensure_connectivity=True)

    assert frame.remote_control_runtime_url == ""
    assert frame.remote_control_runtime_status["public_ws_ready"] is False


def test_remote_nats_autostart_worker_runs_connectivity_checked_runtime_start(frame, monkeypatch):
    frame.remote_control_autostart = True
    seen = {}

    class _ImmediateThread:
        def __init__(self, target=None, daemon=None, args=(), kwargs=None):
            self._target = target

        def start(self):
            if self._target is not None:
                self._target()

    monkeypatch.setattr(main.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(
        frame,
        "_start_remote_nats_runtime_if_configured",
        lambda **kwargs: seen.setdefault("kwargs", kwargs),
    )
    monkeypatch.setattr(frame, "SetStatusText", lambda _text: None)

    frame._schedule_remote_nats_autostart()

    assert seen["kwargs"] == {"ensure_connectivity": True}


def test_fixed_domain_remote_reenables_autostart_after_stale_disabled_state(frame, monkeypatch):
    monkeypatch.delenv("REMOTE_CONTROL_AUTOSTART", raising=False)
    monkeypatch.delenv("CLAUDECODE_REMOTE_CONTROL_AUTOSTART", raising=False)
    frame.remote_control_autostart = False
    frame.remote_control_domain = "wss://rc.tingyou.cc/nats"
    saves = {"count": 0}
    monkeypatch.setattr(frame, "_save_state", lambda: saves.__setitem__("count", saves["count"] + 1))

    frame._initialize_remote_control_settings()

    assert frame.remote_control_autostart is True
    assert saves["count"] == 1


def test_remote_startup_connectivity_restarts_cloudflared_after_public_probe_failure(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_CLOUDFLARED_OWNER_MODE", "service")
    frame.remote_control_host = "0.0.0.0"
    frame.remote_control_runtime_bind = "ws://127.0.0.1:18080/nats"
    frame._remote_nats_transport = object()
    monkeypatch.setattr(
        frame,
        "_remote_runtime_config",
        lambda: {"fixed_domain_mode": True, "port": 18080},
    )
    monkeypatch.setattr(frame, "_remote_local_listener_ready", lambda _port: True)
    monkeypatch.setattr(frame, "_verify_remote_local_health", lambda _token, _port: (True, ""))
    monkeypatch.setattr(frame, "_start_cloudflared_origin_proxy", lambda: True)
    monkeypatch.setattr(
        frame,
        "_query_cloudflared_service",
        lambda: {"exists": True, "running": True, "detail": "", "binary_path": "cloudflared.exe"},
    )
    monkeypatch.setattr(frame, "_ensure_cloudflared_service_url", lambda _port: (True, True))
    restarted = {"calls": 0}
    monkeypatch.setattr(
        frame,
        "_restart_cloudflared_service",
        lambda: restarted.__setitem__("calls", restarted["calls"] + 1) or True,
    )
    retried = []
    monkeypatch.setattr(
        frame,
        "_verify_remote_public_ws_with_retries",
        lambda url: retried.append(url) or (True, ""),
    )
    statuses = []
    monkeypatch.setattr(frame, "SetStatusText", lambda text: statuses.append(text))

    frame._ensure_remote_nats_startup_connectivity(
        token="secret",
        published_url="wss://rc.tingyou.cc/nats?token=secret",
    )

    assert restarted["calls"] == 1
    assert retried == ["wss://rc.tingyou.cc/nats?token=secret"]
    assert statuses
    assert "cloudflared" in statuses[-1]
    assert "rc.tingyou.cc" in statuses[-1]


def test_remote_startup_connectivity_uses_managed_cloudflared_when_service_is_missing(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_CLOUDFLARED_OWNER_MODE", "service")
    frame.remote_control_host = "0.0.0.0"
    frame.remote_control_port = 18080
    frame.remote_control_runtime_bind = "ws://127.0.0.1:18081/nats"
    frame._remote_nats_transport = object()
    frame._remote_nats_websocket_port = 18081
    frame.remote_nats_runtime_status = {
        "enabled": True,
        "websocket_url": "ws://127.0.0.1:18081/nats",
        "cloudflared_url": "wss://rc.tingyou.cc/nats",
    }
    monkeypatch.setattr(
        frame,
        "_remote_runtime_config",
        lambda: {"fixed_domain_mode": True, "port": 18080},
    )
    monkeypatch.setattr(frame, "_remote_local_listener_ready", lambda _port: True)
    monkeypatch.setattr(frame, "_verify_remote_local_health", lambda _token, _port: (True, ""))
    monkeypatch.setattr(frame, "_start_cloudflared_origin_proxy", lambda: True)
    monkeypatch.setattr(
        frame,
        "_query_cloudflared_service",
        lambda: {"exists": False, "running": False, "binary_path": "", "detail": ""},
    )
    started = []
    monkeypatch.setattr(
        frame,
        "_start_managed_cloudflared_process",
        lambda websocket_port: started.append(websocket_port) or True,
        raising=False,
    )
    monkeypatch.setattr(frame, "_verify_managed_cloudflared_process", lambda _port: (True, ""))
    monkeypatch.setattr(frame, "_verify_remote_public_ws", lambda _url: (True, ""))
    statuses = []
    monkeypatch.setattr(frame, "SetStatusText", lambda text: statuses.append(text))

    frame._ensure_remote_nats_startup_connectivity(
        token="secret",
        published_url="wss://rc.tingyou.cc/nats?token=secret",
    )

    assert started == [18080]
    assert statuses
    assert "cloudflared" in statuses[-1]
    assert "rc.tingyou.cc" in statuses[-1]


def test_remote_startup_connectivity_targets_cloudflared_origin_proxy(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_CLOUDFLARED_OWNER_MODE", "service")
    frame.remote_control_host = "0.0.0.0"
    frame.remote_control_port = 18080
    frame.remote_control_runtime_bind = "ws://127.0.0.1:18081/nats"
    frame._remote_nats_transport = object()
    frame._remote_nats_websocket_port = 18081
    frame.remote_nats_runtime_status = {
        "enabled": True,
        "websocket_url": "ws://127.0.0.1:18081/nats",
        "cloudflared_url": "wss://rc.tingyou.cc/nats",
    }
    monkeypatch.setattr(frame, "_query_cloudflared_service", lambda: {"exists": True, "running": True})
    configured_ports = []
    started_proxy = []
    monkeypatch.setattr(
        frame,
        "_remote_runtime_config",
        lambda: {"fixed_domain_mode": True, "port": 18080},
    )
    monkeypatch.setattr(frame, "_remote_local_listener_ready", lambda port: port == 18081)
    monkeypatch.setattr(frame, "_verify_remote_local_health", lambda _token, port: (port == 18081, ""))
    monkeypatch.setattr(frame, "_start_cloudflared_origin_proxy", lambda: started_proxy.append(True) or True)
    monkeypatch.setattr(frame, "_ensure_cloudflared_service_url", lambda port: configured_ports.append(port) or (True, False))
    monkeypatch.setattr(frame, "_start_cloudflared_service", lambda: True)
    monkeypatch.setattr(frame, "_verify_remote_public_ws", lambda _url: (True, ""))

    frame._ensure_remote_nats_startup_connectivity(
        token="secret",
        published_url="wss://rc.tingyou.cc/nats?token=secret",
    )

    assert started_proxy == [True]
    assert configured_ports == [18080]


def test_remote_startup_connectivity_refuses_duplicate_connector_after_reconfigure_failure(frame, monkeypatch):
    frame.remote_control_host = "0.0.0.0"
    frame.remote_control_port = 18080
    frame.remote_control_runtime_bind = "ws://127.0.0.1:18081/nats"
    frame._remote_nats_transport = object()
    frame._remote_nats_websocket_port = 18081
    frame.remote_nats_runtime_status = {
        "enabled": True,
        "websocket_url": "ws://127.0.0.1:18081/nats",
        "cloudflared_url": "wss://rc.tingyou.cc/nats",
    }
    replaced_ports = []
    monkeypatch.setattr(frame, "_remote_runtime_config", lambda: {"fixed_domain_mode": True, "port": 18080})
    monkeypatch.setattr(frame, "_remote_local_listener_ready", lambda port: port == 18081)
    monkeypatch.setattr(frame, "_verify_remote_local_health", lambda _token, port: (port == 18081, ""))
    monkeypatch.setattr(frame, "_start_cloudflared_origin_proxy", lambda: True)
    monkeypatch.setattr(frame, "_cloudflared_origin_port", lambda: 19080)
    monkeypatch.setattr(
        frame,
        "_query_cloudflared_service",
        lambda: {"exists": True, "running": True, "detail": "", "binary_path": "cloudflared.exe"},
    )
    monkeypatch.setattr(frame, "_ensure_cloudflared_service_url", lambda _port: (False, True))
    monkeypatch.setattr(frame, "_replace_stale_cloudflared_with_managed", lambda port: replaced_ports.append(port) or True)
    monkeypatch.setattr(frame, "_restart_cloudflared_service", lambda: (_ for _ in ()).throw(AssertionError("must not restart stale service")))
    monkeypatch.setattr(frame, "_verify_remote_public_ws_with_retries", lambda _url: (True, ""))

    with pytest.raises(RuntimeError, match="cloudflared"):
        frame._ensure_remote_nats_startup_connectivity(
            token="secret",
            published_url="wss://rc.tingyou.cc/nats?token=secret",
        )

    assert replaced_ports == []


def test_remote_startup_connectivity_fails_before_public_probe_when_service_is_stale(frame, monkeypatch):
    frame.remote_control_host = "0.0.0.0"
    frame.remote_control_port = 18080
    frame.remote_control_runtime_bind = "ws://127.0.0.1:18081/nats"
    frame._remote_nats_transport = object()
    frame._remote_nats_websocket_port = 18081
    frame.remote_nats_runtime_status = {
        "enabled": True,
        "websocket_url": "ws://127.0.0.1:18081/nats",
        "cloudflared_url": "wss://rc.tingyou.cc/nats",
    }
    monkeypatch.setattr(frame, "_remote_runtime_config", lambda: {"fixed_domain_mode": True, "port": 18080})
    monkeypatch.setattr(frame, "_remote_local_listener_ready", lambda port: port == 18081)
    monkeypatch.setattr(frame, "_verify_remote_local_health", lambda _token, port: (port == 18081, ""))
    monkeypatch.setattr(frame, "_start_cloudflared_origin_proxy", lambda: True)
    monkeypatch.setattr(frame, "_cloudflared_origin_port", lambda: 19080)
    monkeypatch.setattr(
        frame,
        "_query_cloudflared_service",
        lambda: {"exists": True, "running": True, "detail": "", "binary_path": "cloudflared.exe"},
    )
    monkeypatch.setattr(frame, "_ensure_cloudflared_service_url", lambda _port: (False, True))
    monkeypatch.setattr(frame, "_replace_stale_cloudflared_with_managed", lambda _port: True)
    public_probe = []
    monkeypatch.setattr(frame, "_verify_remote_public_ws_with_retries", lambda url: public_probe.append(url) or (False, "公网仍不可达"))
    monkeypatch.setattr(frame, "_restart_cloudflared_service", lambda: (_ for _ in ()).throw(AssertionError("must not restart stale service")))

    with pytest.raises(RuntimeError, match="cloudflared"):
        frame._ensure_remote_nats_startup_connectivity(
            token="secret",
            published_url="wss://rc.tingyou.cc/nats?token=secret",
        )

    assert public_probe == []


def test_public_file_route_uses_available_cloudflared_origin_port(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_CLOUDFLARED_OWNER_MODE", "service")
    frame.remote_control_port = 18080
    frame.remote_nats_runtime_status = {
        "enabled": True,
        "websocket_url": "ws://127.0.0.1:18081/nats",
        "cloudflared_url": "wss://rc.tingyou.cc/nats",
    }
    frame.file_service.set_public_base_url("https://rc.tingyou.cc")
    started_ports = []
    configured_ports = []
    commands = []

    monkeypatch.setattr(
        frame,
        "_run_remote_check_command",
        lambda args, **_kwargs: commands.append(tuple(args)) or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    monkeypatch.setattr(frame, "_start_file_service_if_configured", lambda: True)
    monkeypatch.setattr(frame, "_can_bind_loopback_tcp_port", lambda port: port == 19080)

    class _FakeProxy:
        def __init__(self, *, listen_host, listen_port, nats_ws_url, file_base_url):
            self.listen_host = listen_host
            self.listen_port = listen_port
            self.nats_ws_url = nats_ws_url
            self.file_base_url = file_base_url
            started_ports.append(listen_port)

        def start(self):
            return None

        def stop(self):
            return None

    monkeypatch.setattr(main, "CloudflaredOriginProxy", _FakeProxy)
    monkeypatch.setattr(
        frame,
        "_query_cloudflared_service",
        lambda: {"exists": True, "running": True, "binary_path": "cloudflared --url http://127.0.0.1:4200", "detail": ""},
    )
    monkeypatch.setattr(frame, "_ensure_cloudflared_service_url", lambda port: configured_ports.append(port) or (True, False))
    monkeypatch.setattr(frame, "_start_cloudflared_service", lambda: True)

    assert frame._ensure_public_file_route_ready() is True
    assert started_ports == [19080]
    assert configured_ports == [19080]
    assert any("add" in command and "v6tov4" in command and "connectport=19080" in command for command in commands)


def test_remote_startup_uses_reconfigured_service_without_duplicate_managed_connector(frame, monkeypatch):
    frame.remote_control_runtime_bind = "ws://127.0.0.1:18082/nats"
    frame.remote_nats_runtime_status = {
        "websocket_url": "ws://127.0.0.1:18082/nats",
        "cloudflared_url": "wss://rc.tingyou.cc/nats",
    }
    frame._remote_nats_transport = object()
    monkeypatch.setattr(frame, "_remote_local_listener_ready", lambda _port: True)
    monkeypatch.setattr(frame, "_verify_remote_local_health", lambda _token, _port: (True, ""))
    monkeypatch.setattr(frame, "_start_cloudflared_origin_proxy", lambda: True)
    monkeypatch.setattr(frame, "_cloudflared_origin_port", lambda: 19080)
    monkeypatch.setattr(
        frame,
        "_query_cloudflared_service",
        lambda: {"exists": True, "running": True, "binary_path": "cloudflared --url http://127.0.0.1:4200", "detail": ""},
    )
    monkeypatch.setattr(frame, "_ensure_cloudflared_service_url", lambda _port: (True, True))
    restarted = []
    monkeypatch.setattr(frame, "_restart_cloudflared_service", lambda: restarted.append(True) or True)
    monkeypatch.setattr(
        frame,
        "_start_managed_cloudflared_process",
        lambda _port: (_ for _ in ()).throw(AssertionError("must not add a duplicate connector")),
    )
    monkeypatch.setattr(frame, "_verify_remote_public_ws_with_retries", lambda _url: (True, ""))

    monkeypatch.setattr(frame, "_remote_runtime_config", lambda: {"fixed_domain_mode": True})
    frame._ensure_remote_nats_startup_connectivity(
        token="token", published_url="wss://rc.tingyou.cc/nats"
    )

    assert restarted == [True]


def test_managed_cloudflared_verification_checks_its_own_pid_command_line(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_CLOUDFLARED_OWNER_MODE", "service")
    frame._managed_cloudflared_process = SimpleNamespace(pid=1234, poll=lambda: None)
    frame._cloudflared_origin_proxy_port = 19080
    monkeypatch.setattr(frame, "_query_process_command_line", lambda pid: "cloudflared tunnel --url http://127.0.0.1:4200")
    monkeypatch.setattr(frame, "_remote_local_listener_ready", lambda _port: True)

    ok, detail = frame._verify_managed_cloudflared_process(19080)

    assert not ok
    assert "19080" in detail
    assert frame._cloudflared_origin_port() == 19080


def test_public_file_route_refuses_duplicate_connector_when_stale_service_cannot_be_updated(frame, monkeypatch):
    frame.remote_control_port = 18080
    frame.remote_nats_runtime_status = {
        "enabled": True,
        "websocket_url": "ws://127.0.0.1:18081/nats",
        "cloudflared_url": "wss://rc.tingyou.cc/nats",
    }
    frame.file_service.set_public_base_url("https://rc.tingyou.cc")
    killed = []
    managed_ports = []

    monkeypatch.setattr(frame, "_start_file_service_if_configured", lambda: True)
    monkeypatch.setattr(frame, "_can_bind_loopback_tcp_port", lambda port: port == 19080)

    class _FakeProxy:
        def __init__(self, *, listen_host, listen_port, nats_ws_url, file_base_url):
            self.listen_host = listen_host
            self.listen_port = listen_port

        def start(self):
            return None

        def stop(self):
            return None

    monkeypatch.setattr(main, "CloudflaredOriginProxy", _FakeProxy)
    monkeypatch.setattr(
        frame,
        "_query_cloudflared_service",
        lambda: {"exists": True, "running": True, "binary_path": "cloudflared --url http://127.0.0.1:4200", "detail": ""},
    )
    monkeypatch.setattr(frame, "_ensure_cloudflared_service_url", lambda _port: (False, True))
    monkeypatch.setattr(frame, "_restart_cloudflared_service", lambda: (_ for _ in ()).throw(AssertionError("must not restart stale service")))
    monkeypatch.setattr(frame, "_kill_cloudflared_processes", lambda: killed.append(True))
    monkeypatch.setattr(frame, "_start_managed_cloudflared_process", lambda port: managed_ports.append(port) or True)

    assert frame._ensure_public_file_route_ready() is False
    assert killed == []
    assert managed_ports == []


def test_remote_startup_connectivity_waits_for_managed_cloudflared_process_public_probe(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_CLOUDFLARED_OWNER_MODE", "service")
    frame.remote_control_host = "0.0.0.0"
    frame.remote_control_runtime_bind = "ws://127.0.0.1:18080/nats"
    frame._remote_nats_transport = object()
    monkeypatch.setattr(
        frame,
        "_remote_runtime_config",
        lambda: {"fixed_domain_mode": True, "port": 18080},
    )
    monkeypatch.setattr(frame, "_remote_local_listener_ready", lambda _port: True)
    monkeypatch.setattr(frame, "_verify_remote_local_health", lambda _token, _port: (True, ""))
    monkeypatch.setattr(frame, "_start_cloudflared_origin_proxy", lambda: True)
    monkeypatch.setattr(
        frame,
        "_query_cloudflared_service",
        lambda: {"exists": False, "running": False, "binary_path": "", "detail": ""},
    )
    monkeypatch.setattr(frame, "_start_managed_cloudflared_process", lambda _port: True, raising=False)
    monkeypatch.setattr(frame, "_verify_managed_cloudflared_process", lambda _port: (True, ""))
    monkeypatch.setattr(
        frame,
        "_restart_cloudflared_service",
        lambda: (_ for _ in ()).throw(AssertionError("should wait for managed process, not restart service")),
    )
    probe_results = iter([(False, "公网隧道握手失败：502"), (True, "")])
    monkeypatch.setattr(frame, "_verify_remote_public_ws", lambda _url: next(probe_results))
    monkeypatch.setattr(main.time, "sleep", lambda _seconds: None)
    statuses = []
    monkeypatch.setattr(frame, "SetStatusText", lambda text: statuses.append(text))

    frame._ensure_remote_nats_startup_connectivity(
        token="secret",
        published_url="wss://rc.tingyou.cc/nats?token=secret",
    )

    assert statuses
    assert "cloudflared" in statuses[-1]
    assert "rc.tingyou.cc" in statuses[-1]


def test_verify_remote_public_ws_requires_authenticated_nats_pong(frame, monkeypatch):
    class _FakeWs:
        def __init__(self, responses):
            self.responses = iter(responses)
            self.sent = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def receive(self, timeout=None):
            return next(self.responses)

        async def send_str(self, payload):
            self.sent.append(payload)

    class _FakeSession:
        def __init__(self, *args, **kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def ws_connect(self, url, heartbeat=None):
            self.ws = _FakeWs(
                [
                    SimpleNamespace(type=main.WSMsgType.BINARY, data=b'INFO {"server_id":"abc"} \r\n'),
                    SimpleNamespace(type=main.WSMsgType.TEXT, data="PONG\r\n"),
                ]
            )
            return self.ws

    session = _FakeSession()
    monkeypatch.setattr(main, "ClientSession", lambda *args, **kwargs: session)

    ok, detail = asyncio.run(frame._verify_remote_public_ws_async("ws://127.0.0.1:18080/nats?token=secret"))

    assert ok is True
    assert detail == ""
    assert session.ws.sent == ['CONNECT {"auth_token":"secret","verbose":false}\r\nPING\r\n']


def test_verify_remote_public_ws_parses_batched_lines_and_answers_server_ping(frame, monkeypatch):
    class _FakeWs:
        def __init__(self):
            self.responses = iter(
                [
                    SimpleNamespace(type=main.WSMsgType.TEXT, data="INFO {}\r\n"),
                    SimpleNamespace(type=main.WSMsgType.TEXT, data="+OK\r\nPING\r\nPONG\r\n"),
                ]
            )
            self.sent = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def receive(self, timeout=None):
            return next(self.responses)

        async def send_str(self, payload):
            self.sent.append(payload)

    class _FakeSession:
        def __init__(self):
            self.ws = _FakeWs()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def ws_connect(self, url, heartbeat=None):
            return self.ws

    session = _FakeSession()
    monkeypatch.setattr(main, "ClientSession", lambda *args, **kwargs: session)

    ok, detail = asyncio.run(frame._verify_remote_public_ws_async("ws://127.0.0.1:18080/nats?token=secret"))

    assert (ok, detail) == (True, "")
    assert session.ws.sent == [
        'CONNECT {"auth_token":"secret","verbose":false}\r\nPING\r\n',
        "PONG\r\n",
    ]


def test_verify_remote_public_ws_rejects_nats_auth_error(frame, monkeypatch):
    class _FakeWs:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def receive(self, timeout=None):
            return next(self.responses)

        async def send_str(self, payload):
            return None

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def ws_connect(self, url, heartbeat=None):
            ws = _FakeWs()
            ws.responses = iter(
                [
                    SimpleNamespace(type=main.WSMsgType.TEXT, data="INFO {}\r\n"),
                    SimpleNamespace(type=main.WSMsgType.TEXT, data="-ERR 'Authorization Violation'\r\n"),
                ]
            )
            return ws

    monkeypatch.setattr(main, "ClientSession", lambda *args, **kwargs: _FakeSession())

    ok, detail = asyncio.run(frame._verify_remote_public_ws_async("ws://127.0.0.1:18080/nats?token=bad"))

    assert ok is False
    assert "认证失败" in detail


def test_verify_remote_public_ws_rejects_non_info_nats_greeting(frame, monkeypatch):
    class _FakeWs:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def receive(self, timeout=None):
            return SimpleNamespace(type=main.WSMsgType.TEXT, data="-ERR 'Server unavailable'\r\n")

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def ws_connect(self, url, heartbeat=None):
            return _FakeWs()

    monkeypatch.setattr(main, "ClientSession", lambda *args, **kwargs: _FakeSession())

    ok, detail = asyncio.run(frame._verify_remote_public_ws_async("ws://127.0.0.1:18080/nats?token=secret"))

    assert ok is False
    assert "响应异常" in detail


def test_restart_cloudflared_service_does_not_kill_unrelated_connectors_when_stop_sticks(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_CLOUDFLARED_OWNER_MODE", "service")
    monkeypatch.setattr(
        frame,
        "_query_cloudflared_service",
        lambda: {"exists": True, "running": True, "detail": "running"},
    )
    commands = []
    monkeypatch.setattr(
        frame,
        "_run_remote_check_command",
        lambda args, timeout=10.0: commands.append(tuple(args)) or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(main, "REMOTE_CONTROL_HEALTH_TIMEOUT_SECONDS", 0)

    assert frame._restart_cloudflared_service() is False
    assert ("sc.exe", "stop", "cloudflared") in commands
    assert ("taskkill", "/F", "/IM", "cloudflared.exe") not in commands


def test_cloudflared_service_rewrites_stale_url(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_CLOUDFLARED_OWNER_MODE", "service")
    qc_output = (
        "SERVICE_NAME: cloudflared\r\n"
        "        TYPE              : 10  WIN32_OWN_PROCESS\r\n"
        '        BINARY_PATH_NAME  : "C:\\Program Files\\cloudflared\\cloudflared.exe" tunnel run --url http://127.0.0.1:10080 --token dummy\r\n'
        "        START_TYPE        : 2   AUTO_START\r\n"
        "        STATE             : 4  RUNNING"
    )
    commands = []

    configured_bin_path = {"value": None}

    def fake_run(args, timeout=10.0):
        commands.append(tuple(args))
        if args[:3] == ["sc.exe", "qc", "cloudflared"]:
            output = qc_output if configured_bin_path["value"] is None else qc_output.replace(
                "--url http://127.0.0.1:10080", configured_bin_path["value"]
            )
            return SimpleNamespace(returncode=0, stdout=output, stderr="")
        if args[:3] == ["sc.exe", "config", "cloudflared"]:
            configured_bin_path["value"] = args[4].split(" --token", 1)[0].split("tunnel run ", 1)[1]
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(frame, "_run_remote_check_command", fake_run)

    configured, changed = frame._ensure_cloudflared_service_url(18080)

    assert configured is True
    assert changed is True
    assert any(
        cmd[:4] == ("sc.exe", "config", "cloudflared", "binPath=") and "--url http://127.0.0.1:18080" in cmd[4]
        for cmd in commands
    )


def test_cloudflared_service_rewrites_equals_style_stale_url(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_CLOUDFLARED_OWNER_MODE", "service")
    state = {
        "exists": True,
        "running": True,
        "detail": "",
        "binary_path": 'cloudflared.exe tunnel run --url=http://127.0.0.1:4200 --token dummy',
    }
    configured_paths = []
    monkeypatch.setattr(frame, "_query_cloudflared_service", lambda: state)

    def set_path(path):
        configured_paths.append(path)
        state["binary_path"] = path
        return True

    monkeypatch.setattr(frame, "_set_cloudflared_service_bin_path", set_path)

    configured, changed = frame._ensure_cloudflared_service_url(19082)

    assert (configured, changed) == (True, True)
    assert "--url=http://127.0.0.1:19082" in configured_paths[0]


def test_remote_startup_reconfigures_stale_running_cloudflared_service(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_CLOUDFLARED_OWNER_MODE", "service")
    frame.remote_control_host = "0.0.0.0"
    frame.remote_control_port = 18080
    frame.remote_control_runtime_bind = "ws://127.0.0.1:18082/nats"
    frame._remote_nats_transport = object()
    frame.remote_nats_runtime_status = {"websocket_url": "ws://127.0.0.1:18082/nats"}
    monkeypatch.setattr(frame, "_remote_runtime_config", lambda: {"fixed_domain_mode": True, "port": 18080})
    monkeypatch.setattr(frame, "_remote_local_listener_ready", lambda port: port == 18082)
    monkeypatch.setattr(frame, "_verify_remote_local_health", lambda _token, _port: (True, ""))
    monkeypatch.setattr(frame, "_start_cloudflared_origin_proxy", lambda: True)
    monkeypatch.setattr(frame, "_cloudflared_origin_port", lambda: 19082)
    monkeypatch.setattr(
        frame,
        "_query_cloudflared_service",
        lambda: {"exists": True, "running": True, "detail": "", "binary_path": "cloudflared.exe"},
    )
    monkeypatch.setattr(frame, "_ensure_cloudflared_service_url", lambda _port: (True, True))
    restarted = []
    monkeypatch.setattr(frame, "_restart_cloudflared_service", lambda: restarted.append(True) or True)
    monkeypatch.setattr(frame, "_cloudflared_running_process_targets_origin", lambda _port: False)
    replaced = []
    monkeypatch.setattr(frame, "_replace_stale_cloudflared_with_managed", lambda port: replaced.append(port) or True)
    monkeypatch.setattr(frame, "_verify_remote_public_ws_with_retries", lambda _url: (True, ""))

    frame._ensure_remote_nats_startup_connectivity(token="secret", published_url="wss://rc.tingyou.cc/nats?token=secret")

    assert restarted == [True]
    assert replaced == []


def test_cloudflared_process_can_satisfy_origin_when_service_is_missing(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_CLOUDFLARED_OWNER_MODE", "service")
    monkeypatch.setattr(
        frame,
        "_query_cloudflared_service",
        lambda: {"exists": False, "running": False, "detail": "", "binary_path": ""},
    )
    monkeypatch.setattr(
        frame,
        "_query_cloudflared_process_command_lines",
        lambda: [
            '"C:\\Program Files (x86)\\cloudflared\\cloudflared.exe" tunnel run --url http://127.0.0.1:18080 --token dummy'
        ],
    )

    configured, changed = frame._ensure_cloudflared_service_url(18080)

    assert configured is True
    assert changed is False


def test_cloudflared_origin_bridge_repoints_portproxy_to_origin_proxy(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_CLOUDFLARED_OWNER_MODE", "service")
    frame.remote_control_port = 18080
    frame._remote_nats_websocket_port = 18081
    frame._cloudflared_origin_proxy_port = 19080
    commands = []

    monkeypatch.setattr(
        frame,
        "_run_remote_check_command",
        lambda args, timeout=10.0: commands.append(tuple(args)) or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    frame._ensure_cloudflared_origin_bridge()

    assert (
        "netsh",
        "interface",
        "portproxy",
        "delete",
        "v4tov4",
        "listenport=18080",
        "listenaddress=127.0.0.1",
    ) in commands
    assert (
        "netsh",
        "interface",
        "portproxy",
        "delete",
        "v6tov4",
        "listenport=18080",
        "listenaddress=::1",
    ) in commands
    assert (
        "netsh",
        "interface",
        "portproxy",
        "add",
        "v4tov4",
        "listenport=18080",
        "listenaddress=127.0.0.1",
        "connectport=19080",
        "connectaddress=127.0.0.1",
    ) in commands
    assert (
        "netsh",
        "interface",
        "portproxy",
        "add",
        "v6tov4",
        "listenport=18080",
        "listenaddress=::1",
        "connectport=19080",
        "connectaddress=127.0.0.1",
    ) in commands


def test_cloudflared_origin_port_prefers_existing_portproxy_target(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_CLOUDFLARED_OWNER_MODE", "service")
    frame.remote_control_port = 18080

    def fake_run(args, timeout=10.0):
        assert args == ["netsh", "interface", "portproxy", "show", "all"]
        return SimpleNamespace(
            returncode=0,
            stdout=(
                "\n"
                "Listen on ipv4:             Connect to ipv4:\n"
                "\n"
                "Address         Port        Address         Port\n"
                "--------------- ----------  --------------- ----------\n"
                "127.0.0.1       18080       127.0.0.1       19080\n"
                "\n"
                "Listen on ipv6:             Connect to ipv4:\n"
                "\n"
                "Address         Port        Address         Port\n"
                "--------------- ----------  --------------- ----------\n"
                "::1             18080       127.0.0.1       19080\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(frame, "_run_remote_check_command", fake_run)
    monkeypatch.setattr(frame, "_can_bind_loopback_tcp_port", lambda port: port == 19080)

    assert frame._cloudflared_origin_bridge_target_ports() == [19080, 19080]
    assert frame._resolve_cloudflared_origin_port() == 19080


def test_cloudflared_origin_port_keeps_current_proxy_when_portproxy_points_to_it(frame, monkeypatch):
    monkeypatch.setenv("REMOTE_CONTROL_CLOUDFLARED_OWNER_MODE", "service")
    frame.remote_control_port = 18080
    frame._cloudflared_origin_proxy = SimpleNamespace(listen_port=19081)

    monkeypatch.setattr(
        frame,
        "_run_remote_check_command",
        lambda args, timeout=10.0: SimpleNamespace(
            returncode=0,
            stdout="127.0.0.1       18080       127.0.0.1       19081\n",
            stderr="",
        ),
    )
    monkeypatch.setattr(frame, "_can_bind_loopback_tcp_port", lambda _port: False)

    assert frame._resolve_cloudflared_origin_port() == 19081


def test_remote_nats_runtime_autostarts_without_env_and_uses_default_token(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "resolve_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(main.ChatFrame, "_legacy_state_paths", lambda self: [self.state_path])
    monkeypatch.setattr(main.ChatFrame, "_migrate_legacy_state_if_needed", lambda self: None)
    monkeypatch.setattr(main.ChatFrame, "_ensure_remote_nats_startup_connectivity", lambda self, **_kwargs: None)
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setenv("REMOTE_CONTROL_AUTOSTART", "1")
    monkeypatch.setenv("REMOTE_CONTROL_PORT", "0")
    class _FakeNatsProcess:
        def __init__(self, config, bundled_dir=None):
            self.config = config

        def start(self, timeout=10):
            return SimpleNamespace()

        def stop(self):
            return None

    class _FakeNatsTransport:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start_threaded(self, url, timeout=10):
            return None

        def stop(self):
            return None

    monkeypatch.setattr(main, "NatsServerProcess", _FakeNatsProcess)
    monkeypatch.setattr(main, "RemoteNatsTransport", _FakeNatsTransport)
    monkeypatch.setattr(main.ChatFrame, "_ensure_cloudflared_origin_bridge", lambda self: None)
    monkeypatch.setattr(main.ChatFrame, "_can_bind_loopback_tcp_port", lambda self, _port: True)
    monkeypatch.setattr(main.ChatFrame, "_resolve_remote_nats_websocket_port", lambda self, _port: 18080)

    frame = main.ChatFrame()
    try:
        assert frame.remote_control_token == main.DEFAULT_REMOTE_CONTROL_TOKEN
        assert frame._remote_nats_transport is not None
        assert frame.remote_nats_runtime_status["websocket_url"] == "ws://127.0.0.1:18080/nats"
    finally:
        frame.Destroy()


def test_remote_nats_runtime_falls_back_when_default_port_is_excluded(frame, monkeypatch):
    monkeypatch.setattr(main, "REMOTE_NATS_PORT_FALLBACKS", (4622,))
    monkeypatch.setattr(frame, "_resolve_remote_nats_websocket_port", lambda _port: 18081)
    monkeypatch.setattr(frame, "_ensure_cloudflared_origin_bridge", lambda: None)
    monkeypatch.setattr(frame, "SetStatusText", lambda _text: None)
    attempts = []
    transport_urls = []

    class _FakeNatsProcess:
        def __init__(self, config, bundled_dir=None):
            self.config = config
            attempts.append(config.port)

        def start(self, timeout=10):
            if self.config.port == 4222:
                raise RuntimeError(
                    "listen tcp 0.0.0.0:4222: bind: An attempt was made to access a socket in a way forbidden by its access permissions."
                )
            return SimpleNamespace()

        def stop(self):
            return None

    class _FakeNatsTransport:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start_threaded(self, url, timeout=10):
            transport_urls.append(url)

        def stop(self):
            return None

    monkeypatch.setattr(main, "NatsServerProcess", _FakeNatsProcess)
    monkeypatch.setattr(main, "RemoteNatsTransport", _FakeNatsTransport)

    frame._start_remote_nats_server_if_configured(token="secret", host="0.0.0.0")

    assert attempts == [4222, 4622]
    assert transport_urls == ["nats://127.0.0.1:4622"]
    assert frame.remote_nats_runtime_status["enabled"] is True
    assert frame.remote_nats_runtime_status["tcp_url"] == "nats://127.0.0.1:4622"


def test_on_close_stops_remote_servers(frame, monkeypatch, wait_for_wx):
    called = []
    monkeypatch.setattr(frame, "_stop_remote_servers", lambda: called.append("stop"))
    monkeypatch.setattr(frame._voice_input, "cancel", lambda: None)
    monkeypatch.setattr(frame._realtime_call, "shutdown", lambda: None)
    monkeypatch.setattr(frame.notes_sync, "close", lambda: None)
    monkeypatch.setattr(frame, "_save_state", lambda: None)
    monkeypatch.setattr(frame._global_ctrl_hook, "stop", lambda: None)
    monkeypatch.setattr(frame, "_unregister_global_hotkey", lambda: None)

    frame.Close()
    wait_for_wx(lambda: getattr(frame, "_close_complete", False), "remote-close")
    assert called == ["stop"]


def test_managed_cloudflared_starts_supplied_token_file_command_and_releases_lease(frame, monkeypatch, tmp_path):
    import base64
    import io
    from tunnel_policy import ConnectorLease, split_command_line
    token_file = tmp_path / "cloudflared-laptop" / "token.txt"
    token_file.parent.mkdir()
    token_file.write_text(base64.b64encode(json.dumps({"a": "fixture", "t":
        "3ac4c76d-ded4-438b-92a8-8d5c2aff037c", "s": "private-test-secret"}).encode()).decode(), encoding="utf-8")
    command = main.subprocess.list2cmdline(["cloudflared.exe", "tunnel", "run", "--token-file",
        token_file.as_posix(), "--url", "http://127.0.0.1:4200"])
    monkeypatch.setenv("REMOTE_CONTROL_CLOUDFLARED_COMMAND", command)
    monkeypatch.setenv("REMOTE_CONTROL_CLOUDFLARED_PROTOCOL", "http2")
    monkeypatch.setattr(main, "resolve_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(frame, "_read_pair_id", lambda: "laptop")
    monkeypatch.setattr(frame, "_managed_cloudflared_log_path", lambda: tmp_path / "connector.log")
    monkeypatch.setattr(frame, "_query_cloudflared_service", lambda: {"exists": False})
    calls, statuses = [], []
    process = SimpleNamespace(pid=4242, stdout=io.BytesIO(b""), poll=lambda: None, terminate=lambda: calls.append("terminate"),
        wait=lambda timeout: None)
    monkeypatch.setattr(main.subprocess, "Popen", lambda argv, **kwargs: calls.append(argv) or process)
    monkeypatch.setattr(frame, "_set_status_text_safe", statuses.append)
    assert frame._start_managed_cloudflared_process(18080)
    argv = split_command_line(calls[0])
    assert argv[argv.index("--token-file") + 1] == token_file.as_posix()
    assert argv[-2:] == ["--url", "http://127.0.0.1:18080"]
    assert argv[argv.index("--protocol") + 1] == "http2"
    competing = ConnectorLease(tmp_path / "cloudflared" / "owners", "laptop", command)
    with pytest.raises(OSError):
        competing.acquire()
    frame._stop_managed_cloudflared_process()
    competing.acquire()
    competing.close()
    token_file.unlink()
    assert frame._start_managed_cloudflared_process(18080) is False
    assert len([value for value in calls if value != "terminate"]) == 1
    assert all("private-test-secret" not in value and str(token_file) not in value for value in statuses)


def test_remote_ws_autostart_is_scheduled_off_startup_path(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "resolve_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(main.ChatFrame, "_legacy_state_paths", lambda self: [self.state_path])
    monkeypatch.setattr(main.ChatFrame, "_migrate_legacy_state_if_needed", lambda self: None)
    monkeypatch.setattr(main.ChatFrame, "_refresh_openclaw_sync_lifecycle", lambda self, force_replay=False: None)
    monkeypatch.setattr(main.ChatFrame, "_start_claudecode_remote_nats_runtime_if_configured", lambda self: None)
    monkeypatch.setattr(main.wx, "CallAfter", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setenv("REMOTE_CONTROL_AUTOSTART", "1")

    scheduled = []
    started = []
    monitors = []

    class _NoRunThread:
        def __init__(self, target=None, daemon=None, args=(), kwargs=None):
            self._target = target
            scheduled.append(target)

        def start(self):
            return None

    monkeypatch.setattr(main.threading, "Thread", _NoRunThread)
    monkeypatch.setattr(
        main.ChatFrame,
        "_start_remote_nats_runtime_if_configured",
        lambda self, *, ensure_connectivity: started.append(ensure_connectivity),
    )

    monkeypatch.setattr(main.ChatFrame, "_start_remote_health_monitor", lambda self: monitors.append(self))
    frame = main.ChatFrame()
    try:
        assert started == []
        assert monitors == []
        scheduler = main.ChatFrame._schedule_remote_nats_autostart
        worker_name = f"{scheduler.__qualname__}.<locals>._worker"
        workers = [target for target in scheduled if getattr(target, "__qualname__", "") == worker_name]
        assert len(workers) == 1
        workers[0]()
        assert started == [True]
        assert monitors == [frame]
    finally:
        frame.Destroy()
