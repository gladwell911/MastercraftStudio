"""Open one explicitly requested HTTP(S) page in an owned visible Chrome window."""
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit
import urllib.request

import websocket


def resource_root():
    return Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


def bundled_skill_root():
    return resource_root() / "assets" / "codex-skills"


def browser_open_argv():
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).resolve().with_name("mc_worker.exe")), "--browser-open"]
    return [sys.executable, str(Path(__file__).resolve())]


def validate_url(url):
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname or any(ord(c) < 32 for c in url):
        raise ValueError("Only a nonempty HTTP(S) URL is supported")
    return url


def automation_profile():
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise RuntimeError("LOCALAPPDATA is unavailable")
    return Path(local) / "ChromeAutomationProfile"


def find_chrome():
    import winreg
    candidates = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe", 0, winreg.KEY_READ | view) as key:
                    candidates.append(Path(winreg.QueryValueEx(key, None)[0].strip('"')))
            except OSError:
                pass
    for name in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
        if os.environ.get(name):
            candidates.append(Path(os.environ[name]) / "Google/Chrome/Application/chrome.exe")
    for path in candidates:
        if path.is_file():
            return path
    raise RuntimeError("Installed Google Chrome was not found")


def command_argv(command):
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    count = ctypes.c_int()
    result = shell.CommandLineToArgvW(command, ctypes.byref(count))
    if not result:
        raise RuntimeError("Cannot parse Chrome command line")
    try:
        return [result[i] for i in range(count.value)]
    finally:
        kernel.LocalFree(result)


def process_snapshot():
    script = r"""$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$processes=@(Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" | ForEach-Object {
  @{pid=[int]$_.ProcessId;parent=[int]$_.ParentProcessId;command=$_.CommandLine;startTime=$_.CreationDate.ToUniversalTime().ToString('o')}
})
$listeners=@(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object {$_.LocalPort -eq 9222} | ForEach-Object {
  @{pid=[int]$_.OwningProcess;address=$_.LocalAddress}
})
@{processes=$processes;listeners=$listeners}|ConvertTo-Json -Depth 5 -Compress
"""
    result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script], capture_output=True,
                            timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError("Process/listener metadata unavailable: " + result.stderr.decode("utf-8", errors="replace")[-1800:])
    return json.loads(result.stdout.decode("utf-8-sig"))


def flag_value(args, name):
    values = []
    for index, arg in enumerate(args):
        if arg.startswith(name + "="):
            values.append(arg[len(name) + 1:])
        elif arg == name:
            values.append(args[index + 1] if index + 1 < len(args) else "")
    if len(values) > 1:
        raise RuntimeError("Duplicate Chrome flag: " + name)
    return values[0] if values else None


def inspect_instance(snapshot, profile, parse=command_argv):
    """Return the unique verified root, or None when starting is safe."""
    roots = []
    for process in snapshot["processes"]:
        command = process.get("command")
        if not command:
            # An inaccessible Chrome could own the profile; never guess.
            raise RuntimeError("Chrome command line cannot be verified")
        args = parse(command)
        data_dir = flag_value(args, "--user-data-dir")
        if data_dir is None:
            continue
        if os.path.normcase(os.path.abspath(data_dir)) != os.path.normcase(os.path.abspath(profile)):
            continue
        if any(arg == "--type" or arg.startswith("--type=") for arg in args):
            continue
        if any(arg == "--headless" or arg.startswith("--headless=") for arg in args):
            raise RuntimeError("Dedicated Chrome is headless; existing instance preserved")
        if flag_value(args, "--remote-debugging-address") != "127.0.0.1" or flag_value(args, "--remote-debugging-port") != "9222":
            raise RuntimeError("Dedicated profile has incompatible debugging flags")
        if not process.get("startTime"):
            raise RuntimeError("Chrome StartTime unavailable")
        roots.append(process)
    if len(roots) > 1:
        raise RuntimeError("Dedicated Chrome root is ambiguous")
    listeners = snapshot["listeners"]
    if not roots:
        if listeners:
            raise RuntimeError("Port 9222 is occupied by an unverified process")
        return None
    root = roots[0]
    if not listeners or any(item["pid"] != root["pid"] or item["address"] != "127.0.0.1" for item in listeners):
        raise RuntimeError("Chrome listener ownership/address cannot be verified")
    return {"pid": root["pid"], "startTime": root["startTime"]}


def guard(profile, expected):
    actual = inspect_instance(process_snapshot(), profile)
    if actual != expected:
        raise RuntimeError("Dedicated Chrome identity changed")


