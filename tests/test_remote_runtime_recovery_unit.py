from __future__ import annotations

import threading

import pytest


def test_cloudflared_same_port_origin_preserves_ipv6_alias_without_ipv4_loop(frame, monkeypatch):
    frame.remote_control_port = 18080
    frame._cloudflared_origin_proxy_port = 18080
    commands = []
    monkeypatch.setattr(frame, "_run_remote_check_command", lambda args, **_: commands.append(args))
    frame._ensure_cloudflared_origin_bridge()
    added = [args for args in commands if "add" in args]
    assert added == [[
        "netsh", "interface", "portproxy", "add", "v6tov4",
        "listenport=18080", "listenaddress=::1", "connectport=18080", "connectaddress=127.0.0.1",
    ]]
    assert len([args for args in commands if "delete" in args]) == 2


class _ProbeClock:
    def __init__(self, cycles):
        self.cycles = cycles
        self.now = 0
        self.stopped = False

    def wait(self, seconds):
        self.now += seconds
        self.cycles -= 1
        return self.stopped or self.cycles < 0

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True


@pytest.mark.parametrize(
    "responses,cycles,local_ok,restarts,ready",
    [([True], 3, True, 0, True),
     ([False, True], 2, True, 0, True),
     ([False, False, True], 2, True, 1, True),
     ([False], 6, True, 2, False),
     ([False], 3, False, 0, False)],
)
def test_remote_health_recovery_matrix(frame, monkeypatch, responses, cycles, local_ok, restarts, ready):
    clock = _ProbeClock(cycles)
    frame._remote_nats_transport = object()
    frame.remote_nats_runtime_status = {"websocket_url": "ws://127.0.0.1:19080/nats"}
    monkeypatch.setattr(frame, "_remote_runtime_config", lambda: {"fixed_domain_mode": True, "published_base": "wss://fixture/nats"})
    monkeypatch.setattr(frame, "_read_remote_control_token", lambda: "fixture-token")
    monkeypatch.setattr(frame, "_verify_remote_local_health", lambda *_: (local_ok, "local failure"))
    monkeypatch.setattr(frame, "_remote_local_listener_ready", lambda *_: local_ok)
    monkeypatch.setattr(frame, "_query_cloudflared_service", lambda: {"exists": True})
    monkeypatch.setattr("main.time.monotonic", lambda: clock.now)
    probe_results = iter(responses)
    probes = []
    restart_times = []
    notifications = []
    def probe(url):
        probes.append(url)
        return next(probe_results, responses[-1]), "public failure fixture-token"
    monkeypatch.setattr(frame, "_verify_remote_public_ws", probe)
    monkeypatch.setattr(frame, "_restart_cloudflared_service", lambda **_: restart_times.append(clock.now) or True)
    monkeypatch.setattr(frame, "_call_after_if_alive", lambda callback: notifications.append(callback))
    frame._remote_health_worker(clock, frame._remote_health_generation)
    assert len(restart_times) == restarts
    if len(restart_times) > 1:
        assert restart_times[1] - restart_times[0] >= 120
    assert frame.remote_control_runtime_status["public_ws_ready"] is ready
    assert "fixture-token" not in frame.remote_control_runtime_status["last_remote_error"]
    assert len(notifications) <= 2
    if not local_ok:
        assert not probes
    frame._remote_nats_transport = None


@pytest.mark.parametrize("closing", [False, True])
def test_remote_health_discards_probe_after_stop_or_close(frame, monkeypatch, closing):
    clock = _ProbeClock(3)
    frame._remote_nats_transport = object()
    frame.remote_nats_runtime_status = {"websocket_url": "ws://127.0.0.1:19080/nats"}
    monkeypatch.setattr(frame, "_remote_runtime_config", lambda: {"fixed_domain_mode": True, "published_base": "wss://fixture/nats"})
    monkeypatch.setattr(frame, "_read_remote_control_token", lambda: "fixture")
    monkeypatch.setattr(frame, "_verify_remote_local_health", lambda *_: (True, ""))
    monkeypatch.setattr(frame, "_remote_local_listener_ready", lambda *_: True)
    def probe(_):
        if closing:
            frame._closing = True
        else:
            frame._remote_health_generation += 1
        return False, "late failure"
    monkeypatch.setattr(frame, "_verify_remote_public_ws", probe)
    monkeypatch.setattr(frame, "_restart_cloudflared_service", lambda: pytest.fail("late restart"))
    monkeypatch.setattr(frame, "_set_remote_runtime_status", lambda **_: pytest.fail("late status"))
    frame._remote_health_worker(clock, frame._remote_health_generation)
    frame._remote_nats_transport = None


