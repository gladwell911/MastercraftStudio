import json
from pathlib import Path
import subprocess
import sys
import io

import pytest

import browser_open as browser
import codex_client


def test_emit_json_is_safe_on_gbk_stdout(monkeypatch):
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="gbk", errors="strict")
    monkeypatch.setattr(sys, "stdout", stream)
    browser.emit({"status": "error", "message": "metadata \ufffd failed"})
    assert json.loads(raw.getvalue().decode("ascii"))["message"] == "metadata \ufffd failed"


@pytest.mark.parametrize("url", ["file:///a", "javascript:alert(1)", "", "https://", "https://example.com/\n"])
def test_reject_non_http_urls(url):
    with pytest.raises(ValueError):
        browser.validate_url(url)


def test_resource_and_argv_source_frozen_and_spaces(monkeypatch, tmp_path):
    module = tmp_path / "source with spaces" / "browser_open.py"
    monkeypatch.setattr(browser, "__file__", str(module))
    assert browser.browser_open_argv() == [sys.executable, str(module.resolve())]
    assert browser.bundled_skill_root() == module.parent / "assets/codex-skills"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle resources"), raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "install space" / "mc.exe"))
    assert browser.browser_open_argv() == [str(tmp_path / "install space" / "mc_worker.exe"), "--browser-open"]
    assert browser.bundled_skill_root() == tmp_path / "bundle resources" / "assets/codex-skills"


def metadata(profile, extra=()):
    args = ["chrome.exe", "--user-data-dir=" + str(profile), "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=9222", *extra]
    return {"processes": [{"pid": 51, "startTime": "start-1", "command": args}],
            "listeners": [{"pid": 51, "address": "127.0.0.1"}]}


@pytest.mark.parametrize("conflict", ["headless", "port", "address", "multi", "inaccessible", "flags"])
def test_conflicts_stop_without_mutations(tmp_path, conflict):
    snapshot = metadata(tmp_path)
    root = snapshot["processes"][0]
    if conflict == "headless": root["command"].append("--headless=new")
    if conflict == "port": snapshot["listeners"][0]["pid"] = 99
    if conflict == "address": snapshot["listeners"][0]["address"] = "0.0.0.0"
    if conflict == "multi": snapshot["processes"].append(dict(root, pid=52))
    if conflict == "inaccessible": root["command"] = None
    if conflict == "flags": root["command"].remove("--remote-debugging-address=127.0.0.1")
    with pytest.raises(RuntimeError):
        browser.inspect_instance(snapshot, tmp_path, parse=lambda value: value)


def test_root_child_and_port_empty(tmp_path):
    snapshot = metadata(tmp_path)
    snapshot["processes"].append(dict(snapshot["processes"][0], pid=52,
        command=snapshot["processes"][0]["command"] + ["--type=renderer"]))
    assert browser.inspect_instance(snapshot, tmp_path, parse=lambda value: value) == {"pid": 51, "startTime": "start-1"}
    assert browser.inspect_instance({"processes": [], "listeners": []}, tmp_path) is None
    with pytest.raises(RuntimeError, match="occupied"):
        browser.inspect_instance({"processes": [], "listeners": snapshot["listeners"]}, tmp_path)


def test_cdp_preserves_error_and_ignores_events():
    class Socket:
        def send(self, message): self.request = json.loads(message)
        def recv(self):
            return json.dumps({"id": 1, "error": {"code": -32602, "message": "bad target"}})
    transport = Socket()
    with pytest.raises(RuntimeError, match="bad target"):
        browser.CDP(transport).call("Browser.getWindowForTarget", {"targetId": "own"})
    assert transport.request["params"] == {"targetId": "own"}


