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


def test_codex_item_command_execution_normalizes_display_fields():
    seen = []
    client = codex_client.CodexAppServerClient(on_event=seen.append)

    client._handle_message(
        {
            "method": "item/completed",
            "params": {
                "threadId": "th",
                "turnId": "tu",
                "item": {
                    "id": "cmd-1",
                    "type": "commandExecution",
                    "title": "Run tests",
                    "command": "pytest -q",
                    "exitCode": 1,
                    "text": "pytest failed",
                },
            },
        }
    )

    assert len(seen) == 1
    assert seen[0].type == "item_completed"
    assert seen[0].status == "commandExecution"
    assert seen[0].title == "Run tests"
    assert seen[0].command == "pytest -q"
    assert seen[0].exit_code == 1
    assert seen[0].text == "pytest failed"
    assert seen[0].raw_text == "pytest failed"
    assert seen[0].display_kind == "command"
    assert seen[0].subtype == "commandExecution"


def test_codex_agent_message_delta_keeps_text_and_raw_text():
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
    assert seen[0].text == "hello"
    assert seen[0].raw_text == "hello"
    assert seen[0].display_kind == "commentary"
    assert seen[0].subtype == "agentMessageDelta"


def test_codex_file_change_generates_summary_when_text_missing():
    seen = []
    client = codex_client.CodexAppServerClient(on_event=seen.append)

    client._handle_message(
        {
            "method": "item/completed",
            "params": {
                "threadId": "th",
                "turnId": "tu",
                "item": {
                    "id": "file-1",
                    "type": "fileChange",
                    "path": "src/main.py",
                },
            },
        }
    )

    assert len(seen) == 1
    assert seen[0].type == "item_completed"
    assert seen[0].display_kind == "artifact"
    assert seen[0].subtype == "fileChange"
    assert seen[0].text == "Changed src/main.py"
    assert seen[0].raw_text == ""


def test_codex_plan_update_keeps_explanation_in_text_and_raw_text():
    seen = []
    client = codex_client.CodexAppServerClient(on_event=seen.append)

    client._handle_message(
        {
            "method": "turn/plan/updated",
            "params": {
                "threadId": "th",
                "turnId": "tu",
                "explanation": "Implement tests first.",
            },
        }
    )

    assert len(seen) == 1
    assert seen[0].type == "plan_updated"
    assert seen[0].text == "Implement tests first."
    assert seen[0].raw_text == "Implement tests first."
    assert seen[0].display_kind == "plan"
    assert seen[0].subtype == "turnPlanUpdated"


def test_codex_diff_update_keeps_diff_in_text_and_raw_text():
    seen = []
    client = codex_client.CodexAppServerClient(on_event=seen.append)

    client._handle_message(
        {
            "method": "turn/diff/updated",
            "params": {
                "threadId": "th",
                "turnId": "tu",
                "diff": "@@ -1 +1 @@\n-old\n+new\n",
            },
        }
    )

    assert len(seen) == 1
    assert seen[0].type == "diff_updated"
    assert seen[0].text == "@@ -1 +1 @@\n-old\n+new\n"
    assert seen[0].raw_text == "@@ -1 +1 @@\n-old\n+new\n"
    assert seen[0].display_kind == "artifact"
    assert seen[0].subtype == "turnDiffUpdated"


def test_codex_stderr_event_keeps_full_line_and_marks_error():
    seen = []
    client = codex_client.CodexAppServerClient(on_event=seen.append)

    client._emit_protocol_event("stderr", {"line": "fatal: bad object"}, {"method": "stderr"})

    assert len(seen) == 1
    assert seen[0].type == "stderr"
    assert seen[0].text == "fatal: bad object"
    assert seen[0].raw_text == "fatal: bad object"
    assert seen[0].display_kind == "error"
    assert seen[0].subtype == "stderr"


def test_codex_protocol_token_count_event_normalizes_usage():
    seen = []
    client = codex_client.CodexAppServerClient(on_event=seen.append)

    client._handle_message(
        {
            "method": "token_count",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "info": {
                    "total_token_usage": {"total_tokens": 44176},
                    "last_token_usage": {"total_tokens": 11891},
                    "model": "gpt-5-codex",
                },
            },
        }
    )

    assert seen[-1].type == "token_count"
    assert seen[-1].usage["used_tokens"] == 44176
    assert seen[-1].usage["context_window"] == 0
    assert seen[-1].usage["source"] == "codex"
    assert seen[-1].usage["exact"] is True
    assert seen[-1].usage["fresh"] is True
    assert seen[-1].usage["model"] == "gpt-5-codex"
    assert seen[-1].data["context_usage"] == seen[-1].usage
    assert client.last_context_usage == seen[-1].usage


def test_codex_event_msg_token_count_payload_normalizes_usage():
    seen = []
    client = codex_client.CodexAppServerClient(on_event=seen.append)

    client._handle_message(
        {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {"total_tokens": 68292},
                    "last_token_usage": {"total_tokens": 23096},
                    "model_context_window": 258400,
                },
            },
        }
    )

    assert seen[-1].type == "token_count"
    assert seen[-1].usage["used_tokens"] == 68292
    assert seen[-1].usage["context_window"] == 258400
    assert seen[-1].usage["source"] == "codex"
    assert seen[-1].data["context_usage"] == seen[-1].usage
    assert client.last_context_usage == seen[-1].usage