def ensure_instance(profile, timeout):
    snapshot = process_snapshot()
    identity = inspect_instance(snapshot, profile)
    if identity is not None:
        return identity
    chrome = find_chrome()
    process = subprocess.Popen([str(chrome), "--user-data-dir=" + str(profile),
        "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=9222",
        "--no-first-run", "--no-default-browser-check", "about:blank"])
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = process_snapshot()
        if snapshot["listeners"]:
            identity = inspect_instance(snapshot, profile)
            if identity is None or identity["pid"] != process.pid:
                raise RuntimeError("Started Chrome root ownership mismatch")
            return identity
        if process.poll() is not None:
            raise RuntimeError("Chrome exited before the debugging listener became available")
        time.sleep(.2)
    raise RuntimeError("Chrome startup timed out; instance preserved")


def http_json(path):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open("http://127.0.0.1:9222" + path, timeout=3) as response:
        return json.load(response)


class CDPError(RuntimeError):
    def __init__(self, method, error):
        self.error = error
        super().__init__(json.dumps({"method": method, "error": error}))


def ready_call(connection, method, params, deadline):
    transient = {
        "Browser.getWindowForTarget": ("browser window not found", "no window found"),
        "Runtime.evaluate": ("execution context was destroyed", "cannot find context with specified id"),
    }
    last_error = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if last_error is not None:
                raise last_error
            raise TimeoutError("CDP readiness timed out: " + method)
        try:
            if isinstance(connection, CDP):
                return connection.call(method, params, timeout=min(5, remaining))
            return connection.call(method, params)
        except CDPError as exc:
            message = str(exc.error.get("message", "")).lower()
            if exc.error.get("code") != -32000 or not any(text in message for text in transient.get(method, ())):
                raise
            last_error = exc
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(.2, remaining))


class CDP:
    def __init__(self, transport):
        self.transport = transport
        self.sequence = 0

    def call(self, method, params=None, timeout=5):
        self.sequence += 1
        deadline = time.monotonic() + timeout
        self.transport.send(json.dumps({"id": self.sequence, "method": method, "params": params or {}}))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("CDP call timed out: " + method)
            settimeout = getattr(self.transport, "settimeout", None)
            if settimeout:
                settimeout(remaining)
            response = json.loads(self.transport.recv())
            if time.monotonic() >= deadline:
                raise TimeoutError("CDP call timed out: " + method)
            if response.get("id") != self.sequence:
                continue
            if "error" in response:
                raise CDPError(method, response["error"])
            return response.get("result", {})

    def close(self):
        self.transport.close()


def connect(url):
    parsed = urlsplit(url)
    if parsed.scheme != "ws" or parsed.hostname != "127.0.0.1" or parsed.port != 9222:
        raise RuntimeError("Unexpected CDP websocket endpoint")
    return CDP(websocket.create_connection(url, suppress_origin=True, timeout=5, http_no_proxy=["127.0.0.1"]))


def user32():
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    for name in ("IsWindowVisible", "IsIconic", "SetForegroundWindow"):
        getattr(user, name).argtypes = [wintypes.HWND]
        getattr(user, name).restype = wintypes.BOOL
    user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetForegroundWindow.restype = wintypes.HWND
    return user


def root_windows(pid):
    user = user32()
    found = set()
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    def visit(hwnd, unused):
        owner = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        class_name = ctypes.create_unicode_buffer(256)
        user.GetClassNameW(hwnd, class_name, len(class_name))
        if owner.value == pid and class_name.value == "Chrome_WidgetWin_1":
            found.add(int(hwnd))
        return True
    callback = callback_type(visit)
    user.EnumWindows(callback, 0)
    return found


def window_state(hwnd, pid, activate=False):
    user = user32()
    owner = wintypes.DWORD()
    user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
    if owner.value != pid:
        raise RuntimeError("Owned HWND process changed")
    if activate:
        if user.IsIconic(hwnd):
            user.ShowWindow(hwnd, 9)
        user.SetForegroundWindow(hwnd)
    return {"hwnd": hwnd, "pid": owner.value, "visible": bool(user.IsWindowVisible(hwnd)),
        "minimized": bool(user.IsIconic(hwnd)), "foregroundHwnd": int(user.GetForegroundWindow() or 0),
        "foreground": int(user.GetForegroundWindow() or 0) == hwnd}


def receipt_path(profile, target_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", target_id):
        raise ValueError("Invalid target ID")
    return profile / "mc-browser-open-targets" / (target_id + ".json")


def save_receipt(profile, receipt):
    path = receipt_path(profile, receipt["targetId"])
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(receipt), encoding="utf-8")
    temporary.replace(path)


def emit(value):
    print(json.dumps(value, ensure_ascii=True), flush=True)


