import json
import os
import shutil
import subprocess
import threading
from typing import Callable


CLAUDECODE_MODEL_PREFIX = "claudecode/"
DEFAULT_CLAUDECODE_MODEL = "claudecode/default"
DEFAULT_TIMEOUT_SECONDS = 300


def is_claudecode_model(model: str) -> bool:
    return str(model or "").strip().startswith(CLAUDECODE_MODEL_PREFIX)


def resolve_claudecode_command() -> list[str]:
    """查找 Claude Code CLI 可执行文件。"""
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
        full_auto: bool = False
    ) -> None:
        self.timeout = max(int(timeout or DEFAULT_TIMEOUT_SECONDS), 1)
        self.auto_approve = auto_approve
        self.full_auto = full_auto

    def stream_chat(
        self,
        user_text: str,
        session_id: str = "",
        on_delta: Callable[[str], None] | None = None,
    ) -> tuple[str, str]:
        """
        以流式方式调用 Claude Code CLI。
        返回 (完整回复文本, 新会话ID)。
        新会话ID 可在下一次调用时传入以保持上下文。
        """
        if not str(user_text or "").strip():
            return ("", session_id)

        command = self._build_command(user_text, str(session_id or "").strip())
        try:
            proc = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
                bufsize=1,
                env=os.environ.copy(),
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "未找到 Claude Code CLI。请先安装 Claude Code，或通过 CLAUDE_BIN 环境变量指定路径。"
            ) from exc

        full_text = ""
        new_session_id = str(session_id or "").strip()
        stderr_lines: list[str] = []

        def _read_stderr() -> None:
            if proc.stderr:
                for raw in proc.stderr:
                    line = raw.strip()
                    if line:
                        stderr_lines.append(line)

        stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
        stderr_thread.start()

        try:
            if proc.stdout:
                for raw_line in proc.stdout:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(obj, dict):
                        continue

                    msg_type = str(obj.get("type") or "")

                    if msg_type == "assistant":
                        # 从 assistant 消息内容中提取文本片段
                        message = obj.get("message") if isinstance(obj.get("message"), dict) else {}
                        content = message.get("content") if isinstance(message.get("content"), list) else []
                        for item in content:
                            if not isinstance(item, dict):
                                continue
                            if str(item.get("type") or "") == "text":
                                text = str(item.get("text") or "")
                                if text:
                                    full_text += text
                                    if callable(on_delta):
                                        on_delta(text)

                    elif msg_type == "result":
                        # 保存 session_id 供下次调用
                        sid = str(obj.get("session_id") or "").strip()
                        if sid:
                            new_session_id = sid
                        # 如果流式过程中没有收到文本，从 result 字段补取
                        if not full_text:
                            result_text = str(obj.get("result") or "").strip()
                            if result_text and result_text not in ("success", "error"):
                                full_text = result_text
                                if callable(on_delta):
                                    on_delta(result_text)
        finally:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            stderr_thread.join(timeout=2)

        rc = proc.returncode
        if rc not in (0, None):
            err_detail = stderr_lines[-1] if stderr_lines else f"退出码 {rc}"
            raise RuntimeError(f"Claude Code CLI 执行失败：{err_detail}")

        if not full_text and stderr_lines:
            raise RuntimeError(f"Claude Code CLI 未返回内容：{stderr_lines[-1]}")

        return (full_text, new_session_id)

    def _build_command(self, user_text: str, session_id: str) -> list[str]:
        cmd = resolve_claudecode_command()
        cmd.extend(["--print", user_text, "--output-format", "stream-json", "--verbose"])
        if session_id:
            cmd.extend(["--resume", session_id])

        # 根据配置选择权限模式
        if self.full_auto:
            # 完全自动模式：使用 dangerously-skip-permissions 完全跳过所有权限检查
            cmd.extend(["--dangerously-skip-permissions"])
            # 添加系统提示，指导 Claude 自主决策，不要询问用户
            auto_prompt = (
                "重要指示：你需要完全自主地完成任务，不要使用 AskUserQuestion 工具询问用户选择方案。"
                "当有多个实现方案时，请根据最佳实践自行选择最合适的方案并直接实施。"
                "只在遇到无法解决的错误或需要用户提供额外信息（如 API 密钥、配置参数等）时才询问用户。"
            )
            cmd.extend(["--append-system-prompt", auto_prompt])
        elif self.auto_approve:
            # 仅自动批准工具调用
            cmd.extend(["--dangerously-skip-permissions"])

        return cmd