def test_continuous_unmatched_events_obey_total_deadline(monkeypatch):
    ticks = [0.0]
    def now():
        ticks[0] += .1
        return ticks[0]
    monkeypatch.setattr(browser.time, "monotonic", now)
    class Events:
        def __init__(self): self.receives, self.timeouts = 0, []
        def send(self, _): pass
        def settimeout(self, value): self.timeouts.append(value)
        def recv(self):
            self.receives += 1
            assert self.receives < 10, "continuous events bypassed total timeout"
            return '{"method":"Page.event"}'
    events = Events()
    with pytest.raises(TimeoutError, match="Browser.getVersion"):
        browser.CDP(events).call("Browser.getVersion", timeout=.6)
    assert 0 < events.receives < 10
    assert events.timeouts == sorted(events.timeouts, reverse=True)


@pytest.mark.parametrize("message,retry", [("Browser window not found", True), ("Cannot find context with specified id", True), ("Access denied", False)])
def test_only_specific_transient_cdp_errors_retry_and_preserve_raw(monkeypatch, message, retry):
    ticks = [0.0]
    monkeypatch.setattr(browser.time, "monotonic", lambda: ticks[0])
    monkeypatch.setattr(browser.time, "sleep", lambda delay: ticks.__setitem__(0, ticks[0] + delay))
    method = "Runtime.evaluate" if "context" in message else "Browser.getWindowForTarget"
    original = browser.CDPError(method, {"code": -32000, "message": message, "data": "original detail"})
    class Failure:
        calls = 0
        def call(self, *_):
            self.calls += 1
            raise original
    connection = Failure()
    with pytest.raises(browser.CDPError) as failure:
        browser.ready_call(connection, method, {}, .5)
    assert failure.value is original
    assert "original detail" in str(failure.value)
    assert (connection.calls > 1) == retry


def test_startup_waits_for_listener_without_relaunch(tmp_path, monkeypatch):
    snapshots = [metadata(tmp_path) for _ in range(3)]
    snapshots[0] = {"processes": [], "listeners": []}
    snapshots[1]["listeners"] = []
    for value in snapshots[1:]:
        value["processes"][0]["command"] = subprocess.list2cmdline(value["processes"][0]["command"])
    sequence = iter(snapshots)
    monkeypatch.setattr(browser, "process_snapshot", lambda: next(sequence))
    monkeypatch.setattr(browser, "find_chrome", lambda: tmp_path / "chrome.exe")
    monkeypatch.setattr(browser.time, "sleep", lambda _: None)
    launches = []
    class Process:
        pid = 51
        def poll(self): return None
    monkeypatch.setattr(browser.subprocess, "Popen", lambda argv: launches.append(argv) or Process())
    assert browser.ensure_instance(tmp_path, 2) == {"pid": 51, "startTime": "start-1"}
    assert len(launches) == 1
    assert "--no-first-run" in launches[0]


