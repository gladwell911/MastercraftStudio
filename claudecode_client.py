import json
import os
import queue
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

        # 队列式用户交互
        self.stdin_queue = queue.Queue()
        self.stdin_writer_thread = None

    def send_user_input(self, user_input: str) -> None:
        """发送用户输入到 Claude Code"""
        self.stdin_queue.put(str(user_input or "").strip())

    def _stdin_writer(self, proc) -> None:
        """在单独的线程中写入 stdin"""
        import time
        try:
            start_time = time.time()
            while proc.poll() is None:  # 进程还在运行
                try:
                    # 从队列中获取用户输入（超时 1 秒）
                    user_input = self.stdin_queue.get(timeout=1)
                    if user_input is None:  # 哨兵值，表示停止
                        break
                    # 写入 stdin
                    if proc.stdin:
                        proc.stdin.write(user_input + "\n")
                        proc.stdin.flush()
                except queue.Empty:
                    # 如果 3 秒内没有收到任何数据，关闭 stdin 让 Claude Code 继续
                    if time.time() - start_time > 3 and proc.stdin:
                        try:
                            proc.stdin.close()
                        except:
                            pass
                        break
                    continue
        except Exception as e:
            pass
        finally:
            if proc.stdin:
                try:
                    proc.stdin.close()
                except:
                    pass

    def stream_chat(
        self,
        user_text: str,
        session_id: str = "",
        on_delta: Callable[[str], None] | None = None,
        on_user_input: Callable[[dict], str] | None = None,
        on_approval: Callable[[dict], str] | None = None,
    ) -> tuple[str, str]:
        """
        以流式方式调用 Claude Code CLI。
        返回 (完整回复文本, 新会话ID)。
        新会话ID 可在下一次调用时传入以保持上下文。

        参数：
        - user_text: 用户输入的文本
        - session_id: 会话 ID，用于恢复上下文
        - on_delta: 流式增量回调
        - on_user_input: 用户输入请求回调，应返回用户的回复
        - on_approval: 批准请求回调，应返回 "approved" 或 "rejected"
        """
        if not str(user_text or "").strip():
            return ("", session_id)

        command = self._build_command(user_text, str(session_id or "").strip())

        try:
            proc = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,  # 总是使用 DEVNULL，避免 stdin 超时
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
        plain_stdout_lines: list[str] = []
        debug_info: dict = {
            "json_lines_received": 0,
            "assistant_messages": 0,
            "result_messages": 0,
            "text_items": 0,
            "parse_errors": 0,
            "message_types": set(),
        }

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
                        debug_info["json_lines_received"] += 1
                    except json.JSONDecodeError as e:
                        debug_info["parse_errors"] += 1
                        plain_stdout_lines.append(line)
                        continue
                    if not isinstance(obj, dict):
                        continue

                    msg_type = str(obj.get("type") or "")
                    debug_info["message_types"].add(msg_type)

                    if msg_type == "assistant":
                        debug_info["assistant_messages"] += 1
                        # 从 assistant 消息内容中提取文本片段
                        message = obj.get("message") if isinstance(obj.get("message"), dict) else {}
                        content = message.get("content") if isinstance(message.get("content"), list) else []
                        for item in content:
                            if not isinstance(item, dict):
                                continue
                            if str(item.get("type") or "") == "text":
                                text = str(item.get("text") or "")
                                if text:
                                    debug_info["text_items"] += 1
                                    full_text += text
                                    if callable(on_delta):
                                        on_delta(text)

                    elif msg_type == "result":
                        debug_info["result_messages"] += 1
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

                    elif msg_type == "user_input":
                        # 处理用户输入请求
                        if callable(on_user_input):
                            user_reply = on_user_input(obj)
                            if user_reply:
                                # 将用户回复写入 stdin（通过队列）
                                self.send_user_input(user_reply)

                    elif msg_type == "approval":
                        # 处理批准请求
                        if callable(on_approval):
                            approval_reply = on_approval(obj)
                            if approval_reply:
                                # 将批准回复写入 stdin（通过队列）
                                self.send_user_input(approval_reply)
        finally:
            # 停止 stdin 写入线程（如果已启动）
            if self.stdin_writer_thread:
                self.stdin_queue.put(None)
                self.stdin_writer_thread.join(timeout=2)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            stderr_thread.join(timeout=2)

        rc = proc.returncode
        if rc not in (0, None):
            err_detail = stderr_lines[-1] if stderr_lines else f"退出码 {rc}"
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
        """格式化调试信息"""
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
