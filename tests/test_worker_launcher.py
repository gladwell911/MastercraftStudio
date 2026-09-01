import worker_launcher


class _Stream:
    def __init__(self):
        self.calls = []

    def reconfigure(self, **kwargs):
        self.calls.append(kwargs)


def test_worker_launcher_forces_utf8_for_piped_stdio(monkeypatch):
    stdin = _Stream()
    stdout = _Stream()
    stderr = _Stream()
    monkeypatch.setattr(worker_launcher.sys, "stdin", stdin)
    monkeypatch.setattr(worker_launcher.sys, "stdout", stdout)
    monkeypatch.setattr(worker_launcher.sys, "stderr", stderr)

    worker_launcher.configure_utf8_stdio()

    assert stdin.calls == [{"encoding": "utf-8", "errors": "strict"}]
    assert stdout.calls == [{"encoding": "utf-8", "errors": "strict"}]
    assert stderr.calls == [{"encoding": "utf-8", "errors": "replace"}]
