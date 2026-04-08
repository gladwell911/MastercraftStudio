"""
调试 Claude Code 输出
"""

import subprocess
import sys
import os

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.getcwd())

from claudecode_client import resolve_claudecode_command


def debug_output():
    """调试 Claude Code 输出"""
    print("\n=== 调试 Claude Code 输出 ===\n")

    cmd = resolve_claudecode_command()
    question = """修改一下主界面上模型组合框中模型的显示名称：
openclaw/main改成openclaw
codex/main改成codex
claudecode/default改成claudeCode"""

    cmd.extend(["--print", question, "--output-format", "stream-json", "--verbose"])

    print(f"执行命令...\n")

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,  # 使用 PIPE
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            bufsize=1,
        )

        print("STDOUT 输出：")
        print("-" * 60)
        line_count = 0
        for line in proc.stdout:
            line_count += 1
            print(f"行 {line_count}: {line[:100]}")
            if line_count >= 20:
                print("... (省略后续行)")
                break

        # 读取剩余的 stdout
        remaining = proc.stdout.read()
        if remaining:
            print(f"\n剩余输出 ({len(remaining)} 字符):")
            print(remaining[:200])

        print("\nSTDERR 输出：")
        print("-" * 60)
        stderr = proc.stderr.read()
        print(stderr if stderr else "(无)")

        proc.wait()
        print(f"\n返回码: {proc.returncode}")

    except Exception as e:
        print(f"异常: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    debug_output()
