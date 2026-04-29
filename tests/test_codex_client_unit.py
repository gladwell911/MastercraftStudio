import threading
import time
from types import SimpleNamespace
from pathlib import Path

import codex_client


def test_codex_model_helper():
    assert codex_client.is_codex_model("codex/main")
    assert not codex_client.is_codex_model("openai/gpt-5.2")


def test_codex_client_start_turn_sends_request_and_returns_result(monkeypatch):
    client = codex_client.CodexAppServerClient()
    sent = []
    seen = {}
    monkeypatch.setattr(client, "_ensure_started", lambda: None)
    monkeypatch.setattr(client, "_send_json", lambda payload: sent.append(payload))

    def _reply():
        time.sleep(0.01)
        client._handle_message({"id": 1, "result": {"turn": {"id": "turn-1"}}})

    threading.Thread(target=_reply, daemon=True).start()
    original_request = client._request_internal

    def _capture_request(method, params=None, timeout=None):
        seen["timeout"] = timeout
        return original_request(method, params=params, timeout=timeout)

    monkeypatch.setattr(client, "_request_internal", _capture_request)
    result = client.start_turn("thread-1", "hello")

    assert result["turn"]["id"] == "turn-1"
    assert seen["timeout"] == codex_client.DEFAULT_CODEX_TURN_TIMEOUT
    assert sent == [
        {
            "id": 1,
            "method": "turn/start",
            "params": {
                "threadId": "thread-1",
                "input": [{"type": "text", "text": "hello"}],
            },
        }
    ]


def test_codex_client_steer_turn_sends_expected_payload(monkeypatch):
    client = codex_client.CodexAppServerClient()
    sent = []
    seen = {}
    monkeypatch.setattr(client, "_ensure_started", lambda: None)
    monkeypatch.setattr(client, "_send_json", lambda payload: sent.append(payload))

    def _reply():
        time.sleep(0.01)
        client._handle_message({"id": 1, "result": {"turnId": "turn-1"}})

    threading.Thread(target=_reply, daemon=True).start()
    original_request = client._request_internal

    def _capture_request(method, params=None, timeout=None):
        seen["timeout"] = timeout
        return original_request(method, params=params, timeout=timeout)

    monkeypatch.setattr(client, "_request_internal", _capture_request)
    client.steer_turn("thread-1", "turn-1", "补充信息")

    assert sent[0]["method"] == "turn/steer"
    assert sent[0]["params"]["expectedTurnId"] == "turn-1"
    assert sent[0]["params"]["input"][0]["text"] == "补充信息"
    assert seen["timeout"] == codex_client.DEFAULT_CODEX_TURN_TIMEOUT


def test_codex_client_start_turn_items_sends_structured_input(monkeypatch):
    client = codex_client.CodexAppServerClient()
    sent = []
    monkeypatch.setattr(client, "_ensure_started", lambda: None)
    monkeypatch.setattr(client, "_send_json", lambda payload: sent.append(payload))

    def _reply():
        time.sleep(0.01)
        client._handle_message({"id": 1, "result": {"turn": {"id": "turn-structured"}}})

    threading.Thread(target=_reply, daemon=True).start()
    items = [
        {"type": "text", "text": "请查看图片"},
        {"type": "localImage", "path": r"C:\tmp\shot.png"},
    ]

    result = client.start_turn_items("thread-1", items)

    assert result["turn"]["id"] == "turn-structured"
    assert sent == [
        {
            "id": 1,
            "method": "turn/start",
            "params": {
                "threadId": "thread-1",
                "input": items,
            },
        }
    ]


def test_codex_client_maps_server_request_to_event():
    seen = []
    client = codex_client.CodexAppServerClient(on_event=seen.append)

    client._handle_message(
        {
            "id": 9,
            "method": "item/tool/requestUserInput",
            "params": {"threadId": "th", "turnId": "tu", "questions": [{"id": "q1"}]},
        }
    )

    assert len(seen) == 1
    assert seen[0].type == "server_request"
    assert seen[0].request_id == 9
    assert seen[0].method == "item/tool/requestUserInput"


def test_codex_client_maps_agent_message_delta_to_event():
    seen = []
    client = codex_client.CodexAppServerClient(on_event=seen.append)

    client._handle_message(
        {
            "method": "item/agentMessage/delta",
            "params": {
                "threadId": "th",
                "turnId": "tu",
                "itemId": "msg-1",
                "delta": "hello",
            },
        }
    )

    assert len(seen) == 1
    assert seen[0].type == "agent_message_delta"
    assert seen[0].text == "hello"


