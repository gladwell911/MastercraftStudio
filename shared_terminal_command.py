"""Windows registration, cold start, and the foreground terminal client."""
from __future__ import annotations

import argparse
import base64
import ctypes
import hashlib
import json
import msvcrt
import os
import re
import subprocess
import sys
import time
import uuid
from tempfile import NamedTemporaryFile
import winreg
from contextlib import contextmanager
from pathlib import Path

from remote_machine_config import _write_json_atomic
from shared_terminal import TerminalClient, TerminalError, TerminalOutcomeUnknown, TerminalUnavailable

PROVIDERS = ("codex", "kimi", "agy")
COMMAND_MARKER = "# MC shared terminal launcher"
LEGACY_COMMAND_MARKER = b"@rem MC shared terminal launcher\r\n"
START_ARGUMENT = "--shared-terminal-start"
MAX_PROMPT_LENGTH = 32_768


def terminal_directory() -> Path:
    local = os.getenv("LOCALAPPDATA")
    return (Path(local) if local else Path.home() / "AppData" / "Local") / "MC" / "SharedTerminal"


def terminal_endpoint(gui: Path) -> Path:
    installation = os.path.normcase(str(gui.resolve())).encode("utf-8")
    scope = hashlib.sha256(installation).hexdigest()[:24]
    return terminal_directory() / "instances" / scope / "endpoint.json"


@contextmanager
def _file_lock(path: Path, deadline: float):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        while True:
            try:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise TerminalError("等待另一个终端启动 MC 超时。请确认 MC 已启动后重试。") from exc
                time.sleep(.1)
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def _search_command(name: str, own: Path, paths: list[str]) -> Path | None:
    for directory in paths:
        candidate_dir = Path(os.path.expandvars(directory.strip().strip('"')))
        if not directory.strip() or candidate_dir.resolve() == own.resolve():
            continue
        for extension in (".ps1", ".exe", ".cmd", ".bat", ".com"):
            candidate = candidate_dir / (name + extension)
            if candidate.is_file():
                return candidate
    # An executable or script in the managed folder still belongs to its owner.
    return next((own / (name + ext) for ext in (".exe", ".bat", ".com")
                 if (own / (name + ext)).is_file()), None)


def _shim_contents(worker: Path, provider: str) -> bytes:
    path = str(worker).replace("'", "''")
    # Windows PowerShell's native invocation drops or reshapes some arguments.
    # Pass the Windows CRT command line directly and keep the console streams.
    source = COMMAND_MARKER + "\n" + rf"""
$info = New-Object System.Diagnostics.ProcessStartInfo
$info.FileName = '{path}'
$info.UseShellExecute = $false
$quoted = foreach ($argument in @('--shared-terminal', '{provider}') + $args) {{
    '"' + ([string]$argument -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"'
}}
$info.Arguments = $quoted -join ' '
$process = [System.Diagnostics.Process]::Start($info)
try {{
    $process.WaitForExit()
    exit $process.ExitCode
}} finally {{
    $process.Dispose()
}}
"""
    return source.replace("\n", "\r\n").encode("utf-8-sig")


def _notify_environment_changed() -> None:
    ctypes.windll.user32.SendMessageTimeoutW(
        0xFFFF, 0x1A, 0, ctypes.c_wchar_p("Environment"), 0x2, 1000, None,
    )