def open_page(url, target_id=None, timeout=25):
    validate_url(url)
    profile = automation_profile()
    identity = inspect_instance(process_snapshot(), profile) if target_id else ensure_instance(profile, timeout)
    if identity is None:
        raise RuntimeError("Owned Chrome instance is unavailable; continuation cannot start a new instance")
    guard(profile, identity)
    browser = connect(http_json("/json/version")["webSocketDebuggerUrl"])
    owned = {"requestedUrl": url}
    if target_id:
        owned["targetId"] = target_id
    try:
        browser.call("Browser.getVersion")
        if target_id:
            receipt = json.loads(receipt_path(profile, target_id).read_text(encoding="utf-8"))
            if receipt["identity"] != identity or receipt["requestedUrl"] != url or receipt["targetId"] != target_id:
                raise RuntimeError("Continuation does not match an owned target in this instance")
        else:
            before = sorted(root_windows(identity["pid"]))
            existing = [item["targetId"] for item in browser.call("Target.getTargets")["targetInfos"]]
            guard(profile, identity)
            target_id = browser.call("Target.createTarget", {"url": url, "newWindow": True, "background": False})["targetId"]
            owned["targetId"] = target_id
            # Flush immediately, even if writing the continuation receipt fails.
            emit({**owned, "status": "created", "identity": identity})
            receipt = {**owned, "identity": identity, "beforeHwnds": before, "existingTargets": existing}
            try:
                save_receipt(profile, receipt)
            except Exception:
                owned["continuationAvailable"] = False
                raise
        owned["targetId"] = target_id
        deadline = time.monotonic() + timeout
        window_id = ready_call(browser, "Browser.getWindowForTarget", {"targetId": target_id}, deadline)["windowId"]
        if "windowId" in receipt and receipt["windowId"] != window_id:
            raise RuntimeError("Owned target window identity changed")
        receipt["windowId"] = window_id
        save_receipt(profile, receipt)
        page = None
        while time.monotonic() < deadline:
            target = next((item for item in http_json("/json/list") if item["id"] == target_id), None)
            if target and target.get("webSocketDebuggerUrl"):
                page = connect(target["webSocketDebuggerUrl"])
                break
            time.sleep(.2)
        if page is None:
            raise TimeoutError("Owned page connection timed out")
        try:
            dom = {}
            while time.monotonic() < deadline:
                result = ready_call(page, "Runtime.evaluate", {"expression":
                    "JSON.stringify({url:location.href,title:document.title,readyState:document.readyState,documentURI:document.documentURI})", "returnByValue": True}, deadline)
                dom = json.loads(result["result"]["value"])
                if dom.get("documentURI", dom.get("url", "")).startswith("chrome-error:"):
                    raise RuntimeError("Page network navigation failed")
                if dom.get("readyState") == "complete" and urlsplit(dom.get("url", "")).scheme in ("http", "https"):
                    break
                time.sleep(.2)
            else:
                raise TimeoutError("Page load timed out")
            owned.update(dom)
            guard(profile, identity)
            candidates = root_windows(identity["pid"]) - set(receipt["beforeHwnds"])
            while not candidates and time.monotonic() < deadline:
                time.sleep(.2)
                candidates = root_windows(identity["pid"]) - set(receipt["beforeHwnds"])
            confirmed_hwnd = receipt.get("hwnd")
            if confirmed_hwnd is not None:
                candidates = {confirmed_hwnd} if confirmed_hwnd in candidates else set()
            if len(candidates) != 1:
                raise RuntimeError("Owned window HWND is ambiguous; retain target ID and do not recreate")
            hwnd = next(iter(candidates))
            receipt["hwnd"] = hwnd
            save_receipt(profile, receipt)
            guard(profile, identity)
            if ready_call(browser, "Browser.getWindowForTarget", {"targetId": target_id}, deadline)["windowId"] != window_id:
                raise RuntimeError("Owned target window identity changed before activation")
            current_candidates = root_windows(identity["pid"]) - set(receipt["beforeHwnds"])
            if (confirmed_hwnd is not None and hwnd not in current_candidates) or (confirmed_hwnd is None and current_candidates != {hwnd}):
                raise RuntimeError("Owned window HWND changed before activation")
            page.call("Page.bringToFront")
            state = window_state(hwnd, identity["pid"], activate=True)
            owned.update(state)
            remaining = {item["targetId"] for item in browser.call("Target.getTargets")["targetInfos"]}
            owned["preexistingTargetsPreserved"] = set(receipt["existingTargets"]).issubset(remaining)
            guard(profile, identity)
            state = window_state(hwnd, identity["pid"])
            owned.update(state)
            if not state["visible"] or state["minimized"] or not state["foreground"]:
                raise RuntimeError("Windows foreground activation failed")
            return {**owned, "windowId": window_id, "status": "ok"}
        finally:
            page.close()
    except Exception as exc:
        return {**owned, "status": "error", "error": type(exc).__name__, "message": str(exc)}
    finally:
        browser.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Explicitly requested HTTP(S) URL to open visibly")
    parser.add_argument("--target-id", help="Continue only a target previously created by this helper")
    args = parser.parse_args(argv)
    try:
        if os.name != "nt":
            raise RuntimeError("Visible Chrome opening requires Windows")
        result = open_page(args.url, args.target_id)
    except Exception as exc:
        result = {"status": "error", "requestedUrl": args.url, "error": type(exc).__name__, "message": str(exc)}
        if args.target_id:
            result["targetId"] = args.target_id
    emit(result)
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