def test_resolve_codex_launch_command_uses_powershell_for_ps1(monkeypatch):
    def _which(name):
        mapping = {
            "codex.ps1": r"C:\Users\test\AppData\Roaming\npm\codex.ps1",
            "powershell.exe": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        }
        return mapping.get(name)

    monkeypatch.setattr(codex_client.os, "name", "nt")
    monkeypatch.setattr(codex_client.shutil, "which", _which)
    monkeypatch.delenv("CODEX_BIN", raising=False)

    command = codex_client.resolve_codex_launch_command()

    assert command == [
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        r"C:\Users\test\AppData\Roaming\npm\codex.ps1",
    ]


def test_resolve_codex_launch_command_prefers_exe_over_cmd(monkeypatch):
    def _which(name):
        mapping = {
            "codex.exe": r"C:\tools\codex.exe",
            "codex.cmd": r"C:\Users\test\AppData\Roaming\npm\codex.cmd",
            "codex.bat": r"C:\Users\test\AppData\Roaming\npm\codex.bat",
            "codex": r"C:\Users\test\AppData\Roaming\npm\codex.ps1",
        }
        return mapping.get(name)

    monkeypatch.setattr(codex_client.os, "name", "nt")
    monkeypatch.setattr(codex_client.shutil, "which", _which)
    monkeypatch.delenv("CODEX_BIN", raising=False)

    command = codex_client.resolve_codex_launch_command()

    assert command == [r"C:\tools\codex.exe"]


def test_ensure_started_uses_resolved_launch_command(monkeypatch):
    client = codex_client.CodexAppServerClient()
    seen = {}

    class _Proc:
        stdin = None
        stdout = None
        stderr = None

        def poll(self):
            return None

    def _popen(args, **kwargs):
        seen["call"] = {"args": args, "kwargs": kwargs}
        return _Proc()

    monkeypatch.setattr(codex_client, "resolve_codex_launch_command", lambda: ["codex.cmd"])
    monkeypatch.setattr(
        codex_client,
        "build_codex_app_server_env",
        lambda cwd=None: ({"CODEX_HOME": r"C:\\tmp\\codex-home"}, Path(r"C:\\tmp\\codex-home")),
    )
    monkeypatch.setattr(codex_client.subprocess, "Popen", _popen)
    monkeypatch.setattr(codex_client.threading, "Thread", lambda *args, **kwargs: SimpleNamespace(start=lambda: None))
    monkeypatch.setattr(client, "_initialize", lambda: seen.setdefault("initialized", True))

    client._ensure_started()

    assert seen["call"]["args"] == [
        "codex.cmd",
        "app-server",
        "--listen",
        "stdio://",
        "--analytics-default-enabled",
    ]
    assert seen["call"]["kwargs"]["env"]["CODEX_HOME"] == r"C:\\tmp\\codex-home"
    assert seen["initialized"] is True