def register_commands(worker: Path, *, user_environment="Environment") -> list[str]:
    """Install the three shims without replacing an existing user's command."""
    directory = terminal_directory() / "bin"
    directory.mkdir(parents=True, exist_ok=True)
    conflicts = []
    with _file_lock(directory.parent / "register.lock", time.monotonic() + 3):
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, user_environment,
                                access=winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as key:
            try:
                user_path, value_kind = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                user_path, value_kind = "", winreg.REG_EXPAND_SZ
            paths = os.environ.get("PATH", "").split(";") + user_path.split(";")
            has_managed_command = False
            for provider in PROVIDERS:
                name = f"mc-{provider}"
                shim = directory / (name + ".ps1")
                legacy = directory / (name + ".cmd")
                conflicting = _search_command(name, directory, paths)
                previous = shim.read_bytes() if shim.is_file() else b""
                legacy_owned = legacy.is_file() and legacy.read_bytes().startswith(LEGACY_COMMAND_MARKER)
                if legacy.exists() and not legacy_owned:
                    conflicting = conflicting or legacy
                if conflicting is not None or (shim.exists() and not previous.startswith(
                    (COMMAND_MARKER + "\r\n").encode("utf-8-sig")
                )):
                    conflicts.append(f"{name} 已由 {conflicting or shim} 提供")
                    continue
                current = _shim_contents(worker.resolve(), provider)
                if current != previous:
                    with NamedTemporaryFile(dir=directory, suffix=".ps1.tmp", delete=False) as stream:
                        temporary = Path(stream.name)
                    try:
                        temporary.write_bytes(current)
                        temporary.replace(shim)
                    finally:
                        temporary.unlink(missing_ok=True)
                if legacy_owned:
                    legacy.unlink()
                has_managed_command = True
            if has_managed_command and not any(
                item.strip() and Path(os.path.expandvars(item.strip().strip('"'))).resolve() == directory.resolve()
                for item in user_path.split(";")
            ):
                winreg.SetValueEx(key, "Path", 0, value_kind, ";".join(filter(None, (user_path, str(directory)))))
                if user_environment == "Environment":
                    _notify_environment_changed()
    return conflicts


def _probe(endpoint: Path, deadline: float) -> tuple[TerminalClient | None, bool]:
    try:
        client = TerminalClient.from_endpoint(endpoint)
        status, body = client.request("ping", {}, timeout=max(.1, min(5.5, deadline - time.monotonic())))
    except TerminalUnavailable as exc:
        return None, exc.verified
    return (client if status == 200 and body.get("ready") is True else None), True


def _process_created_at(pid: int) -> int | None:
    from ctypes import wintypes

    kernel = ctypes.windll.kernel32
    open_process = kernel.OpenProcess
    open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    open_process.restype = wintypes.HANDLE
    wait = kernel.WaitForSingleObject
    wait.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    wait.restype = wintypes.DWORD
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    get_times = kernel.GetProcessTimes
    get_times.argtypes = (wintypes.HANDLE, *(ctypes.POINTER(wintypes.FILETIME),) * 4)
    get_times.restype = wintypes.BOOL
    handle = open_process(0x00101000, False, pid)  # SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        if wait(handle, 0) != 258:  # WAIT_TIMEOUT means still running.
            return None
        times = [wintypes.FILETIME() for _ in range(4)]
        if not get_times(handle, *(ctypes.byref(value) for value in times)):
            return None
        created = times[0]
        return (created.dwHighDateTime << 32) | created.dwLowDateTime
    finally:
        close(handle)


def ensure_terminal(endpoint: Path, launch, *, timeout: float = 30) -> TerminalClient:
    deadline = time.monotonic() + timeout
    client, _ = _probe(endpoint, deadline)
    if client is not None:
        return client
    started_path = endpoint.with_name("starting.json")
    with _file_lock(endpoint.with_name("startup.lock"), deadline):
        try:
            previous = json.loads(started_path.read_text(encoding="utf-8"))
            pid, created_at = int(previous["pid"]), int(previous["created_at"])
        except (OSError, ValueError, TypeError, KeyError):
            pid, created_at = 0, None
        process = None
        while time.monotonic() < deadline:
            client, verified_server = _probe(endpoint, deadline)
            if client is not None:
                started_path.unlink(missing_ok=True)
                return client
            if not verified_server and not (created_at and _process_created_at(pid) == created_at):
                if process is not None:
                    started_path.unlink(missing_ok=True)
                    raise TerminalError("MC 在本机终端服务就绪前退出。请手动运行 mc.exe，检查启动提示后重试。")
                try:
                    process = launch()
                    pid = process.pid
                    created_at = _process_created_at(pid)
                    if created_at is not None:
                        _write_json_atomic(started_path, {"pid": pid, "created_at": created_at})
                except OSError as exc:
                    raise TerminalError("无法启动本机 MC。请检查安装目录并手动运行一次 mc.exe。") from exc
            time.sleep(.15)
    raise TerminalError("等待 MC 本机服务就绪超时。检查 MC 的启动提示，完成后在本窗口重试。")