@pytest.mark.parametrize("stage", ["metadata", "connect"])
def test_main_preserves_continuation_id_on_early_failure(monkeypatch, capsys, stage):
    if stage == "metadata":
        monkeypatch.setattr(browser, "process_snapshot", lambda: (_ for _ in ()).throw(RuntimeError("metadata denied")))
    else:
        monkeypatch.setattr(browser, "process_snapshot", lambda: {})
        monkeypatch.setattr(browser, "inspect_instance", lambda *_: {"pid": 51, "startTime": "start-1"})
        monkeypatch.setattr(browser, "guard", lambda *_: None)
        monkeypatch.setattr(browser, "http_json", lambda _: {"webSocketDebuggerUrl": "ws://127.0.0.1:9222/browser/own"})
        monkeypatch.setattr(browser, "connect", lambda _: (_ for _ in ()).throw(RuntimeError("connect denied")))
    assert browser.main(["--url", "https://example.com/", "--target-id", "known"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["targetId"] == "known" and result["status"] == "error"


@pytest.mark.parametrize("failure", [None, "focus", "ambiguous", "network", "moved", "hwnd_changed", "receipt",
    "window_delayed", "context_delayed", "hwnd_delayed", "guard_pid_create", "guard_start_create", "guard_pid_activate", "guard_start_activate",
    "continue_extra", "continue_moved"])
def test_create_and_continue_only_owned_target(tmp_path, monkeypatch, failure, capsys):
    identity = {"pid": 51, "startTime": "start-1"}
    monkeypatch.setattr(browser, "automation_profile", lambda: tmp_path)
    monkeypatch.setattr(browser, "ensure_instance", lambda *_: identity)
    monkeypatch.setattr(browser, "inspect_instance", lambda *_: identity)
    monkeypatch.setattr(browser, "process_snapshot", lambda: {})
    monkeypatch.setattr(browser, "guard", lambda *_: None)
    if failure and failure.startswith("guard_"):
        # Use real inspect_instance/guard, only replace the OS snapshot source.
        monkeypatch.undo()
        monkeypatch.setattr(browser, "automation_profile", lambda: tmp_path)
        monkeypatch.setattr(browser, "ensure_instance", lambda *_: identity)
        snapshots = [0]
        def snapshot():
            snapshots[0] += 1
            value = metadata(tmp_path)
            value["processes"][0]["command"] = subprocess.list2cmdline(value["processes"][0]["command"])
            threshold = 2 if failure.endswith("create") else 4
            if snapshots[0] >= threshold:
                if "_pid_" in failure:
                    value["processes"][0]["pid"] = 52
                    value["listeners"][0]["pid"] = 52
                else:
                    value["processes"][0]["startTime"] = "start-2"
            return value
        monkeypatch.setattr(browser, "process_snapshot", snapshot)
    if failure == "receipt":
        monkeypatch.setattr(browser, "save_receipt", lambda *_: (_ for _ in ()).throw(OSError("disk full")))
    count = [0]
    def windows(_):
        count[0] += 1
        if count[0] == 1 or (failure == "hwnd_delayed" and count[0] == 2): return {100}
        if failure == "hwnd_changed" and count[0] > 2: return {100, 300}
        return {100, 200, 300} if failure == "ambiguous" or (failure in ("continue_extra", "continue_moved") and count[0] > 3) else {100, 200}
    monkeypatch.setattr(browser, "root_windows", windows)
    activations = []
    def state(hwnd, pid, activate=False):
        if activate: activations.append(hwnd)
        return {"hwnd": hwnd, "pid": pid, "visible": True, "minimized": False, "foreground": failure != "focus"}
    monkeypatch.setattr(browser, "window_state", state)
    requests = []
    class Connection:
        def close(self): pass
        def call(self, method, params=None):
            requests.append((method, params))
            if method == "Target.getTargets": return {"targetInfos": [{"targetId": "old"}]}
            if method == "Target.createTarget": return {"targetId": "own"}
            if method == "Browser.getWindowForTarget":
                calls = sum(1 for name, _ in requests if name == method)
                if failure == "window_delayed" and calls == 1:
                    raise browser.CDPError(method, {"code": -32000, "message": "Browser window not found"})
                return {"windowId": 22 if (failure == "moved" and calls > 1) or (failure == "continue_moved" and calls > 2) else 21}
            if method == "Runtime.evaluate":
                if failure == "context_delayed" and sum(1 for name, _ in requests if name == method) == 1:
                    raise browser.CDPError(method, {"code": -32000, "message": "Execution context was destroyed."})
                dom = {"url": "https://redirect.example/", "title": "Page", "readyState": "complete",
                       "documentURI": "chrome-error://chromewebdata/" if failure == "network" else "https://redirect.example/"}
                return {"result": {"value": json.dumps(dom)}}
            return {}
    monkeypatch.setattr(browser, "connect", lambda _: Connection())
    monkeypatch.setattr(browser, "http_json", lambda path: {"webSocketDebuggerUrl": "browser"} if path == "/json/version" else [{"id": "own", "webSocketDebuggerUrl": "page"}])
    result = browser.open_page("https://example.com/")
    output = capsys.readouterr().out
    if failure and failure.endswith("create"):
        assert result["status"] == "error"
        assert not any(method == "Target.createTarget" for method, _ in requests)
        assert activations == []
        return
    assert json.loads(output)["targetId"] == "own"
    assert result["targetId"] == "own"
    assert result["status"] == ("ok" if failure in (None, "window_delayed", "context_delayed", "hwnd_delayed", "continue_extra", "continue_moved") else "error")
    if failure in ("moved", "hwnd_changed", "guard_pid_activate", "guard_start_activate"):
        assert activations == []
        assert not any(method == "Page.bringToFront" for method, _ in requests)
    if failure == "receipt": assert result["continuationAvailable"] is False
    creates = [params for method, params in requests if method == "Target.createTarget"]
    assert creates == [{"url": "https://example.com/", "newWindow": True, "background": False}]
    assert not any(method in ("Target.closeTarget", "Page.navigate") for method, _ in requests)
    if failure in ("continue_extra", "continue_moved"):
        activations.clear()
        result = browser.open_page("https://example.com/", "own")
        assert result["status"] == ("ok" if failure == "continue_extra" else "error")
        assert activations == ([200] if failure == "continue_extra" else [])
        assert len([1 for method, _ in requests if method == "Target.createTarget"]) == 1
    if failure is None:
        assert result["preexistingTargetsPreserved"]
        assert browser.open_page("https://example.com/", "own")["status"] == "ok"
        assert len([1 for method, _ in requests if method == "Target.createTarget"]) == 1
        assert browser.open_page("https://example.com/", "unknown")["status"] == "error"


def test_launcher_help_does_not_start_worker():
    root = Path(browser.__file__).parent
    result = subprocess.run([sys.executable, str(root / "worker_launcher.py"), "--browser-open", "--help"], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert "--target-id" in result.stdout and '"ready"' not in result.stdout


@pytest.mark.parametrize("policy", [None, "never", "on-request"])
@pytest.mark.parametrize("resume", [False, True])
def test_native_json_rpc_omitted_and_explicit_manual(monkeypatch, policy, resume):
    client = codex_client.CodexAppServerClient()
    messages = []
    monkeypatch.setattr(client, "_request_clearing_context_usage", lambda method, params: messages.append((method, params)))
    kwargs = {} if policy is None else {"approval_policy": policy}
    (client.resume_thread if resume else client.start_thread)("thread-or-cwd", **kwargs)
    params = messages[0][1]
    assert params["approvalPolicy"] == (policy or "on-request")
    assert params["sandbox"] == ("danger-full-access" if policy else "workspace-write")
    assert params["approvalsReviewer"] == ("user" if policy else "auto_review")


def test_launch_env_both_home_paths_and_roots_dedup(tmp_path, monkeypatch):
    client = codex_client.CodexAppServerClient()
    monkeypatch.setattr(codex_client, "build_codex_app_server_env", lambda: ({}, tmp_path))
    for _ in range(2):
        assert json.loads(client._build_launch_env()["MC_BROWSER_OPEN_ARGV"]) == browser.browser_open_argv()
    agents = tmp_path / ".agents/skills"
    agents.mkdir(parents=True)
    monkeypatch.setattr(codex_client, "_user_home", lambda: tmp_path)
    monkeypatch.setattr(codex_client, "bundled_skill_root", lambda: agents)
    monkeypatch.setattr(codex_client, "_link_user_codex_directories", lambda *_: None)
    monkeypatch.setattr(client, "_ensure_started", lambda: None)
    calls = []
    monkeypatch.setattr(client, "request", lambda method, params: calls.append((method, params)))
    client.prepare_task(str(tmp_path))
    assert calls[0] == ("skills/extraRoots/set", {"extraRoots": [str(agents.resolve())]})
    assert calls[1][1]["forceReload"] is True