def test_ensure_started_hides_windows_console_for_codex(monkeypatch):
    client = codex_client.CodexAppServerClient()
    seen = {}

    class _Proc:
        stdin = None
        stdout = None
        stderr = None

        def poll(self):
            return None

    def _popen(args, **kwargs):
        seen["call"] = {"args": args, "kwargs": kwargs}
        return _Proc()

    monkeypatch.setattr(codex_client, "resolve_codex_launch_command", lambda: [r"C:\tools\codex.exe"])
    monkeypatch.setattr(
        codex_client,
        "build_codex_app_server_env",
        lambda cwd=None: ({"CODEX_HOME": r"C:\\tmp\\codex-home"}, Path(r"C:\\tmp\\codex-home")),
    )
    monkeypatch.setattr(codex_client.os, "name", "nt")
    monkeypatch.setattr(codex_client.subprocess, "Popen", _popen)
    monkeypatch.setattr(codex_client.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(codex_client.threading, "Thread", lambda *args, **kwargs: SimpleNamespace(start=lambda: None))
    monkeypatch.setattr(client, "_initialize", lambda: seen.setdefault("initialized", True))

    client._ensure_started()

    assert seen["call"]["kwargs"]["creationflags"] == 0x08000000
    assert seen["initialized"] is True


def test_build_codex_app_server_command_uses_app_server_defaults(monkeypatch):
    monkeypatch.setattr(codex_client, "resolve_codex_launch_command", lambda: ["codex.cmd"])

    command = codex_client.build_codex_app_server_command(r"C:\code\codex")

    assert command == [
        "codex.cmd",
        "app-server",
        "--listen",
        "stdio://",
        "--analytics-default-enabled",
    ]


def test_build_codex_app_server_env_seeds_clean_home(tmp_path, monkeypatch):
    source_home = tmp_path / ".codex"
    source_home.mkdir()
    (source_home / "auth.json").write_text("{\"token\": \"x\"}", encoding="utf-8")
    (source_home / "config.toml").write_text("personality = \"pragmatic\"\n", encoding="utf-8")
    (source_home / "state_5.sqlite").write_text("bad-state", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    env, codex_home = codex_client.build_codex_app_server_env(str(workspace))

    assert codex_home.parent == workspace
    assert codex_home.name == ".codex-home"
    assert env["CODEX_HOME"] == str(codex_home)
    assert (codex_home / "auth.json").read_text(encoding="utf-8") == "{\"token\": \"x\"}"
    assert (codex_home / "config.toml").read_text(encoding="utf-8") == "personality = \"pragmatic\"\n"
    assert not (codex_home / "state_5.sqlite").exists()


def test_build_codex_app_server_env_reuses_persistent_workspace_home(tmp_path, monkeypatch):
    source_home = tmp_path / ".codex"
    source_home.mkdir()
    (source_home / "auth.json").write_text("{\"token\": \"x\"}", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    codex_home = workspace / ".codex-home"
    codex_home.mkdir()
    (codex_home / "rollout.db").write_text("keep", encoding="utf-8")
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    env, reused_home = codex_client.build_codex_app_server_env(str(workspace))

    assert reused_home == codex_home
    assert env["CODEX_HOME"] == str(codex_home)
    assert (codex_home / "rollout.db").read_text(encoding="utf-8") == "keep"
    assert (codex_home / "auth.json").read_text(encoding="utf-8") == "{\"token\": \"x\"}"


def test_build_codex_app_server_env_links_missing_user_skills_into_workspace_home(tmp_path, monkeypatch):
    source_home = tmp_path / ".codex"
    source_home.mkdir()
    source_skills = source_home / "skills"
    source_skills.mkdir()
    (source_skills / "using-superpowers").mkdir()
    (source_skills / "using-superpowers" / "SKILL.md").write_text("skill", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    codex_home = workspace / ".codex-home"
    codex_home.mkdir()
    target_skills = codex_home / "skills"
    target_skills.mkdir()
    (target_skills / ".system").mkdir()
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    env, reused_home = codex_client.build_codex_app_server_env(str(workspace))

    linked_skill = reused_home / "skills" / "using-superpowers"
    assert reused_home == codex_home
    assert env["CODEX_HOME"] == str(codex_home)
    assert linked_skill.exists()
    assert (linked_skill / "SKILL.md").read_text(encoding="utf-8") == "skill"
    assert (reused_home / "skills" / ".system").exists()


def test_build_codex_app_server_env_keeps_existing_workspace_skill_entries(tmp_path, monkeypatch):
    source_home = tmp_path / ".codex"
    source_home.mkdir()
    source_skills = source_home / "skills"
    source_skills.mkdir()
    (source_skills / "using-superpowers").mkdir()
    (source_skills / "using-superpowers" / "SKILL.md").write_text("user-skill", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    codex_home = workspace / ".codex-home"
    codex_home.mkdir()
    target_skills = codex_home / "skills"
    target_skills.mkdir()
    (target_skills / "using-superpowers").mkdir()
    (target_skills / "using-superpowers" / "SKILL.md").write_text("workspace-skill", encoding="utf-8")
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    _, reused_home = codex_client.build_codex_app_server_env(str(workspace))

    assert (reused_home / "skills" / "using-superpowers" / "SKILL.md").read_text(encoding="utf-8") == "workspace-skill"


def test_close_keeps_persistent_codex_home(tmp_path):
    client = codex_client.CodexAppServerClient()

    class _Proc:
        def terminate(self):
            return None

        def wait(self, timeout=None):
            return None

        def poll(self):
            return None

    codex_home = tmp_path / ".codex-home"
    codex_home.mkdir()
    (codex_home / "rollout.db").write_text("keep", encoding="utf-8")
    client._proc = _Proc()
    client._codex_home_dir = codex_home
    client._owns_codex_home_dir = False

    client.close()

    assert codex_home.exists()
    assert (codex_home / "rollout.db").read_text(encoding="utf-8") == "keep"