def _terminal_text(value) -> str:
    # Treat model output and history titles as text even when they contain escape sequences.
    return "".join(character for character in str(value).replace("\r\n", "\n")
                   if character in "\n\t" or (ord(character) >= 32 and not 127 <= ord(character) <= 159))


def _show_response(status: int, body: dict) -> None:
    if status == 200:
        return
    if body.get("message"):
        raise TerminalError(str(body["message"]))
    errors = {
        "provider_unavailable": "当前 MC 版本尚未提供此终端入口。请运行支持该提供方的 MC。",
        "chat_not_found": "找不到此提供方的本机聊天。请重新列出会话并选择。",
        "chat_changed": "聊天已切换原生会话。请重新选择后发送。",
        "ui_unavailable": "MC 正在退出或尚未就绪。请重新打开所选安装。",
        "ui_timeout": "等待 MC 处理请求超时。请检查 MC 后重试。",
        "response_too_large": "本机响应超过传输大小。请在 MC 或 RC 阅读对应聊天。",
    }
    error = str(body.get("error") or "")
    raise TerminalError(errors.get(error, f"MC 无法处理本机请求（{error or status}）。"))


def _watch(client, scope, *, timeout=0):
    deadline = time.monotonic() + timeout if timeout else None
    seen = {}
    while deadline is None or time.monotonic() < deadline:
        status, body = client.request("watch", scope)
        if status == 404 and body.get("error") == "request_not_found":
            if deadline is None:
                raise TerminalError("MC 当前无法确认此请求。请在同一聊天检查历史。")
            time.sleep(.35)
            continue
        _show_response(status, body)
        for row in body.get("steps", []):
            identity, label = row["id"], row["text"]
            if seen.get(identity) != label:
                print(_terminal_text(label), flush=True)
                seen[identity] = label
        interaction_id = body.get("interaction_id")
        if interaction_id and interaction_id not in seen:
            seen[interaction_id] = ""
            print("AGY 等待输入，请在 MC 或 RC 的此聊天处理。", flush=True)
        if body["state"] == "done":
            print(_terminal_text(body["answer"]), flush=True)
            return
        if body["state"] != "pending":
            raise TerminalError(body.get("message") or "任务已结束。请在 MC 查看该回合的状态。")
        deadline = None
        time.sleep(.4)
    raise TerminalError("MC 尚未确认提交。请按原聊天检查历史，再决定是否发送。")


def _send(client, scope, prompt):
    prompt = prompt.strip()
    if not prompt or len(prompt) > MAX_PROMPT_LENGTH:
        raise TerminalError("请输入非空问题，每条最多 32768 个字符。")
    scope = {**scope, "request_id": uuid.uuid4().hex}
    try:
        status, body = client.request("send", {**scope, "text": prompt})
        # wx may have accepted a request before its response crossed the local boundary.
        if body.get("error") in {"ui_timeout", "local_operation_failed"}:
            raise TerminalOutcomeUnknown()
        _show_response(status, body)
    except TerminalOutcomeUnknown:
        print("提交结果未知，正在只读检查。同一请求不会自动重发。", file=sys.stderr)
        _watch(client, scope, timeout=8)
    else:
        _watch(client, {**scope, "turn_index": body["turn_index"]})


def _open_codex(client, scope, prompt):
    status, result = client.request("open_native", scope, timeout=40)
    _show_response(status, result)
    command, env = _codex_tui_command(result, prompt)
    return subprocess.call(command, cwd=result["cwd"], env=env)


