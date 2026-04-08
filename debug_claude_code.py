"""
Claude Code 调试脚本
捕获 stderr 和 stdout 来诊断问题
"""

import subprocess
import json
import sys
import os

# 设置 UTF-8 编码
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, os.getcwd())

from claudecode_client import resolve_claudecode_command


def test_claude_code_directly():
    """直接测试 Claude Code CLI"""
    print("\n=== 直接测试 Claude Code CLI ===\n")

    cmd = resolve_claudecode_command()
    cmd.extend(["--print", "test", "--output-format", "stream-json", "--verbose"])

    print(f"执行命令: {' '.join(cmd[:5])}...\n")

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            bufsize=1,
        )

        # 读取输出
        stdout_lines = []
        stderr_lines = []
        json_count = 0
        parse_errors = 0

        print("STDOUT:")
        print("-" * 60)
        for line in proc.stdout:
            line = line.rstrip('\n')
            stdout_lines.append(line)
            print(f"  {line[:100]}")

            if line.strip():
                try:
                    obj = json.loads(line)
                    json_count += 1
                    print(f"    ✓ JSON 有效，类型: {obj.get('type', 'unknown')}")
                except json.JSONDecodeError as e:
                    parse_errors += 1
                    print(f"    ✗ JSON 解析错误: {str(e)[:50]}")

        print("\nSTDERR:")
        print("-" * 60)
        for line in proc.stderr:
            line = line.rstrip('\n')
            stderr_lines.append(line)
            print(f"  {line}")

        proc.wait()

        print("\n" + "=" * 60)
        print(f"总结:")
        print(f"  返回码: {proc.returncode}")
        print(f"  STDOUT 行数: {len(stdout_lines)}")
        print(f"  STDERR 行数: {len(stderr_lines)}")
        print(f"  有效 JSON 行数: {json_count}")
        print(f"  JSON 解析错误: {parse_errors}")

        if proc.returncode != 0:
            print(f"\n⚠️  Claude Code 返回非零退出码: {proc.returncode}")
            if stderr_lines:
                print(f"最后的错误信息: {stderr_lines[-1]}")

        return proc.returncode == 0

    except Exception as e:
        print(f"❌ 执行失败: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = test_claude_code_directly()
    sys.exit(0 if success else 1)