def test_codex_protocol_namespaced_token_count_event_normalizes_usage():
    seen = []
    client = codex_client.CodexAppServerClient(on_event=seen.append)

    client._handle_message(
        {
            "method": "codex/event/token_count",
            "params": {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "info": {
                    "total_token_usage": {"total_tokens": 44176},
                    "model": "gpt-5-codex",
                },
            },
        }
    )

    assert seen[-1].type == "token_count"
    assert seen[-1].usage["used_tokens"] == 44176
    assert seen[-1].usage["source"] == "codex"
    assert client.last_context_usage == seen[-1].usage


def test_codex_thread_token_usage_updated_event_normalizes_usage():
    seen = []
    client = codex_client.CodexAppServerClient(on_event=seen.append)

    client._handle_message(
        {
            "method": "thread/tokenUsage/updated",
            "params": {
                "threadId": "thread-1",
                "info": {
                    "total_token_usage": {"total_tokens": 44176},
                    "model_context_window": 258400,
                    "model": "gpt-5-codex",
                },
            },
        }
    )

    assert seen[-1].type == "token_count"
    assert seen[-1].usage["used_tokens"] == 44176
    assert seen[-1].usage["context_window"] == 258400
    assert seen[-1].usage["source"] == "codex"
    assert client.last_context_usage == seen[-1].usage


def test_codex_protocol_token_count_sums_usage_fields_when_total_missing():
    seen = []
    client = codex_client.CodexAppServerClient(on_event=seen.append)

    client._handle_message(
        {
            "method": "token_count",
            "params": {
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "cache_read_input_tokens": 30,
                    "cache_creation_input_tokens": 40,
                    "context_window": 1000,
                },
            },
        }
    )

    assert seen[-1].usage["used_tokens"] == 190
    assert seen[-1].usage["context_window"] == 1000
    assert seen[-1].usage["exact"] is True


def test_codex_protocol_token_count_sums_nested_component_usage():
    seen = []
    client = codex_client.CodexAppServerClient(on_event=seen.append)

    client._handle_message(
        {
            "method": "token_count",
            "params": {
                "info": {
                    "total_token_usage": {
                        "input_tokens": 100,
                        "output_tokens": 20,
                        "cache_read_input_tokens": 30,
                        "cache_creation_input_tokens": 40,
                    },
                    "context_window": 1000,
                },
            },
        }
    )

    assert seen[-1].usage["used_tokens"] == 190
    assert seen[-1].usage["context_window"] == 1000
    assert seen[-1].usage["exact"] is True


def test_codex_protocol_token_count_ignores_malformed_usage():
    seen = []
    client = codex_client.CodexAppServerClient(on_event=seen.append)

    client._handle_message(
        {
            "method": "token_count",
            "params": {
                "info": {
                    "total_token_usage": {"total_tokens": "bad"},
                    "context_window": 258400,
                },
            },
        }
    )

    assert seen[-1].type == "token_count"
    assert seen[-1].usage == {}
    assert "context_usage" not in seen[-1].data
    assert client.last_context_usage is None


def test_codex_callback_exception_clears_last_context_usage():
    def _raise(_event):
        raise RuntimeError("callback failed")

    client = codex_client.CodexAppServerClient(on_event=_raise)

    try:
        client._handle_message(
            {
                "method": "token_count",
                "params": {"info": {"total_token_usage": {"total_tokens": 44176}}},
            }
        )
    except RuntimeError:
        pass

    assert client.last_context_usage is None


def test_codex_start_turn_failure_clears_last_context_usage(monkeypatch):
    client = codex_client.CodexAppServerClient()

    def _raise(_method, _params=None, timeout=None):
        client.last_context_usage = {"used_tokens": 44176, "source": "codex"}
        raise RuntimeError("turn failed")

    monkeypatch.setattr(client, "request", _raise)

    try:
        client.start_turn("thread-1", "hello")
    except RuntimeError:
        pass

    assert client.last_context_usage is None


def test_codex_start_thread_failure_clears_last_context_usage(monkeypatch):
    client = codex_client.CodexAppServerClient()

    def _raise(_method, _params=None, timeout=None):
        client.last_context_usage = {"used_tokens": 44176, "source": "codex"}
        raise RuntimeError("thread failed")

    monkeypatch.setattr(client, "request", _raise)

    try:
        client.start_thread(r"C:\code\mc")
    except RuntimeError:
        pass

    assert client.last_context_usage is None


def test_codex_resume_thread_failure_clears_last_context_usage(monkeypatch):
    client = codex_client.CodexAppServerClient()

    def _raise(_method, _params=None, timeout=None):
        client.last_context_usage = {"used_tokens": 44176, "source": "codex"}
        raise RuntimeError("resume failed")

    monkeypatch.setattr(client, "request", _raise)

    try:
        client.resume_thread("thread-1")
    except RuntimeError:
        pass

    assert client.last_context_usage is None


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
