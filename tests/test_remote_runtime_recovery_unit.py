from __future__ import annotations


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
