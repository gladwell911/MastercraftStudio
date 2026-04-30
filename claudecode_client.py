import json
import os
import queue
import shutil
import subprocess
from typing import Callable

from cli_agent_manager import CliRunRequest, get_default_cli_agent_manager
from context_usage import normalize_context_usage


CLAUDECODE_MODEL_PREFIX = "claudecode/"
DEFAULT_CLAUDECODE_MODEL = "claudecode/default"
DEFAULT_TIMEOUT_SECONDS = 300


def is_claudecode_model(model: str) -> bool:
    return str(model or "").strip().startswith(CLAUDECODE_MODEL_PREFIX)


def resolve_claudecode_command() -> list[str]:
    override = str(os.environ.get("CLAUDE_BIN") or "").strip()
    if override:
        return [override]

    if os.name == "nt":
        for candidate in ("claude.cmd", "claude.exe", "claude.bat", "claude"):
            resolved = shutil.which(candidate)
            if resolved:
                return [resolved]
        script_path = shutil.which("claude.ps1")
        if script_path:
            for shell_name in ("pwsh.exe", "pwsh", "powershell.exe", "powershell"):
                shell_path = shutil.which(shell_name)
                if shell_path:
                    return [shell_path, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script_path]

    resolved = shutil.which("claude")
    if resolved:
        return [resolved]
    raise FileNotFoundError(
        "未找到 Claude Code CLI。请先安装 Claude Code，或通过 CLAUDE_BIN 环境变量指定路径。"
    )