def test_remote_stop_does_not_wait_for_blocked_service_recovery(frame, monkeypatch):
    stop = _ProbeClock(2)
    frame._remote_health_stop = stop
    frame._remote_nats_transport = object()
    frame.remote_nats_runtime_status = {"websocket_url": "ws://127.0.0.1:19080/nats"}
    monkeypatch.setattr(frame, "_remote_runtime_config", lambda: {"fixed_domain_mode": True, "published_base": "wss://fixture/nats"})
    monkeypatch.setattr(frame, "_read_remote_control_token", lambda: "fixture")
    monkeypatch.setattr(frame, "_verify_remote_local_health", lambda *_: (True, ""))
    monkeypatch.setattr(frame, "_remote_local_listener_ready", lambda *_: True)
    monkeypatch.setattr(frame, "_verify_remote_public_ws", lambda *_: (False, "fixture failure"))
    monkeypatch.setattr(frame, "_query_cloudflared_service", lambda: {"exists": True})
    monkeypatch.setattr(frame, "_call_after_if_alive", lambda *_: None)
    monkeypatch.setattr(frame, "_stop_managed_cloudflared_process", lambda: None)
    monkeypatch.setattr(frame, "_stop_cloudflared_origin_proxy", lambda: None)
    monkeypatch.setattr(frame, "_set_remote_nats_runtime_status", lambda **_: None)
    entered = threading.Event()
    release = threading.Event()
    stopped = threading.Event()
    def restart(**_kwargs):
        entered.set()
        release.wait(5)
        return True
    monkeypatch.setattr(frame, "_restart_cloudflared_service", restart)
    worker = threading.Thread(target=frame._remote_health_worker, args=(stop, frame._remote_health_generation), daemon=True)
    closer = threading.Thread(target=lambda: (frame._stop_remote_servers(), stopped.set()), daemon=True)
    try:
        worker.start()
        assert entered.wait(2)
        status_after_failure = dict(frame.remote_control_runtime_status)
        closer.start()
        assert stopped.wait(1), "shutdown waited for blocked service I/O"
        assert worker.is_alive()
    finally:
        release.set()
        worker.join(2)
        if closer.ident is not None:
            closer.join(2)
    assert not worker.is_alive()
    assert frame.remote_control_runtime_status == status_after_failure


@pytest.mark.parametrize("fixed,service,expected", [(True, True, 1), (False, True, 0), (True, False, 0)])
def test_remote_health_only_starts_one_service_worker(frame, monkeypatch, fixed, service, expected):
    workers = []
    class Worker:
        def __init__(self, **kwargs):
            workers.append(kwargs)
        def start(self):
            pass
        def is_alive(self):
            return True
    frame._remote_nats_transport = object()
    monkeypatch.setattr(frame, "_remote_runtime_config", lambda: {"fixed_domain_mode": fixed})
    monkeypatch.setattr(frame, "_query_cloudflared_service", lambda: {"exists": service})
    monkeypatch.setattr("main.threading.Thread", Worker)
    frame._start_remote_health_monitor()
    frame._start_remote_health_monitor()
    assert len(workers) == expected
    frame._remote_nats_transport = None


def test_remote_service_recovery_does_not_start_after_shutdown(frame, monkeypatch):
    clock = _ProbeClock(0)
    commands = []
    monkeypatch.setattr(frame, "_query_cloudflared_service", lambda: {"exists": True, "running": True})
    def command(args, **_kwargs):
        commands.append(args)
        clock.set()
    monkeypatch.setattr(frame, "_run_remote_check_command", command)
    monkeypatch.setattr(frame, "_start_cloudflared_service", lambda: pytest.fail("service started after stop"))
    assert frame._restart_cloudflared_service(stop_event=clock) is False
    assert commands == [["sc.exe", "stop", "cloudflared"]]


def test_remote_service_recovery_cancels_during_start_helper_query(frame, monkeypatch):
    clock = _ProbeClock(0)
    commands = []
    queries = 0
    def query():
        nonlocal queries
        queries += 1
        if queries == 4:
            clock.set()
        return {"exists": True, "running": queries == 1}
    monkeypatch.setattr(frame, "_query_cloudflared_service", query)
    monkeypatch.setattr(frame, "_run_remote_check_command", lambda args, **_: commands.append(args))
    assert frame._restart_cloudflared_service(stop_event=clock) is False
    assert queries == 4
    assert commands == [["sc.exe", "stop", "cloudflared"]]