def _codex_tui_command(result, prompt):
    from codex_native_terminal import TOKEN_ENV
    from remote_machine_config import _dpapi_unprotect

    endpoint = result["url"]
    if not re.fullmatch(r"ws://127\.0\.0\.1:[1-9][0-9]{0,4}", endpoint):
        raise TerminalError("MC 返回的 Codex 终端端点无效。")
    protected = base64.b64decode(result["credential"], validate=True)
    token = _dpapi_unprotect(protected)
    if not token or not re.fullmatch(rb"[A-Za-z0-9_-]{43}", token):
        raise TerminalError("无法读取当前 Windows 用户的 Codex 终端凭据。")
    env = dict(os.environ, CODEX_HOME=result["home"])
    if env.get("TERM") in (None, "", "dumb"):
        env["TERM"] = "xterm-256color"
    env[TOKEN_ENV] = token.decode("ascii")
    executable = Path(result["executable"])
    if executable.suffix.lower() != ".exe" or not executable.is_absolute() or not executable.is_file():
        raise TerminalError("找不到 MC 使用的 Codex 可执行文件。请重新启动 MC 并重试。")
    command = [str(executable), "resume", "--remote", endpoint,
               "--remote-auth-token-env", TOKEN_ENV, "--", result["thread_id"]]
    if prompt is not None:
        command.append(prompt)
    return command, env


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="mc-" + str((argv or [""])[0]))
    parser.add_argument("provider", choices=PROVIDERS, help=argparse.SUPPRESS)
    parser.add_argument("--list", action="store_true", help="列出此提供方在本机 MC 中的会话")
    parser.add_argument("--chat", metavar="ID", help="按 MC 聊天 ID 或原生会话 ID 打开已有会话")
    parser.add_argument("--send", metavar="TEXT", help="发送一条消息并等待本轮结束")
    args = parser.parse_args(argv)
    gui = Path(sys.executable).resolve().with_name("mc.exe")
    try:
        client = ensure_terminal(
            terminal_endpoint(gui),
            lambda: subprocess.Popen([str(gui), START_ARGUMENT], cwd=gui.parent,
                                     creationflags=subprocess.CREATE_NO_WINDOW),
        )
        status, body = client.request("list", {"provider": args.provider})
        _show_response(status, body)
        chats = body["chats"]
        if args.list or not args.chat:
            for index, chat in enumerate(chats, 1):
                print(_terminal_text(f'{index}. {chat["title"]} [{chat["chat_id"]}]'))
            if args.list:
                return 0
        if not chats:
            raise TerminalError("没有此提供方的本机会话。请先在 MC 打开或创建一条会话。")
        if args.chat:
            found = [chat for chat in chats if args.chat in (chat["chat_id"], chat["session_id"])]
            if len(found) != 1:
                raise TerminalError("会话不存在或原生 ID 对应多条会话。请用 --list 查找 MC 聊天 ID。")
            selection = found[0]
        else:
            choice = input("输入会话编号或原生会话 ID（回车退出）：").strip()
            if not choice:
                return 0
            if choice.isdecimal() and len(choice) < 12 and 1 <= int(choice) <= len(chats):
                selection = chats[int(choice) - 1]
            else:
                matches = [chat for chat in chats if choice in (chat["chat_id"], chat["session_id"])]
                if len(matches) != 1:
                    raise TerminalError("会话不存在或对应多条会话。请按列表编号选择。")
                selection = matches[0]
        scope = {"provider": args.provider, "chat_id": selection["chat_id"], "session_id": selection["session_id"]}
        print(_terminal_text(f'本机 {body["pair_id"]} · {selection["title"]}'))
        if args.provider == "codex":
            return _open_codex(client, scope, args.send)
        if args.send is not None:
            _send(client, scope, args.send)
            return 0
        while True:
            prompt = input("> ").strip()
            if prompt in ("/exit", "/quit"):
                return 0
            if prompt:
                try:
                    _send(client, scope, prompt)
                except TerminalError as exc:
                    print(_terminal_text(exc), file=sys.stderr)
    except EOFError:
        return 0
    except KeyboardInterrupt:
        print("已返回 PowerShell。正在执行的任务可在 MC 或 RC 查看和停止。", file=sys.stderr)
        return 130
    except (TerminalError, OSError, KeyError) as exc:
        print(_terminal_text(exc), file=sys.stderr)
        return 1