class ClaudeCodeClient:
    def __init__(
        self,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        auto_approve: bool = False,
        full_auto: bool = False,
        cli_manager=None,
    ) -> None:
        self.timeout = max(int(timeout or DEFAULT_TIMEOUT_SECONDS), 1)
        self.auto_approve = auto_approve
        self.full_auto = full_auto
        self.cli_manager = cli_manager if cli_manager is not None else get_default_cli_agent_manager()
        self.stdin_queue = queue.Queue()
        self.stdin_writer_thread = None
        self.last_context_usage = None

    def send_user_input(self, user_input: str) -> None:
        self.stdin_queue.put(str(user_input or "").strip())

    def stream_chat(
        self,
        user_text: str,
        session_id: str = "",
        on_delta: Callable[[str], None] | None = None,
        on_user_input: Callable[[dict], str] | None = None,
        on_approval: Callable[[dict], str] | None = None,
    ) -> tuple[str, str]:
        if not str(user_text or "").strip():
            return ("", session_id)

        self.last_context_usage = None
        command = self._build_command(user_text, str(session_id or "").strip())
        full_text = ""
        new_session_id = str(session_id or "").strip()
        plain_stdout_lines: list[str] = []
        debug_info: dict = {
            "json_lines_received": 0,
            "assistant_messages": 0,
            "result_messages": 0,
            "text_items": 0,
            "parse_errors": 0,
            "message_types": set(),
        }
        pending_output = ""

        def _process_line(raw_line: str) -> None:
            nonlocal full_text, new_session_id
            line = str(raw_line or "").strip()
            if not line:
                return
            try:
                obj = json.loads(line)
                debug_info["json_lines_received"] += 1
            except json.JSONDecodeError:
                debug_info["parse_errors"] += 1
                plain_stdout_lines.append(line)
                return
            if not isinstance(obj, dict):
                return

            msg_type = str(obj.get("type") or "")
            debug_info["message_types"].add(msg_type)

            if msg_type == "assistant":
                debug_info["assistant_messages"] += 1
                message = obj.get("message") if isinstance(obj.get("message"), dict) else {}
                content = message.get("content") if isinstance(message.get("content"), list) else []
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    if str(item.get("type") or "") != "text":
                        continue
                    text = str(item.get("text") or "")
                    if text:
                        debug_info["text_items"] += 1
                        full_text += text
                        if callable(on_delta):
                            on_delta(text)
                return

            if msg_type == "result":
                debug_info["result_messages"] += 1
                sid = str(obj.get("session_id") or "").strip()
                if sid:
                    new_session_id = sid
                model_usage = obj.get("modelUsage") if isinstance(obj.get("modelUsage"), dict) else {}
                if model_usage:
                    model_name, stats = next(iter(model_usage.items()))
                    if isinstance(stats, dict):
                        used_tokens = (
                            int(stats.get("inputTokens") or 0)
                            + int(stats.get("outputTokens") or 0)
                            + int(stats.get("cacheReadInputTokens") or 0)
                            + int(stats.get("cacheCreationInputTokens") or 0)
                        )
                        self.last_context_usage = normalize_context_usage(
                            used_tokens=used_tokens,
                            context_window=stats.get("contextWindow") or 0,
                            source="claudecode",
                            exact=True,
                            fresh=True,
                            model=str(model_name or ""),
                        ).to_dict()
                if not full_text:
                    result_text = str(obj.get("result") or "").strip()
                    if result_text and result_text not in ("success", "error"):
                        full_text = result_text
                        if callable(on_delta):
                            on_delta(result_text)
                return

            if msg_type == "user_input" and callable(on_user_input):
                user_reply = on_user_input(obj)
                if user_reply:
                    self.send_user_input(user_reply)
                return

            if msg_type == "approval" and callable(on_approval):
                approval_reply = on_approval(obj)
                if approval_reply:
                    self.send_user_input(approval_reply)

        def _on_output(chunk: str) -> None:
            nonlocal pending_output
            pending_output += str(chunk or "")
            while "\n" in pending_output:
                line, pending_output = pending_output.split("\n", 1)
                _process_line(line)

        try:
            result = self.cli_manager.run(
                CliRunRequest(
                    agent_id="claudecode",
                    command=command,
                    timeout=self.timeout + 30,
                    env=os.environ.copy(),
                    prefer_pty=True,
                    check=False,
                ),
                on_output=_on_output,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "未找到 Claude Code CLI。请先安装 Claude Code，或通过 CLAUDE_BIN 环境变量指定路径。"
            ) from exc

        if pending_output.strip():
            _process_line(pending_output)
        stderr_lines = [line.strip() for line in str(result.stderr or "").splitlines() if line.strip()]

        if result.returncode not in (0, None):
            err_detail = stderr_lines[-1] if stderr_lines else f"退出码 {result.returncode}"
            debug_msg = self._format_debug_info(debug_info)
            raise RuntimeError(f"Claude Code CLI 执行失败：{err_detail}\n调试信息：{debug_msg}")

        if not full_text and debug_info.get("json_lines_received", 0) == 0 and plain_stdout_lines:
            plain_text = "\n".join(plain_stdout_lines).strip()
            if plain_text:
                full_text = plain_text
                if callable(on_delta):
                    on_delta(plain_text)

        if not full_text and stderr_lines:
            debug_msg = self._format_debug_info(debug_info)
            raise RuntimeError(f"Claude Code CLI 未返回内容：{stderr_lines[-1]}\n调试信息：{debug_msg}")

        if not full_text:
            debug_msg = self._format_debug_info(debug_info)
            raise RuntimeError(f"Claude Code CLI 未返回任何内容。调试信息：{debug_msg}")

        return (full_text, new_session_id)

    def _format_debug_info(self, debug_info: dict) -> str:
        lines = [
            f"JSON 行数: {debug_info.get('json_lines_received', 0)}",
            f"Assistant 消息: {debug_info.get('assistant_messages', 0)}",
            f"Result 消息: {debug_info.get('result_messages', 0)}",
            f"文本项: {debug_info.get('text_items', 0)}",
            f"解析错误: {debug_info.get('parse_errors', 0)}",
            f"消息类型: {', '.join(sorted(debug_info.get('message_types', set())))}",
        ]
        return " | ".join(lines)

    def _build_command(self, user_text: str, session_id: str) -> list[str]:
        cmd = resolve_claudecode_command()
        cmd.extend(["--print", user_text, "--output-format", "stream-json", "--verbose"])
        if session_id:
            cmd.extend(["--resume", session_id])

        if self.full_auto:
            cmd.extend(["--dangerously-skip-permissions"])
            auto_prompt = (
                "重要指示：你需要完全自主地完成任务，不要使用 AskUserQuestion 工具询问用户选择方案。"
                "当有多个实现方案时，请根据最佳实践自行选择最合适的方案并直接实施。"
                "只在遇到无法解决的错误或需要用户提供额外信息时才询问用户。"
            )
            cmd.extend(["--append-system-prompt", auto_prompt])
        elif self.auto_approve:
            cmd.extend(["--dangerously-skip-permissions"])

        return cmd