def test_remote_service_start_cancels_before_retry(frame, monkeypatch):
    clock = _ProbeClock(0)
    commands = []
    monkeypatch.setattr(frame, "_query_cloudflared_service", lambda: {"exists": True, "running": False})
    def command(args, **_kwargs):
        commands.append(args)
        clock.set()
        return None
    monkeypatch.setattr(frame, "_run_remote_check_command", command)
    monkeypatch.setattr("main.time.sleep", lambda _: None)
    assert frame._start_cloudflared_service(stop_event=clock) is False
    assert commands == [["sc.exe", "start", "cloudflared"]]


def test_remote_monitor_is_scheduled_even_when_startup_public_check_fails(frame, monkeypatch):
    workers = []
    monitor_calls = []
    class Worker:
        def __init__(self, *, target, **_kwargs):
            workers.append(target)
        def start(self):
            pass
    frame.remote_control_autostart = True
    monkeypatch.setattr("main.threading.Thread", Worker)
    def startup(**_kwargs):
        raise RuntimeError("fixture public failure")
    monkeypatch.setattr(frame, "_start_remote_nats_runtime_if_configured", startup)
    monkeypatch.setattr("main.wx_call_after_if_alive", lambda *_: None)
    monkeypatch.setattr(frame, "_start_remote_health_monitor", lambda: monitor_calls.append(True))
    frame._schedule_remote_nats_autostart()
    workers[0]()
    assert monitor_calls == [True]


class _ImmediateExitProcess:
    returncode = 7

    def poll(self):
        return self.returncode


class _LiveProcess:
    returncode = None

    def __init__(self) -> None:
        self.terminated = False

    def poll(self):
        return None if not self.terminated else 0

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout=None):
        return 0


def test_managed_cloudflared_immediate_exit_is_failure_with_redacted_diagnostic(
    frame,
    monkeypatch,
) -> None:
    synthetic_token = "synthetic-cloudflared-token"
    statuses: list[str] = []

    def fake_popen(_command, **kwargs):
        kwargs["stdout"].write(
            f"connector rejected --token {synthetic_token}\nuseful connector diagnostic\n".encode()
        )
        return _ImmediateExitProcess()

    monkeypatch.setattr(frame, "_managed_cloudflared_command_line", lambda _port: "cloudflared fake")
    monkeypatch.setattr(frame, "_read_remote_control_token", lambda: synthetic_token)
    monkeypatch.setattr(frame, "_set_status_text_safe", statuses.append)
    monkeypatch.setattr("main.subprocess.Popen", fake_popen)

    assert frame._start_managed_cloudflared_process(19080) is False
    assert frame._managed_cloudflared_process is None
    assert frame._managed_cloudflared_log_handle is None
    assert statuses
    assert "exit_code=7" in statuses[-1]
    assert str(frame._managed_cloudflared_log_path()) in statuses[-1]
    assert "useful connector diagnostic" in statuses[-1]
    assert synthetic_token not in statuses[-1]
    assert "<redacted>" in statuses[-1]


def test_managed_cloudflared_live_process_is_retained_and_log_closed_on_stop(
    frame,
    monkeypatch,
) -> None:
    live = _LiveProcess()
    monkeypatch.setattr(frame, "_managed_cloudflared_command_line", lambda _port: "cloudflared fake")
    monkeypatch.setattr("main.subprocess.Popen", lambda *_args, **_kwargs: live)

    assert frame._start_managed_cloudflared_process(19080) is True
    assert frame._managed_cloudflared_process is live
    assert frame._managed_cloudflared_log_handle is not None

    frame._stop_managed_cloudflared_process()

    assert live.terminated is True
    assert frame._managed_cloudflared_process is None
    assert frame._managed_cloudflared_log_handle is None


def test_managed_cloudflared_diagnostic_retains_bounded_log_tail(frame, monkeypatch) -> None:
    synthetic_token = "synthetic-tail-token"
    log_path = frame._managed_cloudflared_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "old line\n" * 30 + f"failure token={synthetic_token}\nlast useful line\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(frame, "_read_remote_control_token", lambda: synthetic_token)

    detail = frame._managed_cloudflared_diagnostic()

    assert "last useful line" in detail
    assert "old line" in detail
    assert synthetic_token not in detail
    assert "<redacted>" in detail
    assert detail.count("old line") <= 18
